"""Staged hybrid + graph retrieval that emits one event per stage.

Stages: parse -> bm25 -> dense -> hybrid (RRF) -> graph (one hop) -> rerank ->
jev (optional) -> result. Every component is injected so tests run offline; the
live wiring is in ``octo_source.build_live_pipeline``.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from legal_graph.search.hybrid import RRF_K

from .corpus import Corpus, LexicalScorer, best_spans
from .models import EVENT_SCHEMA_VERSION, TextRetrieveRequest

# Reporter citations such as "855 F.2d 874", "410 U.S. 113", "200 Colo. 1".
_CITE = re.compile(r"\b(\d{1,4})\s+([A-Z][A-Za-z0-9.\s]{0,20}?\.?(?:\s?\d[a-z]{1,2})?)\s+(\d{1,5})\b")


def regex_citations(text: str) -> list[str]:
    out = []
    for m in _CITE.finditer(text):
        cite = f"{m.group(1)} {' '.join(m.group(2).split())} {m.group(3)}"
        if cite not in out:
            out.append(cite)
    return out


def citation_parser_name() -> str:
    import importlib.util
    return "eyecite" if importlib.util.find_spec("eyecite") else "regex"


def eyecite_citations(text: str) -> list[str]:
    """Full case citations via eyecite when installed, else the regex fallback."""
    try:
        from eyecite import get_citations
        from eyecite.models import FullCaseCitation
    except ImportError:
        return regex_citations(text)
    out = []
    for c in get_citations(text):
        if isinstance(c, FullCaseCitation):
            cite = c.corrected_citation()
            if cite not in out:
                out.append(cite)
    return out


@dataclass
class Pipeline:
    corpus: Corpus
    lexical: LexicalScorer
    parse_citations: Callable[[str], list[str]] = eyecite_citations
    resolve_citations: Callable[[Sequence[str]], dict[str, str]] | None = None
    dense: Callable[[str, Sequence[str]], Sequence[float]] | None = None
    dense_model: str | None = None
    neighbours: Callable[[Sequence[str]], list[dict[str, str]]] | None = None
    reranker: Callable[[str, Sequence[str]], Sequence[float]] | None = None
    rerank_model: str | None = None
    jev: Callable[[str, str], float] | None = None
    jev_model: str | None = None
    graph_revision: str = "supplied"

    def run(self, req: TextRetrieveRequest) -> Iterator[dict[str, Any]]:
        t_start = time.perf_counter()
        corpus = self.corpus

        def event(stage: str, t0: float, **data: Any) -> dict[str, Any]:
            now = time.perf_counter()
            return {"type": "stage", "schema_version": EVENT_SCHEMA_VERSION, "stage": stage,
                    "duration_ms": round((now - t0) * 1000, 2),
                    "elapsed_ms": round((now - t_start) * 1000, 2), **data}

        # parse
        t0 = time.perf_counter()
        cites = self.parse_citations(req.query)
        resolved = self.resolve_citations(cites) if (cites and self.resolve_citations) else {}
        seeds = [d for d in dict.fromkeys(resolved.values()) if d in corpus.by_doc or self.neighbours]
        yield event("parse", t0, citations=[{"citation": c, "doc_id": resolved.get(c)} for c in cites],
                    parser=citation_parser_name() if self.parse_citations is eyecite_citations else "custom")

        # bm25 (chunk level, max-pooled to cases)
        t0 = time.perf_counter()
        scores = self.lexical.score_all(req.query)
        order = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), i))[: req.bm25_k]
        order = [i for i in order if float(scores[i]) > 0]
        best_chunk: dict[str, int] = {}
        bm25_docs: list[str] = []
        for i in order:
            d = corpus.chunks[i].doc_id
            if d not in best_chunk:
                best_chunk[d] = i
                bm25_docs.append(d)
        bm25_score = {corpus.chunks[best_chunk[d]].doc_id: float(scores[best_chunk[d]]) for d in bm25_docs}
        yield event("bm25", t0, retriever=self.lexical.name, chunks_scored=len(scores),
                    candidates=[{"doc_id": d, "chunk_id": corpus.chunks[best_chunk[d]].chunk_id, "rank": r + 1,
                                 "score": round(bm25_score[d], 4)} for r, d in enumerate(bm25_docs)])

        # dense over the BM25 chunk pool
        dense_docs: list[str] = []
        dense_score: dict[str, float] = {}
        if req.dense and self.dense and order:
            t0 = time.perf_counter()
            dpool = order[: req.dense_pool]
            sims = self.dense(req.query, [corpus.chunks[i].body for i in dpool])
            pairs = sorted(zip(sims, dpool), key=lambda p: (-float(p[0]), p[1]))
            for s, i in pairs:
                d = corpus.chunks[i].doc_id
                if d not in dense_score:
                    dense_score[d] = float(s)
                    dense_docs.append(d)
            yield event("dense", t0, model=self.dense_model, pool_chunks=len(dpool),
                        candidates=[{"doc_id": d, "rank": r + 1, "score": round(dense_score[d], 4)}
                                    for r, d in enumerate(dense_docs)])
        else:
            yield {"type": "stage", "schema_version": EVENT_SCHEMA_VERSION, "stage": "dense", "skipped": True,
                   "reason": "disabled by request" if not req.dense else "no dense model loaded" if not self.dense else "empty pool",
                   "duration_ms": 0.0, "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 2)}

        # hybrid RRF (k=60)
        t0 = time.perf_counter()
        rankings = {"bm25": bm25_docs, **({"dense": dense_docs} if dense_docs else {})}
        fused: dict[str, float] = {}
        contrib: dict[str, dict[str, int]] = {}
        for name, ranking in rankings.items():
            for r, d in enumerate(ranking):
                fused[d] = fused.get(d, 0.0) + 1.0 / (RRF_K + r + 1)
                contrib.setdefault(d, {})[name] = r + 1
        hybrid = sorted(fused, key=lambda d: (-fused[d], d))
        yield event("hybrid", t0, method="rrf", rrf_k=RRF_K, inputs=list(rankings),
                    candidates=[{"doc_id": d, "rank": r + 1, "score": round(fused[d], 6), "ranks": contrib[d]}
                                for r, d in enumerate(hybrid)])

        # graph: one hop around top hybrid cases and resolved citations
        provenance: dict[str, list[dict[str, Any]]] = {d: [{"source": s, "rank": r} for s, r in contrib[d].items()]
                                                     for d in hybrid}
        for d in seeds:
            provenance.setdefault(d, []).insert(0, {"source": "question_citation"})
        pool = list(dict.fromkeys(seeds + hybrid))
        if req.graph and self.neighbours:
            t0 = time.perf_counter()
            frontier = list(dict.fromkeys(seeds + hybrid[: req.graph_seed_docs]))
            edges = self.neighbours(frontier) if frontier else []
            fset, added, used = set(frontier), [], []
            for e in edges:
                for here, there, direction in ((e["source"], e["target"], "outgoing"), (e["target"], e["source"], "incoming")):
                    if here in fset and there not in fset and there in corpus.by_doc:
                        used.append({"source": e["source"], "target": e["target"], "kind": e.get("kind", "cites")})
                        provenance.setdefault(there, []).append(
                            {"source": "graph", "edge": e.get("kind", "cites"), "via": here, "direction": direction})
                        if there not in pool:
                            pool.append(there)
                            added.append(there)
            for d in added:  # best passage for graph-only cases
                idxs = corpus.by_doc[d]
                best_chunk[d] = max(idxs, key=lambda i: (float(scores[i]), -i))
            yield event("graph", t0, hops=1, frontier=frontier, edges_scanned=len(edges), edges=used[:500],
                        added=[{"doc_id": d, "via": [p for p in provenance[d] if p["source"] == "graph"]} for d in added],
                        graph_revision=self.graph_revision)
        else:
            yield {"type": "stage", "schema_version": EVENT_SCHEMA_VERSION, "stage": "graph", "skipped": True,
                   "reason": "disabled by request" if not req.graph else "no graph source",
                   "duration_ms": 0.0, "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 2)}
        for d in pool:
            if d not in best_chunk and d in corpus.by_doc:
                best_chunk[d] = max(corpus.by_doc[d], key=lambda i: (float(scores[i]), -i))
        pool = [d for d in pool if d in best_chunk]

        # rerank
        final_score = {d: fused.get(d, 0.0) for d in pool}
        ranked = sorted(pool, key=lambda d: (-final_score[d], d))
        method = "rrf"
        if req.rerank and self.reranker and ranked:
            t0 = time.perf_counter()
            head = ranked[: req.rerank_k]
            rs = self.reranker(req.query, [corpus.chunks[best_chunk[d]].body for d in head])
            rr = {d: float(s) for d, s in zip(head, rs)}
            ranked = sorted(head, key=lambda d: (-rr[d], d)) + ranked[req.rerank_k:]
            final_score.update(rr)
            method = "cross-encoder"
            yield event("rerank", t0, model=self.rerank_model, reranked=len(head),
                        candidates=[{"doc_id": d, "rank": r + 1, "score": round(rr[d], 4),
                                     "prev_rank": pool_rank} for r, (d, pool_rank) in
                                    enumerate((d, sorted(pool, key=lambda x: (-fused.get(x, 0), x)).index(d) + 1)
                                              for d in ranked[: len(head)])])
        else:
            yield {"type": "stage", "schema_version": EVENT_SCHEMA_VERSION, "stage": "rerank", "skipped": True,
                   "reason": "disabled by request" if not req.rerank else "no reranker loaded",
                   "duration_ms": 0.0, "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 2)}

        top = ranked[: req.k]
        jev_scores: dict[str, float] = {}
        if req.jev:
            if self.jev:
                t0 = time.perf_counter()
                for d in top:
                    try:
                        jev_scores[d] = float(self.jev(req.query, corpus.chunks[best_chunk[d]].body))
                    except Exception as exc:  # provider failure must not kill retrieval
                        jev_scores[d] = float("nan")
                        provenance.setdefault(d, []).append({"source": "jev_error", "error": type(exc).__name__})
                yield event("jev", t0, model=self.jev_model,
                            candidates=[{"doc_id": d, "relevance": None if jev_scores[d] != jev_scores[d] else round(jev_scores[d], 4)}
                                        for d in top])
            else:
                yield {"type": "stage", "schema_version": EVENT_SCHEMA_VERSION, "stage": "jev", "skipped": True,
                       "reason": "OPENROUTER_API_KEY not configured", "duration_ms": 0.0,
                       "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 2)}

        results = []
        for r, d in enumerate(top):
            idxs = sorted(corpus.by_doc[d], key=lambda i: (-float(scores[i]), i))
            chosen = [best_chunk[d]] + [i for i in idxs if i != best_chunk[d]][: req.passages_per_case - 1]
            passages = [{"chunk_id": corpus.chunks[i].chunk_id, "ord": corpus.chunks[i].ord,
                         "text": corpus.chunks[i].body, "bm25": round(float(scores[i]), 4),
                         "spans": best_spans(corpus.chunks[i].body, req.query)} for i in chosen]
            results.append({"rank": r + 1, "case": corpus.case(d), "score": round(final_score[d], 6),
                            "score_kind": method, "rrf": round(fused.get(d, 0.0), 6),
                            "bm25": round(bm25_score[d], 4) if d in bm25_score else None,
                            "dense": round(dense_score[d], 4) if d in dense_score else None,
                            "jev_relevance": (round(jev_scores[d], 4) if d in jev_scores and jev_scores[d] == jev_scores[d] else None),
                            "provenance": provenance.get(d, []), "passages": passages})
        yield {"type": "result", "schema_version": EVENT_SCHEMA_VERSION, "query": req.query,
               "corpus_revision": corpus.revision, "graph_revision": self.graph_revision,
               "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 2), "results": results}
