# CLERC opinion text + text retrieval with/without graph expansion

Refs #40. Folds in the interrupted search track (#28, #29, #33).

## Ingestion
- Source: CLERC `collection.doc.tsv.gz` (8.2 GB), streamed (`benchmarks/clerc/text/ingest.py`, resumable via `state.json`).
  1,842,422 records read; 307,510 kept (graph cases with a CAP id: 307,679).
- Chunking (`chunk.py`): recursive paragraph-first packing to 1000 chars, each opinion truncated to its first 12,000 chars,
  each chunk prefixed with a `case name | court | year` header. 3,617,119 chunks, 3.5 GB on disk (`texts.bin` + offsets).
- Octo (`octo_load.py`, migration 004 tables): only the gold+seed cases of the frozen CLERC queries are stored:
  4,151 `work` / 4,151 `expression` / 51,636 `chunk` rows, `embed_model='none'` (schema vector is 1024-d; benchmark used 384-d).
  Idempotent (`ON CONFLICT DO NOTHING`), so reruns resume.

BM25 is a disk-backed re-implementation of bm25s scoring (`sparse.py`; same k1/b/idf/stopwords/stemmer), because
bm25s needs every tokenized chunk in memory.

## Embedding (CPU budget)
Full dense embedding of 3.6M chunks is infeasible on 8 CPU. Dense = `BAAI/bge-small-en-v1.5` on each query's BM25 top-50
chunk pool only, on a seeded 500-query sample per split (~38k chunks embedded, cached in `emb-cache.npz`).
Reranker: `cross-encoder/ms-marco-MiniLM-L-6-v2`, top 30 docs.

Search-track embedder comparison (2,000-doc CLERC subcorpus, 200 queries, `benchmarks/search-track/`):
modernbert-embed-base_finetune_512 recursive-500 R@10 0.48 / nDCG@10 0.320; fixed-500 0.49 / 0.304; hybrid RRF 0.47 / 0.300;
+MiniLM rerank 0.37 / 0.205. Qwen3-Embedding-0.6B and BGE-M3 variants were configured (`v_main.json`) but the run was
interrupted before they finished; not reported.

## Benchmark (frozen CLERC test, leak control as #25: holdout edges removed)
Indirect: 2,851 queries (112,105 holdout edges removed). Direct: 1,498 (105,351). Sample = 500 seeded queries.

| split | retriever | n | R@5 | R@10 | R@20 | nDCG@10 | MRR |
|---|---|---|---|---|---|---|---|
| indirect | bm25 | 2851 | 0.091 | 0.127 | 0.169 | 0.067 | 0.054 |
| indirect | bm25 +graph | 2851 | 0.164 | 0.262 | 0.395 | 0.137 | 0.114 |
| indirect | bm25 +graph+seeds | 2851 | 0.139 | 0.223 | 0.355 | 0.120 | 0.104 |
| indirect | bm25@sample | 500 | 0.096 | 0.136 | 0.174 | 0.069 | 0.055 |
| indirect | bm25@sample +graph | 500 | 0.198 | 0.292 | 0.430 | 0.158 | 0.131 |
| indirect | bm25@sample +graph+seeds | 500 | 0.168 | 0.256 | 0.392 | 0.142 | 0.121 |
| indirect | hybrid | 500 | 0.104 | 0.148 | 0.190 | 0.078 | 0.062 |
| indirect | hybrid +graph | 500 | 0.194 | 0.302 | 0.448 | 0.158 | 0.129 |
| indirect | hybrid +graph+seeds | 500 | 0.162 | 0.248 | 0.392 | 0.138 | 0.120 |
| indirect | rerank | 500 | 0.068 | 0.106 | 0.176 | 0.056 | 0.049 |
| indirect | rerank +graph | 500 | 0.210 | 0.296 | 0.404 | 0.168 | 0.141 |
| indirect | rerank +graph+seeds | 500 | 0.174 | 0.250 | 0.374 | 0.150 | 0.134 |
| direct | bm25 | 1498 | 0.077 | 0.107 | 0.151 | 0.059 | 0.050 |
| direct | bm25 +graph | 1498 | 0.145 | 0.244 | 0.388 | 0.125 | 0.105 |
| direct | bm25 +graph+seeds | 1498 | 0.121 | 0.199 | 0.347 | 0.107 | 0.097 |
| direct | bm25@sample | 500 | 0.084 | 0.106 | 0.156 | 0.061 | 0.053 |
| direct | bm25@sample +graph | 500 | 0.146 | 0.234 | 0.408 | 0.123 | 0.107 |
| direct | bm25@sample +graph+seeds | 500 | 0.124 | 0.198 | 0.374 | 0.110 | 0.101 |
| direct | hybrid | 500 | 0.086 | 0.112 | 0.160 | 0.063 | 0.053 |
| direct | hybrid +graph | 500 | 0.142 | 0.246 | 0.406 | 0.129 | 0.112 |
| direct | hybrid +graph+seeds | 500 | 0.118 | 0.198 | 0.360 | 0.111 | 0.103 |
| direct | rerank | 500 | 0.042 | 0.072 | 0.140 | 0.033 | 0.029 |
| direct | rerank +graph | 500 | 0.162 | 0.248 | 0.372 | 0.132 | 0.112 |
| direct | rerank +graph+seeds | 500 | 0.136 | 0.200 | 0.334 | 0.120 | 0.113 |

Findings: one-hop graph expansion roughly doubles R@10 and nDCG@10 for every retriever (indirect BM25 R@10 0.127 -> 0.262).
Pool-limited dense adds a little over BM25 (R@10 0.136 -> 0.148). The general MS-MARCO reranker hurts text-only ranking
but gives the best R@5/MRR once graph expansion is on. Seed expansion is worse than hit-only expansion.

## Reuse
Retrievers are pluggable: `legal_graph/search/hybrid.py` (BM25, dense, RRF, rerank), `legal_graph/search/chunking.py`,
`python -m legal_graph.benchmark text ...`; the CLERC driver is `benchmarks/clerc/text/bench.py`.

## Left
Full-corpus dense index; legal reranker; 1024-d embeddings into `chunk.embedding`; finish Qwen3/BGE-M3 comparison.
