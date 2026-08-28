"""
Set up the full S8 exposed posture for a test.

An exposed deployment requires TLS material, a provisioned account, and an
explicit runtime binding whose database and keyring namespace agree with it.
That is four coupled environment variables and a control-plane write, so it
lives here rather than being copied into every suite that needs it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def establish_exposed_posture(monkeypatch, tmp_path: Path, *, username: str = "alpha") -> str:
    """
    Configure an exposed, TLS-satisfied, bound deployment. Returns the user_id.

    The TLS material here is a placeholder pair of files: the exposure check
    validates that they exist, and the *live* socket proofs live in
    tests/test_s8_tls_live_socket.py, which generates a real certificate.
    """
    from bartholomew.platform import accounts
    from bartholomew.platform.runtime_registry import (
        RUNTIME_USER_ID_ENV,
        runtime_handle_for_user_id,
    )
    from bartholomew.platform.store import init_platform_schema

    cert, key = tmp_path / "posture-cert.pem", tmp_path / "posture-key.pem"
    cert.write_text("cert")
    key.write_text("key")

    root = tempfile.mkdtemp(prefix="s8-posture-")
    monkeypatch.setenv("BARTH_PLATFORM_DB_PATH", f"{root}/platform.db")
    monkeypatch.setenv("BARTH_DATA_ROOT", f"{root}/data")
    monkeypatch.setenv("BARTH_API_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setenv("BARTH_API_TLS_CERTFILE", str(cert))
    monkeypatch.setenv("BARTH_API_TLS_KEYFILE", str(key))

    init_platform_schema()
    user_id = accounts.create_account(username, "alpha-participant-password")
    handle = runtime_handle_for_user_id(user_id)
    monkeypatch.setenv(RUNTIME_USER_ID_ENV, user_id)
    monkeypatch.setenv("BARTH_DB_PATH", handle.db_path)
    monkeypatch.setenv("BARTHO_MEMORY_KEYRING_SERVICE", handle.keyring_service)
    return user_id
