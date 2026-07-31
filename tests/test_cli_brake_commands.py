"""
Phase B, stage B6: bartholomew/cli.py's brake on/off/status commands, now
backed by GovernanceBrakeStore instead of the legacy ParkingBrake/
system_flags path, with a resolved-db-path fix and live-daemon detection.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from bartholomew.cli import app
from bartholomew.kernel.governance.brake_store import GovernanceBrakeStore
from bartholomew.kernel.process_lock import ProcessLock

runner = CliRunner()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "cli_test.db")


def test_brake_on_engages_and_reports_no_live_daemon(db_path):
    result = runner.invoke(app, ["brake", "on", "--scope", "skills", "--db", db_path])
    assert result.exit_code == 0
    assert "ENGAGED" in result.stdout
    assert "skills" in result.stdout
    assert "No live daemon detected" in result.stdout

    state = GovernanceBrakeStore(db_path).current_state()
    assert state.engaged is True
    assert state.scopes == frozenset({"skills"})


def test_brake_on_unions_scopes_across_calls(db_path):
    """The direct CLI-level regression test for B3's fix: a second `brake
    on` with a different scope must not silently drop the first."""
    runner.invoke(app, ["brake", "on", "--scope", "global", "--db", db_path])
    result = runner.invoke(app, ["brake", "on", "--scope", "skills", "--db", db_path])
    assert result.exit_code == 0

    state = GovernanceBrakeStore(db_path).current_state()
    assert state.scopes == frozenset({"global", "skills"})


def test_brake_off_disengages(db_path):
    runner.invoke(app, ["brake", "on", "--scope", "global", "--db", db_path])
    result = runner.invoke(app, ["brake", "off", "--db", db_path])
    assert result.exit_code == 0
    assert "DISENGAGED" in result.stdout

    state = GovernanceBrakeStore(db_path).current_state()
    assert state.engaged is False
    assert state.scopes == frozenset()


def test_brake_status_reports_engaged_state(db_path):
    runner.invoke(app, ["brake", "on", "--scope", "voice", "--db", db_path])
    result = runner.invoke(app, ["brake", "status", "--db", db_path])
    assert result.exit_code == 0
    assert "ENGAGED" in result.stdout
    assert "voice" in result.stdout


def test_brake_status_reports_disengaged_state(db_path):
    result = runner.invoke(app, ["brake", "status", "--db", db_path])
    assert result.exit_code == 0
    assert "DISENGAGED" in result.stdout


def test_brake_commands_produce_real_audit_rows(db_path):
    """Audit parity (per docs/PHASE_B_OVERVIEW.md's B6 scope): the legacy
    CLI path's audit call was always a silent no-op (BrakeStorage.
    append_memory() requires a memory_store= the CLI never provided).
    GovernanceBrakeStore's audit is unconditional and atomic."""
    runner.invoke(app, ["brake", "on", "--scope", "global", "--db", db_path])
    runner.invoke(app, ["brake", "off", "--db", db_path])

    log = GovernanceBrakeStore(db_path).audit_log(limit=10)
    assert len(log) == 2
    assert [row["action"] for row in log] == ["disengage", "engage"]
    assert all(row["actor"] == "cli" for row in log)


def test_brake_status_reports_live_daemon_when_lock_held(db_path):
    lock = ProcessLock(f"{db_path}.lock")
    assert lock.try_acquire() is True
    try:
        result = runner.invoke(app, ["brake", "status", "--db", db_path])
        assert result.exit_code == 0
        assert "live daemon appears to be running" in result.stdout
    finally:
        lock.release()


def test_brake_on_still_succeeds_while_a_daemon_holds_the_lock(db_path):
    """
    Deliberately NOT fenced (see docs/B6_IMPLEMENTATION.md section on write
    fencing): brake on/off must keep working as an emergency stop against a
    live daemon, not be blocked by the very lock that indicates one is
    running.
    """
    lock = ProcessLock(f"{db_path}.lock")
    lock.try_acquire()
    try:
        result = runner.invoke(app, ["brake", "on", "--scope", "global", "--db", db_path])
        assert result.exit_code == 0
        assert "ENGAGED" in result.stdout
        # Console output may wrap; check the two halves separately rather
        # than assume they land on the same line.
        assert "live daemon appears to be running" in result.stdout
        assert "next check" in result.stdout
    finally:
        lock.release()

    state = GovernanceBrakeStore(db_path).current_state()
    assert state.engaged is True


def test_brake_commands_default_to_the_daemon_resolved_db_path(monkeypatch, tmp_path):
    """
    The precondition B6's process-lock/CLI-safety story depends on: CLI and
    daemon must target the same file by default, or lock detection and the
    schema swap are both silently meaningless. Previously the CLI defaulted
    to a different filename ("data/bartholomew.db") than the daemon
    ("data/barth.db") -- see docs/B0_BASELINE_REPORT.md section 1.
    """
    resolved = str(tmp_path / "resolved_by_daemon.db")
    monkeypatch.setattr(
        "bartholomew.kernel.daemon._default_db_path",
        lambda: resolved,
    )

    result = runner.invoke(app, ["brake", "on", "--scope", "global"])
    assert result.exit_code == 0

    # The real proof this resolved to the daemon's path rather than the old
    # "data/bartholomew.db" default: state landed in the resolved file, not
    # some other one. (Not asserting on stdout containing the full path --
    # rich's console wrapping can split a long path across lines.)
    state = GovernanceBrakeStore(resolved).current_state()
    assert state.engaged is True
