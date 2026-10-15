import json
import stat

import pytest

from legal_graph import retrieval_bench as rb


def _w(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture()
def bench(tmp_path):
    corpus = [
        {"id": "d1", "title": "Eviction notice", "text": "landlord must give ten days notice before eviction", "date": "2000-01-01"},
        {"id": "d2", "title": "Security deposit", "text": "security deposit returned within one month", "date": "2000-01-01"},
        {"id": "d3", "title": "Later case", "text": "eviction notice landlord tenant", "date": "2020-01-01"},
        {"id": "d4", "title": "Habitability", "text": "warranty of habitability repairs", "date": "2000-01-01"},
    ]
    nodes = [{"id": d["id"], "type": "caselaw"} for d in corpus]
    edges = [{"src": "d1", "dst": "d4", "kind": "cites", "source": "provenance text"},
             {"source": "d2", "target": "d4", "kind": "cites"}]
    queries = [
        {"query_id": "q1", "text": "eviction notice landlord", "gold": ["d1"], "date": "2010-01-01", "split": "test"},
        {"query_id": "q2", "text": "deposit returned", "gold": ["d4"], "seeds": ["d2"], "date": "2010-01-01",
         "split": "test", "exclude": ["d2"]},
    ]
    _w(tmp_path / "corpus.jsonl", corpus)
    _w(tmp_path / "nodes.jsonl", nodes)
    _w(tmp_path / "edges.jsonl", edges)
    _w(tmp_path / "queries.jsonl", queries)
    cfg = {"name": "t", "queries_file": "queries.jsonl", "corpus_file": "corpus.jsonl",
           "nodes_file": "nodes.jsonl", "edges_file": "edges.jsonl",
           "graph": {"depth": 1, "direction": "both", "edge_kinds": ["cites"]},
           "k_values": [1, 5], "ndcg_k": 5,
           "systems": [{"name": "bm25", "kind": "bm25"}, {"name": "seeds", "kind": "seeds"},
                       {"name": "seeds+graph", "kind": "graph", "from": "seeds", "n_seeds": 5},
                       {"name": "fused", "kind": "fuse", "from": ["bm25", "seeds"]}]}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    rb.seal(tmp_path / "config.json")
    return tmp_path


def test_run_is_frozen_write_once_and_scores(bench):
    out = rb.run(bench / "config.json", bench / "runs")
    rec = json.loads((out / "results.json").read_text())
    s = rec["summary"]
    assert s["n_queries"] == 2
    q1 = next(r for r in rec["per_query"] if r["query_id"] == "q1")
    assert q1["bm25"]["recall@1"] == 1.0          # d3 is dated after the query and dropped
    q2 = next(r for r in rec["per_query"] if r["query_id"] == "q2")
    assert q2["seeds+graph"]["recall@5"] == 1.0   # d2 -> d4 via the graph (src/dst + source keys)
    assert q2["bm25"]["recall@5"] == 0.0          # d2 excluded, d4 not lexical match
    assert not (out / "results.json").stat().st_mode & stat.S_IWUSR
    with pytest.raises(FileExistsError):
        rb.run(bench / "config.json", bench / "runs")


def test_tampered_queries_rejected(bench):
    with open(bench / "queries.jsonl", "a") as f:
        f.write(json.dumps({"query_id": "q3", "text": "x", "gold": ["d1"]}) + "\n")
    with pytest.raises(ValueError):
        rb.run(bench / "config.json", bench / "runs")


def test_holdout_removes_query_opinion_edges(bench):
    rows = [json.loads(line) for line in (bench / "queries.jsonl").read_text().splitlines()]
    rows[1]["held_out_opinion"] = "d2"
    _w(bench / "queries.jsonl", rows)
    rb.seal(bench / "config.json")
    rec = json.loads((rb.run(bench / "config.json", bench / "runs") / "results.json").read_text())
    q2 = next(r for r in rec["per_query"] if r["query_id"] == "q2")
    assert q2["seeds+graph"]["recall@5"] == 0.0


def test_mask_paragraph():
    pytest.importorskip("eyecite")
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "bcc", Path(__file__).resolve().parents[1] / "benchmarks/colorado/build_cap_citations.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    from eyecite import get_citations
    p = ("We said so in Levine v. Smith, 192 Colo. 188, 557 P.2d 386 (1976). Levine, 192 Colo. at 189. "
         "Accord Jones v. Roe, 900 P.2d 1, 4 (Colo. 1995).")
    res = lambda c: 1 if c.groups.get("page") in ("188", "386") else 2  # noqa: E731
    out = m.mask_paragraph(p, get_citations(p), {1}, res, ["192 Colo. 188", "557 P.2d 386"])
    assert "Levine" not in out and "192 Colo" not in out and "557 P.2d" not in out
    assert "Jones v. Roe, 900 P.2d 1" in out


def test_bench_diff(bench, tmp_path):
    from legal_graph import bench_diff
    run = rb.run(bench / "config.json", bench / "runs")
    out = bench_diff.diff(run, [("bm25", "seeds+graph")], bench / "queries.jsonl", metrics=("recall@5", "mrr"),
                          found_k=5, n_perm=200, n_boot=100)
    p = out["pairs"][0]
    assert p["only_found_counts"] == {"only_a": 1, "only_b": 1}
    assert p["examples"]["only_b"][0]["query_id"] == "q2"
    st = p["metrics"]["mrr"]
    assert st["win"] + st["tie"] + st["loss"] == 2 and 0 < st["p_permutation"] <= 1
    rec = json.loads((run / "results.json").read_text())
    bench_diff.write(out, rec["per_query"], bench_diff.systems_of(rec), tmp_path / "d")
    assert (tmp_path / "d" / "per_query.tsv").read_text().count("\n") == 3
