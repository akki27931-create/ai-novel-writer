"""本地向量库封装。

主实现：ChromaDB（嵌入式持久化，零运维）。
兜底实现：纯 JSON + numpy 文件存储，保证在 ChromaDB 安装失败时功能不中断。

两套实现暴露完全相同的接口，上层（RAG / 拆书）无需感知差异。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def _collection_name(book_id: int) -> str:
    return f"book_{book_id}"


def _chroma_settings():
    """关闭 ChromaDB 的匿名遥测：这是一个纯本地应用，不应有任何外发请求。"""
    try:
        from chromadb.config import Settings as ChromaSettings

        return ChromaSettings(anonymized_telemetry=False, allow_reset=False)
    except Exception:  # noqa: BLE001
        return None


class VectorStore:
    """向量库门面。"""

    def __init__(self, path: Path, signature: str) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.signature = signature
        self._lock = threading.RLock()
        self._backend = self._create_backend()

    # ------------------------------------------------------------------
    def _create_backend(self) -> "_BaseBackend":
        try:
            return _ChromaBackend(self.path, self.signature)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ChromaDB 不可用（%s），切换到文件向量库兜底实现。", exc)
            return _JsonBackend(self.path, self.signature)

    @property
    def backend_name(self) -> str:
        return self._backend.name

    # ------------------------------------------------------------------
    def ensure_book(self, book_id: int) -> bool:
        """确保本书的向量集合存在。返回 True 表示集合是新建/重建的（需要重新向量化）。"""
        with self._lock:
            return self._backend.ensure_book(book_id)

    def index_chapter(
        self,
        book_id: int,
        chapter_number: int,
        chapter_title: str,
        chunks: list[str],
        embeddings: list[list[float]],
    ) -> int:
        """写入一章的所有分块，返回写入块数。"""
        if not chunks:
            return 0
        with self._lock:
            self.remove_chapter(book_id, chapter_number)
            self._backend.add(book_id, chapter_number, chapter_title, chunks, embeddings)
        return len(chunks)

    def remove_chapter(self, book_id: int, chapter_number: int) -> None:
        with self._lock:
            self._backend.delete_chapter(book_id, chapter_number)

    def delete_book(self, book_id: int) -> None:
        with self._lock:
            self._backend.drop_book(book_id)

    def query(
        self,
        book_id: int,
        embedding: list[float],
        *,
        top_k: int = 8,
        max_chapter_number: int | None = None,
    ) -> list[dict[str, Any]]:
        """检索相关片段。max_chapter_number 用于严格排除“未来章节”，避免剧情穿越。"""
        with self._lock:
            return self._backend.query(
                book_id, embedding, top_k=top_k, max_chapter_number=max_chapter_number
            )

    def indexed_chapters(self, book_id: int) -> set[int]:
        with self._lock:
            return self._backend.indexed_chapters(book_id)

    def count(self, book_id: int) -> int:
        with self._lock:
            return self._backend.count(book_id)


# ======================================================================
# ChromaDB 实现
# ======================================================================
class _ChromaBackend:
    name = "chromadb"

    def __init__(self, path: Path, signature: str) -> None:
        import chromadb  # 延迟导入，导入失败时由上层兜底

        self._signature = signature
        self._meta_file = path / "_meta.json"
        client_kwargs: dict[str, Any] = {"path": str(path / "chroma")}
        settings_obj = _chroma_settings()
        if settings_obj is not None:
            client_kwargs["settings"] = settings_obj
        self._client = chromadb.PersistentClient(**client_kwargs)
        self._meta = _load_json(self._meta_file, {})

    def _collection(self, book_id: int, *, create: bool = True):
        name = _collection_name(book_id)
        if not create:
            try:
                return self._client.get_collection(name=name)
            except Exception:  # noqa: BLE001
                return None

        # 优先用余弦距离；不同 ChromaDB 版本参数位置略有差异，逐个兼容
        for kwargs in (
            {"metadata": {"hnsw:space": "cosine"}},
            {"configuration": {"hnsw": {"space": "cosine"}}},
            {},
        ):
            try:
                return self._client.get_or_create_collection(name=name, **kwargs)
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError(f"无法创建 ChromaDB 集合 {name}")

    def ensure_book(self, book_id: int) -> bool:
        key = str(book_id)
        if self._meta.get(key) and self._meta[key] != self._signature:
            # 向量模型变了，旧向量维度/语义都不再可用，重建
            try:
                self._client.delete_collection(_collection_name(book_id))
            except Exception:  # noqa: BLE001
                pass
            self._meta.pop(key, None)
            self._save_meta()
            self._collection(book_id)
            return True
        if key not in self._meta:
            self._meta[key] = self._signature
            self._save_meta()
            self._collection(book_id)
            return True
        return False

    def _save_meta(self) -> None:
        self._meta_file.write_text(json.dumps(self._meta, ensure_ascii=False, indent=2), "utf-8")

    def add(
        self,
        book_id: int,
        chapter_number: int,
        chapter_title: str,
        chunks: list[str],
        embeddings: list[list[float]],
    ) -> None:
        collection = self._collection(book_id)
        ids = [f"c{chapter_number}-{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "chapter_number": int(chapter_number),
                "chapter_title": chapter_title or "",
                "chunk_index": i,
            }
            for i in range(len(chunks))
        ]
        collection.add(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)

    def delete_chapter(self, book_id: int, chapter_number: int) -> None:
        collection = self._collection(book_id, create=False)
        if collection is None:
            return
        try:
            collection.delete(where={"chapter_number": int(chapter_number)})
        except Exception as exc:  # noqa: BLE001
            logger.debug("删除章节向量失败 book=%s chapter=%s: %s", book_id, chapter_number, exc)

    def drop_book(self, book_id: int) -> None:
        try:
            self._client.delete_collection(_collection_name(book_id))
        except Exception:  # noqa: BLE001
            pass
        self._meta.pop(str(book_id), None)
        self._save_meta()

    def query(
        self,
        book_id: int,
        embedding: list[float],
        *,
        top_k: int,
        max_chapter_number: int | None,
    ) -> list[dict[str, Any]]:
        collection = self._collection(book_id, create=False)
        if collection is None or collection.count() == 0:
            return []
        where = None
        if max_chapter_number is not None:
            where = {"chapter_number": {"$lt": int(max_chapter_number)}}
        try:
            result = collection.query(
                query_embeddings=[embedding],
                n_results=max(1, top_k),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("向量检索失败: %s", exc)
            return []

        items: list[dict[str, Any]] = []
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        for text, meta, distance in zip(documents, metadatas, distances):
            meta = meta or {}
            items.append(
                {
                    "text": text,
                    "chapter_number": int(meta.get("chapter_number", 0)),
                    "chapter_title": meta.get("chapter_title", ""),
                    "score": round(1.0 - float(distance), 4),
                }
            )
        return items

    def indexed_chapters(self, book_id: int) -> set[int]:
        collection = self._collection(book_id, create=False)
        if collection is None or collection.count() == 0:
            return set()
        data = collection.get(include=["metadatas"])
        return {int((m or {}).get("chapter_number", 0)) for m in data.get("metadatas") or []}

    def count(self, book_id: int) -> int:
        collection = self._collection(book_id, create=False)
        return int(collection.count()) if collection is not None else 0


# ======================================================================
# 文件向量库兜底实现（JSON + numpy）
# ======================================================================
class _JsonBackend:
    name = "json-fallback"

    def __init__(self, path: Path, signature: str) -> None:
        self._dir = path / "vectors"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._meta_file = path / "_meta.json"
        self._signature = signature
        self._meta = _load_json(self._meta_file, {})
        self._cache: dict[int, list[dict[str, Any]]] = {}

    def _file(self, book_id: int) -> Path:
        return self._dir / f"{_collection_name(book_id)}.json"

    def _load(self, book_id: int) -> list[dict[str, Any]]:
        if book_id not in self._cache:
            self._cache[book_id] = _load_json(self._file(book_id), [])
        return self._cache[book_id]

    def _flush(self, book_id: int) -> None:
        self._file(book_id).write_text(
            json.dumps(self._cache.get(book_id, []), ensure_ascii=False), "utf-8"
        )

    def _save_meta(self) -> None:
        self._meta_file.write_text(json.dumps(self._meta, ensure_ascii=False, indent=2), "utf-8")

    def ensure_book(self, book_id: int) -> bool:
        key = str(book_id)
        if self._meta.get(key) and self._meta[key] != self._signature:
            self._cache[book_id] = []
            self._flush(book_id)
            self._meta[key] = self._signature
            self._save_meta()
            return True
        if key not in self._meta:
            self._meta[key] = self._signature
            self._save_meta()
            self._load(book_id)
            return True
        return False

    def add(
        self,
        book_id: int,
        chapter_number: int,
        chapter_title: str,
        chunks: list[str],
        embeddings: list[list[float]],
    ) -> None:
        records = self._load(book_id)
        for i, (text, vector) in enumerate(zip(chunks, embeddings)):
            records.append(
                {
                    "chapter_number": int(chapter_number),
                    "chapter_title": chapter_title or "",
                    "chunk_index": i,
                    "text": text,
                    "vector": vector,
                }
            )
        self._flush(book_id)

    def delete_chapter(self, book_id: int, chapter_number: int) -> None:
        records = self._load(book_id)
        self._cache[book_id] = [r for r in records if r["chapter_number"] != int(chapter_number)]
        self._flush(book_id)

    def drop_book(self, book_id: int) -> None:
        self._cache.pop(book_id, None)
        self._file(book_id).unlink(missing_ok=True)
        self._meta.pop(str(book_id), None)
        self._save_meta()

    def query(
        self,
        book_id: int,
        embedding: list[float],
        *,
        top_k: int,
        max_chapter_number: int | None,
    ) -> list[dict[str, Any]]:
        records = self._load(book_id)
        if not records:
            return []
        selected = [
            r
            for r in records
            if max_chapter_number is None or r["chapter_number"] < max_chapter_number
        ]
        if not selected:
            return []
        matrix = np.asarray([r["vector"] for r in selected], dtype=np.float32)
        query = np.asarray(embedding, dtype=np.float32)
        if matrix.shape[1] != query.shape[0]:
            return []
        norms = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(query) or 1.0)
        norms[norms == 0] = 1.0
        scores = (matrix @ query) / norms
        order = np.argsort(-scores)[: max(1, top_k)]
        return [
            {
                "text": selected[i]["text"],
                "chapter_number": selected[i]["chapter_number"],
                "chapter_title": selected[i]["chapter_title"],
                "score": round(float(scores[i]), 4),
            }
            for i in order
        ]

    def indexed_chapters(self, book_id: int) -> set[int]:
        return {int(r["chapter_number"]) for r in self._load(book_id)}

    def count(self, book_id: int) -> int:
        return len(self._load(book_id))


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("读取向量库元数据失败: %s", path)
    return default
