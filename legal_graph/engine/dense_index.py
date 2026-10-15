"""Precomputed dense index over every stored chunk (built once, loaded at engine startup).

The query-time dense stage encodes only the query and scores it against all chunk vectors
(brute-force inner product, float16 in RAM, float32 math). It never re-encodes passages, so it
does not depend on box load.

Two encoder backends (``ENGINE_DENSE_INDEX_BACKEND``):

* ``local`` - sentence-transformers on CPU, default model ``BAAI/bge-small-en-v1.5`` (384-d).
* ``openrouter`` - the OpenRouter embeddings API, default ``baai/bge-large-en-v1.5`` (1024-d).
  Used on the shared box because local CPU embedding of 800k chunks ran at about 10-15
  chunks/s under other agents' load (15+ hours); the API run takes minutes and about USD 2.
  The query is embedded by the same API at request time (one call, about 0.3-1 s).

Where vectors live. Octo ``legal_graph.chunk.embedding`` is ``vector(1024)``:

* 1024-d models (bge-large): vectors are written to ``chunk.embedding`` with
  ``embed_model = '<model>'`` (``--write-octo``) **and** kept in the local cache the engine loads.
* other dims (bge-small 384-d): they do not fit the column; local cache only.

Local cache::

    $ENGINE_CACHE_DIR/dense-<model-slug>.f16.npy   (n_chunks x dim, float16, L2-normalised)
    $ENGINE_CACHE_DIR/dense-<model-slug>.json      ({model, backend, dim, n, chunk_ids_sha256, storage, ...})

Rows are aligned with ``Corpus.chunks`` (``chunks.jsonl`` order = chunk_id order). Build
(resumable, checkpointed per block, streams ``chunks.jsonl``)::

    python -m legal_graph.engine.dense_index --build [--write-octo]
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


BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
OPENROUTER_EMBED_URL = "https://openrouter.ai/api/v1/embeddings"
OPENROUTER_DEFAULT = "baai/bge-large-en-v1.5"


def index_config() -> tuple[str, str]:
    """(backend, model) for the precomputed index from the environment."""
    backend = os.environ.get("ENGINE_DENSE_INDEX_BACKEND", "local")
    default = OPENROUTER_DEFAULT if backend == "openrouter" else os.environ.get("ENGINE_DENSE_MODEL",
                                                                                "BAAI/bge-small-en-v1.5")
    return backend, os.environ.get("ENGINE_DENSE_INDEX_MODEL", default)


def query_prefix(model: str) -> str:
    return BGE_QUERY_PREFIX if "bge" in model.lower() and "m3" not in model.lower() else ""


class EmbeddingBadRequest(RuntimeError):
    pass


def robust_encode(encode, texts: Sequence[str], dim: int, stats: dict[str, int]) -> np.ndarray:
    """Encode a batch; on HTTP 400 bisect, then truncate a single bad text, then give it a zero vector."""
    try:
        return encode(texts)
    except EmbeddingBadRequest:
        if len(texts) > 1:
            h = len(texts) // 2
            return np.concatenate([robust_encode(encode, texts[:h], dim, stats),
                                   robust_encode(encode, texts[h:], dim, stats)])
        t = texts[0]
        for cut in (1500, 600):
            try:
                v = encode([t[:cut] if t.strip() else "(empty)"])
                stats["truncated"] = stats.get("truncated", 0) + 1
                return v
            except EmbeddingBadRequest:
                continue
        stats["zero"] = stats.get("zero", 0) + 1
        return np.zeros((1, dim), dtype=np.float32)


class OpenRouterEncoder:
    """Batch text -> L2-normalised float32 vectors via OpenRouter embeddings. Key from env, never logged."""

    def __init__(self, model: str, timeout: float = 60.0, retries: int = 6):
        self.model, self.timeout, self.retries = model, timeout, retries
        self.cost, self.tokens = 0.0, 0
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RuntimeError("OPENROUTER_API_KEY not set")

    def __call__(self, texts: Sequence[str]) -> np.ndarray:
        import urllib.error
        import urllib.request
        body = json.dumps({"model": self.model, "input": list(texts)}, ensure_ascii=True).encode()
        delay = 2.0
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(OPENROUTER_EMBED_URL, data=body, method="POST", headers={
                "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "Content-Type": "application/json",
                "X-Title": "legal-graphrag-starter"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    d = json.loads(r.read())
                data = sorted(d["data"], key=lambda x: x["index"])
                if len(data) != len(texts):
                    raise ValueError("embedding count mismatch")
                u = d.get("usage") or {}
                self.cost += float(u.get("cost") or 0)
                self.tokens += int(u.get("total_tokens") or 0)
                v = np.asarray([x["embedding"] for x in data], dtype=np.float32)
                return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError, KeyError) as exc:
                code = getattr(exc, "code", 0)
                if code == 400:
                    detail = ""
                    try:
                        detail = exc.read()[:200].decode("utf-8", "replace")  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        pass
                    raise EmbeddingBadRequest(detail) from None
                if attempt >= self.retries or (code and code < 500 and code not in (408, 429)):
                    raise RuntimeError(f"embedding request failed: {type(exc).__name__} {code or ''}".strip()) from None
                time.sleep(delay)
                delay = min(delay * 2, 60)
        raise RuntimeError("unreachable")


def make_encoder(backend: str, model: str, max_len: int = 256):
    """Return encode(texts) -> normalised vectors, and the embedding dimension."""
    if backend == "openrouter":
        enc = OpenRouterEncoder(model)
        return enc, int(enc(["dimension probe"]).shape[1])
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(model, device="cpu")
    m.max_seq_length = max_len
    return (lambda texts: m.encode(list(texts), batch_size=64, normalize_embeddings=True)), \
        int(m.get_sentence_embedding_dimension())


def storage_for(dim: int) -> tuple[str, str]:
    if dim == OCTO_EMBED_DIM:
        return "octo+local-cache", f"{dim}-d vectors fit Octo chunk.embedding vector({OCTO_EMBED_DIM})"
    return "local-cache", f"{dim}-d model does not fit Octo chunk.embedding vector({OCTO_EMBED_DIM})"


class DenseIndex:
    """Exact inner-product search. Uses a faiss fp16 scalar-quantizer flat index when faiss is
    installed (SIMD, multithreaded; ~0.3 s for 804k x 1024 on the loaded box vs ~3 s in numpy)."""

    def __init__(self, vectors: np.ndarray, encode_query, meta: dict[str, Any]):
        self.vectors, self.encode_query, self.meta = vectors, encode_query, meta
        self.faiss = None
        self.n, self.dim = int(vectors.shape[0]), int(vectors.shape[1])
        if os.environ.get("ENGINE_DENSE_FAISS", "1") == "1":
            try:
                import faiss
            except ImportError:
                faiss = None
            if faiss is not None:
                idx = faiss.IndexScalarQuantizer(self.dim, faiss.ScalarQuantizer.QT_fp16, faiss.METRIC_INNER_PRODUCT)
                for a in range(0, self.n, 65536):
                    idx.add(np.ascontiguousarray(vectors[a:a + 65536], dtype=np.float32))
                self.faiss, self.vectors = idx, None  # the fp16 codes live in faiss; drop the numpy copy

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        q = np.asarray(self.encode_query(query), dtype=np.float32)
        if self.faiss is not None:
            scores, ids = self.faiss.search(q.reshape(1, -1), k)
            return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i >= 0]
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
        return {"index_chunks": self.n, "dim": self.dim, "storage": self.meta.get("storage", "local-cache"),
                "search": "faiss-sq-fp16" if self.faiss is not None else "numpy"}


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
    if os.environ.get("ENGINE_DENSE_IN_RAM", "1") == "1" and os.environ.get("ENGINE_DENSE_FAISS", "1") != "1":
        vecs = np.ascontiguousarray(vecs)
    return DenseIndex(vecs, encode_query, meta)


def build(texts: Iterable[str], chunk_ids: Sequence[str], model_name: str, cache_dir: Path,
          max_len: int = 256, backend: str = "local", workers: int = 8, batch: int = 128) -> dict[str, Any]:
    """Embed ``texts`` (an iterable aligned with ``chunk_ids``) into the cache; resumes from the manifest."""
    from concurrent.futures import ThreadPoolExecutor
    encode, dim = make_encoder(backend, model_name, max_len)
    n = len(chunk_ids)
    vp, mp = paths(cache_dir, model_name)
    sha = ids_sha(chunk_ids)
    meta = json.loads(mp.read_text()) if mp.exists() else {}
    if meta.get("chunk_ids_sha256") != sha or meta.get("dim") != dim or not vp.exists():
        storage, reason = storage_for(dim)
        meta = {"model": model_name, "backend": backend, "dim": dim, "n": n, "chunk_ids_sha256": sha, "done": 0,
                "complete": False, "max_len": max_len if backend == "local" else None, "normalized": True,
                "dtype": "float16", "storage": storage, "storage_reason": reason, "octo_written": 0}
        np.lib.format.open_memmap(vp, mode="w+", dtype=np.float16, shape=(n, dim)).flush()
    out = np.load(vp, mmap_mode="r+")
    it: Iterator[str] = iter(texts)
    for _ in range(meta["done"]):
        next(it)
    pool = ThreadPoolExecutor(workers if backend == "openrouter" else 1)
    step = batch * workers * 4 if backend == "openrouter" else BLOCK
    t0 = time.time()
    a = meta["done"]
    while a < n:
        block = [next(it) for _ in range(min(step, n - a))]
        b = a + len(block)
        if backend == "openrouter":
            bad = meta.setdefault("bad_texts", {})
            parts = list(pool.map(lambda x: robust_encode(encode, x, dim, bad),
                                  [block[i:i + batch] for i in range(0, len(block), batch)]))
            vecs = np.concatenate(parts)
        else:
            vecs = encode(block)
        out[a:b] = vecs.astype(np.float16)
        out.flush()
        meta["done"] = b
        if backend == "openrouter":
            meta["api_cost_usd"], meta["api_tokens"] = round(encode.cost, 4), encode.tokens
        mp.write_text(json.dumps(meta))
        rate = len(block) / max(time.time() - t0, 1e-6)
        t0 = time.time()
        print(json.dumps({"done": b, "n": n, "rate": round(rate), **({"cost": meta.get("api_cost_usd")}
                                                                      if backend == "openrouter" else {})}), flush=True)
        a = b
    meta["complete"] = True
    mp.write_text(json.dumps(meta))
    return meta


def write_octo(sql, cache_dir: Path, model: str, chunk_ids: Sequence[str], batch: int = 100, workers: int = 4) -> int:
    """Copy cached vectors into Octo ``chunk.embedding`` (only when dim == 1024). Resumable via the manifest."""
    from concurrent.futures import ThreadPoolExecutor
    vp, mp = paths(cache_dir, model)
    meta = json.loads(mp.read_text())
    if meta["dim"] != OCTO_EMBED_DIM:
        raise ValueError(f"{meta['dim']}-d vectors do not fit chunk.embedding vector({OCTO_EMBED_DIM})")
    vecs = np.load(vp, mmap_mode="r")
    q = ("UPDATE legal_graph.chunk c SET embedding = x.v::vector, embed_model = $2 "
         "FROM jsonb_to_recordset($1::jsonb) AS x(i text, v text) WHERE c.chunk_id = x.i")

    def send(rng: tuple[int, int]) -> None:
        rows = [{"i": chunk_ids[j], "v": "[" + ",".join(f"{x:.5f}" for x in vecs[j].astype(np.float32)) + "]"}
                for j in range(*rng)]
        sql(q, [json.dumps(rows, ensure_ascii=True), model], 1)
    with ThreadPoolExecutor(workers) as ex:
        start = int(meta.get("octo_written", 0))
        window = batch * workers * 10
        for a in range(start, len(chunk_ids), window):
            b = min(a + window, len(chunk_ids))
            list(ex.map(send, [(i, min(i + batch, b)) for i in range(a, b, batch)]))
            meta["octo_written"] = b
            mp.write_text(json.dumps(meta))
            print(json.dumps({"octo_written": b, "n": len(chunk_ids)}), flush=True)
    return int(meta["octo_written"])


def main(argv=None) -> int:
    from .octo_source import load_corpus, octo_executor
    backend, model = index_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--write-octo", action="store_true", help="also store 1024-d vectors in chunk.embedding")
    ap.add_argument("--backend", default=backend, choices=["local", "openrouter"])
    ap.add_argument("--model", default=model)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("ENGINE_CACHE_DIR",
                                                                          "/workspace/lgr-text-data/engine-cache")))
    ap.add_argument("--max-len", type=int, default=int(os.environ.get("ENGINE_DENSE_MAX_LEN", "256")))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    sql = octo_executor()
    load_corpus(sql, a.cache_dir, refresh_only=True)  # make sure chunks.jsonl matches Octo
    path = a.cache_dir / "chunks.jsonl"
    ids = [json.loads(line)["chunk_id"] for line in path.open()]
    if a.build:
        meta = build((json.loads(line)["body"] for line in path.open()), ids, a.model, a.cache_dir, a.max_len,
                     backend=a.backend, workers=a.workers)
        print(json.dumps({k: v for k, v in meta.items() if k != "chunk_ids_sha256"}))
    if a.write_octo:
        print(json.dumps({"octo_written": write_octo(sql, a.cache_dir, a.model, ids)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
