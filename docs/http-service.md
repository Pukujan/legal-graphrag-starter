# HTTP service implementation notes

The API contract is in [`backend-api.md`](backend-api.md). `legal_graph.server.create_app()` is a FastAPI factory with strict Pydantic request models. It exposes health, metadata search, exact authority lookup, neighborhood traversal, citation-prior ranking, and an empty experiments list under `/api/v1`; `/openapi.json` is FastAPI-generated.

Normal startup reads only the repository's frozen `data/nodes.jsonl` and `data/edges.jsonl`, constructs one `CitationGraphIndex` in the ASGI lifespan, and reuses it for requests. Importing `legal_graph.server` does not load the full graph. Tests inject an index, and a synthetic fixture is marked by `synthetic=True`. The API does not read the synthetic Octo storage test snapshot.

Search lowercases and matches substrings in authority IDs and titles, then returns the ID-sorted records. It does not search opinion text or embeddings. Request bodies reject extra fields and strict integer/string validation prevents coercion; the HTTP scan budget is capped at 10,000 edges per request. CORS is limited to the configured origins, with localhost development origins as the default.

Run locally on loopback with:

```sh
PYTHONPATH=. python -m uvicorn legal_graph.server:create_app --factory --host 127.0.0.1 --port 8000
```

Pinned runtime packages: FastAPI 0.143.0, Pydantic 2.14.1, Starlette 1.8.0, and Uvicorn 0.54.0. The test client uses HTTPX 0.28.1. The fixed HTTP contract suite passes 17 tests; all implemented public contract files pass 143 tests locally. Root's independent HTTP holdouts pass 132/132 against the frozen server code.

After that freeze and holdout, a real Uvicorn process on `127.0.0.1:8000` loaded the original JSONL snapshot and served all six route families plus `/openapi.json`. Health returned 119,770 nodes, 160,793 edges, the checksummed source revision, and `synthetic: false`. Search and exact authority lookup returned §1983 records. The default neighborhood returned 500 nodes/499 edges after scanning 1,856 edges and reported `node_budget` truncation; citation ranking returned three results; experiments correctly returned an empty list. No model calls were made. The current local server was left running for the downstream ABA integration.

This prototype has no authentication or deployment configuration. Do not expose it outside a trusted local environment until those concerns are designed separately.
