"""Score eval_set.jsonl with each checker and report accuracy/AUROC/ECE/latency.

Temperature is fit with 5-fold cross-validation (fit on 4 folds, apply to the 5th) so the
after-calibration numbers are out-of-sample. Threshold 0.5 on the calibrated score.
Usage: uv run python benchmarks/answer_check/run_benchmark.py [--limit N]
"""
from __future__ import annotations
import json, os, statistics, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from huggingface_hub import model_info  # noqa: E402
from legal_graph.answer_check import (HHEMScorer, MiniCheckScorer, apply_temperature, auroc,  # noqa: E402
                                      expected_calibration_error, fit_temperature)

HERE = Path(__file__).parent
items = [json.loads(l) for l in open(HERE / "eval_set.jsonl")]
y = [i["label"] for i in items]


def acc(p, t=0.5):
    return sum((q >= t) == bool(l) for q, l in zip(p, y)) / len(y)


def cv_calibrate(p, k=5):
    out = [0.0] * len(p)
    for f in range(k):
        tr = [i for i in range(len(p)) if i % k != f]
        t = fit_temperature([p[i] for i in tr], [y[i] for i in tr])
        for i in range(f, len(p), k):
            out[i] = apply_temperature(p[i], t)
    return out, fit_temperature(p, y)


results = {}
for name, make in [("minicheck-roberta-large", lambda: MiniCheckScorer()),
                   ("hhem-2.1-open", lambda: HHEMScorer())]:
    s = make()
    s.model_version = model_info(s.model).sha[:12]
    lat, raw = [], []
    for it in items:
        t0 = time.perf_counter()
        raw.append(s.score([(it["source"], it["claim"])])[0])
        lat.append((time.perf_counter() - t0) * 1000)
    cal, t_full = cv_calibrate(raw)
    by_kind = {}
    for k in sorted({i["kind"] for i in items}):
        idx = [n for n, i in enumerate(items) if i["kind"] == k]
        by_kind[k] = sum((cal[n] >= 0.5) == bool(y[n]) for n in idx) / len(idx)
    results[name] = dict(model=s.model, version=s.model_version, n=len(y), acc=acc(raw), auroc=auroc(raw, y),
                         ece_raw=expected_calibration_error(raw, y), acc_cal=acc(cal),
                         ece_cal=expected_calibration_error(cal, y), temperature=t_full,
                         p50_ms=statistics.median(lat), p95_ms=sorted(lat)[int(0.95 * len(lat))],
                         acc_by_kind_cal=by_kind)
    json.dump({"raw": raw}, open(HERE / f"scores-{name}.json", "w"))
    print(name, json.dumps(results[name]), flush=True)
results["jev"] = ("skipped: OPENROUTER_API_KEY not set" if not os.environ.get("OPENROUTER_API_KEY")
                  else "not run in this benchmark")
json.dump(results, open(HERE / "results.json", "w"), indent=2)
