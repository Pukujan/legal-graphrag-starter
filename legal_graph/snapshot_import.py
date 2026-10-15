"""Stage, verify and activate a citation-graph snapshot in Octo's app-owned schema.

Usage (key in env var ``octo_db_legal``; never printed):
  python -m legal_graph.snapshot_import migrate --workspace <id>
  python -m legal_graph.snapshot_import import  --workspace <id> [--activate]

Lifecycle: deterministic snapshot UUID (uuid5 of the canonical digest) ->
staging row -> batched idempotent inserts (one statement per request) ->
read back through GraphSnapshotStore and recompute the canonical digest ->
mark ready with exact counts -> optional atomic ready-only activation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from .retrieval import load_dataset
from .storage import GraphSnapshotStore, StorageError

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = uuid.UUID("6f1d3c8e-4b1a-5e7d-9c2f-1a2b3c4d5e6f")
Executor = Callable[[str, list[Any], int], dict[str, Any]]


def _line(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def canonical_edge(edge: dict[str, Any]) -> dict[str, str]:
    return {"source": edge.get("source", edge.get("src")),
            "target": edge.get("target", edge.get("dst")), "kind": edge["kind"]}


def edge_key(edge: dict[str, Any]) -> str:
    e = canonical_edge(edge)
    return _line([e["source"], e["target"], e["kind"]])


def graph_digest(nodes: list[dict], edges: list[dict]) -> str:
    """Order-independent sha256 over canonical node and edge records."""
    h = hashlib.sha256()
    for line in sorted(_line(n) for n in nodes):
        h.update(b"N" + line.encode() + b"\n")
    for line in sorted(_line(canonical_edge(e)) for e in edges):
        h.update(b"E" + line.encode() + b"\n")
    return h.hexdigest()


def split_sql(text: str) -> list[str]:
    """Split a migration into statements, respecting $$-quoted bodies and comments."""
    text = re.sub(r"--[^\n]*", "", text)
    out, buf, in_dollar = [], [], False
    for part in re.split(r"(\$\$|;)", text):
        if part == "$$":
            in_dollar = not in_dollar
            buf.append(part)
        elif part == ";" and not in_dollar:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(part)
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def _ok(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("error") or result.get("isError") or result.get("statementCount") != 1:
        raise StorageError(f"Octo SQL failed: {str(result.get('error') or result)[:300]}")
    return result


def migrate(execute: Executor, migrations_dir: Path = ROOT / "migrations") -> int:
    n = 0
    for path in sorted(migrations_dir.glob("*.sql")):
        for stmt in split_sql(path.read_text()):
            _ok(execute(stmt, [], 1))
            n += 1
    return n


def import_snapshot(execute: Executor, data_dir: Path | None = None, *, batch: int = 2000,
                    activate: bool = False, log=print) -> dict[str, Any]:
    nodes, edges, revision, hashes = load_dataset(data_dir or ROOT / "data")
    digest = graph_digest(nodes, edges)
    snapshot = str(uuid.uuid5(NAMESPACE, digest))
    dataset = f"KeyArgo/legal-graphrag-starter data/nodes.jsonl+edges.jsonl {revision}"
    existing = _ok(execute("SELECT status FROM legal_graph.snapshots WHERE snapshot_id = $1::uuid",
                           [snapshot], 2))["rows"]
    status = existing[0]["status"] if existing else None
    if status is None:
        _ok(execute(
            "INSERT INTO legal_graph.snapshots (snapshot_id, status, source_kind, source_dataset, "
            "source_digest, expected_authority_count, expected_edge_count) "
            "VALUES ($1::uuid, 'staging', 'source_import', $2, $3, $4, $5)",
            [snapshot, dataset, f"sha256:{digest}", len(nodes), len(edges)], 1))
        status = "staging"
    if status == "staging":
        for i in range(0, len(nodes), batch):
            rows = nodes[i:i + batch]
            _ok(execute(
                "INSERT INTO legal_graph.authorities (snapshot_id, authority_id, payload) "
                "SELECT $1::uuid, x->>'id', x FROM jsonb_array_elements($2::jsonb) AS x "
                "ON CONFLICT DO NOTHING", [snapshot, json.dumps(rows, ensure_ascii=True)], 1))
            log(f"authorities {min(i + batch, len(nodes))}/{len(nodes)}")
        for i in range(0, len(edges), batch):
            rows = [{"key": edge_key(e), "p": canonical_edge(e)} for e in edges[i:i + batch]]
            _ok(execute(
                "INSERT INTO legal_graph.citation_edges (snapshot_id, edge_key, source_id, target_id, payload) "
                "SELECT $1::uuid, x->>'key', x->'p'->>'source', x->'p'->>'target', x->'p' "
                "FROM jsonb_array_elements($2::jsonb) AS x ON CONFLICT DO NOTHING",
                [snapshot, json.dumps(rows, ensure_ascii=True)], 1))
            log(f"edges {min(i + batch, len(edges))}/{len(edges)}")
        # Independent verification: read back through the consumer and recompute.
        store = GraphSnapshotStore(execute)
        back_nodes = _read_all(store.get_authorities, snapshot)
        back_edges = _read_all(store.get_citation_edges, snapshot)
        back_digest = graph_digest(back_nodes, back_edges)
        if (len(back_nodes), len(back_edges), back_digest) != (len(nodes), len(edges), digest):
            raise StorageError("readback mismatch; snapshot left in staging")
        _ok(execute(
            "UPDATE legal_graph.snapshots SET status = 'ready', ready_at = CURRENT_TIMESTAMP, "
            "authority_count = (SELECT count(*) FROM legal_graph.authorities WHERE snapshot_id = $1::uuid), "
            "edge_count = (SELECT count(*) FROM legal_graph.citation_edges WHERE snapshot_id = $1::uuid) "
            "WHERE snapshot_id = $1::uuid AND status = 'staging' AND source_digest = $2",
            [snapshot, f"sha256:{digest}"], 1))
    if activate:
        _ok(execute(
            "INSERT INTO legal_graph.active_snapshot(singleton, snapshot_id) "
            "SELECT TRUE, snapshot_id FROM legal_graph.snapshots "
            "WHERE snapshot_id = $1::uuid AND status = 'ready' "
            "ON CONFLICT (singleton) DO UPDATE SET snapshot_id = EXCLUDED.snapshot_id, "
            "activated_at = CURRENT_TIMESTAMP", [snapshot], 1))
    meta = _ok(execute(
        "SELECT status, authority_count, edge_count, source_digest FROM legal_graph.snapshots "
        "WHERE snapshot_id = $1::uuid", [snapshot], 1))["rows"][0]
    return {"snapshot_id": snapshot, "digest": f"sha256:{digest}", "dataset": dataset, **meta}


def _read_all(getter, snapshot: str) -> list[dict]:
    out, cursor = [], None
    while True:
        page = getter(snapshot, cursor=cursor, limit=1000)
        out.extend(page["items"])
        if page["next_cursor"] is None:
            return out
        cursor = page["next_cursor"]


def main(argv: list[str] | None = None) -> int:
    from .octo_client import OctoSqlExecutor
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["migrate", "import"])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--key-env", default="octo_db_legal")
    ap.add_argument("--activate", action="store_true")
    a = ap.parse_args(argv)
    execute = OctoSqlExecutor(a.workspace, key_env=a.key_env)
    if a.command == "migrate":
        print(f"applied {migrate(execute)} idempotent statements")
    else:
        print(json.dumps(import_snapshot(execute, activate=a.activate), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
