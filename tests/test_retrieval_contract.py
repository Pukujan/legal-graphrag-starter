"""Root-authored public contract: written before product implementation."""
import copy
import pytest
from legal_graph.retrieval import retrieve, retrieve_workflow


NODES = [{"id": x} for x in "ABCDEZ"]
EDGES = [
    {"source": "A", "target": "B", "kind": "cites"},
    {"source": "C", "target": "B", "kind": "cites"},
    {"source": "B", "target": "D", "kind": "cites"},
    {"source": "D", "target": "A", "kind": "cites"},
    {"source": "B", "target": "A", "kind": "cites"},
    {"source": "A", "target": "B", "kind": "part_of"},
    {"source": "E", "target": "C", "kind": "part_of"},
]


def ids(result):
    return {n["id"] for n in result["nodes"]}


def edge_ids(result):
    return {(e["source"], e["target"], e["kind"]) for e in result["edges"]}


def oracle(nodes, edges, seeds, depth, direction, kinds):
    """Independent set expansion; no product helper or NetworkX traversal."""
    known = {n["id"] for n in nodes}
    reached = set(seeds) & known
    frontier = reached.copy()
    for _ in range(depth):
        next_layer = set()
        for e in edges:
            s, t = e["source"], e["target"]
            if e["kind"] not in kinds or s not in known or t not in known:
                continue
            if direction in ("outgoing", "both") and s in frontier:
                next_layer.add(t)
            if direction in ("incoming", "both") and t in frontier:
                next_layer.add(s)
        frontier = next_layer - reached
        reached |= frontier
    selected = {(e["source"], e["target"], e["kind"]) for e in edges
                if e["kind"] in kinds and e["source"] in reached and e["target"] in reached}
    return reached, selected


@pytest.mark.parametrize("direction", ["incoming", "outgoing", "both"])
@pytest.mark.parametrize("depth", range(4))
@pytest.mark.parametrize("kinds", [("cites",), ("part_of",), ("cites", "part_of")])
def test_independent_differential(direction, depth, kinds):
    result = retrieve(NODES, EDGES, ["B"], depth=depth, direction=direction, edge_kinds=kinds)
    expected_nodes, expected_edges = oracle(NODES, EDGES, ["B"], depth, direction, kinds)
    assert ids(result) == expected_nodes
    assert edge_ids(result) == expected_edges
    assert result["truncated"] is False
    assert result["truncation_reasons"] == []


def test_defaults_missing_isolated_and_no_mutation():
    original = copy.deepcopy((NODES, EDGES))
    result = retrieve(NODES, EDGES, ["B", "missing", "B"])
    assert ids(result) == {"A", "B", "C"}
    assert result["missing_seeds"] == ["missing"]
    assert ids(retrieve(NODES, EDGES, ["Z"])) == {"Z"}
    assert ids(retrieve(NODES, EDGES, ["missing"])) == set()
    assert ids(retrieve(NODES, EDGES, [])) == set()
    assert (NODES, EDGES) == original


def test_shortest_paths_keep_canonical_edge_direction():
    result = retrieve(NODES, EDGES, ["B"], direction="incoming", depth=2)
    assert result["paths"]["B"] == []
    for node, path in result["paths"].items():
        cursor = "B"
        assert len(path) <= 2
        for e in path:
            assert (e["source"], e["target"], e["kind"]) in edge_ids(result)
            assert e["reverse"] is True
            assert e["target"] == cursor
            cursor = e["source"]
        assert cursor == node


def test_order_and_duplicate_metamorphisms():
    baseline = retrieve(NODES, EDGES, ["B", "A"], direction="both", depth=2)
    changed = retrieve(list(reversed(NODES)), list(reversed(EDGES)) + EDGES,
                       ["A", "B", "B"], direction="both", depth=2)
    assert changed == baseline


def test_edge_reversal_and_direction_swap():
    original = retrieve(NODES, EDGES, ["B"], direction="incoming", depth=2)
    reversed_edges = [dict(e, source=e["target"], target=e["source"]) for e in EDGES]
    changed = retrieve(NODES, reversed_edges, ["B"], direction="outgoing", depth=2)
    assert ids(original) == ids(changed)
    assert edge_ids(original) == {(t, s, k) for s, t, k in edge_ids(changed)}


def test_uncapped_depth_and_kind_monotonicity():
    small = retrieve(NODES, EDGES, ["B"], direction="both", depth=1)
    deep = retrieve(NODES, EDGES, ["B"], direction="both", depth=2)
    broad = retrieve(NODES, EDGES, ["B"], direction="both", depth=2,
                     edge_kinds=("cites", "part_of"))
    assert ids(small) <= ids(deep) <= ids(broad)
    assert edge_ids(small) <= edge_ids(deep) <= edge_ids(broad)


def test_disconnected_component_does_not_change_neighborhood():
    baseline = retrieve(NODES, EDGES, ["B"])
    changed = retrieve(NODES + [{"id": "X"}, {"id": "Y"}],
                       EDGES + [{"source": "X", "target": "Y", "kind": "cites"}], ["B"])
    assert changed == baseline


@pytest.mark.parametrize("options", [{"depth": -1}, {"depth": 4}, {"depth": True},
    {"depth": 1.5}, {"direction": "sideways"}, {"max_nodes": 0},
    {"max_nodes": 501}, {"max_edges": 0}, {"max_edges": 1001},
    {"max_scanned_edges": 0}])
def test_invalid_request(options):
    with pytest.raises(ValueError):
        retrieve(NODES, EDGES, ["B"], **options)


@pytest.mark.parametrize("budget", [{"max_nodes": 2}, {"max_edges": 1}, {"max_scanned_edges": 1}])
def test_budget_reports_truncation(budget):
    result = retrieve(NODES, EDGES, ["B"], direction="both", depth=3, **budget)
    assert result["truncated"] is True
    assert result["truncation_reasons"]
    assert len(result["nodes"]) <= budget.get("max_nodes", 500)
    assert len(result["edges"]) <= budget.get("max_edges", 1000)
    assert all(e["source"] in ids(result) and e["target"] in ids(result) for e in result["edges"])


@pytest.mark.parametrize("direction", ["incoming", "outgoing", "both"])
def test_real_langgraph_matches_direct(direction):
    options = {"depth": 2, "direction": direction, "edge_kinds": ("cites", "part_of")}
    assert retrieve_workflow(NODES, EDGES, ["B", "unknown"], **options) == retrieve(NODES, EDGES, ["B", "unknown"], **options)
