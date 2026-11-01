"""Disk-backed passages and BM25 postings, opened as memory-mapped files.

The in-memory engine keeps all 800k passages as Python objects and every posting as a NumPy array, which is
about 3 GB of heap before any model is loaded, and it rebuilds the BM25 index on every start. Here the same
data is written once as flat files and opened with ``np.memmap``: the operating system keeps the hot pages
in its cache and drops the cold ones, the heap holds only the small lookup tables, and a restart opens the
files in a second instead of re-tokenising the corpus.

Nothing about the answers changes. ``DiskChunks`` is a sequence of the same ``Chunk`` values, ``by_doc`` and
``by_id`` are mappings with the same contents, and ``MmapBM25.score_all`` computes the same Okapi BM25 scores
as ``CompactBM25`` (same tokenizer, same arithmetic, same stored term frequencies).

Layout of an index directory (``meta.json`` is written last, so a half-written index is never current):

    text.bin  text_off.bin    passage bodies, UTF-8, back to back, and int64 offsets (count + 1)
    cid.bin   cid_off.bin     chunk ids, the same way
    ord.bin   doc.bin         int32 passage order, int32 case number (an index into docs.json)
    id_order.bin              int32 chunk indices sorted by chunk id bytes, for lookup by id
    doc_idx.bin doc_off.bin   int32 chunk indices grouped by case (ascending), int64 offsets (cases + 1)
    docs.json                 case ids, in case-number order
    vocab.json  idf.bin       terms (sorted) and float32 idf per term
    post_off.bin              int64 offsets into the postings, per term (terms + 1)
    post_doc.bin post_tf.bin  int32 passage index and uint16 term frequency, term by term
    len.bin                   int32 passage length in tokens
"""
from __future__ import annotations

import array
import json
import math
import shutil
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .corpus import Chunk, Corpus, tokenize

VERSION = 1
_MAX_TF = 65535


def _load(path: Path, dtype: Any) -> np.ndarray:
    """A read-only memory map of a flat file (an empty file is an empty array: ``np.memmap`` refuses those)."""
    if path.stat().st_size == 0:
        return np.empty(0, dtype=dtype)
    return np.memmap(path, dtype=dtype, mode="r")


def _write(path: Path, values: Any, dtype: Any) -> None:
    np.asarray(values, dtype=dtype).tofile(path)


def build_index(rows: Iterable[dict[str, Any]], out_dir: Path, revision: str, k1: float = 1.5, b: float = 0.75,
                source: str = "") -> None:
    """Stream ``rows`` (``chunk_id``, ``work_id``, ``ord``, ``body``) into an index directory.

    One pass over the rows, nothing but the postings kept in memory. The directory is built beside the target
    and renamed into place, so a reader never sees a half-built index and a failed build leaves the old one.
    """
    out_dir = Path(out_dir)
    tmp = out_dir.with_name(out_dir.name + ".building")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    text_off, cid_off = array.array("q", [0]), array.array("q", [0])
    orders, doc_nums, lengths = array.array("i"), array.array("i"), array.array("i")
    cids: list[bytes] = []
    doc_number: dict[str, int] = {}
    docs: dict[str, array.array] = {}
    tfs: dict[str, array.array] = {}
    df: Counter[str] = Counter()

    with (tmp / "text.bin").open("wb") as text_f, (tmp / "cid.bin").open("wb") as cid_f:
        for i, row in enumerate(rows):
            body = row["body"].encode("utf-8")
            cid = row["chunk_id"].encode("utf-8")
            text_f.write(body)
            cid_f.write(cid)
            text_off.append(text_off[-1] + len(body))
            cid_off.append(cid_off[-1] + len(cid))
            cids.append(cid)
            orders.append(int(row["ord"]))
            doc_nums.append(doc_number.setdefault(row["work_id"], len(doc_number)))
            tf = Counter(tokenize(row["body"]))
            lengths.append(sum(tf.values()))
            df.update(tf.keys())
            for term, freq in tf.items():
                bucket = docs.get(term)
                if bucket is None:
                    bucket = docs[term] = array.array("i")
                    tfs[term] = array.array("H")
                bucket.append(i)
                tfs[term].append(freq if freq < _MAX_TF else _MAX_TF)
    n = len(orders)

    for name, values, dtype in (("text_off", text_off, "<i8"), ("cid_off", cid_off, "<i8"), ("ord", orders, "<i4"),
                                ("doc", doc_nums, "<i4"), ("len", lengths, "<i4")):
        _write(tmp / f"{name}.bin", values, dtype)
    _write(tmp / "id_order.bin", sorted(range(n), key=cids.__getitem__), "<i4")
    del cids

    # Passages grouped by case: stable by passage index, so each case lists its passages in ascending order.
    order = np.argsort(np.frombuffer(doc_nums, dtype="<i4"), kind="stable") if n else np.empty(0, dtype="<i4")
    _write(tmp / "doc_idx.bin", order, "<i4")
    counts = np.bincount(np.frombuffer(doc_nums, dtype="<i4"), minlength=len(doc_number)) if n else np.empty(0, dtype=np.int64)
    _write(tmp / "doc_off.bin", np.concatenate([[0], np.cumsum(counts)]), "<i8")
    (tmp / "docs.json").write_text(json.dumps(list(doc_number)))

    terms = sorted(docs)
    avg = float(np.mean(np.frombuffer(lengths, dtype="<i4"))) if n else 1.0
    post_off = array.array("q", [0])
    idf = np.empty(len(terms), dtype="<f4")
    with (tmp / "post_doc.bin").open("wb") as doc_f, (tmp / "post_tf.bin").open("wb") as tf_f:
        for t, term in enumerate(terms):
            docs[term].tofile(doc_f)
            tfs[term].tofile(tf_f)
            post_off.append(post_off[-1] + len(docs[term]))
            idf[t] = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            del docs[term], tfs[term]
    _write(tmp / "post_off.bin", post_off, "<i8")
    idf.tofile(tmp / "idf.bin")
    (tmp / "vocab.json").write_text(json.dumps(terms))

    meta = {"version": VERSION, "revision": revision, "count": n, "terms": len(terms), "postings": post_off[-1],
            "avg_len": avg, "k1": k1, "b": b, "source": source, "complete": True}
    (tmp / "meta.json").write_text(json.dumps(meta))
    if out_dir.exists():
        shutil.rmtree(out_dir)
    tmp.rename(out_dir)


def _meta(out_dir: Path) -> dict[str, Any] | None:
    try:
        meta = json.loads((Path(out_dir) / "meta.json").read_text())
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and meta.get("complete") and meta.get("version") == VERSION else None


def index_is_current(out_dir: Path, revision: str, count: int, source: str | None = None) -> bool:
    """Whether the directory holds a finished index of exactly this corpus revision and size.

    ``source`` is a fingerprint of the file the rows came from (its size and modification time): when given,
    an index built from a different file is stale even if the revision and the count match.
    """
    meta = _meta(out_dir)
    if not meta or meta["revision"] != revision or meta["count"] != count:
        return False
    return source is None or meta.get("source") == source


class DiskChunks(Sequence):
    """The passages, as a read-only sequence of ``Chunk`` built on demand from the mapped files."""

    def __init__(self, root: Path, docs: list[str]):
        self._text, self._text_off = _load(root / "text.bin", np.uint8), _load(root / "text_off.bin", "<i8")
        self._cid, self._cid_off = _load(root / "cid.bin", np.uint8), _load(root / "cid_off.bin", "<i8")
        self._ord, self._doc = _load(root / "ord.bin", "<i4"), _load(root / "doc.bin", "<i4")
        self._docs = docs

    def __len__(self) -> int:
        return len(self._ord)

    def _cid_bytes(self, i: int) -> bytes:
        return self._cid[self._cid_off[i]:self._cid_off[i + 1]].tobytes()

    def body(self, i: int) -> str:
        return self._text[self._text_off[i]:self._text_off[i + 1]].tobytes().decode("utf-8")

    def __getitem__(self, i: Any):
        if isinstance(i, slice):
            return [self[j] for j in range(*i.indices(len(self)))]
        i = int(i)
        if i < 0:
            i += len(self)
        if not 0 <= i < len(self):
            raise IndexError(i)
        return Chunk(self._cid_bytes(i).decode("utf-8"), self._docs[self._doc[i]], int(self._ord[i]), self.body(i))


class _IdMap(Mapping):
    """chunk id -> passage index, by binary search over the ids sorted by their bytes."""

    def __init__(self, chunks: DiskChunks, order: np.ndarray):
        self._chunks, self._order = chunks, order

    def __len__(self) -> int:
        return len(self._order)

    def __iter__(self) -> Iterator[str]:
        return (self._chunks._cid_bytes(int(i)).decode("utf-8") for i in self._order)

    def _find(self, key: Any) -> int | None:
        if not isinstance(key, str):
            return None
        want = key.encode("utf-8")
        lo, hi = 0, len(self._order)
        while lo < hi:
            mid = (lo + hi) // 2
            here = self._chunks._cid_bytes(int(self._order[mid]))
            if here < want:
                lo = mid + 1
            else:
                hi = mid
        if lo < len(self._order) and self._chunks._cid_bytes(int(self._order[lo])) == want:
            return int(self._order[lo])
        return None

    def __contains__(self, key: object) -> bool:
        return self._find(key) is not None

    def __getitem__(self, key: str) -> int:
        found = self._find(key)
        if found is None:
            raise KeyError(key)
        return found


class _DocMap(Mapping):
    """case id -> the passage indices of that case, ascending; small enough to keep the case table in memory."""

    def __init__(self, docs: list[str], idx: np.ndarray, off: np.ndarray):
        self._number = {d: n for n, d in enumerate(docs)}
        self._docs, self._idx, self._off = docs, idx, off

    def __len__(self) -> int:
        return len(self._docs)

    def __iter__(self) -> Iterator[str]:
        return iter(self._docs)

    def __getitem__(self, doc: str) -> list[int]:
        n = self._number[doc]
        return self._idx[self._off[n]:self._off[n + 1]].tolist()


class MmapBM25:
    """Okapi BM25 over memory-mapped postings. The same scores as ``CompactBM25``, without holding them."""

    name = "bm25-mmap"

    def __init__(self, root: Path, meta: dict[str, Any]):
        self.k1, self.b, self.avg = float(meta["k1"]), float(meta["b"]), float(meta["avg_len"])
        self.len = _load(root / "len.bin", "<i4")
        self._off = _load(root / "post_off.bin", "<i8")
        self._doc = _load(root / "post_doc.bin", "<i4")
        self._tf = _load(root / "post_tf.bin", "<u2")
        self._idf = _load(root / "idf.bin", "<f4")
        self._term = {t: i for i, t in enumerate(json.loads((root / "vocab.json").read_text()))}

    def score_all(self, query: str) -> np.ndarray:
        out = np.zeros(len(self.len), dtype=np.float32)
        if not len(self.len):
            return out
        k1, b, avg = self.k1, self.b, self.avg
        for term in set(tokenize(query)):
            t = self._term.get(term)
            if t is None:
                continue
            doc_ids = self._doc[self._off[t]:self._off[t + 1]]
            freq = self._tf[self._off[t]:self._off[t + 1]].astype(np.float32)
            denom = freq + k1 * (1.0 - b + b * self.len[doc_ids] / avg)
            out[doc_ids] += float(self._idf[t]) * freq * (k1 + 1.0) / denom
        return out


def open_corpus(out_dir: Path, docs: dict[str, dict[str, Any]]) -> tuple[Corpus, MmapBM25]:
    """Open a finished index as a ``Corpus`` and its BM25 scorer. ``docs`` is the per-case metadata."""
    root = Path(out_dir)
    meta = _meta(root)
    if meta is None:
        raise FileNotFoundError(f"no finished index in {root}")
    doc_ids: list[str] = json.loads((root / "docs.json").read_text())
    chunks = DiskChunks(root, doc_ids)
    by_id = _IdMap(chunks, _load(root / "id_order.bin", "<i4"))
    by_doc = _DocMap(doc_ids, _load(root / "doc_idx.bin", "<i4"), _load(root / "doc_off.bin", "<i8"))
    corpus = Corpus(chunks, docs, revision=meta["revision"], by_doc=by_doc, by_id=by_id)  # type: ignore[arg-type]
    return corpus, MmapBM25(root, meta)
