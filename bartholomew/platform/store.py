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
# Shared with runtime_registry by variable name rather than by import, so this
# module stays importable without the runtime machinery.
DATA_ROOT_ENV = "BARTH_DATA_ROOT"

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

-- ===========================================================================
-- Package E: device registry
-- ===========================================================================
--
-- Devices belong to accounts, so they are control-plane objects and live
-- here rather than in any user's kernel database. Two properties follow:
-- revoking a lost machine does not require that user's runtime to be
-- running, and no kernel is handed a write path to the credential table.
--
-- Isolation here is by `user_id` predicate rather than by file boundary --
-- the control plane is the shared store by design. Stated plainly in
-- `devices.py` and in docs/E_DEVICE_TRUST_AND_TRUSTED_GROUPS.md rather than
-- left to be inferred.
CREATE TABLE IF NOT EXISTS platform_devices (
  device_id         TEXT PRIMARY KEY,       -- server-generated UUID4, never a label
  user_id           TEXT NOT NULL,          -- the owning tenant
  display_name      TEXT NOT NULL,
  platform          TEXT NOT NULL,          -- 'windows'; provenance, not authority
  companion_version TEXT,
  manifest_version  INTEGER NOT NULL DEFAULT 0,
  capabilities      TEXT NOT NULL DEFAULT '[]',  -- declared [{kind,version}], verbatim
  -- The operator's ceiling, set at approval. NULL means "whatever this
  -- deployment understands"; a JSON list narrows the device to that set, so
  -- approving a machine is not the same act as believing everything it later
  -- says it can do.
  approved_capabilities TEXT,
  status            TEXT NOT NULL,          -- DeviceStatus value; see devices.py
  created_at        INTEGER NOT NULL,
  approved_at       INTEGER,
  enrolled_at       INTEGER,                -- first verified contact
  disabled_at       INTEGER,
  revoked_at        INTEGER,
  -- Written only after a credential verifies. "This device was genuinely
  -- here", never "someone guessed at this device's id".
  last_seen_at      INTEGER,
  FOREIGN KEY(user_id) REFERENCES platform_accounts(user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_platform_devices_user ON platform_devices(user_id);

-- Device credentials. As with sessions, the secret itself is never stored:
-- only its SHA-256 digest, so read access to this table does not yield a
-- usable credential. `user_id` is carried here as well as on the device row
-- so verification can check the tenant binding on the credential it actually
-- matched, rather than re-deriving it.
CREATE TABLE IF NOT EXISTS platform_device_credentials (
  credential_id TEXT PRIMARY KEY,
  device_id     TEXT NOT NULL,
  user_id       TEXT NOT NULL,
  secret_hash   TEXT NOT NULL,
  purpose       TEXT NOT NULL,             -- 'enrolment' (one-time) | 'device'
  created_at    INTEGER NOT NULL,
  expires_at    INTEGER,                   -- enrolment secrets only
  first_used_at INTEGER,
  rotated_at    INTEGER,
  revoked_at    INTEGER,
  FOREIGN KEY(device_id) REFERENCES platform_devices(device_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_device_credentials_hash
  ON platform_device_credentials(secret_hash);
CREATE INDEX IF NOT EXISTS idx_platform_device_credentials_device
  ON platform_device_credentials(device_id);

-- ===========================================================================
-- Package E: trusted groups and explicit sharing
-- ===========================================================================
--
-- A trusted group spans accounts, so it cannot live in any one user's
-- database. This is the one surface in Bartholomew where content crosses a
-- tenant boundary, and it does so only as an explicitly published, typed,
-- sanitized package -- never as raw memory. See
-- `bartholomew/kernel/trusted_share.py` for what may cross and what may not.
CREATE TABLE IF NOT EXISTS platform_trusted_groups (
  group_id    TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  archived_at INTEGER,
  FOREIGN KEY(owner_user_id) REFERENCES platform_accounts(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS platform_group_members (
  group_id   TEXT NOT NULL,
  user_id    TEXT NOT NULL,
  role       TEXT NOT NULL,               -- owner | admin | member
  joined_at  INTEGER NOT NULL,
  removed_at INTEGER,
  PRIMARY KEY (group_id, user_id),
  FOREIGN KEY(group_id) REFERENCES platform_trusted_groups(group_id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES platform_accounts(user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_platform_group_members_user
  ON platform_group_members(user_id);

-- Invitations are explicit, expiring and single-use. An invitation is not
-- membership: acceptance is a separate act by the invited account itself.
CREATE TABLE IF NOT EXISTS platform_group_invitations (
  invitation_id TEXT PRIMARY KEY,
  group_id      TEXT NOT NULL,
  invited_user_id TEXT NOT NULL,
  invited_by_user_id TEXT NOT NULL,
  role          TEXT NOT NULL,
  created_at    INTEGER NOT NULL,
  expires_at    INTEGER NOT NULL,
  accepted_at   INTEGER,
  declined_at   INTEGER,
  revoked_at    INTEGER,
  FOREIGN KEY(group_id) REFERENCES platform_trusted_groups(group_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_platform_group_invitations_user
  ON platform_group_invitations(invited_user_id);

-- Published share packages. One row per (share, revision): a publisher
-- update is a new revision, never an overwrite, so a recipient's adopted
-- version and the publisher's current one are separately addressable and no
-- last-write-wins is possible.
CREATE TABLE IF NOT EXISTS platform_share_packages (
  share_id        TEXT NOT NULL,
  revision        INTEGER NOT NULL,
  group_id        TEXT NOT NULL,
  publisher_user_id TEXT NOT NULL,
  kind            TEXT NOT NULL,          -- competency | correction | household_routine | guidance
  content         TEXT NOT NULL,          -- sanitized JSON; never raw memory
  content_hash    TEXT NOT NULL,
  source_candidate_fingerprint TEXT NOT NULL,
  sanitization    TEXT NOT NULL,          -- {"policy_revision":N,"removed_fields":[...]}
  published_at    TEXT NOT NULL,          -- RFC3339 UTC
  revoked_at      TEXT,
  PRIMARY KEY (share_id, revision),
  FOREIGN KEY(group_id) REFERENCES platform_trusted_groups(group_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_platform_share_packages_group
  ON platform_share_packages(group_id, share_id);

-- What each recipient has done about each share. Separate from the package
-- itself so a publisher revision cannot rewrite a recipient's decision, and
-- so a recipient's adopted revision stays visible after a revocation.
CREATE TABLE IF NOT EXISTS platform_share_receipts (
  share_id          TEXT NOT NULL,
  recipient_user_id TEXT NOT NULL,
  state             TEXT NOT NULL,        -- delivered | declined | adopted
  adopted_revision  INTEGER,
  local_fork        INTEGER NOT NULL DEFAULT 0,
  updated_at        INTEGER NOT NULL,
  PRIMARY KEY (share_id, recipient_user_id)
);
CREATE INDEX IF NOT EXISTS idx_platform_share_receipts_recipient
  ON platform_share_receipts(recipient_user_id);
"""


def resolve_platform_db_path() -> str:
    """
    The control-plane database path, read fresh on every call.

    Read fresh rather than cached at import for the reason documented in
    `services/api/db.resolve_db_path()`: a module-level constant is frozen by
    Python's module cache at whichever import happens first, which silently
    shares one physical file across test files that each set the environment
    variable expecting isolation.

    The default honours `BARTH_DATA_ROOT` rather than hardcoding the
    repository's `data/` directory. Hardcoding it meant that a caller which
    had isolated every *other* persistence surface by setting the data root --
    which is what the test suite does -- still landed on one shared
    control-plane file, reintroducing exactly the cross-caller sharing the
    fresh-read above exists to prevent.
    """
    root = os.getenv(DATA_ROOT_ENV)
    base = Path(root) if root else Path(__file__).resolve().parents[2] / "data"
    default = str(base / "platform.db")
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
    # Package E: added after the device tables first shipped, so it is listed
    # in both places -- `_SCHEMA` for a fresh database, here for one that
    # already exists. A column added only to `_SCHEMA` silently never reaches
    # a live control plane.
    ("platform_devices", "approved_capabilities", "TEXT"),
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
