"""Differential test of the three checkers on the same 248 claims (correct@0.5, per item).

Writes per-item files to per_item/<checker>.jsonl and diff.json (W/T/L, McNemar, permutation p,
paired-bootstrap CI, example ids where only one checker is right) via legal_graph.benchmark_diff.
"""
from __future__ import annotations
import itertools, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legal_graph.benchmark_diff import diff  # noqa: E402

HERE = Path(__file__).parent
items = [json.loads(l) for l in open(HERE / "eval_set.jsonl")]
names = ["minicheck-roberta-large", "hhem-2.1-open", "jev-1.14"]
(HERE / "per_item").mkdir(exist_ok=True)
per = {}
for n in names:
    raw = json.load(open(HERE / f"scores-{n}.json"))["raw"]
    per[n] = {str(i): {"id": i, "kind": it["kind"], "label": it["label"], "prob": round(p, 4),
                       "correct": int((p >= 0.5) == bool(it["label"]))} for i, (it, p) in enumerate(zip(items, raw))}
    with open(HERE / "per_item" / f"{n}.jsonl", "w") as f:
        for r in per[n].values():
            f.write(json.dumps(r) + "\n")
out = {}
for a, b in itertools.combinations(names, 2):
    r = diff(per[a], per[b], "correct", examples=5)
    ex = lambda ids: [{"id": int(i), "kind": items[int(i)]["kind"], "claim": items[int(i)]["claim"][:160],  # noqa: E731
                       a: per[a][i]["prob"], b: per[b][i]["prob"]} for i in ids]
    r["only_a_right"], r["only_b_right"] = ex(r["only_a_right"]), ex(r["only_b_right"])
    out[f"{a} vs {b}"] = r
    print(f"{a} vs {b}: W/T/L {r['win_tie_loss']} McNemar b={r['mcnemar_b']} c={r['mcnemar_c']} "
          f"p={r['mcnemar_p']:.3g} perm p={r['permutation_p']:.3g} diff={r['mean_diff']:+.3f} {r['diff_ci95']}")
json.dump(out, open(HERE / "diff.json", "w"), indent=2)
