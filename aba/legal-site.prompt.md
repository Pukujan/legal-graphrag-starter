# Build: legal research website (landing, ask, case view, public benchmarks)

You build the whole app, backend and frontend, in the stack of your choice, with your own database. Requirements are in
`docs/aba-site-spec.md` in the legal-graphrag-starter repo; this prompt restates them. Do not invent data: every case,
passage, score and benchmark number on screen comes from the retrieval engine API or the app's own query log.

## The engine you call

A separate HTTP service. Read its base URL from `ENGINE_BASE_URL` and its bearer token from `ENGINE_API_TOKEN`. Call it
only from your server side and never expose the token to the browser. Never connect to any database other than your own.

- `GET /api/v1/health` -> `{status, graph, engine: {status: loading|ready|error|disabled, ...}}` (no token needed).
- `POST /api/v1/retrieve` body `{"query": "<question>", "k": 10}` -> `text/event-stream`:
  `event: stage` x N, then `event: result`, then `event: done`; `event: error` on failure.
  Stage data: `{type:"stage", stage, duration_ms, elapsed_ms, ...}` where stage is `parse` (`citations`), `bm25`,
  `dense`, `hybrid` (`candidates[{doc_id, rank, score, ranks}]`), `graph` (`edges[{source,target,kind}]`,
  `added[{doc_id, via}]`), `rerank` (`candidates[{doc_id, rank, score, prev_rank}]`), `jev`; skipped stages carry
  `skipped: true, reason`.
  Result data: `results[{rank, case:{id,name,court,year,link}, score, score_kind, provenance[], passages[{chunk_id,
  text, spans[{start,end,text}]}]}]`.
- `POST /api/v1/check` body `{"claims":[{"text","cited_text","citation","chunk_id"}], "scorer":"minicheck"}` ->
  `{claims[{claim_index, calibrated_prob, accepted}], answer_confidence, abstain, quotes[{quote_verified}]}`.
- `GET /api/v1/cases/{id}` (URL-encode the id) -> `{case, authority, passages[]}`.
- `GET /api/v1/benchmarks` -> `{runs[{run_id, label, dataset_revision, summary, config, per_query_count}]}`;
  `GET /api/v1/benchmarks/{run_id}` -> full run with `per_query`.
- 401 bad token, 422 bad body, 503 engine loading or not configured.

## Pages

- `/` Landing: outcome copy for people who research case law, no technical words, one call to action to `/demo`,
  quiet ambient star field behind, footer "Research aid only. Not legal advice. Always read the full opinion."
- `/demo` Ask: question box; stream stages live into the star field and the workflow; then write the answer, check it,
  and show the answer with inline citation markers, the cited cases list, per-claim support, overall confidence, an
  abstain notice when `abstain` is true, and a "Why these cases" panel built only from `provenance`.
- `/cases/:id`: case details and stored passages, cited span highlighted.
- `/benchmarks` (public, no login): tabs Results, Per-query wins/losses, Query log.

## Answer writing (your backend)

Use a small local model through an Ollama-compatible endpoint at `OLLAMA_BASE_URL`. The prompt contains only the
returned passages. Every sentence ends with a citation marker for a returned case. Quotes must be exact substrings of
returned passage text. Drop any sentence whose marker or quote does not match, then send the remaining claims to
`/api/v1/check`. Keep the final answer object: text with inline citations, cited cases (id, name, court, year, link,
supporting spans), per-claim scores, `answer_confidence`, `abstain`.

## Look and feel

Dark navy, near-white text, cyan and gold accents, green supported, soft red unsupported, grey pending; serif headlines,
clean sans body; generous space. Star field: small points of light with depth, gentle parallax and drift; each case at a
stable position from its id; keyword hits warm white, meaning hits cyan, both gold; reranked top cases glide to the
centre with rank numbers; graph edges draw as growing thin lines; cited cases keep a halo and brighten when their marker
is hovered; click opens the case. Workflow: chain of stages, active one animated, finished ones show count and ms,
skipped ones show the reason. Desktop: conversation left, star field and workflow right. Mobile: short star field on top,
workflow as a progress row, conversation below. Reduced motion: no drift, instant changes. Every visual state also
appears as text.

## Query log

Store in your own database: question (with emails, phone numbers and long digit runs removed), timestamp rounded to the
hour, stage timings, cited case ids, confidence, abstain, error. No IPs, user agents, cookies or any user or device
identifiers. Tell users on the Ask page that questions are logged anonymously and shown publicly.

## Honesty in copy

The engine covers 4,151 US court opinions with stored text inside a larger citation graph. Do not claim more coverage,
legal accuracy, or any metric the API does not return.
