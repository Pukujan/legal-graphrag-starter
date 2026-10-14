"""Decision response validation and the OpenRouter Jev Decisions adapter."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any


class DecisionProviderError(RuntimeError):
    """A provider request failed or returned an unusable response."""


def _probability(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number in [0, 1]")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError(f"{label} must be a finite number in [0, 1]")
    return number


def parse_decision_response(raw: Mapping[str, Any], expected_questions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Validate a provider response without losing its raw provenance."""
    if not isinstance(raw, Mapping) or not isinstance(expected_questions, Mapping):
        raise ValueError("response and expected questions must be mappings")
    answers = raw.get("answers")
    if not isinstance(answers, Mapping) or set(answers) != set(expected_questions):
        raise ValueError("provider answers do not match the requested question IDs")
    normalized: dict[str, Any] = {}
    for question_id, spec in expected_questions.items():
        if not isinstance(question_id, str) or not isinstance(spec, Mapping):
            raise ValueError("invalid expected question specification")
        answer = answers[question_id]
        if not isinstance(answer, Mapping):
            raise ValueError(f"answer {question_id!r} must be an object")
        kind = spec.get("type")
        if kind == "noul":
            if not {"noul"} <= set(answer) or set(answer) - {"noul", "type"}:
                raise ValueError(f"answer {question_id!r} must contain one noul score")
            if "type" in answer and answer["type"] != "noul":
                raise ValueError(f"answer {question_id!r} has the wrong answer type")
            normalized[question_id] = {"kind": "noul", "value": _probability(answer["noul"], question_id)}
        elif kind == "choice":
            choices = spec.get("choices")
            if isinstance(choices, (str, bytes)) or not isinstance(choices, Sequence) or not choices:
                raise ValueError(f"choice question {question_id!r} has no valid choices")
            if any(not isinstance(item, str) for item in choices) or len(set(choices)) != len(choices):
                raise ValueError(f"choice question {question_id!r} has invalid choices")
            if not {"choice", "probabilities", "confidence"} <= set(answer) or set(answer) - {"choice", "probabilities", "confidence", "type"}:
                raise ValueError(f"answer {question_id!r} is incomplete")
            if "type" in answer and answer["type"] != "choice":
                raise ValueError(f"answer {question_id!r} has the wrong answer type")
            value = answer["choice"]
            if value not in choices:
                raise ValueError(f"answer {question_id!r} is outside its choice set")
            probabilities = answer["probabilities"]
            if not isinstance(probabilities, Mapping) or set(probabilities) != set(choices):
                raise ValueError(f"answer {question_id!r} must contain a distribution for every choice")
            distribution = {label: _probability(probabilities[label], f"{question_id}.{label}") for label in choices}
            if abs(sum(distribution.values()) - 1.0) > 1e-6:
                raise ValueError(f"answer {question_id!r} probabilities must sum to one")
            confidence = _probability(answer["confidence"], f"{question_id}.confidence")
            normalized[question_id] = {"kind": "choice", "value": value,
                                       "probabilities": distribution, "confidence": confidence}
        else:
            raise ValueError(f"unsupported answer type for {question_id!r}")
    return {"answers": normalized, "raw": copy.deepcopy(dict(raw)),
            "model": raw.get("model"), "provider": raw.get("provider"),
            "request_id": raw.get("id")}


def cache_key(record: Any) -> str:
    """Hash the complete versioned request record using canonical JSON."""
    try:
        canonical = json.dumps(record, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("cache record must be JSON-compatible and contain no NaN or infinity") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def filter_annotations(annotations: Sequence[Mapping[str, Any]], *, minimum_support: float = 0.8,
                       allowed_assertions: Sequence[str] = ("asserted",)) -> list[Mapping[str, Any]]:
    """Return a policy view; original rows and their judgments remain intact."""
    if isinstance(minimum_support, bool) or not isinstance(minimum_support, (int, float)):
        raise ValueError("minimum_support must be in [0, 1]")
    threshold = float(minimum_support)
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("minimum_support must be in [0, 1]")
    if isinstance(allowed_assertions, (str, bytes)) or not isinstance(allowed_assertions, Sequence):
        raise ValueError("allowed_assertions must be a sequence")
    kept = []
    for annotation in annotations:
        if not isinstance(annotation, Mapping):
            continue
        decision = annotation.get("decision")
        if not isinstance(decision, Mapping):
            continue
        answers = decision.get("answers")
        if not isinstance(answers, Mapping):
            continue
        support, assertion = answers.get("support"), answers.get("assertion")
        if not isinstance(support, Mapping) or not isinstance(assertion, Mapping):
            continue
        value = support.get("value")
        label = assertion.get("value")
        if (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
                and value >= threshold and label in allowed_assertions):
            relationship = answers.get("relationship")
            if "relationship" in answers:
                predicate = annotation.get("predicate")
                relationship_value = relationship.get("value") if isinstance(relationship, Mapping) else None
                if (not isinstance(relationship_value, str)
                        or relationship_value not in {"cites", "distinguishes", "follows", "overrules"}
                        or relationship_value != predicate):
                    continue
            kept.append(annotation)
    return kept


class OpenRouterJevProvider:
    """Minimal client for OpenRouter's Jev Decisions endpoint."""

    endpoint = "https://openrouter.ai/api/alpha/decisions"

    def __init__(self, api_key: str | None = None, timeout: float = 30.0, *, opener=None):
        self.api_key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY")
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    def decide(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required for Jev decisions")
        if not isinstance(request, Mapping) or not all(key in request for key in ("model", "state", "questions")):
            raise ValueError("Jev request must include model, state, and questions")
        body = json.dumps(dict(request), ensure_ascii=False, allow_nan=False).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=body, method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}",
                                              "Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status < 200 or status >= 300:
                    raise DecisionProviderError(f"Jev endpoint returned HTTP {status}")
                raw = json.loads(response.read().decode("utf-8"))
        except DecisionProviderError:
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
                UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DecisionProviderError(f"Jev request failed: {type(exc).__name__}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("answers"), dict):
            raise DecisionProviderError("Jev returned no answer object")
        return raw
