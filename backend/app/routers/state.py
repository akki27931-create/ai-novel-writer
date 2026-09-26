"""路由：剧情状态层（人物位置/行为/持有物/已知信息）。

这一层是「续写前后对不上」的解药：
- 查看：续写时到底喂了哪些状态给模型（可核对、可手改人物卡）
- 补建：给已导入的原文批量补建状态（老库升级、或拆书时跳过了状态层）
- 重建：清空后重跑
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..deps import require_book
from ..schemas import (
    ChapterStateOut,
    CharacterStateOut,
    ExtractStatesRequest,
    StoryStateOut,
)
from ..services.jobs import jobs
from ..services.state import (
    build_state_snapshot,
    list_chapter_states,
    list_character_states,
    missing_state_chapters,
    reset_states,
    state_model_hint,
)
from ..settings_store import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/books", tags=["state"])


@router.get("/{book_id}/state", response_model=StoryStateOut)
def read_state(book_id: int, upto: int | None = None, db: Session = Depends(get_db)) -> StoryStateOut:
    """查看某本书的剧情状态层（含最终拼装出的快照文本）。"""
    require_book(db, book_id)
    settings = get_settings(db, include_secrets=True)

    chapters = list_chapter_states(db, book_id, upto=upto)
    characters = list_character_states(db, book_id)
    missing = [c.number for c in missing_state_chapters(db, book_id, upto=upto)]

    target = upto if upto is not None else (
        (max((c.chapter_number for c in chapters), default=0) or 0) + 1
    )
    snapshot = build_state_snapshot(
        db,
        book_id,
        target_number=target,
        budget=int(settings.get("state_snapshot_budget") or 6000),
    )

    return StoryStateOut(
        book_id=book_id,
        snapshot=snapshot,
        chapters=[ChapterStateOut.model_validate(c) for c in chapters],
        characters=[CharacterStateOut.model_validate(c) for c in characters],
        missing_chapters=missing,
        state_model=state_model_hint(db),
        auto_extract=bool(settings.get("auto_extract_state", True)),
    )


@router.post("/{book_id}/state/extract")
async def start_extract_states(
    book_id: int, payload: ExtractStatesRequest, db: Session = Depends(get_db)
) -> dict[str, object]:
    """补建剧情状态层（批量抽取，走廉价模型档）。"""
    require_book(db, book_id)
    options = payload.model_dump()

    async def runner(job, report):  # noqa: ANN001, ANN202
        from ..services.state import ensure_states, seed_from_analysis

        session = SessionLocal()
        try:
            seed_from_analysis(session, book_id)
            return await ensure_states(
                session,
                book_id,
                upto=options.get("upto"),
                model=options.get("model"),
                provider_id=options.get("provider_id"),
                batch_size=int(options.get("batch_size") or 4),
                limit=int(options.get("limit") or 400),
                report=report,
            )
        finally:
            session.close()

    job = jobs.create("extract-states", runner)
    return {"job_id": job.id, "kind": job.kind, "message": "剧情状态抽取任务已启动"}


@router.delete("/{book_id}/state")
def clear_state(
    book_id: int, chapter_number: int | None = None, db: Session = Depends(get_db)
) -> dict[str, object]:
    """清空状态层。传 chapter_number 只清某一章。"""
    require_book(db, book_id)
    try:
        removed = reset_states(db, book_id, chapter_number)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"book_id": book_id, "removed": removed}
