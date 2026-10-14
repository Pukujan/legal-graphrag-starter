"""Non-destructive evidence and decision annotation graph overlay."""

from __future__ import annotations

import copy
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any

import networkx as nx

from .decisions import filter_annotations


def build_annotation_overlay(citation_graph: nx.MultiDiGraph, annotations: Sequence[Mapping[str, Any]]) -> nx.MultiDiGraph:
    """Copy citation facts and add evidence/decision nodes in reserved namespaces."""
    if not isinstance(citation_graph, nx.MultiDiGraph):
        raise ValueError("citation_graph must be a NetworkX MultiDiGraph")
    overlay = copy.deepcopy(citation_graph)
    original_node_ids = set(overlay.nodes)
    annotation_ids: set[str] = set()
    prepared = []
    for record in annotations:
        if not isinstance(record, Mapping) or not isinstance(record.get("id"), str) or not record["id"]:
            raise ValueError("each annotation needs a non-empty string id")
        source_id = record.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("each annotation needs a non-empty string source_id")
        for field in ("subject_id", "object_id"):
            if record.get(field) is not None and not isinstance(record.get(field), str):
                raise ValueError(f"annotation {field} must be a string or None")
        annotation_key, source_key = f"annotation:{record['id']}", f"source:{source_id}"
        if annotation_key in original_node_ids or source_key in original_node_ids or annotation_key in annotation_ids:
            raise ValueError("annotation/source namespace collision or duplicate annotation ID")
        annotation_ids.add(annotation_key)
        prepared.append((record, annotation_key, source_key))
    for record, annotation_key, source_key in prepared:
        if source_key not in overlay:
            overlay.add_node(source_key, kind="source", source_id=record["source_id"])
        overlay.add_node(annotation_key, kind="annotation", annotation=copy.deepcopy(dict(record)))
        overlay.add_edge(source_key, annotation_key, key="evidence", kind="evidence")
        for field, edge_key in (("subject_id", "subject"), ("object_id", "object")):
            authority_id = record.get(field)
            if authority_id is not None and authority_id in overlay and authority_id not in {source_key, annotation_key}:
                overlay.add_edge(annotation_key, authority_id, key=edge_key, kind=edge_key)
    return overlay


def retrieve_annotated(graph: nx.MultiDiGraph, seeds: Sequence[str], *, hops: int = 1,
                       direction: str = "incoming", minimum_support: float = 0.8,
                       allowed_assertions: Sequence[str] = ("asserted",)) -> set[str]:
    """Retrieve authority IDs over citations and policy-eligible semantic links."""
    if type(hops) is not int or not 0 <= hops <= 3:
        raise ValueError("hops must be an integer between 0 and 3")
    if direction not in {"incoming", "outgoing", "both"}:
        raise ValueError("direction must be 'incoming', 'outgoing', or 'both'")
    if isinstance(seeds, (str, bytes)) or not isinstance(seeds, Sequence) or any(not isinstance(s, str) for s in seeds):
        raise ValueError("seeds must be a sequence of authority IDs")
    eligible = filter_annotations(
        [attrs["annotation"] for _, attrs in graph.nodes(data=True)
         if attrs.get("kind") == "annotation" and isinstance(attrs.get("annotation"), Mapping)],
        minimum_support=minimum_support, allowed_assertions=allowed_assertions)
    semantic = {(row.get("subject_id"), row.get("object_id")) for row in eligible
                if row.get("subject_id") in graph and row.get("object_id") in graph}
    reached = {seed for seed in seeds if seed in graph and graph.nodes[seed].get("kind") != "annotation"
               and graph.nodes[seed].get("kind") != "source"}
    frontier = set(reached)
    for _ in range(hops):
        following: set[str] = set()
        for node in frontier:
            if direction in {"incoming", "both"}:
                following.update(src for src, _, key, attrs in graph.in_edges(node, keys=True, data=True)
                                 if attrs.get("kind", key) == "cites")
                following.update(subject for subject, obj in semantic if obj == node)
            if direction in {"outgoing", "both"}:
                following.update(dst for _, dst, key, attrs in graph.out_edges(node, keys=True, data=True)
                                 if attrs.get("kind", key) == "cites")
                following.update(obj for subject, obj in semantic if subject == node)
        following = {node for node in following if graph.nodes[node].get("kind") not in {"annotation", "source"}}
        frontier = following - reached
        reached.update(frontier)
    return reached
