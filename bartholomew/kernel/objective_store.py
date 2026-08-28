"""
Golden Path slice 2: durable objectives.

Persistence for what the user is actually trying to *achieve*, so Bartholomew
can stay responsible for the outcome across interactions, restarts and time --
rather than making the user re-establish the context every time. See
docs/GOLDEN_PATH_SLICE_2_OBJECTIVE_CONTINUITY.md for the slice note.

An objective is not a task and not an `awaiting_response` entry, and this is
deliberately a third table rather than a reuse of either:

  * a **task** (`bartholomew.skills.tasks`) is a unit of work with a due date.
    "Ring the roofer" is a task. It carries no notion of what doing it was
    *for*, and completing it says nothing about whether the roof got fixed.
  * an **awaiting_response** entry is an obligation waiting on someone else's
    reply, with a reminder/escalation ladder ending in "chase this". Its
    terminal state means "the reply arrived", not "the outcome happened".
  * an **objective** is a desired outcome with a horizon, a history of what
    has actually happened around it, and a completion that means Bartholomew
    should stop caring. Nothing above has that shape.

What it does reuse, exactly: this module's *discipline*. Same synchronous
sqlite3 through `db_ctx.connect()` + `set_wal_pragmas()` (never a bare
`sqlite3.connect()` -- pinned by tests/test_audit_write_integrity.py), same
`BEGIN IMMEDIATE` state-change-and-its-event-row-in-one-transaction as
`awaiting_response_store` and `GovernanceStore`, and the same rule that every
real call site routes it through `blocking_executor.run_off_loop()` (the B2/B8
off-loop discipline). No new database, no second scheduler, no new persistence
subsystem.

State machine:

    active --(block)--> blocked --(unblock)--> active
       \\                    \\
        \\--------------------+--(complete)--> completed   [terminal]
        \\--------------------+--(abandon)---> abandoned   [terminal]

**Terminal means terminal.** `record`, `surface`, `block`, `unblock`,
`complete` and `abandon` all raise `InvalidTransitionError` against an
objective in a terminal state. That is the structural half of "once the
objective is complete, the user should not have to tell Bartholomew to stop
remembering it" -- the other halves being the re-engagement drive's
active-only query and the chat interpretation block's active-only listing.
Three independent stops, because a completed objective that keeps resurfacing
is the single worst outcome this slice can produce.

`objective_events` and the separation it enforces
--------------------------------------------------
The event log is append-only and every row is classified by `event_kind`,
constrained by the schema itself rather than by convention:

  * ``fact``          -- evidence. Something observed or obtained, carrying
                         its `provenance` where it came from outside (e.g. a
                         forecast lookup's provider block, `evidence: True`).
  * ``decision``      -- the user decided something.
  * ``action``        -- something was actually performed.
  * ``proposal``      -- a possible next step. **Hypothetical.**
  * ``state_change``  -- the objective's own lifecycle.
  * ``surfaced``      -- Bartholomew raised the objective with the user.

The `proposal` kind exists so that reasoning about what *might* be done can be
recorded without that reasoning becoming evidence that it *was* done, or that
it is true. `evidence_events()` excludes it structurally -- not by a filter a
caller could forget -- because the alternative is a system that eventually
tells the user it did something it only ever considered doing.

Identity (Session B seam)
-------------------------
`subject_ref` is nullable and is written by nothing in this slice. It exists
so that when authentication/tenancy lands, objectives can be attributed
without a schema migration. Single-user semantics are assumed here, and no
part of this module infers, invents or enforces an identity model.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from bartholomew.kernel.db_ctx import connect, set_wal_pragmas

#: Lifecycle states. `completed` and `abandoned` are terminal.
STATUS_ACTIVE = "active"
STATUS_BLOCKED = "blocked"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"

VALID_STATUSES = frozenset({STATUS_ACTIVE, STATUS_BLOCKED, STATUS_COMPLETED, STATUS_ABANDONED})

#: Once an objective is in one of these, nothing further happens to it, ever.
TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_ABANDONED})

#: How an objective ended. `achieved` is the outcome the user wanted;
#: `no_longer_needed` is a real and common ending that is not a failure; and
#: `abandoned` is giving up on it. All three stop resurfacing identically --
#: the distinction is for the record, not for behaviour.
RESOLUTION_ACHIEVED = "achieved"
RESOLUTION_NO_LONGER_NEEDED = "no_longer_needed"
RESOLUTION_ABANDONED = "abandoned"

VALID_RESOLUTIONS = frozenset(
    {RESOLUTION_ACHIEVED, RESOLUTION_NO_LONGER_NEEDED, RESOLUTION_ABANDONED},
)

#: Event classification. See the module docstring: this is the structural
#: separation between what is known, what was decided, what was done, and what
#: was merely contemplated.
EVENT_FACT = "fact"
EVENT_DECISION = "decision"
EVENT_ACTION = "action"
EVENT_PROPOSAL = "proposal"
EVENT_STATE_CHANGE = "state_change"
EVENT_SURFACED = "surfaced"

VALID_EVENT_KINDS = frozenset(
    {
        EVENT_FACT,
        EVENT_DECISION,
        EVENT_ACTION,
        EVENT_PROPOSAL,
        EVENT_STATE_CHANGE,
        EVENT_SURFACED,
    },
)

#: The kinds that constitute *what is actually so* about an objective.
#: `proposal` is deliberately absent, and `surfaced`/`state_change` are
#: bookkeeping rather than substance.
EVIDENCE_EVENT_KINDS = frozenset({EVENT_FACT, EVENT_DECISION, EVENT_ACTION})

#: Horizon forms. Deliberately three, not a scheduling grammar: a date, a
#: named soft window, or none at all. "This week" is what the roofer example
#: actually says, and refusing to turn it into a false precise date is the
#: point.
HORIZON_BY_DATE = "by_date"
HORIZON_THIS_WEEK = "this_week"
HORIZON_OPEN = "open"

VALID_HORIZON_KINDS = frozenset({HORIZON_BY_DATE, HORIZON_THIS_WEEK, HORIZON_OPEN})


class ObjectiveNotFoundError(RuntimeError):
    """Raised when a transition targets an objective_id that doesn't exist."""


class InvalidTransitionError(RuntimeError):
    """Raised when a transition is attempted against an objective already in a
    terminal state (completed/abandoned), or with an invalid value."""


@dataclass(frozen=True)
class ObjectiveEvent:
    """One append-only entry in an objective's history."""

    id: int
    objective_id: int
    occurred_at: str
    event_kind: str
    summary: str
    provenance: dict[str, Any] = field(default_factory=dict)
    actor: str | None = None

    @property
    def is_evidence(self) -> bool:
        """Whether this row says something is *so*.

        A proposal never does, however plausible it reads."""
        return self.event_kind in EVIDENCE_EVENT_KINDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective_id": self.objective_id,
            "occurred_at": self.occurred_at,
            "event_kind": self.event_kind,
            "summary": self.summary,
            "provenance": dict(self.provenance),
            "actor": self.actor,
            "is_evidence": self.is_evidence,
        }


@dataclass(frozen=True)
class Objective:
    """A desired outcome Bartholomew stays responsible for."""

    id: int
    title: str
    outcome_statement: str | None
    horizon_kind: str
    horizon_date: str | None
    status: str
    opened_at: str
    updated_at: str
    last_surfaced_at: str | None
    #: The id of the `surfaced` event written when this objective was last
    #: raised. The "what changed since last time" window is taken from this
    #: rather than from `last_surfaced_at`, because ISO timestamps here are
    #: second-granular and several events genuinely can share a second --
    #: in which case a timestamp window silently drops or repeats them.
    last_surfaced_event_id: int | None
    completed_at: str | None
    resolution: str | None
    outcome_note: str | None
    #: Session B seam. Nullable, written by nothing in this slice.
    subject_ref: str | None
    actor: str | None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "outcome_statement": self.outcome_statement,
            "horizon_kind": self.horizon_kind,
            "horizon_date": self.horizon_date,
            "status": self.status,
            "opened_at": self.opened_at,
            "updated_at": self.updated_at,
            "last_surfaced_at": self.last_surfaced_at,
            "last_surfaced_event_id": self.last_surfaced_event_id,
            "completed_at": self.completed_at,
            "resolution": self.resolution,
            "outcome_note": self.outcome_note,
            "subject_ref": self.subject_ref,
            "actor": self.actor,
            "is_terminal": self.is_terminal,
        }


def ensure_schema(db_path: str) -> None:
    """Create objectives / objective_events if missing.
    Safe to call every time an ObjectiveStore is constructed."""
    conn = connect(db_path)
    try:
        set_wal_pragmas(conn)
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS objectives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                outcome_statement TEXT,
                horizon_kind TEXT NOT NULL DEFAULT '{HORIZON_OPEN}'
                    CHECK (horizon_kind IN
                        ('{HORIZON_BY_DATE}', '{HORIZON_THIS_WEEK}', '{HORIZON_OPEN}')),
                horizon_date TEXT,
                status TEXT NOT NULL DEFAULT '{STATUS_ACTIVE}'
                    CHECK (status IN
                        ('{STATUS_ACTIVE}', '{STATUS_BLOCKED}',
                         '{STATUS_COMPLETED}', '{STATUS_ABANDONED}')),
                opened_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_surfaced_at TEXT,
                last_surfaced_event_id INTEGER,
                completed_at TEXT,
                resolution TEXT,
                outcome_note TEXT,
                subject_ref TEXT,
                actor TEXT
            )
            """,
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_objectives_status
                ON objectives(status, horizon_date)
            """,
        )
        # The event_kind CHECK is the structural half of the fact/decision/
        # action/proposal separation. A caller cannot write an unclassified
        # row, and cannot invent a kind that `evidence_events()` would then
        # have to guess about.
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS objective_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                objective_id INTEGER NOT NULL REFERENCES objectives(id),
                occurred_at TEXT NOT NULL,
                event_kind TEXT NOT NULL
                    CHECK (event_kind IN
                        ('{EVENT_FACT}', '{EVENT_DECISION}', '{EVENT_ACTION}',
                         '{EVENT_PROPOSAL}', '{EVENT_STATE_CHANGE}', '{EVENT_SURFACED}')),
                summary TEXT NOT NULL,
                provenance_json TEXT,
                actor TEXT
            )
            """,
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_objective_events_objective
                ON objective_events(objective_id, occurred_at)
            """,
        )
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _row_to_objective(row: sqlite3.Row) -> Objective:
    return Objective(
        id=row["id"],
        title=row["title"],
        outcome_statement=row["outcome_statement"],
        horizon_kind=row["horizon_kind"],
        horizon_date=row["horizon_date"],
        status=row["status"],
        opened_at=row["opened_at"],
        updated_at=row["updated_at"],
        last_surfaced_at=row["last_surfaced_at"],
        last_surfaced_event_id=row["last_surfaced_event_id"],
        completed_at=row["completed_at"],
        resolution=row["resolution"],
        outcome_note=row["outcome_note"],
        subject_ref=row["subject_ref"],
        actor=row["actor"],
    )


def _row_to_event(row: sqlite3.Row) -> ObjectiveEvent:
    raw = row["provenance_json"]
    try:
        provenance = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        # A record whose provenance cannot be read is reported with none
        # rather than with a guess. The event itself is not discarded.
        provenance = {}
    if not isinstance(provenance, dict):
        provenance = {}
    return ObjectiveEvent(
        id=row["id"],
        objective_id=row["objective_id"],
        occurred_at=row["occurred_at"],
        event_kind=row["event_kind"],
        summary=row["summary"],
        provenance=provenance,
        actor=row["actor"],
    )


class ObjectiveStore:
    """Durable objectives and their append-only history.

    Deliberately synchronous, for the same reason `AwaitingResponseStore` and
    `GovernanceStore` are: every real call site routes it through
    `blocking_executor.run_off_loop()`, so the blocking SQLite work never
    touches the event loop.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        ensure_schema(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------------------------------------------------------------- reads

    def get(self, objective_id: int) -> Objective | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM objectives WHERE id = ?",
                (objective_id,),
            ).fetchone()
            return _row_to_objective(row) if row else None
        finally:
            conn.close()

    def list(self, status: str | None = None, limit: int = 50) -> list[Objective]:
        conn = self._connect()
        try:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM objectives ORDER BY opened_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM objectives WHERE status = ? ORDER BY opened_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            return [_row_to_objective(r) for r in rows]
        finally:
            conn.close()

    def list_live(self, limit: int = 50) -> list[Objective]:
        """Objectives Bartholomew is still responsible for: active or blocked.

        The single read every re-engagement path uses. A terminal objective
        is structurally out of reach here -- not filtered out downstream,
        where a later caller could forget to filter."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM objectives WHERE status IN (?, ?) "
                "ORDER BY horizon_date IS NULL, horizon_date ASC, opened_at ASC LIMIT ?",
                (STATUS_ACTIVE, STATUS_BLOCKED, limit),
            ).fetchall()
            return [_row_to_objective(r) for r in rows]
        finally:
            conn.close()

    def events(
        self,
        objective_id: int,
        *,
        after_event_id: int | None = None,
        kinds: frozenset[str] | set[str] | None = None,
        limit: int = 200,
    ) -> list[ObjectiveEvent]:
        """This objective's history, oldest first.

        `after_event_id` is how "what changed since I last raised this" is
        derived rather than stored -- events strictly after the one named,
        normally the objective's `last_surfaced_event_id`. Nothing summarises
        the history into prose and keeps that prose: a stored summary is a
        fabrication the moment the events move on.

        The window is by event id, not by timestamp, deliberately.
        `occurred_at` is second-granular, and several events can genuinely
        share a second -- a user says something, evidence arrives and the
        objective is surfaced, all inside the same tick. A timestamp window
        would then either drop a real change or repeat one already reported,
        and both failures look exactly like the system being unreliable
        about the user's own history.
        """
        clauses = ["objective_id = ?"]
        params: list[Any] = [objective_id]
        if after_event_id is not None:
            clauses.append("id > ?")
            params.append(after_event_id)
        if kinds is not None:
            if not kinds:
                return []
            clauses.append(f"event_kind IN ({','.join('?' * len(kinds))})")
            params.extend(sorted(kinds))
        params.append(limit)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM objective_events WHERE {' AND '.join(clauses)} "  # noqa: S608
                "ORDER BY occurred_at ASC, id ASC LIMIT ?",
                params,
            ).fetchall()
            return [_row_to_event(r) for r in rows]
        finally:
            conn.close()

    def evidence_events(
        self,
        objective_id: int,
        *,
        after_event_id: int | None = None,
        limit: int = 200,
    ) -> list[ObjectiveEvent]:
        """Only what is actually so: facts, decisions and performed actions.

        Proposals are excluded **structurally**, here, rather than by a
        filter each caller has to remember. A considered next step is not a
        thing that happened, and the difference between those two is the
        difference between a useful assistant and one that confidently
        reports work it never did."""
        return self.events(
            objective_id,
            after_event_id=after_event_id,
            kinds=EVIDENCE_EVENT_KINDS,
            limit=limit,
        )

    # --------------------------------------------------------------- writes

    def open(
        self,
        *,
        title: str,
        outcome_statement: str | None = None,
        horizon_kind: str = HORIZON_OPEN,
        horizon_date: str | None = None,
        subject_ref: str | None = None,
        actor: str | None = None,
    ) -> Objective:
        """Record a new objective, with its opening state_change event."""
        if not title or not title.strip():
            raise ValueError("an objective needs a title")
        if horizon_kind not in VALID_HORIZON_KINDS:
            raise ValueError(
                f"horizon_kind must be one of {sorted(VALID_HORIZON_KINDS)}, got {horizon_kind!r}",
            )
        now = _now_iso()
        conn = self._connect()
        try:
            set_wal_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO objectives "
                "(title, outcome_statement, horizon_kind, horizon_date, status, "
                " opened_at, updated_at, subject_ref, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    title.strip(),
                    outcome_statement,
                    horizon_kind,
                    horizon_date,
                    STATUS_ACTIVE,
                    now,
                    now,
                    subject_ref,
                    actor,
                ),
            )
            objective_id = cur.lastrowid
            self._insert_event(
                conn,
                objective_id,
                now,
                EVENT_STATE_CHANGE,
                f"objective opened: {title.strip()}",
                {"status": STATUS_ACTIVE},
                actor,
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get(objective_id)

    def record(
        self,
        objective_id: int,
        *,
        event_kind: str,
        summary: str,
        provenance: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> ObjectiveEvent:
        """Append one classified event to a live objective's history.

        `event_kind` is required and has no default: a caller must say
        whether it is recording something that is so, something decided,
        something done, or something merely proposed. There is no
        unclassified write path."""
        if event_kind not in VALID_EVENT_KINDS:
            raise ValueError(
                f"event_kind must be one of {sorted(VALID_EVENT_KINDS)}, got {event_kind!r}",
            )
        if event_kind == EVENT_STATE_CHANGE:
            # State changes belong to the transitions that actually cause
            # them, written in the same transaction. Letting a caller write a
            # free-standing one would let the history claim a lifecycle
            # change the objective never underwent.
            raise ValueError(
                "state_change events are written by the transitions themselves, not by record()",
            )
        if not summary or not summary.strip():
            raise ValueError("an objective event needs a summary")

        now = _now_iso()
        conn = self._connect()
        try:
            set_wal_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")
            self._require_live(conn, objective_id, "record an event against")
            event_id = self._insert_event(
                conn,
                objective_id,
                now,
                event_kind,
                summary.strip(),
                provenance,
                actor,
            )
            conn.execute(
                "UPDATE objectives SET updated_at = ? WHERE id = ?",
                (now, objective_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self._get_event(event_id)

    def surface(self, objective_id: int, *, actor: str | None = None) -> Objective:
        """Mark that Bartholomew has just raised this objective with the user.

        `last_surfaced_at` and the `surfaced` event move in one transaction,
        so the "what changed since last time" window cannot drift from the
        record of when last time was."""
        now = _now_iso()
        conn = self._connect()
        try:
            set_wal_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")
            self._require_live(conn, objective_id, "surface")
            conn.execute(
                "UPDATE objectives SET last_surfaced_at = ?, updated_at = ? WHERE id = ?",
                (now, now, objective_id),
            )
            event_id = self._insert_event(
                conn,
                objective_id,
                now,
                EVENT_SURFACED,
                "raised with the user",
                None,
                actor,
            )
            conn.execute(
                "UPDATE objectives SET last_surfaced_event_id = ? WHERE id = ?",
                (event_id, objective_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get(objective_id)

    def block(
        self,
        objective_id: int,
        *,
        reason: str,
        actor: str | None = None,
    ) -> Objective:
        """Something is in the way. Still Bartholomew's responsibility."""
        return self._set_status(
            objective_id,
            STATUS_BLOCKED,
            summary=f"blocked: {reason}",
            actor=actor,
        )

    def unblock(self, objective_id: int, *, actor: str | None = None) -> Objective:
        return self._set_status(
            objective_id,
            STATUS_ACTIVE,
            summary="no longer blocked",
            actor=actor,
        )

    def complete(
        self,
        objective_id: int,
        *,
        resolution: str = RESOLUTION_ACHIEVED,
        outcome_note: str | None = None,
        actor: str | None = None,
    ) -> Objective:
        """The outcome happened (or stopped being wanted). **Terminal.**

        After this, every other transition raises, `list_live()` no longer
        returns it, and nothing resurfaces it -- which is the whole promise:
        the user should not have to tell Bartholomew to stop remembering
        something that is done."""
        if resolution not in VALID_RESOLUTIONS:
            raise InvalidTransitionError(
                f"resolution must be one of {sorted(VALID_RESOLUTIONS)}, got {resolution!r}",
            )
        return self._set_status(
            objective_id,
            STATUS_COMPLETED,
            summary=f"completed ({resolution})",
            actor=actor,
            resolution=resolution,
            outcome_note=outcome_note,
        )

    def abandon(
        self,
        objective_id: int,
        *,
        outcome_note: str | None = None,
        actor: str | None = None,
    ) -> Objective:
        """Given up on. **Terminal**, and as quiet afterwards as completion."""
        return self._set_status(
            objective_id,
            STATUS_ABANDONED,
            summary="abandoned",
            actor=actor,
            resolution=RESOLUTION_ABANDONED,
            outcome_note=outcome_note,
        )

    # -------------------------------------------------------------- helpers

    def _set_status(
        self,
        objective_id: int,
        new_status: str,
        *,
        summary: str,
        actor: str | None,
        resolution: str | None = None,
        outcome_note: str | None = None,
    ) -> Objective:
        now = _now_iso()
        conn = self._connect()
        try:
            set_wal_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")
            self._require_live(conn, objective_id, "change the status of")
            if new_status in TERMINAL_STATUSES:
                conn.execute(
                    "UPDATE objectives SET status = ?, completed_at = ?, updated_at = ?, "
                    "resolution = ?, outcome_note = ?, actor = ? WHERE id = ?",
                    (new_status, now, now, resolution, outcome_note, actor, objective_id),
                )
            else:
                conn.execute(
                    "UPDATE objectives SET status = ?, updated_at = ?, actor = ? WHERE id = ?",
                    (new_status, now, actor, objective_id),
                )
            self._insert_event(
                conn,
                objective_id,
                now,
                EVENT_STATE_CHANGE,
                summary,
                {
                    "status": new_status,
                    **({"resolution": resolution} if resolution else {}),
                },
                actor,
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get(objective_id)

    def _require_live(self, conn: sqlite3.Connection, objective_id: int, verb: str) -> str:
        """Refuse to touch an objective that no longer exists or is finished.

        Called inside every write transaction, before the write, so the
        terminal check and the write cannot be separated by a race."""
        row = conn.execute(
            "SELECT status FROM objectives WHERE id = ?",
            (objective_id,),
        ).fetchone()
        if row is None:
            raise ObjectiveNotFoundError(f"No objective {objective_id}")
        if row["status"] in TERMINAL_STATUSES:
            raise InvalidTransitionError(
                f"Cannot {verb} objective {objective_id}: it is {row['status']} (terminal)",
            )
        return row["status"]

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        objective_id: int,
        occurred_at: str,
        event_kind: str,
        summary: str,
        provenance: dict[str, Any] | None,
        actor: str | None,
    ) -> int:
        cur = conn.execute(
            "INSERT INTO objective_events "
            "(objective_id, occurred_at, event_kind, summary, provenance_json, actor) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                objective_id,
                occurred_at,
                event_kind,
                summary,
                json.dumps(provenance) if provenance else None,
                actor,
            ),
        )
        return cur.lastrowid

    def _get_event(self, event_id: int) -> ObjectiveEvent:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM objective_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            return _row_to_event(row)
        finally:
            conn.close()
