"""Serve the Octo-backed retrieval engine for the hosted research site.

Loads the live pipeline in the background. Does not read the local JSONL graph.
"""
from __future__ import annotations

import logging

import uvicorn

from legal_graph.retrieval import CitationGraphIndex
from legal_graph.server import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    index = CitationGraphIndex(
        [],
        [],
        dataset_revision="octo-live",
        source_dataset="octo",
    )
    app = create_app(index=index, live_engine=True)
    # 0.0.0.0 so the site container can reach the engine through host.docker.internal.
    # Retrieve stays bearer-token gated.
    uvicorn.run(app, host="0.0.0.0", port=8010, log_level="info")


if __name__ == "__main__":
    main()
