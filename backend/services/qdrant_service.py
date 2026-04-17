import uuid
from datetime import datetime, timezone

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from config import settings
from services.embedding_service import embed_texts, embedding_dim


class QdrantService:
    def __init__(self) -> None:
        self._client: AsyncQdrantClient | None = None
        self._vector_size: int | None = None

    def _c(self) -> AsyncQdrantClient:
        if self._client is None:
            kwargs = {"url": settings.qdrant_url}
            if settings.qdrant_api_key:
                kwargs["api_key"] = settings.qdrant_api_key
            self._client = AsyncQdrantClient(**kwargs)
        return self._client

    async def ensure_collection(self) -> None:
        c = self._c()
        if self._vector_size is None:
            self._vector_size = await embedding_dim()
        cols = await c.get_collections()
        names = [x.name for x in cols.collections]
        if settings.qdrant_collection not in names:
            await c.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=qm.VectorParams(size=self._vector_size, distance=qm.Distance.COSINE),
            )

    async def upsert_documents(
        self,
        texts: list[str],
        payloads: list[dict],
        ids: list[str] | None = None,
    ) -> list[str]:
        if not texts:
            return []
        if ids is not None and len(ids) != len(texts):
            raise ValueError("ids length must match texts length")
        await self.ensure_collection()
        vectors = await embed_texts(texts)
        ids_out = ids or [str(uuid.uuid4()) for _ in texts]
        points = [
            qm.PointStruct(id=ids_out[i], vector=vectors[i], payload={**payloads[i], "text": texts[i]})
            for i in range(len(texts))
        ]
        await self._c().upsert(collection_name=settings.qdrant_collection, points=points)
        return ids_out

    async def search(self, vector: list[float], limit: int) -> list[dict]:
        await self.ensure_collection()
        res = await self._c().search(
            collection_name=settings.qdrant_collection,
            query_vector=vector,
            limit=limit,
            with_payload=True,
        )
        out = []
        for hit in res:
            p = hit.payload or {}
            line = p.get("line_start")
            out.append(
                {
                    "id": str(hit.id),
                    "score": float(hit.score),
                    "text": p.get("text", ""),
                    "timestamp": p.get("timestamp"),
                    "source": p.get("source", "unknown"),
                    "service": p.get("service", ""),
                    "severity": p.get("severity"),
                    "external_id": p.get("external_id"),
                    "file_path": p.get("file_path"),
                    "function_name": p.get("function_name"),
                    "line_start": line,
                    "line": line,
                    "link": p.get("link") or "",
                }
            )
        return out


qdrant_service = QdrantService()
