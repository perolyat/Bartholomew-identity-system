"""
Control-plane persistence: accounts, sessions, platform audit, platform brake.

A **separate database file** from any user's kernel database, and that
separation is a security property rather than a tidiness preference:

* no personal memory ever lives here, so a control-plane compromise does not
  hand over anyone's memory;
* no user's kernel runtime is given a handle to it, so a defect in kernel
  code cannot reach another user's credentials;
* it is the one store that is legitimately shared, which makes "is this
  shared?" answerable by looking at which database a module opens.

Connections go through `bartholomew.kernel.db_ctx` -- the repository's single
connection authority -- so WAL pragmas and busy-timeout behaviour match every
other store rather than being a second, subtly different SQLite dialect.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from bartholomew.kernel.db_ctx import connect, set_wal_pragmas

PLATFORM_DB_PATH_ENV = "BARTH_PLATFORM_DB_PATH"

_SCHEMA = """
-- Accounts. Operator-created only for Alpha: there is no self-registration
-- path in the codebase, by decision, not by omission.
CREATE TABLE IF NOT EXISTS platform_accounts (
  user_id        TEXT PRIMARY KEY,
  username       TEXT NOT NULL,
  kind           TEXT NOT NULL,          -- PrincipalKind value
  password_hash  TEXT NOT NULL,          -- scrypt, self-describing parameters
  created_at     INTEGER NOT NULL,
  disabled_at    INTEGER,                -- non-NULL disables login immediately
  -- Throttling state. A hosted login endpoint with no cost to guessing is a
  -- credential-stuffing target; scrypt makes each guess expensive but not
  -- expensive enough to leave unbounded.
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until    INTEGER
);
-- NOCASE: usernames differing only by case must not be two accounts, or
-- "Taylor" and "taylor" become an impersonation vector at provisioning time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_accounts_username
  ON platform_accounts(username COLLATE NOCASE);

-- Sessions. The token itself is never stored: only its SHA-256 digest, so
-- read access to this table does not yield usable live credentials.
CREATE TABLE IF NOT EXISTS platform_sessions (
  session_id          TEXT PRIMARY KEY,
  token_hash          TEXT NOT NULL,
  user_id             TEXT NOT NULL,
  created_at          INTEGER NOT NULL,
  expires_at          INTEGER NOT NULL,   -- absolute expiry
  last_seen_at        INTEGER NOT NULL,   -- drives idle timeout
  client_fingerprint  TEXT NOT NULL,      -- replay/theft binding; see sessions.py
  revoked_at          INTEGER,
  FOREIGN KEY(user_id) REFERENCES platform_accounts(user_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_sessions_token
  ON platform_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_platform_sessions_user
  ON platform_sessions(user_id);

-- Platform-tier audit. Append-only by convention, matching the existing
-- governance_audit minimum tamper floor (S2): no normal application path
-- updates or deletes historical rows.
CREATE TABLE IF NOT EXISTS platform_audit (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          INTEGER NOT NULL,
  event       TEXT NOT NULL,
  user_id     TEXT,
  detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_platform_audit_ts ON platform_audit(ts DESC);

-- Platform/Admin Parking Brake tier. Deliberately a separate table from the
-- per-user kernel database's parking_brake_state: the Platform tier must not
-- be reachable by the ordinary per-user disengage path. See authority.py.
CREATE TABLE IF NOT EXISTS platform_brake_state (
  id          INTEGER PRIMARY KEY CHECK (id = 1),
  engaged     INTEGER NOT NULL,
  scopes      TEXT NOT NULL,
  revision    INTEGER NOT NULL,
  reason      TEXT,
  actor       TEXT,
  updated_at  INTEGER NOT NULL
);
"""


def resolve_platform_db_path() -> str:
    """
    The control-plane database path, read fresh on every call.

    Read fresh rather than cached at import for the reason documented in
    `services/api/db.resolve_db_path()`: a module-level constant is frozen by
    Python's module cache at whichever import happens first, which silently
    shares one physical file across test files that each set the environment
    variable expecting isolation.
    """
    default = str(Path(__file__).resolve().parents[2] / "data" / "platform.db")
    path = os.getenv(PLATFORM_DB_PATH_ENV, default)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def platform_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Open a control-plane connection through the kernel connection authority."""
    conn = connect(db_path or resolve_platform_db_path())
    set_wal_pragmas(conn)
    conn.row_factory = sqlite3.Row
    # Session rows are FK-bound to accounts so that deleting an account
    # actually invalidates its sessions rather than orphaning live
    # credentials. SQLite enforces that only when asked, per connection.
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columns added after the first schema shipped. Applied as additive ALTERs so
# an existing control-plane database upgrades in place rather than needing a
# migration tool -- and additively only, so no existing row or column is
# rewritten and there is no data-loss path.
_ADDITIVE_COLUMNS = (
    ("platform_accounts", "failed_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("platform_accounts", "locked_until", "INTEGER"),
)


def init_platform_schema(db_path: str | None = None) -> None:
    """Create or upgrade the control-plane schema. Idempotent."""
    with platform_connection(db_path) as conn:
        conn.executescript(_SCHEMA)
        for table, column, decl in _ADDITIVE_COLUMNS:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def record_platform_audit(
    conn: sqlite3.Connection,
    event: str,
    *,
    user_id: str | None = None,
    detail: str | None = None,
    ts: int | None = None,
) -> None:
    """
    Append one platform audit row on the caller's connection/transaction.

    Takes a connection rather than opening its own so an audit row commits
    atomically with the change it describes: an account provisioned without
    its audit row, or vice versa, is a worse record than either alone.

    `detail` must never contain a session token or password material -- see
    the audit-hygiene tests in tests/test_s8_auth_boundary.py.
    """
    conn.execute(
        "INSERT INTO platform_audit(ts, event, user_id, detail) VALUES (?, ?, ?, ?)",
        (int(ts if ts is not None else time.time()), event, user_id, detail),
    )
