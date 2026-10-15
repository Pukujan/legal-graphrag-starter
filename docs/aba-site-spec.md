# Legal site build plan and ABA spec

Status: plan only, as of 2026-10-14. Nothing in this document is built yet unless the "Exists today" column says so. Tracking issue: see the PR that adds this file.

## What we are building

A public website with three parts:

1. **Landing page.** Outcome copy for people who research case law. No technical words.
2. **Demo page.** Someone types a legal question in plain English. The backend finds cases, a small local model writes an answer that cites a retrieved case for every claim, and the page shows the search happening live: a 2.5D star field of case nodes that lights up stage by stage, next to an animated workflow of the stages.
3. **Admin area.** Benchmarks and request logs, behind Cloudflare Access.

ABA (Pukujan/app-builder-automation, headless engine) generates every line of the React app from `aba/legal-site.prompt.md`. Nobody hand-writes the frontend. Everything server-side (answer endpoint, retrieval indexes, models, logs) lives in this fork and ships through issues and PRs. Upstream KeyArgo is never touched.

## Who owns what

| Piece | Owner | Why |
|---|---|---|
| React app: landing, demo, admin pages, star field, workflow animation, chat UI | ABA (generated) | ABA owns all app code (KEYARGO-09). |
| `/api/v1/answer` SSE endpoint, retrieval pipeline, LangGraph graph | This fork, new issue | ABA has no Python/FastAPI path and must not own credentials or models. |
| BM25 + dense indexes over case records | This fork, new issue | Built offline from `data/nodes.jsonl`. |
| Request log store and `/api/v1/admin/*` reads | This fork, new issue | Admin pages only read. |
| Local LLM runtime (Ollama or llama.cpp server) | Mac host config | Runs beside the API; not in either repo. |
| Static hosting + API on one origin, Cloudflare Tunnel, Access policy | Deploy step on the Mac (gravebuster pattern) | ABA has no deploy tool; `deploy/gravebuster/README.md` is the pattern. |
| Marketing copy | Written in the ABA prompt under the human-writing rules; PRD-agent output can replace it later | PRD-agent has no runtime yet (KEYARGO-09). |

## Retrieval and answer pipeline (specified, not built)

All stages run locally on the Mac with small ready-made libraries. LangGraph (already pinned, `langgraph==1.2.15`) orchestrates one `StateGraph` with one node per stage. Each node emits SSE events.

| # | Stage | Library / model (proposed) | Input → output | Exists today? |
|---|---|---|---|---|
| 1 | Lexical search | `bm25s` over case title + citation id + court + statute ids it cites | question → top 100 ids with BM25 scores | No. Today `/search` is a substring match only. |
| 2 | Dense search | `sentence-transformers` with `BAAI/bge-small-en-v1.5` (or `all-MiniLM-L6-v2`), vectors in a local numpy/FAISS file | question → top 100 ids with cosine scores | No. |
| 3 | Fusion | Reciprocal rank fusion, k=60 (a few lines, or `ranx`) | two lists → top 50 | No. |
| 4 | Rerank | Cross-encoder `cross-encoder/ms-marco-MiniLM-L-6-v2` (or `BAAI/bge-reranker-base`) | 50 → top 10 with new order | No. |
| 5 | Graph expansion | Existing `CitationGraphIndex.retrieve` / `graphrag_retrieve` (PR #11), depth 1, both directions, `cites` | top 10 seeds → neighbours + edges, ranked by citation prior | **Yes** (`/api/v1/neighborhood`, `/api/v1/retrieve`). |
| 6 | Jev typed-decision check | `legal_graph/decisions.py` `OpenRouterJevProvider` (`typesafe/jev-1.13`) | candidate + opinion passage → accept/reject + distribution | Provider **exists**; per-candidate use is **pending**: there is no opinion text to judge. Stage emits `status: "pending_text"` for every candidate until text lands. Needs `OPENROUTER_API_KEY` and is the only non-local stage. |
| 7 | Answer | Small local instruct model: `qwen2.5:3b-instruct` via Ollama (fallback `qwen2.5:1.5b`, or Phi-3-mini GGUF via llama.cpp `llama-server`), OpenAI-compatible API on 127.0.0.1 | question + case cards → streamed answer with `[n]` markers | No. |

Grounding rules for stage 7: the prompt contains only the retrieved case cards (name, citation, court, date, URL, and the graph relations that tie it to the question). Every sentence must end with at least one `[n]` that points at a card. A post-check drops any sentence without a valid marker and any marker outside the card list. The model may describe what a case is, where it sits, what it cites and what cites it. Without opinion text it must not state holdings; the system prompt says so and the UI shows it.

Cold start: the model is pulled once (`ollama pull qwen2.5:3b-instruct`, about 2 GB). The API warms embeddings, reranker and LLM in the FastAPI lifespan and reports `warming` in health until ready, so the first visitor never waits on a model load in silence.

### `POST /api/v1/answer` (SSE)

Request (strict, extra fields rejected like the rest of the API):

```json
{"question": "Can a landlord keep my deposit for normal wear and tear in Colorado?", "k": 8, "session_id": "optional-uuid"}
```

Response: `text/event-stream`. Every event is `event: <type>` and `data: <json>`. Each data object includes `run_id` and `t_ms` (ms since the run started).

| event | data fields | UI use |
|---|---|---|
| `run_started` | `question`, `graph_revision`, `stages: [ids]` | build the workflow nodes |
| `stage_started` | `stage` (`bm25`,`dense`,`fusion`,`rerank`,`graph`,`jev`,`answer`) | animate stage active |
| `candidates` | `stage`, `items: [{id, title, court, date, url, score, rank}]` | light star-field nodes |
| `rerank` | `items: [{id, from_rank, to_rank, score}]` | animate reordering |
| `graph` | `nodes: [...]`, `edges: [{src,dst,kind}]`, `truncated` | draw edges spreading out |
| `jev` | `items: [{id, status: "accepted"|"rejected"|"pending_text", distribution?}]` | mark nodes accepted, rejected or grey "awaiting text" |
| `stage_done` | `stage`, `ms`, `count` | stage complete + timing |
| `token` | `text` | stream the answer |
| `citations` | `items: [{n, id, title, url, court, date}]` | clickable cited cases |
| `why` | `items: [{id, reasons: ["matched words in name", "close in meaning", "reranked to #2", "cited by 14 Colorado cases", "cites C.R.S. § 38-12-103"]}]` | "Why these cases" panel |
| `limitation` | `text` | the opinion-text notice |
| `done` | `answer`, `dropped_sentences`, `total_ms` | finish |
| `error` | `stage`, `message` | show error state |

Reasons in `why` are built from real stage outputs only. Nothing in them is generated by the LLM.

### Admin reads (new, read-only)

- `GET /api/v1/admin/runs?limit=50&cursor=` → past answer runs: `run_id, started_at, question, stage timings, citation ids, dropped_sentences, error`.
- `GET /api/v1/admin/runs/{run_id}` → the full event log of one run.
- `GET /api/v1/experiments` (exists, returns an empty list today) → benchmark runs. A follow-up wires `benchmarks/runs/*` and `python -m legal_graph.benchmark` (`--source octo`) results into it. Nothing is invented while it is empty.

Admin routes sit under `/admin` (UI) and `/api/v1/admin` (API). Cloudflare Access protects both paths with an email allow-list policy. The API also verifies the `Cf-Access-Jwt-Assertion` header against the team's JWKS so a request that skips the tunnel is refused.

Logs store questions as typed. Storage: a local SQLite file first; moving it to the Octo Legal RAG workspace is an option, but only through the existing SQL API (consuming-octo policy), no new database.

## Visual direction (for ABA)

The demo is a visual piece, not a dashboard.

- **Star field.** 2.5D scene built with `@react-three/fiber` + `@react-three/drei` (deck.gl is the alternative). Each case is a small point of light on a deep navy field with slight parallax as the cursor moves. Stage colors: BM25 hits warm white, dense hits cyan, both together gold. Rerank: the top ten drift toward the centre in their new order. Graph: thin lines grow out from the top cases to their neighbours. Jev: accepted pulse green, rejected fade to dim red, pending stay grey with a dotted ring. Cited cases keep a soft halo during the answer, and hovering an `[n]` in the text brightens its star.
- **Workflow.** `@xyflow/react` (React Flow) vertical chain of the seven stages with an animated edge on the active stage, a count and ms on each finished stage, and the Jev node labelled "waiting for opinion text" while pending.
- **Layout.** Desktop: chat column left (question box, streamed answer, cited case list, Why panel), star field centre-right, workflow strip on the right. Mobile: star field on top at reduced height, workflow collapses to a horizontal progress row, chat below.
- Reduced motion: no drift, instant state changes. Every animated state also exists as text in the chat column.
- Landing page uses the same star field as slow ambient background, with no labels, no stages and no data.

## What exists today and what is new

| Area | Exists today (master `7b08cfa`) | New work |
|---|---|---|
| Graph data | 119,770 nodes, 160,793 edges, names, citations, courts, dates, CourtListener URLs | none |
| HTTP API | `/health`, `/search` (substring), `/authority`, `/neighborhood`, `/retrieve` (citation prior), `/experiments` (empty). No auth. | `/answer` SSE, `/admin/runs*`, Access JWT check, `warming` health state |
| Lexical / dense / rerank | none | bm25s index, bge-small vectors, RRF, MiniLM cross-encoder, index build script |
| Graph expansion | done (PR #11) | call it from the LangGraph node |
| Jev decisions | provider + parser + cache (PR #8), synthetic runs only | per-candidate node, pending until text exists |
| Opinion text | none. CLERC overlap is 0.2% of gold cites (PR #21) | a Colorado opinion source (see open questions) |
| LLM answer | none | Ollama/llama.cpp + grounded prompt + citation post-check |
| Benchmarks | `python -m legal_graph.benchmark` (`--source octo`), proxy run results | expose them on `/experiments` |
| Frontend | none | all of it, generated by ABA |
| Hosting | none on the Mac | compose/launchd, Cloudflare Tunnel, Access |

## Follow-up fork issues (to open, one PR each)

1. Answer pipeline: indexes (bm25s, dense), RRF, reranker, LangGraph graph, `/api/v1/answer` SSE, grounding post-check, tests with a fake LLM.
2. Run log store + `/api/v1/admin/runs*` + Access JWT check + benchmarks on `/experiments`.
3. Opinion text source + Jev per-candidate checks (blocked on text).
4. Mac deploy: Ollama, API service, static app, tunnel ingress, Access app.

## Build order

1. Merge this spec.
2. Fork issue 1 lands; `/api/v1/answer` streams real events against a local model.
3. Run ABA on the Mac (command below) with `VITE_API_BASE` pointed at the API. ABA's gates only prove it builds; check the rendered pages and SSE handling by hand.
4. Fork issue 2, then ABA iterate run for admin wiring if anything changed.
5. Deploy: tunnel + Access.
6. When opinion text exists: issue 3, then an ABA iterate run that removes the limitation banner wording.

## ABA run on the Mac

```bash
cd ~/aba            # app-builder-automation checkout
~/aba/bin/run-aba.sh headless \
  --prompt ~/legal-graphrag-starter/aba/legal-site.prompt.md \
  --out ~/aba-out/legal-site \
  --scaffold pipeline/scaffold --view-render --json
```

Equivalent without the wrapper:

```bash
cd ~/aba/dyad && npx vite build --config vite.aba-headless.config.mts
cd ~/aba && node --env-file="$ABA_ENV_FILE" dyad/.vite/build/aba_headless.cjs \
  --prompt ~/legal-graphrag-starter/aba/legal-site.prompt.md \
  --out ~/aba-out/legal-site --scaffold pipeline/scaffold --view-render --json
```

`--out` is wiped at start, so never point it at this repo. Later changes use `--workspace ~/aba-out/legal-site --prompt <delta.md>`. Env file: `BUILDER_BASE_URL`, `BUILDER_API_KEY`, `BUILDER_MODEL`, `ABA_MODEL_CHAT` and the other `ABA_MODEL_*` roles. Leave `OCTO_*` unset: the app does not need ABA's database tools, and KEYARGO-09 forbids pointing ABA's Octo provider at an unrelated workspace.

## Deploy shape (Mac)

- `ollama serve` on 127.0.0.1:11434.
- `uvicorn legal_graph.server:create_app --factory --host 127.0.0.1 --port 8000`.
- Static `dist/` and `/api` on one origin (Caddy or the FastAPI app mounting `dist/`), so the app's relative `fetch("/api/v1/...")` works.
- `cloudflared` tunnel (remotely managed) with an ingress rule for the hostname placed before the `http_status:404` catch-all; the PUT replaces the whole list.
- Cloudflare Access application on `<host>/admin*` and `<host>/api/v1/admin*`.
- Rate limit `/api/v1/answer` (Cloudflare rule or in-app) since one Mac runs the model.

## Blockers

- No access to the Mac yet (no SSH/agent, no `~/aba` checkout confirmed, `bin/run-aba.sh` lives only on the Mac and is not in the ABA repo).
- No Cloudflare API token with tunnel + DNS + Access edit rights for this hostname; hostname not chosen.
- No builder gateway key confirmed for the Mac run (`BUILDER_*`, `ABA_MODEL_*`).
- Answer LLM: the plan is a local model, so no key is needed, but the Mac must have Ollama and room for a 2 GB model. A hosted fallback would need a key.
- `OPENROUTER_API_KEY` for Jev, only once opinion text exists.

## Open questions

- Opinion text: CLERC is federal and covers 0.2% of gold cites, so it will not give text for this Colorado graph. Colorado opinions from the Caselaw Access Project or CourtListener bulk data (the graph already carries CourtListener URLs) look like the better source. Owner should pick.
- Hostname, and who goes on the Access allow-list.
- Whether request logs may hold raw question text (privacy) and for how long.
- Whether the landing page needs a call to action beyond "Try the demo" (waitlist form would need a backend).
- Whether the dense index covers only cases with titles (statutes have empty titles).
