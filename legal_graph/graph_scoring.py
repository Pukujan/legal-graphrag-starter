"""Query-aware graph scoring: a pluggable retriever stage that re-scores a first-stage list.

PR #44 found that appending / RRF-fusing graph neighbours ranked by *global* citation
in-degree hurts BM25 on the Colorado citation sets: the neighbours ignore the query. This
stage makes the graph signal query-dependent:

* ``ppr``      personalised PageRank on the (undirected, degree-normalised) citation graph,
               restarting at the first-stage top ``n_seeds`` hits weighted by their
               first-stage score (BM25 score, or 1/(60+rank) when no scores exist).
* ``neighbor`` one hop: score(d) = sum over seed hits h linked to d of w(h) / deg(h).

Fusion: final(d) = text(d)/max(text) + lam * graph(d)/max(graph) over the union of the
first-stage top ``pool`` and the top ``graph_pool`` graph-scored nodes. ``text(d)`` is the
query's own first-stage score for every candidate (BM25 over the full corpus), so graph
neighbours are text-scored against the query before fusion; a neighbour the query does not
match gets only its graph share. Knobs are tuned on the DEV split only
(``benchmarks/colorado/tune_graph.py``).

Leak control: the caller builds the graph with the held-out query opinions' outgoing cites
removed (``retrieval_bench._graph_index``); ``keep`` blocks walks through nodes dated after
the query (their citations did not exist yet); ``allowed(d)`` drops excluded ids, later
documents and ids outside the corpus; ``drop_seed_restart`` removes the restart mass so PPR
does not simply echo the first stage.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

import numpy as np
from scipy import sparse


class GraphScorer:
    def __init__(self, node_ids: Sequence[str], edges: Iterable[tuple[str, str]]):
        self.ids = list(node_ids)
        self.pos = {d: i for i, d in enumerate(self.ids)}
        rows, cols = [], []
        for s, t in edges:
            i, j = self.pos.get(s), self.pos.get(t)
            if i is None or j is None or i == j:
                continue
            rows += [i, j]
            cols += [j, i]
        n = len(self.ids)
        a = sparse.csr_matrix((np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=(n, n))
        a.sum_duplicates()
        a.data[:] = 1.0
        self.deg = np.asarray(a.sum(axis=1)).ravel().astype(np.float32)
        inv = np.where(self.deg > 0, 1.0 / np.maximum(self.deg, 1e-12), 0.0).astype(np.float32)
        # column-stochastic transition: x <- P x spreads the mass at i evenly over its neighbours
        self.pt = (a @ sparse.diags(inv)).tocsr().astype(np.float32)

    def restart(self, seeds: Mapping[str, float]) -> np.ndarray:
        e = np.zeros(len(self.ids), dtype=np.float32)
        for d, w in seeds.items():
            i = self.pos.get(d)
            if i is not None and w > 0:
                e[i] += w
        s = e.sum()
        return e / s if s > 0 else e

    def ppr(self, seeds: Mapping[str, float], *, alpha: float = 0.3, iters: int = 20,
            drop_seed_restart: bool = True, keep: np.ndarray | None = None) -> np.ndarray:
        e = self.restart(seeds)
        if not e.any():
            return e
        x = e.copy()
        for _ in range(iters):
            x = alpha * e + (1 - alpha) * (self.pt @ x)
            if keep is not None:
                x = x * keep
        if drop_seed_restart:
            x = np.maximum(x - alpha * e, 0.0)
        return x

    def neighbor(self, seeds: Mapping[str, float], keep: np.ndarray | None = None) -> np.ndarray:
        e = self.restart(seeds)
        x = self.pt @ e if e.any() else e
        return x * keep if keep is not None else x

    def score(self, seeds: Mapping[str, float], method: str = "ppr", keep=None, **kw) -> np.ndarray:
        if method == "ppr":
            return self.ppr(seeds, keep=keep, **kw)
        if method == "neighbor":
            return self.neighbor(seeds, keep=keep)
        raise ValueError(f"unknown graph method {method}")

    def dates_array(self, dates: Mapping[str, str]) -> np.ndarray:
        return np.array([dates.get(d) or "" for d in self.ids], dtype=object)

    @staticmethod
    def keep_mask(date_arr: np.ndarray, max_date: str | None) -> np.ndarray | None:
        """1.0 for undated nodes and nodes dated on/before ``max_date``."""
        if not max_date:
            return None
        return ((date_arr == "") | (date_arr <= max_date)).astype(np.float32)


def rank_scores(ranked: Sequence[str], c: int = 60) -> dict[str, float]:
    return {d: 1.0 / (c + r + 1) for r, d in enumerate(ranked)}


def fuse_linear(text: Mapping[str, float], graph: np.ndarray, scorer: GraphScorer, *, lam: float,
                allowed: Callable[[str], bool], k: int, graph_pool: int = 100,
                text_of: Callable[[str], float] | None = None) -> list[str]:
    """Rank the union of ``text`` (first-stage candidates) and the top graph nodes.

    ``text_of`` gives the first-stage score of any id (e.g. a full BM25 vector), so graph-only
    candidates are text-scored against the query; without it they get 0 text score.
    """
    cand = dict(text)
    if lam > 0 and graph.any():
        m = min(graph_pool * 3, len(graph) - 1)
        top = np.argpartition(-graph, m)[: m + 1]
        top = top[np.argsort(-graph[top], kind="stable")]
        added = 0
        for i in top:
            if graph[i] <= 0 or added >= graph_pool:
                break
            d = scorer.ids[i]
            if d in cand or not allowed(d):
                continue
            cand[d] = text_of(d) if text_of else 0.0
            added += 1
    tmax = max(cand.values(), default=0.0) or 1.0
    gvals = {d: float(graph[scorer.pos[d]]) if d in scorer.pos else 0.0 for d in cand}
    gmax = max(gvals.values(), default=0.0) or 1.0
    final = {d: cand[d] / tmax + lam * gvals[d] / gmax for d in cand}
    return [d for d, _ in sorted(final.items(), key=lambda x: (-x[1], x[0]))][:k]


def graph_ranking(graph: np.ndarray, scorer: GraphScorer, *, allowed: Callable[[str], bool], exclude=(),
                  k: int = 100) -> list[str]:
    """Top ``k`` allowed nodes by graph score (ids in ``exclude`` skipped)."""
    if not graph.any():
        return []
    m = min(k * 5, len(graph) - 1)
    top = np.argpartition(-graph, m)[: m + 1]
    top = top[np.argsort(-graph[top], kind="stable")]
    ex, out = set(exclude), []
    for i in top:
        if graph[i] <= 0 or len(out) >= k:
            break
        d = scorer.ids[i]
        if d not in ex and allowed(d):
            out.append(d)
    return out


def fuse_rrf(first: Sequence[str], graph_list: Sequence[str], *, w: float = 1.0, k: int = 100, c: int = 60) -> list[str]:
    """Weighted RRF: 1/(c+rank_text) + w/(c+rank_graph). With w=1 and a one-hop neighbour list this
    is the CLERC bench.py fusion; the graph list here is query-aware (PPR / neighbour score)."""
    sc: dict[str, float] = {}
    for r, d in enumerate(first):
        sc[d] = sc.get(d, 0.0) + 1.0 / (c + r + 1)
    for r, d in enumerate(graph_list):
        sc[d] = sc.get(d, 0.0) + w / (c + r + 1)
    return [d for d, _ in sorted(sc.items(), key=lambda x: (-x[1], x[0]))][:k]
