"""DeepSeek wire-contract regressions with synthetic responses, never real keys.

These tests validate integration behavior, not a real DeepSeek deployment.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from backend import create_app, providers
from tools import check_real_rag


def completion(content='{"ok":true}', finish="stop"):
    return {"choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content}}]}


class DeepSeekProviderTests(unittest.TestCase):
    def config(self, **overrides):
        return {"RAG_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "synthetic-deepseek-key",
                "OLLAMA_BASE_URL": "http://127.0.0.1:11434", "OLLAMA_EMBED_MODEL": "mock-local-embedding",
                **overrides}

    def test_factory_defaults_to_unconfigured_deepseek_without_membership(self):
        with tempfile.TemporaryDirectory(prefix="zhiwei-deepseek-default-") as temporary, \
                patch("backend._read_env_file", return_value={}), patch.dict(os.environ, {}, clear=True):
            app = create_app({"TESTING": True, "DATA_DIR": temporary, "SECRET_KEY": "synthetic-test-only",
                              "AI_API_KEY": "old-provider-key-must-not-be-used"})
            self.assertEqual(app.config["RAG_PROVIDER"], "deepseek")
            self.assertEqual(app.config["EMBEDDING_PROVIDER"], "ollama")
            settings = providers.settings(app.config)
            self.assertEqual(settings["base"], "https://api.deepseek.com")
            self.assertEqual(settings["chat_model"], "deepseek-flash")
            self.assertEqual(settings["key"], "")
            self.assertNotIn("chatgpt_store", app.extensions)
            self.assertFalse((Path(temporary) / "chatgpt").exists())
            client = app.test_client()
            with patch("backend.providers._request_json") as network:
                state = client.get("/api/config").get_json()
                self.assertEqual(client.get("/api/chatgpt").status_code, 404)
                self.assertFalse(state["provider_configured"])
                self.assertFalse(state["rag"]["connected"])
                self.assertIn("DEEPSEEK_API_KEY", state["rag"]["message"])
                self.assertNotIn("old-provider-key", json.dumps(state))
                network.assert_not_called()

    def test_chat_wire_contract_uses_dedicated_key_json_and_disabled_thinking(self):
        stale_membership_hook = Mock(side_effect=AssertionError("must not use membership"))
        config = self.config(AI_API_KEY="other-provider-key", CHAT_JSON_PROVIDER=stale_membership_hook,
                             CHAT_PROVIDER_CONFIGURED=True, DEEPSEEK_BASE_URL="https://api.deepseek.com/chat/completions")
        messages = [{"role": "system", "content": 'Return JSON exactly like {"ok":true}.'}]
        with patch("backend.providers._request_json", return_value=completion()) as request:
            self.assertEqual(providers.chat_json(messages, config, max_tokens=64), {"ok": True})
        args, kwargs = request.call_args
        self.assertEqual(args[:2], ("https://api.deepseek.com", "/chat/completions"))
        self.assertEqual(args[2]["model"], "deepseek-flash")
        self.assertEqual(args[2]["thinking"], {"type": "disabled"})
        self.assertEqual(args[2]["response_format"], {"type": "json_object"})
        self.assertEqual(args[2]["max_tokens"], 64)
        self.assertFalse(args[2]["stream"])
        self.assertEqual(kwargs["key"], "synthetic-deepseek-key")
        self.assertNotIn("other-provider-key", json.dumps(args))
        stale_membership_hook.assert_not_called()

    def test_truncated_interrupted_empty_and_malformed_answers_are_rejected(self):
        fixtures = [completion(finish=reason) for reason in
                    (None, "length", "content_filter", "tool_calls", "aborted", "insufficient_system_resource")]
        fixtures += [completion(content=value) for value in ("", "   ", "{", "[]", None)]
        fixtures += [{"choices": []}, {"choices": [None]}]
        for response in fixtures:
            with self.subTest(response=response), patch("backend.providers._request_json", return_value=response):
                with self.assertRaises(providers.ProviderError):
                    providers.chat_json([], self.config())

    def test_embeddings_stay_local_and_never_inherit_deepseek_endpoint_or_key(self):
        config = self.config(AI_EMBED_MODEL="leftover-online-model", AI_API_KEY="leftover-key")
        with patch("backend.providers._request_json", return_value={"embeddings": [[3.0, 4.0]]}) as request:
            self.assertEqual(providers.embed_texts(["synthetic source"], config), [[0.6, 0.8]])
        args, kwargs = request.call_args
        self.assertEqual(args[:2], ("http://127.0.0.1:11434", "/api/embed"))
        self.assertNotIn("key", kwargs)
        for overrides in ({"EMBEDDING_PROVIDER": "openai"},
                          {"EMBEDDING_PROVIDER": "openai", "AI_EMBED_BASE_URL": "https://api.deepseek.com/v1",
                           "AI_EMBED_API_KEY": "explicit-but-unsupported-key"}):
            with self.subTest(overrides=overrides), patch("backend.providers._request_json") as request:
                invalid = {**config, **overrides}
                self.assertFalse(providers.embedding_configured(invalid))
                with self.assertRaises(providers.ProviderError):
                    providers.embed_texts(["synthetic source"], invalid)
                request.assert_not_called()

    def test_model_check_requires_real_probe_contract_and_separate_local_embedding(self):
        calls = []

        def respond(base, path, payload=None, **_kwargs):
            calls.append((base, path))
            if path == "/models":
                return {"data": [{"id": "deepseek-flash"}]}
            if path == "/chat/completions":
                return completion()
            if path == "/api/tags":
                return {"models": [{"name": "mock-local-embedding"}]}
            if path == "/api/embed":
                return {"embeddings": [[1.0, 0.0]]}
            raise AssertionError("unexpected provider route")

        config = self.config(CHAT_CHECK_PROVIDER=Mock(side_effect=AssertionError("must not use membership")))
        with patch("backend.providers._request_json", side_effect=respond):
            result = providers.check_models(config)
        self.assertTrue(result["connected"])
        self.assertTrue(result["chat_ready"])
        self.assertTrue(result["embedding_ready"])
        self.assertEqual(calls, [("https://api.deepseek.com", "/models"),
                                 ("https://api.deepseek.com", "/chat/completions"),
                                 ("http://127.0.0.1:11434", "/api/tags"),
                                 ("http://127.0.0.1:11434", "/api/embed")])
        config["CHAT_CHECK_PROVIDER"].assert_not_called()

    def test_missing_dedicated_key_never_uses_old_api_or_membership_authorization(self):
        hook = Mock(side_effect=AssertionError("membership must remain optional"))
        config = self.config(DEEPSEEK_API_KEY="", AI_API_KEY="old-key", AI_BASE_URL="https://old.invalid",
                             AI_MODEL="old-model", CHAT_JSON_PROVIDER=hook, CHAT_CHECK_PROVIDER=hook,
                             CHAT_STATUS_PROVIDER=hook, CHAT_PROVIDER_CONFIGURED=True)
        self.assertFalse(providers.chat_configured(config))
        with patch("backend.providers._request_json") as request:
            with self.assertRaisesRegex(providers.ProviderError, "DEEPSEEK_API_KEY"):
                providers.chat_json([], config)
            state = providers.status_snapshot(config)
            self.assertFalse(state["connected"])
            request.assert_not_called()
        hook.assert_not_called()

    def test_http_errors_do_not_expose_key_or_upstream_error_body(self):
        marker = "PRIVATE_UPSTREAM_BODY_AND_KEY"
        for code in (401, 402, 429, 500):
            with self.subTest(code=code):
                error = urllib.error.HTTPError("https://api.deepseek.com/chat/completions", code,
                                               marker, {}, io.BytesIO(marker.encode()))
                with patch("backend.providers.urllib.request.urlopen", side_effect=error):
                    with self.assertRaises(providers.ProviderError) as caught:
                        providers.chat_json([], self.config())
                self.assertNotIn(marker, str(caught.exception))
                self.assertNotIn("synthetic-deepseek-key", str(caught.exception))
                error.close()

    def test_truncated_generated_json_falls_back_in_application(self):
        source = "海棠实验的校准周期为七天。"
        invalid_completion = completion(json.dumps({"insufficient": False, "claims": [
            {"text": source, "evidence": [{"index": 1, "quote": source}]}]}, ensure_ascii=False), finish="length")
        with tempfile.TemporaryDirectory(prefix="zhiwei-deepseek-fallback-") as temporary, \
                patch("backend._read_env_file", return_value={}), patch.dict(os.environ, {}, clear=True):
            app = create_app({**self.config(), "TESTING": True, "DATA_DIR": temporary,
                              "SECRET_KEY": "synthetic-test-only"})
            client = app.test_client()
            client.post("/api/auth/register", json={"username": "test_student", "password": "synthetic-password"})
            library = client.post("/api/libraries", json={"name": "合成资料"}).get_json()["library"]["id"]
            with patch("backend.providers.embed_texts", return_value=[[1.0, 0.0]]), \
                    patch("backend.providers._request_json", return_value=invalid_completion):
                response = client.post(f"/api/libraries/{library}/documents",
                                       data={"files": [(io.BytesIO(source.encode()), "fact.txt")]})
                self.assertEqual(response.status_code, 201)
                session = client.post(f"/api/libraries/{library}/sessions", json={}).get_json()["session"]["id"]
                result = client.post(f"/api/sessions/{session}/messages", json={"question": "海棠实验校准周期？"}).get_json()
            self.assertEqual(result["assistant_message"]["mode"], "extractive-fallback")
            self.assertEqual(result["assistant_message"]["generation_status"], "fallback")
            self.assertIn("未由模型生成", result["assistant_message"]["content"])
            self.assertNotIn("synthetic-deepseek-key", json.dumps(result))

    def test_real_check_cli_maps_deepseek_and_local_embedding_without_claiming_success(self):
        args = argparse.Namespace(provider="deepseek", ollama_url=None, base_url=None,
                                  chat_model="deepseek-flash", embedding_model="fixture-embedding",
                                  embedding_provider="ollama")
        calls = []

        def local_only(base, path, *_args, **_kwargs):
            calls.append((base, path))
            self.assertEqual(base, "http://127.0.0.1:11434")
            return {"models": [{"name": "fixture-embedding"}]} if path == "/api/tags" else {"embeddings": [[1.0]]}

        with patch("backend._read_env_file", return_value={}), patch.dict(os.environ, {}, clear=True), \
                patch("backend.providers._request_json", side_effect=local_only), patch("sys.stdout", new=io.StringIO()):
            result = check_real_rag.run(args)
        self.assertEqual(result["models"], {"provider": "deepseek", "chat": "deepseek-flash",
                                           "embedding": "fixture-embedding", "embedding_provider": "ollama"})
        self.assertFalse(result["all_passed"])
        self.assertEqual(result["passed"], 0)
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["temporary_data_removed"])
        self.assertIn("DEEPSEEK_API_KEY", result["provider_check"]["rag"]["message"])
        self.assertTrue(calls)
