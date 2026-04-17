import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from config import settings
from routes.ingest import IngestItem
from routes.ingest import _stable_uuid
from services.qdrant_service import qdrant_service

router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _adf_to_text(node: Any) -> str:
    """
    Jira Cloud often sends description in Atlassian Document Format (ADF).
    Convert a subset of ADF into plain text for embedding/retrieval.
    """
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
        # ADF text node
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            return node["text"]
        # Common containers
        content = node.get("content")
        if content is not None:
            return _adf_to_text(content)
        # Fallback: concatenate any nested values that look like text.
        parts: list[str] = []
        for v in node.values():
            t = _adf_to_text(v)
            if t.strip():
                parts.append(t)
        return "\n".join(parts).strip()
    return ""


async def _ingest_message(
    text: str,
    source: str,
    service: str,
    *,
    link: str | None = None,
    severity: str | None = None,
    channel: str | None = None,
    external_id: str | None = None,
):
    mid = external_id or str(uuid.uuid4())
    item = IngestItem(
        text=text,
        timestamp=_now(),
        source=source,
        service=service or "unknown",
        severity=severity,
        link=link,
        graph={"message_id": mid, "service_id": service or "unknown"},
    )
    pl = {
        "timestamp": item.timestamp,
        "source": item.source,
        "service": item.service,
        "severity": item.severity,
        "external_id": str(mid),
        "file_path": None,
        "function_name": None,
        "line_start": 1,
        "link": item.link or "",
    }
    await qdrant_service.upsert_documents([item.text], [pl])


@router.post("/webhook/slack")
async def webhook_slack(request: Request, x_slack_signature: str | None = Header(default=None)):
    raw = await request.body()
    body: dict[str, Any] = {}
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        body = {}

    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    if settings.slack_signing_secret and x_slack_signature:
        ts = request.headers.get("x-slack-request-timestamp", "")
        if abs(time.time() - int(ts or "0")) > 60 * 5:
            raise HTTPException(status_code=401, detail="Stale request")
        basestring = f"v0:{ts}:{raw.decode('utf-8')}"
        my_sig = (
            "v0="
            + hmac.new(
                settings.slack_signing_secret.encode(),
                basestring.encode(),
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(my_sig, x_slack_signature):
            raise HTTPException(status_code=401, detail="Invalid signature")

    event = body.get("event") or {}
    text = (
        event.get("text")
        or event.get("message", {}).get("text")
        or body.get("text")
        or ""
    )
    channel = event.get("channel") or event.get("channel_id")
    team = (body.get("team_id") or "slack") + ":" + (channel or "unknown")
    if not text.strip():
        return {"ok": True, "ingested": False, "reason": "empty"}

    await _ingest_message(
        text,
        "slack",
        team,
        link=None,
        channel=str(channel) if channel else None,
        external_id=event.get("client_msg_id") or event.get("event_ts"),
    )
    return {"ok": True, "ingested": True}


@router.post("/webhook/teams")
async def webhook_teams(request: Request):
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        body = {}

    if body.get("type") == "message" and body.get("text") == "verify":
        return body

    text = (
        body.get("text")
        or (body.get("body") or {}).get("text")
        or ""
    )
    from_name = (
        body.get("from", {}).get("name")
        if isinstance(body.get("from"), dict)
        else None
    )
    service = (from_name or "teams") + ":" + str(body.get("channelId") or "channel")
    if not str(text).strip():
        return {"ok": True, "ingested": False, "reason": "empty"}

    await _ingest_message(str(text), "teams", service, link=body.get("link"))
    return {"ok": True, "ingested": True}


@router.post("/webhook/jira")
async def webhook_jira(
    request: Request,
    _x_hub_signature: str | None = Header(default=None),
):
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        body = {}

    issue = body.get("issue") or {}
    fields = issue.get("fields") or {}
    summary = fields.get("summary") or issue.get("key") or "Jira update"
    desc = fields.get("description")
    desc_text = _adf_to_text(desc)
    text = f"{summary}\n{desc_text}".strip() if desc_text.strip() else str(summary)

    key = issue.get("key") or body.get("issue_key") or str(uuid.uuid4())[:8]
    service = fields.get("project", {}).get("key", "jira") if isinstance(fields.get("project"), dict) else "jira"
    base = (settings.jira_base_url or "").rstrip("/") or "https://example.atlassian.net"
    browse_link = f"{base}/browse/{key}"

    item = IngestItem(
        text=text,
        timestamp=_now(),
        source="jira",
        service=str(service),
        severity=fields.get("priority", {}).get("name") if isinstance(fields.get("priority"), dict) else None,
        link=browse_link,
        graph={"ticket_id": key, "service_id": str(service)},
    )
    pl = {
        "timestamp": item.timestamp,
        "source": item.source,
        "service": item.service,
        "severity": item.severity,
        "file_path": None,
        "function_name": None,
        "line_start": 1,
        "link": item.link or "",
    }
    doc_id = _stable_uuid("jira", str(key))
    await qdrant_service.upsert_documents([item.text], [pl], ids=[doc_id])
    return {"ok": True, "ingested": True}
