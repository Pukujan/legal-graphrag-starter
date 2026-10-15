"""Chunkers: fixed-size character windows vs paragraph-preserving recursive splits."""
from __future__ import annotations

import re


def fixed_chunks(text: str, size: int = 500, overlap: int = 0) -> list[str]:
    """Cut every ``size`` characters, ignoring structure (LegalBench-RAG baseline)."""
    step = max(1, size - overlap)
    return [text[i:i + size] for i in range(0, len(text), step) if text[i:i + size].strip()]


_SEPS = ("\n\n", "\n", ". ", " ")


def recursive_chunks(text: str, size: int = 500, seps: tuple[str, ...] = _SEPS) -> list[str]:
    """Split on paragraphs, then lines, sentences and words, packing pieces up to ``size``.

    Same algorithm as LangChain's RecursiveCharacterTextSplitter and chonkie's
    RecursiveChunker; kept dependency-free so it runs in CI.
    """
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    sep = next((s for s in seps if s in text), None)
    if sep is None:
        return fixed_chunks(text, size)
    rest = seps[seps.index(sep) + 1:]
    pieces = []
    for part in text.split(sep):
        pieces.extend(recursive_chunks(part, size, rest) if len(part) > size else ([part] if part.strip() else []))
    out, cur = [], ""
    for p in pieces:
        cand = f"{cur}{sep}{p}" if cur else p
        if len(cand) <= size:
            cur = cand
        else:
            if cur:
                out.append(cur.strip())
            cur = p
    if cur.strip():
        out.append(cur.strip())
    return out


def header(meta: dict) -> str:
    """Cheap chunk header (no LLM): case name | court | year | section."""
    year = (meta.get("date") or "")[:4]
    parts = [meta.get("title") or meta.get("id") or "", meta.get("court") or "", year, meta.get("section") or ""]
    return " | ".join(p for p in parts if p)


_WS = re.compile(r"\s+")


def chunk_doc(doc_id: str, text: str, meta: dict | None = None, *, method: str = "recursive",
              size: int = 500, with_header: bool = False, max_chars: int | None = None) -> list[dict]:
    if max_chars:
        text = text[:max_chars]
    fn = recursive_chunks if method == "recursive" else fixed_chunks
    head = header(meta or {}) if with_header else ""
    out = []
    for i, c in enumerate(fn(text, size)):
        body = _WS.sub(" ", c).strip() if method == "fixed" else c
        out.append({"doc_id": doc_id, "seq": i, "text": f"{head}\n{body}" if head else body})
    return out
