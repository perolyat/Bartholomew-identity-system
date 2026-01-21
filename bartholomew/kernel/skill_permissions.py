"""
Skill Permissions
=================

Permission model for controlling skill access to system resources.
Part of Stage 4: Skill Registry + Starter Skills.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


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


@dataclass
class PermissionResult:
    """Result of a permission check."""

    granted: bool
    status: PermissionStatus
    permission: str
    reason: str = ""
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "granted": self.granted,
            "status": self.status.value,
            "permission": self.permission,
            "reason": self.reason,
            "expires_at": self.expires_at,
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
            self._log_audit(skill_id, permission, "check", "granted_auto", now)
            return PermissionResult(
                granted=True,
                status=PermissionStatus.GRANTED,
                permission=permission,
                reason="Auto-granted by manifest",
            )

        # 2. Check session grants
        session_perms = self._session_grants.get(skill_id, set())
        if permission in session_perms:
            self._log_audit(skill_id, permission, "check", "granted_session", now)
            return PermissionResult(
                granted=True,
                status=PermissionStatus.GRANTED,
                permission=permission,
                reason="Granted for session",
            )

        # 3. Check persistent grants (database)
        if self._db_path:
            db_grant = self._check_db_grant(skill_id, permission)
            if db_grant:
                # Check expiration
                if db_grant.expires_at:
                    if db_grant.expires_at > now:
                        self._log_audit(skill_id, permission, "check", "granted_db", now)
                        return PermissionResult(
                            granted=True,
                            status=PermissionStatus.GRANTED,
                            permission=permission,
                            reason="Persistent grant",
                            expires_at=db_grant.expires_at,
                        )
                else:
                    self._log_audit(skill_id, permission, "check", "granted_db", now)
                    return PermissionResult(
                        granted=True,
                        status=PermissionStatus.GRANTED,
                        permission=permission,
                        reason="Persistent grant",
                    )

        # 4. Permission not granted
        self._log_audit(skill_id, permission, "check", "denied", now)
        return PermissionResult(
            granted=False,
            status=PermissionStatus.DENIED,
            permission=permission,
            reason="Not granted",
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
    ) -> None:
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
            logger.warning("Failed to log audit: %s", e)

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
