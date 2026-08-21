"""
WP-A1 accelerated soak: the shape of B-F001, run to a bounded stable state.

Test #1's Phase A -> Phase B observation window recorded a self-sustaining
queue: 49 new nudges (28 curiosity / 21 system-health) accumulated in an
unattended run, the pending count crossing the self-check threshold, each
subsequent self-check adding another queue-health warning, and the queue
frozen at 55 pending when the run ended. The register's closure criterion is
"representative unattended test covers at least the observed generation
pattern/window and shows no recursive/self-sustaining growth".

This module runs the REAL `run_scheduler()` loop -- the real drive registry,
the real Runtime Contract seam, the real SchedulerStore worker thread, the
real SQLite persistence -- against a virtual clock, so days of scheduler
cadence execute in seconds without touching wall-clock waiting. Nothing about
the drives, cadences or thresholds is stubbed; only `time.time()` and
`asyncio.sleep()` inside `scheduler/loop.py` and `scheduler/drives.py` are
redirected at the accelerated clock.

Two properties are asserted, and neither can be obtained by clearing rows:

  * The pending queue reaches a fixed point. It is measured before, at the
    midpoint and at the end, and the second half must add nothing.
  * The genuine obligations seeded before the run are all still there,
    unchanged, at the end.

A "before" run against the pre-WP-A1 accounting and persistence
(`test_uncontained_baseline_grows_without_bound`) is included so the
comparison is made by the test itself rather than by a claim about it.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from bartholomew.kernel.awaiting_response_store import AwaitingResponseStore
from bartholomew.kernel.scheduler import drives as drives_mod
from bartholomew.kernel.scheduler import loop as loop_mod
from bartholomew.kernel.scheduler import persistence as sp
from bartholomew.kernel.scheduler.health import (
    PENDING_NUDGE_DRIFT_THRESHOLD,
    check_drift,
    get_system_metrics,
)
from bartholomew.kernel.scheduler.store import SchedulerStore

# Test #1's observed window was a single unattended overnight-scale run
# (Phase A checkpoint -> first Phase B observation) that produced 49 new
# nudges: 28 curiosity and 21 system-health. Thirty-six simulated hours at
# the registry's real cadences (self_check every 900s, curiosity_probe twice
# an hour) covers that window with room to spare and produces several times
# Test #1's emission volume -- the floors below are asserted, not assumed.
SIMULATED_SECONDS = 36 * 3600
MAX_LOOP_ITERATIONS = 50_000

# Test #1's recorded generation over its observed window (register B-F001).
# The soak must at least reach these, or it is not representative.
TEST1_CURIOSITY_NUDGES = 28
TEST1_HEALTH_NUDGES = 21

# The soak runs the real loop over a long simulated window, so it is far more
# expensive than an ordinary unit test. pyproject's global 120s pytest-timeout
# is a deadlock safety net calibrated for ordinary tests (see its comment
# there); an intentionally long soak needs its own budget. Kept bounded rather
# than removed, so a genuine hang still fails instead of running forever.
SOAK_TIMEOUT_S = 600


class _Clock:
    """A monotonic virtual clock advanced only by the loop's own sleeps and
    by a small per-iteration step, so no test ever waits on wall time.

    Starts at real "now" rather than a fixed epoch: `upsert_scheduled_tasks`
    seeds `next_run_ts` from persistence.py's own `time.time()` (deliberately
    not redirected -- redirecting the whole stdlib module would reach far
    outside the scheduler), so the virtual clock has to begin in the same
    era or nothing is ever due.
    """

    def __init__(self, start: float | None = None):
        self.now = float(start if start is not None else time.time())

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


class _StopSoak(BaseException):
    """Escapes run_scheduler's `except Exception` guard once the simulated
    window is over. BaseException on purpose: the loop must not swallow it."""


class _MinimalCtx:
    """Mirrors the scheduler-relevant surface of a started KernelDaemon.

    `governance_store` is wired for the same reason KernelDaemon.start()
    wires it: without it the Parking Brake check in
    `run_drive_through_runtime_contract` builds a throwaway GovernanceStore
    per drive execution, and each construction runs a full `ensure_schema`
    pass. Reusing one instance is what production does, so the soak measures
    production's cost, not the fallback's.
    """

    def __init__(self, db_path: str, store: SchedulerStore):
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        self.mem = _Mem(db_path)
        self.scheduler_store = store
        self.governance_store = GovernanceStore(db_path)


class _Mem:
    """Duck-typed MemoryStore: enough for drive_reflection_micro and the
    Reflection -> Memory tail, which both tolerate a minimal ctx."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.reflections: list[dict] = []

    async def insert_reflection(self, kind, content, meta=None, ts=None, pinned=False):
        self.reflections.append({"kind": kind, "content": content})
        return len(self.reflections)


@pytest.fixture
def soak_db(tmp_path):
    path = str(tmp_path / "soak.db")
    sp.ensure_schema(path)
    return path


def _pending_counts(db_path: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT kind, COUNT(*) FROM nudges WHERE status='pending' GROUP BY kind",
        ).fetchall()
    finally:
        conn.close()
    return dict(rows)


def _total_pending(db_path: str) -> int:
    return sum(_pending_counts(db_path).values())


def _earliest_next_run(db_path: str) -> float | None:
    """Soonest scheduled run across all tasks, so idle time can be skipped."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        row = conn.execute("SELECT MIN(next_run_ts) FROM scheduled_tasks").fetchone()
    finally:
        conn.close()
    return float(row[0]) if row and row[0] is not None else None


def _rows(db_path: str, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


async def _run_accelerated(
    db_path: str,
    monkeypatch,
    *,
    simulated_seconds: int = SIMULATED_SECONDS,
    midpoint_hook=None,
) -> dict:
    """Run the real scheduler loop over `simulated_seconds` of virtual time.

    Returns a dict of observations taken during the run.
    """
    clock = _Clock()
    deadline = clock.now + simulated_seconds
    halfway = clock.now + simulated_seconds / 2
    observations: dict = {
        "iterations": 0,
        "idle_polls": 0,
        "midpoint_pending": None,
        "midpoint_taken": False,
    }

    real_sleep = asyncio.sleep

    class _FakeTime:
        time = staticmethod(clock.time)

    async def fast_sleep(seconds=0, *a, **k):
        clock.advance(seconds)
        return await real_sleep(0)

    class _FakeAsyncio:
        """Only what scheduler/loop.py reaches for."""

        sleep = staticmethod(fast_sleep)
        CancelledError = asyncio.CancelledError

    # The loop's own tick pacing. Each iteration costs a little virtual time
    # so drives cannot fire infinitely often within one instant; and when
    # nothing is due, the clock jumps straight to the next scheduled run
    # instead of grinding through thousands of five-second idle polls. That
    # is a faithful acceleration of waiting -- it changes only how long the
    # loop idles between firings, never how many firings the cadences
    # produce, which is what this soak measures.
    def stepping_next_due_task(inner):
        async def wrapper(now_ts):
            observations["iterations"] += 1
            if observations["iterations"] > MAX_LOOP_ITERATIONS:
                observations["stopped_on"] = "iteration_cap"
                raise _StopSoak
            clock.advance(1)
            if not observations["midpoint_taken"] and clock.now >= halfway:
                observations["midpoint_taken"] = True
                observations["midpoint_pending"] = _total_pending(db_path)
                if midpoint_hook is not None:
                    midpoint_hook(db_path)
            if clock.now >= deadline:
                observations["stopped_on"] = "simulated_deadline"
                raise _StopSoak

            due = await inner(int(clock.now))
            if due is None:
                observations["idle_polls"] += 1
                next_run = _earliest_next_run(db_path)
                if next_run is not None and next_run > clock.now:
                    clock.advance(min(next_run - clock.now, deadline - clock.now))
            return due

        return wrapper

    store = SchedulerStore(db_path)
    ctx = _MinimalCtx(db_path, store)

    monkeypatch.setattr(loop_mod, "time", _FakeTime)
    monkeypatch.setattr(loop_mod, "asyncio", _FakeAsyncio)
    monkeypatch.setattr(drives_mod, "time", _FakeTime)
    monkeypatch.setattr(store, "next_due_task", stepping_next_due_task(store.next_due_task))

    try:
        with pytest.raises(_StopSoak):
            await loop_mod.run_scheduler(ctx)
    finally:
        await store.close()

    observations["simulated_seconds"] = clock.now - (deadline - simulated_seconds)
    assert observations.get("stopped_on") == "simulated_deadline", (
        "the soak must run the full simulated window, not stop on the "
        f"iteration cap (stopped_on={observations.get('stopped_on')!r}, "
        f"iterations={observations['iterations']})"
    )
    return observations


@pytest.mark.slow
@pytest.mark.timeout(SOAK_TIMEOUT_S)
@pytest.mark.asyncio
class TestAcceleratedQueueSoak:
    async def test_queue_reaches_a_bounded_stable_state(self, soak_db, monkeypatch, capsys):
        """B-F001's closure criterion: not 'grows more slowly', but 'stops'."""
        before = _total_pending(soak_db)
        assert before == 0

        obs = await _run_accelerated(soak_db, monkeypatch)
        after = _pending_counts(soak_db)
        total_after = sum(after.values())

        # Enough of the run actually happened to be representative.
        ticks = _rows(soak_db, "SELECT task_id, COUNT(*) c FROM ticks GROUP BY task_id")
        tick_counts = {r["task_id"]: r["c"] for r in ticks}
        assert obs["stopped_on"] == "simulated_deadline"
        assert tick_counts.get("self_check", 0) >= TEST1_HEALTH_NUDGES, tick_counts
        assert tick_counts.get("curiosity_probe", 0) >= TEST1_CURIOSITY_NUDGES, tick_counts

        # The pattern Test #1 recorded -- repeated curiosity generation plus
        # repeated self-check execution -- did occur, many times over.
        events = sp.list_containment_events(soak_db, limit=100_000)
        assert len(events) >= TEST1_CURIOSITY_NUDGES, (
            "the soak must actually exercise the suppression path at least as "
            "often as Test #1 generated duplicates"
        )

        # ... and the unresolved queue is bounded, and stopped growing.
        assert total_after <= 2, f"expected a bounded queue, got {after}"
        assert after.get("curiosity", 0) <= 1
        assert (
            after.get("system_health", 0) == 0
        ), "with no genuine backlog there is nothing for self_check to warn about"
        assert obs["midpoint_pending"] == total_after, (
            "the second half of the run added items: growth is not bounded "
            f"(midpoint={obs['midpoint_pending']}, end={total_after})"
        )

        # Print the before/after evidence into captured output.
        with capsys.disabled():
            print(
                f"\n[WP-A1 soak] simulated={SIMULATED_SECONDS}s "
                f"iterations={obs['iterations']} idle_polls={obs['idle_polls']} "
                f"ticks={tick_counts} "
                f"pending_before={before} pending_midpoint={obs['midpoint_pending']} "
                f"pending_after={total_after} by_kind={after} "
                f"suppression_events={len(events)}",
            )

    async def test_recursive_self_check_growth_is_zero_under_a_genuine_backlog(
        self,
        soak_db,
        monkeypatch,
        capsys,
    ):
        """The exact B-F001 condition: the pending queue crosses the
        self-check threshold, and self-check then runs many more times."""
        genuine = PENDING_NUDGE_DRIFT_THRESHOLD + 5
        for i in range(genuine):
            sp.insert_nudge(soak_db, "task", f"genuine obligation {i}", [], "user_task", 100 + i)

        metrics_before = get_system_metrics(soak_db)
        assert check_drift(metrics_before) is not None, "the backlog must actually trigger drift"

        obs = await _run_accelerated(soak_db, monkeypatch)
        after = _pending_counts(soak_db)

        health_after = after.get("system_health", 0)
        assert health_after == 1, (
            "repeated self-check execution above the threshold must leave exactly "
            f"one unresolved queue-health item, found {health_after}"
        )
        assert after.get("task", 0) == genuine, "a genuine obligation was lost"
        assert after.get("curiosity", 0) <= 1

        self_check_ticks = _rows(
            soak_db,
            "SELECT COUNT(*) c FROM ticks WHERE task_id='self_check'",
        )[0]["c"]
        assert self_check_ticks >= TEST1_HEALTH_NUDGES, (
            "the soak must execute self_check above the threshold at least as "
            "often as Test #1 did"
        )

        # The condition is still reported, at its true magnitude.
        metrics_after = get_system_metrics(soak_db)
        assert metrics_after["pending_nudges"] == sum(after.values())
        assert metrics_after["pending_nudges_self_generated"] == 1
        assert metrics_after["pending_nudges_countable"] == genuine + after.get("curiosity", 0)
        assert check_drift(metrics_after) is not None

        # The B-F001 measure. The FIRST drift warning is the legitimate
        # report of a genuine backlog, so it is not growth; growth is what
        # every self-check execution AFTER it adds. In Test #1 that number
        # was one item per execution. It must now be exactly zero -- not
        # smaller, zero.
        recursive_growth = (health_after - 1) / max(1, self_check_ticks - 1)
        assert recursive_growth == 0.0, (
            f"{self_check_ticks} self-check executions above the threshold "
            f"produced {health_after} queue-health items "
            f"({recursive_growth} per subsequent execution)"
        )

        with capsys.disabled():
            print(
                f"\n[WP-A1 soak/backlog] self_check_ticks={self_check_ticks} "
                f"iterations={obs['iterations']} genuine_before={genuine} "
                f"pending_after={sum(after.values())} by_kind={after} "
                f"health_items={health_after} recursive_growth={recursive_growth}",
            )

    async def test_awaiting_response_obligations_survive_the_whole_soak(
        self,
        soak_db,
        monkeypatch,
    ):
        """D2 / S1: `awaiting_response` is exempt from generic shedding --
        structurally, because there is no shedding."""
        store = AwaitingResponseStore(soak_db)
        entries = [
            store.open(subject=s, origin_surface="chat", actor="taylor")
            for s in (
                "reply to the landlord about the lease",
                "reply to the landlord about the lease renewal",
                "confirm the invoice",
            )
        ]
        before = [e.to_dict() for e in entries]
        genuine_before = [dict(r) for r in _rows(soak_db, "SELECT * FROM nudges ORDER BY id")]

        await _run_accelerated(soak_db, monkeypatch)

        assert [store.get(e.id).to_dict() for e in entries] == before
        assert len(store.list(status="open")) == 3

        # ... and every pre-existing nudge row is still exactly as it was.
        after_rows = {r["id"]: dict(r) for r in _rows(soak_db, "SELECT * FROM nudges ORDER BY id")}
        for row in genuine_before:
            assert after_rows[row["id"]] == row

    async def test_resolving_the_open_items_lets_the_next_occurrence_through(
        self,
        soak_db,
        monkeypatch,
    ):
        """A bounded queue must not be a permanently silenced one: clearing
        the open item mid-run allows a later genuinely eligible occurrence,
        and the queue re-converges to the same bound."""

        def dismiss_everything(db_path):
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("UPDATE nudges SET status='dismissed' WHERE status='pending'")
                conn.commit()
            finally:
                conn.close()

        await _run_accelerated(soak_db, monkeypatch, midpoint_hook=dismiss_everything)

        total_rows = _rows(soak_db, "SELECT COUNT(*) c FROM nudges")[0]["c"]
        assert total_rows >= 2, (
            "after the mid-run dismissal a later curiosity occurrence must be "
            "representable again"
        )
        assert _total_pending(soak_db) <= 2, "and the queue must re-converge to the same bound"


@pytest.mark.asyncio
class TestUncontainedBaselineForComparison:
    """The 'before' half of the soak evidence. This reproduces the pre-WP-A1
    accounting and persistence directly (no scheduler loop involved) so the
    contrast is demonstrated by a running test rather than asserted in prose.
    If this test ever stops showing growth, the comparison it anchors has
    become meaningless and the soak assertions above are no longer evidence
    of anything."""

    async def test_uncontained_baseline_grows_without_bound(self, soak_db):
        def legacy_check_drift(metrics):
            """check_drift() as it was: the raw total, including the health
            warnings this very check produced."""
            if not metrics.get("db_ok"):
                return "database_unreachable"
            pending = metrics.get("pending_nudges", 0)
            if pending > PENDING_NUDGE_DRIFT_THRESHOLD:
                return f"high_pending_nudges:{pending}"
            return None

        for i in range(PENDING_NUDGE_DRIFT_THRESHOLD + 1):
            sp.insert_nudge(soak_db, "task", f"genuine obligation {i}", [], "user_task", 100 + i)

        samples = []
        for tick in range(60):
            metrics = get_system_metrics(soak_db)
            drift = legacy_check_drift(metrics)
            if drift:
                # persistence.insert_nudge() -- the pre-WP-A1 write path.
                sp.insert_nudge(
                    soak_db,
                    "system_health",
                    f"System drift detected: {drift}",
                    [],
                    "self_check_drift",
                    200_000 + tick,
                )
            # And the pre-WP-A1 curiosity write path, same rotating prompts.
            sp.insert_nudge(
                soak_db,
                "curiosity",
                "How are you feeling right now?",
                [],
                "curiosity_probe",
                200_000 + tick,
            )
            samples.append(_total_pending(soak_db))

        counts = _pending_counts(soak_db)
        assert counts["system_health"] == 60, "baseline: every self-check added a queue item"
        assert counts["curiosity"] == 60, "baseline: every curiosity firing added a queue item"
        assert (
            samples[-1] > samples[len(samples) // 2] > samples[0]
        ), "baseline must demonstrate monotonic growth for the comparison to mean anything"
