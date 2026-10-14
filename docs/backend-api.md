# Application API contract

This is a planned interface until its owning implementation issue passes tests. ABA owns every React component and consumes this contract; the engine writer owns the Python HTTP service. Use ready-made FastAPI, Pydantic validation, CORS middleware and Uvicorn. Bind the local service to loopback by default. Provider credentials stay on the server.

## Version and data
All routes are under `/api/v1`. Each response identifies `graph_revision`. Authority identifiers are exact strings, including Unicode, whitespace and slashes. Return original node and edge attributes. Default graph is the frozen original local dataset; the hosted synthetic storage test snapshot must never silently replace it. Any explicitly supplied synthetic graph has a synthetic marker in health metadata.

| Route | Request | Response |
|---|---|---|
| GET health | none | status ready and graph revision, nodes, edges, source_dataset, synthetic |
| GET search | q string, limit 1–100 default20 | results original node records, graph_revision |
| GET authority | id exact query parameter | node original record, graph_revision; 404 if missing |
| POST neighborhood | seeds, depth, direction, edge_kinds and budgets | unchanged retrieval envelope plus graph_revision |
| POST retrieve | same request plus k 1–500 | unchanged ranking envelope plus graph_revision |
| GET experiments | none | experiments list and graph_revision; empty until a versioned artifact exists |

Search is a deterministic case-insensitive substring lookup over authority IDs and available titles, with ID ordering. It is not semantic/full-opinion search. Neighborhood defaults are incoming direction, cites kinds, depth1, max_nodes500/max_edges1000/max_scanned_edges10000. HTTP requests may not raise the scan budget above10000. Unknown request fields and invalid types/limits return422. Empty/all-missing seeds follow the existing Python contract. Model/provider calls do not occur during health, search, authority or graph reads.

## Wiring and UI
`legal_graph.server.create_app(index=None, *, synthetic=False, allowed_origins=(...))` supports a supplied CitationGraphIndex for tests and the original dataset for normal startup. Prefer the actual LangGraph wrapper for neighborhood orchestration, with an identical result contract. OpenAPI is available at `/openapi.json`. Explicit local development CORS origins are supported; unrelated origins are rejected. The server stays a research prototype until deployment and authentication are separately configured.

ABA receives a configurable API base URL, these response schemas and generated PRD copy. Its bounded 3D graph, table and inspectors render the same records. Display missing seeds, truncation and revision; open only source URLs actually present. Display unavailable experiments or extraction states instead of inventing metrics. Stable labels and accessible fallbacks are part of ABA's generated application.
