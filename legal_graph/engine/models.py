"""Pydantic request models and the documented event/response shapes."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

STAGES = ("parse", "bm25", "dense", "hybrid", "graph", "rerank")
EVENT_SCHEMA_VERSION = "engine-retrieve/1"
CHECK_SCHEMA_VERSION = "engine-check/1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TextRetrieveRequest(_Strict):
    query: Annotated[StrictStr, Field(min_length=3, max_length=4000)]
    k: Annotated[StrictInt, Field(ge=1, le=50)] = 10
    bm25_k: Annotated[StrictInt, Field(ge=10, le=500)] = 100
    dense: StrictBool = True
    dense_pool: Annotated[StrictInt, Field(ge=1, le=200)] = 50
    graph: StrictBool = True
    graph_seed_docs: Annotated[StrictInt, Field(ge=0, le=50)] = 10
    rerank: StrictBool = True
    rerank_k: Annotated[StrictInt, Field(ge=1, le=100)] = 30
    jev: StrictBool = True  # Jev relevance pass inside the rerank stage
    jev_k: Annotated[StrictInt, Field(ge=1, le=30)] = 10
    passages_per_case: Annotated[StrictInt, Field(ge=1, le=5)] = 2
    stream: StrictBool = True


class CheckClaim(_Strict):
    text: Annotated[StrictStr, Field(min_length=1, max_length=4000)]
    cited_text: Annotated[StrictStr, Field(min_length=1, max_length=20000)]
    citation: StrictStr = ""
    chunk_id: StrictStr | None = None


class CheckRequest(_Strict):
    claims: Annotated[list[CheckClaim], Field(min_length=1, max_length=50)]
    scorer: Literal["minicheck", "hhem", "jev"] = "minicheck"
    threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    temperature: Annotated[float, Field(gt=0.0, le=20.0)] = 1.0
