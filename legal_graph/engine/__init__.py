"""Retrieval-engine HTTP API (MVC): models.py = request/event schemas, services in
pipeline.py / corpus.py / checks.py / octo_source.py, controllers in routes.py.

ABA (or any client) calls these routes with a bearer token; the engine alone
holds the Octo and OpenRouter credentials.
"""
