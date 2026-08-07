"""
Tests for bartholomew.kernel.dry_run (the DryRunResult provenance sink)
and bartholomew.orchestrator.safety.governance_store's S5.5 additions
(the global dry-run switch: dry_run_state/dry_run_audit, engage_dry_run/
disengage_dry_run/is_dry_run_engaged). See
docs/S5_5_DRY_RUN_MODE_DESIGN.md.

Isolated store-level tests, mirroring test_governance_store.py's own
structure for the switch tests (same revision-guarded engage/disengage
shape) and test_initiative_store.py's structure for the sink tests.
"""

import sqlite3

import pytest

from bartholomew.kernel.dry_run import (
    DryRunResult,
    ensure_schema,
    list_dry_run_results,
    record_dry_run_result,
)
from bartholomew.orchestrator.safety.governance_store import (
    GovernanceStore,
    StaleGovernanceWriteError,
    is_dry_run_engaged_fail_closed,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "dry_run.db")


def _result(**overrides) -> DryRunResult:
    kwargs = {
        "surface": "initiative",
        "proposed_action": "initiative_propose_maintenance",
        "target": "new",
        "parameters": {"kind": "a"},
        "expected_effects": {"would_create_status": "approved"},
        "governance_decision": "allowed",
        "approval_requirements": {"reason": None},
        "would_execute": True,
        "actor": "test",
    }
    kwargs.update(overrides)
    return DryRunResult(**kwargs)


# ---------------------------------------------------------------------------
# dry_run.py: DryRunResult / dry_run_results sink
# ---------------------------------------------------------------------------


def test_ensure_schema_is_idempotent(db_path):
    ensure_schema(db_path)
    ensure_schema(db_path)  # must not raise "table already exists"


def test_record_and_list_round_trips_all_fields(db_path):
    result = _result()
    record_dry_run_result(db_path, result)

    fetched = list_dry_run_results(db_path)
    assert len(fetched) == 1
    row = fetched[0]
    assert row["dry_run_id"] == result.dry_run_id
    assert row["surface"] == "initiative"
    assert row["proposed_action"] == "initiative_propose_maintenance"
    assert row["target"] == "new"
    assert row["parameters"] == {"kind": "a"}
    assert row["expected_effects"] == {"would_create_status": "approved"}
    assert row["governance_decision"] == "allowed"
    assert row["approval_requirements"] == {"reason": None}
    assert row["would_execute"] is True
    assert row["actor"] == "test"


def test_dry_run_id_defaults_to_a_fresh_uuid_each_time():
    a = _result()
    b = _result()
    assert a.dry_run_id != b.dry_run_id


def test_list_filters_by_surface(db_path):
    record_dry_run_result(db_path, _result(surface="initiative", target="new"))
    record_dry_run_result(db_path, _result(surface="skill", target="notify"))

    initiative_only = list_dry_run_results(db_path, surface="initiative")
    assert len(initiative_only) == 1
    assert initiative_only[0]["surface"] == "initiative"


def test_list_filters_by_target(db_path):
    record_dry_run_result(db_path, _result(target="1"))
    record_dry_run_result(db_path, _result(target="2"))

    only_one = list_dry_run_results(db_path, target="1")
    assert len(only_one) == 1
    assert only_one[0]["target"] == "1"


def test_list_respects_limit(db_path):
    for _ in range(5):
        record_dry_run_result(db_path, _result())
    assert len(list_dry_run_results(db_path, limit=2)) == 2


def test_list_orders_newest_first(db_path):
    first = _result()
    second = _result()
    record_dry_run_result(db_path, first)
    record_dry_run_result(db_path, second)

    fetched = list_dry_run_results(db_path)
    assert fetched[0]["dry_run_id"] == second.dry_run_id
    assert fetched[1]["dry_run_id"] == first.dry_run_id


def test_recording_a_dry_run_result_never_touches_any_other_table(db_path):
    """Structural isolation check: dry_run_results is the only table this
    module's write path can reach."""
    record_dry_run_result(db_path, _result())
    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'",
            ).fetchall()
        }
    finally:
        conn.close()
    assert tables == {"dry_run_results"}


# ---------------------------------------------------------------------------
# governance_store.py: the global dry-run switch (S5.5)
# ---------------------------------------------------------------------------


def test_dry_run_switch_starts_disengaged(db_path):
    store = GovernanceStore(db_path)
    state = store.dry_run_state()
    assert state.engaged is False
    assert state.scopes == frozenset()
    assert state.revision == 0


def test_engage_dry_run_sets_engaged_and_scopes(db_path):
    store = GovernanceStore(db_path)
    state = store.engage_dry_run("initiative", reason="testing", actor="user")
    assert state.engaged is True
    assert state.scopes == frozenset({"initiative"})
    assert state.revision == 1


def test_engage_dry_run_default_scope_is_global(db_path):
    store = GovernanceStore(db_path)
    state = store.engage_dry_run()
    assert state.scopes == frozenset({"global"})


def test_is_dry_run_engaged_global_vs_scoped(db_path):
    store = GovernanceStore(db_path)
    store.engage_dry_run("initiative")
    assert store.is_dry_run_engaged("initiative") is True
    assert store.is_dry_run_engaged("skills") is False

    store.engage_dry_run()  # global
    assert store.is_dry_run_engaged("anything") is True


def test_engaging_is_observed_by_a_separate_store_instance_after_refresh(db_path):
    """Proves persistence, not just in-process caching: a second
    GovernanceStore instance (standing in for a different request/runtime
    reading the same database) sees an engage() made by the first, once
    it refreshes."""
    writer = GovernanceStore(db_path)
    reader = GovernanceStore(db_path)
    assert reader.is_dry_run_engaged("initiative") is False

    writer.engage_dry_run("initiative", reason="observed-by-other-instance test")

    # Without a refresh, the reader's cache is still stale -- this is
    # expected (mirrors the identical property already established for
    # the Parking Brake's own is_blocked()/refresh() pair) and is exactly
    # why every real seam call always refreshes before reading.
    assert reader.is_dry_run_engaged("initiative") is False
    reader.refresh_dry_run()
    assert reader.is_dry_run_engaged("initiative") is True


def test_disengaging_is_observed_by_a_separate_store_instance_after_refresh(db_path):
    writer = GovernanceStore(db_path)
    writer.engage_dry_run("initiative")

    reader = GovernanceStore(db_path)
    reader.refresh_dry_run()
    assert reader.is_dry_run_engaged("initiative") is True

    writer.disengage_dry_run(reason="lifted")
    reader.refresh_dry_run()
    assert reader.is_dry_run_engaged("initiative") is False


def test_disengage_dry_run_raises_on_stale_revision(db_path):
    """The same core invariant parking_brake_state's disengage() enforces,
    against the separate dry-run switch: a delayed disengage() based on a
    stale snapshot must not silently regress a more-recent, more-
    restrictive (still-engaged) state."""
    writer = GovernanceStore(db_path)
    writer.engage_dry_run("initiative")  # revision 1

    stale_reader = GovernanceStore(db_path)  # loads revision 1
    writer.engage_dry_run("initiative", "skills")  # revision 2, more restrictive

    with pytest.raises(StaleGovernanceWriteError):
        stale_reader.disengage_dry_run(reason="attempting stale loosening")

    # The rejected write must leave state completely unchanged.
    current = GovernanceStore(db_path).dry_run_state()
    assert current.engaged is True
    assert current.scopes == frozenset({"initiative", "skills"})
    assert current.revision == 2


def test_disengage_dry_run_succeeds_after_refresh(db_path):
    writer = GovernanceStore(db_path)
    writer.engage_dry_run("initiative")

    stale_reader = GovernanceStore(db_path)
    writer.engage_dry_run("initiative", "skills")

    stale_reader.refresh_dry_run()
    state = stale_reader.disengage_dry_run(reason="confirmed after refresh")
    assert state.engaged is False
    assert state.revision == 3


def test_engage_dry_run_never_refused_regardless_of_staleness(db_path):
    """Tightening (toward simulation-only) is never refused -- only
    disengage_dry_run() (loosening, back to live-eligible) is
    revision-guarded, mirroring engage()'s own established precedent."""
    writer = GovernanceStore(db_path)
    writer.engage_dry_run("initiative")

    stale_reader = GovernanceStore(db_path)
    writer.engage_dry_run("initiative", "skills")

    # stale_reader's engage_dry_run() call still succeeds even though its
    # cached revision is behind -- engage is a monotonic tightening, never
    # a refused loosening.
    state = stale_reader.engage_dry_run("global")
    assert state.engaged is True
    assert state.scopes == frozenset({"global"})


def test_dry_run_audit_records_every_transition(db_path):
    store = GovernanceStore(db_path)
    store.engage_dry_run("initiative", reason="start", actor="user")
    store.disengage_dry_run(reason="stop", actor="user")

    audit = store.list_dry_run_audit()
    actions = [row["action"] for row in reversed(audit)]
    assert actions == ["engaged", "disengaged"]
    assert audit[-1]["reason"] == "start"
    assert audit[-1]["actor"] == "user"


def test_dry_run_switch_is_independent_of_parking_brake_state(db_path):
    """Engaging the dry-run switch must never touch parking_brake_state,
    and vice versa -- distinct concepts, distinct tables, per
    DryRunSwitchState's own docstring."""
    store = GovernanceStore(db_path)
    store.engage_dry_run("initiative")
    assert store.state().engaged is False  # parking brake untouched

    store.engage("skills")
    assert store.dry_run_state().engaged is True  # dry-run switch untouched
    assert store.dry_run_state().scopes == frozenset({"initiative"})


def test_is_dry_run_engaged_fail_closed_reads_fresh_state(db_path):
    """Module-level convenience function (mirrors is_blocked_fail_closed())
    -- reads current persisted state without requiring the caller to hold
    a live GovernanceStore instance."""
    writer = GovernanceStore(db_path)
    assert is_dry_run_engaged_fail_closed("initiative", db_path) is False

    writer.engage_dry_run("initiative")
    assert is_dry_run_engaged_fail_closed("initiative", db_path) is True


def test_is_dry_run_engaged_fail_closed_does_not_catch_its_own_errors(db_path, monkeypatch):
    """S5.5 design doc Sec 10: fail-closed is enforced by the *caller*
    (the Runtime Contract seam denies outright on error) -- this function
    itself must not swallow an exception and silently return False/True,
    or the seam's own fail-closed handling would never engage."""
    store = GovernanceStore(db_path)

    def _boom(self):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(GovernanceStore, "refresh_dry_run", _boom)
    with pytest.raises(RuntimeError):
        is_dry_run_engaged_fail_closed("initiative", db_path, governance_store=store)
