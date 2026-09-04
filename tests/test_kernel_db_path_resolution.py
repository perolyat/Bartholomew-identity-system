"""The operator's brake and the running server name the same database.

Found on the live Windows golden path (docs/G_WINDOWS_COMPANION_COMPLETION.md
§9, Finding 4): `bartholomew brake on` defaulted `--db` to a literal
"data/bartholomew.db" -- a file the server had never opened -- and printed
"ENGAGED" while the server, which honours BARTH_DB_PATH, carried on
dispatching. An emergency stop that appears to work and does nothing.

These tests hold the repair: one resolver, every surface delegating to it,
and the brake CLI without `--db` engaging the brake the server actually reads
-- in both configurations (variable set, variable unset). An explicit `--db`
still wins, because tests and per-user runtimes depend on that.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bartholomew.cli import app
from bartholomew.kernel import db_paths
from bartholomew.kernel.daemon import _default_db_path
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from bartholomew_api_bridge_v0_1.services.api import db as bridge_db

runner = CliRunner()


# ---------------------------------------------------------------------------
# The resolver itself
# ---------------------------------------------------------------------------


def test_explicit_path_wins_over_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("BARTH_DB_PATH", str(tmp_path / "env.db"))
    assert db_paths.resolve_kernel_db_path(str(tmp_path / "mine.db")) == str(tmp_path / "mine.db")


def test_environment_wins_over_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("BARTH_DB_PATH", str(tmp_path / "env.db"))
    assert db_paths.resolve_kernel_db_path() == str(tmp_path / "env.db")


def test_default_is_the_project_root_barth_db(monkeypatch):
    monkeypatch.delenv("BARTH_DB_PATH", raising=False)
    resolved = Path(db_paths.resolve_kernel_db_path(create_parent=False))
    assert resolved.name == "barth.db"
    assert resolved.parent.name == "data"
    assert (resolved.parent.parent / "pyproject.toml").exists()


def test_blank_environment_value_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("BARTH_DB_PATH", "   ")
    assert db_paths.resolve_kernel_db_path(create_parent=False) == db_paths.default_kernel_db_path()


def test_a_bare_filename_does_not_crash_on_parent_creation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert db_paths.resolve_kernel_db_path("barth.db") == "barth.db"


def test_resolution_is_read_fresh_not_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("BARTH_DB_PATH", str(tmp_path / "first.db"))
    assert db_paths.resolve_kernel_db_path() == str(tmp_path / "first.db")
    monkeypatch.setenv("BARTH_DB_PATH", str(tmp_path / "second.db"))
    assert db_paths.resolve_kernel_db_path() == str(tmp_path / "second.db")


def test_describe_names_the_source(monkeypatch, tmp_path):
    monkeypatch.setenv("BARTH_DB_PATH", str(tmp_path / "env.db"))
    assert db_paths.describe_kernel_db_path()["source"] == "BARTH_DB_PATH"
    assert db_paths.describe_kernel_db_path("x.db")["source"] == "explicit"
    monkeypatch.delenv("BARTH_DB_PATH", raising=False)
    assert db_paths.describe_kernel_db_path()["source"] == "default"


# ---------------------------------------------------------------------------
# Every surface delegates to it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_set", [True, False])
def test_server_daemon_and_resolver_agree(monkeypatch, tmp_path, env_set):
    if env_set:
        monkeypatch.setenv("BARTH_DB_PATH", str(tmp_path / "shared.db"))
    else:
        monkeypatch.delenv("BARTH_DB_PATH", raising=False)
    expected = db_paths.resolve_kernel_db_path(create_parent=False)
    assert bridge_db.resolve_db_path() == expected
    assert _default_db_path() == expected


def test_bridge_default_constant_is_the_shared_default():
    assert bridge_db.DEFAULT_DB_PATH == db_paths.default_kernel_db_path()


# ---------------------------------------------------------------------------
# The brake CLI reaches the server's database
# ---------------------------------------------------------------------------


def test_brake_on_without_db_engages_the_database_the_server_reads(monkeypatch, tmp_path):
    """The exact live failure, reproduced and held closed: with BARTH_DB_PATH
    set, `brake on` with no --db must engage the brake in that file -- the one
    the server's own resolver returns -- not in a scratch file."""
    server_db = str(tmp_path / "live" / "barth.db")
    monkeypatch.setenv("BARTH_DB_PATH", server_db)
    monkeypatch.chdir(tmp_path)  # a relative scratch default would land here

    result = runner.invoke(app, ["brake", "on"])
    assert result.exit_code == 0, result.output
    assert "ENGAGED" in result.output
    assert server_db in result.output

    # What the server reads, through the server's own resolver.
    assert GovernanceStore(bridge_db.resolve_db_path()).is_blocked("actuation")
    # And no scratch file appeared where the old default would have written.
    assert not (tmp_path / "data" / "bartholomew.db").exists()

    result = runner.invoke(app, ["brake", "off"])
    assert result.exit_code == 0, result.output
    assert not GovernanceStore(server_db).refresh().engaged


def test_brake_status_without_db_reports_the_servers_database(monkeypatch, tmp_path):
    server_db = str(tmp_path / "barth.db")
    monkeypatch.setenv("BARTH_DB_PATH", server_db)
    result = runner.invoke(app, ["brake", "status"])
    assert result.exit_code == 0, result.output
    assert server_db in result.output
    assert "BARTH_DB_PATH" in result.output


def test_brake_without_db_and_without_env_uses_the_project_default(monkeypatch, tmp_path):
    """With the variable unset both surfaces fall back to the same project
    file. Redirect the project default into tmp_path so the test does not
    touch the repository's real data/ directory."""
    monkeypatch.delenv("BARTH_DB_PATH", raising=False)
    monkeypatch.setattr(db_paths, "find_project_root", lambda: tmp_path)
    expected = str(tmp_path / "data" / "barth.db")
    assert bridge_db.resolve_db_path() == expected

    result = runner.invoke(app, ["brake", "status"])
    assert result.exit_code == 0, result.output
    assert expected in result.output
    assert "(from default)" in result.output


def test_explicit_db_still_wins_on_the_cli(monkeypatch, tmp_path):
    """Tests and per-user runtimes rely on this: a shell with BARTH_DB_PATH
    set must still be able to address a specific file."""
    monkeypatch.setenv("BARTH_DB_PATH", str(tmp_path / "env.db"))
    mine = str(tmp_path / "mine.db")

    result = runner.invoke(app, ["brake", "on", "--db", mine])
    assert result.exit_code == 0, result.output
    assert GovernanceStore(mine).is_blocked("global")
    # The environment's database was not touched by an explicit --db.
    assert not os.path.exists(str(tmp_path / "env.db"))
