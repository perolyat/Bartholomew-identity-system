"""
Tests for bartholomew.kernel.initiative_store (Stage 5, S5.1/S5.2/S5.3).
Isolated tests against the new initiatives/initiative_audit/
initiative_dependencies/initiative_consent schema -- see
docs/S5_1_INITIATIVE_ENGINE_ARCHITECTURE_DESIGN.md and
docs/S5_3_DEFAULT_OFF_CONSENT_AND_MUTE_DESIGN.md for the designs this
implements.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from bartholomew.kernel.initiative_store import (
    VALID_CATEGORIES,
    InitiativeNotFoundError,
    InitiativeStore,
    InvalidTransitionError,
)

FAR_FUTURE = "2099-01-01T00:00:00Z"
FAR_PAST = "2000-01-01T00:00:00Z"


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "initiative.db")


def _propose(store, **overrides):
    kwargs = {
        "kind": "checkin.morning",
        "category": "check_in",
        "confidence": 0.8,
        "rationale": "Good morning check-in",
        "origin_drive": "checkin_morning",
        "expires_at": FAR_FUTURE,
        "governance_decision": "allowed",
    }
    kwargs.update(overrides)
    return store.propose(**kwargs)


def test_fresh_schema_is_empty(db_path):
    store = InitiativeStore(db_path)
    assert store.list() == []


def test_propose_allowed_creates_approved_status(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    assert initiative.status == "approved"
    assert initiative.governance_decision == "allowed"
    assert initiative.kind == "checkin.morning"
    assert initiative.category == "check_in"
    assert initiative.priority == "normal"


def test_propose_denied_still_persists_a_row(db_path):
    """S5.1 design doc Sec 5: 'denied ... Still audited and reflected -- a
    denial is not silently dropped.'"""
    store = InitiativeStore(db_path)
    initiative = _propose(
        store,
        governance_decision="denied",
        governance_reason="no consent",
    )
    assert initiative.status == "denied"
    assert initiative.governance_reason == "no consent"
    assert store.get(initiative.id) is not None


def test_propose_writes_an_audit_row(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT transition FROM initiative_audit WHERE initiative_id = ?",
            (initiative.id,),
        ).fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == ["propose"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": "not_a_real_category"},
        {"priority": "urgent"},
        {"confidence": 1.5},
        {"confidence": -0.1},
        {"rationale": ""},
        {"rationale": "   "},
        {"expires_at": ""},
        {"expires_at": None},
    ],
)
def test_propose_rejects_invalid_input(db_path, overrides):
    store = InitiativeStore(db_path)
    with pytest.raises(InvalidTransitionError):
        _propose(store, **overrides)


def test_propose_records_reserved_dependencies_write_only(db_path):
    """S5.1 design doc Sec 13: depends_on is recorded but nothing reads or
    enforces it in this pass."""
    store = InitiativeStore(db_path)
    parent = _propose(store, kind="a")
    child = _propose(store, kind="b", depends_on=[parent.id])

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT depends_on_id FROM initiative_dependencies WHERE initiative_id = ?",
            (child.id,),
        ).fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == [parent.id]
    # Recording it does not itself block or gate anything.
    assert child.status == "approved"


def test_propose_records_parent_initiative_id(db_path):
    store = InitiativeStore(db_path)
    parent = _propose(store, kind="workflow.parent")
    child = _propose(store, kind="workflow.child", parent_initiative_id=parent.id)
    assert child.parent_initiative_id == parent.id


def test_defer_requires_approved(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    deferred = store.defer(initiative.id, reason="quiet_hours", actor="test")
    assert deferred.status == "deferred"
    assert deferred.deferred_reason == "quiet_hours"


def test_defer_rejects_from_denied(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store, governance_decision="denied")
    with pytest.raises(InvalidTransitionError):
        store.defer(initiative.id, reason="x")


@pytest.mark.parametrize("pre_status_fixture", ["approved", "deferred", "snoozed"])
def test_deliver_accepts_approved_deferred_or_snoozed(db_path, pre_status_fixture):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    if pre_status_fixture == "deferred":
        store.defer(initiative.id, reason="muted")
    elif pre_status_fixture == "snoozed":
        store.deliver(initiative.id)
        store.resolve(initiative.id, resolution="snoozed", due_at=FAR_FUTURE)

    delivered = store.deliver(initiative.id, actor="test")
    assert delivered.status == "delivered"
    assert delivered.delivered_at is not None


def test_deliver_rejects_from_denied(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store, governance_decision="denied")
    with pytest.raises(InvalidTransitionError):
        store.deliver(initiative.id)


def test_resolve_accepted_and_dismissed(db_path):
    store = InitiativeStore(db_path)
    a = _propose(store, kind="a")
    store.deliver(a.id)
    resolved = store.resolve(a.id, resolution="accepted")
    assert resolved.status == "accepted"
    assert resolved.resolution == "accepted"
    assert resolved.resolved_at is not None

    b = _propose(store, kind="b")
    store.deliver(b.id)
    dismissed = store.resolve(b.id, resolution="dismissed")
    assert dismissed.status == "dismissed"


def test_resolve_snoozed_updates_due_at_not_resolved_at(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    store.deliver(initiative.id)
    snoozed = store.resolve(initiative.id, resolution="snoozed", due_at="2030-01-01T00:00:00Z")
    assert snoozed.status == "snoozed"
    assert snoozed.due_at == "2030-01-01T00:00:00Z"
    assert snoozed.resolved_at is None


def test_resolve_snoozed_can_re_enter_delivery(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    store.deliver(initiative.id)
    store.resolve(initiative.id, resolution="snoozed", due_at=FAR_FUTURE)
    redelivered = store.deliver(initiative.id)
    assert redelivered.status == "delivered"


def test_resolve_rejects_invalid_resolution_value(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    store.deliver(initiative.id)
    with pytest.raises(InvalidTransitionError):
        store.resolve(initiative.id, resolution="not_a_real_resolution")


def test_resolve_rejects_from_non_delivered(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)  # status: approved, not delivered
    with pytest.raises(InvalidTransitionError):
        store.resolve(initiative.id, resolution="accepted")


@pytest.mark.parametrize("terminal_fn_name", ["expire", "cancel", "supersede"])
def test_terminal_transitions_from_various_non_terminal_states(db_path, terminal_fn_name):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    fn = getattr(store, terminal_fn_name)
    result = fn(initiative.id, actor="test")
    assert (
        result.status
        == {"expire": "expired", "cancel": "cancelled", "supersede": "superseded"}[terminal_fn_name]
    )
    assert result.resolved_at is not None
    assert result.resolution == result.status


def test_terminal_transitions_reject_already_terminal(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    store.cancel(initiative.id)
    with pytest.raises(InvalidTransitionError):
        store.cancel(initiative.id)
    with pytest.raises(InvalidTransitionError):
        store.expire(initiative.id)
    with pytest.raises(InvalidTransitionError):
        store.supersede(initiative.id)


def test_unknown_initiative_id_raises_not_found(db_path):
    store = InitiativeStore(db_path)
    with pytest.raises(InitiativeNotFoundError):
        store.defer(99999, reason="x")
    with pytest.raises(InitiativeNotFoundError):
        store.expire(99999)


def test_list_expiring_only_returns_non_terminal_past_expiry(db_path):
    store = InitiativeStore(db_path)
    expired_open = _propose(store, kind="a", expires_at=FAR_PAST)
    not_expired = _propose(store, kind="b", expires_at=FAR_FUTURE)
    expired_but_terminal = _propose(store, kind="c", expires_at=FAR_PAST)
    store.cancel(expired_but_terminal.id)

    import time

    due = store.list_expiring(int(time.time()))
    due_ids = {e.id for e in due}
    assert due_ids == {expired_open.id}
    assert not_expired.id not in due_ids
    assert expired_but_terminal.id not in due_ids


def test_list_open_by_kind_excludes_terminal(db_path):
    store = InitiativeStore(db_path)
    a = _propose(store, kind="checkin.morning")
    b = _propose(store, kind="checkin.morning")
    store.cancel(b.id)

    open_entries = store.list_open_by_kind("checkin.morning")
    assert [e.id for e in open_entries] == [a.id]


def test_is_category_consented_defaults_to_false(db_path):
    store = InitiativeStore(db_path)
    assert store.is_category_consented("check_in") is False


def test_is_category_consented_true_once_row_allows_it(db_path):
    store = InitiativeStore(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO initiative_consent (category, allowed, updated_at) "
            "VALUES ('check_in', 1, '2026-01-01T00:00:00Z')",
        )
        conn.commit()
    finally:
        conn.close()
    assert store.is_category_consented("check_in") is True
    assert store.is_category_consented("wellness") is False


def test_payload_round_trips_through_json(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store, payload={"memory_ids": [1, 2, 3], "note": "x"})
    fetched = store.get(initiative.id)
    assert fetched.payload == {"memory_ids": [1, 2, 3], "note": "x"}


def test_list_audit_records_every_transition(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    store.deliver(initiative.id)
    store.resolve(initiative.id, resolution="accepted")

    audit = store.list_audit(initiative.id)
    transitions = [row["transition"] for row in reversed(audit)]
    assert transitions == ["propose", "deliver", "resolve"]


# ---------------------------------------------------------------------------
# S5.3: default-off consent + functional mute
# ---------------------------------------------------------------------------


def test_is_category_consented_and_muted_default_off(db_path):
    store = InitiativeStore(db_path)
    for category in VALID_CATEGORIES:
        assert store.is_category_consented(category) is False
        assert store.is_category_muted(category) is False


def test_get_category_consent_defaults_when_no_row(db_path):
    store = InitiativeStore(db_path)
    assert store.get_category_consent("check_in") == {
        "category": "check_in",
        "allowed": False,
        "muted": False,
        "updated_at": None,
        "actor": None,
    }


def test_set_category_consent_grants_allowed(db_path):
    store = InitiativeStore(db_path)
    result = store.set_category_consent("check_in", allowed=True, actor="user")
    assert result["allowed"] is True
    assert result["muted"] is False
    assert result["updated_at"] is not None
    assert result["actor"] == "user"
    assert store.is_category_consented("check_in") is True


def test_set_category_consent_partial_update_preserves_other_field(db_path):
    store = InitiativeStore(db_path)
    store.set_category_consent("check_in", allowed=True)
    result = store.set_category_consent("check_in", muted=True)
    assert result["allowed"] is True  # unchanged by the muted-only update
    assert result["muted"] is True

    result2 = store.set_category_consent("check_in", muted=False)
    assert result2["allowed"] is True  # still unchanged
    assert result2["muted"] is False


def test_set_category_consent_rejects_unknown_category(db_path):
    store = InitiativeStore(db_path)
    with pytest.raises(InvalidTransitionError):
        store.set_category_consent("not_a_real_category", allowed=True)


def test_list_category_consent_covers_every_valid_category_defaulted(db_path):
    store = InitiativeStore(db_path)
    store.set_category_consent("check_in", allowed=True)

    rows = store.list_category_consent()
    assert {row["category"] for row in rows} == VALID_CATEGORIES
    by_category = {row["category"]: row for row in rows}
    assert by_category["check_in"]["allowed"] is True
    assert by_category["wellness"]["allowed"] is False  # never touched, default-off


def test_list_due_for_delivery_treats_null_due_at_as_due_now(db_path):
    store = InitiativeStore(db_path)
    no_due_at = _propose(store, kind="a")  # due_at defaults to None
    now_ts = int(time.time())
    due = store.list_due_for_delivery(now_ts)
    assert no_due_at.id in {e.id for e in due}


def test_list_due_for_delivery_excludes_not_yet_due(db_path):
    store = InitiativeStore(db_path)
    future = _propose(store, kind="a", due_at="2099-06-01T00:00:00Z")
    now_ts = int(time.time())
    due = store.list_due_for_delivery(now_ts)
    assert future.id not in {e.id for e in due}


def test_list_due_for_delivery_includes_past_due_at(db_path):
    store = InitiativeStore(db_path)
    past = _propose(store, kind="a", due_at="2000-01-01T00:00:00Z")
    now_ts = int(time.time())
    due = store.list_due_for_delivery(now_ts)
    assert past.id in {e.id for e in due}


def test_list_due_for_delivery_includes_deferred_and_snoozed(db_path):
    store = InitiativeStore(db_path)
    deferred = _propose(store, kind="a")
    store.defer(deferred.id, reason="muted")
    snoozed = _propose(store, kind="b")
    store.deliver(snoozed.id)
    store.resolve(snoozed.id, resolution="snoozed", due_at=FAR_PAST)

    now_ts = int(time.time())
    due_ids = {e.id for e in store.list_due_for_delivery(now_ts)}
    assert deferred.id in due_ids
    assert snoozed.id in due_ids


def test_list_due_for_delivery_excludes_terminal_and_proposed_only_states(db_path):
    store = InitiativeStore(db_path)
    denied = _propose(store, kind="a", governance_decision="denied")
    delivered = _propose(store, kind="b")
    store.deliver(delivered.id)
    store.resolve(delivered.id, resolution="accepted")

    now_ts = int(time.time())
    due_ids = {e.id for e in store.list_due_for_delivery(now_ts)}
    assert denied.id not in due_ids
    assert delivered.id not in due_ids  # now "accepted", terminal


# ---------------------------------------------------------------------------
# S5.4: suppression-policy delivery_policy + richer audit detail
# ---------------------------------------------------------------------------


def test_propose_defaults_delivery_policy_to_standard(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    assert initiative.delivery_policy == "standard"


@pytest.mark.parametrize("policy", ["standard", "immediate", "silent", "critical_override"])
def test_propose_accepts_every_valid_delivery_policy(db_path, policy):
    store = InitiativeStore(db_path)
    initiative = _propose(store, delivery_policy=policy)
    assert initiative.delivery_policy == policy


def test_propose_rejects_invalid_delivery_policy(db_path):
    store = InitiativeStore(db_path)
    with pytest.raises(InvalidTransitionError):
        _propose(store, delivery_policy="not_a_real_policy")


def test_delivery_policy_round_trips_through_get(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store, delivery_policy="critical_override")
    fetched = store.get(initiative.id)
    assert fetched.delivery_policy == "critical_override"
    assert fetched.to_dict()["delivery_policy"] == "critical_override"


def test_schema_migration_backfills_delivery_policy_on_existing_database(db_path):
    """Mirrors memory_store.py's own PRAGMA table_info + ALTER TABLE
    pattern -- constructing a second InitiativeStore against an already-
    migrated database must be a no-op, not an error, and pre-existing rows
    (simulated here by dropping the column and re-adding via a fresh
    connection) must default to "standard"."""
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    # Re-opening the store against the same db_path re-runs ensure_schema()
    # -- must not raise "duplicate column" or otherwise fail.
    store2 = InitiativeStore(db_path)
    assert store2.get(initiative.id).delivery_policy == "standard"


def test_defer_writes_structured_json_detail(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    store.defer(initiative.id, reason="quiet_hours")

    audit = store.list_audit(initiative.id)
    defer_row = next(row for row in audit if row["transition"] == "defer")
    assert defer_row["detail"] == {"status": "deferred", "reason": "quiet_hours"}


def test_deliver_writes_structured_json_detail_with_coalescing_metadata(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    store.deliver(initiative.id, coalesced=True, batch_id="b-1", batch_size=3)

    audit = store.list_audit(initiative.id)
    deliver_row = next(row for row in audit if row["transition"] == "deliver")
    detail = deliver_row["detail"]
    assert detail["status"] == "delivered"
    assert detail["coalesced"] is True
    assert detail["batch_id"] == "b-1"
    assert detail["batch_size"] == 3
    assert detail["eligible_at"] is not None


def test_deliver_defaults_to_uncoalesced_single_delivery(db_path):
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    store.deliver(initiative.id)

    audit = store.list_audit(initiative.id)
    deliver_row = next(row for row in audit if row["transition"] == "deliver")
    assert deliver_row["detail"]["coalesced"] is False
    assert deliver_row["detail"]["batch_id"] is None
    assert deliver_row["detail"]["batch_size"] == 1


def test_list_audit_falls_back_to_status_dict_for_legacy_plain_string_detail(db_path):
    """Transitions other than defer/deliver (resolve/expire/cancel/
    supersede) still write a bare status string, unchanged -- list_audit()
    must wrap it rather than raise on the non-JSON value."""
    store = InitiativeStore(db_path)
    initiative = _propose(store)
    store.deliver(initiative.id)
    store.resolve(initiative.id, resolution="accepted")

    audit = store.list_audit(initiative.id)
    resolve_row = next(row for row in audit if row["transition"] == "resolve")
    assert resolve_row["detail"] == {"status": "accepted"}


def test_list_audit_handles_pre_s5_4_raw_string_rows_directly_in_db(db_path):
    """Simulates a row written before this change (a bare status string,
    not JSON) by inserting one directly -- list_audit() must not raise."""
    store = InitiativeStore(db_path)
    initiative = _propose(store)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO initiative_audit (initiative_id, ts, transition, actor, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        (initiative.id, "2026-01-01T00:00:00Z", "defer", None, "deferred"),
    )
    conn.commit()
    conn.close()

    audit = store.list_audit(initiative.id)
    legacy_row = next(row for row in audit if row["ts"] == "2026-01-01T00:00:00Z")
    assert legacy_row["detail"] == {"status": "deferred"}
