"""
The scheduler's drive seam must never lose a cancellation.

Invariant (docs/SCHEDULER_DRIVE_CANCELLATION_REPAIR.md): one cancellation of
the task running a drive through
`runtime_contract.run_drive_through_runtime_contract()` propagates out of the
seam, whatever instant it lands at -- including the instant the drive
completes -- and the drive has finished by the time the seam returns or
raises. So one `task.cancel()` stops `run_scheduler()`, and nothing is left
running behind it.

CPython 3.10/3.11's `asyncio.wait_for()` breaks that at one instant: when the
inner future finished in the same loop iteration as the cancellation, it
returns the inner result instead of raising (`except CancelledError: if
fut.done(): return fut.result()`). The seam used wait_for, so on 3.10/3.11 a
single cancel could be swallowed and the scheduler kept running -- in PR
#125's acceptance run 2 that stranded an xdist worker until the 120 s
per-test timeout killed it.

The race is forced deterministically: the drive requests the cancellation
0-2 `call_soon` hops before it returns, which lands it in the iterations in
which wait_for's swallow branch runs. `test_control_*` proves this harness
really hits the stdlib swallow on 3.10/3.11 (and that 3.12+ does not have
it), so the tests that follow cannot pass without exercising the race.

Every wait on a task here is bounded (`asyncio.wait(..., timeout=...)` plus
an assertion), never an unbounded await: a regression fails the test instead
of hanging the worker.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import logging
import random
import sqlite3
import sys
import time

import pytest

import bartholomew.kernel.runtime_contract as rc
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.scheduler import drives as drives_module
from bartholomew.kernel.scheduler import loop as loop_module

BOUND_S = 5.0
SWALLOWS = sys.version_info < (3, 12)  # stdlib wait_for's swallow branch


def _after_hops(hops, fn):
    """Call fn after `hops` call_soon hops; 0 calls it now."""
    if hops == 0:
        fn()
    else:
        asyncio.get_running_loop().call_soon(_after_hops, hops - 1, fn)


async def _finish(task, timeout=BOUND_S):
    """Bounded wait for a task; True if it finished. On False, cancel it and
    give it one more bound so the test never leaves it running."""
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        task.cancel()
        await asyncio.wait({task}, timeout=timeout)
    return bool(done)


class _RecordingMem:
    """Duck-typed `.mem`: a real db_path (with schema, for the Parking Brake
    read) plus an `insert_reflection` that records each drive outcome."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.outcomes = []

    async def insert_reflection(self, *, kind, content, meta, ts):
        self.outcomes.append(meta["outcome"])
        return len(self.outcomes)


class _Ctx:
    def __init__(self, mem):
        self.mem = mem


@pytest.fixture
async def ctx(tmp_path):
    db_path = str(tmp_path / "seam.db")
    await MemoryStore(db_path).init()
    return _Ctx(_RecordingMem(db_path))


def _seam(ctx, drive, *, timeout=BOUND_S, task_id="self_check"):
    return asyncio.create_task(
        rc.run_drive_through_runtime_contract(ctx, task_id, drive, timeout=timeout),
    )


# -- Causal control: the harness reaches the stdlib swallow ------------------


async def _model_scheduler(await_drive, hops):
    """run_scheduler's shape reduced to its cancellation contract: a
    while-True loop that stops only on CancelledError. Its first drive
    requests a cancel of this task `hops` call_soon hops before returning.
    Returns 'stopped', or 'ran-away' if the cancel was lost."""
    me = asyncio.current_task()
    runs = 0

    async def drive():
        nonlocal runs
        runs += 1
        await asyncio.sleep(0)
        if runs == 1:
            _after_hops(hops, me.cancel)
        return "nudge"

    for _ in range(50):
        try:
            await await_drive(drive(), BOUND_S)
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            return "stopped"
    return "ran-away"


def _stdlib_wait_for(aw, timeout):
    return asyncio.wait_for(aw, timeout=timeout)


def _repaired_await_drive(aw, timeout):
    return rc._await_drive(aw, timeout, "model")


@pytest.mark.parametrize("hops", [0, 1, 2])
async def test_control_stdlib_wait_for_loses_the_cancel_only_before_3_12(hops):
    task = asyncio.create_task(_model_scheduler(_stdlib_wait_for, hops))
    assert await _finish(task)
    expected = "ran-away" if SWALLOWS else "stopped"
    assert task.result() == expected, (
        f"harness did not reproduce CPython {sys.version_info[:2]}'s wait_for "
        f"behaviour at hops={hops}; the regression tests below would prove nothing"
    )


@pytest.mark.parametrize("hops", [0, 1, 2])
async def test_the_seam_helper_never_loses_the_cancel(hops):
    task = asyncio.create_task(_model_scheduler(_repaired_await_drive, hops))
    assert await _finish(task)
    assert task.result() == "stopped"


# -- The seam: a cancel landing as the drive completes -----------------------


@pytest.mark.parametrize("hops", [0, 1, 2])
async def test_a_cancel_landing_as_the_drive_completes_propagates(ctx, hops, caplog):
    caplog.set_level(logging.INFO, logger=rc.__name__)
    box = {}

    async def drive(_ctx):
        box["drive"] = asyncio.current_task()
        await asyncio.sleep(0)
        _after_hops(hops, box["seam"].cancel)
        return "nudge"

    box["seam"] = seam = _seam(ctx, drive)
    assert await _finish(seam)

    # Forbidden: the seam returning ('nudge', 1), a 'completed' reflection
    # for a drive whose tick will not be recorded, or a drive left running.
    assert seam.cancelled(), f"cancel swallowed; seam returned {seam.result()!r}"
    assert ctx.mem.outcomes == []
    assert box["drive"].done()
    assert box["drive"].get_name() == "scheduler-drive:self_check"
    assert "completed while its caller was being cancelled" in caplog.text


async def test_a_drive_that_raises_as_the_cancel_lands_is_logged_not_leaked(ctx, caplog):
    caplog.set_level(logging.INFO)
    box = {}

    async def drive(_ctx):
        await asyncio.sleep(0)
        box["seam"].cancel()
        raise ValueError("drive failed at the last moment")

    box["seam"] = seam = _seam(ctx, drive)
    assert await _finish(seam)
    assert seam.cancelled()
    assert ctx.mem.outcomes == []  # no 'error' reflection: cancellation won

    superseded = [
        r for r in caplog.records if "raised while its caller was being cancelled" in r.getMessage()
    ]
    assert len(superseded) == 1 and superseded[0].exc_info is not None

    gc.collect()
    assert "exception was never retrieved" not in caplog.text


async def test_a_cancel_while_the_drive_runs_cancels_it_before_propagating(ctx):
    seen = []
    started = asyncio.Event()

    async def drive(_ctx):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            seen.append("drive saw the cancel")
            raise

    seam = _seam(ctx, drive)
    await asyncio.wait_for(started.wait(), BOUND_S)
    seam.cancel()
    assert await _finish(seam)
    assert seam.cancelled()
    assert seen == ["drive saw the cancel"]
    assert ctx.mem.outcomes == []


def _slow_cleanup_drive(seen, *, cleanup_s=0.3, started=None, on_cancel_return=None):
    """A drive whose cancellation cleanup takes `cleanup_s`, catching further
    cancellations so the cleanup completes. It re-raises CancelledError, or
    returns `on_cancel_return` when that is set."""

    async def drive(_ctx):
        if started is not None:
            started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            deadline = asyncio.get_running_loop().time() + cleanup_s
            while asyncio.get_running_loop().time() < deadline:
                try:
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    seen.append("further cancel reached the drive")
            seen.append("cleanup finished")
            if on_cancel_return is not None:
                return on_cancel_return
            raise

    return drive


async def test_a_second_cancel_during_cleanup_never_strands_the_drive(ctx):
    """Stdlib 3.10/3.11 wait_for raises at once on the second cancel and
    leaves the drive running; the scheduler task could then end with its
    drive still writing."""
    seen = []
    started = asyncio.Event()
    box = {}
    seam = _seam(ctx, _slow_cleanup_drive(seen, started=started))
    await asyncio.wait_for(started.wait(), BOUND_S)
    seam.add_done_callback(lambda _t: box.setdefault("seen_at_seam_end", list(seen)))
    seam.cancel()
    await asyncio.sleep(0.1)
    seam.cancel()
    assert await _finish(seam)
    assert seam.cancelled()
    assert "further cancel reached the drive" in seen
    assert box["seen_at_seam_end"][-1] == "cleanup finished"
    assert ctx.mem.outcomes == []


async def test_a_cancel_during_the_timeout_settle_waits_for_the_drive(ctx):
    seen = []
    box = {}
    seam = _seam(ctx, _slow_cleanup_drive(seen), timeout=0.05)
    seam.add_done_callback(lambda _t: box.setdefault("seen_at_seam_end", list(seen)))
    await asyncio.sleep(0.15)  # the timeout has fired; the drive is cleaning up
    seam.cancel()
    assert await _finish(seam)
    assert seam.cancelled()
    assert box["seen_at_seam_end"][-1] == "cleanup finished"
    assert ctx.mem.outcomes == []


async def test_a_drive_that_suppresses_the_cancel_still_does_not_lose_it(ctx, caplog):
    """The drive catches the cancellation and returns. The caller's
    cancellation is still raised: stdlib 3.12+ wait_for would return here."""
    caplog.set_level(logging.INFO, logger=rc.__name__)
    started = asyncio.Event()

    async def drive(_ctx):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            return "late"

    seam = _seam(ctx, drive)
    await asyncio.wait_for(started.wait(), BOUND_S)
    seam.cancel()
    assert await _finish(seam)
    assert seam.cancelled()
    assert ctx.mem.outcomes == []
    assert "completed while its caller was being cancelled" in caplog.text


async def test_a_drive_that_is_slow_to_settle_is_reported_and_still_awaited(
    ctx,
    caplog,
    monkeypatch,
):
    monkeypatch.setattr(rc, "_DRIVE_SETTLE_WARN_S", 0.05)
    seen = []
    started = asyncio.Event()
    box = {}
    seam = _seam(ctx, _slow_cleanup_drive(seen, cleanup_s=0.4, started=started))
    await asyncio.wait_for(started.wait(), BOUND_S)
    seam.add_done_callback(lambda _t: box.setdefault("seen_at_seam_end", list(seen)))
    seam.cancel()
    assert await _finish(seam)
    assert seam.cancelled()
    assert box["seen_at_seam_end"][-1] == "cleanup finished"  # never abandoned
    assert "has not finished" in caplog.text
    assert "further cancel reached the drive" not in seen  # the timer never re-cancels


async def test_a_drive_that_ends_cancelled_on_its_own_still_propagates(ctx):
    """A drive whose own await is cancelled by a third party ends cancelled;
    that stops the scheduler today on every Python, and still does."""
    victim = asyncio.get_running_loop().create_future()

    async def drive(_ctx):
        asyncio.get_running_loop().call_soon(victim.cancel)
        await victim

    seam = _seam(ctx, drive)
    assert await _finish(seam)
    assert seam.cancelled()
    assert ctx.mem.outcomes == []


# -- Unchanged behaviour: success, crash, timeout ----------------------------


async def test_a_successful_drive_is_unchanged(ctx):
    async def drive(_ctx):
        await asyncio.sleep(0)
        return "nudge"

    assert await rc.run_drive_through_runtime_contract(ctx, "self_check", drive, timeout=1.0) == (
        "nudge",
        1,
    )
    assert ctx.mem.outcomes == ["completed"]


async def test_a_crashing_drive_is_unchanged(ctx, caplog):
    async def drive(_ctx):
        raise ValueError("boom")

    assert await rc.run_drive_through_runtime_contract(ctx, "self_check", drive, timeout=1.0) == (
        None,
        0,
    )
    assert ctx.mem.outcomes == ["error"]
    assert "Drive self_check crashed" in caplog.text


async def test_a_timeout_still_fails_the_drive_and_cancels_it(ctx, caplog):
    seen = []
    box = {}

    async def drive(_ctx):
        box["drive"] = asyncio.current_task()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            seen.append("drive saw the cancel")
            raise

    result = await rc.run_drive_through_runtime_contract(ctx, "self_check", drive, timeout=0.05)
    assert result == (None, 0)
    assert ctx.mem.outcomes == ["timeout"]
    assert "Drive self_check timed out after" in caplog.text
    assert seen == ["drive saw the cancel"]
    assert box["drive"].done()


async def test_a_drive_that_completes_while_timing_out_keeps_its_result(ctx):
    seen = []
    result = await rc.run_drive_through_runtime_contract(
        ctx,
        "self_check",
        _slow_cleanup_drive(seen, cleanup_s=0.05, on_cancel_return="late"),
        timeout=0.05,
    )
    assert result == ("late", 1)  # asyncio.wait_for parity
    assert ctx.mem.outcomes == ["completed"]


async def test_a_drive_that_raises_while_timing_out_is_an_error(ctx):
    async def drive(_ctx):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise ValueError("failed while being cancelled") from None

    result = await rc.run_drive_through_runtime_contract(ctx, "self_check", drive, timeout=0.05)
    assert result == (None, 0)  # asyncio.wait_for parity
    assert ctx.mem.outcomes == ["error"]


@pytest.mark.parametrize("timeout", [0, -1])
async def test_a_non_positive_timeout_never_starts_the_drive(ctx, timeout):
    started = []

    async def drive(_ctx):
        started.append(True)
        return "nudge"

    result = await rc.run_drive_through_runtime_contract(
        ctx,
        "self_check",
        drive,
        timeout=timeout,
    )
    assert result == (None, 0)  # asyncio.wait_for parity
    assert started == []
    assert ctx.mem.outcomes == ["timeout"]


# -- Stress: a single cancel at a random instant is never lost ---------------


async def _random_model_scheduler(await_drive, rng):
    """run_scheduler's cancellation contract with random drive lengths and one
    cancel of this task at a random call_soon hop. 'stopped' / 'ran-away'."""
    me = asyncio.current_task()
    drive_tasks = []

    async def drive():
        drive_tasks.append(asyncio.current_task())
        for _ in range(rng.randrange(4)):
            await asyncio.sleep(0)
        return "nudge"

    _after_hops(rng.randrange(40), me.cancel)
    for _ in range(200):
        try:
            await await_drive(drive(), BOUND_S)
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            return "stopped", drive_tasks
    return "ran-away", drive_tasks


async def _stress(await_drive, trials, seed):
    rng = random.Random(seed)
    outcomes = []
    for _ in range(trials):
        task = asyncio.create_task(_random_model_scheduler(await_drive, rng))
        assert await _finish(task)
        verdict, drive_tasks = task.result()
        outcomes.append((verdict, sum(1 for t in drive_tasks if not t.done())))
    return outcomes


async def test_stress_a_random_single_cancel_is_never_lost_and_strands_nothing():
    outcomes = await _stress(_repaired_await_drive, trials=500, seed=20260930)
    assert [o for o in outcomes if o != ("stopped", 0)] == []


async def test_stress_control_the_same_harness_loses_cancels_to_stdlib_before_3_12():
    outcomes = await _stress(_stdlib_wait_for, trials=200, seed=20260930)
    lost = sum(1 for verdict, _ in outcomes if verdict == "ran-away")
    if SWALLOWS:
        assert lost > 0, "the stress harness never reached the stdlib swallow"
    else:
        assert lost == 0


# -- The real scheduler: one cancel stops run_scheduler ----------------------


def _write_config(tmp_path):
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


def _ticks(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT task_id FROM ticks").fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize("hops", [0, 1])
async def test_one_cancel_landing_as_a_drive_completes_stops_the_real_scheduler(
    tmp_path,
    monkeypatch,
    hops,
):
    """The acceptance-run-2 hang, deterministically: before the repair this
    scheduler kept running on 3.10/3.11 and wrote further ticks."""
    from bartholomew.kernel.daemon import KernelDaemon

    monkeypatch.setattr(loop_module, "DRIVE_PACE_S", 0.0)
    runs = {"aligned": 0, "other": 0}
    box = {}

    async def aligned(_ctx):
        runs["aligned"] += 1
        await asyncio.sleep(0)
        if runs["aligned"] == 1:
            _after_hops(hops, box["task"].cancel)

    async def other(_ctx):
        runs["other"] += 1

    registry = {
        "aligned": {"fn": aligned, "cadence": "every:3600"},
        "zz_other": {"fn": other, "cadence": "every:1"},
    }
    monkeypatch.setattr(drives_module, "resolve_registry", lambda _ctx: dict(registry))

    cfg = _write_config(tmp_path)
    daemon = KernelDaemon(**cfg)
    box["task"] = task = asyncio.create_task(loop_module.run_scheduler(daemon))
    try:
        stopped = await _finish(task)
    finally:
        drained = await daemon.scheduler_store.close()

    assert stopped, "a single cancel did not stop run_scheduler"
    # run_scheduler's contract (loop.py `except CancelledError: break`) is to
    # return, not to end cancelled; KernelDaemon's done-callback relies on it.
    assert not task.cancelled() and task.result() is None
    assert runs == {"aligned": 1, "other": 0}
    assert _ticks(cfg["db_path"]) == []
    assert drained is True


@pytest.mark.slow
async def test_stress_one_cancel_at_a_random_instant_stops_the_real_scheduler(
    tmp_path,
    monkeypatch,
):
    """The natural race, with the real always-on drives and pacing off: one
    cancel at a random instant in the first 150 ms. Before the repair about
    1 in 30 such cancels was lost on 3.11."""
    from bartholomew.kernel.daemon import KernelDaemon

    monkeypatch.setattr(loop_module, "DRIVE_PACE_S", 0.0)
    rng = random.Random(20260930)
    lost = []
    for trial in range(120):
        trial_dir = tmp_path / f"t{trial}"
        trial_dir.mkdir()
        daemon = KernelDaemon(**_write_config(trial_dir))
        task = asyncio.create_task(loop_module.run_scheduler(daemon))
        await asyncio.sleep(rng.uniform(0.0, 0.15))
        task.cancel()
        try:
            if not await _finish(task):
                lost.append(trial)
        finally:
            await daemon.scheduler_store.close()
    assert lost == [], f"single cancels lost in trials {lost}"


# -- KernelDaemon.stop(): the production caller ------------------------------


async def test_daemon_stop_is_not_slowed_by_a_drive_completing_as_it_cancels(
    tmp_path,
    monkeypatch,
    capsys,
):
    """stop() cancels the scheduler in the step right after
    skill_registry.shutdown(); a drive completing in that step used to
    swallow the cancel on 3.10/3.11, costing stop() its full 5 s bound and
    running further drives after the skills were unloaded."""
    from bartholomew.kernel.daemon import KernelDaemon

    monkeypatch.setattr(loop_module, "DRIVE_PACE_S", 0.0)
    release = asyncio.Event()
    started = asyncio.Event()
    runs = []

    async def aligned(_ctx):
        runs.append(time.monotonic())
        if len(runs) == 1:
            started.set()
            await release.wait()

    registry = {"aligned": {"fn": aligned, "cadence": "every:1"}}
    monkeypatch.setattr(drives_module, "resolve_registry", lambda _ctx: dict(registry))

    cfg = _write_config(tmp_path)
    daemon = KernelDaemon(**cfg)
    await daemon.start()
    stopper = None
    try:
        original_shutdown = daemon.skill_registry.shutdown

        async def shutdown_then_release():
            await original_shutdown()
            release.set()

        daemon.skill_registry.shutdown = shutdown_then_release
        await asyncio.wait_for(started.wait(), BOUND_S)

        t0 = time.monotonic()
        stopper = asyncio.create_task(daemon.stop())
        assert await _finish(stopper, timeout=15.0)
        elapsed = time.monotonic() - t0
    finally:
        if stopper is None or not stopper.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(daemon.stop(), 15.0)

    assert stopper.exception() is None
    assert elapsed < 4.0, f"stop() took {elapsed:.1f}s: the scheduler's cancel was lost"
    assert len(runs) == 1, "a drive ran after stop() had cancelled the scheduler"
    assert _ticks(cfg["db_path"]) == []
    assert "did not terminate within timeout" not in capsys.readouterr().out
