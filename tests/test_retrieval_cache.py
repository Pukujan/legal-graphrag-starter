import pytest

from legal_graph.retrieval_cache import RetrievalCache, cache_key, cached_retrieve

S1, S2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def test_key_normalizes_embedding_and_includes_snapshot():
    assert cache_key(S1, [1, 2, 3]) == cache_key(S1, [2, 4, 6])
    assert cache_key(S1, [1, 2, 3]) != cache_key(S2, [1, 2, 3])
    assert cache_key(S1, [1, 2, 3], {"k": 5}) != cache_key(S1, [1, 2, 3], {"k": 10})
    with pytest.raises(ValueError):
        cache_key(S1, [0, 0])


def test_ttl_expiry():
    now = [0.0]
    cache = RetrievalCache(ttl_seconds=10, clock=lambda: now[0])
    cache.put(S1, [1, 0], [{"id": "a"}])
    assert cache.get(S1, [1, 0]) == [{"id": "a"}]
    now[0] = 11
    assert cache.get(S1, [1, 0]) is None


def test_snapshot_change_invalidates():
    cache = RetrievalCache()
    cache.set_active_snapshot(S1)
    cache.put(S1, [1, 0], [{"id": "a"}])
    assert cache.set_active_snapshot(S2) == 1
    assert cache.get(S1, [1, 0]) is None
    cache.put(S1, [1, 0], [{"id": "stale"}])  # stale writes are ignored
    assert cache.get(S2, [1, 0]) is None


def test_hits_are_still_reranked_and_checked():
    cache, calls = RetrievalCache(), {"retrieve": 0, "rerank": 0, "check": 0}

    def retrieve():
        calls["retrieve"] += 1
        return [{"id": "a", "score": 0.1}, {"id": "b", "score": 0.9}]

    def rerank(c):
        calls["rerank"] += 1
        return sorted(c, key=lambda x: -x["score"])

    def check(c):
        calls["check"] += 1
        return [x for x in c if x["id"] != "a"]

    first = cached_retrieve(cache, S1, [0.3, 0.4], retrieve, rerank, check)
    second = cached_retrieve(cache, S1, [0.6, 0.8], retrieve, rerank, check)
    assert (first["cache_hit"], second["cache_hit"]) == (False, True)
    assert first["results"] == second["results"] == [{"id": "b", "score": 0.9}]
    assert calls == {"retrieve": 1, "rerank": 2, "check": 2}
    third = cached_retrieve(cache, S2, [0.6, 0.8], retrieve, rerank, check)
    assert third["cache_hit"] is False and calls["retrieve"] == 2
