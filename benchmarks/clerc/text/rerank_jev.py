"""Rerank the top-20 "bm25 +graph" candidates of every CLERC test query with Jev vs MiniLM.

Same candidates for every system, so the comparison is paired. Candidates: RRF(bm25 doc list,
one-hop graph expansion of its top 10) from bench.py (holdout edges removed), cut to 20. The
dense "hybrid" list exists only for bench.py's 500-query sample, so bm25+graph is the candidate
generator for the full sets. Candidate text = case name + its best BM25-pool chunk for the query
(else its first chunk; name only when the case has no CLERC text), cut to ``--chars``.

Systems: none (candidate order), minilm (ms-marco-MiniLM-L-6-v2 over query window x candidate
text), jev (one Decisions request per query: one Noul per candidate + the 21-way Choice
{candidates, none} asked twice with option order reversed; score = mean(Noul, avg Choice prob)).
Metrics over the 20-list: R@5, R@10, nDCG@10, MRR (gold outside the 20 = miss for all systems).

Resumable: per-query rows appended to ``--work/rerank-{split}.jsonl``.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from bench import Corpus, Graph, window  # noqa: E402
from legal_graph.benchmark import mrr, ndcg_at, recall_at  # noqa: E402
from legal_graph.jev import JevClient, average_choice, choice_questions, noul  # noqa: E402
from legal_graph.search.hybrid import rrf  # noqa: E402

RERANK = "cross-encoder/ms-marco-MiniLM-L-6-v2"
NOUL_I = ("The passage cites a case whose name was replaced by REDACTED. Is candidate {c} the case that the "
          "REDACTED citation refers to?")
CHOICE_I = ("The passage cites a case whose name was replaced by REDACTED. Which candidate is the case that the "
            "REDACTED citation refers to? Answer none if no candidate fits.")


def m(ranked, gold):
    return {"R@5": recall_at(ranked, gold, 5), "R@10": recall_at(ranked, gold, 10),
            "nDCG@10": ndcg_at(ranked, gold, 10), "MRR": mrr(ranked, gold)}


def jev_request(passage: str, cands: list[tuple[str, str]]):
    keys = [f"c{j:02d}" for j in range(len(cands))]
    state = {"passage": passage, "candidates": {k: {"case": n, "excerpt": t} for k, (n, t) in zip(keys, cands)}}
    qs = {f"n_{k}": {"type": "noul", "instructions": NOUL_I.format(c=k)} for k in keys}
    opts = {k: n for k, (n, _) in zip(keys, cands)}
    opts["none"] = "None of the candidates."
    qs.update(choice_questions("pick", CHOICE_I, opts))
    return keys, state, qs


def jev_scores(resp: dict, keys) -> tuple[list[float], list[float], list[float]]:
    ans = resp["answers"]
    n = [noul(ans, f"n_{k}") for k in keys]
    c = average_choice(ans, "pick", keys + ["none"])
    ch = [c[k] for k in keys]
    return [(a + b) / 2 for a, b in zip(n, ch)], n, ch


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chunks", type=Path, required=True)
    ap.add_argument("--edges", type=Path, required=True)
    ap.add_argument("--clerc-dir", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True, help="bench.py work dir (bm25-*.jsonl) + outputs")
    ap.add_argument("--splits", nargs="*", default=["indirect", "direct"])
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--chars", type=int, default=600)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-jev", action="store_true")
    a = ap.parse_args(argv)
    corpus = Corpus(a.chunks)
    first = {}
    for c, d in enumerate(corpus.cdoc.tolist()):
        first.setdefault(corpus.node[d], c)
    graph = Graph(a.edges)
    rr = __import__("sentence_transformers").CrossEncoder(RERANK, device="cpu", max_length=512)
    client = None if a.no_jev else JevClient(record=a.work / "jev-rerank-fixtures.jsonl")
    lock = threading.Lock()
    for split in a.splits:
        qs = [json.loads(l) for l in open(ROOT / f"benchmarks/clerc/{split}/queries.jsonl", encoding="utf-8")]
        csv.field_size_limit(10 ** 9)
        text = {r[0]: r[1] for r in csv.reader(open(a.clerc_dir / f"test.single-removed.{split}.tsv",
                                                      encoding="utf-8"), delimiter="\t")}
        graph.build({(e[0], e[1]) for q in qs for e in q.get("holdout_edges", []) if e[2] == "cites"})
        bm = {}
        for line in open(a.work / f"bm25-{split}.jsonl"):
            x = json.loads(line)
            bm[x["i"]] = x
        out = a.work / f"rerank-{split}.jsonl"
        done = {json.loads(l)["i"] for l in open(out)} if out.exists() else set()
        todo = [i for i in range(len(qs)) if i not in done][: a.limit or None]
        print(split, "todo", len(todo), flush=True)

        def prep(i):
            q = qs[i]
            b = bm[i]["docs"]
            cand = rrf([b, graph.expand(b[:10])])[: a.k]
            _, best = corpus.docs_of(bm[i]["pool"], 100)
            ct = []
            for d in cand:
                c = best.get(d, first.get(d))
                ct.append((d, corpus.text(c)[: a.chars] if c is not None else ""))
            return q, window(text[q["query_id"].split("-", 2)[2]]), cand, ct

        def work(i):
            q, qt, cand, ct = prep(i)
            gold = set(q["gold"])
            sc = rr.predict([(qt, f"{n}. {t}") for n, t in ct], batch_size=20, show_progress_bar=False)
            mini = [d for _, d in sorted(zip(sc.tolist(), cand), key=lambda x: -x[0])]
            row = {"i": i, "id": q["query_id"], "split": split, "gold": sorted(gold), "cand": cand,
                   "minilm_scores": [round(float(s), 4) for s in sc], "none": m(cand, gold), "minilm": m(mini, gold),
                   "gold_in_cand": int(bool(gold & set(cand)))}
            if client:
                keys, state, qq = jev_request(qt, ct)
                try:
                    r = client.decide(state, qq)
                except Exception as e:  # noqa: BLE001  (left for the resume pass)
                    print("error", i, str(e)[:120], flush=True)
                    return i
                s, n, ch = jev_scores(r, keys)
                order = sorted(range(len(cand)), key=lambda j: -s[j])
                row.update(jev=m([cand[j] for j in order], gold),
                           jev_noul=m([cand[j] for j in sorted(range(len(cand)), key=lambda j: -n[j])], gold),
                           jev_choice=m([cand[j] for j in sorted(range(len(cand)), key=lambda j: -ch[j])], gold),
                           jev_scores=s, jev_noul_scores=n, jev_choice_scores=ch,
                           jev_none=average_choice(r["answers"], "pick", keys + ["none"])["none"],
                           jev_model=r.get("model"), cost=r.get("usage", {}).get("cost"),
                           latency_ms=round(r["_latency_ms"], 1))
            with lock, open(out, "a") as f:
                f.write(json.dumps(row) + "\n")
            return i

        with ThreadPoolExecutor(a.workers) as ex:
            for n_done, _ in enumerate(ex.map(work, todo), 1):
                if n_done % 100 == 0:
                    print(split, n_done, client.usage() if client else "", flush=True)
    if client:
        print("usage", json.dumps(client.usage()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
