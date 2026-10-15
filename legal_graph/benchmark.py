"""Phase 4 retrieval benchmark harness (frozen inputs, immutable outputs).

PROVISIONAL / PROXY ONLY: the bundled gold set is derived from held-out
citation edges of the starter graph. It is NOT real relevance judgments
(qrels) from KeyArgo's hybrid search, and the simulated "hybrid seeds" are
not real hybrid-retriever output. Numbers produced from it must not be read
as evidence of legal retrieval uplift.

Pipeline
  freeze: graph source -> deterministic proxy queries (seeds + gold) -> frozen JSONL
  run:    frozen config + queries -> held-out graph index -> baseline vs
          graph-augmented lists at matched k -> metrics -> write-once run dir

Graph data comes through a ``GraphSource``. ``LocalJsonlSource`` reads the
starter JSONL; ``OctoSnapshotSource`` reads the same records through the
read-only ``GraphSnapshotStore`` (Octo SQL API) once a snapshot has been loaded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from .retrieval import CitationGraphIndex, graphrag_retrieve, load_dataset
from .storage import GraphSnapshotStore, StorageError

PROXY_LABEL = ("PROVISIONAL PROXY GOLD derived from held-out citation edges; "
               "NOT real qrels; no retrieval uplift is claimed.")
ROOT = Path(__file__).resolve().parents[1]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


# ---------------------------------------------------------------- sources
class GraphSource(Protocol):
    def load(self) -> tuple[list[dict], list[dict], str, dict[str, Any]]:
        """Return nodes, edges (source/target/kind), revision, provenance."""


class LocalJsonlSource:
    def __init__(self, data_dir: Path | str | None = None):
        self.data_dir = Path(data_dir) if data_dir else ROOT / "data"

    def load(self):
        nodes, edges, revision, hashes = load_dataset(self.data_dir)
        return nodes, edges, revision, {"kind": "local_jsonl", **hashes}


class OctoSnapshotSource:
    """Read a whole snapshot via the read-only GraphSnapshotStore (keyset pages)."""

    def __init__(self, store: GraphSnapshotStore, snapshot_id: str | None = None, page: int = 1000):
        self.store, self.snapshot_id, self.page = store, snapshot_id, page

    def _all(self, getter, snapshot_id: str) -> list[dict]:
        out, cursor = [], None
        while True:
            page = getter(snapshot_id, cursor=cursor, limit=self.page)
            out.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                return out

    def load(self):
        meta = None
        snapshot_id = self.snapshot_id
        if snapshot_id is None:
            meta = self.store.get_active_snapshot()
            if meta is None:
                raise StorageError("no active Octo snapshot")
            snapshot_id = meta["snapshot_id"]
        nodes = self._all(self.store.get_authorities, snapshot_id)
        edges = []
        for e in self._all(self.store.get_citation_edges, snapshot_id):
            edges.append({"source": e.get("source", e.get("src")),
                          "target": e.get("target", e.get("dst")), "kind": e["kind"]})
        revision = f"octo:{snapshot_id}:" + _sha(_canon([nodes, edges]))
        prov = {"kind": "octo_snapshot", "snapshot_id": snapshot_id,
                "source_digest": (meta or {}).get("source_digest")}
        return nodes, edges, revision, prov


def _graph_digest(nodes, edges) -> str:
    from .snapshot_import import graph_digest
    return "sha256:" + graph_digest(nodes, edges)


# ---------------------------------------------------------------- freezing
def build_proxy_queries(nodes, edges, *, n_queries: int, seed: int, seed_fraction: float,
                        distractors_per_seed: int, min_cites: int, max_cites: int) -> list[dict]:
    """Held-out citing opinions become queries.

    For query opinion q with outgoing CITES set C(q): gold = C(q); simulated
    hybrid seeds = a deterministic fraction of C(q) mixed with random
    distractor authorities. All of q's outgoing edges are removed from the
    graph at run time so the answer edges are never traversable.
    """
    node_ids = sorted({n["id"] for n in nodes})
    caselaw = {n["id"] for n in nodes if n.get("type") == "caselaw"}
    out: dict[str, set[str]] = {}
    for e in edges:
        if e["kind"] == "cites":
            out.setdefault(e["source"], set()).add(e["target"])
    eligible = sorted(q for q, c in out.items() if q in caselaw and min_cites <= len(c) <= max_cites)
    rng = random.Random(seed)
    chosen = sorted(rng.sample(eligible, min(n_queries, len(eligible))))
    queries = []
    for i, q in enumerate(chosen):
        gold = sorted(out[q])
        r = random.Random(f"{seed}:{q}")
        n_seed = max(1, round(len(gold) * seed_fraction))
        true_seeds = sorted(r.sample(gold, n_seed))
        pool = [x for x in node_ids if x not in out[q] and x != q]
        distract = sorted(r.sample(pool, n_seed * distractors_per_seed))
        seeds = true_seeds + distract
        r.shuffle(seeds)
        queries.append({"query_id": f"proxy-{i:04d}", "held_out_opinion": q,
                        "seeds": seeds, "gold": gold, "label": "proxy"})
    return queries


def seal(config_path: Path, source: GraphSource) -> dict:
    """Record sha256 + graph identity for an externally built queries file (e.g. CLERC)."""
    config = json.loads(config_path.read_text())
    nodes, edges, revision, prov = source.load()
    qbytes = (config_path.parent / config["queries_file"]).read_bytes()
    config["frozen"] = {"queries_sha256": _sha(qbytes), "dataset_revision": revision,
                        "graph_digest": _graph_digest(nodes, edges), "source": prov,
                        "n_queries": sum(1 for line in qbytes.splitlines() if line.strip())}
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return config["frozen"]


def freeze(config_path: Path, source: GraphSource) -> dict:
    config = json.loads(config_path.read_text())
    nodes, edges, revision, prov = source.load()
    queries = build_proxy_queries(nodes, edges, **config["proxy"])
    qpath = config_path.parent / config["queries_file"]
    payload = "".join(json.dumps(q, ensure_ascii=False, sort_keys=True) + "\n" for q in queries).encode()
    if qpath.exists() and _sha(qpath.read_bytes()) != _sha(payload):
        raise FileExistsError(f"{qpath} exists with different content; frozen files are immutable")
    qpath.write_bytes(payload)
    config["frozen"] = {"queries_sha256": _sha(payload), "dataset_revision": revision,
                        "graph_digest": _graph_digest(nodes, edges),
                        "source": prov, "n_queries": len(queries)}
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return config["frozen"]


# ---------------------------------------------------------------- metrics
def recall_at(ranked: Sequence[str], gold: set[str], k: int) -> float:
    return len(set(ranked[:k]) & gold) / len(gold) if gold else 0.0


def ndcg_at(ranked: Sequence[str], gold: set[str], k: int) -> float:
    dcg = sum(1 / math.log2(i + 2) for i, d in enumerate(ranked[:k]) if d in gold)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(k, len(gold))))
    return dcg / idcg if idcg else 0.0


def mrr(ranked: Sequence[str], gold: set[str]) -> float:
    return next((1 / (i + 1) for i, d in enumerate(ranked) if d in gold), 0.0)


def grounded(result: dict, seeds: set[str], index: CitationGraphIndex) -> bool:
    """A graph result is grounded when its path is a contiguous chain of real
    index edges starting at a supplied seed and ending at the result."""
    path = result["path"]
    if not path:
        return False
    cur = path[0]["target"] if path[0]["reverse"] else path[0]["source"]
    if cur not in seeds:
        return False
    for step in path:
        if (step["source"], step["target"], step["kind"]) not in index.edges:
            return False
        a, b = (step["target"], step["source"]) if step["reverse"] else (step["source"], step["target"])
        if a != cur:
            return False
        cur = b
    return cur == result["id"]


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else 0.0


# ---------------------------------------------------------------- run
def run(config_path: Path, source: GraphSource, runs_dir: Path) -> Path:
    config = json.loads(config_path.read_text())
    frozen = config.get("frozen") or {}
    qbytes = (config_path.parent / config["queries_file"]).read_bytes()
    if _sha(qbytes) != frozen.get("queries_sha256"):
        raise ValueError("frozen queries do not match recorded sha256")
    nodes, edges, revision, prov = source.load()
    digest = _graph_digest(nodes, edges)
    if revision != frozen.get("dataset_revision") and digest != frozen.get("graph_digest"):
        raise ValueError("graph revision/content differs from the frozen graph")
    queries = [json.loads(line) for line in qbytes.decode().splitlines() if line]
    held = {q["held_out_opinion"] for q in queries if "held_out_opinion" in q}
    holdout = set()
    if config.get("apply_holdout_edges", True):
        holdout = {tuple(e) for q in queries for e in q.get("holdout_edges", [])}
    kept = [e for e in edges if not (e["kind"] == "cites" and e["source"] in held)
            and (e["source"], e["target"], e["kind"]) not in holdout]
    t0 = time.perf_counter()
    index = CitationGraphIndex(nodes, kept, dataset_revision=revision + ":heldout",
                               source_dataset=prov["kind"])
    build_s = time.perf_counter() - t0
    g = config["graph"]
    ks = config["k_values"]
    kmax = max(ks + [config["ndcg_k"]])
    per_query, lat = [], []
    for q in queries:
        gold, seeds = set(q["gold"]), q["seeds"]
        baseline = list(seeds)[:kmax]
        t = time.perf_counter()
        out = graphrag_retrieve(seeds, kmax, index=index, **g)
        lat.append((time.perf_counter() - t) * 1000)
        graph_res = out["results"]
        augmented = (list(seeds) + [r["id"] for r in graph_res if r["id"] not in set(seeds)])[:kmax]
        ground = [grounded(r, set(seeds), index) for r in graph_res]
        row = {"query_id": q["query_id"], "n_gold": len(gold), "n_seeds": len(seeds),
               "answerable": bool(gold) and bool(seeds),
               "graph_returned": len(graph_res), "truncated": out["retrieval"]["truncated"],
               "grounding_rate": _mean([float(x) for x in ground]) if ground else None}
        for name, ranked in (("baseline", baseline), ("augmented", augmented)):
            row[name] = {**{f"recall@{k}": recall_at(ranked, gold, k) for k in ks},
                         f"ndcg@{config['ndcg_k']}": ndcg_at(ranked, gold, config["ndcg_k"]),
                         "mrr": mrr(ranked, gold)}
        per_query.append(row)
    summary = {name: {m: _mean([r[name][m] for r in per_query]) for m in per_query[0][name]}
               for name in ("baseline", "augmented")} if per_query else {}
    answerable = [r for r in per_query if r["answerable"]]
    summary["answerable_subset"] = {
        "n": len(answerable),
        **{name: {m: _mean([r[name][m] for r in answerable]) for m in per_query[0][name]}
           for name in ("baseline", "augmented")}} if per_query else {}
    summary["n_queries"] = len(per_query)
    summary["holdout_edges_removed"] = len(edges) - len(kept)
    gr = [r["grounding_rate"] for r in per_query if r["grounding_rate"] is not None]
    summary["graph_grounding_rate"] = _mean(gr)
    summary["graph_latency_ms"] = {"p50": _pct(lat, 50), "p95": _pct(lat, 95), "max": max(lat, default=0)}
    summary["index_build_s"] = build_s
    summary["truncated_queries"] = sum(r["truncated"] for r in per_query)
    import platform, langgraph, networkx  # noqa: E401
    from importlib.metadata import version
    record = {"label": config.get("label", PROXY_LABEL), "config": config, "dataset_revision": revision,
              "graph_digest": digest, "source": prov,
              "environment": {"python": platform.python_version(), "platform": platform.platform(),
                              "networkx": networkx.__version__, "langgraph": version("langgraph")},
              "summary": summary, "per_query": per_query}
    # Local runs keep the original id scheme; other sources include their kind.
    id_input = {"config": config, "revision": revision}
    if prov["kind"] != "local_jsonl":
        id_input["source"] = prov
    run_id = _sha(_canon(id_input))[:16]
    target = runs_dir / f"{config['name']}-{run_id}"
    target.mkdir(parents=True, exist_ok=False)  # write-once: never overwrite a run
    body = json.dumps(record, indent=2, ensure_ascii=False).encode()
    with open(target / "results.json", "xb") as f:
        f.write(body)
    with open(target / "MANIFEST.sha256", "x") as f:
        f.write(f"{_sha(body)}  results.json\n")
    for p in (target / "results.json", target / "MANIFEST.sha256"):
        p.chmod(0o444)
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=PROXY_LABEL)
    ap.add_argument("command", choices=["freeze", "seal", "run"])
    ap.add_argument("--config", type=Path, default=ROOT / "benchmarks/proxy-v1/config.json")
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--runs-dir", type=Path, default=ROOT / "benchmarks/runs")
    ap.add_argument("--source", choices=["local", "octo"], default="local")
    ap.add_argument("--workspace", help="Octo workspace id (with --source octo)")
    ap.add_argument("--snapshot", help="Octo snapshot id; default = active snapshot")
    a = ap.parse_args(argv)
    if a.source == "octo":
        from .octo_client import OctoSqlExecutor
        src = OctoSnapshotSource(GraphSnapshotStore(OctoSqlExecutor(a.workspace)), a.snapshot)
    else:
        src = LocalJsonlSource(a.data_dir)
    if a.command in ("freeze", "seal"):
        print(json.dumps((freeze if a.command == "freeze" else seal)(a.config, src), indent=2))
    else:
        path = run(a.config, src, a.runs_dir)
        print(path)
        print(json.dumps(json.loads((path / "results.json").read_text())["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
