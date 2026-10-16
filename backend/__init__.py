"""Flask application factory for 知微 · 个人知识库."""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

from .documents import DocumentError, MAX_FILE_BYTES, parse_document, safe_display_name
from .rag import answer_question, model_configured
from .vector_store import LocalTfidfStore, encode_token_counts

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS libraries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    name TEXT NOT NULL, size INTEGER NOT NULL, stored_name TEXT NOT NULL,
    sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(library_id, sha256)
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content TEXT NOT NULL, page INTEGER, start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL, ordinal INTEGER NOT NULL,
    token_counts_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    title TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
    citations_json TEXT NOT NULL DEFAULT '[]', mode TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_libraries_owner ON libraries(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_library ON documents(library_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_sessions_library ON chat_sessions(library_id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_env_file(path: Path) -> dict:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def create_app(test_config: dict | None = None) -> Flask:
    env = _read_env_file(PROJECT_ROOT / ".env")
    app = Flask(__name__, static_folder=None)
    app.config.from_mapping(
        DATA_DIR=os.environ.get("DATA_DIR", env.get("DATA_DIR", str(PROJECT_ROOT / "data"))),
        SECRET_KEY=os.environ.get("SECRET_KEY", env.get("SECRET_KEY", "")),
        AI_API_KEY=os.environ.get("AI_API_KEY", env.get("AI_API_KEY", "")),
        AI_BASE_URL=os.environ.get("AI_BASE_URL", env.get("AI_BASE_URL", "")),
        AI_MODEL=os.environ.get("AI_MODEL", env.get("AI_MODEL", "")),
        VECTOR_STORE=None,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False,  # local HTTP; turn on when deploying behind HTTPS
        MAX_CONTENT_LENGTH=MAX_FILE_BYTES * 10 + 1024 * 1024,
        JSON_SORT_KEYS=False,
    )
    if test_config:
        app.config.update(test_config)
    if app.config["VECTOR_STORE"] is None:
        app.config["VECTOR_STORE"] = LocalTfidfStore()
    if not callable(getattr(app.config["VECTOR_STORE"], "search", None)):
        raise ValueError("VECTOR_STORE must implement search(query, chunks, limit).")
    data_dir = Path(app.config["DATA_DIR"]).expanduser()
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    upload_dir = data_dir / "uploads"
    upload_dir.mkdir(exist_ok=True)
    app.config["DATA_DIR"] = str(data_dir)
    app.config["DATABASE"] = str(data_dir / "zhiwei.sqlite3")
    app.config["UPLOAD_DIR"] = str(upload_dir)
    if not app.config["SECRET_KEY"]:
        key_path = data_dir / ".session-secret"
        if not key_path.exists():
            try:
                with key_path.open("x", encoding="ascii") as file:
                    file.write(secrets.token_hex(32))
            except FileExistsError:
                pass
        app.config["SECRET_KEY"] = key_path.read_text(encoding="ascii").strip()
    app.json.ensure_ascii = False
    with closing(sqlite3.connect(app.config["DATABASE"])) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(chunks)")}
        if "token_counts_json" not in columns:
            connection.execute("ALTER TABLE chunks ADD COLUMN token_counts_json TEXT NOT NULL DEFAULT '{}'")
        connection.commit()

    def database():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"], timeout=20)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys=ON")
        return g.db

    @app.teardown_appcontext
    def close_database(_error):
        connection = g.pop("db", None)
        if connection:
            connection.close()

    @app.before_request
    def verify_origin():
        # Browsers send Origin on cross-origin writes; protect cookie-auth APIs.
        if request.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                raise ApiError("请求来源不受信任，请在本地应用页面操作。", 403)
        user_id = session.get("user_id")
        g.user = database().execute("SELECT id, username FROM users WHERE id=?", (user_id,)).fetchone() if user_id else None

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ApiError)
    def api_error(error):
        return jsonify(error=error.message), error.status

    @app.errorhandler(RequestEntityTooLarge)
    def oversized(_error):
        return jsonify(error="上传内容过大。单文件不超过 20 MB，每批最多 10 个。"), 413

    @app.errorhandler(HTTPException)
    def http_error(error):
        descriptions = {400: "请求格式不正确。", 404: "资源不存在或你没有访问权限。", 405: "不支持该请求方式。", 415: "请发送 JSON 格式的请求。"}
        return jsonify(error=descriptions.get(error.code, "请求未能完成。")), error.code

    def authenticated(function):
        @functools.wraps(function)
        def decorated(*args, **kwargs):
            if g.user is None:
                raise ApiError("请先登录。", 401)
            return function(*args, **kwargs)
        return decorated

    def body() -> dict:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ApiError("请发送有效的 JSON 对象。")
        return data

    def text_field(data, name, maximum, default=""):
        value = data.get(name, default)
        if not isinstance(value, str):
            raise ApiError(f"{name} 必须为文本。")
        value = value.strip()
        if len(value) > maximum:
            raise ApiError(f"{name} 不能超过 {maximum} 个字符。")
        return value

    def owned_library(library_id):
        row = database().execute("SELECT * FROM libraries WHERE id=? AND user_id=?", (library_id, g.user["id"])).fetchone()
        if row is None:
            raise ApiError("知识库不存在或你没有访问权限。", 404)
        return row

    def owned_document(document_id):
        row = database().execute("""SELECT d.* FROM documents d JOIN libraries l ON d.library_id=l.id
                                  WHERE d.id=? AND l.user_id=?""", (document_id, g.user["id"])).fetchone()
        if row is None:
            raise ApiError("文档不存在或你没有访问权限。", 404)
        return row

    def owned_session(session_id):
        row = database().execute("""SELECT s.* FROM chat_sessions s JOIN libraries l ON s.library_id=l.id
                                  WHERE s.id=? AND l.user_id=?""", (session_id, g.user["id"])).fetchone()
        if row is None:
            raise ApiError("对话不存在或你没有访问权限。", 404)
        return row

    def library_json(row):
        result = {key: row[key] for key in ("id", "name", "description", "created_at")}
        connection = database()
        result["document_count"] = connection.execute("SELECT count(*) FROM documents WHERE library_id=?", (row["id"],)).fetchone()[0]
        result["chunk_count"] = connection.execute("SELECT count(*) FROM chunks c JOIN documents d ON c.document_id=d.id WHERE d.library_id=?", (row["id"],)).fetchone()[0]
        result["session_count"] = connection.execute("SELECT count(*) FROM chat_sessions WHERE library_id=?", (row["id"],)).fetchone()[0]
        return result

    def document_json(row):
        result = {key: row[key] for key in ("id", "name", "size", "created_at")}
        result["chunk_count"] = database().execute("SELECT count(*) FROM chunks WHERE document_id=?", (row["id"],)).fetchone()[0]
        return result

    def message_json(row):
        result = {key: row[key] for key in ("id", "role", "content", "mode", "created_at")}
        result["citations"] = json.loads(row["citations_json"])
        for citation in result["citations"]:
            exists = database().execute("SELECT id FROM documents WHERE id=?", (citation["document_id"],)).fetchone()
            citation["available"] = exists is not None
        return result

    def add_document(library_id, raw_name, payload):
        name = safe_display_name(raw_name)
        chunks = parse_document(name, payload)
        digest = hashlib.sha256(payload).hexdigest()
        connection = database()
        if connection.execute("SELECT id FROM documents WHERE library_id=? AND sha256=?", (library_id, digest)).fetchone():
            raise DocumentError("当前知识库已包含内容相同的文档，无需重复上传。")
        stored_name = uuid.uuid4().hex + Path(name).suffix.lower()
        target = upload_dir / stored_name
        target.write_bytes(payload)
        try:
            with connection:
                cursor = connection.execute("INSERT INTO documents(library_id,name,size,stored_name,sha256,created_at) VALUES(?,?,?,?,?,?)",
                                            (library_id, name, len(payload), stored_name, digest, now()))
                document_id = cursor.lastrowid
                connection.executemany("INSERT INTO chunks(document_id,content,page,start_line,end_line,ordinal,token_counts_json) VALUES(?,?,?,?,?,?,?)",
                                       [(document_id, chunk["content"], chunk["page"], chunk["start_line"], chunk["end_line"], chunk["ordinal"], encode_token_counts(chunk["content"])) for chunk in chunks])
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return document_json(connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone())

    @app.get("/api/health")
    def health():
        configured = model_configured(app.config)
        return jsonify(status="ok", mode="llm" if configured else "extractive", model=app.config["AI_MODEL"] if configured else None, embedding="local-tfidf")

    @app.get("/api/config")
    def config():
        configured = model_configured(app.config)
        return jsonify(mode="llm" if configured else "extractive", model=app.config["AI_MODEL"] if configured else None,
                       embedding="local-tfidf", provider_configured=configured, max_file_size_mb=20,
                       max_files_per_batch=10, supported_extensions=[".pdf", ".md", ".txt"])

    @app.get("/api/auth/me")
    @authenticated
    def me():
        return jsonify(user=dict(g.user))

    @app.post("/api/auth/register")
    def register():
        data = body()
        username = text_field(data, "username", 32)
        password = data.get("password")
        if len(username) < 2 or not re.fullmatch(r"[\w\u3400-\u9fff.-]+", username):
            raise ApiError("用户名需为 2–32 个中文、字母、数字、下划线、点或短横线。")
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise ApiError("密码需为 8–128 个字符。")
        connection = database()
        try:
            with connection:
                cursor = connection.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)", (username, generate_password_hash(password), now()))
                user_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            raise ApiError("用户名已存在，请换一个名字或直接登录。", 409)
        session.clear()
        session["user_id"] = user_id
        return jsonify(user={"id": user_id, "username": username}), 201

    @app.post("/api/auth/login")
    def login():
        data = body()
        username = text_field(data, "username", 32)
        password = data.get("password")
        if not isinstance(password, str) or len(password) > 128:
            raise ApiError("用户名或密码错误。", 401)
        row = database().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row is None or not check_password_hash(row["password_hash"], password):
            raise ApiError("用户名或密码错误。", 401)
        session.clear()
        session["user_id"] = row["id"]
        return jsonify(user={"id": row["id"], "username": row["username"]})

    @app.post("/api/auth/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.route("/api/libraries", methods=["GET", "POST"])
    @authenticated
    def libraries():
        connection = database()
        if request.method == "GET":
            rows = connection.execute("SELECT * FROM libraries WHERE user_id=? ORDER BY id DESC", (g.user["id"],)).fetchall()
            return jsonify(libraries=[library_json(row) for row in rows])
        data = body()
        name, description = text_field(data, "name", 80), text_field(data, "description", 500)
        if not name:
            raise ApiError("请填写知识库名称。")
        with connection:
            cursor = connection.execute("INSERT INTO libraries(user_id,name,description,created_at) VALUES(?,?,?,?)", (g.user["id"], name, description, now()))
        return jsonify(library=library_json(owned_library(cursor.lastrowid))), 201

    @app.delete("/api/libraries/<int:library_id>")
    @authenticated
    def delete_library(library_id):
        owned_library(library_id)
        connection = database()
        paths = [upload_dir / row["stored_name"] for row in connection.execute("SELECT stored_name FROM documents WHERE library_id=?", (library_id,))]
        with connection:
            connection.execute("DELETE FROM libraries WHERE id=?", (library_id,))
        for path in paths:
            path.unlink(missing_ok=True)
        return jsonify(ok=True)

    @app.route("/api/libraries/<int:library_id>/documents", methods=["GET", "POST"])
    @authenticated
    def documents(library_id):
        owned_library(library_id)
        if request.method == "GET":
            rows = database().execute("SELECT * FROM documents WHERE library_id=? ORDER BY id DESC", (library_id,)).fetchall()
            return jsonify(documents=[document_json(row) for row in rows])
        files = request.files.getlist("files")
        if not files:
            # Accept one-file clients without changing the documented batch field.
            files = request.files.getlist("file")
        if not files:
            raise ApiError("请选择要上传的文件。")
        if len(files) > 10:
            raise ApiError("每批最多上传 10 个文件。")
        uploaded, errors = [], []
        for file in files:
            name = safe_display_name(file.filename or "")
            try:
                payload = file.stream.read(MAX_FILE_BYTES + 1)
                uploaded.append(add_document(library_id, name, payload))
            except DocumentError as exc:
                errors.append({"name": name, "error": str(exc)})
        return jsonify(documents=uploaded, errors=errors), 201 if uploaded else 400

    @app.delete("/api/documents/<int:document_id>")
    @authenticated
    def delete_document(document_id):
        row = owned_document(document_id)
        with database() as connection:
            connection.execute("DELETE FROM documents WHERE id=?", (document_id,))
        (upload_dir / row["stored_name"]).unlink(missing_ok=True)
        return jsonify(ok=True)

    @app.get("/api/documents/<int:document_id>/chunks")
    @authenticated
    def document_chunks(document_id):
        row = owned_document(document_id)
        chunks = [dict(chunk) for chunk in database().execute("SELECT id,content,page,start_line,end_line,ordinal FROM chunks WHERE document_id=? ORDER BY ordinal", (document_id,))]
        return jsonify(document=document_json(row), chunks=chunks)

    @app.get("/api/documents/<int:document_id>/download")
    @authenticated
    def download_document(document_id):
        row = owned_document(document_id)
        return send_from_directory(upload_dir, row["stored_name"], as_attachment=True, download_name=row["name"])

    @app.post("/api/libraries/<int:library_id>/seed")
    @authenticated
    def seed(library_id):
        owned_library(library_id)
        imported, skipped, errors = [], [], []
        for path in sorted((PROJECT_ROOT / "sample_docs").glob("*")):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".pdf"}:
                try:
                    imported.append(add_document(library_id, path.name, path.read_bytes()))
                except DocumentError as exc:
                    if "内容相同" in str(exc):
                        skipped.append(path.name)
                    else:
                        errors.append({"name": path.name, "error": str(exc)})
        return jsonify(imported=len(imported), skipped=len(skipped), documents=imported, errors=errors)

    @app.route("/api/libraries/<int:library_id>/sessions", methods=["GET", "POST"])
    @authenticated
    def sessions(library_id):
        owned_library(library_id)
        connection = database()
        if request.method == "GET":
            rows = connection.execute("SELECT * FROM chat_sessions WHERE library_id=? ORDER BY id DESC", (library_id,)).fetchall()
            return jsonify(sessions=[dict(row) for row in rows])
        data = request.get_json(silent=True)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ApiError("请发送有效的 JSON 对象。")
        title = text_field(data, "title", 80, "新对话") or "新对话"
        with connection:
            cursor = connection.execute("INSERT INTO chat_sessions(library_id,title,created_at) VALUES(?,?,?)", (library_id, title, now()))
        return jsonify(session=dict(owned_session(cursor.lastrowid))), 201

    @app.route("/api/sessions/<int:session_id>", methods=["GET", "DELETE"])
    @authenticated
    def session_detail(session_id):
        row = owned_session(session_id)
        connection = database()
        if request.method == "DELETE":
            with connection:
                connection.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
            return jsonify(ok=True)
        messages = connection.execute("SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
        return jsonify(session=dict(row), messages=[message_json(item) for item in messages])

    @app.post("/api/sessions/<int:session_id>/messages")
    @authenticated
    def send_message(session_id):
        chat_session = owned_session(session_id)
        question = text_field(body(), "question", 2000)
        if not question:
            raise ApiError("请输入问题。")
        connection = database()
        history = [dict(row) for row in connection.execute("SELECT role,content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 12", (session_id,)).fetchall()][::-1]
        chunks = [dict(row) for row in connection.execute("""SELECT c.*, d.name AS document_name FROM chunks c
            JOIN documents d ON c.document_id=d.id WHERE d.library_id=? ORDER BY c.id""", (chat_session["library_id"],))]
        content, citations, mode, notice = answer_question(question, chunks, history, app.config)
        # The optional network call runs before beginning the write transaction.
        owned_session(session_id)
        with connection:
            user_cursor = connection.execute("INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)", (session_id, "user", question, now()))
            assistant_cursor = connection.execute("INSERT INTO messages(session_id,role,content,citations_json,mode,created_at) VALUES(?,?,?,?,?,?)",
                                                  (session_id, "assistant", content, json.dumps(citations, ensure_ascii=False), mode, now()))
            if not history:
                connection.execute("UPDATE chat_sessions SET title=? WHERE id=?", (question[:32], session_id))
        result = {"user_message": message_json(connection.execute("SELECT * FROM messages WHERE id=?", (user_cursor.lastrowid,)).fetchone()),
                  "assistant_message": message_json(connection.execute("SELECT * FROM messages WHERE id=?", (assistant_cursor.lastrowid,)).fetchone())}
        if notice:
            result["notice"] = notice
        return jsonify(result)

    @app.get("/")
    def index():
        return send_from_directory(PROJECT_ROOT / "frontend", "index.html")

    @app.get("/<path:filename>")
    def frontend_file(filename):
        if filename.startswith("api/"):
            raise ApiError("接口不存在。", 404)
        return send_from_directory(PROJECT_ROOT / "frontend", filename)

    return app
