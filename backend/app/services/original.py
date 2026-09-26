"""从零原创模式。

为什么需要它
-----------
续写别人的书，模型要先"读懂"大量前文才能保证一致；而从头原创时，
设定是**我们自己和模型一起定的**，模型手里始终握有完整的世界观与人物表，
一致性天然好得多 —— 这正是「从头开始写反而更顺」的原因。

流程
----
1. generate_bible : 用户给一句话设定 → AI 产出「故事圣经」（总纲/人物/世界观/伏笔/文风）
2. plan_chapters  : 用故事圣经排出逐章计划（每章标题 + 目标 + 要推进的伏笔）
3. 之后走普通的续写流程：writer.build 会优先采用章节计划里的既定目标，
   状态层（state.py）在每章写完后自动维护人物位置与持有物。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..models import Book, BookAnalysis
from ..prompts import build_prompt
from ..settings_store import get_settings
from .analyzer import normalize_analysis_payload
from .llm import chat_json
from .rag import as_list, format_outline
from .state import seed_from_analysis

logger = logging.getLogger(__name__)

ReportFn = Callable[[float, str, int, int], None]


def _noop(_p: float, _m: str = "", _d: int = 0, _t: int = 0) -> None:
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def generate_bible(
    db: Session,
    req: Any,
    *,
    provider_id: int | None = None,
) -> dict[str, Any]:
    """让 AI 产出故事圣经。"""
    settings = get_settings(db, include_secrets=True)
    prompt = build_prompt(
        "original_bible",
        settings.get("prompt_overrides"),
        book_title=req.title,
        genre=req.genre or "玄幻爽文",
        protagonist=req.protagonist or "（未指定，请你自行设计一个有记忆点的主角）",
        hook=req.hook or "（未指定，请你设计一个足够爽、能支撑长线的核心设定）",
        style_hint=req.style_hint or "节奏快、冲突直接、爽点密集",
        total_chapters=req.total_chapters,
        target_words=req.target_words,
        extra=req.extra or "（无）",
    )
    data, result = await chat_json(
        db,
        messages=[
            {
                "role": "system",
                "content": "你是资深网文爽文策划，只输出严格合法的 JSON，不要任何解释。",
            },
            {"role": "user", "content": prompt},
        ],
        task="analyze",          # 走复杂/推理档
        model=req.model,
        provider_id=provider_id or getattr(req, "provider_id", None),
    )
    if not isinstance(data, dict):
        raise ValueError("故事圣经生成失败：模型没有返回 JSON 对象")
    logger.info("故事圣经生成完成：%s（模型 %s）", req.title, result.model)
    return data


def _parse_plan_items(data: Any, fallback_start: int) -> list[dict[str, Any]]:
    """把模型返回的章节列表规范化。"""
    items = as_list(data.get("chapters") if isinstance(data, dict) else data)
    planned: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=fallback_start):
        card = _as_dict(item)
        if not card:
            continue
        number = int(card.get("number") or index)
        planned.append(
            {
                "number": number,
                "title": str(card.get("title") or "").strip(),
                "goal": str(card.get("goal") or "").strip(),
                "arc": str(card.get("arc") or "").strip(),
                "foreshadows_to_advance": [
                    str(x) for x in as_list(card.get("foreshadows_to_advance")) if str(x).strip()
                ],
            }
        )
    return planned


def _previous_digest(planned: list[dict[str, Any]], keep: int = 3) -> str:
    """把上一批计划的最后几章压成一小段，喂给下一批做衔接。"""
    if not planned:
        return "（这是第一批，从头开始排）"
    lines = []
    for item in planned[-keep:]:
        goal = str(item.get("goal") or "")[:80]
        lines.append(f"- 第{item.get('number')}章 {item.get('title') or ''}：{goal}")
    return "\n".join(lines)


async def plan_chapters(
    db: Session,
    book_id: int,
    *,
    start_chapter: int = 1,
    count: int = 20,
    model: str | None = None,
    provider_id: int | None = None,
    replace: bool = False,
    batch_size: int = 0,
    report: ReportFn | None = None,
) -> list[dict[str, Any]]:
    """按故事圣经排出逐章计划，写入 analysis.chapter_plan。

    分批调用模型（默认每批 5 章）。一次性让模型吐 20 章的计划，在推理模型上
    往往要等好几分钟，而且很容易被 max_tokens 截断；分批之后每批都很快，
    进度条也会跟着动，用户不会再以为卡死了。
    """
    report = report or _noop
    book = db.get(Book, book_id)
    if book is None:
        raise ValueError(f"书籍 {book_id} 不存在")
    analysis = db.query(BookAnalysis).filter(BookAnalysis.book_id == book_id).first()
    if analysis is None:
        raise ValueError("这本书还没有故事圣经，请先执行「生成故事圣经」")

    settings = get_settings(db, include_secrets=True)
    from ..services.rag import format_characters, format_foreshadows

    if not batch_size or batch_size <= 0:
        batch_size = int(settings.get("plan_batch_size") or 5)
    batch_size = max(1, min(batch_size, count))

    characters_text = format_characters(as_list(analysis.characters), limit=12)
    foreshadows_text = format_foreshadows(as_list(analysis.foreshadows)) or "（暂无）"

    planned: list[dict[str, Any]] = []
    last_model = ""
    offset = 0
    while offset < count:
        chunk = min(batch_size, count - offset)
        first = start_chapter + offset
        last = first + chunk - 1
        report(
            0.1 + 0.85 * (offset / count),
            f"正在排第 {first}~{last} 章的计划（已完成 {offset}/{count} 章）…"
            f"每次模型调用可能要等 1~3 分钟",
            offset,
            count,
        )

        prompt = build_prompt(
            "plan_chapters",
            settings.get("prompt_overrides"),
            outline=format_outline(analysis, first),
            characters=characters_text,
            foreshadows=foreshadows_text,
            previous=_previous_digest(planned),
            start_chapter=first,
            end_chapter=last,
            count=chunk,
        )
        data, result = await chat_json(
            db,
            messages=[
                {"role": "system", "content": "你是资深网文作者，只输出严格合法的 JSON。"},
                {"role": "user", "content": prompt},
            ],
            task="outline",
            model=model,
            provider_id=provider_id,
        )
        last_model = result.model
        got = _parse_plan_items(data, first)
        if not got:
            raise ValueError(
                f"章节计划生成失败：模型没有返回第 {first}~{last} 章的计划"
                f"（可到「设置 → 生成参数」把 llm_timeout 调大，或减小每批章节数）"
            )
        planned.extend(got)
        offset += chunk
        report(
            0.1 + 0.85 * (offset / count),
            f"已完成 {min(offset, count)}/{count} 章计划",
            offset,
            count,
        )

    if replace:
        merged = planned
    else:
        existing = {
            int(_as_dict(item).get("number") or 0): _as_dict(item)
            for item in as_list(analysis.chapter_plan)
            if _as_dict(item)
        }
        for item in planned:
            existing[item["number"]] = item
        merged = [existing[k] for k in sorted(existing) if k]

    analysis.chapter_plan = merged
    db.commit()
    logger.info(
        "第 %s~%s 章计划生成完成（%s 批，模型 %s）",
        start_chapter,
        start_chapter + count - 1,
        (count + batch_size - 1) // batch_size,
        last_model,
    )
    return planned


async def create_original_book(
    db: Session,
    req: Any,
    *,
    report: ReportFn | None = None,
    provider_id: int | None = None,
) -> dict[str, Any]:
    """一键建书：生成故事圣经 → 建 Book + BookAnalysis → 排章节计划 → 初始化人物快照。"""
    report = report or _noop
    report(0.05, "正在生成故事圣经（总纲 / 人物 / 世界观 / 伏笔）…", 0, 3)

    bible = await generate_bible(db, req, provider_id=provider_id)

    book = Book(
        title=req.title,
        author="",
        source="original",
        kind="original",
        intro=str(bible.get("synopsis") or "")[:1000],
        total_chapters=0,
        analyzed=True,
    )
    db.add(book)
    db.flush()

    analysis = db.query(BookAnalysis).filter(BookAnalysis.book_id == book.id).first()
    if analysis is None:
        analysis = BookAnalysis(book_id=book.id)
        db.add(analysis)

    normalized = normalize_analysis_payload(bible)
    for key, value in normalized.items():
        setattr(analysis, key, value)
    analysis.raw = {"story_bible": bible}
    db.commit()
    db.refresh(book)

    report(0.5, "正在初始化人物状态快照…", 1, 3)
    seed_from_analysis(db, book.id)

    planned: list[dict[str, Any]] = []
    plan_count = int(getattr(req, "plan_chapters", 0) or 0)
    if plan_count > 0:
        # 把 0.6~0.95 这段留给分批排计划，让进度条真的动起来
        def plan_report(value: float, message: str = "", done: int = 0, total: int = 0) -> None:
            report(0.6 + 0.35 * value, message, done, total)

        try:
            planned = await plan_chapters(
                db,
                book.id,
                start_chapter=1,
                count=plan_count,
                model=req.model,
                provider_id=provider_id,
                replace=True,
                batch_size=int(getattr(req, "plan_batch_size", 0) or 0),
                report=plan_report,
            )
        except Exception as exc:  # noqa: BLE001
            # 计划失败不该毁掉整本书，后面可以单独重排
            logger.warning("章节计划生成失败（可在书籍页单独重排）: %s", exc)
            report(0.95, f"章节计划生成失败（不影响建书）：{exc}", 2, 3)

    report(1.0, f"《{book.title}》创建完成，已排 {len(planned)} 章计划", 3, 3)
    return {
        "book_id": book.id,
        "title": book.title,
        "characters": len(as_list(analysis.characters)),
        "worldview": len(as_list(analysis.worldview)),
        "foreshadows": len(as_list(analysis.foreshadows)),
        "planned_chapters": len(planned),
    }
