"""
TEMPORARY regression test for Phase B stage B4's fail-closed migration
bridge (bartholomew.orchestrator.safety.governance_bridge).

Delete this file in the same change that deletes governance_bridge.py --
once B6 migrates bartholomew/cli.py's `brake on`/`brake off` off the
legacy system_flags-based ParkingBrake and onto GovernanceStore, there
are no longer two independent sources of truth to reconcile, and this
test's entire premise goes away.

Purpose: confirm the dual-check bridge cannot accidentally regress into
a fail-*open* state during the B4-to-B6 migration window, where the live
daemon reads the new schema but bartholomew/cli.py still only writes the
legacy one.
"""

import json
import sqlite3

from bartholomew.orchestrator.safety.governance_bridge import is_blocked_fail_closed
from bartholomew.orchestrator.safety.governance_store import GovernanceStore


def _seed_legacy_system_flags(db_path: str, *, engaged: bool, scopes: list[str]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS system_flags "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)",
        )
        conn.execute(
            "INSERT INTO system_flags VALUES ('parking_brake', ?, '1700000000') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps({"engaged": engaged, "scopes": scopes}),),
        )
        conn.commit()
    finally:
        conn.close()


def test_legacy_blocked_new_store_clear_stays_blocked(tmp_path):
    """Legacy system_flags says blocked, GovernanceStore would allow it
    (never engaged) -- runtime must remain blocked."""
    db_path = str(tmp_path / "governance.db")

    _seed_legacy_system_flags(db_path, engaged=True, scopes=["skills"])
    store = GovernanceStore(db_path)  # imports the legacy row at construction,
    # so explicitly disengage the new store to isolate this scenario: the
    # legacy source alone is what must be caught here, not the migration
    # import.
    store.disengage(reason="test isolation: new store explicitly clear")

    assert is_blocked_fail_closed("skills", db_path, governance_store=store) is True


def test_new_store_blocked_legacy_clear_stays_blocked(tmp_path):
    """GovernanceStore says blocked, legacy system_flags flag is clear (or
    absent) -- runtime must remain blocked."""
    db_path = str(tmp_path / "governance.db")

    store = GovernanceStore(db_path)  # no legacy system_flags row at all
    store.engage("skills", reason="test: new store blocks")

    assert is_blocked_fail_closed("skills", db_path, governance_store=store) is True


def test_new_store_blocked_legacy_explicitly_disengaged_stays_blocked(tmp_path):
    """Same as above, but with an explicit legacy row present and
    disengaged (not merely absent) -- confirms the bridge isn't relying
    on "table missing" as an accidental pass-through."""
    db_path = str(tmp_path / "governance.db")

    _seed_legacy_system_flags(db_path, engaged=False, scopes=[])
    store = GovernanceStore(db_path)
    store.engage("skills", reason="test: new store blocks, legacy explicitly clear")

    assert is_blocked_fail_closed("skills", db_path, governance_store=store) is True


def test_both_sources_clear_allows_execution(tmp_path):
    """Only when both sources report unblocked should execution proceed."""
    db_path = str(tmp_path / "governance.db")

    _seed_legacy_system_flags(db_path, engaged=False, scopes=[])
    store = GovernanceStore(db_path)
    store.disengage(reason="test: both sources clear")

    assert is_blocked_fail_closed("skills", db_path, governance_store=store) is False


def test_both_sources_clear_with_no_legacy_row_at_all_allows_execution(tmp_path):
    """Fresh deployment, no legacy system_flags row ever written, new
    store never engaged -- execution proceeds."""
    db_path = str(tmp_path / "governance.db")

    store = GovernanceStore(db_path)

    assert is_blocked_fail_closed("skills", db_path, governance_store=store) is False


def test_scope_specific_blocking_respected_by_both_sources(tmp_path):
    """A block on an unrelated scope in either source must not block a
    different scope -- the dual-check must not degrade to a blanket
    "either source has any block at all" check."""
    db_path = str(tmp_path / "governance.db")

    _seed_legacy_system_flags(db_path, engaged=True, scopes=["voice"])
    store = GovernanceStore(db_path)
    store.disengage(reason="test isolation")
    store.engage("sight", reason="test: new store blocks a different scope")

    # Neither source blocks "skills" specifically.
    assert is_blocked_fail_closed("skills", db_path, governance_store=store) is False
    # Each source's own scope is still honestly blocked.
    assert is_blocked_fail_closed("voice", db_path, governance_store=store) is True
    assert is_blocked_fail_closed("sight", db_path, governance_store=store) is True


def test_global_scope_in_either_source_blocks_everything(tmp_path):
    db_path = str(tmp_path / "governance.db")

    _seed_legacy_system_flags(db_path, engaged=True, scopes=["global"])
    store = GovernanceStore(db_path)
    store.disengage(reason="test isolation")

    assert is_blocked_fail_closed("skills", db_path, governance_store=store) is True
    assert is_blocked_fail_closed("anything_at_all", db_path, governance_store=store) is True


def test_legacy_disengage_after_migration_snapshot_is_picked_up(tmp_path):
    """
    Regression test for the actual bug this design initially shipped with
    (caught by tests/test_end_to_end_tasks_and_audit.py
    ::test_parking_brake_blocks_then_disengage_allows): GovernanceStore's
    legacy-import migration runs exactly ONCE, at first construction. A
    naive read-only dual-check (`store.is_blocked() or
    legacy_is_blocked()`) would freeze the "new store" side at whatever
    the legacy value was at that one moment -- so a *later* legacy
    disengage() would never be reflected, and the bridge would report
    "still blocked" forever. This test constructs a brand-new
    GovernanceStore instance for each check (no shared instance reused
    across calls), exactly matching how SkillRegistry falls back when it
    has no daemon-owned governance_store -- the real code path the E2E
    test above exercises via the legacy ParkingBrake directly.
    """
    db_path = str(tmp_path / "governance.db")

    _seed_legacy_system_flags(db_path, engaged=True, scopes=["skills"])
    assert is_blocked_fail_closed("skills", db_path, governance_store=None) is True

    _seed_legacy_system_flags(db_path, engaged=False, scopes=[])
    # A fresh GovernanceStore(db_path) here would see parking_brake_state
    # already populated by the prior call's one-time migration and would
    # NOT re-import -- the bridge must still notice the legacy value
    # changed and reflect it, not report the stale imported snapshot.
    assert is_blocked_fail_closed("skills", db_path, governance_store=None) is False

    _seed_legacy_system_flags(db_path, engaged=True, scopes=["skills"])
    assert is_blocked_fail_closed("skills", db_path, governance_store=None) is True
