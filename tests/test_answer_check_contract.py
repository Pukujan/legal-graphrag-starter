import json
from pathlib import Path

import pytest

from legal_graph.answer_check import (Claim, CitationChecker, apply_temperature, auroc,
                                      expected_calibration_error, fit_temperature)


class FakeScorer:
    model, model_version = "fake", "0"

    def __init__(self, probs):
        self.probs = probs

    def score(self, pairs):
        return self.probs[: len(pairs)]


def test_check_emits_storage_schema_and_abstains_on_weak_claim():
    claims = [Claim("A held X.", "X.", "1 Colo. 1"), Claim("B held Y.", "Z.", "2 Colo. 2")]
    out = CitationChecker(FakeScorer([0.9, 0.2]), threshold=0.5).check(claims).to_dict()
    c = out["claims"][0]
    for key in ("raw_prob", "calibrated_prob", "threshold", "accepted", "decided_at", "decider", "model_version"):
        assert key in c
    assert [x["accepted"] for x in out["claims"]] == [True, False]
    assert out["abstain"] is True and out["answer_confidence"] == pytest.approx(0.2)


def test_empty_answer_rejected():
    with pytest.raises(ValueError):
        CitationChecker(FakeScorer([])).check([])


def test_temperature_fit_shrinks_overconfidence():
    probs = [0.99, 0.99, 0.01, 0.01] * 10
    labels = [1, 0, 0, 1] * 10  # 50% right at 99% confidence
    t = fit_temperature(probs, labels)
    assert t > 5
    cal = [apply_temperature(p, t) for p in probs]
    assert expected_calibration_error(cal, labels) < expected_calibration_error(probs, labels)


def test_auroc_bounds():
    assert auroc([0.9, 0.8, 0.1], [1, 1, 0]) == 1.0
    assert auroc([0.1, 0.9], [1, 0]) == 0.0


def test_eval_set_is_labelled_and_large_enough():
    rows = [json.loads(l) for l in open(Path(__file__).parents[1] / "benchmarks/answer_check/eval_set.jsonl")]
    assert len(rows) >= 200
    assert {r["kind"] for r in rows} == {"supported", "neg", "wrong_holding", "wrong_case"}
    assert all(r["label"] == (r["kind"] == "supported") for r in rows)


def test_conformal_threshold_caps_false_accepts():
    from legal_graph.answer_check import conformal_threshold
    probs = [i / 100 for i in range(100)]
    labels = [0] * 100
    t = conformal_threshold(probs, labels, alpha=0.1)
    assert sum(p > t for p in probs) <= 10
