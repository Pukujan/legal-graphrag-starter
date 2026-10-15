from legal_graph import graph_stage
from legal_graph.graph_scoring import GraphScorer, fuse_linear, rank_scores


def _sc():
    #  a - b - c ;  x - y  (two components)
    return GraphScorer(["a", "b", "c", "x", "y"], [("a", "b"), ("b", "c"), ("x", "y")])


def test_ppr_is_query_dependent():
    sc = _sc()
    g = sc.ppr({"a": 1.0}, alpha=0.3)
    assert g[sc.pos["b"]] > 0 and g[sc.pos["x"]] == 0 and g[sc.pos["y"]] == 0
    g2 = sc.ppr({"x": 1.0}, alpha=0.3)
    assert g2[sc.pos["y"]] > 0 and g2[sc.pos["b"]] == 0


def test_keep_mask_blocks_walks_through_future_nodes():
    sc = _sc()
    keep = sc.keep_mask(sc.dates_array({"a": "2000", "b": "2030", "c": "2000"}), "2010")
    g = sc.ppr({"a": 1.0}, keep=keep)
    assert g[sc.pos["b"]] == 0 and g[sc.pos["c"]] == 0


def test_neighbor_weights_by_seed_score():
    sc = GraphScorer(["s1", "s2", "n1", "n2"], [("s1", "n1"), ("s2", "n2")])
    g = sc.neighbor({"s1": 3.0, "s2": 1.0})
    assert g[sc.pos["n1"]] > g[sc.pos["n2"]] > 0


def test_fuse_linear():
    sc = _sc()
    text = {"a": 5.0, "x": 4.0}
    g = sc.ppr({"a": 1.0})
    assert fuse_linear(text, g, sc, lam=0.0, allowed=lambda d: True, k=5) == ["a", "x"]
    out = fuse_linear(text, g, sc, lam=1.0, allowed=lambda d: True, k=5, text_of=lambda d: {"b": 4.5}.get(d, 0.0))
    assert out.index("b") < out.index("x")
    assert "b" not in fuse_linear(text, g, sc, lam=1.0, allowed=lambda d: d != "b", k=5)
    assert rank_scores(["p", "q"]) == {"p": 1 / 61, "q": 1 / 62}


def test_jev_rerank_reorders_top_k_and_replays_cache(tmp_path):
    corpus = [{"id": f"d{i}", "title": f"Case {i}", "text": "t " * 10} for i in range(3)]
    q = {"query_id": "q", "text": "passage [CITATION]"}

    def fake(body):
        cands = body["state"]["candidates"]
        ans = {}
        for k, v in body["questions"].items():
            if k.startswith("n_"):
                ans[k] = {"type": "noul", "noul": 0.9 if cands[k[2:]]["title"] == "Case 2" else 0.1}
            else:
                ans[k] = {"type": "choice", "probabilities": {o: (0.8 if t == "Case 2" else 0.05)
                                                              for o, t in v["criteria"].items()}}
        return 200, {"answers": ans, "usage": {"cost": 0.0}}

    sysdef = {"name": "j", "params": {"k": 3}}
    tr = graph_stage.CachingTransport(tmp_path / "c.jsonl", inner=fake)
    out = graph_stage.run_jev_rerank(sysdef, [q], {"q": ["d0", "d1", "d2"]}, corpus, 10, transport=tr)
    assert out["q"][0] == "d2" and tr.misses == 1
    tr2 = graph_stage.CachingTransport(tmp_path / "c.jsonl", inner=lambda b: (500, {}))
    assert graph_stage.run_jev_rerank(sysdef, [q], {"q": ["d0", "d1", "d2"]}, corpus, 10, transport=tr2) == out
    assert tr2.hits == 1
