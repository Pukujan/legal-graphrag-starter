"""Text retrieval over CLERC opinion text, with vs without one-hop citation-graph expansion.

Queries: the frozen CLERC test single-removed queries from #25
(``benchmarks/clerc/{indirect,direct}/queries.jsonl``); query text is CLERC's passage
with the cited case replaced by REDACTED. Gold = the CLERC doc-qrel case. Queries whose
gold has no text/graph node count as misses (same denominator as #25).

Retrievers (each with and without expansion):
  bm25     hashed BM25 over every chunk of every graph case that has CLERC text.
  hybrid   RRF(bm25, dense). Dense = bge-small-en-v1.5 cosine over the chunks in the
           query's BM25 top-``pool`` chunks only (CPU budget: the full corpus is not
           embedded). This is dense re-scoring of the BM25 candidate pool.
  rerank   hybrid, then cross-encoder ms-marco-MiniLM-L-6-v2 over the top 30 docs
           (best chunk per doc).

Graph expansion (one hop): neighbours (cites, both directions) of the top ``hops_from``
text hits are scored by sum 1/(60+rank of the hit that links to them), ties by global
in-degree, and fused with the text ranking by RRF. ``+seeds`` also expands from the
case citations already in the query text (the #25 seeds).

Leak control (as #25): the frozen ``holdout_edges`` (edges from any case that cites the
gold and every seed, i.e. a possible source opinion of the passage) are removed from the
graph before expansion. ``--no-holdout`` gives the upper bound.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from legal_graph.benchmark import mrr, ndcg_at, recall_at  # noqa: E402
from legal_graph.search.hybrid import rrf  # noqa: E402
from sparse import HashedBM25, build  # noqa: E402

KS = (5, 10, 20)
DENSE = "BAAI/bge-small-en-v1.5"
DENSE_QPREFIX = "Represent this sentence for searching relevant passages: "
RERANK = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class Corpus:
    def __init__(self, d: Path):
        self.docs = json.loads((d / "docs.json").read_text())
        self.node = [x["id"] for x in self.docs]
        self.offsets = np.load(d / "offsets.npy")
        self.cdoc = np.load(d / "chunk_doc.npy")
        self.f = open(d / "texts.bin", "rb")

    def text(self, c: int) -> str:
        self.f.seek(int(self.offsets[c]))
        return self.f.read(int(self.offsets[c + 1] - self.offsets[c])).decode("utf-8", "replace")

    def docs_of(self, chunks, k: int = 100):
        seen, out, best = set(), [], {}
        for c in chunks:
            d = self.node[self.cdoc[c]]
            if d not in seen:
                seen.add(d)
                out.append(d)
                best[d] = int(c)
                if len(out) >= k:
                    break
        return out, best


def window(t: str, w: int = 1000) -> str:
    i = t.find("REDACTED")
    i = len(t) // 2 if i < 0 else i
    return t[max(0, i - w // 2): i + w // 2]


class Graph:
    def __init__(self, edges_path: Path):
        self.adj, self.indeg = defaultdict(set), defaultdict(int)
        self.edges = []
        for line in open(edges_path, encoding="utf-8"):
            e = json.loads(line)
            if e["kind"] == "cites":
                self.edges.append((e["src"], e["dst"]))

    def build(self, holdout: set):
        self.adj.clear()
        self.indeg.clear()
        for s, t in self.edges:
            if (s, t) in holdout:
                continue
            self.adj[s].add(t)
            self.adj[t].add(s)
            self.indeg[t] += 1

    def expand(self, sources, k: int = 100):
        score = defaultdict(float)
        src = set(sources)
        for r, s in enumerate(sources):
            for n in self.adj.get(s, ()):
                if n not in src:
                    score[n] += 1.0 / (60 + r + 1)
        return sorted(score, key=lambda n: (-score[n], -self.indeg.get(n, 0), n))[:k]


def metrics(ranked, gold):
    return {**{f"R@{k}": recall_at(ranked, gold, k) for k in KS}, "nDCG@10": ndcg_at(ranked, gold, 10),
            "MRR": mrr(ranked, gold)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chunks", type=Path, required=True)
    ap.add_argument("--edges", type=Path, required=True)
    ap.add_argument("--clerc-dir", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True, help="cache/checkpoint dir")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--splits", nargs="*", default=["indirect", "direct"])
    ap.add_argument("--sample", type=int, default=500, help="queries per split for dense/rerank")
    ap.add_argument("--pool", type=int, default=100, help="BM25 chunks per query embedded for dense")
    ap.add_argument("--hops-from", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20261014)
    ap.add_argument("--no-holdout", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    a.work.mkdir(parents=True, exist_ok=True)
    corpus = Corpus(a.chunks)
    t0 = time.time()
    bm25 = HashedBM25(build(a.chunks / "texts.bin", corpus.offsets, a.work / "bm25", workers=a.workers))
    print(f"bm25 ready {time.time() - t0:.0f}s chunks={bm25.n}", flush=True)
    graph = Graph(a.edges)
    has_text = set(corpus.node)
    report = {"args": {k: str(v) for k, v in vars(a).items()}, "dense_model": DENSE, "reranker": RERANK,
              "corpus": {"docs": len(corpus.node), "chunks": int(bm25.n)}, "splits": {}}
    emb_model = rr_model = None
    for split in a.splits:
        qs = [json.loads(l) for l in open(ROOT / f"benchmarks/clerc/{split}/queries.jsonl", encoding="utf-8")]
        csv.field_size_limit(10 ** 9)
        text = {r[0]: r[1] for r in csv.reader(open(a.clerc_dir / f"test.single-removed.{split}.tsv",
                                                      encoding="utf-8"), delimiter="\t")}
        holdout = set() if a.no_holdout else {(e[0], e[1]) for q in qs for e in q.get("holdout_edges", [])
                                              if e[2] == "cites"}
        graph.build(holdout)
        sample = set(random.Random(a.seed).sample(range(len(qs)), min(a.sample, len(qs))))
        ck = a.work / f"bm25-{split}.jsonl"
        done = {}
        if ck.exists():
            for l in open(ck):
                x = json.loads(l)
                done[x["i"]] = x
        with open(ck, "a") as f:
            for i, q in enumerate(qs):
                if i in done:
                    continue
                qt = text[q["query_id"].split("-", 2)[2]]
                top, _ = bm25.search(qt, 1000)
                docs, _ = corpus.docs_of(top, 100)
                x = {"i": i, "docs": docs, "pool": [int(c) for c in top[:a.pool]]}
                f.write(json.dumps(x) + "\n")
                done[i] = x
                if i % 250 == 0:
                    print(split, "bm25", i, flush=True)
        rows = defaultdict(list)
        if a.sample:
            emb_model = emb_model or __import__("sentence_transformers").SentenceTransformer(DENSE, device="cpu")
            emb_model.max_seq_length = 256
            rr_model = rr_model or __import__("sentence_transformers").CrossEncoder(RERANK, device="cpu",
                                                                                     max_length=512)
            cache_p = a.work / "emb-cache.npz"
            cache = {}
            if cache_p.exists():
                z = np.load(cache_p)
                cache = dict(zip(z["ids"].tolist(), z["emb"]))
        n_sample_done = 0
        for i, q in enumerate(qs):
            gold, seeds = set(q["gold"]), q["seeds"]
            b = done[i]["docs"]
            lists = {"bm25": b}
            if i in sample:
                qt = window(text[q["query_id"].split("-", 2)[2]])
                pool = done[i]["pool"]
                need = [c for c in pool if c not in cache]
                if need:
                    e = emb_model.encode([corpus.text(c) for c in need], batch_size=32, normalize_embeddings=True,
                                         convert_to_numpy=True, show_progress_bar=False)
                    cache.update(zip(need, e))
                qv = emb_model.encode([DENSE_QPREFIX + qt], normalize_embeddings=True, convert_to_numpy=True,
                                      show_progress_bar=False)[0]
                sims = np.stack([cache[c] for c in pool]) @ qv
                order = [pool[j] for j in np.argsort(-sims, kind="stable")]
                dense, dbest = corpus.docs_of(order, 100)
                hyb = rrf([b, dense])[:100]
                _, bbest = corpus.docs_of(pool, 100)
                top = [d for d in hyb[:30]]
                sc = rr_model.predict([(qt, corpus.text(dbest.get(d, bbest.get(d)))) for d in top
                                       if d in dbest or d in bbest], batch_size=16, show_progress_bar=False)
                top = [d for d in top if d in dbest or d in bbest]
                rer = [d for _, d in sorted(zip(sc, top), key=lambda x: -x[0])] + [d for d in hyb if d not in set(top)]
                lists.update({"bm25@sample": b, "hybrid": hyb, "rerank": rer})
                n_sample_done += 1
                if n_sample_done % 50 == 0:
                    ids = np.fromiter(cache.keys(), dtype=np.int64)
                    np.savez(a.work / "emb-cache.tmp.npz", ids=ids, emb=np.stack([cache[c] for c in ids]))
                    (a.work / "emb-cache.tmp.npz").rename(cache_p)
                    print(split, "dense", n_sample_done, "cached", len(cache), flush=True)
            for name, ranked in lists.items():
                rows[name].append(metrics(ranked, gold))
                exp = graph.expand(ranked[:a.hops_from])
                rows[name + " +graph"].append(metrics(rrf([ranked, exp])[:100], gold))
                exp2 = graph.expand(list(seeds) + ranked[:a.hops_from])
                rows[name + " +graph+seeds"].append(metrics(rrf([ranked, exp2])[:100], gold))
        summ = {name: {"n": len(r), **{m: round(float(np.mean([x[m] for x in r])), 4) for m in r[0]}}
                for name, r in rows.items()}
        report["splits"][split] = {"n_queries": len(qs), "gold_has_text": sum(1 for q in qs if q["gold"] and
                                                                               q["gold"][0] in has_text),
                                   "holdout_edges_removed": len(holdout), "results": summ}
        a.out.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report["splits"][split], indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
