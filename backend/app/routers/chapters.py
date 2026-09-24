"""路由：单章读取 / 修改 / 删除。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Book, Chapter
from ..schemas import ChapterOut, ChapterUpdate
from ..services.indexing import get_store, index_chapters
from ..services.text_utils import count_words

router = APIRouter(prefix="/api/chapters", tags=["chapters"])


def _get_chapter(db: Session, chapter_id: int) -> Chapter:
    chapter = db.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail=f"章节 {chapter_id} 不存在")
    return chapter


@router.get("/{chapter_id}", response_model=ChapterOut)
def get_chapter(chapter_id: int, db: Session = Depends(get_db)) -> Chapter:
    return _get_chapter(db, chapter_id)


@router.patch("/{chapter_id}", response_model=ChapterOut)
def update_chapter(
    chapter_id: int, payload: ChapterUpdate, db: Session = Depends(get_db)
) -> Chapter:
    chapter = _get_chapter(db, chapter_id)
    data = payload.model_dump(exclude_none=True)
    if "title" in data:
        chapter.title = data["title"]
    if "summary" in data:
        chapter.summary = data["summary"]
    content_changed = False
    if "content" in data:
        chapter.content = data["content"]
        chapter.word_count = count_words(data["content"])
        chapter.vectorized = False  # 正文变了，需要重新向量化
        content_changed = True
    db.commit()
    db.refresh(chapter)

    if content_changed:
        # 正文改了但旧向量还在，检索会拿到过期文本，这里立即重建本章索引
        try:
            index_chapters(db, chapter.book_id, [chapter], force=True)
        except Exception:  # noqa: BLE001
            pass
    return chapter


@router.delete(
    "/{chapter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,  # 204 不能有响应体
)
def delete_chapter(chapter_id: int, db: Session = Depends(get_db)) -> None:
    chapter = _get_chapter(db, chapter_id)
    book_id, number = chapter.book_id, chapter.number
    _reindex(book_id, number)
    db.delete(chapter)
    book = db.get(Book, book_id)
    if book is not None:
        book.total_chapters = max(0, (book.total_chapters or 1) - 1)
    db.commit()


def _reindex(book_id: int, chapter_number: int) -> None:
    """从向量库中移除被删章节（失败不影响删除操作）。"""
    try:
        from ..database import SessionLocal

        with SessionLocal() as session:
            _, store = get_store(session)
            store.remove_chapter(book_id, chapter_number)
    except Exception:  # noqa: BLE001
        pass
