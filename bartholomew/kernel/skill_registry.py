"""
Skill Registry
==============

Central registry for managing skill lifecycle: discovery, loading, unloading,
and event routing.

Part of Stage 4: Skill Registry + Starter Skills.
"""

from __future__ import annotations

import asyncio
import contextvars
import importlib
import inspect
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import policy_engine
from .db_ctx import connect, set_wal_pragmas
from .memory.privacy_guard import get_consent_handler
from .redaction_engine import redact_pii
from .reflection import ActionReflection, record_action_reflection
from .runtime_contract import CandidateAction, Interpretation, Observation
from .skill_base import SkillBase, SkillContext, SkillResult, SkillState
from .skill_manifest import SkillManifest, discover_manifests
from .skill_permissions import (
    PERMISSION_CATEGORIES,
    PermissionChecker,
    collect_permission_audit_failures,
    drain_permission_audit_failures,
    get_permission_checker,
)

if TYPE_CHECKING:
    from identity_interpreter.identity_context import IdentityContext

    from .experience_kernel import ExperienceKernel
    from .global_workspace import GlobalWorkspace, WorkspaceEvent
    from .memory_store import MemoryStore
    from .working_memory import WorkingMemoryManager

logger = logging.getLogger(__name__)


@dataclass
class _ActionFrame:
    """One action executing somewhere up the current task's call chain."""

    skill_id: str
    live: bool = True


#: The actions executing up the current task's call chain, outermost first.
#: The registry copies this into each action's task, so an action that asks
#: the registry to run another action on its *own* skill (directly, or
#: through a cycle of skills) is refused at once instead of waiting forever
#: for an occupancy its own caller holds. A frame stops being `live` when its
#: action ends, so a background task the action spawned may call the same
#: skill later, once the action that spawned it is over. See
#: _execute_occupied() and _is_reentrant().
_ACTIVE_SKILL_CHAIN: contextvars.ContextVar[tuple[_ActionFrame, ...]] = contextvars.ContextVar(
    "bartholomew_active_skill_chain",
    default=(),
)


def _is_reentrant(skill_id: str) -> bool:
    """True if an action on `skill_id` is still executing up this call chain."""
    return any(frame.skill_id == skill_id and frame.live for frame in _ACTIVE_SKILL_CHAIN.get())


#: How long a request waits for a skill that is executing another action
#: before it is refused as busy (SkillRegistry(queue_timeout=...)). Longer
#: than the longest legitimate action: a notification webhook POST is capped
#: at 10 s, a contended SQLite write at its 5 s busy_timeout.
DEFAULT_QUEUE_TIMEOUT_S = 30.0

#: How long one action may execute before the registry cancels it and
#: returns the skill to READY (SkillRegistry(execution_timeout=...)).
DEFAULT_EXECUTION_TIMEOUT_S = 60.0

#: How long the registry waits for a cancelled action to actually finish
#: before releasing the skill anyway (a skill that swallows cancellation).
_SETTLE_TIMEOUT_S = 5.0


@dataclass
class SkillOccupancy:
    """
    One loaded skill's occupancy: which action is executing on the instance
    and how many requests are waiting their turn behind it.

    The lock is the serialization unit of the registry's execution contract
    (docs/SKILL_EXECUTION_CONCURRENCY_CONTRACT.md): at most one action runs
    on a skill instance at a time, and an overlapping request waits, in
    arrival order, instead of being refused because the instance happens to
    be RUNNING. Requests to different skills never share a lock.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    in_flight_action: str | None = None
    in_flight_since: float | None = None  # time.monotonic()
    waiters: int = 0

    def to_dict(self) -> dict[str, Any]:
        in_flight_for = (
            round(time.monotonic() - self.in_flight_since, 3)
            if self.in_flight_since is not None
            else None
        )
        return {
            "in_flight_action": self.in_flight_action,
            "in_flight_for_s": in_flight_for,
            "waiters": self.waiters,
        }


@dataclass
class LoadedSkill:
    """Container for a loaded skill instance."""

    manifest: SkillManifest
    instance: SkillBase
    loaded_at: datetime
    subscription_ids: list[str] = field(default_factory=list)
    occupancy: SkillOccupancy = field(default_factory=SkillOccupancy)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.manifest.skill_id,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "state": self.instance.state.value,
            "loaded_at": self.loaded_at.isoformat(),
            "occupancy": self.occupancy.to_dict(),
        }


class SkillRegistry:
    """
    Central registry for skill management.

    Handles:
    - Skill discovery from manifest directory
    - Dynamic loading/unloading of skills
    - Permission enforcement
    - Event routing from GlobalWorkspace to skills
    - Skill state persistence
    """

    # Schema for skill registry state
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS skill_registry_state (
        skill_id TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_loaded TEXT,
        last_error TEXT,
        config_json TEXT
    );

    -- Audit trail for every execute_action() attempt (success, failure,
    -- permission denial, or parking-brake block) -- distinct from
    -- skill_permissions.permission_audit, which only logs permission
    -- checks/grants, not the resulting action outcome.
    CREATE TABLE IF NOT EXISTS skill_action_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_id TEXT NOT NULL,
        action TEXT NOT NULL,
        params_json TEXT,
        status TEXT NOT NULL,
        result_message TEXT,
        result_error TEXT,
        timestamp TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_skill_action_audit_skill
        ON skill_action_audit(skill_id);
    CREATE INDEX IF NOT EXISTS idx_skill_action_audit_time
        ON skill_action_audit(timestamp);

    -- Same table MemoryStore's own schema creates (see memory_store.py).
    -- Recreated here, identically and idempotently, so _is_blocked_by_brake()
    -- can read the parking brake's state even when a SkillRegistry is used
    -- against a db_path whose MemoryStore hasn't been initialized yet (or
    -- isn't used at all) -- otherwise the brake check itself would fail
    -- with "no such table", which _is_blocked_by_brake() treats as blocked
    -- (fail-closed), incorrectly denying every action.
    CREATE TABLE IF NOT EXISTS system_flags (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """

    def __init__(
        self,
        skills_dir: str | Path = "config/skills",
        db_path: str | None = None,
        workspace: GlobalWorkspace | None = None,
        kernel: ExperienceKernel | None = None,
        working_memory: WorkingMemoryManager | None = None,
        memory_store: MemoryStore | None = None,
        permission_checker: PermissionChecker | None = None,
        identity_context: IdentityContext | None = None,
        blocking_executor: Any | None = None,
        governance_store: Any | None = None,
        queue_timeout: float = DEFAULT_QUEUE_TIMEOUT_S,
        execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT_S,
    ) -> None:
        """
        Initialize skill registry.

        Args:
            skills_dir: Directory containing skill manifest YAML files
            db_path: Path to database for registry state
            workspace: GlobalWorkspace for event routing
            kernel: ExperienceKernel reference
            working_memory: WorkingMemoryManager reference
            memory_store: MemoryStore reference
            permission_checker: Permission checker instance
            identity_context: Optional IdentityContext (see
                bartholomew.kernel.policy_engine). When provided,
                execute_action() also consults the Executive's tool-use
                Policy Decision for the skill_id, in addition to the
                existing manifest-permission/parking-brake checks -- closing
                the "Identity.yaml governs only chat" gap (MASTER_PLAN.md's
                "P2.5 -- Runtime Convergence", item 11.2). When None (the
                default), this check is skipped entirely -- matching this
                class's existing pattern for other optional resources (see
                _is_blocked_by_brake()) -- so callers that don't wire an
                Identity Context see no behavior change.
            blocking_executor: Optional bartholomew.kernel.blocking_executor
                .SingleWorkerExecutor. When provided, _is_blocked_by_brake()
                and load_enabled_skills() submit their blocking SQLite work
                to it instead of a one-off asyncio.to_thread() fallback --
                see run_off_loop()'s docstring. None (the default) is fully
                supported, matching this class's existing optional-resource
                pattern.
            governance_store: Optional bartholomew.orchestrator.safety
                .governance_store.GovernanceStore (Phase B stage B4). When
                provided, _is_blocked_by_brake() checks it directly
                instead of constructing a fresh instance per call. None
                (the default, or set later via set_governance_store()
                once KernelDaemon.start() has constructed one) falls back
                to a temporary instance per check.
            queue_timeout: Execution contract -- how long a request waits
                for a skill that is executing another action before it is
                refused as busy (see _execute_occupied()).
            execution_timeout: Execution contract -- how long one action may
                execute before it is cancelled and the skill returned to
                READY; also bounds how long unload_skill() waits for an
                action in flight.
        """
        self._skills_dir = Path(skills_dir)
        self._db_path = db_path
        self._workspace = workspace
        self._kernel = kernel
        self._working_memory = working_memory
        self._memory_store = memory_store
        self._identity_context = identity_context
        self._blocking_executor = blocking_executor
        self._governance_store = governance_store
        self._queue_timeout = float(queue_timeout)
        self._execution_timeout = float(execution_timeout)

        # Permission checker
        self._permission_checker = permission_checker or get_permission_checker(db_path=db_path)

        # Discovered manifests (skill_id -> manifest)
        self._manifests: dict[str, SkillManifest] = {}

        # Loaded skill instances (skill_id -> LoadedSkill)
        self._loaded: dict[str, LoadedSkill] = {}

        # Event subscriptions for routing
        self._channel_subscriptions: dict[str, str] = {}

        # Initialize database
        if self._db_path:
            self._init_database()

        # Discover available skills
        self.discover_skills()

    def _init_database(self) -> None:
        """Initialize database schema."""
        if not self._db_path:
            return

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_connection()
        try:
            conn.executescript(self.SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        """
        Get a database connection configured by the kernel's single
        connection authority (`bartholomew.kernel.db_ctx`).

        WP-A2 / register OP-W004. This was a bare ``sqlite3.connect()``,
        the same pattern `bartholomew/skills/notify.py` documents having
        already been found to raise ``sqlite3.OperationalError: database is
        locked`` under concurrent writers to this shared database file. Two
        concrete consequences were reproduced directly against this module
        before the change:

        * an unconfigured connection never asserts WAL, so whichever
          component creates the database file first decides its journal
          mode -- and this class is constructed during ``KernelDaemon
          .__init__()``, *before* ``MemoryStore.init()`` runs, so on a fresh
          install it created the file in rollback-journal mode, where
          readers and writers block each other;
        * the resulting failures reached this module's ``skill_action_audit``
          write, which swallowed them (see ``_audit_execution``).

        ``set_wal_pragmas()`` brings this connection in line with the rest
        of the kernel -- WAL, ``synchronous=NORMAL``, ``foreign_keys=ON``,
        ``busy_timeout=5000``. It does not, and cannot, remove write
        contention itself; making the resulting failure *truthful* is
        ``_audit_execution``'s job, not this method's.
        """
        if not self._db_path:
            raise RuntimeError("No database configured")
        conn = connect(self._db_path)
        set_wal_pragmas(conn)
        conn.row_factory = sqlite3.Row
        return conn

    # -------------------------------------------------------------------------
    # Discovery
    # -------------------------------------------------------------------------

    def discover_skills(self) -> list[SkillManifest]:
        """
        Discover all skill manifests in the skills directory.

        Returns:
            List of discovered manifests
        """
        manifests = discover_manifests(self._skills_dir)

        self._manifests.clear()
        for manifest in manifests:
            self._manifests[manifest.skill_id] = manifest
            logger.info(
                "Discovered skill: %s v%s",
                manifest.skill_id,
                manifest.version,
            )

        return manifests

    def get_manifest(self, skill_id: str) -> SkillManifest | None:
        """Get manifest for a skill."""
        return self._manifests.get(skill_id)

    def list_available(self) -> list[str]:
        """List all available skill IDs (discovered but not necessarily loaded)."""
        return list(self._manifests.keys())

    def list_loaded(self) -> list[str]:
        """List all currently loaded skill IDs."""
        return list(self._loaded.keys())

    def get_all_manifests(self) -> list[SkillManifest]:
        """Get all discovered manifests."""
        return list(self._manifests.values())

    # -------------------------------------------------------------------------
    # Loading / Unloading
    # -------------------------------------------------------------------------

    async def load_skill(self, skill_id: str) -> bool:
        """
        Load a skill by ID.

        Args:
            skill_id: ID of skill to load

        Returns:
            True if loaded successfully
        """
        # Check if already loaded
        if skill_id in self._loaded:
            logger.warning("Skill already loaded: %s", skill_id)
            return True

        # Get manifest
        manifest = self._manifests.get(skill_id)
        if not manifest:
            logger.error("Unknown skill: %s", skill_id)
            return False

        # Check if enabled
        if not manifest.enabled:
            logger.info("Skill disabled: %s", skill_id)
            return False

        try:
            # Import module and instantiate class
            instance = self._instantiate_skill(manifest)
            if not instance:
                return False

            # Set up auto-permissions from manifest
            if manifest.permissions.level == "auto":
                self._permission_checker.set_auto_permissions(
                    skill_id,
                    manifest.permissions.requires,
                )

            # Create context
            context = self._create_context(manifest)

            # Initialize skill
            instance._set_state(SkillState.LOADING)
            await instance.initialize(context)
            instance._context = context
            instance._loaded_at = datetime.utcnow()
            instance._set_state(SkillState.READY)

            # Subscribe to events
            subscription_ids = self._setup_subscriptions(manifest, instance)

            # Store loaded skill
            self._loaded[skill_id] = LoadedSkill(
                manifest=manifest,
                instance=instance,
                loaded_at=datetime.utcnow(),
                subscription_ids=subscription_ids,
            )

            # Persist state (Phase B stage B8: off the event loop --
            # _persist_skill_state() does real synchronous sqlite3 I/O,
            # confirmed by direct read; previously called directly here).
            from .blocking_executor import run_off_loop

            await run_off_loop(
                self._persist_skill_state,
                skill_id,
                enabled=True,
                executor=self._blocking_executor,
            )

            logger.info(
                "Loaded skill: %s v%s",
                manifest.skill_id,
                manifest.version,
            )
            return True

        except Exception as e:
            logger.exception("Failed to load skill %s: %s", skill_id, e)
            from .blocking_executor import run_off_loop

            await run_off_loop(
                self._persist_skill_state,
                skill_id,
                error=str(e),
                executor=self._blocking_executor,
            )
            return False

    def _instantiate_skill(self, manifest: SkillManifest) -> SkillBase | None:
        """Dynamically import and instantiate a skill class."""
        try:
            module = importlib.import_module(manifest.entry_module)
            cls = getattr(module, manifest.entry_class)

            if not issubclass(cls, SkillBase):
                logger.error(
                    "%s.%s is not a SkillBase subclass",
                    manifest.entry_module,
                    manifest.entry_class,
                )
                return None

            return cls()
        except ImportError as e:
            logger.error(
                "Failed to import %s: %s",
                manifest.entry_module,
                e,
            )
            return None
        except AttributeError as e:
            logger.error(
                "Class %s not found in %s: %s",
                manifest.entry_class,
                manifest.entry_module,
                e,
            )
            return None

    def _create_context(self, manifest: SkillManifest) -> SkillContext:
        """Create a SkillContext for a skill."""
        skill_id = manifest.skill_id

        def check_permission(permission: str) -> bool:
            result = self._permission_checker.check(skill_id, permission)
            return result.granted

        return SkillContext(
            kernel=self._kernel,
            workspace=self._workspace,
            working_memory=self._working_memory,
            memory_store=self._memory_store,
            db_path=self._db_path,
            manifest=manifest,
            check_permission=check_permission,
            # So a skill's own sqlite3 writes join the daemon's off-loop
            # worker and its confirmed drain at shutdown (Phase B stage B2);
            # see SkillBase._run_off_loop().
            blocking_executor=self._blocking_executor,
        )

    async def _permission_call(self, fn: Any, *args: Any) -> Any:
        """
        Run one `PermissionChecker` call off the event loop, under a copy of
        the current task's context.

        `check()` and `grant_session()` read persistent grants and write the
        required `permission_audit` row with synchronous sqlite3. Made on the
        event-loop thread, such a write can only wait for the write lock by
        blocking the loop -- and the lock's holder may be an aiosqlite
        transaction (a `MemoryStore` Reflection write from a scheduler drive)
        whose commit needs that same loop, so the wait can never succeed and
        fails after exactly `busy_timeout` with ``database is locked``. That
        is the mechanism behind the intermittent HTTP 400 on
        `/api/notifications/quiet-hours` and the Windows Merge Candidate
        failures of the writer-lock / WAL class.

        The context copy matters: WP-A2's per-action collector of failed
        permission-audit writes is a `ContextVar`, and a plain executor thread
        carries no context. Running `fn` inside `context.run` lets a failure
        recorded on the worker thread land in the collector `_finish()` drains.
        """
        from .blocking_executor import run_off_loop

        context = contextvars.copy_context()
        return await run_off_loop(context.run, fn, *args, executor=self._blocking_executor)

    def _setup_subscriptions(
        self,
        manifest: SkillManifest,
        instance: SkillBase,
    ) -> list[str]:
        """Set up GlobalWorkspace subscriptions for a skill."""
        subscription_ids = []

        if not self._workspace:
            return subscription_ids

        for sub in manifest.subscriptions:
            # Skills self-subscribe in their own initialize() (called just
            # above in load_skill), and all three bundled skills declare the
            # very channels they already subscribed to. Since neither path
            # sets a filter_fn, registering again here would match every
            # event twice and run handle_event twice per event. The skill's
            # own registration is already live, and its shutdown() clears it
            # via _unsubscribe_all(), so skip the duplicate.
            if sub.channel in instance._subscribed_channels:
                logger.debug(
                    "Skill %s already self-subscribed to channel %s; "
                    "skipping manifest subscription",
                    manifest.skill_id,
                    sub.channel,
                )
                continue

            # Create handler that routes events to skill
            async def async_handler(
                event: WorkspaceEvent,
                skill: SkillBase = instance,
            ) -> None:
                try:
                    await skill.handle_event(event)
                except Exception as e:
                    logger.error(
                        "Skill %s event handler error: %s",
                        skill.skill_id,
                        e,
                    )

            # GlobalWorkspace.publish() (used by most of the kernel, e.g.
            # daemon.py/skill_base.py/working_memory.py) is synchronous and
            # only ever invokes the sync `callback` slot -- it never awaits
            # `async_callback`. Passing a bare async handler as `callback`
            # created the coroutine but never ran it (silent no-op event
            # routing). Schedule it on the running loop instead so the sync
            # path still executes the handler; publish_async() continues to
            # use `async_callback` directly.
            def sync_handler(
                event: WorkspaceEvent,
                _async_handler: Any = async_handler,
                _skill: SkillBase = instance,
            ) -> None:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    logger.warning(
                        "Skill %s event dropped: no running event loop",
                        _skill.skill_id,
                    )
                    return
                loop.create_task(_async_handler(event))

            sub_id = self._workspace.subscribe(
                sub.channel,
                sync_handler,
                async_callback=async_handler,
            )
            subscription_ids.append(sub_id)
            # Recorded so a manifest listing the same channel more than once
            # collapses to a single registration, on the same bookkeeping the
            # skill's own _subscribe_to_channel() uses.
            instance._subscribed_channels.add(sub.channel)
            logger.debug(
                "Skill %s subscribed to channel: %s",
                manifest.skill_id,
                sub.channel,
            )

        return subscription_ids

    async def unload_skill(self, skill_id: str) -> bool:
        """
        Unload a skill.

        Args:
            skill_id: ID of skill to unload

        Returns:
            True if unloaded successfully
        """
        loaded = self._loaded.get(skill_id)
        if not loaded:
            logger.warning("Skill not loaded: %s", skill_id)
            return False

        # Execution contract, shutdown: refuse new arrivals from this moment
        # (admission sees UNLOADING), let the action in flight finish --
        # bounded by the execution timeout that also bounds the action
        # itself -- and hold the occupancy through shutdown, so a request
        # queued behind that action is refused rather than run on an
        # instance that is being shut down.
        loaded.instance._set_state(SkillState.UNLOADING)
        held = await self._acquire_occupancy(loaded.occupancy, self._execution_timeout)
        if not held:
            logger.warning(
                "Skill %s: action %s still executing after %.0fs; unloading anyway",
                skill_id,
                loaded.occupancy.in_flight_action,
                self._execution_timeout,
            )

        try:
            # Unsubscribe from events
            if self._workspace:
                for sub_id in loaded.subscription_ids:
                    self._workspace.unsubscribe(sub_id)

            # Shutdown skill
            await loaded.instance.shutdown()
            loaded.instance._set_state(SkillState.UNLOADED)

            # Clear auto-permissions
            self._permission_checker.clear_auto_permissions(skill_id)

            # Remove from loaded
            del self._loaded[skill_id]

            logger.info("Unloaded skill: %s", skill_id)
            return True

        except Exception as e:
            logger.exception("Failed to unload skill %s: %s", skill_id, e)
            return False
        finally:
            if held:
                loaded.occupancy.lock.release()

    async def reload_skill(self, skill_id: str) -> bool:
        """
        Reload a skill (unload then load).

        Args:
            skill_id: ID of skill to reload

        Returns:
            True if reloaded successfully
        """
        if skill_id in self._loaded:
            await self.unload_skill(skill_id)

        # Re-discover to pick up manifest changes
        self.discover_skills()

        return await self.load_skill(skill_id)

    # -------------------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------------------

    def get_skill(self, skill_id: str) -> SkillBase | None:
        """Get loaded skill instance."""
        loaded = self._loaded.get(skill_id)
        return loaded.instance if loaded else None

    async def execute_action(
        self,
        skill_id: str,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> SkillResult:
        """
        Execute an action on a loaded skill, with S2 audit accounting.

        WP-A2: this thin wrapper exists only to scope the per-action
        collector that carries failed `permission_audit` writes from inside
        the skill's own permission checks back to `_finish()` (see
        `bartholomew.kernel.skill_permissions
        .collect_permission_audit_failures`). The scope is opened here, at
        the outermost boundary of one governed action, so that every audit
        write attributable to that action -- authorisation and execution
        alike -- lands in one verdict. All behaviour is in
        `_execute_action_inner`, unchanged.
        """
        with collect_permission_audit_failures():
            return await self._execute_action_inner(skill_id, action, params)

    async def _execute_action_inner(
        self,
        skill_id: str,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> SkillResult:
        """
        Execute an action on a loaded skill.

        This is the single choke-point every skill execution flows through
        (whether triggered by the planner, an API route, or anything else),
        so it's where the parking-brake check, "ask"-level consent
        resolution, and the execution audit trail all live -- individual
        skills only self-check permissions they already have (see
        SkillBase._require_permission()); they have no path to actually
        *resolve* an "ask" grant, and nothing previously checked the
        parking brake's "skills" scope at all (a documented, supported
        scope per INTERFACES.md that nothing acted on).

        MASTER_PLAN.md item 11.19 (Runtime Convergence): this method now
        builds the same `Observation`/`Interpretation`/`CandidateAction`
        shape chat (`run_chat_through_runtime_contract()`) and scheduler
        drives (`run_drive_through_runtime_contract()`) use, and those
        objects genuinely drive the Governance stage below (the Identity
        Policy check reads `candidate_action.kind`, not a bare local) and
        the Reflection stage (`_finish()` sources `surface` from
        `observation.source`) -- not just constructed and discarded.
        `bartholomew.kernel.runtime_contract.run_skill_through_runtime_
        contract()` is the named production entry point that leads here
        (`Planner.handle_skill_request()`'s sole call target); this method
        remains the directly-callable, directly-tested execution primitive
        underneath it, not a competing path.

        Args:
            skill_id: ID of skill
            action: Action name to execute
            params: Parameters for the action

        Returns:
            SkillResult from the skill
        """
        # Stage 1: Observation. Stage 2: Interpretation -- a skill request
        # carries no natural-language prompt, so (like a scheduler drive's
        # task_id) its "<skill_id>.<action>" is the whole content.
        observation = Observation(source="skill", raw_content=f"{skill_id}.{action}")
        interpretation = Interpretation(observation=observation, prompt=observation.raw_content)
        # Stage 3: Executive -- propose a candidate action. `kind=skill_id`
        # (not "<skill_id>.<action>") deliberately matches the exact grain
        # `evaluate_tool_policy()`/`Identity.yaml`'s `tool_use.allowlist`
        # already operate on -- confirmed by
        # tests/test_runtime_convergence_policy.py's ALLOW_CONTEXT, which
        # allowlists "tasks" (a skill_id), not an "<id>.<action>" pair.
        candidate_action = CandidateAction(kind=skill_id, interpretation=interpretation)

        loaded = self._loaded.get(skill_id)
        if not loaded:
            return await self._finish(
                observation,
                candidate_action,
                action,
                params,
                SkillResult.fail(f"Skill not loaded: {skill_id}"),
            )

        # Execution contract, admission (docs/SKILL_EXECUTION_CONCURRENCY_
        # CONTRACT.md): only a skill's *lifecycle* refuses a request here.
        # RUNNING is occupancy, not unavailability -- a request that finds
        # the skill executing another action waits its turn in
        # _execute_occupied() below, once Governance has allowed it.
        state = loaded.instance.state
        if state not in (SkillState.READY, SkillState.RUNNING):
            return await self._finish(
                observation,
                candidate_action,
                action,
                params,
                SkillResult.fail(
                    f"Skill not ready: {skill_id} (state={state.value})",
                ),
            )

        # Execution contract, re-entrancy: an action asking the registry to
        # run another action on its own skill (directly, or through a cycle
        # of skills) would wait forever for the occupancy its caller holds.
        # Refused at once instead; a nested call to a *different* skill is
        # ordinary and takes that skill's occupancy.
        if _is_reentrant(skill_id):
            return await self._finish(
                observation,
                candidate_action,
                action,
                params,
                SkillResult.fail(
                    f"Re-entrant action refused: {skill_id}.{action} was requested "
                    f"from inside an action already executing on {skill_id}",
                ),
            )

        # Validate action exists
        manifest_action = loaded.manifest.get_action(action)
        if not manifest_action:
            return await self._finish(
                observation,
                candidate_action,
                action,
                params,
                SkillResult.fail(f"Unknown action: {action} for skill {skill_id}"),
            )

        # Stage 4: Governance -- parking brake: fail-closed operational
        # kill-switch, scope="skills".
        if await self._is_blocked_by_brake():
            return await self._finish(
                observation,
                candidate_action,
                action,
                params,
                SkillResult.fail("Blocked by parking brake (scope=skills)"),
            )

        # Governance (cont.) -- Executive Policy Decision: consult the same
        # IdentityContext-derived tool-use policy scheduler drives consult
        # (see bartholomew.kernel.policy_engine and scheduler/loop.py's
        # _run_drive()), evaluated against `candidate_action.kind` -- the
        # CandidateAction constructed above, not a re-derived value -- so
        # this is the Executive/Governance stage genuinely consuming it.
        # Skipped entirely if no IdentityContext was wired in (see
        # __init__'s docstring) -- this is additive, not a replacement for
        # the manifest-permission/parking-brake checks above.
        if self._identity_context is not None:
            policy_decision = policy_engine.evaluate_tool_policy(
                self._identity_context,
                candidate_action.kind,
            )
            if not policy_decision.allowed:
                return await self._finish(
                    observation,
                    candidate_action,
                    action,
                    params,
                    SkillResult.fail(
                        f"Denied by Identity policy: {policy_decision.reason}",
                    ),
                )

        # Resolve any "ask"-level permissions the manifest requires before
        # executing (see _resolve_permissions()'s docstring).
        consent_result = await self._resolve_permissions(loaded.manifest)
        if consent_result is not None:
            return await self._finish(observation, candidate_action, action, params, consent_result)

        # Stage 5+6: Capability + Execution -- only reached once every
        # Governance check above has allowed the CandidateAction. The
        # execution runs under the skill's occupancy (one action at a time
        # per instance, bounded waiting, bounded execution, exact
        # cancellation and exception handling): see _execute_occupied().
        result = await self._execute_occupied(loaded, skill_id, action, params or {})
        return await self._finish(observation, candidate_action, action, params, result)

    async def _execute_occupied(
        self,
        loaded: LoadedSkill,
        skill_id: str,
        action: str,
        params: dict[str, Any],
    ) -> SkillResult:
        """
        Run one governed action under the skill's occupancy.

        This is the execution half of the registry's concurrency contract
        (docs/SKILL_EXECUTION_CONCURRENCY_CONTRACT.md; the admission half is
        in _execute_action_inner()):

        - Occupancy: at most one action executes on a skill instance at a
          time. A request that arrives while another is executing waits its
          turn, in arrival order, and then gets a real outcome -- never a
          refusal for the instance merely being RUNNING. Different skills
          never wait on each other here.
        - Bounded waiting: a wait longer than `queue_timeout` is refused as
          "Skill busy". Cancelling a waiting request removes it from the
          queue with no other effect.
        - Re-checks: what Governance decided before the wait can change
          during it, so the fail-closed parking brake and the skill's
          lifecycle are checked again once the occupancy is held, right
          before the action runs. Consent is not asked twice.
        - Bounded execution: the action runs as its own task and is
          cancelled after `execution_timeout`; the skill returns to READY
          and the request fails as timed out.
        - Cancellation: cancelling the request cancels the action, closes
          the RUNNING window, releases the occupancy and propagates. The
          skill is never left RUNNING.
        - Exceptions: an exception escaping execute() marks the skill ERROR
          (sticky until reload_skill()) and fails the request; a request
          queued behind it is refused with that state -- never admitted to
          a skill the registry considers broken, and never masking the
          ERROR with a later READY.
        - Exactly once: an admitted request executes at most once and the
          registry never retries; the caller records the outcome once.

        The task-per-action shape is deliberate: `asyncio.wait()` on a task
        gives exact cancellation and timeout semantics on every supported
        Python, where `asyncio.wait_for()` on 3.11 can return the inner
        result instead of raising when completion and cancellation land in
        the same loop iteration (docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md §8).
        """
        occupancy = loaded.occupancy
        instance = loaded.instance

        occupancy.waiters += 1
        try:
            acquired = await self._acquire_occupancy(occupancy, self._queue_timeout)
        finally:
            occupancy.waiters -= 1
        if not acquired:
            busy_with = occupancy.in_flight_action or "another request"
            logger.warning(
                "Skill %s action %s refused: busy with %s for longer than %.0fs",
                skill_id,
                action,
                busy_with,
                self._queue_timeout,
            )
            return SkillResult.fail(
                f"Skill busy: {skill_id} (waited {self._queue_timeout:g}s behind {busy_with})",
            )

        frame: _ActionFrame | None = None
        try:
            # Re-checks, now that this request is next in line.
            if self._loaded.get(skill_id) is not loaded or not instance.is_ready:
                return SkillResult.fail(
                    f"Skill not ready: {skill_id} (state={instance.state.value})",
                )
            if await self._is_blocked_by_brake():
                return SkillResult.fail("Blocked by parking brake (scope=skills)")

            occupancy.in_flight_action = action
            occupancy.in_flight_since = time.monotonic()
            instance._set_state(SkillState.RUNNING)

            # The action's task inherits the chain with this action's frame
            # added, so a re-entrant request from inside it (or from a task
            # it spawns while it is still executing) is refused at admission.
            frame = _ActionFrame(skill_id)
            token = _ACTIVE_SKILL_CHAIN.set((*_ACTIVE_SKILL_CHAIN.get(), frame))
            try:
                task = asyncio.create_task(
                    instance.execute(action, params),
                    name=f"skill-action:{skill_id}.{action}",
                )
            finally:
                _ACTIVE_SKILL_CHAIN.reset(token)

            try:
                done, _pending = await asyncio.wait({task}, timeout=self._execution_timeout)
            except asyncio.CancelledError:
                await self._settle(task)
                self._leave_running(instance)
                raise

            if not done:
                await self._settle(task)
                self._leave_running(instance)
                logger.error(
                    "Skill %s action %s exceeded %.0fs and was cancelled",
                    skill_id,
                    action,
                    self._execution_timeout,
                )
                return SkillResult.fail(
                    f"Skill action timed out: {skill_id}.{action} exceeded "
                    f"{self._execution_timeout:g}s and was cancelled",
                )
            if task.cancelled():
                self._leave_running(instance)
                return SkillResult.fail(f"Skill action cancelled: {skill_id}.{action}")
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "Skill %s action %s failed: %s",
                    skill_id,
                    action,
                    exc,
                    exc_info=exc,
                )
                instance._set_error(str(exc))
                return SkillResult.fail(str(exc))
            self._leave_running(instance)
            return task.result()
        finally:
            if frame is not None:
                frame.live = False
            occupancy.in_flight_action = None
            occupancy.in_flight_since = None
            occupancy.lock.release()

    @staticmethod
    async def _acquire_occupancy(occupancy: SkillOccupancy, timeout: float) -> bool:
        """
        Acquire the skill's occupancy lock within `timeout` seconds.

        Returns True when acquired (the caller then owns the lock and must
        release it), False on timeout. Cancellation propagates and never
        leaks the lock: an acquisition that won the lock in the same instant
        the wait was cancelled is given straight back. Waiters are served in
        arrival order (asyncio.Lock is FIFO among non-cancelled waiters).
        """
        acquire = asyncio.ensure_future(occupancy.lock.acquire())
        try:
            done, _pending = await asyncio.wait({acquire}, timeout=timeout)
        except asyncio.CancelledError:
            if SkillRegistry._withdraw(acquire):
                occupancy.lock.release()
            raise
        if done:
            return True
        # Timed out: withdraw, unless the lock was won at the last instant.
        return SkillRegistry._withdraw(acquire)

    @staticmethod
    def _withdraw(acquire: asyncio.Future[bool]) -> bool:
        """
        Withdraw a lock acquisition; True if it had already won the lock.

        A pending acquisition that is cancelled never ends up holding the
        lock: asyncio.Lock hands a lock granted to a cancelled waiter on to
        the next waiter itself. Only an acquisition that has already
        completed owns the lock, and then the caller is responsible for it.
        """
        if acquire.done():
            return (
                not acquire.cancelled() and acquire.exception() is None and bool(acquire.result())
            )
        acquire.cancel()
        return False

    @staticmethod
    async def _settle(task: asyncio.Task[Any]) -> None:
        """
        Cancel an action's task and wait until it has actually finished, so
        the skill's occupancy is never released while its action is still
        executing. Bounded: a skill that swallows cancellation and keeps
        running is abandoned after _SETTLE_TIMEOUT_S (logged) rather than
        holding the skill hostage.
        """
        task.cancel()
        done, _pending = await asyncio.wait({task}, timeout=_SETTLE_TIMEOUT_S)
        if not done:
            logger.error(
                "Skill action %s ignored cancellation for %.0fs; abandoning it",
                task.get_name(),
                _SETTLE_TIMEOUT_S,
            )

    @staticmethod
    def _leave_running(instance: SkillBase) -> None:
        """
        Close the RUNNING window without overwriting a lifecycle transition
        (UNLOADING, ERROR) that landed while the action ran.
        """
        if instance.state is SkillState.RUNNING:
            instance._set_state(SkillState.READY)

    def set_governance_store(self, governance_store: Any) -> None:
        """Wire in the daemon's shared GovernanceStore once constructed
        (Phase B stage B4) -- KernelDaemon.start() calls this after
        constructing its own instance, since GovernanceStore's
        construction does blocking I/O and must happen off the event
        loop, later than this class's own __init__."""
        self._governance_store = governance_store

    async def _is_blocked_by_brake(self) -> bool:
        """
        Check the Parking Brake's "skills" scope.

        Fails closed (treats as blocked) if the check itself errors --
        never silently allow a skill action when the safety gate can't be
        read, matching this codebase's fail-closed governance principle.
        Runs off the event loop (Phase B stage B2; see
        docs/B2_EVENT_LOOP_ISOLATION.md).

        Reads through the daemon's shared GovernanceStore (Phase B stage
        B4). Phase B stage B6 retired the B4 dual-check bridge against
        the legacy system_flags value, now that bartholomew/cli.py's
        `brake on`/`brake off` write only to GovernanceStore.
        """
        if not self._db_path:
            return False

        try:
            from bartholomew.orchestrator.safety.governance_store import (
                is_blocked_fail_closed_off_loop,
            )

            return await is_blocked_fail_closed_off_loop(
                "skills",
                self._db_path,
                governance_store=self._governance_store,
                executor=self._blocking_executor,
            )
        except Exception:
            logger.exception("Parking brake check failed; failing closed")
            return True

    async def _resolve_permissions(self, manifest: SkillManifest) -> SkillResult | None:
        """
        Ensure every permission the manifest declares in `requires` is
        granted before executing an action.

        "auto"-level permissions were already granted at load_skill() time.
        "ask"-level permissions are resolved here via the same registered
        consent handler used for memory consent
        (bartholomew.kernel.memory.privacy_guard) -- the only actual
        interactive "ask the user" plumbing in this codebase, reused for
        consistency rather than inventing a second one. Grants are
        session-scoped only (not persistent), so approving once doesn't
        silently become permanent config. Fails closed (denies) with no
        handler registered, exactly like the memory-consent path.

        Returns:
            None if every required permission is granted; otherwise a
            SkillResult.denied(...) for the first permission that wasn't.
        """
        for permission in manifest.permissions.requires:
            result = await self._permission_call(
                self._permission_checker.check,
                manifest.skill_id,
                permission,
            )
            if result.granted:
                continue

            if manifest.permissions.level != "ask":
                # "never", or an "auto" skill whose permission somehow
                # wasn't set at load time -- either way, a hard deny.
                return SkillResult.denied(permission)

            handler = get_consent_handler()
            if handler is None:
                return SkillResult.denied(permission)

            description = PERMISSION_CATEGORIES.get(permission, permission)
            prompt = (
                f"Skill '{manifest.skill_id}' requests permission '{permission}' ({description})"
            )
            approved = handler(prompt)
            if inspect.isawaitable(approved):
                approved = await approved

            if not approved:
                return SkillResult.denied(permission)

            await self._permission_call(
                self._permission_checker.grant_session,
                manifest.skill_id,
                permission,
            )

        return None

    async def _finish(
        self,
        observation: Observation,
        candidate_action: CandidateAction,
        action: str,
        params: dict[str, Any] | None,
        result: SkillResult,
    ) -> SkillResult:
        """
        Record the reflection for this execution, then return the result
        unchanged. Two writes, one per concern: the detailed, immediate
        `skill_action_audit` compliance row (`_audit_execution`), and the
        unified per-action Reflection into the shared Memory sink
        (`_record_reflection`) that chat also writes -- closing Exit Gate #4's
        "one Reflection shape through one sink" gap (see
        `bartholomew.kernel.reflection`). Exactly one call each, for every
        outcome (success, failure, permission denial, parking-brake block) --
        `execute_action()` has exactly one path to `_finish()` per attempt.

        `candidate_action.kind` is the skill_id the Governance stage in
        `execute_action()` actually evaluated -- reused here (rather than a
        separately threaded skill_id) so the audit row and Reflection are
        provably describing the same CandidateAction Governance decided on.
        """
        skill_id = candidate_action.kind
        # Phase B stage B8: off the event loop -- _audit_execution() does
        # real synchronous sqlite3 I/O (confirmed by direct read),
        # previously called directly here on every single skill action.
        from .blocking_executor import run_off_loop

        audit_error = await run_off_loop(
            self._audit_execution,
            skill_id,
            action,
            params,
            result,
            executor=self._blocking_executor,
        )
        if audit_error:
            result.mark_audit_degraded(audit_error)

        # WP-A2: permission-audit writes that failed while this action was
        # being authorised are collected per-action (see
        # `_collect_permission_audit_failures`) and folded in here, at the
        # same single chokepoint, so one action yields one truthful verdict
        # rather than two partial ones.
        for permission_audit_error in drain_permission_audit_failures():
            result.mark_audit_degraded(permission_audit_error)

        await self._record_reflection(observation, skill_id, action, params, result)
        return result

    async def _record_reflection(
        self,
        observation: Observation,
        skill_id: str,
        action: str,
        params: dict[str, Any] | None,
        result: SkillResult,
    ) -> None:
        """
        Emit the canonical per-action Reflection for a skill execution --
        every attempt (success, failure, permission denial, parking-brake
        block), the same set `_audit_execution` covers. Best-effort: never
        raises (see `record_action_reflection`).

        `surface` is sourced from `observation.source` ("skill") rather than
        a hardcoded literal, so the Reflection is genuinely describing the
        Observation `execute_action()` constructed, not just labeling itself.
        """
        reflection = ActionReflection(
            surface=observation.source,
            action=f"{skill_id}.{action}",
            outcome=result.status.value,
            summary=f"Skill '{skill_id}.{action}': {result.status.value}",
            details={"message": result.message, "error": result.error},
        )
        await record_action_reflection(self._memory_store, reflection)

    def _audit_execution(
        self,
        skill_id: str,
        action: str,
        params: dict[str, Any] | None,
        result: SkillResult,
    ) -> str | None:
        """
        Persist an audit record for every execute_action() attempt --
        success, failure, permission denial, or parking-brake block alike.

        Returns ``None`` when the row persisted, or the failure text when it
        did not.

        WP-A2 / safety gate S2 / register OP-W004. This used to end in a
        bare ``except Exception: logger.exception(...)``, so a governed
        action whose audit row was lost returned an unqualified success --
        the exact condition S2 forbids, and the exact condition Test #1
        observed twice as ``Failed to log audit: database is locked``.

        It still does not raise. Per the approved S2 semantics an action
        that genuinely passed every pre-action gate and genuinely executed
        must not be reported as having failed, and must not be retried,
        merely because its audit write did not persist. The caller
        (`_finish`) turns this return value into a truthful degraded result
        instead.
        """
        if not self._db_path:
            return None

        # Redact PII from top-level string param values before persisting --
        # this table has no consent-gate/redaction pipeline of its own,
        # same class of gap found and fixed for the Experience
        # Kernel/Narrator elsewhere in this codebase.
        sanitized_params = {
            k: (redact_pii(v) if isinstance(v, str) else v) for k, v in (params or {}).items()
        }

        now = datetime.utcnow().isoformat() + "Z"
        try:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO skill_action_audit
                    (skill_id, action, params_json, status, result_message, result_error, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        skill_id,
                        action,
                        json.dumps(sanitized_params),
                        result.status.value,
                        result.message,
                        result.error,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            # ERROR, not the previous silent-ish exception log: a lost audit
            # row for a governed action is a safety-relevant event, not a
            # background warning.
            logger.error(
                "REQUIRED AUDIT WRITE FAILED for %s.%s (skill_action_audit): %s",
                skill_id,
                action,
                e,
            )
            return f"skill_action_audit write failed: {e}"
        return None

    def get_action_audit_log(
        self,
        skill_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get the skill action execution audit log.

        Args:
            skill_id: Optional filter by skill
            limit: Maximum entries to return

        Returns:
            List of audit log entries, most recent first
        """
        if not self._db_path:
            return []

        conn = self._get_connection()
        try:
            if skill_id:
                rows = conn.execute(
                    """
                    SELECT * FROM skill_action_audit
                    WHERE skill_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (skill_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM skill_action_audit
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

            return [dict(row) for row in rows]
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # Event Routing
    # -------------------------------------------------------------------------

    async def route_event(self, event: WorkspaceEvent) -> None:
        """
        Route a GlobalWorkspace event to subscribed skills.

        Called by GlobalWorkspace when events are published.
        """
        channel = event.channel

        for skill_id, loaded in self._loaded.items():
            # Check if skill subscribes to this channel
            if loaded.manifest.subscribes_to(channel):
                try:
                    await loaded.instance.handle_event(event)
                except Exception as e:
                    logger.error(
                        "Skill %s failed to handle event: %s",
                        skill_id,
                        e,
                    )

    # -------------------------------------------------------------------------
    # Auto-activation
    # -------------------------------------------------------------------------

    async def check_auto_activation(
        self,
        context_tags: list[str],
    ) -> list[str]:
        """
        Check and activate skills based on context tags.

        Args:
            context_tags: Current context tags

        Returns:
            List of skill IDs that were activated
        """
        activated = []

        for skill_id, manifest in self._manifests.items():
            # Skip already loaded
            if skill_id in self._loaded:
                continue

            # Check auto-activation triggers
            for trigger in manifest.auto_activate_on:
                if trigger in context_tags:
                    if await self.load_skill(skill_id):
                        activated.append(skill_id)
                        logger.info(
                            "Auto-activated skill %s (trigger: %s)",
                            skill_id,
                            trigger,
                        )
                    break

        return activated

    # -------------------------------------------------------------------------
    # State Persistence
    # -------------------------------------------------------------------------

    def _persist_skill_state(
        self,
        skill_id: str,
        enabled: bool | None = None,
        error: str | None = None,
    ) -> None:
        """Persist skill state to database."""
        if not self._db_path:
            return

        now = datetime.utcnow().isoformat() + "Z"
        conn = self._get_connection()
        try:
            # Check if exists
            row = conn.execute(
                "SELECT * FROM skill_registry_state WHERE skill_id = ?",
                (skill_id,),
            ).fetchone()

            if row:
                if enabled is not None:
                    conn.execute(
                        """
                        UPDATE skill_registry_state
                        SET enabled = ?, last_loaded = ?
                        WHERE skill_id = ?
                        """,
                        (1 if enabled else 0, now, skill_id),
                    )
                if error:
                    conn.execute(
                        """
                        UPDATE skill_registry_state
                        SET last_error = ?
                        WHERE skill_id = ?
                        """,
                        (error, skill_id),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO skill_registry_state
                    (skill_id, enabled, last_loaded, last_error)
                    VALUES (?, ?, ?, ?)
                    """,
                    (skill_id, 1 if enabled else 0, now, error),
                )
            conn.commit()
        finally:
            conn.close()

    async def load_enabled_skills(self) -> int:
        """
        Load all skills that were previously enabled.

        Returns:
            Number of skills loaded
        """
        if not self._db_path:
            return 0

        from .blocking_executor import run_off_loop

        def _read_enabled_skill_ids() -> list[str]:
            conn = self._get_connection()
            try:
                rows = conn.execute(
                    "SELECT skill_id FROM skill_registry_state WHERE enabled = 1",
                ).fetchall()
            finally:
                conn.close()
            return [row["skill_id"] for row in rows]

        skill_ids = await run_off_loop(_read_enabled_skill_ids, executor=self._blocking_executor)

        loaded = 0
        for skill_id in skill_ids:
            if skill_id in self._manifests:
                if await self.load_skill(skill_id):
                    loaded += 1

        return loaded

    # -------------------------------------------------------------------------
    # Status / Info
    # -------------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Get registry status summary."""
        return {
            "skills_dir": str(self._skills_dir),
            "discovered": len(self._manifests),
            "loaded": len(self._loaded),
            "available": self.list_available(),
            "loaded_skills": [loaded.to_dict() for loaded in self._loaded.values()],
        }

    def get_skill_info(self, skill_id: str) -> dict[str, Any] | None:
        """Get detailed info about a skill."""
        manifest = self._manifests.get(skill_id)
        if not manifest:
            return None

        info = manifest.to_dict()
        info["is_loaded"] = skill_id in self._loaded

        if skill_id in self._loaded:
            loaded = self._loaded[skill_id]
            info["state"] = loaded.instance.state.value
            info["loaded_at"] = loaded.loaded_at.isoformat()
            info["status"] = loaded.instance.get_status()
            info["occupancy"] = loaded.occupancy.to_dict()

        info["permissions"] = self._permission_checker.get_grants(skill_id)

        return info

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Shutdown all loaded skills."""
        for skill_id in list(self._loaded.keys()):
            await self.unload_skill(skill_id)

        logger.info("Skill registry shutdown complete")


# Module-level singleton
_registry: SkillRegistry | None = None


def get_skill_registry(
    skills_dir: str | Path = "config/skills",
    db_path: str | None = None,
    **kwargs: Any,
) -> SkillRegistry:
    """Get or create the global skill registry."""
    global _registry
    if _registry is None:
        _registry = SkillRegistry(
            skills_dir=skills_dir,
            db_path=db_path,
            **kwargs,
        )
    return _registry


def reset_skill_registry() -> None:
    """Reset the global skill registry (for testing)."""
    global _registry
    _registry = None
