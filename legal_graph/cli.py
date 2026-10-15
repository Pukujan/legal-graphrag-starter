"""JSON CLI and reproducible local direct/LangGraph benchmark."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from legal_graph.retrieval import (
    DEFAULT_DIRECTION,
    DEFAULT_DEPTH,
    DEFAULT_EDGE_KINDS,
    DEFAULT_MAX_SCANNED_EDGES,
    MAX_EDGES,
    MAX_NODES,
    CitationGraphIndex,
    graphrag_retrieve,
    load_dataset,
)


def _positive(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _metadata(data_dir: Path, revision: str, hashes: dict[str, str]) -> dict[str, Any]:
    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=data_dir.parent, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        git_sha = None
    versions = {}
    for distribution in ("networkx", "langgraph", "numpy", "scipy"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    return {
        "dataset_revision": revision,
        "source_files": {
            "nodes.jsonl": hashes["nodes_sha256"],
            "edges.jsonl": hashes["edges_sha256"],
        },
        "git_sha": git_sha,
        "python": platform.python_version(),
        "dependencies": versions,
    }


def _options(args: argparse.Namespace) -> dict[str, Any]:
    kinds = tuple(args.edge_kind or DEFAULT_EDGE_KINDS)
    if args.max_nodes > MAX_NODES:
        raise ValueError(f"--max-nodes cannot exceed {MAX_NODES}")
    if args.max_edges > MAX_EDGES:
        raise ValueError(f"--max-edges cannot exceed {MAX_EDGES}")
    if args.depth > 3:
        raise ValueError("--depth cannot exceed 3")
    return {
        "depth": args.depth,
        "direction": args.direction,
        "edge_kinds": kinds,
        "max_nodes": args.max_nodes,
        "max_edges": args.max_edges,
        "max_scanned_edges": args.max_scanned_edges,
    }


def _load_index(data_dir: Path) -> tuple[CitationGraphIndex, dict[str, Any], float, float]:
    read_started = time.perf_counter()
    nodes, edges, revision, hashes = load_dataset(data_dir)
    read_seconds = time.perf_counter() - read_started
    build_started = time.perf_counter()
    index = CitationGraphIndex(
        nodes,
        edges,
        dataset_revision=revision,
        source_dataset="data/nodes.jsonl + data/edges.jsonl",
    )
    build_seconds = time.perf_counter() - build_started
    metadata = _metadata(data_dir, revision, hashes)
    metadata["input_counts"] = {"nodes": len(nodes), "edges": len(edges)}
    metadata["indexed_counts"] = {"nodes": len(index.nodes), "edges": len(index.edges)}
    metadata["load_seconds"] = read_seconds + build_seconds
    metadata["read_seconds"] = read_seconds
    metadata["index_build_seconds"] = build_seconds
    return index, metadata, read_seconds, build_seconds


def run_retrieve(args: argparse.Namespace) -> dict[str, Any]:
    data_dir = Path(args.data_dir).resolve()
    index, metadata, _read_seconds, _build_seconds = _load_index(data_dir)
    options = _options(args)
    retrieval = index.retrieve(args.seed, **options)
    ranked = graphrag_retrieve(args.seed, args.k, index=index, **options)
    return {
        "metadata": metadata,
        "retrieval": retrieval,
        "ranking": ranked["results"],
        "truncation": {
            "truncated": retrieval["truncated"],
            "reasons": retrieval["truncation_reasons"],
            "scanned_edges": retrieval["work"]["scanned_edges"],
        },
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    data_dir = Path(args.data_dir).resolve()
    index, metadata, _read_seconds, _build_seconds = _load_index(data_dir)
    options = _options(args)

    direct = index.retrieve(args.seed, **options)
    workflow = index.retrieve_workflow(args.seed, **options)
    if direct != workflow:
        raise RuntimeError("direct and LangGraph results differ before timing")

    for _ in range(args.warmup):
        if index.retrieve(args.seed, **options) != direct:
            raise RuntimeError("direct result changed during warmup")
        if index.retrieve_workflow(args.seed, **options) != direct:
            raise RuntimeError("LangGraph result changed during warmup")

    direct_times: list[float] = []
    workflow_times: list[float] = []
    for _ in range(args.runs):
        started = time.perf_counter()
        direct_result = index.retrieve(args.seed, **options)
        direct_times.append(time.perf_counter() - started)
        started = time.perf_counter()
        workflow_result = index.retrieve_workflow(args.seed, **options)
        workflow_times.append(time.perf_counter() - started)
        if direct_result != workflow_result:
            raise RuntimeError("direct and LangGraph results diverged during timing")

    def summary(samples: list[float]) -> dict[str, Any]:
        return {
            "runs": len(samples),
            "median_seconds": sorted(samples)[len(samples) // 2],
            "p95_seconds": _percentile(samples, 0.95),
        }

    ranked = graphrag_retrieve(args.seed, args.k, index=index, **options)
    result_counts = {
        "nodes": len(direct["nodes"]),
        "edges": len(direct["edges"]),
        "missing_seeds": len(direct["missing_seeds"]),
        "ranked_results": len(ranked["results"]),
        "truncated": direct["truncated"],
        "truncation_reasons": direct["truncation_reasons"],
        "scanned_edges": direct["work"]["scanned_edges"],
    }
    metadata["timing_method"] = "nearest-rank p95; upper-middle median for even run counts"
    return {
        "metadata": metadata,
        "request": {"seeds": sorted(set(args.seed)), "k": args.k, **direct["parameters"]},
        "result_counts": result_counts,
        "direct": summary(direct_times),
        "langgraph": summary(workflow_times),
        "results_equal": True,
        "ranking_preview": ranked["results"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded legal citation graph retrieval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("retrieve", "Return a JSON citation neighborhood and citation-prior ranking"),
        ("benchmark", "Compare direct and LangGraph retrieval on one frozen dataset"),
    ):
        child = subparsers.add_parser(command, help=help_text)
        child.add_argument("--data-dir", default=str(Path(__file__).resolve().parents[1] / "data"))
        child.add_argument("--seed", action="append", default=None, help="seed authority ID; repeatable")
        child.add_argument("--k", type=_positive, default=5)
        child.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
        child.add_argument("--direction", choices=("incoming", "outgoing", "both"), default=DEFAULT_DIRECTION)
        child.add_argument("--edge-kind", action="append", help="allowed edge kind; repeatable")
        child.add_argument("--max-nodes", type=_positive, default=MAX_NODES)
        child.add_argument("--max-edges", type=_positive, default=MAX_EDGES)
        child.add_argument("--max-scanned-edges", type=_positive, default=DEFAULT_MAX_SCANNED_EDGES)
    benchmark = subparsers.choices["benchmark"]
    benchmark.add_argument("--runs", type=_positive, default=30)
    benchmark.add_argument("--warmup", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.seed is None:
        args.seed = ["42 U.S.C. § 1983"]
    if args.command == "benchmark" and args.warmup < 0:
        parser.error("--warmup must be non-negative")
    try:
        result = run_retrieve(args) if args.command == "retrieve" else run_benchmark(args)
    except ValueError as error:
        parser.error(str(error))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
