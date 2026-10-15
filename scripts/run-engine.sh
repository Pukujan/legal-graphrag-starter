#!/usr/bin/env bash
# Starts the retrieval engine on 127.0.0.1:${PORT:-8010}. Secrets come from env files; never echoed.
set -euo pipefail
set -a
. /home/box/.config/study-os/openrouter.env   # OPENROUTER_API_KEY: Jev rerank pass and the jev /check scorer
. /workspace/octo-legal-graph/.env
. /workspace/octo-legal-graph/.env.engine
set +a
unset OPENROUTER_MODEL OPENROUTER_API_URL     # the engine uses JEV_MODEL and the Decisions API URL
export OCTO_API_KEY="$octodb_legal_graph"; unset octodb_legal_graph
export JEV_MODEL="${JEV_MODEL:-typesafe/jev-1.14}"
export ENGINE_LIVE=1 ENGINE_CACHE_DIR=/workspace/lgr-text-data/engine-cache HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export ENGINE_GRAPH_EDGE_CAP="${ENGINE_GRAPH_EDGE_CAP:-20000}"
cd "${ENGINE_REPO:-/workspace/lgr-engine2}"
exec /workspace/lgr-search-data/.venv/bin/python -m uvicorn legal_graph.server:create_app --factory --host 127.0.0.1 --port "${PORT:-8010}"
