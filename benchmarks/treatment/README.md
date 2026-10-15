# Citation-treatment labels for Colorado-to-Colorado edges (Jev)

Refs #7, #43. Decider: TypeSafe Jev `typesafe/jev-1.14-20261014` via OpenRouter Decisions API (`legal_graph/jev.py`).

## Edges (`extract.py`)
CAP static export, all volumes of `colo` (Colorado Reports, 1864-1980) and `colo-app` (Colorado Court of Appeals, 1891-1915):
22,518 cases. An edge is (citing case, cited cite) from CAP `cites_to` where the cited reporter is Colo./Colo. App./Colo. N.P./Colo.
L. Rep.: 62,916 unique edges; 62,804 have the cite string in the opinion text (context = up to two paragraphs containing it, cut
to 1,800 chars, cite marked `[[CITE]]...[[/CITE]]`). Not covered: post-1980 Colorado cases (only in P.2d/P.3d, which are multi-state).
The graph in `data/` has no case-to-case Colorado edges (its 1.3M expanded edges are federal), so CAP is the edge source here.

## Labelling (`label_jev.py`)
One request per edge: Choice over {followed, distinguished, criticized, questioned, overruled, cited-neutral} (each with a
one-line definition), asked twice with option order reversed; probabilities averaged; label = argmax, `raw_prob` = its mean
probability. 16 parallel requests, resumable.

## Results (`report.py` -> `results.json`; all 62,804 edges)
| label | edges | share |
|---|---|---|
| followed | 49,027 | 78.1% |
| cited-neutral | 9,405 | 15.0% |
| distinguished | 3,674 | 5.8% |
| overruled | 276 | 0.4% |
| criticized | 265 | 0.4% |
| questioned | 157 | 0.3% |

- Order consistency (argmax same in both option orders): 96.7%.
- Hand check (`handcheck.jsonl`, 50 edges stratified by Jev label: 15 followed, 10 neutral, 10 distinguished, 5 of each rare
  label): agreement 35/50 = 0.70; by Jev label followed 0.80, cited-neutral 0.80, distinguished 0.80, overruled 0.60,
  questioned 0.60, criticized 0.20. Weighted by the label distribution: ~0.80. Most errors are followed vs cited-neutral and rare
  labels assigned to neutral discussion. The check was done by the agent, not blind (Jev label visible), on truncated context.
- Calibration (temperature fit on hand-check correctness, 5-fold CV): ECE 0.176 raw -> 0.063, T=2.19 (raw probabilities are
  overconfident). Threshold = smallest calibrated score with >=0.9 hand-check precision above it: 0.722; 29,665 edges (47%)
  accepted. With n=50 these numbers are rough.
- Cost: 64,229 calls (incl. ~1.4k duplicate edge ids from a first extract pass) $2.85; **$0.044 per 1,000 calls**.

## Storage
`assertions.jsonl.gz`: one row per edge in the PR #38 `legal_graph.assertion` shape (`assertion_id` uuid5, `work_id`=`cap:<id>`,
`decision_type`=`citation_treatment`, `value`, `decider`, `decider_version`, `raw_prob`, `calibrated_prob`, `threshold`,
`accepted`, `decided_at`). Not loaded into Octo: the table has an FK from `work_id` to `legal_graph.work` (only the 4,151 CLERC
works are there, no Colorado CAP cases), an FK on `decision_type` to `type_registry`, and an exclusion constraint allowing one
assertion per (work, decision_type) per time, so per-edge treatment needs an edge-keyed assertion (or `edge`-subject evidence)
migration first.

## Reproduce
```
cat vols.txt | xargs -P16 -n2 sh -c 'curl -so $0-$1.zip https://static.case.law/$0/$1.zip'   # colo + colo-app volumes
python benchmarks/treatment/extract.py <zip_dir> edges.jsonl
OPENROUTER_API_KEY=... uv run python benchmarks/treatment/label_jev.py edges.jsonl labels.jsonl --workers 16
uv run python benchmarks/treatment/report.py edges.jsonl labels.jsonl benchmarks/treatment/assertions.jsonl.gz
```
