"""
Governance schema: one authoritative definition of parking_brake_state,
brake_runtime, and governance_audit (docs/PHASE_B_OVERVIEW.md section 4's
"one authoritative schema per governance table" invariant).

ensure_schema() is additive and idempotent: CREATE TABLE IF NOT EXISTS only,
safe to call on every startup, and migrates any pre-existing legacy value
from system_flags's "parking_brake" key (bartholomew.orchestrator.safety.
parking_brake.BrakeStorage's storage format) exactly once.
"""

from __future__ import annotations

import json
import sqlite3
import time

from bartholomew.kernel import db_ctx

SCHEMA = """
-- Durable Parking Brake state. Singleton row (id=1) -- there is exactly one
-- brake, per docs/PHASE_B_OVERVIEW.md's "one global brake with optional
-- scopes" description. `revision` is bumped on every transition (engage,
-- disengage, or narrow) and is the ordering signal callers use to detect a
-- stale transition attempt racing a newer one. `last_loosen_revision`
-- records the revision at which the most recent loosening (disengage or
-- narrow) was applied, so a delayed/stale loosening whose own revision
-- check still passes cannot regress state that a newer engage has since
-- tightened again -- see GovernanceBrakeStore.apply_transition()'s
-- ordering check.
CREATE TABLE IF NOT EXISTS parking_brake_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    engaged INTEGER NOT NULL DEFAULT 0,
    scopes_json TEXT NOT NULL DEFAULT '[]',
    revision INTEGER NOT NULL DEFAULT 0,
    last_loosen_revision INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

-- Process/runtime-local binding info, deliberately separate from the
-- durable state above -- which process most recently loaded/wrote the
-- brake, for B6's external-control/CLI-safety work to detect a CLI
-- operation racing a live daemon. Not itself a source of Governance
-- authority; parking_brake_state alone determines whether a scope is
-- blocked.
CREATE TABLE IF NOT EXISTS brake_runtime (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    owner_label TEXT,
    pid INTEGER,
    started_at INTEGER,
    last_seen_at INTEGER,
    schema_version INTEGER NOT NULL DEFAULT 1
);

-- Append-only audit trail. Every row is written in the same transaction as
-- the parking_brake_state write it describes (GovernanceBrakeStore's own
-- connection, one commit) -- unlike the legacy BrakeStorage.append_memory(),
-- which fires an unawaited asyncio task against MemoryStore and is not
-- atomic with the state write it's meant to describe.
CREATE TABLE IF NOT EXISTS governance_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision INTEGER NOT NULL,
    action TEXT NOT NULL,
    scopes_before_json TEXT NOT NULL,
    scopes_after_json TEXT NOT NULL,
    engaged_before INTEGER NOT NULL,
    engaged_after INTEGER NOT NULL,
    actor TEXT,
    reason TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_governance_audit_revision
    ON governance_audit(revision);
"""

_LEGACY_FLAG_KEY = "parking_brake"


def ensure_schema(db_path: str) -> None:
    """
    Create the governance tables if they don't exist, then migrate any
    pre-existing legacy system_flags "parking_brake" value into
    parking_brake_state -- exactly once, and only if parking_brake_state has
    no row yet (idempotent: safe to call on every startup).

    Does not touch system_flags's row -- the legacy BrakeStorage
    implementation keeps reading/writing it until B4 actually swaps the
    runtime over; this migration is read-only with respect to that table.
    """
    with db_ctx.wal_db(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate_legacy_flag_if_present(conn)
        conn.commit()


def _migrate_legacy_flag_if_present(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT id FROM parking_brake_state WHERE id = 1").fetchone()
    if existing is not None:
        return  # already migrated (or already written to by this schema)

    row = None
    try:
        row = conn.execute(
            "SELECT value FROM system_flags WHERE key = ?",
            (_LEGACY_FLAG_KEY,),
        ).fetchone()
    except sqlite3.OperationalError:
        # system_flags doesn't exist yet on a genuinely fresh database --
        # nothing to migrate, not an error.
        pass

    now = int(time.time())
    if row is None:
        engaged, scopes = False, []
    else:
        try:
            data = json.loads(row[0])
            engaged = bool(data.get("engaged", False))
            scopes = sorted(set(data.get("scopes", [])))
        except (ValueError, TypeError, AttributeError):
            # Malformed legacy value: fail closed (matches
            # docs/PHASE_B_OVERVIEW.md section 4's "fail-closed governance"
            # invariant) rather than silently defaulting to an unengaged
            # brake because an unrelated storage row didn't parse.
            engaged, scopes = True, ["global"]

    conn.execute(
        """
        INSERT INTO parking_brake_state
            (id, engaged, scopes_json, revision, last_loosen_revision, updated_at)
        VALUES (1, ?, ?, 0, 0, ?)
        """,
        (int(engaged), json.dumps(scopes), now),
    )
