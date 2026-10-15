"""Tune the query-aware graph stage on the Colorado CAP DEV split only (2008-2014 citing opinions).

Grid over method (ppr / neighbor), alpha, n_seeds, seed weighting and lam; objective = mean R@10
over dev direct + dev indirect (MRR reported). Test queries are never used (asserted by date).

  python benchmarks/colorado/tune_graph.py --data-root /workspace/coeval-data/bench --out benchmarks/colorado/tune-dev.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from legal_graph.benchmark import mrr, recall_at  # noqa: E402
from legal_graph.graph_scoring import fuse_linear  # noqa: E402
from legal_graph.graph_stage import scorer_for  # noqa: E402
from legal_graph.retrieval_bench import BM25, _graph_index, _read_jsonl  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    cfg = json.loads((HERE / "cap-direct/config.json").read_text())
    qs, nsplit = [], {}
    for v in ("direct", "indirect"):
        qq = [q for q in _read_jsonl(HERE / f"cap-{v}/queries.jsonl") if q["split"] == "dev"][: a.limit or None]
        assert all(q["date"] < "2015-01-01" for q in qq)
        nsplit[v] = len(qq)
        qs += qq
    paths = {k: a.data_root / cfg[k] for k in ("corpus_file", "nodes_file", "edges_file")}
    corpus = _read_jsonl(paths["corpus_file"])
    dates = {d["id"]: d.get("date") or "" for d in corpus}
    t0 = time.time()
    bm = BM25(corpus)
    bpos = {d: i for i, d in enumerate(bm.ids)}
    index = _graph_index(paths, qs, cfg)
    sc = scorer_for(index)
    darr = sc.dates_array(dates)
    print("built", round(time.time() - t0), flush=True)
    prep = []
    for q in qs:
        prep.append((q, bm.score_vector(q["text"]),
                     bm.search(q["text"], 100, max_date=q["date"], exclude=q.get("exclude", ())),
                     sc.keep_mask(darr, q["date"])))
    lams = [0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0]
    grid = [("ppr", al, ns, sw) for al in (0.15, 0.3, 0.5) for ns in (5, 10, 20) for sw in ("score", "uniform")]
    grid += [("neighbor", None, ns, sw) for ns in (5, 10, 20) for sw in ("score", "uniform")]
    rows = []
    nd = nsplit["direct"]
    for method, al, ns, sw in grid:
        acc = {lam: ([], []) for lam in lams}
        for q, vec, first, keep in prep:
            text = {d: float(vec[bpos[d]]) for d in first}
            seeds = {d: (text[d] if sw == "score" else 1.0) for d in first[:ns]}
            g = sc.score(seeds, method, keep=keep, **({"alpha": al} if method == "ppr" else {}))
            ex, qd = set(q.get("exclude", ())), q["date"]
            allowed = lambda d: d not in ex and d in dates and not (dates[d] and dates[d] > qd)  # noqa: E731
            gold = set(q["gold"])
            for lam in lams:
                r = fuse_linear(text, g, sc, lam=lam, allowed=allowed, k=20,
                                text_of=lambda d: float(vec[bpos[d]]) if d in bpos else 0.0)
                acc[lam][0].append(recall_at(r, gold, 10))
                acc[lam][1].append(mrr(r, gold))
        for lam in lams:
            r10, m = acc[lam]
            rows.append({"method": method, "alpha": al, "n_seeds": ns, "seed_weight": sw, "lam": lam,
                         "dev_r10": sum(r10) / len(r10), "dev_mrr": sum(m) / len(m),
                         "dev_r10_direct": sum(r10[:nd]) / nd, "dev_r10_indirect": sum(r10[nd:]) / (len(r10) - nd),
                         "dev_mrr_direct": sum(m[:nd]) / nd, "dev_mrr_indirect": sum(m[nd:]) / (len(m) - nd)})
        print(method, al, ns, sw, json.dumps(max(rows, key=lambda r: r["dev_r10"])), flush=True)
    rows.sort(key=lambda r: (-r["dev_r10"], -r["dev_mrr"]))
    a.out.write_text(json.dumps({"n_dev_queries": nsplit, "objective": "mean dev R@10 (direct+indirect)",
                                 "best": rows[0], "grid": rows}, indent=1) + "\n")
    print(json.dumps(rows[:5], indent=1))


if __name__ == "__main__":
    main()
