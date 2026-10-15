"""Offline contract tests for the PROXY benchmark harness (synthetic graph)."""
import json
import pytest
from legal_graph import benchmark as b
from legal_graph.storage import GraphSnapshotStore

NODES = [{"id": f"C{i}", "type": "caselaw"} for i in range(8)] + [{"id": f"S{i}", "type": "us_statute"} for i in range(6)]
EDGES = [{"source": f"C{i}", "target": f"S{j}", "kind": "cites"} for i in range(8) for j in range(6) if (i + j) % 2 == 0]
PROXY = {"n_queries": 3, "seed": 1, "seed_fraction": 0.4, "distractors_per_seed": 1, "min_cites": 3, "max_cites": 10}


class Src:
    def load(self):
        return NODES, EDGES, "rev-1", {"kind": "synthetic"}


def test_metrics():
    g = {"a", "b"}
    assert b.recall_at(["a", "x", "b"], g, 2) == 0.5
    assert b.mrr(["x", "b"], g) == 0.5
    assert b.ndcg_at(["a", "b"], g, 10) == pytest.approx(1.0)
    assert b.ndcg_at(["x"], g, 10) == 0.0


def test_proxy_queries_deterministic_and_labelled():
    q1 = b.build_proxy_queries(NODES, EDGES, **PROXY)
    assert q1 == b.build_proxy_queries(NODES, EDGES, **PROXY)
    for q in q1:
        assert q["label"] == "proxy" and q["held_out_opinion"] not in q["gold"]
        assert set(q["seeds"]) & set(q["gold"])


def _config(tmp_path):
    cfg = {"name": "t", "queries_file": "q.jsonl", "proxy": PROXY,
           "graph": {"depth": 2, "direction": "both", "edge_kinds": ["cites"]},
           "k_values": [2, 5], "ndcg_k": 5}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    return p


def test_run_is_write_once_and_grounded(tmp_path):
    p = _config(tmp_path)
    b.freeze(p, Src())
    out = b.run(p, Src(), tmp_path / "runs")
    rec = json.loads((out / "results.json").read_text())
    assert "NOT real qrels" in rec["label"]
    assert rec["summary"]["graph_grounding_rate"] == pytest.approx(1.0)
    for row in rec["per_query"]:
        for k in (2, 5):
            assert row["augmented"][f"recall@{k}"] >= row["baseline"][f"recall@{k}"] - 1e-12  # augmented keeps seeds first
    with pytest.raises(FileExistsError):
        b.run(p, Src(), tmp_path / "runs")


def test_run_rejects_tampered_queries(tmp_path):
    p = _config(tmp_path)
    b.freeze(p, Src())
    (tmp_path / "q.jsonl").write_text("{}\n")
    with pytest.raises(ValueError):
        b.run(p, Src(), tmp_path / "runs")


def test_octo_source_pages_through_store():
    snap = "11111111-1111-4111-8111-111111111111"
    def execute(sql, params, row_limit):
        if "active_snapshot" in sql:
            rows = [{"snapshot_id": snap, "source_digest": "d"}]
        elif "authorities" in sql:
            rows = [{"authority_id": n["id"], "payload": n} for n in NODES]
            rows = [r for r in rows if len(params) == 2 or r["authority_id"] > params[1]][: params[-1]]
        else:
            es = sorted(({"edge_key": f"{e['source']}|{e['target']}", "payload": {"src": e["source"], "dst": e["target"], "kind": "cites"}} for e in EDGES), key=lambda r: r["edge_key"])
            rows = [r for r in es if len(params) == 2 or r["edge_key"] > params[1]][: params[-1]]
        return {"rows": rows, "rowCount": len(rows), "statementCount": 1, "truncated": False}
    nodes, edges, rev, prov = b.OctoSnapshotSource(GraphSnapshotStore(execute), page=4).load()
    assert len(nodes) == len(NODES) and len(edges) == len(EDGES)
    assert prov["snapshot_id"] == snap and rev.startswith(f"octo:{snap}:")
    assert {(e["source"], e["target"]) for e in edges} == {(e["source"], e["target"]) for e in EDGES}


def test_seal_and_explicit_holdout_edges(tmp_path):
    q = [{"query_id": "c1", "seeds": ["S0"], "gold": ["S2"], "label": "clerc",
          "holdout_edges": [["C0", "S2", "cites"]]}]
    (tmp_path / "q.jsonl").write_text(json.dumps(q[0]) + "\n")
    cfg = {"name": "c", "label": "clerc-test", "queries_file": "q.jsonl",
           "graph": {"depth": 2, "direction": "both", "edge_kinds": ["cites"]}, "k_values": [5], "ndcg_k": 5}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    b.seal(p, Src())
    rec = json.loads((b.run(p, Src(), tmp_path / "runs") / "results.json").read_text())
    assert rec["label"] == "clerc-test" and rec["summary"]["holdout_edges_removed"] == 1
    assert rec["summary"]["answerable_subset"]["n"] == 1
    cfg2 = json.loads(p.read_text()) | {"apply_holdout_edges": False, "name": "c2"}
    p.write_text(json.dumps(cfg2))
    rec2 = json.loads((b.run(p, Src(), tmp_path / "runs") / "results.json").read_text())
    assert rec2["summary"]["holdout_edges_removed"] == 0
