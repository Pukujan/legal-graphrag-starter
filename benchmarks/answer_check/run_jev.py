"""Score eval_set.jsonl with Jev (OpenRouter Decisions API) and add it to results.json.

Resumable: per-item results are appended to work/jev-checker.jsonl. Raw API responses are
recorded to work/jev-checker-fixtures.jsonl (a small subset is committed under tests/fixtures).
Usage: OPENROUTER_API_KEY=... uv run python benchmarks/answer_check/run_jev.py [--workers 8]
"""
from __future__ import annotations
import argparse, json, statistics, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legal_graph.answer_check import apply_temperature, auroc, expected_calibration_error, fit_temperature  # noqa: E402,F401
from legal_graph.jev import JevClient, JevScorer  # noqa: E402

HERE = Path(__file__).parent
ap = argparse.ArgumentParser()
ap.add_argument("--workers", type=int, default=8)
ap.add_argument("--work", type=Path, default=HERE / "work")
a = ap.parse_args()
a.work.mkdir(exist_ok=True)
items = [json.loads(l) for l in open(HERE / "eval_set.jsonl")]
y = [i["label"] for i in items]
ck = a.work / "jev-checker.jsonl"
done = {json.loads(l)["i"]: json.loads(l) for l in open(ck)} if ck.exists() else {}
client = JevClient(record=a.work / "jev-checker-fixtures.jsonl")
sc = JevScorer(client)


def one(i):
    r = sc.score_one(items[i]["source"], items[i]["claim"])
    r["i"] = i
    with open(ck, "a") as f:
        f.write(json.dumps(r) + "\n")
    return r


with ThreadPoolExecutor(a.workers) as ex:
    for r in ex.map(one, [i for i in range(len(items)) if i not in done]):
        done[r["i"]] = r
rows = [done[i] for i in range(len(items))]
raw = [r["prob"] for r in rows]
lat = sorted(r["latency_ms"] for r in rows)
cost = sum(float(r["cost"] or 0) for r in rows)
k = 5
cal = [0.0] * len(raw)
for f in range(k):
    tr = [i for i in range(len(raw)) if i % k != f]
    t = fit_temperature([raw[i] for i in tr], [y[i] for i in tr])
    for i in range(f, len(raw), k):
        cal[i] = apply_temperature(raw[i], t)
acc = lambda p: sum((q >= 0.5) == bool(l) for q, l in zip(p, y)) / len(y)  # noqa: E731
by_kind = {}
for kd in sorted({i["kind"] for i in items}):
    idx = [n for n, i in enumerate(items) if i["kind"] == kd]
    by_kind[kd] = sum((cal[n] >= 0.5) == bool(y[n]) for n in idx) / len(idx)
name = "jev-1.14"
res = json.load(open(HERE / "results.json"))
res.pop("jev", None)
res[name] = dict(model="typesafe/jev-1.14", version=rows[0]["model_version"], n=len(y), acc=acc(raw),
                 auroc=auroc(raw, y), ece_raw=expected_calibration_error(raw, y), acc_cal=acc(cal),
                 ece_cal=expected_calibration_error(cal, y), temperature=fit_temperature(raw, y),
                 p50_ms=statistics.median(lat), p95_ms=lat[int(0.95 * len(lat))], acc_by_kind_cal=by_kind,
                 acc_noul_only=acc([r["noul"] for r in rows]), acc_choice_only=acc([r["choice"] for r in rows]),
                 auroc_noul_only=auroc([r["noul"] for r in rows], y),
                 auroc_choice_only=auroc([r["choice"] for r in rows], y),
                 api_calls=len(rows), api_cost_usd=round(cost, 6),
                 cost_per_1k_calls_usd=round(1000 * cost / len(rows), 4),
                 note=f"{a.workers} concurrent requests; latency is per HTTP call over the internet")
json.dump({"raw": raw}, open(HERE / f"scores-{name}.json", "w"))
json.dump(res, open(HERE / "results.json", "w"), indent=2)
print(json.dumps(res[name], indent=1))
