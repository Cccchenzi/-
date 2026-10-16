# 知微 · 个人知识库问答助手

学生项目场景池项目 02：上传本人资料，自动分块和语义向量化，检索后生成带原文出处的回答，按知识库保存多轮会话。Python Flask + SQLite + 原生网页前端，无需前端构建。

本次修订补齐上一版的核心缺口：**TF-IDF 关键词检索＋原文摘录只是降级模式，不能算完整 AI RAG。** 当前已实现真实嵌入、生成、向量持久化、多轮检索改写和逐条引文校验。模型实际接通情况以页面连接检查和真实验收记录为准。

## 本机启动与首次安装

已配置的电脑直接双击 `start.bat`，或在 VS Code 打开 `zhiwei.code-workspace` 后按 Ctrl+Shift+B。地址为 http://127.0.0.1:8765 。安装 Python / Python Debugger 扩展后也可按 F5。

首次下载项目或换电脑：

1. 安装 Python 3.11+，运行 `setup.bat` 安装依赖。
2. 运行 `setup-ai.bat`。下载经过 SHA-256 核验的官方 Ollama 运行时及 `qwen3-embedding:0.6b` 向量模型，首次合计约 2.1GB；解压另需空间。运行时在 `.runtime/`，模型在 `data/ollama-models/`，不提交 Git。
3. 运行 `start.bat`，创建或登录知微账户，在“模型与索引”中点 **Continue with ChatGPT**。在官方页面登录并授权，返回知微刷新模型并检查连接。
4. 创建知识库，批量上传 PDF / Markdown / TXT。所有片段索引完成后提问；旧资料可点击“重建向量索引”。

本项目通过官方 [Sign in with ChatGPT](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt) 连接会员。资格及模型以官方授权页面和账号模型列表为准，问答使用会员额度；没有读取 Codex 或其他应用的凭据。可在 [ChatGPT 用量设置](https://chatgpt.com/settings/usage) 管理额度及访问权。

本地向量化不发送资料给外部服务；使用 ChatGPT 问答时，**当前问题、检索片段及必要历史会发送给 OpenAI**。Windows 授权凭据由 DPAPI 加密，按知微账户隔离，不发送到网页、不提交仓库。换 Windows 账户或电脑需重新授权。

## 五项项目要求

| 需求 | 当前实现 | 核对方法 |
| --- | --- | --- |
| PDF / MD / TXT 批量上传 | 每批最多 10 份、每份 20MiB；逐文件反馈；同库去重 | 同时上传三种格式，查看片段数和错误提示 |
| 切片向量化、Chroma 升级点 | 约 750 字符分块、120 重叠；稠密嵌入保存 SQLite；模型指纹防止混用；预留 VectorStore | 文件显示索引完成，重启仍可检索；Chroma 本身不是必装组件 |
| 本人文档 AI 问答、多轮 | 当前账户/库内语义检索；模型理解追问并生成结构化结论 | 用同义表达、跨文档问题和指代追问测试；实际模式必须为 AI 生成 |
| 引用溯源 | 每条生成结论绑定片段；引文必须逐字存在；可查文件、页/行和原文 | 非法引文拒绝展示生成答案 |
| 按知识库保存历史 | SQLite 保存会话、消息、引用及实际检索/生成模式 | 切换库、打开历史、重启应用 |

资料不足时拒答；服务失败或引文无效时明确降级为原文摘录。**逐字引文校验不能证明概括一定正确，更不能保证零幻觉**，仍需核对结论是否被原文支持。扫描 PDF 需先 OCR，DOCX 不属于上传格式。

## 模型配置

配置保存在项目 `.env`；修改后重启。模板为 `.env.example`，密钥和令牌不要写入源代码。

推荐的会员＋本地向量方案：

```dotenv
RAG_PROVIDER=chatgpt
EMBEDDING_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_EMBED_MODEL=qwen3-embedding:0.6b
```

完全本地：运行 `powershell -File setup-ai.ps1 -WithLocalChat`，把 RAG_PROVIDER 改为 ollama。问答使用 qwen3:4b，额外下载约 2.5GB，无需会员登录。

兼容 API：设置 RAG_PROVIDER=openai、EMBEDDING_PROVIDER=openai，填写 AI_API_KEY、AI_BASE_URL、AI_MODEL 和 AI_EMBED_MODEL。不同嵌入服务可独立填写 AI_EMBED_BASE_URL / AI_EMBED_API_KEY。远端必须 HTTPS，本机允许 HTTP。会员登录与普通 API Key 是独立接入方式。

RAG_PROVIDER=local 明确选择离线词法摘录。没有模型配置的旧安装也保留此模式。看到“摘录”意味着本次没有完成 AI 生成。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/check_boundaries.py
```

回归测试使用合成数据和模拟远端服务，覆盖隔离、向量持久化、迁移、结构化引文、故障与授权边界，数量以当前输出为准。另有 14 项实际容量/长度边界检查。它们不能替代真实模型验收。

`tools/check_real_rag.py` 对已配置的 Ollama 或兼容 API 做实际验证，使用临时库，要求语义检索＋真实生成＋有效引文，降级不会冒充通过。会员方案在实际授权账户内创建合成验收库测试，凭据保持原位。进展见 [RAG 修订验收](docs/rag-validation.md)。

## 工程与课程材料

| 目录 | 用途 |
| --- | --- |
| backend/ | API、切片、向量、模型与 ChatGPT 授权 |
| frontend/ | 中文网页、资料、模型状态、问答与引用 |
| sample_docs/ | 三份虚构教学样例 |
| tests/、tools/ | 回归、边界、真实模型验证 |
| docs/ | 需求、架构、演示、验收 |
| coursework/ | 需求报告、离线低保真原型、答辩讲稿 |
| .vscode/ | 启动、调试、测试与模型安装任务 |
| data/、.runtime/ | 本机数据和模型，不提交 |

课程入口：[报告与原型](coursework/README.md)、[架构](docs/architecture.md)、[演示](docs/demo.md)。小组编号、成员姓名/学号及提交日期仍需填写。原型使用模拟数据；学生访谈、用户体验和性能指标不能用代码测试代替。

仓库：[Cccchenzi/-](https://github.com/Cccchenzi/-)。默认只监听本机；停止服务后备份 data/。授权文件绑定当前 Windows 账户，不能视为跨电脑免登录凭据。不要把自己的文档、数据库、模型或 .env 上传 GitHub。
