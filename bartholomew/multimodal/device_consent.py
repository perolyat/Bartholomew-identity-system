"""The operator-reachable consent channel for device observation starts.

A device observation start (screen, microphone) must be answered by a person
before any adapter is touched. `privacy_guard`'s plain handler serves that on
an interactive front-end; a headless server has nobody at a terminal, and on
the live Windows golden path every start refused with "No consent handler
registered (fail-closed)" for exactly that reason. This module is the missing
channel: an *ask* a person can see and answer from somewhere else.

How one ask works
-----------------
1. The Runtime Contract's consent gate calls `ask()` with a
   `DeviceConsentRequest`. `ask()` mints a request id and a separate
   high-entropy **answer nonce**, records the pending ask in the kernel
   database, and awaits an `asyncio.Future` -- never blocking the event loop,
   so `/status`, `stop` and `disarm` keep answering while a person decides.
2. A person lists pending asks and answers one. Answering requires the
   nonce. The nonce is written **only** to the kernel database and is never
   returned by any HTTP response, so the only way to answer is to read the
   database file -- the operator's own machine and account -- not to hold the
   device credential.
3. The answer resolves that one Future exactly once, and the record is
   deleted from the in-process holder. Nothing is remembered: the next start
   attempt asks again. A grant is a single start attempt, never continuing
   access.
4. An unanswered ask expires after `DEFAULT_TTL_SECONDS` and denies. A
   cancelled request (client gone) abandons its ask, and a late answer to an
   expired or abandoned ask is refused rather than resurrecting the start.

Why the companion cannot answer its own ask
-------------------------------------------
Three independent measures, because in the default loopback deployment HTTP
identity is disabled and cannot be relied on alone:

* the answer route refuses any request carrying the device credential;
* no response the companion can receive carries the request id's nonce -- the
  refused start's body names an outcome, nothing usable to answer;
* the nonce lives in the kernel database, which the companion process does
  not read.

Bounded
-------
At most `MAX_PENDING_PER_TENANT` asks may be open per tenant; further starts
deny immediately. An authenticated companion cannot spam a person into
habituated approval, and cannot pin unbounded Futures on the loop.

This is not a second governance authority. It answers one question the
Runtime Contract asks, in the place the contract already asks it, and nothing
here can start a session, arm a channel or approve an action.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from bartholomew.kernel.db_ctx import connect
from bartholomew.kernel.memory.privacy_guard import (
    DeviceConsentRequest,
    get_device_consent_handler,
    set_device_consent_handler,
)

logger = logging.getLogger(__name__)

#: How long a person has to answer before the start denies. A person is
#: standing there; this is shorter than the actuation approval ceiling.
DEFAULT_TTL_SECONDS = 180
MAX_TTL_SECONDS = 600
#: Open asks per tenant before further starts deny immediately.
MAX_PENDING_PER_TENANT = 3

TABLE = "device_consent_requests"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    request_id     TEXT PRIMARY KEY,
    answer_nonce   TEXT NOT NULL,
    tenant_id      TEXT NOT NULL,
    principal_id   TEXT NOT NULL,
    device_id      TEXT NOT NULL,
    modality       TEXT NOT NULL,
    prompt         TEXT NOT NULL,
    correlation_id TEXT,
    session_id     TEXT,
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    decision       TEXT,
    decided_at     TEXT,
    decided_by     TEXT,
    note           TEXT
)
"""

DECISION_APPROVED = "approved"
DECISION_DENIED = "denied"
DECISION_EXPIRED = "expired"
DECISION_ABANDONED = "abandoned"


class DeviceConsentError(RuntimeError):
    """The channel is not configured; every ask denies."""


@dataclass
class _Pending:
    request_id: str
    tenant_id: str
    future: asyncio.Future
    loop: asyncio.AbstractEventLoop


#: In a holder rather than bare module globals, for the reason
#: `actuation.arming` gives: a rebound attribute is invisible to a module that
#: already imported the name.
_PENDING: dict[str, _Pending] = {}
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"db_path": None, "ttl_seconds": DEFAULT_TTL_SECONDS}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def configure(*, db_path: str, ttl_seconds: int | None = None) -> None:
    """Name the kernel database the channel records asks in."""
    if not str(db_path or "").strip():
        raise DeviceConsentError("the device consent channel needs a database path")
    ttl = DEFAULT_TTL_SECONDS if ttl_seconds is None else int(ttl_seconds)
    if ttl <= 0:
        raise DeviceConsentError("the consent TTL must be a positive number of seconds")
    with _LOCK:
        _STATE["db_path"] = str(db_path)
        _STATE["ttl_seconds"] = min(ttl, MAX_TTL_SECONDS)
    ensure_schema(str(db_path))


def install(*, db_path: str, ttl_seconds: int | None = None) -> bool:
    """Configure the channel and register `ask` as the device consent handler.

    Returns False, registering nothing, when a *different* device handler is
    already registered: two registrations in one process would silently
    overwrite each other, and a deployment that wired its own channel meant
    it.
    """
    configure(db_path=db_path, ttl_seconds=ttl_seconds)
    current = get_device_consent_handler()
    if current is not None and current is not ask:
        logger.warning(
            "A device consent handler is already registered; the operator "
            "consent channel was not installed over it",
        )
        return False
    set_device_consent_handler(ask)
    logger.info("Operator device-consent channel installed (ttl=%ss)", _STATE["ttl_seconds"])
    return True


def uninstall() -> None:
    """Remove `ask` as the handler if it is the one registered. Idempotent."""
    if get_device_consent_handler() is ask:
        set_device_consent_handler(None)


def is_installed() -> bool:
    return get_device_consent_handler() is ask


def ttl_seconds() -> int:
    return int(_STATE["ttl_seconds"])


def ensure_schema(db_path: str) -> None:
    conn = connect(db_path)
    try:
        conn.execute(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The ask: what the Runtime Contract's consent gate calls
# ---------------------------------------------------------------------------


async def ask(request: DeviceConsentRequest) -> bool:
    """Ask a person, and wait for them. True only on an explicit approval.

    Internally total: every failure is a False, never an exception, except a
    cancellation of the awaiting request, which abandons the ask and is
    re-raised so the caller's own cancellation semantics hold.
    """
    db_path = _STATE["db_path"]
    if not db_path:
        logger.warning("Device consent asked with no channel configured; denying")
        return False

    tenant = str(request.tenant_id or "").strip()
    if not tenant:
        logger.warning("Device consent asked with no tenant; denying")
        return False

    with _LOCK:
        open_for_tenant = sum(1 for p in _PENDING.values() if p.tenant_id == tenant)
    if open_for_tenant >= MAX_PENDING_PER_TENANT:
        logger.warning(
            "Tenant %s already has %d device-consent asks open; denying a further start",
            tenant,
            open_for_tenant,
        )
        return False

    request_id = f"dcr-{uuid.uuid4().hex}"
    nonce = secrets.token_hex(32)
    created = _now()
    expires = created + timedelta(seconds=ttl_seconds())

    try:
        await asyncio.to_thread(
            _insert,
            db_path,
            request_id,
            nonce,
            request,
            tenant,
            created,
            expires,
        )
    except Exception:
        logger.exception("Could not record a device-consent ask; denying")
        return False

    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    with _LOCK:
        _PENDING[request_id] = _Pending(request_id, tenant, future, loop)
    logger.info(
        "Device consent requested: %s (device=%s modality=%s, expires %s)",
        request_id,
        request.device_id,
        request.modality,
        _iso(expires),
    )

    try:
        decision = await asyncio.wait_for(future, timeout=ttl_seconds())
        return bool(decision)
    except asyncio.TimeoutError:
        await asyncio.to_thread(_mark_if_undecided, db_path, request_id, DECISION_EXPIRED)
        logger.info("Device consent %s expired unanswered; denied", request_id)
        return False
    except asyncio.CancelledError:
        _mark_if_undecided(db_path, request_id, DECISION_ABANDONED)
        raise
    except Exception:
        logger.exception("Device consent %s failed while waiting; denied", request_id)
        _mark_if_undecided(db_path, request_id, DECISION_ABANDONED)
        return False
    finally:
        with _LOCK:
            _PENDING.pop(request_id, None)


# ---------------------------------------------------------------------------
# The answer: what a person's surface calls
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnswerOutcome:
    outcome: str
    request_id: str
    detail: str
    #: Whether a start was actually waiting in this process for the answer.
    resolved_a_waiting_start: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "request_id": self.request_id,
            "detail": self.detail,
            "resolved_a_waiting_start": self.resolved_a_waiting_start,
        }


def answer(
    db_path: str,
    request_id: str,
    *,
    nonce: str,
    approve: bool,
    decided_by: str = "operator",
    note: str | None = None,
    tenant_id: str | None = None,
) -> AnswerOutcome:
    """Decide one pending ask. Requires its nonce. Resolves at most one start.

    `tenant_id`, when given, must match the ask's: a person answers their own
    asks, and a mismatch is reported as unknown rather than as a hint that
    someone else's ask exists.

    Synchronous and safe to call from any thread: the waiting Future is
    resolved on its own loop via `call_soon_threadsafe`.
    """
    rid = str(request_id or "").strip()
    row = _load(db_path, rid)
    if row is None:
        return AnswerOutcome("unknown", rid, "No such consent request.")
    if tenant_id is not None and str(row["tenant_id"]) != str(tenant_id):
        return AnswerOutcome("unknown", rid, "No such consent request.")
    if row["decision"] is not None:
        return AnswerOutcome(
            "already_decided",
            rid,
            f"This request was already {row['decision']}; a decision is not retroactive.",
        )
    if _parse(row["expires_at"]) <= _now():
        _mark_if_undecided(db_path, rid, DECISION_EXPIRED)
        return AnswerOutcome(
            "expired",
            rid,
            "This request expired unanswered; the start was denied.",
        )
    presented = str(nonce or "")
    if not presented or not hmac.compare_digest(presented, str(row["answer_nonce"])):
        logger.warning("Device consent %s: answer refused, nonce did not match", rid)
        return AnswerOutcome("refused", rid, "The answer did not carry this request's nonce.")

    decision = DECISION_APPROVED if approve else DECISION_DENIED
    _decide(db_path, rid, decision, decided_by=decided_by, note=note)

    with _LOCK:
        pending = _PENDING.get(rid)
    waiting = False
    if pending is not None:

        def _resolve(fut: asyncio.Future = pending.future, value: bool = bool(approve)) -> None:
            if not fut.done():
                fut.set_result(value)

        pending.loop.call_soon_threadsafe(_resolve)
        waiting = True
    logger.info("Device consent %s %s by %s", rid, decision, decided_by)
    detail = f"Consent {decision}. " + (
        "The waiting start will now proceed through its remaining gates."
        if approve and waiting
        else (
            "The waiting start has been refused."
            if waiting
            else "No start was waiting in this process for this answer; nothing was started."
        )
    )
    return AnswerOutcome(decision, rid, detail, resolved_a_waiting_start=waiting)


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def list_pending(
    db_path: str,
    *,
    tenant_id: str | None = None,
    include_nonce: bool = False,
) -> list[dict[str, Any]]:
    """Open, unexpired asks. `include_nonce` is for a surface that reads the
    database directly on the operator's own machine -- never for HTTP."""
    conn = connect(db_path)
    try:
        conn.row_factory = _row_factory
        ensure_schema_on(conn)
        sql = f"SELECT * FROM {TABLE} WHERE decision IS NULL AND expires_at > ?"
        params: list[Any] = [_iso(_now())]
        if tenant_id:
            sql += " AND tenant_id = ?"
            params.append(str(tenant_id))
        sql += " ORDER BY created_at ASC"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        item = dict(row)
        if not include_nonce:
            item.pop("answer_nonce", None)
        item["seconds_remaining"] = max(
            0,
            int((_parse(row["expires_at"]) - _now()).total_seconds()),
        )
        out.append(item)
    return out


def describe() -> dict[str, Any]:
    with _LOCK:
        open_asks = len(_PENDING)
    return {
        "installed": is_installed(),
        "configured": bool(_STATE["db_path"]),
        "ttl_seconds": ttl_seconds(),
        "open_asks": open_asks,
        "max_pending_per_tenant": MAX_PENDING_PER_TENANT,
    }


def reset_for_tests() -> None:
    """Forget every open ask and the configuration. Tests only."""
    with _LOCK:
        for pending in _PENDING.values():
            try:
                pending.loop.call_soon_threadsafe(
                    lambda f=pending.future: (not f.done()) and f.set_result(False),
                )
            except RuntimeError:
                pass
        _PENDING.clear()
        _STATE["db_path"] = None
        _STATE["ttl_seconds"] = DEFAULT_TTL_SECONDS
    uninstall()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _row_factory(cursor: Any, row: tuple) -> dict[str, Any]:
    return {d[0]: row[i] for i, d in enumerate(cursor.description)}


def ensure_schema_on(conn: Any) -> None:
    conn.execute(_SCHEMA)


def _insert(
    db_path: str,
    request_id: str,
    nonce: str,
    request: DeviceConsentRequest,
    tenant: str,
    created: datetime,
    expires: datetime,
) -> None:
    conn = connect(db_path)
    try:
        ensure_schema_on(conn)
        conn.execute(
            f"INSERT INTO {TABLE} (request_id, answer_nonce, tenant_id, principal_id, "
            "device_id, modality, prompt, correlation_id, session_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request_id,
                nonce,
                tenant,
                str(request.principal_id or ""),
                str(request.device_id or ""),
                str(request.modality or ""),
                str(request.prompt),
                request.correlation_id,
                request.session_id,
                _iso(created),
                _iso(expires),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _load(db_path: str, request_id: str) -> dict[str, Any] | None:
    conn = connect(db_path)
    try:
        conn.row_factory = _row_factory
        ensure_schema_on(conn)
        return conn.execute(
            f"SELECT * FROM {TABLE} WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    finally:
        conn.close()


def _decide(
    db_path: str,
    request_id: str,
    decision: str,
    *,
    decided_by: str,
    note: str | None,
) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            f"UPDATE {TABLE} SET decision = ?, decided_at = ?, decided_by = ?, note = ? "
            "WHERE request_id = ? AND decision IS NULL",
            (decision, _iso(_now()), decided_by, note, request_id),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_if_undecided(db_path: str, request_id: str, decision: str) -> None:
    try:
        conn = connect(db_path)
    except Exception:
        logger.exception("Could not open the kernel database to mark %s %s", request_id, decision)
        return
    try:
        conn.execute(
            f"UPDATE {TABLE} SET decision = ?, decided_at = ? WHERE request_id = ? AND decision IS NULL",
            (decision, _iso(_now()), request_id),
        )
        conn.commit()
    except Exception:
        logger.exception("Could not mark %s %s", request_id, decision)
    finally:
        conn.close()


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "DECISION_ABANDONED",
    "DECISION_APPROVED",
    "DECISION_DENIED",
    "DECISION_EXPIRED",
    "MAX_PENDING_PER_TENANT",
    "AnswerOutcome",
    "DeviceConsentError",
    "answer",
    "ask",
    "configure",
    "describe",
    "ensure_schema",
    "install",
    "is_installed",
    "list_pending",
    "reset_for_tests",
    "ttl_seconds",
    "uninstall",
]
