"""Read-only consumer for immutable legal graph snapshots in Octo SQL."""
from __future__ import annotations

import json
from typing import Any, Callable
from uuid import UUID


class StorageError(RuntimeError):
    """The SQL transport returned an incomplete or invalid storage result."""


Executor = Callable[[str, list[Any], int], dict[str, Any]]


def _snapshot_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("snapshot_id must be a UUID string")
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("snapshot_id must be a UUID string") from exc


def _text_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


class GraphSnapshotStore:
    """Read pages from the app-owned schema using one SQL statement per call."""

    def __init__(self, execute: Executor):
        if not callable(execute):
            raise TypeError("execute must be callable")
        self._execute = execute

    def _query(self, sql: str, params: list[Any], row_limit: int) -> list[dict[str, Any]]:
        try:
            result = self._execute(sql, params, row_limit)
        except Exception as exc:
            raise StorageError("Octo SQL execution failed") from exc
        if not isinstance(result, dict):
            raise StorageError("Octo SQL result must be an object")
        if result.get("error") or result.get("isError"):
            raise StorageError("Octo SQL returned an error envelope")
        if result.get("statementCount") != 1:
            raise StorageError("Octo SQL result must contain exactly one statement")
        if result.get("truncated") is not False:
            raise StorageError("Octo SQL result is truncated or lacks truncation status")
        rows = result.get("rows")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise StorageError("Octo SQL rows must be a list of objects")
        return rows

    @staticmethod
    def _payload(row: dict[str, Any]) -> dict[str, Any]:
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError) as exc:
                raise StorageError("stored payload is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise StorageError("stored payload must be a JSON object")
        return payload

    def get_active_snapshot(self) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT s.snapshot_id::text AS snapshot_id, s.source_kind, "
            "s.source_dataset, s.source_digest, "
            "s.authority_count, s.edge_count, a.activated_at "
            "FROM legal_graph.active_snapshot a "
            "JOIN legal_graph.snapshots s ON s.snapshot_id = a.snapshot_id "
            "WHERE a.singleton = TRUE AND s.status = 'ready'",
            [], 2,
        )
        if len(rows) > 1:
            raise StorageError("active snapshot pointer returned multiple rows")
        if not rows:
            return None
        row = rows[0]
        if (not isinstance(row.get("snapshot_id"), str)
                or not isinstance(row.get("source_digest"), str)
                or ("source_kind" in row and row["source_kind"] not in {"synthetic", "source_import"})
                or ("source_dataset" in row and not isinstance(row["source_dataset"], str))):
            raise StorageError("active snapshot metadata is malformed")
        return row

    def _page(self, table: str, key: str, snapshot_id: str,
              cursor: str | None, limit: int) -> dict[str, Any]:
        snapshot = _snapshot_id(snapshot_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 through 1000")
        if cursor is not None:
            _text_id(cursor, "cursor")
            where_cursor = f" AND {key} > $2"
            params: list[Any] = [snapshot, cursor, limit + 1]
        else:
            where_cursor = ""
            params = [snapshot, limit + 1]
        rows = self._query(
            f"SELECT {key}, payload FROM legal_graph.{table} "
            f"WHERE snapshot_id = $1{where_cursor} ORDER BY {key} ASC LIMIT ${len(params)}",
            params, limit + 1,
        )
        if len(rows) > limit + 1:
            raise StorageError("Octo returned more rows than the requested page bound")
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = [self._payload(row) for row in page_rows]
        next_cursor = page_rows[-1].get(key) if has_more and page_rows else None
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise StorageError("page key is malformed")
        return {"items": items, "next_cursor": next_cursor}

    def get_authorities(self, snapshot_id: str, cursor: str | None = None,
                        limit: int = 100) -> dict[str, Any]:
        return self._page("authorities", "authority_id", snapshot_id, cursor, limit)

    def get_citation_edges(self, snapshot_id: str, cursor: str | None = None,
                           limit: int = 100) -> dict[str, Any]:
        return self._page("citation_edges", "edge_key", snapshot_id, cursor, limit)

    def get_authority(self, authority_id: str, snapshot_id: str) -> dict[str, Any] | None:
        authority = _text_id(authority_id, "authority_id")
        snapshot = _snapshot_id(snapshot_id)
        rows = self._query(
            "SELECT payload FROM legal_graph.authorities "
            "WHERE authority_id = $1 AND snapshot_id = $2 LIMIT 2",
            [authority, snapshot], 2,
        )
        if len(rows) > 1:
            raise StorageError("authority key returned multiple rows")
        return self._payload(rows[0]) if rows else None
