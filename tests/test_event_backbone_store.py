"""The durable state machine underneath the event backbone (Package A).

These are the properties everything above depends on, proven against a real
SQLite database rather than a stand-in: an event is enqueued at most once,
claimed by at most one pass, recovered when a lease expires, quarantined after
a bounded number of attempts, and never reachable from another tenant.

Nothing here touches interpretation, governance or objectives -- those have
their own suites. This is the queue, alone.
"""

from __future__ import annotations

import time

import pytest

from bartholomew.kernel import inbound_store
from bartholomew.kernel.event_processing import store


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "backbone.db")
    inbound_store.ensure_schema(path)
    store.ensure_schema(path)
    return path


def capture(db_path, event_id, *, source_id="src", event_type="observation.note", runtime_id=None):
    """One durably captured event, through the real capture store."""
    return inbound_store.capture_event(
        db_path,
        source_id=source_id,
        event_id=event_id,
        event_type=event_type,
        occurred_at=None,
        payload={"body": f"content for {event_id}"},
        outcome=inbound_store.OUTCOME_CAPTURED,
        governance_reason=None,
        verified_by="test",
        runtime_id=runtime_id,
    )


# ------------------------------------------------------------------ the sweep


def test_the_sweep_enqueues_each_captured_event_exactly_once(db):
    for i in range(3):
        capture(db, f"e{i}")

    assert store.sweep_captured(db, limit=10) == 3
    # Running it again finds nothing new. The second call is the one that
    # matters: a sweep that re-enqueued would give every event a second
    # processing life on every tick.
    assert store.sweep_captured(db, limit=10) == 0
    assert store.pending_count(db) == 3


def test_a_governance_refused_capture_is_never_enqueued(db):
    inbound_store.capture_event(
        db,
        source_id="src",
        event_id="denied",
        event_type="observation.note",
        occurred_at=None,
        payload={"body": "refused"},
        outcome="governance_denied",
        governance_reason="Denied by Identity policy",
        verified_by="test",
        runtime_id=None,
    )
    assert store.sweep_captured(db, limit=10) == 0
    assert store.get(db, "src", "denied") is None


def test_resync_re_examines_earlier_rows_without_duplicating_them(db):
    capture(db, "e0")
    assert store.sweep_captured(db, limit=10) == 1
    claimed = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=60, max_attempts=3)
    assert store.settle(
        db,
        claimed[0].row_id,
        claimed[0].claim_token,
        state=store.STATE_PROCESSED,
        reason="ok",
    )

    store.resync_from(db, from_inbound_row_id=0)
    # Nothing new: the settled event is already known to the backbone, so a
    # resync cannot resurrect it. That is what makes resync safe to run.
    assert store.sweep_captured(db, limit=10) == 0
    assert store.get(db, "src", "e0").state == store.STATE_PROCESSED


def test_the_sweep_is_bounded_by_its_limit(db):
    for i in range(5):
        capture(db, f"e{i}")
    assert store.sweep_captured(db, limit=2) == 2
    assert store.sweep_captured(db, limit=2) == 2
    assert store.sweep_captured(db, limit=2) == 1


def test_an_empty_database_sweeps_to_zero_rather_than_erroring(tmp_path):
    path = str(tmp_path / "empty.db")
    store.ensure_schema(path)
    assert store.sweep_captured(path, limit=10) == 0
    assert store.pending_count(path) == 0


# --------------------------------------------------------------- claiming


def test_claiming_takes_a_lease_and_spends_an_attempt(db):
    capture(db, "e0")
    store.sweep_captured(db, limit=10)

    claimed = store.claim_batch(db, runtime_id=None, limit=5, lease_seconds=60, max_attempts=3)
    assert len(claimed) == 1
    record = claimed[0]
    assert record.state == store.STATE_CLAIMED
    assert record.attempts == 1
    assert record.claim_token
    assert record.lease_expires_ts and record.lease_expires_ts > time.time()

    # A second pass, while the lease holds, sees nothing to take.
    assert store.claim_batch(db, runtime_id=None, limit=5, lease_seconds=60, max_attempts=3) == []


def test_claims_come_oldest_first(db):
    # Received order, not insertion order, is what fairness means here.
    for i in range(3):
        capture(db, f"e{i}")
    store.sweep_captured(db, limit=10)
    claimed = store.claim_batch(db, runtime_id=None, limit=3, lease_seconds=60, max_attempts=5)
    assert [c.event_id for c in claimed] == ["e0", "e1", "e2"]


def test_a_stale_claim_cannot_settle_an_event_another_pass_owns(db):
    capture(db, "e0")
    store.sweep_captured(db, limit=10)
    first = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=1, max_attempts=5)[0]
    time.sleep(1.1)
    second = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=60, max_attempts=5)[0]
    assert second.claim_token != first.claim_token

    # The first pass comes back from the dead and tries to settle. It is told
    # no, rather than overwriting a decision it no longer owns.
    assert (
        store.settle(
            db,
            first.row_id,
            first.claim_token,
            state=store.STATE_PROCESSED,
            reason="stale",
        )
        is False
    )
    assert store.get(db, "src", "e0").state == store.STATE_CLAIMED


# ------------------------------------------------- leases and recovery


def test_an_expired_lease_is_recovered_and_the_event_is_not_lost(db):
    capture(db, "e0")
    store.sweep_captured(db, limit=10)
    first = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=1, max_attempts=5)[0]

    # The process holding this claim dies here: nothing settles it.
    assert store.get(db, "src", "e0").state == store.STATE_CLAIMED
    time.sleep(1.1)

    recovered = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=60, max_attempts=5)
    assert len(recovered) == 1
    assert recovered[0].row_id == first.row_id
    # The attempt the dead process spent is kept: a crash loop must be bounded
    # like any other repeated failure.
    assert recovered[0].attempts == 2


def test_a_release_returns_the_event_without_spending_its_attempt(db):
    capture(db, "e0")
    store.sweep_captured(db, limit=10)
    claimed = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=60, max_attempts=3)[0]
    assert claimed.attempts == 1

    assert store.release(db, claimed.row_id, claimed.claim_token, reason="brake") is True
    record = store.get(db, "src", "e0")
    assert record.state == store.STATE_CAPTURED
    assert record.attempts == 0


# ----------------------------------------------------- bounded retry


def test_a_poison_event_quarantines_after_its_attempts_and_stops_being_claimed(db):
    capture(db, "poison")
    store.sweep_captured(db, limit=10)

    for expected in range(1, 4):
        claimed = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=60, max_attempts=3)
        assert len(claimed) == 1, f"attempt {expected} was not claimable"
        assert claimed[0].attempts == expected
        outcome = store.fail(
            db,
            claimed[0].row_id,
            claimed[0].claim_token,
            error="boom",
            max_attempts=3,
        )
        assert outcome == (store.STATE_QUARANTINED if expected == 3 else store.STATE_CAPTURED)

    record = store.get(db, "src", "poison")
    assert record.state == store.STATE_QUARANTINED
    assert record.disposition_reason == "attempts_exhausted"
    assert "boom" in record.last_error
    assert store.claim_batch(db, runtime_id=None, limit=5, lease_seconds=60, max_attempts=3) == []


def test_a_poison_event_does_not_starve_the_events_behind_it(db):
    capture(db, "a-poison")
    time.sleep(1.05)  # capture stores whole-second timestamps
    capture(db, "b-healthy")
    store.sweep_captured(db, limit=10)

    # One event at a time, poison first, exactly as a batch_limit=1 deployment
    # would see it. The healthy event must not wait for the poison one to be
    # given up on more than the bounded number of times.
    seen = []
    for _ in range(6):
        claimed = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=60, max_attempts=3)
        if not claimed:
            break
        record = claimed[0]
        seen.append(record.event_id)
        if record.event_id == "a-poison":
            store.fail(db, record.row_id, record.claim_token, error="boom", max_attempts=3)
        else:
            store.settle(
                db,
                record.row_id,
                record.claim_token,
                state=store.STATE_PROCESSED,
                reason="ok",
            )

    assert "b-healthy" in seen
    assert store.get(db, "src", "b-healthy").state == store.STATE_PROCESSED
    assert store.get(db, "src", "a-poison").state == store.STATE_QUARANTINED
    # Bounded: the poison event was tried its three times and no more.
    assert seen.count("a-poison") == 3


def test_a_lease_recovered_event_still_quarantines_within_its_attempt_budget(db):
    """A crash loop is bounded by the same counter an error loop is."""
    capture(db, "crashy")
    store.sweep_captured(db, limit=10)
    for expected in range(1, 4):
        claimed = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=1, max_attempts=3)
        assert claimed and claimed[0].attempts == expected
        time.sleep(1.1)  # the process dies without settling, every time

    # The fourth claim attempt finds an exhausted event and quarantines it
    # instead of handing it out again.
    assert store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=60, max_attempts=3) == []
    assert store.get(db, "src", "crashy").state == store.STATE_QUARANTINED


# ----------------------------------------------------- tenant isolation


def test_a_process_can_only_claim_the_events_captured_for_its_own_runtime(db):
    capture(db, "mine", runtime_id="tenant-a")
    capture(db, "theirs", runtime_id="tenant-b")
    capture(db, "unbound", runtime_id=None)
    store.sweep_captured(db, limit=10)

    assert [c.event_id for c in _claim(db, "tenant-a")] == ["mine"]
    assert [c.event_id for c in _claim(db, "tenant-b")] == ["theirs"]
    # NULL-safe: an unbound process claims exactly the unbound events, and
    # never a bound tenant's.
    assert [c.event_id for c in _claim(db, None)] == ["unbound"]


def _claim(db_path, runtime_id):
    return store.claim_batch(
        db_path,
        runtime_id=runtime_id,
        limit=10,
        lease_seconds=60,
        max_attempts=3,
    )


def test_a_tenants_events_are_not_even_visible_to_another_tenants_claim(db):
    capture(db, "theirs", runtime_id="tenant-b")
    store.sweep_captured(db, limit=10)
    assert _claim(db, "tenant-a") == []
    assert _claim(db, None) == []
    assert store.get(db, "src", "theirs").state == store.STATE_CAPTURED


# -------------------------------------------------------- operator recovery


def test_requeue_returns_quarantined_events_with_a_fresh_attempt_budget(db):
    capture(db, "poison")
    store.sweep_captured(db, limit=10)
    for _ in range(3):
        claimed = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=60, max_attempts=3)
        store.fail(db, claimed[0].row_id, claimed[0].claim_token, error="boom", max_attempts=3)
    assert store.get(db, "src", "poison").state == store.STATE_QUARANTINED

    assert store.requeue(db) == 1
    record = store.get(db, "src", "poison")
    assert record.state == store.STATE_CAPTURED
    assert record.attempts == 0
    assert record.disposition_reason == "requeued_by_operator"
    assert (
        len(store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=60, max_attempts=3)) == 1
    )


def test_requeue_does_not_touch_settled_decisions_by_default(db):
    capture(db, "done")
    store.sweep_captured(db, limit=10)
    claimed = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=60, max_attempts=3)[0]
    store.settle(
        db,
        claimed.row_id,
        claimed.claim_token,
        state=store.STATE_IRRELEVANT,
        reason="no_matching_live_objective",
    )
    assert store.requeue(db) == 0
    assert store.get(db, "src", "done").state == store.STATE_IRRELEVANT


def test_requeue_refuses_an_unknown_state(db):
    with pytest.raises(ValueError, match="unknown processing state"):
        store.requeue(db, from_states=("nonsense",))


def test_a_handler_cannot_settle_an_event_straight_into_quarantine(db):
    capture(db, "e0")
    store.sweep_captured(db, limit=10)
    claimed = store.claim_batch(db, runtime_id=None, limit=1, lease_seconds=60, max_attempts=3)[0]
    with pytest.raises(ValueError, match="quarantine is reached through fail"):
        store.settle(
            db,
            claimed.row_id,
            claimed.claim_token,
            state=store.STATE_QUARANTINED,
            reason="nope",
        )


def test_one_tenants_pass_does_not_recover_or_quarantine_anothers_rows(db):
    """Isolation that only covers the read is not isolation.

    Claiming filters by runtime, but a pass also recovers expired leases and
    quarantines exhausted events. Both are mutations, and neither may reach
    another tenant's rows.
    """
    capture(db, "theirs", runtime_id="tenant-b")
    store.sweep_captured(db, limit=10)
    theirs = store.claim_batch(
        db,
        runtime_id="tenant-b",
        limit=1,
        lease_seconds=1,
        max_attempts=1,
    )[0]
    time.sleep(1.1)

    # Tenant A's pass runs while tenant B's lease is expired and its attempt
    # budget is already spent -- both housekeeping conditions are true.
    assert _claim(db, "tenant-a") == []
    untouched = store.get(db, "src", "theirs")
    assert untouched.state == store.STATE_CLAIMED
    assert untouched.claim_token == theirs.claim_token
    assert untouched.attempts == 1

    # Tenant B's own pass is what recovers and then quarantines it.
    assert (
        store.claim_batch(db, runtime_id="tenant-b", limit=1, lease_seconds=60, max_attempts=1)
        == []
    )
    assert store.get(db, "src", "theirs").state == store.STATE_QUARANTINED
