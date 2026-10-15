"""Executable synthetic extraction-to-decision-to-GraphRAG fixture.

The default fixture is deterministic and offline. ``--provider jev`` sends the
same explicitly synthetic passage to OpenRouter's Jev Decisions endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from typing import Any

import networkx as nx

from .annotations import build_annotation_overlay, retrieve_annotated
from .decisions import OpenRouterJevProvider, cache_key, filter_annotations, parse_decision_response
from .extraction import GLiNER2Extractor, link_authority, validate_span


ASSERTIONS = ["asserted", "negated", "hypothetical", "forward_looking", "unclear"]
RELATIONSHIPS = ["cites", "distinguishes", "follows", "overrules", "none", "insufficient", "unclear"]
QUESTIONS = {
    "support": {"type": "noul"},
    "assertion": {"type": "choice", "choices": ASSERTIONS},
    "relationship": {"type": "choice", "choices": RELATIONSHIPS},
}


def build_jev_request(passage: str, candidate_span: dict[str, Any], *, predicate: str,
                      subject_id: str | None, object_id: str | None,
                      model: str = "typesafe/jev-1.13-20260917") -> dict[str, Any]:
    """Build the keyed Jev request for one source-grounded candidate span."""
    span = validate_span(passage, candidate_span)
    if not isinstance(predicate, str) or not predicate:
        raise ValueError("predicate must be a non-empty string")
    for name, value in (("subject_id", subject_id), ("object_id", object_id)):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{name} must be a string or None")
    if not isinstance(model, str) or not model:
        raise ValueError("model must be a non-empty string")
    return {"model": model,
            "state": {"passage": passage,
                      "source_hash": hashlib.sha256(passage.encode("utf-8")).hexdigest(),
                      "candidate_span": span,
                      "subject_id": subject_id, "object_id": object_id,
                      "predicate": predicate},
            "questions": {
                "support": {"type": "noul", "instructions":
                            "Estimate whether this passage supports the stated predicate for this candidate."},
                "assertion": {"type": "choice", "criteria":
                              {label: label.replace("_", " ") for label in ASSERTIONS},
                              "instructions": "Classify the passage assertion status."},
                "relationship": {"type": "choice", "criteria":
                                 {label: label.replace("_", " ") for label in RELATIONSHIPS},
                                 "instructions": "Choose the relationship supported by this passage, or none/insufficient/unclear."},
            }}


def run_demo(*, provider: str = "fixture", run_extractor: bool = False) -> dict[str, Any]:
    passage = "SYNTHETIC FIXTURE: Case A follows Case B for the stated rule."
    candidate = "follows Case B"
    start = passage.index(candidate)
    span = validate_span(passage, {"start": start, "end": start + len(candidate), "text": candidate})
    authorities = [{"id": "synthetic:case-a", "title": "Case A"},
                   {"id": "synthetic:case-b", "title": "Case B"}]
    subject_id = link_authority("Case A", authorities)
    object_id = link_authority("Case B", authorities)
    extraction: dict[str, Any] = {"provider": "manual-synthetic-candidate-fixture", "span": span,
                                  "subject_id": subject_id, "object_id": object_id}
    if run_extractor:
        extractor = GLiNER2Extractor(revision="54785d51df8d86a0d8f2eb212fec15871aad2c4d")
        extracted = extractor.extract_entities(passage, ["case authority"])
        extraction["gliner2_model"] = extractor.model_id
        extraction["gliner2_revision"] = extractor.revision
        extraction["gliner2"] = extracted
        mentions = extracted.get("entities", {}).get("case authority", []) if isinstance(extracted, dict) else []
        linked = []
        for mention in mentions:
            if not isinstance(mention, dict):
                continue
            mention_span = validate_span(passage, {"start": mention.get("start"),
                                                   "end": mention.get("end"), "text": mention.get("text")})
            authority_id = link_authority(mention_span["text"], authorities)
            if authority_id is not None:
                linked.append((mention_span, authority_id))
        linked.sort(key=lambda item: item[0]["start"])
        if len(linked) < 2 or linked[0][1] == linked[1][1]:
            raise ValueError("GLiNER2 did not return two distinct, exactly linked authority spans")
        span = validate_span(passage, {"start": linked[0][0]["end"], "end": linked[1][0]["start"],
                                       "text": passage[linked[0][0]["end"]:linked[1][0]["start"]]})
        subject_id, object_id = linked[0][1], linked[1][1]
        extraction.update({"provider": "gliner2", "span": span,
                           "authority_mentions": [{"span": row, "authority_id": authority}
                                                  for row, authority in linked[:2]],
                           "subject_id": subject_id, "object_id": object_id})

    request = build_jev_request(passage, span, predicate="follows",
                                subject_id=subject_id, object_id=object_id)
    started = time.perf_counter()
    if provider == "fixture":
        raw = {"id": "synthetic-fixture-response", "model": "synthetic-fixture-v1",
               "provider": "offline-fixture", "usage": {"input_tokens": 0, "output_tokens": 0, "cost": 0.0},
               "answers": {"support": {"noul": 0.9},
                           "assertion": {"choice": "asserted", "confidence": 0.9,
                                         "probabilities": {"asserted": 0.9, "negated": 0.025,
                                                           "hypothetical": 0.025, "forward_looking": 0.025,
                                                           "unclear": 0.025}},
                           "relationship": {"choice": "follows", "confidence": 0.9,
                                            "probabilities": {"cites": 0.02, "distinguishes": 0.02,
                                                              "follows": 0.9, "overrules": 0.02,
                                                              "none": 0.02, "insufficient": 0.02,
                                                              "unclear": 0.0}}}}
    elif provider == "jev":
        raw = OpenRouterJevProvider().decide(request)
    else:
        raise ValueError("provider must be fixture or jev")
    elapsed_seconds = time.perf_counter() - started
    decision = parse_decision_response(raw, QUESTIONS)
    source_hash = hashlib.sha256(passage.encode("utf-8")).hexdigest()
    record = {"source_hash": source_hash, "start": span["start"], "end": span["end"],
              "candidate_text": span["text"], "subject_id": subject_id, "object_id": object_id,
              "predicate": "follows", "prompt_version": "decision-demo-v1",
              "schema_version": "decision-schema-v1", "parser_version": "decision-parser-v1",
              "provider": decision["provider"], "model": decision["model"]}
    annotation = {"id": "synthetic-candidate-1", "source_id": "synthetic-passage-1",
                  "subject_id": subject_id, "object_id": object_id, "predicate": "follows",
                  "source_hash": source_hash, "span": span, "decision": decision, "synthetic": True}
    original = nx.MultiDiGraph()
    original.add_node(subject_id, title="Case A", authority_type="case", synthetic=True)
    original.add_node(object_id, title="Case B", authority_type="case", synthetic=True)
    citation_only = retrieve_annotated(original, [subject_id], hops=1, direction="outgoing")
    overlay = build_annotation_overlay(original, [annotation])
    gated_records = filter_annotations([annotation])
    gated_overlay = build_annotation_overlay(original, gated_records)
    return {
        "label": "SYNTHETIC DEMONSTRATION; not a real opinion or legal benchmark",
        "extraction": extraction,
        "request": request,
        "cache_key": cache_key(record),
        "run_metadata": {"provider_mode": provider, "provider": decision["provider"],
                         "model": decision["model"], "request_id": decision["request_id"],
                         "usage": raw.get("usage"), "elapsed_seconds": elapsed_seconds,
                         "python": platform.python_version(), "model_revision": raw.get("model_revision")},
        "raw_citation_graph": {"nodes": list(original.nodes(data=True)),
                                "edges": list(original.edges(keys=True, data=True))},
        "raw_candidate": annotation,
        "views": {"citation_only": sorted(citation_only),
                  "decision_filtered": sorted(retrieve_annotated(gated_overlay, [subject_id], hops=1,
                                                                   direction="outgoing")),
                  "annotated_filter_at_retrieval": sorted(retrieve_annotated(overlay, [subject_id], hops=1,
                                                                               direction="outgoing"))},
        "annotation_node_count": sum(1 for _, attrs in overlay.nodes(data=True)
                                     if attrs.get("kind") == "annotation"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("fixture", "jev"), default="fixture")
    parser.add_argument("--run-gliner", action="store_true", help="attempt pinned local GLiNER2 extraction")
    args = parser.parse_args(argv)
    result = run_demo(provider=args.provider, run_extractor=args.run_gliner)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
