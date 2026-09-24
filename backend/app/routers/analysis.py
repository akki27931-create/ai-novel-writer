"""路由：拆书结果读写、拆书任务、向量化任务。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..deps import require_book
from ..models import BookAnalysis, Chapter
from ..schemas import AnalysisOut, AnalysisSectionUpdate, AnalyzeRequest, VectorizeRequest
from ..services.analyzer import analyze_book
from ..services.indexing import index_chapters
from ..services.jobs import jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/books", tags=["analysis"])

EMPTY = {
    "synopsis": "",
    "outline": {},
    "characters": [],
    "worldview": [],
    "timeline": [],
    "foreshadows": [],
    "style": {},
    "chapter_summaries": [],
}


def _serialize(book_id: int, analysis: BookAnalysis | None) -> AnalysisOut:
    if analysis is None:
        return AnalysisOut(book_id=book_id, updated_at=None, **EMPTY)
    return AnalysisOut(
        book_id=book_id,
        synopsis=analysis.synopsis or "",
        outline=analysis.outline or {},
        characters=analysis.characters or [],
        worldview=analysis.worldview or [],
        timeline=analysis.timeline or [],
        foreshadows=analysis.foreshadows or [],
        style=analysis.style or {},
        chapter_summaries=analysis.chapter_summaries or [],
        updated_at=analysis.updated_at,
    )


@router.get("/{book_id}/analysis", response_model=AnalysisOut)
def get_analysis(book_id: int, db: Session = Depends(get_db)) -> AnalysisOut:
    require_book(db, book_id)
    analysis = db.query(BookAnalysis).filter(BookAnalysis.book_id == book_id).first()
    return _serialize(book_id, analysis)


@router.put("/{book_id}/analysis", response_model=AnalysisOut)
def update_analysis(
    book_id: int, payload: AnalysisSectionUpdate, db: Session = Depends(get_db)
) -> AnalysisOut:
    """手动修改拆书结果（前端可直接编辑 JSON 之外的字段）。"""
    require_book(db, book_id)
    analysis = db.query(BookAnalysis).filter(BookAnalysis.book_id == book_id).first()
    if analysis is None:
        analysis = BookAnalysis(book_id=book_id)
        db.add(analysis)
        db.flush()

    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is None and key != "synopsis":
            continue
        setattr(analysis, key, value)
    db.commit()
    db.refresh(analysis)
    return _serialize(book_id, analysis)


@router.post("/{book_id}/analyze")
async def start_analyze(
    book_id: int, payload: AnalyzeRequest, db: Session = Depends(get_db)
) -> dict[str, object]:
    """启动拆书后台任务（向量化 + 分章摘要 + 全局拆书）。

    必须是 async 端点：后台任务用 asyncio.create_task 调度，同步端点跑在线程池里没有事件循环。
    """
    book = require_book(db, book_id)
    if not book.total_chapters:
        raise HTTPException(status_code=400, detail="该书还没有章节，请先导入小说")

    options = payload.model_dump()

    async def runner(job, report):  # noqa: ANN001, ANN202
        session = SessionLocal()
        try:
            return await analyze_book(
                session,
                book_id,
                model=options.get("model"),
                batch_size=int(options.get("batch_size") or 8),
                max_chapters=options.get("max_chapters"),
                redo_summaries=bool(options.get("redo_summaries")),
                section=str(options.get("section") or "all"),
                report=report,
            )
        finally:
            session.close()

    job = jobs.create("analyze", runner)
    return {"job_id": job.id, "kind": job.kind, "message": "拆书任务已启动"}


@router.post("/{book_id}/vectorize")
async def start_vectorize(
    book_id: int, payload: VectorizeRequest, db: Session = Depends(get_db)
) -> dict[str, object]:
    """单独建立/重建向量索引。"""
    require_book(db, book_id)

    def _fetch(session: Session):
        query = session.query(Chapter).filter(Chapter.book_id == book_id).order_by(Chapter.number)
        if payload.max_chapters:
            query = query.limit(payload.max_chapters)
        return query.all()

    async def runner(job, report):  # noqa: ANN001, ANN202
        session = SessionLocal()
        try:
            chapters = _fetch(session)
            if not chapters:
                raise ValueError("该书还没有章节")
            report(0.02, f"准备向量化 {len(chapters)} 章", 0, len(chapters))

            def on_progress(done: int, total: int) -> None:
                report(done / max(1, total), f"向量化 {done}/{total} 章", done, total)

            return index_chapters(session, book_id, chapters, force=payload.force, on_progress=on_progress)
        finally:
            session.close()

    job = jobs.create("vectorize", runner)
    return {"job_id": job.id, "kind": job.kind, "message": "向量化任务已启动"}
