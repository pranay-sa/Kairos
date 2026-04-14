import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from rag.agent import run_investigation

router = APIRouter()

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def _parse_confidence_from_markdown(md: str) -> float | None:
    m = re.search(r"## Confidence Score\s*\n\s*([0-9]*\.?[0-9]+)", md, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _related_from_hits(hits: list[dict]) -> list[dict]:
    out = []
    for h in hits[:8]:
        text = (h.get("text") or "").strip().replace("\n", " ")
        title = text[:160] + ("…" if len(text) > 160 else "")
        line = h.get("line_start") or h.get("line") or 0
        out.append(
            {
                "title": title or "(empty chunk)",
                "source": h.get("source", "unknown"),
                "line": line,
                "link": h.get("link") or "",
                "citation_label": f"{h.get('source', 'source')} : line {line}",
            }
        )
    return out


class InvestigateRequest(BaseModel):
    issue: str = Field(..., min_length=3, description="Natural language issue description")


class InvestigateResponse(BaseModel):
    markdown: str
    issue_summary: str = ""
    related_incidents: list[dict] = []
    root_cause_hypothesis: str = ""
    suggested_fix: str = ""
    confidence_score: float = 0.0
    insufficient_data: bool = False
    report_path: str | None = None


_SECTION = re.compile(
    r"##\s*(Issue Summary|Related Incidents|Root Cause Hypothesis|Suggested Fix|Confidence Score)\s*\n(.*?)(?=\n## |\Z)",
    re.DOTALL | re.IGNORECASE,
)


def _split_sections(md: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for m in _SECTION.finditer(md or ""):
        parts[m.group(1).strip().lower().replace(" ", "_")] = m.group(2).strip()
    return parts


@router.post("/investigate", response_model=InvestigateResponse)
async def investigate(body: InvestigateRequest):
    result = await run_investigation(body.issue)
    md = result["markdown"]
    conf = float(result["confidence_score"])
    insufficient = bool(result["insufficient_data"])
    hits = result.get("vector_hits") or []

    parsed = _split_sections(md)
    issue_summary = parsed.get("issue_summary", "")
    root_cause = parsed.get("root_cause_hypothesis", "")
    suggested = parsed.get("suggested_fix", "")

    md_conf = _parse_confidence_from_markdown(md)
    if md_conf is not None:
        conf = md_conf

    related = _related_from_hits(hits)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^\w\-]+", "_", body.issue[:40])[:40]
    fname = f"incident_{ts}_{safe}.md"
    path = REPORTS_DIR / fname
    path.write_text(md, encoding="utf-8")

    return InvestigateResponse(
        markdown=md,
        issue_summary=issue_summary,
        related_incidents=related,
        root_cause_hypothesis=root_cause,
        suggested_fix=suggested,
        confidence_score=conf,
        insufficient_data=insufficient,
        report_path=str(path),
    )
