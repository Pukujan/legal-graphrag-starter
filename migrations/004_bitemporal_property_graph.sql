-- Bitemporal property graph with an evolving type registry (RESEARCH-data-model.md).
-- Additive only: creates new tables/indexes; never alters or drops 001-003 tables.
-- btree_gist and pg_trgm are trusted extensions; the Octo workspace role (database
-- owner, NOSUPERUSER) can create them (verified live 2026-10-14).
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 1. Ontology registry: new types are rows, never columns. Deprecate, never delete.
CREATE TABLE IF NOT EXISTS legal_graph.type_registry (
  type_key TEXT PRIMARY KEY CHECK (type_key ~ '^(node|edge|decision|tag):[a-z0-9_.-]+$'),
  kind TEXT NOT NULL CHECK (kind IN ('node','edge','decision','tag')),
  parent_key TEXT REFERENCES legal_graph.type_registry(type_key),
  folio_iri TEXT,
  status TEXT NOT NULL CHECK (status IN ('proposed','accepted','deprecated')),
  schema_version TEXT NOT NULL,
  introduced_by UUID,
  definition TEXT NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (split_part(type_key, ':', 1) = kind)
);

CREATE OR REPLACE FUNCTION legal_graph.type_registry_guard()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'type % cannot be deleted; set status=deprecated', OLD.type_key;
  END IF;
  IF NEW.type_key <> OLD.type_key OR NEW.kind <> OLD.kind THEN
    RAISE EXCEPTION 'type key and kind are stable identifiers';
  END IF;
  IF OLD.status = 'deprecated' AND NEW.status <> 'deprecated' THEN
    RAISE EXCEPTION 'deprecated type % cannot be revived; add a new type', OLD.type_key;
  END IF;
  IF OLD.status = 'accepted' AND NEW.status = 'proposed' THEN
    RAISE EXCEPTION 'accepted type % cannot return to proposed', OLD.type_key;
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS type_registry_guard ON legal_graph.type_registry;
CREATE TRIGGER type_registry_guard BEFORE UPDATE OR DELETE ON legal_graph.type_registry
  FOR EACH ROW EXECUTE FUNCTION legal_graph.type_registry_guard();

-- 2. FRBR/LRMoo identity: work (cluster), alias (reporter cites), expression (opinion text).
CREATE TABLE IF NOT EXISTS legal_graph.work (
  work_id TEXT PRIMARY KEY CHECK (length(work_id) BETWEEN 1 AND 512),
  node_type TEXT NOT NULL REFERENCES legal_graph.type_registry(type_key),
  court_id TEXT, decided_on DATE, case_name TEXT,
  ext_ids JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(ext_ids) = 'object'),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS legal_graph.work_alias (
  alias TEXT PRIMARY KEY CHECK (length(alias) BETWEEN 1 AND 512),
  work_id TEXT NOT NULL REFERENCES legal_graph.work(work_id),
  alias_kind TEXT NOT NULL CHECK (alias_kind IN ('reporter','neutral','docket','authority_id'))
);
CREATE INDEX IF NOT EXISTS work_alias_trgm ON legal_graph.work_alias USING gin (alias gin_trgm_ops);
CREATE INDEX IF NOT EXISTS work_alias_by_work ON legal_graph.work_alias (work_id);
CREATE TABLE IF NOT EXISTS legal_graph.expression (
  expr_id TEXT PRIMARY KEY CHECK (length(expr_id) BETWEEN 1 AND 512),
  work_id TEXT NOT NULL REFERENCES legal_graph.work(work_id),
  opinion_type TEXT, content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  text_ref TEXT,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 3. Bitemporal typed edges. valid_time = true in law; sys_time = when we believed it.
CREATE TABLE IF NOT EXISTS legal_graph.edge (
  edge_id UUID PRIMARY KEY,
  src_work TEXT NOT NULL REFERENCES legal_graph.work(work_id),
  dst_work TEXT NOT NULL REFERENCES legal_graph.work(work_id),
  edge_type TEXT NOT NULL REFERENCES legal_graph.type_registry(type_key),
  valid_time TSTZRANGE NOT NULL CHECK (NOT isempty(valid_time)),
  sys_time TSTZRANGE NOT NULL DEFAULT tstzrange(CURRENT_TIMESTAMP, NULL) CHECK (NOT isempty(sys_time)),
  depth INT CHECK (depth IS NULL OR depth >= 0),
  confidence REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  rank TEXT NOT NULL DEFAULT 'normal' CHECK (rank IN ('preferred','normal','deprecated')),
  props JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(props) = 'object'),
  CONSTRAINT edge_no_bitemporal_overlap EXCLUDE USING gist
    (src_work WITH =, dst_work WITH =, edge_type WITH =, valid_time WITH &&, sys_time WITH &&)
);
CREATE INDEX IF NOT EXISTS edge_current_by_src ON legal_graph.edge (src_work, edge_type) WHERE upper_inf(sys_time);
CREATE INDEX IF NOT EXISTS edge_current_by_dst ON legal_graph.edge (dst_work, edge_type) WHERE upper_inf(sys_time);

-- 4. Typed decisions (Jev / GLiNER gates / human) as bitemporal assertions on works.
CREATE TABLE IF NOT EXISTS legal_graph.assertion (
  assertion_id UUID PRIMARY KEY,
  work_id TEXT NOT NULL REFERENCES legal_graph.work(work_id),
  decision_type TEXT NOT NULL REFERENCES legal_graph.type_registry(type_key),
  value JSONB NOT NULL,
  valid_time TSTZRANGE NOT NULL CHECK (NOT isempty(valid_time)),
  sys_time TSTZRANGE NOT NULL DEFAULT tstzrange(CURRENT_TIMESTAMP, NULL) CHECK (NOT isempty(sys_time)),
  rank TEXT NOT NULL DEFAULT 'normal' CHECK (rank IN ('preferred','normal','deprecated')),
  confidence REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  decider TEXT, decider_version TEXT,
  raw_prob REAL CHECK (raw_prob IS NULL OR raw_prob BETWEEN 0 AND 1),
  calibrated_prob REAL CHECK (calibrated_prob IS NULL OR calibrated_prob BETWEEN 0 AND 1),
  threshold REAL CHECK (threshold IS NULL OR threshold BETWEEN 0 AND 1),
  accepted BOOLEAN,
  CONSTRAINT assertion_no_bitemporal_overlap EXCLUDE USING gist
    (work_id WITH =, decision_type WITH =, valid_time WITH &&, sys_time WITH &&)
);
CREATE INDEX IF NOT EXISTS assertion_current_by_work ON legal_graph.assertion (work_id, decision_type) WHERE upper_inf(sys_time);

-- 5. PROV-O-shaped provenance and evidence spans.
CREATE TABLE IF NOT EXISTS legal_graph.prov_activity (
  activity_id UUID PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('import','classifier','human_review','migration')),
  agent TEXT NOT NULL,
  started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ,
  snapshot_id UUID REFERENCES legal_graph.snapshots(snapshot_id),
  params JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(params) = 'object')
);
CREATE TABLE IF NOT EXISTS legal_graph.evidence (
  evidence_id UUID PRIMARY KEY,
  subject_kind TEXT NOT NULL CHECK (subject_kind IN ('edge','assertion')),
  subject_id UUID NOT NULL,
  activity_id UUID NOT NULL REFERENCES legal_graph.prov_activity(activity_id),
  expr_id TEXT REFERENCES legal_graph.expression(expr_id),
  char_start INT, char_end INT, quote_sha256 TEXT, quote TEXT,
  CHECK (char_start IS NULL OR (char_start >= 0 AND char_end > char_start))
);
CREATE INDEX IF NOT EXISTS evidence_by_subject ON legal_graph.evidence (subject_kind, subject_id);

-- 6. Retrieval chunks: built-in full text (tsvector) + pgvector HNSW.
CREATE TABLE IF NOT EXISTS legal_graph.chunk (
  chunk_id TEXT PRIMARY KEY,
  expr_id TEXT NOT NULL REFERENCES legal_graph.expression(expr_id),
  ord INT NOT NULL CHECK (ord >= 0),
  char_start INT, char_end INT,
  body TEXT NOT NULL,
  tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', body)) STORED,
  embedding vector(1024),
  embed_model TEXT NOT NULL,
  UNIQUE (expr_id, ord)
);
CREATE INDEX IF NOT EXISTS chunk_embedding_hnsw ON legal_graph.chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunk_tsv ON legal_graph.chunk USING gin (tsv);

-- Seed types (idempotent). Edge kinds from the existing snapshot plus treatments.
INSERT INTO legal_graph.type_registry (type_key, kind, status, schema_version, definition) VALUES
  ('node:case', 'node', 'accepted', '0.1.0', 'A judicial decision (CourtListener cluster / CAP case).'),
  ('edge:cites', 'edge', 'accepted', '0.1.0', 'Citing work cites the cited work.'),
  ('edge:overrules', 'edge', 'proposed', '0.1.0', 'Citing work overrules the cited work.'),
  ('edge:follows', 'edge', 'proposed', '0.1.0', 'Citing work follows the cited work.'),
  ('edge:distinguishes', 'edge', 'proposed', '0.1.0', 'Citing work distinguishes the cited work.'),
  ('decision:holding', 'decision', 'proposed', '0.1.0', 'Gate decision that a span states a holding.')
ON CONFLICT (type_key) DO NOTHING;
