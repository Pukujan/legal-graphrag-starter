"""The disk-backed corpus and BM25 index answer exactly like the in-memory ones, from memory-mapped files."""
import random

import numpy as np
import pytest

from legal_graph.engine.corpus import Chunk, CompactBM25, Corpus
from legal_graph.engine.diskindex import build_index, index_is_current, open_corpus
from legal_graph.engine.models import TextRetrieveRequest
from legal_graph.engine.pipeline import Pipeline, regex_citations

WORDS = ("warrant probable cause vehicle search automobile exception arrest officer seizure consent "
         "homestead exemption mineral estate creditor bankruptcy water right priority ditch rancher "
         "contract damages breach statute court appeal judgment evidence suppress traffic stop").split()


def make_rows(n=90, docs=15, seed=7):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        body = " ".join(rng.choice(WORDS) for _ in range(rng.randint(5, 40)))
        if i % 11 == 0:
            body += " § 38-41-201 café résumé naïve"            # non-ASCII must survive the round trip
        if i == 5:
            body = ""                                              # an empty passage is a legal row
        if i == 6:
            body += " " + "x" * 90                                 # an outlier term
        # ids are deliberately not in sorted order, so lookup by id cannot rely on the file order
        rows.append({"chunk_id": f"c-{(i * 37) % n:04d}", "work_id": f"W{i % docs:03d}", "ord": i // docs, "body": body})
    return rows


@pytest.fixture
def built(tmp_path):
    rows = make_rows()
    out = tmp_path / "index"
    build_index(iter(rows), out, revision="r1")          # an iterator: the build streams, it never needs a list
    docs = {r["work_id"]: {"name": r["work_id"], "court": "Ct", "year": 1990, "link": None, "cap_id": None} for r in rows}
    corpus, lexical = open_corpus(out, docs)
    memory = Corpus([Chunk(r["chunk_id"], r["work_id"], r["ord"], r["body"]) for r in rows], docs, revision="r1")
    return rows, out, corpus, lexical, memory


def test_chunks_round_trip(built):
    rows, _, corpus, _, memory = built
    assert len(corpus.chunks) == len(rows)
    for i, row in enumerate(rows):
        assert corpus.chunks[i] == Chunk(row["chunk_id"], row["work_id"], row["ord"], row["body"])
    assert corpus.chunks[np.int64(3)] == memory.chunks[3]       # the pipeline indexes with numpy integers too
    assert corpus.revision == "r1"


def test_by_doc_and_by_id_match_the_in_memory_corpus(built):
    rows, _, corpus, _, memory = built
    assert len(corpus.by_doc) == len(memory.by_doc)
    for doc, idxs in memory.by_doc.items():
        assert doc in corpus.by_doc
        assert list(corpus.by_doc[doc]) == idxs
        assert list(corpus.by_doc.get(doc, [])) == idxs
    assert "nope" not in corpus.by_doc and corpus.by_doc.get("nope", []) == []
    for cid, i in memory.by_id.items():
        assert cid in corpus.by_id and corpus.by_id[cid] == i
    assert "c-9999" not in corpus.by_id


@pytest.mark.parametrize("query", [
    "probable cause automobile exception", "warrant", "the of and", "", "homestead homestead homestead",
    "zzzunknownterm", "warrant zzzunknownterm vehicle", "café", "x" * 90, "38 41 201", "WATER Right PRIORITY",
])
def test_bm25_scores_match_compact_bm25(built, query):
    rows, _, _, lexical, _ = built
    reference = CompactBM25([r["body"] for r in rows])
    got, want = np.asarray(lexical.score_all(query)), np.asarray(reference.score_all(query))
    assert got.shape == want.shape == (len(rows),)
    np.testing.assert_allclose(got, want, rtol=1e-4, atol=1e-5)
    assert lexical.name == "bm25-mmap"


def _strip_timing(events):
    return [{k: v for k, v in e.items() if k not in ("duration_ms", "elapsed_ms")} for e in events]


@pytest.mark.parametrize("query", ["probable cause automobile exception", "homestead exemption mineral estate",
                                   "water right priority ditch rancher"])
def test_pipeline_results_are_identical(built, query):
    rows, _, corpus, lexical, memory = built
    reference = CompactBM25([r["body"] for r in rows])
    edges = [{"source": "W001", "target": "W002", "kind": "cites"}]
    kw = dict(parse_citations=regex_citations, resolve_citations=lambda cs: {},
              neighbours=lambda ids: [e for e in edges if e["source"] in ids or e["target"] in ids],
              graph_revision="g")
    req = TextRetrieveRequest(query=query, rerank=False, dense=False)
    disk = _strip_timing(list(Pipeline(corpus, lexical, **kw).run(req)))
    mem = _strip_timing(list(Pipeline(memory, reference, **kw).run(req)))
    for d, m in zip(disk, mem):
        d, m = dict(d), dict(m)
        for e in (d, m):
            e.pop("retriever", None)                         # the scorer's own name differs by design
        assert d == m


def test_reopening_gives_the_same_answers_and_currency_follows_the_revision(built):
    rows, out, corpus, lexical, _ = built
    assert index_is_current(out, revision="r1", count=len(rows))
    assert not index_is_current(out, revision="r2", count=len(rows))
    assert not index_is_current(out, revision="r1", count=len(rows) + 1)
    assert not index_is_current(out / "missing", revision="r1", count=len(rows))
    again, lex2 = open_corpus(out, corpus.docs)
    np.testing.assert_array_equal(lexical.score_all("probable cause"), lex2.score_all("probable cause"))
    assert again.chunks[10] == corpus.chunks[10]


def test_a_partly_written_index_is_not_current(tmp_path):
    out = tmp_path / "index"
    out.mkdir()
    (out / "meta.json").write_text("{not json")
    assert not index_is_current(out, revision="r1", count=1)


# ---- the live start-up path: stream the Octo load to disk, build once, open mapped ------------------------------

from legal_graph.engine import octo_source  # noqa: E402


class FakeOcto:
    """Just enough of the SQL executor for the three queries the loader makes."""

    def __init__(self, rows):
        self.rows, self.paged = sorted(rows, key=lambda r: r["chunk_id"]), 0

    def __call__(self, query, params, limit):
        if "count(*)" in query:
            return {"rows": [{"n": len(self.rows)}]}
        if "FROM legal_graph.chunk c" in query:
            self.paged += 1
            page = [r for r in self.rows if r["chunk_id"] > params[0]][:limit]
            return {"rows": page}
        if "FROM legal_graph.work" in query:
            works = sorted({r["work_id"] for r in self.rows})
            return {"rows": [{"work_id": w, "court_id": "Ct", "decided_on": "1990-01-02", "case_name": w, "ext_ids": {}}
                             for w in works]}
        raise AssertionError(query)


@pytest.fixture
def octo(monkeypatch):
    monkeypatch.setattr(octo_source, "PAGE", 25)
    monkeypatch.delenv("ENGINE_CHUNK_LIMIT", raising=False)
    return FakeOcto(make_rows())


def test_the_load_streams_to_a_file_and_pages_from_octo_only_once(tmp_path, octo):
    path, count = octo_source.ensure_chunks_file(octo, tmp_path)
    assert count == 90 and path.name == "chunks.jsonl"
    assert sum(1 for _ in path.open()) == 90
    pages = octo.paged
    assert pages >= 4                                   # 90 rows in pages of 25
    again, _ = octo_source.ensure_chunks_file(octo, tmp_path)
    assert again == path and octo.paged == pages        # a complete cache is not fetched again


def test_a_short_cache_is_refetched(tmp_path, octo):
    path, _ = octo_source.ensure_chunks_file(octo, tmp_path)
    lines = path.read_text().splitlines()[:50]
    path.write_text("\n".join(lines) + "\n")
    before = octo.paged
    octo_source.ensure_chunks_file(octo, tmp_path)
    assert octo.paged > before and sum(1 for _ in path.open()) == 90


def test_start_up_builds_once_then_only_opens(tmp_path, octo, monkeypatch):
    built = []
    real = octo_source.build_index
    monkeypatch.setattr(octo_source, "build_index", lambda *a, **k: (built.append(1), real(*a, **k))[1])
    corpus, lexical = octo_source.load_corpus_disk(octo, tmp_path)
    assert built == [1] and corpus.revision == "octo-chunk:90" and len(corpus.chunks) == 90
    assert corpus.docs["W003"]["year"] == 1990
    corpus2, lexical2 = octo_source.load_corpus_disk(octo, tmp_path)
    assert built == [1]                                  # the second start found a current index
    np.testing.assert_array_equal(lexical.score_all("probable cause"), lexical2.score_all("probable cause"))


def test_a_changed_chunk_cache_rebuilds_the_index(tmp_path, octo, monkeypatch):
    built = []
    real = octo_source.build_index
    monkeypatch.setattr(octo_source, "build_index", lambda *a, **k: (built.append(1), real(*a, **k))[1])
    octo_source.load_corpus_disk(octo, tmp_path)
    path = tmp_path / "chunks.jsonl"
    path.write_text(path.read_text().replace("warrant", "warrant", 1) + "")   # same size, same count...
    import os
    os.utime(path, ns=(1, 1))                                                  # ...but not the same file
    octo_source.load_corpus_disk(octo, tmp_path)
    assert built == [1, 1]


def test_the_old_path_stays_when_bm25s_is_installed_or_the_switch_is_off(monkeypatch):
    monkeypatch.setattr(octo_source, "_has_bm25s", lambda: False)
    monkeypatch.delenv("ENGINE_DISK_INDEX", raising=False)
    assert octo_source.disk_index_enabled() is True
    monkeypatch.setenv("ENGINE_DISK_INDEX", "off")
    assert octo_source.disk_index_enabled() is False
    monkeypatch.delenv("ENGINE_DISK_INDEX")
    monkeypatch.setattr(octo_source, "_has_bm25s", lambda: True)
    assert octo_source.disk_index_enabled() is False


def test_an_index_built_from_another_file_is_not_current(built):
    rows, out, *_ = built
    assert index_is_current(out, revision="r1", count=len(rows))                       # no fingerprint asked for
    assert not index_is_current(out, revision="r1", count=len(rows), source="other")   # built with source=""
