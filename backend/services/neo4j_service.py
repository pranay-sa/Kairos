"""
Deprecated: Neo4j has been removed from KAIROS.

This module is kept as a stub to avoid import-time failures in older branches,
but it intentionally provides no graph functionality.
"""


class Neo4jService:
    def __init__(self) -> None:
        raise RuntimeError("Neo4j support was removed; use Qdrant vector retrieval only.")


neo4j_service = None
