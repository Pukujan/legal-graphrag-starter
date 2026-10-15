# Colorado retrieval benchmarks (issue #41)

These benchmarks are public stand-ins until the client's private qrels arrive. All three share one
harness, `legal_graph/retrieval_bench.py`. It has frozen sha256 inputs, pluggable systems
(`seeds`, `bm25`, `graph`, `fuse`, `plugin`), write-once run directories and a `MANIFEST.sha256`.
Paired differential testing is in `legal_graph/bench_diff.py`.

| Set | Queries | Corpus | Gold |
| --- | --- | --- | --- |
| `cap-direct/`, `cap-indirect/` | 2,000 test + 1,000 dev each | 40,897 Colorado CAP opinions | cited Colorado CAP case |
| `housing_qa/` | 192 (Colorado) | 30,056 C.R.S. sections (reglab statutes.tsv) | C.R.S. sections |
| `clerc-direct/`, `clerc-indirect/` | 1,498 / 2,851 (PR #25 frozen files) | PR #25 graph; BM25 rankings from PR #42 | CLERC doc qrels |

## 1. Self-built Colorado case-law citation set (`build_cap_citations.py`)

**Source.** CAP static bulk, CC0. It covers every Colorado-jurisdiction case in Colo., Colo. App., Colo. N.P., Colo. L. Rep., P.2d and P.3d: 40,897 cases. CAP coverage ends in 2019.

**Fetch.** `fetch_cap.py meta|text DATA_ROOT`. It streams and resumes, and skips cases already on disk.

**Graph.** Edges are CAP `cites_to` links between Colorado cases: 237,405 CITES edges.

**Queries.** Each query is a paragraph of a majority opinion (at least 250 characters) with at least one eyecite full case citation that resolves to an earlier Colorado case. Resolution goes through the reporter or public-domain citations CAP lists for each case, and only unique matches count.
- **Target:** one per paragraph, chosen deterministically.
- **Masking:** every occurrence of the target is masked:
  - the full cite with its caption, pin cite and court/year;
  - glued parallel cites;
  - short cites in the same volume/reporter, with their antecedent;
  - distinctive party-name words (replaced with `[CASE]`).
- **Dropped:** paragraphs where the target is the subsequent history of a case named in the same paragraph.
- **direct:** no other resolvable Colorado case is cited in the paragraph.
- **indirect:** other cited Colorado cases remain in the text and are the query's `seeds`.

**Temporal holdout.**
- Test: citing opinions decided on or after 2015-01-01. Candidates: 5,649 direct and 3,966 indirect; 2,000 of each are sampled.
- Dev: opinions from 2008 to 2014. Candidates: 10,085 direct and 7,457 indirect; 1,000 of each are sampled.

**Leakage control.**
- Every query opinion's outgoing CITES edges are removed before the graph index is built (`held_out_opinion`).
- The citing opinion itself is excluded from retrieval.
- Corpus docs and graph results decided after the citing opinion are dropped.

**Hand check.** 100 items, 0% hard errors and 5% minor defects. See `HANDCHECK.md`.

## 2. housing_qa Colorado (`build_housing_qa.py`)

- **Source:** reglab/housing_qa, CC-BY-SA-4.0. Questions and gold are copied from that dataset into `housing_qa/queries.jsonl` under the same licence, with attribution to RegLab / Stanford.
- **Coverage:** 192 Colorado questions and 572 gold statute references. All of them are in statutes.tsv, which has 30,056 Colorado sections.
- **Structure graph:** `part_of` links (section to article to title) plus 44,292 in-text C.R.S. cross-reference `cites` edges.
- **Dense model:** `dense_plugin.py` runs BAAI/bge-small-en-v1.5 over the first 2,000 characters of each section, on CPU.

## 3. CLERC rerun

The same frozen queries and the same graph as PR #25, with matching sha256. Seeds and seeds+graph reproduce the PR #25 run exactly on R@k and nDCG. MRR differs slightly because this harness ranks to depth 100 (PR #25 used 20).

`bm25_pr42` is PR #42's chunk BM25 over CLERC opinion text, loaded with `precomputed_plugin.py`. PR #42's own "+graph" uses a different expansion (chunk-score hops), so its numbers are not directly comparable to the graph rows here.

## Reproduce
```
uv venv --python 3.12 && uv pip install -r requirements-dev.txt eyecite   # + requirements-search.txt for dense
python benchmarks/colorado/fetch_cap.py meta $D && python benchmarks/colorado/fetch_cap.py text $D
python benchmarks/colorado/build_cap_citations.py build $D
python benchmarks/colorado/build_housing_qa.py questions.json statutes.tsv $D
PYTHONPATH=benchmarks/colorado python -m legal_graph.retrieval_bench run --config benchmarks/colorado/cap-indirect/config.json --data-root $D
python -m legal_graph.bench_diff benchmarks/runs/<run> --queries benchmarks/colorado/cap-indirect/queries.jsonl --pairs bm25:bm25+graph_rrf
```

## Results

**Reading the tables.**
- **Recall@k** is the fraction of gold retrieved in the top k.
- **W/T/L** counts queries where system a is better, tied, or worse than system b.
- **p** is a two-sided paired sign-flip permutation test with 10,000 resamples. Bootstrap 95% CIs are in each `*-diff/diff.json`.
- Per-query metrics: `*-diff/per_query.tsv`. Five example queries per side where only one system found the gold: `diff.json` → `examples`.
- **System names:**
  - `graph_append` = top-n first-stage results, then graph results, then the rest.
  - `graph_rrf` = reciprocal-rank fusion of the first-stage list and the graph list.
  - The graph ranks expansions by global citation in-degree, and that ranking does not use the query.

**housing_qa Colorado (192 q)** (`housingqa-co-6fe464223ef1a801`)

| system | R@1 | R@5 | R@10 | R@20 | ndcg@10 | mrr |
|---|---|---|---|---|---|---|
| bm25 | 0.142 | 0.301 | 0.410 | 0.477 | 0.300 | 0.335 |
| dense_bge_small | 0.078 | 0.209 | 0.316 | 0.413 | 0.222 | 0.263 |
| hybrid_rrf | 0.156 | 0.348 | 0.425 | 0.530 | 0.339 | 0.408 |
| bm25+graph_append | 0.142 | 0.301 | 0.301 | 0.301 | 0.254 | 0.311 |
| bm25+graph_rrf | 0.124 | 0.256 | 0.301 | 0.410 | 0.222 | 0.249 |
| hybrid+graph_append | 0.156 | 0.348 | 0.348 | 0.355 | 0.306 | 0.393 |

| a vs b | R@10 a / b | W/T/L (a better / tie / b better) | p (perm) | MRR a / b | W/T/L | p | found only by a / b (R@10) |
|---|---|---|---|---|---|---|---|
| bm25 vs bm25+graph_append | 0.410 / 0.301 | 44/148/0 | 0.0001 | 0.335 / 0.311 | 58/131/3 | 0.0001 | 29 / 0 |
| bm25 vs bm25+graph_rrf | 0.410 / 0.301 | 44/148/0 | 0.0001 | 0.335 / 0.249 | 116/75/1 | 0.0001 | 29 / 0 |
| bm25 vs dense_bge_small | 0.410 / 0.316 | 51/104/37 | 0.0046 | 0.335 / 0.263 | 82/44/66 | 0.0225 | 39 / 17 |
| bm25 vs hybrid_rrf | 0.410 / 0.425 | 24/121/47 | 0.6207 | 0.335 / 0.408 | 44/74/74 | 0.0046 | 19 / 21 |
| dense_bge_small vs hybrid_rrf | 0.316 / 0.425 | 14/128/50 | 0.0001 | 0.263 / 0.408 | 29/60/103 | 0.0001 | 4 / 28 |
| hybrid_rrf vs hybrid+graph_append | 0.425 / 0.348 | 38/154/0 | 0.0001 | 0.408 / 0.393 | 50/132/10 | 0.0001 | 11 / 0 |

**Colorado CAP direct, test (2,000 q)** (`capco-direct-test-401a3f6df39480bc`)

| system | R@5 | R@10 | R@20 | ndcg@10 | mrr |
|---|---|---|---|---|---|
| bm25 | 0.469 | 0.543 | 0.617 | 0.388 | 0.348 |
| bm25+graph_append | 0.469 | 0.543 | 0.549 | 0.388 | 0.340 |
| bm25+graph_rrf | 0.357 | 0.472 | 0.549 | 0.261 | 0.203 |

| a vs b | R@10 a / b | W/T/L (a better / tie / b better) | p (perm) | MRR a / b | W/T/L | p | found only by a / b (R@10) |
|---|---|---|---|---|---|---|---|
| bm25 vs bm25+graph_append | 0.543 / 0.543 | 0/2000/0 | 1.0000 | 0.348 / 0.340 | 449/1499/52 | 0.0001 | 0 / 0 |
| bm25 vs bm25+graph_rrf | 0.543 / 0.472 | 148/1846/6 | 0.0001 | 0.348 / 0.203 | 1436/532/32 | 0.0001 | 148 / 6 |

**Colorado CAP indirect, test (2,000 q)** (`capco-indirect-test-1f3689bc25e69c7a`)

| system | R@5 | R@10 | R@20 | ndcg@10 | mrr |
|---|---|---|---|---|---|
| bm25 | 0.281 | 0.385 | 0.469 | 0.229 | 0.193 |
| bm25+graph_append | 0.281 | 0.385 | 0.398 | 0.229 | 0.184 |
| bm25+graph_rrf | 0.181 | 0.290 | 0.398 | 0.142 | 0.110 |
| seeds | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| seeds+graph | 0.015 | 0.040 | 0.067 | 0.016 | 0.014 |
| seeds+graph_rrf_bm25 | 0.172 | 0.290 | 0.435 | 0.128 | 0.095 |

| a vs b | R@10 a / b | W/T/L (a better / tie / b better) | p (perm) | MRR a / b | W/T/L | p | found only by a / b (R@10) |
|---|---|---|---|---|---|---|---|
| bm25 vs bm25+graph_append | 0.385 / 0.385 | 0/2000/0 | 1.0000 | 0.193 / 0.184 | 571/1312/117 | 0.0001 | 0 / 0 |
| bm25 vs bm25+graph_rrf | 0.385 / 0.290 | 208/1773/19 | 0.0001 | 0.193 / 0.110 | 1314/613/73 | 0.0001 | 208 / 19 |
| seeds vs seeds+graph | 0.000 / 0.040 | 0/1920/80 | 0.0001 | 0.000 / 0.014 | 0/1543/457 | 0.0001 | 0 / 80 |
| bm25 vs seeds+graph_rrf_bm25 | 0.385 / 0.290 | 337/1515/148 | 0.0001 | 0.193 / 0.095 | 1052/632/316 | 0.0001 | 337 / 148 |

**CLERC direct (1,498 q)** (`clerc-direct-rerun-8b40e33769d753f2`)

| system | R@5 | R@10 | R@20 | ndcg@10 | mrr |
|---|---|---|---|---|---|
| seeds | 0.039 | 0.040 | 0.040 | 0.029 | 0.025 |
| seeds+graph | 0.062 | 0.109 | 0.195 | 0.054 | 0.048 |
| bm25_pr42 | 0.077 | 0.107 | 0.152 | 0.059 | 0.050 |
| bm25_pr42+graph_append | 0.077 | 0.107 | 0.198 | 0.059 | 0.059 |
| bm25_pr42+graph_rrf | 0.075 | 0.121 | 0.198 | 0.056 | 0.051 |
| bm25_pr42+seeds+graph_rrf | 0.149 | 0.200 | 0.266 | 0.108 | 0.089 |

| a vs b | R@10 a / b | W/T/L (a better / tie / b better) | p (perm) | MRR a / b | W/T/L | p | found only by a / b (R@10) |
|---|---|---|---|---|---|---|---|
| seeds vs seeds+graph | 0.040 / 0.109 | 0/1394/104 | 0.0001 | 0.025 / 0.048 | 0/941/557 | 0.0001 | 0 / 104 |
| bm25_pr42 vs bm25_pr42+graph_append | 0.107 / 0.107 | 0/1498/0 | 1.0000 | 0.050 / 0.059 | 215/719/564 | 0.0001 | 0 / 0 |
| bm25_pr42 vs bm25_pr42+graph_rrf | 0.107 / 0.121 | 44/1388/66 | 0.0447 | 0.050 / 0.051 | 370/593/535 | 0.7904 | 44 / 66 |
| bm25_pr42 vs bm25_pr42+seeds+graph_rrf | 0.107 / 0.200 | 18/1322/158 | 0.0001 | 0.050 / 0.089 | 126/864/508 | 0.0001 | 18 / 158 |

**CLERC indirect (2,851 q)** (`clerc-indirect-rerun-6d2a9c027aa81d8d`)

| system | R@5 | R@10 | R@20 | ndcg@10 | mrr |
|---|---|---|---|---|---|
| seeds | 0.046 | 0.052 | 0.054 | 0.034 | 0.028 |
| seeds+graph | 0.059 | 0.102 | 0.182 | 0.052 | 0.048 |
| bm25_pr42 | 0.091 | 0.127 | 0.169 | 0.067 | 0.054 |
| bm25_pr42+graph_append | 0.091 | 0.127 | 0.210 | 0.067 | 0.061 |
| bm25_pr42+graph_rrf | 0.072 | 0.132 | 0.210 | 0.059 | 0.050 |
| bm25_pr42+seeds+graph_rrf | 0.165 | 0.220 | 0.279 | 0.117 | 0.094 |

| a vs b | R@10 a / b | W/T/L (a better / tie / b better) | p (perm) | MRR a / b | W/T/L | p | found only by a / b (R@10) |
|---|---|---|---|---|---|---|---|
| seeds vs seeds+graph | 0.052 / 0.102 | 0/2707/144 | 0.0001 | 0.028 / 0.048 | 0/1885/966 | 0.0001 | 0 / 144 |
| bm25_pr42 vs bm25_pr42+graph_append | 0.127 / 0.127 | 0/2851/0 | 1.0000 | 0.054 / 0.061 | 452/1470/929 | 0.0001 | 0 / 0 |
| bm25_pr42 vs bm25_pr42+graph_rrf | 0.127 / 0.132 | 102/2634/115 | 0.4176 | 0.054 / 0.050 | 808/1173/870 | 0.1497 | 102 / 115 |
| bm25_pr42 vs bm25_pr42+seeds+graph_rrf | 0.127 / 0.220 | 37/2513/301 | 0.0001 | 0.054 / 0.094 | 235/1691/925 | 0.0001 | 37 / 301 |

## Findings
1. **Lexical retrieval is the strong baseline on the Colorado sets.** BM25 R@10 is 0.543 on direct and 0.385 on indirect.
2. **Query-independent graph expansion does not help a text retriever on any Colorado set.**
   - Appending graph results after the top 10 leaves R@10 unchanged and lowers MRR.
   - RRF-fusing them costs about 0.07–0.10 R@10 (p < 0.001).
   - On housing_qa the structural/cross-reference graph also hurts.
3. **The graph does help when the only signal is the citations already in the text.**
   - Colorado indirect seeds+graph reaches R@10 0.040, from 0 for seeds alone: 80 wins and no losses.
   - CLERC seeds plus PR #42 BM25, RRF-fused, nearly doubles BM25 R@10: 0.107 → 0.200 on direct, 0.127 → 0.220 on indirect, p < 0.001.
   - Next step: query-aware graph scoring (personalised PageRank from the BM25 hits, text-similarity reranking of graph candidates) rather than in-degree.
4. **housing_qa:** hybrid RRF beats BM25 on MRR (0.408 vs 0.335, p = 0.005), but not significantly on R@10 (p = 0.62). Dense alone is worse than BM25.

## Caveats
- **No reranker or dense run on the Colorado CAP set.** CPU time was shared with another worker: bge-small over 30k statutes took about 45 minutes. The plugin hook is ready (`dense_plugin.py`, or PR #42's `legal_graph.search`).
- **CAP stops in 2019,** so "post-2015" test opinions are 2015–2019.
- **One gold per query,** although many boilerplate propositions have several valid authorities.

## JusticeDAO patent-legal-ir-graphrag: nothing to copy for evaluation
Checked on 2026-10-14 through the HF API.
- **Contents:** a corpus (2,174 CFR 37, MPEP and 35 U.S.C. documents), BM25 postings, 256-d vectors, a graph and routing indexes.
- **No evaluation data:** no queries, no qrels and no metrics.
- **Its "evaluate" suite** is 9 hand-written pass/fail rule lookups. It lives in the external `ipfs_datasets_py` repo, not in the dataset.
- **Reusable ideas:** citation-cue boosting (explicit "37 CFR 1.97" cues and a topic-to-section lexicon), and CID-pinned manifests, which are similar to our sha256 freezing.
