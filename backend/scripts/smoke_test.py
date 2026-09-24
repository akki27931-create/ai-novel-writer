"""离线端到端冒烟测试（不需要网络，也不需要真实 DeepSeek API Key）。

做法：
- 把 DATA_DIR 指到临时目录，避免污染真实数据；
- monkeypatch 掉 LLM 调用（chat / chat_json / chat_stream），返回可预期的假数据；
- 然后真实跑一遍：建库 → 导入分章 → 向量化 → 检索 → 拆书 → 拼装 RAG 上下文 →
  续写 → 保存为章节 → 通过 FastAPI TestClient 打接口。

用法（在 backend/ 目录下）：
    .venv\\Scripts\\python.exe scripts\\smoke_test.py
退出码 0 表示全部通过。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

TMP_DATA = tempfile.mkdtemp(prefix="novel-smoke-")
os.environ["DATA_DIR"] = TMP_DATA
os.environ["DEEPSEEK_API_KEY"] = "sk-smoke-test-only"
os.environ["DEEPSEEK_BASE_URL"] = "https://api.deepseek.com"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [OK] {name}")
    else:
        FAILED.append(f"{name} {detail}")
        print(f"  [FAIL] {name} {detail}")


# ======================================================================
# 假 LLM
# ======================================================================
from app.services.llm import ChatResult  # noqa: E402


def record_mock_usage(db, book_id, task: str, model: str, input_tokens: int, output_tokens: int) -> None:
    """假 LLM 也要写用量流水，否则 /api/usage 会是空的（真实 chat 内部会做这件事）。"""
    try:
        from app.services.llm import _record

        _record(db, book_id, task, model or "mock-model", input_tokens, output_tokens)
    except Exception:  # noqa: BLE001
        pass


async def fake_chat(db, *, messages, task, model=None, book_id=None, temperature=None, max_tokens=None, json_mode=False):
    text = (
        "第X章 模拟章节\n\n"
        "林越脚步一顿，回头看向那片翻涌的黑雾。\n"
        "“既然躲不过，那就打。”他握紧了手中的断剑。\n"
        "剑气冲霄，整座山谷都在震颤。\n"
    ) * 4
    record_mock_usage(db, book_id, task, model or "mock-flash", 800, 1200)
    return ChatResult(
        text=text,
        model=model or "mock-flash",
        input_tokens=800,
        output_tokens=1200,
        duration_ms=42,
    )


async def fake_chat_json(db, *, messages, task, model=None, book_id=None, max_tokens=None, temperature=None):
    prompt = messages[-1]["content"] if messages else ""

    # 自动推演下一章：真实实现用的是 plan_next_chapter 模板
    if "做规划" in prompt:
        plan = {
            "title": "比武大会",
            "goal": "主角在郡城比武大会上一路碾压，逼出隐藏的天才对手，末尾引出上古残魂。",
            "foreshadows_to_advance": ["断剑铭文"],
            "hook": "残魂苏醒",
        }
        record_mock_usage(db, book_id, task, model or "mock-pro", 600, 300)
        return plan, ChatResult(json.dumps(plan, ensure_ascii=False), "mock-pro", 600, 300, 15)

    if task == "summarize":
        numbers = [int(n) for n in re.findall(r"=== 第(\d+)章", prompt)]
        summaries = [
            {
                "chapter_number": n,
                "title": f"第{n}章",
                "summary": f"第{n}章摘要：主角遭遇冲突并解决，实力提升，末尾留下新线索。",
                "events": [f"事件{n}"],
                "characters": ["林越", "苏清月"],
            }
            for n in numbers
        ]
        return {"summaries": summaries}, ChatResult(json.dumps({"summaries": summaries}), "mock-flash", 100, 200, 10)
    record_mock_usage(db, book_id, task, model or "mock-pro", 1200, 900)
    payload = {
        "synopsis": "主角林越自边境小城崛起，一路逆袭，最终揭开封魔谷的上古真相。",
        "outline": {
            "volumes": [{"name": "第一卷 边城", "range": "第1-5章", "summary": "觉醒与初战", "key_points": ["觉醒剑意"]}],
            "current_stage": "主角刚离开边城",
            "next_directions": ["前往郡城参加比武"],
        },
        "characters": [
            {
                "name": "林越",
                "identity": "边城少年",
                "personality": "隐忍果断",
                "relations": "与苏清月互相扶持",
                "status": "练气三层，身怀上古剑意",
                "first_chapter": 1,
                "importance": "主角",
            },
            {
                "name": "苏清月",
                "identity": "药阁弟子",
                "personality": "外冷内热",
                "relations": "林越的同伴",
                "status": "安然",
                "first_chapter": 2,
                "importance": "重要配角",
            },
        ],
        "worldview": [
            {"category": "势力", "name": "封魔谷", "description": "上古封印之地", "first_chapter": 1},
            {"category": "道具", "name": "断剑", "description": "残破却蕴含剑意", "first_chapter": 1},
        ],
        "timeline": [{"chapter": 1, "event": "边城被袭", "impact": "主角觉醒"}],
        "foreshadows": [
            {
                "content": "断剑上的残缺铭文",
                "planted_chapter": 1,
                "status": "未回收",
                "possible_payoff": "指向封魔谷封印",
                "keywords": ["断剑", "铭文"],
            }
        ],
        "style": {
            "tone": "热血爽文，节奏紧凑",
            "common_words": ["剑意", "冷笑", "杀意"],
            "sentence_patterns": ["短句连击", "对话推进剧情"],
            "dialogue_style": "短促有力",
            "pacing": "快，三句话一个反转",
            "taboos": ["大段抒情"],
            "sample": "剑气冲霄，整座山谷都在震颤。",
        },
    }
    return payload, ChatResult(json.dumps(payload, ensure_ascii=False), "mock-pro", 1200, 900, 20)


async def fake_chat_stream(db, *, messages, task, model=None, book_id=None, temperature=None, max_tokens=None):
    record_mock_usage(db, book_id, task, model or "mock-flash", 500, 60)
    pieces = ["林越缓缓吐出一口气，", "抬剑指向对面。\n\n", "“来吧。”"]
    collected: list[str] = []
    for piece in pieces:
        collected.append(piece)
        yield {"type": "delta", "text": piece}
    text = "".join(collected)
    yield {
        "type": "done",
        "text": text,
        "model": model or "mock-flash",
        "input_tokens": 500,
        "output_tokens": 60,
        "duration_ms": 30,
    }


def patch_llm() -> None:
    import app.services.analyzer as analyzer
    import app.services.writer as writer

    analyzer.chat_json = fake_chat_json
    writer.chat = fake_chat
    writer.chat_json = fake_chat_json  # 自动推演剧情也走它
    writer.chat_stream = fake_chat_stream


# ======================================================================
SAMPLE_BOOK = """《剑起边城》

作者：测试作者

第一卷 边城

第一章 觉醒
林越蹲在城墙下，手里攥着一截断剑。
远处黑雾翻涌，守城的老卒已经倒下。
“既然躲不过，那就打。”
他握紧断剑，一股陌生的剑意自心底升起，直冲云霄。

第二章 药阁
苏清月背着药箱走进巷子，看见满地狼藉。
“你伤的太重了。”
她把药粉撒在伤口上，动作利落。
林越看着她，忽然觉得这座边城还没那么冷。

第三章 夜袭
黑雾中的影子终于动了。
数十道黑影扑向城墙，林越断剑横扫，剑气把最前面的两个影子切成碎片。
“原来这就是剑意。”他低声道。

第四章 追杀
郡城来的执法者堵住了巷口。
“交出断剑，饶你不死。”
林越笑了，笑得毫无温度。
他一步一步往前走，脚下的青石板寸寸碎裂。

第五章 出城
天亮时，边城的火终于熄了。
林越背着断剑站在城门口，回头看了一眼。
“我会回来的。”
苏清月跟在他身后，没有多说一个字。
"""


def test_text_utils() -> None:
    print("\n[1] 中文分章 / 分块")
    from app.services.text_utils import chunk_text, count_words, split_chapters, tail

    chapters = split_chapters(SAMPLE_BOOK)
    check("分章数量 = 5", len(chapters) == 5, f"实际 {len(chapters)}")
    if chapters:
        check("首章标题含「第一章」", "第一章" in chapters[0].title, chapters[0].title)
        check("首章正文非空", count_words(chapters[0].content) > 20)

    long_text = "这是一段测试文本。" * 200
    chunks = chunk_text(long_text, size=200, overlap=40)
    check("长文本被切成多块", len(chunks) > 1, f"实际 {len(chunks)}")
    check("tail 截断生效", len(tail(long_text, 50)) <= 52)

    # 无标题正文也要能切
    plain = "这是没有任何章节标题的正文。" * 500
    fallback = split_chapters(plain)
    check("无标题时按长度兜底切分", len(fallback) >= 2, f"实际 {len(fallback)}")


def test_embeddings() -> None:
    print("\n[2] 向量化（无模型时自动降级）")
    from app.services.embeddings import EmbeddingService

    embedder = EmbeddingService.get("BAAI/bge-small-zh-v1.5")
    print(f"      后端模式：{embedder.mode}，维度：{embedder.dimension}")
    a = embedder.encode_one("林越握紧断剑，剑意冲霄")
    b = embedder.encode_one("林越握紧断剑，剑意冲霄")
    c = embedder.encode_one("今天天气不错，我们去钓鱼")
    check("相同文本向量一致", a == b)
    check("向量维度正确", len(a) == embedder.dimension)

    from app.services.embeddings import cosine

    check("相似句相似度高于无关句", cosine(a, b) > cosine(a, c))


def test_import_and_index() -> None:
    print("\n[3] 导入 + 向量索引 + 检索")
    from app.database import SessionLocal, init_db
    from app.models import Chapter
    from app.services import importer
    from app.services.indexing import get_store, index_chapters
    from app.services.text_utils import split_chapters

    init_db()
    db = SessionLocal()
    try:
        book = importer.create_book(
            db,
            title="剑起边城",
            author="测试作者",
            source="txt",
            chapters=split_chapters(SAMPLE_BOOK),
        )
        check("书籍入库", book.id is not None)
        check("章节数 = 5", book.total_chapters == 5, str(book.total_chapters))

        chapters = db.query(Chapter).filter(Chapter.book_id == book.id).order_by(Chapter.number).all()
        stats = index_chapters(db, book.id, chapters)
        check("全部章节已向量化", stats["indexed"] == 5, str(stats))

        _embedder, store = get_store(db)
        check("向量库有数据", store.count(book.id) > 0, str(store.count(book.id)))
        check("索引章节号覆盖 1-5", store.indexed_chapters(book.id) == {1, 2, 3, 4, 5})

        # 检索必须排除未来章节
        from app.services.rag import retrieve

        hits = retrieve(db, book, target_number=3, goal="断剑 剑意 黑雾", top_k=5)
        check("检索返回结果", len(hits) > 0, str(len(hits)))
        check(
            "检索未返回第3章及之后的内容",
            all(h["chapter_number"] < 3 for h in hits),
            str([h["chapter_number"] for h in hits]),
        )
        return book.id
    finally:
        db.close()


def test_analyze_and_rag(book_id: int) -> None:
    print("\n[4] 拆书（Map-Reduce）+ RAG 上下文拼装")
    from app.database import SessionLocal
    from app.models import BookAnalysis
    from app.services.analyzer import analyze_book
    from app.services.rag import build_slots, load_analysis

    db = SessionLocal()
    try:
        progress: list[float] = []
        result = asyncio.run(
            analyze_book(
                db,
                book_id,
                batch_size=3,
                report=lambda p, m="", d=0, t=0: progress.append(p),
            )
        )
        check("拆书任务返回统计", "characters" in result, str(result))
        check("拆书进度有更新", len(progress) >= 3 and progress[-1] == 1.0, str(progress[-3:]))

        analysis = load_analysis(db, book_id)
        check("拆书结果入库", isinstance(analysis, BookAnalysis))
        check("人物卡 >= 2", len(analysis.characters or []) >= 2)
        check("世界观非空", len(analysis.worldview or []) > 0)
        check("伏笔非空", len(analysis.foreshadows or []) > 0)
        check("文风样本包含原文", bool((analysis.style or {}).get("sample")))
        check("分章摘要 = 5", len(analysis.chapter_summaries or []) == 5, str(len(analysis.chapter_summaries or [])))

        from app.models import Book

        book = db.get(Book, book_id)
        slots = build_slots(db, book, target_number=6, goal="主角在郡城比武大会上碾压天才", top_k=5)
        check("大纲槽位非空", "全书总纲" in slots.outline)
        check("核心设定含人物卡", "人物卡" in slots.core_settings)
        check("核心设定含文风样本", "文风样本" in slots.core_settings)
        check("伏笔槽位含未回收伏笔", "断剑" in slots.foreshadows or "未回收" in slots.foreshadows)
        check("上一章结尾非空", len(slots.previous_tail) > 20)
        check("检索片段已拼入核心设定", "原文相关片段" in slots.core_settings)
        return slots
    finally:
        db.close()


def test_writer(book_id: int) -> None:
    print("\n[5] 续写（含内置提示词渲染）+ 保存为章节")
    from app.database import SessionLocal
    from app.prompts import BUILTIN_PROMPTS
    from app.schemas import GenerateRequest
    from app.services.writer import build, generate, save_as_chapter

    db = SessionLocal()
    try:
        req = GenerateRequest(
            book_id=book_id,
            chapter_number=6,
            goal="主角在郡城比武大会上碾压天才，末尾引出上古残魂",
            target_words=3000,
            mode="continue",
        )
        prepared = asyncio.run(build(db, req))
        prompt = prepared.prompt
        check("提示词包含 Role 段", "# Role" in prompt)
        check("提示词包含本章目标", "郡城比武大会" in prompt)
        check("提示词填入了字数要求", "3000字" in prompt)
        check("提示词不含未替换占位符", "{goal}" not in prompt and "{outline}" not in prompt)
        check("内置续写模板存在", "continue_chapter" in BUILTIN_PROMPTS)

        generation = asyncio.run(generate(db, req))
        check("生成记录状态成功", generation.status == "success", generation.error)
        check("生成结果非空", len(generation.result) > 50)
        check("记录了 token", generation.input_tokens > 0 and generation.output_tokens > 0)
        check("记录了费用", generation.cost_usd >= 0)

        chapter = save_as_chapter(
            db, book_id, generation.result, number=6, title="第6章"
        )
        check("保存为章节成功", chapter is not None and chapter.number == 6)
        check("新章节标记为 AI 生成", chapter is not None and chapter.is_original is False)
        check("新章节已向量化", bool(chapter and chapter.vectorized))
        return generation.id
    finally:
        db.close()


def test_auto_writing(book_id: int) -> None:
    """全自动：目标留空 -> AI 自己推演剧情、自己起标题、连写多章。"""
    print("\n[5b] 全自动续写（AI 自己决定剧情 / 标题 / 连写 N 章）")
    from app.database import SessionLocal
    from app.models import Chapter
    from app.schemas import BatchGenerateRequest, GenerateRequest
    from app.services.writer import build, generate_batch

    db = SessionLocal()
    try:
        req = GenerateRequest(
            book_id=book_id,
            chapter_number=7,
            goal="",              # 关键：不填目标
            target_words=1500,
            mode="continue",
        )
        prepared = asyncio.run(build(db, req))
        check("目标留空时 AI 自动推演了剧情", bool(prepared.auto_planned))
        check("自动推演出的目标非空", len(prepared.goal) > 20, prepared.goal[:40])
        check("自动拟定了章节标题", prepared.title == "比武大会", prepared.title)
        check("自动目标已写入提示词", prepared.goal[:12] in prepared.prompt)
        check("计划里含要推进的伏笔", isinstance((prepared.plan or {}).get("foreshadow"), list) or "foreshadow" in str(prepared.plan))

        before = db.query(Chapter).filter(Chapter.book_id == book_id).count()
        result = asyncio.run(
            generate_batch(db, BatchGenerateRequest(book_id=book_id, count=2, target_words=800))
        )
        after = db.query(Chapter).filter(Chapter.book_id == book_id).count()
        check("连写返回 2 章", result.get("count") == 2, str(result.get("count")))
        check("章节数增加 2", after - before == 2, f"{before} -> {after}")
        saved = [c for c in result.get("chapters", []) if c.get("chapter_id")]
        check("两章都入库了", len(saved) == 2, str(saved))
        check("章节标题带上了 AI 拟定的名字", all("比武大会" in str(c.get("title")) for c in saved), str(saved))
        check("章节有正文", all(int(c.get("words") or 0) > 0 for c in saved), str(saved))
    finally:
        db.close()


def test_api(book_id: int) -> None:
    print("\n[6] FastAPI 接口（TestClient）")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        res = client.get("/api/health")
        check("GET /api/health", res.status_code == 200 and res.json()["status"] == "ok", res.text[:200])

        res = client.get("/api/books")
        check("GET /api/books", res.status_code == 200 and len(res.json()) >= 1)

        res = client.get(f"/api/books/{book_id}/chapters")
        check("GET 章节列表", res.status_code == 200 and len(res.json()) >= 6, str(len(res.json())))
        check(
            "AI 自动拟定的标题已出现在目录里",
            any("比武大会" in (c.get("title") or "") for c in res.json()),
            str([c.get("title") for c in res.json()][-4:]),
        )

        res = client.get(f"/api/books/{book_id}/analysis")
        check("GET 拆书结果", res.status_code == 200 and len(res.json()["characters"]) >= 2)

        res = client.put(
            f"/api/books/{book_id}/analysis",
            json={"synopsis": "手动修改后的总纲"},
        )
        check("PUT 手动修改拆书结果", res.status_code == 200 and res.json()["synopsis"] == "手动修改后的总纲")

        res = client.get("/api/settings")
        check("GET 设置", res.status_code == 200 and "text_model" in res.json()["settings"])
        settings = res.json()["settings"]
        check("设置不泄露明文 API Key", "api_key" not in settings and "api_key_present" in settings)

        res = client.put("/api/settings", json={"temperature": 0.7, "api_key": ""})
        check("PUT 设置（空 api_key 不覆盖）", res.status_code == 200 and res.json()["settings"]["temperature"] == 0.7)

        res = client.post(
            "/api/books/paste",
            json={"title": "粘贴测试书", "author": "", "content": SAMPLE_BOOK, "chapter_title": "", "split": True},
        )
        check("POST /api/books/paste", res.status_code == 200 and res.json()["chapters_created"] == 5, res.text[:300])
        pasted_id = res.json()["book"]["id"]

        res = client.post(f"/api/books/{book_id}/vectorize", json={"force": True})
        check("POST 启动向量化任务", res.status_code == 200 and "job_id" in res.json(), res.text[:200])
        job_id = res.json()["job_id"]
        deadline = time.time() + 60
        job = {}
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job.get("status") in {"success", "error", "cancelled"}:
                break
            time.sleep(0.4)
        check("向量化任务完成", job.get("status") == "success", json_dumps(job))

        res = client.post(
            "/api/generate/preview",
            json={"book_id": book_id, "goal": "比武大会碾压天才", "target_words": 1500, "mode": "continue"},
        )
        check("POST 上下文预览", res.status_code == 200 and "prompt" in res.json(), res.text[:300])
        preview = res.json()
        check("预览含检索片段", isinstance(preview.get("retrieved"), list))

        res = client.post(
            "/api/generate/preview",
            json={"book_id": book_id, "goal": "", "target_words": 1500, "mode": "continue"},
        )
        check(
            "目标留空时预览会自动推演剧情",
            res.status_code == 200 and res.json().get("auto_planned") is True,
            res.text[:300],
        )
        check("预览返回了 AI 拟定的标题与目标", bool(res.json().get("title")) and bool(res.json().get("goal")))

        res = client.post(
            "/api/generate",
            json={"book_id": book_id, "goal": "比武大会碾压天才", "target_words": 1500, "mode": "continue"},
        )
        check("POST 续写", res.status_code == 200 and len(res.json()["result"]) > 50, res.text[:300])
        generation_id = res.json()["id"]

        res = client.post(f"/api/generations/{generation_id}/save", json={"number": 20, "title": "第20章"})
        check("生成结果保存为章节", res.status_code == 200 and res.json()["number"] == 20, res.text[:300])

        res = client.post(f"/api/generations/{generation_id}/save", json={"number": 20, "title": "第20章"})
        check("重复章节号被拒绝", res.status_code == 400, res.text[:200])

        # 一键连写：AI 全自动
        res = client.post(
            "/api/generate/batch",
            json={"book_id": book_id, "count": 1, "target_words": 800},
        )
        check("POST 一键连写返回任务", res.status_code == 200 and "job_id" in res.json(), res.text[:300])
        batch_job = res.json()["job_id"]
        deadline = time.time() + 90
        job = {}
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{batch_job}").json()
            if job.get("status") in {"success", "error", "cancelled"}:
                break
            time.sleep(0.4)
        check("一键连写任务完成", job.get("status") == "success", json_dumps(job))
        check(
            "连写结果包含入库章节",
            bool((job.get("result") or {}).get("chapters")),
            json_dumps(job.get("result")),
        )

        res = client.post(
            "/api/consistency-check",
            json={"book_id": book_id, "source_text": "林越拔剑，苏清月在旁。"},
        )
        check("POST 伏笔/矛盾检查", res.status_code == 200, res.text[:200])

        res = client.get("/api/usage")
        check("GET 用量统计", res.status_code == 200 and res.json()["total_requests"] > 0, res.text[:200])

        res = client.get("/api/prompts")
        items = res.json()["items"]
        check("GET 提示词列表", res.status_code == 200 and len(items) >= 6)

        res = client.delete(f"/api/books/{pasted_id}")
        check("DELETE 书籍", res.status_code == 204, res.text[:200])


def json_dumps(value) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False)[:400]
    except Exception:  # noqa: BLE001
        return str(value)[:400]


# ======================================================================
def main() -> int:
    print("=" * 70)
    print("本地 AI 小说续写 —— 离线冒烟测试")
    print(f"临时数据目录：{TMP_DATA}")
    print("=" * 70)

    patch_llm()
    try:
        test_text_utils()
        test_embeddings()
        book_id = test_import_and_index()
        test_analyze_and_rag(book_id)
        test_writer(book_id)
        test_auto_writing(book_id)
        test_api(book_id)
    except Exception:  # noqa: BLE001
        print("\n!!! 测试过程中抛出异常：")
        traceback.print_exc()
        FAILED.append("未捕获异常")

    print("\n" + "=" * 70)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - 失败：{item}")
    print("=" * 70)

    shutil.rmtree(TMP_DATA, ignore_errors=True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
