"""BM25 (bm25s) + dense (sentence-transformers) retrieval fused with RRF, plus a cross-encoder."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

RRF_K = 60


def rrf(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion (Cormack et al. 2009): score = sum 1/(k + rank)."""
    score: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for r, d in enumerate(ranking):
            score[d] += 1.0 / (k + r + 1)
    return sorted(score, key=lambda d: (-score[d], d))


def chunks_to_docs(chunk_ids: Sequence[int], scores: Sequence[float], chunk_doc: Sequence[str], k: int) -> list[str]:
    """Max-pool chunk hits to documents, keeping the best rank per doc."""
    seen, out = set(), []
    for c in chunk_ids:
        d = chunk_doc[c]
        if d not in seen:
            seen.add(d)
            out.append(d)
            if len(out) >= k:
                break
    return out


class BM25Index:
    def __init__(self, texts: Sequence[str]):
        import bm25s
        import Stemmer
        self._bm25s = bm25s
        self.stemmer = Stemmer.Stemmer("english")
        self.model = bm25s.BM25()
        self.model.index(bm25s.tokenize(list(texts), stopwords="en", stemmer=self.stemmer, show_progress=False),
                         show_progress=False)
        self.n = len(texts)

    def search(self, queries: Sequence[str], k: int):
        toks = self._bm25s.tokenize(list(queries), stopwords="en", stemmer=self.stemmer, show_progress=False)
        ids, scores = self.model.retrieve(toks, k=min(k, self.n), show_progress=False, n_threads=4)
        return ids, scores


QUERY_PROMPTS = {
    "Qwen/Qwen3-Embedding-0.6B": "Instruct: Given a legal passage with a citation removed, retrieve the cited court opinion\nQuery: ",
    "freelawproject/modernbert-embed-base_finetune_512": "search_query: ",
}
DOC_PROMPTS = {"freelawproject/modernbert-embed-base_finetune_512": "search_document: "}


class DenseIndex:
    def __init__(self, model_name: str, texts: Sequence[str], *, max_len: int = 512, batch_size: int = 16):
        from sentence_transformers import SentenceTransformer
        self.name = model_name
        self.model = SentenceTransformer(model_name, device="cpu", trust_remote_code=True)
        self.model.max_seq_length = max_len
        pre = DOC_PROMPTS.get(model_name, "")
        self.emb = self.model.encode([pre + t for t in texts], batch_size=batch_size, normalize_embeddings=True,
                                     convert_to_numpy=True, show_progress_bar=False)

    def search(self, queries: Sequence[str], k: int):
        import numpy as np
        pre = QUERY_PROMPTS.get(self.name, "")
        q = self.model.encode([pre + t for t in queries], batch_size=8, normalize_embeddings=True,
                              convert_to_numpy=True, show_progress_bar=False)
        s = q @ self.emb.T
        k = min(k, s.shape[1])
        idx = np.argpartition(-s, k - 1, axis=1)[:, :k]
        order = np.take_along_axis(s, idx, 1).argsort(axis=1)[:, ::-1]
        idx = np.take_along_axis(idx, order, 1)
        return idx, np.take_along_axis(s, idx, 1)


class Reranker:
    def __init__(self, model_name: str, max_len: int = 512):
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(model_name, device="cpu", max_length=max_len)

    def rerank(self, query: str, cand_docs: Sequence[str], doc_text: dict[str, str]) -> list[str]:
        scores = self.model.predict([(query, doc_text[d]) for d in cand_docs], batch_size=16, show_progress_bar=False)
        return [d for _, d in sorted(zip(scores, cand_docs), key=lambda x: -x[0])]
