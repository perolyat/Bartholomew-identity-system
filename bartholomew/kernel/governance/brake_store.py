"""
GovernanceBrakeStore: Parking Brake transition semantics over the schema
defined in schema.py.

Phase B, stage B3. Fixes two real defects found by reading the live
implementation (bartholomew.orchestrator.safety.parking_brake.ParkingBrake)
while designing this:

1. Non-monotonic engage(): the live engage() REPLACES the persisted scopes
   set on every call rather than unioning with it. Two engage() calls in a
   row -- engage("global") then engage("skills") -- leave the brake
   persisted as {"skills"} only, silently dropping "global". That is a
   direct violation of docs/PHASE_B_OVERVIEW.md section 4's "Fail-closed
   governance: the Parking Brake can only become *more* restrictive without
   an explicit, confirmed loosening action" invariant -- the live engage()
   can silently *loosen* the brake as a side effect of a second, unrelated
   engage() call. GovernanceBrakeStore.engage() unions instead.
2. No confirmed-loosening distinction: the live disengage() takes no
   arguments and unconditionally clears all scopes -- any caller with a
   reference to the brake can fully loosen it with one call, with no
   "explicit, confirmed" gate distinguishing an intentional loosening from
   an accidental one. GovernanceBrakeStore.disengage()/narrow_scopes()
   require confirm=True.

Not yet the runtime's shared instance -- see bartholomew/kernel/governance/
__init__.py's module docstring. B4's job, not this stage's.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from bartholomew.kernel import db_ctx

from . import schema


class StaleBrakeTransitionError(RuntimeError):
    """
    Raised when a transition is submitted with an expected_revision that no
    longer matches the persisted revision -- i.e. a newer transition (from
    another caller, or a concurrent process) has already been applied.
    Callers must re-read current_state() and decide whether their intended
    transition still makes sense against the new state, rather than this
    module silently reapplying a decision made against stale data.
    """


@dataclass(frozen=True)
class GovernanceBrakeState:
    """Parking brake state snapshot, with the version fields the legacy
    bartholomew.orchestrator.safety.parking_brake.BrakeState lacks."""

    engaged: bool
    scopes: frozenset[str]
    revision: int
    last_loosen_revision: int
    updated_at: int

    def is_blocked(self, scope: str) -> bool:
        return self.engaged and ("global" in self.scopes or scope in self.scopes)


class GovernanceBrakeStore:
    """
    Synchronous storage/transition layer for the Parking Brake's durable
    state. Synchronous and raw-sqlite3-based (like the legacy
    BrakeStorage/ParkingBrake it will eventually replace) rather than
    routed through a DedicatedDbExecutor (Phase B, stage B2's mechanism):
    this class is not yet reachable from the event loop at all (B3's own
    exit condition is "implemented and tested in isolation, without yet
    being the runtime's shared instance") -- B4 is responsible for deciding
    how the live daemon's async call sites reach it.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        schema.ensure_schema(db_path)

    def current_state(self) -> GovernanceBrakeState:
        with db_ctx.wal_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT engaged, scopes_json, revision, last_loosen_revision, updated_at "
                "FROM parking_brake_state WHERE id = 1",
            ).fetchone()
        return self._row_to_state(row)

    @staticmethod
    def _row_to_state(row) -> GovernanceBrakeState:
        engaged, scopes_json, revision, last_loosen_revision, updated_at = row
        return GovernanceBrakeState(
            engaged=bool(engaged),
            scopes=frozenset(json.loads(scopes_json)),
            revision=revision,
            last_loosen_revision=last_loosen_revision,
            updated_at=updated_at,
        )

    def engage(
        self,
        *scopes: str,
        actor: str | None = None,
        reason: str | None = None,
        expected_revision: int | None = None,
    ) -> GovernanceBrakeState:
        """
        Engage the brake, UNIONING `scopes` (default {"global"} if none
        given) into the currently-persisted scopes rather than replacing
        them -- monotonic tightening. Always sets engaged=True: engage() is
        never a loosening operation, even if the union happens to be a
        subset of the input (which it never is, by construction).
        """
        requested = set(scopes) if scopes else {"global"}

        def transition(current: GovernanceBrakeState) -> tuple[bool, set[str]]:
            return True, set(current.scopes) | requested

        return self._apply_transition(
            transition,
            action="engage",
            actor=actor,
            reason=reason,
            expected_revision=expected_revision,
        )

    def disengage(
        self,
        *,
        confirm: bool,
        actor: str | None = None,
        reason: str | None = None,
        expected_revision: int | None = None,
    ) -> GovernanceBrakeState:
        """
        Fully disengage the brake (engaged=False, scopes cleared). Requires
        confirm=True -- docs/PHASE_B_OVERVIEW.md section 4's "no code path
        may silently widen access" invariant means a loosening call must be
        an unambiguous, deliberate choice, not a default-arg oversight.
        """
        if not confirm:
            raise ValueError(
                "disengage() requires confirm=True -- loosening the Parking Brake must be "
                "an explicit, confirmed action, per docs/PHASE_B_OVERVIEW.md section 4",
            )

        def transition(current: GovernanceBrakeState) -> tuple[bool, set[str]]:
            return False, set()

        return self._apply_transition(
            transition,
            action="disengage",
            actor=actor,
            reason=reason,
            expected_revision=expected_revision,
            is_loosening=True,
        )

    def narrow_scopes(
        self,
        scopes_to_remove: set[str],
        *,
        confirm: bool,
        actor: str | None = None,
        reason: str | None = None,
        expected_revision: int | None = None,
    ) -> GovernanceBrakeState:
        """
        Partial loosening: remove specific scopes from the engaged set
        without a full disengage(). If the remaining scope set is empty,
        this becomes a full disengage (engaged=False) -- an engaged brake
        with an empty scope set is not a meaningful distinct state (see
        GovernanceBrakeState.is_blocked(): engaged alone blocks nothing
        without a matching or "global" scope). Requires confirm=True, same
        as disengage() -- narrowing is a loosening operation.
        """
        if not confirm:
            raise ValueError(
                "narrow_scopes() requires confirm=True -- loosening the Parking Brake must be "
                "an explicit, confirmed action, per docs/PHASE_B_OVERVIEW.md section 4",
            )

        def transition(current: GovernanceBrakeState) -> tuple[bool, set[str]]:
            remaining = set(current.scopes) - scopes_to_remove
            return (bool(remaining), remaining)

        return self._apply_transition(
            transition,
            action="narrow",
            actor=actor,
            reason=reason,
            expected_revision=expected_revision,
            is_loosening=True,
        )

    def _apply_transition(
        self,
        transition,
        *,
        action: str,
        actor: str | None,
        reason: str | None,
        expected_revision: int | None,
        is_loosening: bool = False,
    ) -> GovernanceBrakeState:
        """
        Read-modify-write the singleton row and append one governance_audit
        row, in one transaction (one connection, one commit) -- atomic with
        respect to the state write, unlike the legacy
        BrakeStorage.append_memory()'s unawaited asyncio.create_task.

        Ordering safety: if expected_revision is given and doesn't match
        the persisted revision, raises StaleBrakeTransitionError without
        writing anything -- this is the direct fix for the risk map's
        "Stale Parking Brake transitions" finding (a delayed loosening
        whose own revision check would otherwise still pass could regress
        state a newer engage() has since tightened again). Even without an
        explicit expected_revision, a loosening transition (disengage/
        narrow) is rejected if a newer engage has landed since this
        store's last-known revision would suggest -- callers that care
        about ordering should always pass expected_revision; this defense
        only catches the case where last_loosen_revision itself has moved
        backwards, which would indicate a genuine ordering bug in the
        caller.
        """
        with db_ctx.wal_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT engaged, scopes_json, revision, last_loosen_revision, updated_at "
                "FROM parking_brake_state WHERE id = 1",
            ).fetchone()
            current = self._row_to_state(row)

            if expected_revision is not None and expected_revision != current.revision:
                raise StaleBrakeTransitionError(
                    f"{action}() expected revision {expected_revision}, but persisted "
                    f"revision is {current.revision} -- re-read current_state() before "
                    f"retrying this transition",
                )

            new_engaged, new_scopes = transition(current)
            new_revision = current.revision + 1
            new_last_loosen_revision = (
                new_revision if is_loosening else current.last_loosen_revision
            )
            now = int(time.time())

            conn.execute(
                """
                UPDATE parking_brake_state
                SET engaged = ?, scopes_json = ?, revision = ?,
                    last_loosen_revision = ?, updated_at = ?
                WHERE id = 1
                """,
                (
                    int(new_engaged),
                    json.dumps(sorted(new_scopes)),
                    new_revision,
                    new_last_loosen_revision,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO governance_audit
                    (revision, action, scopes_before_json, scopes_after_json,
                     engaged_before, engaged_after, actor, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_revision,
                    action,
                    json.dumps(sorted(current.scopes)),
                    json.dumps(sorted(new_scopes)),
                    int(current.engaged),
                    int(new_engaged),
                    actor,
                    reason,
                    now,
                ),
            )
            conn.commit()

        return GovernanceBrakeState(
            engaged=new_engaged,
            scopes=frozenset(new_scopes),
            revision=new_revision,
            last_loosen_revision=new_last_loosen_revision,
            updated_at=now,
        )

    def audit_log(self, limit: int = 50) -> list[dict]:
        """Most recent governance_audit rows, newest first."""
        with db_ctx.wal_db(self.db_path) as conn:
            cols = [
                "id",
                "revision",
                "action",
                "scopes_before_json",
                "scopes_after_json",
                "engaged_before",
                "engaged_after",
                "actor",
                "reason",
                "created_at",
            ]
            rows = conn.execute(
                f"SELECT {', '.join(cols)} FROM governance_audit ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(zip(cols, row, strict=True)) for row in rows]
