# LGR-0003 — Decision and evidence graph enrichment

<!-- continuity:task {"acceptance":["The unchanged root-authored decision, provider, and annotation contracts pass.","The existing public retrieval and ranking contracts remain passing.","The offline synthetic pipeline shows citation-only and decision-enriched retrieval separately; filtered and annotated retrieval agree under one policy.","Provider answers retain raw distributions and request/model/provider/usage metadata.","Local GLiNER runtime failures are explicit; no legal-efficacy claim is made."],"depends_on":["LGR-0001","LGR-0002"],"goal":"Integrate source-grounded candidate extraction, Jev passage decisions, and a non-destructive GraphRAG annotation overlay.","id":"LGR-0003","issue_url":"https://github.com/Pukujan/legal-graphrag-starter/issues/4","next_action":"Commit the passing local increment and provide its exact SHA to root for independent decision holdouts.","owner":"Luna decision_executor","priority":"high","protocol_version":"0.1.0-draft","schema":"project-continuity.task.v1","status":"active","why":"The citation graph lacks a source-linked, inspectable decision layer, so retrieval cannot compare citation-only evidence with passage-specific model judgments."} -->

## Problem and intended outcome

The frozen citation graph records links between authorities but has no source-grounded decision overlay. This task adds a small, inspectable pipeline for span candidates, exact authority ID links, provider decisions about a cited predicate, and GraphRAG traversal over those annotations while preserving the citation-only graph and every decision outcome.

The first hosted decision provider is Jev through OpenRouter's official Decisions endpoint. The first local extraction option is the pinned GLiNER2.5 checkpoint. Model support scores and assertion labels describe model judgments about a passage; they do not establish legal truth, treatment, or authority status.

## Identity and lineage

- Owning leaf: [issue #4](https://github.com/Pukujan/legal-graphrag-starter/issues/4)
- Parent: [issue #1](https://github.com/Pukujan/legal-graphrag-starter/issues/1)
- Dependencies: [issue #2 / LGR-0001 setup](https://github.com/Pukujan/legal-graphrag-starter/issues/2) and [issue #3 / LGR-0002 citation baseline](https://github.com/Pukujan/legal-graphrag-starter/issues/3)
- Primary writer: Luna (`decision_executor`)
- Branch: `codex/decision-graph-enrichment`
- Base: frozen citation/ranking kernel `dffc60f8da3e4358cf1df2647932523f9b1a8819`

## Scope

- Validate exact candidate offsets and resolve authority IDs only through unique normalized ID/title matches.
- Add a minimal OpenRouter Jev Decisions adapter, strict parser, stable complete-record cache key, and explicit policy view.
- Preserve source/model/provider/request/usage metadata, the raw answer distributions, and all assertion outcomes.
- If a relationship judgment is present, allow semantic traversal only when a supported label matches the candidate predicate; preserve rejected judgments in the overlay.
- Copy original citation graph facts and add annotation/source evidence nodes without overwriting authorities.
- Compare citation-only, decision-filtered, and annotation-filtered-at-retrieval outputs on a clearly synthetic executable fixture.
- Attempt the local GLiNER2.5 extractor lazily and report unavailable runtime/model failures explicitly.

## Acceptance criteria

- The unchanged root-authored decision and provider contracts and updated annotation contracts pass.
- The existing public retrieval and ranking contracts remain passing.
- A default offline synthetic pipeline runs without a credential and shows that filtered and annotated retrieval agree for the same policy while the citation-only result remains distinct.
- A successful provider call retains its raw keyed distributions, model/provider/request identity, token/cost usage, and timing.
- No real legal treatment or benchmark is claimed without sourced opinions and independent human labels.
- The engine CI job runs all five public contract suites.

## Evidence and caveats

- The frozen base passed 66 retrieval/ranking public contracts; root's independent holdouts passed 110/110 on that exact commit before handoff.
- The first enrichment contract run failed collection because `legal_graph.decisions` and `legal_graph.extraction` did not exist, as expected before implementation.
- This worker's implementation checkpoint currently passes 112 public contracts (66 kernel + 46 enrichment); a synthetic Jev request succeeded. Exact run metadata is recorded in `docs/decision-enrichment.md`.
- The local pinned GLiNER2.5 attempt failed before inference with `RuntimeError: Failed to initialize cpuinfo!`; no legal extraction result is claimed.
- Remote pull request, required checks, and merge status are not verified by this local task projection.

## Related records

- Leaf issue #4 → parent issue #1; depends on issue #2 / LGR-0001 and issue #3 / LGR-0002.
- Primary writer: Luna (`decision_executor`); branch `codex/decision-graph-enrichment`; issue #4 verified by the parent before handoff; scope correction comment added on 2026-10-14.
- Root's 110 independent kernel holdouts passed before this task's checkout handoff. No decision-stage holdout has been read.
- Hosted engine-tests and stack-train were reported passing; ACS adopter validation failed because the required read-only token is missing. No PR for this leaf exists yet.

## Checkpoint log

2026-10-14 — Added strict Jev response parsing and OpenRouter transport, exact source-span and authority-ID helpers, non-destructive citation graph overlay, relationship-predicate policy guard, lazy GLiNER2.5 adapter, and the explicitly synthetic executable pipeline. Copied root contracts remain byte-identical; combined public suite passes 112/112. Synthetic Jev call returned HTTP 200 from TypeSafe model `typesafe/jev-1.13-20260917` (601 input / 150 output tokens, `$0.000025242`, 2.913 s); this is a passage judgment on invented fixture text, not a legal evaluation. GLiNER model initialization failed with `Failed to initialize cpuinfo!`, so no extraction inference is claimed. **Next:** commit this local increment and provide its exact SHA to root for independent decision holdouts.

## Boundary and next action

This task does not add a real-opinion benchmark, legal treatment labels, a general provider framework, or an authority-status classifier. Next: complete test/CI/doc validation and freeze the local increment for independent decision holdouts.
