"""In-process passage corpus and lexical scorers.

``Corpus`` holds the stored passages (Octo ``legal_graph.chunk`` rows) and case
metadata. Passages are never rewritten: every passage and span the API returns
is a slice of a stored chunk body.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and are as at be by for from has have in is it its of on or that the this to was were will with".split())
_SENT = re.compile(r"[^.!?\n]+[.!?]?")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    ord: int
    body: str


@dataclass
class Corpus:
    chunks: list[Chunk]
    docs: dict[str, dict[str, Any]]  # doc_id -> {name, court, year, link, cap_id}
    revision: str = "supplied"
    by_doc: dict[str, list[int]] = field(default_factory=dict)
    by_id: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        grouped: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(self.chunks):
            grouped[c.doc_id].append(i)
            self.by_id[c.chunk_id] = i
        self.by_doc = dict(grouped)

    def case(self, doc_id: str) -> dict[str, Any]:
        meta = self.docs.get(doc_id, {})
        return {"id": doc_id, "name": meta.get("name") or doc_id, "court": meta.get("court"),
                "year": meta.get("year"), "link": meta.get("link"), "cap_id": meta.get("cap_id")}


class LexicalScorer(Protocol):
    name: str

    def score_all(self, query: str) -> Sequence[float]:
        """BM25 score of the query against every chunk, aligned with Corpus.chunks."""


class PurePythonBM25:
    """Okapi BM25 (k1=1.5, b=0.75; Lucene idf), dependency-free. Used in tests and small corpora."""

    name = "bm25-python"

    def __init__(self, texts: Sequence[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(tokenize(t)) for t in texts]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1.0
        df: Counter[str] = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(texts)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(self.tf):
            for t in c:
                self.postings[t].append(i)

    def score_all(self, query: str) -> list[float]:
        out = [0.0] * len(self.tf)
        for t in set(tokenize(query)):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.postings[t]:
                f = self.tf[i][t]
                out[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return out


class Bm25sScorer:
    """bm25s (same tokenizer/stemmer as legal_graph.search.hybrid.BM25Index); needs requirements-search.txt."""

    name = "bm25s"

    def __init__(self, texts: Sequence[str]):
        from legal_graph.search.hybrid import BM25Index
        self._index = BM25Index(texts)

    def score_all(self, query: str):
        idx = self._index
        toks = idx._bm25s.tokenize([query], stopwords="en", stemmer=idx.stemmer, show_progress=False,
                                   return_ids=False)[0]
        return idx.model.get_scores(toks)


def best_spans(body: str, query: str, n: int = 2) -> list[dict[str, Any]]:
    """Sentence spans of ``body`` with most query-term overlap; offsets index into ``body``."""
    q = set(tokenize(query))
    scored = []
    for m in _SENT.finditer(body):
        text = m.group(0)
        stripped = text.strip()
        if len(stripped) < 20:
            continue
        start = m.start() + (len(text) - len(text.lstrip()))
        overlap = len(q & set(tokenize(stripped)))
        if overlap:
            scored.append((overlap, start, start + len(stripped)))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [{"start": s, "end": e, "text": body[s:e], "query_term_overlap": o} for o, s, e in sorted(scored[:n], key=lambda x: x[1])]
