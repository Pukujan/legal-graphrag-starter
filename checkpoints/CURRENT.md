# Current Repository Checkpoint

<!-- continuity:current {"active_task":"LGR-0003","active_task_file":"tasks/TASK-LGR-0003-decision-graph-enrichment.md","protocol_version":"0.1.0-draft","schema":"project-continuity.current.v1"} -->

This is an as-of projection; live GitHub issues own progression. Link the owning leaf, parent ancestry and dependencies for active work.

## Program state

Phase: decision and evidence graph enrichment.

## Completed

- ACS multi-agent hotload `0.1.0` applied at `5352653f4348e74afdf49e04cacc753f2b4beeb7` (official mesh-sync HEAD; not the train-certified `83fdf459c65016343ad5be1637f93ce0413525c7` revision).
- Full PCM `0.7.0` at `8cbfc3c8e9f13eadf2663fd6dd060588ee29399d`, CGM `0.5.12` at `fe2054ba323257510eff4e31de1dcd4a1460d1c7` (all eight modules), and OIO `0.1.0` at `2d1215502bc683e5bcbc6dc637b8b2b4cf28f3b7` installed from the official ACS mesh pins.
- The six core `.content-system` adapter files, per-feature filename legend, and explicitly labeled concept reference asset are present.
- PCM single-checkout continuity files and OIO issue intake are installed. No product implementation has begun.
- Local ACS setup commit `645f016c84cc7853745614e0c986a0a76f8331de` passed the local validator stack; remote PR/required gates are still pending.
- The unchanged starter baseline was executed at `102cf1cf347cc753622a753ae4cd16438abe8f8d`; see `baseline-audit.md` in the session scratch area for timing, graph counts and checksums.
- The bounded retrieval/ranking kernel, LangGraph wrapper, CLI, and Python contract-test job are implemented locally on `codex/citation-retrieval-baseline`. The unchanged public retrieval/ranking suite passes 66/66; see `docs/retrieval-baseline.md` for input hashes and the observed benchmark.
- The decision/evidence overlay, Jev Decisions adapter, strict answer parser, exact span/linker helpers, and runnable synthetic pipeline are implemented locally on `codex/decision-graph-enrichment`. The combined public suite passes 112/112; see `docs/decision-enrichment.md`. A synthetic Jev call succeeded, while local GLiNER model inference remains blocked by CPU metadata initialization.

## Active

- [LGR-0003 — decision and evidence graph enrichment](../tasks/TASK-LGR-0003-decision-graph-enrichment.md), owning leaf [issue #4](https://github.com/Pukujan/legal-graphrag-starter/issues/4), parent [issue #1](https://github.com/Pukujan/legal-graphrag-starter/issues/1), dependencies: issue #2 / LGR-0001 and issue #3 / LGR-0002. Primary writer: Luna (`decision_executor`). Branch: `codex/decision-graph-enrichment`, based on frozen kernel `dffc60f`.

## Queued

- Independent decision-stage holdouts are pending. Do not read their source until root releases them for evaluation after this implementation freeze.

## Blockers

Setup issue #2 and engine issue #3 remain upstream dependencies. The repository API reports `allow_auto_merge: false`; the rulesets endpoint returned an empty list, and branch protection could not be read because the GitHub integration returned HTTP 403. The workflow needs the `ACS_READ_TOKEN` repository secret to check out the private ACS source; hosted evidence reports the ACS adopter gate failed for the missing token while engine tests and stack-train passed. These are remote delivery blockers, not local validation claims.

## Next atomic action

Freeze the tested enrichment increment and give root the exact local SHA for independent decision holdouts. Remote ACS validation and merge gates remain unresolved; do not claim remote delivery.
