"""
Root-level shim for uvicorn entry point.

Allows running: uvicorn app:app --reload --port 5173

The actual FastAPI application is defined in:
bartholomew_api_bridge_v0_1/services/api/app.py
"""

from bartholomew_api_bridge_v0_1.services.api.app import app


__all__ = ["app"]
