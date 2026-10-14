# Test-driven implementation and independent evaluation

## Order
Root writes contracts and tests before Luna implementation. Luna first records the expected import/contract failures, implements the smallest passing engine, then records actual results. Do not weaken a failing assertion without a root-approved contract correction. Freeze the implementation commit before running root holdouts. After a failure is disclosed, that case is a regression test rather than an unseen holdout; use fresh independent cases for another holdout round.

## Public suite
test_retrieval_contract.py contains an independent set-expansion oracle, paths and source direction checks, missing/isolated cases, duplicate/order metamorphisms, reversal plus direction swap, uncapped depth/kind monotonicity, disconnected-component invariance, invalid inputs, truthful resource truncation and actual LangGraph/direct parity. These assert externally visible behavior rather than private implementation details.

## Decision contracts
Test parser validation with missing question IDs, invalid numeric distributions, invalid labels and provider failures. Test candidate-specific source evidence, negation and hypothetical classification, preservation of contradiction and modality, immutable originals, cache invalidation across source/prompt/model/schema changes, and equality of a policy-filtered annotated graph with the corresponding gated graph. Use mocked transport for deterministic contracts and a separately marked live provider smoke. Model paraphrase and label-order robustness are measured with declared tolerances; stochastic outputs are not required to be identical.

## Holdouts
Root holds fixture details and expected answers separately from engineering task handoffs. Coverage includes Unicode and near-collision identifiers, self-loops, cycles, reciprocal links, multi-kind pairs, disconnected nodes, multiple seeds and resource boundaries. Use an independent oracle and original source integrity checks. Shared filesystem separation is procedural, not a security boundary. No claim of cryptographic secrecy.

## Differential and metamorphic limits
Monotonic reachability assertions apply only when budgets do not truncate. Disconnected-component invariance applies to local neighborhoods, not whole-graph PageRank. A k-prefix assertion is valid only with unchanged candidate population and scores. Exact renamed ranking parity is not guaranteed when identifiers break score ties. Retrieval and provider benchmarks use frozen inputs, repeated measurements and documented hardware/versions; synthetic metrics do not establish legal recall.

## Delivery gate
Report failing as well as passing counts, exact tested commit and pending gates. Local tests, pushed commits, PR CI and merged delivery are distinct states. Required repository gates are verified before merge. App validation needs actual API integration and rendered inspection, not only a successful build.
