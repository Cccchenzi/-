"""HTTP clients for Ollama embeddings and DeepSeek/OpenAI-compatible chat.

No model or credentials are downloaded. Models must be installed/configured by
the operator. Public errors deliberately omit upstream bodies and private URLs.
"""
from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class ProviderError(ValueError):
    pass


def provider_name(config):
    return str(config.get("RAG_PROVIDER", "local")).strip().lower()


def settings(config):
    provider = provider_name(config)
    if provider == "deepseek":
        base = str(config.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
        if base.endswith("/chat/completions"):
            base = base[:-len("/chat/completions")]
        return {"provider": provider, "base": base,
                "chat_model": str(config.get("DEEPSEEK_MODEL") or "deepseek-flash").strip(),
                "embedding_model": "", "key": str(config.get("DEEPSEEK_API_KEY", "")).strip()}
    if provider == "ollama":
        return {"provider": provider, "base": str(config.get("OLLAMA_BASE_URL", "")).rstrip("/"),
                "chat_model": str(config.get("OLLAMA_CHAT_MODEL", "")),
                "embedding_model": str(config.get("OLLAMA_EMBED_MODEL", "")), "key": ""}
    base = str(config.get("AI_BASE_URL", "")).rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")]
    display = config.get("CHAT_MODEL_DISPLAY")
    display = display(config) if callable(display) else display
    return {"provider": provider, "base": base, "chat_model": str(display or config.get("AI_MODEL", "")),
            "embedding_model": str(config.get("AI_EMBED_MODEL", "")), "key": str(config.get("AI_API_KEY", ""))}


def chat_configured(config):
    if provider_name(config) == "local":
        return False
    if provider_name(config) != "deepseek" and callable(config.get("CHAT_JSON_PROVIDER")):
        ready = config.get("CHAT_PROVIDER_CONFIGURED", False)
        return bool(ready(config) if callable(ready) else ready)
    data = settings(config)
    return bool(data["base"].strip() and data["chat_model"].strip()
                and (data["provider"] == "ollama" or data["key"].strip()))


def embedding_settings(config):
    provider = provider_name(config)
    selected = str(config.get("EMBEDDING_PROVIDER") or ("ollama" if provider in {"ollama", "deepseek", "chatgpt"} else "openai")).lower()
    if selected == "ollama":
        return {"provider": "ollama", "base": str(config.get("OLLAMA_BASE_URL", "")).rstrip("/"),
                "model": str(config.get("OLLAMA_EMBED_MODEL", "")), "key": ""}
    data = settings(config)
    # DeepSeek is a chat provider in this app. A separate online embedding
    # service must have its own explicit endpoint/key; never inherit DeepSeek's.
    return {"provider": "openai", "base": str(config.get("AI_EMBED_BASE_URL") or (data["base"] if provider != "deepseek" else "")).rstrip("/"),
            "model": str(config.get("AI_EMBED_MODEL", "")),
            "key": str(config.get("AI_EMBED_API_KEY") or (data["key"] if provider != "deepseek" else ""))}


def embedding_configured(config):
    if provider_name(config) == "local":
        return False
    data = embedding_settings(config)
    if data["provider"] == "openai" and urlsplit(data["base"]).hostname == "api.deepseek.com":
        return False
    return bool(data["model"].strip() and data["base"].strip() and (data["provider"] == "ollama" or data["key"].strip()))


def embedding_fingerprint(config):
    data = embedding_settings(config)
    # Optional revision supports pinning a provider's mutable model alias.
    identity = [data["provider"], data["base"], data["model"], str(config.get("EMBEDDING_REVISION", ""))]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def _request_json(base, path, payload=None, key="", timeout=180):
    parsed = urlsplit(base)
    if parsed.username or parsed.password:
        raise ProviderError("服务地址不能包含凭据，请使用后端密钥配置。")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ProviderError("模型服务需使用 HTTPS，或本机 HTTP 地址。")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(base.rstrip("/") + path,
                                     data=json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None,
                                     headers=headers, method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            raw = response.read(16_000_001)
        if len(raw) > 16_000_000:
            raise ProviderError("模型服务响应过大，已停止处理。")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("error"):
            raise ProviderError("模型服务返回错误或无效响应。请检查本机模型和服务配置。")
        return result
    except ProviderError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ProviderError("模型服务拒绝授权，请检查后端 API Key。") from None
        if exc.code == 404:
            raise ProviderError("模型或接口不存在，请检查模型名称及服务地址。") from None
        raise ProviderError("模型服务请求失败，请检查服务运行状态。") from None
    except Exception:
        raise ProviderError("无法连接模型服务或请求超时，请启动服务并确认所需模型已安装。") from None


def list_deepseek_models(config):
    """Read the live catalog; IDs, not labels, are used in chat requests."""
    if provider_name(config) != "deepseek":
        raise ProviderError("当前未使用 DeepSeek 服务。")
    data = settings(config)
    if not data["key"]:
        raise ProviderError("DeepSeek API Key 尚未配置，请填写 DEEPSEEK_API_KEY 后重启。")
    response = _request_json(data["base"], "/models", key=data["key"], timeout=config.get("CHECK_TIMEOUT", 8))
    rows = response.get("data")
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ProviderError("DeepSeek 模型列表格式无效。")
    models, seen = [], set()
    for row in rows:
        identifier = row.get("id") if isinstance(row, dict) else None
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 200 or any(character.isspace() or ord(character) < 32 for character in identifier):
            raise ProviderError("DeepSeek 模型列表格式无效。")
        label = row.get("name")
        label = label.strip() if isinstance(label, str) else ""
        if not label or len(label) > 200 or any(ord(character) < 32 for character in label):
            label = identifier
        if identifier not in seen:
            models.append({"id": identifier, "display_name": label})
            seen.add(identifier)
    return models


def normalize_vectors(vectors, expected_count=None):
    if not isinstance(vectors, list) or not vectors or (expected_count is not None and len(vectors) != expected_count):
        raise ProviderError("嵌入服务返回的向量数量不匹配。")
    dimension = None
    normalized = []
    for vector in vectors:
        if not isinstance(vector, list) or not vector or len(vector) > 65536:
            raise ProviderError("嵌入向量维度无效。")
        if dimension is None:
            dimension = len(vector)
        if len(vector) != dimension or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector):
            raise ProviderError("嵌入向量包含非法数值或不一致维度。")
        try:
            norm = math.sqrt(sum(float(value) ** 2 for value in vector))
        except OverflowError:
            raise ProviderError("嵌入向量数值超出可计算范围。") from None
        if not math.isfinite(norm) or norm <= 1e-12:
            raise ProviderError("嵌入服务返回了无效的零向量。")
        normalized.append([float(value) / norm for value in vector])
    return normalized


def embed_texts(texts, config):
    if not texts:
        return []
    if not embedding_configured(config):
        raise ProviderError("语义嵌入未配置完整，文档已保存，请配置后重建索引。")
    data = embedding_settings(config)
    timeout = config.get("EMBED_TIMEOUT", 180)
    if data["provider"] == "ollama":
        response = _request_json(data["base"], "/api/embed", {"model": data["model"], "input": texts, "truncate": False,
                                 "keep_alive": "5m"}, timeout=timeout)
        vectors = response.get("embeddings")
    else:
        base = data["base"]
        path = "" if base.endswith("/embeddings") else "/embeddings"
        response = _request_json(base, path, {"model": data["model"], "input": texts, "encoding_format": "float"},
                                 key=data["key"], timeout=timeout)
        rows = response.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ProviderError("嵌入服务没有返回有效向量数据。")
        try:
            rows = sorted(rows, key=lambda row: row["index"])
            if [row["index"] for row in rows] != list(range(len(texts))):
                raise ValueError()
            vectors = [row["embedding"] for row in rows]
        except (KeyError, TypeError, ValueError):
            raise ProviderError("嵌入服务返回的向量索引无效。") from None
    return normalize_vectors(vectors, len(texts))


def chat_json(messages, config, schema=None, max_tokens=1800):
    if not chat_configured(config):
        if provider_name(config) == "deepseek":
            raise ProviderError("DeepSeek API Key 尚未配置，请在本地 .env 填写 DEEPSEEK_API_KEY 后重启。")
        raise ProviderError("生成模型配置不完整。")
    if provider_name(config) != "deepseek" and callable(config.get("CHAT_JSON_PROVIDER")):
        result = config["CHAT_JSON_PROVIDER"](messages, config, schema, max_tokens)
        if not isinstance(result, dict):
            raise ProviderError("生成模型返回的答案结构无效。")
        return result
    data = settings(config)
    timeout = config.get("MODEL_TIMEOUT", 180)
    if data["provider"] == "ollama":
        payload = {"model": data["chat_model"], "messages": messages, "stream": False, "think": False,
                   "format": schema or "json", "keep_alive": "5m",
                   "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 8192}}
        response = _request_json(data["base"], "/api/chat", payload, timeout=timeout)
        content = response.get("message", {}).get("content")
    else:
        payload = {"model": data["chat_model"], "messages": messages, "temperature": 0,
                   "max_tokens": max_tokens, "response_format": {"type": "json_object"}}
        if data["provider"] == "deepseek":
            # Official Chat Completions JSON mode, with thinking explicitly off.
            # Prompts supplied by RAG/probes already request JSON and give shape.
            payload.update(thinking={"type": "disabled"}, stream=False)
        response = _request_json(data["base"], "/chat/completions", payload, key=data["key"], timeout=timeout)
        try:
            choice = response["choices"][0]
            if not isinstance(choice, dict):
                raise ProviderError("生成模型没有返回有效答案。")
            if data["provider"] == "deepseek" and choice.get("finish_reason") != "stop":
                raise ProviderError("DeepSeek 回答未完整结束或已截断，未采用本次生成内容。")
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("生成模型没有返回有效答案。") from None
    if not isinstance(content, str) or len(content) > 50000:
        raise ProviderError("生成模型响应格式无效。")
    try:
        result = json.loads(content)
    except (TypeError, ValueError):
        raise ProviderError("生成模型未返回要求的结构化答案。") from None
    if not isinstance(result, dict):
        raise ProviderError("生成模型返回的答案结构无效。")
    return result


def status_snapshot(config):
    data = settings(config)
    embedding = embedding_settings(config)
    complete = chat_configured(config) and embedding_configured(config)
    previous = config.get("_MODEL_STATUS", {})
    same = previous.get("fingerprint") == embedding_fingerprint(config) and previous.get("chat_model") == data["chat_model"]
    result = {"provider": data["provider"], "chat_model": data["chat_model"], "embedding_model": embedding["model"], "embedding_provider": embedding["provider"],
            "configured": complete, "checked": bool(same and previous.get("checked")),
            "connected": bool(same and previous.get("connected")), "chat_ready": bool(same and previous.get("chat_ready")),
            "embedding_ready": bool(same and previous.get("embedding_ready")),
            "message": previous.get("message") if same else ("尚未检查模型服务。" if complete else "当前为本地摘录，或模型配置不完整。")}
    if data["provider"] == "deepseek" and not data["key"]:
        result.update(configured=False, checked=False, connected=False, chat_ready=False,
                      message="DeepSeek API Key 尚未配置，请在本地 .env 填写 DEEPSEEK_API_KEY 后重启；当前仅能展示检索原文。")
    if data["provider"] != "deepseek" and callable(config.get("CHAT_STATUS_PROVIDER")):
        state = config["CHAT_STATUS_PROVIDER"](config)
        same_profile = state.get("profile_id") == previous.get("profile_id") and state.get("profile_id") is not None
        result["checked"] = result["checked"] and same_profile
        result["chat_ready"] = result["chat_ready"] and bool(state.get("chat_ready")) and same_profile
        result["connected"] = result["connected"] and bool(state.get("connected")) and same_profile
        result["chat_model"] = state.get("chat_model") or result["chat_model"]
        current_message = state.get("message") or ""
        previous_message = result["message"] if same_profile else "请检查当前账户的模型连接。"
        # check_models already includes the chat status before the embedding
        # result. Keep it once instead of prepending the identical status.
        result["message"] = (previous_message if current_message and previous_message and previous_message.startswith(current_message)
                             else " ".join(filter(None, [current_message, previous_message])))
    return result


def check_models(config):
    data = settings(config)
    embedding = embedding_settings(config)
    result = {"provider": data["provider"], "chat_model": data["chat_model"], "embedding_model": embedding["model"],
              "embedding_provider": embedding["provider"],
              "configured": chat_configured(config) and embedding_configured(config), "checked": True,
              "connected": False, "chat_ready": False, "embedding_ready": False, "fingerprint": embedding_fingerprint(config)}
    if data["provider"] == "local":
        result["message"] = "当前选择本地词法摘录模式；请配置生成与嵌入模型以启用完整 RAG。"
        return result
    messages = []
    chat_connected = embed_connected = False
    timeout = config.get("CHECK_TIMEOUT", 8)
    try:
        if data["provider"] != "deepseek" and (callable(config.get("CHAT_CHECK_PROVIDER")) or callable(config.get("CHAT_STATUS_PROVIDER"))):
            checker = config.get("CHAT_CHECK_PROVIDER") or config["CHAT_STATUS_PROVIDER"]
            state = checker(config)
            chat_connected = bool(state.get("connected"))
            result["chat_ready"] = bool(state.get("chat_ready"))
            result["chat_model"] = state.get("chat_model") or result["chat_model"]
            result["profile_id"] = state.get("profile_id")
            if state.get("message"):
                messages.append(str(state["message"]))
        elif not chat_configured(config):
            if data["provider"] == "deepseek":
                raise ProviderError("DeepSeek API Key 尚未配置，请填写 DEEPSEEK_API_KEY 后重启。")
            raise ProviderError("生成模型尚未配置完整。")
        elif data["provider"] == "ollama":
            response = _request_json(data["base"], "/api/tags", timeout=timeout)
            names = {row.get("name", row.get("model", "")) for row in response.get("models", []) if isinstance(row, dict)}
            chat_connected = True
            result["chat_ready"] = data["chat_model"] in names or data["chat_model"] + ":latest" in names
        else:
            response = _request_json(data["base"], "/models", key=data["key"], timeout=timeout)
            names = {row.get("id", "") for row in response.get("data", []) if isinstance(row, dict)}
            chat_connected = True
            result["chat_ready"] = data["chat_model"] in names
        if not result["chat_ready"]:
            messages.append("生成模型尚未就绪或不在服务模型列表中。")
        elif data["provider"] == "deepseek" or not callable(config.get("CHAT_CHECK_PROVIDER")):
            probe = chat_json([{"role": "system", "content": 'Return only JSON {"ok":true} for a connection check.'},
                               {"role": "user", "content": "Check."}], config,
                              schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                                      "required": ["ok"], "additionalProperties": False}, max_tokens=32)
            if probe != {"ok": True} or probe.get("ok") is not True:
                raise ProviderError("生成模型没有通过真实推理检查。")
    except ProviderError as exc:
        result["chat_ready"], chat_connected = False, False
        messages.append(str(exc))
    except Exception:
        result["chat_ready"], chat_connected = False, False
        messages.append("无法确认当前账号的生成模型状态。")
    try:
        if not embedding_configured(config):
            raise ProviderError("语义嵌入模型尚未配置完整。")
        if embedding["provider"] == "ollama":
            response = _request_json(embedding["base"], "/api/tags", timeout=timeout)
            names = {row.get("name", row.get("model", "")) for row in response.get("models", []) if isinstance(row, dict)}
            embed_connected = True
            result["embedding_ready"] = embedding["model"] in names or embedding["model"] + ":latest" in names
        else:
            base = embedding["base"]
            if base.endswith("/embeddings"):
                base = base[:-len("/embeddings")]
            response = _request_json(base, "/models", key=embedding["key"], timeout=timeout)
            names = {row.get("id", "") for row in response.get("data", []) if isinstance(row, dict)}
            embed_connected = True
            result["embedding_ready"] = embedding["model"] in names
        if not result["embedding_ready"]:
            messages.append("嵌入模型不在服务模型列表中，请安装或检查名称。")
        else:
            # A model name in a catalog is not proof it can execute. Probe one
            # harmless string, and validate its actual numeric embedding.
            normalize_vectors(embed_texts(["知微模型连接检查"], config), 1)
    except ProviderError as exc:
        result["embedding_ready"], embed_connected = False, False
        messages.append(str(exc))
    except Exception:
        result["embedding_ready"], embed_connected = False, False
        messages.append("无法检查嵌入模型服务，请确认服务运行状态。")
    result["connected"] = chat_connected and embed_connected
    if result["chat_ready"] and result["embedding_ready"]:
        messages.append("生成服务检查与嵌入模型推理检查已完成。")
    result["message"] = " ".join(messages)
    return result
