# 知微

个人知识库问答助手。上传学习资料后，通过语义检索和 DeepSeek 生成带原文引用的回答，支持多轮追问与会话历史。

**Flask · SQLite · 原生 JavaScript · DeepSeek · Ollama**

## 功能

- **资料管理**：按知识库批量上传 PDF、Markdown、TXT，自动解析、分块和建立向量索引。
- **资料问答**：基于当前知识库回答问题，支持跨文档检索、文章概览和多轮追问。
- **引用溯源**：回答关联原文片段，可查看来源文件、页码或行号。
- **会话历史**：按账户和知识库保存对话、引用及当次回答模式。
- **模型选择**：默认使用 DeepSeek Flash，可在界面切换当前 API 支持的模型；向量模型在本机运行。

## 快速开始

环境：Windows x64、Python 3.11+。首次安装需要联网下载依赖、Ollama 和向量模型。

### 1. 安装

```powershell
git clone https://github.com/Cccchenzi/-.git zhiwei
cd zhiwei
.\setup.bat
.\setup-ai.bat
```

`setup-ai.bat` 会安装本地向量服务，并在 `.env` 不存在时从模板创建配置。

### 2. 配置密钥

在 [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) 获取 API 密钥，填入项目根目录的 `.env`：

```dotenv
DEEPSEEK_API_KEY=填入你的密钥
```

默认回答模型为 `deepseek-flash`，向量模型为 `qwen3-embedding:0.6b`。其他参数和模型接入方式见[配置说明](docs/configuration.md)。

### 3. 启动

```powershell
.\start.bat
```

浏览器打开 <http://127.0.0.1:8765>。也可在 VS Code 中打开 `zhiwei.code-workspace`，安装工作区推荐的 Python 扩展后，按 **F5** 调试或 **Ctrl+Shift+B** 启动。

注册账户 → 检查模型连接 → 创建知识库 → 上传资料 → 等待索引完成 → 提问并核对引用。初次体验可使用“导入示例”。

## 项目结构

```text
backend/       API、文档解析、检索与模型调用
frontend/      网页界面
sample_docs/   演示资料
tests/         自动化测试
tools/         验证脚本
docs/          配置、架构、需求与使用说明
coursework/    课程需求报告与交互原型
```

运行产生的数据、模型和密钥保存在 `data/`、`.runtime/`、`.env`，均不提交 Git。

## 文档与测试

- [配置说明](docs/configuration.md) · [架构说明](docs/architecture.md) · [使用演示](docs/demo.md)
- [需求文档](docs/requirements.md) · [课程报告与原型](coursework/README.md)
- [验证工具](tools/README.md) · [验证记录](docs/rag-validation.md)

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 使用边界

支持可提取文字的 PDF、Markdown 和 TXT，每批最多 10 份、单份 20 MiB；扫描 PDF 需先做 OCR。资料不足时拒答，生成失败时明确显示原文摘录。引用经过原文校验，结论仍需人工核对。

向量化在本机完成；使用 DeepSeek 问答时，问题、检索片段和必要历史会发送给 DeepSeek。应用默认仅监听本机地址。
