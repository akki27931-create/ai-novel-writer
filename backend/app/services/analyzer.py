"""拆书模块。

流程（Map-Reduce，避免一次性塞进全书）：
1. 向量化：章节分块 -> 向量库（供后续 RAG 使用）
2. Map   ：分批生成分章摘要（Flash，成本低）
3. Reduce：基于分章摘要 + 原文文风样本，产出 人物卡/世界观/时间线/伏笔/文风（Pro）
4. 落库  ：保存为可手动编辑的 JSON
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..models import Book, BookAnalysis, Chapter
from ..prompts import build_prompt
from ..settings_store import get_settings
from .indexing import index_chapters
from .llm import chat_json
from .text_utils import clamp_text

logger = logging.getLogger(__name__)

# 送入摘要模型的单章正文上限（越大越准，但输出也越长、越容易触发 max_tokens 截断）
CHAPTER_TEXT_LIMIT = 1800
SUMMARY_BUDGET = 16000

SECTION_FIELDS = {
    "characters": "characters",
    "worldview": "worldview",
    "timeline": "timeline",
    "foreshadows": "foreshadows",
    "style": "style",
    "outline": "outline",
}

ReportFn = Callable[[float, str, int, int], None]


def _noop(_progress: float, _message: str = "", _done: int = 0, _total: int = 0) -> None:
    return None


async def analyze_book(
    db: Session,
    book_id: int,
    *,
    model: str | None = None,
    batch_size: int = 8,
    max_chapters: int | None = None,
    redo_summaries: bool = False,
    section: str = "all",
    with_states: bool = True,
    state_provider_id: int | None = None,
    report: ReportFn | None = None,
) -> dict[str, Any]:
    """拆书全流程：向量化 → 分章摘要 → 全局拆书 → 剧情状态层。

    with_states=True 时会额外给**原文**建立剧情状态层。
    这一步是续写一致性的前提：没有它，续写第 N 章时就不知道
    「上一章谁在哪、手上有什么」，只能靠 3000 字尾巴去猜。
    """
    outer = report or _noop
    # 状态层占最后 28% 的进度
    scaled = 0.72 if with_states else 1.0

    def report(progress: float, message: str = "", done: int = 0, total: int = 0) -> None:
        outer(progress * scaled, message, done, total)

    settings = get_settings(db, include_secrets=True)
    book = db.get(Book, book_id)
    if book is None:
        raise ValueError(f"书籍 {book_id} 不存在")

    query = db.query(Chapter).filter(Chapter.book_id == book_id).order_by(Chapter.number)
    if max_chapters:
        query = query.limit(max_chapters)
    chapters = query.all()
    if not chapters:
        raise ValueError("该书还没有章节，无法拆书")

    # ---------- 1. 向量化 ----------
    report(0.02, f"开始向量化 {len(chapters)} 章 ...", 0, len(chapters))

    def on_index(done: int, total: int) -> None:
        report(0.02 + 0.23 * (done / max(1, total)), f"向量化 {done}/{total} 章", done, total)

    stats = index_chapters(db, book_id, chapters, on_progress=on_index)

    # ---------- 2. Map：分章摘要 ----------
    analysis = db.query(BookAnalysis).filter(BookAnalysis.book_id == book_id).first()
    if analysis is None:
        analysis = BookAnalysis(book_id=book_id)
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

    summary_map: dict[int, dict[str, Any]] = {}
    for item in analysis.chapter_summaries or []:
        if isinstance(item, dict) and item.get("chapter_number"):
            summary_map[int(item["chapter_number"])] = item
    # 数据库里已有的章节摘要也复用
    for chapter in chapters:
        if chapter.summary and chapter.number not in summary_map:
            summary_map[chapter.number] = {
                "chapter_number": chapter.number,
                "title": chapter.title,
                "summary": chapter.summary,
                "events": [],
                "characters": [],
            }

    pending = [
        c for c in chapters if redo_summaries or c.number not in summary_map
    ]
    batches = [pending[i : i + max(1, batch_size)] for i in range(0, len(pending), max(1, batch_size))]
    report(0.25, f"需要生成 {len(pending)} 章摘要（{len(batches)} 批）", 0, len(batches))

    for index, batch in enumerate(batches, start=1):
        chapters_text = "\n\n".join(
            f"=== 第{c.number}章 {c.title} ===\n{clamp_text(c.content, CHAPTER_TEXT_LIMIT)}"
            for c in batch
        )
        prompt = build_prompt(
            "chapter_summary",
            settings.get("prompt_overrides"),
            chapters_text=chapters_text,
        )
        try:
            data, _ = await chat_json(
                db,
                messages=[
                    {"role": "system", "content": "你是严谨的小说拆解助手，只输出 JSON，不要任何解释。"},
                    {"role": "user", "content": prompt},
                ],
                task="summarize",
                model=model,
                book_id=book_id,
            )
        except Exception as exc:  # noqa: BLE001
            # 失败就立刻中止：继续跑后面的批次只会白烧 token，而且用户看不到真正原因
            raise RuntimeError(
                f"第 {index}/{len(batches)} 批章节摘要失败"
                f"（第 {batch[0].number}-{batch[-1].number} 章）：{exc}"
            ) from exc
        for item in (data.get("summaries") if isinstance(data, dict) else data) or []:
            if not isinstance(item, dict):
                continue
            number = int(item.get("chapter_number") or 0)
            if not number:
                continue
            summary_map[number] = {
                "chapter_number": number,
                "title": item.get("title") or "",
                "summary": item.get("summary") or "",
                "events": item.get("events") or [],
                "characters": item.get("characters") or [],
            }

        # 每批结束即落库，任务中断也不会全丢
        analysis.chapter_summaries = [summary_map[k] for k in sorted(summary_map)]
        for chapter in batch:
            record = summary_map.get(chapter.number)
            if record and record.get("summary"):
                chapter.summary = str(record["summary"])
        db.commit()
        report(
            0.25 + 0.35 * (index / max(1, len(batches))),
            f"已生成 {index}/{len(batches)} 批章节摘要",
            index,
            len(batches),
        )

    # ---------- 3. Reduce：全局拆书 ----------
    report(0.62, "正在汇总全书设定、人物、伏笔与文风 ...", 0, 1)
    summaries_text = _compress_summaries(analysis.chapter_summaries or [], SUMMARY_BUDGET)
    style_samples = _collect_style_samples(chapters)

    prompt = build_prompt(
        "book_analysis",
        settings.get("prompt_overrides"),
        book_title=book.title,
        chapter_summaries=summaries_text,
        style_samples=style_samples,
    )
    data, result = await chat_json(
        db,
        messages=[
            {
                "role": "system",
                "content": "你是资深网文编辑与设定管理专家，只输出严格合法的 JSON，不要任何解释或 Markdown 代码块。",
            },
            {"role": "user", "content": prompt},
        ],
        task="analyze",
        model=model,
        book_id=book_id,
    )
    if not isinstance(data, dict):
        raise ValueError("拆书模型返回的不是 JSON 对象")

    _apply_analysis(analysis, data, section=section)
    analysis.raw = {"last_model_output": data, "model": result.model}
    book.analyzed = True
    db.commit()

    states_result: dict[str, Any] | None = None
    if with_states:
        # ---------- 4. 剧情状态层 ----------
        # 拆书产出的是「静态设定」（人物是谁、世界规则），
        # 状态层产出的是「动态状态」（此刻在哪、在做什么、手上有什么）。
        # 续写靠的是后者，所以这一步不能省。
        from .state import ensure_states, seed_from_analysis

        seed_from_analysis(db, book_id)

        def on_state(progress: float, message: str = "", done: int = 0, total: int = 0) -> None:
            outer(
                0.72 + 0.28 * progress,
                message or "正在建立剧情状态层…",
                done,
                total,
            )

        states_result = await ensure_states(
            db,
            book_id,
            upto=max_chapters,
            model=None,
            provider_id=state_provider_id,
            report=on_state,
        )

    # 用 outer 而不是 report：进度已经由状态层推到 1.0，再乘缩放系数会造成回退
    outer(1.0, "拆书完成", 1, 1)

    return {
        "book_id": book_id,
        "section": section,
        "chapters_indexed": stats,
        "summaries": len(analysis.chapter_summaries or []),
        "characters": len(analysis.characters or []),
        "worldview": len(analysis.worldview or []),
        "timeline": len(analysis.timeline or []),
        "foreshadows": len(analysis.foreshadows or []),
        "states": states_result or {},
        "model": result.model,
    }


# ----------------------------------------------------------------------
def _compress_summaries(summaries: list[Any], budget: int) -> str:
    """把分章摘要压缩到预算内：按比例截断，而不是丢弃章节。"""
    items = [s for s in summaries if isinstance(s, dict)]
    if not items:
        return "（暂无分章摘要）"

    def render(limit: int | None) -> str:
        lines = []
        for item in items:
            number = item.get("chapter_number", "?")
            summary = str(item.get("summary") or "")
            if limit is not None:
                summary = summary[:limit]
            title = item.get("title") or ""
            lines.append(f"第{number}章 {title}：{summary}")
        return "\n".join(lines)

    full = render(None)
    if len(full) <= budget:
        return full

    per_chapter = max(40, int(budget / max(1, len(items))) - 20)
    return render(per_chapter)[:budget]


def _collect_style_samples(chapters: list[Chapter], total_chars: int = 2800) -> str:
    """从开头 / 中段 / 末尾各取一段原文，用于提炼文风。"""
    if not chapters:
        return "（暂无原文）"
    picks: list[Chapter] = [chapters[0]]
    if len(chapters) > 2:
        picks.append(chapters[len(chapters) // 2])
    if len(chapters) > 1:
        picks.append(chapters[-1])

    per_pick = max(400, total_chars // len(picks))
    blocks = []
    for chapter in picks:
        body = clamp_text(chapter.content, per_pick)
        blocks.append(f"【第{chapter.number}章 {chapter.title}】\n{body}")
    return "\n\n".join(blocks)


def normalize_analysis_payload(data: dict[str, Any]) -> dict[str, Any]:
    """把模型输出的拆书 JSON 规范成 ORM 可写入的结构（供原创模式复用）。"""
    return {
        "synopsis": str(data.get("synopsis") or "").strip(),
        "outline": data.get("outline") if isinstance(data.get("outline"), dict) else {},
        "characters": _ensure_list(data.get("characters")),
        "worldview": _ensure_list(data.get("worldview")),
        "timeline": _ensure_list(data.get("timeline")),
        "foreshadows": _ensure_list(data.get("foreshadows")),
        "style": data.get("style") if isinstance(data.get("style"), dict) else {},
    }


def _apply_analysis(analysis: BookAnalysis, data: dict[str, Any], *, section: str) -> None:
    """把模型输出写回 ORM，同时做字段规范化。"""
    normalized = {
        "synopsis": str(data.get("synopsis") or "").strip(),
        "outline": data.get("outline") if isinstance(data.get("outline"), dict) else {},
        "characters": _ensure_list(data.get("characters")),
        "worldview": _ensure_list(data.get("worldview")),
        "timeline": _ensure_list(data.get("timeline")),
        "foreshadows": _ensure_list(data.get("foreshadows")),
        "style": data.get("style") if isinstance(data.get("style"), dict) else {},
    }

    if section == "all":
        for key, value in normalized.items():
            setattr(analysis, key, value)
        return

    # 只更新指定板块，其余保留用户已手动修改的内容
    if section in normalized:
        setattr(analysis, section, normalized[section])
    if section == "outline" and normalized["synopsis"]:
        analysis.synopsis = normalized["synopsis"]


def _ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, (dict, str))]
    if isinstance(value, dict):
        return [
            {"name": key, **(val if isinstance(val, dict) else {"description": str(val)})}
            for key, val in value.items()
        ]
    return []


# ----------------------------------------------------------------------
async def analyze_section(
    db: Session, book_id: int, section: str, *, model: str | None = None
) -> dict[str, Any]:
    """单独重跑某个板块（例如重新提取伏笔）。

    不动剧情状态层：重跑单个板块通常只是修正静态设定，
    状态层有自己独立的补建入口（/api/books/{id}/state/extract）。
    """
    return await analyze_book(
        db,
        book_id,
        model=model,
        section=section,
        redo_summaries=False,
        with_states=False,
    )
