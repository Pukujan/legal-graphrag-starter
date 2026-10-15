"""Stream CAP Colorado opinions into Octo ``legal_graph.work/expression/chunk``.

Input: ``--text`` JSONL (``{cap_id, head, ops:[{type,text}]}``, the opinion text the
CAP-Colorado benchmarks use) and ``--meta`` dir of ``colo-*.jsonl`` (``{cap_id, name,
date, court, cites, reporter, vol, file}``). Text is chunked like the CLERC store
(``recursive`` paragraph splits, 1000 chars, ``name | court | year`` header), but full
length (CLERC opinions were truncated to 12,000 chars).

Streams the text file line by line (meta held in memory, ~50 MB), inserts in batches
with ``ON CONFLICT DO NOTHING`` from a small thread pool, and checkpoints the line
number of the last fully-written window in ``--checkpoint`` so a rerun resumes.
Cases whose cap_id is already a work (CLERC overlap) are skipped. ``work.ext_ids``
carries cap_id, reporter, volume, file, the original (possibly partial) date, the CAP
static JSON URL (``link``) and the case.law HTML URL. Partial dates (``YYYY-MM``,
``YYYY``) are stored in ``decided_on`` as the first day of the period; ``ext_ids.date``
keeps the original. Needs ``octo_db_legal`` (write key) in the environment.

2026-10-15 run: 40,897 cases, 753,010 chunks, 0 CLERC overlaps, 162 s.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from legal_graph.engine.links import cap_html_url, cap_static_url  # noqa: E402
from legal_graph.octo_client import OctoSqlExecutor  # noqa: E402
from legal_graph.search.chunking import chunk_doc  # noqa: E402

WORK_SQL = """INSERT INTO legal_graph.work (work_id, node_type, court_id, decided_on, case_name, ext_ids)
SELECT w, 'node:case', c, d::date, t, x::jsonb
FROM jsonb_to_recordset($1::jsonb) AS r(w text, c text, d text, t text, x text)
ON CONFLICT (work_id) DO NOTHING"""
EXPR_SQL = """INSERT INTO legal_graph.expression (expr_id, work_id, opinion_type, content_sha256, text_ref)
SELECT e, w, 'combined', h, r FROM jsonb_to_recordset($1::jsonb) AS x(e text, w text, h text, r text)
ON CONFLICT (expr_id) DO NOTHING"""
CHUNK_SQL = """INSERT INTO legal_graph.chunk (chunk_id, expr_id, ord, body, embed_model)
SELECT i, e, o, b, 'none' FROM jsonb_to_recordset($1::jsonb) AS x(i text, e text, o int, b text)
ON CONFLICT (chunk_id) DO NOTHING"""
COURTS = {"Colo.": "Supreme Court of Colorado", "Colo. App.": "Colorado Court of Appeals",
          "Colo. App. Ct.": "Colorado Court of Appeals"}


def full_date(d: str) -> str | None:
    return {10: d, 7: d + "-01", 4: d + "-01-01"}.get(len(d))


def case_rows(rec: dict, meta: dict, size: int) -> tuple[dict, dict, list[dict]]:
    cap = str(rec["cap_id"])
    name = meta.get("name") or f"CAP {cap}"
    cite = (meta.get("cites") or [None])[0]
    work_id = f"{name}, {cite}" if cite else f"cap:{cap}"
    court = COURTS.get(meta.get("court"), meta.get("court"))
    text = "\n\n".join([rec.get("head") or ""] + [o.get("text") or "" for o in rec.get("ops") or []]).strip()
    chunks = chunk_doc(cap, text, {"title": name, "court": court, "date": meta.get("date")},
                       method="recursive", size=size, with_header=True)
    e = f"cap:{cap}:combined"
    ext = {"cap_id": cap, "reporter": meta.get("reporter"), "volume": meta.get("vol"), "file": meta.get("file"),
           "date": meta.get("date"), "cites": meta.get("cites"), "source": "cap-colorado",
           "link": cap_static_url(meta.get("reporter"), meta.get("vol"), meta.get("file")),
           "html": cap_html_url(meta.get("reporter"), meta.get("vol"), meta.get("file"))}
    work = {"w": work_id, "c": court, "d": full_date(meta.get("date") or ""), "t": name,
            "x": json.dumps(ext, ensure_ascii=True)}
    expr = {"e": e, "w": work_id, "h": hashlib.sha256(text.encode()).hexdigest(), "r": f"cap-colorado#{cap}"}
    return work, expr, [{"i": f"{e}:{c['seq']}", "e": e, "o": int(c["seq"]), "b": c["text"]} for c in chunks]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text", type=Path, required=True)
    ap.add_argument("--meta", type=Path, required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--cases-per-window", type=int, default=400)
    ap.add_argument("--batch-chars", type=int, default=250_000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--size", type=int, default=1000)
    a = ap.parse_args(argv)
    meta = {}
    for p in sorted(a.meta.glob("*.jsonl")):
        for line in open(p, encoding="utf-8"):
            m = json.loads(line)
            meta[str(m["cap_id"])] = m
    ex = OctoSqlExecutor(a.workspace)
    have = {r["cap"] for r in ex("SELECT ext_ids->>'cap_id' AS cap FROM legal_graph.work WHERE "
                                 "coalesce(ext_ids->>'source','') <> 'cap-colorado'", [], 100000)["rows"]}
    done = int(a.checkpoint.read_text()) if a.checkpoint.exists() else 0
    seen_ids: set[str] = set()
    pool = ThreadPoolExecutor(a.workers)
    stats = {"cases": 0, "chunks": 0, "skipped_overlap": 0, "skipped_nometa": 0}

    def batches(rows, key):
        cur, n = [], 0
        for r in rows:
            cur.append(r)
            n += len(r.get(key, "")) + 64
            if n >= a.batch_chars or len(cur) >= 500:
                yield cur
                cur, n = [], 0
        if cur:
            yield cur

    def flush(works, exprs, chunks):
        for sql, rows, key in ((WORK_SQL, works, "t"), (EXPR_SQL, exprs, "h"), (CHUNK_SQL, chunks, "b")):
            list(pool.map(lambda b: ex(sql, [json.dumps(b, ensure_ascii=True)], 1), batches(rows, key)))

    t0 = time.time()
    works, exprs, chunks = [], [], []
    lineno = 0
    with open(a.text, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            rec = json.loads(line)
            cap = str(rec["cap_id"])
            m = meta.get(cap)
            if lineno <= done:
                if m:
                    seen_ids.add(f"{m.get('name')}, {(m.get('cites') or [None])[0]}")
                continue
            if cap in have:
                stats["skipped_overlap"] += 1
                continue
            if not m:
                stats["skipped_nometa"] += 1
                continue
            w, e, cs = case_rows(rec, m, a.size)
            if w["w"] in seen_ids:  # duplicate name+cite: disambiguate by cap id
                w["w"] = e["w"] = f"{w['w']} [cap:{cap}]"
            seen_ids.add(w["w"])
            works.append(w)
            exprs.append(e)
            chunks.extend(cs)
            stats["cases"] += 1
            stats["chunks"] += len(cs)
            if len(works) >= a.cases_per_window:
                flush(works, exprs, chunks)
                a.checkpoint.write_text(str(lineno))
                works, exprs, chunks = [], [], []
                print(json.dumps({"line": lineno, **stats, "s": round(time.time() - t0)}), flush=True)
    if works:
        flush(works, exprs, chunks)
    a.checkpoint.write_text(str(lineno))
    r = ex("SELECT (SELECT count(*) FROM legal_graph.work) w, (SELECT count(*) FROM legal_graph.expression) e, "
           "(SELECT count(*) FROM legal_graph.chunk) c, (SELECT count(*) FROM legal_graph.work "
           "WHERE ext_ids->>'source' = 'cap-colorado') co", [], 1)
    print(json.dumps({"done": True, **stats, "octo": r["rows"][0], "s": round(time.time() - t0)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
