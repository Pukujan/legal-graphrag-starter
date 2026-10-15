"""Aggregate rerank_jev.py rows: per-split means and paired diffs (legal_graph.benchmark_diff).

Usage: python rerank_report.py <work_dir>   -> benchmarks/clerc/text/rerank-results.json,
per-query files in <work_dir>/per_query/<split>-<system>.jsonl.
"""
from __future__ import annotations
import itertools, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from legal_graph.benchmark_diff import diff  # noqa: E402

SYSTEMS = ["none", "minilm", "jev", "jev_noul", "jev_choice"]
METRICS = ["R@5", "R@10", "nDCG@10", "MRR"]


def main(work: str) -> int:
    w = Path(work)
    (w / "per_query").mkdir(exist_ok=True)
    out, cost, calls = {"candidates": "top-20 of bm25 +graph (bench.py, holdout edges removed)", "splits": {}}, 0.0, 0
    lat = []
    for split in ["indirect", "direct"]:
        p = w / f"rerank-{split}.jsonl"
        if not p.exists():
            continue
        rows = {}
        for line in open(p):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "jev" in r:
                rows[r["i"]] = r
        rows = list(rows.values())
        cost += sum(float(r["cost"] or 0) for r in rows)
        calls += len(rows)
        lat += [r["latency_ms"] for r in rows]
        per = {s: {r["id"]: {"id": r["id"], **r[s]} for r in rows} for s in SYSTEMS}
        for s in SYSTEMS:
            with open(w / "per_query" / f"{split}-{s}.jsonl", "w") as f:
                for x in per[s].values():
                    f.write(json.dumps(x) + "\n")
        means = {s: {m: round(sum(x[m] for x in per[s].values()) / len(rows), 4) for m in METRICS} for s in SYSTEMS}
        diffs = {}
        for a, b in [("jev", "none"), ("jev", "minilm"), ("minilm", "none")]:
            diffs[f"{a} vs {b}"] = {m: diff(per[a], per[b], m, examples=5) for m in METRICS}
        out["splits"][split] = {"n": len(rows), "gold_in_top20": round(sum(r["gold_in_cand"] for r in rows) / len(rows), 4),
                                "means": means, "diffs": diffs}
    lat.sort()
    out["jev_usage"] = {"calls": calls, "cost_usd": round(cost, 4), "cost_per_1k_calls_usd": round(1000 * cost / calls, 4),
                        "latency_p50_ms": lat[len(lat) // 2], "latency_p95_ms": lat[int(0.95 * len(lat))]}
    (HERE / "rerank-results.json").write_text(json.dumps(out, indent=2) + "\n")
    for split, s in out["splits"].items():
        print(f"## {split} n={s['n']} gold in top20={s['gold_in_top20']}")
        for k, v in s["means"].items():
            print(f"| {k} | " + " | ".join(f"{v[m]:.4f}" for m in METRICS) + " |")
        for k, d in s["diffs"].items():
            print(k, "; ".join(f"{m} {r['mean_diff']:+.4f} [{r['diff_ci95'][0]:+.4f},{r['diff_ci95'][1]:+.4f}] "
                              f"W/T/L {'/'.join(map(str, r['win_tie_loss']))} p={r['permutation_p']:.3g}"
                              for m, r in d.items()))
    print(json.dumps(out["jev_usage"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
