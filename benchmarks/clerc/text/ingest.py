"""Stream CLERC's collection.doc.tsv.gz and keep opinion text only for cases in the graph.

The 8.2 GB file is read record by record (never loaded whole). Output is written in
shards of SHARD records each; ``state.json`` records how many input records are done,
so an interrupted run resumes after the last finished shard.

    python benchmarks/clerc/text/ingest.py --collection collection.doc.tsv.gz \
        --nodes data-expanded/nodes.jsonl --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
from pathlib import Path

SHARD = 100_000


def graph_cap_ids(nodes_path: Path, query_files: list[Path] = ()) -> dict[str, dict]:
    """CAP id -> node metadata. Nodes carry ``cap_id``; a few pre-existing starter nodes
    do not, so gold CAP ids from the frozen queries are added as well."""
    keep, by_id = {}, {}
    for line in open(nodes_path, encoding="utf-8"):
        x = json.loads(line)
        if x.get("cap_id") is not None:
            keep[str(x["cap_id"])] = {"id": x["id"], "title": x.get("title") or "", "court": x.get("court") or "",
                                      "date": x.get("date") or ""}
        by_id[x["id"]] = x
    for qf in query_files:
        for line in open(qf, encoding="utf-8"):
            q = json.loads(line)
            if q.get("gold") and str(q.get("gold_cap_id")) not in keep and q["gold"][0] in by_id:
                x = by_id[q["gold"][0]]
                keep[str(q["gold_cap_id"])] = {"id": x["id"], "title": x.get("title") or "",
                                               "court": x.get("court") or "", "date": x.get("date") or ""}
    return keep


def records(path: Path):
    csv.field_size_limit(sys.maxsize)
    with gzip.open(path, "rb") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        for row in csv.reader(text, delimiter="\t"):
            if len(row) >= 2:
                yield row[0], row[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collection", type=Path, required=True)
    ap.add_argument("--nodes", type=Path, required=True)
    ap.add_argument("--queries", type=Path, nargs="*", default=[], help="frozen queries.jsonl files")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    state_p = a.out / "state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {"records_done": 0, "kept": 0, "shards": []}
    if state.get("finished"):
        print("already finished", state)
        return 0
    keep = graph_cap_ids(a.nodes, a.queries)
    print(f"graph cases with cap_id: {len(keep)}", flush=True)
    done, n, buf = state["records_done"], 0, []

    def flush():
        nonlocal buf
        shard = a.out / f"part-{len(state['shards']):04d}.jsonl.gz"
        with gzip.open(shard.with_suffix(".tmp"), "wt", encoding="utf-8") as f:
            for r in buf:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        shard.with_suffix(".tmp").rename(shard)
        state["shards"].append(shard.name)
        state["records_done"] = n
        state["kept"] += len(buf)
        state_p.write_text(json.dumps(state))
        print(f"records {n} kept {state['kept']}", flush=True)
        buf = []

    for did, txt in records(a.collection):
        n += 1
        if n <= done:
            continue
        if did in keep:
            buf.append({"cap_id": did, **keep[did], "text": txt})
        if n % SHARD == 0:
            flush()
    flush()
    state["finished"] = True
    state_p.write_text(json.dumps(state))
    print("finished", {k: v for k, v in state.items() if k != "shards"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
