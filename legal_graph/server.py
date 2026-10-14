"""FastAPI application boundary for the local legal citation graph.

Use ``uvicorn legal_graph.server:create_app --factory --host 127.0.0.1`` for
local development. The graph is loaded once during application lifespan; tests
can inject an already constructed ``CitationGraphIndex`` without reading the
full local dataset.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from .retrieval import CitationGraphIndex, graphrag_retrieve, load_dataset

MAX_NODES = 500
MAX_EDGES = 1000
MAX_DEPTH = 3
MAX_SCANNED_EDGES = 10_000
DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NeighborhoodRequest(_RequestModel):
    seeds: list[StrictStr]
    depth: Annotated[StrictInt, Field(ge=0, le=MAX_DEPTH)] = 1
    direction: Literal["incoming", "outgoing", "both"] = "incoming"
    edge_kinds: list[Annotated[StrictStr, Field(min_length=1)]] = Field(
        default_factory=lambda: ["cites"]
    )
    max_nodes: Annotated[StrictInt, Field(ge=1, le=MAX_NODES)] = MAX_NODES
    max_edges: Annotated[StrictInt, Field(ge=1, le=MAX_EDGES)] = MAX_EDGES
    max_scanned_edges: Annotated[StrictInt, Field(ge=1, le=MAX_SCANNED_EDGES)] = (
        MAX_SCANNED_EDGES
    )


class RetrieveRequest(NeighborhoodRequest):
    k: Annotated[StrictInt, Field(ge=1, le=MAX_NODES)]


def _make_local_index() -> CitationGraphIndex:
    nodes, edges, revision, _hashes = load_dataset()
    return CitationGraphIndex(
        nodes,
        edges,
        dataset_revision=revision,
        source_dataset="data/nodes.jsonl + data/edges.jsonl",
    )


def create_app(
    index: CitationGraphIndex | None = None,
    *,
    synthetic: bool = False,
    allowed_origins: Sequence[str] = DEFAULT_ORIGINS,
) -> FastAPI:
    """Create the graph API, optionally injecting an index for tests.

    The normal path loads only the frozen local JSONL graph during startup.
    This function never substitutes an Octo test snapshot for that dataset.
    """
    origins = tuple(allowed_origins)
    if any(not isinstance(origin, str) or not origin for origin in origins):
        raise ValueError("allowed_origins must contain non-empty origin strings")
    if "*" in origins:
        raise ValueError("wildcard CORS origins are not supported")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.graph_index is None:
            app.state.graph_index = _make_local_index()
        yield

    app = FastAPI(
        title="Legal Graph API",
        version="1.0.0",
        description=(
            "Local research prototype API for bounded citation graph browsing. "
            "Search is metadata substring matching, not semantic legal retrieval."
        ),
        lifespan=lifespan,
    )
    app.state.graph_index = index
    app.state.synthetic = bool(synthetic)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    def graph_index() -> CitationGraphIndex:
        current = app.state.graph_index
        if current is None:
            raise RuntimeError("graph index is not initialized; enter application lifespan")
        return current

    def revision() -> str:
        return graph_index().dataset_revision

    def reject_unknown_query(request: Request, allowed: set[str] | None = None) -> None:
        unknown = set(request.query_params) - (allowed or set())
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"unknown query parameter(s): {', '.join(sorted(unknown))}",
            )

    @app.get("/api/v1/health")
    def health(request: Request):
        reject_unknown_query(request)
        current = graph_index()
        return {
            "status": "ready",
            "graph": {
                "revision": current.dataset_revision,
                "nodes": len(current.nodes),
                "edges": len(current.edges),
                "source_dataset": current.source_dataset,
                "synthetic": app.state.synthetic,
            },
        }

    @app.get("/api/v1/search")
    def search(
        request: Request,
        q: Annotated[str, Query()],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        reject_unknown_query(request, {"q", "limit"})
        query = q.casefold()
        current = graph_index()
        matching = [
            node
            for node_id, node in current.nodes.items()
            if query in node_id.casefold()
            or (isinstance(node.get("title"), str) and query in node["title"].casefold())
        ]
        matching.sort(key=lambda node: node["id"])
        return {"results": matching[:limit], "graph_revision": current.dataset_revision}

    @app.get("/api/v1/authority")
    def authority(request: Request, id: Annotated[str, Query()]):
        reject_unknown_query(request, {"id"})
        current = graph_index()
        node = current.nodes.get(id)
        if node is None:
            raise HTTPException(status_code=404, detail="authority not found")
        return {"node": node, "graph_revision": current.dataset_revision}

    @app.post("/api/v1/neighborhood")
    def neighborhood(request: Request, body: NeighborhoodRequest):
        reject_unknown_query(request)
        current = graph_index()
        result = current.retrieve_workflow(
            body.seeds,
            depth=body.depth,
            direction=body.direction,
            edge_kinds=body.edge_kinds,
            max_nodes=body.max_nodes,
            max_edges=body.max_edges,
            max_scanned_edges=body.max_scanned_edges,
        )
        return {**result, "graph_revision": current.dataset_revision}

    @app.post("/api/v1/retrieve")
    def retrieve(request: Request, body: RetrieveRequest):
        reject_unknown_query(request)
        current = graph_index()
        result = graphrag_retrieve(
            body.seeds,
            body.k,
            index=current,
            depth=body.depth,
            direction=body.direction,
            edge_kinds=body.edge_kinds,
            max_nodes=body.max_nodes,
            max_edges=body.max_edges,
            max_scanned_edges=body.max_scanned_edges,
        )
        return {**result, "graph_revision": current.dataset_revision}

    @app.get("/api/v1/experiments")
    def experiments(request: Request):
        reject_unknown_query(request)
        return {"experiments": [], "graph_revision": revision()}

    return app


__all__ = ["NeighborhoodRequest", "RetrieveRequest", "create_app"]
