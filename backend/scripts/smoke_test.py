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


async def fake_chat(db, *, messages, task, model=None, book_id=None, temperature=None, max_tokens=None, json_mode=False, provider_id=None):
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


def make_state(number: int) -> dict:
    """确定性的假状态抽取结果：位置/物品随章节推移变化，便于断言一致性。"""
    return {
        "chapter_number": number,
        "summary": f"第{number}章摘要：主角遭遇冲突并解决，实力提升，末尾留下新线索。",
        "location": f"第{number}号场景（郡城东街）",
        "time_note": f"距上一章 {number} 天",
        "present_characters": [
            {
                "name": "林越",
                "location": "郡城东街",
                "doing": f"在第{number}章里追查断剑来历",
                "mood": "警惕",
                "goal": "查清断剑铭文",
                "status": f"练气{number}层",
                "items": ["断剑"],
                "knows": [f"第{number}章的关键情报"],
            },
        ],
        # 只让苏清月在偶数章出场：这样第 5 章的快照里她属于「其余人物现状」，
        # 正好覆盖「谁不在场」这条关键信息
        **(
            {
                "present_characters": [
                    {
                        "name": "林越",
                        "location": "郡城东街",
                        "doing": f"在第{number}章里追查断剑来历",
                        "mood": "警惕",
                        "goal": "查清断剑铭文",
                        "status": f"练气{number}层",
                        "items": ["断剑"],
                        "knows": [f"第{number}章的关键情报"],
                    },
                    {
                        "name": "苏清月",
                        "location": "药阁",
                        "doing": "配药",
                        "mood": "平静",
                        "goal": "跟上林越",
                        "status": "安然",
                        "items": [],
                        "knows": [],
                    },
                ]
            }
            if number % 2 == 0
            else {}
        ),
        "items": [{"name": "断剑", "holder": "林越", "note": "第%d章确认归属" % number}],
        "known_facts": [f"第{number}章确认了封魔谷的方向"],
        "open_threads": [f"第{number}章末尾有人跟踪林越"],
        "new_foreshadows": (
            [{"content": f"第{number}章墙上那道血痕", "keywords": ["血痕"]}] if number % 3 == 0 else []
        ),
        "resolved_foreshadows": [],
    }


async def fake_chat_json(db, *, messages, task, model=None, book_id=None, max_tokens=None, temperature=None, provider_id=None):
    prompt = messages[-1]["content"] if messages else ""

    # --- 剧情状态抽取（单章 / 批量）---
    if "小说场记" in prompt:
        numbers = [int(n) for n in re.findall(r"=== 第(\d+)章", prompt)]
        if not numbers:
            match = re.search(r"本章正文（第\s*(\d+)\s*章）", prompt)
            numbers = [int(match.group(1))] if match else [1]
        states = [make_state(n) for n in numbers]
        record_mock_usage(db, book_id, task, model or "mock-cheap", 900, 400)
        if len(states) == 1:
            return states[0], ChatResult(json.dumps(states[0], ensure_ascii=False), "mock-cheap", 900, 400, 12)
        payload = {"states": states}
        return payload, ChatResult(json.dumps(payload, ensure_ascii=False), "mock-cheap", 900, 400, 12)

    # --- 原创：故事圣经 ---
    if "网文爽文策划" in prompt and "故事圣经" in prompt:
        bible = {
            "synopsis": "少年秦风在陨星坠落的荒原捡到一枚青铜残片，从此踏入宗门纷争，最终揭开星陨之秘。",
            "outline": {
                "volumes": [{"name": "第一卷 荒原", "range": "第1-30章", "summary": "觉醒与初战", "key_points": ["青铜残片认主"]}],
                "current_stage": "开局",
                "next_directions": ["进入青云宗"],
            },
            "characters": [
                {"name": "秦风", "identity": "荒原少年", "personality": "倔强", "relations": "主角",
                 "status": "凡人", "importance": "主角"},
                {"name": "柳如烟", "identity": "青云宗外门弟子", "personality": "清冷", "relations": "同伴",
                 "status": "筑基初期", "importance": "重要配角"},
            ],
            "worldview": [{"category": "势力", "name": "青云宗", "description": "东域三大宗门之一"}],
            "timeline": [{"chapter": 1, "event": "陨星坠落", "impact": "主角获得青铜残片"}],
            "foreshadows": [
                {"content": "青铜残片上的星图", "planted_chapter": 1, "status": "未回收",
                 "possible_payoff": "指向星陨之地", "keywords": ["青铜残片", "星图"]}
            ],
            "style": {"tone": "热血", "common_words": ["残片"], "sentence_patterns": ["短句"],
                      "dialogue_style": "干脆", "pacing": "快", "taboos": ["大段抒情"], "sample": ""},
        }
        record_mock_usage(db, book_id, task, model or "mock-pro", 1500, 900)
        return bible, ChatResult(json.dumps(bible, ensure_ascii=False), "mock-pro", 1500, 900, 18)

    # --- 原创：章节计划 ---
    if "逐章计划" in prompt:
        start = int(re.search(r"第\s*(\d+)\s*章到第", prompt).group(1))
        count = int(re.search(r"共\s*(\d+)\s*章", prompt).group(1))
        chapters = [
            {
                "number": start + i,
                "title": f"荒原第{start + i}战",
                "goal": f"第{start + i}章：秦风在荒原遭遇妖兽，用青铜残片反杀，末尾引来宗门注意。",
                "arc": "第一卷 荒原",
                "foreshadows_to_advance": ["青铜残片上的星图"],
            }
            for i in range(count)
        ]
        payload = {"chapters": chapters}
        record_mock_usage(db, book_id, task, model or "mock-pro", 1200, 700)
        return payload, ChatResult(json.dumps(payload, ensure_ascii=False), "mock-pro", 1200, 700, 16)

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


async def fake_chat_stream(db, *, messages, task, model=None, book_id=None, temperature=None, max_tokens=None, provider_id=None):
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
    import app.services.original as original
    import app.services.state as state
    import app.services.writer as writer

    # 注意：这些模块都是 `from .llm import chat_json` 直接导入到自己的命名空间，
    # 所以必须逐个替换，只改 llm.chat_json 是不够的。
    analyzer.chat_json = fake_chat_json
    state.chat_json = fake_chat_json
    original.chat_json = fake_chat_json
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
        # 新增：状态层
        check("状态快照槽位非空", len(slots.story_state) > 20, slots.story_state[:80])
        check(
            "状态快照含「截至第N章的状态」",
            "的状态" in slots.story_state,
            slots.story_state[:120],
        )
        check(
            "状态快照标注了在场人物位置",
            "林越" in slots.story_state and "位置" in slots.story_state,
            slots.story_state[:200],
        )
        # 新增：上一章应该是「整章」而不是 3000 字尾巴
        from app.models import Chapter as _Chapter

        chapter5 = db.query(_Chapter).filter_by(book_id=book_id, number=5).first()
        check(
            "上一章按整章送入（不再只给 3000 字尾巴）",
            chapter5 is not None and slots.previous_tail.strip().startswith(chapter5.content.strip()[:20]),
            slots.previous_tail[:60],
        )
        return slots
    finally:
        db.close()


def test_state_layer(book_id: int) -> None:
    print("\n[4b] 剧情状态层（人物位置/持有物/已知信息）")
    from app.database import SessionLocal
    from app.models import ChapterState, CharacterState
    from app.services.state import (
        build_state_snapshot,
        list_character_states,
        missing_state_chapters,
    )

    db = SessionLocal()
    try:
        states = (
            db.query(ChapterState)
            .filter(ChapterState.book_id == book_id)
            .order_by(ChapterState.chapter_number)
            .all()
        )
        check("原文 5 章都已建立状态", len(states) >= 5, f"实际 {len(states)}")
        check("状态含场景地点", bool(states and states[-1].location), str(states[-1].location if states else ""))
        check(
            "状态含在场人物及其行为",
            bool(states and states[-1].present_characters),
            str(states[-1].present_characters[:1] if states else ""),
        )
        check(
            "状态记录了物品归属",
            bool(states and states[-1].items),
            str(states[-1].items if states else ""),
        )

        chars = list_character_states(db, book_id)
        names = {c.name for c in chars}
        check("人物快照已生成", len(chars) >= 2, str(names))
        lin = next((c for c in chars if c.name == "林越"), None)
        check("人物快照记录了当前位置", bool(lin and lin.location), str(lin.location if lin else ""))
        check("人物快照记录了正在做的事", bool(lin and lin.doing), str(lin.doing if lin else ""))
        check("人物快照记录了持有物品", bool(lin and lin.items), str(lin.items if lin else ""))
        check(
            "人物快照记录了 last_seen_chapter",
            bool(lin and lin.last_seen_chapter >= 5),
            str(lin.last_seen_chapter if lin else 0),
        )

        snapshot = build_state_snapshot(db, book_id, target_number=6)
        check("快照含「其余人物现状」区块", "其余人物现状" in snapshot, snapshot[:200])
        check("快照含「关键物品归属」区块", "关键物品归属" in snapshot, snapshot[:300])
        check("快照含「悬而未决」区块", "悬而未决" in snapshot, snapshot[:400])

        check("状态覆盖后无缺失章节", missing_state_chapters(db, book_id) == [], str(missing_state_chapters(db, book_id)))
        return names
    finally:
        db.close()


def test_keyword_recall(book_id: int) -> None:
    print("\n[4c] 关键词必召回（语义召回的兜底）")
    from app.database import SessionLocal
    from app.models import Book
    from app.services.indexing import get_store
    from app.services.rag import retrieve

    db = SessionLocal()
    try:
        book = db.get(Book, book_id)
        _embedder, store = get_store(db)
        hits = store.keyword_search(book_id, ["断剑"], limit=3, max_chapter_number=6)
        check("关键词检索能命中「断剑」", len(hits) > 0, str(len(hits)))
        check(
            "关键词检索标注 recall=keyword",
            all(h.get("recall") == "keyword" for h in hits),
            str(hits[:1]),
        )
        check(
            "关键词检索同样排除未来章节",
            all(h["chapter_number"] < 6 for h in hits),
            str([h["chapter_number"] for h in hits]),
        )

        merged = retrieve(
            db,
            book,
            target_number=6,
            goal="郡城比武",
            top_k=5,
            must_recall=["断剑", "林越"],
            include_meta=True,
        )
        check("合并检索结果非空", len(merged) > 0, str(len(merged)))
        check(
            "合并结果里含关键词召回的片段",
            any(item.get("recall") == "keyword" for item in merged),
            str([item.get("recall") for item in merged]),
        )
        check(
            "关键词片段排在语义片段之前（优先级更高）",
            not merged
            or merged[0].get("recall") == "keyword"
            or all(item.get("recall") != "keyword" for item in merged),
            str([item.get("recall") for item in merged]),
        )
    finally:
        db.close()


def test_sample_book() -> None:
    """用仓库自带的原创测试稿跑一遍导入 + 索引 + 关键词召回。

    这个用例不依赖任何 mock：分章、分块、向量化、关键词召回全部走真实代码，
    用来验证「细节锚点」真的能被召回来（这正是旧版漏掉关键伏笔的原因）。
    """
    print("\n[4f] 自带原创测试稿（真实分章 / 分块 / 关键词召回）")
    from app.database import SessionLocal
    from app.models import Book, Chapter
    from app.services import importer
    from app.services.indexing import get_store, index_chapters
    from app.services.text_utils import count_words, split_chapters

    sample = BACKEND_DIR.parent / "samples" / "星陨荒原.txt"
    if not sample.exists():
        check("测试稿存在", False, str(sample))
        return
    text = sample.read_text(encoding="utf-8")
    chapters = split_chapters(text)
    # 测试稿共 9 章，结尾停在「秦风在藏经阁第七层看见完整残片」的悬念上，
    # 正好用来验证「续写第 10 章会不会前后打架」
    check("测试稿能正确分章（9 章）", len(chapters) == 9, f"实际 {len(chapters)} 章")
    check("首章标题正确", chapters and "第一章" in chapters[0].title, chapters[0].title if chapters else "")
    check("正文非空", all(count_words(c.content) > 200 for c in chapters[:5]))

    db = SessionLocal()
    try:
        book = importer.create_book(
            db, title="星陨荒原（测试稿）", author="本项目自带", source="txt", chapters=chapters
        )
        rows = (
            db.query(Chapter).filter(Chapter.book_id == book.id).order_by(Chapter.number).all()
        )
        index_chapters(db, book.id, rows)

        _embedder, store = get_store(db)

        # 全新书籍（什么都还没索引）不能被当成「向量模型变了、已清空」，
        # 否则第一次生成时日志里会冒出误导性的清空警告。
        check("全新书籍的 ensure_book 返回 False", store.ensure_book(999999) is False)
        check("再次调用仍是 False", store.ensure_book(999999) is False)
        store.delete_book(999999)

        # 这几个都是「换个措辞就召不回来」的关键锚点
        anchors = ["青铜残片", "老瘸子", "柳如烟", "残册", "陨心"]
        for keyword in anchors:
            hits = store.keyword_search(book.id, [keyword], limit=3, max_chapter_number=999)
            check(f"关键词召回命中「{keyword}」", len(hits) > 0, f"{len(hits)} 条")

        # 物品归属的关键章节必须能被召回：第 7 章残片交到老瘸子手上
        hits = store.keyword_search(book.id, ["老瘸子", "残片"], limit=5, max_chapter_number=999)
        numbers = {h["chapter_number"] for h in hits}
        check("能召回「残片易手」相关章节", 7 in numbers, str(sorted(numbers)))

        # 检索必须排除未来章节（防止剧透）
        limited = store.keyword_search(book.id, ["青铜残片"], limit=10, max_chapter_number=5)
        check(
            "关键词召回同样排除未来章节",
            all(h["chapter_number"] < 5 for h in limited),
            str([h["chapter_number"] for h in limited]),
        )

        # 老瘸子在第 6 章才说「图纸」，第 7 章收下残片；这两章必须都能命中
        check(
            "能同时召回第6、7章（人物连续出场）",
            {6, 7} & numbers == {6, 7} or 6 in numbers,
            str(sorted(numbers)),
        )

        db.delete(book)
        db.commit()
    finally:
        db.close()


def test_providers() -> None:
    print("\n[4d] LLM 网关（多网关配置与切换）")
    from app.database import SessionLocal
    from app.models import Provider
    from app.settings_store import (
        LEGACY_PLACEHOLDER_MODELS,
        delete_provider,
        get_active_provider,
        list_providers,
        set_active_provider,
        upsert_provider,
    )

    db = SessionLocal()
    try:
        created = upsert_provider(
            db,
            {
                "name": "mock-gateway",
                "base_url": "https://example.invalid/v1",
                "api_key": "sk-mock-1234567890abcd",
                "text_model": "mock-flash",
                "reasoning_model": "mock-pro",
                "cheap_model": "mock-cheap",
            },
        )
        check("新建网关成功", created.id is not None)

        items = list_providers(db)
        target = next((p for p in items if p["name"] == "mock-gateway"), None)
        check("网关出现在列表里", target is not None)
        check("列表不泄露明文 key", target is not None and "api_key" not in target, str(target))
        check("列表返回脱敏 key", bool(target and target.get("api_key_masked")), str(target))

        check("第一个网关自动激活", get_active_provider(db) is not None)

        upsert_provider(db, {"id": created.id, "api_key": "", "text_model": "mock-flash-2"})
        again = next(p for p in list_providers(db) if p["id"] == created.id)
        check("空 api_key 不会清空已存的 key", again["api_key_present"] is True)
        check("模型名可以更新", again["text_model"] == "mock-flash-2", again["text_model"])

        set_active_provider(db, created.id)
        check("切换激活网关成功", get_active_provider(db).id == created.id)

        # 「一键修正模型名」必须三档都写回，漏掉 cheap_model 会让状态抽取继续报模型不存在
        from app.settings_store import touch_provider_models

        touch_provider_models(
            db,
            created.id,
            {"text_model": "fixed-text", "reasoning_model": "fixed-pro", "cheap_model": "fixed-cheap"},
        )
        fixed = db.get(Provider, created.id)
        check("修正写回正文模型", fixed.text_model == "fixed-text", fixed.text_model)
        check("修正写回复杂任务模型", fixed.reasoning_model == "fixed-pro", fixed.reasoning_model)
        check("修正写回廉价模型", fixed.cheap_model == "fixed-cheap", fixed.cheap_model)

        # 占位模型名要能被识别出来（否则启动时不会提醒，用户只能等 403）
        from app.settings_store import placeholder_model_warnings

        hits = placeholder_model_warnings(
            {"text_model": "deepseek-v4.1-flash", "reasoning_model": "某个真模型", "cheap_model": ""}
        )
        check("能识别占位模型名", len(hits) == 1 and "deepseek-v4.1-flash" in hits[0], str(hits))
        check(
            "真实模型名不误报",
            placeholder_model_warnings({"text_model": "deepseek-chat", "reasoning_model": "qwen-max"}) == [],
        )

        delete_provider(db, created.id)
        check("删除网关成功", db.query(Provider).filter(Provider.id == created.id).first() is None)
    finally:
        db.close()

    # 403「no access to model」必须翻译成可操作的中文，而不是把原始 JSON 丢给用户
    from app.services.llm import _friendly_error

    raw = (
        "Error code: 403 - {'error': {'message': 'This token has no access to model "
        "deepseek-v4.1-pro (request id: 2026xxxx)', 'type': 'invalid_request_error'}}"
    )
    friendly = _friendly_error(RuntimeError(raw))
    check("403 报错被翻译成中文提示", "no access to model" not in friendly, friendly[:80])
    check("提示里点出了模型名", "deepseek-v4.1-pro" in friendly, friendly[:120])
    check("提示里给了解决办法", "拉取可用模型" in friendly and "一键修正" in friendly)

    other_403 = _friendly_error(RuntimeError("Error code: 403 - forbidden by gateway"))
    check("无模型信息的 403 也走友好分支", "403" in other_403 and "设置" in other_403, other_403[:80])

    # 用户把 Key 填在「旧版单网关」而自动创建的网关是空的 —— 必须能兜底，否则报「未配置 API Key」
    from app.config import env
    from app.settings_store import resolve_llm_config, update_settings

    db = SessionLocal()
    saved_env_key = env.deepseek_api_key
    try:
        # 先屏蔽环境变量，否则测不到「回退到旧设置」这条路径
        env.deepseek_api_key = ""
        update_settings(
            db, {"api_key": "sk-legacy-fallback", "text_model": "legacy-text", "reasoning_model": "legacy-pro"}
        )
        empty = upsert_provider(db, {"name": "空网关", "base_url": "https://example.invalid/v1"})
        set_active_provider(db, empty.id)
        merged = resolve_llm_config(db)
        check("网关 key 为空时回退到旧设置", merged["api_key"] == "sk-legacy-fallback", merged["api_key"])
        check("网关模型为空时回退到旧设置", merged["text_model"] == "legacy-text", merged["text_model"])
        check("廉价模型缺省沿用正文模型", merged["cheap_model"] == "legacy-text", merged["cheap_model"])

        delete_provider(db, empty.id)
        update_settings(db, {"api_key": None, "text_model": "", "reasoning_model": ""})
    finally:
        env.deepseek_api_key = saved_env_key
        db.close()

    # 三档都没配模型名时必须明确报错，而不是猜一个名字去撞 403
    from app.services.llm import MISSING_MODEL_HINT, _pick_model

    check("没配模型名时不猜默认名字", _pick_model({}, {}, "continue", None) == "", "应当返回空串")
    check(
        "没配模型名时优先用复杂档",
        _pick_model({}, {}, "outline", None) == "",
    )
    check(
        "配了正文模型就回退到它",
        _pick_model({}, {"text_model": "only-text"}, "analyze", None) == "only-text",
    )
    check("提示语指向设置页", "模型路由" in MISSING_MODEL_HINT, MISSING_MODEL_HINT[:40])

    # 仓库自身不能再把占位模型名当默认值，否则新装的用户一上手就 403
    from app.settings_store import DEFAULT_SETTINGS

    shipped = {
        str(DEFAULT_SETTINGS.get("text_model") or ""),
        str(DEFAULT_SETTINGS.get("reasoning_model") or ""),
    }
    check(
        "默认设置里没有占位模型名",
        not (shipped & LEGACY_PLACEHOLDER_MODELS),
        str(shipped),
    )
    check("默认模型名留空（交给拉取可用模型）", shipped == {""}, str(shipped))

    # 一个模型名都没有时，chat 必须在发起网络请求**之前**就报出可操作的错误
    from app.database import SessionLocal as _SessionLocal
    from app.services.llm import LLMError, chat

    db = _SessionLocal()
    try:
        for row in db.query(Provider).all():
            delete_provider(db, row.id)
        update_settings(db, {"text_model": "", "reasoning_model": "", "state_model": "", "api_key": ""})
        try:
            asyncio.run(
                chat(db, messages=[{"role": "user", "content": "hi"}], task="continue")
            )
            check("未配模型名时 chat 会报错", False, "竟然没报错")
        except LLMError as exc:
            check("未配模型名时 chat 会报错", True)
            check("报错内容指向设置页", "模型路由" in str(exc), str(exc)[:60])
    finally:
        db.close()


def test_original_mode() -> None:
    print("\n[4e] 从零原创模式（故事圣经 + 章节计划）")
    from app.database import SessionLocal
    from app.models import Book, BookAnalysis
    from app.schemas import OriginalBookRequest, PlanChaptersRequest
    from app.services.original import create_original_book, plan_chapters

    db = SessionLocal()
    try:
        req = OriginalBookRequest(
            title="星陨荒原",
            genre="东方玄幻",
            protagonist="荒原少年秦风",
            hook="捡到一枚会吸收星辰之力的青铜残片",
            total_chapters=60,
            target_words=2500,
            plan_chapters=3,
        )
        result = asyncio.run(create_original_book(db, req))
        book_id = result["book_id"]
        book = db.get(Book, book_id)
        check("原创书已建立", book is not None and book.kind == "original", str(book and book.kind))
        check("新书没有正文章节", (book.total_chapters or 0) == 0, str(book.total_chapters))

        analysis = db.query(BookAnalysis).filter(BookAnalysis.book_id == book_id).first()
        check("故事圣经已落库", analysis is not None and bool(analysis.synopsis))
        check("人物卡非空", len(analysis.characters or []) >= 2, str(len(analysis.characters or [])))
        check("伏笔非空", len(analysis.foreshadows or []) >= 1)
        check("章节计划已排出", len(analysis.chapter_plan or []) == 3, str(analysis.chapter_plan))

        from app.models import CharacterState

        seeded = db.query(CharacterState).filter(CharacterState.book_id == book_id).count()
        check("原创模式自动初始化了人物快照", seeded >= 2, str(seeded))

        # 续排计划，并验证不会丢掉已有章节
        more = asyncio.run(
            plan_chapters(db, book_id, start_chapter=4, count=2, replace=False)
        )
        check("可以续排章节计划", len(more) == 2, str(more))
        db.refresh(analysis)
        check("续排后计划合并为 5 章", len(analysis.chapter_plan or []) == 5, str(len(analysis.chapter_plan or [])))

        # 分批排计划：12 章按每批 5 章应该拆成 3 次模型调用，并且进度真的在动。
        # 这是"进度卡着不动"的根治点：一次让推理模型吐 12~20 章会等好几分钟。
        import app.services.original as original_service

        calls: list[str] = []
        original_jsons = original_service.chat_json

        async def counting_chat_json(db_, *, messages, task, **kwargs):  # noqa: ANN001, ANN003, ANN202
            calls.append(f"{task}:{len(messages[-1]['content'])}")
            return await original_jsons(db_, messages=messages, task=task, **kwargs)

        original_service.chat_json = counting_chat_json
        progress_snapshots: list[tuple[float, str, int, int]] = []
        try:
            batched = asyncio.run(
                plan_chapters(
                    db,
                    book_id,
                    start_chapter=1,
                    count=12,
                    replace=True,
                    batch_size=5,
                    report=lambda p, m="", d=0, t=0: progress_snapshots.append((round(p, 3), m, d, t)),
                )
            )
        finally:
            original_service.chat_json = original_jsons

        check("12 章按每批 5 章拆成 3 次调用", len(calls) == 3, f"实际 {len(calls)} 次")
        check("分批后总量正确", len(batched) == 12, f"实际 {len(batched)} 章")
        check(
            "计划的章节号连续且从 1 开始",
            [int(x["number"]) for x in batched] == list(range(1, 13)),
            str([x["number"] for x in batched])[:60],
        )
        check("分批过程中有进度上报", len(progress_snapshots) >= 5, f"实际 {len(progress_snapshots)} 次")
        check(
            "进度是单调不降的",
            all(
                progress_snapshots[i][0] <= progress_snapshots[i + 1][0]
                for i in range(len(progress_snapshots) - 1)
            ),
            str([p[0] for p in progress_snapshots]),
        )
        check(
            "每批都说明了在排第几章",
            any("第 1~5 章" in p[1] and "1~5" in p[1] for p in progress_snapshots)
            and any("第 6~10 章" in p[1] for p in progress_snapshots),
            str([p[1] for p in progress_snapshots])[:160],
        )
        tiny = asyncio.run(
            plan_chapters(db, book_id, start_chapter=20, count=1, replace=False, batch_size=1)
        )
        check("每批 1 章时仍能排出", len(tiny) == 1 and int(tiny[0]["number"]) == 20, str(tiny))

        # 原创模式写第 1 章时应直接采用计划里的目标
        from app.schemas import GenerateRequest
        from app.services.writer import build

        prepared = asyncio.run(
            build(db, GenerateRequest(book_id=book_id, chapter_number=1, mode="continue", target_words=800))
        )
        check("既定计划的目标被自动采用", "荒原第1战" not in prepared.goal and len(prepared.goal) > 10, prepared.goal[:60])
        check("既定计划已写入提示词", "本章既定计划" in prepared.prompt, prepared.prompt[:200])
        check("提示词含剧情状态槽位", "当前剧情状态" in prepared.prompt)
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
        check("设置返回了 providers 字段", "providers" in res.json(), str(list(res.json().keys())))

        res = client.put("/api/settings", json={"temperature": 0.7, "api_key": ""})
        check("PUT 设置（空 api_key 不覆盖）", res.status_code == 200 and res.json()["settings"]["temperature"] == 0.7)

        # 新增：状态层接口
        res = client.get(f"/api/books/{book_id}/state")
        check("GET 剧情状态", res.status_code == 200 and "snapshot" in res.json(), res.text[:200])
        state_body = res.json()
        check("状态返回了章节状态列表", len(state_body.get("chapters") or []) >= 5, str(len(state_body.get("chapters") or [])))
        check("状态返回了人物快照列表", len(state_body.get("characters") or []) >= 2, str(len(state_body.get("characters") or [])))
        check("状态接口返回 snapshot 文本", "在场人物" in state_body.get("snapshot", ""), state_body.get("snapshot", "")[:150])

        res = client.post(f"/api/books/{book_id}/state/extract", json={"batch_size": 2})
        check("POST 补建状态任务", res.status_code == 200 and "job_id" in res.json(), res.text[:200])
        state_job = res.json()["job_id"]
        deadline = time.time() + 60
        job = {}
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{state_job}").json()
            if job.get("status") in {"success", "error", "cancelled"}:
                break
            time.sleep(0.4)
        check("补建状态任务完成", job.get("status") == "success", json_dumps(job))

        # 新增：网关接口
        res = client.get("/api/providers")
        check("GET 网关列表", res.status_code == 200 and "providers" in res.json(), res.text[:200])
        check("网关列表返回 resolved", bool(res.json().get("resolved", {}).get("base_url")), res.text[:300])

        res = client.post(
            "/api/providers",
            json={"name": "smoke-gw", "base_url": "https://example.invalid/v1", "api_key": "sk-smoke-abcdefghijkl"},
        )
        check("POST 新建网关", res.status_code == 200, res.text[:300])
        gw_id = res.json()["id"]
        check("网关响应已脱敏", "api_key" not in res.json(), res.text[:300])

        res = client.post(f"/api/providers/{gw_id}/activate")
        check("POST 切换网关", res.status_code == 200, res.text[:200])

        res = client.delete(f"/api/providers/{gw_id}")
        check("DELETE 网关", res.status_code == 200 and gw_id not in [p["id"] for p in res.json()["providers"]], res.text[:200])

        # 新增：从零原创
        res = client.post(
            "/api/books/original",
            json={"title": "接口原创书", "genre": "玄幻", "total_chapters": 30, "plan_chapters": 2},
        )
        check("POST 创建原创书", res.status_code == 200 and "job_id" in res.json(), res.text[:300])
        original_job = res.json()["job_id"]
        deadline = time.time() + 90
        job = {}
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{original_job}").json()
            if job.get("status") in {"success", "error", "cancelled"}:
                break
            time.sleep(0.4)
        check("原创建书任务完成", job.get("status") == "success", json_dumps(job))
        original_id = (job.get("result") or {}).get("book_id")
        check("原创书返回了 book_id", bool(original_id), json_dumps(job))

        if original_id:
            res = client.post(
                f"/api/books/{original_id}/plan-chapters/sync",
                json={"start_chapter": 3, "count": 2},
            )
            check("POST 续排章节计划", res.status_code == 200 and res.json()["count"] == 2, res.text[:300])

            res = client.get(f"/api/books/{original_id}/analysis")
            check(
                "原创书的章节计划已写入拆书结果",
                res.status_code == 200 and len(res.json().get("chapter_plan") or []) >= 4,
                res.text[:300],
            )

            res = client.delete(f"/api/books/{original_id}")
            check("DELETE 原创书", res.status_code == 204, res.text[:200])

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
        test_state_layer(book_id)
        test_keyword_recall(book_id)
        test_sample_book()
        test_providers()
        test_original_mode()
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
