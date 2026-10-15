-- App-owned citation snapshot storage. Apply each statement separately via Octo.
CREATE SCHEMA IF NOT EXISTS legal_graph;

CREATE TABLE IF NOT EXISTS legal_graph.snapshots (
  snapshot_id UUID PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('staging', 'ready')),
  source_kind TEXT NOT NULL DEFAULT 'synthetic' CHECK (source_kind IN ('synthetic', 'source_import')),
  source_dataset TEXT NOT NULL DEFAULT 'synthetic-roundtrip-test' CHECK (length(source_dataset) > 0),
  source_digest TEXT NOT NULL CHECK (length(source_digest) > 0),
  expected_authority_count BIGINT NOT NULL CHECK (expected_authority_count >= 0),
  expected_edge_count BIGINT NOT NULL CHECK (expected_edge_count >= 0),
  authority_count BIGINT,
  edge_count BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ready_at TIMESTAMPTZ,
  CHECK ((status = 'staging' AND ready_at IS NULL) OR
         (status = 'ready' AND ready_at IS NOT NULL AND
          authority_count = expected_authority_count AND
          edge_count = expected_edge_count))
);

CREATE TABLE IF NOT EXISTS legal_graph.authorities (
  snapshot_id UUID NOT NULL REFERENCES legal_graph.snapshots(snapshot_id),
  authority_id TEXT NOT NULL,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  PRIMARY KEY (snapshot_id, authority_id)
);

CREATE TABLE IF NOT EXISTS legal_graph.citation_edges (
  snapshot_id UUID NOT NULL REFERENCES legal_graph.snapshots(snapshot_id),
  edge_key TEXT NOT NULL,
  source_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  PRIMARY KEY (snapshot_id, edge_key)
);

CREATE INDEX IF NOT EXISTS citation_edges_by_source
  ON legal_graph.citation_edges (snapshot_id, source_id, edge_key);
CREATE INDEX IF NOT EXISTS citation_edges_by_target
  ON legal_graph.citation_edges (snapshot_id, target_id, edge_key);

CREATE TABLE IF NOT EXISTS legal_graph.active_snapshot (
  singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
  snapshot_id UUID NOT NULL REFERENCES legal_graph.snapshots(snapshot_id),
  activated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE OR REPLACE FUNCTION legal_graph.require_staging_snapshot()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
  target_snapshot UUID;
  snapshot_status TEXT;
BEGIN
  target_snapshot := COALESCE(NEW.snapshot_id, OLD.snapshot_id);
  SELECT status INTO snapshot_status
    FROM legal_graph.snapshots WHERE snapshot_id = target_snapshot;
  IF snapshot_status IS DISTINCT FROM 'staging' THEN
    RAISE EXCEPTION 'snapshot % is immutable unless staging', target_snapshot;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS authorities_staging_only ON legal_graph.authorities;
CREATE TRIGGER authorities_staging_only
  BEFORE INSERT OR UPDATE OR DELETE ON legal_graph.authorities
  FOR EACH ROW EXECUTE FUNCTION legal_graph.require_staging_snapshot();
DROP TRIGGER IF EXISTS citation_edges_staging_only ON legal_graph.citation_edges;
CREATE TRIGGER citation_edges_staging_only
  BEFORE INSERT OR UPDATE OR DELETE ON legal_graph.citation_edges
  FOR EACH ROW EXECUTE FUNCTION legal_graph.require_staging_snapshot();

CREATE OR REPLACE FUNCTION legal_graph.prevent_ready_snapshot_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = 'ready' THEN
    RAISE EXCEPTION 'ready snapshot % is immutable', OLD.snapshot_id;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS snapshots_ready_immutable ON legal_graph.snapshots;
CREATE TRIGGER snapshots_ready_immutable
  BEFORE UPDATE OR DELETE ON legal_graph.snapshots
  FOR EACH ROW EXECUTE FUNCTION legal_graph.prevent_ready_snapshot_change();

-- Readiness is recorded only when caller-provided digest and actual row counts
-- have been independently checked against the staging input. The activation
-- statement below is a single atomic pointer replacement and refuses staging.
-- Callers must not mark a snapshot ready before their digest verification.
-- To finalize staging: compute counts and a canonical input digest in the
-- adopter, then update exactly one staging row with matching counts/digest and
-- ready_at=CURRENT_TIMESTAMP. To activate, execute this as one statement with
-- a bound UUID parameter:
-- INSERT INTO legal_graph.active_snapshot(singleton, snapshot_id)
-- SELECT TRUE, snapshot_id FROM legal_graph.snapshots
-- WHERE snapshot_id = $1 AND status = 'ready'
-- ON CONFLICT (singleton) DO UPDATE SET
--   snapshot_id = EXCLUDED.snapshot_id, activated_at = CURRENT_TIMESTAMP;
