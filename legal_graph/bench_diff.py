"""Differential testing between systems of one retrieval_bench / benchmark run.

For each system pair: per-query win/tie/loss on a metric, paired two-sided
permutation (sign-flip) test and paired bootstrap 95% CI of the mean
difference, plus example queries where only one system retrieved the gold
within k (exact McNemar too when the metric is 0/1, via ``legal_graph.benchmark_diff``,
the two-file variant from PR #45). Works on ``legal_graph.retrieval_bench`` runs (systems are the
per-query keys) and on legacy ``legal_graph.benchmark`` runs (baseline/augmented).

  python -m legal_graph.bench_diff RUN_DIR [--pairs a:b,c:d] [--queries Q.jsonl]
         [--metrics recall@10,mrr] [--found-k 10] [--out RUN_DIR-diff.json]
Writes ``diff.json`` and ``per_query.tsv`` into --out-dir (default: RUN_DIR + "-diff").
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
from pathlib import Path


def systems_of(rec: dict) -> list[str]:
    row = rec["per_query"][0]
    return [k for k, v in row.items() if isinstance(v, dict) and "mrr" in v]


def paired(a: list[float], b: list[float], *, n_perm: int = 10000, n_boot: int = 2000, seed: int = 0) -> dict:
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    obs = sum(d) / n if n else 0.0
    rng = random.Random(seed)
    nz = [x for x in d if x != 0]
    hits = 0
    for _ in range(n_perm):
        s = sum(x if rng.random() < 0.5 else -x for x in nz)
        if abs(s / n) >= abs(obs) - 1e-12:
            hits += 1
    boots = []
    for _ in range(n_boot):
        boots.append(sum(d[rng.randrange(n)] for _ in range(n)) / n)
    boots.sort()
    from .benchmark_diff import mcnemar_exact  # shared with the PR #45 two-file diff tool
    binary = all(x in (0, 1, 0.0, 1.0) for x in a) and all(x in (0, 1, 0.0, 1.0) for x in b)
    extra = {"p_mcnemar": mcnemar_exact(sum(x > y for x, y in zip(a, b)), sum(x < y for x, y in zip(a, b)))} \
        if binary else {}
    return {**extra, "mean_a": sum(a) / n, "mean_b": sum(b) / n, "mean_diff": obs,
            "win": sum(x > 0 for x in d), "tie": sum(x == 0 for x in d), "loss": sum(x < 0 for x in d),
            "p_permutation": (hits + 1) / (n_perm + 1),
            "ci95_bootstrap": [boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot) - 1]]}


def diff(run_dir: Path, pairs=None, queries=None, metrics=("recall@10", "mrr"), found_k=10,
         n_examples=5, n_perm=10000, n_boot=2000) -> dict:
    rec = json.loads((run_dir / "results.json").read_text())
    rows = rec["per_query"]
    systems = systems_of(rec)
    if not pairs:
        pairs = list(itertools.combinations(systems, 2))
    qtext = {}
    if queries:
        for line in Path(queries).read_text().splitlines():
            if line.strip():
                q = json.loads(line)
                qtext[q["query_id"]] = q
    out = {"run": str(run_dir), "n_queries": len(rows), "found_k": found_k, "pairs": []}
    fk = f"recall@{found_k}"
    for a, b in pairs:
        entry = {"a": a, "b": b, "metrics": {}}
        for m in metrics:
            entry["metrics"][m] = paired([r[a][m] for r in rows], [r[b][m] for r in rows],
                                         n_perm=n_perm, n_boot=n_boot)
        only = {"only_a": [], "only_b": []}
        for r in rows:
            fa, fb = r[a].get(fk, 0) > 0, r[b].get(fk, 0) > 0
            if fa != fb:
                only["only_a" if fa else "only_b"].append(r["query_id"])
        entry["only_found_counts"] = {k: len(v) for k, v in only.items()}
        ex = {}
        for side, ids in only.items():
            pick = sorted(ids, key=lambda i: hash_key(i))[:n_examples]
            ex[side] = [{"query_id": i, "text": (qtext.get(i, {}).get("text") or "")[:400],
                         "gold": qtext.get(i, {}).get("gold") or qtext.get(i, {}).get("gold_case")} for i in pick]
        entry["examples"] = ex
        out["pairs"].append(entry)
    return out


def hash_key(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


def write(out: dict, rec_rows: list[dict], systems: list[str], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "diff.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    ms = list(rec_rows[0][systems[0]].keys())
    with open(out_dir / "per_query.tsv", "w") as f:
        f.write("\t".join(["query_id"] + [f"{s}:{m}" for s in systems for m in ms]) + "\n")
        for r in rec_rows:
            f.write("\t".join([r["query_id"]] + [f"{r[s][m]:.4f}" for s in systems for m in ms]) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--pairs", default=None, help="a:b,c:d (default: all pairs)")
    ap.add_argument("--queries", default=None)
    ap.add_argument("--metrics", default="recall@10,mrr")
    ap.add_argument("--found-k", type=int, default=10)
    ap.add_argument("--out-dir", type=Path, default=None)
    a = ap.parse_args(argv)
    pairs = [tuple(p.split(":", 1)) for p in a.pairs.split(",")] if a.pairs else None
    out = diff(a.run_dir, pairs, a.queries, tuple(a.metrics.split(",")), a.found_k)
    rec = json.loads((a.run_dir / "results.json").read_text())
    write(out, rec["per_query"], systems_of(rec), a.out_dir or Path(str(a.run_dir).rstrip("/") + "-diff"))
    for p in out["pairs"]:
        mm = p["metrics"]
        print(f"{p['a']} vs {p['b']}: " + "; ".join(
            f"{m} {v['mean_a']:.3f} vs {v['mean_b']:.3f} W/T/L {v['win']}/{v['tie']}/{v['loss']} p={v['p_permutation']:.4f}"
            for m, v in mm.items()) + f"; only-found {p['only_found_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
