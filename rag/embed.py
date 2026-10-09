"""Local embeddings (fastembed / ONNX, CPU). No API key, no per-call cost, and the
model (~130 MB) is cached after the first download."""
from __future__ import annotations

from functools import lru_cache
from typing import List

import numpy as np

from rag.config import EMBED_MODEL, QUERY_PREFIX


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding
    return TextEmbedding(EMBED_MODEL)


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return (v / np.where(n == 0, 1, n)).astype(np.float32)


def passages(texts: List[str], batch_size: int = 64) -> np.ndarray:
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    return _unit(np.array(list(_model().embed(texts, batch_size=batch_size)), dtype=np.float32))


def query(text: str) -> np.ndarray:
    return _unit(np.array(list(_model().embed([QUERY_PREFIX + text])), dtype=np.float32))[0]
