# Current Repository Checkpoint

<!-- continuity:current {"active_task":"LGR-0001","active_task_file":"tasks/TASK-LGR-0001-setup-acs-adopter-stack.md","protocol_version":"0.1.0-draft","schema":"project-continuity.current.v1"} -->

This is an as-of projection; live GitHub issues own progression. Link the owning leaf, parent ancestry and dependencies for active work.

## Program state

Phase: stack installed; product work not started.

## Completed

- ACS multi-agent hotload `0.1.0` applied at `5352653f4348e74afdf49e04cacc753f2b4beeb7` (official mesh-sync HEAD; not the train-certified `83fdf459c65016343ad5be1637f93ce0413525c7` revision).
- Full PCM `0.7.0` at `8cbfc3c8e9f13eadf2663fd6dd060588ee29399d`, CGM `0.5.12` at `fe2054ba323257510eff4e31de1dcd4a1460d1c7` (all eight modules), and OIO `0.1.0` at `2d1215502bc683e5bcbc6dc637b8b2b4cf28f3b7` installed from the official ACS mesh pins.
- The six core `.content-system` adapter files, per-feature filename legend, and explicitly labeled concept reference asset are present.
- PCM single-checkout continuity files and OIO issue intake are installed. No product implementation has begun.

## Active

- [LGR-0001 — ACS adopter setup](../tasks/TASK-LGR-0001-setup-acs-adopter-stack.md), owning leaf [issue #2](https://github.com/Pukujan/legal-graphrag-starter/issues/2), parent [issue #1](https://github.com/Pukujan/legal-graphrag-starter/issues/1), dependencies: none. External key: `KEYARGO-SETUP-001`. Primary writer: acs_executor (Luna). Branch: `codex/acs-hotload-install`.

## Queued

- No product task projections yet; root issue #1 lists follow-on deliverables.

## Blockers

No product work has started. GitHub issue tracking is enabled and setup issue #2 is open and assigned to Pukujan. The repository API reports `allow_auto_merge: false`; the rulesets endpoint returned an empty list, and branch protection could not be read because the GitHub integration returned HTTP 403. The workflow needs the `ACS_READ_TOKEN` repository secret to check out the private ACS source; secret availability is unverified. No Actions run or PR exists yet. The local setup branch has not yet been committed or pushed.

## Next atomic action

Rerun the pinned validators, commit setup-only changes, push `codex/acs-hotload-install`, and open a reviewable PR linked to issue #2. Keep governance and CI status pending until verified. Do not start product implementation until setup is committed and the task projection is current.
