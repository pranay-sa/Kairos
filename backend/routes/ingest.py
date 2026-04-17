import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from pydantic import BaseModel, Field

from services.qdrant_service import qdrant_service
from services.jira_service import jira_service

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


class IngestRequest(BaseModel):
    items: list[IngestItem]


class IngestResponse(BaseModel):
    inserted: int
    ids: list[str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_uuid(*parts: str) -> str:
    """
    Qdrant point IDs must be UUIDs or unsigned ints.
    Use uuid5 so IDs are stable across re-ingests (dedupe / upsert behavior).
    """
    key = ":".join([p.strip() for p in parts if (p or "").strip()])
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


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


def _adf_to_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, (int, float, bool)):
        return str(node)
    if isinstance(node, list):
        parts = [_adf_to_text(x) for x in node]
        return "\n".join([p for p in parts if p.strip()]).strip()
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            return node["text"]
        content = node.get("content")
        if content is not None:
            return _adf_to_text(content)
        parts: list[str] = []
        for v in node.values():
            t = _adf_to_text(v)
            if t.strip():
                parts.append(t)
        return "\n".join(parts).strip()
    return ""


@router.post("/ingest/jira/backfill")
async def backfill_jira(
    jql: str = "order by updated desc",
    max_issues: int = 500,
):
    """
    Backfill older Jira issues into the vector store.
    Requires JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN to be set.
    """
    if max_issues <= 0:
        raise HTTPException(status_code=400, detail="max_issues must be > 0")

    page_size = 50
    ingested = 0
    ids: list[str] = []
    next_page_token: str | None = None
    base = ""
    try:
        base = (jira_service._base() or "").rstrip("/")  # type: ignore[attr-defined]
    except Exception:
        base = ""

    while ingested < max_issues:
        try:
            data = await jira_service.search_issues(
                jql=jql,
                max_results=min(page_size, max_issues - ingested),
                next_page_token=next_page_token,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        issues = data.get("issues") or []
        if not issues:
            break

        texts: list[str] = []
        payloads: list[dict] = []
        batch_ids: list[str] = []

        for it in issues:
            key = it.get("key")
            fields = it.get("fields") or {}
            summary = fields.get("summary") or key or "Jira issue"
            desc = _adf_to_text(fields.get("description"))
            text = f"{summary}\n{desc}".strip() if desc.strip() else str(summary)
            proj = fields.get("project", {}).get("key") if isinstance(fields.get("project"), dict) else "jira"
            prio = fields.get("priority", {}).get("name") if isinstance(fields.get("priority"), dict) else None
            updated = fields.get("updated") or fields.get("created")
            link = f"{base}/browse/{key}" if base and key else ""

            if not key:
                continue

            texts.append(text)
            payloads.append(
                {
                    "timestamp": updated,
                    "source": "jira",
                    "service": str(proj or "jira"),
                    "severity": prio,
                    "external_id": str(key),
                    "file_path": None,
                    "function_name": None,
                    "line_start": 1,
                    "link": link,
                }
            )
            batch_ids.append(_stable_uuid("jira", str(key)))

        if texts:
            out_ids = await qdrant_service.upsert_documents(texts, payloads, ids=batch_ids)
            ingested += len(out_ids)
            ids.extend(out_ids)

        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    return {"ingested": ingested, "ids": ids[:50], "jql": jql}


async def run_jira_backfill(*, jql: str, max_issues: int) -> dict:
    """
    Internal helper used by startup hooks.
    Mirrors the API behavior but returns a dict instead of raising HTTP errors.
    """
    if max_issues <= 0:
        return {"ingested": 0, "ids": [], "jql": jql, "error": "max_issues must be > 0"}
    try:
        return await backfill_jira(jql=jql, max_issues=max_issues)  # type: ignore[misc]
    except HTTPException as exc:
        return {"ingested": 0, "ids": [], "jql": jql, "error": str(exc.detail)}
