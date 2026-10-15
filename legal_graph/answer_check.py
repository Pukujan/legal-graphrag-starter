"""Answer-time citation and confidence checker.

An answer is split into claims; each claim carries the source text it cites. A
``SupportScorer`` returns P(claim is supported by its cited text). Scores are
calibrated with a single temperature (Guo et al., 2017) and compared with an
abstain threshold. The result maps one-to-one onto the ``assertion``/decision
columns proposed by the data-model track (raw_prob, calibrated_prob, threshold,
accepted, decided_at, decider, model_version), matching the merged
``assertion`` table (PR #38).

Model-backed scorers import ``torch``/``transformers`` lazily so the core module
and its contract tests stay dependency-light and offline.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Protocol, Sequence

SCORE_SCHEMA_VERSION = "answer-check/1"


@dataclass(frozen=True)
class Claim:
    text: str
    cited_text: str
    citation: str = ""  # e.g. "200 Colo. 1"; free text, not resolved here


class SupportScorer(Protocol):
    model: str
    model_version: str

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """Return P(supported) for each (cited_text, claim) pair."""


@dataclass
class ClaimScore:
    claim_index: int
    citation: str
    raw_prob: float
    calibrated_prob: float
    threshold: float
    accepted: bool
    decider: str
    model_version: str
    temperature: float
    decided_at: str
    schema_version: str = SCORE_SCHEMA_VERSION


@dataclass
class AnswerScore:
    claims: list[ClaimScore]
    answer_confidence: float  # min calibrated claim score (weakest link)
    threshold: float
    abstain: bool
    decider: str
    model_version: str
    decided_at: str
    latency_ms: float
    schema_version: str = SCORE_SCHEMA_VERSION
    aggregation: str = "min"

    def to_dict(self) -> dict:
        return asdict(self)


def _logit(p: float, eps: float = 1e-6) -> float:
    p = min(max(p, eps), 1 - eps)
    return math.log(p / (1 - p))


def apply_temperature(p: float, temperature: float) -> float:
    return 1.0 / (1.0 + math.exp(-_logit(p) / temperature))


def fit_temperature(probs: Sequence[float], labels: Sequence[int]) -> float:
    """Single-parameter temperature minimising NLL (golden-section search on log T)."""
    z = [_logit(p) for p in probs]

    def nll(log_t: float) -> float:
        t = math.exp(log_t)
        total = 0.0
        for zi, y in zip(z, labels):
            q = min(max(1.0 / (1.0 + math.exp(-zi / t)), 1e-12), 1 - 1e-12)
            total -= y * math.log(q) + (1 - y) * math.log(1 - q)
        return total / max(len(z), 1)

    lo, hi = math.log(0.05), math.log(20.0)
    g = (math.sqrt(5) - 1) / 2
    a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    for _ in range(80):
        if nll(a) < nll(b):
            hi = b
        else:
            lo = a
        a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    return math.exp((lo + hi) / 2)


def expected_calibration_error(probs: Sequence[float], labels: Sequence[int], bins: int = 10) -> float:
    n = len(probs)
    ece = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probs) if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if idx:
            conf = sum(probs[i] for i in idx) / len(idx)
            acc = sum(labels[i] for i in idx) / len(idx)
            ece += len(idx) / n * abs(conf - acc)
    return ece


def conformal_threshold(probs: Sequence[float], labels: Sequence[int], alpha: float = 0.05) -> float:
    """Split-conformal abstain threshold: accept a claim only if its score exceeds the
    ceil((n+1)(1-alpha))/n quantile of scores on known-unsupported calibration claims, so at most
    ~alpha of unsupported claims are accepted (marginal guarantee). Same rule MAPIE uses for
    one-class risk control; implemented inline to avoid a dependency."""
    neg = sorted(p for p, y in zip(probs, labels) if y == 0)
    if not neg:
        raise ValueError("need unsupported calibration claims")
    k = min(len(neg), math.ceil((len(neg) + 1) * (1 - alpha)))
    return neg[k - 1]


def bootstrap_ci(metric, probs, labels, n: int = 1000, seed: int = 0) -> tuple[float, float]:
    import random
    rng = random.Random(seed)
    m = len(probs)
    vals = []
    for _ in range(n):
        idx = [rng.randrange(m) for _ in range(m)]
        vals.append(metric([probs[i] for i in idx], [labels[i] for i in idx]))
    vals.sort()
    return vals[int(0.025 * n)], vals[int(0.975 * n) - 1]


def auroc(probs: Sequence[float], labels: Sequence[int]) -> float:
    pos = [p for p, y in zip(probs, labels) if y == 1]
    neg = [p for p, y in zip(probs, labels) if y == 0]
    if not pos or not neg:
        return float("nan")
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


@dataclass
class CitationChecker:
    scorer: SupportScorer
    threshold: float = 0.5
    temperature: float = 1.0
    _clock: object = field(default=None, repr=False)

    def check(self, claims: Sequence[Claim]) -> AnswerScore:
        if not claims:
            raise ValueError("answer has no claims to check")
        now = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()
        raw = self.scorer.score([(c.cited_text, c.text) for c in claims])
        latency = (time.perf_counter() - t0) * 1000
        scores = []
        for i, (c, p) in enumerate(zip(claims, raw)):
            cal = apply_temperature(p, self.temperature)
            scores.append(ClaimScore(i, c.citation, float(p), cal, self.threshold, cal >= self.threshold,
                                     self.scorer.model, self.scorer.model_version, self.temperature, now))
        conf = min(s.calibrated_prob for s in scores)
        return AnswerScore(scores, conf, self.threshold, conf < self.threshold,
                           self.scorer.model, self.scorer.model_version, now, latency)


class MiniCheckScorer:
    """MiniCheck (Tang et al., 2024) sequence-classification variants, e.g. RoBERTa-Large."""

    def __init__(self, model: str = "lytang/MiniCheck-RoBERTa-Large", revision: str = "main", max_len: int = 512):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.model, self.model_version, self._torch, self.max_len = model, revision, torch, max_len
        self._tok = AutoTokenizer.from_pretrained(model, revision=revision)
        self._m = AutoModelForSequenceClassification.from_pretrained(model, revision=revision).eval()

    def score(self, pairs):
        out = []
        with self._torch.no_grad():
            for doc, claim in pairs:
                enc = self._tok(doc, claim, truncation="only_first", max_length=self.max_len, return_tensors="pt")
                out.append(float(self._m(**enc).logits.softmax(-1)[0, 1]))
        return out


class HHEMScorer:
    """Vectara HHEM-2.1-Open (flan-t5-base backbone, trust_remote_code)."""

    def __init__(self, model: str = "vectara/hallucination_evaluation_model", revision: str = "main"):
        import torch
        from transformers import AutoModelForSequenceClassification

        self.model, self.model_version, self._torch = model, revision, torch
        self._m = AutoModelForSequenceClassification.from_pretrained(model, revision=revision, trust_remote_code=True).eval()

    def score(self, pairs):
        with self._torch.no_grad():
            return [float(self._m.predict([p])[0]) for p in pairs]
