"""Text-retrieval benchmark: each variant changes one thing (chunker, embedder, fusion, reranker).

Datasets
  proxy  : benchmarks/proxy-v1 (held-out citation edges, PROXY gold). Nodes have no
           opinion text, so the indexed text is the node's metadata only:
           id (case name + reporter citation), title, court, date. Query = the
           held-out opinion's own metadata. Corpus = all gold + seed nodes plus a
           seeded random sample of other nodes (CPU budget).
  clerc  : CLERC test single-removed indirect queries (real CLERC doc qrels). Query =
           1000 characters around the REDACTED citation. Corpus = opinion text from
           CLERC collection.doc for the sampled golds plus other CLERC golds and
           random CLERC docs as distractors, truncated to ``max_chars`` per doc.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import time
from pathlib import Path

from ..benchmark import mrr, ndcg_at, recall_at
from .chunking import chunk_doc
from .hybrid import BM25Index, DenseIndex, Reranker, chunks_to_docs, rrf

KS = (5, 10, 20)


def load_proxy(root: Path, n_corpus: int, seed: int, n: int):
    nodes = {}
    for line in open(root / "data/nodes.jsonl"):
        x = json.loads(line)
        nodes[x["id"]] = x
    qs = [json.loads(l) for l in open(root / "benchmarks/proxy-v1/queries.jsonl")][:n]
    keep = {g for q in qs for g in q["gold"]} | {s for q in qs for s in q["seeds"]}
    held = {q["held_out_opinion"] for q in qs}
    others = sorted(set(nodes) - keep - held)
    keep |= set(random.Random(seed).sample(others, max(0, min(len(others), n_corpus - len(keep)))))

    def text(x):
        return " ".join(v for v in (x["id"], x.get("title"), x.get("court"), (x.get("date") or "")[:4]) if v)
    docs = {d: {"text": text(nodes[d]), "meta": nodes[d]} for d in sorted(keep)}
    queries = [{"qid": q["query_id"], "text": text(nodes[q["held_out_opinion"]]), "gold": set(q["gold"])} for q in qs]
    return docs, queries


def _window(t: str, w: int = 1000) -> str:
    i = t.find("REDACTED")
    i = len(t) // 2 if i < 0 else i
    return t[max(0, i - w // 2): i + w // 2]


def load_clerc(clerc_dir: Path, docs_tsv: Path, n: int, n_corpus: int, seed: int, max_chars: int):
    csv.field_size_limit(10 ** 9)
    qrel: dict[str, set[str]] = {}
    for line in open(clerc_dir / "qrels-doc.test.indirect.tsv"):
        a = line.split()
        qrel.setdefault(a[0], set()).add(a[2])
    raw = {}
    for line in open(docs_tsv, encoding="utf-8", errors="replace"):
        did, _, t = line.rstrip("\n").partition("\t")
        raw[did.lstrip("R")] = t
    rows = [r for r in csv.reader(open(clerc_dir / "test.single-removed.indirect.tsv"), delimiter="\t")]
    rows = [r for r in rows if qrel.get(r[0]) and qrel[r[0]] <= raw.keys()]
    rows = sorted(random.Random(seed).sample(rows, min(n, len(rows))))
    gold = {g for r in rows for g in qrel[r[0]]}
    others = sorted(set(raw) - gold)
    pick = gold | set(random.Random(seed).sample(others, max(0, min(len(others), n_corpus - len(gold)))))
    docs = {d: {"text": raw[d][:max_chars], "meta": {"title": raw[d][:80].split("\n")[0]}} for d in sorted(pick)}
    queries = [{"qid": r[0], "text": _window(r[1]), "gold": qrel[r[0]]} for r in rows]
    return docs, queries


def run_variant(docs, queries, v: dict, cache: dict) -> dict:
    ck = (v.get("chunker", "recursive"), v.get("size", 500), v.get("header", False))
    t0 = time.perf_counter()
    if ck not in cache:
        chunks = []
        for d, x in docs.items():
            chunks += chunk_doc(d, x["text"], x["meta"], method=ck[0], size=ck[1], with_header=ck[2])
        cache[ck] = {"chunks": chunks}
    c = cache[ck]
    texts, cdoc = [x["text"] for x in c["chunks"]], [x["doc_id"] for x in c["chunks"]]
    rets = v["retrievers"]
    for r in rets:
        if r not in c:
            c[r] = BM25Index(texts) if r == "bm25" else DenseIndex(r.split(":", 1)[1], texts,
                                                                   max_len=v.get("max_len", 512))
    build_s = time.perf_counter() - t0
    reranker = None
    if v.get("reranker"):
        reranker = cache.get(("rr", v["reranker"])) or Reranker(v["reranker"])
        cache[("rr", v["reranker"])] = reranker
    depth = v.get("depth", 300)
    rows, lat = [], []
    for q in queries:
        t = time.perf_counter()
        lists, best = [], {}
        for r in rets:
            ids, _ = c[r].search([q["text"]], depth)
            for ci in ids[0]:
                best.setdefault(cdoc[ci], texts[ci])
            lists.append(chunks_to_docs(ids[0], None, cdoc, 100))
        ranked = rrf(lists) if len(lists) > 1 else lists[0]
        if reranker:
            top = v.get("rerank_top", 30)
            ranked = reranker.rerank(q["text"], ranked[:top], best) + ranked[top:]
        lat.append((time.perf_counter() - t) * 1000)
        g = q["gold"]
        rows.append({**{f"R@{k}": recall_at(ranked, g, k) for k in KS}, "NDCG@10": ndcg_at(ranked, g, 10),
                     "MRR": mrr(ranked, g)})
    m = {k: round(statistics.mean(r[k] for r in rows), 4) for k in rows[0]}
    return {"variant": v["name"], **m, "p50_ms": round(statistics.median(lat), 1),
            "build_s": round(build_s, 1), "n_queries": len(rows), "n_docs": len(docs), "n_chunks": len(texts)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", choices=["proxy", "clerc"], required=True)
    ap.add_argument("--variants", type=Path, required=True, help="JSON list of variant dicts")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--n-corpus", type=int, default=2000)
    ap.add_argument("--max-chars", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=20261014)
    ap.add_argument("--clerc-dir", type=Path)
    ap.add_argument("--clerc-docs", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    if a.dataset == "proxy":
        docs, queries = load_proxy(root, a.n_corpus, a.seed, a.n)
    else:
        docs, queries = load_clerc(a.clerc_dir, a.clerc_docs, a.n, a.n_corpus, a.seed, a.max_chars)
    cache, results = {}, []
    for v in json.loads(a.variants.read_text()):
        res = run_variant(docs, queries, v, cache)
        print(json.dumps(res), flush=True)
        results.append(res)
        if a.out:
            a.out.write_text(json.dumps({"dataset": a.dataset, "args": {k: str(x) for k, x in vars(a).items()},
                                         "results": results}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
