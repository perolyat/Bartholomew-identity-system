"""Durable state the External Capability Interface owns, and deliberately only that.

Two tables, and the reason there are only two is the point. Everything else a
boundary appears to need already exists and is reused rather than copied:

* **The exchange itself is not stored here.** An inbound observation, request
  or result is captured by `bartholomew/kernel/inbound_store.py` through
  `run_inbound_through_runtime_contract()`, the same governed capture path
  `/api/inbound/events` uses. There is no second event table, no second
  idempotency key and no second provenance record.
* **Endpoint identity, enrolment, credentials, capability declaration and
  revocation** belong to `bartholomew/platform/devices.py`. Nothing here
  duplicates a row of it.
* **Audit** is `ActionReflection` through the existing sink, and the
  governance record is the captured row's own outcome column.

What is genuinely new, and therefore lives here:

`eci_directives`
    The correlation ledger. A directive Bartholomew issued, and -- exactly
    once -- what the endpoint reported back about it. This is the state that
    makes requirement (G) real: without a durable record of what was issued,
    a returning result has nothing to be correlated *to*, and "correlated"
    would mean "believed".

`eci_availability`
    The endpoint's own statement that it can currently serve a capability it
    declared. `capabilities.py` explains why this is a separate fact from
    declaration; this is where it persists.

Settlement is exactly-once, by a conditional UPDATE
---------------------------------------------------
`settle_directive()` is a single `UPDATE ... WHERE state = 'issued'`, and the
row count decides the answer. Two deliveries of the same result -- a retry, a
duplicated send, a malicious replay -- settle it once and the second is
reported as already settled rather than overwriting the first. This is the
same mechanism `bartholomew/actuation/store.py` uses to lease an action
exactly once, for the same reason: a check-then-write would have a window, and
a boundary's window is somebody else's opportunity.

A result is refused rather than attributed
------------------------------------------
Three separate refusals are kept apart, and none of them is "close enough":
a correlation id this runtime never issued (`UNCORRELATED`), one issued to a
*different* endpoint (`WRONG_ENDPOINT` -- a cross-endpoint write attempt, not
a mistake to be forgiven), and one whose window had closed (`EXPIRED`). A
result that cannot be correlated is never attached to a plausible directive.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from bartholomew.kernel.db_ctx import wal_db

from .capabilities import AvailabilityReport
from .contract import (
    CapabilityRef,
    DirectiveStatus,
    GovernedDirective,
    canonical_json,
    parse_iso,
    to_iso,
    utc_now,
)

ECI_SCHEMA = """
CREATE TABLE IF NOT EXISTS eci_directives (
    directive_id TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL UNIQUE,
    endpoint_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    capability_kind TEXT NOT NULL,
    capability_version INTEGER NOT NULL,
    parameters_json TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    issued_by TEXT NOT NULL,
    state TEXT NOT NULL,
    settled_at TEXT,
    result_status TEXT,
    result_reason TEXT,
    result_exchange_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_eci_directives_endpoint
    ON eci_directives(endpoint_id, issued_at DESC);

CREATE TABLE IF NOT EXISTS eci_availability (
    endpoint_id TEXT NOT NULL,
    capability_kind TEXT NOT NULL,
    capability_version INTEGER NOT NULL,
    available INTEGER NOT NULL,
    detail TEXT NOT NULL,
    reported_at TEXT NOT NULL,
    PRIMARY KEY (endpoint_id, capability_kind, capability_version)
);
"""

#: A directive that has been issued and not yet reported on.
STATE_ISSUED = "issued"
#: A directive the endpoint has reported on. Terminal: there is no transition
#: out of it, so a second result cannot re-open one.
STATE_SETTLED = "settled"


class EciPersistenceError(RuntimeError):
    """A write did not happen. Never reported to an endpoint as if it had.

    Distinct from every governance refusal: this is the boundary failing, not
    Bartholomew declining, and the two must not be collapsed -- an endpoint
    that retries a refusal will loop, and one that does not retry a failure
    loses the exchange.
    """


class SettleOutcome(str, Enum):
    """What became of one returning result. A closed, honest set."""

    SETTLED = "settled"
    #: No directive with that correlation id was ever issued by this runtime.
    UNCORRELATED = "uncorrelated"
    #: The directive exists but belongs to a different endpoint. Refused.
    WRONG_ENDPOINT = "wrong_endpoint"
    #: The directive's window had already closed when the result arrived.
    EXPIRED = "expired"
    #: A result for this directive was already recorded. The first one stands.
    ALREADY_SETTLED = "already_settled"


@dataclass(frozen=True)
class StoredDirective:
    """A directive as it stands in the ledger."""

    directive_id: str
    correlation_id: str
    endpoint_id: str
    user_id: str
    capability: CapabilityRef
    issued_at: str
    expires_at: str
    issued_by: str
    state: str
    settled_at: str | None = None
    result_status: str | None = None
    result_reason: str | None = None
    result_exchange_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "directive_id": self.directive_id,
            "correlation_id": self.correlation_id,
            "endpoint_id": self.endpoint_id,
            "capability": self.capability.as_dict(),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "issued_by": self.issued_by,
            "state": self.state,
            "settled_at": self.settled_at,
            "result_status": self.result_status,
            "result_reason": self.result_reason,
        }


def ensure_schema(db_path: str) -> None:
    """Create the boundary's two tables if absent. Idempotent and additive.

    `CREATE TABLE IF NOT EXISTS` only: an existing database gains two tables
    and loses nothing, so there is no migration and no data-loss path.
    """
    try:
        with wal_db(db_path, timeout=30.0, label="eci_ensure_schema") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executescript(ECI_SCHEMA)
            conn.commit()
    except sqlite3.Error as exc:
        raise EciPersistenceError(
            f"The External Capability Interface schema is unavailable: "
            f"{type(exc).__name__}: {exc}",
        ) from exc


def record_directive(db_path: str, directive: GovernedDirective, *, user_id: str) -> None:
    """Write an issued directive to the correlation ledger.

    Called *before* the directive is handed to an endpoint, never after. A
    directive an endpoint holds but the ledger does not know about is one
    whose result can never be correlated, so the write is the thing that makes
    issuing it safe -- and a failed write raises rather than letting the
    directive go out uncorrelatable.
    """
    try:
        with wal_db(db_path, timeout=30.0, label="eci_record_directive") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute(
                """
                INSERT INTO eci_directives (
                    directive_id, correlation_id, endpoint_id, user_id,
                    capability_kind, capability_version, parameters_json,
                    issued_at, expires_at, issued_by, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    directive.directive_id,
                    directive.correlation_id,
                    directive.endpoint_id,
                    user_id,
                    directive.capability.kind,
                    directive.capability.version,
                    canonical_json(directive.parameters),
                    to_iso(directive.issued_at),
                    to_iso(directive.expires_at),
                    directive.issued_by,
                    STATE_ISSUED,
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise EciPersistenceError(
            f"Directive {directive.directive_id} was NOT recorded: a directive with "
            f"that id or correlation id already exists ({exc}). It was not issued.",
        ) from exc
    except sqlite3.Error as exc:
        raise EciPersistenceError(
            f"Directive {directive.directive_id} was NOT recorded "
            f"({type(exc).__name__}: {exc}). It was not issued.",
        ) from exc


def get_directive(db_path: str, correlation_id: str) -> StoredDirective | None:
    """The ledger row for one correlation id, if this runtime issued it."""
    try:
        with wal_db(db_path, timeout=5.0, label="eci_get_directive") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            row = conn.execute(
                """
                SELECT directive_id, correlation_id, endpoint_id, user_id,
                       capability_kind, capability_version, issued_at, expires_at,
                       issued_by, state, settled_at, result_status, result_reason,
                       result_exchange_id
                  FROM eci_directives
                 WHERE correlation_id = ?
                """,
                (correlation_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise EciPersistenceError(
            f"The directive ledger could not be read ({type(exc).__name__}: {exc}).",
        ) from exc
    if row is None:
        return None
    return StoredDirective(
        directive_id=row[0],
        correlation_id=row[1],
        endpoint_id=row[2],
        user_id=row[3],
        capability=CapabilityRef(kind=row[4], version=int(row[5])),
        issued_at=row[6],
        expires_at=row[7],
        issued_by=row[8],
        state=row[9],
        settled_at=row[10],
        result_status=row[11],
        result_reason=row[12],
        result_exchange_id=row[13],
    )


@dataclass(frozen=True)
class SettleResult:
    """What settlement did, and the directive it was about if there was one."""

    outcome: SettleOutcome
    directive: StoredDirective | None
    reason: str


def settle_directive(
    db_path: str,
    *,
    correlation_id: str,
    endpoint_id: str,
    status: DirectiveStatus,
    reason: str | None,
    result_exchange_id: str,
    now: datetime | None = None,
) -> SettleResult:
    """Correlate one returning result to the directive that caused it.

    The checks are ordered so that each refusal is the most specific true one,
    and the endpoint comparison happens **before** anything is written: a
    result naming another endpoint's directive is a cross-endpoint write
    attempt and is refused outright, never partially applied.

    Expiry is evaluated against the ledger's own recorded window rather than
    anything the endpoint sent, because a sender that could supply the clock
    could supply itself more time.
    """
    moment = now or utc_now()
    existing = get_directive(db_path, correlation_id)

    if existing is None:
        return SettleResult(
            outcome=SettleOutcome.UNCORRELATED,
            directive=None,
            reason=(
                f"No directive with correlation id {correlation_id!r} was issued by "
                "this runtime. The result was not applied and was not attributed to "
                "any other directive."
            ),
        )

    if existing.endpoint_id != endpoint_id:
        return SettleResult(
            outcome=SettleOutcome.WRONG_ENDPOINT,
            directive=None,
            reason=(
                f"Directive {correlation_id!r} was issued to a different endpoint. "
                "An endpoint may only report results for directives issued to it; "
                "the result was not applied."
            ),
        )

    if existing.state == STATE_SETTLED:
        return SettleResult(
            outcome=SettleOutcome.ALREADY_SETTLED,
            directive=existing,
            reason=(
                f"A result for directive {correlation_id!r} was already recorded "
                f"({existing.result_status}). The first result stands and this one "
                "was not applied."
            ),
        )

    try:
        expires = parse_iso(existing.expires_at)
    except Exception:  # noqa: BLE001 - an unreadable window is a closed one
        expires = moment

    if moment >= expires:
        # Settled, not silently dropped: the directive's window closed, and
        # that is itself the outcome worth recording. `timed_out` is distinct
        # from anything the endpoint reported -- including from `unknown` --
        # because "we stopped waiting" and "the endpoint does not know" are
        # different facts about the same directive.
        settled = _apply_settlement(
            db_path,
            correlation_id=correlation_id,
            status="timed_out",
            reason=(
                f"The result arrived after the directive's window closed at "
                f"{existing.expires_at}. The endpoint reported {status.value!r}."
            ),
            result_exchange_id=result_exchange_id,
            moment=moment,
        )
        return SettleResult(
            outcome=SettleOutcome.EXPIRED if settled else SettleOutcome.ALREADY_SETTLED,
            directive=get_directive(db_path, correlation_id),
            reason=(
                f"Directive {correlation_id!r} had already expired at "
                f"{existing.expires_at}; the result is recorded as timed out rather "
                "than as the outcome the endpoint reported."
            ),
        )

    settled = _apply_settlement(
        db_path,
        correlation_id=correlation_id,
        status=status.value,
        reason=reason,
        result_exchange_id=result_exchange_id,
        moment=moment,
    )
    if not settled:
        # Lost the race to a concurrent delivery of the same result. The other
        # one is the record; this one changed nothing and says so.
        return SettleResult(
            outcome=SettleOutcome.ALREADY_SETTLED,
            directive=get_directive(db_path, correlation_id),
            reason=(
                f"A concurrent result for directive {correlation_id!r} settled it "
                "first. The first result stands and this one was not applied."
            ),
        )
    return SettleResult(
        outcome=SettleOutcome.SETTLED,
        directive=get_directive(db_path, correlation_id),
        reason=(
            f"The result was correlated to directive {correlation_id!r} and recorded "
            f"as {status.value!r}."
        ),
    )


def _apply_settlement(
    db_path: str,
    *,
    correlation_id: str,
    status: str,
    reason: str | None,
    result_exchange_id: str,
    moment: datetime,
) -> bool:
    """One conditional UPDATE. True if this caller is the one that settled it.

    `WHERE state = ?` is what makes settlement exactly-once: SQLite reports
    how many rows changed, and a second delivery changes none.
    """
    try:
        with wal_db(db_path, timeout=30.0, label="eci_settle_directive") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            cursor = conn.execute(
                """
                UPDATE eci_directives
                   SET state = ?, settled_at = ?, result_status = ?,
                       result_reason = ?, result_exchange_id = ?
                 WHERE correlation_id = ? AND state = ?
                """,
                (
                    STATE_SETTLED,
                    to_iso(moment),
                    status,
                    reason,
                    result_exchange_id,
                    correlation_id,
                    STATE_ISSUED,
                ),
            )
            conn.commit()
            return cursor.rowcount == 1
    except sqlite3.Error as exc:
        raise EciPersistenceError(
            f"The result for directive {correlation_id!r} was NOT recorded "
            f"({type(exc).__name__}: {exc}).",
        ) from exc


def expire_overdue_directives(db_path: str, *, now: datetime | None = None) -> int:
    """Settle every issued directive whose window has closed. Returns the count.

    A directive nobody ever reported on is not left `issued` forever pretending
    to be in flight. This is how *endpoint disappears* is handled honestly at
    the ledger: the row becomes `timed_out`, which is a fact, rather than
    staying open, which would be a claim that something is still happening.

    It creates no retry. `bartholomew/actuation/recovery.py` is this
    repository's authority on what follows a failed or unknown outcome, and
    nothing here contradicts it: a fresh attempt is a new directive, decided
    by the Bartholomew seam that issues one, never manufactured by a sweep.
    """
    moment = to_iso(now or utc_now())
    try:
        with wal_db(db_path, timeout=30.0, label="eci_expire_directives") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            cursor = conn.execute(
                """
                UPDATE eci_directives
                   SET state = ?, settled_at = ?, result_status = 'timed_out',
                       result_reason = 'The directive expired with no result reported.'
                 WHERE state = ? AND expires_at <= ?
                """,
                (STATE_SETTLED, moment, STATE_ISSUED, moment),
            )
            conn.commit()
            return int(cursor.rowcount or 0)
    except sqlite3.Error as exc:
        raise EciPersistenceError(
            f"Overdue directives could not be settled ({type(exc).__name__}: {exc}).",
        ) from exc


def record_availability(db_path: str, endpoint_id: str, report: AvailabilityReport) -> None:
    """Record one endpoint's statement about one capability. Last write wins.

    Replacing rather than appending on purpose: this is a statement about
    *now*, and a history of readiness claims is not what any caller needs. The
    durable history of what was actually asked for and what came back is the
    directive ledger, which is append-only in the way that matters.
    """
    try:
        with wal_db(db_path, timeout=30.0, label="eci_record_availability") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute(
                """
                INSERT INTO eci_availability (
                    endpoint_id, capability_kind, capability_version,
                    available, detail, reported_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(endpoint_id, capability_kind, capability_version)
                DO UPDATE SET available = excluded.available,
                              detail = excluded.detail,
                              reported_at = excluded.reported_at
                """,
                (
                    endpoint_id,
                    report.capability.kind,
                    report.capability.version,
                    1 if report.available else 0,
                    report.detail,
                    to_iso(report.reported_at),
                ),
            )
            conn.commit()
    except sqlite3.Error as exc:
        raise EciPersistenceError(
            f"The availability report for {report.capability} was NOT recorded "
            f"({type(exc).__name__}: {exc}).",
        ) from exc


def get_availability(
    db_path: str,
    endpoint_id: str,
    capability: CapabilityRef,
) -> AvailabilityReport | None:
    """The endpoint's last statement about one capability, if it ever made one."""
    try:
        with wal_db(db_path, timeout=5.0, label="eci_get_availability") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            row = conn.execute(
                """
                SELECT available, detail, reported_at
                  FROM eci_availability
                 WHERE endpoint_id = ? AND capability_kind = ? AND capability_version = ?
                """,
                (endpoint_id, capability.kind, capability.version),
            ).fetchone()
    except sqlite3.Error as exc:
        raise EciPersistenceError(
            f"Availability could not be read ({type(exc).__name__}: {exc}).",
        ) from exc
    if row is None:
        return None
    try:
        reported_at = parse_iso(row[2])
    except Exception:  # noqa: BLE001 - an unreadable timestamp is not a report
        return None
    return AvailabilityReport(
        capability=capability,
        available=bool(row[0]),
        reported_at=reported_at,
        detail=row[1] or "",
    )


def list_directives(db_path: str, endpoint_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Recent directives for one endpoint, newest first. A read surface only."""
    try:
        with wal_db(db_path, timeout=5.0, label="eci_list_directives") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            rows = conn.execute(
                """
                SELECT directive_id, correlation_id, endpoint_id, user_id,
                       capability_kind, capability_version, issued_at, expires_at,
                       issued_by, state, settled_at, result_status, result_reason,
                       result_exchange_id
                  FROM eci_directives
                 WHERE endpoint_id = ?
                 ORDER BY issued_at DESC
                 LIMIT ?
                """,
                (endpoint_id, max(1, min(int(limit), 200))),
            ).fetchall()
    except sqlite3.Error as exc:
        raise EciPersistenceError(
            f"The directive ledger could not be read ({type(exc).__name__}: {exc}).",
        ) from exc
    return [
        StoredDirective(
            directive_id=r[0],
            correlation_id=r[1],
            endpoint_id=r[2],
            user_id=r[3],
            capability=CapabilityRef(kind=r[4], version=int(r[5])),
            issued_at=r[6],
            expires_at=r[7],
            issued_by=r[8],
            state=r[9],
            settled_at=r[10],
            result_status=r[11],
            result_reason=r[12],
            result_exchange_id=r[13],
        ).as_dict()
        for r in rows
    ]


__all__ = [
    "ECI_SCHEMA",
    "STATE_ISSUED",
    "STATE_SETTLED",
    "EciPersistenceError",
    "SettleOutcome",
    "SettleResult",
    "StoredDirective",
    "ensure_schema",
    "expire_overdue_directives",
    "get_availability",
    "get_directive",
    "list_directives",
    "record_availability",
    "record_directive",
    "settle_directive",
]
