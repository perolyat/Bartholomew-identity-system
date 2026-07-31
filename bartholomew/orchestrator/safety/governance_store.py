"""
Governance schema and Parking Brake persistence (Phase B stage B3).

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
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from bartholomew.kernel.db_ctx import connect, set_wal_pragmas

_STATE_ROW_ID = 1


class StaleGovernanceWriteError(RuntimeError):
    """Raised when disengage()'s expected_revision no longer matches the
    currently-persisted revision -- the caller's view of state is stale,
    and the write is refused rather than silently regressing a
    more-recent, more-restrictive state. Callers should reload state
    (GovernanceStore(db_path) or .refresh()) and decide whether to retry,
    not blindly resubmit the same stale write."""


@dataclass(frozen=True)
class GovernanceState:
    """Parking brake state snapshot, with the revision it was read at."""

    engaged: bool
    scopes: frozenset[str]
    revision: int


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
                revision INTEGER NOT NULL
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
            "INSERT INTO governance_audit (ts, action, scopes, reason, revision) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, action, json.dumps(scopes), "imported from legacy system_flags row", revision),
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

    def engage(self, *scopes: str, reason: str | None = None) -> GovernanceState:
        """
        Engage (tighten). Always applies -- monotonic tightening is never
        refused, regardless of this instance's cached revision. Replace
        semantics: the given scopes replace the persisted set (see this
        module's docstring for the accepted limitation this carries).
        """
        scopes_set = set(scopes) if scopes else {"global"}
        return self._write(engaged=True, scopes=scopes_set, action="engaged", reason=reason)

    def disengage(
        self,
        *,
        reason: str | None = None,
        expected_revision: int | None = None,
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
        """
        if expected_revision is None:
            expected_revision = self._cache.revision
        return self._write(
            engaged=False,
            scopes=set(),
            action="disengaged",
            reason=reason,
            expected_revision=expected_revision,
        )

    def _write(
        self,
        *,
        engaged: bool,
        scopes: set[str],
        action: str,
        reason: str | None,
        expected_revision: int | None = None,
    ) -> GovernanceState:
        """
        Writes the new state and its audit record in one transaction --
        the two are never observable independently: a crash before commit
        leaves both absent, a crash after commit leaves both present.

        expected_revision, when given, guards the write with an atomic
        `UPDATE ... WHERE revision = ?`: if no row matches (someone else
        already advanced the revision), the write is rolled back and
        StaleGovernanceWriteError is raised instead of committing.
        """
        now = int(time.time())
        conn = connect(self.db_path)
        try:
            set_wal_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")
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
                "INSERT INTO governance_audit (ts, action, scopes, reason, revision) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, action, json.dumps(sorted_scopes), reason, new_revision),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

        self._cache = GovernanceState(engaged, frozenset(scopes), new_revision)
        return self._cache
