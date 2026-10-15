"""Bounded citation-neighborhood retrieval over the frozen local graph export.

The public functions accept ordinary lists so callers can use fixtures or their
existing seed search. ``CitationGraphIndex`` is the reusable NetworkX reference
cache for applications and benchmarks: construction is deliberately separate
from repeated traversal timing.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, TypedDict

import networkx as nx
from langgraph.graph import END, START, StateGraph

MAX_NODES = 500
MAX_EDGES = 1000
MAX_DEPTH = 3
DEFAULT_MAX_SCANNED_EDGES = 10_000
DEFAULT_EDGE_KINDS = ("cites",)
DEFAULT_DIRECTION = "incoming"
DEFAULT_DEPTH = 1
DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _edge_identity(edge: dict[str, Any]) -> tuple[str, str, str]:
    return edge["source"], edge["target"], edge["kind"]


def _validate_ids(values: Sequence[str], label: str) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{label} must be a sequence of strings")
    if any(not isinstance(value, str) for value in values):
        raise ValueError(f"{label} must contain only strings")
    return list(values)


def _validate_request(
    seeds: Sequence[str],
    *,
    depth: int = DEFAULT_DEPTH,
    direction: str = DEFAULT_DIRECTION,
    edge_kinds: Sequence[str] = DEFAULT_EDGE_KINDS,
    max_nodes: int = MAX_NODES,
    max_edges: int = MAX_EDGES,
    max_scanned_edges: int = DEFAULT_MAX_SCANNED_EDGES,
) -> tuple[list[str], dict[str, Any]]:
    seed_values = _validate_ids(seeds, "seeds")
    if type(depth) is not int or not 0 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be an integer between 0 and {MAX_DEPTH}")
    if not isinstance(direction, str) or direction not in {"incoming", "outgoing", "both"}:
        raise ValueError("direction must be 'incoming', 'outgoing', or 'both'")
    if isinstance(edge_kinds, (str, bytes)) or not isinstance(edge_kinds, Sequence):
        raise ValueError("edge_kinds must be a sequence of strings")
    if any(not isinstance(kind, str) or not kind for kind in edge_kinds):
        raise ValueError("edge_kinds must contain non-empty strings")
    for name, value, maximum in (
        ("max_nodes", max_nodes, MAX_NODES),
        ("max_edges", max_edges, MAX_EDGES),
        ("max_scanned_edges", max_scanned_edges, None),
    ):
        if type(value) is not int or value <= 0 or (maximum is not None and value > maximum):
            limit = f" and no greater than {maximum}" if maximum is not None else ""
            raise ValueError(f"{name} must be a positive integer{limit}")
    kinds = sorted(set(edge_kinds))
    parameters = {
        "depth": depth,
        "direction": direction,
        "edge_kinds": kinds,
        "max_nodes": max_nodes,
        "max_edges": max_edges,
        "max_scanned_edges": max_scanned_edges,
    }
    return sorted(set(seed_values)), parameters


def load_dataset(data_dir: Path | str = DATA_DIR) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, dict[str, str]]:
    """Load the original JSONL export and return canonical source/target edges.

    ``source``/``target`` are the public API names; the source export's ``src``
    and ``dst`` fields are translated without changing the authorities.
    """
    directory = Path(data_dir)
    node_path = directory / "nodes.jsonl"
    edge_path = directory / "edges.jsonl"
    node_bytes = node_path.read_bytes()
    edge_bytes = edge_path.read_bytes()
    nodes = [json.loads(line) for line in node_bytes.decode("utf-8").splitlines() if line.strip()]
    raw_edges = [json.loads(line) for line in edge_bytes.decode("utf-8").splitlines() if line.strip()]
    edges = [
        {**{key: value for key, value in edge.items() if key not in {"src", "dst"}},
         "source": edge["src"], "target": edge["dst"]}
        for edge in raw_edges
    ]
    hashes = {
        "nodes_sha256": hashlib.sha256(node_bytes).hexdigest(),
        "edges_sha256": hashlib.sha256(edge_bytes).hexdigest(),
    }
    revision = f"sha256:{hashes['nodes_sha256']}:{hashes['edges_sha256']}"
    return nodes, edges, revision, hashes


class _WorkflowState(TypedDict, total=False):
    index: "CitationGraphIndex"
    seeds: list[str]
    options: dict[str, Any]
    result: dict[str, Any]


def _compile_workflow():
    """Compile a real LangGraph validate → expand → return workflow."""

    def validate_node(state: _WorkflowState) -> dict[str, Any]:
        seeds, parameters = _validate_request(state["seeds"], **state["options"])
        return {"seeds": seeds, "options": parameters}

    def expand_node(state: _WorkflowState) -> dict[str, Any]:
        return {"result": state["index"]._retrieve_validated(state["seeds"], state["options"])}

    def return_node(state: _WorkflowState) -> dict[str, Any]:
        return {"result": state["result"]}

    builder = StateGraph(_WorkflowState)
    builder.add_node("validate", validate_node)
    builder.add_node("expand", expand_node)
    builder.add_node("return", return_node)
    builder.add_edge(START, "validate")
    builder.add_edge("validate", "expand")
    builder.add_edge("expand", "return")
    builder.add_edge("return", END)
    return builder.compile()


class CitationGraphIndex:
    """Reusable NetworkX index with bounded, deterministic neighborhood queries."""

    def __init__(
        self,
        nodes: Sequence[dict[str, Any]],
        edges: Sequence[dict[str, Any]],
        *,
        dataset_revision: str = "supplied",
        source_dataset: str = "supplied",
    ) -> None:
        if not isinstance(nodes, Sequence) or isinstance(nodes, (str, bytes)):
            raise ValueError("nodes must be a sequence of objects")
        if not isinstance(edges, Sequence) or isinstance(edges, (str, bytes)):
            raise ValueError("edges must be a sequence of objects")

        node_records: dict[str, dict[str, Any]] = {}
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("id"), str):
                raise ValueError("each node must be an object with a string id")
            node_id = node["id"]
            prior = node_records.get(node_id)
            if prior is None or _stable_json(node) < _stable_json(prior):
                node_records[node_id] = dict(node)

        edge_records: dict[tuple[str, str, str], dict[str, Any]] = {}
        for edge in edges:
            if not isinstance(edge, dict):
                raise ValueError("each edge must be an object")
            source, target, kind = edge.get("source"), edge.get("target"), edge.get("kind")
            if not all(isinstance(value, str) and value for value in (source, target, kind)):
                raise ValueError("each edge must have non-empty string source, target, and kind")
            if source not in node_records or target not in node_records:
                continue
            record = dict(edge)
            identity = (source, target, kind)
            prior = edge_records.get(identity)
            if prior is None or _stable_json(record) < _stable_json(prior):
                edge_records[identity] = record

        self.nodes = node_records
        self.edges = edge_records
        self.dataset_revision = dataset_revision
        self.source_dataset = source_dataset
        self.graph = nx.MultiDiGraph()
        for node_id in sorted(node_records):
            self.graph.add_node(node_id, **{key: value for key, value in node_records[node_id].items() if key != "id"})
        for identity in sorted(edge_records):
            record = edge_records[identity]
            source, target, kind = identity
            attributes = {key: value for key, value in record.items() if key not in {"source", "target", "kind", "reverse"}}
            self.graph.add_edge(source, target, key=kind, kind=kind, **attributes)
        self._citation_indegree: Counter[str] = Counter()
        for source, target, kind in sorted(edge_records):
            if kind == "cites":
                self._citation_indegree[target] += 1
        self._workflow = _compile_workflow()

    def _record(self, identity: tuple[str, str, str], reverse: bool | None = None) -> dict[str, Any]:
        result = {key: value for key, value in self.edges[identity].items() if key != "reverse"}
        if reverse is not None:
            result["reverse"] = reverse
        return result

    def _incident(self, node_id: str, direction: str) -> Iterable[tuple[tuple[str, str, str], bool]]:
        if direction in {"incoming", "both"}:
            for source, target, kind in self.graph.in_edges(node_id, keys=True):
                yield (source, target, kind), True
        if direction in {"outgoing", "both"}:
            for source, target, kind in self.graph.out_edges(node_id, keys=True):
                yield (source, target, kind), False

    @staticmethod
    def _path_key(path: list[dict[str, Any]]) -> tuple[Any, ...]:
        if not path:
            return ()
        first = path[0]
        seed = first["target"] if first["reverse"] else first["source"]
        return (seed, tuple((edge["source"], edge["target"], edge["kind"], edge["reverse"]) for edge in path))

    def _retrieve_validated(self, seeds: list[str], parameters: dict[str, Any]) -> dict[str, Any]:
        reached = [seed for seed in seeds if seed in self.nodes]
        reached_set = set(reached)
        missing = [seed for seed in seeds if seed not in self.nodes]
        paths: dict[str, list[dict[str, Any]]] = {seed: [] for seed in reached}
        depths = {seed: 0 for seed in reached}
        scan_seen: set[tuple[str, str, str]] = set()
        scanned_edges = 0
        visited_nodes = 0
        reasons: set[str] = set()
        expansion_stopped = False
        allowed_kinds = set(parameters["edge_kinds"])

        if len(reached) > parameters["max_nodes"]:
            reached = reached[: parameters["max_nodes"]]
            reached_set = set(reached)
            paths = {seed: [] for seed in reached}
            depths = {seed: 0 for seed in reached}
            reasons.add("node_budget")

        frontier = list(reached)
        for layer in range(parameters["depth"]):
            next_paths: dict[str, list[dict[str, Any]]] = {}
            for current in sorted(frontier):
                visited_nodes += 1
                for identity, reverse in self._incident(current, parameters["direction"]):
                    if identity not in scan_seen:
                        if scanned_edges >= parameters["max_scanned_edges"]:
                            reasons.add("scan_budget")
                            expansion_stopped = True
                            break
                        scan_seen.add(identity)
                        scanned_edges += 1
                    if identity[2] not in allowed_kinds:
                        continue
                    source, target, _kind = identity
                    neighbor = source if reverse else target
                    if neighbor in reached_set:
                        continue
                    candidate = [*paths[current], self._record(identity, reverse)]
                    existing = next_paths.get(neighbor)
                    if existing is not None:
                        if self._path_key(candidate) < self._path_key(existing):
                            next_paths[neighbor] = candidate
                        continue
                    if len(reached_set) + len(next_paths) >= parameters["max_nodes"]:
                        reasons.add("node_budget")
                        expansion_stopped = True
                        break
                    next_paths[neighbor] = candidate
                if expansion_stopped:
                    break
            paths.update(next_paths)
            depths.update({node: layer + 1 for node in next_paths})
            reached_set.update(next_paths)
            if expansion_stopped:
                break
            frontier = sorted(next_paths)
            if not frontier:
                break

        # A max-edge budget must leave every returned shortest path inspectable.
        output_nodes = set(reached_set)
        path_edge_ids = {
            _edge_identity(edge)
            for path in paths.values()
            for edge in path
        }
        if len(path_edge_ids) > parameters["max_edges"]:
            reasons.add("edge_budget")
            output_nodes = set(seed for seed in reached if seed in self.nodes)
            path_edge_ids = set()
            ordered_candidates = sorted(
                (node for node in reached_set if node not in output_nodes),
                key=lambda node: (depths.get(node, MAX_DEPTH + 1), self._path_key(paths[node]), node),
            )
            for node in ordered_candidates:
                candidate_edges = {_edge_identity(edge) for edge in paths[node]}
                if len(path_edge_ids | candidate_edges) <= parameters["max_edges"]:
                    output_nodes.add(node)
                    path_edge_ids |= candidate_edges

        selected_edge_ids: set[tuple[str, str, str]] = set(path_edge_ids)
        output_scan_stopped = False
        for node in sorted(output_nodes):
            for identity, _reverse in self._incident(node, "outgoing"):
                if identity not in scan_seen:
                    if scanned_edges >= parameters["max_scanned_edges"]:
                        reasons.add("scan_budget")
                        output_scan_stopped = True
                        break
                    scan_seen.add(identity)
                    scanned_edges += 1
                if identity[2] not in allowed_kinds:
                    continue
                if identity[1] not in output_nodes or identity in selected_edge_ids:
                    continue
                if len(selected_edge_ids) >= parameters["max_edges"]:
                    reasons.add("edge_budget")
                    output_scan_stopped = True
                    break
                selected_edge_ids.add(identity)
            if output_scan_stopped:
                break

        # If a scan cap stopped edge enumeration, other induced edges may remain.
        # The explicit reason makes the partial edge set visible to callers.
        output_paths = {node: paths[node] for node in sorted(output_nodes)}
        output = {
            "nodes": [self.nodes[node] for node in sorted(output_nodes)],
            "edges": [self._record(identity) for identity in sorted(selected_edge_ids)],
            "missing_seeds": missing,
            "paths": output_paths,
            "parameters": parameters,
            "truncated": bool(reasons),
            "truncation_reasons": sorted(reasons),
            "work": {"scanned_edges": scanned_edges, "visited_nodes": visited_nodes},
        }
        return output

    def retrieve(
        self,
        seeds: Sequence[str],
        *,
        depth: int = DEFAULT_DEPTH,
        direction: str = DEFAULT_DIRECTION,
        edge_kinds: Sequence[str] = DEFAULT_EDGE_KINDS,
        max_nodes: int = MAX_NODES,
        max_edges: int = MAX_EDGES,
        max_scanned_edges: int = DEFAULT_MAX_SCANNED_EDGES,
    ) -> dict[str, Any]:
        normalized_seeds, parameters = _validate_request(
            seeds, depth=depth, direction=direction, edge_kinds=edge_kinds,
            max_nodes=max_nodes, max_edges=max_edges, max_scanned_edges=max_scanned_edges,
        )
        return self._retrieve_validated(normalized_seeds, parameters)

    def retrieve_workflow(
        self,
        seeds: Sequence[str],
        *,
        depth: int = DEFAULT_DEPTH,
        direction: str = DEFAULT_DIRECTION,
        edge_kinds: Sequence[str] = DEFAULT_EDGE_KINDS,
        max_nodes: int = MAX_NODES,
        max_edges: int = MAX_EDGES,
        max_scanned_edges: int = DEFAULT_MAX_SCANNED_EDGES,
    ) -> dict[str, Any]:
        options = {
            "depth": depth, "direction": direction, "edge_kinds": edge_kinds,
            "max_nodes": max_nodes, "max_edges": max_edges,
            "max_scanned_edges": max_scanned_edges,
        }
        normalized_seeds, normalized_options = _validate_request(seeds, **options)
        result = self._workflow.invoke({"index": self, "seeds": normalized_seeds, "options": normalized_options})
        return result["result"]

    def global_citation_indegree(self, node_id: str) -> int:
        return self._citation_indegree[node_id]


def retrieve(
    nodes: Sequence[dict[str, Any]],
    edges: Sequence[dict[str, Any]],
    seeds: Sequence[str],
    *,
    depth: int = DEFAULT_DEPTH,
    direction: str = DEFAULT_DIRECTION,
    edge_kinds: Sequence[str] = DEFAULT_EDGE_KINDS,
    max_nodes: int = MAX_NODES,
    max_edges: int = MAX_EDGES,
    max_scanned_edges: int = DEFAULT_MAX_SCANNED_EDGES,
) -> dict[str, Any]:
    """Return a deterministic, bounded, induced citation neighborhood."""
    return CitationGraphIndex(nodes, edges).retrieve(
        seeds, depth=depth, direction=direction, edge_kinds=edge_kinds,
        max_nodes=max_nodes, max_edges=max_edges, max_scanned_edges=max_scanned_edges,
    )


def retrieve_workflow(
    nodes: Sequence[dict[str, Any]],
    edges: Sequence[dict[str, Any]],
    seeds: Sequence[str],
    *,
    depth: int = DEFAULT_DEPTH,
    direction: str = DEFAULT_DIRECTION,
    edge_kinds: Sequence[str] = DEFAULT_EDGE_KINDS,
    max_nodes: int = MAX_NODES,
    max_edges: int = MAX_EDGES,
    max_scanned_edges: int = DEFAULT_MAX_SCANNED_EDGES,
) -> dict[str, Any]:
    """Run the same contract through an actual LangGraph StateGraph."""
    return CitationGraphIndex(nodes, edges).retrieve_workflow(
        seeds, depth=depth, direction=direction, edge_kinds=edge_kinds,
        max_nodes=max_nodes, max_edges=max_edges, max_scanned_edges=max_scanned_edges,
    )


def graphrag_retrieve(
    seed_citations: Sequence[str],
    k: int,
    *,
    nodes: Sequence[dict[str, Any]] | None = None,
    edges: Sequence[dict[str, Any]] | None = None,
    index: CitationGraphIndex | None = None,
    mode: str = "citation_only",
    depth: int = DEFAULT_DEPTH,
    direction: str = DEFAULT_DIRECTION,
    edge_kinds: Sequence[str] = DEFAULT_EDGE_KINDS,
    max_nodes: int = MAX_NODES,
    max_edges: int = MAX_EDGES,
    max_scanned_edges: int = DEFAULT_MAX_SCANNED_EDGES,
) -> dict[str, Any]:
    """Rank reached non-seeds by global distinct incoming citation count.

    This simple citation prior is a transparent baseline, not a text relevance
    score or evidence of legal authority/effectiveness.
    """
    if type(k) is not int or not 1 <= k <= MAX_NODES:
        raise ValueError(f"k must be an integer between 1 and {MAX_NODES}")
    if mode != "citation_only":
        raise ValueError("only mode='citation_only' is available in this increment")
    if index is not None:
        if nodes is not None or edges is not None:
            raise ValueError("provide either index or nodes and edges, not both")
        graph_index = index
        revision = graph_index.dataset_revision
        dataset = graph_index.source_dataset
    elif nodes is None and edges is None:
        loaded_nodes, loaded_edges, revision, _hashes = load_dataset()
        dataset = "data/nodes.jsonl + data/edges.jsonl"
        graph_index = CitationGraphIndex(
            loaded_nodes, loaded_edges, dataset_revision=revision, source_dataset=dataset
        )
    elif nodes is not None and edges is not None:
        graph_index = CitationGraphIndex(nodes, edges)
        revision = graph_index.dataset_revision
        dataset = graph_index.source_dataset
    else:
        raise ValueError("nodes and edges must be supplied together")

    normalized_seeds, _parameters = _validate_request(
        seed_citations, depth=depth, direction=direction, edge_kinds=edge_kinds,
        max_nodes=max_nodes, max_edges=max_edges, max_scanned_edges=max_scanned_edges,
    )
    retrieval = graph_index.retrieve(
        normalized_seeds, depth=depth, direction=direction, edge_kinds=edge_kinds,
        max_nodes=max_nodes, max_edges=max_edges, max_scanned_edges=max_scanned_edges,
    )
    seed_set = set(normalized_seeds)
    candidates = [node["id"] for node in retrieval["nodes"] if node["id"] not in seed_set]
    candidates.sort(key=lambda node_id: (
        -graph_index.global_citation_indegree(node_id),
        len(retrieval["paths"][node_id]),
        node_id,
    ))
    node_records = {node["id"]: node for node in retrieval["nodes"]}
    results = []
    for node_id in candidates[:k]:
        path = retrieval["paths"][node_id]
        seed_id = None
        if path:
            first = path[0]
            seed_id = first["target"] if first["reverse"] else first["source"]
        results.append({
            "id": node_id,
            "score": graph_index.global_citation_indegree(node_id),
            "method": "global_citation_in_degree",
            "node": node_records[node_id],
            "path": path,
            "provenance": {
                "seed_id": seed_id,
                "source_dataset": dataset,
                "dataset_revision": revision,
            },
        })
    return {"results": results, "retrieval": retrieval}


__all__ = [
    "CitationGraphIndex", "graphrag_retrieve", "load_dataset", "retrieve", "retrieve_workflow",
]
