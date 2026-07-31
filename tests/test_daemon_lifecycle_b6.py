"""
Phase B, stage B6: process lock integration with KernelDaemon, and B5's
lifecycle tests re-verified with the lock now in place (per
docs/PHASE_B_OVERVIEW.md's B6 exit condition: "B5's lifecycle integration
tests pass again with the process lock added" -- tests/test_daemon_lifecycle_b5.py
itself already re-runs green with no changes needed beyond the one crash-
simulation fixture noted in its own file, which is the direct evidence for
that exit condition; this file adds the process-lock-specific scenarios B5's
own suite didn't cover).
"""

from __future__ import annotations

import pytest

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


@pytest.mark.asyncio
async def test_second_daemon_refused_while_first_is_running(fresh_config) -> None:
    first = _make_daemon(fresh_config)
    await first.start()
    try:
        second = _make_daemon(fresh_config)
        with pytest.raises(RuntimeError, match="already holds the process lock"):
            await second.start()

        # The refused instance must not be poisoned -- it never touched any
        # of its own sub-resources, so a caller retrying later (e.g. once
        # `first` has stopped) is retrying against a genuinely untouched
        # instance, not one whose executors are already closed.
        assert second._poisoned is False
    finally:
        await first.stop()


@pytest.mark.asyncio
async def test_second_daemon_succeeds_after_first_stops(fresh_config) -> None:
    first = _make_daemon(fresh_config)
    await first.start()
    await first.stop()

    second = _make_daemon(fresh_config)
    await second.start()
    try:
        assert second.process_lock.is_held_by_other() is False
    finally:
        await second.stop()


@pytest.mark.asyncio
async def test_lock_released_after_failed_start(fresh_config) -> None:
    """
    A daemon whose start() fails partway (after acquiring the lock) must
    release it -- otherwise a startup failure would permanently lock out
    every future start() attempt against that database.
    """

    class InjectedTestError(RuntimeError):
        pass

    first = _make_daemon(fresh_config)

    def _boom():
        raise InjectedTestError("simulated failure")

    first.narrator.subscribe_to_workspace = _boom

    with pytest.raises(InjectedTestError):
        await first.start()

    second = _make_daemon(fresh_config)
    await second.start()  # must succeed -- the lock was released by the unwind
    await second.stop()


@pytest.mark.asyncio
async def test_lock_released_only_as_the_final_shutdown_step(fresh_config) -> None:
    """
    A probe checking the lock mid-stop() (before stop() returns) should
    still see it held -- the lock is released last, per
    docs/B6_IMPLEMENTATION.md's binding to B5's lifecycle-terminal-state
    conditions. This test checks the state immediately after start()
    (clearly still held) and immediately after stop() (clearly released);
    it does not attempt to observe the exact mid-stop() window, which is
    not itself an externally awaitable point in this coroutine-based API.
    """
    from bartholomew.kernel.process_lock import ProcessLock

    daemon = _make_daemon(fresh_config)
    await daemon.start()

    checker = ProcessLock(f"{fresh_config['db_path']}.lock")
    assert checker.is_held_by_other() is True

    await daemon.stop()
    assert checker.is_held_by_other() is False
