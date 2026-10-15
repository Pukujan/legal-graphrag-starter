-- Some real authority IDs exceed PostgreSQL's btree row limit (the longest
-- source ID is 7,091 bytes). Key uniqueness on a sha256 of the exact text and
-- use hash indexes for endpoint lookups. The stored text IDs are unchanged.
-- Backslashes are doubled so the bytea cast reads the text literally.
-- Idempotent; existing indexes are recreated under the same names, so
-- 001's "CREATE INDEX IF NOT EXISTS" never rebuilds the btree versions.
ALTER TABLE legal_graph.authorities
  ADD COLUMN IF NOT EXISTS authority_sha BYTEA
  GENERATED ALWAYS AS (sha256(replace(authority_id, '\', '\\')::bytea)) STORED;
ALTER TABLE legal_graph.authorities
  DROP CONSTRAINT IF EXISTS authorities_pkey,
  ADD CONSTRAINT authorities_pkey PRIMARY KEY (snapshot_id, authority_sha);
ALTER TABLE legal_graph.citation_edges
  ADD COLUMN IF NOT EXISTS edge_sha BYTEA
  GENERATED ALWAYS AS (sha256(replace(edge_key, '\', '\\')::bytea)) STORED;
ALTER TABLE legal_graph.citation_edges
  DROP CONSTRAINT IF EXISTS citation_edges_pkey,
  ADD CONSTRAINT citation_edges_pkey PRIMARY KEY (snapshot_id, edge_sha);
DROP INDEX IF EXISTS legal_graph.citation_edges_by_source;
CREATE INDEX citation_edges_by_source ON legal_graph.citation_edges USING hash (source_id);
DROP INDEX IF EXISTS legal_graph.citation_edges_by_target;
CREATE INDEX citation_edges_by_target ON legal_graph.citation_edges USING hash (target_id);
