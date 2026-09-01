"""Durable processing state for captured events: one table, one state machine.

`inbound_events` is the record of *what arrived*. It says nothing about what
was done with it, and it must not: capture's whole discipline is that a row
there means "received", never "understood". This table is the missing half --
the record of *what happened next* -- and it is deliberately a separate table
so that neither authority can quietly redefine the other.

The state machine, and nothing outside it::

    captured  --claim-->  claimed  --settle-->  processed
                                   --settle-->  irrelevant
                                   --settle-->  refused
                                   --fail  -->  captured (attempt spent)
                                   --fail  -->  quarantined (attempts exhausted)
                                   --release-> captured (attempt refunded)
                                   --lease  -->  captured (recovered by a later pass)

`processed`, `irrelevant`, `refused` and `quarantined` are terminal. Nothing
in normal operation leaves a terminal state; an operator can put a
`quarantined` or `refused` event back with `requeue()`, which is a deliberate
act and is recorded as one.

Four properties this file exists to guarantee
---------------------------------------------
**Idempotency.** `UNIQUE (source_id, event_id)` -- the same pair capture
already made unique, so an event has at most one processing row however many
times it is swept, redelivered or recovered.

**No lost work.** A claim is a lease with an expiry, not a lock. A process
that dies holding claims loses nothing: the next pass finds the expired lease
and returns the event to `captured`.

**No duplicated effects.** A claim carries a token, and every settle/fail/
release is conditional on it. A worker whose lease expired mid-flight cannot
settle an event another pass has since taken -- its write matches no row and
reports False. (The downstream effect is separately idempotent: evidence
attachment checks the objective's own history. Both hold, independently.)

**Tenant isolation.** Every row carries the `runtime_id` capture recorded, and
claiming filters on it. A process bound to one runtime cannot claim, process
or settle another's events, and the filter is in the same statement that does
the claiming rather than in a caller that might forget it.

Synchronous sqlite3 by design, called through `run_off_loop()` from the async
seams, exactly like `inbound_store` and `scheduler/persistence`. This adds no
connection policy, no second writer process and no long transaction.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from bartholomew.kernel.db_ctx import wal_db

logger = logging.getLogger(__name__)

# -- states -----------------------------------------------------------------

#: Swept from `inbound_events`, waiting for a pass to take it.
STATE_CAPTURED = "captured"
#: A pass holds a lease on it right now.
STATE_CLAIMED = "claimed"
#: A handler ran and its effect (if any) is durable. Terminal.
STATE_PROCESSED = "processed"
#: Nothing here bears on anything Bartholomew is carrying. Terminal, and an
#: explicit verdict rather than an absence of one.
STATE_IRRELEVANT = "irrelevant"
#: Deliberately not acted on: an unknown event type, a payload that is not
#: what its type promises, a policy denial, or an interpretation that would
#: have had to guess. Terminal, and never an error.
STATE_REFUSED = "refused"
#: Repeatedly failed. Terminal, held for inspection, and out of the way of
#: every later event. Terminal.
STATE_QUARANTINED = "quarantined"

TERMINAL_STATES = frozenset(
    {STATE_PROCESSED, STATE_IRRELEVANT, STATE_REFUSED, STATE_QUARANTINED},
)
PENDING_STATES = frozenset({STATE_CAPTURED, STATE_CLAIMED})
ALL_STATES = TERMINAL_STATES | PENDING_STATES

#: The dispositions a handler may ask for. `quarantined` is deliberately not
#: among them: quarantine is what repeated *failure* produces, and a handler
#: that could elect it directly would be able to hide a refusal as a fault.
SETTLEABLE_STATES = frozenset({STATE_PROCESSED, STATE_IRRELEVANT, STATE_REFUSED})

_STATE_CHECK = ", ".join(f"'{s}'" for s in sorted(ALL_STATES))

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS event_processing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    envelope_version INTEGER NOT NULL,
    inbound_row_id INTEGER,
    source_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    runtime_id TEXT,
    payload_sha256 TEXT NOT NULL,
    received_at TEXT NOT NULL,
    received_ts INTEGER NOT NULL,
    enqueued_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ({_STATE_CHECK})),
    attempts INTEGER NOT NULL DEFAULT 0,
    claim_token TEXT,
    claimed_at TEXT,
    lease_expires_ts INTEGER,
    last_attempt_at TEXT,
    last_error TEXT,
    settled_at TEXT,
    disposition_reason TEXT,
    result_json TEXT,
    UNIQUE (source_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_event_processing_ready
    ON event_processing(state, received_ts, id);
CREATE INDEX IF NOT EXISTS idx_event_processing_lease
    ON event_processing(state, lease_expires_ts);
CREATE INDEX IF NOT EXISTS idx_event_processing_runtime
    ON event_processing(runtime_id, state);

-- How far the sweep has read `inbound_events`. One row, by construction.
--
-- A watermark rather than a NOT EXISTS join because the join degenerates to a
-- full scan of `inbound_events` on every tick once everything is synced. It is
-- safe here for a specific, checkable reason: SQLite permits one write
-- transaction at a time and `inbound_events.id` is assigned inside that
-- transaction, so ids become visible in ascending order and a row can never
-- appear below a watermark already passed. `resync_from()` exists for the case
-- where that reasoning is ever wrong, or a database is repaired by hand.
CREATE TABLE IF NOT EXISTS event_processing_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_inbound_row_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_SELECT_COLUMNS = (
    "id, envelope_version, inbound_row_id, source_id, event_id, event_type, "
    "runtime_id, payload_sha256, received_at, received_ts, enqueued_at, state, "
    "attempts, claim_token, claimed_at, lease_expires_ts, last_attempt_at, "
    "last_error, settled_at, disposition_reason, result_json"
)


class EventProcessingStateError(RuntimeError):
    """A durable processing-state operation failed.

    Raised rather than swallowed, for the same reason `InboundPersistenceError`
    is: a caller must never report an event as settled when the settlement did
    not reach the disk.
    """


@dataclass(frozen=True)
class ProcessingRecord:
    """One `event_processing` row."""

    row_id: int
    envelope_version: int
    inbound_row_id: int | None
    source_id: str
    event_id: str
    event_type: str
    runtime_id: str | None
    payload_sha256: str
    received_at: str
    received_ts: int
    enqueued_at: str
    state: str
    attempts: int
    claim_token: str | None
    claimed_at: str | None
    lease_expires_ts: int | None
    last_attempt_at: str | None
    last_error: str | None
    settled_at: str | None
    disposition_reason: str | None
    result: dict[str, Any] | None

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.row_id,
            "envelope_version": self.envelope_version,
            "inbound_row_id": self.inbound_row_id,
            "source_id": self.source_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "runtime_id": self.runtime_id,
            "payload_sha256": self.payload_sha256,
            "received_at": self.received_at,
            "enqueued_at": self.enqueued_at,
            "state": self.state,
            "attempts": self.attempts,
            "claimed_at": self.claimed_at,
            "lease_expires_ts": self.lease_expires_ts,
            "last_attempt_at": self.last_attempt_at,
            "last_error": self.last_error,
            "settled_at": self.settled_at,
            "disposition_reason": self.disposition_reason,
            "result": self.result,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_to_epoch(value: str | None) -> int:
    """Whole seconds from capture's ISO timestamp, or 0 when unreadable.

    0 sorts an unreadable timestamp to the front of the queue, which is the
    safe direction: an event whose age nobody can establish is processed
    first rather than being left behind indefinitely.
    """
    if not value:
        return 0
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _row_to_record(row: tuple[Any, ...]) -> ProcessingRecord:
    result: dict[str, Any] | None = None
    if row[20]:
        try:
            parsed = json.loads(row[20])
            result = parsed if isinstance(parsed, dict) else {"value": parsed}
        except (TypeError, ValueError):
            result = None
    return ProcessingRecord(
        row_id=row[0],
        envelope_version=row[1],
        inbound_row_id=row[2],
        source_id=row[3],
        event_id=row[4],
        event_type=row[5],
        runtime_id=row[6],
        payload_sha256=row[7],
        received_at=row[8],
        received_ts=row[9],
        enqueued_at=row[10],
        state=row[11],
        attempts=row[12],
        claim_token=row[13],
        claimed_at=row[14],
        lease_expires_ts=row[15],
        last_attempt_at=row[16],
        last_error=row[17],
        settled_at=row[18],
        disposition_reason=row[19],
        result=result,
    )


def _connect(db_path: str, label: str):
    return wal_db(db_path, timeout=30.0, label=label)


def ensure_schema(db_path: str) -> None:
    """Create the processing table and cursor if missing. Idempotent.

    Called from `KernelDaemon.start()` before the scheduler task exists, and
    again defensively by the processing pass -- the same belt-and-braces the
    scheduler's own schema uses, and for the same reason: a surface that can
    be reached before startup finished must not 500 on a missing table.
    """
    try:
        with _connect(db_path, "event_processing_ensure_schema") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executescript(SCHEMA)
            conn.commit()
    except sqlite3.Error as e:
        raise EventProcessingStateError(
            f"event-processing schema unavailable: {type(e).__name__}: {e}",
        ) from e


def table_exists(db_path: str) -> bool:
    """Whether this database has ever had processing state.

    Reported truthfully by the health and evidence surfaces: "no table" and
    "no events" are different findings and neither may be rendered as the
    other.
    """
    try:
        with _connect(db_path, "event_processing_table_exists") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            return (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_processing'",
                ).fetchone()
                is not None
            )
    except sqlite3.Error:
        return False


# -- the sweep ---------------------------------------------------------------


def _read_cursor(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT last_inbound_row_id FROM event_processing_cursor WHERE id = 1",
    ).fetchone()
    return int(row[0]) if row else 0


def _write_cursor(conn: sqlite3.Connection, value: int) -> None:
    conn.execute(
        "INSERT INTO event_processing_cursor (id, last_inbound_row_id, updated_at) "
        "VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET last_inbound_row_id = excluded.last_inbound_row_id, "
        "updated_at = excluded.updated_at",
        (int(value), _now_iso()),
    )


def sweep_captured(db_path: str, *, limit: int = 200, envelope_version: int = 1) -> int:
    """Enqueue captured inbound events that have no processing row yet.

    The sweep, not the capture call, is what puts an event into the backbone.
    That ordering is deliberate:

    * capture stays exactly what it was -- one write, one acknowledgement, no
      second table on the request path that could fail after the row exists;
    * events captured before this feature existed are picked up on the first
      tick after an upgrade, with no migration step;
    * events captured while the process was down are picked up when it comes
      back, because the authority is the table, not a call that happened once.

    Only rows whose capture `outcome` is `captured` are enqueued. A row that
    Governance refused was recorded precisely so the refusal is visible, and
    processing it would be acting on something Governance declined.

    Returns the number of rows enqueued.
    """
    from bartholomew.kernel.inbound_store import OUTCOME_CAPTURED

    now = _now_iso()
    try:
        with _connect(db_path, "event_processing_sweep") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executescript(SCHEMA)
            has_inbound = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbound_events'",
            ).fetchone()
            if not has_inbound:
                # Nothing has ever been captured here. Not an error.
                return 0

            conn.execute("BEGIN IMMEDIATE")
            try:
                watermark = _read_cursor(conn)
                rows = conn.execute(
                    "SELECT id, source_id, event_id, event_type, received_at, "
                    "payload_sha256, runtime_id "
                    "FROM inbound_events WHERE outcome = ? AND id > ? "
                    "ORDER BY id ASC LIMIT ?",
                    (OUTCOME_CAPTURED, watermark, int(limit)),
                ).fetchall()
                if not rows:
                    conn.commit()
                    return 0

                enqueued = 0
                highest = watermark
                for row in rows:
                    inbound_id = int(row[0])
                    highest = max(highest, inbound_id)
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO event_processing "
                        "(envelope_version, inbound_row_id, source_id, event_id, "
                        " event_type, runtime_id, payload_sha256, received_at, "
                        " received_ts, enqueued_at, state, attempts) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (
                            int(envelope_version),
                            inbound_id,
                            row[1],
                            row[2],
                            row[3],
                            row[6],
                            row[5],
                            row[4],
                            _iso_to_epoch(row[4]),
                            now,
                            STATE_CAPTURED,
                        ),
                    )
                    enqueued += cur.rowcount or 0
                _write_cursor(conn, highest)
                conn.commit()
                return enqueued
            except BaseException:
                conn.rollback()
                raise
    except sqlite3.Error as e:
        raise EventProcessingStateError(
            f"could not sweep captured events into processing state: {type(e).__name__}: {e}",
        ) from e


def resync_from(db_path: str, *, from_inbound_row_id: int = 0) -> int:
    """Rewind the sweep watermark so earlier captured events are re-examined.

    The operator's answer to "the backbone and the capture table disagree".
    Re-sweeping is safe at any time: `UNIQUE (source_id, event_id)` means an
    event already known to the backbone is skipped, whatever state it is in,
    so this can never resurrect a settled event or duplicate one.

    Returns the watermark it set.
    """
    target = max(0, int(from_inbound_row_id))
    try:
        with _connect(db_path, "event_processing_resync") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            try:
                _write_cursor(conn, target)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    except sqlite3.Error as e:
        raise EventProcessingStateError(
            f"could not rewind the event-processing sweep cursor: {type(e).__name__}: {e}",
        ) from e
    return target


# -- claiming ----------------------------------------------------------------


def new_claim_token() -> str:
    return uuid.uuid4().hex


def claim_batch(
    db_path: str,
    *,
    runtime_id: str | None,
    limit: int,
    lease_seconds: int,
    max_attempts: int,
    now_ts: int | None = None,
) -> list[ProcessingRecord]:
    """Take a lease on up to `limit` events belonging to `runtime_id`.

    One `BEGIN IMMEDIATE` transaction doing three things in a fixed order, so
    a pass never races itself or another process:

    1. **Recover expired leases.** Anything still `claimed` past its expiry
       goes back to `captured`. Its spent attempt is *kept*: a process that
       died holding the event may well have died because of it, and a
       crash-loop must be bounded like any other repeated failure.
    2. **Quarantine the exhausted.** Anything `captured` that has already used
       its attempts is moved out of the way before the selection below, so a
       poison event cannot be picked ahead of healthy ones a second time.
    3. **Claim.** Oldest received first, so the queue is fair and the "oldest
       unprocessed event" the health surface reports is a real head-of-line
       age rather than an artefact of insertion order.

    `runtime_id` is matched with `IS`, which is NULL-safe: a process with no
    runtime binding (the single-user local deployment) claims exactly the
    events captured with no binding, and never another runtime's.
    """
    now = int(time.time()) if now_ts is None else int(now_ts)
    now_text = _now_iso()
    lease_until = now + max(1, int(lease_seconds))
    claimed: list[ProcessingRecord] = []

    try:
        with _connect(db_path, "event_processing_claim") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Both housekeeping steps are scoped to this runtime, not
                # just the claim below. Recovering or quarantining another
                # tenant's row is a mutation of state this process has no
                # business touching, even though it never reads its content --
                # isolation that only covers the read is not isolation.
                conn.execute(
                    "UPDATE event_processing SET state = ?, claim_token = NULL, "
                    "claimed_at = NULL, lease_expires_ts = NULL, "
                    "last_error = COALESCE(last_error, ?) "
                    "WHERE state = ? AND runtime_id IS ? AND lease_expires_ts IS NOT NULL "
                    "AND lease_expires_ts <= ?",
                    (
                        STATE_CAPTURED,
                        "claim lease expired before the event was settled; recovered",
                        STATE_CLAIMED,
                        runtime_id,
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE event_processing SET state = ?, settled_at = ?, "
                    "disposition_reason = ?, claim_token = NULL, "
                    "lease_expires_ts = NULL "
                    "WHERE state = ? AND runtime_id IS ? AND attempts >= ?",
                    (
                        STATE_QUARANTINED,
                        now_text,
                        "attempts_exhausted",
                        STATE_CAPTURED,
                        runtime_id,
                        int(max_attempts),
                    ),
                )

                rows = conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM event_processing "  # noqa: S608
                    "WHERE state = ? AND runtime_id IS ? "
                    "ORDER BY received_ts ASC, id ASC LIMIT ?",
                    (STATE_CAPTURED, runtime_id, int(limit)),
                ).fetchall()

                for row in rows:
                    token = new_claim_token()
                    conn.execute(
                        "UPDATE event_processing SET state = ?, claim_token = ?, "
                        "claimed_at = ?, lease_expires_ts = ?, attempts = attempts + 1, "
                        "last_attempt_at = ? WHERE id = ? AND state = ?",
                        (
                            STATE_CLAIMED,
                            token,
                            now_text,
                            lease_until,
                            now_text,
                            row[0],
                            STATE_CAPTURED,
                        ),
                    )
                    record = _row_to_record(row)
                    claimed.append(
                        replace(
                            record,
                            state=STATE_CLAIMED,
                            attempts=record.attempts + 1,
                            claim_token=token,
                            claimed_at=now_text,
                            lease_expires_ts=lease_until,
                            last_attempt_at=now_text,
                        ),
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    except sqlite3.Error as e:
        raise EventProcessingStateError(
            f"could not claim events for processing: {type(e).__name__}: {e}",
        ) from e
    return claimed


# -- settling ----------------------------------------------------------------


def _guarded_update(
    db_path: str,
    row_id: int,
    claim_token: str,
    *,
    sql: str,
    params: tuple[Any, ...],
    label: str,
) -> bool:
    """Apply an update only while this claim still holds the event.

    Every terminal write goes through here. The `claim_token` and
    `state = 'claimed'` guard is what makes a stale worker harmless: if its
    lease expired and another pass took the event, its write matches nothing
    and it is told so, rather than overwriting a decision it no longer owns.
    """
    try:
        with _connect(db_path, label) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            cur = conn.execute(sql, (*params, int(row_id), claim_token, STATE_CLAIMED))
            conn.commit()
            return (cur.rowcount or 0) > 0
    except sqlite3.Error as e:
        raise EventProcessingStateError(
            f"could not update event-processing row {row_id}: {type(e).__name__}: {e}",
        ) from e


def settle(
    db_path: str,
    row_id: int,
    claim_token: str,
    *,
    state: str,
    reason: str,
    result: dict[str, Any] | None = None,
) -> bool:
    """Move a claimed event to a terminal disposition.

    Returns False when the claim no longer holds -- the caller must treat
    that as "someone else owns this now", never as a failure to record.
    """
    if state not in SETTLEABLE_STATES:
        raise ValueError(
            f"state must be one of {sorted(SETTLEABLE_STATES)}, got {state!r}; "
            "quarantine is reached through fail(), not chosen by a handler",
        )
    return _guarded_update(
        db_path,
        row_id,
        claim_token,
        sql=(
            "UPDATE event_processing SET state = ?, settled_at = ?, "
            "disposition_reason = ?, result_json = ?, claim_token = NULL, "
            "lease_expires_ts = NULL, last_error = NULL "
            "WHERE id = ? AND claim_token = ? AND state = ?"
        ),
        params=(
            state,
            _now_iso(),
            reason,
            json.dumps(result, sort_keys=True, default=str) if result is not None else None,
        ),
        label="event_processing_settle",
    )


def fail(
    db_path: str,
    row_id: int,
    claim_token: str,
    *,
    error: str,
    max_attempts: int,
) -> str:
    """Record a failed attempt, and quarantine once attempts are exhausted.

    Returns the state the event is now in (`captured` or `quarantined`), or
    the empty string when the claim no longer held so nothing was written.

    Quarantining here rather than on the next claim matters for the property
    that a poison event does not starve later ones: the event leaves the ready
    queue at the moment its last attempt fails, so the very next pass sees a
    queue containing only work that can still succeed.
    """
    now_text = _now_iso()
    try:
        with _connect(db_path, "event_processing_fail") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Read the attempt count and decide inside the same
                # transaction as the write. Splitting them would let a
                # concurrent recovery pass change the count between the two,
                # which is exactly how a bounded retry stops being bounded.
                row = conn.execute(
                    "SELECT attempts FROM event_processing "
                    "WHERE id = ? AND claim_token = ? AND state = ?",
                    (int(row_id), claim_token, STATE_CLAIMED),
                ).fetchone()
                if row is None:
                    conn.commit()
                    return ""
                exhausted = int(row[0]) >= int(max_attempts)
                if exhausted:
                    conn.execute(
                        "UPDATE event_processing SET state = ?, settled_at = ?, "
                        "disposition_reason = ?, last_error = ?, claim_token = NULL, "
                        "lease_expires_ts = NULL "
                        "WHERE id = ? AND claim_token = ? AND state = ?",
                        (
                            STATE_QUARANTINED,
                            now_text,
                            "attempts_exhausted",
                            error,
                            int(row_id),
                            claim_token,
                            STATE_CLAIMED,
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE event_processing SET state = ?, claim_token = NULL, "
                        "claimed_at = NULL, lease_expires_ts = NULL, last_error = ? "
                        "WHERE id = ? AND claim_token = ? AND state = ?",
                        (
                            STATE_CAPTURED,
                            error,
                            int(row_id),
                            claim_token,
                            STATE_CLAIMED,
                        ),
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    except sqlite3.Error as e:
        raise EventProcessingStateError(
            f"could not record a failed processing attempt for row {row_id}: "
            f"{type(e).__name__}: {e}",
        ) from e
    return STATE_QUARANTINED if exhausted else STATE_CAPTURED


def release(
    db_path: str,
    row_id: int,
    claim_token: str,
    *,
    reason: str,
    refund_attempt: bool = True,
) -> bool:
    """Put a claimed event back without holding its attempt against it.

    The Parking Brake path, and the batch-deadline path. Neither is the
    event's fault, and neither may bring it closer to quarantine -- a halt
    that quietly consumed the backlog's retries would be a halt that destroyed
    the backlog it was supposed to preserve.
    """
    attempt_expr = "attempts = MAX(0, attempts - 1), " if refund_attempt else ""
    return _guarded_update(
        db_path,
        row_id,
        claim_token,
        sql=(
            f"UPDATE event_processing SET state = ?, {attempt_expr}"  # noqa: S608
            "claim_token = NULL, claimed_at = NULL, lease_expires_ts = NULL, "
            "last_error = ? WHERE id = ? AND claim_token = ? AND state = ?"
        ),
        params=(STATE_CAPTURED, reason),
        label="event_processing_release",
    )


def current_attempts(db_path: str, row_id: int) -> int:
    with _connect(db_path, "event_processing_attempts") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        row = conn.execute(
            "SELECT attempts FROM event_processing WHERE id = ?",
            (int(row_id),),
        ).fetchone()
    return int(row[0]) if row else 0


# -- reading -----------------------------------------------------------------


def get(db_path: str, source_id: str, event_id: str) -> ProcessingRecord | None:
    if not table_exists(db_path):
        return None
    with _connect(db_path, "event_processing_get") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM event_processing "  # noqa: S608
            "WHERE source_id = ? AND event_id = ?",
            (source_id, event_id),
        ).fetchone()
    return _row_to_record(row) if row else None


def list_by_state(
    db_path: str,
    state: str,
    *,
    limit: int = 50,
    runtime_id: str | None = None,
    any_runtime: bool = True,
) -> list[ProcessingRecord]:
    if not table_exists(db_path):
        return []
    sql = f"SELECT {_SELECT_COLUMNS} FROM event_processing WHERE state = ?"  # noqa: S608
    params: list[Any] = [state]
    if not any_runtime:
        sql += " AND runtime_id IS ?"
        params.append(runtime_id)
    sql += " ORDER BY received_ts ASC, id ASC LIMIT ?"
    params.append(int(limit))
    with _connect(db_path, "event_processing_list") as conn:
        conn.execute("PRAGMA busy_timeout = 3000")
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_row_to_record(r) for r in rows]


def pending_count(db_path: str) -> int:
    """Non-terminal events. The number backpressure is measured against."""
    if not table_exists(db_path):
        return 0
    try:
        with _connect(db_path, "event_processing_pending_count") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            row = conn.execute(
                "SELECT COUNT(*) FROM event_processing WHERE state IN (?, ?)",
                (STATE_CAPTURED, STATE_CLAIMED),
            ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error as e:
        raise EventProcessingStateError(
            f"could not read the event-processing backlog size: {type(e).__name__}: {e}",
        ) from e


# -- operator recovery -------------------------------------------------------


def requeue(
    db_path: str,
    *,
    source_id: str | None = None,
    event_id: str | None = None,
    from_states: tuple[str, ...] = (STATE_QUARANTINED, STATE_REFUSED),
    reset_attempts: bool = True,
    limit: int = 100,
) -> int:
    """Put quarantined or refused events back in the ready queue.

    The documented recovery action, and a deliberate one: it names the states
    it will move and, by default, both of them, because the two operator
    situations are the same shape -- "the reason it stopped has been fixed"
    (a handler bug, a missing registration, a policy entry that should have
    been there).

    `processed` and `irrelevant` are not requeueable and are deliberately not
    in the default: those are decisions that were *reached*, and re-running
    them would put the system back in front of a question it already answered.
    Pass them explicitly if a repair genuinely requires it.

    Attempts are reset by default; a requeue that kept the exhausted counter
    would quarantine again on its first pass, which is not what an operator
    asking for a retry means.

    Returns the number of rows moved.
    """
    invalid = [s for s in from_states if s not in ALL_STATES]
    if invalid:
        raise ValueError(f"unknown processing state(s): {invalid}")
    if not from_states:
        return 0

    placeholders = ", ".join("?" for _ in from_states)
    where = [f"state IN ({placeholders})"]
    params: list[Any] = list(from_states)
    if source_id is not None:
        where.append("source_id = ?")
        params.append(source_id)
    if event_id is not None:
        where.append("event_id = ?")
        params.append(event_id)

    attempts_expr = "attempts = 0, " if reset_attempts else ""
    try:
        with _connect(db_path, "event_processing_requeue") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            try:
                ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM event_processing "  # noqa: S608
                        f"WHERE {' AND '.join(where)} ORDER BY id ASC LIMIT ?",
                        (*params, int(limit)),
                    ).fetchall()
                ]
                if not ids:
                    conn.commit()
                    return 0
                id_placeholders = ", ".join("?" for _ in ids)
                cur = conn.execute(
                    "UPDATE event_processing SET state = ?, "  # noqa: S608
                    f"{attempts_expr}"
                    "settled_at = NULL, disposition_reason = ?, claim_token = NULL, "
                    "claimed_at = NULL, lease_expires_ts = NULL, result_json = NULL "
                    f"WHERE id IN ({id_placeholders})",
                    (STATE_CAPTURED, "requeued_by_operator", *ids),
                )
                conn.commit()
                moved = cur.rowcount or 0
            except BaseException:
                conn.rollback()
                raise
    except sqlite3.Error as e:
        raise EventProcessingStateError(
            f"could not requeue event-processing rows: {type(e).__name__}: {e}",
        ) from e
    logger.warning(
        "Event processing: %s event(s) requeued by operator action from %s",
        moved,
        ", ".join(from_states),
    )
    return moved


__all__ = [
    "ALL_STATES",
    "PENDING_STATES",
    "SCHEMA",
    "SETTLEABLE_STATES",
    "STATE_CAPTURED",
    "STATE_CLAIMED",
    "STATE_IRRELEVANT",
    "STATE_PROCESSED",
    "STATE_QUARANTINED",
    "STATE_REFUSED",
    "TERMINAL_STATES",
    "EventProcessingStateError",
    "ProcessingRecord",
    "claim_batch",
    "current_attempts",
    "ensure_schema",
    "fail",
    "get",
    "list_by_state",
    "new_claim_token",
    "pending_count",
    "release",
    "requeue",
    "resync_from",
    "settle",
    "sweep_captured",
    "table_exists",
]
