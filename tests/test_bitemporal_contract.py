"""Offline contract for migration 004 and the bitemporal adapter (no network)."""
import re
from pathlib import Path

import pytest

from legal_graph.bitemporal import BitemporalGraphStore
from legal_graph.snapshot_import import split_sql
from legal_graph.storage import GraphSnapshotStore, StorageError

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "004_bitemporal_property_graph.sql"
EXISTING = {"snapshots", "authorities", "citation_edges", "active_snapshot"}


def test_migration_is_additive_and_idempotent():
    stmts = split_sql(MIGRATION.read_text())
    assert len(stmts) > 15
    for s in stmts:
        head = s.lstrip().upper()
        assert not head.startswith(("DROP TABLE", "ALTER TABLE", "DELETE", "TRUNCATE", "UPDATE"))
        if head.startswith(("CREATE TABLE", "CREATE INDEX", "CREATE EXTENSION")):
            assert "IF NOT EXISTS" in head.split("(")[0]
        for table in EXISTING:
            assert not re.search(rf"(ALTER|DROP|INSERT INTO|UPDATE)\s+(TABLE\s+)?legal_graph\.{table}\b", s)


def test_migration_has_required_tables_and_constraints():
    sql = MIGRATION.read_text()
    for table in ["type_registry", "work", "work_alias", "expression", "edge", "assertion",
                  "prov_activity", "evidence", "chunk"]:
        assert f"legal_graph.{table} (" in sql
    assert sql.count("EXCLUDE USING gist") == 2
    assert "USING hnsw (embedding vector_cosine_ops)" in sql and "tsvector" in sql.lower()
    assert "'proposed','accepted','deprecated'" in sql


class Fake:
    def __init__(self, rows=None):
        self.calls, self.rows = [], rows or []

    def __call__(self, sql, params, limit):
        self.calls.append((sql, params, limit))
        return {"rows": self.rows, "statementCount": 1, "truncated": False}


def test_types_are_never_deleted():
    store = BitemporalGraphStore(Fake())
    with pytest.raises(ValueError):
        store.set_type_status("edge:cites", "proposed")
    with pytest.raises(ValueError):
        store.propose_type("bogus", "x", "0.1.0")


def test_add_edge_only_for_accepted_types_and_bound_params():
    fake = Fake()
    BitemporalGraphStore(fake).add_edge("a", "b", "edge:cites", valid_from="2001-01-01T00:00:00Z")
    sql, params, _ = fake.calls[0]
    assert "status = 'accepted'" in sql and "tstzrange" in sql
    assert params[1:4] == ["a", "b", "edge:cites"]


def test_as_of_query_is_bitemporal():
    fake = Fake([{"edge_id": "x"}])
    rows = BitemporalGraphStore(fake).edges_as_of("a", valid_at="2010-01-01T00:00:00Z",
                                                  known_at="2026-01-01T00:00:00Z")
    sql = fake.calls[0][0]
    assert rows == [{"edge_id": "x"}]
    assert "valid_time @>" in sql and "sys_time @>" in sql and "deprecated" in sql
    with pytest.raises(ValueError):
        BitemporalGraphStore(fake).edges_as_of("a", valid_at="2010-01-01", direction="both")


def test_retract_closes_sys_time_instead_of_delete():
    fake = Fake()
    BitemporalGraphStore(fake).retract_edge("00000000-0000-0000-0000-000000000001")
    assert "sys_time = tstzrange(lower(sys_time), CURRENT_TIMESTAMP)" in fake.calls[0][0]
    assert "DELETE" not in fake.calls[0][0]


def test_decision_records_gate_fields():
    fake = Fake()
    BitemporalGraphStore(fake).record_decision(
        "w", "decision:holding", {"label": True}, valid_from="2000-01-01T00:00:00Z",
        decider="jev", decider_version="1.13", raw_prob=0.7, calibrated_prob=0.62, threshold=0.6)
    params = fake.calls[0][1]
    assert params[6:] == ["jev", "1.13", 0.7, 0.6, True]


def test_error_envelope_raises():
    store = BitemporalGraphStore(lambda *a: {"error": "x"})
    with pytest.raises(StorageError):
        store.accepted_types()


def test_snapshot_store_interface_unchanged():
    for name in ["get_active_snapshot", "get_authorities", "get_citation_edges", "get_authority"]:
        assert callable(getattr(GraphSnapshotStore, name))
