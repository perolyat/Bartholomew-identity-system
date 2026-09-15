"""Windows writer-lock / WAL reliability repair (2026-09): the event-loop convoy.

The defect, stated once
-----------------------
`MemoryStore` writes through aiosqlite. aiosqlite runs each call on a worker
thread and hands the result back through the event loop, so a write is two
round trips: ``await db.execute("INSERT ...")`` (the worker now holds SQLite's
one write lock) and ``await db.commit()`` (the worker releases it). Between
those two awaits the lock is held by a thread whose *only* way to release it
is for the event loop to run the coroutine one more step.

Every scheduler drive records a Reflection that way, several times a minute.

A synchronous ``sqlite3`` write made on the event-loop thread inside that
window blocks the loop while waiting for the lock. The holder cannot commit,
because committing needs the loop that is blocked. So the writer waits its
whole ``busy_timeout`` (5 s through `db_ctx.set_wal_pragmas`) and then fails
with ``database is locked`` -- deterministically, once the window is hit. On a
loaded Windows CI runner the window is hit often. That is the mechanism behind
`tests/test_notifications_api.py`'s HTTP 400 (`notify._save_settings` plus the
permission audit write, both on the loop), and behind FND-04's failing
vertical slice (`eci/store.record_directive`, on the loop inside the spoken
output seam's `speak_fn`, with a drive's Reflection write mid-transaction --
the same run's `self_check` tick shows ``dur_ms=5000``).

The repair is the discipline the repository already had (Phase B stage B2):
a statement that can wait for the write lock never runs on the loop thread.
Off the loop, the same write waits the few milliseconds until the commit lands.

What these tests prove
----------------------
* `test_the_mechanism...` -- the convoy itself, in the smallest form, so the
  claim above is executable rather than narrative.
* The ``..._completes_while_a_memory_write_is_in_flight`` tests -- each
  repaired production entry point, run against a real in-flight `MemoryStore`
  transaction that only the event loop can release. Under the pre-repair code
  each one blocks the loop, waits the full 5 s, and either fails outright or
  silently loses its audit row; after the repair each completes in well under
  a second. They would have failed on `main` at `57f86f8`.
* `test_the_repaired_paths_write_nothing...` -- the structural invariant, via
  `tests/helpers/event_loop_sqlite.py`: no lock-waiting statement on a loop
  thread from any repaired path, holder or no holder. This is the guard
  against the defect being reintroduced one call site at a time.
"""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite
import pytest

from bartholomew.eci import capabilities as caps
from bartholomew.eci import store as eci_store
from bartholomew.eci.boundary import DirectiveIssuer, submit
from bartholomew.eci.contract import (
    CapabilityRef,
    DirectiveStatus,
    EndpointIdentity,
    ExchangeKind,
    ExchangeOutcome,
    InboundExchange,
    utc_now,
)
from bartholomew.integration.eci_responder import (
    SPOKEN_OUTPUT_KIND,
    SPOKEN_OUTPUT_VERSION,
    SpokenAcknowledgementResponder,
)
from bartholomew.kernel.db_ctx import connect, set_wal_pragmas, wal_db
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.scheduler.drives import drive_fts_optimize
from bartholomew.kernel.skill_base import SkillContext
from bartholomew.kernel.skill_permissions import PermissionChecker
from bartholomew.skills.calendar_draft import CalendarDraftSkill
from bartholomew.skills.notify import NotifySkill
from bartholomew.skills.tasks import TasksSkill
from tests.helpers.event_loop_sqlite import forbid_sqlite_writes_on_event_loop

#: `set_wal_pragmas` sets `busy_timeout = 5000`, so a convoyed writer takes at
#: least this long to fail. A repaired writer completes as soon as the holder
#: commits (`RELEASE_AFTER_S` below, plus milliseconds). The budget sits well
#: clear of both, so a slow runner cannot turn a pass into a false failure.
BUSY_TIMEOUT_S = 5.0
RELEASE_AFTER_S = 0.3
COMPLETION_BUDGET_S = 2.5

SPEAK = CapabilityRef(kind=SPOKEN_OUTPUT_KIND, version=SPOKEN_OUTPUT_VERSION)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _HeldAiosqliteWrite:
    """A real aiosqlite transaction held open across event-loop turns.

    `execute()` has run on the aiosqlite worker thread, so the write lock is
    held; `commit()` has not been submitted. That is exactly the state
    `MemoryStore.insert_reflection()` is in between its two awaits, and
    exactly like it, nothing but the event loop can end it.
    """

    def __init__(self, db_path: str, sql: str, params: tuple[Any, ...] = ()):
        self.db_path = db_path
        self._sql = sql
        self._params = params
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> _HeldAiosqliteWrite:
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute(self._sql, self._params)
        return self

    async def release(self) -> None:
        if self._db is not None:
            await self._db.commit()

    async def __aexit__(self, *exc: object) -> None:
        db, self._db = self._db, None
        if db is not None:
            await db.commit()
            await db.close()


def _held_memory_write(db_path: str) -> _HeldAiosqliteWrite:
    """The production holder: a Reflection row, mid-transaction."""
    return _HeldAiosqliteWrite(
        db_path,
        "INSERT INTO reflections(kind, content, meta, ts, pinned) VALUES (?, ?, ?, ?, 0)",
        ("convoy_probe", "held across the loop", None, "2026-09-14T00:00:00Z"),
    )


async def _race(
    holder: _HeldAiosqliteWrite,
    work: Callable[[], Awaitable[Any]],
    *,
    release_after: float = RELEASE_AFTER_S,
) -> tuple[Any, float]:
    """Run `work` while the holder commits `release_after` seconds later.

    The holder's commit is an ordinary coroutine on the same loop. If `work`
    blocks the loop synchronously waiting for the lock, that commit cannot run
    until `work` gives up -- the convoy. If `work` waits off the loop, the
    commit runs on time and `work` completes right after it.
    """

    async def release() -> None:
        await asyncio.sleep(release_after)
        await holder.release()

    started = time.monotonic()
    result, _ = await asyncio.gather(work(), release())
    return result, time.monotonic() - started


async def _permission_outcome(skill: Any, permission: str) -> Any:
    """Call a skill's permission self-check whatever its shape.

    Awaitable on the repaired tree; a plain call on the tree before it. Testing
    both shapes lets the tests that reach it fail on the unrepaired code for
    the defect, not for the changed signature.
    """
    outcome = skill._require_permission(permission)
    if inspect.isawaitable(outcome):
        outcome = await outcome
    return outcome


async def _kernel_db(path) -> str:
    """A database with the kernel's own schema, the way the daemon creates it."""
    db_path = str(path)
    await MemoryStore(db_path).init()
    return db_path


def _query(db_path: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    conn = connect(db_path)
    try:
        set_wal_pragmas(conn)
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


async def _skill(cls: type, db_path: str, check: Callable[[str], bool] | None = None):
    skill = cls()
    await skill.initialize(
        SkillContext(db_path=db_path, check_permission=check or (lambda _permission: True)),
    )
    return skill


class _Device:
    """Stand-in for `platform.devices.VerifiedDevice`, duck-typed as the ECI reads it."""

    def __init__(self) -> None:
        self.manifest = self

    def declares(self, kind: str, version: int) -> bool:
        return kind == SPEAK.kind and version == SPEAK.version

    def authorizes(self, kind: str, version: int) -> bool:
        return self.declares(kind, version)


ENDPOINT = EndpointIdentity(
    endpoint_id="dev-convoy",
    user_id="user-convoy",
    verified_by="eci-device-credential",
    endpoint_kind="windows",
)


def _ready_issuer(ledger_db: str) -> DirectiveIssuer:
    """An issuer whose endpoint has declared and reported the speech capability."""
    eci_store.ensure_schema(ledger_db)
    eci_store.record_availability(
        ledger_db,
        ENDPOINT.endpoint_id,
        caps.AvailabilityReport(capability=SPEAK, available=True, reported_at=utc_now()),
    )
    return DirectiveIssuer(endpoint=ENDPOINT, device=_Device(), db_path=ledger_db, now=utc_now())


def _request(exchange_id: str) -> InboundExchange:
    return InboundExchange(
        endpoint=ENDPOINT,
        exchange_id=exchange_id,
        kind=ExchangeKind.REQUEST,
        payload={"text": "are you there?"},
    )


class _DriveCtx:
    """The minimal duck-typed context `drive_fts_optimize` reads."""

    def __init__(self, db_path: str):
        self.mem = MemoryStore(db_path)


def _fts_available(db_path: str) -> bool:
    return bool(
        _query(db_path, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_fts'"),
    )


# ---------------------------------------------------------------------------
# 1. The mechanism, executable.
# ---------------------------------------------------------------------------


async def test_the_mechanism_a_writer_on_the_loop_cannot_outwait_an_aiosqlite_commit(tmp_path):
    db_path = str(tmp_path / "convoy.db")
    with wal_db(db_path) as conn:
        conn.execute("CREATE TABLE t(x INTEGER)")
        conn.commit()

    def write_through_the_authority(busy_timeout_ms: int) -> None:
        with wal_db(db_path) as conn:
            conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            conn.execute("INSERT INTO t(x) VALUES (2)")
            conn.commit()

    async with _HeldAiosqliteWrite(db_path, "INSERT INTO t(x) VALUES (1)") as held:
        # On the loop thread: the holder's commit is queued behind this call,
        # so the busy handler retries for its whole budget and then gives up.
        # A short budget keeps the demonstration short; the outcome does not
        # depend on its length, because nothing can deliver the commit.
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            write_through_the_authority(500)
        assert time.monotonic() - started >= 0.45, "it did not even wait its busy_timeout"

        # The identical write, off the loop, with the loop free to deliver the
        # holder's commit: it waits for the commit and then succeeds. It waits
        # with the production budget (5 s, `set_wal_pragmas`): on a loaded
        # runner the loop can take far longer than the 0.1 s release delay to
        # get the holder's commit onto the aiosqlite thread, and a writer that
        # gives up first would report the convoy where there is none. The
        # completion budget below still bounds it well under that.
        _, elapsed = await _race(
            held,
            lambda: asyncio.to_thread(write_through_the_authority, 5000),
            release_after=0.1,
        )
        assert elapsed < COMPLETION_BUDGET_S

    assert _query(db_path, "SELECT x FROM t ORDER BY x") == [(1,), (2,)]


# ---------------------------------------------------------------------------
# 2. Each repaired production writer, against a real in-flight Memory write.
# ---------------------------------------------------------------------------


async def test_notify_quiet_hours_write_completes_while_a_memory_write_is_in_flight(tmp_path):
    db_path = await _kernel_db(tmp_path / "kernel.db")
    notify = await _skill(NotifySkill, db_path)

    async with _held_memory_write(db_path) as held:
        result, elapsed = await _race(
            held,
            lambda: notify.execute("set_quiet_hours", {"start": "21:00", "end": "08:00"}),
        )

    assert result.success, result.error
    assert elapsed < COMPLETION_BUDGET_S, f"took {elapsed:.2f}s: the write convoyed on the loop"
    assert _query(db_path, "SELECT quiet_hours_start FROM notification_settings") == [("21:00",)]


async def test_notify_send_and_mute_writes_complete_while_a_memory_write_is_in_flight(tmp_path):
    db_path = await _kernel_db(tmp_path / "kernel.db")
    notify = await _skill(NotifySkill, db_path)

    async with _held_memory_write(db_path) as held:
        sent, elapsed = await _race(held, lambda: notify.execute("send", {"message": "hello"}))
    assert sent.success, sent.error
    assert elapsed < COMPLETION_BUDGET_S, f"took {elapsed:.2f}s: the write convoyed on the loop"

    async with _held_memory_write(db_path) as held:
        muted, elapsed = await _race(held, lambda: notify.execute("mute"))
    assert muted.success, muted.error
    assert elapsed < COMPLETION_BUDGET_S, f"took {elapsed:.2f}s: the write convoyed on the loop"
    assert _query(db_path, "SELECT muted FROM notification_settings") == [(1,)]


async def test_tasks_create_write_completes_while_a_memory_write_is_in_flight(tmp_path):
    db_path = await _kernel_db(tmp_path / "kernel.db")
    tasks = await _skill(TasksSkill, db_path)

    async with _held_memory_write(db_path) as held:
        result, elapsed = await _race(held, lambda: tasks.execute("create", {"title": "probe"}))

    assert result.success, result.error
    assert elapsed < COMPLETION_BUDGET_S, f"took {elapsed:.2f}s: the write convoyed on the loop"
    assert _query(db_path, "SELECT title FROM skill_tasks") == [("probe",)]


async def test_calendar_create_write_completes_while_a_memory_write_is_in_flight(tmp_path):
    db_path = await _kernel_db(tmp_path / "kernel.db")
    calendar = await _skill(CalendarDraftSkill, db_path)

    async with _held_memory_write(db_path) as held:
        result, elapsed = await _race(
            held,
            lambda: calendar.execute(
                "create",
                {"title": "probe", "start": "2026-09-15T10:00:00"},
            ),
        )

    assert result.success, result.error
    assert elapsed < COMPLETION_BUDGET_S, f"took {elapsed:.2f}s: the write convoyed on the loop"


async def test_a_skills_permission_audit_write_completes_while_a_memory_write_is_in_flight(
    tmp_path,
):
    """The audit row `PermissionChecker.check()` writes is a required record
    (WP-A2 / S2). On the loop it was lost after 5 s; off the loop it lands."""
    db_path = await _kernel_db(tmp_path / "kernel.db")
    checker = PermissionChecker(db_path=db_path, auto_permissions={"probe": ["memory.write"]})
    tasks = await _skill(
        TasksSkill,
        db_path,
        check=lambda permission: checker.check("probe", permission).granted,
    )

    async with _held_memory_write(db_path) as held:
        denial, elapsed = await _race(held, lambda: _permission_outcome(tasks, "memory.write"))

    assert denial is None
    assert elapsed < COMPLETION_BUDGET_S, f"took {elapsed:.2f}s: the audit write convoyed"
    assert _query(
        db_path,
        "SELECT result FROM permission_audit WHERE skill_id = 'probe'",
    ) == [("granted_auto",)], "the required permission_audit row was lost"


async def test_eci_directive_issue_completes_while_a_memory_write_is_in_flight(tmp_path):
    """FND-04's failure, isolated: the directive ledger write inside `speak_fn`.

    The responder's own seam reads the brake and writes its Reflection into a
    second database, so the ledger write is the first thing that waits for
    the held lock -- the shape the CI failure had.
    """
    ledger_db = await _kernel_db(tmp_path / "ledger.db")
    seam_db = await _kernel_db(tmp_path / "seam.db")
    issuer = _ready_issuer(ledger_db)
    responder = SpokenAcknowledgementResponder(
        db_path=seam_db,
        runtime_cfg={"voice": {"spoken_output": True}},
    )

    async with _held_memory_write(ledger_db) as held:
        directive, elapsed = await _race(held, lambda: responder.respond(_request("e1"), issuer))

    assert directive is not None, "no directive was issued: the ledger write failed"
    assert elapsed < COMPLETION_BUDGET_S, f"took {elapsed:.2f}s: the ledger write convoyed"
    assert eci_store.get_directive(ledger_db, directive.correlation_id) is not None


async def test_fts_optimize_drive_completes_while_a_memory_write_is_in_flight(tmp_path, capsys):
    db_path = await _kernel_db(tmp_path / "kernel.db")
    if not _fts_available(db_path):
        pytest.skip("this SQLite build has no FTS5, so there is no index to optimize")

    async with _held_memory_write(db_path) as held:
        _, elapsed = await _race(held, lambda: drive_fts_optimize(_DriveCtx(db_path)))

    assert elapsed < COMPLETION_BUDGET_S, f"took {elapsed:.2f}s: the optimize convoyed"
    assert "Error optimizing FTS index" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 3. The invariant: nothing repaired writes on the loop thread, ever.
# ---------------------------------------------------------------------------


async def test_the_repaired_paths_write_nothing_to_sqlite_on_the_event_loop_thread(tmp_path):
    db_path = await _kernel_db(tmp_path / "kernel.db")
    checker = PermissionChecker(db_path=db_path, auto_permissions={"probe": ["memory.write"]})
    notify = await _skill(NotifySkill, db_path)
    tasks = await _skill(TasksSkill, db_path)
    calendar = await _skill(CalendarDraftSkill, db_path)
    audited = await _skill(
        TasksSkill,
        db_path,
        check=lambda permission: checker.check("probe", permission).granted,
    )
    issuer = _ready_issuer(db_path)
    responder = SpokenAcknowledgementResponder(
        db_path=db_path,
        runtime_cfg={"voice": {"spoken_output": True}},
    )

    with forbid_sqlite_writes_on_event_loop() as log:
        assert (await notify.execute("set_quiet_hours", {"start": "21:00", "end": "08:00"})).success
        assert (await notify.execute("mute")).success
        assert (await notify.execute("unmute")).success
        assert (await notify.execute("send", {"message": "hello"})).success
        created = await tasks.execute("create", {"title": "probe"})
        assert created.success
        assert (await tasks.execute("complete", {"task_id": created.data["id"]})).success
        assert (await tasks.execute("delete", {"task_id": created.data["id"]})).success
        assert (
            await calendar.execute("create", {"title": "probe", "start": "2026-09-15T10:00:00"})
        ).success
        assert await _permission_outcome(audited, "memory.write") is None

        directive = await responder.respond(_request("e1"), issuer)
        assert directive is not None
        receipt = await submit(
            InboundExchange(
                endpoint=ENDPOINT,
                exchange_id="r1",
                kind=ExchangeKind.RESULT,
                payload={},
                correlation_id=directive.correlation_id,
                status=DirectiveStatus.SUCCEEDED,
            ),
            device=_Device(),
            db_path=db_path,
        )
        assert receipt.outcome is ExchangeOutcome.ACCEPTED, receipt.reason

        if _fts_available(db_path):
            await drive_fts_optimize(_DriveCtx(db_path))

    assert log.production_writes() == [], (
        "SQLite writes ran on the event-loop thread:\n" + log.describe()
    )
