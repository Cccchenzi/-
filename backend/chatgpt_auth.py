"""Official Sign in with ChatGPT for this local application.

Uses a new app-specific OAuth registration, never another application's tokens.
Reference: https://developers.openai.com/siwc/token-sharing-open-source/sign-in
"""
from __future__ import annotations

import base64
import ctypes
import functools
import hashlib
import html
import json
import logging
import math
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import jwt
from flask import Blueprint, g, has_request_context, jsonify, request, session

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
CALLBACK = "/auth/chatgpt/callback"


class ChatGPTError(ValueError):
    """Only safe, user-facing messages may be used as exception text."""


class InvalidGrant(ChatGPTError):
    """The server has positively identified an unusable renewable session."""


class CredentialLock:
    """Reentrant thread lock plus OS file lock, including across app processes."""
    def __init__(self, path):
        self.path = path
        self.thread_lock = threading.RLock()
        self.local = threading.local()

    def __enter__(self):
        self.thread_lock.acquire()
        depth = getattr(self.local, "depth", 0)
        if depth:
            self.local.depth = depth + 1
            return self
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            handle = os.fdopen(descriptor, "r+b", buffering=0)
            if os.fstat(descriptor).st_size == 0:
                handle.write(b"0")
            deadline = time.monotonic() + 90
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        handle.close()
                        raise ChatGPTError("授权正由另一个进程更新，请稍后重试。") from None
                    time.sleep(0.05)
            self.local.handle, self.local.depth = handle, 1
            return self
        except Exception:
            self.thread_lock.release()
            raise

    def __exit__(self, *_args):
        self.local.depth -= 1
        try:
            if not self.local.depth:
                handle = self.local.handle
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
        finally:
            self.thread_lock.release()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ChatGPTError("官方接口发生了意外重定向，请稍后重试。")


def _http(url, payload=None, bearer=None, form=False, stream=False):
    headers = {"Accept": "text/event-stream" if stream else "application/json"}
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        data = (urllib.parse.urlencode(payload).encode() if form else
                json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        response = urllib.request.build_opener(NoRedirect()).open(req, timeout=180 if stream else 30)
        if stream:
            return response
        with response:
            body = response.read(4_000_001)
        if len(body) > 4_000_000:
            raise ChatGPTError("官方接口响应过大。")
        return json.loads(body) if body else {}
    except ChatGPTError:
        raise
    except urllib.error.HTTPError as exc:
        if url == TOKEN:
            try:
                failure = json.loads(exc.read(65537))
                code = failure.get("error")
                code = code.get("code") if isinstance(code, dict) else code
                if code in {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"}:
                    raise InvalidGrant("ChatGPT 授权已失效，请重新连接。") from None
            except InvalidGrant:
                raise
            except Exception:
                pass
        if exc.code in (401, 403):
            raise ChatGPTError("ChatGPT 授权不可用，请重新授权并确认会员方案允许此应用。") from None
        if exc.code == 429:
            raise ChatGPTError("ChatGPT 会员用量达到限制，请稍后重试或在 ChatGPT 中查看额度。") from None
        raise ChatGPTError("ChatGPT 官方服务暂时不可用，请稍后重试；不会展示未校验的生成内容。") from None
    except Exception:
        raise ChatGPTError("无法连接 ChatGPT 官方服务，请检查网络后重试。") from None


def _protect(raw: bytes, decrypt=False) -> bytes:
    """Windows DPAPI binds this app's credential file to the Windows account."""
    if os.name != "nt":
        return raw

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)):
        raise ChatGPTError("Windows 无法安全保存或读取此应用的授权。请使用当前 Windows 账户重新授权。")
    try:
        return ctypes.string_at(destination.data, destination.size)
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree(ctypes.cast(destination.data, ctypes.c_void_p))


class CredentialStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = CredentialLock(self.directory / ".credentials.lock")
        host_file = self.directory / "host.json"
        with self.lock:
            if not host_file.exists():
                self._atomic(host_file, json.dumps({"host_id": "urn:uuid:" + str(uuid.uuid4())}).encode())
            self.host_id = json.loads(host_file.read_text("utf-8"))["host_id"]

    @staticmethod
    def _atomic(path, raw):
        temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
        try:
            descriptor = os.open(str(temporary), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def _path(self, user_id):
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ChatGPTError("请先登录知微。")
        return self.directory / f"user-{user_id}.bin"

    def read(self, user_id):
        path = self._path(user_id)
        if not path.exists():
            return {"profiles": [], "active": None, "epoch": 0}
        try:
            raw = path.read_bytes()
            if os.name == "nt":
                if not raw.startswith(b"DPAPI1\n"):
                    raise ChatGPTError("授权文件格式不匹配，请重新授权。")
                raw = _protect(raw[7:], decrypt=True)
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("profiles"), list) or any(
                not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("client_id"), str)
                for item in data["profiles"]):
                raise ValueError("structure")
            data.setdefault("epoch", 0)
            if not isinstance(data["epoch"], int) or isinstance(data["epoch"], bool):
                raise ValueError("epoch")
            return data
        except ChatGPTError:
            raise
        except Exception:
            raise ChatGPTError("此知微账户的授权文件无法读取，请重新授权。") from None

    def write(self, user_id, data):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if os.name == "nt":
            raw = b"DPAPI1\n" + _protect(raw)
        self._atomic(self._path(user_id), raw)


def _validate_identity(id_token, client_id, nonce=None, subject=None):
    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
            raise ValueError("algorithm")
        keys = _http(ISSUER + "/.well-known/jwks.json")["keys"]
        item = next(item for item in keys if item.get("kid") == header.get("kid"))
        key = jwt.PyJWK.from_dict(item).key
        claims = jwt.decode(id_token, key, algorithms=["RS256"], audience=client_id,
                            issuer=ISSUER, options={"require": ["exp", "iss", "aud", "sub"]}, leeway=15)
        if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ValueError("nonce")
        if not isinstance(claims["sub"], str) or not claims["sub"] or (subject is not None and claims["sub"] != subject):
            raise ValueError("subject")
        if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and claims.get("azp") != client_id:
            raise ValueError("authorized party")
        return claims
    except Exception:
        raise ChatGPTError("ChatGPT 返回的身份验证未通过；原有账号连接没有被替换。") from None


def _token_fields(response, existing=None):
    if not isinstance(response, dict):
        raise ChatGPTError("ChatGPT 未返回有效授权。")
    scope = response.get("scope", " ".join((existing or {}).get("scopes", [])))
    if not isinstance(scope, str):
        raise ChatGPTError("ChatGPT 返回的授权权限格式无效。")
    scopes = scope.split()
    access = response.get("access_token")
    expiry = response.get("expires_in", 0)
    token_type = response.get("token_type", "Bearer")
    if not isinstance(access, str) or not access or not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise ChatGPTError("ChatGPT 未返回有效授权。")
    if not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or not math.isfinite(expiry) or not 0 < expiry <= 31_536_000:
        raise ChatGPTError("ChatGPT 授权的有效期无效。")
    refresh = response.get("refresh_token", (existing or {}).get("refresh_token", ""))
    identity = response.get("id_token", (existing or {}).get("id_token", ""))
    if not isinstance(refresh, str) or not isinstance(identity, str):
        raise ChatGPTError("ChatGPT 返回的授权凭据格式无效。")
    return {"access_token": access, "refresh_token": refresh,
            "id_token": identity,
            "expires_at": time.time() + expiry, "scopes": scopes}


def consume_response(response):
    """Require a terminal success event; never leak malformed upstream data."""
    pieces, completed, event_lines, size = [], False, [], 0

    def event_data(data):
        nonlocal completed, pieces
        if data == "[DONE]":
            return
        event = json.loads(data)
        if not isinstance(event, dict):
            raise ChatGPTError("ChatGPT 返回了无效事件。")
        kind = event.get("type")
        if kind == "response.output_text.delta":
            delta = event.get("delta")
            if not isinstance(delta, str):
                raise ChatGPTError("ChatGPT 返回了无效文本片段。")
            pieces.append(delta)
        elif kind == "response.completed":
            terminal = event.get("response", {})
            if not isinstance(terminal, dict) or terminal.get("status", "completed") != "completed":
                raise ChatGPTError("ChatGPT 未成功完成回答。")
            final_text = []
            for output in terminal.get("output", []):
                if not isinstance(output, dict):
                    raise ChatGPTError("ChatGPT 完成事件格式无效。")
                for content in output.get("content", []):
                    if content.get("type") == "output_text":
                        text = content.get("text")
                        if not isinstance(text, str):
                            raise ChatGPTError("ChatGPT 完成事件没有有效文本。")
                        final_text.append(text)
            if final_text:
                pieces = final_text
            completed = True
        elif kind in {"error", "response.failed", "response.incomplete"}:
            raise ChatGPTError("ChatGPT 未完成回答，可能是会员额度或服务限制；请查看会员用量后重试。")

    try:
        with response:
            while True:
                line = response.readline(1_000_001)
                if not line:
                    if event_lines:
                        event_data("\n".join(event_lines))
                    break
                size += len(line)
                if size > 4_000_000 or len(line) > 1_000_000:
                    raise ChatGPTError("生成响应超出限制。")
                line = line.decode("utf-8").rstrip("\r\n")
                if line.startswith("data:"):
                    event_lines.append(line[5:].lstrip())
                elif not line and event_lines:
                    event_data("\n".join(event_lines))
                    event_lines = []
        if not completed or not pieces:
            raise ChatGPTError("ChatGPT 连接中断，未收到完整回答。")
        result = json.loads("".join(pieces))
        if not isinstance(result, dict):
            raise ChatGPTError("ChatGPT 回答不是要求的 JSON 对象。")
        return result
    except ChatGPTError:
        raise
    except Exception:
        raise ChatGPTError("ChatGPT 响应格式无效或连接中断，未采用本次生成内容。") from None


def init_chatgpt(app):
    store = CredentialStore(Path(app.config["DATA_DIR"]) / "chatgpt")
    pending = {}
    blueprint = Blueprint("chatgpt", __name__)

    def user_id():
        user = getattr(g, "user", None) if has_request_context() else None
        return int(user["id"]) if user else None

    def authenticated(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            if user_id() is None:
                return jsonify(error="请先登录知微。"), 401
            try:
                return function(*args, **kwargs)
            except ChatGPTError as exc:
                return jsonify(error=str(exc)), 400
            except Exception:
                return jsonify(error="ChatGPT 连接操作未完成，请稍后重试。"), 400
        return wrapped

    def body():
        value = request.get_json(silent=True)
        if value is None and not request.data:
            return {}
        if not isinstance(value, dict):
            raise ChatGPTError("请发送有效的 JSON 对象。")
        return value

    def sharing(selected):
        return {"resource.invoke", "chatgpt.tokens.use.direct"}.issubset(selected.get("scopes", []))

    def clear_tokens(selected):
        for name in ("access_token", "refresh_token", "id_token", "expires_at", "scopes", "models", "model",
                     "checked", "checked_model", "checked_at", "probe_succeeded"):
            selected.pop(name, None)

    def assert_current(selected):
        with store.lock:
            data = store.read(user_id())
            current = profile(data)
            if (not current or current["id"] != selected["id"] or data["epoch"] != selected.get("_epoch")
                    or not current.get("access_token") or current.get("disconnecting") or not sharing(current)):
                raise ChatGPTError("ChatGPT 连接或模型已改变，已丢弃旧请求结果，请重新操作。")
            return data, current

    def profile(data):
        return next((item for item in data["profiles"] if item["id"] == data["active"]), None)

    def connection_status():
        with store.lock:
            data = store.read(user_id())
            selected = profile(data) or {}
            permitted = sharing(selected)
            connected = bool(selected.get("access_token") and permitted and not selected.get("disconnecting")
                             and (selected.get("expires_at", 0) > time.time() or selected.get("refresh_token")))
            checked = bool(connected and selected.get("checked_model") == selected.get("model")
                           and time.time() - selected.get("checked_at", 0) < 300)
            ready = checked and bool(selected.get("probe_succeeded"))
            message = ("ChatGPT 真实推理检查已通过；回答消耗当前会员额度。" if ready else
                       "最近一次真实推理检查未通过，请检查会员权限或稍后重试。" if checked else
                       "已保存 ChatGPT 授权，请点击检查连接验证真实推理。" if connected else
                       "身份已验证，但未允许使用 ChatGPT 会员额度，请重新授权相关权限。" if selected.get("access_token") and not permitted else
                       "请使用 ChatGPT 登录并授权知微使用会员额度。")
            return {"connected": connected, "active_profile_id": data["active"],
                    "sharing": permitted, "checked": checked, "chat_ready": ready,
                    "profiles": [{"id": item["id"], "label": (item.get("email") or "待完成授权") + " · " + item["id"][:6],
                                  "connected": bool(item.get("access_token"))} for item in data["profiles"]],
                    "models": selected.get("models", []), "selected_model": selected.get("model", ""),
                    "message": message}

    def access_profile():
        with store.lock:
            data = store.read(user_id())
            selected = profile(data)
            if not selected or not selected.get("access_token") or selected.get("disconnecting"):
                raise ChatGPTError("请先使用 ChatGPT 登录。")
            if not sharing(selected):
                raise ChatGPTError("当前授权未允许使用会员额度，请重新授权相关权限。")
            if selected.get("expires_at", 0) < time.time() + 90:
                if not selected.get("refresh_token"):
                    raise ChatGPTError("ChatGPT 授权已过期，请重新登录。")
                try:
                    response = _http(TOKEN, {"grant_type": "refresh_token", "client_id": selected["client_id"],
                                             "refresh_token": selected["refresh_token"], "resource": RESOURCE}, form=True)
                except InvalidGrant:
                    clear_tokens(selected)
                    data["epoch"] += 1
                    store.write(user_id(), data)
                    raise
                fields = _token_fields(response, selected)
                if response.get("id_token"):
                    _validate_identity(response["id_token"], selected["client_id"], subject=selected["subject"])
                selected.update(fields)
                store.write(user_id(), data)
                if not sharing(selected):
                    raise ChatGPTError("刷新后的授权未允许会员推理，请重新授权。")
            return dict(selected, _epoch=data["epoch"])

    def refresh_models():
        selected = access_profile()
        result = _http(RESOURCE + "/models", bearer=selected["access_token"])
        models = [{"slug": item["slug"], "display_name": item.get("display_name", item["slug"])}
                  for item in result.get("models", []) if isinstance(item, dict)
                  and item.get("visibility") == "list" and isinstance(item.get("slug"), str)]
        if not models:
            raise ChatGPTError("此 ChatGPT 账户暂未返回可用模型，请确认会员权限。")
        with store.lock:
            data, target = assert_current(selected)
            target["models"] = models
            if target.get("model") not in {item["slug"] for item in models}:
                target["model"] = models[0]["slug"]
                target["probe_succeeded"] = False
            store.write(user_id(), data)
        return models

    @blueprint.get("/api/chatgpt")
    @authenticated
    def status_route():
        return jsonify(connection_status())

    @blueprint.post("/api/chatgpt/connect")
    @authenticated
    def connect_route():
        origin = urllib.parse.urlsplit(request.host_url)
        if origin.scheme != "http" or origin.hostname != "127.0.0.1":
            raise ChatGPTError("请从 http://127.0.0.1 的本地知微页面发起授权。")
        payload = body()
        with store.lock:
            data = store.read(user_id())
            selected_id = payload.get("profile_id")
            selected = next((item for item in data["profiles"] if item["id"] == selected_id), None)
            if selected_id and not selected:
                raise ChatGPTError("该连接不存在。")
            if selected and selected.get("disconnecting"):
                raise ChatGPTError("正在断开该连接，请完成后再授权。")
            state, nonce, verifier = (secrets.token_urlsafe(40) for _ in range(3))
            binding = session.setdefault("chatgpt_oauth_session", secrets.token_urlsafe(32))
            client_id = selected["client_id"] if selected else "dynamic_agent_client"
            redirect = request.host_url.rstrip("/") + CALLBACK
            attempt = {"state": state, "nonce": nonce, "verifier": verifier, "redirect": redirect,
                       "user_id": user_id(), "client_id": client_id, "profile_id": selected_id,
                       "created": time.time(), "epoch": data["epoch"], "session_binding": binding}
            for key in list(pending):
                if time.time() - pending[key]["created"] > 600:
                    pending.pop(key)
            pending[state] = attempt
            query = {"client_id": client_id, "ext_agent_host_id": store.host_id,
                     "response_type": "code", "redirect_uri": redirect, "scope": SCOPES,
                     "resource": RESOURCE, "state": state, "nonce": nonce, "code_challenge_method": "S256",
                     "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")}
            if not selected:
                query["agent_name_hint"] = "Zhiwei Knowledge Assistant"
            elif selected.get("access_token") and not sharing(selected):
                # An explicit reconnect after identity-only consent must offer
                # the missing plan-use permission, not silently reuse its grant.
                query["prompt"] = "consent"
            return jsonify(authorization_url=AUTHORIZE + "?" + urllib.parse.urlencode(query))

    @blueprint.get(CALLBACK)
    def callback_route():
        successful = False
        message = "授权未完成，请回到知微重试。"
        try:
            state = request.args.get("state", "")
            with store.lock:
                attempt = pending.get(state)
                if (not attempt or user_id() != attempt["user_id"] or time.time() - attempt["created"] > 600
                        or session.get("chatgpt_oauth_session") != attempt.get("session_binding")
                        or request.base_url != attempt["redirect"] or store.read(user_id())["epoch"] != attempt["epoch"]):
                    raise ChatGPTError("授权请求已过期或不属于当前知微账户，请重新发起。")
                pending.pop(state)
            if request.args.get("error"):
                raise ChatGPTError("你未完成 ChatGPT 授权。可以回到知微重新连接。")
            client_id = request.args.get("client_id") or attempt["client_id"]
            if (not client_id or len(client_id) > 200 or client_id == "dynamic_agent_client"
                    or (attempt["client_id"] != "dynamic_agent_client" and client_id != attempt["client_id"])):
                raise ChatGPTError("ChatGPT 应用注册信息不匹配，请重新授权。")
            code = request.args.get("code")
            if not code or len(code) > 8000:
                raise ChatGPTError("未收到有效授权码。")
            with store.lock:
                data = store.read(user_id())
                if data["epoch"] != attempt["epoch"]:
                    raise ChatGPTError("连接状态已改变，请重新发起授权。")
                selected = next((item for item in data["profiles"] if item["id"] == attempt["profile_id"]), None)
                if selected is None:
                    selected = {"id": uuid.uuid4().hex, "client_id": client_id}
                    data["profiles"].append(selected)
                    store.write(user_id(), data)  # Retain issued registration if code exchange fails.
                selected_id, expected_subject = selected["id"], selected.get("subject")
            response = _http(TOKEN, {"grant_type": "authorization_code", "client_id": client_id,
                                     "code": code, "code_verifier": attempt["verifier"],
                                     "redirect_uri": attempt["redirect"], "resource": RESOURCE}, form=True)
            identity = _validate_identity(response.get("id_token", ""), client_id, attempt["nonce"], expected_subject)
            fields = _token_fields(response)
            with store.lock:
                data = store.read(user_id())
                if data["epoch"] != attempt["epoch"]:
                    raise ChatGPTError("授权期间连接已断开或切换，未保存这次返回的凭据。")
                selected = next(item for item in data["profiles"] if item["id"] == selected_id)
                selected.update(fields, subject=identity["sub"], email=str(identity.get("email", "ChatGPT 账户")))
                selected["probe_succeeded"] = False
                data["active"] = selected_id
                data["epoch"] += 1
                store.write(user_id(), data)
            if sharing(selected):
                try:
                    refresh_models()
                    message = "ChatGPT 授权完成。请回到知微检查模型连接，即可开始问答。"
                except ChatGPTError:
                    message = "ChatGPT 授权已保存；模型列表暂未读取成功，请回到知微点击刷新模型。"
            else:
                message = "ChatGPT 身份已验证，但尚未允许会员额度推理；请回到知微重新授权相关权限。"
            successful = True
        except ChatGPTError as exc:
            message = str(exc)
        except Exception:
            message = "授权未能完成，请回到知微重新连接。"
        script = ("if(window.opener){window.opener.postMessage({type:'zhiwei-chatgpt-connected'},window.location.origin);}" if successful else "")
        nonce = secrets.token_urlsafe(24)
        page = ('<!doctype html><meta charset="utf-8"><title>知微 · ChatGPT 授权</title>'
                '<h1>知微 · ChatGPT 授权</h1><p>' + html.escape(message) +
                '</p><p><a href="/">返回知微</a></p>' + f'<script nonce="{nonce}">{script}</script>')
        return page, 200 if successful else 400, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer", "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; base-uri 'none'"}

    @blueprint.post("/api/chatgpt/select")
    @authenticated
    def select_route():
        selected_id = body().get("profile_id")
        with store.lock:
            data = store.read(user_id())
            if not any(item["id"] == selected_id for item in data["profiles"]):
                raise ChatGPTError("该连接不存在。")
            data["active"] = selected_id
            data["epoch"] += 1
            store.write(user_id(), data)
        notice = None
        try:
            if connection_status()["sharing"]:
                refresh_models()
        except ChatGPTError as exc:
            notice = str(exc)
        return jsonify({**connection_status(), **({"notice": notice} if notice else {})})

    @blueprint.post("/api/chatgpt/model")
    @authenticated
    def model_route():
        model = body().get("model")
        with store.lock:
            data = store.read(user_id())
            selected = profile(data)
            if not selected or model not in {item["slug"] for item in selected.get("models", [])}:
                raise ChatGPTError("请从当前账号实际返回的模型列表中选择。")
            selected["model"] = model
            selected["probe_succeeded"] = False
            data["epoch"] += 1
            store.write(user_id(), data)
        return jsonify(connection_status())

    @blueprint.post("/api/chatgpt/refresh-models")
    @authenticated
    def models_route():
        refresh_models()
        return jsonify(connection_status())

    @blueprint.post("/api/chatgpt/disconnect")
    @authenticated
    def disconnect_route():
        notice = "已断开此知微账户的 ChatGPT 连接。"
        marker = secrets.token_hex(16)
        with store.lock:
            data = store.read(user_id())
            selected = profile(data)
            selected_snapshot = dict(selected) if selected else None
            data["epoch"] += 1
            for key in list(pending):
                if pending[key]["user_id"] == user_id():
                    pending.pop(key, None)
            if selected:
                selected["disconnecting"] = marker
                selected["probe_succeeded"] = False
            store.write(user_id(), data)
        # Block all new uses immediately, but retain the token only long enough
        # to attempt official revocation. Never hold a disk lock during retries.
        if selected_snapshot and selected_snapshot.get("refresh_token"):
            revoked = False
            for attempt_number in range(2):
                try:
                    discovery = _http(ISSUER + "/.well-known/openid-configuration")
                    endpoint = discovery["revocation_endpoint"]
                    parsed = urllib.parse.urlsplit(endpoint)
                    if parsed.scheme != "https" or parsed.hostname != "auth.openai.com" or parsed.username or parsed.password or parsed.port not in (None, 443):
                        raise ChatGPTError("官方注销接口验证失败。")
                    _http(endpoint, {"token": selected_snapshot["refresh_token"], "token_type_hint": "refresh_token",
                                     "client_id": selected_snapshot["client_id"]}, form=True)
                    revoked = True
                    break
                except Exception:
                    if attempt_number == 0:
                        time.sleep(0.2)
            if not revoked:
                notice = "本地授权已清除，远端注销未确认；可在 ChatGPT 设置中断开知微。"
        with store.lock:
            data = store.read(user_id())
            target = next((item for item in data["profiles"] if selected_snapshot and item["id"] == selected_snapshot["id"]), None)
            if target and target.get("disconnecting") == marker:
                clear_tokens(target)
                target.pop("disconnecting", None)
                store.write(user_id(), data)
        return jsonify({**connection_status(), "notice": notice})

    def chat_ready(_config):
        try:
            if user_id() is None:
                return False
            state = connection_status()
            return state["connected"] and bool(state["selected_model"])
        except ChatGPTError:
            return False

    def chat_status(_config):
        configured = chat_ready(_config)
        try:
            state = connection_status() if user_id() else {}
        except ChatGPTError:
            state = {"message": "当前知微账户的授权文件无法读取，请重新授权。"}
        return {"configured": configured, "checked": bool(state.get("checked")),
                "connected": bool(state.get("connected")), "chat_ready": bool(state.get("chat_ready")),
                "profile_id": state.get("active_profile_id"),
                "chat_model": state.get("selected_model", ""), "message": state.get("message", "请先登录知微并连接 ChatGPT。")}

    def chat_json(messages, config, schema=None, max_tokens=None):
        selected = access_profile()
        if not selected.get("model"):
            raise ChatGPTError("请先刷新并选择 ChatGPT 模型。")
        instructions = "\n".join(item["content"] for item in messages if item["role"] in {"system", "developer"})
        inputs = [item for item in messages if item["role"] not in {"system", "developer"}]
        payload = {"model": selected["model"], "instructions": instructions,
                   "input": inputs, "store": False, "stream": True}
        if schema:
            payload["text"] = {"format": {"type": "json_schema", "name": "grounded_result", "strict": True, "schema": schema}}
        # ChatGPT plan usage does not accept temperature/max_output_tokens or previous_response_id.
        assert_current(selected)
        response = _http(RESOURCE + "/responses", payload, bearer=selected["access_token"], stream=True)
        result = consume_response(response)
        assert_current(selected)  # Do not deliver a reply from a disconnected/switched profile.
        return result

    def chat_check(config):
        """A requested check consumes a tiny real inference, never a cached pass."""
        selected = None
        try:
            with store.lock:
                data = store.read(user_id())
                current = profile(data)
                if current:
                    current.update(probe_succeeded=False, checked_model=current.get("model", ""), checked_at=time.time())
                    store.write(user_id(), data)
            refresh_models()
            selected = access_profile()
            schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                      "required": ["ok"], "additionalProperties": False}
            result = chat_json([
                {"role": "system", "content": 'Connection check only. Return exactly the JSON object {"ok":true}.'},
                {"role": "user", "content": "Confirm that a complete response can be generated."}], config, schema=schema)
            if result != {"ok": True} or result.get("ok") is not True:
                raise ChatGPTError("ChatGPT 未返回连接检查要求的完整结果。")
            with store.lock:
                data, current = assert_current(selected)
                current.update(probe_succeeded=True, checked_model=current["model"], checked_at=time.time())
                store.write(user_id(), data)
            return chat_status(config)
        except Exception as exc:
            if selected:
                try:
                    with store.lock:
                        data, current = assert_current(selected)
                        current.update(probe_succeeded=False, checked_model=current.get("model", ""), checked_at=time.time())
                        store.write(user_id(), data)
                except ChatGPTError:
                    pass
            status = chat_status(config)
            status.update(checked=True, connected=False, chat_ready=False,
                          message=str(exc) if isinstance(exc, ChatGPTError) else "ChatGPT 真实推理检查未通过，请稍后重试。")
            return status

    class HideOAuthCallback(logging.Filter):
        def filter(self, record):
            return CALLBACK not in record.getMessage()

    logging.getLogger("werkzeug").addFilter(HideOAuthCallback())
    app.register_blueprint(blueprint)
    app.config.update(CHAT_JSON_PROVIDER=chat_json, CHAT_PROVIDER_CONFIGURED=chat_ready,
                      CHAT_STATUS_PROVIDER=chat_status, CHAT_CHECK_PROVIDER=chat_check,
                      CHAT_MODEL_DISPLAY=lambda config: chat_status(config)["chat_model"])
    app.extensions["chatgpt_store"] = store
    app.extensions["chatgpt_pending"] = pending
