"""路由：从零原创模式。

思路：先和 AI 一起把「故事圣经」定下来（总纲 / 人物 / 世界观 / 伏笔 / 文风），
再排好逐章计划，之后每一章都按计划写。
因为设定从一开始就在模型手里，人物位置、持有物、时间线天然不容易跑偏 ——
这比"续写别人的书、只给前面几章"稳定得多。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..deps import require_book
from ..schemas import OriginalBookRequest, PlanChaptersRequest
from ..services.jobs import jobs
from ..services.original import plan_chapters

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/books", tags=["original"])


@router.post("/original")
async def create_original(payload: OriginalBookRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    """用一句话设定，让 AI 生成故事圣经并建书（后台任务）。"""
    options = payload.model_dump()

    async def runner(job, report):  # noqa: ANN001, ANN202
        from ..services.original import create_original_book

        session = SessionLocal()
        try:
            return await create_original_book(
                session,
                OriginalBookRequest(**options),
                report=report,
                provider_id=options.get("provider_id"),
            )
        finally:
            session.close()

    job = jobs.create("original-book", runner)
    return {
        "job_id": job.id,
        "kind": job.kind,
        "message": f"正在为你构思《{payload.title}》，稍等片刻…",
    }


@router.post("/{book_id}/plan-chapters")
async def start_plan_chapters(
    book_id: int, payload: PlanChaptersRequest, db: Session = Depends(get_db)
) -> dict[str, object]:
    """按故事圣经排出逐章计划（可分批续排）。"""
    require_book(db, book_id)
    options = payload.model_dump()

    async def runner(job, report):  # noqa: ANN001, ANN202
        session = SessionLocal()
        try:
            total = int(options.get("count") or 20)
            report(0.05, f"正在推演 {total} 章的计划…", 0, total)
            planned = await plan_chapters(
                session,
                book_id,
                start_chapter=int(options.get("start_chapter") or 1),
                count=total,
                model=options.get("model"),
                provider_id=options.get("provider_id"),
                replace=bool(options.get("replace")),
                batch_size=int(options.get("batch_size") or 0),
                report=report,
            )
            report(1.0, f"已排出 {len(planned)} 章计划", total, total)
            return {"book_id": book_id, "planned": planned}
        finally:
            session.close()

    job = jobs.create("plan-chapters", runner)
    return {"job_id": job.id, "kind": job.kind, "message": "章节计划任务已启动"}


@router.post("/{book_id}/plan-chapters/sync")
async def plan_chapters_sync(
    book_id: int, payload: PlanChaptersRequest, db: Session = Depends(get_db)
) -> dict[str, object]:
    """同步版本，适合排少量章节（任务量小、前端想立刻拿到结果时用）。"""
    require_book(db, book_id)
    try:
        planned = await plan_chapters(
            db,
            book_id,
            start_chapter=payload.start_chapter,
            count=payload.count,
            model=payload.model,
            provider_id=payload.provider_id,
            replace=payload.replace,
            batch_size=payload.batch_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"book_id": book_id, "count": len(planned), "planned": planned}
