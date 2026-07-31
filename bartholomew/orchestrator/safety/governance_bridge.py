"""
Temporary fail-closed migration bridge (Phase B stage B4).

Once B4 lands, the live daemon's real construction sites read Governance
state through B3's GovernanceStore (governance_store.py), not the legacy
system_flags-based ParkingBrake. But bartholomew/cli.py's `brake on`/
`brake off` -- out of B4's scope, B6's -- still writes only to the legacy
system_flags value until B6 migrates it. Without a bridge, a CLI
operator's kill switch would silently stop affecting the running daemon
the moment B4 lands, until B6 closes that gap.

This module is that bridge: fail-closed dual-check, blocked if EITHER
the new schema or the legacy system_flags value says blocked. It is
explicitly temporary -- delete this module and switch every call site
back to GovernanceStore alone once B6 retires bartholomew/cli.py's
legacy write path. See docs/B4_GOVERNANCE_RUNTIME_INTEGRATION.md and
tests/test_governance_bridge_dual_check.py (also temporary, deleted in
the same B6 change).

Design note -- why this mirrors, not just reads, the legacy value:
GovernanceStore.__init__() imports the legacy system_flags row exactly
ONCE (B3's own, correct, one-time-cutover design). Nothing in B4 ever
calls GovernanceStore.engage()/disengage() on the shared instance, so
without an active sync step here, that one-time-imported snapshot would
go stale the moment the CLI changes the legacy value again -- a later
`brake off` would be invisible to the bridge forever, exactly the kind
of silent regression this bridge exists to prevent. So every check
mirrors the current legacy value into the store via a real, audited
write (tagged with _MIRROR_REASON) whenever they disagree -- but ONLY
when the store's own latest state isn't already a genuine (non-mirror,
non-"migrated") engagement: mirroring a less-restrictive legacy value
over a genuinely-engaged store would be an incorrect loosening, exactly
what this bridge must never do. A genuinely-engaged store is never
overridden by a mirror; a genuinely-disengaged (or migrated, or
previously-mirrored) store is always kept in sync with the current
legacy value, which can only ever tighten it via a mirror engage() (safe)
or match it via a mirror disengage() (also safe, since a store that
isn't genuinely engaged has no independent restriction to lose).
"""

from __future__ import annotations

import json
import sqlite3

from bartholomew.kernel.blocking_executor import SingleWorkerExecutor, run_off_loop
from bartholomew.orchestrator.safety.governance_store import GovernanceStore

_MIRROR_REASON = "B4 bridge: mirrored from legacy system_flags"


def legacy_is_blocked(db_path: str, scope: str) -> bool:
    """
    Read system_flags' legacy 'parking_brake' row directly -- mirrors
    bartholomew.orchestrator.safety.parking_brake.ParkingBrake._load()/
    is_blocked() exactly, without constructing a full ParkingBrake (whose
    audit-logging setup this bridge doesn't need).
    """
    state = _read_legacy_state(db_path)
    if state is None:
        return False
    engaged, scopes = state
    return engaged and ("global" in scopes or scope in scopes)


def _read_legacy_state(db_path: str) -> tuple[bool, set[str]] | None:
    """Returns (engaged, scopes) from system_flags' legacy row, or None
    if the table/row doesn't exist (nothing to mirror or be blocked by)."""
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM system_flags WHERE key = 'parking_brake'",
            ).fetchone()
    except sqlite3.OperationalError:
        # system_flags doesn't exist yet (fresh DB, MemoryStore hasn't
        # initialized).
        return None
    if row is None:
        return None
    data = json.loads(row[0])
    return bool(data.get("engaged")), set(data.get("scopes", []))


def _latest_audit_action_and_reason(db_path: str) -> tuple[str | None, str | None]:
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT action, reason FROM governance_audit ORDER BY id DESC LIMIT 1",
            ).fetchone()
    except sqlite3.OperationalError:
        return None, None
    if row is None:
        return None, None
    return row[0], row[1]


def _store_is_genuinely_engaged(db_path: str) -> bool:
    """
    True only if GovernanceStore's most recent transition was a real
    engage() -- not the one-time legacy migration ("migrated") and not
    this bridge's own mirror writes (_MIRROR_REASON) -- i.e. the store
    has independent authority over its current engaged state, not just a
    stale or synced copy of the legacy source (which is checked
    separately, always fresh, in legacy_is_blocked()).

    A genuinely-*disengaged* store needs no such protection: mirroring a
    more-restrictive legacy value onto it only ever tightens, never
    loosens, so it's always safe to sync regardless of how it got there.
    """
    action, reason = _latest_audit_action_and_reason(db_path)
    return action == "engaged" and reason != _MIRROR_REASON


def is_blocked_fail_closed(
    scope: str,
    db_path: str,
    *,
    governance_store: GovernanceStore | None = None,
) -> bool:
    """
    Dual-check, kept honestly in sync rather than merely read once: see
    this module's docstring for why a plain read-only OR isn't
    sufficient. Blocks if EITHER the new schema's genuine state or the
    (always freshly re-read) legacy system_flags value says blocked.
    """
    store = governance_store or GovernanceStore(db_path)
    store.refresh()

    legacy_state = _read_legacy_state(db_path)
    if legacy_state is not None and not _store_is_genuinely_engaged(store.db_path):
        legacy_engaged, legacy_scopes = legacy_state
        current = store.state()
        if current.engaged != legacy_engaged or current.scopes != frozenset(legacy_scopes):
            if legacy_engaged:
                store.engage(*legacy_scopes, reason=_MIRROR_REASON)
            else:
                store.disengage(reason=_MIRROR_REASON)

    return store.is_blocked(scope) or legacy_is_blocked(db_path, scope)


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
