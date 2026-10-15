"""Write docs/openapi-engine.json from the app factory (offline, no models)."""
import json
from pathlib import Path

from legal_graph.retrieval import CitationGraphIndex
from legal_graph.server import create_app

if __name__ == "__main__":
    app = create_app(index=CitationGraphIndex([], []), api_token="x", live_engine=False)
    out = Path(__file__).resolve().parents[2] / "docs" / "openapi-engine.json"
    out.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    print(out)
