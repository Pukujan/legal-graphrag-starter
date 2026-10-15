# Answer-time citation and confidence checker: benchmark

Refs issue #30. Module: `legal_graph/answer_check.py`.

## Why it matters
A legal answer can cite a real Colorado case that does not say what the sentence claims. The checker scores each claim
against the text it cites, calibrates that score, and tells the answer to abstain when the weakest claim is below a threshold.

## Eval set (`eval_set.jsonl`, 248 items, built by `build_eval_set.py`)
- Source: Caselaw Access Project static export, Colorado Reports volumes 190-200 (even), majority opinions, 740 cases scanned.
- Supported (132, label 1): the first "we hold/conclude that ..." sentence of a case, rewritten as "The court held/concluded that ...",
  paired with the 5-sentence window around it.
- Unsupported (116, label 0), made by perturbation: `neg` 36 (insert "not"), `wrong_holding` 36 (antonym swap such as
  affirm/reverse, did/did not, valid/invalid), `wrong_case` 44 (another case's holding cited against this window).
- Labels come from construction (seed 7). 12 random unsupported items were read by hand; all contradicted or were absent from their
  source. Caveat: supported claims are near-verbatim, so this set is easy; paraphrased claims are a next step.

## Results (CPU, 8 cores; n=248; 95% bootstrap CIs; temperature fit with 5-fold CV)
| checker | acc@0.5 | AUROC | ECE raw | ECE after temp. scaling | temperature | conformal thr. (alpha=0.05) / false-accept / false-abstain | latency p50 / p95 ms |
|---|---|---|---|---|---|---|---|
| MiniCheck-RoBERTa-Large (`74c8919647e6`) | 0.992 | 0.999 [0.998-1.000] | 0.044 [0.040-0.057] | 0.013 [0.006-0.026] | 0.45 | 0.146 / 0.034 / 0.000 | 437 / 1218 |
| HHEM-2.1-Open (`de9a5e6ba9ba`) | 0.774 | 0.896 [0.852-0.932] | 0.188 [0.143-0.237] | 0.122 [0.094-0.173] | 1.93 | 0.861 / 0.034 / 0.727 | 181 / 3213 |
| Jev yes/no | skipped: `OPENROUTER_API_KEY` not set | | | | | | |

Accuracy by kind (calibrated): MiniCheck 1.00 on supported, neg and wrong_case, and 0.94 on wrong_holding. HHEM 1.00 on supported and wrong_case,
but only 0.22 on neg and 0.22 on wrong_holding: it misses flipped holdings. Temperature scaling does not change accuracy at 0.5 (it is monotonic).

## Score schema (stored per claim; matches the merged `assertion` columns in PR #38)
`raw_prob`, `calibrated_prob`, `threshold`, `accepted`, `decided_at` (UTC ISO-8601), `decider` (HF model id), `model_version`
(HF commit sha prefix), plus `temperature`, `citation` and `schema_version="answer-check/1"`. Answer level: `answer_confidence` = min
calibrated claim score, `abstain` = answer_confidence < threshold.

## Reproduce
```
for v in 190 192 194 196 198 200; do ...  # download https://static.case.law/colo/$v/cases/*.json into a cache dir
uv run python benchmarks/answer_check/build_eval_set.py <cache_dir> benchmarks/answer_check/eval_set.jsonl
uv pip install torch transformers==4.46.3 sentencepiece
uv run python benchmarks/answer_check/run_benchmark.py && uv run python benchmarks/answer_check/report.py
```
Not done here: DeepEval CI tests and pointer-resolved quoting (RESEARCH-confidence.md); MAPIE is replaced by an inline split-conformal rule.
