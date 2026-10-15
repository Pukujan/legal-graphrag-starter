# LGR-0004 — Existing Octo graph snapshot storage

<!-- continuity:task {"acceptance":["Approved storage contracts pass unchanged with parameterized, snapshot-pinned reads.","The app-owned legal_graph schema stages immutable snapshots, enforces expected row counts before readiness, records caller-computed source digest/provenance, and atomically activates ready revisions without modifying Octo infrastructure or native graph state.","A small explicitly synthetic snapshot round-trips through the existing workspace SQL API when the scoped key permits writes.","Storage and existing engine contract suites pass; hosted CI and merge facts are reported exactly."],"depends_on":["LGR-0001","LGR-0002","LGR-0003"],"goal":"Add an immutable app-owned citation snapshot store over the existing Octo workspace SQL API.","id":"LGR-0004","issue_url":"https://github.com/Pukujan/legal-graphrag-starter/issues/7","next_action":"Freeze the tested local branch and hand its exact SHA to root for independent storage holdouts.","owner":"Luna acs_executor","priority":"high","protocol_version":"0.1.0-draft","schema":"project-continuity.task.v1","status":"active","why":"The engine needs a consistent, source-faithful storage interface backed by the existing Octo workspace database."} -->

## Identity and lineage

- Owning leaf: [issue #7](https://github.com/Pukujan/legal-graphrag-starter/issues/7)
- Parent: [issue #1](https://github.com/Pukujan/legal-graphrag-starter/issues/1)
- Dependencies: setup [issue #2 / LGR-0001](https://github.com/Pukujan/legal-graphrag-starter/issues/2), retrieval [issue #3 / LGR-0002](https://github.com/Pukujan/legal-graphrag-starter/issues/3), and enrichment [issue #4 / LGR-0003](https://github.com/Pukujan/legal-graphrag-starter/issues/4)
- Primary writer: Luna (`acs_executor`)
- Branch: `codex/octo-graph-storage`
- Base: frozen enrichment commit `d9e5aa9cd7fdfcba678724d4ac3bb729afbb8508`

## Goal

Provide `GraphSnapshotStore(execute)` over Octo's existing workspace SQL endpoint. Preserve exact authority IDs and source payloads. Pin every page to one immutable snapshot and use bound parameters for identifiers, cursors and limits.

## Acceptance

- Materialize `root-specs/test_storage_contract.py` byte-for-byte and record the expected red run before implementation.
- Implement `get_active_snapshot`, `get_authorities`, `get_citation_edges`, and `get_authority` against the approved executor contract.
- Add a minimal migration in the adopter's `legal_graph` schema with staging, source digest/provenance metadata, expected-count enforcement on readiness and one-statement atomic activation. A future importer must compute and verify canonical source digests; this increment does not implement corpus ingestion.
- Read at most 1,000 page items with one sentinel row; reject malformed/error, multi-statement, or truncated API results.
- Use a synthetic-only, small round-trip through the already-provisioned Legal RAG workspace when write access is available. Label it as synthetic and leave it isolated from source data.
- Run unchanged root storage tests and the existing retrieval, ranking, decision, provider and annotation suites. Freeze the local commit for independent root storage holdouts before any remote publication.

## Boundaries

No new database, connection-string exposure, corpus bulk import, destructive cleanup, native graph rebuild, Octo backend/repository change, support-repository edit, or application UI work.

## Observed evidence and limits

- Issue #7 was read from GitHub on 2026-10-14 and is open with matching task identity and dependencies.
- The existing workspace SQL API and bound parameters succeeded in read-only probes. Before this task, the database had no application tables or views. This task installed only the app-owned `legal_graph` schema and two synthetic test revisions.
- A live read-only request containing two `SELECT` statements returned `statementCount: 2` with only the last result. This consumer must issue one statement per call and reject results whose statement count is not one.
- Native graph health describes a separate epistemic projection; it is not a citation-edge storage surface.
- Hosted setup CI remains blocked at private ACS source access; no hosted storage or merge result is claimed.

## Next action

Freeze the tested local branch and hand the exact SHA to root for independent storage holdouts before remote publication.

## Checkpoint log

- Red run: the unchanged root-authored contract initially failed collection with `ModuleNotFoundError: legal_graph.storage`, as expected before implementation.
- Implementation: added the parameterized snapshot reader, app-owned snapshot schema and explicit synthetic/source provenance fields. Added the approved storage suite to the hosted engine test job.
- Local validation: the unchanged storage suite and existing retrieval, ranking, decision, provider, and annotation suites pass 126/126.
- Existing workspace smoke: created only explicitly synthetic snapshots through the existing Octo SQL API. Verified staged counts, ready-only activation, exact Unicode authority and edge attributes, same-revision reads, and that an intentionally incomplete staging revision stays unactivated. The canonical fixture digest calculation and stored value are recorded in `docs/storage-contract.md`; the database stores the supplied digest but does not hash or compare JSONB rows itself. No general source-import digest verifier is implemented.
- No source corpus import, Octo infrastructure setup, native graph mutation, or remote publication occurred.
- Next: freeze the local branch and hand the exact commit to root for independent storage holdouts.
