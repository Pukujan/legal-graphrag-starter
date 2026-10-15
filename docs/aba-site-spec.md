# Legal research site: requirements for ABA

Status: requirements, 2026-10-15. ABA (headless app builder) builds the whole app, backend and frontend, and chooses
its own stack, libraries and database. This document says what the site must do and look like, not how to build it.
The retrieval engine in this repo is a separate service the app calls over HTTP; its contract is
[`docs/engine-api.md`](engine-api.md) (OpenAPI: [`docs/openapi-engine.json`](openapi-engine.json)).

## Purpose and audience

People who research case law (lawyers, clerks, law students, researchers) ask a legal question in plain English and get
a short answer in which every claim cites a real court opinion, with the passage it relies on one click away. The site
also shows, live and visually, how the cases were found, and publishes how well the search performs.

It is a research aid, not legal advice. No accounts and no login anywhere.

## Pages

1. **Landing (`/`).** Outcome-led copy, no technical words (never: graph, model, AI, LLM, embedding, retrieval,
   benchmark, pipeline, BM25, reranker, Octo, Jev). One clear call to action into the demo. Quiet ambient version of the
   star field (below) behind the page, no data, no labels. Footer: "Research aid only. Not legal advice. Always read the
   full opinion."
2. **Ask (`/demo`).** Question box, streamed answer, cited cases, the "why these cases" panel, the live star field and
   the stage workflow. Details below.
3. **Case view (`/cases/:id` or a side panel).** Case name, court, year, link when available, and its stored passages,
   with the cited span highlighted when opened from an answer.
4. **Benchmarks (`/benchmarks`), public.** Tabs:
   - *Results*: one row per frozen run from `GET /api/v1/benchmarks` (label, dataset, retriever variants, Recall@k,
     nDCG@10, MRR as reported). Show numbers exactly as the API returns them; do not compute new metrics.
   - *Per-query wins and losses*: for a selected run (`GET /api/v1/benchmarks/{run_id}`), the per-query rows, sortable,
     showing where graph expansion helped or hurt versus the text-only baseline.
   - *Query log*: anonymous recent questions asked on the site with stage timings, number of cited cases, confidence and
     abstain flag, stored in the app's own database.

## Behaviours on the Ask page

- The app sends the question to `POST /api/v1/retrieve` (server side, with the engine token) and relays the stage
  events to the browser as they arrive. Each stage appears the moment its event lands; never wait for the end.
- Stages, in order: question parse (citations found in the question), keyword search, meaning search, fusion, citation
  graph (one hop), rerank, optional Jev relevance, then answer writing and answer check, which the app performs.
- **Answer generation is the app's job.** Use a small local model behind an Ollama-compatible endpoint
  (`OLLAMA_BASE_URL`). Its prompt contains only the returned passages. Every sentence ends with an inline citation
  marker pointing at a returned case. Quotes must be exact substrings of returned passages (`passages[].text`,
  preferably a returned `span`); the app drops any sentence whose marker or quote does not match.
- **Answer check.** The app splits the answer into claims and sends each with its cited passage and `chunk_id` to
  `POST /api/v1/check`. It shows a per-claim support indicator, an overall confidence, and when `abstain` is true it
  says plainly that the evidence is not strong enough instead of presenting the answer as settled.
- **Final answer object** the app keeps and renders: answer text with inline citations; cited cases (id, name, court,
  year, link, supporting passage spans); per-claim support scores; `answer_confidence`; `abstain`.
- **Why these cases.** For each cited case, plain-language reasons built only from the stream's `provenance`
  (e.g. "matched your words", "close in meaning", "cited by a top result", "you cited it", "moved up to #2 on a closer
  read"). Never let the answer model write these.
- Errors: a visible, specific message for engine unavailable or loading (503), a bad request (422), or an `error`
  event mid-stream; keep whatever stages already arrived on screen.

## Look and feel

- Dark, calm and precise. Deep navy background, near-white text, muted grey secondary text; cyan and gold accents;
  green for supported, soft red for unsupported, grey for pending. Serif display headlines, clean sans body. Generous
  spacing, no dashboard chrome on landing or Ask.
- **Star field.** A full-bleed field of small points of light with a sense of depth, slight parallax with the pointer
  and slow drift. Each candidate case gets a stable position (derived from its id). As stages stream in: keyword hits
  glow warm white, meaning hits cyan, cases found by both gold; reranked top cases glide toward the centre in their new
  order with a small rank number; graph edges draw as thin lines growing from a case to its neighbour; Jev-accepted
  cases pulse green; cited cases keep a soft halo and brighten when their citation marker is hovered. Clicking a star
  opens the case.
- **Workflow.** A visible chain of the stages with the active stage animated, and each finished stage showing its count
  and duration in ms from the event. Skipped stages show their reason.
- Layout: desktop has the conversation column on the left and the star field with the workflow beside it; mobile puts a
  shorter star field on top, collapses the workflow to a progress row and puts the conversation below.
- Accessibility: every visual state also exists as text; honour reduced-motion (no drift, instant state changes);
  keyboard reachable; sufficient contrast.

## Data needs

- From the engine (only via its HTTP API; the app never gets an Octo key and never talks to Octo):
  `GET /api/v1/health`, `POST /api/v1/retrieve`, `POST /api/v1/check`, `GET /api/v1/cases/{id}`,
  `POST /api/v1/neighborhood`, `GET /api/v1/benchmarks`, `GET /api/v1/benchmarks/{run_id}`.
- The app's own database holds: query log rows (question text, timestamp rounded to the hour, stage timings, cited case
  ids, confidence, abstain, error) and nothing else.
- **Query logs hold no personal data**: no IP addresses, user agents, cookies, account or device ids, and no free-text
  fields other than the question. Strip email addresses, phone numbers and long digit runs from the question before
  storing. Logs are public on the Benchmarks page, so say on the Ask page that questions are logged anonymously.

## Engine API contract (summary; full schemas in `docs/engine-api.md`)

- Base URL from `ENGINE_BASE_URL` (local `http://127.0.0.1:8010`; a tunnel hostname later). Bearer token from
  `ENGINE_API_TOKEN`, kept server side.
- `POST /api/v1/retrieve` `{"query": "...", "k": 10}` streams `event: stage` (parse, bm25, dense, hybrid, graph, rerank
  (cross-encoder plus Jev relevance by default); each with `duration_ms`, `elapsed_ms`, candidates with ids, scores and ranks, graph edges), then
  `event: result` (ranked cases with metadata, `provenance`, verbatim passages and spans), then `event: done`.
- `POST /api/v1/check` `{"claims": [{"text", "cited_text", "citation", "chunk_id"}], "scorer": "minicheck"}` returns
  per-claim `calibrated_prob` and `accepted`, `answer_confidence`, `abstain`, and quote verification.
- Coverage today: 45,048 US court opinions with stored, quotable text: all 40,897 Colorado opinions in the Caselaw
  Access Project (Colorado Supreme Court and Court of Appeals, 1864 to 2019) plus 4,151 federal and other-state opinions
  from the CLERC benchmark, linked into a 547k-node citation graph. Copy may say "Colorado case law through 2019" but
  must not claim complete coverage of any other state, of federal law, of statutes, or of Colorado opinions after 2019.

## Env var names the app uses

`ENGINE_BASE_URL`, `ENGINE_API_TOKEN`, `OLLAMA_BASE_URL` (and the answer model name it chooses). Values are never
committed.
