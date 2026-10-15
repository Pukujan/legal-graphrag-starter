"""Paragraph-chunk the ingested opinion text, one header line per chunk.

Splits on paragraph breaks first, then lines, sentences and words (``recursive_chunks``
from ``legal_graph.search.chunking``), packing pieces up to ``--size`` characters. Each
chunk starts with ``case name | court | year``. Output (in ``--out``):

  texts.bin     UTF-8 chunk texts, concatenated
  offsets.npy   int64 byte offsets into texts.bin (n_chunks + 1)
  chunk_doc.npy int32 index into docs.json for each chunk
  chunk_seq.npy int32 position of the chunk within its document
  docs.json     list of {cap_id, id, title, court, date, n_chars}
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from legal_graph.search.chunking import chunk_doc  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--size", type=int, default=1000)
    ap.add_argument("--max-chars", type=int, default=0, help="truncate each opinion first (0 = full text)")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    state = json.loads((a.text_dir / "state.json").read_text())
    docs, offsets, cdoc, cseq, pos = [], [0], [], [], 0
    with open(a.out / "texts.bin.tmp", "wb") as tf:
        for shard in state["shards"]:
            for line in gzip.open(a.text_dir / shard, "rt", encoding="utf-8"):
                x = json.loads(line)
                text = x.pop("text")
                if a.max_chars:
                    text = text[:a.max_chars]
                meta = {"title": x["title"] or x["id"], "court": x["court"], "date": x["date"]}
                di = len(docs)
                docs.append({**x, "n_chars": len(text)})
                for c in chunk_doc(x["cap_id"], text, meta, method="recursive", size=a.size, with_header=True):
                    b = c["text"].encode("utf-8")
                    tf.write(b)
                    pos += len(b)
                    offsets.append(pos)
                    cdoc.append(di)
                    cseq.append(c["seq"])
            print(shard, len(docs), len(cdoc), flush=True)
    (a.out / "texts.bin.tmp").rename(a.out / "texts.bin")
    np.save(a.out / "offsets.npy", np.asarray(offsets, dtype=np.int64))
    np.save(a.out / "chunk_doc.npy", np.asarray(cdoc, dtype=np.int32))
    np.save(a.out / "chunk_seq.npy", np.asarray(cseq, dtype=np.int32))
    (a.out / "docs.json").write_text(json.dumps(docs))
    stats = {"docs": len(docs), "chunks": len(cdoc), "bytes": pos, "size": a.size, "max_chars": a.max_chars}
    (a.out / "stats.json").write_text(json.dumps(stats))
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
