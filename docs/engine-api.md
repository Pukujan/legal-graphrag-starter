# Retrieval-engine HTTP API (contract for ABA and other clients)

Status: implemented on this branch (`legal_graph/engine/`, wired into `legal_graph.server.create_app`).
Machine-readable contract: [`docs/openapi-engine.json`](openapi-engine.json) (FastAPI-generated, regenerate with
`python -m legal_graph.engine.export_openapi`). Offline tests: `tests/test_engine_api.py`.

The engine is the only thing that talks to Octo, local models and OpenRouter. It holds those credentials server-side.
Clients (the ABA-built app) get one bearer token and call the routes below. They never get an Octo key, never touch Octo
directly, and keep their own app database.

## Base URL and auth

- Base URL: `ENGINE_BASE_URL` on the client side. Local today: `http://127.0.0.1:8010`. A Cloudflare tunnel hostname
  will be published later; the paths do not change.
- Every route except `GET /api/v1/health` and `GET /openapi.json` requires `Authorization: Bearer <ENGINE_API_TOKEN>`.
  Missing or wrong token: `401` with `WWW-Authenticate: Bearer`. If the engine was started without `ENGINE_API_TOKEN`,
  token-protected engine routes answer `503` (fail closed).
- The token belongs to the app's server side. Do not ship it to browsers; proxy engine calls through the app backend.
- CORS defaults to localhost dev origins only (`allowed_origins` in `create_app`).

## Routes

| Method, path | Purpose |
|---|---|
| `GET /api/v1/health` | Liveness, graph revision, engine status (`loading`/`ready`/`error`/`disabled`), loaded models. Public. |
| `POST /api/v1/retrieve` with `{"query": ...}` | Staged hybrid + graph retrieval. SSE stream (default) or one JSON object (`"stream": false`). |
| `POST /api/v1/retrieve` with `{"seeds": [...], "k": n}` | Original citation-prior ranking (unchanged, see `backend-api.md`). |
| `POST /api/v1/check` | Claims plus cited passages in; per-claim support, `answer_confidence`, `abstain` out. |
| `GET /api/v1/cases/{case_id}` | Case metadata, Octo authority record, every stored passage for that case. |
| `POST /api/v1/neighborhood` | Bounded graph neighbourhood (unchanged, see `backend-api.md`). |
| `GET /api/v1/authority?id=`, `GET /api/v1/search?q=` | Exact lookup / metadata substring search on the starter graph (unchanged). |
| `GET /api/v1/benchmarks` | List of frozen runs from `benchmarks/runs/*/results.json` (summary + config). |
| `GET /api/v1/benchmarks/{run_id}` | One run in full, including `per_query` rows (wins/losses). |

Case ids are Octo work / authority ids such as `Brinegar v. United States, 338 U.S. 160`. URL-encode them in the path.

## `POST /api/v1/retrieve` (text query)

Request (unknown fields rejected, strict types):

```json
{"query": "Is a warrantless search of an automobile allowed with probable cause? See 456 U.S. 798",
 "k": 10, "bm25_k": 100, "dense": true, "dense_pool": 50, "graph": true, "graph_seed_docs": 10,
 "rerank": true, "rerank_k": 30, "jev": true, "jev_k": 10, "passages_per_case": 2, "stream": true}
```

Only `query` (3 to 4000 chars) is required. Limits: `k` 1-50, `bm25_k` 10-500, `dense_pool` 1-200,
`graph_seed_docs` 0-50, `rerank_k` 1-100, `jev_k` 1-30, `passages_per_case` 1-5. `jev` (default `true`) turns on
the Jev relevance pass inside the `rerank` stage; it runs only when the engine has `OPENROUTER_API_KEY` and `rerank` is true.

### Stream framing

`Content-Type: text/event-stream`. Each message is `event: <name>\ndata: <one-line ASCII JSON>\n\n`.
Event names: `stage` (one per stage, in order `parse, bm25, dense, hybrid, graph, rerank`; there is no separate `jev` stage any more), `result` (once),
`error` (only on failure, after which the stream ends), `done` (always last). Non-finite numbers are sent as `null`.

Every `stage` data object has:

```json
{"type": "stage", "schema_version": "engine-retrieve/1", "stage": "bm25",
 "duration_ms": 48.9, "elapsed_ms": 359.3}
```

A stage that did not run has `"skipped": true` and a `"reason"` (`disabled by request`, `no dense model loaded`,
`no reranker loaded`, `no graph source`, `OPENROUTER_API_KEY not configured`).

Per-stage fields:

| stage | extra fields |
|---|---|
| `parse` | `parser` (`eyecite`, the default and installed in the engine env; `regex` only if eyecite is missing), `citations: [{citation, doc_id or null}]` (reporter cites in the question, resolved against the active Octo snapshot) |
| `bm25` | `retriever` (`bm25s`), `chunks_scored`, `candidates: [{doc_id, chunk_id, rank, score}]` (chunk hits max-pooled to cases) |
| `dense` | `model`, `mode`. `mode: "index"` (normal): full-corpus search of the precomputed chunk index; extra fields `top_chunks` (= `dense_pool`), `index_chunks`, `dim`, `storage`, `candidates: [{doc_id, chunk_id, rank, score}]`. `mode: "pool"` (fallback when the index is missing or stale): re-encodes the top `dense_pool` BM25 chunks; `pool_chunks`, `candidates: [{doc_id, rank, score}]`. Scores are cosine. |
| `hybrid` | `method: "rrf"`, `rrf_k: 60`, `inputs: ["bm25","dense"]`, `candidates: [{doc_id, rank, score, ranks: {bm25: r, dense: r}}]` |
| `graph` | `hops: 1`, `frontier: [doc_id]`, `edges_scanned` (edges returned by the one SQL read), `edge_cap` (`ENGINE_GRAPH_EDGE_CAP`, default 20,000), `truncated` (`true` when `edges_scanned` reached the cap, so some neighbours were not seen), `edges_used`, `edges: [{source, target, kind}]` (edges that added a case, max 500), `added: [{doc_id, via: [{source:"graph", edge, via, direction}]}]`, `graph_revision` |
| `rerank` | `method` (`cross-encoder`, `cross-encoder+jev`, or `jev`), `model`, `reranked`, `cross_encoder: {model, reranked, duration_ms}`, `jev: {model, k, scored, failed, duration_ms}` or `jev: {skipped: true, reason}`, `candidates: [{doc_id, rank, score, jev_relevance or null, prev_rank}]`. Jev scores the top `jev_k` cross-encoder cases in parallel (relevance = mean of a Noul and an order-averaged Choice, `typesafe/jev-1.14`) and reorders that head by it; a failed Jev call keeps its case after the scored ones and adds a `jev_error` provenance entry. |

### `result` event (also the `result` key in JSON mode)

```json
{"type": "result", "schema_version": "engine-retrieve/1", "query": "...",
 "corpus_revision": "octo-chunk:804646", "graph_revision": "5f469d7d-18b6-5bb5-95e6-72b348e59799",
 "elapsed_ms": 4210.5,
 "results": [{
   "rank": 1,
   "case": {"id": "Brinegar v. United States, 338 U.S. 160", "name": "Brinegar v. United States",
            "court": "Supreme Court of the United States", "year": 1949,
            "link": "https://static.case.law/us/338/cases/0160-01.json",
            "html_link": "https://case.law/caselaw/?reporter=us&volume=338&case=0160-01", "cap_id": "..."},
   "score": 3.39, "score_kind": "cross-encoder", "rrf": 0.0312, "bm25": 12.1, "dense": 0.71, "jev_relevance": null,
   "provenance": [{"source": "bm25", "rank": 4}, {"source": "dense", "rank": 2},
                  {"source": "graph", "edge": "cites", "via": "Ornelas v. United States, 517 U.S. 690", "direction": "outgoing"},
                  {"source": "question_citation"}],
   "passages": [{"chunk_id": "cap:...:combined:3", "ord": 3, "text": "<stored chunk body, verbatim>", "bm25": 12.1,
                 "spans": [{"start": 120, "end": 260, "text": "<exact slice of text>", "query_term_overlap": 4}]}]
 }]}
```

Rules the client can rely on:
- `passages[].text` is the stored Octo chunk body, byte for byte. `spans[].text == text[start:end]`.
  Any quote shown to a user must be one of these spans or another exact substring of a returned passage.
- `provenance` lists every retriever or graph edge that put the case in the pool. It drives a "why these cases" panel.
- `score_kind` says how the list was ordered (`cross-encoder+jev`, `cross-encoder`, `jev`, or `rrf`); `score` is the cross-encoder score when reranked, else RRF. `jev_relevance` is the Jev probability for the top `jev_k`.
- `case.link` is the CAP static JSON (`https://static.case.law/<reporter>/<volume>/cases/<first page>-<ordinal>.json`) and `case.html_link` the case.law viewer URL. Colorado cases get both from their CAP metadata; CLERC cases were resolved once by fetching the candidate static file and accepting it only when its `id` equals the stored `cap_id` (`python -m legal_graph.engine.links`). Unresolved cases have `null`.

### Errors

| status | when |
|---|---|
| 401 | missing or wrong bearer token |
| 422 | invalid body (unknown field, wrong type, out-of-range value) |
| 503 | engine still loading (`health.engine.status == "loading"`), token not configured, or scorer not configured |
| 200 + `event: error` | a stage failed mid-stream; data `{type:"error", error, detail}`, then `done` |

## `POST /api/v1/check`

The client generates the answer itself (its own model), splits it into claims and sends each claim with the exact passage
text it cites. The engine scores support with `legal_graph.answer_check`.

```json
{"claims": [{"text": "Police may search a car without a warrant if they have probable cause.",
             "cited_text": "<exact span from a returned passage>", "citation": "338 U.S. 160",
             "chunk_id": "cap:...:combined:3"}],
 "scorer": "minicheck", "threshold": 0.5, "temperature": 1.0}
```

`scorer`: `minicheck` (default, `lytang/MiniCheck-RoBERTa-Large`), `hhem` (`vectara/hallucination_evaluation_model`), or
`jev` (OpenRouter Decisions API via the `legal_graph.jev` client, model `JEV_MODEL`, default `typesafe/jev-1.14`;
support = mean of a Noul and an order-averaged Choice; 503 unless `OPENROUTER_API_KEY` is set). `typesafe/jev-router` is a
chat-completions router that the Decisions API rejects, so it is not a valid `JEV_MODEL`.
1-50 claims per call.

Response (`AnswerScore.to_dict()` plus quote checks):

```json
{"claims": [{"claim_index": 0, "citation": "338 U.S. 160", "raw_prob": 0.93, "calibrated_prob": 0.93, "threshold": 0.5,
             "accepted": true, "decider": "lytang/MiniCheck-RoBERTa-Large", "model_version": "main",
             "temperature": 1.0, "decided_at": "2026-10-15T04:20:00+00:00", "schema_version": "answer-check/1"}],
 "answer_confidence": 0.93, "threshold": 0.5, "abstain": false, "aggregation": "min",
 "decider": "lytang/MiniCheck-RoBERTa-Large", "model_version": "main", "decided_at": "...", "latency_ms": 812.0,
 "schema_version": "answer-check/1", "schema_version_engine": "engine-check/1",
 "quotes": [{"claim_index": 0, "chunk_id": "cap:...:combined:3", "chunk_found": true, "quote_verified": true, "char_start": 120}],
 "unverified_quotes": []}
```

`answer_confidence` is the minimum calibrated claim score. `abstain` is true when it is below `threshold` **or** any
claim with a `chunk_id` quotes text that is not in that stored chunk. Benchmarked accuracy and calibration are in
`benchmarks/answer_check/README.md`.

## Latency targets

Targets on an idle 8-core CPU box with models warm: parse < 50 ms, bm25 < 1 s (804k chunks), dense (query encode +
full index scan) < 1 s, graph < 1.5 s (one Octo SQL round-trip, up to 20,000 edges), rerank: cross-encoder (30) < 2.5 s
plus Jev (10 parallel calls) about 3-6 s, full stream < 10 s p50 with Jev; `/check` MiniCheck < 300 ms per
claim. Measured smoke results are in the PR; the box was heavily loaded by other jobs when they were taken, so they are
upper bounds. The client must render stages as they arrive and not wait for `result`.

## Engine configuration (names only; values live in untracked env files)

| env var | used for |
|---|---|
| `ENGINE_API_TOKEN` | bearer token clients must send (box: `/workspace/octo-legal-graph/.env.engine`, mode 600) |
| `ENGINE_LIVE=1` | load the live pipeline from Octo at startup (background thread; health shows progress) |
| `OCTO_BASE_URL` | default `https://octodb.design-bakery.com/api` |
| `OCTO_WORKSPACE_ID` | default `76d7577b-2274-42b4-a8dc-1ff1c0efa741` (Octo Legal RAG) |
| `OCTO_API_KEY` | Octo workspace key (falls back to legacy `octo_db_legal`) |
| `OCTO_SNAPSHOT_ID` | pin a graph snapshot; default reads `legal_graph.active_snapshot` |
| `ENGINE_CACHE_DIR` | chunk cache (`chunks.jsonl`) and dense index (`dense-<model>.f16.npy` + `.json`), default `/workspace/lgr-text-data/engine-cache` |
| `ENGINE_DENSE_MODEL`, `ENGINE_DENSE_MAX_LEN` | default `BAAI/bge-small-en-v1.5`, 256 |
| `ENGINE_DENSE_IN_RAM` | `1` (default) copies the dense index into RAM; `0` memory-maps it |
| `ENGINE_GRAPH_EDGE_CAP` | max edges read per graph stage, default 20,000 |
| `ENGINE_RERANK_MODEL`, `ENGINE_RERANK_MAX_LEN` | default `cross-encoder/ms-marco-MiniLM-L-6-v2`, 384 |
| `OPENROUTER_API_KEY`, `JEV_MODEL` | Jev rerank pass and `jev` scorer; default model `typesafe/jev-1.14` (Decisions API) |
| `ENGINE_JEV_RERANK` | `1` (default) wires Jev into the rerank stage when the key is present; `0` disables it engine-wide |
| `JEV_RETRIES`, `JEV_TIMEOUT` | Jev retries per call (default 2) and per-request timeout in seconds (default 30) |

Client side (ABA app): `ENGINE_BASE_URL`, `ENGINE_API_TOKEN`, plus whatever its own answer model needs
(for example `OLLAMA_BASE_URL` for an Ollama-compatible endpoint). The engine does not generate answers.

Octo requests from the engine send `User-Agent: legal-graph/0.1` (see `legal_graph/octo_client.py`). Keep that header on any Octo client.

## Run

```sh
pip install -r requirements-search.txt -r requirements-dev.txt   # + answer-check deps: transformers
ENGINE_LIVE=1 ENGINE_API_TOKEN=... OCTO_API_KEY=... \
  python -m uvicorn legal_graph.server:create_app --factory --host 127.0.0.1 --port 8010
```

On the box, `/workspace/octo-legal-graph/run-engine.sh` (copy in `scripts/run-engine.sh`) does this. It sources
`/home/box/.config/study-os/openrouter.env` (for `OPENROUTER_API_KEY`) and the Octo and engine env files with `set -a`,
and never echoes values. The engine env is a uv venv (`uv pip install -r requirements-search.txt -r requirements-dev.txt
transformers`, which includes eyecite). First start pages 804,646 chunks out of Octo (about 12 minutes) into
`chunks.jsonl`; later starts read that cache if the row count matches.

### Dense index (build once)

```sh
python -m legal_graph.engine.dense_index --build     # resumable; checkpoints every 4,096 chunks
```

Every stored chunk is embedded once with the configured dense model (default bge-small, 384-d, L2-normalised,
max 256 tokens) into `$ENGINE_CACHE_DIR/dense-BAAI__bge-small-en-v1.5.f16.npy` (float16, about 0.6 GB) with a `.json`
manifest (model, dim, n, sha256 of the chunk-id order). The engine loads it at startup and encodes only the query at
request time. **Storage: local cache, not Octo.** `legal_graph.chunk.embedding` is `vector(1024)` and bge-small is
384-d, so the vectors do not fit the column; `chunk.embedding` stays NULL (`embed_model='none'`). A 1024-d model would
fit (the manifest then says `storage: octo+local-cache`). If the chunk set changes, the manifest no longer matches, the
engine logs a warning and uses `mode: "pool"` until the index is rebuilt.

## Engine layout (MVC, engine only)

- Model: `legal_graph/engine/models.py` (request schemas, schema versions), `corpus.py` (passages, BM25).
- Services: `pipeline.py` (staged retrieval), `checks.py` (support scoring, Jev), `octo_source.py` (Octo SQL, model wiring).
- Controllers/views: `routes.py` (auth, SSE framing, benchmarks), `legal_graph/server.py` (app factory, legacy routes).

## Library entry points (import, do not rewrite)

| module | entry points |
|---|---|
| `legal_graph.search.hybrid` | `rrf(rankings, k=60) -> list[str]`; `chunks_to_docs(chunk_ids, scores, chunk_doc, k)`; `BM25Index(texts).search(queries, k)`; `DenseIndex(model_name, texts, max_len=512, batch_size=16).search(queries, k)`; `Reranker(model_name, max_len=512).rerank(query, cand_docs, doc_text)` |
| `legal_graph.search.chunking` | `fixed_chunks`, `recursive_chunks(text, size=500)`, `header(meta)`, `chunk_doc(doc_id, text, meta, method="recursive")` |
| `legal_graph.retrieval` | `CitationGraphIndex(nodes, edges, dataset_revision=, source_dataset=)` with `.retrieve(seeds, depth=1, direction="incoming", edge_kinds=("cites",), max_nodes=500, max_edges=1000, max_scanned_edges=10000)` and `.retrieve_workflow(...)`; `graphrag_retrieve(seed_citations, k, index=...)`; `load_dataset()` |
| `legal_graph.retrieval_cache` | `RetrievalCache(path=":memory:", ttl_seconds=3600).get/put/set_active_snapshot`; `cached_retrieve(cache, snapshot_id, embedding, ...)` |
| `legal_graph.answer_check` | `Claim(text, cited_text, citation="")`; `CitationChecker(scorer, threshold=0.5, temperature=1.0).check(claims) -> AnswerScore`; `MiniCheckScorer()`, `HHEMScorer()`; `fit_temperature`, `conformal_threshold`, `expected_calibration_error` |
| `legal_graph.decisions` | `OpenRouterJevProvider(api_key=None).decide(request)`; `parse_decision_response(raw, questions)` |
| `legal_graph.engine.checks` | `JevScorer(model=None)` (SupportScorer over `legal_graph.jev`, plus `.relevance(query, passage)`) |
| `legal_graph.jev` | `JevClient(model="typesafe/jev-1.14").decide(state, questions)`, `choice_questions`, `average_choice`, `noul` |
| `legal_graph.engine.dense_index` | `build(texts, chunk_ids, model, cache_dir)`, `load(cache_dir, model, chunk_ids, encode_query) -> DenseIndex` |
| `legal_graph.engine.links` | `reporter_slug`, `cap_static_url`, `cap_html_url`, `resolve_cap_link(cap_id, cite)`, `backfill(sql)` |
| `legal_graph.engine.pipeline` | `Pipeline(corpus, lexical, ...).run(TextRetrieveRequest) -> Iterator[event]` |
| `legal_graph.octo_client` | `OctoSqlExecutor(workspace_id, key_env="octo_db_legal", base_url=...)(sql, params, row_limit)` |

## Data the engine reads (Octo Legal RAG, schema `legal_graph`)

| table | rows (2026-10-15) | use |
|---|---|---|
| `active_snapshot` | 1 (`5f469d7d-18b6-5bb5-95e6-72b348e59799`) | which graph snapshot is live |
| `snapshots` | 6 | snapshot provenance and counts |
| `authorities` | 547,219 (all snapshots) | graph nodes; `payload` has title, court, date, citations, cap_id |
| `citation_edges` | 1,473,123 (all snapshots) | graph edges (`source_id`, `target_id`, `payload.kind`); hash-indexed on both ends |
| `work`, `expression` | 45,048 each (4,151 CLERC + 40,897 CAP Colorado) | cases with stored opinion text (migration 004) |
| `chunk` | 804,646 (51,636 CLERC + 753,010 Colorado) | stored passages (`body`, `ord`), the only quotable text |
| `type_registry` | 6 | node/edge/decision type keys |
| `edge`, `assertion`, `evidence`, `work_alias`, `prov_activity` | 0 | migration 004 tables, not populated yet |

Example reads the engine runs (parameterised):

```sql
SELECT snapshot_id FROM legal_graph.active_snapshot;
SELECT source_id, target_id, payload->>'kind' AS kind FROM legal_graph.citation_edges
 WHERE snapshot_id = $1 AND (source_id = ANY($2::text[]) OR target_id = ANY($2::text[])) LIMIT 20000;
SELECT authority_id FROM legal_graph.authorities WHERE snapshot_id = $1 AND payload->'citations' ? $2 LIMIT 1;
SELECT c.chunk_id, e.work_id, c.ord, c.body FROM legal_graph.chunk c
  JOIN legal_graph.expression e ON e.expr_id = c.expr_id WHERE c.chunk_id > $1 ORDER BY c.chunk_id LIMIT 2000;
```

Coverage: text exists for 45,048 cases: the 4,151 cases behind the frozen CLERC queries (federal and other states,
opinions truncated to 12,000 chars) and all 40,897 CAP Colorado opinions (Colorado Supreme Court, Court of Appeals and a
few district courts, 1864 to 2019, full text) loaded by `benchmarks/cap_colorado/octo_load_co.py` (streamed,
checkpointed, idempotent). 27,515 of the Colorado cases are authorities in the active graph snapshot, so graph
expansion works for them. The graph has 547k nodes but the engine can only return a case it has passages for. Outside
Colorado, state coverage is still only the CLERC subset.

## Data on the box

- `/workspace/lgr-text-data/` : CLERC source (`collection.doc.tsv.gz`, 7.7 GB), full chunked text for 307,510 cases
  (`chunks/`, 3.5 GB, 3.6M chunks), treatment edges/labels, benchmark logs, and `engine-cache/chunks.jsonl`
  (the 804,646 Octo chunks the engine serves) and the dense index. Colorado source text: `/workspace/coeval-data/co/`
  (`text/part-000.jsonl`, `meta/colo-*.jsonl`; the same CAP volumes as `/workspace/lgr-text-data/cap-colo/`).
- `/workspace/lgr-search-data/` : search-track docs and results; its `.venv` is the Python env used to run the engine.
- Models are in the Hugging Face cache (`~/.cache/huggingface/hub`): bge-small, MiniLM cross-encoder, MiniCheck, HHEM.

Moving the engine to the Mac needs: this repo, `pip install -r requirements-search.txt` + `transformers`, the four HF
models (or network on first start), and the env file. The BM25 index is built in memory at startup from Octo chunks
(about 50 MB of text); dense vectors are computed per query over the BM25 pool, so there is no embedding file to copy.
Copy `engine-cache/chunks.jsonl` only to skip the first Octo download.
