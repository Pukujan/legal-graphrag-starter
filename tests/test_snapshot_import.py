"""Offline tests for the Octo snapshot importer (fake executor, no network)."""
import json
from pathlib import Path
import pytest
from legal_graph import snapshot_import as si
from legal_graph.storage import StorageError


def test_digest_is_order_independent_and_edge_alias_tolerant():
    n = [{"id": "A", "type": "x"}, {"id": "B"}]
    e = [{"source": "A", "target": "B", "kind": "cites"}]
    assert si.graph_digest(n, e) == si.graph_digest(n[::-1], [{"src": "A", "dst": "B", "kind": "cites"}])
    assert si.graph_digest(n, e) != si.graph_digest(n, [])


def test_edge_key_is_unambiguous():
    assert si.edge_key({"source": "a,b", "target": "c", "kind": "cites"}) != \
        si.edge_key({"source": "a", "target": "b,c", "kind": "cites"})


def test_split_sql_keeps_dollar_bodies():
    stmts = si.split_sql("CREATE A; -- c;\nCREATE FUNCTION f() AS $$ BEGIN x; y; END; $$;\nDROP B;")
    assert len(stmts) == 3 and "x; y;" in stmts[1]
    for path in Path("migrations").glob("*.sql"):
        assert all(s.strip() for s in si.split_sql(path.read_text()))


class FakeOcto:
    """Tiny in-memory stand-in honouring the SQL shapes the importer sends."""
    def __init__(self, corrupt=False):
        self.snap, self.auth, self.edges, self.active, self.corrupt = {}, {}, {}, None, corrupt

    def __call__(self, sql, params, row_limit):
        r = []
        if sql.startswith("SELECT status FROM"):
            r = [{"status": self.snap[params[0]]["status"]}] if params[0] in self.snap else []
        elif sql.startswith("INSERT INTO legal_graph.snapshots"):
            self.snap[params[0]] = {"status": "staging", "source_digest": params[2]}
        elif "legal_graph.authorities (" in sql:
            for x in json.loads(params[1]):
                self.auth.setdefault(x["id"], x)
        elif "legal_graph.citation_edges (" in sql:
            for x in json.loads(params[1]):
                p = dict(x["p"])
                if self.corrupt:
                    p["kind"] = "tampered"
                self.edges.setdefault(x["key"], p)
        elif sql.startswith("SELECT authority_id, payload") or sql.startswith("SELECT edge_key, payload"):
            key, src = ("authority_id", self.auth) if "authority_id" in sql else ("edge_key", self.edges)
            items = sorted(src.items())
            if len(params) == 3:
                items = [i for i in items if i[0] > params[1]]
            r = [{key: k, "payload": v} for k, v in items[: params[-1]]]
        elif sql.startswith("UPDATE legal_graph.snapshots"):
            self.snap[params[0]].update(status="ready", authority_count=len(self.auth), edge_count=len(self.edges))
        elif sql.startswith("INSERT INTO legal_graph.active_snapshot"):
            if self.snap[params[0]]["status"] == "ready":
                self.active = params[0]
        elif sql.startswith("SELECT status, authority_count"):
            s = self.snap[params[0]]
            r = [{"status": s["status"], "authority_count": s.get("authority_count"),
                  "edge_count": s.get("edge_count"), "source_digest": s["source_digest"]}]
        return {"rows": r, "rowCount": len(r), "statementCount": 1, "truncated": False}


def _data(tmp_path):
    (tmp_path / "nodes.jsonl").write_text("".join(json.dumps({"id": f"N{i}", "type": "caselaw"}) + "\n" for i in range(7)))
    (tmp_path / "edges.jsonl").write_text("".join(json.dumps({"src": f"N{i}", "dst": f"N{(i+1)%7}", "kind": "cites"}) + "\n" for i in range(7)))
    return tmp_path


def test_import_stage_verify_activate_idempotent(tmp_path):
    octo = FakeOcto()
    out = si.import_snapshot(octo, _data(tmp_path), batch=3, activate=True, log=lambda *_: None)
    assert out["status"] == "ready" and out["authority_count"] == 7 and out["edge_count"] == 7
    assert octo.active == out["snapshot_id"]
    again = si.import_snapshot(octo, tmp_path, batch=3, log=lambda *_: None)
    assert again["snapshot_id"] == out["snapshot_id"]


def test_readback_mismatch_leaves_staging(tmp_path):
    octo = FakeOcto(corrupt=True)
    with pytest.raises(StorageError):
        si.import_snapshot(octo, _data(tmp_path), batch=3, activate=True, log=lambda *_: None)
    assert list(octo.snap.values())[0]["status"] == "staging" and octo.active is None


def test_octo_client_retries_transport_errors(monkeypatch):
    import io, urllib.error
    from legal_graph import octo_client
    monkeypatch.setenv("octo_test_key", "x")
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.URLError("handshake timed out")
        return Resp(b'{"rows":[],"statementCount":1,"truncated":false}')
    monkeypatch.setattr(octo_client.urllib.request, "urlopen", fake)
    monkeypatch.setattr(octo_client.time, "sleep", lambda s: None)
    ex = octo_client.OctoSqlExecutor("ws", key_env="octo_test_key")
    assert ex("SELECT 1", [], 1)["statementCount"] == 1 and len(calls) == 3
