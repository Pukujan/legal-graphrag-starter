"""Snapshot-keyed retrieval cache (results of retrieval, never final answers).

Backed by stdlib ``sqlite3``: one process, a few thousand entries, no network
service needed, so Redis/RedisVL would be overkill here. The key is
``(snapshot_id, sha256(quantized L2-normalized query embedding), params)``.
Entries expire by TTL and every entry for an older snapshot is dropped when a
different snapshot becomes active. A hit returns candidates only: callers must
still rerank and run the citation/support checks (see ``cached_retrieve``).
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from typing import Any, Callable, Sequence


def normalize_embedding(vector: Sequence[float], decimals: int = 4) -> list[float]:
    values = [float(x) for x in vector]
    if not values or any(not math.isfinite(x) for x in values):
        raise ValueError("embedding must be a non-empty finite vector")
    norm = math.sqrt(sum(x * x for x in values))
    if norm == 0:
        raise ValueError("embedding must not be all zeros")
    return [round(x / norm, decimals) + 0.0 for x in values]


def cache_key(snapshot_id: str, embedding: Sequence[float], params: dict[str, Any] | None = None) -> str:
    payload = json.dumps([snapshot_id, normalize_embedding(embedding), params or {}],
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class RetrievalCache:
    def __init__(self, path: str = ":memory:", ttl_seconds: float = 3600.0,
                 clock: Callable[[], float] = time.time):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl, self._clock = ttl_seconds, clock
        self._db = sqlite3.connect(path)
        self._db.execute("CREATE TABLE IF NOT EXISTS retrieval_cache (key TEXT PRIMARY KEY, "
                         "snapshot_id TEXT NOT NULL, created_at REAL NOT NULL, candidates TEXT NOT NULL)")
        self._db.execute("CREATE INDEX IF NOT EXISTS rc_snapshot ON retrieval_cache(snapshot_id)")
        self._snapshot: str | None = None

    def set_active_snapshot(self, snapshot_id: str) -> int:
        """Invalidate every entry not keyed by ``snapshot_id``; returns rows dropped."""
        if snapshot_id == self._snapshot:
            return 0
        self._snapshot = snapshot_id
        cur = self._db.execute("DELETE FROM retrieval_cache WHERE snapshot_id <> ?", (snapshot_id,))
        self._db.commit()
        return cur.rowcount

    def get(self, snapshot_id: str, embedding: Sequence[float], params: dict | None = None) -> list | None:
        if self._snapshot is not None and snapshot_id != self._snapshot:
            return None
        key = cache_key(snapshot_id, embedding, params)
        row = self._db.execute("SELECT created_at, candidates FROM retrieval_cache WHERE key = ?",
                               (key,)).fetchone()
        if row is None:
            return None
        if self._clock() - row[0] > self.ttl:
            self._db.execute("DELETE FROM retrieval_cache WHERE key = ?", (key,))
            self._db.commit()
            return None
        return json.loads(row[1])

    def put(self, snapshot_id: str, embedding: Sequence[float], candidates: list,
            params: dict | None = None) -> None:
        if self._snapshot is not None and snapshot_id != self._snapshot:
            return  # never cache results computed against a stale snapshot
        self._db.execute("INSERT OR REPLACE INTO retrieval_cache VALUES (?, ?, ?, ?)",
                         (cache_key(snapshot_id, embedding, params), snapshot_id, self._clock(),
                          json.dumps(candidates)))
        self._db.commit()


def cached_retrieve(cache: RetrievalCache, snapshot_id: str, embedding: Sequence[float],
                    retrieve: Callable[[], list], rerank: Callable[[list], list],
                    check: Callable[[list], list], params: dict | None = None) -> dict[str, Any]:
    """Return reranked, checked results. Rerank and check run on hits and misses alike."""
    cache.set_active_snapshot(snapshot_id)
    candidates = cache.get(snapshot_id, embedding, params)
    hit = candidates is not None
    if not hit:
        candidates = retrieve()
        cache.put(snapshot_id, embedding, candidates, params)
    return {"cache_hit": hit, "snapshot_id": snapshot_id, "results": check(rerank(candidates))}
