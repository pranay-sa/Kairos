from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from config import settings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class GraphPage:
    values: list[dict[str, Any]]
    next_link: str | None
    delta_link: str | None


class MicrosoftGraphService:
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

        if not (settings.ms_tenant_id and settings.ms_client_id and settings.ms_client_secret):
            raise RuntimeError("Microsoft credentials missing (MS_TENANT_ID / MS_CLIENT_ID / MS_CLIENT_SECRET)")

        url = f"https://login.microsoftonline.com/{settings.ms_tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": settings.ms_client_id,
            "client_secret": settings.ms_client_secret,
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, data=data)
            r.raise_for_status()
            body = r.json()

        token = str(body.get("access_token", ""))
        if not token:
            raise RuntimeError("Graph token response missing access_token")

        expires_in = int(body.get("expires_in", 3600))
        self._token = token
        self._token_expiry = _utc_now() + timedelta(seconds=expires_in)
        return token

    async def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(url, params=params, headers=headers)
            r.raise_for_status()
            return dict(r.json())

    async def get_page(self, url: str, *, params: dict[str, Any] | None = None) -> GraphPage:
        body = await self.get_json(url, params=params)
        values = body.get("value") or []
        if not isinstance(values, list):
            values = []
        next_link = body.get("@odata.nextLink")
        delta_link = body.get("@odata.deltaLink")
        return GraphPage(values=list(values), next_link=str(next_link) if next_link else None, delta_link=str(delta_link) if delta_link else None)


ms_graph_service = MicrosoftGraphService()

