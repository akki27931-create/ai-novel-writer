"""向量索引构建：把章节正文分块、向量化、写入向量库。"""

from __future__ import annotations

import logging
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from ..config import env
from ..models import Chapter
from ..settings_store import get_settings
from .embeddings import EmbeddingService
from .text_utils import chunk_text
from .vector_store import VectorStore

logger = logging.getLogger(__name__)

_cache: dict[str, object] = {"signature": None, "store": None}


def get_embedder(db: Session) -> EmbeddingService:
    settings = get_settings(db, include_secrets=True)
    return EmbeddingService.get(str(settings.get("embedding_model") or "BAAI/bge-small-zh-v1.5"))


def get_store(db: Session) -> tuple[EmbeddingService, VectorStore]:
    """返回 (向量化服务, 向量库)。向量模型变化时自动重建向量库。"""
    embedder = get_embedder(db)
    signature = embedder.signature
    if _cache["signature"] != signature or _cache["store"] is None:
        logger.info("初始化向量库，嵌入签名=%s", signature)
        _cache["store"] = VectorStore(env.chroma_path, signature)
        _cache["signature"] = signature
    return embedder, _cache["store"]  # type: ignore[return-value]


def reset_store_cache() -> None:
    """切换嵌入模型后调用，强制下次重新创建向量库。"""
    _cache["signature"] = None
    _cache["store"] = None


ProgressCb = Callable[[int, int], None]


def index_chapters(
    db: Session,
    book_id: int,
    chapters: Iterable[Chapter],
    *,
    force: bool = False,
    on_progress: ProgressCb | None = None,
) -> dict[str, int]:
    """为章节建立向量索引。返回统计信息。"""
    chapter_list = list(chapters)
    embedder, store = get_store(db)

    # 只有「向量模型变化导致旧索引被清空」时才为 True。
    # 索引为空但章节标记已向量化的情况（例如手动删了向量文件）由下面的
    # `chapter.number not in indexed` 兜住，同样会触发重跑。
    created = store.ensure_book(book_id)
    if created:
        logger.info("书籍 %s 的向量索引因向量模型变化已重建，将全量向量化", book_id)

    indexed = store.indexed_chapters(book_id)
    total = len(chapter_list)
    done = skipped = failed = 0

    for chapter in chapter_list:
        needs = force or created or not chapter.vectorized or chapter.number not in indexed
        if not needs:
            skipped += 1
            done += 1
            if on_progress:
                on_progress(done, total)
            continue

        chunks = chunk_text(chapter.content)
        if not chunks:
            skipped += 1
            done += 1
            continue
        try:
            vectors = embedder.encode(chunks)
            store.index_chapter(book_id, chapter.number, chapter.title, chunks, vectors)
            chapter.vectorized = True
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            failed += 1
            logger.warning("章节 %s 向量化失败: %s", chapter.number, exc)

        done += 1
        if on_progress:
            on_progress(done, total)

    return {"total": total, "indexed": done - skipped, "skipped": skipped, "failed": failed}
