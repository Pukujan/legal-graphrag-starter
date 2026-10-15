"""Colorado-to-Colorado citation edges with their citing paragraphs, from CAP static zips.

Input: a dir of https://static.case.law/{colo,colo-app}/<vol>.zip files. An edge is (citing case,
cited cite) from the case's ``cites_to`` where the cited reporter is Colorado (Colo., Colo. App.,
Colo. N.P., Colo. L. Rep.). Context = up to two opinion paragraphs that contain the cite string
(first occurrences), cut to 1,800 chars, with the cite marked as [[CITE]]...[[/CITE]].
Edges whose cite string never appears in the opinion text are counted and skipped.
Usage: python extract.py <zip_dir> <out.jsonl>
"""
from __future__ import annotations
import glob, json, re, sys, zipfile

CO = {"Colo.", "Colo. App.", "Colo. N.P.", "Colo. L. Rep."}


def main(zdir: str, out: str) -> int:
    n_edges = n_ctx = n_cases = 0
    with open(out, "w", encoding="utf-8") as f:
        for zp in sorted(glob.glob(f"{zdir}/*.zip")):
            z = zipfile.ZipFile(zp)
            for name in z.namelist():
                if not re.match(r"^json/[^/]+\.json$", name):
                    continue
                c = json.loads(z.read(name))
                n_cases += 1
                ops = c.get("casebody", {}).get("opinions", [])
                seen = set()
                for e in c.get("cites_to", []):
                    if e.get("reporter") not in CO or e["cite"] in seen:
                        continue
                    seen.add(e["cite"])
                    n_edges += 1
                    oi = e.get("opinion_index", 0)
                    op = ops[oi] if 0 <= oi < len(ops) else (ops[0] if ops else {})
                    paras = [p for p in op.get("text", "").split("\n") if e["cite"] in p][:2]
                    if not paras:
                        continue
                    ctx = "\n".join(paras)
                    i = ctx.find(e["cite"])
                    lo = max(0, i - 1200)
                    ctx = ctx[lo: lo + 1800].replace(e["cite"], f"[[CITE]]{e['cite']}[[/CITE]]", 1)
                    n_ctx += 1
                    f.write(json.dumps({"edge_id": f"{c['id']}->{e['cite']}", "src_cap_id": c["id"],
                                        "src": f"{c['name_abbreviation']}, {c['citations'][0]['cite']}",
                                        "src_date": c["decision_date"], "dst_cite": e["cite"],
                                        "dst_cap_ids": e.get("case_ids", []), "opinion_type": op.get("type"),
                                        "context": ctx}, ensure_ascii=True) + "\n")
    print(json.dumps({"cases": n_cases, "co_co_edges": n_edges, "edges_with_context": n_ctx}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
