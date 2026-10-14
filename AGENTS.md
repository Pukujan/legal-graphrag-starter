# Agent Operating Contract

## Start

Read PROJECT → CURRENT → active TASK → minimum relevant spec before editing.

For GitHub repositories, verify the live linked issue with `continuity issue verify <TASK-ID>` before resuming; the issue owns task scope and lifecycle, merged default-branch history owns accepted code, and PR checks/merge records own delivery. Resolve discrepancies from the issue before editing.

When a GitHub issue reference appears in a pull-request description or commit message, use a supported issue-closing keyword only when merging should complete that issue. GitHub treats `close`, `closes`, `closed`, `fix`, `fixes`, `fixed`, `resolve`, `resolves`, and `resolved` followed by an issue reference as a close directive; negation does not cancel it. For progress-only work, link with `Refs #<number>` or the GitHub sidebar. After each merge, verify the live issue state before changing task status. See [GitHub's issue-linking rules](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue).

<!-- pcm:github-progression:start -->
## GitHub-owned progression

GitHub Issues are required for PCM-governed project work and own task scope, acceptance, priority, ownership, dependencies, lifecycle and durable project progression. Merged default-branch history owns accepted code and normative/domain documents; PR checks and merge records own delivery facts. Checked-in PROJECT/CURRENT/TASK/checkpoint/handoff documents are mandatory versioned projections for task state, not a parallel authority. Local files, registries, context packs and chat are ephemeral execution aids. Domain-document ownership stays with the target project.

Every issue progress update MUST link the leaf child issue that owns the work, its parent ancestry and dependencies (or explicitly none). A top-level deliverable identifies itself as the leaf and says parent: none. Create one child per independently deliverable scope, never one per comment. Record task ID, primary writer and branch on the issue before creating its repository projection. Re-read live issues and relevant source revisions before resuming; the issue verifier checks identity/status, not semantic agreement.

Authorized owner/user direction can revise intent: record it on the owning GitHub issue with a correction/supersession link before dependent work. It cannot alter observed CI/merge facts or waive required gates. Stale projections yield to their field's authority. If direction, ownership or evidence conflicts remain unresolved, pause affected work and record uncertainty; continue independent safe work. One primary writer owns each task branch/checkpoint stream. Coordinate shared-document edits through linked issues/PRs, re-read the current base and reconcile concurrent changes; never force-push or overwrite another writer. Issue prose is not an atomic lock.

Label observed results, repository/external evidence, agent reports and inference separately. Preserve contradictory evidence with source/revision and mark conclusions disputed or unknown until resolved. Append correction/supersession evidence; never rewrite checkpoint history. An upstream correction MUST identify affected descendants and assumptions on their issues; pause, re-plan and revalidate dependent work before resuming. Follow explicit parent/dependency links within the affected scope; cycles or unknown lineage block affected claims. No graph database, local canonical ledger or autonomous polling agent is required.

Before every push, synchronize relevant docs and task/checkpoint projections, CURRENT/HANDOFF when affected, and reviewed catalog/generated index. Record leaf/parent/dependency links, source issue/comment revision, as-of status, evidence, blockers and next action. Commit product/docs first; `continuity checkpoint` then commits and synchronously pushes the checkpoint with a stable request ID. After every successful push, manually publish a leaf issue receipt keyed by request ID and exact pushed SHA, linking changed docs/checkpoint, PR, tests and pending gates; add a linked parent progression update. Retry a missing receipt without another checkpoint/push; inspect for the same key before posting. --receipt-repo and --receipt-issue are opt-in and still require a proven lookup; omit them and the receipt stays manual. Automatic issue-comment synchronization is not implemented; issue #67 is CLOSED (owner freeze decision 2026-09-25) and its unmet guaranteed-completion acceptance transferred to #110.

Required CI and GitHub auto-merge are mandatory. Arm auto-merge only after the increment's final push: a later push races the merge window and strands outside accepted history. Verify protection, required reviews/checks on the exact current-base or merge-queue candidate, and auto-merge; missing, failed, skipped, stale or unverified gates fail closed: no completion or cleanup. After CI/merge, append the exact check results, PR/merge SHA and live issue status to the leaf and link the parent update; fetch and verify accepted history. Reconcile material doc/status corrections in a new synchronized increment. Receipt-only transitions need no recursive doc commit: docs retain an explicit as-of/pending state and point to the live issue. Never label local-only or merely pushed work delivered. Preserve unsafe resources and keep incomplete issues open.
<!-- pcm:github-progression:end -->

<!-- pcm:issue-log-format:start -->
## Issue log format (issue-log-format 1.2.0)

<!-- pcm:policy {"id":"issue-log-format","policy_version":"1.2.0","protocol_version":"0.1.0-draft"} -->

Write issue logs, progress updates, and pull requests in one plain-language shape a newcomer can follow. Pick the tier by the kind of issue, not by preference. **Core tier (every issue log):** title states the problem and intended direction; a 1-3 paragraph summary naming who/what is affected, the consequence, and what this proposes; identity and lineage (leaf owning issue, parent ancestry or none, task ID, primary writer, branch); observed facts vs interpretation, with inferences labelled *inferred*; acceptance criteria with numeric thresholds marked *(proposed)* when untested; boundaries/non-goals and one next action. **Investigation tier (incidents, failures, research, design issues):** numbered symptoms; hypotheses with Status, confirm/refute, and experiment; evidence with provenance; a **Counter-signal** entry when one exists; honest caveat; problems-vs-gaps; a **Proposal** labelled *(proposal)* stating none of it exists unless named as existing. **Pull requests open reader-first:** problem and consequence, what changes, how to verify, and what stays unchanged; lineage links; evidence and one next action; long logs collapsed or linked; reference issues with "Refs #<number>" and use closing keywords only when closing at merge is intended. **Diagrams (mermaid):** when a record describes a flow with 4+ ordered steps or 2+ branches, add a fenced mermaid diagram *and* keep an adjacent text list or table so the record survives render failure; default to `graph TD` (vertical) because wide `LR` flows shrink to illegible strips on phones — reserve `LR` for 4 or fewer short nodes; cap 8 nodes and 6-word labels; wrap diagrams that may exceed the container width inside `<details>` (GitHub mounts the renderer lazily on expand); preview the rendered diagram before publishing (broken syntax shows a visible parse error) and never cite renderer URLs as standalone sources. **Readability rules:** give every SHA, comment id, flag, file path, or tool name a plain-word meaning in the same sentence before it carries load; write evidence as the claim first, numbers as support (“nothing this change could break failed (263 tests, same six machine-environment failures as before)”), never bare counts; no unexplained acronym or bare identifier on first use in any tier; PR openings and checkpoint Completed/Next lines start with one problem sentence a newcomer can follow; the rule set applies to CURRENT projections and checkpoint entries exactly as to issue logs. No private absolute paths or secrets; link rather than paste long logs. See `https://github.com/Pukujan/project-continuity-modules/blob/8cbfc3c8e9f13eadf2663fd6dd060588ee29399d/docs/ISSUE_LOG_FORMAT.md` for the full format, exemplar, and examples.
<!-- pcm:issue-log-format:end -->

## Continuity path naming

New continuity-managed source paths (tasks, claims, adopter folders, new prose filenames) use pronounceable words a newcomer can say aloud. See `https://github.com/Pukujan/project-continuity-modules/blob/8cbfc3c8e9f13eadf2663fd6dd060588ee29399d/docs/CONTINUITY_PATH_NAMING.md`. Keep existing public paths stable; do not rewrite merged history for naming alone. Generated artifact/media filenames belong to CGM ([content-generation-modules#26](https://github.com/Pukujan/content-generation-modules/issues/26)) — pin and apply that helper when generating content; do not reimplement it in PCM.

Store checkout roots only in the private per-device registry with `continuity workspace register --root <checkout>`. Before creating a worktree, inspect registered roots and Git's worktree list. Reuse one clean, unlocked matching task branch; stop on dirty, locked, conflicting, or ambiguous matches. Do not scan drives or copy absolute paths into shared handoffs.

## Scope

Work only inside the active bounded task. Split or revise the task before materially expanding scope.

## Workspace mode: single checkout

Use the main checkout for sequential work. Do not create clones or linked worktrees. This is the strictest and simplest option when parallel isolation is not needed.

## Continuity records

<!-- pcm:policy {"id":"continuity-records","policy_version":"1.3.0","protocol_version":"0.1.0-draft"} -->

For continuity issues, progress updates, pull requests, and project-state documents, explain the human problem and outcome first, then scope, status, linked evidence, and one next action. Cite external claims and tie repository claims to a revision, issue, PR, or CI result. Record reproduction details only when needed. Keep PR openings skimmable; link long logs. Preserve existing project ownership outside continuity. Do not claim automatic tracker synchronization or chat capture unless implemented and tested.

## Checkpoint

Before stopping after meaningful work, append completed work, exact evidence, decisions, changed paths, blockers, and one next atomic action.

If canonical continuity state is temporarily unavailable, treat that as degraded continuity rather than an execution blocker: keep safe authorized work moving, use an already-authorized alternate checkout/host, and run `continuity checkpoint <TASK-ID> --root <canonical-root> --recovery-root <alternate-root> --agent <name> --completed <work> --evidence <result> --next <next-action>` to write the JSON recovery receipt under `.continuity/recovery/`. Do not write an ad-hoc checkpoint under `checkpoints/`, replace the alternate task file, treat a physical worktree as project identity, repair storage merely to write a checkpoint, or request redundant permission. Reconcile later with `continuity recovery reconcile --root <canonical-root> --file <receipt>`.

For a normal checkpoint, commit the product change first and then run `continuity checkpoint`; it prints a stable `REQUEST_ID`, commits, and synchronously pushes the checkpoint to the task branch. If interrupted, retry with the same `--request-id`; the same payload is a no-op and a different payload is rejected. Open or update a PR after pushing. GitHub CI and auto-merge then run asynchronously, gated by required reviews/checks and any merge queue. Confirm the merge before marking complete or removing the worktree.

If `.continuity/documents.json` exists, every fresh session or task takeover/resumption must consult it before deciding the next action, not only before writing documentation. Run `git fetch origin`, then `continuity docs find "<issue title and task-objective terms>" --task <TASK-ID>`; read the returned matches and declared neighbors before deciding that prior work is missing or creating/replacing a document. Investigate `NEEDS_REVIEW`/`REMOTE_UNKNOWN` before relying on old evidence. The generated human view is checked by `continuity validate`.

<!-- oio:issue-log-guidance:start -->
Before filing an observational or operational issue log, read `.oio/ontology/ISSUE_LOG_ONTOLOGY.md`, `.oio/ontology/project.json`, and `.oio/ontology/AGENT_GUIDE.md`. Confirm the exact destination and filing action are authorized. On OIO, ACS, CGM, and PCM, do not submit an issue or write files without explicit human direction for that destination and action. A proposal can remain a local draft until directed. Never treat adoption as permission to write to an adopter or sibling repository.
<!-- oio:issue-log-guidance:end -->
