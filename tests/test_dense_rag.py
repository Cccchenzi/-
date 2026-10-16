"""Dense/RAG regressions with deterministic mock providers; never real AI calls.

These tests prove validation, persistence and isolation behavior. They do not
measure a model's semantic or factual accuracy. Real-provider examples are in
tools/check_real_rag.py and must be reported separately.
"""
from __future__ import annotations

from contextlib import closing
import io
import json
import math
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend import create_app, providers
from backend.vector_store import DenseVectorStore, LocalTfidfStore, tokenize


def model_answer(text="海棠实验的校准周期为七天。", quote=None, index=1):
    return {"insufficient": False, "claims": [{"text": text, "evidence": [
        {"index": index, "quote": quote if quote is not None else "海棠实验的校准周期为七天。"}
    ]}]}


class ProviderVectorValidationTests(unittest.TestCase):
    """Exercise the real embedding response boundary, not a fake search API."""

    def test_membership_status_keeps_chat_message_once_and_embedding_result(self):
        message = "ChatGPT 真实推理检查已通过；回答消耗当前会员额度。"
        state = {"profile_id": "profile-a", "chat_model": "mock-chat", "checked": True,
                 "connected": True, "chat_ready": True, "message": message}
        config = {"RAG_PROVIDER": "chatgpt", "EMBEDDING_PROVIDER": "ollama",
                  "OLLAMA_BASE_URL": "http://127.0.0.1:11434", "OLLAMA_EMBED_MODEL": "fake-embedding",
                  "CHAT_JSON_PROVIDER": lambda *_args: {}, "CHAT_PROVIDER_CONFIGURED": True,
                  "CHAT_MODEL_DISPLAY": "mock-chat", "CHAT_STATUS_PROVIDER": lambda _config: state}
        config["_MODEL_STATUS"] = {**state, "embedding_ready": True,
                                    "fingerprint": providers.embedding_fingerprint(config),
                                    "message": message + " 生成服务检查与嵌入模型推理检查已完成。"}
        result = providers.status_snapshot(config)
        self.assertEqual(result["message"].count(message), 1)
        self.assertIn("嵌入模型推理检查已完成", result["message"])
        self.assertTrue(result["connected"])

    def test_invalid_vectors_have_controlled_provider_errors(self):
        fixtures = [
            ([], 1), ([[1.0, 0.0]], 2), ([[1.0, 0.0], [1.0]], 2),
            ([[float("nan"), 1.0]], 1), ([[float("inf"), 1.0]], 1),
            ([[0.0, 0.0]], 1), ([[True, 1.0]], 1), ([["1", 2.0]], 1),
            ([[1e308, 1e308]], 1),
        ]
        for vectors, count in fixtures:
            with self.subTest(vectors=vectors):
                with self.assertRaises(providers.ProviderError):
                    providers.normalize_vectors(vectors, count)

    def test_openai_embeddings_reorder_by_index_and_validate_response(self):
        config = {"RAG_PROVIDER": "openai", "EMBEDDING_PROVIDER": "openai", "AI_API_KEY": "fake-key", "AI_BASE_URL": "https://example.invalid/v1",
                  "AI_MODEL": "fake-chat", "AI_EMBED_MODEL": "fake-embedding"}
        response = {"data": [{"index": 1, "embedding": [0.0, 4.0]}, {"index": 0, "embedding": [3.0, 0.0]}]}
        with patch("backend.providers._request_json", return_value=response):
            actual = providers.embed_texts(["first source", "second source"], config)
        self.assertEqual(actual, [[1.0, 0.0], [0.0, 1.0]])
        for bad in [
            {"data": [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [2.0]}]},
            {"data": [{"index": 0, "embedding": [float("nan")]}]},
        ]:
            with self.subTest(response=bad), patch("backend.providers._request_json", return_value=bad):
                with self.assertRaises(providers.ProviderError):
                    providers.embed_texts(["first source", "second source"], config)

    def test_semantic_vector_retrieval_does_not_require_lexical_overlap(self):
        # Equal vectors are a controlled fixture, not evidence of a real model's quality.
        config = {"RAG_PROVIDER": "ollama", "EMBEDDING_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                  "OLLAMA_CHAT_MODEL": "fake-chat", "OLLAMA_EMBED_MODEL": "fake-embedding"}
        source, query = "作业材料经由教务系统投递。", "练习成果应通过什么渠道上交？"
        self.assertFalse(set(tokenize(source)) & set(tokenize(query)))
        row = {"id": 1, "document_id": 3, "content": source,
               "embedding_json": "[1.0,0.0]", "embedding_status": "ready",
               "embedding_fingerprint": providers.embedding_fingerprint(config)}
        self.assertEqual(LocalTfidfStore().search(query, [row]), [])
        with patch("backend.providers.embed_texts", return_value=[[1.0, 0.0]]):
            matches = DenseVectorStore(config).search(query, [row])
        self.assertEqual([match.chunk["id"] for match in matches], [1])
        self.assertAlmostEqual(matches[0].score, 1.0)


class DenseRagIntegrationTests(unittest.TestCase):
    source = "海棠实验采用蓝色滤镜。\n海棠实验的校准周期为七天。\n海棠实验由魏老师负责。"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="zhiwei-dense-test-")
        self.addCleanup(self.directory.cleanup)
        self.config = {
            "TESTING": True, "DATA_DIR": self.directory.name, "SECRET_KEY": "dense-tests-only",
            "RAG_PROVIDER": "openai", "EMBEDDING_PROVIDER": "openai", "AI_API_KEY": "fake-key-never-sent",
            "AI_BASE_URL": "https://example.invalid/v1", "AI_MODEL": "mock-chat",
            "AI_EMBED_MODEL": "mock-embedding-v1", "AI_EMBED_BASE_URL": "", "AI_EMBED_API_KEY": "",
            "MIN_EMBEDDING_SCORE": 0.2,
        }
        self.embeddings = patch("backend.providers.embed_texts", side_effect=lambda texts, config: [[1.0, 0.0, 0.0] for _ in texts]).start()
        self.addCleanup(patch.stopall)
        self.generation = patch("backend.rag._call_grounded_model", return_value=model_answer()).start()
        self.rewrite = patch("backend.rag._rewrite_query", side_effect=lambda question, history, config:
            " ".join([item["content"] for item in history if item["role"] == "user"][-1:] + [question])).start()
        patch("backend.providers._request_json", side_effect=AssertionError("Unit tests must never call a real provider")).start()
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.register(self.client, "dense_student")
        self.library_id = self.library(self.client, "语义检索验收")

    def register(self, client, name):
        response = client.post("/api/auth/register", json={"username": name, "password": "test-password-2026"})
        self.assertEqual(response.status_code, 201, response.get_json())

    def library(self, client, name):
        response = client.post("/api/libraries", json={"name": name})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["library"]["id"]

    def upload(self, content=None, library_id=None, client=None, name="实验笔记.md"):
        response = (client or self.client).post(f"/api/libraries/{library_id or self.library_id}/documents",
            data={"files": [(io.BytesIO((self.source if content is None else content).encode()), name)]},
            content_type="multipart/form-data")
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["documents"][0]

    def session(self, library_id=None, client=None):
        response = (client or self.client).post(f"/api/libraries/{library_id or self.library_id}/sessions", json={})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["session"]["id"]

    def ask(self, question="海棠实验的校准周期是什么？", session_id=None, library_id=None, client=None):
        client = client or self.client
        session_id = session_id or self.session(library_id=library_id, client=client)
        response = client.post(f"/api/sessions/{session_id}/messages", json={"question": question})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def rows(self):
        with closing(sqlite3.connect(self.app.config["DATABASE"])) as database:
            database.row_factory = sqlite3.Row
            return [dict(row) for row in database.execute("SELECT * FROM chunks ORDER BY id")]

    def test_upload_persists_dense_vectors_and_reopen_reuses_them(self):
        document = self.upload()
        rows = self.rows()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["embedding_status"], "ready")
            self.assertEqual(row["embedding_fingerprint"], providers.embedding_fingerprint(self.app.config))
            vector = json.loads(row["embedding_json"])
            self.assertTrue(all(math.isfinite(value) for value in vector))
            self.assertAlmostEqual(sum(value * value for value in vector), 1.0)
        reopened = create_app(self.config).test_client()
        reopened.post("/api/auth/login", json={"username": "dense_student", "password": "test-password-2026"})
        self.embeddings.reset_mock()
        result = self.ask(client=reopened)["assistant_message"]
        self.assertEqual(result["mode"], "llm")
        self.assertEqual(result["retrieval_mode"], "dense")
        self.assertEqual(result["generation_status"], "validated")
        self.assertEqual(result["citations"][0]["document_id"], document["id"])
        calls = [text for call in self.embeddings.call_args_list for text in call.args[0]]
        self.assertEqual(len(calls), 1, "Reopening must embed the query, not re-embed all stored documents.")
        self.assertNotIn(self.source, calls)

    def test_successful_answer_contains_exact_quotes_and_persisted_claims(self):
        document = self.upload()
        session_id = self.session()
        answer = self.ask(session_id=session_id)["assistant_message"]
        self.assertEqual(answer["mode"], "llm")
        self.assertEqual(answer["generation_status"], "validated")
        self.assertEqual(len(answer["claims"]), 1)
        self.assertIn("[1]", answer["content"])
        for citation in answer["citations"]:
            self.assertEqual(citation["document_id"], document["id"])
            self.assertTrue(citation["quotes"])
            for quote in citation["quotes"]:
                self.assertIn(quote, citation["content"])
                self.assertIn(quote, self.source)
        saved = self.client.get(f"/api/sessions/{session_id}").get_json()["messages"][1]
        self.assertEqual(saved["claims"], answer["claims"])
        self.assertEqual(saved["generation_status"], "validated")

    def test_model_change_requires_reindex_and_never_reuses_old_vectors(self):
        self.upload()
        old_fingerprint = self.rows()[0]["embedding_fingerprint"]
        self.app.config["AI_EMBED_MODEL"] = "mock-embedding-v2"
        self.generation.reset_mock()
        result = self.ask()
        self.assertNotEqual(result["assistant_message"]["retrieval_mode"], "dense")
        self.assertEqual(result["assistant_message"]["mode"], "llm")
        self.assertEqual(result["assistant_message"]["generation_status"], "validated")
        self.assertTrue(result.get("notice"))
        self.assertTrue(self.generation.called, "Generation may still use the explicitly degraded lexical evidence.")
        reindex = self.client.post(f"/api/libraries/{self.library_id}/reindex")
        self.assertEqual(reindex.status_code, 200, reindex.get_json())
        self.assertGreater(reindex.get_json()["indexed_chunks"], 0)
        self.assertNotEqual(self.rows()[0]["embedding_fingerprint"], old_fingerprint)
        self.assertEqual(self.ask()["assistant_message"]["retrieval_mode"], "dense")

    def test_embedding_failure_keeps_document_and_announces_fallback_then_recovers(self):
        self.embeddings.side_effect = providers.ProviderError("模拟嵌入服务暂不可用")
        document = self.upload()
        self.assertTrue(self.rows())
        self.assertTrue(all(row["embedding_status"] != "ready" for row in self.rows()))
        answer = self.ask()
        self.assertEqual(answer["assistant_message"]["mode"], "llm")
        self.assertEqual(answer["assistant_message"]["generation_status"], "validated")
        self.assertEqual(answer["assistant_message"]["retrieval_mode"], "lexical")
        self.assertIn("七天", answer["assistant_message"]["content"])
        self.assertTrue(answer.get("notice"))
        self.assertTrue(self.generation.called)
        self.assertEqual(self.client.get(f"/api/documents/{document['id']}/chunks").status_code, 200)
        self.embeddings.side_effect = lambda texts, config: [[1.0, 0.0, 0.0] for _ in texts]
        response = self.client.post(f"/api/libraries/{self.library_id}/reindex")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["failed_chunks"], 0)
        self.assertEqual(self.ask()["assistant_message"]["generation_status"], "validated")

    def test_corrupted_saved_vectors_and_query_dimensions_cannot_be_used(self):
        self.upload()
        for bad_vector in ["[NaN,0.0,0.0]", "[0.0,0.0,0.0]", "[1.0,0.0]"]:
            with self.subTest(vector=bad_vector):
                with closing(sqlite3.connect(self.app.config["DATABASE"])) as database:
                    database.execute("UPDATE chunks SET embedding_json=?", (bad_vector,))
                    database.commit()
                self.generation.reset_mock()
                result = self.ask()
                self.assertEqual(result["assistant_message"]["mode"], "llm")
                self.assertEqual(result["assistant_message"]["generation_status"], "validated")
                self.assertEqual(result["assistant_message"]["retrieval_mode"], "lexical")
                self.assertTrue(result.get("notice"))
                self.assertTrue(self.generation.called)

    def test_invalid_generated_claims_and_quotes_are_rejected_not_relabelled_generated(self):
        self.upload()
        cases = {
            "unknown_index": model_answer(index=999),
            "fabricated_quote": model_answer(quote="海棠实验预算为88888元。"),
            "noncontiguous_quote": model_answer(quote="海棠实验七天"),
            "no_evidence": {"insufficient": False, "claims": [{"text": "七天", "evidence": []}]},
            "no_claims": {"insufficient": False, "claims": []},
            "ambiguous_refusal_with_claims": {"insufficient": True, "claims": model_answer()["claims"]},
            "wrong_boolean": {"insufficient": "false", "claims": model_answer()["claims"]},
            "boolean_reference": model_answer(index=True),
            "smuggled_reference": model_answer(text="校准周期为七天。[999]"),
        }
        for name, response in cases.items():
            with self.subTest(case=name):
                self.generation.return_value = response
                result = self.ask()
                answer = result["assistant_message"]
                self.assertEqual(answer["mode"], "extractive-fallback")
                self.assertEqual(answer["generation_status"], "fallback")
                self.assertNotIn("88888", answer["content"])
                self.assertTrue(result.get("notice"))

    def test_insufficient_evidence_refuses_even_if_dense_retrieval_returns_matches(self):
        self.upload()
        self.generation.return_value = {"insufficient": True, "claims": []}
        answer = self.ask("海棠实验的预算是多少钱？")["assistant_message"]
        self.assertEqual(answer["mode"], "refusal")
        self.assertEqual(answer["generation_status"], "insufficient")
        self.assertEqual(answer["citations"], [])
        self.assertTrue(self.generation.called)

    def test_no_semantic_matches_refuses_without_generating(self):
        self.upload()
        self.embeddings.side_effect = lambda texts, config: [[0.0, 1.0, 0.0] for _ in texts]
        answer = self.ask("完全未知的遥远星球问题")["assistant_message"]
        self.assertEqual(answer["retrieval_mode"], "dense")
        self.assertEqual(answer["mode"], "refusal")
        self.assertEqual(answer["citations"], [])
        self.generation.assert_not_called()

    def test_query_embedding_service_failure_is_explicit_even_with_stored_vectors(self):
        self.upload()
        self.embeddings.side_effect = providers.ProviderError("query service unavailable")
        result = self.ask()
        self.assertEqual(result["assistant_message"]["retrieval_mode"], "lexical")
        self.assertEqual(result["assistant_message"]["generation_status"], "validated")
        self.assertTrue(result.get("notice"))
        self.assertTrue(all(row["embedding_status"] == "ready" for row in self.rows()))

    def test_generation_transport_failure_does_not_expose_provider_secret(self):
        self.upload()
        self.generation.side_effect = TimeoutError("PRIVATE_API_KEY_AND_PROVIDER_BODY")
        result = self.ask()
        self.assertEqual(result["assistant_message"]["mode"], "extractive-fallback")
        self.assertEqual(result["assistant_message"]["generation_status"], "fallback")
        self.assertTrue(result.get("notice"))
        self.assertNotIn("PRIVATE_API_KEY", json.dumps(result))

    def test_model_configuration_is_not_reported_as_a_successful_connection(self):
        before = self.client.get("/api/config").get_json()["rag"]
        self.assertTrue(before["configured"])
        self.assertFalse(before["connected"])
        with patch("backend.providers._request_json", side_effect=RuntimeError("PRIVATE_PROVIDER_ERROR")):
            response = self.client.post("/api/models/check")
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertTrue(result["rag"]["configured"])
        self.assertFalse(result["rag"]["connected"])
        self.assertFalse(result["rag"]["chat_ready"])
        self.assertNotIn("PRIVATE_PROVIDER_ERROR", json.dumps(result))

    def test_current_library_only_reaches_embedding_generation_and_reindex(self):
        own = self.upload()
        other_library = self.library(self.client, "另一资料库")
        self.upload("密柳资料库的独有口令是cypress-728。", library_id=other_library, name="secret.md")
        stranger = self.app.test_client()
        self.register(stranger, "another_student")
        stranger_library = self.library(stranger, "另一用户资料")
        self.upload("其他用户的唯一秘密是sunflower-819。", library_id=stranger_library, client=stranger)
        result = self.ask()["assistant_message"]
        self.assertTrue(all(item["document_id"] == own["id"] for item in result["citations"]))
        sent = self.generation.call_args.args
        self.assertNotIn("cypress-728", str(sent[:3]))
        self.assertNotIn("sunflower-819", str(sent[:3]))
        self.assertEqual(stranger.post(f"/api/libraries/{self.library_id}/reindex").status_code, 404)
        self.assertEqual(stranger.get(f"/api/documents/{own['id']}/chunks").status_code, 404)

    def test_cross_document_claims_each_keep_their_own_source(self):
        first = self.upload()
        second = self.upload("海棠实验的答辩地点是思源楼B307。", name="地点.txt")

        def two_claims(question, citations, history, config):
            result = []
            for citation in citations:
                quote = ("海棠实验的校准周期为七天。" if citation["document_id"] == first["id"]
                         else "海棠实验的答辩地点是思源楼B307。")
                result.append({"text": quote, "evidence": [{"index": citation["index"], "quote": quote}]})
            return {"insufficient": False, "claims": result}

        self.generation.side_effect = two_claims
        answer = self.ask("校准周期和答辩地点分别是什么？")["assistant_message"]
        self.assertEqual(answer["generation_status"], "validated")
        self.assertEqual({item["document_id"] for item in answer["citations"]}, {first["id"], second["id"]})
        self.assertEqual(len(answer["claims"]), 2)
        self.assertIn("七天", answer["content"])
        self.assertIn("B307", answer["content"])

    def test_multiturn_pronoun_has_context_but_new_topic_does_not(self):
        self.upload()
        session_id = self.session()
        self.generation.return_value = model_answer("负责人是魏老师。", "海棠实验由魏老师负责。")
        self.ask("海棠实验由谁负责？", session_id=session_id)
        self.generation.return_value = model_answer()
        self.ask("他负责的实验多久校准一次？", session_id=session_id)
        self.assertTrue(self.generation.call_args.args[2], "Pronoun follow-up must send relevant conversation context.")
        self.assertIn("海棠实验由谁负责", str(self.generation.call_args.args[2]))
        self.ask("请介绍海棠实验的滤镜。", session_id=session_id)
        self.assertEqual(self.generation.call_args.args[2], [], "An independent question must not inherit unrelated old instructions.")

    def test_deleting_document_and_library_removes_dense_rows_and_old_sources(self):
        first = self.upload()
        session_id = self.session()
        self.ask(session_id=session_id)
        self.assertEqual(self.client.delete(f"/api/documents/{first['id']}").status_code, 200)
        self.assertEqual(self.rows(), [])
        self.generation.reset_mock()
        answer = self.ask()["assistant_message"]
        self.assertEqual(answer["mode"], "refusal")
        self.assertEqual(answer["citations"], [])
        self.generation.assert_not_called()
        saved = self.client.get(f"/api/sessions/{session_id}").get_json()["messages"][1]
        self.assertFalse(saved["citations"][0]["available"])
        self.upload()
        self.assertTrue(self.rows())
        self.assertEqual(self.client.delete(f"/api/libraries/{self.library_id}").status_code, 200)
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()
