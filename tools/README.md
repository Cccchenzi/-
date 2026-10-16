# 开发工具

在项目根目录运行。Python 工具使用 `setup.bat` 创建的虚拟环境。

## Python 检查

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/check_boundaries.py
.\.venv\Scripts\python.exe tools/check_real_rag.py --provider deepseek
```

| 命令 | 作用 / 准备 |
| --- | --- |
| `unittest discover` | 业务、隔离、向量、引文、授权及模型选择回归；使用模拟服务，无需 API Key |
| `check_boundaries.py` | 实际上传字节、批量数量及输入长度边界；使用临时数据，不调用模型 |
| `check_real_rag.py` | 真实嵌入、检索、生成、多轮和引用；先按[模型配置](../docs/configuration.md)准备服务，本地嵌入需 Ollama 已运行 |

独立脚本的 JSON 默认写入系统临时目录，可用 `--output <JSON路径>` 指定位置。真实模型检查使用临时合成资料，可能产生 API 费用；支持 `--provider deepseek/ollama/openai`，不支持 `chatgpt`。它使用部署默认配置，不读取正式账户的模型选择；指定模型可加 `--chat-model <模型ID>`。完整参数使用 `--help` 查看。

## 浏览器检查

需要 Node.js、Playwright 和 Microsoft Edge。安装工具依赖后运行：

```powershell
npm install --no-save --package-lock=false playwright
node tools/check_chat_layout.cjs
node tools/check_model_selector.cjs
```

两个脚本用实际无头 Edge 和模拟 API，分别检查长会话布局及模型选择交互，无需启动后端。JSON 和截图默认写入系统临时目录，可用 `--output <目录>` 指定位置。`PLAYWRIGHT_MODULE` 可指定已有模块路径，`EDGE_EXECUTABLE` 可指定 Edge 可执行文件。

## 本地运行时

`local_runtime.py` 由 `run.py` 调用，发现或启动已安装的本机 Ollama，不下载模型。首次安装运行项目根目录的 `setup-ai.bat`。
