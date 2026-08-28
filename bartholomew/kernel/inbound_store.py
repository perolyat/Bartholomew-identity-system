"""Persistence for governed inbound capture (Session D).

One table, `inbound_events`, holding what arrived, from where, when, and what
Governance said about it. Deliberately not Memory: capture records that
*something arrived*, never that Bartholomew believes it, and nothing here
writes to `memories`, `nudges` or objectives.

Provenance is the point of the table. Every row can answer:

* where did this come from?      -- `source_id`, `verified_by`
* what claimed it?               -- `event_type` (opaque), `event_id`
* when was it received?          -- `received_at` (ours), `occurred_at` (theirs)
* what was accepted?             -- `payload_json`, `payload_sha256`
* what happened to it?           -- `outcome`, `governance_reason`

Idempotency is a `UNIQUE(source_id, event_id)` constraint, mirroring the
scheduler's existing `ticks.idempotency_key TEXT UNIQUE` rather than inventing
a second mechanism. The constraint -- not the pre-check -- is the guarantee:
two concurrent deliveries of the same event race the pre-check, and only one
can win the INSERT.

Synchronous sqlite3 by design, called through `run_off_loop()` from the async
seam, matching every other persistence surface in this repository (Phase B
stage B2). Single-writer SQLite assumptions are respected: this adds no new
connection policy, no second writer process, and no long transaction.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bartholomew.kernel.db_ctx import wal_db

INBOUND_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbound_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT,
    received_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    outcome TEXT NOT NULL,
    governance_reason TEXT,
    verified_by TEXT NOT NULL,
    runtime_id TEXT,
    UNIQUE (source_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_inbound_events_received
    ON inbound_events(received_at DESC);
"""

#: The only outcome that means "durably captured". Anything else is a refusal
#: or a failure, and a caller must not report it as success.
OUTCOME_CAPTURED = "captured"


class InboundPersistenceError(RuntimeError):
    """Persisting a captured event failed.

    Raised rather than swallowed: an acknowledgement that implies capture must
    never be returned for an event that was not written.
    """


@dataclass(frozen=True)
class StoredInboundEvent:
    """One `inbound_events` row, as the seam and the API report it."""

    row_id: int
    source_id: str
    event_id: str
    event_type: str
    occurred_at: str | None
    received_at: str
    payload_sha256: str
    outcome: str
    governance_reason: str | None
    verified_by: str
    runtime_id: str | None
    duplicate: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.row_id,
            "source_id": self.source_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "received_at": self.received_at,
            "payload_sha256": self.payload_sha256,
            "outcome": self.outcome,
            "governance_reason": self.governance_reason,
            "verified_by": self.verified_by,
            "runtime_id": self.runtime_id,
            "duplicate": self.duplicate,
        }


def ensure_schema(db_path: str) -> None:
    """Create the inbound table if it does not exist. Idempotent."""
    with wal_db(db_path, timeout=30.0, label="inbound_ensure_schema") as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.executescript(INBOUND_SCHEMA)
        conn.commit()


def payload_digest(payload: Any) -> tuple[str, str]:
    """Canonical JSON for a payload, and its SHA-256.

    `sort_keys=True` so the digest is stable across two deliveries of the same
    event whose JSON key order differs -- the digest is a provenance record of
    *what was accepted*, not of the sender's byte formatting.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row_to_event(row: tuple[Any, ...], *, duplicate: bool) -> StoredInboundEvent:
    return StoredInboundEvent(
        row_id=row[0],
        source_id=row[1],
        event_id=row[2],
        event_type=row[3],
        occurred_at=row[4],
        received_at=row[5],
        payload_sha256=row[6],
        outcome=row[7],
        governance_reason=row[8],
        verified_by=row[9],
        runtime_id=row[10],
        duplicate=duplicate,
    )


_SELECT_COLUMNS = (
    "id, source_id, event_id, event_type, occurred_at, received_at, "
    "payload_sha256, outcome, governance_reason, verified_by, runtime_id"
)


def get_event(db_path: str, source_id: str, event_id: str) -> StoredInboundEvent | None:
    """The already-stored event for this (source, id), if there is one."""
    with wal_db(db_path, timeout=5.0, label="inbound_get_event") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM inbound_events "  # noqa: S608 - fixed column list
            "WHERE source_id = ? AND event_id = ?",
            (source_id, event_id),
        ).fetchone()
    return _row_to_event(row, duplicate=True) if row else None


def capture_event(
    db_path: str,
    *,
    source_id: str,
    event_id: str,
    event_type: str,
    occurred_at: str | None,
    payload: Any,
    outcome: str,
    governance_reason: str | None,
    verified_by: str,
    runtime_id: str | None,
) -> StoredInboundEvent:
    """Durably record one inbound event, or report the existing one.

    Returns a `StoredInboundEvent` with `duplicate=True` when this
    (source_id, event_id) was already captured -- the retry of an external
    system must not become a second logical event, and must not be reported
    as a fresh capture either.

    Raises `InboundPersistenceError` if the write fails for any other reason.
    The caller is required to translate that into an honest failure response:
    nothing was captured, so nothing may be acknowledged as captured.
    """
    canonical, digest = payload_digest(payload)
    received_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )

    try:
        with wal_db(db_path, timeout=30.0, label="inbound_capture_event") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            try:
                cur = conn.execute(
                    """INSERT INTO inbound_events
                       (source_id, event_id, event_type, occurred_at, received_at,
                        payload_json, payload_sha256, outcome, governance_reason,
                        verified_by, runtime_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_id,
                        event_id,
                        event_type,
                        occurred_at,
                        received_at,
                        canonical,
                        digest,
                        outcome,
                        governance_reason,
                        verified_by,
                        runtime_id,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                # The UNIQUE constraint, not the pre-check, is the authority on
                # duplicates -- two concurrent deliveries both pass a pre-check
                # and only one reaches here first. Report the row that actually
                # exists, so `duplicate=True` is never claimed without one.
                conn.rollback()
                row = conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM inbound_events "  # noqa: S608
                    "WHERE source_id = ? AND event_id = ?",
                    (source_id, event_id),
                ).fetchone()
                if row is None:
                    raise
                return _row_to_event(row, duplicate=True)

            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM inbound_events WHERE id = ?",  # noqa: S608
                (cur.lastrowid,),
            ).fetchone()
    except sqlite3.Error as e:
        raise InboundPersistenceError(
            f"Inbound event {source_id}/{event_id} was NOT persisted: {type(e).__name__}: {e}",
        ) from e

    if row is None:  # pragma: no cover - the row was just written in this transaction
        raise InboundPersistenceError(
            f"Inbound event {source_id}/{event_id} was written but could not be read back",
        )
    return _row_to_event(row, duplicate=False)


def recent_events(db_path: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Most recently received events, newest first, for the inspection surface.

    Payloads are deliberately not returned: the inspection surface answers
    "what arrived and what happened to it", and a list endpoint is the wrong
    place to re-emit third-party content wholesale.
    """
    with wal_db(db_path, timeout=5.0, label="inbound_recent_events") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbound_events'",
        ).fetchone()
        if not table:
            # Nothing has ever been captured in this database. An empty list is
            # the truthful answer; an error would not be.
            return []
        rows = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM inbound_events "  # noqa: S608
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_row_to_event(r, duplicate=False).as_dict() for r in rows]
