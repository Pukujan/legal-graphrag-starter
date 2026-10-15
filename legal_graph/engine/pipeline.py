"""Staged hybrid + graph retrieval that emits one event per stage.

Stages: parse (eyecite) -> bm25 -> dense (precomputed chunk index) -> hybrid (RRF) ->
graph (one hop, capped edge scan) -> rerank (cross-encoder, then optional Jev relevance
over the head) -> result. Every component is injected so tests run offline; the
live wiring is in ``octo_source.build_live_pipeline``.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from legal_graph.search.hybrid import RRF_K

from .corpus import Corpus, LexicalScorer, best_spans
from .models import EVENT_SCHEMA_VERSION, TextRetrieveRequest

# Reporter citations such as "855 F.2d 874", "410 U.S. 113", "200 Colo. 1".
_CITE = re.compile(r"\b(\d{1,4})\s+([A-Z][A-Za-z0-9.\s]{0,20}?\.?(?:\s?\d[a-z]{1,2})?)\s+(\d{1,5})\b")


def _finite(x: float | None) -> float | None:
    return round(x, 4) if x is not None and x == x else None


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
    # Full-corpus dense search over a precomputed index: (query, k) -> [(chunk_index, score)].
    dense_index: Callable[[str, int], list[tuple[int, float]]] | None = None
    dense_index_info: dict[str, Any] | None = None
    graph_edge_cap: int | None = None
    jev_workers: int = 8

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

        # dense: full-corpus search over the precomputed index, else re-encode the BM25 pool
        dense_docs: list[str] = []
        dense_score: dict[str, float] = {}
        dense_chunk: dict[str, int] = {}
        if req.dense and self.dense_index is not None:
            t0 = time.perf_counter()
            for i, sc in self.dense_index(req.query, req.dense_pool):
                d = corpus.chunks[i].doc_id
                if d not in dense_score:
                    dense_score[d], dense_chunk[d] = float(sc), i
                    dense_docs.append(d)
            yield event("dense", t0, model=self.dense_model, mode="index", top_chunks=req.dense_pool,
                        **(self.dense_index_info or {}),
                        candidates=[{"doc_id": d, "chunk_id": corpus.chunks[dense_chunk[d]].chunk_id, "rank": r + 1,
                                     "score": round(dense_score[d], 4)} for r, d in enumerate(dense_docs)])
        elif req.dense and self.dense and order:
            t0 = time.perf_counter()
            dpool = order[: req.dense_pool]
            sims = self.dense(req.query, [corpus.chunks[i].body for i in dpool])
            pairs = sorted(zip(sims, dpool), key=lambda p: (-float(p[0]), p[1]))
            for sc, i in pairs:
                d = corpus.chunks[i].doc_id
                if d not in dense_score:
                    dense_score[d], dense_chunk[d] = float(sc), i
                    dense_docs.append(d)
            yield event("dense", t0, model=self.dense_model, mode="pool", pool_chunks=len(dpool),
                        candidates=[{"doc_id": d, "rank": r + 1, "score": round(dense_score[d], 4)}
                                    for r, d in enumerate(dense_docs)])
        else:
            yield {"type": "stage", "schema_version": EVENT_SCHEMA_VERSION, "stage": "dense", "skipped": True,
                   "reason": "disabled by request" if not req.dense else "no dense model loaded"
                   if not (self.dense or self.dense_index) else "empty pool",
                   "duration_ms": 0.0, "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 2)}
        for d, i in dense_chunk.items():
            best_chunk.setdefault(d, i)

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
            truncated = bool(self.graph_edge_cap is not None and len(edges) >= self.graph_edge_cap)
            if self.graph_edge_cap is not None:
                edges = edges[: self.graph_edge_cap]
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
            yield event("graph", t0, hops=1, frontier=frontier, edges_scanned=len(edges), edge_cap=self.graph_edge_cap,
                        truncated=truncated, edges_used=len(used), edges=used[:500],
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

        # rerank: cross-encoder over the head, then (optional, default on) Jev relevance over the top jev_k
        final_score = {d: fused.get(d, 0.0) for d in pool}
        ranked = sorted(pool, key=lambda d: (-final_score[d], d))
        prev_rank = {d: r + 1 for r, d in enumerate(ranked)}
        method = "rrf"
        jev_scores: dict[str, float] = {}
        if req.rerank and ranked and (self.reranker or (req.jev and self.jev)):
            t0 = time.perf_counter()
            head = ranked[: req.rerank_k]
            info: dict[str, Any] = {}
            if self.reranker:
                t1 = time.perf_counter()
                rs = self.reranker(req.query, [corpus.chunks[best_chunk[d]].body for d in head])
                rr = {d: float(x) for d, x in zip(head, rs)}
                ranked = sorted(head, key=lambda d: (-rr[d], d)) + ranked[req.rerank_k:]
                final_score.update(rr)
                method = "cross-encoder"
                info["cross_encoder"] = {"model": self.rerank_model, "reranked": len(head),
                                         "duration_ms": round((time.perf_counter() - t1) * 1000, 2)}
            if req.jev and self.jev:
                t1 = time.perf_counter()
                jhead = ranked[: req.jev_k]

                def one(d: str) -> float:
                    try:
                        return float(self.jev(req.query, corpus.chunks[best_chunk[d]].body))
                    except Exception as exc:  # provider failure must not kill retrieval
                        provenance.setdefault(d, []).append({"source": "jev_error", "error": type(exc).__name__})
                        return float("nan")
                with ThreadPoolExecutor(max(1, min(self.jev_workers, len(jhead)))) as ex:
                    jev_scores = dict(zip(jhead, ex.map(one, jhead)))
                ok = {d: v for d, v in jev_scores.items() if v == v}
                if ok:  # Jev order within the head; failed calls keep their order after the scored cases
                    pos = {d: r for r, d in enumerate(jhead)}
                    jsorted = sorted(jhead, key=lambda d: (0, -ok[d], pos[d]) if d in ok else (1, 0.0, pos[d]))
                    ranked = jsorted + ranked[len(jhead):]
                    method = "jev" if method == "rrf" else method + "+jev"
                info["jev"] = {"model": self.jev_model, "k": len(jhead), "scored": len(ok),
                               "failed": len(jev_scores) - len(ok),
                               "duration_ms": round((time.perf_counter() - t1) * 1000, 2)}
            else:
                info["jev"] = {"skipped": True, "reason": "disabled by request" if not req.jev
                               else "OPENROUTER_API_KEY not configured"}
            yield event("rerank", t0, method=method, model=self.rerank_model, reranked=len(head), **info,
                        candidates=[{"doc_id": d, "rank": r + 1, "score": round(final_score[d], 4),
                                     "jev_relevance": _finite(jev_scores.get(d)), "prev_rank": prev_rank[d]}
                                    for r, d in enumerate(ranked[: len(head)])])
        else:
            yield {"type": "stage", "schema_version": EVENT_SCHEMA_VERSION, "stage": "rerank", "skipped": True,
                   "reason": "disabled by request" if not req.rerank else "no reranker loaded",
                   "jev": {"skipped": True, "reason": "disabled by request" if not (req.rerank and req.jev)
                           else "OPENROUTER_API_KEY not configured"},
                   "duration_ms": 0.0, "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 2)}

        top = ranked[: req.k]
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
                            "jev_relevance": _finite(jev_scores.get(d)),
                            "provenance": provenance.get(d, []), "passages": passages})
        yield {"type": "result", "schema_version": EVENT_SCHEMA_VERSION, "query": req.query,
               "corpus_revision": corpus.revision, "graph_revision": self.graph_revision,
               "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 2), "results": results}
