"""
Root-level shim for uvicorn entry point.

Allows running: uvicorn app:app --reload --port 5173

The actual FastAPI application is defined in:
bartholomew_api_bridge_v0_1/services/api/app.py
"""

import os

import uvicorn

from bartholomew.platform.exposure import assert_exposure_is_safe, uvicorn_tls_kwargs
from bartholomew_api_bridge_v0_1.services.api.app import app, resolve_bind_host

__all__ = ["app", "serve"]


def serve() -> None:
    """
    Run the API on the address the access boundary resolves.

    Loopback-only unless a non-loopback bind has been deliberately enabled
    (see `resolve_bind_host()`). Launch through this rather than passing
    `--host` to uvicorn by hand, so the default lives in code that can be
    tested rather than in a shell string that can be copied wrong.

    **This is the canonical serve path, and it configures TLS on the actual
    socket.** Validating that certificate files exist is not the same as
    serving TLS: `uvicorn_tls_kwargs()` is what hands the material to the
    listener, so an exposed deployment speaks HTTPS rather than merely having
    passed a file-existence check. A process launched some other way (the
    `uvicorn` CLI with an explicit non-loopback `--host`) bypasses this
    function entirely, which is why the request boundary independently
    refuses a plaintext request on an exposed deployment -- see
    `admission_middleware`.
    """
    # Fail closed before binding, not on the first request: by then the
    # socket is already open. Also verifies the bound runtime user.
    assert_exposure_is_safe()
    uvicorn.run(
        app,
        host=resolve_bind_host(),
        port=int(os.getenv("BARTH_API_PORT", "5173")),
        **uvicorn_tls_kwargs(),
    )


if __name__ == "__main__":
    serve()
