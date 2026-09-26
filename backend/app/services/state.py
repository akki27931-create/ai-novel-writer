"""剧情状态层：解决「续写前后对不上」的核心模块。

问题背景
--------
旧实现只把「上一章最后 3000 字」+ 若干语义检索片段喂给模型，
于是「人物在哪、在做什么、手上有什么、知道什么」这类**瞬时状态**
必须靠模型从碎片里猜，写到十几章之后必然前后矛盾。

本模块的做法
-----------
1. 每写完一章，就用便宜模型从正文里抽取一次**客观状态**（谁在场、在哪、做什么、
   持有什么、新知道什么、哪些线还悬着、回收了哪些伏笔）。
2. 抽出来的状态落库（chapter_states），并 merge 进人物滚动快照（character_states）。
3. 续写时把「截至上一章的状态快照」作为一等上下文注入，
   而不是让模型去猜。同时明确告知「谁不在场」。

这样状态与 token 消耗解耦：快照是压缩过的结构化数据，
比把前 20 章原文塞进上下文便宜得多。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from sqlalchemy.orm import Session

from ..models import Book, BookAnalysis, Chapter, CharacterState, ChapterState
from ..prompts import build_prompt
from ..settings_store import get_settings
from .llm import chat_json, resolve_model
from .text_utils import clamp_text

logger = logging.getLogger(__name__)

# 送入状态抽取模型的本章正文上限（一章约 3000 字，留足余量）
CHAPTER_TEXT_LIMIT = 12000
# 批量抽取时，单章正文上限（批 4 章 ≈ 1 万字输入，避免一次吃太多）
PER_CHAPTER_TEXT_LIMIT = 3000
# 快照里最多展示多少个主要人物的「当前状态」
SNAPSHOT_CHARACTER_LIMIT = 18
# 快照里展示最近多少章的脉络
RECENT_SUMMARY_LIMIT = 6

ReportFn = Callable[[float, str, int, int], None]

_IMPORTANCE_ORDER = {"主角": 0, "重要配角": 1, "配角": 2}


def _noop(_p: float, _m: str = "", _d: int = 0, _t: int = 0) -> None:
    return None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(text: str) -> str:
    """归一化用于比对伏笔：去掉空白与标点，只留实义字。"""
    return "".join(ch for ch in (text or "") if ch.isalnum())


# ----------------------------------------------------------------------
# 读取
# ----------------------------------------------------------------------
def get_chapter_state(db: Session, book_id: int, chapter_number: int) -> ChapterState | None:
    return (
        db.query(ChapterState)
        .filter(ChapterState.book_id == book_id, ChapterState.chapter_number == chapter_number)
        .first()
    )


def list_chapter_states(db: Session, book_id: int, *, upto: int | None = None) -> list[ChapterState]:
    query = db.query(ChapterState).filter(ChapterState.book_id == book_id)
    if upto is not None:
        query = query.filter(ChapterState.chapter_number < upto)
    return query.order_by(ChapterState.chapter_number).all()


def list_character_states(db: Session, book_id: int) -> list[CharacterState]:
    rows = db.query(CharacterState).filter(CharacterState.book_id == book_id).all()
    rows.sort(
        key=lambda r: (
            _IMPORTANCE_ORDER.get(r.importance or "", 3),
            -int(r.last_seen_chapter or 0),
        )
    )
    return rows


def latest_state(db: Session, book_id: int, upto: int) -> ChapterState | None:
    """取「第 upto 章之前」的最后一章状态，即续写第 upto 章时应该对齐的状态。"""
    return (
        db.query(ChapterState)
        .filter(ChapterState.book_id == book_id, ChapterState.chapter_number < upto)
        .order_by(ChapterState.chapter_number.desc())
        .first()
    )


def missing_state_chapters(db: Session, book_id: int, *, upto: int | None = None) -> list[Chapter]:
    """找出还没抽取状态、但有正文可抽的章节。"""
    query = db.query(Chapter).filter(Chapter.book_id == book_id, Chapter.word_count > 0)
    if upto is not None:
        query = query.filter(Chapter.number < upto)
    chapters = query.order_by(Chapter.number).all()
    return [c for c in chapters if not c.state_extracted]


# ----------------------------------------------------------------------
# 快照拼装（供 rag.py 使用）
# ----------------------------------------------------------------------
def _format_present_characters(state: ChapterState | None) -> str:
    if state is None:
        return ""
    lines: list[str] = []
    for item in _as_list(state.present_characters):
        card = _as_dict(item)
        name = _text(card.get("name"))
        if not name:
            continue
        parts = [
            f"位置：{_text(card.get('location'))}" if _text(card.get("location")) else "",
            f"在做：{_text(card.get('doing'))}" if _text(card.get("doing")) else "",
            f"目标：{_text(card.get('goal'))}" if _text(card.get("goal")) else "",
            f"情绪：{_text(card.get('mood'))}" if _text(card.get("mood")) else "",
            f"持有：{'、'.join(_text(i) for i in _as_list(card.get('items'))) if _as_list(card.get('items')) else ''}",
        ]
        detail = "；".join(p for p in parts if p)
        lines.append(f"- {name}（{detail}）" if detail else f"- {name}")
    return "\n".join(lines)


def _format_character_snapshot(rows: list[CharacterState], present_names: set[str]) -> str:
    """未被本章「在场」覆盖的人物，用滚动快照补上，并标注最后出场章节。"""
    lines: list[str] = []
    for row in rows[:SNAPSHOT_CHARACTER_LIMIT]:
        if row.name in present_names:
            continue
        parts = [
            f"位置：{row.location}" if row.location else "",
            f"在做：{row.doing}" if row.doing else "",
            f"目标：{row.goal}" if row.goal else "",
            f"状态：{row.status}" if row.status else "",
            f"持有：{'、'.join(_text(i) for i in _as_list(row.items))}" if _as_list(row.items) else "",
        ]
        detail = "；".join(p for p in parts if p)
        tag = f"最后出场：第{row.last_seen_chapter}章" if row.last_seen_chapter else "尚未出场"
        lines.append(f"- {row.name}（{tag}{'；' + detail if detail else ''}）")
    return "\n".join(lines)


def _format_items(state: ChapterState | None, rows: list[CharacterState]) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for item in _as_list(state.items) if state else []:
        card = _as_dict(item)
        name = _text(card.get("name"))
        if not name or name in seen:
            continue
        seen.add(name)
        holder = _text(card.get("holder")) or "不明"
        note = _text(card.get("note"))
        lines.append(f"- {name} → 在{holder}手上" + (f"（{note}）" if note else ""))
    return "\n".join(lines)


def _format_threads(states: list[ChapterState]) -> str:
    lines: list[str] = []
    for state in states[-3:]:
        for item in _as_list(state.open_threads):
            text = _text(item)
            if text:
                lines.append(f"- （第{state.chapter_number}章末）{text}")
    return "\n".join(lines[-10:])


def _format_recent_summaries(states: list[ChapterState], limit: int) -> str:
    lines: list[str] = []
    for state in states[-limit:]:
        if state.summary:
            lines.append(f"第{state.chapter_number}章：{state.summary}")
    return "\n".join(lines)


def build_state_snapshot(
    db: Session,
    book_id: int,
    *,
    target_number: int,
    budget: int = 6000,
    analysis: BookAnalysis | None = None,
) -> str:
    """拼装「截至目标章节之前」的剧情状态快照。

    这是续写上下文里最优先的一块：它明确告诉模型
    「谁在哪、在做什么、手上有什么、谁不在场、什么线还悬着」。
    """
    states = list_chapter_states(db, book_id, upto=target_number)
    if not states:
        return "（暂无剧情状态记录。建议先点「抽取剧情状态」，或让系统在保存章节后自动抽取。）"

    latest = states[-1]
    rows = list_character_states(db, book_id)
    present_names = {
        _text(_as_dict(item).get("name"))
        for item in _as_list(latest.present_characters)
    }

    blocks: list[str] = []
    head = [f"【截至第{latest.chapter_number}章的状态】"]
    if latest.location:
        head.append(f"场景：{latest.location}")
    if latest.time_note:
        head.append(f"时间：{latest.time_note}")
    blocks.append("\n".join(head))

    present = _format_present_characters(latest)
    if present:
        blocks.append(f"【上一章结束时在场人物】\n{present}")

    absent = _format_character_snapshot(rows, present_names)
    if absent:
        blocks.append(
            "【其余人物现状（不在场者，本章不应无故出现）】\n" + absent
        )

    items = _format_items(latest, rows)
    if items:
        blocks.append(f"【关键物品归属】\n{items}")

    facts: list[str] = []
    for state in states[-3:]:
        for item in _as_list(state.known_facts):
            text = _text(item)
            if text:
                facts.append(f"- （第{state.chapter_number}章）{text}")
    if facts:
        blocks.append("【近期关键知情信息】\n" + "\n".join(facts[-10:]))

    threads = _format_threads(states)
    if threads:
        blocks.append(f"【悬而未决】\n{threads}")

    recent = _format_recent_summaries(states, RECENT_SUMMARY_LIMIT)
    if recent:
        blocks.append(f"【最近剧情脉络】\n{recent}")

    return clamp_text("\n\n".join(blocks), budget)


def build_current_state_for_extract(
    db: Session, book_id: int, chapter_number: int, *, budget: int = 4000
) -> str:
    """抽取状态时，把「上一章的状态」作为对照输入，避免状态被重写而非增量更新。"""
    snapshot = build_state_snapshot(db, book_id, target_number=chapter_number, budget=budget)
    return snapshot


# ----------------------------------------------------------------------
# 写入
# ----------------------------------------------------------------------
def _upsert_character_states(
    db: Session, book_id: int, chapter_number: int, data: dict[str, Any]
) -> None:
    """把本章抽出的「在场人物」merge 进滚动快照。"""
    known = {
        row.name: row
        for row in db.query(CharacterState).filter(CharacterState.book_id == book_id).all()
    }

    for item in _as_list(data.get("present_characters")):
        card = _as_dict(item)
        name = _text(card.get("name"))
        if not name:
            continue
        row = known.get(name)
        if row is None:
            row = CharacterState(book_id=book_id, name=name)
            db.add(row)
            known[name] = row

        # 位置/行为/目标这类瞬时状态：抽到就覆盖，抽不到就保留旧值（比清空更安全）
        for field, key in (
            ("location", "location"),
            ("doing", "doing"),
            ("mood", "mood"),
            ("goal", "goal"),
            ("status", "status"),
        ):
            value = _text(card.get(key))
            if value:
                setattr(row, field, value)

        items = [_text(i) for i in _as_list(card.get("items")) if _text(i)]
        if items:
            row.items = items

        knows = [_text(i) for i in _as_list(card.get("knows")) if _text(i)]
        if knows:
            merged = list(dict.fromkeys([*(row.knows or []), *knows]))
            row.knows = merged[-40:]  # 只保留最近的，避免无限膨胀

        row.last_seen_chapter = max(int(row.last_seen_chapter or 0), chapter_number)

    db.flush()


def _apply_items(db: Session, book_id: int, chapter_number: int, data: dict[str, Any]) -> None:
    """物品易手：把最新归属同步到持有人的人物快照上。"""
    rows = db.query(CharacterState).filter(CharacterState.book_id == book_id).all()
    if not rows:
        return
    for item in _as_list(data.get("items")):
        card = _as_dict(item)
        name = _text(card.get("name"))
        holder = _text(card.get("holder"))
        if not name or not holder:
            continue
        for row in rows:
            items = [i for i in _as_list(row.items) if _text(i)]
            if row.name == holder:
                if name not in items:
                    items.append(name)
                    row.items = items
            elif name in items:
                # 物品已易手到别人手上，从原持有人身上移除
                row.items = [i for i in items if i != name]
    db.flush()


def _merge_foreshadows(
    analysis: BookAnalysis | None, chapter_number: int, data: dict[str, Any]
) -> None:
    """伏笔闭环：新埋的入库，本章回收的标记为已回收。"""
    if analysis is None:
        return
    existing = [_as_dict(item) for item in _as_list(analysis.foreshadows)]
    existing = [item for item in existing if item]

    resolved = [
        _as_dict(item)
        for item in _as_list(data.get("resolved_foreshadows"))
        if _text(_as_dict(item).get("content"))
    ]
    for done in resolved:
        key = _norm(_text(done.get("content")))
        if not key:
            continue
        for item in existing:
            target = _norm(_text(item.get("content")))
            if not target:
                continue
            if key in target or target in key:
                item["status"] = f"已回收（第{chapter_number}章）"
                item["resolved_chapter"] = chapter_number
                if _text(done.get("how")):
                    item["resolved_how"] = _text(done.get("how"))
                break

    existing_norms = {_norm(_text(item.get("content"))) for item in existing}
    for item in _as_list(data.get("new_foreshadows")):
        card = _as_dict(item)
        content = _text(card.get("content"))
        if not content or _norm(content) in existing_norms:
            continue
        existing.append(
            {
                "content": content,
                "planted_chapter": chapter_number,
                "status": "未回收",
                "possible_payoff": "",
                "keywords": [_text(k) for k in _as_list(card.get("keywords")) if _text(k)],
            }
        )
        existing_norms.add(_norm(content))

    analysis.foreshadows = existing


def _write_back_summary(
    db: Session,
    analysis: BookAnalysis | None,
    chapter: Chapter,
    summary: str,
    present_characters: list[Any] | None = None,
) -> None:
    """把 AI 章节的摘要写进 chapter_summaries —— 这是旧版最大的信息断层。"""
    chapter.summary = summary
    if analysis is None or not summary:
        return
    items = [_as_dict(item) for item in _as_list(analysis.chapter_summaries)]
    items = [item for item in items if item and item.get("chapter_number")]
    names = [
        _text(_as_dict(c).get("name"))
        for c in (present_characters or [])
        if _text(_as_dict(c).get("name"))
    ]
    for item in items:
        if int(item.get("chapter_number") or 0) == chapter.number:
            item["summary"] = summary
            item["title"] = item.get("title") or chapter.title
            if names:
                item["characters"] = names
            break
    else:
        items.append(
            {
                "chapter_number": chapter.number,
                "title": chapter.title,
                "summary": summary,
                "events": [],
                "characters": names,
            }
        )
    items.sort(key=lambda x: int(x.get("chapter_number") or 0))
    analysis.chapter_summaries = items


def apply_state(
    db: Session, book: Book, chapter: Chapter, data: dict[str, Any]
) -> ChapterState:
    """把抽取结果落库：章节状态 + 人物快照 + 伏笔闭环 + 章节摘要。"""
    analysis = db.query(BookAnalysis).filter(BookAnalysis.book_id == book.id).first()

    row = get_chapter_state(db, book.id, chapter.number)
    if row is None:
        row = ChapterState(book_id=book.id, chapter_number=chapter.number)
        db.add(row)

    row.summary = _text(data.get("summary"))[:600]
    row.location = _text(data.get("location"))[:300]
    row.time_note = _text(data.get("time_note"))[:300]
    row.present_characters = [
        _as_dict(item) for item in _as_list(data.get("present_characters")) if _as_dict(item)
    ]
    row.items = [_as_dict(item) for item in _as_list(data.get("items")) if _as_dict(item)]
    row.known_facts = [_text(i) for i in _as_list(data.get("known_facts")) if _text(i)]
    row.open_threads = [_text(i) for i in _as_list(data.get("open_threads")) if _text(i)]
    row.new_foreshadows = [
        _as_dict(item) for item in _as_list(data.get("new_foreshadows")) if _as_dict(item)
    ]
    row.resolved_foreshadows = [
        _as_dict(item) for item in _as_list(data.get("resolved_foreshadows")) if _as_dict(item)
    ]

    db.flush()
    _upsert_character_states(db, book.id, chapter.number, data)
    _apply_items(db, book.id, chapter.number, data)
    _merge_foreshadows(analysis, chapter.number, data)
    _write_back_summary(db, analysis, chapter, row.summary, row.present_characters)

    chapter.state_extracted = True
    db.commit()
    db.refresh(row)
    return row


# ----------------------------------------------------------------------
# 抽取
# ----------------------------------------------------------------------
async def extract_chapter_state(
    db: Session,
    book: Book,
    chapter: Chapter,
    *,
    model: str | None = None,
    provider_id: int | None = None,
) -> dict[str, Any]:
    """调用（便宜）模型，从一章正文里抽取客观状态。"""
    settings = get_settings(db, include_secrets=True)
    analysis = db.query(BookAnalysis).filter(BookAnalysis.book_id == book.id).first()

    from ..services.rag import format_foreshadows

    prompt = build_prompt(
        "extract_state",
        settings.get("prompt_overrides"),
        current_state=build_current_state_for_extract(db, book.id, chapter.number),
        foreshadows=(
            format_foreshadows(_as_list(analysis.foreshadows)) if analysis else ""
        )
        or "（暂无未回收伏笔记录）",
        chapter_text=clamp_text(chapter.content, CHAPTER_TEXT_LIMIT),
        chapter_number=chapter.number,
    )
    data, result = await chat_json(
        db,
        messages=[
            {
                "role": "system",
                "content": "你是严谨的小说场记，只依据给定正文记录客观事实，只输出合法 JSON。",
            },
            {"role": "user", "content": prompt},
        ],
        task="extract_state",   # 走廉价模型档
        model=model or str(settings.get("state_model") or "") or None,
        book_id=book.id,
        provider_id=provider_id,
    )
    if not isinstance(data, dict):
        raise ValueError("状态抽取失败：模型没有返回 JSON 对象")
    logger.info(
        "第 %s 章状态抽取完成：在场 %s 人（模型 %s）",
        chapter.number,
        len(_as_list(data.get("present_characters"))),
        result.model,
    )
    return data


async def extract_states_batch(
    db: Session,
    book: Book,
    chapters: list[Chapter],
    *,
    model: str | None = None,
    provider_id: int | None = None,
) -> dict[int, dict[str, Any]]:
    """一次抽取连续多章的状态（用于给导入的原文批量补建状态层，省 token）。"""
    settings = get_settings(db, include_secrets=True)
    analysis = db.query(BookAnalysis).filter(BookAnalysis.book_id == book.id).first()

    from ..services.rag import format_foreshadows

    chapters_text = "\n\n".join(
        f"=== 第{c.number}章 {c.title} ===\n{clamp_text(c.content, PER_CHAPTER_TEXT_LIMIT)}"
        for c in chapters
    )
    prompt = build_prompt(
        "extract_state_batch",
        settings.get("prompt_overrides"),
        current_state=build_current_state_for_extract(db, book.id, chapters[0].number),
        foreshadows=(
            format_foreshadows(_as_list(analysis.foreshadows)) if analysis else ""
        )
        or "（暂无未回收伏笔记录）",
        chapters_text=chapters_text,
        start_chapter=chapters[0].number,
        end_chapter=chapters[-1].number,
    )
    data, _result = await chat_json(
        db,
        messages=[
            {
                "role": "system",
                "content": "你是严谨的小说场记，只依据给定正文记录客观事实，只输出合法 JSON。",
            },
            {"role": "user", "content": prompt},
        ],
        task="extract_state",
        model=model or str(settings.get("state_model") or "") or None,
        book_id=book.id,
        provider_id=provider_id,
    )
    if not isinstance(data, dict):
        raise ValueError("批量状态抽取失败：模型没有返回 JSON 对象")
    items = data.get("states") or data.get("summaries") or []
    result: dict[int, dict[str, Any]] = {}
    for item in _as_list(items):
        card = _as_dict(item)
        number = int(card.get("chapter_number") or 0)
        if number:
            result[number] = card
    return result


async def ensure_states(
    db: Session,
    book_id: int,
    *,
    upto: int | None = None,
    model: str | None = None,
    provider_id: int | None = None,
    report: ReportFn | None = None,
    limit: int = 400,
    batch_size: int = 4,
) -> dict[str, Any]:
    """补全所有缺失的章节状态（拆书后、或老库升级后跑一次）。

    批量抽取失败时自动退化为逐章抽取，保证不会因为一章 JSON 出错整批报废。
    """
    report = report or _noop
    book = db.get(Book, book_id)
    if book is None:
        raise ValueError(f"书籍 {book_id} 不存在")

    pending = missing_state_chapters(db, book_id, upto=upto)[:limit]
    if not pending:
        report(1.0, "所有章节都已有状态记录", 0, 0)
        return {"book_id": book_id, "extracted": 0, "failed": 0, "total": 0, "errors": []}

    size = max(1, int(batch_size or 1))
    batches = [pending[i : i + size] for i in range(0, len(pending), size)]

    done = failed = 0
    errors: list[str] = []

    for index, batch in enumerate(batches, start=1):
        head, tail_number = batch[0].number, batch[-1].number
        report(
            (index - 1) / len(batches),
            f"抽取第 {head}~{tail_number} 章状态（{index}/{len(batches)}）",
            index - 1,
            len(batches),
        )

        extracted: dict[int, dict[str, Any]] = {}
        if len(batch) > 1:
            try:
                extracted = await extract_states_batch(
                    db, book, batch, model=model, provider_id=provider_id
                )
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning("第 %s~%s 章批量抽取失败，退化为逐章：%s", head, tail_number, exc)

        # 按章节号顺序落库，保证物品归属能正确接续传递
        for chapter in batch:
            data = extracted.get(chapter.number)
            if data is None:
                try:
                    data = await extract_chapter_state(
                        db, book, chapter, model=model, provider_id=provider_id
                    )
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    failed += 1
                    errors.append(f"第{chapter.number}章：{exc}")
                    logger.warning("第 %s 章状态抽取失败: %s", chapter.number, exc)
                    continue
            try:
                apply_state(db, book, chapter, data)
                done += 1
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                failed += 1
                errors.append(f"第{chapter.number}章（落库）：{exc}")

    report(1.0, f"状态抽取完成：成功 {done} 章，失败 {failed} 章", len(pending), len(pending))
    return {
        "book_id": book_id,
        "extracted": done,
        "failed": failed,
        "total": len(pending),
        "errors": errors[:5],
    }


async def extract_and_apply(
    db: Session,
    book_id: int,
    chapter_number: int,
    *,
    model: str | None = None,
    provider_id: int | None = None,
) -> ChapterState | None:
    """单章即时抽取（保存章节后调用）。失败不抛错，只记日志。"""
    book = db.get(Book, book_id)
    if book is None:
        return None
    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.number == chapter_number)
        .first()
    )
    if chapter is None or not (chapter.content or "").strip():
        return None
    try:
        data = await extract_chapter_state(
            db, book, chapter, model=model, provider_id=provider_id
        )
        return apply_state(db, book, chapter, data)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("第 %s 章状态抽取失败（不影响正文保存）: %s", chapter_number, exc)
        return None


def state_model_hint(db: Session) -> str:
    """给前端展示：状态抽取会用哪个模型。"""
    return resolve_model(db, "extract_state")


def seed_from_analysis(db: Session, book_id: int) -> int:
    """用拆书/故事圣经里的人物卡初始化人物快照。

    这些是「全书视角的静态信息」（身份、性格、关系），
    至于位置/行为/目标要靠逐章抽取来填。
    """
    analysis = db.query(BookAnalysis).filter(BookAnalysis.book_id == book_id).first()
    if analysis is None:
        return 0
    known = {
        row.name for row in db.query(CharacterState).filter(CharacterState.book_id == book_id).all()
    }
    created = 0
    for item in _as_list(analysis.characters):
        card = _as_dict(item)
        name = _text(card.get("name"))
        if not name or name in known:
            continue
        known.add(name)
        db.add(
            CharacterState(
                book_id=book_id,
                name=name,
                importance=_text(card.get("importance")) or "配角",
                identity=_text(card.get("identity")),
                personality=_text(card.get("personality")),
                relations=_text(card.get("relations")),
                status=_text(card.get("status")),
                items=[_text(i) for i in _as_list(card.get("items")) if _text(i)],
                knows=[_text(i) for i in _as_list(card.get("knows")) if _text(i)],
                last_seen_chapter=int(card.get("first_chapter") or 0),
            )
        )
        created += 1
    if created:
        db.commit()
    return created


def reset_states(db: Session, book_id: int, chapter_number: int | None = None) -> int:
    """删除状态记录，便于重跑。chapter_number 为 None 时清空整本。"""
    query = db.query(ChapterState).filter(ChapterState.book_id == book_id)
    if chapter_number is not None:
        query = query.filter(ChapterState.chapter_number == chapter_number)
    removed = query.count()
    query.delete(synchronize_session=False)

    chapter_query = db.query(Chapter).filter(Chapter.book_id == book_id)
    if chapter_number is not None:
        chapter_query = chapter_query.filter(Chapter.number == chapter_number)
    for chapter in chapter_query.all():
        chapter.state_extracted = False
    db.commit()
    return removed


def iter_state_rows(db: Session, book_id: int) -> Iterable[ChapterState]:
    return db.query(ChapterState).filter(ChapterState.book_id == book_id).order_by(
        ChapterState.chapter_number
    )
