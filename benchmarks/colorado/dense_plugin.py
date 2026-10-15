"""Dense retriever plugin for legal_graph.retrieval_bench (``kind: plugin``).

config: {"kind": "plugin", "callable": "dense_plugin:retrieve",
         "params": {"model": "BAAI/bge-small-en-v1.5", "max_chars": 2000, "k": 100,
                    "cache": "/path/emb.npy", "query_prefix": "..."}}
Run with PYTHONPATH=benchmarks/colorado. CPU only; document embeddings are cached
(keyed by corpus ids) so reruns are cheap. Documents are truncated to the first
``max_chars`` characters of title + text (512-token model window).
"""
from __future__ import annotations

import hashlib
import os


def retrieve(queries, corpus, params):
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(int(params.get("threads", 4)))
    model = SentenceTransformer(params.get("model", "BAAI/bge-small-en-v1.5"), device="cpu")
    mc = int(params.get("max_chars", 2000))
    ids = [d["id"] for d in corpus]
    key = hashlib.sha256(("\n".join(ids) + params.get("model", "") + str(mc)).encode()).hexdigest()[:12]
    cache = params.get("cache")
    path = f"{cache}.{key}.npy" if cache else None
    if path and os.path.exists(path):
        emb = np.load(path)
    else:
        texts = [((d.get("title") or "") + "\n" + (d.get("text") or ""))[:mc] for d in corpus]
        emb = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False,
                           convert_to_numpy=True).astype("float32")
        if path:
            np.save(path, emb)
    qp = params.get("query_prefix", "Represent this sentence for searching relevant passages: ")
    qe = model.encode([qp + q["text"] for q in queries], batch_size=64, normalize_embeddings=True,
                      convert_to_numpy=True).astype("float32")
    k = int(params.get("k", 100))
    dates = [d.get("date") or "" for d in corpus]
    out = {}
    for i, q in enumerate(queries):
        s = emb @ qe[i]
        order = np.argsort(-s)
        ex, res = set(q.get("exclude", ())), []
        for j in order:
            if ids[j] in ex or (q.get("date") and dates[j] and dates[j] > q["date"]):
                continue
            res.append(ids[j])
            if len(res) >= k:
                break
        out[q["query_id"]] = res
    return out
