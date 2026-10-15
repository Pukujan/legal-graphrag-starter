"""Adapter for the bitemporal property graph (migrations/004).

Uses the same one-statement executor as ``GraphSnapshotStore`` so the existing
snapshot tables and reader stay untouched. Rules enforced here and in SQL:

* Types are rows in ``type_registry``: added as ``proposed``, then ``accepted``,
  later ``deprecated``. Nothing is deleted.
* Facts are never updated in place. A correction closes ``sys_time`` on the old
  row and inserts a new row; an overruling closes ``valid_time`` on a new
  belief row. History is therefore always queryable "as known at" a time.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Callable

from .storage import StorageError

Executor = Callable[[str, list[Any], int], dict[str, Any]]
KINDS = {"node", "edge", "decision", "tag"}
TYPE_STATUSES = ("proposed", "accepted", "deprecated")


def _iso(value: datetime | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.isoformat()


class BitemporalGraphStore:
    def __init__(self, execute: Executor):
        if not callable(execute):
            raise TypeError("execute must be callable")
        self._execute = execute

    def _run(self, sql: str, params: list[Any], row_limit: int = 1000) -> list[dict[str, Any]]:
        try:
            result = self._execute(sql, params, row_limit)
        except Exception as exc:
            raise StorageError("Octo SQL execution failed") from exc
        if not isinstance(result, dict) or result.get("error") or result.get("isError"):
            raise StorageError("Octo SQL returned an error envelope")
        if result.get("statementCount") != 1:
            raise StorageError("Octo SQL result must contain exactly one statement")
        if result.get("truncated"):
            raise StorageError("Octo SQL result is truncated")
        rows = result.get("rows", [])
        if not isinstance(rows, list):
            raise StorageError("Octo SQL rows must be a list")
        return rows

    # ---- ontology registry -------------------------------------------------
    def propose_type(self, type_key: str, definition: str, schema_version: str, *,
                     parent_key: str | None = None, folio_iri: str | None = None,
                     introduced_by: str | None = None) -> None:
        kind = type_key.split(":", 1)[0]
        if kind not in KINDS or ":" not in type_key or not definition:
            raise ValueError("type_key must be '<kind>:<name>' with a definition")
        self._run(
            "INSERT INTO legal_graph.type_registry (type_key, kind, parent_key, folio_iri, status, "
            "schema_version, introduced_by, definition) VALUES ($1, $2, $3, $4, 'proposed', $5, $6::uuid, $7) "
            "ON CONFLICT (type_key) DO NOTHING",
            [type_key, kind, parent_key, folio_iri, schema_version, introduced_by, definition], 1)

    def set_type_status(self, type_key: str, status: str) -> None:
        if status not in ("accepted", "deprecated"):
            raise ValueError("status must be accepted or deprecated (types are never deleted)")
        self._run("UPDATE legal_graph.type_registry SET status = $2 WHERE type_key = $1",
                  [type_key, status], 1)

    def accepted_types(self, kind: str | None = None) -> list[str]:
        rows = self._run(
            "SELECT type_key FROM legal_graph.type_registry WHERE status = 'accepted' "
            "AND ($1::text IS NULL OR kind = $1) ORDER BY type_key", [kind])
        return [r["type_key"] for r in rows]

    # ---- identity ----------------------------------------------------------
    def upsert_work(self, work_id: str, *, node_type: str = "node:case", court_id: str | None = None,
                    decided_on: str | None = None, case_name: str | None = None,
                    ext_ids: dict[str, Any] | None = None) -> None:
        self._run(
            "INSERT INTO legal_graph.work (work_id, node_type, court_id, decided_on, case_name, ext_ids) "
            "VALUES ($1, $2, $3, $4::date, $5, $6::jsonb) ON CONFLICT (work_id) DO NOTHING",
            [work_id, node_type, court_id, decided_on, case_name, json.dumps(ext_ids or {})], 1)

    # ---- bitemporal facts --------------------------------------------------
    def add_edge(self, src: str, dst: str, edge_type: str, *, valid_from: datetime | str,
                 valid_to: datetime | str | None = None, confidence: float | None = None,
                 depth: int | None = None, props: dict[str, Any] | None = None,
                 edge_id: str | None = None) -> str:
        edge_id = edge_id or str(uuid.uuid4())
        self._run(
            "INSERT INTO legal_graph.edge (edge_id, src_work, dst_work, edge_type, valid_time, "
            "confidence, depth, props) SELECT $1::uuid, $2, $3, $4, tstzrange($5::timestamptz, $6::timestamptz), "
            "$7, $8, $9::jsonb FROM legal_graph.type_registry t WHERE t.type_key = $4 AND t.status = 'accepted'",
            [edge_id, src, dst, edge_type, _iso(valid_from), _iso(valid_to), confidence, depth,
             json.dumps(props or {})], 1)
        return edge_id

    def retract_edge(self, edge_id: str) -> None:
        """Stop believing an edge (transaction time) without deleting it."""
        self._run(
            "UPDATE legal_graph.edge SET sys_time = tstzrange(lower(sys_time), CURRENT_TIMESTAMP) "
            "WHERE edge_id = $1::uuid AND upper_inf(sys_time)", [edge_id], 1)

    def edges_as_of(self, work_id: str, *, valid_at: datetime | str, known_at: datetime | str | None = None,
                    direction: str = "out", edge_types: list[str] | None = None,
                    limit: int = 1000) -> list[dict[str, Any]]:
        """Edges true in law at ``valid_at`` as we believed them at ``known_at`` (default now)."""
        col = {"out": "src_work", "in": "dst_work"}.get(direction)
        if col is None:
            raise ValueError("direction must be 'out' or 'in'")
        return self._run(
            "SELECT edge_id::text, src_work, dst_work, edge_type, confidence, rank, props "
            f"FROM legal_graph.edge WHERE {col} = $1 AND valid_time @> $2::timestamptz "
            "AND sys_time @> COALESCE($3::timestamptz, CURRENT_TIMESTAMP) AND rank <> 'deprecated' "
            "AND ($4::text[] IS NULL OR edge_type = ANY($4::text[])) ORDER BY edge_id LIMIT $5",
            [work_id, _iso(valid_at), _iso(known_at), edge_types, limit], limit)

    # ---- decisions + evidence ----------------------------------------------
    def record_decision(self, work_id: str, decision_type: str, value: Any, *, valid_from: datetime | str,
                        decider: str, decider_version: str, raw_prob: float | None,
                        calibrated_prob: float | None, threshold: float | None,
                        assertion_id: str | None = None) -> str:
        """Store a gate decision: probability, calibrated score, threshold, model version."""
        accepted = None if calibrated_prob is None or threshold is None else calibrated_prob >= threshold
        assertion_id = assertion_id or str(uuid.uuid4())
        self._run(
            "INSERT INTO legal_graph.assertion (assertion_id, work_id, decision_type, value, valid_time, "
            "confidence, decider, decider_version, raw_prob, calibrated_prob, threshold, accepted) "
            "VALUES ($1::uuid, $2, $3, $4::jsonb, tstzrange($5::timestamptz, NULL), $6, $7, $8, $9, $6, $10, $11)",
            [assertion_id, work_id, decision_type, json.dumps(value), _iso(valid_from), calibrated_prob,
             decider, decider_version, raw_prob, threshold, accepted], 1)
        return assertion_id

    def add_evidence(self, subject_kind: str, subject_id: str, activity_id: str, *,
                     expr_id: str | None, char_start: int | None, char_end: int | None,
                     quote_sha256: str | None = None) -> str:
        if subject_kind not in ("edge", "assertion"):
            raise ValueError("subject_kind must be edge or assertion")
        evidence_id = str(uuid.uuid4())
        self._run(
            "INSERT INTO legal_graph.evidence (evidence_id, subject_kind, subject_id, activity_id, expr_id, "
            "char_start, char_end, quote_sha256) VALUES ($1::uuid, $2, $3::uuid, $4::uuid, $5, $6, $7, $8)",
            [evidence_id, subject_kind, subject_id, activity_id, expr_id, char_start, char_end, quote_sha256], 1)
        return evidence_id
