"""Plugin: load rankings produced elsewhere (e.g. PR #42's CLERC BM25 over opinion chunks).

params: {"path": "bm25-direct.jsonl"}; each line {"i": n, "docs": [...]} where n is the
0-based position in the frozen queries file (PR #42 convention), or {"query_id": ..., "docs": [...]}.
"""
import json


def load(queries, corpus, params):
    out = {}
    for line in open(params["path"]):
        r = json.loads(line)
        qid = r.get("query_id") or queries[r["i"]]["query_id"]
        out[qid] = r["docs"]
    return {q["query_id"]: out.get(q["query_id"], []) for q in queries}
