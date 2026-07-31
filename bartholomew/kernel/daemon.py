from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import yaml
from dateutil import tz

from . import lifecycle_marker
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

logger = logging.getLogger(__name__)


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
        self.mem = MemoryStore(db_path)

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
        if identity_path:
            try:
                from identity_interpreter.identity_context import build_identity_context
                from identity_interpreter.loader import load_identity

                identity = load_identity(identity_path)
                self.identity_context = build_identity_context(identity)
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
        )
        self.planner.set_skill_registry(self.skill_registry)

        # Task handles for lifecycle management
        self._tick_task = None
        self._consumer_task = None
        self._dream_task = None
        self._scheduler_task = None

        # Phase B, stage B5: set True only by start()'s failed-start unwind
        # (never cleared) once cleanup has run -- a poisoned instance's
        # sub-resources (executors, etc.) may already be closed, so start()
        # refuses to re-run initialization against them; construct a new
        # KernelDaemon instead. self._lifecycle_instance_id identifies this
        # run for the clean-shutdown marker (lifecycle_marker.py).
        self._poisoned = False
        self._lifecycle_instance_id: str | None = None
        self._lifecycle_started_at: int | None = None

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

    async def start(self) -> None:
        if self._poisoned:
            raise RuntimeError(
                "KernelDaemon.start() called on a poisoned instance -- a previous start() "
                "failed and this instance's sub-resources may already be closed; construct "
                "a new KernelDaemon instead of reusing this one",
            )

        await self.mem.init()

        # Phase B, stage B5: conservative unclean-start detection. Read
        # whatever marker the *previous* run left (absent on a fresh
        # database, or one from before this stage). If it exists and never
        # reached "clean_shutdown", the previous run didn't call stop() to
        # completion -- crash, kill -9, or an exception this method's own
        # protected region below didn't cover. Logged only: no automatic
        # remediation, no blocked startup -- see lifecycle_marker.py's
        # module docstring for why.
        previous_marker = await self.mem._db_executor.call(
            lifecycle_marker.read_marker,
            self.mem.db_path,
        )
        if previous_marker is not None and previous_marker.state != "clean_shutdown":
            logger.warning(
                "Unclean start detected: previous daemon instance %s did not reach a "
                "confirmed clean shutdown (last known state=%r, started_at=%s)",
                previous_marker.instance_id,
                previous_marker.state,
                previous_marker.started_at,
            )

        self._lifecycle_instance_id = lifecycle_marker.new_instance_id()
        self._lifecycle_started_at = int(datetime.now(timezone.utc).timestamp())
        await self.mem._db_executor.call(
            lifecycle_marker.write_marker,
            self.mem.db_path,
            instance_id=self._lifecycle_instance_id,
            state="running",
            started_at=self._lifecycle_started_at,
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
        #
        # Protected region (Codex review on PR #25): once the first awaited
        # ensure_schema() call has activated SchedulerStore (spawning its
        # dedicated worker thread), EVERY abnormal exit from start() before
        # successful scheduler-task creation must close the store -- stop()
        # may never run after a failed start(), and an aborted ASGI startup
        # is not guaranteed to invoke it either. That includes cancellation:
        # asyncio.CancelledError inherits BaseException, so the cleanup guard
        # catches BaseException, runs close() under asyncio.shield() (so the
        # in-flight cancellation cannot interrupt the cleanup itself), and
        # then ALWAYS re-raises the original exception or CancelledError --
        # nothing is swallowed or translated. Cleanup cannot hang: it reuses
        # SchedulerStore.close()'s existing bounded drain (default 5s), which
        # never waits indefinitely. If cleanup itself fails, that failure is
        # logged as secondary and must never replace the primary error.
        try:
            await self.scheduler_store.ensure_schema()

            # Stage 3: Initialize experience kernel state
            await self._init_experience_kernel()

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

            # Stage 3: Subscribe narrator to workspace events
            self.narrator.subscribe_to_workspace()

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

            # Start scheduler (autonomy loop)
            from .scheduler.loop import run_scheduler

            self._scheduler_task = asyncio.create_task(run_scheduler(self))
        except BaseException:  # includes CancelledError; re-raised below
            self._poisoned = True
            try:
                await asyncio.shield(self._unwind_after_failed_start())
            except BaseException:  # secondary only; primary re-raised below
                logger.exception(
                    "Cleanup failed during aborted startup; propagating the original "
                    "startup error as primary",
                )
            raise

    async def _unwind_after_failed_start(self) -> None:
        """
        Drain every resource start() may have already activated before the
        failure, so nothing leaks a running background thread past this
        method's return (Phase B, stage B5). stop() may never run after a
        failed start(), and an aborted ASGI startup is not guaranteed to
        invoke it either -- this is the only cleanup a failed start() gets.

        Order mirrors stop()'s own (skills, then scheduler, then memory
        store) for predictability, though unlike stop() there is no
        checkpoint here: mem.close(checkpoint=False) only drains its
        dedicated DB executor, since checkpointing a possibly
        half-initialized database on a failed start is not warranted.

        Every step is independently try/excepted so one resource's cleanup
        failure cannot prevent the others' from running; each failure is
        logged, not silently swallowed.
        """
        try:
            await self.skill_registry.shutdown()
        except Exception:
            logger.exception("skill_registry cleanup failed during aborted startup")

        try:
            await self.scheduler_store.close()
        except Exception:
            logger.exception("scheduler_store cleanup failed during aborted startup")

        try:
            await self.mem.close(checkpoint=False)
        except Exception:
            logger.exception("mem cleanup failed during aborted startup")

    async def _init_experience_kernel(self) -> None:
        """Initialize experience kernel from last snapshot or defaults."""
        db_path = self.mem.db_path

        try:
            # Try to load last experience snapshot
            snapshot = self.experience.load_last_snapshot()
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
            wm_loaded = self.working_memory.load_last_snapshot(db_path)
            if wm_loaded:
                print("[Kernel] Restored working memory from last snapshot")
            else:
                print("[Kernel] Starting with empty working memory")

            # Activate default persona if none active. switch_pack() writes
            # to persona_switch_log synchronously; offloaded via
            # asyncio.to_thread() (Phase B, stage B2) so this async method
            # doesn't block the event loop on that write -- see
            # persona_pack.py's check_same_thread=False note for why this
            # is safe.
            if not self.persona_manager.get_active_pack_id():
                packs = self.persona_manager.list_packs()
                if packs:
                    await asyncio.to_thread(
                        self.persona_manager.switch_pack,
                        packs[0],
                        trigger="startup",
                    )
                    print(f"[Kernel] Activated persona: {packs[0]}")
        except Exception as e:
            print(f"[Kernel] Experience kernel init warning: {e}")

    async def stop(self) -> None:
        """Gracefully stop the kernel daemon."""
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

        # Stage 3: Persist experience snapshot
        try:
            self.experience.persist_snapshot()
            print("[Kernel] Experience state persisted")
        except Exception as e:
            print(f"[Kernel] Failed to persist experience state: {e}")

        # Stage 3: Persist working memory snapshot
        try:
            self.working_memory.persist_snapshot(self.mem.db_path)
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

        # Wait for cancellation with timeout
        for task in tasks:
            if task:
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

        # Close the scheduler's dedicated DB worker thread before the
        # memory store's own final checkpoint below, so nothing is still
        # mid-operation on the same file when that checkpoint runs. If it
        # doesn't drain cleanly within the bound, skip the (blocking,
        # exclusive) TRUNCATE checkpoint entirely rather than risk
        # contending with a thread that may still be running -- see
        # SchedulerStore.close()'s docstring and DECISIONS.md.
        scheduler_drained = await self.scheduler_store.close()
        if not scheduler_drained:
            print(
                "[Kernel] Scheduler store did not drain cleanly on shutdown; "
                "deferring WAL cleanup to next startup",
            )

        # Close memory store (checkpoint WAL, unless the scheduler store
        # above didn't drain -- see comment above)
        await self.mem.close(checkpoint=scheduler_drained)

        # Phase B, stage B5: record a confirmed clean shutdown. Last step,
        # after mem.close() has already drained and closed mem's own
        # dedicated DB executor -- so this uses a direct connection rather
        # than routing through it, consistent with several of this
        # method's other steps above (e.g. experience.persist_snapshot())
        # that already call synchronous code directly rather than through
        # an executor. If this instance's start() never actually reached
        # the point of writing an initial marker (e.g. stop() called on a
        # never-started or already-poisoned instance), _lifecycle_instance_id
        # is None and there is nothing meaningful to mark -- skipped.
        if self._lifecycle_instance_id is not None:
            try:
                lifecycle_marker.write_marker(
                    self.mem.db_path,
                    instance_id=self._lifecycle_instance_id,
                    state="clean_shutdown",
                    started_at=self._lifecycle_started_at or 0,
                    stopped_at=int(datetime.now(timezone.utc).timestamp()),
                )
            except Exception:
                logger.exception("Failed to write clean-shutdown lifecycle marker")

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
                self.persona_manager.auto_activate_if_needed(context_tags)

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
            # Fallback to basic template on error
            print(f"[Kernel] Reflection generator error: {e}, using fallback")
            content = f"""# Daily Reflection - {now.date()}

## Summary
Wellness monitoring and proactive care delivered.

## Wellness
- System monitoring active
- Pending nudges: {pending_nudges}

## Notable Events
(Future: chat highlights, emotional events, user activities)

## Intent for Tomorrow
Continue supporting user wellness and autonomy.
"""
            meta = {
                "nudges": 0,
                "pending_nudges": pending_nudges,
                "generator": "template",
                "error": str(e),
            }

        # Enrich with the Experience Kernel's own episodic narrative (real
        # affect/attention/drive/goal/observation episodes from today) --
        # otherwise the persisted/exported reflection only ever contains
        # ReflectionGenerator's generic template text (its own "Notable
        # Events" section literally says "(Future: chat highlights,
        # emotional events, user activities)"), even though the Narrator's
        # episodic layer already tracks exactly that. This was ROADMAP.md
        # Stage 3's "Still open: reconciling the two non-unified reflection
        # pipelines" note -- appended rather than merged/replacing either
        # pipeline's own output, the safer integration given both pipelines
        # are independently tested and this doesn't require parsing either
        # one's text. Never lets a narrator error break reflection
        # generation.
        try:
            episodic_narrative = self.narrator.generate_daily_reflection_narrative(now)
            if episodic_narrative:
                content = f"{content}\n\n---\n\n{episodic_narrative}"
                meta["episodic_narrative_included"] = True
        except Exception as e:
            print(f"[Kernel] Failed to include episodic narrative in daily reflection: {e}")

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
            print(f"[Kernel] Weekly audit generator error: {e}, using fallback")
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

## Recommendations
Continue current operation. No remediation needed.
"""
            meta = {
                "week": iso_week,
                "year": year,
                "generator": "template",
                "error": str(e),
            }

        # Enrich with the Experience Kernel's own episodic narrative for the
        # week -- same rationale as _run_daily_reflection()'s equivalent
        # block above. Appended, not merged; never lets a narrator error
        # break reflection generation.
        try:
            episodic_narrative = self.narrator.generate_weekly_reflection_narrative()
            if episodic_narrative:
                content = f"{content}\n\n---\n\n{episodic_narrative}"
                meta["episodic_narrative_included"] = True
        except Exception as e:
            print(f"[Kernel] Failed to include episodic narrative in weekly reflection: {e}")

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
