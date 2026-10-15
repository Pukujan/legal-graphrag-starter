"""Summarise saved scores: accuracy, AUROC, ECE (95% bootstrap CI) before/after CV temperature
scaling, and a split-conformal abstain threshold (alpha=0.05) with its out-of-fold false-accept rate."""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legal_graph.answer_check import (apply_temperature, auroc, bootstrap_ci, conformal_threshold,  # noqa: E402
                                      expected_calibration_error as ece, fit_temperature)

HERE = Path(__file__).parent
y = [json.loads(l)["label"] for l in open(HERE / "eval_set.jsonl")]
res = json.load(open(HERE / "results.json"))
k = 5
for name, r in res.items():
    if not isinstance(r, dict):
        continue
    raw = json.load(open(HERE / f"scores-{name}.json"))["raw"]
    cal, fa, fr = [0.0] * len(raw), 0, 0
    for f in range(k):
        tr = [i for i in range(len(raw)) if i % k != f]
        te = range(f, len(raw), k)
        t = fit_temperature([raw[i] for i in tr], [y[i] for i in tr])
        thr = conformal_threshold([apply_temperature(raw[i], t) for i in tr], [y[i] for i in tr], 0.05)
        for i in te:
            cal[i] = apply_temperature(raw[i], t)
            fa += (cal[i] > thr) and y[i] == 0
            fr += (cal[i] <= thr) and y[i] == 1
    t_all = fit_temperature(raw, y)
    r.update(ece_raw_ci=bootstrap_ci(ece, raw, y), ece_cal_ci=bootstrap_ci(ece, cal, y),
             auroc_ci=bootstrap_ci(auroc, raw, y),
             conformal_threshold_cal=conformal_threshold([apply_temperature(p, t_all) for p in raw], y, 0.05),
             conformal_false_accept=fa / y.count(0), conformal_false_abstain=fr / y.count(1))
json.dump(res, open(HERE / "results.json", "w"), indent=2)
for name, r in res.items():
    if isinstance(r, dict):
        print(f"| {name} | {r['acc']:.3f} | {r['auroc']:.3f} [{r['auroc_ci'][0]:.3f}-{r['auroc_ci'][1]:.3f}] | "
              f"{r['ece_raw']:.3f} [{r['ece_raw_ci'][0]:.3f}-{r['ece_raw_ci'][1]:.3f}] | "
              f"{r['ece_cal']:.3f} [{r['ece_cal_ci'][0]:.3f}-{r['ece_cal_ci'][1]:.3f}] | T={r['temperature']:.2f} | "
              f"{r['conformal_threshold_cal']:.3f} / FA {r['conformal_false_accept']:.3f} / FR {r['conformal_false_abstain']:.3f} | "
              f"{r['p50_ms']:.0f} / {r['p95_ms']:.0f} |")
    else:
        print(name, r)
