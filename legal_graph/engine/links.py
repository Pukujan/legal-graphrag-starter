"""Case links from the Caselaw Access Project static mirror (static.case.law).

CAP serves every case at ``https://static.case.law/<reporter>/<volume>/cases/<file>.json``
where ``file`` is ``<first page, 4 digits>-<ordinal, 2 digits>`` and at the HTML viewer
``https://case.law/caselaw/?reporter=<reporter>&volume=<volume>&case=<file>``.
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

STATIC = "https://static.case.law"
_CITE = re.compile(r"^(\d{1,4})\s+(.+?)\s+(\d{1,5})$")


def reporter_slug(abbrev: str) -> str:
    """CAP reporter slug: 'U.S.'->'us', 'F.2d'->'f2d', 'F. Supp. 2d'->'f-supp-2d', 'S. Ct.'->'s-ct'."""
    s = abbrev.strip().lower().rstrip(".")
    s = re.sub(r"\.\s+|\s+", "-", s)
    return s.replace(".", "")


def case_file(page: int | str, ordinal: int = 1) -> str:
    return f"{int(page):04d}-{ordinal:02d}"


def cap_static_url(reporter: str | None, volume: Any, file: str | None) -> str | None:
    if not (reporter and volume is not None and file):
        return None
    return f"{STATIC}/{reporter}/{volume}/cases/{file}.json"


def cap_html_url(reporter: str | None, volume: Any, file: str | None) -> str | None:
    if not (reporter and volume is not None and file):
        return None
    return f"https://case.law/caselaw/?reporter={reporter}&volume={volume}&case={file}"


def parse_cite(cite: str) -> tuple[str, int, int] | None:
    m = _CITE.match(cite.strip())
    if not m:
        return None
    return reporter_slug(m.group(2)), int(m.group(1)), int(m.group(3))


def resolve_cap_link(cap_id: str | int, cite: str, max_ordinal: int = 4, opener=urllib.request.urlopen,
                     timeout: float = 20.0) -> dict[str, Any] | None:
    """Find the static file for ``cite`` whose JSON ``id`` equals ``cap_id`` (tries ordinals 01..max)."""
    p = parse_cite(cite)
    if not p:
        return None
    slug, vol, page = p
    for o in range(1, max_ordinal + 1):
        f = case_file(page, o)
        url = cap_static_url(slug, vol, f)
        try:
            with opener(urllib.request.Request(url, headers={"User-Agent": "legal-graph/0.1"}), timeout=timeout) as r:
                data = json.loads(r.read())
        except Exception:  # noqa: BLE001 - 404 or transport error: stop at the first missing ordinal
            return None
        if str(data.get("id")) == str(cap_id):
            return {"link": url, "html": cap_html_url(slug, vol, f), "reporter": slug, "volume": vol, "file": f}
    return None


def backfill(sql, workers: int = 16, limit: int = 100000) -> dict[str, int]:
    """Set ``work.ext_ids.link/html`` for works that have a cap_id and a reporter cite but no link.

    The cite is the work_id suffix after the last ', ' (CLERC ids look like 'Name, 855 F.2d 874').
    Each candidate URL is fetched and accepted only if its JSON ``id`` equals the stored cap_id.
    """
    from concurrent.futures import ThreadPoolExecutor
    rows = sql("SELECT work_id, ext_ids->>'cap_id' AS cap FROM legal_graph.work WHERE ext_ids ? 'cap_id' "
               "AND NOT ext_ids ? 'link'", [], limit)["rows"]

    def one(r):
        return r, resolve_cap_link(r["cap"], r["work_id"].rsplit(", ", 1)[-1])
    stats = {"candidates": len(rows), "resolved": 0, "unresolved": 0}
    with ThreadPoolExecutor(workers) as ex:
        for r, link in ex.map(one, rows):
            if not link:
                stats["unresolved"] += 1
                continue
            sql("UPDATE legal_graph.work SET ext_ids = ext_ids || $2::jsonb WHERE work_id = $1 AND NOT ext_ids ? 'link'",
                [r["work_id"], json.dumps({"link": link["link"], "html": link["html"]}, ensure_ascii=True)], 1)
            stats["resolved"] += 1
            if stats["resolved"] % 500 == 0:
                print(json.dumps(stats), flush=True)
    return stats


if __name__ == "__main__":
    from .octo_source import octo_executor
    print(json.dumps(backfill(octo_executor())), flush=True)
