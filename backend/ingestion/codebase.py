import os
from pathlib import Path

from services.qdrant_service import qdrant_service


def _chunk_text(text: str, max_chars: int = 1200) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + max_chars])
        i += max_chars
    return chunks


def _guess_function_name(lines: list[str], line_idx: int) -> str | None:
    for j in range(max(0, line_idx - 8), min(len(lines), line_idx + 3)):
        line = lines[j]
        if line.strip().startswith("def "):
            return line.split("def ")[1].split("(")[0].strip()
        if line.strip().startswith("function "):
            return line.strip().split()[1].split("(")[0].strip()
    return None


async def ingest_codebase_folder(
    root: str,
    include_globs: list[str],
    service: str,
    max_files: int,
) -> int:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        return 0

    exts = {g.strip()[1:].lower() for g in include_globs if g.strip().startswith("*.")}
    all_ext = any(g.strip() in ("*", "*.*", "**/*") for g in include_globs)

    files: list[Path] = []
    for dirpath, _, filenames in os.walk(root_path):
        parts = Path(dirpath).parts
        if any(p in ("node_modules", ".git", "dist", "build", "__pycache__", ".venv") for p in parts):
            continue
        for name in filenames:
            if len(files) >= max_files:
                break
            p = Path(dirpath) / name
            suf = p.suffix.lower()
            if not all_ext and exts and suf not in exts:
                continue
            try:
                if p.is_file() and p.stat().st_size < 400_000:
                    files.append(p)
            except OSError:
                continue
        if len(files) >= max_files:
            break

    texts: list[str] = []
    payloads: list[dict] = []
    count = 0

    for fp in files:
        try:
            raw = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = raw.splitlines()
        try:
            rel = str(fp.relative_to(root_path))
        except ValueError:
            rel = str(fp.name)

        chunks = _chunk_text(raw)
        offset = 0
        for ch in chunks:
            line_start = raw[:offset].count("\n") + 1
            fn = _guess_function_name(lines, max(0, line_start - 1))
            texts.append(f"FILE {rel}\n{ch}")
            payloads.append(
                {
                    "source": "codebase",
                    "service": service,
                    "severity": None,
                    "file_path": rel,
                    "function_name": fn,
                    "line_start": line_start,
                    "link": "",
                    "timestamp": None,
                }
            )
            offset += len(ch)
            count += 1

    if texts:
        await qdrant_service.upsert_documents(texts, payloads)
    return count
