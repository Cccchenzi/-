# 验证与本地模型工具

从项目根目录使用已安装依赖的 Python 环境运行。回归、边界、真实模型及界面检查验证不同层次，应分别记录；当前结果见 [RAG 修订验收](../docs/rag-validation.md)。

## 后端回归

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`tests/` 覆盖业务、语义索引、结构化引文、故障、数据隔离和会员授权边界。使用临时数据和模拟远端服务，不需要真实会员或 API Key；通过数量以本次输出为准，不能等同于真实模型问答准确率。

## 精确边界：check_boundaries.py

```powershell
.\.venv\Scripts\python.exe tools/check_boundaries.py
```

该脚本独立执行 14 项检查，不属于 `tests/` 的回归数量，不需要启动网页服务。结果 JSON 默认写入系统临时目录的新文件并打印位置；全通过退出码为 `0`，失败为 `1`。可通过 `--output` 指定报告文件：

```powershell
.\.venv\Scripts\python.exe tools/check_boundaries.py --output "$env:TEMP\zhiwei-boundary-results.json"
```

| 边界 | 条件与预期 |
| --- | --- |
| 批量上传 | 10 个内容不同的 TXT 成功；11 个拒绝且不增加资料 |
| 文件大小 | 实际 20 MiB（20,971,520 字节）有效 PDF 成功；大 1 字节被拒绝 |
| 用户名长度 | 2、32 个 ASCII 字符成功；1、33 个被拒绝 |
| 密码长度 | 8、128 个 ASCII 字符成功；7、129 个被拒绝 |
| 问题长度 | 空库接受 2000 字符并保存拒答消息；2001 字符被拒绝且不写入消息 |

20 MiB 文件是现场构造的单页文本 PDF，以无害空白扩充字节并保留正确对象偏移。它验证实际上传上限，不代表大篇幅 PDF 的解析性能；部分快速回归使用缩小常量，该脚本补充实际上限端点。

脚本强制本地摘录，使用临时数据库和合成资料，完成后清理；不读取用户知识库或调用外部模型。报告不保存密码、密钥或用户文件内容。它不覆盖所有 PDF 类型、提取字符/页数上限、检索质量、性能或并发。

## 真实模型：check_real_rag.py

该脚本使用应用测试客户端，但实际请求选定的生成和嵌入模型。它读取项目 `.env` 或环境变量中的配置，在临时数据库创建合成资料；不读取、修改或删除正常应用的数据目录。运行前须自行安装、启动或配置对应服务。

```powershell
# 已安装本地生成和嵌入模型，并确认 EMBEDDING_PROVIDER=ollama
.\.venv\Scripts\python.exe tools/check_real_rag.py --provider ollama

# 已在 .env 配置兼容 API 与嵌入服务；不要把密钥放进命令行
.\.venv\Scripts\python.exe tools/check_real_rag.py --provider openai
```

可用参数包括 `--provider`、`--ollama-url`、`--base-url`、`--chat-model`、`--embedding-model`、`--output`；完整参数用 `--help` 查看。`EMBEDDING_PROVIDER` 仍由配置决定，`--provider` 只选择生成接入方式。远程服务会接收合成问题、证据及必要上下文，并可能产生费用。

检查要求真实向量持久化、同义检索、跨文档生成、多轮指代及有效逐字引文，同时核对拒答、账户/知识库隔离和删除后检索。摘录降级不能作为正向生成通过。JSON 报告默认写入系统临时目录，可用 `--output` 指定位置；数量和结果以实际报告为准。

**目前该独立脚本仅支持 `ollama` 与 `openai`，不支持 `chatgpt`。** 不要复制会员令牌或授权文件到临时数据库来绕过这一限制。会员验收在本人已授权的应用账户内新建合成测试库完成，范围及结果见 [验收记录](../docs/rag-validation.md)。

## 本机运行时：local_runtime.py

此模块由启动流程调用，用于发现或启动已安装的本机 Ollama 服务；它不是验收脚本，也不会在应用启动时自动下载模型。首次安装使用项目根目录 `setup-ai.bat`，运行时与模型文件不提交 Git。推荐只安装 `qwen3-embedding:0.6b` 供会员方案向量化；完全本地生成的额外安装与配置见 [README](../README.md)。
