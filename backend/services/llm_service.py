from openai import AsyncOpenAI

from config import settings

SYSTEM_PROMPT = """You are KAIROS, an incident investigation assistant.

RULES (must follow):
1. Answer ONLY using the RETRIEVED CONTEXT below. Do not use outside knowledge or assumptions.
2. If the context does not contain enough evidence to support a claim, state explicitly: "No sufficient evidence found in retrieved data" for that part.
3. Do not guess root causes or fixes. Hypotheses must be clearly labeled as hypotheses and grounded in cited context.
4. Every factual statement in Issue Summary and Related Incidents must cite sources using inline tags exactly like: [SOURCE_NAME line N] where N is the line number from the context block (use the "line" field shown per chunk).
5. If overall evidence is weak or contradictory, keep answers short and conservative.

Output MUST be valid Markdown with exactly these sections and headings:

# Incident Report

## Issue Summary
(text with inline [SOURCE line N] citations where applicable)

## Related Incidents
- Bullet list; each item may include [SOURCE line N] and optional link field from context if provided

## Root Cause Hypothesis
(grounded; if not supported, say insufficient evidence)

## Suggested Fix
(only if supported by context; otherwise say insufficient evidence)

## Confidence Score
A number from 0.0 to 1.0 on its own line, then a one-sentence justification based only on retrieval quality and context alignment.
"""


def _format_vector_context(chunks: list[dict]) -> str:
    lines = []
    for i, c in enumerate(chunks, start=1):
        line = c.get("line_start") or i
        src = c.get("source", "unknown")
        svc = c.get("service", "")
        sev = c.get("severity", "")
        link = c.get("link", "")
        meta = f'line={line} source={src} service={svc} severity={sev} link={link}'
        lines.append(f"--- Chunk {i} [{meta}] ---\n{c.get('text', '')}\n")
    return "\n".join(lines)


async def generate_report(
    user_issue: str,
    vector_chunks: list[dict],
    graph_summary: str,
) -> str:
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set")
    client = AsyncOpenAI(api_key=settings.groq_api_key, base_url=settings.llm_base_url)
    vctx = _format_vector_context(vector_chunks)
    user_content = f"""USER ISSUE:
{user_issue}

RETRIEVED VECTOR CONTEXT:
{vctx}

NEO4J GRAPH CONTEXT (relationships; may be empty):
{graph_summary or "(none)"}
"""
    resp = await client.chat.completions.create(
        model=settings.llm_chat_model,
        temperature=0.1,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    return (resp.choices[0].message.content or "").strip()
