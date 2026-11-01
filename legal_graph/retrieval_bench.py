"""Pluggable retrieval benchmark harness (frozen inputs, write-once runs).

Generalises ``legal_graph.benchmark`` from "seed list vs graph" to any set of
named *systems* scored on the same frozen queries:

  seeds   ranked = the query's ``seeds`` (citations already in the query text)
  bm25    in-repo BM25 (numpy/scipy only) over a frozen JSONL corpus
  graph   take another system's top ``n_seeds`` ids, expand with
          ``graphrag_retrieve`` on the held-out citation graph, then
          ``append`` (top n_seeds, then graph results, then the rest of the
          first-stage list; with ``seeds`` this is the PR #25 convention) or
          ``rrf`` (reciprocal-rank fusion of first-stage and graph lists)
  fuse    reciprocal-rank fusion of earlier systems (``"from": [a, b]``)
  plugin  ``"callable": "pkg.module:fn"``; ``fn(queries, corpus, params)``
          returns ``{query_id: [doc ids]}``. This is the hook for the
          dense / hybrid / reranker retrievers built elsewhere.

Inputs are sha256-frozen in the config (``seal``). A run writes
``<runs>/<name>-<id>/results.json`` + ``MANIFEST.sha256`` with
exclusive-create, chmod 0444. Query fields: ``query_id``, ``text``,
``gold`` (ids), optional ``seeds``, ``date`` (ISO; corpus docs and graph
results dated after it are dropped), ``exclude`` (ids never returned, e.g.
the citing opinion itself), ``held_out_opinion`` / ``holdout_edges``
(citation edges removed before the graph index is built).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .benchmark import _canon, _mean, _sha, mrr, ndcg_at, recall_at

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"[a-z0-9]+(?:[.\-§][a-z0-9]+)*")
STOP = frozenset("a an and are as at be by for from has have in is it its of on or that the this to was were which with not no".split())


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP and len(t) < 40]


def _read_jsonl(path: Path) -> list[dict]:
    import gzip
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- BM25
class BM25:
    """Okapi BM25 over a sparse term-document matrix (scipy)."""

    def __init__(self, docs: list[dict], *, k1: float = 0.9, b: float = 0.4, field: str = "text",
                 title_field: str | None = "title"):
        import numpy as np
        from scipy import sparse
        self.ids = [d["id"] for d in docs]
        self.dates = [d.get("date") or "" for d in docs]
        from array import array
        vocab: dict[str, int] = {}
        rows, cols, vals, lens = array("i"), array("i"), array("f"), []
        for j, d in enumerate(docs):
            text = d.get(field) or ""
            if title_field and d.get(title_field):
                text = f"{d[title_field]} {text}"
            tf = Counter(tokenize(text))
            lens.append(sum(tf.values()))
            for t, c in tf.items():
                rows.append(vocab.setdefault(t, len(vocab)))
                cols.append(j)
                vals.append(c)
        n = len(docs)
        tf = sparse.csr_matrix((np.frombuffer(vals, dtype=np.float32),
                                (np.frombuffer(rows, dtype=np.int32), np.frombuffer(cols, dtype=np.int32))),
                               shape=(len(vocab), n))
        del rows, cols, vals
        dl = np.array(lens, dtype=np.float32)
        avg = float(dl.mean()) if n else 1.0
        df = np.diff(tf.indptr).astype(np.float32)
        self.idf = np.log(1 + (n - df + 0.5) / (df + 0.5)).astype(np.float32)
        tf = tf.tocoo()
        norm = k1 * (1 - b + b * dl[tf.col] / avg)
        w = tf.data * (k1 + 1) / (tf.data + norm)
        self.w = sparse.csr_matrix((w.astype(np.float32), (tf.row, tf.col)), shape=tf.shape)
        self.vocab = vocab

    def score_vector(self, text: str):
        """BM25 score of every corpus doc for ``text`` (aligned with ``self.ids``)."""
        import numpy as np
        terms = [self.vocab[t] for t in set(tokenize(text)) if t in self.vocab]
        if not terms:
            return np.zeros(len(self.ids), dtype=np.float32)
        return np.asarray(self.w[terms].T @ self.idf[terms]).ravel()

    def search(self, text: str, k: int, *, max_date: str | None = None, exclude=()) -> list[str]:
        import numpy as np
        scores = self.score_vector(text)
        if not scores.any():
            return []
        order = np.argsort(-scores, kind="stable")
        out, ex = [], set(exclude)
        for j in order:
            if scores[j] <= 0 or len(out) >= k:
                break
            if self.ids[j] in ex or (max_date and self.dates[j] and self.dates[j] > max_date):
                continue
            out.append(self.ids[j])
        return out


def rrf(lists: list[list[str]], k: int, c: int = 60) -> list[str]:
    score: dict[str, float] = {}
    for lst in lists:
        for r, d in enumerate(lst):
            score[d] = score.get(d, 0.0) + 1.0 / (c + r + 1)
    return [d for d, _ in sorted(score.items(), key=lambda x: (-x[1], x[0]))][:k]


# ---------------------------------------------------------------- run
def _resolve(config_path: Path, rel: str | None) -> Path | None:
    if not rel:
        return None
    p = Path(rel)
    return p if p.is_absolute() else (config_path.parent / p)


def seal(config_path: Path, data_root: Path | None = None) -> dict:
    cfg = json.loads(config_path.read_text())
    frozen = {"queries_sha256": _file_sha(_resolve(config_path, cfg["queries_file"]))}
    for key in ("corpus_file", "nodes_file", "edges_file"):
        if cfg.get(key):
            frozen[key.replace("_file", "_sha256")] = _file_sha(_data(config_path, cfg[key], data_root))
    frozen["n_queries"] = len(_read_jsonl(_resolve(config_path, cfg["queries_file"])))
    cfg["frozen"] = frozen
    config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    return frozen


def _data(config_path: Path, rel: str, data_root: Path | None) -> Path:
    if data_root is not None and not Path(rel).is_absolute():
        return data_root / rel
    return _resolve(config_path, rel)


def run(config_path: Path, runs_dir: Path, data_root: Path | None = None, only_split: str | None = None) -> Path:
    cfg = json.loads(config_path.read_text())
    frozen = cfg.get("frozen") or {}
    qpath = _resolve(config_path, cfg["queries_file"])
    if _file_sha(qpath) != frozen.get("queries_sha256"):
        raise ValueError("frozen queries do not match recorded sha256")
    paths = {}
    for key in ("corpus_file", "nodes_file", "edges_file"):
        if cfg.get(key):
            p = _data(config_path, cfg[key], data_root)
            if _file_sha(p) != frozen.get(key.replace("_file", "_sha256")):
                raise ValueError(f"{key} does not match recorded sha256")
            paths[key] = p
    queries = _read_jsonl(qpath)
    split = only_split or cfg.get("split")
    if split:
        queries = [q for q in queries if q.get("split") == split]
    ks, nk = cfg["k_values"], cfg["ndcg_k"]
    kmax = max(ks + [nk, cfg.get("depth_k", 100)])
    corpus = _read_jsonl(paths["corpus_file"]) if "corpus_file" in paths else []
    dates = {d["id"]: d.get("date") or "" for d in corpus}
    timings: dict[str, float] = {}
    ranked: dict[str, dict[str, list[str]]] = {}
    index = None
    bm25_index: dict[str, Any] = {}
    stage_stats: dict[str, dict] = {}
    for sysdef in cfg["systems"]:
        name, kind = sysdef["name"], sysdef["kind"]
        t0 = time.perf_counter()
        if kind == "seeds":
            ranked[name] = {q["query_id"]: list(q.get("seeds") or [])[:kmax] for q in queries}
        elif kind == "bm25":
            bm = BM25(corpus, k1=sysdef.get("k1", 0.9), b=sysdef.get("b", 0.4),
                      field=sysdef.get("field", "text"), title_field=sysdef.get("title_field", "title"))
            ranked[name] = {q["query_id"]: bm.search(q["text"], kmax, max_date=q.get("date"),
                                                     exclude=q.get("exclude", ())) for q in queries}
            bm25_index[name] = bm
        elif kind == "qgraph":
            if index is None:
                index = _graph_index(paths, queries, cfg)
            from .graph_stage import run_qgraph
            ranked[name] = run_qgraph(sysdef, queries, ranked[sysdef["from"]], index, cfg.get("graph", {}),
                                      bm25_index.get(sysdef.get("text_scores_from", "")), dates, kmax)
        elif kind == "jev_rerank":
            from .graph_stage import STATS, run_jev_rerank
            ranked[name] = run_jev_rerank(sysdef, queries, ranked[sysdef["from"]], corpus, kmax)
            stage_stats[name] = STATS.get(name, {})
        elif kind == "plugin":
            mod, fn = sysdef["callable"].split(":")
            out = getattr(importlib.import_module(mod), fn)(queries, corpus, sysdef.get("params", {}))
            ranked[name] = {qid: list(v)[:kmax] for qid, v in out.items()}
        elif kind == "fuse":
            ranked[name] = {q["query_id"]: rrf([ranked[f].get(q["query_id"], []) for f in sysdef["from"]], kmax)
                            for q in queries}
        elif kind == "graph":
            if index is None:
                index = _graph_index(paths, queries, cfg)
            from .retrieval import graphrag_retrieve
            g = cfg.get("graph", {})
            res = {}
            for q in queries:
                first = ranked[sysdef["from"]].get(q["query_id"], [])
                seeds = [s for s in first[: sysdef.get("n_seeds", 10)] if s in index.nodes]
                ex = set(q.get("exclude", ())) | set(first)
                graph_ids: list[str] = []
                if seeds:
                    out = graphrag_retrieve(seeds, min(500, kmax), index=index,
                                            **{k: g[k] for k in ("depth", "direction", "edge_kinds") if k in g})
                    for r in out["results"]:
                        d = r["id"]
                        if d in ex or (corpus and d not in dates) or (q.get("date") and dates.get(d, "") > q["date"]):
                            continue
                        graph_ids.append(d)
                if sysdef.get("fuse", "append") == "rrf":
                    res[q["query_id"]] = rrf([first, graph_ids], kmax)
                else:
                    n = sysdef.get("n_seeds", 10)
                    merged = list(dict.fromkeys(first[:n] + graph_ids + first[n:]))
                    res[q["query_id"]] = merged[:kmax]
            ranked[name] = res
        else:
            raise ValueError(f"unknown system kind {kind}")
        timings[name] = time.perf_counter() - t0
    per_query, summary = [], {}
    for q in queries:
        gold = set(q["gold"])
        row = {"query_id": q["query_id"], "n_gold": len(gold), "n_seeds": len(q.get("seeds") or [])}
        for name in ranked:
            lst = ranked[name].get(q["query_id"], [])
            row[name] = {**{f"recall@{k}": recall_at(lst, gold, k) for k in ks},
                         f"ndcg@{nk}": ndcg_at(lst, gold, nk), "mrr": mrr(lst, gold)}
        per_query.append(row)
    for name in ranked:
        summary[name] = {m: _mean([r[name][m] for r in per_query]) for m in per_query[0][name]} if per_query else {}
    summary["n_queries"] = len(per_query)
    summary["system_seconds"] = timings
    if stage_stats:
        summary["stage_stats"] = stage_stats
    import platform
    record = {"label": cfg.get("label", ""), "config": cfg, "split": split,
              "environment": {"python": platform.python_version(), "platform": platform.platform()},
              "summary": summary, "per_query": per_query}
    run_id = _sha(_canon({"config": cfg, "split": split}))[:16]
    target = runs_dir / f"{cfg['name']}{'-' + split if split else ''}-{run_id}"
    target.mkdir(parents=True, exist_ok=False)
    body = json.dumps(record, indent=2, ensure_ascii=False).encode()
    with open(target / "results.json", "xb") as f:
        f.write(body)
    with open(target / "MANIFEST.sha256", "x") as f:
        f.write(f"{_sha(body)}  results.json\n")
    for p in (target / "results.json", target / "MANIFEST.sha256"):
        p.chmod(0o444)
    return target


def _graph_index(paths, queries, cfg):
    from .retrieval import CitationGraphIndex
    nodes = _read_jsonl(paths["nodes_file"])
    # starter/PR #25 exports use src/dst; their "source" key is provenance text
    edges = [{"source": e["src"] if "src" in e else e["source"],
              "target": e["dst"] if "dst" in e else e["target"], "kind": e["kind"]}
             for e in _read_jsonl(paths["edges_file"])]
    held = {q["held_out_opinion"] for q in queries if q.get("held_out_opinion")}
    hold = {tuple(e) for q in queries for e in q.get("holdout_edges", [])}
    kept = [e for e in edges if not (e["kind"] == "cites" and e["source"] in held)
            and (e["source"], e["target"], e["kind"]) not in hold]
    return CitationGraphIndex(nodes, kept, dataset_revision="sha256:" + _sha(_canon(cfg.get("frozen", {}))),
                              source_dataset=cfg["name"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="pluggable frozen retrieval benchmark")
    ap.add_argument("command", choices=["seal", "run"])
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--data-root", type=Path, default=None, help="base dir for corpus/nodes/edges files")
    ap.add_argument("--runs-dir", type=Path, default=ROOT / "benchmarks/runs")
    ap.add_argument("--split", default=None)
    a = ap.parse_args(argv)
    if a.command == "seal":
        print(json.dumps(seal(a.config, a.data_root), indent=2))
    else:
        p = run(a.config, a.runs_dir, a.data_root, a.split)
        print(p)
        print(json.dumps(json.loads((p / "results.json").read_text())["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
