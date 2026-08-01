"""
Phase B stage B9: adversarial validation against the integrated B0-B8
result. Unlike the existing lifecycle tests (which monkeypatch specific
functions to simulate one failure mode at a time), these exercise real
adversarial conditions -- genuine file corruption, genuinely concurrent
daemon starts, repeated crash cycles -- against the actual, integrated
KernelDaemon/GovernanceStore/ProcessLock/RequestAdmission stack. See
docs/B9_RECOVERY_ROLLBACK_ADVERSARIAL_VALIDATION.md.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from bartholomew.kernel.daemon import DaemonLifecycleState, KernelDaemon, UnsafeStartupError
from bartholomew.kernel.process_lock import ProcessLockHeldError
from bartholomew.orchestrator.safety.governance_store import GovernanceStore


@pytest.fixture
def config_files(tmp_path):
    (tmp_path / "kernel.yaml").write_text(
        'timezone: "Australia/Brisbane"\nloop_interval_seconds: 1\n',
    )
    (tmp_path / "persona.yaml").write_text('name: "Test Bartholomew"\n')
    (tmp_path / "policy.yaml").write_text("policies: []\n")
    (tmp_path / "drives.yaml").write_text("drives: []\n")
    return {
        "cfg_path": str(tmp_path / "kernel.yaml"),
        "db_path": str(tmp_path / "test.db"),
        "persona_path": str(tmp_path / "persona.yaml"),
        "policy_path": str(tmp_path / "policy.yaml"),
        "drives_path": str(tmp_path / "drives.yaml"),
    }


def _make_daemon(config_files):
    return KernelDaemon(**config_files)


def _incidents(db_path):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT lifecycle_state_reached, exception_type, final_outcome "
            "FROM startup_incidents ORDER BY id",
        ).fetchall()
    finally:
        conn.close()
    return rows


# -- Real (not monkeypatched) file corruption ---------------------------------


async def test_genuinely_corrupted_database_fails_real_quick_check_and_aborts_startup(
    config_files,
):
    """Unlike test_daemon_lifecycle_integrity.py's monkeypatched
    equivalent, this corrupts real bytes in a real SQLite file and
    confirms PRAGMA quick_check genuinely detects it -- proving the
    mechanism, not just the code path that reacts to it."""
    db_path = config_files["db_path"]

    # Bring a real database into existence with real Phase B schema, then
    # simulate a crash (no clean shutdown marker).
    daemon = _make_daemon(config_files)
    await daemon.start()
    await daemon.stop()

    store = GovernanceStore(db_path)
    store.open_new_runtime()  # unclean marker: no mark_clean_shutdown() follows

    # Corrupt real bytes in the middle of the file -- past the header,
    # inside actual page data, so PRAGMA quick_check has real damage to find.
    with open(db_path, "r+b") as f:
        f.seek(4096)
        f.write(b"\xff" * 256)

    daemon2 = _make_daemon(config_files)
    with pytest.raises(UnsafeStartupError):
        await daemon2.start()

    assert daemon2.lifecycle_state is DaemonLifecycleState.FAILED
    rows = _incidents(db_path)
    assert rows[-1][2] == "failed"


# -- Genuinely concurrent daemon starts (not sequential) -----------------------


async def test_truly_concurrent_daemon_starts_only_one_wins(config_files):
    """Phase B stage B6's process lock as a real mutual-exclusion
    primitive under real concurrency -- asyncio.gather runs both start()
    calls interleaved on the same event loop, not one after the other,
    exercising the lock's actual exclusion property rather than a
    sequential "second call after the first already succeeded" check."""
    daemon_a = _make_daemon(config_files)
    daemon_b = _make_daemon(config_files)

    results = await asyncio.gather(
        daemon_a.start(),
        daemon_b.start(),
        return_exceptions=True,
    )

    succeeded = [r for r in results if r is None]
    failed = [r for r in results if isinstance(r, BaseException)]

    assert len(succeeded) == 1
    assert len(failed) == 1
    assert isinstance(failed[0], ProcessLockHeldError)

    winner = daemon_a if daemon_a.lifecycle_state is DaemonLifecycleState.RUNNING else daemon_b
    await winner.stop()


# -- Repeated crash/recovery cycles --------------------------------------------


async def test_repeated_unclean_shutdowns_each_recorded_independently(config_files):
    """Two crashes in a row (not just one) -- each must be independently
    detected, recovered from, and logged, not conflated or silently
    skipped after the first recovery."""
    db_path = config_files["db_path"]

    for _cycle in range(2):
        store = GovernanceStore(db_path)
        store.open_new_runtime()  # crash: no mark_clean_shutdown()

        daemon = _make_daemon(config_files)
        await daemon.start()
        assert daemon.lifecycle_state is DaemonLifecycleState.RUNNING
        await daemon.stop()
        assert daemon.governance_store.runtime_marker().clean is True

    rows = _incidents(db_path)
    assert len(rows) == 2
    assert all(outcome == "started_after_recovery" for *_rest, outcome in rows)


# -- Admission draining under a governance-writing in-flight request ----------


async def test_stop_drains_an_admitted_request_that_itself_writes_governance(config_files):
    """A more adversarial version of B7's own drain test: the in-flight
    admitted "request" doesn't just sleep, it performs a real Governance
    write (engage()) against the daemon's own shared store while stop()
    is concurrently trying to close the write fence -- proving the
    ordering (admission drains BEFORE the write fence closes) actually
    holds under real contention, not just in the comment that documents
    it."""
    daemon = _make_daemon(config_files)
    await daemon.start()

    token = daemon.admission.try_admit()
    write_succeeded = False
    write_error: Exception | None = None

    async def in_flight_request():
        nonlocal write_succeeded, write_error
        await asyncio.sleep(0.05)
        try:
            daemon.governance_store.engage("skills", reason="in-flight request")
            write_succeeded = True
        except Exception as e:
            write_error = e
        finally:
            daemon.admission.release(token)

    request_task = asyncio.create_task(in_flight_request())
    await daemon.stop()
    await request_task

    # The write must have completed successfully -- the fence must not
    # have closed until after admission finished draining.
    assert write_succeeded is True
    assert write_error is None
