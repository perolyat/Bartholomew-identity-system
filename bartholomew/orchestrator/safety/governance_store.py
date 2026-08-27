"""
Governance schema and Parking Brake persistence (Phase B stages B3, B5).

A governance-owned schema (parking_brake_state, governance_audit) for
Parking Brake state -- narrow, separate from MemoryStore's own schema,
closing the ownership violation docs/B0_PERSISTENCE_BASELINE.md found:
today's brake state lives in system_flags, a table MemoryStore's own
SCHEMA creates and seeds.

Deliberately NOT yet the runtime's shared instance: bartholomew.orchestrator
.safety.parking_brake's ParkingBrake/BrakeStorage are untouched and remain
the live, wired-in implementation, still reading/writing system_flags.
GovernanceStore below is a new, separate, isolated-tested class -- wiring
it in as the runtime's shared instance is B4's job, not this stage's. See
docs/B3_GOVERNANCE_PERSISTENCE.md.

Deliberately synchronous: this module is not called from the event loop
anywhere yet (see above), so Phase B stage B2's off-loop pattern does not
apply here -- B4 is responsible for wiring this in through
bartholomew.kernel.blocking_executor.run_off_loop() the same way
construct_parking_brake_off_loop() already does for the current
ParkingBrake, when it actually becomes reachable from async code.

Design notes on the two non-negotiable invariants this module implements:
- Revision-guarded loosening: engage() (tightening) always applies --
  never refused, matching "the brake can only become more restrictive
  without an explicit, confirmed loosening action." disengage()
  (loosening) defaults to checking the calling instance's own
  last-loaded revision against the currently-persisted one, raising
  StaleGovernanceWriteError instead of silently regressing a more-recent
  state if they don't match -- the "confirmed" part of that invariant.
- Accepted limitation, not fixed here: engage() keeps simple replace
  semantics (this stage's approved direction), not a union of scopes.
  With only one real production caller today (bartholomew/cli.py's
  `brake on`, per docs/B0_PERSISTENCE_BASELINE.md Sec 5/6 and B3's own
  planning pass), a stale engage() from an old snapshot could in theory
  replace a concurrently-more-restrictive state with a less-restrictive
  one -- building union/monotonic-widening semantics for a caller that
  doesn't exist yet was explicitly deferred, to be revisited only if B4's
  live-daemon caller re-inventory finds a real concurrent caller.

Phase B stage B5 adds the write-fence/clean-marker (brake_runtime) and
Startup Incident Log (startup_incidents) schema the B3 planning pass
deferred here -- kept in this module rather than a separate
daemon-lifecycle module because both are fundamentally a Governance
concern (a trust assertion about whether the previous runtime completed
cleanly), and Governance is this project's single authority for runtime
integrity state, not something split across persistence systems.
KernelDaemon (daemon.py) orchestrates USING these methods -- deciding
when to run an integrity check, what counts as "obviously recoverable",
and what belongs in an incident record -- since only the daemon knows
its own resource-startup sequence; GovernanceStore itself only owns the
persistence and the fence enforcement. See
docs/B5_STARTUP_SHUTDOWN_INTEGRITY.md.

Phase B stage B6 retires bartholomew/cli.py's last legacy Governance
write path (`brake on`/`brake off` now call engage()/disengage() on this
module's GovernanceStore directly instead of the old system_flags-backed
ParkingBrake/BrakeStorage), which also retires
bartholomew.orchestrator.safety.governance_bridge -- B4's temporary
dual-check bridge that existed only because the CLI's legacy write path
could otherwise silently diverge from this store. With nothing left
writing the legacy value, a plain read of this store is sufficient
again. `is_blocked_fail_closed()`/`is_blocked_fail_closed_off_loop()`
below are deliberately named and shaped as drop-in replacements for the
bridge's identically-named functions, so B6's call-site changes are a
one-line import swap, not a rewrite. See
docs/B6_EXTERNAL_GOVERNANCE_CLI_SAFETY.md.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from bartholomew.kernel.blocking_executor import SingleWorkerExecutor, run_off_loop
from bartholomew.kernel.db_ctx import connect, set_wal_pragmas

_STATE_ROW_ID = 1
_RUNTIME_ROW_ID = 1


class StaleGovernanceWriteError(RuntimeError):
    """Raised when disengage()'s expected_revision no longer matches the
    currently-persisted revision -- the caller's view of state is stale,
    and the write is refused rather than silently regressing a
    more-recent, more-restrictive state. Callers should reload state
    (GovernanceStore(db_path) or .refresh()) and decide whether to retry,
    not blindly resubmit the same stale write."""


class WriteFenceClosedError(RuntimeError):
    """Raised when engage()/disengage() is attempted while brake_runtime's
    write fence is closed (Phase B stage B5) -- shutdown has begun (or
    completed) and no further Governance writes are accepted. Does not
    apply to the fence-management operations themselves (open_new_runtime,
    close_write_fence, mark_clean_shutdown), which are the mechanism that
    controls the fence, not writes gated by it."""


@dataclass(frozen=True)
class GovernanceState:
    """Parking brake state snapshot, with the revision it was read at."""

    engaged: bool
    scopes: frozenset[str]
    revision: int


@dataclass(frozen=True)
class RuntimeMarker:
    """brake_runtime's persisted state: the current (or most recent)
    runtime's identity and whether its shutdown was confirmed clean."""

    runtime_id: str
    started_at: int
    clean: bool
    write_fence_open: bool


def _ensure_actor_column(conn) -> None:
    """
    Additive migration (Stage 1, S1.1): older governance_audit tables were
    created before the `actor` column existed. CREATE TABLE IF NOT EXISTS
    above is a no-op against an existing table, so this backfills the
    column on databases that predate it. Safe to call every time
    ensure_schema() runs -- checking PRAGMA table_info() first makes it
    idempotent against a database that already has the column.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(governance_audit)").fetchall()}
    if "actor" not in cols:
        conn.execute("ALTER TABLE governance_audit ADD COLUMN actor TEXT")


def ensure_schema(db_path: str) -> None:
    """
    Create parking_brake_state and governance_audit if missing, and
    perform the additive, idempotent legacy system_flags import.

    Safe to call every time a GovernanceStore is constructed, or
    independently (e.g. at startup, once B4 wires this in) -- every
    statement is IF NOT EXISTS / conditional on the table already having
    a row, so re-running this after a partial or interrupted prior run
    reaches the same end state without duplicating anything.
    """
    conn = connect(db_path)
    try:
        set_wal_pragmas(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS parking_brake_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                engaged INTEGER NOT NULL DEFAULT 0,
                scopes TEXT NOT NULL DEFAULT '[]',
                revision INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )
            """,
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS governance_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                action TEXT NOT NULL,
                scopes TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                actor TEXT
            )
            """,
        )
        _ensure_actor_column(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS brake_runtime (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                runtime_id TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                clean INTEGER NOT NULL DEFAULT 0,
                write_fence_open INTEGER NOT NULL DEFAULT 1
            )
            """,
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS startup_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                runtime_id TEXT,
                lifecycle_state_reached TEXT NOT NULL,
                exception_type TEXT,
                exception_message TEXT,
                traceback TEXT,
                resources_started TEXT NOT NULL,
                resources_not_started TEXT NOT NULL,
                previous_shutdown_clean INTEGER,
                integrity_checks_performed TEXT NOT NULL,
                recovery_actions_attempted TEXT NOT NULL,
                final_outcome TEXT NOT NULL
            )
            """,
        )
        conn.commit()

        _import_legacy_state_if_needed(conn)
    finally:
        conn.close()


def _import_legacy_state_if_needed(conn) -> None:
    """
    If parking_brake_state has no singleton row yet, seed it -- importing
    system_flags' pre-existing "parking_brake" row if one exists (additive,
    non-destructive: system_flags/the legacy ParkingBrake are untouched),
    else a fresh disengaged default. Idempotent: a second call finds the
    singleton row already present and does nothing further.
    """
    existing = conn.execute(
        "SELECT id FROM parking_brake_state WHERE id = ?",
        (_STATE_ROW_ID,),
    ).fetchone()
    if existing is not None:
        return

    system_flags_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'system_flags'",
    ).fetchone()
    legacy_row = None
    if system_flags_exists is not None:
        legacy_row = conn.execute(
            "SELECT value FROM system_flags WHERE key = 'parking_brake'",
        ).fetchone()

    now = int(time.time())
    if legacy_row is not None:
        data = json.loads(legacy_row[0])
        engaged = bool(data.get("engaged"))
        scopes = sorted(set(data.get("scopes", [])))
        revision = 1
        action = "migrated"
    else:
        engaged = False
        scopes = []
        revision = 0
        action = None  # fresh default; not itself a transition worth auditing

    conn.execute(
        "INSERT INTO parking_brake_state (id, engaged, scopes, revision, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (_STATE_ROW_ID, int(engaged), json.dumps(scopes), revision, now),
    )
    if action is not None:
        conn.execute(
            "INSERT INTO governance_audit (ts, action, scopes, reason, revision, actor) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                now,
                action,
                json.dumps(scopes),
                "imported from legacy system_flags row",
                revision,
                "migration",
            ),
        )
    conn.commit()


class GovernanceStore:
    """
    Isolated, tested Parking Brake persistence against the new governance
    schema. Not yet the runtime's shared instance -- see this module's
    docstring.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        ensure_schema(db_path)
        self._cache = self._load()

    def _load(self) -> GovernanceState:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT engaged, scopes, revision FROM parking_brake_state WHERE id = ?",
                (_STATE_ROW_ID,),
            ).fetchone()
        finally:
            conn.close()
        engaged, scopes_json, revision = row
        return GovernanceState(bool(engaged), frozenset(json.loads(scopes_json)), revision)

    def refresh(self) -> GovernanceState:
        """Re-read persisted state, replacing this instance's cached view."""
        self._cache = self._load()
        return self._cache

    def state(self) -> GovernanceState:
        return self._cache

    def is_blocked(self, scope: str) -> bool:
        st = self._cache
        return st.engaged and ("global" in st.scopes or scope in st.scopes)

    def list_audit(self, limit: int = 50) -> list[dict]:
        """
        Most recent governance_audit entries, newest first (Stage 1, S1.5).
        Read-only; does not touch or require self._cache. `scopes` is
        decoded from its persisted JSON string into a list for each row.
        """
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id, ts, action, scopes, reason, revision, actor "
                "FROM governance_audit ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "id": row[0],
                "ts": row[1],
                "action": row[2],
                "scopes": json.loads(row[3]),
                "reason": row[4],
                "revision": row[5],
                "actor": row[6],
            }
            for row in rows
        ]

    def engage(
        self,
        *scopes: str,
        reason: str | None = None,
        actor: str | None = None,
    ) -> GovernanceState:
        """
        Engage (tighten). Always applies -- monotonic tightening is never
        refused, regardless of this instance's cached revision. Replace
        semantics: the given scopes replace the persisted set (see this
        module's docstring for the accepted limitation this carries).

        `actor` identifies who/what requested the transition (e.g. "cli",
        "user", a future authenticated identity) for the audit trail --
        purely descriptive, not verified or authorized here (see the
        `governance` API route module for the current, no-auth caveat).
        """
        scopes_set = set(scopes) if scopes else {"global"}
        return self._write(
            engaged=True,
            scopes=scopes_set,
            action="engaged",
            reason=reason,
            actor=actor,
        )

    def disengage(
        self,
        *,
        reason: str | None = None,
        expected_revision: int | None = None,
        actor: str | None = None,
    ) -> GovernanceState:
        """
        Disengage (loosen). Refused with StaleGovernanceWriteError if
        `expected_revision` (defaulting to this instance's own
        last-loaded revision) no longer matches the currently-persisted
        revision -- i.e. someone else changed state since this instance's
        view was taken. Callers that genuinely want to force a loosening
        regardless of concurrent changes must pass
        expected_revision=<the value read from a fresh refresh()>,
        making that an explicit, visible choice rather than an accident
        of stale caching.

        `actor`: see engage()'s docstring.
        """
        if expected_revision is None:
            expected_revision = self._cache.revision
        return self._write(
            engaged=False,
            scopes=set(),
            action="disengaged",
            reason=reason,
            expected_revision=expected_revision,
            actor=actor,
        )

    def _write(
        self,
        *,
        engaged: bool,
        scopes: set[str],
        action: str,
        reason: str | None,
        expected_revision: int | None = None,
        actor: str | None = None,
    ) -> GovernanceState:
        """
        Writes the new state and its audit record in one transaction --
        the two are never observable independently: a crash before commit
        leaves both absent, a crash after commit leaves both present.

        expected_revision, when given, guards the write with an atomic
        `UPDATE ... WHERE revision = ?`: if no row matches (someone else
        already advanced the revision), the write is rolled back and
        StaleGovernanceWriteError is raised instead of committing.

        Refuses with WriteFenceClosedError (Phase B stage B5) if
        brake_runtime's write fence is closed -- a missing brake_runtime
        row (no KernelDaemon lifecycle has called open_new_runtime() on
        this db_path) is treated as "fence open" (permissive default),
        matching this class's existing pattern for other optional
        machinery.
        """
        now = int(time.time())
        conn = connect(self.db_path)
        try:
            set_wal_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")

            fence_row = conn.execute(
                "SELECT write_fence_open FROM brake_runtime WHERE id = ?",
                (_RUNTIME_ROW_ID,),
            ).fetchone()
            if fence_row is not None and not fence_row[0]:
                raise WriteFenceClosedError(
                    "Governance write refused: write fence is closed "
                    "(shutdown in progress or already completed)",
                )

            current_revision = conn.execute(
                "SELECT revision FROM parking_brake_state WHERE id = ?",
                (_STATE_ROW_ID,),
            ).fetchone()[0]

            if expected_revision is not None and expected_revision != current_revision:
                raise StaleGovernanceWriteError(
                    f"disengage() expected revision {expected_revision}, "
                    f"but persisted state is at revision {current_revision} -- "
                    "refusing to loosen based on stale data",
                )

            new_revision = current_revision + 1
            sorted_scopes = sorted(scopes)
            conn.execute(
                "UPDATE parking_brake_state "
                "SET engaged = ?, scopes = ?, revision = ?, updated_at = ? "
                "WHERE id = ?",
                (int(engaged), json.dumps(sorted_scopes), new_revision, now, _STATE_ROW_ID),
            )
            conn.execute(
                "INSERT INTO governance_audit (ts, action, scopes, reason, revision, actor) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, action, json.dumps(sorted_scopes), reason, new_revision, actor),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

        self._cache = GovernanceState(engaged, frozenset(scopes), new_revision)
        return self._cache

    # -- Runtime lifecycle / write-fence (Phase B stage B5) ------------------

    def runtime_marker(self) -> RuntimeMarker | None:
        """Read brake_runtime's current state, or None if no runtime has
        ever called open_new_runtime() against this db_path."""
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT runtime_id, started_at, clean, write_fence_open "
                "FROM brake_runtime WHERE id = ?",
                (_RUNTIME_ROW_ID,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        runtime_id, started_at, clean, write_fence_open = row
        return RuntimeMarker(runtime_id, started_at, bool(clean), bool(write_fence_open))

    def previous_shutdown_was_clean(self) -> bool | None:
        """True/False if a prior runtime recorded its own clean/unclean
        state, None if this is the first runtime ever to open against
        this db_path (nothing to distrust -- there's no prior shutdown to
        have been unclean)."""
        marker = self.runtime_marker()
        return None if marker is None else marker.clean

    def open_new_runtime(self) -> str:
        """
        Start a new runtime lifecycle: generates a fresh runtime_id,
        (re)opens the write fence, and marks this runtime as not-yet-clean
        -- the honest default until mark_clean_shutdown() proves otherwise.
        Must be called before this runtime is considered "the" runtime
        for write-fence purposes; does not itself check or report the
        *previous* runtime's clean state -- call previous_shutdown_was_
        clean() first if that's needed, since this call overwrites it.

        Not gated by the write fence itself -- this is the mechanism that
        controls the fence, not a write the fence gates.
        """
        runtime_id = uuid.uuid4().hex
        now = int(time.time())
        conn = connect(self.db_path)
        try:
            set_wal_pragmas(conn)
            conn.execute(
                "INSERT INTO brake_runtime (id, runtime_id, started_at, clean, write_fence_open) "
                "VALUES (?, ?, ?, 0, 1) "
                "ON CONFLICT(id) DO UPDATE SET "
                "runtime_id = excluded.runtime_id, started_at = excluded.started_at, "
                "clean = 0, write_fence_open = 1",
                (_RUNTIME_ROW_ID, runtime_id, now),
            )
            conn.commit()
        finally:
            conn.close()
        return runtime_id

    def close_write_fence(self) -> None:
        """
        Stop accepting further Governance writes (engage()/disengage(),
        including Phase B stage B4's dual-check bridge mirror writes) --
        the first step of shutdown's write-freeze-and-drain sequence.
        Idempotent. Not gated by the fence itself, for the same reason
        open_new_runtime() isn't.
        """
        conn = connect(self.db_path)
        try:
            set_wal_pragmas(conn)
            conn.execute(
                "UPDATE brake_runtime SET write_fence_open = 0 WHERE id = ?",
                (_RUNTIME_ROW_ID,),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_clean_shutdown(self) -> None:
        """
        Record that this runtime's shutdown completed cleanly -- must
        only be called after every resource this runtime owns has been
        confirmed (not merely requested) to have terminated; the caller
        (KernelDaemon.stop()) is responsible for that ordering. This is
        deliberately the last Governance write of a clean shutdown, per
        the "clean-marker honesty" invariant: the marker reflects
        Python-verified termination, not an assumption.
        """
        conn = connect(self.db_path)
        try:
            set_wal_pragmas(conn)
            conn.execute(
                "UPDATE brake_runtime SET clean = 1 WHERE id = ?",
                (_RUNTIME_ROW_ID,),
            )
            conn.commit()
        finally:
            conn.close()

    def record_startup_incident(
        self,
        *,
        runtime_id: str | None,
        lifecycle_state_reached: str,
        exception_type: str | None = None,
        exception_message: str | None = None,
        traceback: str | None = None,
        resources_started: list[str],
        resources_not_started: list[str],
        previous_shutdown_clean: bool | None,
        integrity_checks_performed: list[str],
        recovery_actions_attempted: list[str],
        final_outcome: str,
    ) -> None:
        """
        Append an immutable Startup Incident Log record (Phase B stage
        B5) -- a separate operational audit trail from normal runtime
        logging, written whenever an unclean shutdown is detected or
        startup fails, intended for diagnostics, reliability trend
        analysis, and eventual self-diagnosis use.
        """
        now = int(time.time())
        conn = connect(self.db_path)
        try:
            set_wal_pragmas(conn)
            conn.execute(
                "INSERT INTO startup_incidents "
                "(ts, runtime_id, lifecycle_state_reached, exception_type, "
                "exception_message, traceback, resources_started, resources_not_started, "
                "previous_shutdown_clean, integrity_checks_performed, "
                "recovery_actions_attempted, final_outcome) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now,
                    runtime_id,
                    lifecycle_state_reached,
                    exception_type,
                    exception_message,
                    traceback,
                    json.dumps(resources_started),
                    json.dumps(resources_not_started),
                    None if previous_shutdown_clean is None else int(previous_shutdown_clean),
                    json.dumps(integrity_checks_performed),
                    json.dumps(recovery_actions_attempted),
                    final_outcome,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def run_quick_integrity_check(db_path: str) -> tuple[bool, str]:
    """
    Lightweight SQLite integrity check (Phase B stage B5) -- `PRAGMA
    quick_check` rather than the more thorough (and much more expensive)
    `PRAGMA integrity_check`, per this stage's "lightweight verification"
    direction. Returns (ok, raw_result) -- ok is True only if the result
    is exactly "ok"; any other result (a list of specific problems, one
    per row in practice) is surfaced verbatim in `raw_result` for the
    incident log, not swallowed.

    Phase B stage B9 (real, adversarial-test-caught bug, not
    hypothetical): sufficiently severe corruption makes `PRAGMA
    quick_check` itself raise `sqlite3.DatabaseError` ("database disk
    image is malformed") instead of returning a descriptive row -- a
    real corrupted file, not a monkeypatched failure, triggered this.
    Caught here and folded into the same (False, ...) contract every
    caller already handles, so the daemon still raises the intended,
    diagnosable `UnsafeStartupError` instead of an unrelated raw
    `DatabaseError` leaking out of what's meant to be a safety check.
    """
    conn = connect(db_path)
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as e:
        return False, f"quick_check raised {type(e).__name__}: {e}"
    finally:
        conn.close()
    result = row[0] if row else "unknown"
    return result == "ok", result


class ParkingBrakeEngagedError(RuntimeError):
    """A state-changing operation was refused because the brake is engaged.

    Raised by operations governed under "inspect, but do not mutate" (see
    `COGNITIVE_RUNTIME.md`'s "The kill-switch: `ParkingBrake`"), where the
    correct outcome is to leave the pending work untouched and resolvable
    once the brake is released -- not to fail it, and not to discard it.
    """

    def __init__(self, message: str, *, scopes: frozenset[str] | None = None):
        super().__init__(message)
        self.scopes = frozenset(scopes or ())


def engaged_state_fail_closed(
    db_path: str,
    *,
    governance_store: GovernanceStore | None = None,
) -> GovernanceState:
    """Refreshed brake state, for operations gated on the brake being engaged
    **at all** rather than on one subsystem scope.

    `is_blocked_fail_closed()` answers "is scope X halted?", which is the
    right question for a subsystem (`skills`, `voice`, `scheduler`, ...).
    Some operations are not a subsystem: resolving a pending consent request
    mutates the user's memory and belongs to no scope in the existing axis,
    so gating it on any single scope would be arbitrary. Those read the
    engaged flag directly.

    Fails closed by construction: a governance read that raises propagates,
    aborting the caller before it mutates anything.
    """
    store = governance_store or GovernanceStore(db_path)
    return store.refresh()


async def engaged_state_fail_closed_off_loop(
    db_path: str,
    *,
    governance_store: GovernanceStore | None = None,
    executor: SingleWorkerExecutor | None = None,
) -> GovernanceState:
    """Off-event-loop wrapper around `engaged_state_fail_closed()`, matching
    `is_blocked_fail_closed_off_loop()`'s pattern -- the read is synchronous
    SQLite and must not run on the event loop."""
    return await run_off_loop(
        engaged_state_fail_closed,
        db_path,
        governance_store=governance_store,
        executor=executor,
    )


# ---------------------------------------------------------------------------
# Additional halt authority (S8 Platform/Admin tier)
# ---------------------------------------------------------------------------
#
# A deployment with a higher-scope halt authority registers a check here. It
# is a *registration hook* rather than an import for one load-bearing reason:
# the dependency must point one way only. This module -- the Personal/User
# brake, the thing a person uses to stop their own Bartholomew -- must never
# depend on any platform, control plane or network, so that a platform outage
# can never leave local autonomous execution unstoppable. Importing the
# platform package here would invert that and put a shared service on the
# local kill switch's path.
#
# The hook composes **restrictively**: execution proceeds only if neither
# authority blocks it, and it can only ever add a halt. It cannot release one,
# because `is_blocked_fail_closed` returns True as soon as the local store
# says so, without consulting the hook at all.
# A holder rather than a bare module global, so registration mutates a
# container instead of rebinding a module attribute.
_HALT_AUTHORITY: dict[str, Callable[[str], bool] | None] = {"check": None}


def register_additional_halt_check(check: Callable[[str], bool] | None) -> None:
    """
    Register (or clear, with None) a higher-scope halt authority.

    `check(scope)` returns True if that authority halts `scope`. It is called
    only when the local brake has *not* already blocked, and any exception it
    raises is treated as a halt -- an unreadable safety halt is not evidence
    of the absence of one.
    """
    _HALT_AUTHORITY["check"] = check


def is_blocked_fail_closed(
    scope: str,
    db_path: str,
    *,
    governance_store: GovernanceStore | None = None,
) -> bool:
    """
    Fail-closed Governance read (Phase B stage B6): a plain refresh() +
    is_blocked() against this store, with no dual-check against the
    legacy system_flags value -- see this module's docstring for why
    that's now sufficient. Kept as a module-level function (mirroring
    the retired governance_bridge.is_blocked_fail_closed()'s name and
    signature) so callers with no live GovernanceStore instance handy
    (e.g. a synchronous, non-daemon caller) can pass just a db_path.

    Since S8 this is also the single composition point for a registered
    higher-scope halt authority (the Platform/Admin tier). Composing here
    rather than at each call site means every downstream execution boundary
    that already consults Governance -- skill execution, the runtime
    contract's autonomous work and its governed state mutations -- gets the
    composed answer without any of them changing.
    """
    store = governance_store or GovernanceStore(db_path)
    store.refresh()
    if store.is_blocked(scope):
        # The local brake alone is sufficient to halt, and is answered
        # without consulting anything else. This is the path that keeps
        # working when every remote service is gone.
        return True

    check = _HALT_AUTHORITY["check"]
    if check is None:
        return False
    try:
        return bool(check(scope))
    except Exception:
        # Fail closed: a higher-scope authority we cannot read is treated as
        # engaged, matching this module's contract for an unreadable brake.
        return True


async def is_blocked_fail_closed_off_loop(
    scope: str,
    db_path: str,
    *,
    governance_store: GovernanceStore | None = None,
    executor: SingleWorkerExecutor | None = None,
) -> bool:
    """
    Off-event-loop wrapper (Phase B stage B2 pattern) around
    is_blocked_fail_closed() -- see run_off_loop()'s docstring for the
    executor/fallback behavior.
    """
    return await run_off_loop(
        is_blocked_fail_closed,
        scope,
        db_path,
        governance_store=governance_store,
        executor=executor,
    )
