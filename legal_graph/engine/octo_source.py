"""Live data access: Octo SQL (key held server-side) + local model wiring.

Env: OCTO_BASE_URL, OCTO_WORKSPACE_ID, OCTO_API_KEY (or legacy octo_db_legal),
OCTO_SNAPSHOT_ID (default = legal_graph.active_snapshot), ENGINE_CACHE_DIR,
ENGINE_DENSE_MODEL, ENGINE_RERANK_MODEL, OPENROUTER_API_KEY, JEV_MODEL.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Sequence

from legal_graph.octo_client import DEFAULT_BASE_URL, OctoSqlExecutor

from .corpus import Bm25sScorer, Chunk, CompactBM25, Corpus, PurePythonBM25
from .diskindex import build_index, index_is_current, open_corpus
from .pipeline import Pipeline

log = logging.getLogger("legal_graph.engine")
DEFAULT_WORKSPACE = "76d7577b-2274-42b4-a8dc-1ff1c0efa741"
DENSE_DEFAULT = "BAAI/bge-small-en-v1.5"
RERANK_DEFAULT = "cross-encoder/ms-marco-MiniLM-L-6-v2"
PAGE = 2000


def octo_executor() -> OctoSqlExecutor:
    key_env = "OCTO_API_KEY" if os.environ.get("OCTO_API_KEY") else "octo_db_legal"
    return OctoSqlExecutor(os.environ.get("OCTO_WORKSPACE_ID", DEFAULT_WORKSPACE), key_env=key_env,
                           base_url=os.environ.get("OCTO_BASE_URL", DEFAULT_BASE_URL))


def active_snapshot(sql) -> str:
    if os.environ.get("OCTO_SNAPSHOT_ID"):
        return os.environ["OCTO_SNAPSHOT_ID"]
    return sql("SELECT snapshot_id FROM legal_graph.active_snapshot", [], 1)["rows"][0]["snapshot_id"]


def load_corpus(sql, cache_dir: Path) -> Corpus:
    """Page every chunk (+ case metadata) out of Octo once; cache as JSONL for restarts."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "chunks.jsonl"
    count = int(sql("SELECT count(*) AS n FROM legal_graph.chunk", [], 1)["rows"][0]["n"])
    limit_env = os.environ.get("ENGINE_CHUNK_LIMIT", "").strip()
    if limit_env:
        count = min(count, int(limit_env))
        path = cache_dir / f"chunks.limit{count}.jsonl"
    rows: list[dict[str, Any]] = []
    if path.exists():
        rows = [json.loads(line) for line in path.open()]
    if len(rows) != count:
        rows, last = [], ""
        while len(rows) < count:
            page = sql("SELECT c.chunk_id, e.work_id, c.ord, c.body FROM legal_graph.chunk c "
                       "JOIN legal_graph.expression e ON e.expr_id = c.expr_id WHERE c.chunk_id > $1 "
                       "ORDER BY c.chunk_id LIMIT " + str(PAGE), [last], PAGE)["rows"]
            if not page:
                break
            rows.extend(page)
            last = page[-1]["chunk_id"]
            log.info("loaded %d/%d chunks", len(rows), count)
        if len(rows) > count:
            rows = rows[:count]
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=True) + "\n")
        tmp.replace(path)
    docs: dict[str, dict[str, Any]] = {}
    for r in sql("SELECT work_id, court_id, decided_on, case_name, ext_ids FROM legal_graph.work", [], 100000)["rows"]:
        cap = (r.get("ext_ids") or {}).get("cap_id")
        docs[r["work_id"]] = {"name": r.get("case_name"), "court": r.get("court_id"),
                              "year": int(r["decided_on"][:4]) if r.get("decided_on") else None,
                              "cap_id": cap, "link": None}
    chunks = [Chunk(r["chunk_id"], r["work_id"], int(r["ord"]), r["body"]) for r in rows]
    return Corpus(chunks, docs, revision=f"octo-chunk:{count}")


class OctoGraph:
    def __init__(self, sql, snapshot_id: str, limit: int = 5000):
        self.sql, self.snapshot_id, self.limit = sql, snapshot_id, limit

    def neighbours(self, doc_ids: Sequence[str]) -> list[dict[str, str]]:
        ids = list(doc_ids)
        rows = self.sql("SELECT source_id, target_id, payload->>'kind' AS kind FROM legal_graph.citation_edges "
                        "WHERE snapshot_id = $1 AND (source_id = ANY($2::text[]) OR target_id = ANY($2::text[])) "
                        f"LIMIT {self.limit}", [self.snapshot_id, ids], self.limit)["rows"]
        return [{"source": r["source_id"], "target": r["target_id"], "kind": r["kind"] or "cites"} for r in rows]

    def resolve(self, cites: Sequence[str]) -> dict[str, str]:
        out = {}
        for c in cites:
            rows = self.sql("SELECT authority_id FROM legal_graph.authorities WHERE snapshot_id = $1 "
                            "AND payload->'citations' ? $2 LIMIT 1", [self.snapshot_id, c], 1)["rows"]
            if rows:
                out[c] = rows[0]["authority_id"]
        return out

    def authority(self, doc_id: str) -> dict[str, Any] | None:
        rows = self.sql("SELECT payload FROM legal_graph.authorities WHERE snapshot_id = $1 AND authority_id = $2 LIMIT 1",
                        [self.snapshot_id, doc_id], 1)["rows"]
        return rows[0]["payload"] if rows else None


def _has_bm25s() -> bool:
    try:
        import bm25s  # noqa: F401
    except ImportError:
        return False
    return True


def disk_index_enabled() -> bool:
    """The disk-backed index replaces ``CompactBM25``. ``bm25s`` stems differently, so when it is installed
    (and would have been used) the old path stays; ``ENGINE_DISK_INDEX=off`` forces the old path anyway."""
    return os.environ.get("ENGINE_DISK_INDEX", "on").lower() != "off" and not _has_bm25s()


def _jsonl_rows(path: Path):
    with path.open() as f:
        for line in f:
            yield json.loads(line)


def ensure_chunks_file(sql, cache_dir: Path) -> tuple[Path, int]:
    """The JSONL cache of every chunk, paged out of Octo once. Streams to disk: no list of rows is kept."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "chunks.jsonl"
    count = int(sql("SELECT count(*) AS n FROM legal_graph.chunk", [], 1)["rows"][0]["n"])
    limit_env = os.environ.get("ENGINE_CHUNK_LIMIT", "").strip()
    if limit_env:
        count = min(count, int(limit_env))
        path = cache_dir / f"chunks.limit{count}.jsonl"
    if path.exists():
        with path.open("rb") as f:
            have = sum(1 for _ in f)
        if have == count:
            return path, count
    tmp, last, written = path.with_suffix(".tmp"), "", 0
    with tmp.open("w") as f:
        while written < count:
            page = sql("SELECT c.chunk_id, e.work_id, c.ord, c.body FROM legal_graph.chunk c "
                       "JOIN legal_graph.expression e ON e.expr_id = c.expr_id WHERE c.chunk_id > $1 "
                       "ORDER BY c.chunk_id LIMIT " + str(PAGE), [last], PAGE)["rows"]
            if not page:
                break
            for r in page[: count - written]:
                f.write(json.dumps(r, ensure_ascii=True) + "\n")
            written += min(len(page), count - written)
            last = page[-1]["chunk_id"]
            log.info("loaded %d/%d chunks", written, count)
    tmp.replace(path)
    return path, written


def load_docs(sql) -> dict[str, dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = {}
    for r in sql("SELECT work_id, court_id, decided_on, case_name, ext_ids FROM legal_graph.work", [], 100000)["rows"]:
        cap = (r.get("ext_ids") or {}).get("cap_id")
        docs[r["work_id"]] = {"name": r.get("case_name"), "court": r.get("court_id"),
                              "year": int(r["decided_on"][:4]) if r.get("decided_on") else None,
                              "cap_id": cap, "link": None}
    return docs


def load_corpus_disk(sql, cache_dir: Path):
    """The corpus and its BM25 scorer as memory-mapped files. Built once from the chunk cache (a streaming
    pass, a minute or two); every later start only opens the files."""
    path, count = ensure_chunks_file(sql, cache_dir)
    revision = f"octo-chunk:{count}"
    stat = path.stat()
    source = f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"
    index_dir = cache_dir / ("index-" + path.stem)
    if not index_is_current(index_dir, revision, count, source):
        log.info("building the disk index for %d chunks in %s", count, index_dir)
        build_index(_jsonl_rows(path), index_dir, revision, source=source)
    return open_corpus(index_dir, load_docs(sql))


def build_live_pipeline() -> tuple[Pipeline, OctoGraph]:
    sql = octo_executor()
    snapshot = active_snapshot(sql)
    cache_dir = Path(os.environ.get("ENGINE_CACHE_DIR", "/workspace/lgr-text-data/engine-cache"))
    if disk_index_enabled():
        corpus, lexical = load_corpus_disk(sql, cache_dir)
    else:
        corpus = load_corpus(sql, cache_dir)
        texts = [c.body for c in corpus.chunks]
        try:
            lexical = Bm25sScorer(texts)
        except ImportError:
            # Per-chunk Counters of the full opinion corpus do not fit on the demo machine.
            lexical = CompactBM25(texts) if len(texts) > 20_000 else PurePythonBM25(texts)
    graph = OctoGraph(sql, snapshot)
    p = Pipeline(corpus, lexical, resolve_citations=graph.resolve, neighbours=graph.neighbours,
                 graph_revision=snapshot)
    try:
        from sentence_transformers import CrossEncoder, SentenceTransformer
        dname = os.environ.get("ENGINE_DENSE_MODEL", DENSE_DEFAULT)
        dm = SentenceTransformer(dname, device="cpu")
        dm.max_seq_length = int(os.environ.get("ENGINE_DENSE_MAX_LEN", "256"))
        qprefix = "Represent this sentence for searching relevant passages: " if "bge" in dname else ""

        emb_cache: dict[int, Any] = {}  # passage-text hash -> vector; chunks recur across queries

        def dense(query: str, passages: Sequence[str]):
            q = dm.encode([qprefix + query], normalize_embeddings=True)[0]
            missing = [t for t in dict.fromkeys(passages) if hash(t) not in emb_cache]
            if missing:
                for t, v in zip(missing, dm.encode(missing, batch_size=32, normalize_embeddings=True)):
                    emb_cache[hash(t)] = v
                while len(emb_cache) > 200_000:
                    emb_cache.pop(next(iter(emb_cache)))
            return [float(emb_cache[hash(t)] @ q) for t in passages]
        rname = os.environ.get("ENGINE_RERANK_MODEL", RERANK_DEFAULT)
        rm = CrossEncoder(rname, device="cpu", max_length=int(os.environ.get("ENGINE_RERANK_MAX_LEN", "384")))
        p.dense, p.dense_model = dense, dname
        p.reranker, p.rerank_model = (lambda q, ps: rm.predict([(q, x) for x in ps], batch_size=16).tolist()), rname
    except ImportError:
        log.warning("sentence-transformers not installed; dense and rerank stages are skipped")
    if os.environ.get("OPENROUTER_API_KEY"):
        from .checks import JevScorer
        js = JevScorer()
        p.jev, p.jev_model = js.relevance, js.model
    return p, graph
