# AI 学习助手后端

后端使用 Flask，已完成资料、聊天、测评、进度和周报接口，以及 DeepSeek 驱动的资料问答、测评生成、评分和学习周报。

## 目录

```text
backend/
├─ app/
│  ├─ api/          # 按资料、聊天、测评、进度、报告拆分的接口
│  ├─ models/       # 需求文档中的数据库模型
│  ├─ services/     # 文本匹配、DeepSeek 访问、测评及报告任务
│  ├─ config.py
│  ├─ extensions.py
│  └─ __init__.py   # Flask 应用工厂
├─ instance/        # SQLite、上传文件等本地运行数据
├─ tests/
├─ .env.example
├─ requirements.txt
└─ wsgi.py
```

## 本地启动

进入 `backend` 目录后执行：

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
Copy-Item .env.example .env
.venv/Scripts/python -m flask --app wsgi init-db
.venv/Scripts/python -m flask --app wsgi run --debug
```

服务默认地址为 `http://127.0.0.1:5000`。可用 `GET /api/v1/health` 检查连接。

## 配置

`.env` 中分别配置：

- `LLM_BASE_URL`：兼容 OpenAI API 的服务根地址；客户端会在其后调用 `/chat/completions`。
- `LLM_API_KEY`：访问密钥，只保存在本机环境文件中。
- `LLM_MODEL`：模型名称。
- `LLM_TIMEOUT_SECONDS`：单次请求超时秒数；网络错误和临时服务错误最多尝试 3 次。
- `DATABASE_URL`：数据库连接；默认使用 `backend/instance/learning_assistant.db`。
- `CORS_ORIGINS`：允许访问 API 的前端来源，以逗号分隔。

资料问答以问题和近期对话为关键词，在数据库保存的文本片段中匹配相关段落后交给 DeepSeek；出题读取所选版本的完整解析文本。代码不使用向量模型或 Embedding。

## DeepSeek 能力

- 聊天回答使用学习导师系统提示词，并只接受当前输入中真实存在的资料引用。
- 出题严格校验题型与难度配比、四选一结构、知识点、资料来源和简答评分规则。
- 客观题由后台精确判分；DeepSeek 负责简答题的 0／0.5／1 分评分和逐题反馈，总分由后台计算。
- 周报把日期范围内的上传、提问、已评分测评与薄弱点汇总给 DeepSeek，并保存统计和内容快照。
- JSON 响应兼容纯 JSON、`json` 前缀和 Markdown JSON 代码块。

## 接口范围

- 资料：同名检查、上传、列表、详情、解析失败重试、来源查看、笔记保存和物理删除。
- 聊天：创建、分页列表、发消息、历史消息和删除对话。发消息成功后才在同一事务中保存双方消息。
- 测评：异步出题、提交、异步评分、结果、评分重试和历史成绩。
- 进度：总览、当前版本知识点、薄弱点、资料详情、成绩趋势和学习笔记。
- 周报：异步生成、历史列表、详情和 UTF-8 Markdown 下载。

## 资料处理

- 上传先保存原文件并创建 `processing` 版本，接口返回 `202`；后台工作线程继续解析并将所有文字保存到 `material_chunks`。
- PDF 按页、PPT/PPTX 按幻灯片、Word 按段落及表格、Markdown 按标题段落、TXT 按文本块保存位置数据。
- `.docx/.pptx` 使用纯 Python 解析；旧版 `.doc/.ppt` 在 Windows 上使用已安装的 Microsoft Word 和 PowerPoint，只读且隐藏窗口打开。
- 解析完成后版本变为 `ready`；文件损坏、加密、扫描件或没有可提取文字时变为 `failed` 并保存失败原因。
- 删除资料会物理删除源文件、资料版本、解析文字及依赖这些源数据的记录。

## 校验

```powershell
.venv/Scripts/python -m pytest tests
```

可在 `backend` 目录执行 `python -m flask --app wsgi list-tables` 检查数据库中的表。
