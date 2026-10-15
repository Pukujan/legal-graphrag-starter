"""Offline Jev tests: recorded Decisions responses replayed by request hash; no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks" / "treatment"))
from legal_graph import benchmark_diff as bd  # noqa: E402
from legal_graph.jev import (FixtureTransport, JevClient, JevError, JevScorer, average_choice,  # noqa: E402
                             choice_questions, noul)

FIX = ROOT / "tests" / "fixtures" / "jev_recorded.jsonl"
CASES = [json.loads(l) for l in open(FIX)]


@pytest.fixture
def client():
    return JevClient(transport=FixtureTransport(FIX), retries=0)


def test_checker_replays_recorded_answers(client):
    items = [json.loads(l) for l in open(ROOT / "benchmarks" / "answer_check" / "eval_set.jsonl")]
    s = JevScorer(client)
    probs = s.score([(items[i]["source"], items[i]["claim"]) for i in range(4)])
    assert all(0 <= p <= 1 for p in probs)
    labels = [items[i]["label"] for i in range(4)]
    assert [p >= 0.5 for p in probs] == [bool(l) for l in labels]
    assert client.calls == 4 and client.cost > 0
    assert s.model_version.startswith("typesafe/jev-1.14")


def test_treatment_request_is_stable(client):
    from label_jev import LABELS, request
    for c in [c for c in CASES if c["case"] == "treatment"]:
        state, qs = request(c["edge"])
        r = client.decide(state, qs)
        p = average_choice(r["answers"], "treatment", list(LABELS))
        assert abs(sum(p.values()) - 1) < 0.05


def test_unrecorded_request_never_hits_network(client):
    with pytest.raises(JevError):
        client.decide({"x": 1}, {"q": {"type": "noul", "instructions": "?"}})


def test_choice_permutation_and_average():
    qs = choice_questions("t", "pick", {"a": None, "b": None, "c": None})
    assert list(qs["t_p0"]["criteria"]) == ["a", "b", "c"] and list(qs["t_p1"]["criteria"]) == ["c", "b", "a"]
    ans = {"t_p0": {"type": "choice", "probabilities": {"a": 0.8, "b": 0.2, "c": 0}},
           "t_p1": {"type": "choice", "probabilities": {"a": 0.4, "b": 0.6, "c": 0}}}
    assert average_choice(ans, "t", ["a", "b", "c"]) == pytest.approx({"a": 0.6, "b": 0.4, "c": 0.0})
    with pytest.raises(JevError):
        noul({"x": {"type": "choice"}}, "x")


def test_rerank_response_shape():
    r = [c for c in CASES if c["case"] == "rerank"][0]["response"]
    keys = sorted(k[2:] for k in r["answers"] if k.startswith("n_"))
    sys.path.insert(0, str(ROOT / "benchmarks" / "clerc" / "text"))
    from rerank_jev import jev_scores
    s, n, ch = jev_scores(r, keys)
    assert len(s) == len(keys) == 20 and all(0 <= x <= 1 for x in s)


def test_benchmark_diff():
    a = {str(i): {"m": 1.0 if i < 8 else 0.0} for i in range(10)}
    b = {str(i): {"m": 1.0 if i < 2 else 0.0} for i in range(10)}
    r = bd.diff(a, b, "m")
    assert r["win_tie_loss"] == [6, 4, 0] and r["mcnemar_b"] == 6 and r["mcnemar_c"] == 0
    assert r["mcnemar_p"] == pytest.approx(2 / 64) and r["permutation_p"] < 0.1
    assert r["only_a_right"] == ["2", "3", "4", "5", "6"] and r["only_b_right"] == []
    assert bd.mcnemar_exact(0, 0) == 1.0


def test_benchmark_diff_cli(tmp_path):
    pa, pb = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    pa.write_text("".join(json.dumps({"id": i, "MRR": 1 / (i + 1)}) + "\n" for i in range(5)))
    pb.write_text("".join(json.dumps({"id": i, "MRR": 0.2}) + "\n" for i in range(5)))
    out = tmp_path / "d.json"
    assert bd.main([str(pa), str(pb), "--metric", "MRR", "--json", str(out)]) == 0
    assert json.loads(out.read_text())["metrics"]["MRR"]["n"] == 5
