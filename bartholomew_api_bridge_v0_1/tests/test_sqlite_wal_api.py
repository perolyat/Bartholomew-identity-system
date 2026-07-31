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


def test_api_db_ctx_is_kernel_db_ctx_not_a_second_implementation():
    """
    B1 (Phase B): the API bridge previously held its own independent copy of
    every connection-policy function. Pin that it is now a re-export, not
    just behaviorally similar -- a future accidental reintroduction of a
    second implementation must fail this test, not silently drift again.
    """
    from bartholomew.kernel import db_ctx as kernel_db_ctx

    for name in (
        "connect",
        "set_wal_pragmas",
        "wal_checkpoint",
        "wal_checkpoint_truncate",
        "close_quietly",
        "close_all_and_checkpoint",
        "wal_db",
    ):
        assert getattr(db_ctx, name) is getattr(
            kernel_db_ctx, name
        ), f"db_ctx.{name} is a separate object from kernel db_ctx.{name} -- re-export broken"


def test_api_wal_db_does_not_checkpoint_by_default(tmp_path, monkeypatch):
    """
    B1 (Phase B): the API bridge's wal_db() previously ran an unconditional
    PRAGMA wal_checkpoint(TRUNCATE) on every exit -- a blocking hot-path
    checkpoint on every liveness API read. It must now default to no
    explicit checkpoint at all (relying on SQLite's automatic WAL
    checkpoint), matching the kernel module's default.
    """
    from bartholomew.kernel import db_ctx as kernel_db_ctx

    db_path = tmp_path / "no_checkpoint.db"
    calls = []
    monkeypatch.setattr(
        kernel_db_ctx,
        "wal_checkpoint",
        lambda *a, **kw: calls.append((a, kw)) or None,
    )

    with db_ctx.wal_db(str(db_path), timeout=30.0) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS t(x)")
        conn.commit()

    assert calls == [], f"wal_db() with no checkpoint= argument still checkpointed: {calls}"

    with db_ctx.wal_db(str(db_path), timeout=30.0, checkpoint="TRUNCATE") as conn:
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()

    assert len(calls) == 1, "wal_db(checkpoint='TRUNCATE') should still checkpoint when asked"
