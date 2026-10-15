"""Precomputed dense index over every stored chunk (built once, loaded at engine startup).

The query-time dense stage then encodes only the query and scores it against all chunk
vectors (brute-force inner product, float16 storage, float32 math), so it no longer
re-encodes candidate passages under box load.

Where vectors live: Octo ``legal_graph.chunk.embedding`` is ``vector(1024)``. The default
dense model ``BAAI/bge-small-en-v1.5`` is 384-d, so its vectors do **not** fit that column
and are kept in a local cache instead::

    $ENGINE_CACHE_DIR/dense-<model-slug>.f16.npy   (n_chunks x dim, float16, L2-normalised)
    $ENGINE_CACHE_DIR/dense-<model-slug>.json      ({model, dim, n, chunk_ids_sha256, storage, ...})

Rows are aligned with ``Corpus.chunks`` (``chunks.jsonl`` order = chunk_id order). The engine
always reads the local cache; ``storage`` in the manifest records whether the vectors would
fit Octo. Build (resumable, checkpointed every block, streams ``chunks.jsonl``)::

    python -m legal_graph.engine.dense_index --build
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("legal_graph.engine")
OCTO_EMBED_DIM = 1024
BLOCK = 4096


def slug(model: str) -> str:
    return model.replace("/", "__")


def ids_sha(chunk_ids: Iterable[str]) -> str:
    h = hashlib.sha256()
    for c in chunk_ids:
        h.update(c.encode())
        h.update(b"\n")
    return h.hexdigest()


def paths(cache_dir: Path, model: str) -> tuple[Path, Path]:
    return cache_dir / f"dense-{slug(model)}.f16.npy", cache_dir / f"dense-{slug(model)}.json"


def storage_for(dim: int) -> tuple[str, str]:
    if dim == OCTO_EMBED_DIM:
        return "octo+local-cache", f"{dim}-d vectors fit Octo chunk.embedding vector({OCTO_EMBED_DIM})"
    return "local-cache", f"{dim}-d model does not fit Octo chunk.embedding vector({OCTO_EMBED_DIM})"


class DenseIndex:
    def __init__(self, vectors: np.ndarray, encode_query, meta: dict[str, Any]):
        self.vectors, self.encode_query, self.meta = vectors, encode_query, meta

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        q = np.asarray(self.encode_query(query), dtype=np.float32)
        n = self.vectors.shape[0]
        best_i, best_s = [], []
        step = 131072
        for a in range(0, n, step):
            s = self.vectors[a:a + step].astype(np.float32) @ q
            kk = min(k, s.shape[0])
            top = np.argpartition(-s, kk - 1)[:kk]
            best_i.append(top + a)
            best_s.append(s[top])
        bi, bs = np.concatenate(best_i), np.concatenate(best_s)
        order = np.lexsort((bi, -bs))[:k]
        return [(int(bi[j]), float(bs[j])) for j in order]

    def info(self) -> dict[str, Any]:
        return {"index_chunks": int(self.vectors.shape[0]), "dim": int(self.vectors.shape[1]),
                "storage": self.meta.get("storage", "local-cache")}


def load(cache_dir: Path, model: str, chunk_ids: Sequence[str], encode_query) -> DenseIndex | None:
    vp, mp = paths(cache_dir, model)
    if not (vp.exists() and mp.exists()):
        log.warning("no dense index at %s; dense stage uses pool mode", vp)
        return None
    meta = json.loads(mp.read_text())
    if not meta.get("complete") or meta.get("n") != len(chunk_ids) or meta.get("chunk_ids_sha256") != ids_sha(chunk_ids):
        log.warning("dense index %s is stale or incomplete (n=%s, corpus=%d); using pool mode", vp.name,
                    meta.get("n"), len(chunk_ids))
        return None
    vecs = np.load(vp, mmap_mode="r")
    if os.environ.get("ENGINE_DENSE_IN_RAM", "1") == "1":
        vecs = np.ascontiguousarray(vecs)
    return DenseIndex(vecs, encode_query, meta)


def build(texts: Iterable[str], chunk_ids: Sequence[str], model_name: str, cache_dir: Path,
          max_len: int = 256, batch: int = 64) -> dict[str, Any]:
    """Embed ``texts`` (an iterable aligned with ``chunk_ids``) into the cache; resumes from the manifest."""
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(model_name, device="cpu")
    m.max_seq_length = max_len
    dim = int(m.get_sentence_embedding_dimension())
    n = len(chunk_ids)
    vp, mp = paths(cache_dir, model_name)
    sha = ids_sha(chunk_ids)
    meta = json.loads(mp.read_text()) if mp.exists() else {}
    if meta.get("chunk_ids_sha256") != sha or meta.get("dim") != dim or not vp.exists():
        storage, reason = storage_for(dim)
        meta = {"model": model_name, "dim": dim, "n": n, "chunk_ids_sha256": sha, "done": 0, "complete": False,
                "max_len": max_len, "normalized": True, "dtype": "float16", "storage": storage,
                "storage_reason": reason}
        np.lib.format.open_memmap(vp, mode="w+", dtype=np.float16, shape=(n, dim)).flush()
    out = np.load(vp, mmap_mode="r+")
    it: Iterator[str] = iter(texts)
    for _ in range(meta["done"]):
        next(it)
    t0 = time.time()
    a = meta["done"]
    while a < n:
        block = [next(it) for _ in range(min(BLOCK, n - a))]
        b = a + len(block)
        out[a:b] = m.encode(block, batch_size=batch, normalize_embeddings=True).astype(np.float16)
        out.flush()
        meta["done"] = b
        mp.write_text(json.dumps(meta))
        rate = len(block) / max(time.time() - t0, 1e-6)
        t0 = time.time()
        print(json.dumps({"done": b, "n": n, "rate": round(rate)}), flush=True)
        a = b
    meta["complete"] = True
    mp.write_text(json.dumps(meta))
    return meta


def main(argv=None) -> int:
    from .octo_source import DENSE_DEFAULT, load_corpus, octo_executor
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", required=True)
    ap.add_argument("--model", default=os.environ.get("ENGINE_DENSE_MODEL", DENSE_DEFAULT))
    ap.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("ENGINE_CACHE_DIR",
                                                                          "/workspace/lgr-text-data/engine-cache")))
    ap.add_argument("--max-len", type=int, default=int(os.environ.get("ENGINE_DENSE_MAX_LEN", "256")))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    load_corpus(octo_executor(), a.cache_dir, refresh_only=True)  # make sure chunks.jsonl matches Octo
    path = a.cache_dir / "chunks.jsonl"
    ids = [json.loads(line)["chunk_id"] for line in path.open()]
    meta = build((json.loads(line)["body"] for line in path.open()), ids, a.model, a.cache_dir, a.max_len)
    print(json.dumps({k: v for k, v in meta.items() if k != "chunk_ids_sha256"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
