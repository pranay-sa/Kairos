import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.neo4j_service import neo4j_service
from services.qdrant_service import qdrant_service

router = APIRouter()


class IngestItem(BaseModel):
    text: str = Field(..., min_length=1)
    timestamp: str | None = None
    source: str = Field(..., description="slack|teams|jira|log|codebase")
    service: str = Field(default="unknown")
    severity: str | None = None
    file_path: str | None = None
    function_name: str | None = None
    line_start: int | None = None
    link: str | None = None
    graph: dict[str, Any] | None = None


class IngestRequest(BaseModel):
    items: list[IngestItem]


class IngestResponse(BaseModel):
    inserted: int
    ids: list[str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest):
    texts: list[str] = []
    payloads: list[dict] = []
    ids_out: list[str] = []

    for it in body.items:
        ts = it.timestamp or _now_iso()
        pl = {
            "timestamp": ts,
            "source": it.source,
            "service": it.service,
            "severity": it.severity,
            "file_path": it.file_path,
            "function_name": it.function_name,
            "line_start": it.line_start,
            "link": it.link or "",
        }
        texts.append(it.text)
        payloads.append(pl)

        g = it.graph or {}
        if g.get("service_id"):
            neo4j_service.upsert_service(str(g["service_id"]), g.get("service_name"))
        if g.get("incident_id"):
            neo4j_service.upsert_incident(str(g["incident_id"]), g.get("incident_title"))
        if g.get("ticket_id"):
            neo4j_service.upsert_ticket(str(g["ticket_id"]), g.get("ticket_key"))
        if g.get("message_id"):
            neo4j_service.upsert_message(str(g["message_id"]), g.get("channel"))
        if g.get("depends_from") and g.get("depends_to"):
            neo4j_service.link_service_depends(str(g["depends_from"]), str(g["depends_to"]))
        if g.get("incident_id") and g.get("caused_by_service"):
            neo4j_service.link_caused_by(str(g["incident_id"]), str(g["caused_by_service"]))
        if g.get("incident_a") and g.get("incident_b"):
            neo4j_service.link_related_incidents(str(g["incident_a"]), str(g["incident_b"]))
        if g.get("incident_id") and g.get("ticket_id"):
            neo4j_service.link_reported_in(str(g["incident_id"]), str(g["ticket_id"]))
        if g.get("message_id") and g.get("incident_id"):
            neo4j_service.link_message_to_incident(str(g["message_id"]), str(g["incident_id"]))

    vids = await qdrant_service.upsert_documents(texts, payloads)
    ids_out.extend(vids)

    return IngestResponse(inserted=len(ids_out), ids=ids_out)


@router.post("/ingest/codebase")
async def ingest_codebase(
    root: str = ".",
    include_globs: str = "*.py,*.ts,*.tsx,*.js,*.go,*.yaml",
    service: str = "codebase",
    max_files: int = 80,
):
    from ingestion.codebase import ingest_codebase_folder

    n = await ingest_codebase_folder(
        root=root,
        include_globs=include_globs.split(","),
        service=service,
        max_files=max_files,
    )
    return {"files_indexed": n}
