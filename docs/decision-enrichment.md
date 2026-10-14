# Decision and evidence enrichment

## Scope and provenance

This increment adds an optional evidence layer over the frozen citation graph. Citation nodes and edges are copied unchanged into an overlay. Candidate spans, offsets, linked authority IDs, source hashes, full provider responses, and all decision labels remain available even when a retrieval policy excludes a candidate. A provider's support score is its model estimate for one passage and predicate; it is not an estimate of legal truth or authority status.

Authority linking is intentionally exact: normalized Unicode, case, and whitespace matching against an authority ID or title must identify one unique node. The extractor never creates authority IDs. Span offsets use Python string indices with an exclusive end and must exactly reproduce the source substring. If an optional `relationship` judgment is present, it must select a supported relationship label that exactly matches the candidate's `predicate`; `none`, `insufficient`, `unclear`, missing, and mismatching relation judgments stay in the evidence record but cannot create a traversable semantic link. Existing records without that optional question remain eligible under the support/assertion policy.

## Provider and local extractor

`build_jev_request(passage, candidate_span, *, predicate, subject_id, object_id, model=...)` builds a candidate-specific request with support, assertion-status (`asserted`, `negated`, `hypothetical`, `forward_looking`, `unclear`), and relationship questions. Relationship options include `none`, `insufficient`, and `unclear`. `OpenRouterJevProvider` uses the official OpenRouter Decisions endpoint (`POST /api/alpha/decisions`) and takes a request with `model`, `state`, and `questions`. It returns the provider's raw response, including model/provider/request identifiers, usage, and answer distributions. `parse_decision_response` validates each keyed answer before policy use. Request cache keys cover the full caller-supplied record, which should include source hash, candidate offsets, predicate, provider/model, and prompt/schema/parser versions.

The first configured model is `typesafe/jev-1.13-20260917`; select one provider per normal request. The executable uses a deterministic offline fixture by default; `--provider jev` makes the actual provider call. Provide `OPENROUTER_API_KEY` in the environment. No key is needed for local fixture execution.

The optional local extractor is the Apache-2.0 `gliner2` package and `fastino/gliner2.5-base-v1` model, pinned by the runner to model revision `54785d51df8d86a0d8f2eb212fec15871aad2c4d`. The official package supports `AutoExtractor.from_pretrained(...)` and `extract(..., include_confidence=True, include_spans=True)`. Install its local extra with `python -m pip install -r requirements-extraction.txt`; first inference downloads model weights. This project does not claim GLiNER2 extraction quality on legal opinions.

Official references: [GLiNER2 repository and API](https://github.com/fastino-ai/GLiNER2) (Apache-2.0) and [OpenRouter Alpha Decisions API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request).

## Runnable synthetic pipeline

The public `run_demo(*, provider="fixture", run_extractor=False)` pipeline is deliberately labeled synthetic and is not a real opinion, treatment label, or benchmark:

```sh
python -m legal_graph.decision_demo
```

It reports the original citation-only result, the policy-filtered annotation result, and annotation filtering at retrieval. The last two must match for the same policy while the complete overlay retains every annotation. To use Jev on the same synthetic candidate:

```sh
OPENROUTER_API_KEY=... python -m legal_graph.decision_demo --provider jev
```

To attempt the pinned local extraction step, add `--run-gliner`. The implementation is lazy and reports a specific `ExtractionUnavailableError` if package, model download, or runtime support is unavailable; it does not fall back to invented spans.

The current worker's bounded local GLiNER2.5 attempt used the pinned 194-million-parameter checkpoint and CPU Torch, but model initialization failed with `RuntimeError: Failed to initialize cpuinfo!` in a runtime without the expected CPU metadata. A scalar Torch tensor worked. The failure was not repaired by patching the model or runtime. Real GLiNER extraction remains unverified here; the synthetic fixture and contract tests do not stand in for model inference.

An actual synthetic Jev call succeeded through the pipeline: HTTP 200 from TypeSafe model `typesafe/jev-1.13-20260917`, 601 input tokens, 150 output tokens, cost `$0.000025242`, and 2.913 seconds elapsed. Its support estimate was `0.97`; assertion was `asserted` with probability `0.99`; relationship was `follows` with probability `1.0`. These are model judgments about the synthetic passage only. The output retained the full keyed distributions and provider response metadata. This smoke call does not validate legal accuracy.

## Reproduce the contract tests

```sh
python -m pytest -q \
  tests/test_retrieval_contract.py tests/test_ranking_contract.py \
  tests/test_decision_contract.py tests/test_provider_contract.py \
  tests/test_annotation_contract.py
```

The approved contract suite is deterministic and uses a fake transport, so it does not make an external call. The CI engine job runs these same five suites. Retrieval-quality and legal-effect claims require a separate sourced opinion benchmark with independent human labels; none is included in this increment.
