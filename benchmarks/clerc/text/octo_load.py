"""Load chunks for a set of cases into Octo ``legal_graph.work/expression/chunk`` (migration 004).

Only a documented subset is loaded (the gold and seed cases of the frozen CLERC
queries, i.e. the #25 set T):
the full chunk store is millions of rows and lives on disk. Rows are inserted in
batches through ``jsonb_to_recordset`` with ``ON CONFLICT DO NOTHING``, so a rerun
resumes. ``embedding`` stays NULL (``embed_model = 'none'``): the schema column is
vector(1024) and the CPU benchmark used a 384-d model only on candidate pools.
Needs ``octo_db_legal`` in the environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from legal_graph.octo_client import OctoSqlExecutor  # noqa: E402

WORK_SQL = """INSERT INTO legal_graph.work (work_id, node_type, court_id, decided_on, case_name, ext_ids)
SELECT w, 'node:case', c, CASE WHEN d ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN d::date END, t, jsonb_build_object('cap_id', cap)
FROM jsonb_to_recordset($1::jsonb) AS x(w text, c text, d text, t text, cap text)
ON CONFLICT (work_id) DO NOTHING"""
EXPR_SQL = """INSERT INTO legal_graph.expression (expr_id, work_id, opinion_type, content_sha256, text_ref)
SELECT e, w, 'combined', h, r FROM jsonb_to_recordset($1::jsonb) AS x(e text, w text, h text, r text)
ON CONFLICT (expr_id) DO NOTHING"""
CHUNK_SQL = """INSERT INTO legal_graph.chunk (chunk_id, expr_id, ord, body, embed_model)
SELECT i, e, o, b, 'none' FROM jsonb_to_recordset($1::jsonb) AS x(i text, e text, o int, b text)
ON CONFLICT (chunk_id) DO NOTHING"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chunks", type=Path, required=True)
    ap.add_argument("--queries", type=Path, nargs="+", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--batch", type=int, default=150)
    a = ap.parse_args(argv)
    want = set()
    for f in a.queries:
        for q in map(json.loads, open(f, encoding="utf-8")):
            want.update(q.get("gold") or [])
            want.update(q.get("seeds") or [])
    docs = json.loads((a.chunks / "docs.json").read_text())
    off, cdoc, cseq = (np.load(a.chunks / f) for f in ("offsets.npy", "chunk_doc.npy", "chunk_seq.npy"))
    keep = {i for i, d in enumerate(docs) if d["id"] in want}
    ex = OctoSqlExecutor(a.workspace)
    by_doc: dict[int, list[int]] = {}
    for c in np.flatnonzero(np.isin(cdoc, np.fromiter(keep, dtype=np.int32))):
        by_doc.setdefault(int(cdoc[c]), []).append(int(c))
    f = open(a.chunks / "texts.bin", "rb")

    def text(c):
        f.seek(int(off[c]))
        return f.read(int(off[c + 1] - off[c])).decode("utf-8", "replace")

    works, exprs, chunks = [], [], []
    for di, cs in sorted(by_doc.items()):
        d = docs[di]
        bodies = [text(c) for c in cs]
        e = f"cap:{d['cap_id']}:combined"
        works.append({"w": d["id"], "c": d["court"], "d": d["date"], "t": d["title"], "cap": d["cap_id"]})
        exprs.append({"e": e, "w": d["id"], "h": hashlib.sha256("\n".join(bodies).encode()).hexdigest(),
                      "r": f"clerc:collection.doc.tsv.gz#{d['cap_id']}"})
        chunks += [{"i": f"{e}:{int(cseq[c])}", "e": e, "o": int(cseq[c]), "b": b} for c, b in zip(cs, bodies)]
    for sql, rows in ((WORK_SQL, works), (EXPR_SQL, exprs), (CHUNK_SQL, chunks)):
        for i in range(0, len(rows), a.batch):
            ex(sql, [json.dumps(rows[i:i + a.batch], ensure_ascii=True)], 1)
            if (i // a.batch) % 20 == 0:
                print(sql.split()[2], i, "/", len(rows), flush=True)
    r = ex("SELECT (SELECT count(*) FROM legal_graph.work) w, (SELECT count(*) FROM legal_graph.expression) e, "
           "(SELECT count(*) FROM legal_graph.chunk) c", [], 1)
    print("octo counts", r["rows"][0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
