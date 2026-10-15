"""Draw the 100-item hand-check sample (50 direct + 50 indirect, test split, seed 41)
and flag automatic leak signals. Usage: python handcheck_sample.py DATA_ROOT > sample.txt"""
import glob
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
root = Path(sys.argv[1])
meta = {}
for f in glob.glob(str(root / "cap-co/meta/*.jsonl")):
    for line in open(f):
        m = json.loads(line)
        meta[f"cap:{m['cap_id']}"] = m
GEN = {"people", "state", "colorado", "city", "county", "board", "department", "estate", "marriage",
       "matter", "company", "bank", "inc", "corp", "united", "industrial", "claim", "appeals", "office"}
PRE = re.compile(r"((?:[A-Z][\w’'.\-]*\.?|&|of|v\.)\s+)\[CITATION\]")
for v in ("direct", "indirect"):
    raw = {json.loads(x)["query_id"]: json.loads(x)["raw_paragraph"] for x in open(root / f"cap-co/raw-{v}.jsonl")}
    qs = [json.loads(x) for x in open(HERE / f"cap-{v}/queries.jsonl")]
    qs = [q for q in qs if q["split"] == "test"]
    random.Random(41).shuffle(qs)
    for q in qs[:50]:
        g = meta[q["gold"][0]]
        r = raw[q["query_id"]]
        in_raw = any(c.replace(" ", "") in r.replace(" ", "") for c in g["cites"])
        words = [w for w in re.findall(r"[A-Z][A-Za-z'\-]{3,}", g["name"]) if w.lower() not in GEN]
        leak_name = [w for w in words if re.search(rf"\b{re.escape(w)}\b", q["text"])]
        leak_cite = [c for c in g["cites"] if c in q["text"]]
        pre = PRE.search(q["text"])
        print(f"### {v} {q['query_id']} | gold={g['name']} {g['cites']} | gold_in_raw={in_raw} "
              f"leak_cite={leak_cite} leak_name={leak_name} pre={pre.group(0) if pre else ''}")
        print("Q:", q["text"].replace("\n", " "))
        print()
