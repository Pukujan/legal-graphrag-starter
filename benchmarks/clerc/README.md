# CLERC public benchmark

CLERC (`jhu-clsp/CLERC` on Hugging Face) is built from Caselaw Access Project (CAP) federal case law, and its document ids are CAP case ids.

**History:** the 2026-10-14 overlap check (`overlap.py`, `overlap-2026-10-14.json`) found only about 0.2% of CLERC's gold citations in the Colorado starter graph. We have since added a federal and Supreme Court extension.

## Federal and Supreme Court extension (`build_graph.py`)
- **Source:** CAP static bulk metadata, `https://static.case.law/<reporter>/<vol>/CasesMetadata.json`, for the reporters U.S., F.2d, F.3d, F.Supp, F.Supp.2d and F.App'x. That is 5,216 volumes and 1,378,342 cases. The files are streamed, and only id/name/court/date/citations/cites_to are kept.
- **Seeds:** the other full case citations in each CLERC query, found with eyecite and resolved to CAP ids by reporter citation.
- **Nodes:**
  - T = every CLERC test gold document plus every resolved seed (4,152 cases);
  - plus every scanned case that cites at least 2 members of T (305,028 co-citing cases);
  - 1,503 of these cases already existed in the starter graph and keep their starter id.
  - **307,677 nodes added.** Each new node carries `cap_id`, `citations`, `reporter`, `court`, `date` and `title`.
- **Edges:** CAP `cites_to` edges between graph nodes where at least one end is in T. **1,151,535 edges added.**
- **Expanded graph:** 427,447 nodes and 1,312,328 edges, written to `data-expanded/` (gitignored). File hashes are in `graph-manifest.json`.
- **Not included:** opinion text. CLERC's 8.2 GB `collection.doc.tsv.gz` has it, keyed by the same CAP ids.

## Frozen queries (`indirect/`, `direct/`)
- **Splits:** test, single-removed. Indirect has 2,851 queries and direct has 1,498. The gold answer is the CAP document from CLERC's doc-level qrels.
- **Coverage:** the gold document is in the graph for 2,507 indirect and 1,390 direct queries. Queries whose gold falls outside the scanned reporters count as misses.
- **Leakage control:** CLERC does not identify the opinion each query was taken from. Any node that cites the gold and all of a query's seeds could be that source, so its edge to the gold is listed in `holdout_edges` and removed before indexing. This is conservative. The `config-noholdout.json` variants run without the removal and give an upper bound.
- **Seeds are not a retriever:** they are the case citations already present in the query text. No hybrid or BM25 retriever is involved. The baseline is that seed list; the graph-augmented list is the seeds followed by `graphrag_retrieve` results (depth 2, both directions, CITES only, ranked by global citation in-degree).

Reproduce:
```
uv run --python 3.12 --with eyecite python benchmarks/clerc/build_graph.py fetch
uv run --python 3.12 --with eyecite python benchmarks/clerc/build_graph.py build
python -m legal_graph.benchmark run --config benchmarks/clerc/indirect/config.json --data-dir data-expanded
```
