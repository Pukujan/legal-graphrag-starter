# Query-aware graph retrieval: Colorado results (2026-10-15)

Problem: the old graph expansion ranked neighbours by citation in-degree, ignoring the query, so adding the graph lowered recall (CAP direct R@10 .543 -> .472, indirect .385 -> .290). This change seeds personalized PageRank (PPR) or one-hop neighbour scoring from BM25 hits weighted by BM25 score, scores every candidate by its BM25 similarity to the query, and fuses linearly with a weight tuned on the dev split only (CAP 2008-2014 opinions, 1000 direct + 1000 indirect queries; `benchmarks/colorado/tune-dev.json`, 312 configs incl. weighted-RRF variants). Test = CAP opinions >= 2015 (2000 queries each); housing_qa (192 Qs) has no dev split and reuses the CAP params.

Dev picks: neighbour (20 seeds, score-weighted, lam 0.3), dev R@10 .481 -> .526; PPR (alpha .15, 5 seeds, lam 0.5) .481 -> .525. Weighted RRF fusion was worse than linear on dev (best .483).

## Test R@10 / MRR (W/T/L = per-query wins/ties/losses for the second system on R@10; p = paired test from bench_diff)

| System | CAP direct | CAP indirect | housing_qa |
|---|---|---|---|
| BM25 | .543 / .348 | .385 / .193 | .410 / .335 |
| BM25 + old graph RRF | .472 / .203 (sig worse) | .290 / .110 (sig worse) | .301 / .249 (sig worse) |
| BM25 + PPR (new) | .555 / .336, W/T/L 112/1799/89, p=.13 | **.442** / .202, 194/1725/81, p<.001 | .406 / .330, p=.83 |
| BM25 + neighbour (new) | **.570** / .356, 107/1839/54, p<.001 | **.433** / .202, 142/1812/46, p<.001 | .408 / .370 (MRR p=.04) |
| dense bge-small | .121 / .070 | .059 / .029 | .316 / .263 |
| hybrid RRF (BM25+dense) | .442 / .219 (sig worse) | .280 / .121 (sig worse) | .425 / .408 (MRR p=.005) |
| BM25 > Jev rerank top20 | .596 / .475, p<.001 | .440 / .313, p<.001 | .450 / .461, p=.01 |
| BM25+PPR > Jev | INCOMPLETE (1596/2000 failed) | INCOMPLETE (1688/2000 failed) | .442 / .474 |
| hybrid > Jev | NOT RUN (key limit) | NOT RUN (key limit) | **.497 / .513** |

Jev on CAP was cut off when the OpenRouter key hit its fixed total limit ($14.12 of $14.00, "does not reset"); failed queries keep first-stage order, so those two CAP rows equal their inputs and must not be read as results. Responses are cached (`/workspace/coeval-data/jev-cache-capco-*.jsonl`), so re-running the same configs after the limit is raised only pays for the missing calls.

## Recommendation
Ship query-aware graph retrieval (neighbour, lam 0.3) as a retrieval stage for case-citation search: it significantly beats BM25 on both CAP test sets (+2.7 and +4.8 pts R@10, p<.001) with parameters chosen on dev only. Retire the in-degree graph RRF. On statutes (housing_qa) the graph gives no recall gain; keep it as an explanation layer there and use hybrid + Jev rerank. Jev rerank of BM25 gives the largest single gain everywhere it ran; whether graph + Jev stacks on CAP is still open (blocked by key limit).

Runs: `benchmarks/runs/capco-direct-qgraph-test-05d16560c2a5aa67`, `capco-indirect-qgraph-test-bffbf1ca4e530642`, `housingqa-co-qgraph-92db950531920204` (+ `-diff.txt`).
