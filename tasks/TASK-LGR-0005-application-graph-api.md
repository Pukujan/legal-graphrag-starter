# TASK-LGR-0005 — Application Graph Api

<!-- continuity:task {"acceptance":["The unchanged HTTP contract tests pass against an injected synthetic graph.","Health, metadata substring search, exact authority lookup, bounded neighborhood, citation ranking, experiments and OpenAPI routes match the authored contract.","Strict request validation returns 422 for invalid types, limits, and unknown fields; CORS allows configured local origins and rejects unrelated origins.","The default service loads the frozen original JSONL graph once during startup, identifies its revision/source, and never substitutes the synthetic Octo test snapshot.","All implemented public contracts pass and a real loopback Uvicorn smoke is recorded after freeze."],"depends_on":["LGR-0002","LGR-0003","LGR-0004"],"goal":"Expose the tested legal citation graph engine through a bounded FastAPI HTTP API.","id":"LGR-0005","issue_url":"https://github.com/Pukujan/legal-graphrag-starter/issues/9","next_action":"Publish the synchronized frozen API increment as a stacked PR and report hosted checks; keep the local loopback service available for ABA integration.","owner":"Luna baseline_executor","priority":"high","protocol_version":"0.1.0-draft","schema":"project-continuity.task.v1","status":"active","why":"ABA needs a stable server interface for authorities, search, neighborhoods, rankings and experiment availability."} -->

- Status: implementation frozen locally; independent tests and loopback smoke passed, awaiting remote PR checks
- Owner: Luna baseline_executor
- Priority: high
- Depends on: retrieval issue #3 / LGR-0002, enrichment issue #4 / LGR-0003, and storage issue #7 / LGR-0004

## Goal

Expose the tested legal citation graph engine through a bounded FastAPI HTTP API.

## Identity and lineage

- Owning leaf: [issue #9](https://github.com/Pukujan/legal-graphrag-starter/issues/9)
- Parent: [issue #1](https://github.com/Pukujan/legal-graphrag-starter/issues/1)
- Dependencies: [issue #3 / LGR-0002](https://github.com/Pukujan/legal-graphrag-starter/issues/3), [issue #4 / LGR-0003](https://github.com/Pukujan/legal-graphrag-starter/issues/4), [issue #7 / LGR-0004](https://github.com/Pukujan/legal-graphrag-starter/issues/7)
- Primary writer: Luna (`baseline_executor`)
- Branch: `codex/application-graph-api`
- Base: storage freeze `03372bc1afdf32580f55288e8efe9e1a3898d027`

## Why

ABA needs a stable server interface for authorities, search, neighborhoods, rankings and experiment availability.

## Allowed files

- `legal_graph/server.py`
- `tests/test_http_contract.py`
- `docs/backend-api.md` (materialized unchanged from root's authored spec)
- `docs/http-service.md`
- `tasks/TASK-LGR-0005-application-graph-api.md`
- `checkpoints/CURRENT.md`
- `.github/workflows/project-gates.yml`
- `requirements.txt` and `requirements-dev.txt`
- `docs/CONTINUITY_INDEX.md` and `.continuity/documents.json` when maintained by continuity tooling

## Human outcome

ABA can call a bounded, versioned HTTP service for graph metadata, exact authorities, neighborhoods, rankings and available experiment artifacts without owning a parallel retrieval implementation.

## Scope and boundaries

- In scope: FastAPI/Pydantic/Uvicorn service, injected `CitationGraphIndex`, lazy default loading of the original local JSONL snapshot, strict request schemas, explicit local CORS, deterministic metadata-only search, and documented loopback smoke.
- Out of scope: React components or generated application, authentication/deployment, full-opinion or semantic search, model/provider calls on graph reads, experiments not backed by versioned artifacts, and any claim of legal effectiveness.
- Dependencies/uncertainty: prior retrieval, decision/enrichment, and Octo snapshot storage tasks are frozen on the local chain; public hosted checks still include the existing ACS token failure.

## Acceptance criteria

- [x] The unchanged root-authored HTTP contract suite passes with injected synthetic graph data (17/17).
- [x] Health, deterministic metadata substring search, exact authority lookup, bounded neighborhood, citation ranking, empty experiments and OpenAPI match the authored contract.
- [x] Pydantic rejects invalid types/limits/unknown fields with 422; CORS accepts the configured localhost origin and rejects an unrelated origin.
- [x] Default graph loading is deferred to application lifespan, occurs once per app, and reads only the original local JSONL dataset; injected test graphs are visibly marked synthetic when requested.
- [x] All implemented public contracts pass (143/143), root independent HTTP holdouts pass (132/132) on frozen server commit `20be27fc4fae6c2893390322b26f9877b683e006`, and real loopback Uvicorn smoke succeeds on the original graph after the holdout.

## Evidence and sources

Link repository state at a revision and cite external factual claims directly. Record commands and results for claims that need verification.

- Issue #9 was fetched through the connected GitHub integration on 2026-10-14; it is open and names LGR-0005, parent #1, dependencies #3/#4/#7, writer, and branch as recorded above.
- Local continuity issue verification was attempted; the pinned tool cannot launch `gh` in this environment. The GitHub connector successfully returned issue #9 and its current acceptance.
- Storage base freeze: `03372bc1afdf32580f55288e8efe9e1a3898d027`; root reported 126 public tests and 123 independent holdouts passing on that exact base.
- Root's authored `backend-api.md` and `test_http_contract.py` were copied byte-for-byte before implementation. Initial red run failed collection with `ModuleNotFoundError: legal_graph.server`, as expected.
- Installed runtime pins in the scratch dependency target: FastAPI 0.143.0, Pydantic 2.14.1, Starlette 1.8.0, and Uvicorn 0.54.0; HTTPX 0.28.1 is the test client dependency. Local `PYTHONPATH` points to `/workspace/scratch/511fd205df68/engine-deps`.
- HTTP contract command currently passes: `python -m pytest -q tests/test_http_contract.py` → 17 passed. Full public suite currently passes: `python -m pytest -q tests` → 143 passed.
- Frozen implementation commit `20be27fc4fae6c2893390322b26f9877b683e006` passed root's independent 132/132 HTTP holdouts; head and working tree were unchanged by the holdout.
- Post-holdout loopback Uvicorn smoke at `127.0.0.1:8000` verified health (119,770 nodes/160,793 edges, `synthetic: false`), search, exact §1983 authority lookup, default one-hop neighborhood (500 nodes/499 edges, 1,856 scanned, node-budget truncated), ranking, empty experiments, and all six OpenAPI route paths. Generated schema exported to the session scratch artifact; server remains available for ABA.
- CI now runs `pytest -q tests`, covering every implemented public contract. The prior hosted run 38515779328 showed engine-tests success and stack-train success; adopter validation failed only at the missing ACS_READ_TOKEN guard and aggregate gates failed accordingly. This API branch's hosted run is pending.

## Reproduction details (only when needed)

Starting revision, material inputs/configuration, runtime, exact command or prompt, observed result, and limitations.

## Related records

- Required leaf owning issue, parent ancestry and dependencies: issue #9 → parent #1; depends on issues #3, #4, and #7.
- Primary writer / branch / source issue revision / as-of status: Luna baseline_executor / `codex/application-graph-api` / issue #9 verified open through GitHub connector on 2026-10-14.
- Related PR/CI evidence and push receipt: implementation is local and frozen; remote PR/checks pending. Existing CI aggregate still includes the unresolved ACS token gate.

## Checkpoint log

2026-10-14 — Materialized authored spec and HTTP contract unchanged; confirmed expected red import failure; implemented the FastAPI factory and all six `/api/v1` route families. The 17 fixed HTTP tests and 143-test public suite pass. Root's independent holdouts passed 132/132 on `20be27fc4fae6c2893390322b26f9877b683e006`. A real loopback Uvicorn smoke on original data verified all routes, default limits and OpenAPI; the local service remains available on `127.0.0.1:8000`. Next: publish the synchronized snapshot stacked on the storage branch and report hosted checks.

## Handoff

Read PROJECT → CURRENT → this task → minimum relevant spec. Checkpoint before stopping.
