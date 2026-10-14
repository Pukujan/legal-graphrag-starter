# TASK-LGR-0002 — Citation Retrieval Baseline

<!-- continuity:task {"acceptance":["Public retrieval and ranking contracts pass unchanged.","Direct and LangGraph paths return identical results under equal caps.","CLI reports bounded retrieval and reproducible direct/LangGraph timing with graph loading separate."],"depends_on":["LGR-0001"],"goal":"Implement bounded citation retrieval, citation-prior ranking, a LangGraph wrapper, and measured CLI baseline.","id":"LGR-0002","issue_url":"https://github.com/Pukujan/legal-graphrag-starter/issues/3","next_action":"Run independent root holdouts, then hand the single checkout to the decision/extraction writer.","owner":"Luna baseline_executor","priority":"high","protocol_version":"0.1.0-draft","schema":"project-continuity.task.v1","status":"active","why":"The starter can traverse a citation graph but has no bounded, inspectable retrieval API for downstream application work."} -->

- Status: implementation frozen locally; awaiting independent holdouts and dependent decision-stage handoff
- Owner: Luna baseline_executor
- Priority: high
- Depends on: LGR-0001 / issue #2 (ACS setup; local validation and commit are complete, remote delivery gates remain pending)

## Goal

Implement bounded citation retrieval, citation-prior ranking, a LangGraph wrapper, and measured CLI baseline.

## Why

The starter can traverse a citation graph but has no bounded, inspectable retrieval API for downstream application work.

## Allowed files

- `legal_graph/retrieval.py`
- `legal_graph/cli.py`
- `tests/test_retrieval_contract.py`
- `tests/test_ranking_contract.py`
- `docs/product-design.md`
- `docs/system-design.md`
- `docs/test-design.md`
- `docs/CONTINUITY_INDEX.md` and `.continuity/documents.json` when maintained by the continuity tooling

## Human outcome

Readers can expand a bounded citation neighborhood, inspect exact directed source edges and shortest paths, and compare a deterministic citation-prior ranker with a thin LangGraph wrapper.

## Scope and boundaries

- In scope: stable NetworkX-based retrieval from JSONL records, one-to-three-hop traversal with hard caps and truthful truncation, missing-seed reporting, deterministic citation-in-degree ranking, direct/LangGraph equivalence, and a runnable CLI that separates load time from repeated query timing.
- Out of scope: React components, changing source citations, provisioning a database, dense retrieval, enrichment/provider decisions, and claims of legal efficacy.
- Dependencies/uncertainty: setup task LGR-0001 / issue #2; hosted app integration and annotation workflow belong to dependent tasks. Local task bookkeeping uses the pinned PCM source via its venv Python because the continuity console script is absent; the GitHub connector verified issue #3 is open and matches this task, while `continuity issue verify` cannot launch `gh` in this environment.

## Acceptance criteria

- [x] `retrieve()` and `retrieve_workflow()` match the root-authored independent oracle, path, missing-seed, order-invariance, validation, and budget contracts (66 public tests pass).
- [x] `graphrag_retrieve()` matches the root-authored citation-prior ranking and provenance contract.
- [x] Direct and LangGraph results are exactly equal for equal frozen inputs and caps.
- [x] CLI loads the frozen graph and returns JSON; benchmark records exact source SHA, dependencies, load time, repeated traversal medians/p95, output counts, and truncation counts.
- [x] Public tests pass unchanged; initial expected import-error red run, actual green output, measurements, and limitations are recorded in `docs/retrieval-baseline.md`.

## Evidence and sources

- Owning leaf: [issue #3](https://github.com/Pukujan/legal-graphrag-starter/issues/3); parent: [issue #1](https://github.com/Pukujan/legal-graphrag-starter/issues/1); dependency: [issue #2](https://github.com/Pukujan/legal-graphrag-starter/issues/2).
- Issue #3 was fetched from GitHub on 2026-10-14 and is open; its task identity and acceptance match this projection. The fetched issue's `updated_at` was 2026-10-14T04:57:40Z.
- Branch: `codex/citation-retrieval-baseline`; based on local ACS setup commit `645f016c84cc7853745614e0c986a0a76f8331de`; no remote merge or gate status is claimed.
- Root-authored design and public contracts were written before product implementation: `/workspace/scratch/511fd205df68/root-specs/{product-design.md,system-design.md,test-design.md,test_retrieval_contract.py,test_ranking_contract.py}`.
- Starting source baseline: `102cf1cf347cc753622a753ae4cd16438abe8f8d`; baseline audit: `/workspace/scratch/511fd205df68/baseline-audit.md`.
- Implementation branch: `codex/citation-retrieval-baseline`; frozen local implementation commit is recorded in the task checkpoint after commit. The run used `PYTHONPATH=/workspace/scratch/511fd205df68/engine-deps` with Python 3.12.14, NetworkX 3.7, and LangGraph 1.2.15.
- Public verification command: `python -m pytest -q tests/test_retrieval_contract.py tests/test_ranking_contract.py` → `66 passed in 0.39s`.
- Real-data benchmark command: `python -m legal_graph.cli benchmark --runs 30 --warmup 5` → 30 equal-result runs; direct median/p95 3.593/4.458 ms; LangGraph median/p95 4.868/6.060 ms; graph read/index 0.880/2.182 s; bounded result 500 nodes/499 edges, node-budget truncated.

## Reproduction details (only when needed)

Starting revision, material inputs/configuration, runtime, exact command or prompt, observed result, and limitations.

## Related records

- Required leaf owning issue, parent ancestry and dependencies: issue #3 → parent #1; depends on issue #2.
- Primary writer / branch / source issue revision / as-of status: Luna baseline_executor / `codex/citation-retrieval-baseline` / issue #3 verified open 2026-10-14.
- Related PR/CI evidence and push receipt: none for this local freeze; no remote run is claimed. Existing setup PR #5 remains separate and unmerged.

## Checkpoint log

2026-10-14 — Public contracts passed unchanged (66/66); CLI retrieval and 30-run benchmark completed against checksummed source JSONL. Direct and LangGraph outputs matched; node-budget truncation and zero-score top-five ranking limitation recorded. Awaiting root holdouts before handing the single checkout to the dependent decision writer.

## Handoff

Read PROJECT → CURRENT → this task → minimum relevant spec. Checkpoint before stopping.
