#!/usr/bin/env python3
"""GraphRAG starter — Colorado legal citation graph.

Loads the exported citation graph (nodes.jsonl + edges.jsonl) into a NetworkX
DiGraph and demonstrates the traversals a GraphRAG retriever is built from.
Everything here runs offline on the data export — no server, no credentials.

    pip install networkx
    python starter.py

Graph model
-----------
* nodes  = legal authorities (cases, statutes, regs, rules, forms)
    {id, type, court, date, title, url}
* edges  = directed relationships
    kind="cites"   : src opinion/section cites dst authority   (the citation graph)
    kind="part_of" : src section is part of dst parent statute (structure)
"""
import json
from pathlib import Path

import networkx as nx

DATA = Path(__file__).parent / "data"


def load_graph() -> nx.DiGraph:
    g = nx.DiGraph()
    for line in (DATA / "nodes.jsonl").read_text(encoding="utf-8").splitlines():
        n = json.loads(line)
        g.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
    for line in (DATA / "edges.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        g.add_edge(e["src"], e["dst"], kind=e["kind"])
    return g


def cites(g, node):
    """Authorities this document relies on (outgoing 'cites')."""
    return [d for _, d, k in g.out_edges(node, data="kind") if k == "cites"]


def cited_by(g, node):
    """Later documents that cite this authority (incoming 'cites')."""
    return [s for s, _, k in g.in_edges(node, data="kind") if k == "cites"]


def graphrag_expand(g, seeds, hops=1):
    """STARTER STUB for a GraphRAG retriever.

    Given seed authorities (e.g. the top hits from the existing hybrid search),
    expand along the citation graph to pull the authorities they rely on and the
    later cases that cite them. A real implementation would then re-rank the
    expanded set and feed it to the answer synthesizer.
    """
    frontier, out = set(seeds), set(seeds)
    for _ in range(hops):
        nxt = set()
        for n in frontier:
            if n in g:
                nxt.update(cites(g, n))
                nxt.update(cited_by(g, n))
        nxt -= out
        out |= nxt
        frontier = nxt
    return out


def cites_subgraph(g) -> nx.DiGraph:
    """Just the citation edges — the graph GraphRAG actually traverses
    ('part_of' structural edges are kept separate)."""
    return g.edge_subgraph([(u, v) for u, v, k in g.edges(data="kind")
                            if k == "cites"]).copy()


def main():
    g = load_graph()
    print(f"loaded: {g.number_of_nodes():,} nodes  {g.number_of_edges():,} edges\n")

    # 1) authority ranking — most-CITED authorities (citation in-degree only)
    print("== most-cited authorities (citation in-degree) ==")
    citerank = sorted(((len(cited_by(g, n)), n) for n in g), reverse=True)[:8]
    for d, n in citerank:
        print(f"  {d:>4}  {n}")

    # 2) a concrete traversal: pick the top-cited authority, show who relies on it
    anchor = citerank[0][1]
    users = cited_by(g, anchor)
    print(f"\n== {len(users)} documents cite {anchor!r}; first 5 ==")
    for u in users[:5]:
        print(f"  - {u}  [{g.nodes[u].get('type','?')}]")

    # 3) PageRank on the citation subgraph — 'authoritativeness' beyond raw
    #    counts (the knowledge-graph payoff). Needs numpy+scipy.
    try:
        cg = cites_subgraph(g)
        pr = nx.pagerank(cg, alpha=0.85)
        print("\n== top authorities by PageRank (citation subgraph) ==")
        for n, s in sorted(pr.items(), key=lambda x: -x[1])[:8]:
            print(f"  {s:.5f}  {n}")
    except ModuleNotFoundError:
        print("\n(skipping PageRank — `pip install numpy scipy` to enable)")

    # 4) GraphRAG expansion demo from a seed hit
    seeds = [anchor]
    expanded = graphrag_expand(g, seeds, hops=1)
    print(f"\n== GraphRAG 1-hop expansion from {seeds[0]!r}: "
          f"{len(seeds)} seed -> {len(expanded)} authorities ==")


if __name__ == "__main__":
    main()
