"""路由：书籍与章节管理、导入。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_book
from ..models import Book, Chapter
from ..schemas import (
    BookCreate,
    BookOut,
    BookUpdate,
    ChapterBrief,
    ChapterCreate,
    ChapterOut,
    ChapterUpdate,
    PasteRequest,
    UploadResult,
)
from ..services import importer
from ..services.indexing import get_store
from ..services.text_utils import RawChapter, count_words, split_chapters

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/books", tags=["books"])


# ======================================================================
# 书籍
# ======================================================================
@router.get("", response_model=list[BookOut])
def list_books(q: str | None = None, db: Session = Depends(get_db)) -> list[Book]:
    query = db.query(Book)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Book.title.like(like), Book.author.like(like)))
    return query.order_by(Book.updated_at.desc()).all()


@router.post("", response_model=BookOut, status_code=status.HTTP_201_CREATED)
def create_book(payload: BookCreate, db: Session = Depends(get_db)) -> Book:
    book = Book(**payload.model_dump())
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


@router.get("/{book_id}", response_model=BookOut)
def get_book(book_id: int, db: Session = Depends(get_db)) -> Book:
    return require_book(db, book_id)


@router.patch("/{book_id}", response_model=BookOut)
def update_book(book_id: int, payload: BookUpdate, db: Session = Depends(get_db)) -> Book:
    book = require_book(db, book_id)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(book, key, value)
    db.commit()
    db.refresh(book)
    return book


@router.delete(
    "/{book_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,  # 204 不能有响应体
)
def delete_book(book_id: int, db: Session = Depends(get_db)) -> None:
    book = require_book(db, book_id)
    try:
        _, store = get_store(db)
        store.delete_book(book_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("删除向量集合失败（忽略）: %s", exc)
    db.delete(book)
    db.commit()


# ======================================================================
# 章节
# ======================================================================
@router.get("/{book_id}/chapters", response_model=list[ChapterBrief])
def list_chapters(book_id: int, db: Session = Depends(get_db)) -> list[Chapter]:
    require_book(db, book_id)
    return (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id)
        .order_by(Chapter.number)
        .all()
    )


@router.post(
    "/{book_id}/chapters", response_model=ChapterOut, status_code=status.HTTP_201_CREATED
)
def create_chapter(
    book_id: int, payload: ChapterCreate, db: Session = Depends(get_db)
) -> Chapter:
    book = require_book(db, book_id)
    if payload.number and payload.number > 0:
        number = payload.number
        exists = (
            db.query(Chapter)
            .filter(Chapter.book_id == book_id, Chapter.number == number)
            .first()
        )
        if exists:
            raise HTTPException(status_code=409, detail=f"第 {number} 章已存在")
    else:
        number = (book.total_chapters or 0) + 1
    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=payload.title or f"第{number}章",
        content=payload.content,
        word_count=count_words(payload.content),
        is_original=payload.is_original,
        origin=payload.origin,
    )
    db.add(chapter)
    db.flush()
    book.total_chapters = db.query(Chapter).filter(Chapter.book_id == book_id).count()
    db.commit()
    db.refresh(chapter)
    return chapter


# ======================================================================
# 导入：TXT / EPUB 上传
# ======================================================================
@router.post("/upload", response_model=UploadResult)
async def upload_book(
    file: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
    db: Session = Depends(get_db),
) -> UploadResult:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传的文件为空")

    filename = (file.filename or "novel.txt").strip()
    lower = filename.lower()

    try:
        if lower.endswith(".epub"):
            parsed = _parse_epub_from_bytes(raw, filename)
            source = "epub"
        else:
            parsed = importer.parse_txt(raw, fallback_title=title or filename.rsplit(".", 1)[0])
            source = "txt"
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"解析文件失败：{exc}") from exc

    if not parsed.chapters:
        raise HTTPException(
            status_code=400,
            detail="未能从文件中识别出章节内容，请确认文件编码或改用“手动粘贴”。",
        )

    book = importer.create_book(
        db,
        title=title or parsed.title or filename,
        author=author or parsed.author,
        intro=parsed.intro,
        source=source,
        chapters=parsed.chapters,
    )
    return UploadResult(
        book=BookOut.model_validate(book),
        chapters_created=book.total_chapters,
        message=f"导入成功，共 {book.total_chapters} 章。建议接下来执行“拆书”。",
    )


def _parse_epub_from_bytes(raw: bytes, filename: str) -> importer.ParsedBook:
    """EPUB 需要落盘后解析（ebooklib 只接受路径）。"""
    import hashlib

    from ..config import env

    digest = hashlib.md5(filename.encode("utf-8")).hexdigest()[:12]
    target = env.upload_path / f"import_{digest}.epub"
    target.write_bytes(raw)
    return importer.parse_epub(target)


@router.post("/paste", response_model=UploadResult)
def paste_book(payload: PasteRequest, db: Session = Depends(get_db)) -> UploadResult:
    if payload.split:
        chapters = split_chapters(payload.content)
    else:
        chapters = [RawChapter(number=1, title=payload.chapter_title or "第1章", content=payload.content)]
    if not chapters:
        raise HTTPException(status_code=400, detail="粘贴内容为空或无法解析")

    book = importer.create_book(
        db,
        title=payload.title or "粘贴的小说",
        author=payload.author,
        source="manual",
        chapters=chapters,
    )
    return UploadResult(
        book=BookOut.model_validate(book),
        chapters_created=book.total_chapters,
        message=f"导入成功，共 {book.total_chapters} 章。",
    )
