"""
WP-A2 -- Audit-write integrity and truthful governed-action failure.

Closes register **OP-W004** (S0, Band A) and the audit-failure-semantics
half of safety gate **S2**.

Test #1 emitted ``Failed to log audit: database is locked`` twice, the exact
affected events were never established, and the governed actions involved
reported unqualified success. Two defects produced that:

1. Several production modules opened the shared SQLite database with a bare
   ``sqlite3.connect()`` rather than through the kernel's single connection
   authority (``bartholomew.kernel.db_ctx``). An unconfigured connection
   never asserts WAL, so whichever component created the database file first
   decided its journal mode -- and ``PermissionChecker`` / ``SkillRegistry``
   are constructed during ``KernelDaemon.__init__()``, *before*
   ``MemoryStore.init()`` runs.
2. Every audit write swallowed its own failure (``except Exception:
   logger.warning(...)``), so a lost audit row was invisible to the action
   that lost it.

**The approved S2 semantics this file pins** (see the WP-A2 approval):

* a failed **pre-action** gate (parking brake, consent, authorisation,
  policy) is still fail-closed -- the action must not execute;
* an action that passed those gates and genuinely executed must **not** be
  reported as failed merely because a required audit write was lost;
* it must **not** be presented as full success either -- both facts are
  reported: the action succeeded, and required audit persistence failed;
* an already-successful action is **not** retried because its audit failed.

Failure injection is deliberate and real. Three genuine mechanisms are used,
none of them a monkeypatched exception: holding SQLite's single writer lock
with a second connection; a SQLite ABORT trigger on one audit table; and
pointing a store at a path that cannot be opened. Which one each test uses
matters -- see `_AuditWritesFail` for why a held lock cannot exercise the
degraded-success path.
"""

from __future__ import annotations

import ast
import asyncio
import os
import pathlib
import shutil
import sqlite3
import tempfile

import pytest

from bartholomew.kernel.db_ctx import connect, set_wal_pragmas
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.skill_base import SkillResult, SkillResultStatus
from bartholomew.kernel.skill_permissions import (
    PermissionChecker,
    collect_permission_audit_failures,
    drain_permission_audit_failures,
    reset_permission_checker,
)
from bartholomew.kernel.skill_registry import SkillRegistry, reset_skill_registry


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except (PermissionError, FileNotFoundError):
            pass


@pytest.fixture(autouse=True)
def reset_singletons():
    reset_skill_registry()
    reset_permission_checker()
    set_consent_handler(None)
    yield
    reset_skill_registry()
    reset_permission_checker()
    set_consent_handler(None)


def _query_all(db_path: str, sql: str, params: tuple = ()) -> list[tuple]:
    conn = connect(db_path)
    try:
        set_wal_pragmas(conn)
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class _HeldWriteLock:
    """Hold SQLite's single writer lock for the duration of the block.

    Real contention, not a mock: SQLite permits exactly one writer, so every
    other write against this database gets SQLITE_BUSY and -- once the busy
    timeout expires -- raises ``database is locked``. This is the same
    condition Test #1 hit; here it is deterministic.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path

    def __enter__(self):
        self._conn = sqlite3.connect(self._db_path, timeout=30)
        self._conn.execute("BEGIN IMMEDIATE")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS wp_a2_lock_probe (id INTEGER PRIMARY KEY)",
        )
        self._conn.execute("INSERT INTO wp_a2_lock_probe (id) VALUES (1)")
        return self

    def __exit__(self, *exc):
        self._conn.rollback()
        self._conn.close()
        return False


class _AuditWritesFail:
    """Make writes to one audit table fail, and nothing else.

    A held write lock (above) is too blunt for the S2 semantics: it also
    blocks the Parking Brake's own read, which then correctly fails closed,
    so the action never executes and there is no successful action left to
    be degraded. That is real and desirable behaviour -- it is pinned
    separately in `TestPreActionGatesStayFailClosed` -- but it cannot
    exercise "the action ran and its audit was lost".

    This injects the failure at exactly the row that must not be lost, using
    a real SQLite ABORT trigger. Nothing is monkeypatched: the production
    code path executes a genuine INSERT and gets a genuine
    `sqlite3.IntegrityError` back.
    """

    def __init__(self, db_path: str, table: str):
        self._db_path = db_path
        self._table = table

    def _exec(self, sql: str) -> None:
        conn = connect(self._db_path)
        try:
            set_wal_pragmas(conn)
            conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    def __enter__(self):
        self._exec(
            f"CREATE TRIGGER wp_a2_block_{self._table} "
            f"BEFORE INSERT ON {self._table} "
            f"BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END",
        )
        return self

    def __exit__(self, *exc):
        self._exec(f"DROP TRIGGER IF EXISTS wp_a2_block_{self._table}")
        return False


# ---------------------------------------------------------------------------
# 1. The connection authority (root cause part 1)
# ---------------------------------------------------------------------------


class TestConnectionAuthority:
    """Every covered module opens the shared DB through `db_ctx`."""

    COVERED_MODULES = [
        "bartholomew/kernel/skill_permissions.py",
        "bartholomew/kernel/skill_registry.py",
        "bartholomew/kernel/consent_gate.py",
        "bartholomew/skills/notify.py",
        "bartholomew/skills/tasks.py",
        "bartholomew/skills/calendar_draft.py",
        "bartholomew/orchestrator/safety/parking_brake.py",
    ]

    def test_no_covered_module_opens_the_shared_db_with_a_bare_connect(self):
        """Structural guard against reintroducing the OP-W004 root cause.

        `notify.py` already carried this fix and it was not propagated,
        which is precisely how OP-W004 survived. A structural assertion is
        what stops that happening a second time.

        Parsed with `ast` rather than grepped, so the prose in these
        modules' own docstrings -- which necessarily quotes the pattern it
        is warning about -- is not mistaken for a call site.
        """
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        offenders: list[str] = []
        for rel in self.COVERED_MODULES:
            tree = ast.parse((repo_root / rel).read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "connect"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "sqlite3"
                ):
                    offenders.append(f"{rel}:{node.lineno}")
        assert offenders == [], (
            "These call sites open the shared database without the kernel's "
            "connection authority (bartholomew.kernel.db_ctx). Use "
            "connect() + set_wal_pragmas():\n  " + "\n  ".join(offenders)
        )

    def test_permission_checker_creates_the_database_in_wal_mode(self, temp_db):
        """The pre-WP-A2 bare connect left a fresh DB in rollback-journal mode.

        `PermissionChecker` is constructed in `KernelDaemon.__init__()`,
        before `MemoryStore.init()`, so on a fresh install it decided the
        journal mode for the whole shared database. In rollback-journal mode
        readers and writers block each other, which is how an ordinary
        concurrent read turned into `database is locked`.
        """
        os.unlink(temp_db)
        PermissionChecker(db_path=temp_db)
        assert _query_all(temp_db, "PRAGMA journal_mode")[0][0].lower() == "wal"

    def test_skill_registry_creates_the_database_in_wal_mode(self, temp_db):
        os.unlink(temp_db)
        SkillRegistry(db_path=temp_db)
        assert _query_all(temp_db, "PRAGMA journal_mode")[0][0].lower() == "wal"


# ---------------------------------------------------------------------------
# 2. The result contract
# ---------------------------------------------------------------------------


class TestDegradedResultContract:
    def test_a_healthy_result_is_fully_successful(self):
        result = SkillResult.ok(data={"ok": True})
        assert result.success is True
        assert result.audit_degraded is False
        assert result.audit_error is None
        assert result.fully_successful is True

    def test_marking_degraded_never_changes_the_action_verdict(self):
        """S2's core distinction: the action's own outcome is untouched."""
        result = SkillResult.ok(data={"ok": True})
        result.mark_audit_degraded("skill_action_audit write failed: database is locked")

        assert result.status is SkillResultStatus.SUCCESS
        assert result.success is True, "a performed action must not be reported as failed"
        assert result.audit_degraded is True
        assert result.fully_successful is False, "but it is not FULL success either"

    def test_multiple_lost_writes_are_all_retained(self):
        """The record must say how much was lost, not merely that something was."""
        result = SkillResult.ok()
        result.mark_audit_degraded("first")
        result.mark_audit_degraded("second")
        assert "first" in result.audit_error
        assert "second" in result.audit_error

    def test_to_dict_carries_the_degraded_state(self):
        result = SkillResult.ok()
        result.mark_audit_degraded("boom")
        payload = result.to_dict()
        assert payload["status"] == "success"
        assert payload["audit_degraded"] is True
        assert payload["audit_error"] == "boom"


# ---------------------------------------------------------------------------
# 3. Deliberate failure injection through the real governed path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAuditFailureIsVisibleAndSafe:
    async def test_healthy_action_is_audited_and_not_degraded(self, temp_db):
        registry = SkillRegistry(db_path=temp_db)
        assert await registry.load_skill("tasks") is True

        result = await registry.execute_action("tasks", "create", {"title": "Buy milk"})

        assert result.success is True
        assert result.audit_degraded is False
        assert result.fully_successful is True
        assert ("tasks", "create", "success") in _query_all(
            temp_db,
            "SELECT skill_id, action, status FROM skill_action_audit",
        )

    async def test_lost_audit_write_yields_a_truthful_degraded_result(self, temp_db):
        """The S2 gate itself: the action ran, its audit row did not persist."""
        registry = SkillRegistry(db_path=temp_db)
        assert await registry.load_skill("tasks") is True

        with _AuditWritesFail(temp_db, "skill_action_audit"):
            result = await registry.execute_action(
                "tasks",
                "create",
                {"title": "Audit will be lost"},
            )

        # Fact 1: the action itself genuinely succeeded and says so.
        assert result.success is True, "a performed action must not be reported as failed"
        assert _query_all(
            temp_db,
            "SELECT title FROM skill_tasks WHERE title = ?",
            ("Audit will be lost",),
        ) == [("Audit will be lost",)]

        # Fact 2: required audit persistence failed, and says so.
        assert result.audit_degraded is True, "the lost audit write must be reported"
        assert "skill_action_audit" in (result.audit_error or "")

        # Together: not full success.
        assert result.fully_successful is False

        # And the row really is absent -- the degradation is not cosmetic.
        assert _query_all(temp_db, "SELECT COUNT(*) FROM skill_action_audit")[0][0] == 0

    async def test_a_lost_audit_write_never_raises(self, temp_db):
        """A missing audit row must not turn into an exception at the seam."""
        registry = SkillRegistry(db_path=temp_db)
        assert await registry.load_skill("tasks") is True

        with _AuditWritesFail(temp_db, "skill_action_audit"):
            result = await registry.execute_action("tasks", "list", {})

        assert isinstance(result, SkillResult)
        assert result.audit_degraded is True

    async def test_the_action_is_not_retried_because_its_audit_failed(self, temp_db):
        """Approved semantics: never re-run an already-successful action.

        `tasks.create` is observable -- a retry would leave two rows. The
        audit write for this action is lost, and exactly one task exists
        afterwards.
        """
        registry = SkillRegistry(db_path=temp_db)
        assert await registry.load_skill("tasks") is True

        with _AuditWritesFail(temp_db, "skill_action_audit"):
            result = await registry.execute_action(
                "tasks",
                "create",
                {"title": "Exactly once"},
            )
        assert result.audit_degraded is True

        rows = _query_all(
            temp_db,
            "SELECT title FROM skill_tasks WHERE title = ?",
            ("Exactly once",),
        )
        assert len(rows) == 1, "an audit failure must never cause the action to repeat"

    async def test_permission_audit_failure_reaches_the_action_result(self, temp_db):
        """`permission_audit` rows are written from inside the skill's own
        permission checks, which have no return path to the SkillResult.
        WP-A2's per-action collector is what closes that gap."""
        registry = SkillRegistry(db_path=temp_db)
        assert await registry.load_skill("tasks") is True

        with _AuditWritesFail(temp_db, "permission_audit"):
            result = await registry.execute_action("tasks", "create", {"title": "perm"})

        assert result.success is True, "a permission-audit loss must not fail the action"
        assert result.audit_degraded is True
        assert "permission_audit" in (result.audit_error or ""), result.audit_error
        assert result.fully_successful is False

    async def test_both_audit_losses_are_reported_together(self, temp_db):
        """One action, one verdict -- naming every audit row it lost."""
        registry = SkillRegistry(db_path=temp_db)
        assert await registry.load_skill("tasks") is True

        with _AuditWritesFail(temp_db, "skill_action_audit"):
            with _AuditWritesFail(temp_db, "permission_audit"):
                result = await registry.execute_action("tasks", "create", {"title": "both"})

        assert result.audit_degraded is True
        assert "skill_action_audit" in result.audit_error
        assert "permission_audit" in result.audit_error


# ---------------------------------------------------------------------------
# 4. Fail-closed governance is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPreActionGatesStayFailClosed:
    async def test_parking_brake_still_blocks_and_is_not_a_degraded_success(self, temp_db):
        """The degraded path must never become a way past a pre-action gate."""
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        registry = SkillRegistry(db_path=temp_db)
        assert await registry.load_skill("tasks") is True

        GovernanceStore(temp_db).engage("skills")

        result = await registry.execute_action("tasks", "create", {"title": "blocked"})

        assert result.success is False
        assert "parking brake" in (result.error or "").lower()
        assert result.fully_successful is False
        # The action did not run, so there is nothing to call a success.
        assert _query_all(temp_db, "SELECT title FROM skill_tasks") == []

    async def test_an_unreadable_brake_blocks_the_action_under_contention(self, temp_db):
        """Found while building WP-A2's injection harness, and worth pinning.

        Under a genuinely held write lock the Parking Brake's own state read
        fails, and `_is_blocked_by_brake()` fails **closed** -- the action is
        refused rather than allowed through. That is the correct direction
        (safety gate S5 requires exactly this), and it must not be
        "fixed" into a degraded success by any future reading of S2: the
        degraded path is only for actions that genuinely executed.
        """
        registry = SkillRegistry(db_path=temp_db)
        assert await registry.load_skill("tasks") is True

        with _HeldWriteLock(temp_db):
            result = await registry.execute_action(
                "tasks",
                "create",
                {"title": "must not run"},
            )

        assert result.success is False, "an unreadable brake must fail closed"
        assert result.fully_successful is False
        assert (
            _query_all(
                temp_db,
                "SELECT title FROM skill_tasks WHERE title = ?",
                ("must not run",),
            )
            == []
        ), "a fail-closed action must leave no real-world effect"

    async def test_an_unreadable_audit_store_does_not_grant_anything(self, temp_db):
        """Authorisation is decided by policy, never by audit availability --
        in either direction. A failed audit write must not grant, and must
        not revoke, a permission."""
        checker = PermissionChecker(db_path=temp_db, auto_permissions={"tasks": ["task.write"]})

        with _HeldWriteLock(temp_db):
            granted = checker.check("tasks", "task.write")
            denied = checker.check("tasks", "not.granted")

        assert granted.granted is True, "an audit failure must not revoke a real grant"
        assert denied.granted is False, "an audit failure must not manufacture a grant"
        assert granted.audit_error and "permission_audit" in granted.audit_error
        assert denied.audit_error and "permission_audit" in denied.audit_error


# ---------------------------------------------------------------------------
# 5. The collector itself
# ---------------------------------------------------------------------------


class TestPermissionAuditCollector:
    def test_failures_outside_an_action_are_not_collected(self, temp_db):
        """No active action means no result to degrade. The failure is still
        logged at ERROR; it simply has nowhere to be attributed."""
        checker = PermissionChecker(db_path=temp_db, auto_permissions={"tasks": ["task.write"]})
        with _HeldWriteLock(temp_db):
            checker.check("tasks", "task.write")
        assert drain_permission_audit_failures() == []

    def test_draining_is_scoped_and_clears(self, temp_db):
        checker = PermissionChecker(db_path=temp_db, auto_permissions={"tasks": ["task.write"]})
        with collect_permission_audit_failures():
            with _HeldWriteLock(temp_db):
                checker.check("tasks", "task.write")
            first = drain_permission_audit_failures()
            second = drain_permission_audit_failures()

        assert len(first) == 1
        assert "permission_audit" in first[0]
        assert second == [], "draining twice must not double-report one loss"

    def test_a_missing_database_file_is_reported_not_swallowed(self):
        """The other real cause seen in reproduction: the store points at a
        path that cannot be opened at all (`unable to open database file`),
        rather than at a contended one."""
        stale_root = pathlib.Path(tempfile.mkdtemp())
        stale_db = str(stale_root / "data" / "gone.db")
        (stale_root / "data").mkdir()
        checker = PermissionChecker(db_path=stale_db, auto_permissions={"tasks": ["task.write"]})
        shutil.rmtree(stale_root)

        with collect_permission_audit_failures():
            result = checker.check("tasks", "task.write")
            collected = drain_permission_audit_failures()

        assert result.granted is True
        assert result.audit_error is not None
        assert len(collected) == 1


@pytest.mark.asyncio
class TestConcurrentActionsDoNotContaminateEachOther:
    async def test_a_degraded_action_does_not_leak_into_the_next_one(self, temp_db):
        """The collector is scoped per action, so a degraded action must not
        make the next healthy one look degraded. Without the scope reset,
        collected failures would accumulate across actions forever and every
        subsequent action would inherit a verdict it did not earn."""
        registry = SkillRegistry(db_path=temp_db)
        assert await registry.load_skill("tasks") is True

        warmup = await registry.execute_action("tasks", "list", {})
        assert warmup.audit_degraded is False

        with _AuditWritesFail(temp_db, "permission_audit"):
            degraded = await registry.execute_action("tasks", "list", {})

        healthy = await registry.execute_action("tasks", "list", {})

        assert degraded.audit_degraded is True
        assert (
            healthy.audit_degraded is False
        ), "a later action must start with a clean audit verdict"

    async def test_concurrent_actions_get_independent_verdicts(self, temp_db):
        """Two actions in flight at once must not contaminate each other.

        The collector is a ContextVar, so each asyncio task gets its own.
        Run a degraded action and a healthy one concurrently and assert each
        reports only its own outcome.
        """
        registry = SkillRegistry(db_path=temp_db)
        assert await registry.load_skill("tasks") is True
        assert (await registry.execute_action("tasks", "list", {})).audit_degraded is False

        async def healthy_after(delay: float):
            await asyncio.sleep(delay)
            return await registry.execute_action("tasks", "list", {})

        with _AuditWritesFail(temp_db, "permission_audit"):
            degraded, _ = await asyncio.gather(
                registry.execute_action("tasks", "list", {}),
                healthy_after(0),
            )
        healthy = await registry.execute_action("tasks", "list", {})

        assert degraded.audit_degraded is True
        assert healthy.audit_degraded is False
