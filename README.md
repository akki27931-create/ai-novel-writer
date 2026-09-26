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

- 🔒 **数据全在本地**：书稿、向量库、网关 Key 都留在你自己的机器上
- 🧠 **真的会"读"书**：拆书产出人物卡 / 世界观 / 时间线 / 未回收伏笔 / 文风样本
- 🧭 **剧情状态层（关键）**：逐章记录「谁在哪、在做什么、手上有什么、知道什么」，续写时作为一等上下文注入 —— 这是续写不再前后打架的根本原因
- 🎯 **严格 RAG**：不会把全书塞进上下文，成本可控（整本拆书约几毛钱）
- 🔎 **关键词必召回**：人名 / 物品名 / 地点名做字面兜底召回，补上语义检索漏掉的关键锚点
- 🤖 **全自动续写**：目标留空，AI 自己推演剧情；还能「一键连写 N 章」
- ✍️ **从零原创**：也可以不给原文，让 AI 先立故事圣经 + 逐章计划，再一章一章写下去
- 🔌 **多网关可切换**：任何 OpenAI 兼容接口（DeepSeek / 硅基流动 / 火山方舟 / 通义 / 本地 Ollama·vLLM）都能接，随时切换
- 🧩 **可插拔**：向量库、向量模型、LLM、番茄 MCP 全部可换，装不上会自动降级而不是报错

> ⚠️ **免责声明**：本项目仅供个人学习研究使用。请勿公开传播或商用通过第三方渠道获取的小说内容，本项目不内置任何小说内容。
> 仓库里的 `samples/星陨荒原.txt` 是本项目自带的**原创测试稿**，可自由使用。
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

网页里的顺序：**导入小说 → 重新拆书（含建立剧情状态层）→ 点「🤖 一键续写下一章」**。

不想导入别人的书？首页「从零原创」可以让 AI 先立设定 + 排逐章计划，再一章一章写下去。
想验证续写一致性，用自带的 `samples/星陨荒原.txt` 跑一遍，步骤见
[如何验证续写一致性](#如何验证续写一致性)。

> 🆕 **本次改造重点**：新增**剧情状态层**（逐章记录人物位置 / 在做什么 / 持有物 / 已知信息），
> 续写时作为最高优先级上下文注入；新增**关键词必召回**（补上语义检索漏掉的关键锚点）；
> 上一章改为**整章**送入；上下文预算按优先级重排；LLM 改为**多网关可切换**并分**三档模型**；
> 新增**从零原创模式**。旧数据可平滑迁移，见 [从旧版本升级](#从旧版本升级数据库迁移)。

---

## 目录

- [功能一览](#功能一览)
- [剧情状态层：续写为什么不再前后打架](#剧情状态层续写为什么不再前后打架)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [配置 LLM 网关](#配置-llm-网关)
- [模型名与三档模型路由](#模型名与三档模型路由)
- [导入小说](#导入小说)
- [使用流程](#使用流程)
- [从零原创模式](#从零原创模式)
- [如何验证续写一致性](#如何验证续写一致性)
- [从旧版本升级（数据库迁移）](#从旧版本升级数据库迁移)
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
| 剧情状态层 | 逐章抽取并滚动维护**人物位置 / 正在做的事 / 持有物品 / 已知信息 / 未决线索**，是续写一致性的依据 |
| 上下文控制 | 按优先级拼装：**剧情状态快照 → 上一章全文 → 未回收伏笔 → 大纲 → 人物卡/世界观/文风 → 检索片段**，可预览实际发送的 prompt |
| 检索 | 语义召回（向量）+ **关键词必召回**（人名/物品名/地点名/伏笔关键词字面兜底）双路合并，严格排除未来章节防剧透 |
| AI 续写 | RAG 检索 + 内置续写提示词，**全自动模式（AI 自己决定剧情与章节名）**、只生成正文 / 只生成大纲 / 润色正文 / 重写正文，支持流式输出 |
| 一键连写 | 「连写 N 章」：每章自动推演剧情 → 写正文 → 自动入库 → **抽状态** → 建索引，第 N 章真正读到第 N-1 章的状态 |
| 从零原创 | 一句话设定 → AI 产出故事圣经（总纲/人物/世界观/伏笔/文风）→ 逐章计划 → 按计划逐章写 |
| 多网关 | 任意 OpenAI 兼容接口，可配置多个并存、随时切换；三档模型分别用于正文 / 复杂任务 / 廉价任务 |
| 伏笔闭环 | 每章抽取时同步：新埋的伏笔自动入库、本章回收的自动标记为「已回收（第 N 章）」 |
| 成本统计 | 记录每次请求的 input/output token 与预估费用（可按网关分别配置单价） |
| 后台任务 | 拆书 / 状态抽取 / 向量化 / 连写 / 番茄下载都是后台任务，网页实时显示进度 |
| 设置 | 网关、三档模型、温度、max_tokens、top_k、状态参数、汇率、提示词全部可视化配置 |

---

## 剧情状态层：续写为什么不再前后打架

**旧版的根本问题**：为了让上下文便宜，只把「上一章最后 3000 字」+ 少量语义检索片段喂给模型。
但「人物现在在哪、正在做什么、手上拿着什么、知道哪些信息」属于**瞬时状态**，
靠模型从碎片里猜，写到十几章必然前后矛盾。而且旧版 AI 写的章节**不会回写**人物卡和章节摘要，
越写越偏。

**现在的做法**（`backend/app/services/state.py`）：

1. **逐章抽取**：每写完一章，用廉价模型从正文里抽取客观状态 —— 只记录正文真实写到的内容，
   不允许推测。产出写入 `chapter_states` 表。
2. **滚动快照**：抽取结果 merge 进 `character_states` 表（每人一条），
   因此「谁在哪、在做什么」永远指向此刻，而不是拆书那一刻。
3. **一等上下文**：续写时把「截至上一章的状态快照」注入提示词，并**明确告知谁不在场**，
   人物位置、持有物、已知信息都有据可依。
4. **闭环维护**：同一趟里还会把 AI 章节的摘要回写进 `chapter_summaries`（补上旧版的信息断层），
   并自动维护伏笔的「新埋 / 已回收」。

状态快照长这样：

```
【截至第9章的状态】
场景：青云宗藏经阁第七层
时间：上山第十天夜里
【上一章结束时在场人物】
- 秦风（位置：藏经阁第七层；在做：推开七层木门；目标：确认残片；情绪：手抖；持有：无）
【其余人物现状（不在场者，本章不应无故出现）】
- 柳如烟（最后出场：第6章；位置：青石镇；在做：养伤；状态：左肩骨刺）
- 老瘸子（最后出场：第7章；位置：青石镇铁匠铺；在做：替秦风保管青铜残片）
【关键物品归属】
- 青铜残片 → 在老瘸子手上（第7章托付）
【悬而未决】
- （第9章末）匣底那块完整残片是谁放进去的
【最近剧情脉络】
第7章：…… 第8章：…… 第9章：……
```

状态快照是**结构化压缩**的：它比把前 20 章原文塞进上下文便宜得多，信息却更准。
所以这不是「花更多钱换一致性」，而是「花更少的钱拿到更好的一致性」。

**代价**：每章多一次廉价模型的调用（输入约一章正文，输出一段 JSON）。
按 DeepSeek 的廉价档估算，一章几厘钱级别。可以在设置里关掉「保存后自动抽取」。

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11+ / FastAPI / SQLAlchemy 2.0 |
| 数据库 | SQLite（业务数据；`chapter_states` / `character_states` / `providers` 为本次新增表） |
| 向量库 | ChromaDB 嵌入式（≥1.0，预编译 wheel 免编译；安装失败时自动降级为内置文件向量库） |
| 向量模型 | 本地 `BAAI/bge-small-zh-v1.5`（可选）；未安装时降级为内置字符 n-gram 哈希向量 |
| 前端 | React 18 + TypeScript + Vite + Tailwind CSS |
| LLM | 任意 **OpenAI 兼容**接口（DeepSeek / 硅基流动 / 火山方舟 / 通义 / 本地 Ollama·vLLM），多网关可切换 |
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

## 配置 LLM 网关

任何 **OpenAI 兼容**接口都能接。在「设置 → LLM 网关」里可以配置**多个**网关并存，随时切换：

| 字段 | 说明 |
| --- | --- |
| 名称 | 自定义，例如 `deepseek` / `siliconflow` / `本地ollama` |
| Base URL | 例如 `https://api.deepseek.com`、`https://api.siliconflow.cn/v1`、`http://localhost:11434/v1` |
| API Key | 只保存在本机 SQLite（`backend/data/app.db`），接口只返回脱敏值 `sk-abc1...wxyz` |
| 正文 / 复杂 / 廉价模型 | 三档模型名，见下一节 |

首次启动会自动用旧配置建一个默认网关；也可以直接手动新建。没有启用任何网关时，会退回
「旧版单网关兜底」那组字段（`api_key` + `base_url` + 模型名）。

**环境变量方式**（Docker / CI 推荐，作为兜底优先级最低一档）：

```dotenv
DEEPSEEK_API_KEY=sk-你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

> API Key 永远不会被硬编码在代码里。`.env` 与 `backend/data/` 都已在 `.gitignore` 中忽略。

本地模型（Ollama / vLLM）示例：Base URL 填 `http://localhost:11434/v1`，
API Key 随便填一个非空字符串（例如 `ollama`），模型名填 `qwen2.5:14b` 之类。

---

## 模型名与三档模型路由

路由分三档（`backend/app/services/llm.py::_pick_model`）：

| 档位 | 字段 | 负责的任务 | 选型建议 |
| --- | --- | --- | --- |
| 廉价 | `cheap_model` | **剧情状态抽取**、分章摘要 | 调用量最大，用最便宜的模型 |
| 复杂 | `reasoning_model` | 拆书、大纲推演、伏笔/矛盾检查 | 推理能力强 |
| 正文 | `text_model` | 续写、润色、重写 | 文笔好、够快 |

某档留空时会逐级回退到正文模型，所以任何配置都能跑起来。每次生成前也可以在续写页手动指定模型。

> 📌 **重要：模型名必须和你的网关一致，而它只能从网关拿**
>
> 仓库**不再预设任何模型名**（故意留空）。原因很简单：各家网关的模型名都不一样，
> 预设一个必然出错 —— 早先版本预设的 `deepseek-v4.1-flash` / `deepseek-v4.1-pro`
> 是需求文档里的**占位名**，真实账号里通常不存在，照抄会直接吃一个难懂的 403：
>
> ```
> Error code: 403 - {'error': {'message': 'This token has no access to model
> deepseek-v4.1-pro', 'type': 'invalid_request_error'}}
> ```
>
> 所以标准动作只有三步（设置页会自己校验并弹红色提示）：
>
> 1. 「设置 → 模型路由」→ 点「**拉取可用模型**」（免费接口，列出你账号真实可用的名字）
> 2. 点红色提示里的「**一键修正**」（一次性填好正文 / 复杂 / 廉价三档）
> 3. 点「**保存设置**」
>
> 若还在用早先版本留下的占位名，**启动时后端就会打印警告**提醒你去做上面这三步，
> 不用等到生成失败才发现。模型名一个都没配时，生成会立刻给出同样的指引，
> 而不是猜一个名字去撞 403。

### ⚠️ max_tokens 要给足（否则拆书会失败）

很多推理模型会先"思考"再输出，而**思考 token 也算在 `max_tokens` 里**。

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
   后台会依次执行：章节分块向量化 → 分批生成分章摘要 → 全局拆书 → **建立剧情状态层**。
   对话框里的「同时建立剧情状态层」默认勾选，**建议保持开启**（这是续写一致性的前提）。
3. 在「拆书结果」里检查并**手动修正**人物卡 / 世界观 / 伏笔（可以单板块重跑，单板块重跑不会重做状态层）。
4. 切到「续写」页（或直接在书籍页点「🤖 一键续写下一章」）：
   - **全自动模式（默认）**：什么都不用填。AI 读取总纲 + 当前剧情状态 + 未回收伏笔 + 最近章节摘要 + 上一章全文，
     自己决定本章剧情、自己拟章节名，写完自动入库**并自动抽取本章状态**。
   - **手动模式**：切到「我自己指定目标」，填本章目标；也可选模式（正文 / 大纲 / 润色 / 重写）和模型。
   - 想一次多写几章：用「🚀 一键连写 N 章」。每章的流程是
     **推演 → 写正文 → 入库 → 抽状态 → 建索引**，所以第 N 章真正对齐的是第 N-1 章的状态。
   - 先点「**预览上下文**」可以确认喂给模型的材料（留空目标时会真实调用一次模型来推演剧情）。
   - 右侧「**剧情状态**」卡片能看到每个人物当前的位置/在做的事/持有物，以及哪些章节还没建状态。
5. 生成完成后：「复制」「保存为章节」「一键润色」「重写」「检查伏笔/矛盾」。
6. 「用量」页查看 token 消耗和预估费用。

### 内置的续写提示词

`backend/app/prompts.py` 里集中了全部模板，可以在「设置 → 提示词」里在线覆盖（覆盖内容存数据库，可一键恢复内置）：

| 模板 key | 用途 |
| --- | --- |
| `continue_chapter` | 续写正文（含**硬性一致性要求**：必须接上一章结尾、人物位置与状态必须对齐） |
| `outline_only` / `polish_text` / `rewrite_text` | 只出大纲 / 润色 / 重写 |
| `chapter_summary` / `book_analysis` | 拆书的 Map / Reduce 两步 |
| `extract_state` / `extract_state_batch` | 剧情状态抽取（单章 / 批量） |
| `plan_next_chapter` | 自动推演下一章（会看到状态快照与既定章节计划） |
| `consistency_check` | 伏笔与逻辑矛盾检查 |
| `original_bible` / `plan_chapters` | 从零原创的故事圣经 / 逐章计划 |

发送给模型的上下文**按优先级**拼装：

```
1. 剧情状态快照（谁在哪、在做什么、手上有什么、谁不在场、悬而未决）  ← 最高优先
2. 上一章全文（超过预算才截断；可在设置里关掉「整章」改为只送末尾 N 字）
3. 未回收伏笔
4. 全书总纲 / 分卷摘要 / 本章既定计划（原创模式）
5. 相关人物卡 + 相关世界观 + 文风样本
6. 原文片段：语义召回 top-k + 关键词必召回（★标记的「关键锚点」）
```

检索时会**硬性排除目标章节及其之后的章节**，避免剧透和剧情穿越（关键词召回同样遵守）。

---

## 从零原创模式

不想受别人原文约束时，直接用「首页 → 从零原创」：

1. 填书名 + 题材，主角设定 / 金手指可以留空让 AI 设计。
2. AI 产出**故事圣经**：总纲、分卷大纲、人物卡（≥6 人）、世界观、长线伏笔、文风约定。
3. 同时排出**逐章计划**（每章标题 + 目标 + 要推进的伏笔），建书时可以指定排多少章。
4. 之后每章都走普通续写流程：`writer.build` 会**优先采用章节计划里的既定目标**，
   AI 只补细节、不改主线；状态层照常维护人物位置与持有物。

**为什么原创反而更省心**：设定是「你和模型一起定」的，模型全程握着完整世界观与人物表；
而续写别人的书，模型只能靠前文片段去反推设定。「从零写更稳」的直觉是对的 ——
但更准确的说法是：**关键在有没有状态层**。续写模式加上状态层后，一致性同样有保障。

计划可以在书籍页继续「续排」（例如先排 20 章，写到第 15 章时再排 21~60 章）。

### 排计划是「分批」的，不是一次吐 20 章

早先版本一次让模型输出 20 章计划，在推理模型上要等好几分钟（实测单次 89 秒），
期间进度条一动不动，看起来就像卡死了；而且输出很容易撞 `max_tokens` 被截断。

现在按 `plan_batch_size`（默认 **5** 章/批）分批调用，每批结束都会更新进度，
并且后一批会带上「上一批的最后几章」做衔接，不会各写各的。
`设置 → 生成参数` 里可以调批大小：批越小越快、需要调的次数越多。

另外所有 LLM 请求现在都有**显式超时**（`llm_timeout`，默认 600 秒）和有限重试
（`llm_max_retries`，默认 1 次）。早先没设超时，用的是 SDK 默认的 600 秒 × 2 次重试 ——
上游异常时任务能静默挂半小时，这正是「进度一直卡着不动」的另一半原因。
现在超时会被主动中断，并给出可操作的提示。

---

## 如何验证续写一致性

仓库自带一份原创测试稿 `samples/星陨荒原.txt`（9 章，本项目自有内容，可自由使用）。
它刻意埋了四类「最容易对不上」的陷阱：

| 陷阱 | 具体埋点 | 观察点 |
| --- | --- | --- |
| 物品易手 | 青铜残片：第1章秦风捡到 → 第2章柳如烟拿走研习 → 第3章归还 → **第7章交给老瘸子保管** | 续写第10章时，残片应当在**老瘸子**手上，而不是秦风怀里 |
| 人物离场 | 第6章柳如烟留在青石镇养伤，此后不再出场 | 第10章不应让柳如烟凭空出现在青云宗 |
| 长线伏笔 | 第2章提到「残片外圈缺的一角叫陨心，被青云宗压在藏经阁第七层」 | 第9章看到完整残片后，AI 应当主动推进这条伏笔 |
| 有据可查的知情信息 | 第4章秦风得知「黑潮三日内至」，第3章柳如烟告知「星陨遗物认主后会吃星气变强」 | 续写时不应让秦风表现得「不知道」这些事 |

**操作步骤**：

1. 首页「上传 TXT / EPUB」导入 `samples/星陨荒原.txt`。
2. 书籍页 →「重新拆书」，保持「同时建立剧情状态层」勾选。
3. 打开「续写」页 → 右侧「剧情状态」卡片应看到：
   - `秦风` 的位置为青云宗藏经阁第七层；
   - `老瘸子` 的位置为青石镇铁匠铺，且**持有：青铜残片**；
   - `柳如烟` 最后出场为第 6 章。
4. 点「预览上下文」→ 展开「★ 当前剧情状态」，确认残片归属、离场人物都被写进去了。
5. 目标留空，点「一键续写下一章」。检查生成结果是否：残片仍在老瘸子手上、柳如烟没有出现、
   剧情往「陨心 / 藏经阁第七层」推进。
6. 想对比的话，可以到「设置 → 剧情状态」把「上一章按整章送入上下文」关掉、并关掉关键词必召回，
   再跑一次 —— 差别通常一眼可见。

不消耗 API 的快速回归（离线自检，含测试稿的分章 / 分块 / 关键词召回用例）：

```bash
cd backend && .venv/Scripts/python scripts/smoke_test.py     # 152 项检查，退出码 0 表示全通过
```

---

## 从旧版本升级（数据库迁移）

升级后**第一次启动会自动建新表、补新列**（`init_db()` → `Base.metadata.create_all` + 轻量列迁移），
旧数据不会丢。新表：

| 表 | 用途 |
| --- | --- |
| `chapter_states` | 每章的剧情状态增量 |
| `character_states` | 人物的滚动状态快照 |
| `providers` | LLM 网关配置（多网关） |

新增列：`books.kind`、`book_analyses.chapter_plan`、`chapters.state_extracted`。

也可以手动跑迁移脚本（幂等，可反复执行）：

```bash
cd backend
.venv/Scripts/python scripts/migrate.py --dry-run   # 先看会改什么
.venv/Scripts/python scripts/migrate.py             # 真正执行
```

它会顺带做三件事：

1. 把老书的 `kind` 统一置为 `imported`；
2. 用拆书得到的人物卡初始化 `character_states`（作为状态快照的起点）；
3. 用旧配置（`base_url` / `api_key` / 模型名）自动建一个默认网关，保证开箱即用；
   并清理旧版 `app_settings` 里的明文 `api_key`（已由 `providers` 表接管）。

**老书的历史章节还没有剧情状态**，需要补建（会消耗 API 额度，走廉价模型档）：

- 网页：「续写」页右侧「剧情状态」→ 点「**补建状态**」；
- 或调接口：`POST /api/books/{id}/state/extract`。

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
│   ├── app/
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
│       │   ├── state.py        # ★ 剧情状态层：查看 / 补建 / 清空
│       │   ├── original.py     # ★ 从零原创：故事圣经 / 章节计划
│       │   ├── writing.py      # 续写、流式生成、伏笔检查
│       │   ├── settings.py     # 设置、LLM 网关、提示词、模型列表、用量
│       │   ├── fanqie.py       # 番茄 MCP
│       │   └── system.py       # 健康检查、后台任务
│       └── services/
│           ├── llm.py          # OpenAI 兼容客户端 + 三档模型路由 + 多网关 + token 统计
│           ├── state.py        # ★ 剧情状态层：逐章抽取 + 人物快照 + 伏笔闭环
│           ├── original.py     # ★ 从零原创：故事圣经 + 逐章计划
│           ├── embeddings.py   # 本地向量模型 / 哈希向量兜底
│           ├── vector_store.py # ChromaDB + 文件向量库兜底（含关键词必召回）
│           ├── indexing.py     # 章节分块向量化
│           ├── rag.py          # ★ 上下文拼装（状态快照优先 + 双路召回）
│           ├── analyzer.py     # 拆书 Map-Reduce（末尾接状态层建设）
│           ├── writer.py       # 续写/润色/重写/保存章节 + 写完即抽状态
│           ├── importer.py     # TXT/EPUB 解析与入库
│           ├── fanqie.py       # 番茄 MCP stdio 客户端（可插拔）
│           ├── text_utils.py   # 中文分章、分块、字数统计
│           ├── usage.py        # 用量统计
│           └── jobs.py         # 后台任务管理
│   └── scripts/
│       ├── smoke_test.py       # 离线端到端自检（152 项，不联网、不需要真实 Key）
│       ├── migrate.py          # 旧库迁移（建表 / 补列 / 回填）
│       └── rebuild_index.py    # 重建向量索引
├── samples/
│   └── 星陨荒原.txt             # ★ 自带原创测试稿（9 章，用于验证续写一致性）
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
| **POST** | **`/api/books/original`** | **从零原创：生成故事圣经并建书（后台任务）** |
| **POST** | **`/api/books/{id}/plan-chapters`** | **排 / 续排章节计划（后台任务）** |
| **POST** | **`/api/books/{id}/plan-chapters/sync`** | 同上，同步版本（少量章节时用） |
| GET | `/api/books/{id}/chapters` | 章节列表 |
| GET | `/api/chapters/{id}` | 章节正文 |
| GET | `/api/books/{id}/analysis` | 拆书结果（含 `chapter_plan`） |
| PUT | `/api/books/{id}/analysis` | 手动修改拆书结果 |
| POST | `/api/books/{id}/analyze` | 启动拆书任务（`with_states` 控制是否建状态层） |
| POST | `/api/books/{id}/vectorize` | 建立向量索引 |
| **GET** | **`/api/books/{id}/state`** | **查看剧情状态（快照文本 + 章节状态 + 人物快照 + 缺失章节）** |
| **POST** | **`/api/books/{id}/state/extract`** | **补建剧情状态层（走廉价模型）** |
| **DELETE** | **`/api/books/{id}/state`** | **清空状态（可只清某一章）** |
| POST | `/api/generate/preview` | 预览本次上下文（含 `story_state`） |
| POST | `/api/generate` | 一次性续写 |
| POST | `/api/generate/stream` | 流式续写（SSE，含 `status` 事件） |
| POST | `/api/generate/batch` | 一键连写 N 章（全自动，后台任务） |
| POST | `/api/generations/{id}/save` | 生成结果保存为章节（并自动抽状态） |
| POST | `/api/consistency-check` | 伏笔 / 逻辑矛盾检查（已纳入状态快照） |
| GET | `/api/fanqie/status` | 番茄 MCP 状态 |
| GET/PUT | `/api/settings` | 读取 / 修改设置（返回值含 `providers`） |
| **GET/POST** | **`/api/providers`** | **列出 / 新建 LLM 网关** |
| **PUT/DELETE** | **`/api/providers/{id}`** | **修改 / 删除网关** |
| **POST** | **`/api/providers/{id}/activate`** | **切换当前网关** |
| GET | `/api/settings/models` | 拉取该网关可用模型（`?provider_id=`） |
| GET | `/api/settings/validate` | 校验三档模型名 |
| POST | `/api/settings/autofix-models` | 一键修正模型名 |
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
A：到「设置 → LLM 网关」确认当前启用的网关填了 Key（留空表示不修改，不会覆盖已存的）。
用环境变量 `DEEPSEEK_API_KEY` 时注意不要有多余空格/引号。

**Q：拆书很慢 / 花钱多？**
A：拆书时填上「只拆前 N 章」（比如 50）先用少量章节试跑。三档模型分工明确：
分章摘要和剧情状态抽取走**廉价模型**，只有全局拆书那一步走复杂模型；已生成的章节摘要会复用。
另外剧情状态层可以单独补建，不必重跑整个拆书。

**Q：续写内容接不上前文（人物位置、手上东西、谁知道什么对不上）？**
A：这是本项目这轮改造专门解决的问题，按顺序排查：

1. 「续写」页右侧「剧情状态」卡片 → 看**人物位置 / 在做的事 / 持有物**是否和你印象里一致。
   如果有「待补建 N 章」，点「**补建状态**」（老书升级后必做一次）。
2. 点「预览上下文」→ 展开「★ 当前剧情状态」，确认关键信息都进去了。
3. 确认「设置 → 剧情状态」里「上一章按整章送入上下文」和「关键词必召回」都是开着的。
4. 如果某个细节仍然丢，说明它没被状态抽取抓到：可以直接去
   `GET /api/books/{id}/state` 看那一章的 `present_characters` / `items`，
   或者用一条更明确的「本章目标」把它点出来。
5. 想反过来验证效果，可以关掉上面两个开关再跑一次对比。

**Q：AI 写的章节，摘要和人物卡会不会更新？**
A：会。每写完一章都会：抽取本章状态 → merge 进人物快照 → 把摘要回写进 `chapter_summaries`
→ 维护伏笔（新埋的入库、本章回收的标记为已回收）。所以连写第 N 章时，
看到的是包含第 N-1 章的完整信息，不会出现旧版那种「摘要永远停在拆书那一刻」的断层。

**Q：能用自己的 CodeBuddy 积分 / 其他 AI 平台的额度吗？**
A：CodeBuddy 官方**没有**对外开放的 API / API Key，积分只能在 IDE 内使用，
第三方应用无法调用，所以接不了。但这个项目本来就是 OpenAI 兼容协议，
「设置 → LLM 网关」新建一个网关、填上任意平台的 Base URL + Key + 模型名就能用：
硅基流动、火山方舟、通义、本地 Ollama / vLLM 都可以，随时切换。

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
A：你网关上的模型名和填的不一样。到「设置 → 模型路由」点「拉取可用模型」，
或直接点红色的「一键修正」按钮（会一次修好正文 / 复杂 / 廉价三档）。

**Q：报 `403 ... This token has no access to model deepseek-v4.1-pro`？**
A：你的 Key 是有效的，只是**没有这个模型名的权限** —— 因为它根本不存在。
`deepseek-v4.1-flash` / `deepseek-v4.1-pro` 是需求文档里的**占位名**，
早先版本把它们当默认值，所以会一路带进网关配置。

按上面「模型名与三档模型路由」的三步走即可：「拉取可用模型」→「一键修正」→「保存设置」。
升级上来的老书注意：**网关里的旧模型名要手动改一次**，启动日志里会有黄色警告提醒你。

**Q：报「还没有配置模型名」？**
A：这是刻意的行为。仓库不预设模型名（猜错只会换来 403），所以第一次用要走一遍
「拉取可用模型 → 一键修正 → 保存设置」。设置页里那一栏是空的，不是在报错。

**Q：拆书 / 排计划 / 从零原创的进度条长时间不动，是卡死了吗？**
A：大多数情况是**在等模型返回**，不是在卡。判断方法看进度条右侧的「已运行 N 分 N 秒」：

- 时间在走、百分比不动 → 正常，正在等一次模型调用。推理模型一次生成较多内容要 1~3 分钟。
  进度条超过 15 秒会多显示一行说明。
- 想让它动起来 → 把「每批章节数」（拆书）/「每批排几章」（排计划）调小，批小了每批都快。
- 超过 `llm_timeout`（默认 600 秒）→ 会被**主动中断**并给出提示，不会无限挂着。
  觉得等太久就把 `llm_timeout` 调小，让失败来得更快一点。
- 想看真实进度 → 日志在 `backend/data/logs/app.log`，每次模型请求的耗时都记在里面。

**Q：日志里出现「向量索引与当前向量模型不匹配，已清空」？**
A：只有**真的换了向量模型**（`embedding_mode` / `embedding_model` 变了）时才会出现，
此时需要重新执行拆书或「建立向量索引」。早先版本对**全新书籍**也会误报这句，
已修正 —— 现在 `ensure_book()` 只在真正清空旧索引时返回 True。

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
- `samples/星陨荒原.txt` 是**本项目为验证续写一致性而原创绘写的测试稿**，不含任何第三方作品，可自由使用。
