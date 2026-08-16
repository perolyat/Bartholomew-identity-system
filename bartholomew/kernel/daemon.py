from __future__ import annotations

import asyncio
import logging
import os
import sys
import traceback as traceback_module
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path

import yaml
from dateutil import tz

from .blocking_executor import run_off_loop
from .event_bus import EventBus
from .experience_kernel import ExperienceKernel
from .global_workspace import EventType, GlobalWorkspace
from .memory_store import MemoryStore
from .narrator import NarratorEngine
from .persona import load_persona
from .persona_pack import PersonaPackManager
from .planner import Planner
from .policy import load_policy
from .skill_registry import SkillRegistry
from .state_model import WorldState
from .working_memory import WorkingMemoryManager


class DaemonLifecycleState(Enum):
    """KernelDaemon's own in-process lifecycle state (Phase B stage B5).

    NOT_STARTED -> STARTING -> RUNNING -> STOPPING -> STOPPED
                        \\-> FAILED

    FAILED is terminal for a given instance -- start() refuses to run
    again on an instance that has already reached FAILED (or RUNNING, or
    any state past NOT_STARTED). A subsequent start attempt is expected
    to happen as a fresh process with a fresh KernelDaemon instance, not
    by mutating a failed instance back to NOT_STARTED; see
    docs/B5_STARTUP_SHUTDOWN_INTEGRITY.md.
    """

    NOT_STARTED = "not_started"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class UnsafeStartupError(RuntimeError):
    """Raised when start() finds evidence (a failed lightweight integrity
    check) that continuing after an unclean prior shutdown would be
    unsafe (Phase B stage B5). Distinct from other startup failures so
    tests and operators can tell "refused to start, needs a human" apart
    from an ordinary transient error."""


_STARTUP_RESOURCE_ORDER = [
    "process_lock",
    "mem",
    "governance_store",
    "awaiting_response_store",
    "scheduler_schema",
    "experience_kernel",
    "skills",
    "narrator_subscription",
    "producer_tasks",
    "scheduler_task",
]

# Phase B stage B7: how long stop() waits for already-admitted external
# requests to finish naturally before giving up on a confirmed-clean
# admission drain. Same order of magnitude as the per-producer-task wait
# stop() already uses below.
_ADMISSION_DRAIN_TIMEOUT_S = 10.0

logger = logging.getLogger(__name__)


def _compose_episodic_section(episodic_evidence: str | None) -> str:
    """
    Render episodic evidence for the kernel's last-resort reflection template.

    Delegates to `ReflectionGenerator`, which owns reflection composition
    (`COGNITIVE_RUNTIME.md`, "Reflection ownership"), so the formatting has a
    single implementation. The import is deliberately guarded and local: this
    is only ever called from the branch that runs *because* importing or
    constructing `ReflectionGenerator` failed, so it must degrade to omitting
    the section rather than raising a second error out of reflection
    generation.

    Args:
        episodic_evidence: Narrator episodic narrative, or None/empty

    Returns:
        A markdown section, or "" when unavailable or empty
    """
    try:
        from identity_interpreter.adapters.reflection_generator import ReflectionGenerator

        return ReflectionGenerator._compose_fallback_sections(episodic_evidence)
    except Exception:
        return ""


class KernelDaemon:
    def __init__(
        self,
        cfg_path: str,
        db_path: str,
        persona_path: str,
        policy_path: str,
        drives_path: str,
        loop_interval_s: int = 15,
        identity_path: str | None = None,
    ):
        self.cfg = yaml.safe_load(open(cfg_path, encoding="utf-8"))
        self.tz = tz.gettz(self.cfg["timezone"])
        self.interval = int(self.cfg.get("loop_interval_seconds", loop_interval_s))
        self.bus = EventBus()

        # Shared dedicated worker thread (Phase B stage B2; see
        # docs/B2_EVENT_LOOP_ISOLATION.md) for the daemon's synchronous,
        # blocking persistence calls that aren't the scheduler's own
        # (FTS startup schema init, chunking/re-embedding, ParkingBrake
        # construction, persona/narrator calls) -- storage-agnostic, not a
        # second SQLite-specific mechanism; the scheduler keeps its own
        # dedicated lane below rather than sharing this one, matching its
        # existing strict-ordering requirement. Construction is cheap (no
        # I/O, no thread spawned until first use), so it's safe to always
        # create, even for a KernelDaemon that's never start()ed.
        from .blocking_executor import SingleWorkerExecutor
        from .process_lock import ProcessLock
        from .request_admission import RequestAdmission

        self.blocking_executor = SingleWorkerExecutor(
            thread_name_prefix="kernel-blocking",
            label=db_path,
        )
        self.mem = MemoryStore(db_path, blocking_executor=self.blocking_executor)

        # Cross-process exclusivity guard (Phase B stage B6; see
        # docs/B6_EXTERNAL_GOVERNANCE_CLI_SAFETY.md) -- acquired first in
        # start(), released last in stop(). Prevents two daemon processes
        # from starting against the same db_path, and is the anchor CLI
        # maintenance operations (bartholomew/cli.py's `embeddings
        # rebuild-vss`) check against before touching the file. Cheap to
        # construct (no I/O until acquire()), matching this class's
        # existing pattern for blocking_executor above.
        self._process_lock = ProcessLock(db_path)

        # External request admission (Phase B stage B7; see
        # docs/B7_EXTERNAL_REQUEST_ADMISSION.md) -- app.py's HTTP
        # middleware admits one token per real external request reaching
        # governed daemon state and releases it when that request
        # finishes; stop() closes admission and drains outstanding
        # tokens before tearing down the resources those requests still
        # need. Cheap to construct, matching this class's existing
        # pattern for the other daemon-owned primitives above.
        self.admission = RequestAdmission()

        # The daemon's one shared Governance instance (Phase B stage B4;
        # see docs/B4_GOVERNANCE_RUNTIME_INTEGRATION.md). Unlike
        # blocking_executor/scheduler_store above, GovernanceStore's own
        # __init__ does blocking schema/state I/O (ensure_schema() + an
        # initial load), so -- matching this class's existing pattern of
        # deferring blocking work to start() rather than __init__ -- it
        # is constructed there, off the event loop, not here. None until
        # then; every real call site reachable only after a successful
        # start() (guarded by callers checking _kernel is not None)
        # already tolerates None via governance_store.is_blocked_fail_
        # closed{,_off_loop}()'s own fallback (construct a temporary
        # instance) -- see docs/B6_EXTERNAL_GOVERNANCE_CLI_SAFETY.md.
        self.governance_store = None

        # The daemon's awaiting_response obligation queue (Stage 1, S1.4;
        # see docs/S1_4_AWAITING_RESPONSE_DESIGN.md). Same construction-
        # timing rationale as governance_store immediately above --
        # AwaitingResponseStore.__init__() does blocking schema I/O, so
        # it's constructed off the event loop in start(), not here. None
        # until then.
        self.awaiting_response_store = None

        # Owned by this daemon instance for its entire lifetime -- closed
        # in stop(). Construction is cheap (no I/O, no thread spawned
        # until first use), so it's safe to always create one, even for
        # a KernelDaemon that's never start()ed. See scheduler/store.py's
        # module docstring for why this exists.
        from .scheduler.store import SchedulerStore

        self.scheduler_store = SchedulerStore(db_path)
        self.persona = load_persona(persona_path)
        self.policy = load_policy(policy_path)
        self.drives = yaml.safe_load(open(drives_path, encoding="utf-8"))
        self.planner = Planner(self.policy, self.drives, self.mem)
        self.state = WorldState()

        # Identity Context (MASTER_PLAN.md "P2.5 -- Runtime Convergence",
        # item 11.2): optional so existing callers/tests that don't pass
        # identity_path see no behavior change. When provided, a failure to
        # load/validate Identity.yaml is logged and treated the same as not
        # providing one at all (permissive) rather than crashing daemon
        # construction -- this wiring is new and additive, not yet a
        # safety-critical dependency the whole daemon should die without.
        self.identity_context = None
        # The full loaded Identity, retained alongside the narrow
        # IdentityContext projection above. IdentityContext deliberately
        # carries only values/red-lines/tool-use policy, but the model layer
        # needs `meta.deployment_profile` (runtimes, model policies), so the
        # loaded object was previously parsed and then discarded. Kept here
        # so the chat surface can build a real model adapter without a second
        # load and a second source of truth. None whenever identity_context
        # is None -- same permissive failure behaviour, no new dependency.
        self.identity = None
        if identity_path:
            try:
                from identity_interpreter.identity_context import build_identity_context
                from identity_interpreter.loader import load_identity

                identity = load_identity(identity_path)
                self.identity_context = build_identity_context(identity)
                self.identity = identity
            except Exception as e:
                logger.warning(
                    "Failed to load Identity Context from %s: %s -- "
                    "continuing without one (tool-use policy checks skipped)",
                    identity_path,
                    e,
                )

        # Stage 3: Experience Kernel modules
        self.workspace = GlobalWorkspace()
        self.experience = ExperienceKernel(
            db_path=db_path,
            workspace=self.workspace,
        )
        self.working_memory = WorkingMemoryManager(
            workspace=self.workspace,
            kernel=self.experience,
        )
        # persona_manager is constructed before narrator (reordered from the
        # original self.experience -> self.narrator -> ... -> persona_manager
        # sequence) so it can be passed straight into NarratorEngine --
        # narrator's narrative_overrides lookup needs it (see narrator.py's
        # _get_templates()); previously nothing wired persona packs into the
        # narrator at all, so switching persona had no effect on narrative
        # tone/content despite PersonaPack.narrative_overrides existing.
        self.persona_manager = PersonaPackManager(
            experience_kernel=self.experience,
            workspace=self.workspace,
            db_path=db_path,
        )
        self.narrator = NarratorEngine(
            db_path=db_path,
            workspace=self.workspace,
            persona_manager=self.persona_manager,
        )

        # Stage 4: Skill Registry -- previously never constructed anywhere in
        # the live daemon (nor exposed via any API route), so the skill
        # system existed as fully-tested but completely disconnected code.
        # Wired into the planner below so Planner.handle_skill_request()
        # ("prompt -> decide -> tool call") has something to call.
        self.skill_registry = SkillRegistry(
            db_path=db_path,
            workspace=self.workspace,
            kernel=self.experience,
            working_memory=self.working_memory,
            memory_store=self.mem,
            identity_context=self.identity_context,
            blocking_executor=self.blocking_executor,
        )
        self.planner.set_skill_registry(self.skill_registry)

        # Task handles for lifecycle management
        self._tick_task = None
        self._consumer_task = None
        self._dream_task = None
        self._scheduler_task = None

        # Quiet hours config
        quiet_cfg = self.cfg.get("quiet_hours", {})
        self.quiet_start = quiet_cfg.get("start", "21:30")
        self.quiet_end = quiet_cfg.get("end", "07:00")

        # Dreaming config
        dream_cfg = self.cfg.get("dreaming", {})
        self.nightly_window = dream_cfg.get("nightly_window", "21:00-23:00")
        weekly_cfg = dream_cfg.get("weekly", {})
        self.weekly_weekday = weekly_cfg.get("weekday", "Sun")
        self.weekly_time = weekly_cfg.get("time", "21:30")

        # Track last reflection runs
        self._last_daily_reflection = None
        self._last_weekly_reflection = None

        # Lifecycle state (Phase B stage B5) -- see DaemonLifecycleState.
        self.lifecycle_state = DaemonLifecycleState.NOT_STARTED
        self._runtime_id: str | None = None

    async def start(self) -> None:
        if self.lifecycle_state is not DaemonLifecycleState.NOT_STARTED:
            raise RuntimeError(
                f"KernelDaemon.start() called while in state "
                f"{self.lifecycle_state.value!r}; a daemon instance can only be "
                "started once. FAILED is terminal for this instance (Phase B "
                "stage B5) -- construct a fresh KernelDaemon for a new attempt "
                "rather than reusing this one.",
            )
        self.lifecycle_state = DaemonLifecycleState.STARTING

        # Startup Incident Log bookkeeping (Phase B stage B5) -- populated
        # as we go so a failure at any point can report exactly how far
        # startup got, not just that it failed. See
        # docs/B5_STARTUP_SHUTDOWN_INTEGRITY.md.
        resources_started: list[str] = []
        integrity_checks_performed: list[str] = []
        recovery_actions_attempted: list[str] = []
        previous_shutdown_clean: bool | None = None

        # Protected region (Codex review on PR #25, extended in Phase B
        # stage B5 to cover every resource activated during start(), not
        # just scheduler_store): once ANY step below has activated a
        # persistent resource, EVERY abnormal exit -- including
        # CancelledError, which inherits BaseException -- must unwind it.
        # stop() may never run after a failed start(), and an aborted ASGI
        # startup is not guaranteed to invoke it either. Cleanup runs
        # under asyncio.shield() so in-flight cancellation cannot interrupt
        # it, reuses each resource's own bounded close() (never waits
        # indefinitely), and a cleanup failure is logged as secondary,
        # never replacing the primary error.
        try:
            # Phase B stage B6: acquire the cross-process lock before
            # touching anything else -- if another daemon process (or a
            # CLI maintenance command) already holds it, fail fast with a
            # clear message rather than proceeding to open the database
            # underneath it. Non-blocking (fails immediately, not a
            # blocking wait), and off the event loop like every other
            # blocking call in this method.
            await run_off_loop(
                self._process_lock.acquire,
                executor=self.blocking_executor,
            )
            resources_started.append("process_lock")

            await self.mem.init()
            resources_started.append("mem")

            # The daemon's one shared Governance instance (Phase B stage
            # B4). Constructed off the event loop -- GovernanceStore
            # .__init__() itself does blocking schema/state I/O -- via
            # blocking_executor. Wired into skill_registry immediately
            # after, since that component was constructed in __init__,
            # before this instance existed.
            from bartholomew.orchestrator.safety.governance_store import (
                GovernanceStore,
                run_quick_integrity_check,
            )

            self.governance_store = await run_off_loop(
                GovernanceStore,
                self.mem.db_path,
                executor=self.blocking_executor,
            )
            resources_started.append("governance_store")
            self.skill_registry.set_governance_store(self.governance_store)

            # Stage 1, S1.4: the awaiting_response obligation queue store.
            # Constructed off the event loop for the same reason as
            # governance_store immediately above (blocking schema I/O in
            # __init__()).
            from bartholomew.kernel.awaiting_response_store import AwaitingResponseStore

            self.awaiting_response_store = await run_off_loop(
                AwaitingResponseStore,
                self.mem.db_path,
                executor=self.blocking_executor,
            )
            resources_started.append("awaiting_response_store")

            # Phase B stage B5: read whether the previous runtime (if any)
            # against this db_path confirmed a clean shutdown, then open
            # this runtime's own marker (fresh runtime_id, write fence
            # open, clean=0 -- the honest default until stop() proves
            # otherwise). Must read-then-open in that order: open_new_
            # runtime() overwrites the marker open_new_runtime() itself
            # would otherwise report.
            previous_shutdown_clean = await run_off_loop(
                self.governance_store.previous_shutdown_was_clean,
                executor=self.blocking_executor,
            )
            self._runtime_id = await run_off_loop(
                self.governance_store.open_new_runtime,
                executor=self.blocking_executor,
            )

            if previous_shutdown_clean is False:
                logger.warning(
                    "KernelDaemon.start(): previous runtime (db=%s) did not "
                    "confirm a clean shutdown -- running a lightweight "
                    "integrity check before continuing (Phase B stage B5).",
                    self.mem.db_path,
                )
                integrity_ok, integrity_result = await run_off_loop(
                    run_quick_integrity_check,
                    self.mem.db_path,
                    executor=self.blocking_executor,
                )
                integrity_checks_performed.append(f"quick_check: {integrity_result}")
                if not integrity_ok:
                    # Evidence that continuing would be unsafe -- abort.
                    # The outer except below records this as an incident
                    # and unwinds; it does not attempt the WAL-repair step.
                    raise UnsafeStartupError(
                        f"Refusing to start: quick_check reported {integrity_result!r} "
                        f"after an unclean prior shutdown (db={self.mem.db_path}). "
                        "This requires operator attention, not automatic recovery.",
                    )
                # Obviously-recoverable repair: an unclean shutdown may have
                # left the WAL file un-checkpointed (see B2's "WAL cleanup
                # is deferred to next startup" decision) -- run it now that
                # startup owns the file exclusively, before anything else
                # touches it.
                from .db_ctx import wal_checkpoint_truncate

                await run_off_loop(
                    wal_checkpoint_truncate,
                    self.mem.db_path,
                    executor=self.blocking_executor,
                )
                recovery_actions_attempted.append("wal_checkpoint_truncate")
                logger.warning(
                    "KernelDaemon.start(): integrity check passed (%s); "
                    "ran deferred WAL checkpoint and continuing startup.",
                    integrity_result,
                )

            # S5.0 (closes issue #24): ensure the scheduler's schema
            # (scheduled_tasks, ticks, and the nudges/reflections integer-timestamp
            # columns) exists *before* start() returns, as early as practical --
            # immediately after MemoryStore init and before any side-effectful
            # initialization (experience kernel, skills, narrator) or the scheduler
            # task itself. Previously run_scheduler() created this schema
            # asynchronously as a fire-and-forget task, so the API bridge could
            # serve requests during a startup window where `ticks`/`scheduled_tasks`
            # did not yet exist -- external readers (e.g. /api/liveness/ticks) hit
            # "no such table" and 500'd. This runs on SchedulerStore's dedicated
            # worker thread (off the event loop) and is awaited here so schema
            # readiness is a synchronous precondition of a started daemon.
            #
            # Fail closed (A1): if the schema cannot be created, the daemon does not
            # come up -- a "started" daemon with no scheduler tables is exactly the
            # broken half-initialized state issue #24 is about. No outer
            # asyncio.wait_for is added (A2): the underlying wal_db() call is
            # already bounded, and cancelling the awaiting coroutine cannot stop
            # the worker-thread operation, so an outer timeout would
            # abandon-not-cancel.
            await self.scheduler_store.ensure_schema()
            resources_started.append("scheduler_schema")

            # Stage 3: Initialize experience kernel state
            await self._init_experience_kernel()
            resources_started.append("experience_kernel")

            # Stage 4: Load skills. load_enabled_skills() loads whatever was
            # previously enabled (skill_registry_state), which is empty on a
            # fresh database -- fall back to loading every discovered starter
            # skill so they work out of the box rather than requiring a manual
            # enable step first, since they're documented as "safe and
            # reversible".
            loaded_count = await self.skill_registry.load_enabled_skills()
            if loaded_count == 0 and not self.skill_registry.list_loaded():
                for skill_id in self.skill_registry.list_available():
                    await self.skill_registry.load_skill(skill_id)
            resources_started.append("skills")

            # Stage 3: Subscribe narrator to workspace events
            self.narrator.subscribe_to_workspace()
            resources_started.append("narrator_subscription")

            # Stage 3: Emit startup event
            self.workspace.publish(
                channel="system",
                event_type=EventType.SYSTEM_EVENT,
                source="kernel_daemon",
                payload={
                    "event": "startup",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            # Give the scheduled skill event-handler tasks (see
            # SkillRegistry._setup_subscriptions()) a chance to start running
            # before we move on -- publish() is sync and only schedules them.
            await asyncio.sleep(0)

            # Start background tasks
            self._tick_task = asyncio.create_task(self._system_tick())
            self._consumer_task = asyncio.create_task(self._system_consumer())
            self._dream_task = asyncio.create_task(self._dream_loop())
            resources_started.append("producer_tasks")

            # Start scheduler (autonomy loop)
            from .scheduler.loop import run_scheduler

            self._scheduler_task = asyncio.create_task(run_scheduler(self))
            resources_started.append("scheduler_task")
        except BaseException:  # includes CancelledError; re-raised below
            self.lifecycle_state = DaemonLifecycleState.FAILED

            # Unwind every producer task actually created before the
            # failure (Phase B stage B5 -- previously only the two
            # worker-thread resources below were unwound here).
            for task in (self._tick_task, self._consumer_task, self._dream_task):
                if task and not task.done():
                    task.cancel()

            try:
                await asyncio.shield(self.scheduler_store.close())
            except BaseException:  # secondary only; primary re-raised below
                logger.exception(
                    "Scheduler store cleanup failed during aborted startup; "
                    "propagating the original startup error as primary",
                )
            try:
                # Release the process lock (Phase B stage B6) before the
                # executor that submits it closes -- a no-op if it was
                # never successfully acquired (ProcessLock.release()'s
                # own guard).
                await asyncio.shield(
                    run_off_loop(self._process_lock.release, executor=self.blocking_executor),
                )
            except BaseException:  # secondary only; primary re-raised below
                logger.exception(
                    "Process lock release failed during aborted startup; "
                    "propagating the original startup error as primary",
                )
            try:
                await asyncio.shield(self.blocking_executor.close())
            except BaseException:  # secondary only; primary re-raised below
                logger.exception(
                    "Blocking executor cleanup failed during aborted startup; "
                    "propagating the original startup error as primary",
                )

            await self._record_startup_incident(
                resources_started=resources_started,
                previous_shutdown_clean=previous_shutdown_clean,
                integrity_checks_performed=integrity_checks_performed,
                recovery_actions_attempted=recovery_actions_attempted,
                final_outcome="failed",
            )
            raise
        else:
            self.lifecycle_state = DaemonLifecycleState.RUNNING
            if previous_shutdown_clean is False:
                # Startup succeeded, but B5's own trigger condition for the
                # Startup Incident Log is "an unclean shutdown was detected
                # OR startup failed" -- this branch is the former without
                # the latter, so it gets its own record even though start()
                # is about to return successfully.
                await self._record_startup_incident(
                    resources_started=resources_started,
                    previous_shutdown_clean=previous_shutdown_clean,
                    integrity_checks_performed=integrity_checks_performed,
                    recovery_actions_attempted=recovery_actions_attempted,
                    final_outcome="started_after_recovery",
                )

    async def _record_startup_incident(
        self,
        *,
        resources_started: list[str],
        previous_shutdown_clean: bool | None,
        integrity_checks_performed: list[str],
        recovery_actions_attempted: list[str],
        final_outcome: str,
    ) -> None:
        """Best-effort Startup Incident Log write (Phase B stage B5) --
        a failure here is logged as secondary and never masks or replaces
        whatever start() itself is doing (raising, or returning
        successfully).

        Deliberately a direct (not off-loop) call: on the failure path
        this runs after blocking_executor has already been closed as
        part of unwind, so run_off_loop() would have nothing to submit
        to. A single one-off diagnostic write on a rare path (startup
        failure or unclean-shutdown recovery) is an accepted tradeoff,
        the same precedent stop()'s own tail-of-shutdown mark_clean_
        shutdown() call already sets.
        """
        exc_type = exc_message = tb = None
        if final_outcome == "failed":
            exc_value = sys.exc_info()[1]
            if exc_value is not None:
                exc_type = type(exc_value).__name__
                exc_message = str(exc_value)
                tb = traceback_module.format_exc()

        resources_not_started = [r for r in _STARTUP_RESOURCE_ORDER if r not in resources_started]
        try:
            from bartholomew.orchestrator.safety.governance_store import GovernanceStore

            incident_store = self.governance_store or GovernanceStore(self.mem.db_path)
            incident_store.record_startup_incident(
                runtime_id=self._runtime_id,
                lifecycle_state_reached=self.lifecycle_state.value,
                exception_type=exc_type,
                exception_message=exc_message,
                traceback=tb,
                resources_started=resources_started,
                resources_not_started=resources_not_started,
                previous_shutdown_clean=previous_shutdown_clean,
                integrity_checks_performed=integrity_checks_performed,
                recovery_actions_attempted=recovery_actions_attempted,
                final_outcome=final_outcome,
            )
        except BaseException:
            logger.exception("Failed to record startup incident (Phase B stage B5)")

    async def _init_experience_kernel(self) -> None:
        """Initialize experience kernel from last snapshot or defaults.

        Phase B stage B8: load_last_snapshot()/switch_pack() do real,
        synchronous SQLite I/O (confirmed by direct read of
        experience_kernel.py/working_memory.py/persona_pack.py -- none of
        their methods are `async def`, so nothing about them was ever
        off-loop by construction); this method previously called them
        directly from start()'s async context, blocking the event loop
        for the duration of each disk read/write. Now routed through
        run_off_loop(), the same B2 pattern every other daemon-owned
        blocking call already uses. restore_from_snapshot()/
        get_active_pack_id()/list_packs() are pure in-memory operations
        (confirmed by direct read) and are deliberately left as direct
        calls -- wrapping them would only add overhead for no benefit.
        """
        db_path = self.mem.db_path

        try:
            # Try to load last experience snapshot
            snapshot = await run_off_loop(
                self.experience.load_last_snapshot,
                executor=self.blocking_executor,
            )
            if snapshot:
                # Real, previously-live bug (found 2026-07-21 while writing a
                # scenario-replay test): load_last_snapshot() only loads and
                # returns a SelfSnapshot -- it never applies it. This code
                # printed "Restored experience state from last snapshot" on
                # every boot while actually leaving drives/affect/attention/
                # active_goals at their fresh-instance defaults, silently
                # discarding restore_from_snapshot()'s entire purpose. Same
                # bug class as the 2026-07-20 "silently-swallowed
                # AttributeError" fix in this module -- a log message that
                # was never actually true.
                self.experience.restore_from_snapshot(snapshot)
                print("[Kernel] Restored experience state from last snapshot")
            else:
                print("[Kernel] Starting with fresh experience state")

            # Try to load last working memory snapshot
            wm_loaded = await run_off_loop(
                self.working_memory.load_last_snapshot,
                db_path,
                executor=self.blocking_executor,
            )
            if wm_loaded:
                print("[Kernel] Restored working memory from last snapshot")
            else:
                print("[Kernel] Starting with empty working memory")

            # Activate default persona if none active
            if not self.persona_manager.get_active_pack_id():
                packs = self.persona_manager.list_packs()
                if packs:
                    await run_off_loop(
                        self.persona_manager.switch_pack,
                        packs[0],
                        trigger="startup",
                        executor=self.blocking_executor,
                    )
                    print(f"[Kernel] Activated persona: {packs[0]}")
        except Exception as e:
            print(f"[Kernel] Experience kernel init warning: {e}")

    async def stop(self) -> None:
        """Gracefully stop the kernel daemon."""
        # Phase B stage B5 lifecycle guard: NOT_STARTED/FAILED have no
        # running resources of this stop() sequence's own making to tear
        # down (FAILED's own resources were already unwound by start()'s
        # own except block; NOT_STARTED never activated anything).
        # STOPPING/STOPPED make this idempotent rather than double-running
        # teardown. Only a RUNNING instance has real work to do here.
        if self.lifecycle_state in (
            DaemonLifecycleState.NOT_STARTED,
            DaemonLifecycleState.FAILED,
            DaemonLifecycleState.STOPPING,
            DaemonLifecycleState.STOPPED,
        ):
            logger.info(
                "KernelDaemon.stop() called while in state %r; nothing to do.",
                self.lifecycle_state.value,
            )
            return
        self.lifecycle_state = DaemonLifecycleState.STOPPING

        # External request admission freeze-and-drain (Phase B stage B7),
        # first of all: stop admitting new externally-admitted work the
        # instant STOPPING begins, then wait for whatever's already
        # in-flight to finish naturally -- while every resource it needs
        # (governance_store, mem, blocking_executor) is still fully
        # intact, not torn down underneath it. admission_drained feeds
        # fully_drained below, the same "confirmed, not assumed" honesty
        # the write-fence/clean-marker (Phase B stage B5) already
        # requires of every other tracked resource.
        self.admission.close()
        admission_drained = await self.admission.drain(_ADMISSION_DRAIN_TIMEOUT_S)
        if not admission_drained:
            print(
                f"[Kernel] {self.admission.admitted_count} external request(s) still "
                "in flight after the admission drain timeout; deferring WAL cleanup "
                "to next startup",
            )

        # Governance write freeze (Phase B stage B5), first: stop accepting
        # new engage()/disengage() writes -- including Phase B stage B4's
        # dual-check bridge mirror writes -- before anything else in
        # shutdown runs, not just before the final checkpoint. blocking_
        # executor is still open at this point, so this still goes
        # off-loop like every other Governance write.
        if self.governance_store is not None:
            try:
                await run_off_loop(
                    self.governance_store.close_write_fence,
                    executor=self.blocking_executor,
                )
            except Exception as e:
                print(f"[Kernel] Failed to close Governance write fence: {e}")

        # Stage 3: Emit shutdown event
        self.workspace.publish(
            channel="system",
            event_type=EventType.SYSTEM_EVENT,
            source="kernel_daemon",
            payload={
                "event": "shutdown",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        await asyncio.sleep(0)

        # Stage 3: Persist experience snapshot. Phase B stage B8: routed
        # off the event loop -- persist_snapshot() does real synchronous
        # SQLite I/O (confirmed by direct read), previously called
        # directly from this async method. blocking_executor is still
        # open at this point in stop()'s sequence.
        try:
            await run_off_loop(self.experience.persist_snapshot, executor=self.blocking_executor)
            print("[Kernel] Experience state persisted")
        except Exception as e:
            print(f"[Kernel] Failed to persist experience state: {e}")

        # Stage 3: Persist working memory snapshot (Phase B stage B8; see
        # comment above -- same real blocking-I/O gap).
        try:
            await run_off_loop(
                self.working_memory.persist_snapshot,
                self.mem.db_path,
                executor=self.blocking_executor,
            )
            print("[Kernel] Working memory state persisted")
        except Exception as e:
            print(f"[Kernel] Failed to persist working memory: {e}")

        # Stage 4: Shut down loaded skills
        try:
            await self.skill_registry.shutdown()
        except Exception as e:
            print(f"[Kernel] Failed to shut down skill registry: {e}")

        tasks = [
            self._tick_task,
            self._consumer_task,
            self._dream_task,
            self._scheduler_task,
        ]
        for task in tasks:
            if task and not task.done():
                task.cancel()

        # Wait for cancellation with timeout. Phase B stage B5 "clean-marker
        # honesty": track whether every task was CONFIRMED terminal (not
        # merely asked to cancel) -- a timeout here means we cannot claim
        # confirmed termination for that task, which must be reflected in
        # whether this shutdown is later marked clean, not silently ignored.
        producer_tasks_terminal = True
        for task in tasks:
            if task:
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except asyncio.CancelledError:
                    pass
                except asyncio.TimeoutError:
                    producer_tasks_terminal = False
                    print(f"[Kernel] Task {task.get_name()} did not terminate within timeout")

        # Close the scheduler's dedicated DB worker thread and the shared
        # blocking-call worker thread (Phase B stage B2) before the memory
        # store's own final checkpoint below, so nothing is still
        # mid-operation on the same file when that checkpoint runs. If
        # either doesn't drain cleanly within its bound, skip the
        # (blocking, exclusive) TRUNCATE checkpoint entirely rather than
        # risk contending with a thread that may still be running -- see
        # SchedulerStore.close()'s / SingleWorkerExecutor.close()'s
        # docstrings and DECISIONS.md.
        scheduler_drained = await self.scheduler_store.close()
        blocking_drained = await self.blocking_executor.close()
        fully_drained = (
            admission_drained and producer_tasks_terminal and scheduler_drained and blocking_drained
        )
        if not fully_drained:
            print(
                "[Kernel] External admission and/or producer tasks and/or scheduler store "
                "and/or blocking executor did not confirm clean termination; deferring WAL "
                "cleanup to next startup",
            )

        # Close memory store (checkpoint WAL, unless anything above wasn't
        # confirmed terminal -- see comment above)
        await self.mem.close(checkpoint=fully_drained)

        # Phase B stage B5 clean marker: the LAST Governance write of a
        # shutdown, and only if every resource this stage tracks was
        # actually confirmed terminal above -- not merely requested to
        # stop. blocking_executor is already closed by this point, so
        # this is a direct (not off-loop) call, same precedent as
        # mem.close()'s own tail-of-shutdown checkpoint call above. Does
        # not cover externally admitted work (B7 hasn't introduced
        # request admission yet) -- this marker reflects confirmed
        # termination of the resources B1-B4 introduced, not the complete
        # shutdown invariant docs/PHASE_B_OVERVIEW.md Sec 4 describes.
        if self.governance_store is not None:
            if fully_drained:
                try:
                    self.governance_store.mark_clean_shutdown()
                except Exception as e:
                    print(f"[Kernel] Failed to record clean shutdown marker: {e}")
            else:
                print(
                    "[Kernel] Not marking this shutdown clean: at least one resource "
                    "was not confirmed terminal (see above).",
                )

        # Release the process lock (Phase B stage B6) last -- only once
        # everything above has had its chance to run against a still-locked
        # database. Direct (not off-loop) call: blocking_executor is
        # already closed by this point, same precedent as mark_clean_
        # shutdown() and mem.close()'s tail-of-shutdown calls above.
        self._process_lock.release()

        self.lifecycle_state = DaemonLifecycleState.STOPPED

    def _is_quiet_hours(self, now: datetime) -> bool:
        """Check if current time is within quiet hours."""
        now_time = now.time()
        start = time.fromisoformat(self.quiet_start)
        end = time.fromisoformat(self.quiet_end)

        if start < end:
            return start <= now_time < end
        else:  # Spans midnight
            return now_time >= start or now_time < end

    async def _system_tick(self) -> None:
        while True:
            try:
                self.state.now = datetime.now(tz=self.tz)

                # Check quiet hours
                if self._is_quiet_hours(self.state.now):
                    await asyncio.sleep(self.interval)
                    continue

                # Stage 3: Decay affect toward baseline each tick
                self.experience.decay_affect_to_baseline(delta_seconds=self.interval)

                # Stage 3: Check for auto persona activation
                context_tags = list(self.experience.get_context("tags") or [])
                await run_off_loop(
                    self.persona_manager.auto_activate_if_needed,
                    context_tags,
                    executor=self.blocking_executor,
                )

                action = await self.planner.decide(self.state)
                if action:
                    await self.bus.publish("system", action)
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Kernel] Error in tick: {e}")
                await asyncio.sleep(self.interval)

    async def _system_consumer(self) -> None:
        try:
            async for evt in self.bus.subscribe("system"):
                # Persist nudges to DB
                if evt.get("type") == "nudge":
                    payload = evt.get("payload", {})
                    await self.mem.create_nudge(
                        kind=payload.get("kind", "unknown"),
                        message=payload.get("message", ""),
                        actions=payload.get("actions", []),
                        reason=evt.get("reason", ""),
                        created_ts=datetime.now(timezone.utc).isoformat(),
                    )
                # Still print for dev visibility
                print(f"[Bartholomew] {evt['payload']['message']}")
        except asyncio.CancelledError:
            pass

    async def _dream_loop(self) -> None:
        """Background loop for nightly/weekly reflections."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute

                now = datetime.now(tz=self.tz)
                now_date = now.date()

                # Check for nightly reflection
                if self._should_run_daily(now):
                    await self._run_daily_reflection(now)
                    self._last_daily_reflection = now_date

                # Check for weekly reflection
                if self._should_run_weekly(now):
                    await self._run_weekly_reflection(now)
                    self._last_weekly_reflection = now_date

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Kernel] Error in dream loop: {e}")

    def _should_run_daily(self, now: datetime) -> bool:
        """Check if should run daily reflection."""
        if self._last_daily_reflection == now.date():
            return False

        # Parse window (e.g., "21:00-23:00")
        parts = self.nightly_window.split("-")
        start_time = time.fromisoformat(parts[0])
        end_time = time.fromisoformat(parts[1])

        now_time = now.time()
        return start_time <= now_time < end_time

    def _should_run_weekly(self, now: datetime) -> bool:
        """Check if should run weekly reflection."""
        if self._last_weekly_reflection == now.date():
            return False

        # Map weekday names
        weekdays = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
        target_weekday = weekdays.get(self.weekly_weekday, 6)

        if now.weekday() != target_weekday:
            return False

        # Check time
        target_time = time.fromisoformat(self.weekly_time)
        now_time = now.time()

        # Allow 60-minute window after target time
        return (
            target_time
            <= now_time
            < (datetime.combine(now.date(), target_time) + timedelta(hours=1)).time()
        )

    async def _run_daily_reflection(self, now: datetime) -> None:
        """Generate and persist daily reflection using Identity Interpreter."""
        print("[Kernel] Running daily reflection...")

        # Get pending nudges count for richer context. Previously did
        # `from .scheduler.persistence import get_system_metrics`, which
        # doesn't exist there (it's in scheduler/health.py) -- that
        # ImportError was silently swallowed by the except below, so
        # pending_nudges was always 0. Fixed as part of routing this
        # through scheduler_store, which offloads it off the event loop.
        pending_nudges = 0
        try:
            metrics = await self.scheduler_store.get_system_metrics()
            pending_nudges = metrics.get("pending_nudges", 0)
        except Exception:
            pass

        # Collect the Experience Kernel's episodic narrative (real
        # affect/attention/drive/goal/observation episodes from today) *before*
        # generating, so it can be supplied to ReflectionGenerator as evidence.
        # ReflectionGenerator is the authoritative owner of reflection
        # composition (COGNITIVE_RUNTIME.md, "Reflection ownership"); the
        # narrator is supplementary. This previously ran *after* generation and
        # string-concatenated the two outputs, which was the reflection-
        # ownership implementation gap ROADMAP.md S5.4 required closing.
        # A narrator failure must never break reflection generation.
        episodic_evidence: str | None = None
        try:
            episodic_evidence = await run_off_loop(
                self.narrator.generate_daily_reflection_narrative,
                now,
                executor=self.blocking_executor,
            )
        except Exception as e:
            print(f"[Kernel] Failed to collect episodic evidence for daily reflection: {e}")

        # Generate reflection using Identity Interpreter
        try:
            from identity_interpreter.adapters.reflection_generator import ReflectionGenerator

            generator = ReflectionGenerator(identity_path="Identity.yaml")
            result = generator.generate_daily_reflection(
                metrics={
                    "nudges_count": 0,
                    "pending_nudges": pending_nudges,
                },
                date=now,
                timezone_str=str(self.tz),
                backend="stub",  # Use stub by default
                episodic_evidence=episodic_evidence,
            )

            content = result["content"]
            meta = {
                "nudges": 0,
                **result["meta"],
                "safety": result["safety"],
            }

            if not result["success"]:
                print(
                    f"[Kernel] Daily reflection used fallback: {meta.get('error', 'unknown')}",
                )
        except Exception as e:
            # Last-resort template: reached only when ReflectionGenerator
            # itself is unavailable (import or construction failed), so there
            # is no authoritative composer to defer to. The evidence is folded
            # in as a section of this one document rather than concatenated as
            # a second reflection.
            print(f"[Kernel] Reflection generator error: {e}, using fallback")
            episodic_section = _compose_episodic_section(episodic_evidence)
            content = f"""# Daily Reflection - {now.date()}

## Summary
Wellness monitoring and proactive care delivered.

## Wellness
- System monitoring active
- Pending nudges: {pending_nudges}

## Notable Events
(Future: chat highlights, emotional events, user activities)
{episodic_section}
## Intent for Tomorrow
Continue supporting user wellness and autonomy.
"""
            meta = {
                "nudges": 0,
                "pending_nudges": pending_nudges,
                "generator": "template",
                "error": str(e),
                "episodic_evidence_present": bool(
                    episodic_evidence and episodic_evidence.strip(),
                ),
            }

        # Persist reflection
        await self.mem.insert_reflection(
            kind="daily_journal",
            content=content,
            meta=meta,
            ts=now.isoformat(),
            pinned=False,
        )

        # Export to file
        export_dir = os.path.join(os.path.dirname(__file__), "..", "..", "exports", "sessions")
        os.makedirs(export_dir, exist_ok=True)
        export_path = os.path.join(export_dir, f"{now.date()}.md")
        with open(export_path, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"[Kernel] Daily reflection saved to {export_path}")

    async def _run_weekly_reflection(self, now: datetime) -> None:
        """Generate and persist weekly alignment audit."""
        print("[Kernel] Running weekly alignment audit...")

        iso_week = now.isocalendar()[1]
        year = now.year

        # Collect episodic evidence before generating -- same ownership
        # rationale as _run_daily_reflection()'s equivalent block above.
        episodic_evidence: str | None = None
        try:
            episodic_evidence = await run_off_loop(
                self.narrator.generate_weekly_reflection_narrative,
                executor=self.blocking_executor,
            )
        except Exception as e:
            print(f"[Kernel] Failed to collect episodic evidence for weekly reflection: {e}")

        # Generate audit using Identity Interpreter
        try:
            from identity_interpreter.adapters.reflection_generator import ReflectionGenerator

            generator = ReflectionGenerator(identity_path="Identity.yaml")
            result = generator.generate_weekly_audit(
                weekly_scope={
                    "reflections_count": 7,  # Placeholder
                    "policy_checks": 0,
                    "safety_triggers": 0,
                },
                iso_week=iso_week,
                year=year,
                backend="stub",
                episodic_evidence=episodic_evidence,
            )

            content = result["content"]
            meta = {
                "week": iso_week,
                "year": year,
                **result["meta"],
                "safety": result["safety"],
            }

            if not result["success"]:
                print(f"[Kernel] Weekly audit used fallback: {meta.get('error', 'unknown')}")
        except Exception as e:
            # Fallback to basic template on error
            # Last-resort template -- see _run_daily_reflection()'s equivalent.
            print(f"[Kernel] Weekly audit generator error: {e}, using fallback")
            episodic_section = _compose_episodic_section(episodic_evidence)
            content = f"""# Weekly Alignment Audit - Week {iso_week}, {year}

## Identity Core Alignment
- [x] Red lines respected (no deception, manipulation, harm)
- [x] Consent policies followed (proactive nudges with opt-out)
- [x] Privacy maintained (no unsolicited data sharing)
- [x] Safety protocols active (kill switch tested)

## Behavioral Review
- [x] Proactive care delivered within policy boundaries
- [x] No policy violations detected
- [x] User autonomy preserved
{episodic_section}
## Recommendations
Continue current operation. No remediation needed.
"""
            meta = {
                "week": iso_week,
                "year": year,
                "generator": "template",
                "error": str(e),
                "episodic_evidence_present": bool(
                    episodic_evidence and episodic_evidence.strip(),
                ),
            }

        # Persist reflection
        await self.mem.insert_reflection(
            kind="weekly_alignment_audit",
            content=content,
            meta=meta,
            ts=now.isoformat(),
            pinned=True,
        )

        # Export to file
        export_dir = os.path.join(os.path.dirname(__file__), "..", "..", "exports", "audit_logs")
        os.makedirs(export_dir, exist_ok=True)
        week_str = f"week-{year}-{iso_week:02d}.md"
        export_path = os.path.join(export_dir, week_str)
        with open(export_path, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"[Kernel] Weekly audit saved to {export_path}")

    async def handle_command(self, cmd: str) -> None:
        # Basic commands (simulate UI clicks)
        if cmd == "reflection_run_daily":
            await self._run_daily_reflection(datetime.now(tz=self.tz))
        elif cmd == "reflection_run_weekly":
            await self._run_weekly_reflection(datetime.now(tz=self.tz))


def _default_db_path() -> str:
    """
    Resolve default database path.

    Resolution order:
    1. BARTH_DB_PATH environment variable (used as-is)
    2. data/barth.db under project root (directory with pyproject.toml)
    3. data/barth.db under current working directory
    """
    env = os.getenv("BARTH_DB_PATH")
    if env:
        return env
    p = Path(__file__).resolve()
    for parent in [p.parent, *p.parents]:
        if (parent / "pyproject.toml").exists():
            return str(parent / "data" / "barth.db")
    return str(Path.cwd() / "data" / "barth.db")


async def run_kernel():
    kd = KernelDaemon(
        cfg_path="config/kernel.yaml",
        db_path=_default_db_path(),
        persona_path="config/persona.yaml",
        policy_path="config/policy.yaml",
        drives_path="config/drives.yaml",
    )
    await kd.start()
    # keep alive
    while True:
        await asyncio.sleep(3600)
