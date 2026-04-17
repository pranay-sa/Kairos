from typing import TypedDict

from langgraph.graph import END, StateGraph

from config import settings
from services.embedding_service import embed_query
from services.llm_service import generate_report
from services.qdrant_service import qdrant_service


def _confidence_from_hits(hits: list[dict]) -> float:
    if not hits:
        return 0.0
    scores = [h["score"] for h in hits if h.get("score") is not None]
    if not scores:
        return 0.2
    avg = sum(scores) / len(scores)
    coverage = min(1.0, len(hits) / max(1, settings.vector_top_k))
    return float(min(1.0, 0.55 * avg + 0.25 * coverage + 0.1))


def _service_hints(hits: list[dict]) -> list[str]:
    names: list[str] = []
    for h in hits:
        s = (h.get("service") or "").strip()
        if s and s not in names:
            names.append(s)
    return names[:12]


def _chat_hints(hits: list[dict]) -> list[str]:
    chats: list[str] = []
    for h in hits:
        if (h.get("source") or "") != "teams":
            continue
        svc = (h.get("service") or "").strip()
        if svc.startswith("teams_chat:"):
            cid = svc.split("teams_chat:", 1)[1].strip()
            if cid and cid not in chats:
                chats.append(cid)
    return chats[:8]


class AgentState(TypedDict):
    query: str
    vector_hits: list[dict]
    confidence: float
    markdown: str
    insufficient_data: bool


async def node_vector(state: AgentState) -> dict:
    q = state["query"]
    vec = await embed_query(q)
    try:
        hits = await qdrant_service.search(vec, settings.vector_top_k)
    except Exception as exc:
        # Degrade gracefully when Qdrant is unavailable/misconfigured.
        print(f"[kairos] Qdrant search warning: {exc}")
        hits = []
    conf = _confidence_from_hits(hits)
    return {"vector_hits": hits, "confidence": conf}


async def node_validate(state: AgentState) -> dict:
    if state["confidence"] < settings.confidence_threshold or not state["vector_hits"]:
        md = """# Incident Report

## Issue Summary
Insufficient data: retrieval confidence is below the configured threshold or no documents were found in the vector store. No sufficient evidence found.

## Related Incidents
- No sufficient evidence found

## Root Cause Hypothesis
No sufficient evidence found in retrieved data.

## Suggested Fix
No sufficient evidence found in retrieved data.

## Confidence Score
0.0 — Not enough grounded context to investigate.
"""
        return {"markdown": md, "insufficient_data": True}
    return {"insufficient_data": False}


async def node_generate(state: AgentState) -> dict:
    if state.get("insufficient_data"):
        return {}
    md = await generate_report(state["query"], state["vector_hits"])
    return {"markdown": md}


def _route_after_validate(state: AgentState) -> str:
    return "end" if state.get("insufficient_data") else "generate"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("retrieve_vector", node_vector)
    g.add_node("validate", node_validate)
    g.add_node("generate", node_generate)

    g.set_entry_point("retrieve_vector")
    g.add_edge("retrieve_vector", "validate")
    g.add_conditional_edges("validate", _route_after_validate, {"end": END, "generate": "generate"})
    g.add_edge("generate", END)
    return g.compile()


investigation_graph = build_graph()


async def run_investigation(query: str) -> dict:
    initial: AgentState = {
        "query": query,
        "vector_hits": [],
        "confidence": 0.0,
        "markdown": "",
        "insufficient_data": False,
    }
    out = await investigation_graph.ainvoke(initial)
    return {
        "markdown": out.get("markdown", ""),
        "confidence_score": float(out.get("confidence", 0.0)),
        "insufficient_data": bool(out.get("insufficient_data", False)),
        "vector_hits": out.get("vector_hits", []),
    }
