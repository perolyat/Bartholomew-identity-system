"""
Skill Registry
==============

Central registry for managing skill lifecycle: discovery, loading, unloading,
and event routing.

Part of Stage 4: Skill Registry + Starter Skills.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import policy_engine
from .db_executor import DedicatedDbExecutor
from .memory.privacy_guard import get_consent_handler
from .redaction_engine import redact_pii
from .reflection import ActionReflection, record_action_reflection
from .runtime_contract import CandidateAction, Interpretation, Observation
from .skill_base import SkillBase, SkillContext, SkillResult, SkillState
from .skill_manifest import SkillManifest, discover_manifests
from .skill_permissions import PERMISSION_CATEGORIES, PermissionChecker, get_permission_checker

if TYPE_CHECKING:
    from identity_interpreter.identity_context import IdentityContext

    from .experience_kernel import ExperienceKernel
    from .global_workspace import GlobalWorkspace, WorkspaceEvent
    from .memory_store import MemoryStore
    from .working_memory import WorkingMemoryManager

logger = logging.getLogger(__name__)


# -- Synchronous DB bodies, run on SkillRegistry's dedicated DB executor
# thread (Phase B, stage B2) rather than directly on the event loop from
# _audit_execution()/_persist_skill_state()/load_enabled_skills(). Plain
# module-level functions so they have no reference to the SkillRegistry
# instance -- only the db_path and values they need.


def _audit_execution_sync(
    db_path: str,
    skill_id: str,
    action: str,
    params_json: str,
    status: str,
    message: str | None,
    error: str | None,
    timestamp: str,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO skill_action_audit
            (skill_id, action, params_json, status, result_message, result_error, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (skill_id, action, params_json, status, message, error, timestamp),
        )
        conn.commit()
    finally:
        conn.close()


def _persist_skill_state_sync(
    db_path: str,
    skill_id: str,
    enabled: bool | None,
    error: str | None,
    now: str,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
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


def _load_enabled_skill_ids_sync(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT skill_id FROM skill_registry_state WHERE enabled = 1",
        ).fetchall()
        return [row["skill_id"] for row in rows]
    finally:
        conn.close()


@dataclass
class LoadedSkill:
    """Container for a loaded skill instance."""

    manifest: SkillManifest
    instance: SkillBase
    loaded_at: datetime
    subscription_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.manifest.skill_id,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "state": self.instance.state.value,
            "loaded_at": self.loaded_at.isoformat(),
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
        """
        self._skills_dir = Path(skills_dir)
        self._db_path = db_path
        self._workspace = workspace
        self._kernel = kernel
        self._working_memory = working_memory
        self._memory_store = memory_store
        self._identity_context = identity_context

        # Permission checker
        self._permission_checker = permission_checker or get_permission_checker(db_path=db_path)

        # Discovered manifests (skill_id -> manifest)
        self._manifests: dict[str, SkillManifest] = {}

        # Loaded skill instances (skill_id -> LoadedSkill)
        self._loaded: dict[str, LoadedSkill] = {}

        # Event subscriptions for routing
        self._channel_subscriptions: dict[str, str] = {}

        # Phase B, stage B2: _audit_execution(), _persist_skill_state(), and
        # load_enabled_skills() used to call sqlite3.connect() synchronously,
        # directly on the event loop from async def methods (see
        # docs/B0_BASELINE_REPORT.md section 3). One dedicated worker thread
        # for all three, matching SchedulerStore's proven pattern.
        self._db_executor = DedicatedDbExecutor(f"skill-registry:{db_path or 'none'}")

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
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript(self.SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        if not self._db_path:
            raise RuntimeError("No database configured")
        conn = sqlite3.connect(self._db_path)
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

            # Persist state
            await self._persist_skill_state(skill_id, enabled=True)

            logger.info(
                "Loaded skill: %s v%s",
                manifest.skill_id,
                manifest.version,
            )
            return True

        except Exception as e:
            logger.exception("Failed to load skill %s: %s", skill_id, e)
            await self._persist_skill_state(skill_id, error=str(e))
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
        )

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

        try:
            # Unsubscribe from events
            if self._workspace:
                for sub_id in loaded.subscription_ids:
                    self._workspace.unsubscribe(sub_id)

            # Shutdown skill
            loaded.instance._set_state(SkillState.UNLOADING)
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

        if not loaded.instance.is_ready:
            return await self._finish(
                observation,
                candidate_action,
                action,
                params,
                SkillResult.fail(
                    f"Skill not ready: {skill_id} (state={loaded.instance.state.value})",
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
        if self._is_blocked_by_brake():
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
        # Governance check above has allowed the CandidateAction.
        try:
            loaded.instance._set_state(SkillState.RUNNING)
            result = await loaded.instance.execute(action, params or {})
            loaded.instance._set_state(SkillState.READY)
            return await self._finish(observation, candidate_action, action, params, result)
        except Exception as e:
            logger.exception(
                "Skill %s action %s failed: %s",
                skill_id,
                action,
                e,
            )
            loaded.instance._set_error(str(e))
            return await self._finish(
                observation,
                candidate_action,
                action,
                params,
                SkillResult.fail(str(e)),
            )

    def _is_blocked_by_brake(self) -> bool:
        """
        Check the global ParkingBrake's "skills" scope.

        Fails closed (treats as blocked) if the check itself errors --
        never silently allow a skill action when the safety gate can't be
        read, matching this codebase's fail-closed governance principle.
        """
        if not self._db_path:
            return False

        try:
            from bartholomew.orchestrator.safety.parking_brake import (
                BrakeStorage,
                ParkingBrake,
            )

            storage = BrakeStorage(self._db_path)
            brake = ParkingBrake(storage)
            return brake.is_blocked("skills")
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
            result = self._permission_checker.check(manifest.skill_id, permission)
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

            self._permission_checker.grant_session(manifest.skill_id, permission)

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
        await self._audit_execution(skill_id, action, params, result)
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

    async def _audit_execution(
        self,
        skill_id: str,
        action: str,
        params: dict[str, Any] | None,
        result: SkillResult,
    ) -> None:
        """
        Persist an audit record for every execute_action() attempt --
        success, failure, permission denial, or parking-brake block alike.
        """
        if not self._db_path:
            return

        # Redact PII from top-level string param values before persisting --
        # this table has no consent-gate/redaction pipeline of its own,
        # same class of gap found and fixed for the Experience
        # Kernel/Narrator elsewhere in this codebase.
        sanitized_params = {
            k: (redact_pii(v) if isinstance(v, str) else v) for k, v in (params or {}).items()
        }

        now = datetime.utcnow().isoformat() + "Z"
        try:
            await self._db_executor.call(
                _audit_execution_sync,
                self._db_path,
                skill_id,
                action,
                json.dumps(sanitized_params),
                result.status.value,
                result.message,
                result.error,
                now,
            )
        except Exception:
            logger.exception("Failed to write skill action audit record")

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

    async def _persist_skill_state(
        self,
        skill_id: str,
        enabled: bool | None = None,
        error: str | None = None,
    ) -> None:
        """Persist skill state to database."""
        if not self._db_path:
            return

        now = datetime.utcnow().isoformat() + "Z"
        await self._db_executor.call(
            _persist_skill_state_sync,
            self._db_path,
            skill_id,
            enabled,
            error,
            now,
        )

    async def load_enabled_skills(self) -> int:
        """
        Load all skills that were previously enabled.

        Returns:
            Number of skills loaded
        """
        if not self._db_path:
            return 0

        skill_ids = await self._db_executor.call(_load_enabled_skill_ids_sync, self._db_path)

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

        info["permissions"] = self._permission_checker.get_grants(skill_id)

        return info

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Shutdown all loaded skills, then drain this instance's dedicated
        DB executor (Phase B, stage B2) so its worker thread's termination
        is confirmed, not merely submitted-and-assumed, before this method
        returns."""
        for skill_id in list(self._loaded.keys()):
            await self.unload_skill(skill_id)

        drained = await self._db_executor.close()
        if not drained:
            logger.warning(
                "SkillRegistry._db_executor did not drain cleanly on shutdown()",
            )

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
