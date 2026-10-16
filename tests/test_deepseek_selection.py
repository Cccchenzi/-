"""Per-user DeepSeek selection contracts, using synthetic provider responses."""
from __future__ import annotations

from contextlib import closing
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend import create_app, providers


class DeepSeekSelectionTests(unittest.TestCase):
    source = "海棠实验校准周期为七天。"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="zhiwei-model-selection-")
        self.addCleanup(self.temporary.cleanup)
        self.config = {"TESTING": True, "DATA_DIR": self.temporary.name, "SECRET_KEY": "synthetic-only",
                       "RAG_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "synthetic-private-key",
                       "DEEPSEEK_BASE_URL": "https://api.deepseek.com", "DEEPSEEK_MODEL": "deepseek-flash",
                       "EMBEDDING_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                       "OLLAMA_EMBED_MODEL": "synthetic-embedding"}
        with patch("backend._read_env_file", return_value={}), patch.dict(os.environ, {}, clear=True):
            self.app = create_app(self.config)
        self.catalog = [{"id": "deepseek-flash", "name": "DeepSeek Flash", "object": "model"},
                        {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "object": "model"},
                        {"id": "future-model", "name": "Future model", "object": "model"}]
        self.calls = []
        self.network = patch("backend.providers._request_json", side_effect=self.wire).start()
        self.addCleanup(patch.stopall)
        self.alice = self.app.test_client()
        self.alice_id = self.register(self.alice, "model_alice")
        self.bob = self.app.test_client()
        self.register(self.bob, "model_bob")

    def register(self, client, username):
        response = client.post("/api/auth/register", json={"username": username, "password": "synthetic-password"})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["user"]["id"]

    def wire(self, base, path, payload=None, **kwargs):
        self.calls.append((base, path, payload))
        if path == "/models":
            self.assertEqual(kwargs["key"], "synthetic-private-key")
            return {"object": "list", "data": self.catalog}
        if path == "/api/tags":
            return {"models": [{"name": "synthetic-embedding"}]}
        if path == "/api/embed":
            self.assertNotIn("key", kwargs)
            return {"embeddings": [[1.0, 0.0] for _ in payload["input"]]}
        if path == "/chat/completions":
            if payload["max_tokens"] == 32:
                answer = {"ok": True}
            elif payload["max_tokens"] == 250:
                answer = {"query": "海棠实验校准周期是什么"}
            else:
                evidence = json.loads(payload["messages"][-1]["content"])["evidence"]
                answer = {"insufficient": False, "claims": [{"text": self.source,
                    "evidence": [{"index": evidence[0]["index"], "quote": self.source}]}]}
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer, ensure_ascii=False)}}]}
        raise AssertionError("Unexpected provider route")

    def select(self, model, client=None):
        response = (client or self.alice).post("/api/deepseek/model", json={"model": model})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def current(self, client=None):
        return (client or self.alice).get("/api/config").get_json()

    def same_user_client(self):
        client = self.app.test_client()
        with client.session_transaction() as state:
            state["user_id"] = self.alice_id
        return client

    def create_chat(self, upload=True):
        library = self.alice.post("/api/libraries", json={"name": "Selection fixture"}).get_json()["library"]["id"]
        if upload:
            response = self.alice.post(f"/api/libraries/{library}/documents",
                data={"files": [(io.BytesIO(self.source.encode()), "source.txt")]})
            self.assertEqual(response.status_code, 201, response.get_json())
        return self.alice.post(f"/api/libraries/{library}/sessions", json={}).get_json()["session"]["id"]

    def test_catalog_requires_login_and_rejects_cross_origin_without_network(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/api/deepseek/models").status_code, 401)
        self.assertEqual(anonymous.post("/api/deepseek/model", json={"model": "deepseek-flash"}).status_code, 401)
        for path, method in [("/api/deepseek/models", "get"), ("/api/deepseek/model", "post")]:
            with self.subTest(path=path):
                response = getattr(self.alice, method)(path, json={"model": "deepseek-flash"}, headers={"Origin": "https://evil.invalid"})
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.calls, [])

    def test_live_catalog_exposes_labels_and_unhardcoded_models_without_secrets(self):
        first = self.alice.get("/api/deepseek/models")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["selected_model"], "deepseek-flash")
        self.assertEqual(first.get_json()["models"][1], {"id": "deepseek-v4-pro", "display_name": "DeepSeek V4 Pro"})
        self.assertNotIn("synthetic-private-key", first.get_data(as_text=True))
        self.catalog.append({"id": "newly-released"})
        second = self.alice.get("/api/deepseek/models").get_json()
        self.assertIn({"id": "newly-released", "display_name": "newly-released"}, second["models"])
        selected = self.select("newly-released")
        self.assertEqual(selected["model"], "newly-released")
        self.assertFalse(selected["rag"]["checked"])
        self.assertEqual(len([call for call in self.calls if call[1] == "/models"]), 3)
        self.assertFalse(any(call[1] == "/chat/completions" for call in self.calls))

    def test_selection_is_per_user_persists_restart_and_never_changes_app_default(self):
        self.select("deepseek-v4-pro")
        self.assertEqual(self.current()["model"], "deepseek-v4-pro")
        self.assertEqual(self.current(self.bob)["model"], "deepseek-flash")
        self.assertEqual(self.app.config["DEEPSEEK_MODEL"], "deepseek-flash")
        with patch("backend._read_env_file", return_value={}), patch.dict(os.environ, {}, clear=True):
            reopened = create_app(self.config).test_client()
        reopened.post("/api/auth/login", json={"username": "model_alice", "password": "synthetic-password"})
        self.assertEqual(self.current(reopened)["model"], "deepseek-v4-pro")
        self.assertFalse(self.current(reopened)["rag"]["checked"])
        self.select("future-model", self.bob)
        self.assertEqual(self.current()["model"], "deepseek-v4-pro")
        self.assertEqual(self.current(self.bob)["model"], "future-model")

    def test_existing_database_gains_settings_table_without_touching_user_documents(self):
        session_id = self.create_chat()
        with closing(sqlite3.connect(self.app.config["DATABASE"])) as database:
            database.execute("DROP TABLE user_model_settings")
            database.commit()
        with patch("backend._read_env_file", return_value={}), patch.dict(os.environ, {}, clear=True):
            reopened = create_app(self.config).test_client()
        reopened.post("/api/auth/login", json={"username": "model_alice", "password": "synthetic-password"})
        self.assertEqual(reopened.get(f"/api/sessions/{session_id}").status_code, 200)
        self.select("future-model", reopened)
        self.assertEqual(self.current(reopened)["model"], "future-model")

    def test_invalid_input_removed_catalog_and_provider_failure_keep_current_selection(self):
        self.select("deepseek-v4-pro")
        for payload in ({}, {"model": ""}, {"model": False}, {"model": "x" * 201}, [], {"model": "not-listed"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.alice.post("/api/deepseek/model", json=payload).status_code, 400)
                self.assertEqual(self.current()["model"], "deepseek-v4-pro")
        self.catalog = [row for row in self.catalog if row["id"] != "future-model"]
        self.assertEqual(self.alice.post("/api/deepseek/model", json={"model": "future-model"}).status_code, 400)
        with patch("backend.providers._request_json", side_effect=providers.ProviderError("PRIVATE_UPSTREAM_BODY_AND_KEY")):
            for method, path in [("get", "/api/deepseek/models"), ("post", "/api/deepseek/model")]:
                response = getattr(self.alice, method)(path, json={"model": "deepseek-flash"})
                self.assertEqual(response.status_code, 502)
                self.assertNotIn("PRIVATE_", response.get_data(as_text=True))
        self.assertEqual(self.current()["model"], "deepseek-v4-pro")

    def test_missing_key_disabled_provider_and_malformed_catalog_are_safe(self):
        self.app.config["DEEPSEEK_API_KEY"] = ""
        for method, path in [("get", "/api/deepseek/models"), ("post", "/api/deepseek/model")]:
            response = getattr(self.alice, method)(path, json={"model": "deepseek-flash"})
            self.assertEqual(response.status_code, 503)
            self.assertIn("DEEPSEEK_API_KEY", response.get_json()["error"])
        self.assertEqual(self.calls, [])
        self.app.config["RAG_PROVIDER"] = "local"
        self.assertEqual(self.alice.get("/api/deepseek/models").status_code, 409)
        self.app.config.update(RAG_PROVIDER="deepseek", DEEPSEEK_API_KEY="synthetic-private-key")
        for malformed in ({}, {"data": "wrong"}, {"data": [None]}, {"data": [{"id": "invalid id"}]}):
            with self.subTest(catalog=malformed), patch("backend.providers._request_json", return_value=malformed):
                response = self.alice.get("/api/deepseek/models")
                self.assertEqual(response.status_code, 502)

    def test_check_cache_is_per_user_and_switch_returns_fresh_unchecked_config(self):
        checked = self.alice.post("/api/models/check").get_json()
        self.assertTrue(checked["rag"]["connected"])
        self.assertFalse(self.current(self.bob)["rag"]["checked"])
        self.assertFalse(self.app.test_client().get("/api/config").get_json()["rag"]["checked"])
        self.select("future-model", self.bob)
        self.assertTrue(self.bob.post("/api/models/check").get_json()["rag"]["connected"])
        changed = self.select("deepseek-v4-pro")
        self.assertEqual(changed["model"], "deepseek-v4-pro")
        self.assertEqual(changed["rag"]["chat_model"], "deepseek-v4-pro")
        self.assertFalse(changed["rag"]["checked"])
        self.assertFalse(changed["rag"]["connected"])
        self.assertTrue(self.current(self.bob)["rag"]["connected"])
        self.assertEqual(self.alice.get("/api/health").get_json()["model"], "deepseek-v4-pro")
        self.calls.clear()
        self.assertTrue(self.alice.post("/api/models/check").get_json()["rag"]["connected"])
        probes = [payload for _, path, payload in self.calls if path == "/chat/completions"]
        self.assertEqual([payload["model"] for payload in probes], ["deepseek-v4-pro"])

    def test_successful_generation_and_history_record_actual_selected_model_only(self):
        session_id = self.create_chat()
        self.select("deepseek-v4-pro")
        result = self.alice.post(f"/api/sessions/{session_id}/messages", json={"question": "海棠实验校准周期是什么？"}).get_json()
        self.assertEqual(result["assistant_message"]["mode"], "llm")
        self.assertEqual(result["assistant_message"]["generation_model"], "deepseek-v4-pro")
        payloads = [payload for _, path, payload in self.calls if path == "/chat/completions"]
        self.assertEqual(payloads[-1]["model"], "deepseek-v4-pro")
        saved = self.alice.get(f"/api/sessions/{session_id}").get_json()["messages"][-1]
        self.assertEqual(saved["generation_model"], "deepseek-v4-pro")
        with patch("backend.rag._call_grounded_model", side_effect=TimeoutError("PRIVATE_KEY")):
            fallback = self.alice.post(f"/api/sessions/{session_id}/messages", json={"question": "海棠实验校准周期是什么？"}).get_json()["assistant_message"]
        self.assertEqual(fallback["mode"], "extractive-fallback")
        self.assertNotIn("generation_model", fallback)
        empty_session = self.create_chat(upload=False)
        refusal = self.alice.post(f"/api/sessions/{empty_session}/messages", json={"question": "这是什么文章"}).get_json()["assistant_message"]
        self.assertEqual(refusal["mode"], "refusal")
        self.assertNotIn("generation_model", refusal)
        self.app.config["RAG_PROVIDER"] = "local"
        local = self.alice.post(f"/api/sessions/{session_id}/messages", json={"question": "这是什么文章"}).get_json()["assistant_message"]
        self.assertEqual(local["mode"], "extractive")
        self.assertNotIn("generation_model", local)

    def test_inflight_check_cannot_revive_status_after_switch_or_roundtrip(self):
        original = providers.check_models
        for switch_back in (False, True):
            with self.subTest(switch_back=switch_back):
                self.select("deepseek-flash")
                started, release = threading.Event(), threading.Event()
                result = {}
                other_tab = self.same_user_client()

                def delayed(config):
                    started.set()
                    if not release.wait(5):
                        raise AssertionError("Test failed to release model check")
                    return original(config)

                def run():
                    try:
                        result["response"] = other_tab.post("/api/models/check")
                    except Exception as error:
                        result["error"] = error

                with patch("backend.providers.check_models", side_effect=delayed):
                    thread = threading.Thread(target=run)
                    thread.start()
                    try:
                        self.assertTrue(started.wait(5))
                        self.select("deepseek-v4-pro")
                        if switch_back:
                            self.select("deepseek-flash")
                    finally:
                        release.set()
                        thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertNotIn("error", result)
                response = result["response"].get_json()
                self.assertEqual(response["model"], "deepseek-flash" if switch_back else "deepseek-v4-pro")
                self.assertFalse(response["rag"]["checked"])
                self.assertFalse(self.current()["rag"]["checked"])

    def test_rewrite_and_generation_share_snapshot_during_concurrent_switch(self):
        session_id = self.create_chat()
        self.alice.post(f"/api/sessions/{session_id}/messages", json={"question": "海棠实验校准周期是什么？"})
        self.calls.clear()
        started, release = threading.Event(), threading.Event()
        result = {}
        other_tab = self.same_user_client()

        def delayed(base, path, payload=None, **kwargs):
            if path == "/chat/completions" and payload["max_tokens"] == 250:
                started.set()
                if not release.wait(5):
                    raise AssertionError("Test failed to release rewrite")
            return self.wire(base, path, payload, **kwargs)

        def run():
            try:
                result["response"] = other_tab.post(f"/api/sessions/{session_id}/messages", json={"question": "它多久校准一次？"})
            except Exception as error:
                result["error"] = error

        with patch("backend.providers._request_json", side_effect=delayed):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(started.wait(5))
                self.select("deepseek-v4-pro")
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", result)
        answer = result["response"].get_json()["assistant_message"]
        self.assertEqual(answer["mode"], "llm")
        self.assertEqual(answer["generation_model"], "deepseek-flash")
        models = [payload["model"] for _, path, payload in self.calls if path == "/chat/completions"]
        self.assertEqual(models, ["deepseek-flash", "deepseek-flash"])
        self.assertEqual(self.current()["model"], "deepseek-v4-pro")
        saved = self.alice.get(f"/api/sessions/{session_id}").get_json()["messages"][-1]
        self.assertEqual(saved["generation_model"], "deepseek-flash")


if __name__ == "__main__":
    unittest.main()
