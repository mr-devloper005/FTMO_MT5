"""Local embeddings (fastembed / ONNX, CPU). No API key, no per-call cost, and the
model (~130 MB) is cached after the first download.

The model lives in a persistent folder (``RAG_MODEL_CACHE`` or ``rag/state/models``), not
fastembed's default temp folder: Windows' Storage Sense / temp cleanup would otherwise delete
it and the 24/7 PC would need Hugging Face access again. The folder can be copied to a machine
without internet access."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List

import numpy as np

from rag.config import EMBED_MODEL, QUERY_PREFIX, STATE


def model_cache_dir() -> Path:
    return Path(os.environ.get("RAG_MODEL_CACHE") or (STATE / "models"))


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding
    cache = model_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(EMBED_MODEL, cache_dir=str(cache))


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return (v / np.where(n == 0, 1, n)).astype(np.float32)


def passages(texts: List[str], batch_size: int = 64) -> np.ndarray:
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    return _unit(np.array(list(_model().embed(texts, batch_size=batch_size)), dtype=np.float32))


def query(text: str) -> np.ndarray:
    return _unit(np.array(list(_model().embed([QUERY_PREFIX + text])), dtype=np.float32))[0]
