"""starter.load_graph reads the exported citation JSONL as UTF-8."""
import starter


def test_load_graph_reads_utf8_citation_export():
    g = starter.load_graph()
    assert g.number_of_nodes() == 119_770
    assert g.number_of_edges() == 160_793
    # nodes.jsonl contains U+201C/U+201D. cp1252 cannot decode that byte.
    assert any("\u201cMERS\u201d" in (g.nodes[n].get("title") or "") for n in g)
