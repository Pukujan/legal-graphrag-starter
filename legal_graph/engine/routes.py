"""Controllers for the engine routes: /retrieve (SSE), /check, /case, /benchmarks."""
from __future__ import annotations

import hmac
import json
import math
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from .checks import ScorerRegistry, run_check
from .models import CheckRequest, TextRetrieveRequest

BENCH_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "runs"


def _clean(x: Any) -> Any:
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def sse(event: dict[str, Any]) -> str:
    name = event.get("type", "message")
    return f"event: {name}\ndata: {json.dumps(_clean(event), ensure_ascii=True, allow_nan=False)}\n\n"


def require_token(request: Request) -> None:
    expected = request.app.state.api_token
    if not expected:
        raise HTTPException(503, detail="engine routes disabled: ENGINE_API_TOKEN is not configured")
    header = request.headers.get("authorization", "")
    if not (header.startswith("Bearer ") and hmac.compare_digest(header[7:].encode(), expected.encode())):
        raise HTTPException(401, detail="missing or invalid bearer token", headers={"WWW-Authenticate": "Bearer"})


def _blank(value: Any) -> bool:
    return value is None or value == ""


def fill_case(case: dict[str, Any], authority: Any, passages: Any) -> dict[str, Any]:
    """Fill court, year, link and cap id from the authority row and chunk ids.

    The work row for a live opinion is often empty. The authority payload still
    carries the court, decision date and CourtListener URL, and the chunk id
    carries the CAP id (``cap:123:combined:0``).
    """
    filled = dict(case)
    auth = authority if isinstance(authority, dict) else {}
    if _blank(filled.get("court")) and isinstance(auth.get("court"), str):
        filled["court"] = auth["court"]
    if _blank(filled.get("year")) and isinstance(auth.get("date"), str) and len(auth["date"]) >= 4:
        year = auth["date"][:4]
        if year.isdigit():
            filled["year"] = int(year)
    url = auth.get("url")
    if _blank(filled.get("link")) and isinstance(url, str) and url.startswith(("http://", "https://")):
        filled["link"] = url
    if _blank(filled.get("cap_id")) and isinstance(passages, list):
        for passage in passages:
            chunk_id = passage.get("chunk_id") if isinstance(passage, dict) else None
            if isinstance(chunk_id, str) and chunk_id.startswith("cap:") and chunk_id.count(":") >= 2:
                cap_id = chunk_id.split(":", 2)[1]
                if cap_id.isdigit():
                    filled["cap_id"] = cap_id
                    break
    title = auth.get("title")
    if (_blank(filled.get("name")) or filled.get("name") == filled.get("id")) and isinstance(title, str) and title:
        filled["name"] = title
    return filled


def list_benchmarks(bench_dir: Path) -> list[dict[str, Any]]:
    runs = []
    for p in sorted(bench_dir.glob("*/results.json")):
        d = json.loads(p.read_text())
        runs.append({"run_id": p.parent.name, "label": d.get("label"), "dataset_revision": d.get("dataset_revision"),
                     "summary": d.get("summary"), "config": d.get("config"),
                     "per_query_count": len(d.get("per_query") or [])})
    return runs


def text_retrieve_response(p, body: TextRetrieveRequest):
    if not body.stream:
        events = list(p.run(body))
        return _clean({"stages": events[:-1], "result": events[-1]})

    def gen():
        try:
            for e in p.run(body):
                yield sse(e)
        except Exception as exc:  # report, then close the stream
            yield sse({"type": "error", "error": type(exc).__name__, "detail": str(exc)[:300]})
        yield sse({"type": "done"})
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def build_router(get_pipeline: Callable[[], Any], registry: ScorerRegistry, bench_dir: Path = BENCH_DIR,
                 get_graph: Callable[[], Any] | None = None) -> APIRouter:
    r = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])

    def pipeline():
        p = get_pipeline()
        if p is None:
            raise HTTPException(503, detail="retrieval pipeline is still loading")
        return p

    @r.post("/check", summary="Per-claim support scores, confidence and abstain")
    def check(body: CheckRequest):
        if not registry.available(body.scorer):
            raise HTTPException(503, detail=f"scorer {body.scorer!r} is not configured on this engine")
        p = get_pipeline()

        def chunk_text(cid: str):
            if p is None or cid not in p.corpus.by_id:
                return None
            return p.corpus.chunks[p.corpus.by_id[cid]].body
        try:
            return _clean(run_check(body, registry, chunk_text))
        except ImportError as exc:
            raise HTTPException(503, detail=f"scorer dependencies missing: {exc.name}") from None

    @r.get("/cases/{doc_id:path}", summary="Case metadata and stored passages")
    def case(doc_id: str):
        p = pipeline()
        if doc_id not in p.corpus.by_doc and doc_id not in p.corpus.docs:
            raise HTTPException(404, detail="case not found in the text corpus")
        payload = get_graph().authority(doc_id) if get_graph and get_graph() else None
        passages = [{"chunk_id": p.corpus.chunks[i].chunk_id, "ord": p.corpus.chunks[i].ord,
                     "text": p.corpus.chunks[i].body} for i in p.corpus.by_doc.get(doc_id, [])]
        return {"case": fill_case(p.corpus.case(doc_id), payload, passages), "authority": payload,
                "passages": passages}

    @r.get("/benchmarks", summary="Frozen benchmark runs (benchmarks/runs/*/results.json)")
    def benchmarks():
        return {"runs": _clean(list_benchmarks(bench_dir))}

    @r.get("/benchmarks/{run_id}", summary="One benchmark run including per-query rows")
    def benchmark(run_id: str):
        p = bench_dir / run_id / "results.json"
        if "/" in run_id or ".." in run_id or not p.is_file():
            raise HTTPException(404, detail="benchmark run not found")
        return _clean(json.loads(p.read_text()))

    return r
