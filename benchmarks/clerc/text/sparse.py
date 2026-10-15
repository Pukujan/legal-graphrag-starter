"""Disk-backed BM25 over millions of chunks without holding token lists in memory.

``bm25s`` needs every tokenized chunk in Python lists before indexing, which does not
fit in 15 GB for the full CLERC graph subset. This builds the same Okapi BM25 scores
(k1=1.5, b=0.75, Lucene idf, English stopwords + Snowball stemmer as in bm25s) as
term-major sparse shards, counted in parallel worker ranges that are saved to disk
and resumed. Term ids are crc32(stem) mod 2**24 (hashing trick; collisions are rare).
"""
from __future__ import annotations

import json
import re
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import scipy.sparse as sp

NBUCK = 1 << 24
K1, B = 1.5, 0.75
_TOK = re.compile(r"(?u)\b\w\w+\b")
_STEM = None
_CACHE: dict[str, int] = {}


def _stopwords() -> frozenset:
    try:
        from bm25s.stopwords import STOPWORDS_EN
        return frozenset(STOPWORDS_EN)
    except Exception:  # pragma: no cover - bm25s optional
        return frozenset()


_SW = _stopwords()


def term_ids(text: str) -> list[int]:
    global _STEM
    if _STEM is None:
        import Stemmer
        _STEM = Stemmer.Stemmer("english")
    out = []
    for w in _TOK.findall(text.lower()):
        if w in _SW:
            continue
        t = _CACHE.get(w)
        if t is None:
            t = zlib.crc32(_STEM.stemWord(w).encode()) % NBUCK
            if len(_CACHE) < 2_000_000:
                _CACHE[w] = t
        out.append(t)
    return out


def _count_range(args):
    texts_path, offsets, lo, hi, out = args  # offsets = global offsets[lo:hi + 1]
    out = Path(out)
    if out.exists():
        return str(out)
    indptr, idx, cnt = [0], [], []
    with open(texts_path, "rb") as f:
        f.seek(int(offsets[0]))
        blob = f.read(int(offsets[-1] - offsets[0]))
    base = int(offsets[0])
    for i in range(hi - lo):
        t = blob[int(offsets[i]) - base:int(offsets[i + 1]) - base].decode("utf-8", "replace")
        u, c = np.unique(np.asarray(term_ids(t), dtype=np.int32), return_counts=True)
        idx.append(u)
        cnt.append(c.astype(np.int32))
        indptr.append(indptr[-1] + len(u))
    tmp = out.with_suffix(".tmp.npz")
    np.savez(tmp, indptr=np.asarray(indptr, np.int64),
             idx=np.concatenate(idx) if idx else np.zeros(0, np.int32),
             cnt=np.concatenate(cnt) if cnt else np.zeros(0, np.int32))
    tmp.rename(out)
    return str(out)


def build(texts_path: Path, offsets: np.ndarray, work: Path, *, workers: int = 4, step: int = 100_000,
          parts_per_shard: int = 5) -> Path:
    """Count terms per chunk in resumable ranges, then write BM25 shards ``shard-*.npz``.

    Each shard is a CSC matrix (chunks x term buckets) for a contiguous chunk range,
    weighted with corpus-global idf and average length, so peak memory stays at one
    shard instead of the whole corpus.
    """
    work.mkdir(parents=True, exist_ok=True)
    done = work / "shards.json"
    if done.exists():
        return done
    n = len(offsets) - 1
    jobs = [(str(texts_path), offsets[lo:min(n, lo + step) + 1], lo, min(n, lo + step),
             str(work / f"tf-{lo:09d}.npz")) for lo in range(0, n, step)]
    with ProcessPoolExecutor(workers) as ex:
        for j, _ in enumerate(ex.map(_count_range, jobs)):
            print("tf", j + 1, "/", len(jobs), flush=True)
    df = np.zeros(NBUCK, np.int64)
    tokens = 0.0
    for j in jobs:
        p = np.load(j[4])
        df += np.bincount(p["idx"], minlength=NBUCK)
        tokens += float(p["cnt"].sum())
    avg = tokens / max(n, 1)
    idf = np.log(1 + (n - df + 0.5) / (df + 0.5)).astype(np.float32)
    del df
    shards = []
    for si in range(0, len(jobs), parts_per_shard):
        group = [np.load(j[4]) for j in jobs[si:si + parts_per_shard]]
        indptr, off = [np.zeros(1, np.int64)], 0
        for p in group:
            indptr.append(p["indptr"][1:] + off)
            off += int(p["indptr"][-1])
        indptr = np.concatenate(indptr)
        idx = np.concatenate([p["idx"] for p in group])
        cnt = np.concatenate([p["cnt"] for p in group]).astype(np.float32)
        del group
        m_rows = len(indptr) - 1
        rows = np.repeat(np.arange(m_rows, dtype=np.int32), np.diff(indptr))
        dl = np.bincount(rows, weights=cnt, minlength=m_rows).astype(np.float32)
        norm = (K1 * (1 - B + B * dl / avg)).astype(np.float32)
        cnt = idf[idx] * cnt * (K1 + 1) / (cnt + norm[rows])
        del rows
        m = sp.csr_matrix((cnt, idx, indptr), shape=(m_rows, NBUCK)).tocsc()
        del cnt, idx
        name = f"shard-{len(shards):03d}.npz"
        sp.save_npz(work / name, m, compressed=False)
        shards.append({"file": name, "start": int(jobs[si][2]), "rows": m_rows})
        del m
        print("shard", name, flush=True)
    done.write_text(json.dumps({"n": n, "avgdl": avg, "shards": shards}))
    return done


class HashedBM25:
    def __init__(self, manifest: Path):
        meta = json.loads(Path(manifest).read_text())
        self.shards = [(x["start"], sp.load_npz(Path(manifest).parent / x["file"]).tocsc()) for x in meta["shards"]]
        self.n = meta["n"]

    def scores(self, query: str) -> np.ndarray:
        q = np.unique(np.asarray(term_ids(query), dtype=np.int64))
        out = np.zeros(self.n, np.float32)
        if len(q):
            for start, m in self.shards:
                out[start:start + m.shape[0]] = np.asarray(m[:, q].sum(axis=1)).ravel()
        return out

    def search(self, query: str, k: int):
        s = self.scores(query)
        k = min(k, self.n)
        top = np.argpartition(-s, k - 1)[:k]
        top = top[np.argsort(-s[top], kind="stable")]
        return top, s[top]
