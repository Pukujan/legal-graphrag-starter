"""Differential comparison of two systems on the same benchmark items.

Input: two per-item results files (JSONL, one object per item) sharing an id field and a
numeric metric field (e.g. ``{"id": "q17", "MRR": 0.5, "R@10": 1}`` or ``{"id": 3, "correct": 1}``).
Output: per-item win/tie/loss, mean difference with paired-bootstrap 95% CI, two-sided paired
permutation (sign-flip) p-value, exact McNemar p-value when the metric is 0/1, and up to
``--examples`` ids where only one system is right (metric > 0 for one, == 0 for the other).

Usage:
  python -m legal_graph.benchmark_diff A.jsonl B.jsonl --metric MRR [--id id] [--names A B] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections.abc import Sequence
from pathlib import Path


def load(path: Path | str, id_field: str = "id") -> dict:
    out = {}
    for line in open(path, encoding="utf-8"):
        if line.strip():
            x = json.loads(line)
            out[str(x[id_field])] = x
    return out


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from the discordant counts b (A right, B wrong) and c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def paired_bootstrap_ci(d: Sequence[float], n: int = 2000, seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    m = len(d)
    means = sorted(sum(d[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def permutation_p(d: Sequence[float], n: int = 5000, seed: int = 0) -> float:
    """Two-sided paired sign-flip permutation test on per-item differences."""
    rng = random.Random(seed)
    obs = abs(sum(d))
    nz = [x for x in d if x != 0]
    if not nz:
        return 1.0
    hits = sum(abs(sum(x if rng.random() < 0.5 else -x for x in nz)) >= obs - 1e-12 for _ in range(n))
    return (hits + 1) / (n + 1)


def diff(a: dict, b: dict, metric: str, examples: int = 5, n_boot: int = 2000, seed: int = 0) -> dict:
    ids = sorted(set(a) & set(b))
    if not ids:
        raise ValueError("no shared ids")
    va = [float(a[i][metric]) for i in ids]
    vb = [float(b[i][metric]) for i in ids]
    d = [x - y for x, y in zip(va, vb)]
    win, tie, loss = sum(x > 0 for x in d), sum(x == 0 for x in d), sum(x < 0 for x in d)
    out = {"metric": metric, "n": len(ids), "mean_a": sum(va) / len(va), "mean_b": sum(vb) / len(vb),
           "mean_diff": sum(d) / len(d), "diff_ci95": paired_bootstrap_ci(d, n_boot, seed),
           "win_tie_loss": [win, tie, loss], "permutation_p": permutation_p(d, seed=seed),
           "only_a_right": [i for i, x, y in zip(ids, va, vb) if x > 0 and y == 0][:examples],
           "only_b_right": [i for i, x, y in zip(ids, va, vb) if y > 0 and x == 0][:examples]}
    if set(va) <= {0.0, 1.0} and set(vb) <= {0.0, 1.0}:
        bb = sum(x == 1 and y == 0 for x, y in zip(va, vb))
        cc = sum(x == 0 and y == 1 for x, y in zip(va, vb))
        out.update(mcnemar_b=bb, mcnemar_c=cc, mcnemar_p=mcnemar_exact(bb, cc))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("--metric", nargs="+", required=True)
    ap.add_argument("--id", default="id")
    ap.add_argument("--names", nargs=2, default=["A", "B"])
    ap.add_argument("--examples", type=int, default=5)
    ap.add_argument("--json", type=Path)
    x = ap.parse_args(argv)
    a, b = load(x.a, x.id), load(x.b, x.id)
    res = {"a": x.names[0], "b": x.names[1], "metrics": {m: diff(a, b, m, x.examples) for m in x.metric}}
    for m, r in res["metrics"].items():
        w, t, l = r["win_tie_loss"]
        line = (f"{x.names[0]} vs {x.names[1]} {m}: n={r['n']} {r['mean_a']:.4f} vs {r['mean_b']:.4f} "
                f"diff={r['mean_diff']:+.4f} [{r['diff_ci95'][0]:+.4f},{r['diff_ci95'][1]:+.4f}] W/T/L={w}/{t}/{l} "
                f"perm p={r['permutation_p']:.4g}")
        if "mcnemar_p" in r:
            line += f" McNemar b={r['mcnemar_b']} c={r['mcnemar_c']} p={r['mcnemar_p']:.4g}"
        print(line)
    if x.json:
        x.json.write_text(json.dumps(res, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
