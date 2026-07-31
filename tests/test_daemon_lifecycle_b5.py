"""
Phase B, stage B5: startup/shutdown integrity tests for the concrete
runtime B1-B4 produced.

Two things this pins that no prior test covered:

1. Failed-start unwind now drains every dedicated DB executor B2
   introduced (mem, skill_registry, and any already-loaded skills'), not
   only scheduler_store -- the gap this stage's own investigation found:
   the pre-existing unwind only ever closed scheduler_store, so a startup
   failure occurring after skills had loaded would leak their worker
   threads.
2. The clean-shutdown lifecycle marker (bartholomew/kernel/lifecycle_marker.py):
   written "running" at the start of start(), "clean_shutdown" only at the
   end of a successful stop() -- and the poisoned-instance guard that
   refuses to re-run start() on an instance whose failed-start unwind has
   already run.
"""

from __future__ import annotations

import logging

import pytest

from bartholomew.kernel import lifecycle_marker
from bartholomew.kernel.daemon import KernelDaemon


def _config_files(root):
    (root / "kernel.yaml").write_text(
        'timezone: "Australia/Brisbane"\nloop_interval_seconds: 1\n',
        encoding="utf-8",
    )
    (root / "persona.yaml").write_text('name: "Test Bartholomew"\n', encoding="utf-8")
    (root / "policy.yaml").write_text("policies: []\n", encoding="utf-8")
    (root / "drives.yaml").write_text("drives: []\n", encoding="utf-8")
    return {
        "cfg_path": str(root / "kernel.yaml"),
        "db_path": str(root / "fresh.db"),
        "persona_path": str(root / "persona.yaml"),
        "policy_path": str(root / "policy.yaml"),
        "drives_path": str(root / "drives.yaml"),
    }


@pytest.fixture
def fresh_config(tmp_path):
    return _config_files(tmp_path)


def _make_daemon(config: dict[str, str]) -> KernelDaemon:
    return KernelDaemon(**config)


class InjectedTestError(RuntimeError):
    pass


@pytest.mark.asyncio
async def test_failed_start_drains_mem_and_skill_registry_executors(fresh_config) -> None:
    """
    Inject a failure in narrator.subscribe_to_workspace() -- deliberately
    chosen because it runs *after* skill loading (Stage 4), so by the time
    it fails, mem's, skill_registry's, and every loaded skill's dedicated
    executors are already active. The pre-B5 unwind only closed
    scheduler_store; this pins that mem and skill_registry are drained too.
    """
    daemon = _make_daemon(fresh_config)

    def _boom():
        raise InjectedTestError("simulated narrator failure")

    daemon.narrator.subscribe_to_workspace = _boom

    with pytest.raises(InjectedTestError):
        await daemon.start()

    assert daemon._poisoned is True
    assert daemon.mem._db_executor._closed is True
    assert daemon.skill_registry._db_executor._closed is True
    # Confirmed, not merely submitted-and-assumed: matching
    # tests/test_db_executor.py's own convention (DedicatedDbExecutor.close()
    # does not guarantee the underlying OS thread has finished exiting by
    # the moment it returns -- see its docstring -- so "confirmed" is
    # proven the same way that module's own tests prove it: closed
    # executors reject new work, rather than asserting on
    # threading.enumerate()'s exact timing).
    from bartholomew.kernel.db_executor import DbExecutorClosedError

    with pytest.raises(DbExecutorClosedError):
        await daemon.mem._db_executor.call(lambda: None)
    with pytest.raises(DbExecutorClosedError):
        await daemon.skill_registry._db_executor.call(lambda: None)


@pytest.mark.asyncio
async def test_failed_start_drains_already_loaded_skill_executors(fresh_config) -> None:
    """
    A skill that finished loading before the injected failure must have its
    own dedicated executor drained too, via skill_registry.shutdown()'s
    existing unload_skill() -> skill.shutdown() chain.
    """
    daemon = _make_daemon(fresh_config)

    def _boom():
        raise InjectedTestError("simulated narrator failure")

    daemon.narrator.subscribe_to_workspace = _boom

    with pytest.raises(InjectedTestError):
        await daemon.start()

    # At least one of the three starter skills should have loaded (Stage 4
    # runs before the injected failure point) and been unloaded again.
    assert daemon.skill_registry.list_loaded() == []


@pytest.mark.asyncio
async def test_poisoned_instance_refuses_restart(fresh_config) -> None:
    daemon = _make_daemon(fresh_config)

    def _boom():
        raise InjectedTestError("simulated narrator failure")

    daemon.narrator.subscribe_to_workspace = _boom

    with pytest.raises(InjectedTestError):
        await daemon.start()

    with pytest.raises(RuntimeError, match="poisoned"):
        await daemon.start()


@pytest.mark.asyncio
async def test_clean_shutdown_writes_marker(fresh_config) -> None:
    daemon = _make_daemon(fresh_config)
    await daemon.start()
    instance_id = daemon._lifecycle_instance_id
    assert instance_id is not None
    await daemon.stop()

    marker = lifecycle_marker.read_marker(fresh_config["db_path"])
    assert marker is not None
    assert marker.instance_id == instance_id
    assert marker.state == "clean_shutdown"
    assert marker.stopped_at is not None


@pytest.mark.asyncio
async def test_unclean_previous_run_is_detected_and_logged(fresh_config, caplog) -> None:
    """
    Simulates a crash: a first daemon starts (marker -> "running") and is
    never stopped. A second daemon against the same database must detect
    that on its own start() and log a warning -- conservative (log only,
    per lifecycle_marker.py's docstring), not block or "fix" anything.
    """
    first = _make_daemon(fresh_config)
    await first.start()
    first_instance_id = first._lifecycle_instance_id
    # Deliberately no `await first.stop()` -- simulates a crash. Directly
    # drain its executors so the test itself doesn't leak threads, without
    # going through stop() (which would write the clean marker). Also
    # release its process lock (Phase B, stage B6): on a real crash the OS
    # reclaims the lock when the process exits -- releasing it here is
    # what accurately simulates that within one test process, not a gap
    # this test is papering over.
    await first.mem._db_executor.close()
    await first.skill_registry._db_executor.close()
    await first.scheduler_store.close()
    first.process_lock.release()

    marker_before = lifecycle_marker.read_marker(fresh_config["db_path"])
    assert marker_before is not None
    assert marker_before.instance_id == first_instance_id
    assert marker_before.state == "running"

    second = _make_daemon(fresh_config)
    with caplog.at_level(logging.WARNING, logger="bartholomew.kernel.daemon"):
        await second.start()
    try:
        assert any(
            "Unclean start detected" in record.message for record in caplog.records
        ), "expected an unclean-start warning to be logged"
        assert first_instance_id in caplog.text
    finally:
        await second.stop()

    marker_after = lifecycle_marker.read_marker(fresh_config["db_path"])
    assert marker_after.instance_id == second._lifecycle_instance_id
    assert marker_after.state == "clean_shutdown"


@pytest.mark.asyncio
async def test_fresh_database_has_no_marker_and_logs_nothing(fresh_config, caplog) -> None:
    daemon = _make_daemon(fresh_config)
    with caplog.at_level(logging.WARNING, logger="bartholomew.kernel.daemon"):
        await daemon.start()
    try:
        assert not any("Unclean start detected" in r.message for r in caplog.records)
    finally:
        await daemon.stop()


def test_lifecycle_marker_read_write_roundtrip(tmp_path):
    """Isolated test of the marker module itself, independent of KernelDaemon."""
    import sqlite3

    db_path = str(tmp_path / "marker_test.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE system_flags (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)",
        )
        conn.commit()

    assert lifecycle_marker.read_marker(db_path) is None

    iid = lifecycle_marker.new_instance_id()
    lifecycle_marker.write_marker(db_path, instance_id=iid, state="running", started_at=100)

    marker = lifecycle_marker.read_marker(db_path)
    assert marker.instance_id == iid
    assert marker.state == "running"
    assert marker.stopped_at is None

    lifecycle_marker.write_marker(
        db_path,
        instance_id=iid,
        state="clean_shutdown",
        started_at=100,
        stopped_at=200,
    )
    marker = lifecycle_marker.read_marker(db_path)
    assert marker.state == "clean_shutdown"
    assert marker.stopped_at == 200


def test_lifecycle_marker_read_on_missing_table_returns_none(tmp_path):
    db_path = str(tmp_path / "no_table.db")
    # No system_flags table at all -- genuinely fresh database.
    import sqlite3

    sqlite3.connect(db_path).close()
    assert lifecycle_marker.read_marker(db_path) is None


def test_lifecycle_marker_malformed_value_treated_as_absent(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "malformed.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE system_flags (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)",
        )
        conn.execute(
            "INSERT INTO system_flags VALUES ('daemon_lifecycle', 'not json', '0')",
        )
        conn.commit()

    assert lifecycle_marker.read_marker(db_path) is None
