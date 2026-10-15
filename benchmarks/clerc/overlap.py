"""Measure CLERC (jhu-clsp/CLERC, HF hub) citation overlap with the starter graph.

Usage: python benchmarks/clerc/overlap.py generation/test.jsonl [generation/train.jsonl ...]
Files: https://huggingface.co/datasets/jhu-clsp/CLERC/resolve/main/generation/{test,train}.jsonl
Each record's ``citations`` is a list of [reporter_citation, cited_case_text].
Matching: normalized reporter citation (spaces/periods removed, lowercase)
against the trailing reporter citation in caselaw node ids ("Name, 732 P.2d 1172").
"""
import collections, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def norm(c):
    return re.sub(r"[\s.]", "", c).lower()


def graph_index():
    idx = {}
    for line in open(ROOT / "data/nodes.jsonl", encoding="utf-8"):
        n = json.loads(line)
        if n.get("type") != "caselaw":
            continue
        m = re.search(r",\s*(\d+\s+[A-Za-z][\w.\s']*?\s+\d+)\s*$", n["id"])
        if m:
            idx.setdefault(norm(m.group(1)), n["id"])
    return idx


def main(paths):
    idx = graph_index()
    out = {"graph_caselaw_with_reporter_cite": len(idx)}
    for p in paths:
        rows = [json.loads(l) for l in open(p, encoding="utf-8")]
        per, reporters = [], collections.Counter()
        for r in rows:
            cites = {c[0] for c in r["citations"]}
            reporters.update(re.sub(r"[\d\s]", "", c) for c in cites)
            per.append((sum(norm(c) in idx for c in cites), len(cites)))
        out[Path(p).name] = {
            "examples": len(rows), "unique_cites": sum(b for _, b in per),
            "mapped_cites": sum(a for a, _ in per),
            "examples_with_ge1_mapped": sum(a >= 1 for a, _ in per),
            "examples_with_ge2_mapped": sum(a >= 2 for a, _ in per),
            "top_reporters": reporters.most_common(8)}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
