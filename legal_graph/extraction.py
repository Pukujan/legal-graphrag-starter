"""Span validation, exact authority linking, and lazy GLiNER2 integration."""

from __future__ import annotations

import unicodedata
from typing import Any


def _normal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def validate_span(text: str, span: dict[str, Any]) -> dict[str, Any]:
    """Validate exact source offsets with an exclusive end offset."""
    if not isinstance(text, str) or not isinstance(span, dict):
        raise ValueError("text and span must be supplied")
    start, end, value = span.get("start"), span.get("end"), span.get("text")
    if type(start) is not int or type(end) is not int or not isinstance(value, str):
        raise ValueError("span needs integer start/end and string text")
    if start < 0 or end <= start or end > len(text) or text[start:end] != value:
        raise ValueError("span text must exactly match the source offsets")
    return {**span}


def link_authority(mention: str, authority_nodes: Any) -> str | None:
    """Resolve only one exact normalized authority ID or title match."""
    if not isinstance(mention, str) or not mention.strip():
        return None
    query = _normal(mention)
    matches: set[str] = set()
    for node in authority_nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            continue
        node_id = node["id"]
        if _normal(node_id) == query or (isinstance(node.get("title"), str) and _normal(node["title"]) == query):
            matches.add(node_id)
    return next(iter(matches)) if len(matches) == 1 else None


class ExtractionUnavailableError(RuntimeError):
    """The optional local extractor could not be loaded or run."""


class GLiNER2Extractor:
    """Lazy official GLiNER2 AutoExtractor wrapper; no weights load at import."""

    def __init__(self, model_id: str = "fastino/gliner2.5-base-v1", *, revision: str | None = None):
        self.model_id, self.revision = model_id, revision
        self._extractor = None

    def _load(self):
        if self._extractor is None:
            try:
                from gliner2 import GLiNER2  # noqa: F401 - availability check for official package
                from gliner2 import AutoExtractor
                self._extractor = AutoExtractor.from_pretrained(self.model_id, revision=self.revision,
                                                                map_location="cpu")
            except Exception as exc:
                raise ExtractionUnavailableError(
                    f"Could not load local GLiNER2 model {self.model_id!r}: {type(exc).__name__}: {exc}"
                ) from exc
        return self._extractor

    def extract(self, text: str, *, schema: Any) -> Any:
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        try:
            result = self._load().extract(text, schema=schema, include_confidence=True, include_spans=True)
        except ExtractionUnavailableError:
            raise
        except Exception as exc:
            raise ExtractionUnavailableError(f"GLiNER2 extraction failed: {type(exc).__name__}: {exc}") from exc
        return result

    def extract_entities(self, text: str, entity_types: list[str]) -> Any:
        """Extract labeled character spans through GLiNER2's documented API."""
        if not isinstance(text, str) or not isinstance(entity_types, list) or not entity_types:
            raise ValueError("text and a non-empty entity_types list are required")
        if any(not isinstance(label, str) or not label for label in entity_types):
            raise ValueError("entity_types must contain non-empty strings")
        try:
            return self._load().extract_entities(text, entity_types, include_confidence=True,
                                                 include_spans=True)
        except ExtractionUnavailableError:
            raise
        except Exception as exc:
            raise ExtractionUnavailableError(f"GLiNER2 extraction failed: {type(exc).__name__}: {exc}") from exc
