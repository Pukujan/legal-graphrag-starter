# Build: Casefinder website (landing, live demo, admin)

Build a React + TypeScript app on the existing scaffold (shadcn/ui, Tailwind, routes in `src/pages`, every component mounted). Three routes: `/` landing, `/demo` live demo, `/admin` admin. Do not invent data. Every number on screen comes from the API below. If a request fails, show an error state with the message.

## Config

Read the API base from `import.meta.env.VITE_API_BASE`, default `""` (same origin). All calls go to `${API_BASE}/api/v1/...`.

## Design

Dark default. Background deep navy `#070B1A`, ink `#E6EAF2`, muted ink `#8A93A8`, accent cyan `#5CE1E6`, gold `#F2C14E`, accepted green `#4ADE80`, rejected red `#F87171`, pending grey `#64748B`. Use the tokenized colors (`bg-background`, `text-primary`, `border`) mapped to these. Type: a serif display face for headlines (e.g. "Fraunces" or "Source Serif 4"), Inter for body. Generous spacing, no card grids of stats, no dashboard chrome on `/` or `/demo`.

Add these dependencies: `three`, `@react-three/fiber`, `@react-three/drei`, `@xyflow/react`, `react-markdown`.

### Shared StarField component

A full-bleed 2.5D canvas (`@react-three/fiber`). About 1,500 small points of light at random depth on the navy field, gentle parallax following the pointer, slow drift. Props: `mode: "ambient" | "live"` and, in live mode, a `nodes` map keyed by case id with a `state`. In ambient mode it has no labels and no interaction. Respect `prefers-reduced-motion`: no drift, no parallax, state changes are instant.

In live mode, case ids from the API are assigned stable positions (hash the id). States and looks:
- `lexical` warm white, `semantic` cyan, `both` gold (a case found by both).
- `reranked`: the top cases glide toward the centre, ordered by `to_rank`; show a small rank number next to each.
- `graph`: thin cyan lines grow from source to neighbour over 600 ms for each edge in the `graph` event.
- `accepted` green pulse, `rejected` fade to dim red, `pending_text` grey with a dotted ring.
- `cited`: soft gold halo. Hovering citation `[n]` in the answer brightens that star and shows the case name.
Clicking a star opens that case's card in the chat column.

## `/` Landing page

Atmosphere: StarField in `ambient` mode behind everything. No technical words anywhere on this page: never say graph, model, AI, LLM, embeddings, retrieval, benchmark, pipeline, Octo, Jev, BM25 or reranker. Use this copy as written:

- Headline: "Find the case that matters before opposing counsel does."
- Subhead: "Ask a question the way you'd ask a colleague. Get a short answer and the Colorado cases behind it, each one a click away."
- Primary button: "Try it with your question" → `/demo`. Secondary link: "See how answers are sourced" (scrolls to section 3).
- Section 1, title "Start with the question, not the search terms." Body: "You know what you're trying to find out. You shouldn't have to guess which words a court used. Type the question, and we bring back the cases that speak to it."
- Section 2, title "Every sentence points to a case." Body: "Each line of an answer carries a citation you can open. If we can't tie a sentence to a case, we leave it out."
- Section 3, title "You can see why a case showed up." Body: "Next to each answer is a short note on why each case made the list: which cases it cites, which cite it, and how it relates to your question."
- Section 4, title "Built around Colorado law." Body: "More than a hundred thousand Colorado cases, statutes and rules, and the citations between them."
- Closing band: "Bring the question you were going to spend the afternoon on." Button "Try it now" → `/demo`.
- Footer, small: "Research aid only. Not legal advice. Always read the full opinion."

Sections are short text blocks over the star field with plenty of space, fading in on scroll (respect reduced motion).

## `/demo` Live demo

Layout on desktop: left column (about 38% width) is the chat; centre-right is StarField in `live` mode; a narrow right strip holds the workflow. Under 900 px: star field on top (40vh), workflow becomes a horizontal step row, chat below.

### Chat column
- A textarea with placeholder "e.g. Can a Colorado landlord keep a deposit for normal wear and tear?" and an "Ask" button. Three example question chips that fill the box.
- A notice above the first answer, always visible, muted style: "We don't have the full text of these opinions yet, so answers describe which cases are relevant and how they connect. They don't quote or summarize holdings." The text comes from the `limitation` event when present; this is the fallback.
- Answer area streams `token` events. Render `[n]` markers as small superscript buttons; clicking scrolls to and highlights citation n.
- "Cited cases" list from the `citations` event: number, case name as a link to `url` (new tab, only if url is non-empty), court and date when present. Missing court/date are shown as "court not listed" / "date not listed", never invented.
- "Why these cases" panel (collapsible, open by default) from the `why` event: each case name followed by its reasons as short bullet lines.
- On `error`, show the stage and message and keep what already rendered.

### Workflow strip
`@xyflow/react`, vertical, fixed positions, not draggable. Nodes in order with these display labels: "Keyword match" (bm25), "Meaning match" (dense), "Merge" (fusion), "Re-rank" (rerank), "Follow citations" (graph), "Decision check" (jev), "Write answer" (answer). States: idle grey; active with animated edge into it and a pulsing border; done with count and ms (from `stage_done`). When every `jev` item is `pending_text`, the Decision check node shows "waiting for opinion text" in grey instead of a count.

### Data: `POST /api/v1/answer` (Server-Sent Events)

Request body JSON: `{"question": string, "k": 8}`. Response is `text/event-stream`. Because it is a POST, read it with `fetch` and a `ReadableStream` reader and parse `event:` / `data:` lines yourself (EventSource does not support POST). Abort the previous request when a new question is asked.

Every `data` is JSON with `run_id` and `t_ms`. Events:
- `run_started` `{question, graph_revision, stages: string[]}` → reset scene and workflow.
- `stage_started` `{stage}`.
- `candidates` `{stage: "bm25"|"dense"|"fusion", items: [{id, title, court, date, url, score, rank}]}` → bm25 items get `lexical`, dense items `semantic`, an id in both becomes `both`.
- `rerank` `{items: [{id, from_rank, to_rank, score}]}` → `reranked`.
- `graph` `{nodes: [{id, title, type, court, date, url}], edges: [{src, dst, kind}], truncated: boolean}` → add nodes, animate edges. If truncated, show "showing part of the neighbourhood" under the workflow.
- `jev` `{items: [{id, status: "accepted"|"rejected"|"pending_text"}]}`.
- `stage_done` `{stage, ms, count}`.
- `token` `{text}`.
- `citations` `{items: [{n, id, title, url, court, date}]}` → `cited`.
- `why` `{items: [{id, reasons: string[]}]}`.
- `limitation` `{text}`.
- `done` `{answer, dropped_sentences, total_ms}`.
- `error` `{stage, message}`.

Minimal reader:

```ts
const res = await fetch(`${API_BASE}/api/v1/answer`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({question, k: 8}), signal});
const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
let buf = "";
for (;;) { const {value, done} = await reader.read(); if (done) break; buf += value;
  let i; while ((i = buf.indexOf("\n\n")) >= 0) { const block = buf.slice(0, i); buf = buf.slice(i + 2);
    const ev = /^event: (.*)$/m.exec(block)?.[1]; const data = /^data: (.*)$/m.exec(block)?.[1];
    if (ev && data) handle(ev, JSON.parse(data)); } }
```

Also call `GET /api/v1/health` on load: `{status, graph: {revision, nodes, edges, source_dataset, synthetic}}`. If status is not `ready`, disable Ask and show "Warming up, one moment" and poll every 3 s. If `synthetic` is true, show a "sample data" badge.

Include a dev-only fixture mode (`?fixture=1`) that replays a recorded event list with realistic timing so the visuals can be checked without the backend. Label it "Sample replay" on screen. It must never run unless the query flag is set.

## `/admin` Admin

Plain, readable layout (this one may look like a tool). Access is handled by Cloudflare in front of the site; the app shows no login form. If an admin call returns 401 or 403, show "You need to sign in through the company login to see this page."

Tabs:
- **Runs**: `GET /api/v1/admin/runs?limit=50&cursor=` → `{runs: [{run_id, started_at, question, stage_ms: {bm25, dense, fusion, rerank, graph, jev, answer}, citation_ids: string[], dropped_sentences, error}], next_cursor}`. Table with time, question (truncated), total ms, citations count, error flag. Row click → `GET /api/v1/admin/runs/{run_id}` → `{run_id, events: [{event, data}]}` shown as a timeline, with a "Replay" button that feeds the events to a small StarField in live mode.
- **Benchmarks**: `GET /api/v1/experiments` → `{experiments: [...], graph_revision}`. Render each experiment's fields as returned. When the list is empty, show "No benchmark runs published yet." Do not draw any chart from made-up values.
- **Health**: `GET /api/v1/health` fields as a definition list.

## Done means
- All three routes render with the real API and with `?fixture=1` on `/demo`.
- No technical terms appear on `/`.
- Every `[n]` in an answer resolves to an item in the cited list.
- Missing fields show "not listed", never placeholder values.
