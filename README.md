# 本地 AI 小说续写网站

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![Tailwind](https://img.shields.io/badge/TailwindCSS-3.4-38BDF8?logo=tailwindcss&logoColor=white)
![VectorDB](https://img.shields.io/badge/VectorDB-ChromaDB-FF6B6B)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

> 一个**完全本地部署**的中文网文爽文 AI 续写工具：导入小说 → AI 拆书 → RAG 检索 → **一键自动续写**。

**它解决什么问题**：追更追到断更、或者想接着自己喜欢的小说往下写，但不想自己构思剧情。
把小说丢进来，AI 自动读完整本书的人物、世界观、伏笔和文风，然后**自己决定下一章写什么、自己起章节名**，
你只要点一个按钮。

- 🔒 **数据全在本地**：书稿、向量库、API Key 都留在你自己的机器上
- 🧠 **真的会"读"书**：拆书产出人物卡 / 世界观 / 时间线 / 未回收伏笔 / 文风样本
- 🎯 **严格 RAG**：不会把全书塞进上下文，成本可控（整本拆书约几毛钱）
- 🤖 **全自动续写**：目标留空，AI 自己推演剧情；还能「一键连写 N 章」
- 🧩 **可插拔**：向量库、向量模型、LLM、番茄 MCP 全部可换，装不上会自动降级而不是报错

> ⚠️ **免责声明**：本项目仅供个人学习研究使用。请勿公开传播或商用通过第三方渠道获取的小说内容，本项目不内置任何小说内容。
>
> 详细使用说明见 [使用说明.md](./使用说明.md)（小白向），本文档偏技术。也可以先跑 `backend/scripts/smoke_test.py` 离线自检。

---

## 快速开始（TL;DR）

```bash
git clone <本仓库地址> && cd <仓库目录>

# 方式 A：Docker 一键启动（推荐）
cp .env.example .env          # 填入 DEEPSEEK_API_KEY
docker compose up -d --build  # 前端 http://localhost:5173

# 方式 B：本地直接跑
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cd ../frontend && npm install && npm run build
cd ../backend && .venv/Scripts/python -m uvicorn app.main:app --port 8000   # 打开 http://localhost:8000
```

Windows 用户更简单：装完依赖后直接**双击 `启动.bat`**，浏览器会自动打开。

网页里的顺序：**导入小说 → 重新拆书 → 点「🤖 一键续写下一章」**。

---

## 目录

- [功能一览](#功能一览)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [配置 DeepSeek API Key](#配置-deepseek-api-key)
- [模型名与模型路由](#模型名与模型路由)
- [导入小说](#导入小说)
- [使用流程](#使用流程)
- [向量化说明](#向量化说明)
- [目录结构](#目录结构)
- [API 一览](#api-一览)
- [常见问题](#常见问题)

---

## 功能一览

| 模块 | 说明 |
| --- | --- |
| 小说导入 | 番茄小说（MCP）/ TXT / EPUB / 手动粘贴，自动识别编码（UTF-8/GBK/GB18030/BIG5）并切分章节 |
| 拆书 | 章节分块向量化 + Map-Reduce 拆书，产出**人物卡 / 世界观 / 时间线 / 伏笔 / 文风样本 / 大纲 / 分章摘要**，全部可手动编辑 |
| AI 续写 | RAG 检索 + 内置续写提示词，**全自动模式（AI 自己决定剧情与章节名）**、只生成正文 / 只生成大纲 / 润色正文 / 重写正文，支持流式输出 |
| 一键连写 | 「连写 N 章」：每章自动推演剧情 → 写正文 → 自动入库并建索引，第 N 章能读到第 N-1 章的内容 |
| 上下文控制 | 只发送「总纲 + 相关人物卡 + 相关世界观 + 未回收伏笔 + 最近 1~2 章原文 + 本章目标」，可预览实际发送的 prompt |
| 模型路由 | 正文类任务走 Flash，拆书/大纲/伏笔检查走 Pro；每次生成前可手动切换模型 |
| 成本统计 | 记录每次请求的 input/output token 与预估费用（可在设置页改单价） |
| 后台任务 | 拆书 / 向量化 / 番茄下载都是后台任务，网页实时显示进度，不阻塞操作 |
| 设置 | API Key、Base URL、模型名、温度、max_tokens、top_k、汇率、提示词全部可视化配置 |

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11+ / FastAPI / SQLAlchemy 2.0 |
| 数据库 | SQLite（业务数据） |
| 向量库 | ChromaDB 嵌入式（≥1.0，预编译 wheel 免编译；安装失败时自动降级为内置文件向量库） |
| 向量模型 | 本地 `BAAI/bge-small-zh-v1.5`（可选）；未安装时降级为内置字符 n-gram 哈希向量 |
| 前端 | React 18 + TypeScript + Vite + Tailwind CSS |
| LLM | DeepSeek API（OpenAI 兼容协议，`base_url=https://api.deepseek.com`） |
| 部署 | Docker Compose 一键启动；也支持本地直接跑 |

---

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
# 1. 准备环境变量
cp .env.example .env
#    然后编辑 .env，填入 DEEPSEEK_API_KEY

# 2. 一键启动
docker compose up -d --build

# 3. 打开浏览器
#    前端：http://localhost:5173
#    接口文档：http://localhost:8000/docs
```

数据（SQLite + 向量库 + 上传文件）保存在 Docker 卷 `novel-data` 中，重启不丢。
如果要连本地向量模型一起打进镜像：

```bash
docker compose build --build-arg INSTALL_EMBEDDINGS=1 backend
```

### 方式二：本地直接运行（开发用）

**后端**（需要 Python 3.11 或 3.12）：

```bash
cd backend

# 创建虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# （可选，推荐）安装本地中文语义向量模型，续写时检索更准
# Windows / 无独显机器建议装 CPU 版 torch（约 200MB，而不是默认 CUDA 版的 2.5GB）
pip install "torch>=2.2.0" --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-embeddings.txt

# （可选）离线自检：不联网、不需要真实 API Key，跑一遍完整业务链路
python scripts/smoke_test.py

# 回到项目根目录，准备好 .env
cd ..
cp .env.example .env   # 填入 DEEPSEEK_API_KEY

# 启动后端
cd backend
uvicorn app.main:app --reload --port 8000
```

**前端**（需要 Node 18+）：

```bash
cd frontend
npm install
npm run dev
# 打开 http://localhost:5173
```

Vite 已配置把 `/api` 代理到 `http://127.0.0.1:8000`，所以前端直接跑就能连上后端。

---

## 配置 DeepSeek API Key

有两种方式，**环境变量优先级更高**：

1. **环境变量（推荐，符合安全实践）**
   在项目根目录 `.env` 中：

   ```dotenv
   DEEPSEEK_API_KEY=sk-你的key
   DEEPSEEK_BASE_URL=https://api.deepseek.com
   ```

   Docker Compose 会自动读取同目录的 `.env`。

2. **网页设置页**
   打开「设置 → DeepSeek → API Key」填写，保存在本地 SQLite（`backend/data/app.db`），不会上传到任何第三方。
   设置页只显示脱敏后的 `sk-abc1...wxyz`，不会回传明文。

> API Key 永远不会被硬编码在代码里，`.env` 与 `backend/data/` 都已在 `.gitignore` 中忽略。

---

## 模型名与模型路由

**默认策略**（可在设置页调整）：

| 任务 | 默认模型 | 说明 |
| --- | --- | --- |
| 正文续写、扩写、润色、重写、分章摘要 | `deepseek-v4.1-flash` | 量大、要快、要便宜 |
| 拆书、大纲推演、伏笔检查、逻辑矛盾修复 | `deepseek-v4.1-pro` | 需要更强推理 |

> 📌 **重要：模型名必须和账号一致**
> 需求文档里写的 `deepseek-v4.1-flash` / `deepseek-v4.1-pro` 只是占位名。
> **2026-09 实测可用的是 `deepseek-flash` 和 `deepseek-v4-pro`**（没有 `v4.1`，也没有 `deepseek-chat`）。
> 填错的典型报错：
>
> ```
> The supported API model names are deepseek-flash, deepseek-v4-pro,
> but you passed deepseek-v4.1-pro.
> ```
>
> 设置页现在会自动校验模型名，配置错了会直接弹红色提示，并提供「**一键修正**」按钮。

### ⚠️ 启用 max_tokens 要给足（否则拆书会失败）

`deepseek-flash` 和 `deepseek-v4-pro` 都是**推理模型**：它们会先"思考"，再输出答案，
而**思考 token 也算在 `max_tokens` 里**。

所以 `max_tokens` 太小会出现这种症状：

- 日志里 `output` tokens 正好等于 `max_tokens`；
- 摘要报「第 N 批章节摘要失败」、「JSON 解析失败」；
- 续写生成的正文突然断在半句话。

**默认已调整为 32768**。如果你把模型换成别的推理模型，建议不要低于 16384。
另外拆书时「每批章节数」建议 3~5，批次越大输出越长、越容易触顶。

### 费用单价

设置页可改，默认参考值（美元 / 百万 token）：

| 模型 | 输入 | 缓存输入 | 输出 |
| --- | --- | --- | --- |
| deepseek-flash | 0.27 | 0.07 | 1.10 |
| deepseek-v4-pro | 0.55 | 0.14 | 2.19 |

> 名称里含 `pro` / `reason` / `r1` / `max` 的模型，未配置单价时按上表第二行估算，其余按第一行。
> 费用仅为估算，实际以 DeepSeek 官方账单为准。

费用为**估算值**，实际以 DeepSeek 官方账单为准。

---

## 导入小说

### 1. 番茄小说（需要 MCP server，当前上游接口不可用）

> ⚠️ **实测结论（2026-09）**：MCP 服务本身可正常启动并列出 14 个工具，
> 但它依赖的第三方数据接口 `http://101.35.133.34:5000` **已经下线**（TCP/Ping 均不可达），
> 因此**当前无法真正搜索和下载**。这属于外部服务问题，本项目无法修复。
> 请优先使用下面的 TXT / EPUB 上传；等接口恢复后，番茄导入即可直接使用。

配置方式（在 `.env` 或「设置 → 番茄 MCP」中填写）：

```dotenv
# 注意包名必须带 scope 前缀
FANQIE_MCP_COMMAND=npx -y @fysh925/mcp-server-fanqie
# 可选：上游数据接口地址（默认 http://101.35.133.34:5000）
FANQIE_API_BASE=
```

设置页提供「**运行完整自检**」，会分步报告：进程启动 → 工具列表 → 调用上游搜书接口，
方便区分“命令填错”和“上游接口挂了”。

适配器是**可插拔**的：启动 MCP server 后用 `tools/list` 自动发现工具，并按候选名优先级匹配
（`search_books` / `get_book_detail` / `get_simple_directory` / `get_chapter_content` 等），
参数名严格按官方工具签名构造（例如搜索用 `key` 而不是 `keyword`）。
因此换一个同类 MCP server 通常也能直接用。

> 需要 Node.js ≥ 16。Docker 镜像已内置 Node。

### 2. 上传 TXT / EPUB

首页「上传文件」：选择文件 → 填书名 → 上传。
TXT 会自动识别编码并**按「第X章 / 第X回 / Chapter N」切分章节**；没有章节标记时按约 3000 字一块切分。

### 3. 手动粘贴

首页「手动粘贴」：勾选「按第X章自动切分」即可粘贴多章内容。

---

## 使用流程

1. **导入**一本书（上面任一方式）。
2. 进入书籍页 → 点「**重新拆书**」。
   后台会依次执行：章节分块向量化（进度 0~25%）→ 分批生成分章摘要（25~60%）→ 全局拆书（60~100%）。
3. 在「拆书结果」里检查并**手动修正**人物卡 / 世界观 / 伏笔（可以单板块重跑）。
4. 切到「续写」页（或直接在书籍页点「🤖 一键续写下一章」）：
   - **全自动模式（默认）**：什么都不用填。AI 读取总纲 + 未回收伏笔 + 最近章节摘要 + 上一章结尾，
     自己决定本章剧情、自己拟章节名，写完自动入库。
   - **手动模式**：切到「我自己指定目标」，填本章目标；也可选模式（正文 / 大纲 / 润色 / 重写）和模型。
   - 想一次多写几章：用「🚀 一键连写 N 章」，每章都会重新推演、并以上一章为上下文。
   - 可先点「**预览上下文**」，确认喂给模型的材料是否合理（留空目标时会真实调用一次模型来推演剧情）。
5. 生成完成后：「复制」「保存为章节」「一键润色」「重写」「检查伏笔/矛盾」。
   全自动连写会自动保存，并立即建立向量索引，所以下一章能读到它。
6. 「用量」页查看 token 消耗和预估费用。

### 内置的续写提示词

`backend/app/prompts.py` 内置了需求指定的续写模板（Role / Context / Task / Output Format），
运行时动态填充大纲、核心设定、未回收伏笔、上一章结尾、本章目标、字数。
所有提示词都在这个文件里，也可以在「设置 → 提示词」里在线覆盖（覆盖内容存数据库，可一键恢复内置）。

发送给模型的上下文**只包含**：

```
全书总纲 / 分卷摘要
相关人物卡（按本章目标与检索结果筛选）
相关世界观设定
未回收伏笔
最近 1~2 章原文（上一章结尾取 3000 字）
本次目标章节之前的相关原文片段（向量检索 top-k）
用户填写的本章目标与字数
```

检索时会**硬性排除目标章节及其之后的章节**，避免剧透和剧情穿越。

---

## 向量化说明

- 默认使用本地模型 `BAAI/bge-small-zh-v1.5`（需 `pip install -r requirements-embeddings.txt`，首次运行自动下载约 100MB）。
- 未安装 `sentence-transformers` 时，自动降级为**内置的字符 n-gram 哈希向量**（纯 numpy，零额外依赖），RAG 依然可用，只是语义召回稍弱。网页上的拆书/续写流程完全不受影响。
- 更换向量模型后，向量库会自动重建，需要重新执行「建立向量索引」。
- 向量数据位置：本地模式 `backend/data/chroma/`，Docker 模式在 `novel-data` 卷中。

### 命令行重建索引

换了向量模型（例如把内置哈希向量换成 `bge-small-zh`）之后必须重建索引，
除了网页上的「建立向量索引」按钮，也可以直接用脚本：

```bash
cd backend
.venv\Scripts\python.exe scripts\rebuild_index.py            # 重建所有书
.venv\Scripts\python.exe scripts\rebuild_index.py --book-id 1
.venv\Scripts\python.exe scripts\rebuild_index.py --check "断剑 剑意"   # 顺便抽查检索效果
```

脚本只操作向量库，不会改动小说正文，可放心重复执行。

> **首次下载模型慢/失败？** 国内访问不了 `huggingface.co`，程序会自动改用镜像
> `https://hf-mirror.com`；也可以在 `.env` 里用 `HF_ENDPOINT` 指定别的源。

### 当前实际使用的向量后端

打开 <http://localhost:8000/api/system/info> 可以看到运行时诊断信息，例如：

```json
{
  "vector_backend": "chromadb",
  "embedding_mode": "sentence-transformers",
  "embedding_model": "BAAI/bge-small-zh-v1.5",
  "embedding_dimension": 512
}
```

- `vector_backend` 为 `chromadb` 表示用的是 ChromaDB；为 `json-fallback` 表示 ChromaDB 不可用，已降级。
- `embedding_mode` 为 `hash` 表示没装 `sentence-transformers`，用的是内置哈希向量。

---

## 目录结构

```
.
├── docker-compose.yml          # 一键启动（backend + frontend）
├── .env.example                # 环境变量模板
├── README.md
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt            # 核心依赖
│   ├── requirements-embeddings.txt # 可选：本地中文向量模型
│   └── app/
│       ├── main.py             # FastAPI 入口，生产模式顺带托管前端 dist
│       ├── config.py           # 环境变量配置
│       ├── database.py         # SQLite + SQLAlchemy
│       ├── models.py           # ORM 模型
│       ├── schemas.py          # 请求/响应模型
│       ├── settings_store.py   # 设置读写 + 费用估算
│       ├── prompts.py          # ★ 全部内置提示词模板
│       ├── routers/
│       │   ├── books.py        # 书籍/章节/上传/粘贴
│       │   ├── chapters.py     # 单章读写
│       │   ├── analysis.py     # 拆书、向量化
│       │   ├── writing.py      # 续写、流式生成、伏笔检查
│       │   ├── settings.py     # 设置、提示词、模型列表、用量
│       │   ├── fanqie.py       # 番茄 MCP
│       │   └── system.py       # 健康检查、后台任务
│       └── services/
│           ├── llm.py          # DeepSeek 客户端 + 模型路由 + token 统计
│           ├── embeddings.py   # 本地向量模型 / 哈希向量兜底
│           ├── vector_store.py # ChromaDB + 文件向量库兜底
│           ├── indexing.py     # 章节分块向量化
│           ├── rag.py          # ★ 上下文拼装与相关性筛选
│           ├── analyzer.py     # 拆书 Map-Reduce
│           ├── writer.py       # 续写/润色/重写/保存章节
│           ├── importer.py     # TXT/EPUB 解析与入库
│           ├── fanqie.py       # 番茄 MCP stdio 客户端（可插拔）
│           ├── text_utils.py   # 中文分章、分块、字数统计
│           ├── usage.py        # 用量统计
│           └── jobs.py         # 后台任务管理
└── frontend/
    ├── Dockerfile
    ├── nginx.conf              # 反向代理 /api（关闭缓冲以支持 SSE）
    └── src/
        ├── pages/              # 首页 / 书籍页 / 续写页 / 设置页 / 用量页
        ├── components/
        └── api.ts
```

---

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/system/info` | 向量后端 / 向量模型诊断 |
| GET | `/api/books` | 书籍列表 |
| POST | `/api/books/upload` | 上传 TXT / EPUB |
| POST | `/api/books/paste` | 粘贴导入 |
| GET | `/api/books/{id}/chapters` | 章节列表 |
| GET | `/api/chapters/{id}` | 章节正文 |
| GET | `/api/books/{id}/analysis` | 拆书结果 |
| PUT | `/api/books/{id}/analysis` | 手动修改拆书结果 |
| POST | `/api/books/{id}/analyze` | 启动拆书任务 |
| POST | `/api/books/{id}/vectorize` | 建立向量索引 |
| POST | `/api/generate/preview` | 预览本次上下文 |
| POST | `/api/generate` | 一次性续写 |
| POST | `/api/generate/stream` | 流式续写（SSE） |
| POST | `/api/generate/batch` | 一键连写 N 章（全自动，后台任务） |
| POST | `/api/generations/{id}/save` | 生成结果保存为章节 |
| POST | `/api/consistency-check` | 伏笔 / 逻辑矛盾检查 |
| GET | `/api/fanqie/status` | 番茄 MCP 状态 |
| GET/PUT | `/api/settings` | 读取 / 修改设置 |
| GET | `/api/settings/models` | 拉取账号可用模型 |
| GET | `/api/usage` | 用量与费用统计 |
| GET/PUT/DELETE | `/api/prompts` | 查看 / 覆盖 / 恢复内置提示词 |
| GET | `/api/jobs/{id}` | 后台任务进度 |

完整交互式文档：<http://localhost:8000/docs>

---

## 常见问题

**Q：报错「模型名不存在或账号无权限」？**
A：默认模型名按需求填写，但要以你账号实际可用为准。到「设置 → 拉取可用模型」，
把 `deepseek-chat` / `deepseek-reasoner` 填进去，或在续写页手动选模型。

**Q：报错「API Key 无效」？**
A：确认 `.env` 里的 `DEEPSEEK_API_KEY` 无误，且没有多余空格/引号。环境变量优先于设置页。

**Q：拆书很慢 / 花钱多？**
A：拆书时把「只拆前 N 章」填上（比如 50），先用少量章节试跑；分章摘要用的是便宜模型，
只有全局拆书那一步走 Pro。已经生成过的章节摘要会复用，不会重复计费。

**Q：续写内容接不上前文 / 出现剧透？**
A：先确认已经「重新拆书」并建立了向量索引；然后在续写页点「预览上下文」，
检查检索片段、人物卡、上一章结尾是否合理。也可以把「携带最近章节数」调到 2~3。

**Q：不想装 torch，能用吗？**
A：能。不装 `sentence-transformers` 时会自动降级为内置哈希向量，功能完全可用。

**Q：数据存在哪？能备份吗？**
A：全部在 `backend/data/`（Docker 下是 `novel-data` 卷）：`app.db` 是业务数据，
`chroma/` 是向量库，`uploads/` 是上传的 EPUB。整个目录拷走即可备份。向量库可随时重建。

**Q：拆书时摘要失败 / 报「JSON 解析失败」/ 正文写到一半断掉？**
A：几乎都是 `max_tokens` 太小。这个模型是**推理模型**，思考 token 也算在 `max_tokens` 里，
所以输出很容易被截断。到「设置 → 生成参数」把 `max_tokens` 调到 **32768**，
拆书时把「每批章节数」改成 **3~5**。

**Q：报「模型名不存在 / The supported API model names are ...」？**
A：你账号可用的模型名和填的不一样。到「设置 → 模型路由」点「拉取可用模型」，
或直接点红色的「一键修正」按钮。

**Q：遇到没见过的报错想去排查？**
A：日志文件在 `backend/data/logs/app.log`（Docker 里在 `novel-data` 卷的 `logs/app.log`），
里面记录了每次失败的完整原因，可以直接拿出来对照或发给别人看。

**Q：番茄小说搜不到 / 提示「番茄 MCP 不可用」？**
A：先到「设置 → 番茄 MCP」点「运行完整自检」看卡在哪一步：

- 如果**只有最后一步**（调用上游搜书接口）失败 → 是这个 MCP 依赖的第三方接口挂了，
  外部原因，本项目无法修复，请改用 TXT / EPUB 上传导入；
- 如果**前三步**就失败 → 检查命令是否写对（必须是 `npx -y @fysh925/mcp-server-fanqie`，
  不能漏掉 `@fysh925/`），以及电脑上是否装了 Node.js。

**Q：怎么把番茄的书变成 TXT？**
A：用任意开源下载工具导出 TXT，然后在首页「上传 TXT / EPUB」导入；后续拆书、续写完全一样。

**Q：pip 安装时报 `Microsoft Visual C++ 14.0 or greater is required`？**
A：这是旧版 chromadb（0.5.x）在 Windows 上编译 `chroma-hnswlib` 导致的。本项目已改用
`chromadb>=1.0`（预编译 wheel，无需编译器）。若仍报错，执行 `pip install "chromadb>=1.0,<2"`；
或者直接卸载 chromadb，程序会自动降级使用内置的文件向量库，功能不受影响。

**Q：端口被占用？**
A：改 `docker-compose.yml` 里的端口映射，或本地启动时 `uvicorn ... --port 8080`（同时改 `frontend/vite.config.ts` 的代理目标）。

---

## 版权声明

- 本项目使用 [MIT License](./LICENSE) 开源，欢迎 fork 和二次开发。
- 本项目仅用于**个人学习与研究**。
- 通过番茄小说等第三方渠道下载的内容版权归原作者与平台所有，请**不要公开传播或用于任何商业用途**。
- 使用第三方 MCP / 接口产生的任何风险由使用者自行承担。
- 本项目不内置任何小说内容，仓库中也不包含任何用户的书稿数据。
