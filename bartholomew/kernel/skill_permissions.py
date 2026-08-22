"""
Skill Permissions
=================

Permission model for controlling skill access to system resources.
Part of Stage 4: Skill Registry + Starter Skills.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .db_ctx import connect, set_wal_pragmas

logger = logging.getLogger(__name__)


class PermissionLevel(Enum):
    """Permission levels for skills."""

    NEVER = "never"  # Skill cannot use this permission
    ASK = "ask"  # Prompt user each time
    AUTO = "auto"  # Always allowed (within scope)


class PermissionStatus(Enum):
    """Result of a permission check."""

    GRANTED = "granted"
    DENIED = "denied"
    PENDING = "pending"  # Awaiting user approval


# Standard permission categories
PERMISSION_CATEGORIES = {
    # Memory access
    "memory.read": "Read from memory store",
    "memory.write": "Write to memory store",
    "memory.delete": "Delete from memory store",
    # Nudge system
    "nudge.create": "Create nudges/notifications",
    "nudge.read": "Read nudge status",
    "nudge.dismiss": "Dismiss nudges",
    # Filesystem (sandboxed)
    "filesystem.read": "Read files (within sandbox)",
    "filesystem.write": "Write files (within sandbox)",
    # Network (strictly controlled)
    "network.fetch": "Make HTTP requests (within allowlist)",
    # System
    "system.status": "Read system status",
    "system.config": "Read configuration",
}


@dataclass
class PermissionRequest:
    """Record of a permission request."""

    skill_id: str
    permission: str
    status: PermissionStatus
    timestamp: str
    context: dict[str, Any] = field(default_factory=dict)
    expires_at: str | None = None
    granted_by: str | None = None  # "user", "auto", "config"

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "permission": self.permission,
            "status": self.status.value,
            "timestamp": self.timestamp,
            "context": self.context,
            "expires_at": self.expires_at,
            "granted_by": self.granted_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PermissionRequest:
        return cls(
            skill_id=data["skill_id"],
            permission=data["permission"],
            status=PermissionStatus(data["status"]),
            timestamp=data["timestamp"],
            context=data.get("context", {}),
            expires_at=data.get("expires_at"),
            granted_by=data.get("granted_by"),
        )


# ---------------------------------------------------------------------------
# WP-A2: per-action collection of failed permission-audit writes
# ---------------------------------------------------------------------------
#
# Safety gate S2 requires that a governed action cannot present full success
# when a required audit write failed. The `skill_action_audit` row is written
# by `SkillRegistry._finish()`, which can report its own failure directly. The
# `permission_audit` rows are different: they are written from inside
# `PermissionChecker.check()`, which is reached through the skill's own
# `SkillContext.has_permission` closure while the action is executing -- there
# is no return path from there to the action's SkillResult.
#
# A ContextVar bridges exactly that gap and nothing more. It is per-asyncio-task
# (so two concurrent actions cannot contaminate each other's verdict), it holds
# only in-memory strings for the lifetime of one action, and it is NOT a second
# audit store: nothing is persisted here and nothing here is a record of what
# happened -- it carries the fact that a record was *lost* back to the one place
# that can report it truthfully.
#
# Outside a governed action no collector is installed. In that case a failed
# permission-audit write is logged at ERROR and not collected, because there is
# no action result for it to degrade.

_permission_audit_failures: ContextVar[list[str] | None] = ContextVar(
    "bartholomew_permission_audit_failures",
    default=None,
)


@contextmanager
def collect_permission_audit_failures() -> Iterator[None]:
    """Install a fresh per-action collector for failed permission-audit writes."""
    token = _permission_audit_failures.set([])
    try:
        yield
    finally:
        _permission_audit_failures.reset(token)


def record_permission_audit_failure(message: str) -> None:
    """Record a failed required permission-audit write against the current action."""
    sink = _permission_audit_failures.get()
    if sink is not None:
        sink.append(message)


def drain_permission_audit_failures() -> list[str]:
    """Return and clear the permission-audit failures collected for this action."""
    sink = _permission_audit_failures.get()
    if not sink:
        return []
    drained = list(sink)
    sink.clear()
    return drained


@dataclass
class PermissionResult:
    """Result of a permission check."""

    granted: bool
    status: PermissionStatus
    permission: str
    reason: str = ""
    expires_at: str | None = None

    #: WP-A2 / S2: set when this check's required `permission_audit` write
    #: did not persist. The check's own verdict (`granted`) is unaffected --
    #: authorisation is decided by policy, never by whether the audit row
    #: was written -- but the loss is no longer invisible to the caller.
    audit_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "granted": self.granted,
            "status": self.status.value,
            "permission": self.permission,
            "reason": self.reason,
            "expires_at": self.expires_at,
            "audit_error": self.audit_error,
        }


class PermissionChecker:
    """
    Checks and enforces skill permissions.

    Manages:
    - Permission grants (session and persistent)
    - Permission requests and approvals
    - Audit logging of permission checks
    """

    # SQL schema for permission storage
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS skill_permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_id TEXT NOT NULL,
        permission TEXT NOT NULL,
        status TEXT NOT NULL,
        granted_by TEXT,
        granted_at TEXT NOT NULL,
        expires_at TEXT,
        context_json TEXT,
        UNIQUE(skill_id, permission)
    );

    CREATE TABLE IF NOT EXISTS permission_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_id TEXT NOT NULL,
        permission TEXT NOT NULL,
        action TEXT NOT NULL,
        result TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        context_json TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_perm_skill
        ON skill_permissions(skill_id);
    CREATE INDEX IF NOT EXISTS idx_audit_skill
        ON permission_audit(skill_id);
    CREATE INDEX IF NOT EXISTS idx_audit_time
        ON permission_audit(timestamp);
    """

    def __init__(
        self,
        db_path: str | None = None,
        auto_permissions: dict[str, list[str]] | None = None,
    ) -> None:
        """
        Initialize permission checker.

        Args:
            db_path: Path to SQLite database for persistent grants
            auto_permissions: Dict mapping skill_id -> list of auto-granted
                permissions
        """
        self._db_path = db_path
        self._auto_permissions = auto_permissions or {}

        # Session grants (cleared on restart)
        self._session_grants: dict[str, set[str]] = {}

        # Initialize database if path provided
        if self._db_path:
            self._init_database()

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
        * the resulting failures reached this module's audit write, which
          swallowed them (see ``_log_audit``).

        ``set_wal_pragmas()`` brings this connection in line with the rest
        of the kernel -- WAL, ``synchronous=NORMAL``, ``foreign_keys=ON``,
        ``busy_timeout=5000``. It does not, and cannot, remove write
        contention itself; making the resulting failure *truthful* is
        ``_log_audit``'s job, not this method's.
        """
        if not self._db_path:
            raise RuntimeError("No database configured")
        conn = connect(self._db_path)
        set_wal_pragmas(conn)
        conn.row_factory = sqlite3.Row
        return conn

    def check(
        self,
        skill_id: str,
        permission: str,
        context: dict[str, Any] | None = None,
    ) -> PermissionResult:
        """
        Check if a skill has a specific permission.

        Args:
            skill_id: Skill requesting permission
            permission: Permission being requested
            context: Optional context for the check

        Returns:
            PermissionResult with grant status
        """
        now = datetime.utcnow().isoformat() + "Z"

        # 1. Check auto-granted permissions
        auto_perms = self._auto_permissions.get(skill_id, [])
        if permission in auto_perms:
            audit_error = self._log_audit(skill_id, permission, "check", "granted_auto", now)
            return PermissionResult(
                granted=True,
                status=PermissionStatus.GRANTED,
                permission=permission,
                reason="Auto-granted by manifest",
                audit_error=audit_error,
            )

        # 2. Check session grants
        session_perms = self._session_grants.get(skill_id, set())
        if permission in session_perms:
            audit_error = self._log_audit(skill_id, permission, "check", "granted_session", now)
            return PermissionResult(
                granted=True,
                status=PermissionStatus.GRANTED,
                permission=permission,
                reason="Granted for session",
                audit_error=audit_error,
            )

        # 3. Check persistent grants (database)
        if self._db_path:
            db_grant = self._check_db_grant(skill_id, permission)
            if db_grant:
                # Check expiration
                if db_grant.expires_at:
                    if db_grant.expires_at > now:
                        audit_error = self._log_audit(
                            skill_id,
                            permission,
                            "check",
                            "granted_db",
                            now,
                        )
                        return PermissionResult(
                            granted=True,
                            status=PermissionStatus.GRANTED,
                            permission=permission,
                            reason="Persistent grant",
                            expires_at=db_grant.expires_at,
                            audit_error=audit_error,
                        )
                else:
                    audit_error = self._log_audit(
                        skill_id,
                        permission,
                        "check",
                        "granted_db",
                        now,
                    )
                    return PermissionResult(
                        granted=True,
                        status=PermissionStatus.GRANTED,
                        permission=permission,
                        reason="Persistent grant",
                        audit_error=audit_error,
                    )

        # 4. Permission not granted
        audit_error = self._log_audit(skill_id, permission, "check", "denied", now)
        return PermissionResult(
            granted=False,
            status=PermissionStatus.DENIED,
            permission=permission,
            reason="Not granted",
            audit_error=audit_error,
        )

    def _check_db_grant(self, skill_id: str, permission: str) -> PermissionRequest | None:
        """Check for persistent grant in database."""
        if not self._db_path:
            return None

        conn = self._get_connection()
        try:
            row = conn.execute(
                """
                SELECT * FROM skill_permissions
                WHERE skill_id = ? AND permission = ?
                    AND status = 'granted'
                """,
                (skill_id, permission),
            ).fetchone()

            if row:
                return PermissionRequest(
                    skill_id=row["skill_id"],
                    permission=row["permission"],
                    status=PermissionStatus.GRANTED,
                    timestamp=row["granted_at"],
                    expires_at=row["expires_at"],
                    granted_by=row["granted_by"],
                )
            return None
        finally:
            conn.close()

    def grant_session(
        self,
        skill_id: str,
        permission: str,
    ) -> None:
        """
        Grant a permission for the current session.

        Args:
            skill_id: Skill to grant permission to
            permission: Permission to grant
        """
        if skill_id not in self._session_grants:
            self._session_grants[skill_id] = set()
        self._session_grants[skill_id].add(permission)

        now = datetime.utcnow().isoformat() + "Z"
        self._log_audit(skill_id, permission, "grant_session", "success", now)
        logger.info("Session permission granted: %s -> %s", skill_id, permission)

    def grant_persistent(
        self,
        skill_id: str,
        permission: str,
        granted_by: str = "user",
        expires_at: str | None = None,
    ) -> None:
        """
        Grant a persistent permission (stored in database).

        Args:
            skill_id: Skill to grant permission to
            permission: Permission to grant
            granted_by: Who granted the permission
            expires_at: Optional expiration timestamp
        """
        if not self._db_path:
            # Fall back to session grant
            self.grant_session(skill_id, permission)
            return

        now = datetime.utcnow().isoformat() + "Z"
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO skill_permissions
                (skill_id, permission, status, granted_by, granted_at,
                    expires_at)
                VALUES (?, ?, 'granted', ?, ?, ?)
                """,
                (skill_id, permission, granted_by, now, expires_at),
            )
            conn.commit()
            self._log_audit(skill_id, permission, "grant_persistent", "success", now)
            logger.info("Persistent permission granted: %s -> %s", skill_id, permission)
        finally:
            conn.close()

    def revoke(
        self,
        skill_id: str,
        permission: str,
    ) -> None:
        """
        Revoke a permission (both session and persistent).

        Args:
            skill_id: Skill to revoke permission from
            permission: Permission to revoke
        """
        now = datetime.utcnow().isoformat() + "Z"

        # Revoke session grant
        if skill_id in self._session_grants:
            self._session_grants[skill_id].discard(permission)

        # Revoke persistent grant
        if self._db_path:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    DELETE FROM skill_permissions
                    WHERE skill_id = ? AND permission = ?
                    """,
                    (skill_id, permission),
                )
                conn.commit()
            finally:
                conn.close()

        self._log_audit(skill_id, permission, "revoke", "success", now)
        logger.info("Permission revoked: %s -> %s", skill_id, permission)

    def revoke_all(self, skill_id: str) -> None:
        """
        Revoke all permissions for a skill.

        Args:
            skill_id: Skill to revoke all permissions from
        """
        now = datetime.utcnow().isoformat() + "Z"

        # Clear session grants
        if skill_id in self._session_grants:
            del self._session_grants[skill_id]

        # Clear persistent grants
        if self._db_path:
            conn = self._get_connection()
            try:
                conn.execute(
                    "DELETE FROM skill_permissions WHERE skill_id = ?",
                    (skill_id,),
                )
                conn.commit()
            finally:
                conn.close()

        self._log_audit(skill_id, "*", "revoke_all", "success", now)
        logger.info("All permissions revoked for: %s", skill_id)

    def get_grants(self, skill_id: str) -> list[str]:
        """
        Get all granted permissions for a skill.

        Args:
            skill_id: Skill to get permissions for

        Returns:
            List of granted permission names
        """
        grants = set()

        # Auto-granted
        grants.update(self._auto_permissions.get(skill_id, []))

        # Session grants
        grants.update(self._session_grants.get(skill_id, set()))

        # Persistent grants
        if self._db_path:
            conn = self._get_connection()
            try:
                rows = conn.execute(
                    """
                    SELECT permission FROM skill_permissions
                    WHERE skill_id = ? AND status = 'granted'
                    """,
                    (skill_id,),
                ).fetchall()
                grants.update(row["permission"] for row in rows)
            finally:
                conn.close()

        return sorted(grants)

    def set_auto_permissions(
        self,
        skill_id: str,
        permissions: list[str],
    ) -> None:
        """
        Set auto-granted permissions for a skill.

        Called when loading skill manifest.

        Args:
            skill_id: Skill ID
            permissions: List of permissions to auto-grant
        """
        self._auto_permissions[skill_id] = permissions

    def clear_auto_permissions(self, skill_id: str) -> None:
        """Clear auto permissions for a skill (on unload)."""
        if skill_id in self._auto_permissions:
            del self._auto_permissions[skill_id]

    def _log_audit(
        self,
        skill_id: str,
        permission: str,
        action: str,
        result: str,
        timestamp: str,
    ) -> str | None:
        """Log permission action to audit trail."""
        if not self._db_path:
            return

        try:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO permission_audit
                    (skill_id, permission, action, result, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (skill_id, permission, action, result, timestamp),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            # WP-A2 / register OP-W004 / safety gate S2. This was
            # `logger.warning("Failed to log audit: %s", e)` and nothing
            # else -- the exact line Test #1 emitted twice as "Failed to log
            # audit: database is locked", after which the governed action
            # reported unqualified success. The write still must not raise
            # (a lost audit row must not fail an action that legitimately
            # executed, per the approved S2 semantics), but it is now
            # reported: at ERROR, and to the current action so `_finish()`
            # can mark the result degraded.
            message = f"permission_audit write failed for {skill_id}/{permission}: {e}"
            logger.error("REQUIRED AUDIT WRITE FAILED: %s", message)
            record_permission_audit_failure(message)
            return message
        return None

    def get_audit_log(
        self,
        skill_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get permission audit log.

        Args:
            skill_id: Optional filter by skill
            limit: Maximum entries to return

        Returns:
            List of audit log entries
        """
        if not self._db_path:
            return []

        conn = self._get_connection()
        try:
            if skill_id:
                rows = conn.execute(
                    """
                    SELECT * FROM permission_audit
                    WHERE skill_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (skill_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM permission_audit
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

            return [dict(row) for row in rows]
        finally:
            conn.close()


# Module-level singleton
_checker: PermissionChecker | None = None


def get_permission_checker(
    db_path: str | None = None,
) -> PermissionChecker:
    """Get or create the global permission checker."""
    global _checker
    if _checker is None:
        _checker = PermissionChecker(db_path=db_path)
    return _checker


def reset_permission_checker() -> None:
    """Reset the global permission checker (for testing)."""
    global _checker
    _checker = None
