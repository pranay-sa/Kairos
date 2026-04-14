import base64
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import settings

router = APIRouter()


class CreatePRRequest(BaseModel):
    title: str = Field(..., min_length=3)
    markdown: str = Field(..., min_length=10)
    filename: str = Field(default="reports/incident_report.md")
    branch_prefix: str = Field(default="kairos/incident-report")


class CreatePRResponse(BaseModel):
    branch: str
    pull_request_url: str
    file_path: str


async def _github_headers() -> dict[str, str]:
    if not settings.github_token:
        raise HTTPException(status_code=501, detail="GITHUB_TOKEN not configured")
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@router.post("/create-pr", response_model=CreatePRResponse)
async def create_pr(body: CreatePRRequest):
    owner = settings.github_owner
    repo = settings.github_repo
    if not owner or not repo:
        raise HTTPException(status_code=501, detail="GITHUB_OWNER and GITHUB_REPO must be set")

    base = f"https://api.github.com/repos/{owner}/{repo}"
    headers = await _github_headers()

    async with httpx.AsyncClient(timeout=60.0) as client:
        ref_res = await client.get(f"{base}/git/ref/heads/{settings.github_default_branch}", headers=headers)
        if ref_res.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Cannot read default branch: {ref_res.text}")
        base_sha = ref_res.json()["object"]["sha"]

        branch = f"{body.branch_prefix}-{int(time.time())}"
        cref = await client.post(
            f"{base}/git/refs",
            headers=headers,
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        if cref.status_code not in (201, 422):
            raise HTTPException(status_code=400, detail=f"Create branch failed: {cref.text}")

        content_b64 = base64.b64encode(body.markdown.encode("utf-8")).decode("ascii")
        put = await client.put(
            f"{base}/contents/{body.filename}",
            headers=headers,
            json={
                "message": body.title,
                "content": content_b64,
                "branch": branch,
            },
        )
        if put.status_code not in (200, 201):
            raise HTTPException(status_code=400, detail=f"Create file failed: {put.text}")

        pr = await client.post(
            f"{base}/pulls",
            headers=headers,
            json={
                "title": body.title,
                "head": branch,
                "base": settings.github_default_branch,
                "body": "Automated incident report from KAIROS.",
            },
        )
        if pr.status_code != 201:
            raise HTTPException(status_code=400, detail=f"Create PR failed: {pr.text}")
        data: dict[str, Any] = pr.json()

    return CreatePRResponse(
        branch=branch,
        pull_request_url=data.get("html_url", ""),
        file_path=body.filename,
    )
