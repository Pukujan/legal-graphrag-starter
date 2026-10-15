"""Self-built Colorado case-law citation retrieval set (CLERC / LePaRD style).

Source: CAP static bulk (https://static.case.law, CC0). Every Colorado-jurisdiction
case in Colo., Colo. App., Colo. N.P., Colo. L. Rep., P.2d and P.3d.

  fetch-meta / fetch-text   stream CasesMetadata.json + per-case JSON into DATA_ROOT/cap-co/
  build                     corpus, graph and frozen queries

Query construction
  * Citing opinion = a Colorado opinion (majority text only). Paragraphs = text lines.
  * eyecite finds full case citations; each is resolved to a Colorado CAP id through the
    reporter/public-domain citations CAP lists for every Colorado case.
  * One target per paragraph (deterministic choice). Every occurrence of the target is masked
    as [CITATION]: the full citation span (case name, cite, pin, court/year), parallel cites,
    short cites to the same volume/reporter and their antecedent name, and whole-word
    mentions of the target's distinctive party names.
  * direct   = the paragraph cites no other resolvable Colorado case (no seeds).
    indirect = the paragraph keeps >= 1 other resolvable Colorado case citation; those ids
               are the query's ``seeds`` (they are visible in the query text).
  * Temporal holdout: citing opinions decided >= 2015-01-01 -> test; 2008-2014 -> dev.
  * Leakage: ``held_out_opinion`` makes the harness drop every outgoing citation edge of
    every query opinion before the graph index is built; ``exclude`` drops the citing
    opinion from retrieval; ``date`` drops corpus docs and graph results decided after it.
Usage: python build_cap_citations.py build DATA_ROOT
"""
from __future__ import annotations

import glob
import hashlib
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
GENERIC = set("""people state colorado city county board department estate marriage matter
re in united states town district company co inc corp corporation bank association
industrial claim appeals office commission public utilities school the of and""".split())
MAX_TEST, MAX_DEV = 2000, 1000


def norm(cite: str) -> str:
    return re.sub(r"\s+", " ", cite.replace("P. 2d", "P.2d").replace("P. 3d", "P.3d")).strip()


def load_meta(root: Path):
    meta = {}
    for f in sorted(glob.glob(str(root / "cap-co/meta/*.jsonl"))):
        for line in open(f):
            m = json.loads(line)
            meta[m["cap_id"]] = m
    return meta


def load_text(root: Path):
    for f in sorted(glob.glob(str(root / "cap-co/text/*.jsonl"))):
        for line in open(f):
            yield json.loads(line)


def h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:12], 16)


TAIL = re.compile(r"(,\s*(?:¶¶?\s*)?[0-9][0-9\-–, n.&]*)?(\s*\([^()]{0,40}[0-9]{4}\))?")


def cite_span(c, cites):
    """Own span for a full cite: eyecite's full_span() overruns on parallel cites."""
    s, e = c.span()
    fs = c.full_span()[0]
    prev = max([x.span()[1] for x in cites if x.span()[1] <= s] + [0])
    start = max(fs, prev, s - 150)
    return start, e


NAMEISH = re.compile(r"((?:[A-Z][\w’'.\-]*|&|of|the|for|in|ex|rel\.|v\.|No\.|[0-9]+)[ ,]+)+$")
SIGNAL = re.compile(r"^(?:(?:See|see|also|Accord|accord|Cf\.|cf\.|But|but|e\.g\.,?|Compare|In|Id\.|citing|quoting|and|with)[ ,]+)+")
HISTORY = re.compile(r"(?:aff['’]d|rev['’]d|vacated|aff['’]g|rev['’]g|overruled|cert\. (?:granted|denied)|modified)"
                     r"(?: in part)?(?: on other grounds)?(?: by)?[,\s]*$")
RESIDUE = re.compile(r"^\s*,?\s*(?:[0-9]+\s+)?(?:P\.[23]d|Colo\.(?: App\.)?)\s+(?:at\s+)?[0-9][0-9,\- ]*")


def extend_name(par, start):
    """eyecite starts abbreviated captions late ("Wash. [Cnty. Bd. ...]"); walk back over
    caption-like tokens, then drop leading signal words. Stops at long words ending in '.'."""
    window = par[max(0, start - 120):start]
    m = NAMEISH.search(window)
    if not m:
        return start
    chunk = m.group(0)
    toks = chunk.split(" ")
    keep = []
    for t in reversed(toks):
        if t.endswith(".") and len(t.rstrip(".")) > 6 and keep:
            break
        keep.append(t)
    chunk = " ".join(reversed(keep))
    sig = SIGNAL.match(chunk)
    if sig:
        chunk = chunk[sig.end():]
    return start - len(chunk)


def mask_paragraph(par, cites, target_ids, resolve, target_cites=()):
    """Return masked text or None. cites = eyecite citations of par."""
    from eyecite.models import FullCaseCitation, ShortCaseCitation
    spans, names = [], set()
    vr = {(m.group(1), m.group(2)) for m in (re.match(r"(\d+) (.+) \d+$", norm(c)) for c in target_cites) if m}
    for c in cites:
        if isinstance(c, FullCaseCitation) and resolve(c) in target_ids:
            st, en = cite_span(c, cites)
            st = extend_name(par, st)
            if HISTORY.search(par[:st]):
                return None  # subsequent history of a same-caption case: the name gives it away
            m = TAIL.match(par, en)
            spans.append((st, m.end() if m else en))
            vr.add((c.groups.get("volume"), norm(c.groups.get("reporter") or "")))
            for nm in (c.metadata.plaintiff, c.metadata.defendant):
                for w in re.findall(r"[A-Z][A-Za-z'\-]{3,}", nm or ""):
                    if w.lower() not in GENERIC:
                        names.add(w)
    if not spans:
        return None
    # unresolved parallel cites glued to a target cite ("2016 COA 103, 411 P.3d 1068", "2013 COA 110M")
    changed = True
    while changed:
        changed = False
        for c in cites:
            if not isinstance(c, FullCaseCitation):
                continue
            cs, ce = c.span()
            for i, (s0, e0) in enumerate(spans):
                if cs < s0 and re.fullmatch(r"[A-Z]?[\s,;¶0-9\-]*", par[ce:s0]) and not (s0 > cs >= spans[i][0]):
                    spans[i] = (extend_name(par, max(c.full_span()[0], cs - 150, max([x.span()[1] for x in cites if x.span()[1] <= cs] + [0]))), e0)
                    changed = True
                    break
    for c in cites:
        if isinstance(c, ShortCaseCitation) and (c.groups.get("volume"), norm(c.groups.get("reporter") or "")) in vr:
            s, e = c.span()
            ant = c.metadata.antecedent_guess
            if ant:
                m = re.search(re.escape(ant) + r"[,\s]*$", par[:s])
                if m:
                    s = m.start()
            spans.append((s, e))
    spans.sort()
    out, last = [], 0
    for s, e in spans:
        if s < last:
            last = max(last, e)
            continue
        seg = par[last:s]
        if out and RESIDUE.match(seg):
            seg = seg[RESIDUE.match(seg).end():]
        out.append(seg)
        out.append("[CITATION]")
        last = e
    tail = par[last:]
    out.append(tail[RESIDUE.match(tail).end():] if RESIDUE.match(tail) else tail)
    text = "".join(out)
    for w in sorted(names, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(w)}\b", "[CASE]", text)
    return re.sub(r"(\[CITATION\][\s,;]*)+\[CITATION\]", "[CITATION]", text)


def build(root: Path):
    from eyecite import get_citations
    from eyecite.models import FullCaseCitation
    root = Path(root)
    meta = load_meta(root)
    by_cite = {}
    for cid, m in meta.items():
        for c in m["cites"]:
            by_cite.setdefault(norm(c), set()).add(cid)
    by_cite = {k: next(iter(v)) for k, v in by_cite.items() if len(v) == 1}
    out = root / "cap-co"
    # graph: CAP cites_to restricted to Colorado cases
    n_edges = 0
    with open(out / "nodes.jsonl", "w") as fn, open(out / "edges.jsonl", "w") as fe:
        for cid in sorted(meta):
            m = meta[cid]
            fn.write(json.dumps({"id": f"cap:{cid}", "type": "caselaw", "court": m["court"], "date": m["date"],
                                 "title": f"{m['name']}, {'; '.join(m['cites'])}"}) + "\n")
            for t in sorted({i for x in m["cites_to"] for i in x["ids"] if i in meta and i != cid}):
                fe.write(json.dumps({"source": f"cap:{cid}", "target": f"cap:{t}", "kind": "cites"}) + "\n")
                n_edges += 1
    cands = {"test": {"direct": [], "indirect": []}, "dev": {"direct": [], "indirect": []}}
    n_docs = 0
    with open(out / "corpus.jsonl", "w") as fc:
        for rec in load_text(root):
            cid = rec["cap_id"]
            m = meta.get(cid)
            if m is None or "err" in rec:
                continue
            ops = rec.get("ops") or []
            text = "\n".join(o["text"] for o in ops)
            fc.write(json.dumps({"id": f"cap:{cid}", "title": m["name"], "date": m["date"], "text": text},
                                ensure_ascii=False) + "\n")
            n_docs += 1
            split = "test" if m["date"] >= "2015-01-01" else "dev" if m["date"] >= "2008-01-01" else None
            if not split:
                continue
            maj = "\n".join(o["text"] for o in ops if o.get("type") == "majority") or (ops[0]["text"] if ops else "")
            for pi, par in enumerate(p for p in maj.split("\n") if len(p) >= 250):
                cites = get_citations(par)

                def resolve(c, _m=m):
                    r = by_cite.get(norm(c.corrected_citation()))
                    return r if r is not None and r != cid and meta[r]["date"] < _m["date"] else None
                full = [(c, resolve(c)) for c in cites if isinstance(c, FullCaseCitation)]
                ids = sorted({r for _, r in full if r is not None})
                if not ids:
                    continue
                tgt = ids[h(f"{cid}:{pi}") % len(ids)]
                q = mask_paragraph(par, cites, {tgt}, resolve, meta[tgt]["cites"])
                if q is None or len(q) < 200 or "[CITATION]" not in q:
                    continue
                others = [i for i in ids if i != tgt]
                variant = "indirect" if others else "direct"
                cands[split][variant].append({
                    "query_id": f"coc-{variant}-{cid}-{pi}", "split": split, "variant": variant,
                    "text": q, "gold": [f"cap:{tgt}"], "seeds": [f"cap:{i}" for i in others],
                    "date": m["date"], "held_out_opinion": f"cap:{cid}", "exclude": [f"cap:{cid}"],
                    "citing_case": f"{m['name']}, {'; '.join(m['cites'])}",
                    "gold_case": f"{meta[tgt]['name']}, {'; '.join(meta[tgt]['cites'])}",
                    "raw_paragraph": par})
    stats = {"corpus_docs": n_docs, "graph_nodes": len(meta), "graph_edges": n_edges}
    for variant in ("direct", "indirect"):
        rows = []
        for split, cap in (("test", MAX_TEST), ("dev", MAX_DEV)):
            pool = sorted(cands[split][variant], key=lambda q: h(q["query_id"]))
            stats[f"{split}_{variant}_candidates"] = len(pool)
            rows += sorted(pool[:cap], key=lambda q: q["query_id"])
            stats[f"{split}_{variant}"] = min(cap, len(pool))
        d = HERE / f"cap-{variant}"
        d.mkdir(exist_ok=True)
        with open(d / "queries.jsonl", "w", encoding="utf-8") as f:
            for q in rows:
                f.write(json.dumps({k: v for k, v in q.items() if k != "raw_paragraph"},
                                   ensure_ascii=False, sort_keys=True) + "\n")
        with open(out / f"raw-{variant}.jsonl", "w", encoding="utf-8") as f:
            for q in rows:
                f.write(json.dumps({"query_id": q["query_id"], "raw_paragraph": q["raw_paragraph"]},
                                   ensure_ascii=False) + "\n")
    print(json.dumps(stats, indent=2))
    (HERE / "cap-build-stats.json").write_text(json.dumps(stats, indent=2) + "\n")


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(Path(sys.argv[2]))
