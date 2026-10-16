"""Integration acceptance tests. No real model request and no user data used.

Run from project root: python -m unittest discover -s tests -v
"""
from __future__ import annotations

from contextlib import closing
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import create_app
from backend.vector_store import RankedChunk


def text_pdf(text="Project Lumen deadline is 14 November 2026."):
    """Build one text page with correct offsets, without an extra PDF writer dep."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, item in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode() + item + b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(payload)


class AppAcceptanceTests(unittest.TestCase):
    password = "test-pass-2026"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="zhiwei-test-")
        self.addCleanup(self.directory.cleanup)
        self.config = {
            "TESTING": True,
            "DATA_DIR": self.directory.name,
            "SECRET_KEY": "test-only-secret",
            "RAG_PROVIDER": "local",
            "EMBEDDING_PROVIDER": "",
            "AI_API_KEY": "",
            "AI_BASE_URL": "",
            "AI_MODEL": "",
        }
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.register(self.client, "student_a")
        self.library_id = self.library(self.client, "软件工程资料")

    def register(self, client, username):
        response = client.post("/api/auth/register", json={"username": username, "password": self.password})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["user"]["id"]

    def library(self, client, name):
        response = client.post("/api/libraries", json={"name": name})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["library"]["id"]

    def chat(self, client=None, library_id=None):
        client = client or self.client
        library_id = library_id or self.library_id
        response = client.post(f"/api/libraries/{library_id}/sessions", json={"title": "验收会话"})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["session"]["id"]

    def upload(self, files, client=None, library_id=None):
        client = client or self.client
        library_id = library_id or self.library_id
        parts = [(io.BytesIO(content.encode("utf-8") if isinstance(content, str) else content), name)
                 for name, content in files]
        return client.post(f"/api/libraries/{library_id}/documents", data={"files": parts},
                           content_type="multipart/form-data")

    def ask(self, session_id, question, client=None):
        return (client or self.client).post(f"/api/sessions/{session_id}/messages", json={"question": question})

    def upload_fact(self):
        content = "海棠实验采用蓝色滤镜。\n海棠实验的校准周期为七天。\n负责人在每周一记录校准结果。"
        response = self.upload([("实验笔记.md", content)])
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["documents"][0]["id"], content

    def configure_fake_model(self):
        # Exercise structured generation explicitly. This fixture retains its
        # local search adapter so no real embedding service is needed.
        self.app.config.update(RAG_PROVIDER="openai", AI_API_KEY="test-key-never-sent",
                               AI_BASE_URL="https://example.invalid/v1", AI_MODEL="test-model", AI_EMBED_MODEL="")

    def test_authentication_lifecycle_and_password_hash(self):
        self.assertEqual(self.client.get("/api/auth/me").get_json()["user"]["username"], "student_a")
        with closing(sqlite3.connect(self.app.config["DATABASE"])) as database:
            stored = database.execute("SELECT password_hash FROM users WHERE username='student_a'").fetchone()[0]
        self.assertNotEqual(stored, self.password)
        self.assertNotIn(self.password, stored)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/libraries").status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "student_a", "password": "wrong-password"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "student_a", "password": self.password}).status_code, 200)
        self.assertEqual(len(self.client.get("/api/libraries").get_json()["libraries"]), 1)

    def test_duplicate_username_and_invalid_registration(self):
        duplicate = self.client.post("/api/auth/register", json={"username": "STUDENT_A", "password": self.password})
        self.assertEqual(duplicate.status_code, 409)
        for username, password in [("x", self.password), ("bad space", self.password), ("new_user", "short")]:
            with self.subTest(username=username):
                response = self.client.post("/api/auth/register", json={"username": username, "password": password})
                self.assertEqual(response.status_code, 400)

    def test_rejects_invalid_json_and_empty_names(self):
        for route in ["/api/libraries", f"/api/libraries/{self.library_id}/sessions"]:
            with self.subTest(route=route):
                response = self.client.post(route, json=["unexpected", "array"])
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())
        self.assertEqual(self.client.post("/api/libraries", json={"name": "  "}).status_code, 400)
        self.assertEqual(self.client.post("/api/libraries", json={"name": "x" * 81}).status_code, 400)

    def test_cross_origin_write_is_rejected(self):
        response = self.client.post("/api/libraries", json={"name": "不应创建"}, headers={"Origin": "https://untrusted.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(self.client.get("/api/libraries").get_json()["libraries"]), 1)

    def test_batch_upload_pdf_markdown_and_text_preserves_sources(self):
        response = self.upload([
            ("里程碑.pdf", text_pdf()),
            ("课程.md", "# 验收\n每个 Must 故事包含正常、边界、异常场景。"),
            ("计划.txt", "青竹计划的演示口令是星河二十七。"),
        ])
        self.assertEqual(response.status_code, 201, response.get_json())
        result = response.get_json()
        self.assertEqual(len(result["documents"]), 3, result)
        self.assertEqual(result["errors"], [])
        for document in result["documents"]:
            self.assertGreater(document["chunk_count"], 0)
            chunks = self.client.get(f"/api/documents/{document['id']}/chunks").get_json()["chunks"]
            self.assertTrue(all(chunk["content"].strip() for chunk in chunks))
            if document["name"].endswith(".pdf"):
                self.assertEqual(chunks[0]["page"], 1)
                self.assertIn("14 November 2026", chunks[0]["content"])
            downloaded = self.client.get(f"/api/documents/{document['id']}/download")
            self.assertEqual(downloaded.status_code, 200)
            downloaded.close()

    def test_mixed_batch_reports_each_failure_and_keeps_valid_document(self):
        response = self.upload([
            ("有效.txt", "有效资料记载验收日期为十一月六日。"),
            ("损坏.pdf", b"%PDF-1.4 broken data"),
            ("空.txt", b""),
            ("空白.md", " \n\t\n"),
            ("不支持.docx", b"not supported"),
            ("二进制.txt", b"\x00\x01\x02binary"),
        ])
        self.assertEqual(response.status_code, 201, response.get_json())
        result = response.get_json()
        self.assertEqual([document["name"] for document in result["documents"]], ["有效.txt"])
        self.assertEqual(len(result["errors"]), 5)
        self.assertTrue(all(error["error"] for error in result["errors"]))
        self.assertEqual(len(list((Path(self.directory.name) / "uploads").iterdir())), 1)

    def test_all_invalid_files_and_empty_batch_create_no_documents(self):
        response = self.upload([("空白.txt", " \n "), ("格式.exe", b"binary")])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["documents"], [])
        self.assertEqual(self.upload([]).status_code, 400)
        self.assertEqual(self.client.get(f"/api/libraries/{self.library_id}/documents").get_json()["documents"], [])
        with closing(sqlite3.connect(self.app.config["DATABASE"])) as database:
            self.assertEqual(database.execute("SELECT count(*) FROM chunks").fetchone()[0], 0)

    def test_batch_limit_and_oversized_file(self):
        response = self.upload([(f"note-{index}.txt", f"Document number {index}") for index in range(11)])
        self.assertEqual(response.status_code, 400)
        with patch("backend.MAX_FILE_BYTES", 64), patch("backend.documents.MAX_FILE_BYTES", 64):
            response = self.upload([("oversized.txt", b"x" * 65)])
        self.assertEqual(response.status_code, 400)
        self.assertIn("20 MB", response.get_json()["errors"][0]["error"])
        self.assertEqual(self.client.get(f"/api/libraries/{self.library_id}/documents").get_json()["documents"], [])

    def test_duplicate_content_does_not_duplicate_chunks(self):
        document_id, content = self.upload_fact()
        first = self.client.get(f"/api/documents/{document_id}/chunks").get_json()["chunks"]
        duplicate = self.upload([("另一文件名.txt", content)])
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("内容相同", duplicate.get_json()["errors"][0]["error"])
        after = self.client.get(f"/api/documents/{document_id}/chunks").get_json()["chunks"]
        self.assertEqual(first, after)
        self.assertEqual(len(self.client.get(f"/api/libraries/{self.library_id}/documents").get_json()["documents"]), 1)

    def test_text_encoding_and_filename_are_handled(self):
        response = self.upload([("../../课程笔记.TXT", "校准周期为七天。".encode("gb18030"))])
        self.assertEqual(response.status_code, 201, response.get_json())
        document = response.get_json()["documents"][0]
        self.assertEqual(document["name"], "课程笔记.TXT")
        chunks = self.client.get(f"/api/documents/{document['id']}/chunks").get_json()["chunks"]
        self.assertIn("七天", chunks[0]["content"])

    def test_citations_are_real_current_library_chunks_and_original_is_downloadable(self):
        document_id, source = self.upload_fact()
        response = self.ask(self.chat(), "海棠实验的校准周期是什么？")
        self.assertEqual(response.status_code, 200)
        answer = response.get_json()["assistant_message"]
        self.assertEqual(answer["mode"], "extractive")
        self.assertIn("七天", answer["content"])
        self.assertTrue(answer["citations"])
        chunks = {chunk["id"]: chunk for chunk in self.client.get(f"/api/documents/{document_id}/chunks").get_json()["chunks"]}
        for citation in answer["citations"]:
            self.assertEqual(citation["document_id"], document_id)
            self.assertEqual(citation["content"], chunks[citation["chunk_id"]]["content"])
            self.assertIn(citation["content"], source)
            self.assertTrue(citation["available"])
            self.assertIn(f"[{citation['index']}]", answer["content"])
        downloaded = self.client.get(f"/api/documents/{document_id}/download")
        self.assertEqual(downloaded.data.decode("utf-8"), source)
        downloaded.close()

    def test_empty_library_and_unrelated_questions_refuse_without_model_call(self):
        self.configure_fake_model()
        with patch("backend.rag._call_grounded_model") as model:
            response = self.ask(self.chat(), "火星的直径是多少？")
            answer = response.get_json()["assistant_message"]
            self.assertEqual(answer["mode"], "refusal")
            self.assertEqual(answer["citations"], [])
            self.upload_fact()
            response = self.ask(self.chat(), "Quantum cryptocurrency pineapple astronomy")
            self.assertEqual(response.get_json()["assistant_message"]["mode"], "refusal")
            model.assert_not_called()

    def test_multiturn_messages_are_ordered_and_followup_uses_context(self):
        self.upload_fact()
        session_id = self.chat()
        self.assertEqual(self.ask(session_id, "海棠实验采用什么滤镜？").status_code, 200)
        followup = self.ask(session_id, "它的校准周期呢？").get_json()["assistant_message"]
        self.assertIn("七天", followup["content"])
        self.assertTrue(followup["citations"])
        detail = self.client.get(f"/api/sessions/{session_id}").get_json()
        self.assertEqual([message["role"] for message in detail["messages"]], ["user", "assistant", "user", "assistant"])
        self.assertEqual(detail["session"]["title"], "海棠实验采用什么滤镜？")

    def test_empty_and_oversized_question_leave_history_empty(self):
        session_id = self.chat()
        for question in ["  ", "x" * 2001]:
            self.assertEqual(self.ask(session_id, question).status_code, 400)
        self.assertEqual(self.client.get(f"/api/sessions/{session_id}").get_json()["messages"], [])

    def test_different_libraries_do_not_share_sources_or_session_lists(self):
        self.upload_fact()
        first_session = self.chat()
        second_library = self.library(self.client, "另一门课")
        second_session = self.chat(library_id=second_library)
        result = self.ask(second_session, "海棠实验的校准周期是什么？").get_json()["assistant_message"]
        self.assertEqual(result["mode"], "refusal")
        self.assertEqual(result["citations"], [])
        self.assertEqual([item["id"] for item in self.client.get(f"/api/libraries/{second_library}/sessions").get_json()["sessions"]], [second_session])
        self.assertEqual([item["id"] for item in self.client.get(f"/api/libraries/{self.library_id}/sessions").get_json()["sessions"]], [first_session])

    def test_other_user_cannot_read_write_or_delete_owned_resources(self):
        document_id, _ = self.upload_fact()
        session_id = self.chat()
        self.ask(session_id, "海棠实验的校准周期是什么？")
        other = self.app.test_client()
        self.register(other, "student_b")
        self.assertEqual(other.get("/api/libraries").get_json()["libraries"], [])
        operations = [
            ("get", f"/api/libraries/{self.library_id}/documents", {}),
            ("get", f"/api/libraries/{self.library_id}/sessions", {}),
            ("post", f"/api/libraries/{self.library_id}/sessions", {"json": {}}),
            ("post", f"/api/libraries/{self.library_id}/seed", {}),
            ("delete", f"/api/libraries/{self.library_id}", {}),
            ("get", f"/api/documents/{document_id}/chunks", {}),
            ("get", f"/api/documents/{document_id}/download", {}),
            ("delete", f"/api/documents/{document_id}", {}),
            ("get", f"/api/sessions/{session_id}", {}),
            ("delete", f"/api/sessions/{session_id}", {}),
            ("post", f"/api/sessions/{session_id}/messages", {"json": {"question": "海棠实验"}}),
        ]
        for method, route, kwargs in operations:
            with self.subTest(method=method, route=route):
                response = getattr(other, method)(route, **kwargs)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("七天", response.get_data(as_text=True))
        self.assertEqual(self.upload([("入侵.txt", "应被拒绝")], client=other).status_code, 404)
        self.assertEqual(self.client.get(f"/api/documents/{document_id}/chunks").status_code, 200)
        self.assertEqual(len(self.client.get(f"/api/sessions/{session_id}").get_json()["messages"]), 2)

    def test_data_persists_when_app_is_recreated(self):
        document_id, _ = self.upload_fact()
        session_id = self.chat()
        self.ask(session_id, "海棠实验的校准周期是什么？")
        reopened = create_app(self.config).test_client()
        login = reopened.post("/api/auth/login", json={"username": "student_a", "password": self.password})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(reopened.get("/api/libraries").get_json()["libraries"][0]["id"], self.library_id)
        self.assertEqual(reopened.get(f"/api/documents/{document_id}/chunks").status_code, 200)
        messages = reopened.get(f"/api/sessions/{session_id}").get_json()["messages"]
        self.assertEqual(len(messages), 2)
        self.assertIn("七天", messages[1]["content"])

    def test_model_success_has_only_current_library_evidence(self):
        document_id, _ = self.upload_fact()
        unrelated = self.library(self.client, "私人独立资料")
        self.upload([("secret.txt", "另一资料库的 secret token 是 orchid-928。")], library_id=unrelated)
        self.configure_fake_model()
        structured = {"insufficient": False, "claims": [{"text": "海棠实验的校准周期为七天。",
                      "evidence": [{"index": 1, "quote": "海棠实验的校准周期为七天。"}]}]}
        with patch("backend.rag._call_grounded_model", return_value=structured) as model:
            response = self.ask(self.chat(), "海棠实验的校准周期是什么？")
        result = response.get_json()["assistant_message"]
        self.assertEqual(result["mode"], "llm")
        self.assertEqual(result["generation_status"], "validated")
        self.assertEqual(result["citations"][0]["quotes"], ["海棠实验的校准周期为七天。"])
        sent_citations = model.call_args.args[1]
        self.assertTrue(sent_citations)
        self.assertTrue(all(citation["document_id"] == document_id for citation in sent_citations))
        self.assertNotIn("orchid-928", str(model.call_args.args[:3]))

    def test_model_timeout_and_invalid_citations_fall_back_without_error_leak(self):
        self.upload_fact()
        self.configure_fake_model()
        for behavior in ["timeout", "invalid", "missing"]:
            with self.subTest(behavior=behavior):
                evidence = [{"index": 999, "quote": "海棠实验的校准周期为七天。"}] if behavior == "invalid" else []
                kwargs = ({"side_effect": TimeoutError("private provider secret should not leak")} if behavior == "timeout"
                          else {"return_value": {"insufficient": False, "claims": [{"text": "校准周期为七天。", "evidence": evidence}]}})
                with patch("backend.rag._call_grounded_model", **kwargs):
                    response = self.ask(self.chat(), "海棠实验的校准周期是什么？")
                self.assertEqual(response.status_code, 200)
                result = response.get_json()
                self.assertEqual(result["assistant_message"]["mode"], "extractive-fallback")
                self.assertTrue(result["assistant_message"]["citations"])
                self.assertIn("七天", result["assistant_message"]["content"])
                self.assertTrue(result["notice"])
                self.assertNotIn("private provider secret", str(result))

    def test_incomplete_model_configuration_stays_local(self):
        self.upload_fact()
        self.app.config.update(RAG_PROVIDER="openai", AI_API_KEY="test-key-never-sent", AI_BASE_URL="", AI_MODEL="")
        with patch("backend.providers._request_json") as model:
            result = self.ask(self.chat(), "海棠实验的校准周期是什么？").get_json()
            answer = result["assistant_message"]
            self.assertEqual(answer["mode"], "extractive-fallback")
            self.assertIn("七天", answer["content"])
            self.assertEqual(answer["failure_stage"], "generation")
            self.assertEqual(answer["failure_code"], "generation_failed")
            self.assertIn("生成请求失败", result["notice"])
            self.assertEqual(answer["notice"], result["notice"])
            model.assert_not_called()
        self.assertFalse(self.client.get("/api/config").get_json()["provider_configured"])

    def test_explicit_local_mode_never_uses_leftover_provider_settings(self):
        # Switching to local must stop both uploads and questions from sending
        # document contents to a previously configured provider.
        self.app.config.update(AI_API_KEY="leftover-key-never-sent", AI_BASE_URL="https://example.invalid/v1",
                               AI_MODEL="leftover-chat", AI_EMBED_MODEL="leftover-embedding",
                               AI_EMBED_API_KEY="leftover-embedding-key", AI_EMBED_BASE_URL="https://embedding.invalid/v1",
                               CHAT_PROVIDER_CONFIGURED=True, CHAT_JSON_PROVIDER=lambda *_args: {"ok": True})
        for embedding_provider in ("openai", "ollama"):
            with self.subTest(embedding_provider=embedding_provider):
                self.app.config["EMBEDDING_PROVIDER"] = embedding_provider
                library_id = self.library(self.client, "离线验证 " + embedding_provider)
                with patch("backend.providers.embed_texts") as embedding, \
                        patch("backend.providers.chat_json") as generation, \
                        patch("backend.providers._request_json") as http:
                    uploaded = self.upload([("离线笔记.txt", "离线实验的校准周期为七天。")], library_id=library_id)
                    self.assertEqual(uploaded.status_code, 201)
                    self.assertEqual(self.client.post(f"/api/libraries/{library_id}/reindex").status_code, 200)
                    session_id = self.chat(library_id=library_id)
                    for question in ("离线实验的校准周期？", "它的周期呢？"):
                        answer = self.ask(session_id, question).get_json()["assistant_message"]
                        self.assertEqual(answer["mode"], "extractive")
                        self.assertEqual(answer["retrieval_mode"], "lexical")
                        self.assertIn("七天", answer["content"])
                    status = self.client.post("/api/models/check").get_json()
                    self.assertEqual(status["mode"], "extractive")
                    self.assertEqual(status["embedding"], "local-tfidf")
                    self.assertFalse(status["provider_configured"])
                    self.assertFalse(status["rag"]["configured"])
                    self.assertFalse(status["rag"]["connected"])
                    embedding.assert_not_called()
                    generation.assert_not_called()
                    http.assert_not_called()

    def test_uploaded_term_vectors_persist_and_reopen(self):
        document_id, _ = self.upload_fact()
        with closing(sqlite3.connect(self.app.config["DATABASE"])) as database:
            rows = database.execute("SELECT token_counts_json FROM chunks WHERE document_id=?", (document_id,)).fetchall()
        self.assertTrue(rows)
        counts = json.loads(rows[0][0])
        self.assertGreater(counts.get("海棠", 0), 0)
        self.assertTrue(all(isinstance(value, int) and value > 0 for value in counts.values()))
        reopened = create_app(self.config).test_client()
        reopened.post("/api/auth/login", json={"username": "student_a", "password": self.password})
        session_id = self.chat(client=reopened)
        answer = self.ask(session_id, "海棠实验的校准周期是什么？", client=reopened).get_json()["assistant_message"]
        self.assertEqual(answer["mode"], "extractive")
        self.assertIn("七天", answer["content"])

    def test_vector_store_can_be_replaced_without_cross_library_rows(self):
        document_id, _ = self.upload_fact()
        other_library = self.library(self.client, "向量隔离库")
        self.upload([("另一资料.txt", "松柏计划使用橙色滤镜。")], library_id=other_library)

        class RecordingStore:
            def __init__(self):
                self.rows = []
                self.query = None

            def search(self, query, chunks, limit=5):
                self.query = query
                self.rows = list(chunks)
                return [RankedChunk(self.rows[0], 0.99)] if self.rows else []

        store = RecordingStore()
        self.app.config["VECTOR_STORE"] = store
        answer = self.ask(self.chat(), "用可替换检索接口定位资料").get_json()["assistant_message"]
        self.assertEqual(store.query, "用可替换检索接口定位资料")
        self.assertTrue(store.rows)
        self.assertTrue(all(row["document_id"] == document_id for row in store.rows))
        self.assertEqual(answer["citations"][0]["document_id"], document_id)
        self.assertEqual(answer["mode"], "extractive")

    def test_vector_adapter_cannot_inject_out_of_scope_citations(self):
        self.upload_fact()
        other_library = self.library(self.client, "另一个库")
        other_document = self.upload([("保密.txt", "月桂代号的密码为不能泄露。")], library_id=other_library).get_json()["documents"][0]
        other_chunk = self.client.get(f"/api/documents/{other_document['id']}/chunks").get_json()["chunks"][0]

        class IncorrectStore:
            def search(self, query, chunks, limit=5):
                return [RankedChunk(other_chunk, 1.0)]

        self.app.config["VECTOR_STORE"] = IncorrectStore()
        answer = self.ask(self.chat(), "找一下相关资料").get_json()["assistant_message"]
        self.assertEqual(answer["mode"], "refusal")
        self.assertEqual(answer["citations"], [])
        self.assertNotIn("不能泄露", answer["content"])

    def test_legacy_chunk_schema_migrates_without_losing_retrievability(self):
        document_id, _ = self.upload_fact()
        with closing(sqlite3.connect(self.app.config["DATABASE"])) as database:
            database.execute("ALTER TABLE chunks DROP COLUMN token_counts_json")
            database.commit()
        reopened_app = create_app(self.config)
        with closing(sqlite3.connect(reopened_app.config["DATABASE"])) as database:
            counts = database.execute("SELECT token_counts_json FROM chunks WHERE document_id=?", (document_id,)).fetchone()[0]
        self.assertEqual(json.loads(counts), {})
        reopened = reopened_app.test_client()
        reopened.post("/api/auth/login", json={"username": "student_a", "password": self.password})
        session_id = self.chat(client=reopened)
        answer = self.ask(session_id, "海棠实验的校准周期是什么？", client=reopened).get_json()["assistant_message"]
        self.assertEqual(answer["mode"], "extractive")
        self.assertIn("七天", answer["content"])

    def test_document_delete_removes_retrieval_and_history_marks_source_missing(self):
        document_id, _ = self.upload_fact()
        session_id = self.chat()
        self.ask(session_id, "海棠实验的校准周期是什么？")
        self.assertEqual(self.client.delete(f"/api/documents/{document_id}").status_code, 200)
        history = self.client.get(f"/api/sessions/{session_id}").get_json()["messages"]
        self.assertFalse(history[1]["citations"][0]["available"])
        self.assertEqual(self.client.get(f"/api/documents/{document_id}/chunks").status_code, 404)
        self.assertEqual(self.ask(self.chat(), "海棠实验的校准周期是什么？").get_json()["assistant_message"]["mode"], "refusal")

    def test_deleted_document_id_is_not_reused_for_historical_citation(self):
        document_id, _ = self.upload_fact()
        session_id = self.chat()
        self.ask(session_id, "海棠实验的校准周期是什么？")
        self.client.delete(f"/api/documents/{document_id}")
        uploaded = self.upload([("完全不同.txt", "银杏计划位于月球模拟基地。")]).get_json()["documents"][0]
        self.assertNotEqual(uploaded["id"], document_id)
        citations = self.client.get(f"/api/sessions/{session_id}").get_json()["messages"][1]["citations"]
        self.assertFalse(citations[0]["available"])

    def test_delete_library_cascades_documents_chunks_sessions_and_files(self):
        self.upload_fact()
        session_id = self.chat()
        self.ask(session_id, "海棠实验的校准周期是什么？")
        self.assertEqual(self.client.delete(f"/api/libraries/{self.library_id}").status_code, 200)
        with closing(sqlite3.connect(self.app.config["DATABASE"])) as database:
            for table in ["libraries", "documents", "chunks", "chat_sessions", "messages"]:
                self.assertEqual(database.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0, table)
        self.assertEqual(list((Path(self.directory.name) / "uploads").iterdir()), [])
        self.assertEqual(self.client.get(f"/api/sessions/{session_id}").status_code, 404)

    def test_sample_import_is_idempotent(self):
        first = self.client.post(f"/api/libraries/{self.library_id}/seed").get_json()
        self.assertGreaterEqual(first["imported"], 3)
        self.assertEqual(first["errors"], [])
        second = self.client.post(f"/api/libraries/{self.library_id}/seed").get_json()
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["skipped"], first["imported"])
        self.assertEqual(second["errors"], [])


if __name__ == "__main__":
    unittest.main()
