"""Durable state for governed Windows actions. Two tables, one state machine.

`windows_action_requests` holds one row per action and the single `state`
column that decides whether it may still run. `windows_action_results` is the
append-only history of what devices observed. Neither is Memory: an action is a
record of something Bartholomew was asked to do, never something it believes,
and nothing here writes to `memories`, `nudges` or objectives.

**The state machine is the replay defence, and it is enforced by the database
rather than by a read-then-write.** Every transition is a single conditional
`UPDATE ... WHERE state = <the state we believed>`, and a `rowcount` of zero is
a refusal. Two concurrent leases of the same action therefore cannot both
succeed: they race one `UPDATE`, one wins, and the loser is told the action was
already leased instead of dispatching it a second time. A pre-check followed by
a write would let both through, which is precisely the shape of the bug this
table exists to prevent -- the same reasoning `inbound_store`'s
`UNIQUE(source_id, event_id)` records for duplicate capture.

    pending_approval --approve--> approved --lease--> leased --result--> succeeded
              |                       |                  |               failed
              |                       |                  |               unknown
              +--refuse-------------->+--cancel--------->+-------------> cancelled
                                                                         refused

Every terminal state is final. There is no transition out of one, so a
duplicate delivery, a late result, or a second lease after an outcome are all
no-ops that report what already happened.

**Parameters are purged at the terminal state.** `parameters_json` is the only
place a piece of text that is about to be typed, or copied to a clipboard, is
stored -- it has to be, because the device has to be handed it. It is deleted
the moment the action reaches a terminal state, and it is never what a list
endpoint, a Reflection or an evidence row reads: those read
`parameters_redacted_json`, in which sensitive values are already digests.

Synchronous `sqlite3` by design, called through `run_off_loop()` from the async
seam, matching every other persistence surface in this repository (Phase B
stage B2). No new connection policy, no second writer, no long transaction.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from bartholomew.kernel.db_ctx import wal_db

from .request import parse_iso, to_iso
from .result import ActionResult, ActionResultStatus, ErrorCategory

ACTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS windows_action_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    capability_version INTEGER NOT NULL,
    parameters_json TEXT,
    parameters_redacted_json TEXT NOT NULL,
    parameter_fingerprint TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    requested_by TEXT NOT NULL,
    risk_class TEXT NOT NULL,
    approval_requirement TEXT NOT NULL,
    repeatability TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL,
    state_reason TEXT,
    approved_by TEXT,
    approved_at TEXT,
    lease_count INTEGER NOT NULL DEFAULT 0,
    leased_at TEXT,
    terminal_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, action_id)
);
CREATE INDEX IF NOT EXISTS idx_windows_action_requests_dispatch
    ON windows_action_requests(tenant_id, device_id, state);
CREATE INDEX IF NOT EXISTS idx_windows_action_requests_issued
    ON windows_action_requests(issued_at DESC);

CREATE TABLE IF NOT EXISTS windows_action_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    status TEXT NOT NULL,
    error_category TEXT,
    detail TEXT,
    evidence_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (tenant_id, action_id, status)
);
CREATE INDEX IF NOT EXISTS idx_windows_action_results_recorded
    ON windows_action_results(recorded_at DESC);
"""

#: Most times an idempotent action may be leased. Bounded so an idempotent
#: action cannot become an unbounded retry loop against the same machine.
MAX_IDEMPOTENT_LEASES = 3

#: How long past its expiry a *leased* action is left alone before it is
#: recorded as `unknown`. A device that took an action needs room to finish it
#: and report; sweeping it the instant its window closed cancelled actions
#: underneath the devices that were running them.
LEASE_GRACE_SECONDS = 300


class ActionPersistenceError(RuntimeError):
    """The action state could not be written or read.

    Every caller must treat this as a refusal. An action whose state cannot be
    read is an action whose replay state cannot be checked, and dispatching one
    would be dispatching without the check.
    """


class ActionState(str, Enum):
    """Where an action is in its life. One column, one authority."""

    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    LEASED = "leased"
    REFUSED = "refused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


TERMINAL_STATES: frozenset[ActionState] = frozenset(
    {
        ActionState.REFUSED,
        ActionState.SUCCEEDED,
        ActionState.FAILED,
        ActionState.CANCELLED,
        ActionState.UNKNOWN,
    },
)

#: How a stored state is reported through the contract's seven-value result
#: vocabulary. `pending_approval` and `approved` are both `accepted`: the
#: request has been admitted and recorded, and nothing has run.
_STATE_TO_STATUS: dict[ActionState, ActionResultStatus] = {
    ActionState.PENDING_APPROVAL: ActionResultStatus.ACCEPTED,
    ActionState.APPROVED: ActionResultStatus.ACCEPTED,
    ActionState.LEASED: ActionResultStatus.STARTED,
    ActionState.REFUSED: ActionResultStatus.REFUSED,
    ActionState.SUCCEEDED: ActionResultStatus.SUCCEEDED,
    ActionState.FAILED: ActionResultStatus.FAILED,
    ActionState.CANCELLED: ActionResultStatus.CANCELLED,
    ActionState.UNKNOWN: ActionResultStatus.UNKNOWN,
}

_RESULT_TO_STATE: dict[ActionResultStatus, ActionState] = {
    ActionResultStatus.SUCCEEDED: ActionState.SUCCEEDED,
    ActionResultStatus.FAILED: ActionState.FAILED,
    ActionResultStatus.CANCELLED: ActionState.CANCELLED,
    ActionResultStatus.UNKNOWN: ActionState.UNKNOWN,
}

_COLUMNS = (
    "id, tenant_id, action_id, device_id, capability, capability_version, "
    "parameters_json, parameters_redacted_json, parameter_fingerprint, "
    "correlation_id, causation_id, requested_by, risk_class, approval_requirement, "
    "repeatability, issued_at, expires_at, state, state_reason, approved_by, "
    "approved_at, lease_count, leased_at, terminal_at, updated_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class StoredAction:
    """One action row, as it stands."""

    row_id: int
    tenant_id: str
    action_id: str
    device_id: str
    capability: str
    capability_version: int
    #: None once the action reached a terminal state and its parameters were
    #: purged. A caller that needs them must read the action before it ends.
    parameters: dict[str, Any] | None
    parameters_redacted: dict[str, Any]
    parameter_fingerprint: str
    correlation_id: str
    causation_id: str | None
    requested_by: str
    risk_class: str
    approval_requirement: str
    repeatability: str
    issued_at: str
    expires_at: str
    state: ActionState
    state_reason: str | None
    approved_by: str | None
    approved_at: str | None
    lease_count: int
    leased_at: str | None
    terminal_at: str | None
    updated_at: str

    @property
    def status(self) -> ActionResultStatus:
        """This action's state in the contract's seven-value vocabulary."""
        return _STATE_TO_STATUS[self.state]

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def as_dict(self, *, include_parameters: bool = False) -> dict[str, Any]:
        """The inspection form. Redacted parameters unless explicitly asked."""
        out: dict[str, Any] = {
            "action_id": self.action_id,
            "tenant_id": self.tenant_id,
            "device_id": self.device_id,
            "capability": self.capability,
            "capability_version": self.capability_version,
            "parameters": dict(self.parameters_redacted),
            "parameter_fingerprint": self.parameter_fingerprint,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "requested_by": self.requested_by,
            "risk_class": self.risk_class,
            "approval_requirement": self.approval_requirement,
            "repeatability": self.repeatability,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "state": self.state.value,
            "status": self.status.value,
            "reason": self.state_reason,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "lease_count": self.lease_count,
            "leased_at": self.leased_at,
            "terminal_at": self.terminal_at,
            "updated_at": self.updated_at,
        }
        if include_parameters:
            out["canonical_parameters"] = dict(self.parameters or {})
        return out


def _row(row: tuple[Any, ...]) -> StoredAction:
    return StoredAction(
        row_id=row[0],
        tenant_id=row[1],
        action_id=row[2],
        device_id=row[3],
        capability=row[4],
        capability_version=int(row[5]),
        parameters=json.loads(row[6]) if row[6] else None,
        parameters_redacted=json.loads(row[7]) if row[7] else {},
        parameter_fingerprint=row[8],
        correlation_id=row[9],
        causation_id=row[10],
        requested_by=row[11],
        risk_class=row[12],
        approval_requirement=row[13],
        repeatability=row[14],
        issued_at=row[15],
        expires_at=row[16],
        state=ActionState(row[17]),
        state_reason=row[18],
        approved_by=row[19],
        approved_at=row[20],
        lease_count=int(row[21] or 0),
        leased_at=row[22],
        terminal_at=row[23],
        updated_at=row[24],
    )


def _is_missing_table(error: sqlite3.OperationalError) -> bool:
    """Whether this is "the table does not exist yet" and nothing worse.

    `sqlite3.OperationalError` covers a missing table *and* `unable to open
    database file`, `disk I/O error` and `attempt to write a readonly
    database`. Treating them alike made the inspection surface answer
    `200 {"actions": []}` -- indistinguishable from "this tenant has never
    requested anything" -- for a database the process could not read at all.
    A halt must not hide what Bartholomew was about to do, and neither must a
    broken disk.
    """
    return "no such table" in str(error).lower()


def ensure_schema(db_path: str) -> None:
    """Create the action tables if they do not exist. Idempotent."""
    try:
        with wal_db(db_path, timeout=30.0, label="windows_action_ensure_schema") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executescript(ACTION_SCHEMA)
            conn.commit()
    except sqlite3.Error as e:
        raise ActionPersistenceError(
            f"the Windows action schema is unavailable: {type(e).__name__}: {e}",
        ) from e


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


def get_action(db_path: str, *, tenant_id: str, action_id: str) -> StoredAction | None:
    """One action, or None. Tenant-qualified: another tenant's id is unknown."""
    try:
        with wal_db(db_path, timeout=5.0, label="windows_action_get") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM windows_action_requests "  # noqa: S608
                "WHERE tenant_id = ? AND action_id = ?",
                (tenant_id, action_id),
            ).fetchone()
    except sqlite3.Error as e:
        raise ActionPersistenceError(
            f"action {action_id} could not be read: {type(e).__name__}: {e}",
        ) from e
    return _row(row) if row else None


def recent_actions(
    db_path: str,
    *,
    tenant_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """The inspection surface. Redacted parameters only, newest first.

    Readable while the Parking Brake is engaged, because inspection is exactly
    what a halt must not hide.
    """
    try:
        with wal_db(db_path, timeout=5.0, label="windows_action_recent") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM windows_action_requests "  # noqa: S608
                "WHERE tenant_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (tenant_id, int(limit), int(offset)),
            ).fetchall()
    except sqlite3.OperationalError as e:
        if not _is_missing_table(e):
            raise ActionPersistenceError(
                f"the action list could not be read: {type(e).__name__}: {e}",
            ) from e
        # The table does not exist yet: nothing has ever been requested.
        return []
    except sqlite3.Error as e:
        raise ActionPersistenceError(
            f"the action list could not be read: {type(e).__name__}: {e}",
        ) from e
    return [_row(r).as_dict() for r in rows]


def results_for(db_path: str, *, tenant_id: str, action_id: str) -> list[dict[str, Any]]:
    """Every result recorded against one action, oldest first."""
    try:
        with wal_db(db_path, timeout=5.0, label="windows_action_results") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            rows = conn.execute(
                "SELECT status, error_category, detail, evidence_json, observed_at, "
                "recorded_at FROM windows_action_results "
                "WHERE tenant_id = ? AND action_id = ? ORDER BY id ASC",
                (tenant_id, action_id),
            ).fetchall()
    except sqlite3.OperationalError as e:
        if not _is_missing_table(e):
            raise ActionPersistenceError(
                f"results for {action_id} could not be read: {type(e).__name__}: {e}",
            ) from e
        return []
    except sqlite3.Error as e:
        raise ActionPersistenceError(
            f"results for {action_id} could not be read: {type(e).__name__}: {e}",
        ) from e
    return [
        {
            "status": r[0],
            "error_category": r[1],
            "detail": r[2],
            "evidence": json.loads(r[3]) if r[3] else {},
            "observed_at": r[4],
            "recorded_at": r[5],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------


def create_action(
    db_path: str,
    *,
    record: dict[str, Any],
    canonical_parameters: dict[str, Any],
    state: ActionState,
    state_reason: str | None = None,
) -> tuple[StoredAction, bool]:
    """Insert one action, or report the existing one for that (tenant, action_id).

    Returns `(action, created)`. `created=False` means the row already existed
    and this call changed nothing -- which the caller needs to know, because a
    re-submission that lands on a finished action is a very different answer
    from a fresh one, and reporting both as "created" told clients to wait for
    an approval prompt that was never coming.

    An existing row is **not** overwritten. Re-submitting the same `action_id`
    is a retry of a request, and a retry must land on what already exists --
    otherwise a caller could change the parameters of an action after it was
    approved simply by re-submitting it, which is the exact substitution an
    approval's fingerprint exists to prevent. The `UNIQUE (tenant_id,
    action_id)` constraint, not a pre-check, is what guarantees it under
    concurrency.
    """
    now = _now()
    terminal = state in TERMINAL_STATES
    try:
        with wal_db(db_path, timeout=30.0, label="windows_action_create") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            try:
                cur = conn.execute(
                    """INSERT INTO windows_action_requests
                       (tenant_id, action_id, device_id, capability, capability_version,
                        parameters_json, parameters_redacted_json, parameter_fingerprint,
                        correlation_id, causation_id, requested_by, risk_class,
                        approval_requirement, repeatability, issued_at, expires_at,
                        state, state_reason, lease_count, terminal_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                    (
                        record["tenant_id"],
                        record["action_id"],
                        record["device_id"],
                        record["capability"],
                        int(record["capability_version"]),
                        # Not stored at all for an action that is already
                        # terminal: a refused action never needs its parameters
                        # handed to anything.
                        None if terminal else json.dumps(canonical_parameters, sort_keys=True),
                        json.dumps(record["parameters"], sort_keys=True),
                        record["parameter_fingerprint"],
                        record["correlation_id"],
                        record.get("causation_id"),
                        record["requested_by"],
                        record["risk_class"],
                        record["approval_requirement"],
                        record["repeatability"],
                        record["issued_at"],
                        record["expires_at"],
                        state.value,
                        state_reason,
                        now if terminal else None,
                        now,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                row = conn.execute(
                    f"SELECT {_COLUMNS} FROM windows_action_requests "  # noqa: S608
                    "WHERE tenant_id = ? AND action_id = ?",
                    (record["tenant_id"], record["action_id"]),
                ).fetchone()
                if row is None:  # pragma: no cover - the constraint just fired
                    raise
                return _row(row), False
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM windows_action_requests WHERE id = ?",  # noqa: S608
                (cur.lastrowid,),
            ).fetchone()
    except sqlite3.Error as e:
        raise ActionPersistenceError(
            f"action {record.get('action_id')} was NOT recorded: {type(e).__name__}: {e}",
        ) from e
    if row is None:  # pragma: no cover - written in this transaction
        raise ActionPersistenceError("the action was written but could not be read back")
    return _row(row), True


def _transition(
    db_path: str,
    *,
    tenant_id: str,
    action_id: str,
    from_states: tuple[ActionState, ...],
    to_state: ActionState,
    reason: str | None,
    extra_set: str = "",
    extra_params: tuple[Any, ...] = (),
    label: str,
) -> StoredAction | None:
    """One conditional state transition. Returns the row iff *this* call moved it.

    `None` means the action was not in any of `from_states` -- somebody else
    moved it first, it was already terminal, or it never existed. Every caller
    turns that into a refusal rather than retrying, because the whole point is
    that only one caller may win.
    """
    now = _now()
    terminal = to_state in TERMINAL_STATES
    placeholders = ", ".join("?" for _ in from_states)
    try:
        with wal_db(db_path, timeout=30.0, label=label) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            cur = conn.execute(
                "UPDATE windows_action_requests SET "  # noqa: S608 - fixed fragments
                "state = ?, state_reason = ?, updated_at = ?"
                + (", terminal_at = ?" if terminal else "")
                # Parameters exist only to be handed to a device. Once the
                # action can never be dispatched again, they are deleted.
                + (", parameters_json = NULL" if terminal else "")
                + extra_set
                + f" WHERE tenant_id = ? AND action_id = ? AND state IN ({placeholders})",
                (
                    to_state.value,
                    reason,
                    now,
                    *((now,) if terminal else ()),
                    *extra_params,
                    tenant_id,
                    action_id,
                    *[s.value for s in from_states],
                ),
            )
            moved = cur.rowcount
            if not moved:
                conn.rollback()
                return None
            # Read back **inside** the transaction, before the commit. After
            # it, another writer can move the row between the commit and the
            # SELECT, and the caller -- which treats "not None" as "I performed
            # this transition" -- would then be handed a row in somebody else's
            # state. That produced a 200 "Approved. The action is now eligible
            # to be leased" describing a row that read `cancelled`.
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM windows_action_requests "  # noqa: S608
                "WHERE tenant_id = ? AND action_id = ?",
                (tenant_id, action_id),
            ).fetchone()
            conn.commit()
    except sqlite3.Error as e:
        raise ActionPersistenceError(
            f"action {action_id} could not be moved to {to_state.value}: "
            f"{type(e).__name__}: {e}",
        ) from e
    return _row(row) if row else None


def mark_approved(
    db_path: str,
    *,
    tenant_id: str,
    action_id: str,
    approver: str,
) -> StoredAction | None:
    """`pending_approval -> approved`. Only from pending; never re-approvable."""
    return _transition(
        db_path,
        tenant_id=tenant_id,
        action_id=action_id,
        from_states=(ActionState.PENDING_APPROVAL,),
        to_state=ActionState.APPROVED,
        reason=None,
        extra_set=", approved_by = ?, approved_at = ?",
        extra_params=(approver, _now()),
        label="windows_action_approve",
    )


def mark_pending_again(
    db_path: str,
    *,
    tenant_id: str,
    action_id: str,
) -> StoredAction | None:
    """`approved -> pending_approval`, to undo an approval that did not record.

    The **only** backwards transition in this machine, and it exists for one
    situation: `grant_action_approval` wins the conditional move to `approved`
    and then fails to write the approval row. Without this the action is stuck
    -- `mark_approved` moves only from `pending_approval`, so it can never be
    approved again, and dispatch refuses for the missing approval until expiry.

    Safe because an action at `approved` with no approval on file cannot be
    leased: `evaluate_dispatch_admission` reads the approval and refuses with
    `APPROVAL_MISSING` before `try_lease` is reached. It moves only from
    `approved`, so a cancel or a lease that got there first wins and this
    returns None.
    """
    return _transition(
        db_path,
        tenant_id=tenant_id,
        action_id=action_id,
        from_states=(ActionState.APPROVED,),
        to_state=ActionState.PENDING_APPROVAL,
        reason="the approval could not be recorded; returned to pending",
        extra_set=", approved_by = NULL, approved_at = NULL",
        label="windows_action_unapprove",
    )


def mark_refused(
    db_path: str,
    *,
    tenant_id: str,
    action_id: str,
    reason: str,
) -> StoredAction | None:
    """Any non-terminal state -> `refused`."""
    return _transition(
        db_path,
        tenant_id=tenant_id,
        action_id=action_id,
        from_states=(ActionState.PENDING_APPROVAL, ActionState.APPROVED, ActionState.LEASED),
        to_state=ActionState.REFUSED,
        reason=reason,
        label="windows_action_refuse",
    )


def mark_cancelled(
    db_path: str,
    *,
    tenant_id: str,
    action_id: str,
    reason: str,
) -> StoredAction | None:
    """Any non-terminal state -> `cancelled`. A leased action can be withdrawn.

    Cancelling a leased action does not reach out and stop a device -- nothing
    here can. What it does is make the action un-leasable and refuse any result
    the device later reports, so a cancelled action can never be recorded as
    having succeeded.
    """
    return _transition(
        db_path,
        tenant_id=tenant_id,
        action_id=action_id,
        from_states=(ActionState.PENDING_APPROVAL, ActionState.APPROVED, ActionState.LEASED),
        to_state=ActionState.CANCELLED,
        reason=reason,
        label="windows_action_cancel",
    )


def try_lease(
    db_path: str,
    *,
    tenant_id: str,
    action_id: str,
    repeatable: bool,
) -> StoredAction | None:
    """Take the one lease on an approved action, or return None.

    The `lease_count` guard in the `WHERE` clause is what makes a
    non-repeatable action non-repeatable: the row can move to `leased` exactly
    once, and a second caller -- a retry, a duplicate delivery, a second
    companion process -- finds `lease_count = 0` no longer true and is refused.
    An idempotent action may be re-leased up to `MAX_IDEMPOTENT_LEASES` times,
    which bounds a redelivery loop rather than permitting one.
    """
    now = _now()
    if repeatable:
        from_states = (ActionState.APPROVED, ActionState.LEASED)
        guard = " AND lease_count < ?"
        guard_params: tuple[Any, ...] = (MAX_IDEMPOTENT_LEASES,)
    else:
        from_states = (ActionState.APPROVED,)
        guard = " AND lease_count = 0"
        guard_params = ()

    placeholders = ", ".join("?" for _ in from_states)
    try:
        with wal_db(db_path, timeout=30.0, label="windows_action_lease") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            cur = conn.execute(
                "UPDATE windows_action_requests SET "  # noqa: S608 - fixed fragments
                "state = ?, lease_count = lease_count + 1, leased_at = ?, updated_at = ? "
                f"WHERE tenant_id = ? AND action_id = ? AND state IN ({placeholders})"
                # Expiry in the `WHERE`, not only in the admission that runs
                # before it. The admission's expiry check sits several awaits
                # earlier -- two brake reads and a memory read -- so on its own
                # it is a time-of-check/time-of-use gap. Costing one comparison
                # to close it inside the same statement that wins the lease is
                # a better trade than reasoning about how wide the window is.
                " AND expires_at > ?" + guard,
                (
                    ActionState.LEASED.value,
                    now,
                    now,
                    tenant_id,
                    action_id,
                    *[s.value for s in from_states],
                    now,
                    *guard_params,
                ),
            )
            moved = cur.rowcount
            if not moved:
                conn.rollback()
                return None
            # Inside the transaction; see `_transition` for why.
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM windows_action_requests "  # noqa: S608
                "WHERE tenant_id = ? AND action_id = ?",
                (tenant_id, action_id),
            ).fetchone()
            conn.commit()
    except sqlite3.Error as e:
        raise ActionPersistenceError(
            f"action {action_id} could not be leased: {type(e).__name__}: {e}",
        ) from e
    return _row(row) if row else None


def dispatchable_action_ids(
    db_path: str,
    *,
    tenant_id: str,
    device_id: str,
    limit: int = 10,
) -> list[str]:
    """Approved, unexpired actions for one device, oldest first.

    A candidate list only. Each id still goes through the whole admission --
    brake, device, capability, approval, expiry, replay -- before it is leased,
    so appearing here authorises nothing.
    """
    try:
        with wal_db(db_path, timeout=5.0, label="windows_action_dispatchable") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            rows = conn.execute(
                "SELECT action_id FROM windows_action_requests "
                "WHERE tenant_id = ? AND device_id = ? AND state = ? AND expires_at > ? "
                "ORDER BY id ASC LIMIT ?",
                (
                    tenant_id,
                    device_id,
                    ActionState.APPROVED.value,
                    _now(),
                    max(1, min(int(limit), 50)),
                ),
            ).fetchall()
    except sqlite3.OperationalError as e:
        if not _is_missing_table(e):
            raise ActionPersistenceError(
                f"dispatchable actions could not be read: {type(e).__name__}: {e}",
            ) from e
        return []
    except sqlite3.Error as e:
        raise ActionPersistenceError(
            f"dispatchable actions could not be read: {type(e).__name__}: {e}",
        ) from e
    return [r[0] for r in rows]


def record_result(
    db_path: str,
    *,
    result: ActionResult,
) -> tuple[StoredAction | None, bool]:
    """Record one device-observed result and move the action to its terminal state.

    Returns `(action, recorded)`. `recorded=False` means the action was not in
    a state this result could apply to -- it had already ended, or was never
    leased -- and the result was **not** written. A late or duplicate result is
    therefore inert rather than able to overwrite an outcome, which is what
    stops a device from reporting success for an action that was cancelled
    underneath it.

    A `started` result is a progress note, not an outcome: it is appended to
    the history and leaves the state alone.
    """
    now = _now()
    is_progress = result.status is ActionResultStatus.STARTED
    to_state = None if is_progress else _RESULT_TO_STATE.get(result.status)
    if not is_progress and to_state is None:
        raise ActionPersistenceError(
            f"a device may not report {result.status.value!r} as an outcome",
        )

    # **One transaction for the state change and the evidence row.** Splitting
    # them lost outcomes irrecoverably: the terminal transition committed, the
    # evidence insert then failed on a lock, the caller saw a 503 and retried
    # -- and the retry found the action already terminal, so the history ended
    # up with no row at all for an outcome the action was recorded as having
    # reached. `BEGIN IMMEDIATE` takes the write lock up front, so the two
    # either both land or neither does.
    try:
        with wal_db(db_path, timeout=30.0, label="windows_action_result") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("BEGIN IMMEDIATE")

            if is_progress:
                # A progress note leaves the state alone, but it still has to
                # see the state to know it belongs to a live lease.
                current = conn.execute(
                    f"SELECT {_COLUMNS} FROM windows_action_requests "  # noqa: S608
                    "WHERE tenant_id = ? AND action_id = ?",
                    (result.tenant_id, result.action_id),
                ).fetchone()
                if current is None or ActionState(current[17]) is not ActionState.LEASED:
                    conn.rollback()
                    return (_row(current) if current else None), False
            else:
                moved = conn.execute(
                    "UPDATE windows_action_requests SET state = ?, state_reason = ?, "
                    "terminal_at = ?, updated_at = ?, parameters_json = NULL "
                    "WHERE tenant_id = ? AND action_id = ? AND state = ?",
                    (
                        to_state.value,
                        (result.error_category.value if result.error_category else None),
                        now,
                        now,
                        result.tenant_id,
                        result.action_id,
                        ActionState.LEASED.value,
                    ),
                ).rowcount
                if not moved:
                    current = conn.execute(
                        f"SELECT {_COLUMNS} FROM windows_action_requests "  # noqa: S608
                        "WHERE tenant_id = ? AND action_id = ?",
                        (result.tenant_id, result.action_id),
                    ).fetchone()
                    conn.rollback()
                    return (_row(current) if current else None), False

            inserted = conn.execute(
                """INSERT OR IGNORE INTO windows_action_results
                   (tenant_id, action_id, device_id, status, error_category, detail,
                    evidence_json, observed_at, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.tenant_id,
                    result.action_id,
                    result.device_id,
                    result.status.value,
                    result.error_category.value if result.error_category else None,
                    result.detail,
                    json.dumps(result.evidence, sort_keys=True),
                    result.observed_at or now,
                    now,
                ),
            ).rowcount

            if is_progress and not inserted:
                # `UNIQUE (tenant_id, action_id, status)` collapsed a repeated
                # `started` note. Nothing was recorded, and the device is told
                # that -- rather than told its observation landed when it was
                # silently dropped.
                current = conn.execute(
                    f"SELECT {_COLUMNS} FROM windows_action_requests "  # noqa: S608
                    "WHERE tenant_id = ? AND action_id = ?",
                    (result.tenant_id, result.action_id),
                ).fetchone()
                conn.rollback()
                return (_row(current) if current else None), False

            row = conn.execute(
                f"SELECT {_COLUMNS} FROM windows_action_requests "  # noqa: S608
                "WHERE tenant_id = ? AND action_id = ?",
                (result.tenant_id, result.action_id),
            ).fetchone()
            conn.commit()
    except sqlite3.Error as e:
        raise ActionPersistenceError(
            f"the result for {result.action_id} could not be recorded: {type(e).__name__}: {e}",
        ) from e
    return (_row(row) if row else None), True


def expire_overdue(db_path: str, *, tenant_id: str) -> int:
    """Bring overdue actions to a terminal state, and purge their parameters.

    Two sweeps, because two situations are genuinely different and running one
    sweep over both recorded actions that happened as actions that never did.

    **Not yet dispatched** (`pending_approval`, `approved`) past its expiry ->
    `cancelled`. Nothing ran, the dispatch path would have refused it anyway,
    and this only makes the stored state agree with that.

    **Leased** -- a device took it and is running it *now*. Sweeping those to
    `cancelled` the moment the expiry passed was wrong twice over: the lease
    poll runs this on every call, so an action whose window closed while its
    handler was mid-flight was cancelled underneath the device, and the honest
    result the device then reported was declined as a late one. An action that
    really launched a program was durably recorded as never having run.

    So a lease is given `LEASE_GRACE_SECONDS` past the expiry to report, and
    what it becomes afterwards is `unknown`, not `cancelled` -- because that is
    what is true. The device took the action and we never heard back; whether
    it happened is exactly the thing nobody knows.

    Parameters are purged on both paths: they exist only to be handed to a
    device, and neither of these can be dispatched again.
    """
    now = _now()
    grace = to_iso(parse_iso(now) - timedelta(seconds=LEASE_GRACE_SECONDS))
    swept = 0
    try:
        with wal_db(db_path, timeout=30.0, label="windows_action_expire") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            swept += conn.execute(
                "UPDATE windows_action_requests SET state = ?, state_reason = ?, "
                "terminal_at = ?, updated_at = ?, parameters_json = NULL "
                "WHERE tenant_id = ? AND expires_at <= ? AND state IN (?, ?)",
                (
                    ActionState.CANCELLED.value,
                    ErrorCategory.EXPIRED.value,
                    now,
                    now,
                    tenant_id,
                    now,
                    ActionState.PENDING_APPROVAL.value,
                    ActionState.APPROVED.value,
                ),
            ).rowcount
            swept += conn.execute(
                "UPDATE windows_action_requests SET state = ?, state_reason = ?, "
                "terminal_at = ?, updated_at = ?, parameters_json = NULL "
                "WHERE tenant_id = ? AND expires_at <= ? AND state = ?",
                (
                    ActionState.UNKNOWN.value,
                    ErrorCategory.EFFECT_UNVERIFIABLE.value,
                    now,
                    now,
                    tenant_id,
                    grace,
                    ActionState.LEASED.value,
                ),
            ).rowcount
            conn.commit()
    except sqlite3.OperationalError as e:
        if not _is_missing_table(e):
            raise ActionPersistenceError(
                f"overdue actions could not be expired: {type(e).__name__}: {e}",
            ) from e
        return 0
    except sqlite3.Error as e:
        raise ActionPersistenceError(
            f"overdue actions could not be expired: {type(e).__name__}: {e}",
        ) from e
    return int(swept or 0)
