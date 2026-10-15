"""CLERC check for the query-aware graph stage (no tuning here: parameters come from the Colorado DEV
grid, ``benchmarks/colorado/tune-dev.json``; a lam sweep is reported as sensitivity only).

First stage = the cached bench.py BM25 doc lists (``--work/bm25-{split}.jsonl``, top 100, ranks
only, so text score = 1/(60+rank)). Graph = PR #25 expanded graph, ``cites`` only, the frozen
``holdout_edges`` removed (same leak control as bench.py). Systems:

  bm25           cached list
  bm25+graph     bench.py baseline: RRF(bm25, one-hop expansion of the top 10 ranked by
                 sum 1/(60+rank), ties by global in-degree)
  bm25+ppr       legal_graph.graph_scoring PPR + linear fusion (CAP-dev-tuned params)
  bm25+neighbor  one-hop neighbour score + linear fusion (CAP-dev-tuned params)

Writes per-query JSONL (one file per system, ``benchmark_diff`` format) and a summary JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from bench import Graph, metrics  # noqa: E402
from legal_graph.graph_scoring import GraphScorer, fuse_linear, rank_scores  # noqa: E402
from legal_graph.search.hybrid import rrf  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edges", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--params", type=Path, default=ROOT / "benchmarks/colorado/qgraph-params.json")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--splits", nargs="*", default=["indirect", "direct"])
    ap.add_argument("--sweep", default="0.1,0.2,0.5,1.0,2.0")
    a = ap.parse_args(argv)
    P = json.loads(a.params.read_text())
    a.out.mkdir(parents=True, exist_ok=True)
    g = Graph(a.edges)
    summary = {"params": P, "splits": {}}
    for split in a.splits:
        qs = [json.loads(l) for l in open(ROOT / f"benchmarks/clerc/{split}/queries.jsonl", encoding="utf-8")]
        hold = {(e[0], e[1]) for q in qs for e in q.get("holdout_edges", []) if e[2] == "cites"}
        g.build(hold)
        edges = [(s, t) for s, t in g.edges if (s, t) not in hold]
        nodes = sorted({x for e in edges for x in e})
        sc = GraphScorer(nodes, edges)
        bm = {}
        for line in open(a.work / f"bm25-{split}.jsonl"):
            x = json.loads(line)
            bm[x["i"]] = x["docs"]
        systems = ["bm25", "bm25+graph"] + [f"bm25+{m}" for m in P] + \
                  [f"bm25+ppr@lam{l}" for l in a.sweep.split(",")]
        rows = {s: [] for s in systems}
        for i, q in enumerate(qs):
            b, gold = bm[i], set(q["gold"])
            lists = {"bm25": b, "bm25+graph": rrf([b, g.expand(b[:10])])[:100]}
            text = rank_scores(b)
            for m, p in P.items():
                seeds = {d: (text[d] if p["seed_weight"] == "score" else 1.0) for d in b[: p["n_seeds"]]}
                gs = sc.score(seeds, p["method"], **({"alpha": p["alpha"]} if p["method"] == "ppr" else {}))
                lists[f"bm25+{m}"] = fuse_linear(text, gs, sc, lam=p["lam"], allowed=lambda d: True, k=100)
                if m == "ppr":
                    for l in a.sweep.split(","):
                        lists[f"bm25+ppr@lam{l}"] = fuse_linear(text, gs, sc, lam=float(l), allowed=lambda d: True,
                                                                 k=100)
            for s in systems:
                rows[s].append({"id": q["query_id"], **metrics(lists[s], gold)})
        res = {}
        for s in systems:
            with open(a.out / f"{split}.{s.replace('+', '_')}.jsonl", "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows[s])
            res[s] = {k: round(sum(r[k] for r in rows[s]) / len(rows[s]), 4) for k in rows[s][0] if k != "id"}
        summary["splits"][split] = {"n_queries": len(qs), "holdout_edges_removed": len(hold), "results": res}
        print(split, json.dumps(res, indent=1), flush=True)
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
