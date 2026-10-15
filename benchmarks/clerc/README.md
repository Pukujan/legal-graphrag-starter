# CLERC overlap check (2026-10-14): no adapter added

CLERC (Case Law Evaluation and Retrieval Corpus) is at https://huggingface.co/datasets/jhu-clsp/CLERC (NAACL 2025 Findings; code at abehou/CLERC). It is built from Caselaw Access Project federal case law.

**Files:**
- **Retrieval task:**
  - `collection/collection.doc.tsv.gz` (8.2 GB) and `collection.passage.tsv.gz` (9.2 GB);
  - `queries/test.{single,all}-removed.{direct,indirect}.tsv`, with 2,851 indirect queries;
  - `qrels/qrels-doc.test.*.tsv`, which map query ids to CAP document ids.
- **Generation task:** `generation/{test,train,all}.jsonl`. Each record carries `citations` as a list of [reporter_citation, cited_case_text].

**Method:** the retrieval qrels use only CAP document ids. Mapping those ids to reporter citations would need the 8.2 GB document collection, which is over the size budget, so it was not downloaded. Instead, overlap was measured on the gold citations in the generation splits, which come from the same federal CAP source:
- `generation/test.jsonl` (104 MB, all 1,000 records);
- `generation/train.jsonl` (512 MB, all 5,000 records).

Each reporter citation was matched to the trailing reporter citation of a caselaw node id. 48,901 of the graph's 61,411 caselaw nodes have one.

| split | examples | unique gold cites | mapped | examples with >=1 mapped | examples with >=2 mapped |
|---|---|---|---|---|---|
| test | 1,000 | 2,648 | 3 | 2 | 1 |
| train | 5,000 | 13,163 | 22 | 21 | 1 |

**Why so little overlaps:**
- CLERC's gold citations are mostly F.2d/F.3d (about 63%) and U.S./S.Ct./L.Ed.
- The starter graph is Colorado-centred: P.2d/P.3d, Colo., Colo. App., D. Colo. F.Supp and B.R.
- It contains almost no circuit-court or Supreme Court reporter citations (25 F.2d/F.3d ids and no U.S. ids).

**Decision:** about 0.2% of gold citations map, and only 1–21 examples have any mapped citation. That is far below the roughly 100-query threshold, so no CLERC adapter was added. The proxy benchmark remains the only automated eval.

**What could make CLERC usable:** a graph that includes federal appellate and Supreme Court authorities, or a Colorado-filtered CLERC subset. The latter would need the CAP document metadata to map document ids to courts.

Reproduce: `python benchmarks/clerc/overlap.py generation/test.jsonl generation/train.jsonl`.
