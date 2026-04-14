from __future__ import annotations

import anyio
from fastembed import TextEmbedding

from config import settings

_model: TextEmbedding | None = None
_dim: int | None = None


def _get_model() -> TextEmbedding:
    global _model
    if _model is None:
        _model = TextEmbedding(model_name=settings.embedding_model)
    return _model


def _embed_sync(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    m = _get_model()
    return [list(v) for v in m.embed(texts)]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    return await anyio.to_thread.run_sync(_embed_sync, texts)


async def embed_query(text: str) -> list[float]:
    vecs = await embed_texts([text])
    return vecs[0] if vecs else []


async def embedding_dim() -> int:
    global _dim
    if _dim is not None:
        return _dim
    vec = await embed_query("dimension probe")
    _dim = len(vec)
    return _dim
