import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi import HTTPException
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
        src = (h.get("source") or "unknown").strip()
        ext = (h.get("external_id") or "").strip()
        if src.lower() == "jira" and ext:
            label = f"jira {ext}"
        else:
            label = f"{src} : line {line}"
        out.append(
            {
                "title": title or "(empty chunk)",
                "source": src,
                "line": line,
                "link": h.get("link") or "",
                "citation_label": label,
            }
        )
    return out


_SOURCE_LIST_RE = re.compile(r"\[(\s*SOURCE\s*\d+(?:\s*,\s*SOURCE\s*\d+)*)\s*\]", re.IGNORECASE)
_SOURCE_NUM_RE = re.compile(r"SOURCE\s*(\d+)", re.IGNORECASE)


def _decorate_sources(md: str, hits: list[dict]) -> str:
    """
    Convert LLM placeholders like [SOURCE 1] or [SOURCE 2, SOURCE 3]
    into clickable citations using the retrieved hit metadata (link, source, line).
    """
    if not md or not hits:
        return md or ""

    def cite(n: int) -> str:
        i = n - 1
        if i < 0 or i >= len(hits):
            return f"Source {n}"
        h = hits[i] or {}
        src = (h.get("source") or "source").strip()
        line = h.get("line_start") or h.get("line") or 0
        link = (h.get("link") or "").strip()
        ext = (h.get("external_id") or "").strip()
        if src.lower() == "jira" and ext:
            label = f"jira {ext}"
        else:
            label = f"{src} line {line}".strip()
        return f"[{label}]({link})" if link else label

    def repl(m: re.Match) -> str:
        nums = [int(x) for x in _SOURCE_NUM_RE.findall(m.group(1) or "")]
        if not nums:
            return m.group(0)
        return "(" + "; ".join(cite(n) for n in nums) + ")"

    # Turn bracketed SOURCE lists into "(linked citations)".
    return _SOURCE_LIST_RE.sub(repl, md)


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

_RESTART_WORD_RE = re.compile(r"\brestart\b", re.IGNORECASE)


def _maybe_add_restart_button(suggested_fix: str) -> str:
    text = (suggested_fix or "").strip()
    if not text:
        return ""
    if not _RESTART_WORD_RE.search(text):
        return text
    if "data-kairos-action=\"restart\"" in text:
        return text
    return (
        text
        + "\n\n"
        + '<button type="button" data-kairos-action="restart" class="kairos-restart-btn">Restart</button>'
    )


def _split_sections(md: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for m in _SECTION.finditer(md or ""):
        parts[m.group(1).strip().lower().replace(" ", "_")] = m.group(2).strip()
    return parts


@router.post("/investigate", response_model=InvestigateResponse)
async def investigate(body: InvestigateRequest):
    try:
        result = await run_investigation(body.issue)
    except RuntimeError as exc:
        # Common misconfiguration cases (e.g., missing API key) should not show up as a generic 500.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Unexpected failures should still return a clear message to the frontend.
        raise HTTPException(status_code=503, detail=f"Investigation failed: {exc}") from exc
    md = result["markdown"]
    conf = float(result["confidence_score"])
    insufficient = bool(result["insufficient_data"])
    hits = result.get("vector_hits") or []
    md = _decorate_sources(md, hits)

    parsed = _split_sections(md)
    issue_summary = parsed.get("issue_summary", "")
    root_cause = parsed.get("root_cause_hypothesis", "")
    suggested = _maybe_add_restart_button(parsed.get("suggested_fix", ""))

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
