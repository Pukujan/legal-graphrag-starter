"""Freeze the reglab/housing_qa Colorado slice (CC-BY-SA-4.0).

Inputs (download from https://huggingface.co/datasets/reglab/housing_qa):
  data/questions.json (unzipped), data/statutes.tsv (unzipped, 3.4 GB)
Outputs:
  benchmarks/colorado/housing_qa/queries.jsonl      192 Colorado questions, gold = statute idx
  <data-root>/housing_qa/corpus.jsonl               30,056 Colorado statute sections (not committed)
  <data-root>/housing_qa/nodes.jsonl, edges.jsonl   structure graph: part_of (section -> article,
                                                    article -> title) + cites (in-text C.R.S. cross refs)
Usage: python build_housing_qa.py QUESTIONS_JSON STATUTES_TSV DATA_ROOT
"""
import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
SEC = re.compile(r"§\s*([0-9]+(?:\.[0-9]+)?-[0-9]+(?:\.[0-9]+)?-[0-9]+(?:\.[0-9]+)?)")
REF = re.compile(r"\b([0-9]{1,2}(?:\.[0-9])?-[0-9]{1,3}(?:\.[0-9])?-[0-9]{3,4}(?:\.[0-9]+)?)\b")


def main(qpath, tsv, root):
    root = Path(root) / "housing_qa"
    root.mkdir(parents=True, exist_ok=True)
    csv.field_size_limit(10**9)
    docs = []
    for row in csv.DictReader(open(tsv, encoding="utf-8"), delimiter="\t"):
        if row["state"] != "colorado":
            continue
        m = SEC.search(row["citation"])
        docs.append({"id": f"hq:{row['idx']}", "title": row["citation"] + " " + row["path"].split("|")[-1],
                     "text": row["text"], "section": m.group(1) if m else None, "path": row["path"]})
    docs.sort(key=lambda d: int(d["id"][3:]))
    with open(root / "corpus.jsonl", "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps({k: d[k] for k in ("id", "title", "text")}, ensure_ascii=False) + "\n")
    by_sec = {d["section"]: d["id"] for d in docs if d["section"]}
    nodes, edges = {}, set()
    for d in docs:
        nodes[d["id"]] = {"id": d["id"], "type": "co_statute", "title": d["title"][:200]}
        parts = d["path"].split("|")
        parents = ["hqpath:" + "|".join(parts[1:i]) for i in range(len(parts) - 1, 2, -1)]
        child = d["id"]
        for p in parents:
            nodes.setdefault(p, {"id": p, "type": "statute_part", "title": p.split("|")[-1][:200]})
            edges.add((child, p, "part_of"))
            child = p
        for ref in set(REF.findall(d["text"])):
            tgt = by_sec.get(ref)
            if tgt and tgt != d["id"]:
                edges.add((d["id"], tgt, "cites"))
    with open(root / "nodes.jsonl", "w") as f:
        for n in sorted(nodes):
            f.write(json.dumps(nodes[n], ensure_ascii=False) + "\n")
    with open(root / "edges.jsonl", "w") as f:
        for s, t, k in sorted(edges):
            f.write(json.dumps({"source": s, "target": t, "kind": k}) + "\n")
    qs = [q for q in json.load(open(qpath)) if q["state"] == "Colorado"]
    ids = {d["id"] for d in docs}
    out = []
    for q in qs:
        gold = sorted({f"hq:{s['statute_idx']}" for s in q["statutes"]})
        assert all(g in ids for g in gold), q["idx"]
        out.append({"query_id": f"hqa-co-{q['idx']}", "text": q["question"], "gold": gold,
                    "answer": q["answer"], "gold_citations": [s["citation"] for s in q["statutes"]]})
    with open(HERE / "housing_qa" / "queries.jsonl", "w", encoding="utf-8") as f:
        for q in out:
            f.write(json.dumps(q, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({"corpus": len(docs), "nodes": len(nodes), "edges": len(edges),
                      "cites_edges": sum(1 for e in edges if e[2] == "cites"), "queries": len(out)}))


if __name__ == "__main__":
    main(*sys.argv[1:4])
