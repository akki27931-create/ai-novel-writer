"""文本向量化服务。

优先使用本地中文模型 BAAI/bge-small-zh-v1.5（需安装 requirements-embeddings.txt）。
未安装 sentence-transformers 时，自动降级为内置的字符 n-gram 哈希向量，
保证 RAG 检索始终可用（语义能力弱一些，但仍是有效的相似度检索）。
"""

from __future__ import annotations

import logging
import os
import re
import threading

import numpy as np

logger = logging.getLogger(__name__)

HASH_DIM = 512
# bge 系列做检索时，query 需要加指令前缀，效果更好
BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
# 国内网络通常访问不了 huggingface.co，自动回退到这个镜像下载模型
HF_MIRROR = "https://hf-mirror.com"


def _resolve_hf_endpoint() -> str | None:
    """选择 HuggingFace 下载源。

    优先用用户显式设置的 HF_ENDPOINT；否则探测 huggingface.co 是否可达，
    不可达就自动切到国内镜像，避免模型下载卡死。
    """
    if os.environ.get("HF_ENDPOINT"):
        return os.environ["HF_ENDPOINT"]
    try:
        import httpx

        with httpx.Client(timeout=3.5) as client:
            client.head("https://huggingface.co", follow_redirects=True)
        return None  # 官方源可达
    except Exception:  # noqa: BLE001
        logger.info("huggingface.co 不可达，自动改用镜像 %s 下载向量模型", HF_MIRROR)
        return HF_MIRROR


class EmbeddingService:
    """单例式向量化服务，支持模型 / 哈希两种后端。"""

    _lock = threading.Lock()
    _instances: dict[str, "EmbeddingService"] = {}

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name or "BAAI/bge-small-zh-v1.5"
        self._model = None
        self._load_attempted = False
        self.mode = "hash"

    # ------------------------------------------------------------------
    @classmethod
    def get(cls, model_name: str) -> "EmbeddingService":
        key = model_name or "default"
        with cls._lock:
            if key not in cls._instances:
                cls._instances[key] = cls(model_name)
            return cls._instances[key]

    # ------------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._load_attempted:
            return
        self._load_attempted = True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            endpoint = _resolve_hf_endpoint()
            if endpoint:
                os.environ["HF_ENDPOINT"] = endpoint
            # 下载慢时不要过早超时放弃
            os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")

            logger.info("正在加载本地向量模型 %s ...", self.model_name)
            self._model = SentenceTransformer(self.model_name)
            self.mode = "sentence-transformers"
            logger.info("向量模型加载完成，维度=%s", self.dimension)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "未能加载 sentence-transformers 模型（%s），降级为字符 n-gram 哈希向量。"
                "如需更好的检索效果，请执行: pip install -r requirements-embeddings.txt",
                exc,
            )
            self._model = None
            self.mode = "hash"

    @property
    def dimension(self) -> int:
        self._ensure_loaded()
        if self._model is not None:
            try:
                return int(self._model.get_sentence_embedding_dimension())
            except Exception:  # noqa: BLE001
                return HASH_DIM
        return HASH_DIM

    @property
    def signature(self) -> str:
        """用于判断向量库是否需要重建。"""
        self._ensure_loaded()
        return f"{self.mode}:{self.model_name}:{self.dimension}"

    # ------------------------------------------------------------------
    def encode(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        self._ensure_loaded()
        if self._model is not None:
            payload = [f"{BGE_QUERY_PREFIX}{t}" if is_query else t for t in texts]
            vectors = self._model.encode(
                payload,
                normalize_embeddings=True,
                batch_size=32,
                show_progress_bar=False,
            )
            return [v.tolist() for v in np.asarray(vectors, dtype=np.float32)]
        return [self._hash_embed(t, is_query=is_query) for t in texts]

    def encode_one(self, text: str, *, is_query: bool = False) -> list[float]:
        result = self.encode([text or ""], is_query=is_query)
        return result[0] if result else [0.0] * HASH_DIM

    # ------------------------------------------------------------------
    def _hash_embed(self, text: str, *, is_query: bool = False) -> list[float]:
        """字符 uni/bi/tri-gram 的带符号哈希向量（numpy 向量化，速度快）。"""
        cleaned = re.sub(r"\s+", "", text or "")
        vector = np.zeros(HASH_DIM, dtype=np.float32)
        if not cleaned:
            return vector.tolist()

        codes = np.fromiter((ord(ch) for ch in cleaned), dtype=np.int64, count=len(cleaned))
        # 短文本权重更高；用 uint64 乘法做 64 位溢出截断，得到稳定的哈希
        multiplier = np.uint64(2654435761)
        for n, weight in ((1, 1.0), (2, 1.6), (3, 1.0)):
            if len(codes) < n:
                continue
            if n == 1:
                grams = codes
            elif n == 2:
                grams = codes[:-1] * 131 + codes[1:]
            else:
                grams = codes[:-2] * 17161 + codes[1:-1] * 131 + codes[2:]
            hashed = grams.astype(np.uint64) * multiplier
            hashed = hashed ^ (hashed >> np.uint64(33))
            indices = (hashed % np.uint64(HASH_DIM)).astype(np.int64)
            signs = np.where(
                (hashed & np.uint64(1)) == np.uint64(1), -1.0, 1.0
            ).astype(np.float32) * weight
            np.add.at(vector, indices, signs)

        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector /= norm
        return vector.tolist()


def cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(np.dot(va, vb) / denom) if denom else 0.0
