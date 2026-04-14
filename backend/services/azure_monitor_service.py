import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from config import settings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str) -> datetime:
    # Azure returns ISO timestamps; allow both Z and offset forms.
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


@dataclass(frozen=True)
class AzureLogRow:
    time_generated: str
    text: str
    payload: dict[str, Any]
    stable_id: str


class AzureMonitorService:
    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expiry: datetime | None = None

    async def _get_token(self) -> str:
        if (
            self._token
            and self._token_expiry
            and self._token_expiry > (_utc_now() + timedelta(minutes=2))
        ):
            return self._token

        if not (settings.azure_tenant_id and settings.azure_client_id and settings.azure_client_secret):
            raise RuntimeError("Azure credentials missing (AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET)")

        url = f"https://login.microsoftonline.com/{settings.azure_tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": settings.azure_client_id,
            "client_secret": settings.azure_client_secret,
            "grant_type": "client_credentials",
            "scope": "https://api.loganalytics.io/.default",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, data=data)
            r.raise_for_status()
            body = r.json()

        token = str(body.get("access_token", ""))
        if not token:
            raise RuntimeError("Azure token response missing access_token")

        expires_in = int(body.get("expires_in", 3600))
        self._token = token
        self._token_expiry = _utc_now() + timedelta(seconds=expires_in)
        return token

    def load_checkpoint(self) -> datetime | None:
        path = settings.azure_state_path
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            s = obj.get("last_time_generated")
            if not s:
                return None
            return _parse_iso(str(s))
        except FileNotFoundError:
            return None
        except Exception:
            # If the checkpoint file is corrupted, start fresh (don’t crash the API).
            return None

    def save_checkpoint(self, last_time_generated: datetime) -> None:
        path = settings.azure_state_path
        if not path:
            return
        _ensure_parent_dir(path)
        obj = {"last_time_generated": last_time_generated.astimezone(timezone.utc).isoformat()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)

    async def query_logs(self, since: datetime | None, until: datetime) -> list[AzureLogRow]:
        if not settings.azure_log_analytics_workspace_id:
            raise RuntimeError("AZURE_LOG_ANALYTICS_WORKSPACE_ID missing")

        token = await self._get_token()
        endpoint = settings.azure_log_analytics_endpoint.rstrip("/")
        url = f"{endpoint}/v1/workspaces/{settings.azure_log_analytics_workspace_id}/query"

        params: dict[str, Any] = {}
        if since is not None:
            # Log Analytics expects an ISO8601 timespan or a duration; use start/end for precise windows.
            params["timespan"] = f"{since.astimezone(timezone.utc).isoformat()}/{until.astimezone(timezone.utc).isoformat()}"

        payload = {"query": settings.azure_log_analytics_query}
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, json=payload, params=params, headers=headers)
            r.raise_for_status()
            body = r.json()

        tables = body.get("tables") or []
        if not tables:
            return []
        table = tables[0]
        cols = [c.get("name") for c in (table.get("columns") or [])]
        rows = table.get("rows") or []

        out: list[AzureLogRow] = []
        for row in rows:
            row_obj: dict[str, Any] = {}
            for i, name in enumerate(cols):
                if not name:
                    continue
                if i < len(row):
                    row_obj[str(name)] = row[i]

            # Try to normalize the most common fields.
            tg = (
                row_obj.get("TimeGenerated")
                or row_obj.get("timeGenerated")
                or row_obj.get("Timestamp")
                or row_obj.get("timestamp")
            )
            time_generated = str(tg) if tg is not None else until.isoformat()

            msg = (
                row_obj.get("Message")
                or row_obj.get("message")
                or row_obj.get("RenderedDescription")
                or row_obj.get("msg")
            )

            # If there is no single message field, store the full row as JSON.
            text = str(msg) if msg is not None else json.dumps(row_obj, ensure_ascii=False)

            severity = row_obj.get("SeverityLevel") or row_obj.get("Level") or row_obj.get("severity")
            service = (
                row_obj.get("Cloud_RoleName")
                or row_obj.get("AppRoleName")
                or row_obj.get("ServiceName")
                or "azure_monitor"
            )

            stable_key = f"{settings.azure_log_analytics_workspace_id}|{time_generated}|{service}|{text}"
            stable_id = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()

            out.append(
                AzureLogRow(
                    time_generated=time_generated,
                    text=text,
                    payload={
                        "timestamp": time_generated,
                        "source": "azure_monitor",
                        "service": str(service),
                        "severity": str(severity) if severity is not None else None,
                    },
                    stable_id=stable_id,
                )
            )
        return out


azure_monitor_service = AzureMonitorService()

