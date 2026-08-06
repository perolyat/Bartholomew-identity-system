"""
Regression test for a CI flake found on PR #38 (Stage 1, S1.4/S1.6):
bartholomew_api_bridge_v0_1/services/api/db.py's DB_PATH used to be a
module-level constant, resolved exactly once at this module's first
import. Because pytest imports it exactly once per test session, every
test file that set os.environ["BARTH_DB_PATH"] to its own private tempdir
*before* importing app.py -- each expecting an isolated database --
actually shared whichever ONE path won that "first import" race, for
every test file collected after it.

Each such file's `client` fixture starts a real KernelDaemon, including a
live background scheduler. Because those daemons silently pointed at the
SAME physical SQLite file, one file's scheduler ticks and another,
unrelated file's foreground HTTP-triggered writes (e.g.
tests/test_notifications_api.py's notify-skill settings writes) could
genuinely race each other for that one file -- this is what actually
produced the observed "database is locked" flake, not a defect in the
awaiting_response feature itself (see DECISIONS.md's corresponding entry
for the full investigation).

The fix: db.resolve_db_path() reads BARTH_DB_PATH fresh on every call,
and app.py's startup() now calls it at KernelDaemon-construction time
instead of importing the frozen DB_PATH constant. These are targeted
tests for that specific property -- not full API-bridge integration tests
(those already exist elsewhere) -- covering the exact scheduler/database
interaction that broke.
"""

from __future__ import annotations

from bartholomew_api_bridge_v0_1.services.api import db as db_module


def test_resolve_db_path_reflects_the_current_env_var_not_a_cached_value(monkeypatch, tmp_path):
    first_path = str(tmp_path / "first" / "test.db")
    second_path = str(tmp_path / "second" / "test.db")

    monkeypatch.setenv("BARTH_DB_PATH", first_path)
    assert db_module.resolve_db_path() == first_path

    # The regression this guards against: a frozen module-level constant
    # would still report first_path here, since it was only ever read
    # once, at this module's first import.
    monkeypatch.setenv("BARTH_DB_PATH", second_path)
    assert db_module.resolve_db_path() == second_path


def test_resolve_db_path_creates_the_parent_directory(monkeypatch, tmp_path):
    target = tmp_path / "does" / "not" / "exist" / "yet" / "test.db"
    assert not target.parent.exists()

    monkeypatch.setenv("BARTH_DB_PATH", str(target))
    resolved = db_module.resolve_db_path()

    assert resolved == str(target)
    assert target.parent.exists()


def test_resolve_db_path_falls_back_to_default_when_unset(monkeypatch):
    monkeypatch.delenv("BARTH_DB_PATH", raising=False)
    assert db_module.resolve_db_path() == db_module.DEFAULT_DB_PATH


def test_two_sequential_app_startups_with_different_env_get_isolated_daemons(
    monkeypatch,
    tmp_path,
):
    """
    End-to-end proof of the actual fix: two separate TestClient lifecycles
    -- mirroring how pytest runs two different API test *files*
    sequentially in one session, each with its own module-scoped `client`
    fixture -- each setting BARTH_DB_PATH before starting, must each get
    a KernelDaemon pointed at its OWN database file, not silently share
    one the way they did before app.py's startup() was fixed to call
    resolve_db_path() fresh instead of importing a frozen constant.
    """
    from fastapi.testclient import TestClient

    from bartholomew_api_bridge_v0_1.services.api import app as app_module

    first_path = str(tmp_path / "session_a" / "test.db")
    second_path = str(tmp_path / "session_b" / "test.db")

    monkeypatch.setenv("BARTH_DB_PATH", first_path)
    with TestClient(app_module.app):
        assert app_module._kernel.mem.db_path == first_path

    monkeypatch.setenv("BARTH_DB_PATH", second_path)
    with TestClient(app_module.app):
        assert app_module._kernel.mem.db_path == second_path

    assert first_path != second_path
