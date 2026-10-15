import pytest

from legal_graph.search.chunking import chunk_doc, fixed_chunks, header, recursive_chunks
from legal_graph.search.hybrid import chunks_to_docs, rrf


def test_fixed_chunks_cover_text():
    t = "a" * 1234
    c = fixed_chunks(t, 500)
    assert [len(x) for x in c] == [500, 500, 234] and "".join(c) == t


def test_recursive_keeps_paragraphs_and_respects_size():
    t = "\n\n".join(["Para one is short.", "Para two. " * 30, "Para three."])
    c = recursive_chunks(t, 120)
    assert c[0].startswith("Para one is short.")
    assert all(len(x) <= 120 for x in c)
    assert c[-1].endswith("Para three.")


def test_header_and_chunk_doc():
    meta = {"title": "People v. Doe", "court": "Colo. App.", "date": "2001-05-02"}
    assert header(meta) == "People v. Doe | Colo. App. | 2001"
    c = chunk_doc("d1", "x " * 400, meta, method="fixed", size=300, with_header=True)
    assert c[0]["text"].startswith("People v. Doe | Colo. App. | 2001\n") and c[0]["doc_id"] == "d1"


def test_rrf_rewards_agreement():
    assert rrf([["x", "y"], ["y", "z"]])[0] == "y"


def test_chunks_to_docs_max_pools():
    assert chunks_to_docs([3, 0, 1, 2], None, ["A", "A", "B", "C"], 2) == ["C", "A"]


def test_bm25_index_ranks_lexical_match():
    pytest.importorskip("bm25s")
    pytest.importorskip("Stemmer")
    from legal_graph.search.hybrid import BM25Index
    idx = BM25Index(["summary judgment standard", "custody of children", "tax refund"])
    ids, _ = idx.search(["children custody dispute"], 2)
    assert ids[0][0] == 1
