"""Text retrieval over whatever text a node has: chunking, BM25, dense, RRF, rerank.

Heavy dependencies (bm25s, sentence-transformers, torch, chonkie) are optional
and listed in ``requirements-search.txt``; they are imported lazily.
"""
