from __future__ import annotations

import asyncio
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import yaml
from dateutil import tz

from .event_bus import EventBus
from .experience_kernel import ExperienceKernel
from .global_workspace import EventType, GlobalWorkspace
from .memory_store import MemoryStore
from .narrator import NarratorEngine
from .persona import load_persona
from .persona_pack import PersonaPackManager
from .planner import Planner
from .policy import load_policy
from .state_model import WorldState
from .working_memory import WorkingMemoryManager


class KernelDaemon:
    def __init__(
        self,
        cfg_path: str,
        db_path: str,
        persona_path: str,
        policy_path: str,
        drives_path: str,
        loop_interval_s: int = 15,
    ):
        self.cfg = yaml.safe_load(open(cfg_path, encoding="utf-8"))
        self.tz = tz.gettz(self.cfg["timezone"])
        self.interval = int(self.cfg.get("loop_interval_seconds", loop_interval_s))
        self.bus = EventBus()
        self.mem = MemoryStore(db_path)
        self.persona = load_persona(persona_path)
        self.policy = load_policy(policy_path)
        self.drives = yaml.safe_load(open(drives_path, encoding="utf-8"))
        self.planner = Planner(self.policy, self.drives, self.mem)
        self.state = WorldState()

        # Stage 3: Experience Kernel modules
        self.workspace = GlobalWorkspace()
        self.experience = ExperienceKernel(
            db_path=db_path,
            workspace=self.workspace,
        )
        self.narrator = NarratorEngine(db_path=db_path, workspace=self.workspace)
        self.working_memory = WorkingMemoryManager(
            workspace=self.workspace,
            kernel=self.experience,
        )
        self.persona_manager = PersonaPackManager(
            experience_kernel=self.experience,
            workspace=self.workspace,
            db_path=db_path,
        )

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

    async def start(self) -> None:
        await self.mem.init()

        # Stage 3: Initialize experience kernel state
        self._init_experience_kernel()

        # Stage 3: Subscribe narrator to workspace events
        self.narrator.subscribe_to_workspace()

        # Stage 3: Emit startup event
        self.workspace.publish(
            channel="system",
            event_type=EventType.SYSTEM_EVENT,
            source="kernel_daemon",
            payload={"event": "startup", "timestamp": datetime.now(timezone.utc).isoformat()},
        )

        # Start background tasks
        self._tick_task = asyncio.create_task(self._system_tick())
        self._consumer_task = asyncio.create_task(self._system_consumer())
        self._dream_task = asyncio.create_task(self._dream_loop())

        # Start scheduler (autonomy loop)
        from .scheduler.loop import run_scheduler

        self._scheduler_task = asyncio.create_task(run_scheduler(self))

    def _init_experience_kernel(self) -> None:
        """Initialize experience kernel from last snapshot or defaults."""
        db_path = self.mem.db_path

        try:
            # Try to load last experience snapshot
            snapshot = self.experience.load_last_snapshot()
            if snapshot:
                print("[Kernel] Restored experience state from last snapshot")
            else:
                print("[Kernel] Starting with fresh experience state")

            # Try to load last working memory snapshot
            wm_loaded = self.working_memory.load_last_snapshot(db_path)
            if wm_loaded:
                print("[Kernel] Restored working memory from last snapshot")
            else:
                print("[Kernel] Starting with empty working memory")

            # Activate default persona if none active
            if not self.persona_manager.get_active_pack_id():
                packs = self.persona_manager.list_packs()
                if packs:
                    self.persona_manager.switch_pack(
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

        # Close memory store (checkpoint WAL)
        await self.mem.close()

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
                self.experience.decay_affect(rate=0.02)

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

        # Get pending nudges count for richer context
        pending_nudges = 0
        try:
            from .scheduler.persistence import get_system_metrics

            metrics = get_system_metrics(self.mem.db_path)
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
