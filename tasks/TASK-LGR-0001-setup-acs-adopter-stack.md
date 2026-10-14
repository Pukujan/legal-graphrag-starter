# TASK-LGR-0001 — Setup ACS Adopter Stack

<!-- continuity:task {"acceptance":["Pinned ACS install has READY state and records the exact mesh-sync SHA and train-certification discrepancy.","Full CGM adapter validation, ACS hotload_check, OIO check, PCM validate, and PCM preflight all pass.","Setup branch contains only stack adoption, adapter/reference provenance, coordination, continuity, and validation workflow files; no product implementation.","Changes are committed and pushed on codex/acs-hotload-install and a reviewable PR is opened against master.","Report actual CI, branch ruleset, required gates, and auto-merge evidence without claiming unobserved outcomes."],"depends_on":[],"goal":"Install and validate the complete ACS adopter stack","id":"LGR-0001","issue_url":"https://github.com/Pukujan/legal-graphrag-starter/issues/2","next_action":"Rerun pinned validators, commit the local setup branch, push it, and open a reviewable PR; record remote governance status from live evidence.","owner":"acs_executor (Luna)","priority":"p0","protocol_version":"0.1.0-draft","schema":"project-continuity.task.v1","status":"active","why":"Create a verified, issue-led, reviewable project foundation before any product implementation."} -->

- Status: active
- Owner: acs_executor (Luna)
- Priority: p0
- Depends on: none

## Goal

Install and validate the complete ACS adopter stack.

## Why

Create a verified, issue-led, reviewable project foundation before any product implementation.

## Allowed files

- `.content-system/**`, `assets/legal-graph-concept*`, `.coord/**`, `.continuity/**`, `schemas/**`, `.oio/**`, `.github/**`, `AGENTS.md`, `HANDOFF.md`, `PROJECT.md`, `checkpoints/**`, `docs/CONTINUITY_INDEX.md`, `stack-manifest.json`, and this task file.
- Do not implement product UI, retrieval, decision, or backend features under this setup task.

## Human outcome

The new fork has a reproducible, pinned agent setup with clear task ownership, truthful content guidance, source provenance, and automated checks before product work begins.

## Scope and boundaries

- In scope: full ACS multi-agent hotload `0.1.0`; full PCM; all eight CGM modules; OIO; content adapter and concept provenance; per-feature media filename legend; setup workflow; continuity projections; issue/role linkage; exact validators; setup PR.
- Out of scope: product implementation; changing ACS/PCM/CGM/OIO or other upstream repositories; claiming CI, protection, or merge status before observing them.
- Dependencies/uncertainty: GitHub Issues are enabled and owner setup task #2 is open. The public adopter workflow checks out private ACS at a pinned commit and therefore requires a repository Actions secret `ACS_READ_TOKEN` with read-only access; secret availability is unverified. Branch ruleset and auto-merge settings need separate remote verification. ACS revision `5352653f4348e74afdf49e04cacc753f2b4beeb7` is official mesh-sync HEAD, not train-certified; it differs from certified ACS `83fdf459c65016343ad5be1637f93ce0413525c7` only in `stack-mesh.json`.

## Acceptance criteria

- [x] Exact current ACS/PCM/CGM/OIO pins and ACS revision discrepancy are recorded in `.coord/hotload.lock.json`, `stack-manifest.json`, and the checkpoint.
- [x] The full CGM adapter validator reports `VALID`; ACS `hotload_check` reports `OK` without skipping CGM; OIO `--check` reports `VALID`; PCM `validate` and `preflight` report `VALID` / `TARGET_VALID`.
- [x] `.github/workflows/project-gates.yml` includes pinned adopter checks, reusable train check, and a fail-closed aggregate job named exactly `gates`; YAML parses locally.
- [x] The asset path uses CGM’s safe-twin filename helper and has its required per-feature legend in `.content-system/filename-legends/`.
- [ ] Commit setup-only changes and push `codex/acs-hotload-install`.
- [ ] Open a reviewable PR and record observed CI, ruleset/required-gate, and auto-merge state.

## Evidence and sources

GitHub issue #1 is the parent project issue. GitHub issue #2 is this owning leaf issue and carries external coordination key `KEYARGO-SETUP-001`; canonical PCM task ID is `LGR-0001`. The leaf issue is open and assigned to Pukujan as of 2026-10-14T04:55:00Z. The untouched fork base is `102cf1cf347cc753622a753ae4cd16438abe8f8d`. Local validators pass; no commit, push, PR, or GitHub Actions run is recorded yet. GitHub repository metadata reports auto-merge disabled and no repository rulesets; the branch-protection endpoint returned HTTP 403 to the connected integration. CI must have `ACS_READ_TOKEN` for the private ACS checkout; secret availability is not observable through the current connector.

## Reproduction details

Dependency checkouts are under the ACS cache at their exact mesh pins: PCM `8cbfc3c8e9f13eadf2663fd6dd060588ee29399d`, CGM `fe2054ba323257510eff4e31de1dcd4a1460d1c7`, and OIO `2d1215502bc683e5bcbc6dc637b8b2b4cf28f3b7`. ACS checkout is official mesh-sync HEAD `5352653f4348e74afdf49e04cacc753f2b4beeb7`. The isolated validator environment uses `jsonschema==4.23.0`. CGM, ACS, OIO, PCM validate, PCM preflight, ACS pin projection, workflow YAML, and continuity document checks pass. `continuity issue verify` could not run because `gh` is not installed; issue #2 was independently fetched through the GitHub connector and confirmed open.

## Related records

- Required leaf owning issue, parent ancestry, and dependencies: leaf [#2](https://github.com/Pukujan/legal-graphrag-starter/issues/2), parent [#1](https://github.com/Pukujan/legal-graphrag-starter/issues/1), dependencies none.
- External coordination key / canonical task ID: `KEYARGO-SETUP-001` / `LGR-0001`.
- Primary writer / branch / source issue revision / as-of status: acs_executor (Luna) / `codex/acs-hotload-install` / issue #2 updated 2026-10-14T04:55:00Z / active.
- Related PR/CI evidence and push receipt: pending; no remote commit or run exists.

## Checkpoint log

No checkpoints yet.

## Handoff

Read PROJECT → AGENTS → CURRENT → this task → minimum relevant spec. Check the live issue before resuming. Do not begin product work until setup changes are committed and the issue/task projection is current.
