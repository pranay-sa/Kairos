from __future__ import annotations

from typing import Any

import httpx

from config import settings


class JiraService:
    def _base(self) -> str:
        base = (settings.jira_base_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("JIRA_BASE_URL is not set")
        return base

    def _auth(self) -> tuple[str, str]:
        if not settings.jira_email or not settings.jira_api_token:
            raise RuntimeError("JIRA_EMAIL / JIRA_API_TOKEN are not set")
        return (settings.jira_email, settings.jira_api_token)

    async def search_issues(
        self,
        *,
        jql: str,
        max_results: int = 50,
        fields: list[str] | None = None,
        next_page_token: str | None = None,
    ) -> dict[str, Any]:
        base = self._base()
        auth = self._auth()
        # Jira Cloud removed /rest/api/3/search (CHANGE-2046).
        # Use /rest/api/3/search/jql which paginates via nextPageToken.
        url = f"{base}/rest/api/3/search/jql"
        resolved_fields = fields or [
            "summary",
            "description",
            "updated",
            "created",
            "priority",
            "project",
            "issuetype",
            "status",
        ]
        params = {
            "jql": jql,
            "maxResults": int(max_results),
            "fields": ",".join(resolved_fields),
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, params=params, auth=auth, headers={"Accept": "application/json"})
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = (exc.response.text or "").strip()
                msg = f"Jira search failed ({exc.response.status_code})."
                if detail:
                    msg += f" Response: {detail[:500]}"
                raise RuntimeError(msg) from exc
            return r.json()


jira_service = JiraService()

