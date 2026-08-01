"""
Tests for Phase B stage B6: bartholomew/cli.py's `brake on`/`brake off`/
`brake status` against GovernanceStore directly (replacing the legacy
ParkingBrake/BrakeStorage path), and `embeddings rebuild-vss` refusing to
run while the process lock is held. See
docs/B6_EXTERNAL_GOVERNANCE_CLI_SAFETY.md.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from bartholomew.cli import app
from bartholomew.kernel.process_lock import ProcessLock
from bartholomew.orchestrator.safety.governance_store import GovernanceStore

runner = CliRunner()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


# -- brake on/off/status now write through GovernanceStore -------------------


def test_brake_on_defaults_to_global_scope(db_path):
    result = runner.invoke(app, ["brake", "on", "--db", db_path])
    assert result.exit_code == 0
    assert "ENGAGED" in result.stdout

    state = GovernanceStore(db_path).state()
    assert state.engaged is True
    assert state.scopes == frozenset({"global"})


def test_brake_on_with_explicit_scopes(db_path):
    result = runner.invoke(
        app,
        ["brake", "on", "--scope", "skills", "--scope", "sight", "--db", db_path],
    )
    assert result.exit_code == 0

    state = GovernanceStore(db_path).state()
    assert state.engaged is True
    assert state.scopes == frozenset({"skills", "sight"})


def test_brake_on_writes_a_cli_tagged_audit_reason(db_path):
    runner.invoke(app, ["brake", "on", "--scope", "skills", "--db", db_path])

    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        reason = conn.execute(
            "SELECT reason FROM governance_audit ORDER BY id DESC LIMIT 1",
        ).fetchone()[0]
    finally:
        conn.close()
    assert reason.startswith("CLI:")


def test_brake_off_disengages(db_path):
    runner.invoke(app, ["brake", "on", "--db", db_path])
    result = runner.invoke(app, ["brake", "off", "--db", db_path])
    assert result.exit_code == 0
    assert "DISENGAGED" in result.stdout

    state = GovernanceStore(db_path).state()
    assert state.engaged is False


def test_brake_status_reflects_governance_store_state(db_path):
    runner.invoke(app, ["brake", "on", "--scope", "voice", "--db", db_path])
    result = runner.invoke(app, ["brake", "status", "--db", db_path])
    assert result.exit_code == 0
    assert "ENGAGED" in result.stdout
    assert "voice" in result.stdout


def test_brake_status_on_fresh_db_is_disengaged(db_path):
    result = runner.invoke(app, ["brake", "status", "--db", db_path])
    assert result.exit_code == 0
    assert "DISENGAGED" in result.stdout


def test_brake_on_refused_with_clear_message_when_write_fence_closed(db_path):
    store = GovernanceStore(db_path)
    store.open_new_runtime()
    store.close_write_fence()

    result = runner.invoke(app, ["brake", "on", "--db", db_path])
    assert result.exit_code == 1
    assert "Could not engage" in result.stdout


def test_brake_off_refused_with_clear_message_when_write_fence_closed(db_path):
    store = GovernanceStore(db_path)
    store.engage("global")
    store.open_new_runtime()
    store.close_write_fence()

    result = runner.invoke(app, ["brake", "off", "--db", db_path])
    assert result.exit_code == 1
    assert "Could not disengage" in result.stdout


def test_brake_off_surfaces_stale_write_with_actionable_message(db_path, monkeypatch):
    GovernanceStore(db_path).engage("global")

    from bartholomew.orchestrator.safety.governance_store import (
        GovernanceStore as GovernanceStoreClass,
    )
    from bartholomew.orchestrator.safety.governance_store import (
        StaleGovernanceWriteError,
    )

    def raising_disengage(self, *, reason=None, expected_revision=None, actor=None):
        raise StaleGovernanceWriteError("state changed since it was last read")

    monkeypatch.setattr(GovernanceStoreClass, "disengage", raising_disengage)

    result = runner.invoke(app, ["brake", "off", "--db", db_path])
    assert result.exit_code == 1
    assert "Could not disengage" in result.stdout
    assert "retry" in result.stdout.lower()


# -- rebuild-vss respects the process lock ------------------------------------


def test_rebuild_vss_refuses_when_daemon_holds_the_lock(db_path, tmp_path):
    # embeddings_rebuild_vss() checks os.path.exists(db) before acquiring
    # the lock -- the file must exist for the lock-conflict path to be
    # the one actually exercised.
    open(db_path, "a").close()

    lock = ProcessLock(db_path)
    lock.acquire()
    try:
        result = runner.invoke(app, ["embeddings", "rebuild-vss", "--db", db_path])
        assert result.exit_code == 1
        assert "another process" in result.stdout.lower()
    finally:
        lock.release()


def test_rebuild_vss_releases_lock_even_when_vss_extension_missing(db_path):
    """The lock must not leak even when the command fails for an
    unrelated reason (no sqlite-vss extension installed, the expected
    case in a plain test environment)."""
    open(db_path, "a").close()

    runner.invoke(app, ["embeddings", "rebuild-vss", "--db", db_path])

    lock = ProcessLock(db_path)
    lock.acquire()  # must not raise -- the command's own lock was released
    lock.release()
