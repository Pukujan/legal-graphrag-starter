# Legal Graph — Project Contract

<!-- continuity:project {"id":"legal-graph","protocol_version":"0.1.0-draft","schema":"project-continuity.project.v1","title":"Legal Graph"} -->

<!-- pcm:github-progression:start -->
## GitHub-owned progression

GitHub Issues are required for PCM-governed project work and own task scope, acceptance, priority, ownership, dependencies, lifecycle and durable project progression. Merged default-branch history owns accepted code and normative/domain documents; PR checks and merge records own delivery facts. Checked-in PROJECT/CURRENT/TASK/checkpoint/handoff documents are mandatory versioned projections for task state, not a parallel authority. Local files, registries, context packs and chat are ephemeral execution aids. Domain-document ownership stays with the target project.

Every issue progress update MUST link the leaf child issue that owns the work, its parent ancestry and dependencies (or explicitly none). A top-level deliverable identifies itself as the leaf and says parent: none. Create one child per independently deliverable scope, never one per comment. Record task ID, primary writer and branch on the issue before creating its repository projection. Re-read live issues and relevant source revisions before resuming; the issue verifier checks identity/status, not semantic agreement.

Authorized owner/user direction can revise intent: record it on the owning GitHub issue with a correction/supersession link before dependent work. It cannot alter observed CI/merge facts or waive required gates. Stale projections yield to their field's authority. If direction, ownership or evidence conflicts remain unresolved, pause affected work and record uncertainty; continue independent safe work. One primary writer owns each task branch/checkpoint stream. Coordinate shared-document edits through linked issues/PRs, re-read the current base and reconcile concurrent changes; never force-push or overwrite another writer. Issue prose is not an atomic lock.

Label observed results, repository/external evidence, agent reports and inference separately. Preserve contradictory evidence with source/revision and mark conclusions disputed or unknown until resolved. Append correction/supersession evidence; never rewrite checkpoint history. An upstream correction MUST identify affected descendants and assumptions on their issues; pause, re-plan and revalidate dependent work before resuming. Follow explicit parent/dependency links within the affected scope; cycles or unknown lineage block affected claims. No graph database, local canonical ledger or autonomous polling agent is required.

Before every push, synchronize relevant docs and task/checkpoint projections, CURRENT/HANDOFF when affected, and reviewed catalog/generated index. Record leaf/parent/dependency links, source issue/comment revision, as-of status, evidence, blockers and next action. Commit product/docs first; `continuity checkpoint` then commits and synchronously pushes the checkpoint with a stable request ID. After every successful push, manually publish a leaf issue receipt keyed by request ID and exact pushed SHA, linking changed docs/checkpoint, PR, tests and pending gates; add a linked parent progression update. Retry a missing receipt without another checkpoint/push; inspect for the same key before posting. --receipt-repo and --receipt-issue are opt-in and still require a proven lookup; omit them and the receipt stays manual. Automatic issue-comment synchronization is not implemented; issue #67 is CLOSED (owner freeze decision 2026-09-25) and its unmet guaranteed-completion acceptance transferred to #110.

Required CI and GitHub auto-merge are mandatory. Arm auto-merge only after the increment's final push: a later push races the merge window and strands outside accepted history. Verify protection, required reviews/checks on the exact current-base or merge-queue candidate, and auto-merge; missing, failed, skipped, stale or unverified gates fail closed: no completion or cleanup. After CI/merge, append the exact check results, PR/merge SHA and live issue status to the leaf and link the parent update; fetch and verify accepted history. Reconcile material doc/status corrections in a new synchronized increment. Receipt-only transitions need no recursive doc commit: docs retain an explicit as-of/pending state and point to the live issue. Never label local-only or merely pushed work delivered. Preserve unsafe resources and keep incomplete issues open.
<!-- pcm:github-progression:end -->

## Main goal

Build and evaluate a local-first legal authority graph research prototype. It should let engineers and researchers explore citation relationships, inspect the selected authority and its sources, and test whether graph traversal improves on the existing hybrid search baseline. The first engine plan includes an early JEV/OpenAI Decisions stage and a local open-source alternative. The explorer, decision stage, and retrieval evaluation are not implemented yet.

## Why

The starter already contains a public legal authority graph and a small traversal demo. Its checked-in inventory reports 119,770 nodes and 160,793 edges, while its README says the GraphRAG retrieval layer does not yet exist. A source-visible research interface can make the graph easier to inspect and provide a disciplined way to test retrieval ideas.

## Scope

The initial scope is a bounded authority graph explorer with search, a readable sparse graph, selected-node and edge inspection, and source links. The first retrieval/decision stage should be designed early around JEV/OpenAI Decisions while retaining a local open-source option. Measure changes against the current hybrid retrieval baseline with a documented question set, recall@k, NDCG, and citation-grounding checks before making efficacy claims.

## Non-goals

This project does not provide legal advice, determine whether an authority remains valid or controlling, or replace review by a qualified legal professional. Graph position and citation counts are not legal judgments. Do not invent authorities, quotations, outcomes, or evaluation results. No production service access or credentials are provided by this starter.

## Definition of success

Readers can inspect why authorities are connected and open the source for a selected node or edge. Sample and illustrative content is labeled. Any retrieval or decision-stage benefit is reported only from a reproducible comparison against the baseline, with limitations and citation-grounding results visible. The local open-source option remains usable without external provider credentials.
