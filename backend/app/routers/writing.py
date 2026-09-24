"""路由：AI 续写、流式生成、生成记录管理、伏笔/矛盾检查。"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..deps import require_book
from ..models import Chapter, Generation
from ..schemas import (
    BatchGenerateRequest,
    ChapterOut,
    ConsistencyRequest,
    GenerateContext,
    GenerateRequest,
    GenerationOut,
    RetrievedChunk,
    SaveGenerationRequest,
)
from ..services.jobs import jobs
from ..services.writer import (
    PROMPT_BY_MODE,
    build,
    check_consistency,
    generate,
    generate_batch,
    generate_stream,
    save_as_chapter,
)
from ..services.text_utils import count_words

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["writing"])


def _fail_400(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# ----------------------------------------------------------------------
# 生成前的上下文预览（让用户看到到底喂了哪些材料给模型）
# ----------------------------------------------------------------------
@router.post("/generate/preview", response_model=GenerateContext)
async def preview(payload: GenerateRequest, db: Session = Depends(get_db)) -> GenerateContext:
    """预览本次会发送什么给模型（含 AI 自动推演的剧情计划）。

    注意：如果目标留空，这一步会真的调用一次模型来推演剧情。
    """
    try:
        prepared = await build(db, payload)
    except ValueError as exc:
        raise _fail_400(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    slots = prepared.slots
    return GenerateContext(
        chapter_number=slots.chapter_number,
        model=prepared.model,
        prompt=prepared.prompt,
        outline_text=slots.outline,
        core_settings_text=slots.core_settings,
        foreshadow_text=slots.foreshadows,
        previous_tail=slots.previous_tail,
        goal=prepared.goal,
        title=prepared.title,
        auto_planned=prepared.auto_planned,
        plan=prepared.plan,
        retrieved=[
            RetrievedChunk(
                chapter_number=item["chapter_number"],
                chapter_title=item.get("chapter_title", ""),
                score=item["score"],
                text=item["text"],
            )
            for item in slots.retrieved
        ],
    )


# ----------------------------------------------------------------------
# 非流式生成
# ----------------------------------------------------------------------
@router.post("/generate", response_model=GenerationOut)
async def generate_once(
    payload: GenerateRequest, db: Session = Depends(get_db)
) -> Generation:
    try:
        return await generate(db, payload)
    except ValueError as exc:
        raise _fail_400(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ----------------------------------------------------------------------
# 流式生成（SSE）
# ----------------------------------------------------------------------
def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/generate/stream")
async def generate_sse(payload: GenerateRequest) -> StreamingResponse:
    """流式返回生成结果。

    不使用 Depends(get_db)：StreamingResponse 的生成器可能在依赖清理之后才执行，
    这里自行管理会话生命周期，保证整个流式过程都有可用的数据库连接。
    """
    session = SessionLocal()

    async def event_stream():  # noqa: ANN202
        try:
            async for event in generate_stream(session, payload):
                yield _sse(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("流式生成失败")
            yield _sse({"type": "error", "message": str(exc)})
        finally:
            yield "data: [DONE]\n\n"
            session.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/generate/batch")
async def generate_batch_endpoint(
    payload: BatchGenerateRequest, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """一键连写 N 章（全自动）：AI 自己决定每章写什么、叫什么标题，写完自动入库。"""
    require_book(db, payload.book_id)
    options = payload.model_dump()

    async def runner(job, report):  # noqa: ANN001, ANN202
        session = SessionLocal()
        try:
            return await generate_batch(
                session, BatchGenerateRequest(**options), report=report
            )
        finally:
            session.close()

    job = jobs.create("auto-write", runner)
    return {
        "job_id": job.id,
        "kind": job.kind,
        "message": f"已开始全自动连写 {payload.count} 章",
    }


# ----------------------------------------------------------------------
# 生成记录
# ----------------------------------------------------------------------
@router.get("/generations", response_model=list[GenerationOut])
def list_generations(
    book_id: int | None = None, limit: int = 30, db: Session = Depends(get_db)
) -> list[Generation]:
    query = db.query(Generation)
    if book_id:
        query = query.filter(Generation.book_id == book_id)
    return query.order_by(Generation.id.desc()).limit(max(1, min(limit, 200))).all()


@router.get("/generations/{generation_id}", response_model=GenerationOut)
def get_generation(generation_id: int, db: Session = Depends(get_db)) -> Generation:
    row = db.get(Generation, generation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="生成记录不存在")
    return row


@router.delete("/generations/{generation_id}", status_code=204, response_model=None)
def delete_generation(generation_id: int, db: Session = Depends(get_db)) -> None:
    row = db.get(Generation, generation_id)
    if row is not None:
        db.delete(row)
        db.commit()


@router.post("/generations/{generation_id}/save", response_model=ChapterOut)
def save_generation(
    generation_id: int, payload: SaveGenerationRequest, db: Session = Depends(get_db)
) -> Chapter:
    """把生成结果保存（或覆盖）为书籍章节。"""
    row = db.get(Generation, generation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="生成记录不存在")
    if not (row.result or "").strip():
        raise HTTPException(status_code=400, detail="该记录没有正文内容")

    overwrite = payload.replace_chapter_id is not None
    number = payload.number or row.chapter_number
    try:
        chapter = save_as_chapter(
            db,
            row.book_id,
            row.result,
            number=number,
            title=payload.title or row.title or f"第{number}章",
            overwrite=overwrite,
        )
    except ValueError as exc:
        raise _fail_400(exc) from exc

    if chapter is None:
        raise HTTPException(status_code=400, detail="保存失败：内容为空")
    row.saved_chapter_id = chapter.id
    db.commit()
    db.refresh(chapter)
    return chapter


# ----------------------------------------------------------------------
# 伏笔 / 逻辑矛盾检查
# ----------------------------------------------------------------------
@router.post("/consistency-check")
async def consistency(payload: ConsistencyRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    require_book(db, payload.book_id)
    if not payload.source_text.strip():
        raise HTTPException(status_code=400, detail="请提供需要检查的正文")
    try:
        return await check_consistency(db, payload.book_id, payload.source_text, payload.model)
    except ValueError as exc:
        raise _fail_400(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/prompts/mapping")
def prompt_mapping() -> dict[str, str]:
    """模式 -> 提示词模板键 的映射，供前端提示。"""
    return PROMPT_BY_MODE


@router.get("/books/{book_id}/stats")
def book_stats(book_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """书籍统计：字数、章节数、已向量化章节数、生成次数。"""
    require_book(db, book_id)
    chapters = db.query(Chapter).filter(Chapter.book_id == book_id).all()
    return {
        "chapters": len(chapters),
        "original_chapters": sum(1 for c in chapters if c.is_original),
        "ai_chapters": sum(1 for c in chapters if not c.is_original),
        "words": sum(c.word_count or count_words(c.content) for c in chapters),
        "vectorized": sum(1 for c in chapters if c.vectorized),
        "generations": db.query(Generation).filter(Generation.book_id == book_id).count(),
    }
