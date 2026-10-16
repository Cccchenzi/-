"""Overview, safe failure reporting and source-span regressions; no real AI calls."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from backend import create_app, providers
from backend.rag import answer_question, _validate_grounded_answer
from backend.vector_store import RankedChunk


def chunk(identifier, content, *, document_id=1, name="paper.pdf", page=1, ordinal=None):
    return {"id": identifier, "document_id": document_id, "document_name": name,
            "content": content, "page": page, "ordinal": ordinal or identifier,
            "start_line": 1, "end_line": 10, "embedding_status": "ready"}


def grounded(question, citations, history, config):
    return {"insufficient": False, "claims": [{"text": "资料中的内容。", "evidence": [
        {"index": citation["index"], "quote": citation["content"]} for citation in citations]}]}


class OverviewRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.rows = [chunk(1, "PREPRINT 1\nA Unified Anomaly Detection Framework"),
                     chunk(2, "Abstract—We present a unified framework."),
                     chunk(3, "It supports several visual inspection scenarios."),
                     chunk(4, "Index Terms—Anomaly detection, computer vision."),
                     chunk(5, "1. Introduction: related research."),
                     chunk(100, "MVTec table: 99.3 98.7 94.2", page=12),
                     chunk(130, "Acknowledgments and references.", page=15)]
        self.store = Mock()
        self.store.search.return_value = [RankedChunk(self.rows[-2], .99), RankedChunk(self.rows[-1], .95)]

    def test_generic_overview_uses_ordered_opening_not_high_scoring_tables(self):
        metadata = {}
        with patch("backend.rag._call_grounded_model", side_effect=grounded) as model:
            answer, citations, mode, notice = answer_question("这是什么文章", list(reversed(self.rows)), [],
                {"RAG_PROVIDER": "ollama", "VECTOR_STORE": self.store}, metadata)
        self.store.search.assert_not_called()
        self.assertEqual(mode, "llm")
        self.assertEqual([item["chunk_id"] for item in citations], [1, 2, 3, 4])
        self.assertTrue(all(item["page"] == 1 and item["score"] is None for item in citations))
        self.assertEqual(metadata["retrieval_mode"], "overview")
        self.assertEqual(metadata["notice"], notice)
        self.assertNotIn("MVTec", str(model.call_args.args[:3]))

    def test_polite_overview_requests_and_specific_questions_keep_distinct_routes(self):
        positives = ["请问这是什么文章？", "请帮我总结这篇论文", "这篇论文主要讲什么？",
                     "概括一下这篇文章的主要内容", "What is this paper about?"]
        for question in positives:
            with self.subTest(question=question):
                metadata = {}
                answer_question(question, self.rows, [], {"RAG_PROVIDER": "local", "VECTOR_STORE": self.store}, metadata)
                self.assertEqual(metadata["retrieval_mode"], "overview")
        self.store.search.assert_not_called()
        negatives = ["这篇文章的实验结果是什么？", "总结这篇文章的局限性", "第12页MVTec结果是什么？", "它主要讲什么？"]
        for question in negatives:
            with self.subTest(question=question):
                metadata = {}
                _, citations, _, _ = answer_question(question, self.rows, [],
                    {"RAG_PROVIDER": "local", "VECTOR_STORE": self.store}, metadata)
                self.assertEqual(metadata["retrieval_mode"], "lexical")
                self.assertEqual(citations[0]["chunk_id"], 100)
                self.assertEqual(self.store.search.call_args.args[0], question)

    def test_multiple_documents_and_ordinals_request_explicit_filename(self):
        rows = self.rows + [chunk(200, "Another paper opening", document_id=2, name="another.pdf")]
        for question, corpus in [("这是什么文章", rows), ("第二篇主要讲什么", rows), ("第二篇主要讲什么", self.rows)]:
            with self.subTest(question=question, documents=len(corpus)):
                metadata = {}
                with patch("backend.rag._call_grounded_model") as model:
                    text, citations, mode, notice = answer_question(question, corpus, [],
                        {"RAG_PROVIDER": "ollama", "VECTOR_STORE": self.store}, metadata)
                self.assertEqual(mode, "refusal")
                self.assertEqual(citations, [])
                self.assertIn("文件名", text)
                self.assertEqual(metadata["notice"], notice)
                self.assertTrue(metadata["requires_document_selection"])
                model.assert_not_called()
        self.store.search.assert_not_called()

    def test_unique_filename_handles_overlapping_names_and_duplicate_names(self):
        rows = [chunk(1, "Wrong source", name="a.pdf"),
                chunk(2, "Correct source", document_id=2, name="data.pdf")]
        metadata = {}
        with patch("backend.rag._call_grounded_model", side_effect=grounded) as model:
            _, citations, mode, _ = answer_question("总结 data.pdf", rows,
                [{"role": "user", "content": "a.pdf 的表格是什么？"}],
                {"RAG_PROVIDER": "ollama", "VECTOR_STORE": self.store}, metadata)
        self.assertEqual(mode, "llm")
        self.assertEqual([item["document_id"] for item in citations], [2])
        self.assertEqual(model.call_args.args[2], [])
        rows.append(chunk(3, "Different content, same name", document_id=3, name="data.pdf"))
        _, citations, mode, _ = answer_question("总结 data.pdf", rows, [], {"RAG_PROVIDER": "local"})
        self.assertEqual(mode, "refusal")
        self.assertEqual(citations, [])

    def test_pronoun_followup_keeps_normal_context_retrieval(self):
        history = [{"role": "user", "content": "这篇文章的算法是什么？"},
                   {"role": "assistant", "content": "资料介绍了一种算法。"}]
        metadata = {}
        config = {"RAG_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                  "OLLAMA_CHAT_MODEL": "mock-chat", "VECTOR_STORE": self.store}
        with patch("backend.rag._rewrite_query", return_value="该算法主要解决什么问题") as rewrite, \
                patch("backend.rag._call_grounded_model", side_effect=grounded) as model:
            answer_question("它主要讲什么？", self.rows, history, config, metadata)
        rewrite.assert_called_once()
        self.assertEqual(model.call_args.args[2], history)
        self.assertEqual(metadata["retrieval_mode"], "dense")
        self.assertEqual(self.store.search.call_args.args[0], "该算法主要解决什么问题")

    def test_empty_library_never_calls_generation_for_overview(self):
        metadata = {}
        with patch("backend.rag._call_grounded_model") as model:
            _, citations, mode, notice = answer_question("这是什么文章", [], [], {"RAG_PROVIDER": "ollama"}, metadata)
        model.assert_not_called()
        self.assertEqual((citations, mode), ([], "refusal"))
        self.assertIn("notice", metadata)
        self.assertEqual(metadata["notice"], notice)


class QuoteAndFailureTests(unittest.TestCase):
    def validate(self, source, quote):
        citation = {"index": 1, "content": source, "document_id": 1}
        response = {"insufficient": False, "claims": [{"text": "有来源的结论", "evidence": [{"index": 1, "quote": quote}]}]}
        return _validate_grounded_answer(response, [citation])

    def test_layout_whitespace_maps_back_to_exact_original_source_span(self):
        source = "Preface. A unified\n  framework\tfor full-spectrum anomaly detection. End."
        _, citations, claims, _ = self.validate(source, "A unified framework for full-spectrum anomaly detection.")
        expected = "A unified\n  framework\tfor full-spectrum anomaly detection."
        self.assertEqual(citations[0]["quotes"], [expected])
        self.assertEqual(claims[0]["evidence"][0]["quote"], expected)
        self.assertIn(expected, source)
        # Multiple whitespace styles, including non-breaking spaces, stay exact in storage.
        _, citations, _, _ = self.validate("结果：12\u00a0\u00a0percent", "结果：12 percent")
        self.assertEqual(citations[0]["quotes"], ["结果：12\u00a0\u00a0percent"])

    def test_whitespace_tolerance_cannot_change_number_word_punctuation_or_order(self):
        cases = [("value 1 2", "value 12"), ("value 12", "value 1 2"),
                 ("word a b", "word ab"), ("word ab", "word a b"),
                 ("metric 9.4", "metric 94"), ("alpha-beta", "alpha beta"),
                 ("first then second", "second then first"),
                 ("alpha extra beta", "alpha beta"), ("value 94", "value 95")]
        for source, quote in cases:
            with self.subTest(source=source, quote=quote), self.assertRaises(ValueError):
                self.validate(source, quote)

    def test_generation_and_validation_failures_have_distinct_safe_persistable_metadata(self):
        rows = [chunk(1, "Only supported source text.")]
        for stage, effect in [("generation", TimeoutError("PRIVATE_KEY_AND_RESPONSE")),
                              ("validation", {"insufficient": False, "claims": [{"text": "PRIVATE_FAKE", "evidence": [{"index": 1, "quote": "PRIVATE_FAKE"}]}]})]:
            with self.subTest(stage=stage):
                metadata = {}
                kwargs = {"side_effect": effect} if isinstance(effect, Exception) else {"return_value": effect}
                with patch("backend.rag._call_grounded_model", **kwargs):
                    result = answer_question("这是什么文章", rows, [], {"RAG_PROVIDER": "ollama"}, metadata)
                self.assertEqual(result[2], "extractive-fallback")
                self.assertEqual(metadata["failure_stage"], stage)
                self.assertEqual(metadata["failure_code"], "generation_failed" if stage == "generation" else "evidence_validation_failed")
                self.assertEqual(metadata["notice"], result[3])
                self.assertNotIn("PRIVATE_", json.dumps([result, metadata]))
                self.assertNotIn("模型服务暂不可用", result[0])

    def test_extractive_display_is_bounded_but_citation_sources_remain_complete(self):
        rows = [chunk(index, f"Paragraph {index}: " + "long source " * 250) for index in range(1, 5)]
        text, citations, mode, _ = answer_question("这是什么文章", rows, [], {"RAG_PROVIDER": "local"})
        self.assertEqual(mode, "extractive")
        self.assertLess(len(text), 1800)
        self.assertEqual(len(citations), 4)
        self.assertTrue(all(item["content"] == rows[index]["content"] for index, item in enumerate(citations)))
        self.assertIn("完整片段见引用", text)


class OverviewPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="zhiwei-overview-")
        self.addCleanup(temporary.cleanup)
        with patch("backend._read_env_file", return_value={}):
            self.app = create_app({"TESTING": True, "DATA_DIR": temporary.name, "SECRET_KEY": "synthetic-tests-only",
                                   "RAG_PROVIDER": "local", "EMBEDDING_PROVIDER": ""})
        self.client = self.app.test_client()
        self.client.post("/api/auth/register", json={"username": "overview-user", "password": "test-password-2026"})
        self.library = self.add_library(self.client, "Current library")
        self.add_document(self.client, self.library, "paper.md", "# A Research Paper\nAbstract: A supported overview.")
        self.session = self.client.post(f"/api/libraries/{self.library}/sessions", json={}).get_json()["session"]["id"]

    def add_library(self, client, name):
        return client.post("/api/libraries", json={"name": name}).get_json()["library"]["id"]

    def add_document(self, client, library, name, text):
        response = client.post(f"/api/libraries/{library}/documents", data={"files": [(io.BytesIO(text.encode()), name)]}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["documents"][0]["id"]

    def ask(self, question="这是什么文章"):
        response = self.client.post(f"/api/sessions/{self.session}/messages", json={"question": question})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def assert_saved(self, result):
        saved = self.client.get(f"/api/sessions/{self.session}").get_json()["messages"][-1]
        self.assertEqual(saved["notice"], result.get("notice"))
        for key in ("notice", "retrieval_mode", "generation_status", "failure_stage", "failure_code"):
            self.assertEqual(saved.get(key), result["assistant_message"].get(key))

    def test_overview_success_local_failure_and_clarification_notices_survive_reload(self):
        local = self.ask()
        self.assertEqual(local["assistant_message"]["mode"], "extractive")
        self.assert_saved(local)
        self.app.config["RAG_PROVIDER"] = "ollama"
        with patch("backend.rag._call_grounded_model", side_effect=grounded):
            generated = self.ask()
        self.assertEqual(generated["assistant_message"]["mode"], "llm")
        self.assert_saved(generated)
        with patch("backend.rag._call_grounded_model", side_effect=TimeoutError("PRIVATE_TEST_SECRET")):
            failed = self.ask()
        self.assert_saved(failed)
        self.assertEqual(failed["assistant_message"]["failure_stage"], "generation")
        with patch("backend.rag._call_grounded_model", return_value={"insufficient": False, "claims": []}):
            invalid = self.ask()
        self.assert_saved(invalid)
        self.assertEqual(invalid["assistant_message"]["failure_stage"], "validation")
        self.app.config["RAG_PROVIDER"] = "local"
        self.add_document(self.client, self.library, "other.md", "# Another paper\nDifferent source.")
        clarification = self.ask()
        self.assertEqual(clarification["assistant_message"]["mode"], "refusal")
        self.assert_saved(clarification)

    def test_overview_receives_only_current_library_and_account_documents(self):
        other_library = self.add_library(self.client, "Other library")
        self.add_document(self.client, other_library, "secret.md", "OTHER_LIBRARY_PRIVATE_MARKER")
        other = self.app.test_client()
        other.post("/api/auth/register", json={"username": "other-overview-user", "password": "test-password-2026"})
        other_library = self.add_library(other, "Other user library")
        self.add_document(other, other_library, "secret.md", "OTHER_USER_PRIVATE_MARKER")
        self.app.config["RAG_PROVIDER"] = "ollama"
        with patch("backend.rag._call_grounded_model", side_effect=grounded) as model:
            response = self.ask()
        self.assertEqual(response["assistant_message"]["mode"], "llm")
        self.assertNotIn("PRIVATE_MARKER", str(model.call_args.args[:3]))
        self.assertNotIn("PRIVATE_MARKER", json.dumps(response))
        self.assertEqual(other.get(f"/api/sessions/{self.session}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
