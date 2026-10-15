-- Existing workspace upgrade for explicit sample-vs-source provenance.
-- Defaults classify existing smoke-test rows as synthetic. A future corpus
-- import must name its source_dataset and set source_kind='source_import'.
ALTER TABLE legal_graph.snapshots
  ADD COLUMN IF NOT EXISTS source_kind TEXT NOT NULL DEFAULT 'synthetic'
  CHECK (source_kind IN ('synthetic', 'source_import'));
ALTER TABLE legal_graph.snapshots
  ADD COLUMN IF NOT EXISTS source_dataset TEXT NOT NULL DEFAULT 'synthetic-roundtrip-test'
  CHECK (length(source_dataset) > 0);
