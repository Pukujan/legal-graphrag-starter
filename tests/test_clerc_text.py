"""Offline tests for the CLERC opinion-text pipeline (ingest filter, chunker, expansion, BM25)."""
import gzip
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
TEXT = ROOT / "benchmarks/clerc/text"


def _load(name):
    if str(TEXT) not in sys.path:
        sys.path.insert(0, str(TEXT))
    return importlib.import_module(name)


def test_ingest_keeps_only_graph_cases_and_resumes(tmp_path):
    ingest = _load("ingest")
    nodes = tmp_path / "nodes.jsonl"
    nodes.write_text("\n".join(json.dumps(x) for x in [
        {"id": "A v. B, 1 F.3d 1", "cap_id": 11, "title": "A v. B", "court": "C", "date": "1990-01-01"},
        {"id": "Starter case", "title": "S"}]) + "\n")
    q = tmp_path / "q.jsonl"
    q.write_text(json.dumps({"gold": ["Starter case"], "gold_cap_id": 33}) + "\n")
    coll = tmp_path / "c.tsv.gz"
    with gzip.open(coll, "wt") as f:
        f.write('11\tfirst text\n22\tnot in graph\n33\t"quoted\ttext"\n')
    out = tmp_path / "out"
    assert ingest.main(["--collection", str(coll), "--nodes", str(nodes), "--queries", str(q), "--out", str(out)]) == 0
    state = json.loads((out / "state.json").read_text())
    rows = [json.loads(l) for s in state["shards"] for l in gzip.open(out / s, "rt")]
    assert [r["cap_id"] for r in rows] == ["11", "33"]
    assert rows[1]["text"] == "quoted\ttext" and rows[1]["id"] == "Starter case"
    assert state["finished"] and state["records_done"] == 3
    assert ingest.main(["--collection", str(coll), "--nodes", str(nodes), "--out", str(out)]) == 0  # no-op


def test_chunks_carry_header_and_cover_text(tmp_path):
    chunk = _load("chunk")
    tdir = tmp_path / "t"
    tdir.mkdir()
    para = "The court held that the statute applies. " * 40
    with gzip.open(tdir / "part-0000.jsonl.gz", "wt") as f:
        f.write(json.dumps({"cap_id": "1", "id": "A v. B, 1 F.3d 1", "title": "A v. B", "court": "Ct",
                            "date": "1999-02-02", "text": para + "\n\n" + para}) + "\n")
    (tdir / "state.json").write_text(json.dumps({"shards": ["part-0000.jsonl.gz"]}))
    assert chunk.main(["--text-dir", str(tdir), "--out", str(tmp_path / "c"), "--size", "500"]) == 0
    off = np.load(tmp_path / "c/offsets.npy")
    blob = (tmp_path / "c/texts.bin").read_bytes()
    texts = [blob[off[i]:off[i + 1]].decode() for i in range(len(off) - 1)]
    assert len(texts) > 2 and all(t.startswith("A v. B | Ct | 1999\n") for t in texts)
    assert all(len(t.split("\n", 1)[1]) <= 500 for t in texts)


def test_expansion_respects_holdout():
    pytest.importorskip("numpy")
    bench = _load("bench") if importlib.util.find_spec("scipy") else pytest.skip("scipy")
    g = bench.Graph.__new__(bench.Graph)
    from collections import defaultdict
    g.adj, g.indeg = defaultdict(set), defaultdict(int)
    g.edges = [("src", "gold"), ("hit", "x"), ("hit", "gold")]
    g.build(set())
    assert "gold" in g.expand(["hit"])
    g.build({("hit", "gold")})
    assert g.expand(["hit"]) == ["x"]


def test_hashed_bm25_ranks_matching_chunk(tmp_path):
    pytest.importorskip("Stemmer")
    sparse = _load("sparse")
    texts = ["summary judgment standard genuine dispute", "habeas corpus petition denied",
             "the contract was breached by the seller"]
    blob = "".join(texts).encode()
    off = np.cumsum([0] + [len(t.encode()) for t in texts])
    (tmp_path / "texts.bin").write_bytes(blob)
    idx = sparse.HashedBM25(sparse.build(tmp_path / "texts.bin", off, tmp_path / "w", workers=1, step=2, parts_per_shard=1))
    top, _ = idx.search("petition for habeas corpus", 3)
    assert int(top[0]) == 1
