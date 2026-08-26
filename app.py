"""
Root-level shim for uvicorn entry point.

Allows running: uvicorn app:app --reload --port 5173

The actual FastAPI application is defined in:
bartholomew_api_bridge_v0_1/services/api/app.py
"""

import os

import uvicorn

from bartholomew_api_bridge_v0_1.services.api.app import app, resolve_bind_host

__all__ = ["app", "serve"]


def serve() -> None:
    """
    Run the API on the address the access boundary resolves.

    Loopback-only unless a non-loopback bind has been deliberately enabled
    (see `resolve_bind_host()`). Launch through this rather than passing
    `--host` to uvicorn by hand, so the default lives in code that can be
    tested rather than in a shell string that can be copied wrong.
    """
    uvicorn.run(app, host=resolve_bind_host(), port=int(os.getenv("BARTH_API_PORT", "5173")))


if __name__ == "__main__":
    serve()
