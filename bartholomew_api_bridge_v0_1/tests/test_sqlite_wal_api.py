"""
Test SQLite WAL defaults are properly configured in API layer.

Validates that the API DB init path sets:
- PRAGMA journal_mode=WAL
- PRAGMA busy_timeout >= 5000
- PRAGMA synchronous=NORMAL or FULL
- PRAGMA foreign_keys=ON

This mirrors tests/test_sqlite_wal.py but validates API layer db_ctx parity.
"""

import sqlite3

from bartholomew_api_bridge_v0_1.services.api import db_ctx


def test_wal_pragmas_applied_via_set_wal_pragmas_api(tmp_path):
    """Verify API set_wal_pragmas applies all required settings."""
    db_path = tmp_path / "test_api.db"
    conn = sqlite3.connect(str(db_path))

    # Apply pragmas using the API canonical function
    db_ctx.set_wal_pragmas(conn)

    # Check journal_mode
    mode = conn.execute("PRAGMA journal_mode;").fetchone()[0].lower()
    assert mode == "wal", f"Expected journal_mode=wal, got {mode}"

    # Check busy_timeout
    busy = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
    assert int(busy) >= 5000, f"Expected busy_timeout >= 5000, got {busy}"

    # Check synchronous (0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA)
    sync = str(conn.execute("PRAGMA synchronous;").fetchone()[0])
    expected = "Expected synchronous=NORMAL(1) or FULL(2)"
    assert sync in ("1", "2"), f"{expected}, got {sync}"

    # Check foreign_keys
    fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
    assert fk == 1, f"Expected foreign_keys=ON, got {fk}"

    conn.close()


def test_wal_pragmas_applied_via_context_manager_api(tmp_path):
    """Verify API wal_db context manager applies all required settings."""
    db_path = tmp_path / "test_ctx_api.db"

    with db_ctx.wal_db(str(db_path)) as conn:
        # Check journal_mode
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0].lower()
        assert mode == "wal", f"Expected journal_mode=wal, got {mode}"

        # Check busy_timeout
        busy = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert int(busy) >= 5000, f"Expected busy_timeout >= 5000, got {busy}"

        # Check synchronous (0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA)
        sync = str(conn.execute("PRAGMA synchronous;").fetchone()[0])
        expected = "Expected synchronous=NORMAL(1) or FULL(2)"
        assert sync in ("1", "2"), f"{expected}, got {sync}"

        # Check foreign_keys
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk == 1, f"Expected foreign_keys=ON, got {fk}"


def test_api_kernel_pragma_parity(tmp_path):
    """Verify API and kernel db_ctx apply identical PRAGMAs."""
    from bartholomew.kernel import db_ctx as kernel_db_ctx

    api_db = tmp_path / "api.db"
    kernel_db = tmp_path / "kernel.db"

    # Apply API pragmas
    api_conn = sqlite3.connect(str(api_db))
    db_ctx.set_wal_pragmas(api_conn)

    # Apply kernel pragmas
    kernel_conn = sqlite3.connect(str(kernel_db))
    kernel_db_ctx.set_wal_pragmas(kernel_conn)

    # Extract pragma values for comparison
    def get_pragmas(conn):
        return {
            "journal_mode": conn.execute("PRAGMA journal_mode;").fetchone()[0].lower(),
            "busy_timeout": int(conn.execute("PRAGMA busy_timeout;").fetchone()[0]),
            "synchronous": int(conn.execute("PRAGMA synchronous;").fetchone()[0]),
            "foreign_keys": int(conn.execute("PRAGMA foreign_keys;").fetchone()[0]),
        }

    api_pragmas = get_pragmas(api_conn)
    kernel_pragmas = get_pragmas(kernel_conn)

    # Assert parity
    assert (
        api_pragmas == kernel_pragmas
    ), f"API and kernel PRAGMA mismatch:\nAPI:    {api_pragmas}\nKernel: {kernel_pragmas}"

    api_conn.close()
    kernel_conn.close()


def test_api_db_ctx_is_kernel_db_ctx(tmp_path):
    """
    Guard rail (Phase B stage B1): the API layer's db_ctx module must be the
    same module as the kernel's, not a parallel copy that can silently
    re-diverge. See docs/B0_PERSISTENCE_BASELINE.md Sec 4 for the divergence
    this consolidation resolved.
    """
    from bartholomew.kernel import db_ctx as kernel_db_ctx

    assert db_ctx.wal_db is kernel_db_ctx.wal_db
    assert db_ctx.set_wal_pragmas is kernel_db_ctx.set_wal_pragmas
    assert db_ctx.wal_checkpoint_truncate is kernel_db_ctx.wal_checkpoint_truncate


def test_api_wal_db_no_longer_checkpoints_on_every_call(tmp_path, monkeypatch):
    """
    Regression test for the B1-resolved hot-path checkpoint problem: the
    API layer's wal_db() previously called wal_checkpoint_truncate()
    unconditionally on every exit (including on read-only liveness GET
    routes). It must not do so anymore unless a checkpoint is explicitly
    requested.
    """
    from bartholomew.kernel import db_ctx as kernel_db_ctx

    calls: list[str] = []
    monkeypatch.setattr(
        kernel_db_ctx,
        "wal_checkpoint",
        lambda *a, **k: calls.append(k.get("mode", "")),
    )

    db_path = tmp_path / "test_no_checkpoint.db"
    with db_ctx.wal_db(str(db_path)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS t(x)")
        conn.commit()
    assert calls == [], f"wal_db() checkpointed without being asked: {calls}"

    with db_ctx.wal_db(str(db_path), checkpoint="TRUNCATE") as conn:
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
    assert calls == ["TRUNCATE"], f"explicit checkpoint request was not honoured: {calls}"
