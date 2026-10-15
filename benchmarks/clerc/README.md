# CLERC public benchmark

CLERC (`jhu-clsp/CLERC` on Hugging Face) is built from Caselaw Access Project (CAP) federal case law, and its document ids are CAP case ids.

**History:** the 2026-10-14 overlap check (`overlap.py`, `overlap-2026-10-14.json`) found only about 0.2% of CLERC's gold citations in the Colorado starter graph. We have since added a federal and Supreme Court extension.

## Federal and Supreme Court extension (`build_graph.py`)
- **Source:** CAP static bulk metadata, `https://static.case.law/<reporter>/<vol>/CasesMetadata.json`, for the reporters U.S., F.2d, F.3d, F.Supp, F.Supp.2d and F.App'x. That is 5,216 volumes and 1,378,342 cases. The files are streamed, and only id/name/court/date/citations/cites_to are kept.
- **Seeds:** the other full case citations in each CLERC query, found with eyecite and resolved to CAP ids by reporter citation.
- **Nodes:**
  - T = every CLERC test gold document plus every resolved seed (4,152 cases);
  - plus every scanned case that cites at least 2 members of T (305,028 co-citing cases);
  - 1,503 of these cases already existed in the starter graph and keep their starter id.
  - **307,677 nodes added.** Each new node carries `cap_id`, `citations`, `reporter`, `court`, `date` and `title`.
- **Edges:** CAP `cites_to` edges between graph nodes where at least one end is in T. **1,151,535 edges added.**
- **Expanded graph:** 427,447 nodes and 1,312,328 edges, written to `data-expanded/` (gitignored). File hashes are in `graph-manifest.json`.
- **Not included:** opinion text. CLERC's 8.2 GB `collection.doc.tsv.gz` has it, keyed by the same CAP ids.

## Frozen queries (`indirect/`, `direct/`)
- **Splits:** test, single-removed. Indirect has 2,851 queries and direct has 1,498. The gold answer is the CAP document from CLERC's doc-level qrels.
- **Coverage:** the gold document is in the graph for 2,507 indirect and 1,390 direct queries. Queries whose gold falls outside the scanned reporters count as misses.
- **Leakage control:** CLERC does not identify the opinion each query was taken from. Any node that cites the gold and all of a query's seeds could be that source, so its edge to the gold is listed in `holdout_edges` and removed before indexing. This is conservative. The `config-noholdout.json` variants run without the removal and give an upper bound.
- **Seeds are not a retriever:** they are the case citations already present in the query text. No hybrid or BM25 retriever is involved. The baseline is that seed list; the graph-augmented list is the seeds followed by `graphrag_retrieve` results (depth 2, both directions, CITES only, ranked by global citation in-degree).

Reproduce:
```
uv run --python 3.12 --with eyecite python benchmarks/clerc/build_graph.py fetch
uv run --python 3.12 --with eyecite python benchmarks/clerc/build_graph.py build
python -m legal_graph.benchmark run --config benchmarks/clerc/indirect/config.json --data-dir data-expanded
```

## Jev reranker on the full CLERC test sets (#43, `text/rerank_jev.py`, `text/rerank_report.py`)
Candidates: top 20 of `bm25 +graph` (bench.py, holdout edges removed) for every query; gold in the top 20 for 39.5% (indirect) /
38.8% (direct), which caps every reranker. The dense hybrid list exists only for bench.py's 500-query sample, so it is not used.
Candidate text = case name + best BM25-pool chunk (else first chunk), 600 chars. Jev: one request per query, a Noul per candidate
("is candidate cXX the case the REDACTED citation refers to?") + a 21-way Choice (candidates + none) in two option orders,
score = mean(Noul, mean Choice). MiniLM = ms-marco-MiniLM-L-6-v2 on the same (query window, name + text) pairs.

| split | system | R@5 | R@10 | nDCG@10 | MRR |
|---|---|---|---|---|---|
| indirect (2,851) | none | 0.164 | 0.262 | 0.137 | 0.109 |
| | MiniLM | 0.093 | 0.190 | 0.086 | 0.069 |
| | Jev (Noul+Choice) | **0.294** | **0.345** | **0.247** | **0.220** |
| | Jev Choice only | 0.308 | 0.346 | 0.256 | 0.230 |
| direct (1,498) | none | 0.146 | 0.244 | 0.125 | 0.100 |
| | MiniLM | 0.085 | 0.168 | 0.076 | 0.063 |
| | Jev (Noul+Choice) | **0.270** | **0.326** | **0.229** | **0.202** |
| | Jev Choice only | 0.286 | 0.333 | 0.241 | 0.216 |

Paired differences (95% paired-bootstrap CI, W/T/L per query, sign-flip permutation p; full detail and only-one-right examples in
`text/rerank-results.json`): indirect Jev vs none MRR +0.112 [+0.100,+0.123] W/T/L 756/1849/246, R@5 +0.131; Jev vs MiniLM MRR
+0.151 [+0.139,+0.163] 895/1809/147; direct Jev vs none MRR +0.103 [+0.088,+0.118] 375/981/142; Jev vs MiniLM MRR +0.139
[+0.124,+0.155] 467/952/79. All p<=0.0002 (5,000 permutations). MiniLM is worse than no rerank on both splits (as in bench.py).
Cost: 4,349 calls, $1.25, $0.287 per 1,000 calls; latency p50/p95 149/220 ms.
Caveat: Jev sees case names and may recognise famous cases from pretraining (MiniLM gets the same names).
