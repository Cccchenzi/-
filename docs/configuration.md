# 模型配置

项目根目录的 `.env` 保存部署配置，系统环境变量优先。`setup-ai.bat` 成功后会在 `.env` 不存在时从 `.env.example` 创建它。修改配置后重启应用；密钥不提交 Git。

## 默认配置

| 设置 | 默认值 / 用途 |
| --- | --- |
| `RAG_PROVIDER` | `deepseek`，使用 DeepSeek 生成回答 |
| `DEEPSEEK_API_KEY` | 留空，填写自己的 API 密钥 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | `deepseek-flash` |
| `EMBEDDING_PROVIDER` | `ollama`，本机生成语义向量 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` |
| `OLLAMA_EMBED_MODEL` | `qwen3-embedding:0.6b` |

登录后检查模型连接。网页可刷新模型目录并显式应用其他模型；选择按知微账户保存，无需重启，覆盖该账户的部署默认模型。切换后需重新检查连接，已有选择不会因重启恢复为 Flash。同一部署共用 API 密钥及额度。

向量化在本机完成；DeepSeek 会收到问题、检索片段及必要历史。更换生成模型无需重建索引；更换嵌入模型或服务后需重建各库索引。模型别名内容变化时，可修改 `EMBEDDING_REVISION` 使旧向量失效。

## 其他运行方式

在 `.env` 修改以下参数并重启；未列出的本地嵌入配置保持上表默认值。

| 方式 | 配置与准备 |
| --- | --- |
| 完全本地 Ollama | `RAG_PROVIDER=ollama`、`OLLAMA_CHAT_MODEL=qwen3:4b`；先用下方命令安装生成模型 |
| OpenAI 兼容 API | `RAG_PROVIDER=openai`，填写 `AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL`；服务需支持 Chat Completions 和 JSON 对象输出 |
| ChatGPT 会员登录 | `RAG_PROVIDER=chatgpt`；重启后使用页面会员登录入口并检查模型。默认 DeepSeek 配置不使用会员授权 |
| 离线摘录 | `RAG_PROVIDER=local`；使用词法检索和原文摘录，不调用生成或嵌入服务 |

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-ai.ps1 -WithLocalChat
```

安装脚本使用 Windows x64 运行时和上述 Qwen 模型；其他 Ollama 模型需自行安装。应用只自动管理 `http://127.0.0.1:11434` 的服务，其他地址需自行启动。

如需在线嵌入，设置 `EMBEDDING_PROVIDER=openai` 并填写 `AI_EMBED_BASE_URL`、`AI_EMBED_API_KEY`、`AI_EMBED_MODEL`。在线嵌入会发送文档片段；DeepSeek 聊天密钥不会用于嵌入接口。远程地址须使用 HTTPS，本机地址可用 HTTP。

## 数据与超时

| 设置 | 默认值 / 用途 |
| --- | --- |
| `DATA_DIR` | `data`，保存 SQLite、原件和本地模型；相对路径以项目根目录为准 |
| `SECRET_KEY` | 留空时生成并保存本地会话签名密钥 |
| `MODEL_TIMEOUT` | `180`，单次生成请求超时秒数 |
| `EMBED_TIMEOUT` | `180`，单次嵌入请求超时秒数 |

修改 `DATA_DIR` 不会自动迁移资料，自定义目录须另加 Git 忽略规则。可选会员授权位于数据目录的 `chatgpt/`，在 Windows 上绑定当前系统账户，不可作为跨电脑免登录凭据。
