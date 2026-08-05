"""
Stage 1, S1.4: the `awaiting_response` obligation queue.

Persistence for `COGNITIVE_RUNTIME.md`'s `awaiting_response` state -- matters
the user or Bartholomew is waiting on a reply for, so they aren't silently
forgotten. See docs/S1_4_AWAITING_RESPONSE_DESIGN.md for the full design;
this module implements that design's schema and state machine.

Deliberately synchronous, mirroring
bartholomew.orchestrator.safety.governance_store's own module docstring
rationale: this class is not called from the event loop directly anywhere --
every real call site routes it through
bartholomew.kernel.blocking_executor.run_off_loop(), the same off-loop
discipline Phase B stage B2 established for every other daemon-owned
blocking SQLite call.

State machine (see design doc Sec 4):
    opened --(reminder due)--> reminded --(still overdue)--> escalated
       \\                          \\                            \\
        \\--------------------(reply received or explicit resolve)-> resolved

Every transition writes its state change and an `awaiting_response_audit`
row in one transaction -- the two are never observable independently,
mirroring GovernanceStore's own atomic state+audit write.
"""

from __future__ import annotations

import calendar
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from bartholomew.kernel.db_ctx import connect, set_wal_pragmas

VALID_STATUSES = frozenset({"open", "reminded", "escalated", "resolved"})
VALID_RESOLUTIONS = frozenset({"reply_received", "user_dismissed", "manual"})

# Proposed starting defaults (design doc Sec 6, explicitly flagged there as
# an implementation/approval-time choice, not a design blocker).
DEFAULT_DUE_OFFSET_S = 24 * 3600
REMINDER_INTERVAL_S = 24 * 3600
ESCALATE_AFTER_REMINDERS = 3
ESCALATE_AFTER_OVERDUE_S = 72 * 3600


class AwaitingResponseNotFoundError(RuntimeError):
    """Raised when a transition targets an entry_id that doesn't exist."""


class InvalidTransitionError(RuntimeError):
    """Raised when a transition is attempted against an entry already in a
    terminal state (resolved), or with an invalid resolution value."""


@dataclass(frozen=True)
class AwaitingResponseEntry:
    id: int
    subject: str
    origin_surface: str
    context_ref: str | None
    status: str
    opened_at: str
    due_at: str | None
    reminder_count: int
    last_reminded_at: str | None
    escalated_at: str | None
    resolved_at: str | None
    resolution: str | None
    actor: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "origin_surface": self.origin_surface,
            "context_ref": self.context_ref,
            "status": self.status,
            "opened_at": self.opened_at,
            "due_at": self.due_at,
            "reminder_count": self.reminder_count,
            "last_reminded_at": self.last_reminded_at,
            "escalated_at": self.escalated_at,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
            "actor": self.actor,
        }


def ensure_schema(db_path: str) -> None:
    """Create awaiting_response / awaiting_response_audit if missing.
    Safe to call every time an AwaitingResponseStore is constructed."""
    conn = connect(db_path)
    try:
        set_wal_pragmas(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS awaiting_response (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                origin_surface TEXT NOT NULL,
                context_ref TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                opened_at TEXT NOT NULL,
                due_at TEXT,
                reminder_count INTEGER NOT NULL DEFAULT 0,
                last_reminded_at TEXT,
                escalated_at TEXT,
                resolved_at TEXT,
                resolution TEXT,
                actor TEXT
            )
            """,
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_awaiting_response_status
                ON awaiting_response(status, due_at)
            """,
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS awaiting_response_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL REFERENCES awaiting_response(id),
                ts TEXT NOT NULL,
                transition TEXT NOT NULL,
                actor TEXT,
                detail TEXT
            )
            """,
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_entry(row: sqlite3.Row) -> AwaitingResponseEntry:
    return AwaitingResponseEntry(
        id=row["id"],
        subject=row["subject"],
        origin_surface=row["origin_surface"],
        context_ref=row["context_ref"],
        status=row["status"],
        opened_at=row["opened_at"],
        due_at=row["due_at"],
        reminder_count=row["reminder_count"],
        last_reminded_at=row["last_reminded_at"],
        escalated_at=row["escalated_at"],
        resolved_at=row["resolved_at"],
        resolution=row["resolution"],
        actor=row["actor"],
    )


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class AwaitingResponseStore:
    """Isolated, tested persistence for the awaiting_response obligation
    queue. Not itself governed -- see runtime_contract.py's
    run_awaiting_response_through_runtime_contract(), the sole intended
    caller of every mutating method here (design doc's non-negotiable
    invariant: no direct store write from a route or skill bypasses the
    Runtime Contract seam)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        ensure_schema(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, entry_id: int) -> AwaitingResponseEntry | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM awaiting_response WHERE id = ?",
                (entry_id,),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_entry(row) if row is not None else None

    def list(self, status: str | None = None, limit: int = 50) -> list[AwaitingResponseEntry]:
        conn = self._connect()
        try:
            if status is not None:
                rows = conn.execute(
                    "SELECT * FROM awaiting_response WHERE status = ? ORDER BY id DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM awaiting_response ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        finally:
            conn.close()
        return [_row_to_entry(row) for row in rows]

    def list_open_by_origin(self, origin_surface: str) -> list[AwaitingResponseEntry]:
        """Every non-terminal entry for a given origin surface -- used by
        the narrow same-surface auto-resolution heuristic (design doc Sec
        7): only auto-resolves when exactly one such entry exists."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM awaiting_response "
                "WHERE origin_surface = ? AND status != 'resolved' "
                "ORDER BY id ASC",
                (origin_surface,),
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_entry(row) for row in rows]

    def list_due_for_transition(self, now_ts: int) -> list[AwaitingResponseEntry]:
        """Entries in ('open', 'reminded') whose due_at has passed and are
        due for their next reminder/escalation -- reminders repeat every
        REMINDER_INTERVAL_S while an entry stays overdue and unresolved."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM awaiting_response "
                "WHERE status IN ('open', 'reminded') AND due_at IS NOT NULL "
                "ORDER BY due_at ASC",
            ).fetchall()
        finally:
            conn.close()
        entries = [_row_to_entry(row) for row in rows]
        due = []
        for entry in entries:
            due_ts = _parse_iso(entry.due_at)
            if due_ts is None or due_ts > now_ts:
                continue
            last = _parse_iso(entry.last_reminded_at) if entry.last_reminded_at else None
            if last is not None and (now_ts - last) < REMINDER_INTERVAL_S:
                continue
            due.append(entry)
        return due

    def next_transition_for(self, entry: AwaitingResponseEntry, now_ts: int) -> str:
        """'escalate' if the entry has already hit the reminder-count or
        elapsed-overdue-time threshold (design doc Sec 6's proposed
        defaults), else 'remind'."""
        due_ts = _parse_iso(entry.due_at) if entry.due_at else None
        overdue_s = (now_ts - due_ts) if due_ts is not None else 0
        if (
            entry.reminder_count >= ESCALATE_AFTER_REMINDERS
            or overdue_s >= ESCALATE_AFTER_OVERDUE_S
        ):
            return "escalate"
        return "remind"

    def list_audit(self, entry_id: int, limit: int = 50) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, entry_id, ts, transition, actor, detail "
                "FROM awaiting_response_audit WHERE entry_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (entry_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "id": row["id"],
                "entry_id": row["entry_id"],
                "ts": row["ts"],
                "transition": row["transition"],
                "actor": row["actor"],
                "detail": row["detail"],
            }
            for row in rows
        ]

    def open(
        self,
        *,
        subject: str,
        origin_surface: str,
        context_ref: str | None = None,
        due_at: str | None = None,
        actor: str | None = None,
    ) -> AwaitingResponseEntry:
        now = _now_iso()
        if due_at is None:
            due_at = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(time.time() + DEFAULT_DUE_OFFSET_S),
            )
        conn = self._connect()
        try:
            set_wal_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO awaiting_response "
                "(subject, origin_surface, context_ref, status, opened_at, due_at, actor) "
                "VALUES (?, ?, ?, 'open', ?, ?, ?)",
                (subject, origin_surface, context_ref, now, due_at, actor),
            )
            entry_id = cur.lastrowid
            conn.execute(
                "INSERT INTO awaiting_response_audit (entry_id, ts, transition, actor, detail) "
                "VALUES (?, ?, 'opened', ?, ?)",
                (entry_id, now, actor, subject),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get(entry_id)

    def remind(self, entry_id: int, *, actor: str | None = None) -> AwaitingResponseEntry:
        return self._advance(entry_id, "reminded", "reminded", actor=actor)

    def escalate(self, entry_id: int, *, actor: str | None = None) -> AwaitingResponseEntry:
        return self._advance(entry_id, "escalated", "escalated", actor=actor)

    def resolve(
        self,
        entry_id: int,
        *,
        resolution: str,
        actor: str | None = None,
    ) -> AwaitingResponseEntry:
        if resolution not in VALID_RESOLUTIONS:
            raise InvalidTransitionError(
                f"resolution must be one of {sorted(VALID_RESOLUTIONS)}, got {resolution!r}",
            )
        return self._advance(entry_id, "resolved", "resolved", actor=actor, resolution=resolution)

    def _advance(
        self,
        entry_id: int,
        new_status: str,
        transition: str,
        *,
        actor: str | None,
        resolution: str | None = None,
    ) -> AwaitingResponseEntry:
        now = _now_iso()
        conn = self._connect()
        try:
            set_wal_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM awaiting_response WHERE id = ?",
                (entry_id,),
            ).fetchone()
            if row is None:
                raise AwaitingResponseNotFoundError(f"No awaiting_response entry {entry_id}")
            if row["status"] == "resolved":
                raise InvalidTransitionError(
                    f"awaiting_response entry {entry_id} is already resolved (terminal)",
                )

            if new_status == "reminded":
                conn.execute(
                    "UPDATE awaiting_response SET status = ?, reminder_count = reminder_count + 1, "
                    "last_reminded_at = ?, actor = ? WHERE id = ?",
                    (new_status, now, actor, entry_id),
                )
            elif new_status == "escalated":
                conn.execute(
                    "UPDATE awaiting_response SET status = ?, escalated_at = ?, actor = ? "
                    "WHERE id = ?",
                    (new_status, now, actor, entry_id),
                )
            elif new_status == "resolved":
                conn.execute(
                    "UPDATE awaiting_response SET status = ?, resolved_at = ?, resolution = ?, "
                    "actor = ? WHERE id = ?",
                    (new_status, now, resolution, actor, entry_id),
                )
            else:  # pragma: no cover - defensive, all callers use the three above
                raise ValueError(f"Unknown new_status {new_status!r}")

            conn.execute(
                "INSERT INTO awaiting_response_audit (entry_id, ts, transition, actor, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (entry_id, now, transition, actor, resolution),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get(entry_id)


def _parse_iso(value: str | None) -> int | None:
    """Parse a 'YYYY-MM-DDTHH:MM:SSZ' timestamp (always UTC, matching
    _now_iso()'s own format) to a Unix epoch second count. Returns None for
    an unparseable/missing value -- treated as "no due date" by callers
    rather than raising."""
    if not value:
        return None
    try:
        return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None
