"""Label citation treatment of Colorado-to-Colorado edges with a Jev Choice.

Labels: followed, distinguished, criticized, questioned, overruled, cited-neutral. The Choice is
asked twice per request (given and reversed option order) and the probabilities averaged.
Resumable: rows are appended to ``--out``; edges already there are skipped.
Usage: OPENROUTER_API_KEY=... python label_jev.py edges.jsonl labels.jsonl [--workers 16]
"""
from __future__ import annotations
import argparse, json, sys, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legal_graph.jev import JevClient, average_choice, choice_questions  # noqa: E402

LABELS = {
    "followed": "The citing court relies on the cited case as binding or persuasive authority and applies its rule or holding.",
    "distinguished": "The citing court explains why the cited case does not control because its facts or issue differ.",
    "criticized": "The citing court disagrees with or disapproves of the cited case's reasoning without overruling it.",
    "questioned": "The citing court casts doubt on whether the cited case is still good law or correctly decided.",
    "overruled": "The citing court overrules or expressly abrogates the cited case, in whole or in part.",
    "cited-neutral": "The cited case is mentioned for background, procedural history, a general proposition, or 'see also' "
                     "without the court applying, distinguishing, or criticizing it.",
}
INSTR = ("The paragraph is from a Colorado court opinion. How does the citing court treat the case cited at "
         "[[CITE]]...[[/CITE]]?")


def request(row: dict):
    return {"citing_case": row["src"], "cited": row["dst_cite"], "paragraph": row["context"]}, \
        choice_questions("treatment", INSTR, LABELS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("edges", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--record", type=Path)
    a = ap.parse_args(argv)
    done = set()
    if a.out.exists():
        done = {json.loads(l)["edge_id"] for l in open(a.out)}
    rows = [r for r in map(json.loads, open(a.edges)) if r["edge_id"] not in done][: a.limit or None]
    print("todo", len(rows), flush=True)
    client = JevClient(record=a.record)
    lock = threading.Lock()
    names = list(LABELS)

    def one(r):
        state, qs = request(r)
        try:
            resp = client.decide(state, qs)
        except Exception as e:  # noqa: BLE001  (left for the resume pass)
            print("error", r["edge_id"], str(e)[:120], flush=True)
            return
        p = average_choice(resp["answers"], "treatment", names)
        lab = max(p, key=p.get)
        out = {"edge_id": r["edge_id"], "label": lab, "raw_prob": p[lab], "probs": p,
               "per_order": [resp["answers"][f"treatment_p{k}"]["choice"] for k in range(2)],
               "model_version": resp.get("model"), "cost": resp.get("usage", {}).get("cost"),
               "latency_ms": round(resp["_latency_ms"], 1)}
        with lock, open(a.out, "a") as f:
            f.write(json.dumps(out) + "\n")

    with ThreadPoolExecutor(a.workers) as ex:
        for n, _ in enumerate(ex.map(one, rows), 1):
            if n % 2000 == 0:
                print(n, json.dumps(client.usage()), flush=True)
    print("usage", json.dumps(client.usage()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
