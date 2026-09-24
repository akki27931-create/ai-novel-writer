"""FastAPI 公共依赖。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .database import get_db  # noqa: F401  (对外统一出口)

__all__ = ["get_db", "require_book"]


def require_book(db: Session, book_id: int):
    """按 ID 取书，不存在时抛出 404。"""
    from fastapi import HTTPException, status

    from .models import Book

    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"书籍 {book_id} 不存在")
    return book
