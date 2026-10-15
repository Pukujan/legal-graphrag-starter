"""Retriever stages for ``legal_graph.retrieval_bench``: query-aware graph scoring and Jev rerank.

``kind: "qgraph"`` (see ``legal_graph.graph_scoring``)::

  {"name": "bm25+ppr", "kind": "qgraph", "from": "bm25", "text_scores_from": "bm25",
   "method": "ppr", "n_seeds": 10, "alpha": 0.3, "lam": 0.5, "pool": 100, "graph_pool": 100,
   "seed_weight": "score"}

``text_scores_from`` names an earlier ``bm25`` system whose index gives the query's BM25 score
for every candidate (graph neighbours included). Without it (no corpus, e.g. CLERC seeds) the
first-stage list is scored by 1/(60+rank) and graph-only candidates get text score 0.

``kind: "jev_rerank"``: rerank the top ``k`` (20) of ``from`` with TypeSafe Jev
(``typesafe/jev-1.14`` via the OpenRouter Decisions API, PR #45 recipe: one Noul per candidate +
a (k+1)-way Choice asked in two option orders, score = mean(Noul, mean Choice)); the tail keeps
its order. Responses are cached in ``params.cache`` (JSONL keyed by request hash) so reruns
replay offline; a query whose request fails keeps the first-stage order (counted in STATS).
"""
from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .graph_scoring import GraphScorer, fuse_linear, rank_scores

_SCORERS: dict[int, GraphScorer] = {}
STATS: dict[str, dict] = {}


def scorer_for(index, edge_kinds=("cites",)) -> GraphScorer:
    key = id(index)
    if key not in _SCORERS:
        _SCORERS[key] = GraphScorer(sorted(index.nodes), [(s, t) for (s, t, k) in index.edges if k in edge_kinds])
    return _SCORERS[key]


def run_qgraph(sysdef, queries, first_stage, index, gcfg, bm25, dates, kmax) -> dict[str, list[str]]:
    sc = scorer_for(index, tuple(gcfg.get("edge_kinds", ["cites"])))
    n_seeds, lam = int(sysdef.get("n_seeds", 10)), float(sysdef.get("lam", 0.5))
    pool, gpool = int(sysdef.get("pool", 100)), int(sysdef.get("graph_pool", 100))
    method = sysdef.get("method", "ppr")
    kw = {"alpha": float(sysdef.get("alpha", 0.3)), "drop_seed_restart": bool(sysdef.get("drop_seed_restart", True))} \
        if method == "ppr" else {}
    # temporal leak control: no walks through opinions decided after the query
    node_dates = dates or {d: (n.get("date") or "") for d, n in index.nodes.items()}
    darr = sc.dates_array(node_dates)
    bpos = {d: i for i, d in enumerate(bm25.ids)} if bm25 is not None else None
    out = {}
    for q in queries:
        qid = q["query_id"]
        full = list(first_stage.get(qid, []))
        first = full[:pool]
        ex, qd = set(q.get("exclude", ())), q.get("date")

        def allowed(d, ex=ex, qd=qd):
            if d in ex:
                return False
            if dates and d not in dates:
                return False
            dd = node_dates.get(d, "")
            return not (qd and dd and dd > qd)

        if bm25 is not None:
            vec = bm25.score_vector(q["text"])
            text = {d: float(vec[bpos[d]]) for d in first if d in bpos}
            text_of = (lambda d, vec=vec: float(vec[bpos[d]]) if d in bpos else 0.0)
        else:
            text, text_of = rank_scores(first), None
        if sysdef.get("seed_weight", "score") == "uniform":
            seeds = {d: 1.0 for d in first[:n_seeds]}
        else:
            seeds = {d: text.get(d, 0.0) for d in first[:n_seeds]}
        g = sc.score(seeds, method, keep=sc.keep_mask(darr, qd), **kw)
        ranked = fuse_linear(text, g, sc, lam=lam, allowed=allowed, k=kmax, graph_pool=gpool, text_of=text_of)
        seen = set(ranked)
        out[qid] = (ranked + [d for d in full if d not in seen])[:kmax]
    return out


# ------------------------------------------------------------------ Jev rerank
_TOK = re.compile(r"[a-z0-9]+")


def best_window(qtext: str, text: str, chars: int = 600, scan: int = 30000) -> str:
    """The ``chars`` window of ``text`` sharing most distinct query tokens (stride chars/2)."""
    text = text[:scan]
    if len(text) <= chars:
        return text
    qt = {t for t in _TOK.findall(qtext.lower()) if len(t) > 3}
    best, bs = 0, -1
    for s in range(0, max(1, len(text) - chars + 1), chars // 2):
        n = len(qt & set(_TOK.findall(text[s:s + chars].lower())))
        if n > bs:
            best, bs = s, n
    return text[best:best + chars]


class CachingTransport:
    """Replay cached responses; call ``inner`` (default: live HttpTransport) on a miss and append it."""

    def __init__(self, path: Path, inner=None):
        self.path, self.data, self.lock = Path(path), {}, threading.Lock()
        if self.path.exists():
            for line in open(self.path, encoding="utf-8"):
                x = json.loads(line)
                self.data[x["key"]] = x["response"]
        self.inner = inner
        self.hits = self.misses = 0

    def __call__(self, body):
        from .jev import HttpTransport, request_key
        k = request_key(body)
        if k in self.data:
            self.hits += 1
            return 200, self.data[k]
        if self.inner is None:
            self.inner = HttpTransport()
        status, resp = self.inner(body)
        if status == 200 and "answers" in resp:
            with self.lock:
                self.misses += 1
                self.data[k] = resp
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"key": k, "response": resp}, ensure_ascii=True) + "\n")
        return status, resp


PROMPTS = {
    "citation": ("The passage cites a case; the citation was replaced by [CITATION]. Is candidate {c} the case "
                 "that [CITATION] refers to?",
                 "The passage cites a case; the citation was replaced by [CITATION]. Which candidate is the case "
                 "that [CITATION] refers to? Answer none if no candidate fits."),
    "statute": ("Is candidate {c} a statute section that answers the question?",
                "Which candidate statute section best answers the question? Answer none if no candidate fits."),
}


def run_jev_rerank(sysdef, queries, first_stage, corpus, kmax, transport=None) -> dict[str, list[str]]:
    from .jev import JevClient, average_choice, choice_questions, noul
    p = sysdef.get("params", {})
    k, chars, qchars = int(p.get("k", 20)), int(p.get("chars", 600)), int(p.get("query_chars", 2000))
    prompt = p.get("prompt", "citation")
    noul_i, choice_i = PROMPTS[prompt]
    docs = {d["id"]: d for d in corpus}
    tr = transport or CachingTransport(Path(os.path.expanduser(p.get("cache", "jev-cache.jsonl"))))
    client = JevClient(model=p.get("model", "typesafe/jev-1.14"), transport=tr)

    def one(q):
        cand = list(first_stage.get(q["query_id"], []))
        top = cand[:k]
        if len(top) < 2:
            return q["query_id"], cand, None
        keys = [f"c{j:02d}" for j in range(len(top))]
        cands = {}
        for key, d in zip(keys, top):
            doc = docs.get(d, {})
            cands[key] = {"title": (doc.get("title") or d)[:200],
                          "excerpt": best_window(q["text"], doc.get("text") or "", chars)}
        state = {("passage" if prompt == "citation" else "question"): q["text"][:qchars], "candidates": cands}
        qs = {f"n_{key}": {"type": "noul", "instructions": noul_i.format(c=key)} for key in keys}
        opts = {key: cands[key]["title"] for key in keys}
        opts["none"] = "None of the candidates."
        qs.update(choice_questions("pick", choice_i, opts))
        try:
            r = client.decide(state, qs)
            ans = r["answers"]
            ch = average_choice(ans, "pick", keys + ["none"])
            s = [(noul(ans, f"n_{key}") + ch[key]) / 2 for key in keys]
        except Exception:  # noqa: BLE001  (fail open: keep first-stage order)
            return q["query_id"], cand, False
        order = sorted(range(len(top)), key=lambda j: (-s[j], j))
        return q["query_id"], [top[j] for j in order] + cand[k:], True

    out, ok, failed = {}, 0, 0
    with ThreadPoolExecutor(int(p.get("workers", 16))) as ex:
        for qid, lst, good in ex.map(one, queries):
            out[qid] = lst[:kmax]
            ok += good is True
            failed += good is False
    STATS[sysdef["name"]] = {"reranked": ok, "failed_kept_order": failed, "n": len(queries),
                             "cache_hits": getattr(tr, "hits", None), **client.usage()}
    return out
