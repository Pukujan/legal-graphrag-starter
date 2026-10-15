"""Answer-support checking for POST /api/v1/check (wraps legal_graph.answer_check)."""
from __future__ import annotations

import os
from typing import Any, Callable

from legal_graph.answer_check import CitationChecker, Claim
from legal_graph import jev as _jev

from .models import CHECK_SCHEMA_VERSION, CheckRequest

# Decisions-API model (legal_graph.jev, PR #45). "typesafe/jev-router" is a chat router that the
# Decisions API rejects, so it is never the default.
JEV_MODEL_DEFAULT = _jev.DEFAULT_MODEL  # "typesafe/jev-1.14"


class JevScorer(_jev.JevScorer):
    """/check SupportScorer and rerank relevance scorer over the Jev Decisions API.

    ``score`` (support) is inherited from ``legal_graph.jev.JevScorer``: mean of a Noul and an
    order-averaged Choice. ``relevance`` asks the same two-question form about query relevance.
    """

    REL_INSTR = ("Is this passage from a court opinion relevant to answering the legal question? It is relevant "
                 "only if it states law or facts that bear directly on the question.")
    REL_OPTIONS = {"relevant": "The passage bears directly on answering the question.",
                   "not_relevant": "The passage does not help answer the question."}

    def __init__(self, model: str | None = None, client: _jev.JevClient | None = None):
        super().__init__(client or _jev.JevClient(model=model or os.environ.get("JEV_MODEL", JEV_MODEL_DEFAULT),
                                                  transport=_jev.HttpTransport(timeout=float(os.environ.get("JEV_TIMEOUT", "30"))),
                                                  retries=int(os.environ.get("JEV_RETRIES", "2"))))

    def relevance(self, query: str, passage: str) -> float:
        qs = {"relevance": {"type": "noul", "instructions": self.REL_INSTR,
                            "criteria": {"true": self.REL_OPTIONS["relevant"], "false": self.REL_OPTIONS["not_relevant"]}},
              **_jev.choice_questions("rel", self.REL_INSTR, self.REL_OPTIONS)}
        r = self.client.decide({"question": query, "passage": passage}, qs)
        n = _jev.noul(r["answers"], "relevance")
        c = _jev.average_choice(r["answers"], "rel", list(self.REL_OPTIONS))["relevant"]
        return (n + c) / 2


class ScorerRegistry:
    """Lazily constructs and caches the heavy model scorers."""

    def __init__(self, factories: dict[str, Callable[[], Any]] | None = None):
        self._factories = factories or {
            "minicheck": lambda: __import__("legal_graph.answer_check", fromlist=["x"]).MiniCheckScorer(),
            "hhem": lambda: __import__("legal_graph.answer_check", fromlist=["x"]).HHEMScorer(),
            "jev": JevScorer,
        }
        self._cache: dict[str, Any] = {}

    def available(self, name: str) -> bool:
        if name == "jev" and self._factories.get("jev") is JevScorer:
            return bool(os.environ.get("OPENROUTER_API_KEY"))
        return name in self._factories

    def get(self, name: str):
        if name not in self._cache:
            self._cache[name] = self._factories[name]()
        return self._cache[name]


def run_check(req: CheckRequest, registry: ScorerRegistry, chunk_text: Callable[[str], str | None]) -> dict[str, Any]:
    quotes = []
    for i, c in enumerate(req.claims):
        if c.chunk_id is None:
            quotes.append({"claim_index": i, "chunk_id": None, "quote_verified": None})
            continue
        body = chunk_text(c.chunk_id)
        quotes.append({"claim_index": i, "chunk_id": c.chunk_id, "chunk_found": body is not None,
                       "quote_verified": bool(body is not None and c.cited_text in body),
                       "char_start": body.find(c.cited_text) if body and c.cited_text in body else None})
    checker = CitationChecker(registry.get(req.scorer), threshold=req.threshold, temperature=req.temperature)
    result = checker.check([Claim(c.text, c.cited_text, c.citation) for c in req.claims]).to_dict()
    unverified = [q["claim_index"] for q in quotes if q["quote_verified"] is False]
    result.update(schema_version_engine=CHECK_SCHEMA_VERSION, quotes=quotes,
                  abstain=bool(result["abstain"] or unverified), unverified_quotes=unverified)
    return result
