"""Offline contract tests for the retrieval-engine routes (no network, no models)."""
import json

import pytest
from fastapi.testclient import TestClient

from legal_graph.engine.checks import ScorerRegistry
from legal_graph.engine.corpus import Chunk, Corpus, PurePythonBM25, best_spans
from legal_graph.engine.pipeline import Pipeline, regex_citations
from legal_graph.retrieval import CitationGraphIndex
from legal_graph.server import create_app

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CHUNKS = [
    Chunk("a:0", "A", 0, "Case A | Ct | 1990\nA warrantless search of a car requires probable cause. Other text here."),
    Chunk("a:1", "A", 1, "Case A | Ct | 1990\nThe court discussed damages at length in this section."),
    Chunk("b:0", "B", 0, "Case B | Ct | 2001\nProbable cause exists when facts would lead a prudent person to believe."),
    Chunk("c:0", "C", 0, "Case C | Ct | 1975\nAn unrelated contract dispute about shipping wheat across state lines."),
    Chunk("d:0", "D", 0, "Case D | Ct | 1980\nA vehicle exception to the warrant requirement was recognised long ago."),
]
DOCS = {d: {"name": f"Case {d}", "court": "Ct", "year": 1990, "link": None, "cap_id": None} for d in "ABCD"}
EDGES = [{"source": "A", "target": "D", "kind": "cites"}, {"source": "C", "target": "B", "kind": "cites"}]


class FakeScorer:
    model, model_version = "fake-scorer", "0"

    def score(self, pairs):
        return [0.9 if claim.lower().split()[0] in doc.lower() else 0.1 for doc, claim in pairs]


def make_pipeline(**kw):
    corpus = Corpus(CHUNKS, DOCS, revision="synthetic-corpus")
    return Pipeline(corpus, PurePythonBM25([c.body for c in CHUNKS]),
                    parse_citations=regex_citations, resolve_citations=lambda cs: {c: "C" for c in cs if c == "12 F.3d 34"},
                    dense=lambda q, ps: [len(set(q.lower().split()) & set(p.lower().split())) / 10 for p in ps],
                    dense_model="fake-dense",
                    neighbours=lambda ids: [e for e in EDGES if e["source"] in ids or e["target"] in ids],
                    reranker=lambda q, ps: [float(len(p)) / 1000 for p in ps], rerank_model="fake-rerank",
                    graph_revision="synthetic-graph", **kw)


@pytest.fixture
def client(tmp_path):
    run = tmp_path / "proxy-v1-x"
    run.mkdir()
    (run / "results.json").write_text(json.dumps({"label": "proxy", "dataset_revision": "r", "summary": {"recall@10": 0.5},
                                                  "config": {}, "per_query": [{"q": 1}]}))
    index = CitationGraphIndex([{"id": "A"}, {"id": "D"}], [EDGES[0]], dataset_revision="g", source_dataset="s")
    app = create_app(index=index, synthetic=True, pipeline=make_pipeline(), api_token=TOKEN,
                     scorers=ScorerRegistry({"minicheck": FakeScorer}), bench_dir=tmp_path, live_engine=False)
    with TestClient(app) as c:
        yield c


def stream(client, body):
    r = client.post("/api/v1/retrieve", json=body, headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    out = []
    for block in r.text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        out.append((name[len("event: "):], json.loads(data[len("data: "):])))
    return out


def test_auth_required(client):
    assert client.post("/api/v1/retrieve", json={"query": "probable cause"}).status_code == 401
    assert client.post("/api/v1/retrieve", json={"query": "probable cause"},
                       headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/v1/benchmarks").status_code == 401
    assert client.get("/api/v1/health").status_code == 200


def test_disabled_without_token():
    app = create_app(index=CitationGraphIndex([], []), pipeline=make_pipeline(), api_token="", live_engine=False)
    with TestClient(app) as c:
        assert c.get("/api/v1/benchmarks").status_code == 503


def test_stream_stage_order_and_result(client):
    events = stream(client, {"query": "probable cause for a warrantless car search, see 12 F.3d 34", "k": 3})
    stages = [d["stage"] for n, d in events if n == "stage"]
    assert stages == ["parse", "bm25", "dense", "hybrid", "graph", "rerank"]
    assert events[-1][0] == "done" and events[-2][0] == "result"
    parse = events[0][1]
    assert parse["citations"] == [{"citation": "12 F.3d 34", "doc_id": "C"}]
    for _, d in events[:-2]:
        assert d["duration_ms"] >= 0 and d["schema_version"] == "engine-retrieve/1"
    graph = next(d for n, d in events if d.get("stage") == "graph")
    assert {"doc_id": "D", "via": [{"source": "graph", "edge": "cites", "via": "A", "direction": "outgoing"}]} in graph["added"]
    result = events[-2][1]
    assert len(result["results"]) == 3
    for r in result["results"]:
        stored = {c.chunk_id: c.body for c in CHUNKS}
        for p in r["passages"]:
            assert p["text"] == stored[p["chunk_id"]]
            for s in p["spans"]:
                assert p["text"][s["start"]:s["end"]] == s["text"]
        assert r["provenance"]


def test_question_citation_provenance_and_skips(client):
    events = stream(client, {"query": "contract dispute 12 F.3d 34", "dense": False, "rerank": False, "jev": True})
    by = {d.get("stage"): d for n, d in events if n == "stage"}
    assert by["dense"]["skipped"] and by["rerank"]["skipped"]
    assert by["jev"]["skipped"] and "OPENROUTER" in by["jev"]["reason"]
    top = events[-2][1]["results"]
    c = next(r for r in top if r["case"]["id"] == "C")
    assert c["provenance"][0] == {"source": "question_citation"}


def test_json_mode_and_validation(client):
    r = client.post("/api/v1/retrieve", json={"query": "probable cause", "stream": False}, headers=AUTH)
    assert r.status_code == 200 and r.json()["result"]["type"] == "result"
    assert client.post("/api/v1/retrieve", json={"query": "x"}, headers=AUTH).status_code == 422
    assert client.post("/api/v1/retrieve", json={"query": "probable cause", "bogus": 1}, headers=AUTH).status_code == 422


def test_legacy_retrieve_unchanged(client):
    r = client.post("/api/v1/retrieve", json={"seeds": ["D"], "k": 1})
    assert r.status_code == 200 and "results" in r.json()


def test_check_scores_and_quote_verification(client):
    body = {"claims": [
        {"text": "probable cause is needed", "cited_text": "A warrantless search of a car requires probable cause.", "chunk_id": "a:0"},
        {"text": "zebra rule", "cited_text": "text not in the passage", "chunk_id": "a:1"}]}
    r = client.post("/api/v1/check", json=body, headers=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert [q["quote_verified"] for q in d["quotes"]] == [True, False]
    assert d["abstain"] is True and d["unverified_quotes"] == [1]
    assert d["claims"][0]["accepted"] is True and d["claims"][1]["accepted"] is False
    assert d["answer_confidence"] == pytest.approx(0.1)
    assert client.post("/api/v1/check", json={**body, "scorer": "jev"}, headers=AUTH).status_code == 503


def test_case_and_benchmarks(client):
    r = client.get("/api/v1/cases/A", headers=AUTH)
    assert r.status_code == 200 and [p["chunk_id"] for p in r.json()["passages"]] == ["a:0", "a:1"]
    assert client.get("/api/v1/cases/ZZ", headers=AUTH).status_code == 404
    runs = client.get("/api/v1/benchmarks", headers=AUTH).json()["runs"]
    assert runs == [{"run_id": "proxy-v1-x", "label": "proxy", "dataset_revision": "r", "summary": {"recall@10": 0.5},
                     "config": {}, "per_query_count": 1}]
    assert client.get("/api/v1/benchmarks/proxy-v1-x", headers=AUTH).json()["per_query"] == [{"q": 1}]
    assert client.get("/api/v1/benchmarks/nope", headers=AUTH).status_code == 404


def test_openapi_lists_engine_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in ["/api/v1/retrieve", "/api/v1/check", "/api/v1/benchmarks", "/api/v1/cases/{doc_id}", "/api/v1/health"]:
        assert p in paths


def test_best_spans_offsets():
    body = "Header line\nFirst sentence about cats. Probable cause is a standard. End."
    spans = best_spans(body, "probable cause")
    assert spans and body[spans[0]["start"]:spans[0]["end"]] == spans[0]["text"]
