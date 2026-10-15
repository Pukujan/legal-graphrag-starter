"""Build the federal/SCOTUS CAP extension graph and frozen CLERC queries.

Sources (public, no keys):
  * Harvard CAP static bulk metadata: https://static.case.law/<reporter>/<vol>/CasesMetadata.json
    (case id, name, court, date, reporter citations, cites_to -> CAP case ids).
    Reporters: us, f2d, f3d, f-supp, f-supp-2d, f-appx.
  * CLERC test split (jhu-clsp/CLERC, HF hub): queries/test.single-removed.{indirect,direct}.tsv
    and qrels/qrels-doc.test.{indirect,direct}.tsv. CLERC document ids ARE CAP case ids.

Stages (uv run --python 3.12 --with eyecite python benchmarks/clerc/build_graph.py <stage>):
  fetch   stream CasesMetadata.json per volume -> compact JSONL parts (only needed fields kept)
  build   seeds = eyecite full case citations in each query, resolved to CAP ids by reporter cite;
          T = CLERC gold docs + resolved seeds; C = cases citing >= 2 members of T (co-citers);
          nodes = T | C; edges = CAP cites_to edges among nodes with at least one endpoint in T.
          Cases already in the starter graph (same reporter cite) reuse the starter node id.
          Writes data-expanded/{nodes,edges}.jsonl (starter + federal; gitignored) and the
          frozen benchmarks/clerc/{indirect,direct}/queries.jsonl.
Leakage control: CLERC does not name the query's source opinion, so every node that cites the
gold AND all resolved seeds (a possible source) has its edge to the gold listed in
``holdout_edges``; the benchmark removes those edges before indexing (conservative).
"""
from __future__ import annotations

import concurrent.futures as cf
import glob
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("CAP_WORK", "/workspace/cap"))
CLERC = Path(os.environ.get("CLERC_DIR", "/workspace/clerc"))
REPORTERS = ["us", "f2d", "f3d", "f-supp", "f-supp-2d", "f-appx"]
UA = {"User-Agent": "legal-graphrag-starter/0.1"}


def norm(cite: str) -> str:
    return re.sub(r"[\s.]", "", cite).lower()


def _get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()


def fetch():
    parts = WORK / "parts"
    parts.mkdir(parents=True, exist_ok=True)

    def vol(job):
        r, v = job
        out = parts / f"{r}-{v}.jsonl"
        if out.exists():
            return
        data = json.loads(_get(f"https://static.case.law/{r}/{v}/CasesMetadata.json"))
        tmp = out.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for c in data:
                court = c.get("court") or {}
                f.write(json.dumps({
                    "cap_id": c["id"], "name": c.get("name_abbreviation"), "date": c.get("decision_date"),
                    "court": court.get("name"), "court_abbr": court.get("name_abbreviation"),
                    "cites": [x["cite"] for x in c.get("citations", [])], "reporter": r,
                    "cites_to": sorted({i for t in c.get("cites_to") or [] for i in t.get("case_ids") or []}),
                }, ensure_ascii=False) + "\n")
        tmp.rename(out)

    jobs = []
    for r in REPORTERS:
        html = _get(f"https://static.case.law/{r}/").decode()
        jobs += [(r, int(v)) for v in sorted(set(re.findall(rf"static.case.law/{r}/(\d+)/'", html)))]
    with cf.ThreadPoolExecutor(24) as ex:
        list(ex.map(vol, jobs))
    print("volumes", len(jobs))


def _tsv(path):
    for line in open(path, encoding="utf-8"):
        yield line.rstrip("\n").split("\t", 1)


def build():
    from eyecite import get_citations
    from eyecite.models import FullCaseCitation

    cases, by_cite = {}, {}
    for p in sorted(glob.glob(str(WORK / "parts/*.jsonl"))):
        for line in open(p, encoding="utf-8"):
            c = json.loads(line)
            cases[c["cap_id"]] = c
            for x in c["cites"]:
                by_cite.setdefault(norm(x), c["cap_id"])

    queries = {}
    for split in ("indirect", "direct"):
        qrels = {q: int(d) for q, _, d, _ in (l.split() for l in open(CLERC / f"qrels-doc.test.{split}.tsv"))}
        for q, text in _tsv(CLERC / f"test.single-removed.{split}.tsv"):
            seeds = []
            for c in get_citations(text):
                if isinstance(c, FullCaseCitation):
                    g = c.groups
                    cap = by_cite.get(norm(f"{g.get('volume')} {g.get('reporter')} {g.get('page')}"))
                    if cap is not None and cap not in seeds:
                        seeds.append(cap)
            queries[(split, q)] = {"gold": qrels[q], "seeds": seeds}

    T = {v["gold"] for v in queries.values() if v["gold"] in cases}
    T |= {s for v in queries.values() for s in v["seeds"]}
    C = {i for i, c in cases.items() if sum(t in T for t in c["cites_to"]) >= 2}
    N = T | C
    E = sorted({(i, t) for i in N for t in cases[i]["cites_to"] if t in N and t != i and (i in T or t in T)})

    starter = [json.loads(l) for l in open(ROOT / "data/nodes.jsonl", encoding="utf-8")]
    starter_by_cite = {}
    for n in starter:
        m = re.search(r",\s*(\d+\s+[A-Za-z][\w.\s']*?\s+\d+)\s*$", n["id"]) if n.get("type") == "caselaw" else None
        if m:
            starter_by_cite.setdefault(norm(m.group(1)), n["id"])

    node_id, new_nodes, used, reused = {}, [], {n["id"] for n in starter}, 0
    for cap in sorted(N):
        c = cases[cap]
        hit = next((starter_by_cite[norm(x)] for x in c["cites"] if norm(x) in starter_by_cite), None)
        if hit:
            node_id[cap] = hit
            reused += 1
            continue
        nid = f"{c['name']}, {c['cites'][0]}" if c["cites"] else f"{c['name']} [CAP {cap}]"
        if nid in used:
            nid = f"{nid} [CAP {cap}]"
        used.add(nid)
        node_id[cap] = nid
        new_nodes.append({"id": nid, "type": "caselaw", "court": c["court"] or "", "date": c["date"] or "",
                          "title": c["name"] or "", "url": "", "cap_id": cap, "citations": c["cites"],
                          "reporter": c["reporter"], "source": "CAP static.case.law"})
    new_edges = [{"src": node_id[a], "dst": node_id[b], "kind": "cites", "source": "CAP cites_to"} for a, b in E]

    out = ROOT / "data-expanded"
    out.mkdir(exist_ok=True)
    for name, base, extra in (("nodes", "data/nodes.jsonl", new_nodes), ("edges", "data/edges.jsonl", new_edges)):
        with open(out / f"{name}.jsonl", "w", encoding="utf-8") as f:
            f.write(open(ROOT / base, encoding="utf-8").read())
            for r in extra:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    cites_to = {i: set(cases[i]["cites_to"]) for i in N}
    for split in ("indirect", "direct"):
        rows = []
        for (s, q), v in sorted(queries.items(), key=lambda kv: (kv[0][0], int(kv[0][1]))):
            if s != split:
                continue
            g, seeds = v["gold"], [x for x in v["seeds"] if x in node_id]
            holdout = []
            if g in node_id and seeds:
                holdout = sorted([node_id[x], node_id[g], "cites"] for x in N
                                 if g in cites_to[x] and all(sd in cites_to[x] for sd in seeds))
            rows.append({"query_id": f"clerc-{split}-{q}", "seeds": [node_id[x] for x in seeds],
                         "gold": [node_id[g]] if g in node_id else [], "gold_cap_id": g,
                         "gold_in_graph": g in node_id, "holdout_edges": holdout, "label": "clerc"})
        d = ROOT / "benchmarks/clerc" / split
        d.mkdir(parents=True, exist_ok=True)
        (d / "queries.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

    manifest = {
        "sources": {"cap": "https://static.case.law/{reporter}/{vol}/CasesMetadata.json", "reporters": REPORTERS,
                    "clerc": "https://huggingface.co/datasets/jhu-clsp/CLERC (test single-removed, doc qrels)"},
        "cap_cases_scanned": len(cases), "T_gold_and_seed_cases": len(T), "C_cociters_not_in_T": len(C - T),
        "federal_nodes_total": len(N), "federal_nodes_reused_from_starter": reused,
        "nodes_added": len(new_nodes), "edges_added": len(new_edges),
        "expanded_nodes_sha256": hashlib.sha256((out / "nodes.jsonl").read_bytes()).hexdigest(),
        "expanded_edges_sha256": hashlib.sha256((out / "edges.jsonl").read_bytes()).hexdigest(),
    }
    (ROOT / "benchmarks/clerc/graph-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    {"fetch": fetch, "build": build}[sys.argv[1]]()
