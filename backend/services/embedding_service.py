from __future__ import annotations

import anyio
import shutil
from fastembed import TextEmbedding
from pathlib import Path

from config import settings

_model: TextEmbedding | None = None
_dim: int | None = None


def _cache_dir_path() -> Path | None:
    cache_dir = (settings.fastembed_cache_dir or "").strip()
    if not cache_dir:
        return None
    p = Path(cache_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _maybe_clear_corrupt_cache(exc: Exception) -> bool:
    """
    fastembed downloads ONNX artifacts into a local cache. If a download is interrupted,
    we can end up with a snapshot missing `model.onnx`, which then hard-fails at runtime.
    When we detect this class of error, delete the model cache folder and retry once.
    """
    msg = str(exc)
    if "model.onnx" not in msg and "NO_SUCHFILE" not in msg:
        return False
    cache_dir = _cache_dir_path()
    if cache_dir is None:
        return False

    # FastEmbed uses a HuggingFace-like cache layout under cache_dir.
    # The exact folder name differs from settings.embedding_model, so we remove the whole cache
    # directory to guarantee a clean re-download (it's safe: it only contains model artifacts).
    try:
        shutil.rmtree(cache_dir, ignore_errors=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


def _get_model() -> TextEmbedding:
    global _model
    if _model is None:
        cache_dir = _cache_dir_path()
        try:
            if cache_dir is not None:
                _model = TextEmbedding(model_name=settings.embedding_model, cache_dir=str(cache_dir))
            else:
                _model = TextEmbedding(model_name=settings.embedding_model)
        except Exception as exc:
            if _maybe_clear_corrupt_cache(exc):
                # retry once with fresh cache
                if cache_dir is not None:
                    _model = TextEmbedding(model_name=settings.embedding_model, cache_dir=str(cache_dir))
                else:
                    _model = TextEmbedding(model_name=settings.embedding_model)
            else:
                raise
    return _model


def _embed_sync(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    try:
        m = _get_model()
        return [list(v) for v in m.embed(texts)]
    except Exception as exc:
        # If the model cache got corrupted after initialization, clear and retry once.
        global _model
        if _maybe_clear_corrupt_cache(exc):
            _model = None
            m = _get_model()
            return [list(v) for v in m.embed(texts)]
        raise


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
