"""AI 续写模块。

三种模式（对应需求五）：
- continue : 只生成正文（严格 RAG 上下文 + 内置续写提示词）
- outline  : 只生成大纲（走 Pro 模型）
- polish   : 润色正文（保留剧情，改文风）
- rewrite  : 重写正文（保留剧情要点，重写节奏）

生成结果统一落库到 generations 表，可保存为章节、可继续润色/重写。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from sqlalchemy.orm import Session

from ..models import Book, BookAnalysis, Chapter, ChapterState, Generation
from ..prompts import build_prompt
from ..settings_store import get_settings
from .indexing import index_chapters
from .llm import chat, chat_json, chat_stream, parse_json, resolve_model
from .rag import (
    PromptSlots,
    as_list,
    build_slots,
    format_chapter_plan,
    format_foreshadows,
    format_outline,
    load_analysis,
    previous_chapters_text,
    resolve_target_number,
)
from .state import build_state_snapshot, extract_and_apply
from .text_utils import count_words

logger = logging.getLogger(__name__)

PROMPT_BY_MODE = {
    "continue": "continue_chapter",
    "outline": "outline_only",
    "polish": "polish_text",
    "rewrite": "rewrite_text",
}

# 与 llm._pick_model 的复杂任务集合保持一致
TASK_BY_MODE = {
    "continue": "continue",
    "outline": "outline",
    "polish": "polish",
    "rewrite": "rewrite",
}

SYSTEM_PROMPT = (
    "你是一位专业的中文网文爽文作者，擅长模仿原有文风、保持剧情连贯、节奏快、爽点足。"
    "严格遵守用户给出的任务与输出格式；只输出成果本身，不要解释、不要总结、不要寒暄。"
)

ReportFn = Callable[[float, str, int, int], None]


def _noop(_p: float, _m: str = "", _d: int = 0, _t: int = 0) -> None:
    return None


# ----------------------------------------------------------------------
def _require_source(mode: str, source_text: str) -> None:
    if mode in {"polish", "rewrite"} and not (source_text or "").strip():
        raise ValueError(f"{mode} 模式必须提供待处理正文（source_text）")


@dataclass
class Prepared:
    """一次续写所需的全部材料（含 AI 自动拟定的计划）。"""

    book: Book
    slots: PromptSlots
    prompt: str
    model: str
    goal: str
    title: str = ""
    plan: dict[str, Any] | None = None
    auto_planned: bool = False


def _recent_summaries(
    db: Session, book_id: int, analysis: BookAnalysis | None, target_number: int, limit: int = 8
) -> str:
    """取目标章节之前最近几章的摘要，供 AI 推演剧情。

    关键点：AI 续写的章节原本**不会**进入 analysis.chapter_summaries，
    于是连写时第 N 章的规划看不到第 N-1 章写的是什么。这里把
    chapter_states（每章都会写）合并进来，彻底补上这个断层。
    """
    merged: dict[int, str] = {}
    if analysis is not None:
        for item in as_list(analysis.chapter_summaries):
            if not isinstance(item, dict):
                continue
            number = int(item.get("chapter_number") or 0)
            if number and number < target_number:
                merged[number] = str(item.get("summary") or "")

    # chapter_states 是更新的来源，覆盖摘书结果里可能过期的条目
    states = (
        db.query(ChapterState)
        .filter(ChapterState.book_id == book_id, ChapterState.chapter_number < target_number)
        .order_by(ChapterState.chapter_number)
        .all()
    )
    for state in states:
        if state.summary:
            merged[state.chapter_number] = state.summary

    if not merged:
        return "（暂无章节摘要，建议先执行拆书或抽取剧情状态）"
    lines = [
        f"第{number}章：{merged[number][:200]}"
        for number in sorted(merged)[-limit:]
    ]
    return "\n".join(lines)


async def plan_next_chapter(
    db: Session,
    book: Book,
    target_number: int,
    analysis: BookAnalysis | None,
    model: str | None = None,
    provider_id: int | None = None,
) -> dict[str, Any]:
    """让 AI 自己决定下一章写什么（标题 + 目标 + 要推进的伏笔 + 结尾钩子）。

    与旧版的关键差异：规划阶段就能看到「当前剧情状态」和「原创模式的既定章节计划」，
    所以推演出的剧情不会跟上一章的人物位置/持有物冲突，也不会跑偏主线。
    """
    settings = get_settings(db, include_secrets=True)
    prev_tail, _recent = previous_chapters_text(
        db,
        book.id,
        target_number,
        1,
        full_previous=bool(settings.get("previous_full_chapter", True)),
        tail_chars=int(settings.get("previous_tail_chars") or 3000),
    )
    foreshadows = (
        format_foreshadows(as_list(analysis.foreshadows)) if analysis else ""
    )

    prompt = build_prompt(
        "plan_next_chapter",
        settings.get("prompt_overrides"),
        outline=format_outline(analysis, target_number),
        story_state=build_state_snapshot(
            db,
            book.id,
            target_number=target_number,
            budget=int(settings.get("state_snapshot_budget") or 6000),
            analysis=analysis,
        ),
        foreshadows=foreshadows or "（暂无未回收伏笔记录）",
        recent_summaries=_recent_summaries(db, book.id, analysis, target_number),
        previous_tail=prev_tail or "（这是本书的开头，没有上一章）",
        chapter_plan=format_chapter_plan(analysis, target_number),
        chapter_number=target_number,
    )
    data, result = await chat_json(
        db,
        messages=[
            {"role": "system", "content": "你是资深网文主编，只输出严格合法的 JSON。"},
            {"role": "user", "content": prompt},
        ],
        task="outline",  # 走复杂任务模型（推理能力更强）
        model=model,
        book_id=book.id,
        provider_id=provider_id,
    )
    if not isinstance(data, dict):
        raise ValueError("自动推演剧情失败：模型没有返回 JSON 计划")
    logger.info(
        "AI 自动拟定第 %s 章：标题=%s（%s tokens）",
        target_number,
        data.get("title"),
        result.output_tokens,
    )
    return data


def _planned_chapter(analysis: BookAnalysis | None, target_number: int) -> dict[str, Any] | None:
    """原创模式：从章节计划里取出本章的既定目标。"""
    if analysis is None:
        return None
    for item in as_list(analysis.chapter_plan):
        if isinstance(item, dict) and int(item.get("number") or 0) == target_number:
            return item
    return None


async def extract_state_after_save(
    db: Session, book_id: int, chapter_number: int, *, provider_id: int | None = None
) -> None:
    """保存章节后自动抽取剧情状态；受设置开关控制，失败不影响主流程。"""
    settings = get_settings(db, include_secrets=True)
    if not settings.get("auto_extract_state", True):
        return
    await extract_and_apply(db, book_id, chapter_number, provider_id=provider_id)


async def build(db: Session, req: Any) -> Prepared:
    """构建上下文与最终提示词；需要时先让 AI 自动推演剧情。"""
    book = db.get(Book, req.book_id)
    if book is None:
        raise ValueError(f"书籍 {req.book_id} 不存在")
    _require_source(req.mode, getattr(req, "source_text", ""))

    settings = get_settings(db, include_secrets=True)
    target = resolve_target_number(db, book, req.chapter_number)
    analysis = load_analysis(db, book.id)

    goal = (getattr(req, "goal", "") or "").strip()
    plan: dict[str, Any] | None = None
    auto_planned = False
    title = (getattr(req, "chapter_title", "") or "").strip()

    provider_id = getattr(req, "provider_id", None)

    # 原创模式：本章在既定章节计划里已经有目标，直接采用，不必再让 AI 推演
    planned = _planned_chapter(analysis, target)
    if planned and not goal:
        goal = str(planned.get("goal") or "").strip()
        title = title or str(planned.get("title") or "").strip()

    writable = req.mode in {"continue", "outline"}
    if writable and (not goal or getattr(req, "auto_goal", False)):
        plan = await plan_next_chapter(
            db, book, target, analysis, req.model, provider_id=provider_id
        )
        goal = str(plan.get("goal") or "").strip() or goal
        title = title or str(plan.get("title") or "").strip()
        auto_planned = True

    if not goal:
        raise ValueError(
            "请填写「本章目标」，或打开「让 AI 自己决定剧情」，让它自动往下写。"
        )

    slots = build_slots(
        db,
        book,
        target_number=target,
        goal=goal,
        top_k=req.retrieval_top_k or int(settings.get("retrieval_top_k") or 8),
        previous_count=getattr(req, "previous_chapters", 2) or 2,
        analysis=analysis,
    )

    mapping = slots.as_mapping()
    mapping.update(
        {
            "chapter_number": target,
            "goal": goal,
            "target_words": req.target_words,
            "source_text": getattr(req, "source_text", "") or "",
            "chapter_plan": format_chapter_plan(analysis, target),
        }
    )
    prompt = build_prompt(
        PROMPT_BY_MODE[req.mode],
        settings.get("prompt_overrides"),
        **mapping,
    )
    model = resolve_model(db, TASK_BY_MODE[req.mode], req.model, provider_id=provider_id)
    return Prepared(
        book=book,
        slots=slots,
        prompt=prompt,
        model=model,
        goal=goal,
        title=title,
        plan=plan,
        auto_planned=auto_planned,
    )


def _history(db: Session, book_id: int) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]


def _new_generation(db: Session, req: Any, prepared: Prepared) -> Generation:
    row = Generation(
        book_id=req.book_id,
        chapter_number=prepared.slots.chapter_number,
        mode=req.mode,
        title=prepared.title,
        model=prepared.model,
        goal=prepared.goal,
        target_words=int(req.target_words or 0),
        prompt=prepared.prompt,
        source_text=getattr(req, "source_text", "") or "",
        status="running",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


async def generate(db: Session, req: Any, report: ReportFn | None = None) -> Generation:
    """一次性生成（非流式）。"""
    report = report or _noop
    provider_id = getattr(req, "provider_id", None)
    prepared = await build(db, req)
    target = prepared.slots.chapter_number
    report(0.05, "上下文拼装完成，正在调用模型 ...", 0, 1)

    row = _new_generation(db, req, prepared)
    try:
        result = await chat(
            db,
            messages=_history(db, prepared.book.id)
            + [{"role": "user", "content": prepared.prompt}],
            task=TASK_BY_MODE[req.mode],
            model=req.model,
            book_id=prepared.book.id,
            temperature=req.temperature,
            provider_id=provider_id,
        )
    except Exception as exc:  # noqa: BLE001
        row.status = "error"
        row.error = str(exc)
        db.commit()
        report(1.0, f"生成失败：{exc}", 1, 1)
        raise

    row.result = result.text
    row.model = result.model
    row.input_tokens = result.input_tokens
    row.output_tokens = result.output_tokens
    row.duration_ms = result.duration_ms
    row.status = "success"

    from ..settings_store import estimate_cost, get_settings

    row.cost_usd, row.cost_cny = estimate_cost(result.model, result.input_tokens, result.output_tokens, db)

    if result.truncated:
        limit = get_settings(db, include_secrets=True).get("max_tokens")
        row.error = (
            f"⚠️ 输出达到 max_tokens={limit} 被截断，本章可能不完整。"
            f"请在「设置 → 生成参数」把 max_tokens 调大（建议 32768），或把字数要求调小。"
        )

    # 被截断的内容不自动保存，避免把残章写进正文
    if getattr(req, "auto_save", False) and req.mode == "continue" and not result.truncated:
        try:
            chapter = save_as_chapter(
                db,
                prepared.book.id,
                result.text,
                number=target,
                title=_chapter_title(target, prepared.title),
                overwrite=False,
                allow_renumber=True,
            )
            if chapter is not None:
                row.saved_chapter_id = chapter.id
                report(0.9, "正在抽取本章剧情状态 ...", 1, 1)
                await extract_state_after_save(
                    db, prepared.book.id, chapter.number, provider_id=provider_id
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("自动保存章节失败（不影响生成结果）: %s", exc)
            row.error = f"自动保存失败：{exc}"

    db.commit()
    db.refresh(row)
    report(1.0, "生成完成", 1, 1)
    return row


async def generate_stream(db: Session, req: Any) -> AsyncIterator[dict[str, Any]]:
    """流式生成，逐段返回给前端（SSE）。"""
    provider_id = getattr(req, "provider_id", None)
    prepared = await build(db, req)
    target = prepared.slots.chapter_number
    row = _new_generation(db, req, prepared)

    yield {
        "type": "meta",
        "generation_id": row.id,
        "chapter_number": target,
        "model": prepared.model,
        "prompt": prepared.prompt,
        "goal": prepared.goal,
        "title": prepared.title,
        "auto_planned": prepared.auto_planned,
        "plan": prepared.plan,
        "story_state": prepared.slots.story_state,
        "retrieved": [
            {
                "chapter_number": item["chapter_number"],
                "chapter_title": item.get("chapter_title", ""),
                "score": item["score"],
                "text": item["text"],
                "recall": item.get("recall", "semantic"),
            }
            for item in prepared.slots.retrieved
        ],
    }

    try:
        async for event in chat_stream(
            db,
            messages=_history(db, prepared.book.id)
            + [{"role": "user", "content": prepared.prompt}],
            task=TASK_BY_MODE[req.mode],
            model=req.model,
            book_id=prepared.book.id,
            temperature=req.temperature,
            provider_id=provider_id,
        ):
            if event["type"] == "delta":
                yield {"type": "delta", "text": event["text"]}
                continue

            row.result = event["text"]
            row.model = event["model"]
            row.input_tokens = event["input_tokens"]
            row.output_tokens = event["output_tokens"]
            row.duration_ms = event["duration_ms"]
            row.status = "success"

            from ..settings_store import estimate_cost, get_settings

            row.cost_usd, row.cost_cny = estimate_cost(
                event["model"], event["input_tokens"], event["output_tokens"], db
            )

            truncated = bool(event.get("truncated"))
            if truncated:
                limit = get_settings(db, include_secrets=True).get("max_tokens")
                row.error = (
                    f"⚠️ 输出达到 max_tokens={limit} 被截断，本章可能不完整。"
                    f"请在「设置 → 生成参数」把 max_tokens 调大（建议 32768）。"
                )

            if (
                getattr(req, "auto_save", False)
                and req.mode == "continue"
                and not truncated
            ):
                try:
                    chapter = save_as_chapter(
                        db,
                        prepared.book.id,
                        event["text"],
                        number=target,
                        title=_chapter_title(target, prepared.title),
                        overwrite=False,
                        allow_renumber=True,
                    )
                    if chapter is not None:
                        row.saved_chapter_id = chapter.id
                        yield {
                            "type": "status",
                            "message": "正在抽取本章剧情状态 ...",
                        }
                        await extract_state_after_save(
                            db, prepared.book.id, chapter.number, provider_id=provider_id
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("自动保存章节失败（不影响生成结果）: %s", exc)
                    row.error = f"自动保存失败：{exc}"

            db.commit()
            db.refresh(row)
            yield {
                "type": "done",
                "generation_id": row.id,
                "chapter_number": target,
                "model": row.model,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "cost_usd": row.cost_usd,
                "cost_cny": row.cost_cny,
                "word_count": count_words(row.result),
                "saved_chapter_id": row.saved_chapter_id,
                "truncated": truncated,
                "warning": row.error,
            }
    except Exception as exc:  # noqa: BLE001
        row.status = "error"
        row.error = str(exc)
        db.commit()
        yield {"type": "error", "message": str(exc), "generation_id": row.id}


def _chapter_title(number: int, ai_title: str = "") -> str:
    """拼章节标题：有 AI 拟定的标题就用「第N章 标题」。"""
    ai_title = (ai_title or "").strip()
    return f"第{number}章 {ai_title}" if ai_title else f"第{number}章"


async def generate_batch(
    db: Session, req: Any, report: ReportFn | None = None
) -> dict[str, Any]:
    """一口气连写 N 章（全自动）。

    每一章都会重新推演剧情、重新检索上下文、写完立刻入库并建立向量索引，
    因此第 3 章能读到第 2 章刚写的内容，剧情是连贯推进的。
    """
    from ..schemas import GenerateRequest

    report = report or _noop
    book = db.get(Book, req.book_id)
    if book is None:
        raise ValueError(f"书籍 {req.book_id} 不存在")

    count = max(1, min(int(getattr(req, "count", 5) or 5), 20))
    chapter_number = getattr(req, "start_chapter", None) or None
    provider_id = getattr(req, "provider_id", None)
    completed: list[dict[str, Any]] = []

    for index in range(count):
        target = resolve_target_number(db, book, chapter_number)
        report(
            0.02 + 0.95 * (index / count),
            f"正在写第 {index + 1}/{count} 章（第 {target} 章）：AI 正在推演剧情…",
            index,
            count,
        )

        single = GenerateRequest(
            book_id=req.book_id,
            chapter_number=target,
            goal="",                     # 留空 = 让 AI 自己决定
            target_words=int(getattr(req, "target_words", 3000) or 3000),
            model=req.model,
            provider_id=provider_id,
            mode="continue",
            previous_chapters=int(getattr(req, "previous_chapters", 2) or 2),
            auto_save=False,             # 由本函数统一负责入库，带上 AI 拟定的标题
        )

        generation = await generate(db, single)

        chapter = None
        if not generation.error or "截断" not in (generation.error or ""):
            chapter = save_as_chapter(
                db,
                book.id,
                generation.result,
                number=target,
                title=_chapter_title(target, generation.title),
                overwrite=False,
                allow_renumber=True,
            )
        if chapter is not None:
            generation.saved_chapter_id = chapter.id
            db.commit()
            report(
                0.02 + 0.95 * ((index + 0.5) / count),
                f"正在抽取第 {chapter.number} 章剧情状态…",
                index,
                count,
            )
            # 关键：先把本章状态落库，下一章的推演才能对齐人物位置与持有物
            await extract_state_after_save(
                db, book.id, chapter.number, provider_id=provider_id
            )

        completed.append(
            {
                "generation_id": generation.id,
                "chapter_id": chapter.id if chapter else None,
                "number": chapter.number if chapter else target,
                "title": chapter.title if chapter else "",
                "words": chapter.word_count if chapter else 0,
                "warning": generation.error or "",
            }
        )
        chapter_number = None  # 后续章节自动接续

        report(
            0.02 + 0.95 * ((index + 1) / count),
            f"已完成 {index + 1}/{count} 章"
            + (f"（{chapter.title}，{chapter.word_count} 字）" if chapter else "（未能入库）"),
            index + 1,
            count,
        )

    report(1.0, f"连写完成，共生成 {len(completed)} 章", count, count)
    return {"count": len(completed), "chapters": completed}


# ----------------------------------------------------------------------
def save_as_chapter(
    db: Session,
    book_id: int,
    content: str,
    *,
    number: int | None = None,
    title: str | None = None,
    overwrite: bool = False,
    allow_renumber: bool = False,
) -> Chapter | None:
    """把生成结果保存为章节；并立即建立向量索引，供后续续写检索。

    allow_renumber=True 时，如果目标章节号已被占用，会自动顺延到下一个空号
    （用于“生成后自动保存”，避免因为号段冲突把内容丢掉）。
    """
    content = (content or "").strip()
    if not content:
        return None

    if number is None or number <= 0:
        number = _next_number(db, book_id)

    existing = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.number == number)
        .first()
    )
    if existing is not None and not overwrite:
        if allow_renumber:
            number = _next_number(db, book_id)
            existing = None
        else:
            raise ValueError(f"第 {number} 章已存在，请选择覆盖或换一个章节号")

    if existing is not None:
        existing.content = content
        existing.title = title or existing.title
        existing.word_count = count_words(content)
        existing.is_original = False
        existing.origin = "ai"
        existing.vectorized = False
        chapter = existing
    else:
        chapter = Chapter(
            book_id=book_id,
            number=number,
            title=title or f"第{number}章",
            content=content,
            word_count=count_words(content),
            is_original=False,
            origin="ai",
        )
        db.add(chapter)

    db.flush()
    book = db.get(Book, book_id)
    if book is not None:
        book.total_chapters = db.query(Chapter).filter(Chapter.book_id == book_id).count()
    db.commit()
    db.refresh(chapter)

    # 新增章节立刻向量化（失败不影响保存）
    try:
        index_chapters(db, book_id, [chapter], force=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("新章节向量化失败（不影响保存）: %s", exc)
    return chapter


def _next_number(db: Session, book_id: int) -> int:
    last = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id)
        .order_by(Chapter.number.desc())
        .first()
    )
    return (last.number + 1) if last else 1


async def check_consistency(
    db: Session,
    book_id: int,
    source_text: str,
    model: str | None = None,
    provider_id: int | None = None,
) -> dict[str, Any]:
    """伏笔检查 / 逻辑矛盾检查（走 Pro 模型）。"""
    book = db.get(Book, book_id)
    if book is None:
        raise ValueError(f"书籍 {book_id} 不存在")
    settings = get_settings(db, include_secrets=True)
    slots = build_slots(
        db,
        book,
        target_number=resolve_target_number(db, book, None),
        goal="检查正文与设定的冲突、人物是否跑偏、伏笔是否遗漏",
        top_k=int(settings.get("retrieval_top_k") or 8),
        previous_count=1,
    )
    # 把「当前剧情状态」一并交给校对，它才有依据判断「人物是不是不该出现在这里」
    core_settings = slots.story_state + "\n\n" + slots.core_settings
    prompt = build_prompt(
        "consistency_check",
        settings.get("prompt_overrides"),
        core_settings=core_settings,
        foreshadows=slots.foreshadows,
        source_text=source_text,
    )
    result = await chat(
        db,
        messages=[
            {"role": "system", "content": "你是严谨的网文剧情校对，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        task="consistency",
        model=model,
        book_id=book_id,
        provider_id=provider_id,
    )
    try:
        data = parse_json(result.text)
    except Exception:  # noqa: BLE001
        data = {"issues": [], "overall": result.text[:300]}
    return {"report": data, "model": result.model, "usage": {
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }}
