"""ASGI entry point for PythonAnywhere.

PythonAnywhere starts Uvicorn directly, so the persistent records snapshot must
be prepared before the FastAPI application is imported.
"""

from scripts.serve import bootstrap_records


bootstrap_records()

from backend.app import app  # noqa: E402  (bootstrap must run first)
