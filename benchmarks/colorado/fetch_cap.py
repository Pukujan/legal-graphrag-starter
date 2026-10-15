"""Stream Colorado-jurisdiction cases from CAP static bulk (CC0) into DATA_ROOT/cap-co/.

  meta/<reporter>-<vol>.jsonl  id, name, date, court, cites, cites_to (CasesMetadata.json)
  text/part-*.jsonl            head matter + opinions per case (cases/<file>.json)
Reporters: colo, colo-app, colo-n-p, colo-l-rep, p2d, p3d (Colorado jurisdiction only).
Resumable: finished volumes / cases are skipped. Usage: python fetch_cap.py meta|text DATA_ROOT
"""
import concurrent.futures as cf
import glob
import json
import os
import re
import sys
import urllib.request as U

H = {"User-Agent": "curl/8"}
REPORTERS = ["colo", "colo-app", "colo-n-p", "colo-l-rep", "p2d", "p3d"]


def get(url):
    for a in range(4):
        try:
            return U.urlopen(U.Request(url, headers=H), timeout=120).read()
        except Exception:
            if a == 3:
                raise


def meta(root):
    os.makedirs(f"{root}/cap-co/meta", exist_ok=True)

    def vols(r):
        return sorted({int(v) for v in re.findall(rf"static.case.law/{r}/(\d+)/'", get(f"https://static.case.law/{r}/").decode())})

    def one(rv):
        r, v = rv
        out = f"{root}/cap-co/meta/{r}-{v}.jsonl"
        if os.path.exists(out):
            return
        d = json.loads(get(f"https://static.case.law/{r}/{v}/CasesMetadata.json"))
        with open(out + ".tmp", "w") as f:
            for c in d:
                if c.get("jurisdiction", {}).get("name") != "Colo.":
                    continue
                f.write(json.dumps({"cap_id": c["id"], "name": c.get("name_abbreviation"), "date": c.get("decision_date"),
                                    "court": c["court"]["name_abbreviation"], "cites": [x["cite"] for x in c.get("citations", [])],
                                    "reporter": r, "vol": v, "file": c.get("file_name"),
                                    "cites_to": [{"cite": t.get("cite"), "ids": t.get("case_ids") or [], "pin": t.get("pin_cites")}
                                                 for t in c.get("cites_to") or []]}) + "\n")
        os.rename(out + ".tmp", out)
    with cf.ThreadPoolExecutor(24) as ex:
        list(ex.map(one, [(r, v) for r in REPORTERS for v in vols(r)]))


def text(root):
    os.makedirs(f"{root}/cap-co/text", exist_ok=True)
    ms = [json.loads(x) for f in sorted(glob.glob(f"{root}/cap-co/meta/*.jsonl")) for x in open(f)]
    done = {json.loads(x)["cap_id"] for f in glob.glob(f"{root}/cap-co/text/*.jsonl") for x in open(f)}
    todo = [m for m in ms if m["cap_id"] not in done]

    def one(m):
        try:
            d = json.loads(get(f"https://static.case.law/{m['reporter']}/{m['vol']}/cases/{m['file']}.json"))
        except Exception as e:
            return {"cap_id": m["cap_id"], "err": str(e)}
        cb = d.get("casebody") or {}
        return {"cap_id": m["cap_id"], "head": cb.get("head_matter", ""),
                "ops": [{"type": o.get("type"), "text": o.get("text", "")} for o in cb.get("opinions", [])]}
    with open(f"{root}/cap-co/text/part-{len(glob.glob(f'{root}/cap-co/text/*.jsonl')):03d}.jsonl", "a") as out, \
            cf.ThreadPoolExecutor(32) as ex:
        for i, r in enumerate(ex.map(one, todo)):
            out.write(json.dumps(r) + "\n")
            if i % 2000 == 0:
                out.flush()


if __name__ == "__main__":
    {"meta": meta, "text": text}[sys.argv[1]](sys.argv[2])
