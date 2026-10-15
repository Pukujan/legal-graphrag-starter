# Phase 4 retrieval benchmark harness — PROXY run

> **PROVISIONAL / PROXY ONLY.** The gold set is built from held-out citation edges in the starter graph. It is **not** real qrels, and the "hybrid seeds" are simulated, not output from KeyArgo's hybrid retriever. **No retrieval uplift is claimed.**

## What it does
- `python -m legal_graph.benchmark freeze` takes a `GraphSource` and builds deterministic proxy queries. Each query is a held-out caselaw opinion q. Its gold set is every authority q CITES. Its seeds are 30% of that gold set plus an equal number of random distractors. The output is `benchmarks/proxy-v1/queries.jsonl`. The config records the sha256 of that file and the dataset revision; both are checked before every run.
- `python -m legal_graph.benchmark run` removes the held-out opinions' outgoing CITES edges from the graph, then compares two lists at the same k:
  - **baseline**: the seed list as-is, which stands in for hybrid output.
  - **graph-augmented**: the seeds, followed by `graphrag_retrieve` results (depth 2, both directions, CITES only).
- Metrics: Recall@{5,10,20}, NDCG@10, MRR, graph grounding rate, retrieval latency p50/p95, and how many queries hit the size limits.
- Results go to `benchmarks/runs/<name>-<hash of config+revision>/results.json` with a `MANIFEST.sha256`. A run is written once and never overwritten: the directory must not already exist, and the files are set read-only.

## Data sources
`LocalJsonlSource` reads `data/*.jsonl`. `OctoSnapshotSource` reads the same records through the read-only `GraphSnapshotStore` (Octo SQL API, keyset pages of up to 1,000). It can be used once a snapshot has been staged with a write-scoped key. The harness never writes to Octo.

## Known biases (read before quoting numbers)
- The augmented list keeps the seeds first, so by construction it is never worse than the baseline at the same k. The gain at large k mostly reflects the extra slots, not ranking quality.
- `graphrag_retrieve` ranks by global citation in-degree. It is a prior with no query text behind it.
- Many queries hit the 500-node or scan-edge limits at depth 2, which truncates their results.
- Gold = "authorities this opinion cited" measures co-citation recoverability, not legal relevance.

## Reading the graph from Octo
`python -m legal_graph.benchmark run --source octo --workspace <id>` loads the active snapshot through `GraphSnapshotStore`. A run is accepted when the graph's canonical content digest equals `frozen.graph_digest`; the original file revision only matches for local runs. Run ids for non-local sources also hash the source provenance, so a local run and an Octo run land in separate directories.

### Octo parity (2026-10-14)
Octo snapshot `ebcfa2f6-a025-5b3d-9966-9b34a2d4ffcd` (source_import; 119,770 authorities and 160,793 edges; digest `sha256:842750b0…7d80`) was staged, verified by readback, marked ready and activated in the legal-graph-rag workspace. The proxy-v1 benchmark was run from Octo (`proxy-v1-7e3a860fd17fbd63`) and from the local files (`proxy-v1-454d8f6a0edc4802`). Every per-query row and every summary metric is identical in the two runs, except timing. Both runs also match the original local run `proxy-v1-8d3a7690173dcebd`. The proxy caveats above still apply: these are not real qrels, and no uplift is claimed.
