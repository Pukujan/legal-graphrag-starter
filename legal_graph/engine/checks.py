"""Answer-support checking for POST /api/v1/check (wraps legal_graph.answer_check)."""
from __future__ import annotations

import os
from typing import Any, Callable

from legal_graph.answer_check import CitationChecker, Claim
from legal_graph.decisions import OpenRouterJevProvider, parse_decision_response

from .models import CHECK_SCHEMA_VERSION, CheckRequest

JEV_MODEL_DEFAULT = "typesafe/jev-router"
_SUPPORT_Q = {"support": {"type": "noul", "instructions":
                          "Estimate the probability that the passage fully supports the claim."}}


class JevScorer:
    """SupportScorer over OpenRouter Jev Decisions (one noul 'support' question per pair)."""

    def __init__(self, model: str | None = None, provider: OpenRouterJevProvider | None = None):
        self.model = model or os.environ.get("JEV_MODEL", JEV_MODEL_DEFAULT)
        self.model_version = self.model
        self._provider = provider or OpenRouterJevProvider()

    def _ask(self, state: dict[str, Any], instructions: str) -> float:
        q = {"support": {**_SUPPORT_Q["support"], "instructions": instructions}}
        raw = self._provider.decide({"model": self.model, "state": state, "questions": q})
        return parse_decision_response(raw, q)["answers"]["support"]["value"]

    def score(self, pairs):
        return [self._ask({"passage": doc, "claim": claim}, _SUPPORT_Q["support"]["instructions"]) for doc, claim in pairs]

    def relevance(self, query: str, passage: str) -> float:
        return self._ask({"question": query, "passage": passage},
                         "Estimate the probability that this passage is relevant to answering the question.")


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
