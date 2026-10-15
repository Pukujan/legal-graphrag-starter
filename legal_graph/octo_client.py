"""Minimal executor for Octo's workspace SQL endpoint (POST /api/workspaces/<id>/query).

The API key is read from an environment variable and never logged or returned.
The callable matches ``GraphSnapshotStore``'s executor signature.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://octodb.design-bakery.com/api"


class OctoSqlExecutor:
    def __init__(self, workspace_id: str, *, key_env: str = "octo_db_legal",
                 base_url: str = DEFAULT_BASE_URL, timeout: float = 120.0):
        if not os.environ.get(key_env):
            raise RuntimeError(f"environment variable {key_env} is not set")
        self.workspace_id, self._key_env = workspace_id, key_env
        self.url = f"{base_url.rstrip('/')}/workspaces/{workspace_id}/query"
        self.timeout = timeout

    def __call__(self, sql: str, params: list[Any], row_limit: int) -> dict[str, Any]:
        body = {"sql": sql, "rowLimit": row_limit}
        if params:
            body["params"] = params
        # ASCII-only body: Octo's readBody concatenates raw chunks, which corrupts
        # multi-byte UTF-8 split across chunk boundaries (observed as U+FFFD).
        request = urllib.request.Request(
            self.url, data=json.dumps(body, ensure_ascii=True).encode(),
            headers={"Authorization": "Bearer " + os.environ[self._key_env],
                     "Content-Type": "application/json", "User-Agent": "legal-graph/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:  # surface the server envelope, not headers
            detail = exc.read()[:500].decode("utf-8", "replace")
            raise RuntimeError(f"Octo SQL HTTP {exc.code}: {detail}") from None
