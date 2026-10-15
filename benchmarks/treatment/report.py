"""Summarise Jev treatment labels: distribution, order consistency, hand-check agreement,
calibration (temperature fit on the hand-check, 5-fold CV), and assertion rows (PR #38 schema).

Usage: python report.py edges.jsonl labels.jsonl assertions.jsonl.gz
Writes benchmarks/treatment/results.json and a gzipped JSONL of assertion rows.
"""
from __future__ import annotations
import collections, datetime, gzip, json, sys, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legal_graph.answer_check import apply_temperature, expected_calibration_error as ece, fit_temperature  # noqa: E402

HERE = Path(__file__).parent
NS = uuid.UUID("6f1c3c1e-6c55-4e7a-9d0f-0b1e7a7e0c28")


def main(edges_p, labels_p, out_p) -> int:
    edges = {r["edge_id"]: r for r in map(json.loads, open(edges_p))}
    labels = {}
    for line in open(labels_p):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        labels.setdefault(r["edge_id"], r)  # first row wins (extract once emitted duplicate edge ids)
    L = [r for r in labels.values() if r["edge_id"] in edges]
    dist = collections.Counter(r["label"] for r in L)
    consistent = sum(r["per_order"][0] == r["per_order"][1] for r in L) / len(L)
    hc = [json.loads(l) for l in open(HERE / "handcheck.jsonl")]
    per_lab = collections.defaultdict(list)
    for h in hc:
        per_lab[h["jev_label"]].append(h["agree"])
    acc_by = {k: sum(v) / len(v) for k, v in per_lab.items()}
    weighted = sum(dist[k] / len(L) * acc_by.get(k, 0) for k in dist)
    p = [h["raw_prob"] for h in hc]
    y = [h["agree"] for h in hc]
    k = 5
    cal = [0.0] * len(p)
    for f in range(k):
        tr = [i for i in range(len(p)) if i % k != f]
        t = fit_temperature([p[i] for i in tr], [y[i] for i in tr])
        for i in range(f, len(p), k):
            cal[i] = apply_temperature(p[i], t)
    T = fit_temperature(p, y)
    thr = 0.5
    for t in sorted({round(apply_temperature(x, T), 3) for x in p}):
        sel = [a for x, a in zip(p, y) if apply_temperature(x, T) >= t]
        if len(sel) >= 10 and sum(sel) / len(sel) >= 0.9:
            thr = t
            break
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    n_acc = 0
    with gzip.open(out_p, "wt") as f:
        for r in L:
            e = edges[r["edge_id"]]
            c = apply_temperature(r["raw_prob"], T)
            n_acc += c >= thr
            f.write(json.dumps({
                "assertion_id": str(uuid.uuid5(NS, r["edge_id"] + "|" + (r["model_version"] or ""))),
                "work_id": f"cap:{e['src_cap_id']}", "decision_type": "citation_treatment",
                "value": {"edge_id": r["edge_id"], "src": e["src"], "dst_cite": e["dst_cite"],
                          "dst_cap_ids": e["dst_cap_ids"], "label": r["label"], "probs": r["probs"]},
                "valid_from": e["src_date"], "confidence": round(c, 4), "decider": "typesafe/jev-1.14",
                "decider_version": r["model_version"], "raw_prob": round(r["raw_prob"], 4),
                "calibrated_prob": round(c, 4), "threshold": thr, "accepted": bool(c >= thr), "decided_at": now},
                ensure_ascii=True) + "\n")
    res = {"edges_total": len(edges), "edges_labelled": len(L), "label_distribution": dict(dist.most_common()),
           "order_consistency": consistent, "api_cost_usd": round(sum(float(r["cost"] or 0) for r in L), 4),
           "cost_per_1k_calls_usd": round(1000 * sum(float(r["cost"] or 0) for r in L) / len(L), 4),
           "handcheck": {"n": len(hc), "agreement": sum(y) / len(y), "agreement_by_jev_label": acc_by,
                         "population_weighted_agreement": weighted,
                         "ece_raw": ece(p, y), "ece_cal_cv": ece(cal, y), "temperature": T},
           "threshold_calibrated": thr, "accepted": int(n_acc), "accepted_share": n_acc / len(L),
           "model_versions": dict(collections.Counter(r["model_version"] for r in L))}
    (HERE / "results.json").write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
