# 测试说明

以下为截至 2026-10-16 的验收记录，结果对应当时的代码、配置和测试资料。

| 验证范围 | 已记录结果 | 测试条件 |
| --- | --- | --- |
| 后端回归 | 108/108 | 临时数据、模拟远端服务；覆盖权限、导入、向量、问答、引用、授权和模型选择 |
| 容量与长度边界 | 14/14 | 实际 20 MiB 文件及超限文件，批量数量、用户名、密码和问题长度端点 |
| DeepSeek RAG | 12/12 | Flash 与本地 Qwen 嵌入；真实调用，合成资料，覆盖同义问法、多轮、拒答、引用和隔离 |
| 模型选择 | 10/10 | 实际模型目录、按账号保存、重启及历史；Flash、Pro 各一次带引用生成 |
| 单份 PDF 回放 | 5/5 | 只读已解析片段；四条真实生成和一条资料外拒答 |
| 长会话界面 | 26/26 | 实际 Edge，合成 API 响应；长回答、来源面板、桌面和窄屏布局 |
| 模型选择界面 | 20/20 | 实际 Edge，合成 API 响应；切换、失败提示、历史标签和登录竞态 |

真实 RAG 使用 `deepseek-flash` 和本机 `qwen3-embedding:0.6b`，模型选择另验证 `deepseek-v4-pro`。真实生成通过 Flask 测试客户端调用业务接口，浏览器检查未调用模型。检查数量包含连接、上传和隔离等步骤，不等于独立问答样本数，也不能合计为准确率。

## 运行检查

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/check_boundaries.py
.\.venv\Scripts\python.exe tools/check_real_rag.py --provider deepseek
node tools/check_chat_layout.cjs
node tools/check_model_selector.cjs
```

真实模型检查需要配置 API Key 和本地嵌入服务，会消耗 API 额度；使用临时合成资料，不读取日常知识库。浏览器检查需要 Playwright 和 Edge。参数、依赖及报告输出位置见[工具说明](../tools/README.md)。

真人任务实验、效率改善和性能目标尚未验证。有限案例不能保证所有文档都能正确回答；引用校验后仍需核对结论是否受原文支持。更换模型或服务配置后，应重新运行相关检查并记录结果。
