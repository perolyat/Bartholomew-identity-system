"""
Isolated tests for bartholomew.kernel.governance (Phase B, stage B3).

Not integration tests against the live daemon -- per
docs/PHASE_B_OVERVIEW.md's B3 exit condition, this module is not yet the
runtime's shared Parking Brake instance (that's B4). These tests exercise
the schema, migration, and transition semantics standalone.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from bartholomew.kernel.governance import schema
from bartholomew.kernel.governance.brake_store import (
    GovernanceBrakeStore,
    StaleBrakeTransitionError,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "governance_test.db")


# -- Schema / migration -------------------------------------------------------


def test_ensure_schema_is_idempotent(db_path):
    schema.ensure_schema(db_path)
    schema.ensure_schema(db_path)  # must not raise or duplicate the singleton row

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM parking_brake_state").fetchone()
    assert rows[0] == 1


def test_fresh_database_defaults_to_disengaged(db_path):
    store = GovernanceBrakeStore(db_path)
    state = store.current_state()
    assert state.engaged is False
    assert state.scopes == frozenset()
    assert state.revision == 0


def test_legacy_flag_migrates_into_new_schema(db_path):
    # Simulate the legacy BrakeStorage's storage format existing already
    # (e.g. from before this stage's schema existed).
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE system_flags (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)",
        )
        conn.execute(
            "INSERT INTO system_flags VALUES ('parking_brake', ?, '1700000000')",
            (json.dumps({"engaged": True, "scopes": ["global", "skills"]}),),
        )
        conn.commit()

    store = GovernanceBrakeStore(db_path)
    state = store.current_state()
    assert state.engaged is True
    assert state.scopes == frozenset({"global", "skills"})
    assert state.revision == 0  # migration itself is not a transition


def test_legacy_migration_is_idempotent(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE system_flags (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)",
        )
        conn.execute(
            "INSERT INTO system_flags VALUES ('parking_brake', ?, '1700000000')",
            (json.dumps({"engaged": True, "scopes": ["global"]}),),
        )
        conn.commit()

    store = GovernanceBrakeStore(db_path)
    store.engage("skills")  # real transition after migration

    # Re-running ensure_schema (e.g. a second process start) must not
    # re-migrate and clobber the transition that happened since.
    schema.ensure_schema(db_path)
    state = store.current_state()
    assert state.scopes == frozenset({"global", "skills"})


def test_malformed_legacy_value_fails_closed(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE system_flags (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)",
        )
        conn.execute(
            "INSERT INTO system_flags VALUES ('parking_brake', 'not valid json', '0')",
        )
        conn.commit()

    store = GovernanceBrakeStore(db_path)
    state = store.current_state()
    assert state.engaged is True
    assert "global" in state.scopes


def test_no_legacy_flag_no_system_flags_table_at_all(db_path):
    # Genuinely fresh DB: no system_flags table exists yet.
    store = GovernanceBrakeStore(db_path)
    state = store.current_state()
    assert state.engaged is False
    assert state.scopes == frozenset()


# -- Monotonic engage() ---------------------------------------------------


def test_engage_defaults_to_global_scope(db_path):
    store = GovernanceBrakeStore(db_path)
    state = store.engage()
    assert state.engaged is True
    assert state.scopes == frozenset({"global"})


def test_engage_unions_scopes_rather_than_replacing(db_path):
    """
    The core bug fix this stage exists for: the live
    bartholomew.orchestrator.safety.parking_brake.ParkingBrake.engage()
    REPLACES the scopes set on every call, so engage("global") followed by
    engage("skills") ends up persisted as {"skills"} only -- silently
    dropping "global". GovernanceBrakeStore.engage() must union instead.
    """
    store = GovernanceBrakeStore(db_path)
    store.engage("global")
    state = store.engage("skills")

    assert state.engaged is True
    assert state.scopes == frozenset({"global", "skills"})


def test_engage_always_sets_engaged_true(db_path):
    store = GovernanceBrakeStore(db_path)
    state = store.engage("scheduler")
    assert state.engaged is True


def test_engage_bumps_revision_but_not_last_loosen_revision(db_path):
    store = GovernanceBrakeStore(db_path)
    s1 = store.engage("global")
    s2 = store.engage("skills")

    assert s2.revision == s1.revision + 1
    assert s2.last_loosen_revision == 0  # engage is never a loosening


# -- Confirmed disengage() / narrow_scopes() -------------------------------


def test_disengage_without_confirm_raises(db_path):
    store = GovernanceBrakeStore(db_path)
    store.engage("global")

    with pytest.raises(ValueError, match="confirm=True"):
        store.disengage(confirm=False)


def test_disengage_with_confirm_clears_state(db_path):
    store = GovernanceBrakeStore(db_path)
    store.engage("global", "skills")

    state = store.disengage(confirm=True)
    assert state.engaged is False
    assert state.scopes == frozenset()


def test_disengage_sets_last_loosen_revision(db_path):
    store = GovernanceBrakeStore(db_path)
    store.engage("global")
    state = store.disengage(confirm=True)
    assert state.last_loosen_revision == state.revision


def test_narrow_scopes_without_confirm_raises(db_path):
    store = GovernanceBrakeStore(db_path)
    store.engage("global", "skills")

    with pytest.raises(ValueError, match="confirm=True"):
        store.narrow_scopes({"skills"}, confirm=False)


def test_narrow_scopes_removes_only_specified_scopes(db_path):
    store = GovernanceBrakeStore(db_path)
    store.engage("global", "skills", "scheduler")

    state = store.narrow_scopes({"skills"}, confirm=True)
    assert state.engaged is True
    assert state.scopes == frozenset({"global", "scheduler"})


def test_narrow_scopes_to_empty_fully_disengages(db_path):
    store = GovernanceBrakeStore(db_path)
    store.engage("skills")

    state = store.narrow_scopes({"skills"}, confirm=True)
    assert state.engaged is False
    assert state.scopes == frozenset()


def test_narrow_scopes_of_unrelated_scope_is_a_no_op_on_membership(db_path):
    store = GovernanceBrakeStore(db_path)
    store.engage("global")

    state = store.narrow_scopes({"nonexistent"}, confirm=True)
    assert state.engaged is True
    assert state.scopes == frozenset({"global"})


# -- Stale-revision ordering safety -----------------------------------------


def test_stale_expected_revision_is_rejected(db_path):
    store = GovernanceBrakeStore(db_path)
    s1 = store.engage("global")  # revision 1
    store.engage("skills")  # revision 2, moves past s1

    with pytest.raises(StaleBrakeTransitionError):
        store.disengage(confirm=True, expected_revision=s1.revision)

    # Rejected transition must not have written anything.
    state = store.current_state()
    assert state.scopes == frozenset({"global", "skills"})
    assert state.revision == 2


def test_matching_expected_revision_succeeds(db_path):
    store = GovernanceBrakeStore(db_path)
    s1 = store.engage("global")

    state = store.engage("skills", expected_revision=s1.revision)
    assert state.scopes == frozenset({"global", "skills"})


def test_stale_revision_check_prevents_delayed_loosen_from_regressing_a_newer_engage(db_path):
    """
    Direct scenario from docs/PHASE_B_RISK_MAP.md's B3 row: a delayed
    loosening whose own revision check would otherwise still pass must not
    regress state a newer, intervening engage() has since tightened again.
    """
    store = GovernanceBrakeStore(db_path)
    s1 = store.engage("global")  # revision 1 -- some caller reads this state...

    # ...meanwhile a different actor tightens further before the first
    # caller's (now-stale) loosen request is applied.
    store.engage("skills")  # revision 2

    with pytest.raises(StaleBrakeTransitionError):
        store.narrow_scopes({"global"}, confirm=True, expected_revision=s1.revision)

    state = store.current_state()
    assert state.scopes == frozenset({"global", "skills"})


# -- Atomic audit trail -------------------------------------------------------


def test_every_transition_appends_exactly_one_audit_row(db_path):
    store = GovernanceBrakeStore(db_path)
    store.engage("global", actor="test", reason="unit test")
    store.engage("skills")
    store.disengage(confirm=True)

    log = store.audit_log(limit=10)
    assert len(log) == 3
    assert [row["action"] for row in log] == ["disengage", "engage", "engage"]  # newest first


def test_audit_row_captures_before_after_state(db_path):
    store = GovernanceBrakeStore(db_path)
    store.engage("global")
    store.engage("skills", actor="operator-1", reason="incident response")

    log = store.audit_log(limit=1)
    row = log[0]
    assert row["action"] == "engage"
    assert json.loads(row["scopes_before_json"]) == ["global"]
    assert json.loads(row["scopes_after_json"]) == ["global", "skills"]
    assert row["engaged_before"] == 1
    assert row["engaged_after"] == 1
    assert row["actor"] == "operator-1"
    assert row["reason"] == "incident response"


def test_rejected_stale_transition_writes_no_audit_row(db_path):
    store = GovernanceBrakeStore(db_path)
    s1 = store.engage("global")
    store.engage("skills")

    with pytest.raises(StaleBrakeTransitionError):
        store.disengage(confirm=True, expected_revision=s1.revision)

    log = store.audit_log(limit=10)
    assert len(log) == 2  # only the two successful engage() calls
    assert all(row["action"] == "engage" for row in log)


# -- is_blocked() semantics ---------------------------------------------------


def test_is_blocked_false_when_disengaged(db_path):
    store = GovernanceBrakeStore(db_path)
    state = store.current_state()
    assert state.is_blocked("skills") is False


def test_is_blocked_true_for_matching_scope(db_path):
    store = GovernanceBrakeStore(db_path)
    state = store.engage("skills")
    assert state.is_blocked("skills") is True
    assert state.is_blocked("scheduler") is False


def test_is_blocked_true_for_any_scope_when_global_engaged(db_path):
    store = GovernanceBrakeStore(db_path)
    state = store.engage("global")
    assert state.is_blocked("skills") is True
    assert state.is_blocked("anything") is True
