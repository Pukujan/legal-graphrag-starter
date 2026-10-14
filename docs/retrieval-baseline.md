# Citation retrieval baseline

## Scope

This increment adds bounded, deterministic citation neighborhood retrieval over the supplied JSONL graph, a thin LangGraph wrapper, citation in-degree ranking, and a JSON CLI. It preserves directed edge source and target fields, edge kinds, and source attributes. It does not use full-text opinions or relevance judgments and makes no claim about legal effectiveness.

## Verification

The public contract files were authored before implementation and were copied without modification. The expected initial red run failed during collection because `legal_graph` did not exist. After implementation, the unchanged retrieval and ranking contracts passed together: **66 passed**.

Reproduce with Python 3.12 and the pinned dependencies:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q tests/test_retrieval_contract.py tests/test_ranking_contract.py
python -m legal_graph.cli retrieve --seed '42 U.S.C. § 1983'
python -m legal_graph.cli benchmark --runs 30 --warmup 5
```

The local environment used NetworkX 3.7, LangGraph 1.2.15, NumPy 2.5.4, and SciPy 1.18.1. The CI workflow runs the same two contract files on Python 3.12 and makes their result a required part of the aggregate `gates` job. The ACS, PCM, CGM, OIO, and stack-train checks remain in that aggregate.

## Frozen dataset and observed run

The input has **119,770 nodes** and **160,793 edges** (110,211 `cites`, 50,582 `part_of`). The raw audit found no duplicate node IDs, duplicate edge triples, repeated source/target pairs, parallel pair kinds, or dangling endpoints. Source hashes:

| File | SHA-256 |
|---|---|
| `data/nodes.jsonl` | `fee8dc5a3b6f395391d04c0738595895f36733d76cb6aaaf0e833cb08ed4102e` |
| `data/edges.jsonl` | `1fa8cc9d99d22f4de1b7226836e663a9589fcf1da6d954a10f717861c2b57367` |

The benchmark loads and indexes the graph once outside the repeated query timing. On one 30-run local sample (5 warmups), graph read took 0.880 s and index construction 2.182 s (3.062 s total). Direct retrieval median/p95 was 3.593/4.458 ms; LangGraph was 4.868/6.060 ms. Both returned exactly equal results. These measurements describe this run and environment; they do not establish a general performance advantage.

For seed `42 U.S.C. § 1983`, the default one-hop incoming `cites` request returned 500 nodes and 499 edges, scanned 1,856 edges, and truthfully reported `node_budget` truncation. The citation-prior preview’s first five scores were all zero because candidate selection hit the bounded neighborhood’s deterministic ID ordering. This is a bounded baseline demonstration, not a relevance or legal-quality result. `part_of` traversal remains an explicit opt-in.

The unchanged starter baseline and broader duplicate/end-point audit are recorded in the session’s `baseline-audit.md`; the starter source itself was not changed.
