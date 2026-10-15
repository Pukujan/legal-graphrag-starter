# Current Repository Checkpoint

<!-- continuity:current {"active_task":"LGR-0005","active_task_file":"tasks/TASK-LGR-0005-application-graph-api.md","protocol_version":"0.1.0-draft","schema":"project-continuity.current.v1"} -->

This is an as-of projection; live GitHub issues own progression. Link the owning leaf, parent ancestry and dependencies for active work.

## Program state

Phase: application graph API.

## Completed

- ACS multi-agent hotload `0.1.0` applied at `5352653f4348e74afdf49e04cacc753f2b4beeb7` (official mesh-sync HEAD; not the train-certified `83fdf459c65016343ad5be1637f93ce0413525c7` revision).
- Full PCM `0.7.0` at `8cbfc3c8e9f13eadf2663fd6dd060588ee29399d`, CGM `0.5.12` at `fe2054ba323257510eff4e31de1dcd4a1460d1c7` (all eight modules), and OIO `0.1.0` at `2d1215502bc683e5bcbc6dc637b8b2b4cf28f3b7` installed from the official ACS mesh pins.
- The six core `.content-system` adapter files, per-feature filename legend, and explicitly labeled concept reference asset are present.
- PCM single-checkout continuity files and OIO issue intake are installed; issue-led product tasks follow those controls.
- Local ACS setup commit `645f016c84cc7853745614e0c986a0a76f8331de` passed the local validator stack; remote PR/required gates are still pending.
- The unchanged starter baseline was executed at `102cf1cf347cc753622a753ae4cd16438abe8f8d`; see `baseline-audit.md` in the session scratch area for timing, graph counts and checksums.
- The bounded retrieval/ranking kernel, LangGraph wrapper, CLI, and Python contract-test job are implemented locally on `codex/citation-retrieval-baseline`. The unchanged public retrieval/ranking suite passes 66/66; see `docs/retrieval-baseline.md` for input hashes and the observed benchmark.
- The decision/evidence overlay, Jev Decisions adapter, strict answer parser, exact span/linker helpers, and runnable synthetic pipeline are implemented locally on `codex/decision-graph-enrichment`. The combined public suite passed before storage; see `docs/decision-enrichment.md`. A synthetic Jev call succeeded, while local GLiNER model inference remains blocked by CPU metadata initialization.
- The app-owned Octo snapshot reader and schema are implemented on `codex/octo-graph-storage`. The public storage and prior engine suites pass 126/126. A small synthetic-only live workspace round-trip validated two complete revisions, Unicode identifiers and edge attributes, and an incomplete staging revision that stayed inactive; see `docs/storage-contract.md`.
- Root's independent storage holdouts passed 123 tests against freeze `03372bc1afdf32580f55288e8efe9e1a3898d027`; checkout ownership is released for the HTTP service increment.
- The root-authored API contract and HTTP tests are materialized unchanged. The FastAPI service now passes its HTTP contract suite (17/17) and the combined public suite (143/143); work is active on `codex/application-graph-api`.
- Frozen API implementation `20be27fc4fae6c2893390322b26f9877b683e006` passed root's independent 132/132 HTTP holdouts without a tree change. A subsequent loopback Uvicorn smoke on the original graph verified all endpoints, bounded truncation and `/openapi.json`; the local service remains running on `127.0.0.1:8000` for ABA integration.

## Active

- [LGR-0005 — application graph API](../tasks/TASK-LGR-0005-application-graph-api.md), owning leaf [issue #9](https://github.com/Pukujan/legal-graphrag-starter/issues/9), parent [issue #1](https://github.com/Pukujan/legal-graphrag-starter/issues/1), dependencies: issues #3 / LGR-0002, #4 / LGR-0003, and #7 / LGR-0004. Primary writer: Luna (`baseline_executor`). Branch: `codex/application-graph-api`, based on storage freeze `03372bc1afdf32580f55288e8efe9e1a3898d027`.

## Queued

- Publish the synchronized LGR-0005 snapshot stacked on the storage branch and report hosted check results. Keep the running loopback service available to ABA; do not inspect holdout sources.

## Blockers

Hosted delivery remains blocked on `ACS_READ_TOKEN`: the latest retrieval CI's engine tests and stack-train passed, while ACS adopter validation and aggregate gates failed before running the private ACS validators. Auto-merge is unavailable in the repository settings, branch protections cannot be read through the current connection, and no required remote checks are claimed available. This work remains a local increment pending its own hosted run.

## Next atomic action

The API increment has local and independent verification; its hosted run and PR are pending. Existing CI may still fail at missing `ACS_READ_TOKEN`. Repository auto-merge remains unavailable, so no merge claim is made.
