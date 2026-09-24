"""路由：番茄小说 MCP 适配（搜索 / 详情 / 目录 / 批量下载导入）。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..schemas import FanqieImportRequest, FanqieSearchRequest, FanqieStatusOut
from ..services import importer
from ..services.fanqie import FanqieAdapter, FanqieUnavailable
from ..services.jobs import jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fanqie", tags=["fanqie"])


def _unavailable(exc: FanqieUnavailable) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=f"{exc}（可改用「上传 TXT / EPUB」或「手动粘贴」导入）",
    )


@router.get("/status", response_model=FanqieStatusOut)
async def status(db: Session = Depends(get_db)) -> FanqieStatusOut:
    adapter = FanqieAdapter.from_db(db)
    return FanqieStatusOut(**await adapter.status())


@router.post("/selftest")
async def selftest(db: Session = Depends(get_db)) -> dict[str, Any]:
    """逐步自检番茄链路，帮助定位卡在哪一环（进程 / 工具 / 上游接口）。"""
    adapter = FanqieAdapter.from_db(db)
    return await adapter.selftest()


@router.post("/search")
async def search(payload: FanqieSearchRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    adapter = FanqieAdapter.from_db(db)
    try:
        result = await adapter.search(payload.keyword)
    except FanqieUnavailable as exc:
        raise _unavailable(exc) from exc
    return {"keyword": payload.keyword, "result": result}


@router.post("/detail")
async def detail(payload: FanqieImportRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    adapter = FanqieAdapter.from_db(db)
    try:
        book = await adapter.book_detail(payload.book_id)
    except FanqieUnavailable as exc:
        raise _unavailable(exc) from exc
    return {"book_id": payload.book_id, "detail": book}


@router.post("/catalog")
async def catalog(payload: FanqieImportRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    adapter = FanqieAdapter.from_db(db)
    try:
        result = await adapter.catalog(payload.book_id)
    except FanqieUnavailable as exc:
        raise _unavailable(exc) from exc
    return {"book_id": payload.book_id, "catalog": result}


@router.post("/import")
async def start_import(payload: FanqieImportRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """后台任务：按书籍 ID 批量下载章节并入库。"""
    options = payload.model_dump()

    async def runner(job, report):  # noqa: ANN001, ANN202
        session = SessionLocal()
        try:
            adapter = FanqieAdapter.from_db(session)
            report(0.03, "正在连接番茄 MCP ...")

            def on_progress(done: int, total: int, message: str = "") -> None:
                report(0.03 + 0.67 * (done / max(1, total)), message, done, total)

            data = await adapter.download(
                str(options["book_id"]),
                start_chapter=int(options.get("start_chapter") or 1),
                max_chapters=options.get("max_chapters"),
                on_progress=on_progress,
            )
            report(0.7, f"下载完成，共 {len(data['chapters'])} 章，正在入库 ...")
            book = importer.create_book(
                session,
                title=options.get("title") or data["title"],
                author=data["author"],
                intro=data["intro"],
                cover_url=data["cover_url"],
                source="fanqie",
                source_id=str(options["book_id"]),
                chapters=data["chapters"],
            )
            result: dict[str, Any] = {
                "book_id": book.id,
                "title": book.title,
                "chapters": book.total_chapters,
            }
            if options.get("analyze"):
                from ..services.analyzer import analyze_book

                report(0.8, "开始拆书 ...")
                result["analysis"] = await analyze_book(
                    session, book.id, report=lambda p, m="", d=0, t=0: report(0.8 + 0.2 * p, m, d, t)
                )
            return result
        finally:
            session.close()

    job = jobs.create("fanqie-import", runner)
    return {"job_id": job.id, "message": "番茄下载任务已启动"}
