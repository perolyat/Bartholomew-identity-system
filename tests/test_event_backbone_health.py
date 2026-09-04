"""Operational visibility for the event backbone (Package A).

Two surfaces, one requirement: an operator, and a reviewer reading a frozen
report days later, must be able to see backlog size, the age of the oldest
unprocessed event, when processing last succeeded, how much work is being
redone, and what has been quarantined and why.

The truthfulness rule is the same one `evidence_report` already holds to and
is asserted here directly: a database with no processing table reports
``available: false``, never zero. "Nothing was processed" and "we cannot tell
what was processed" are opposite findings.
"""

from __future__ import annotations

import time

import pytest

from bartholomew.kernel import inbound_store
from bartholomew.kernel.event_processing import store
from bartholomew.kernel.event_processing.adapters import OBSERVATION_NOTE
from bartholomew.kernel.event_processing.config import EventProcessingSettings
from bartholomew.kernel.event_processing.health import health_component, processing_health
from bartholomew.runtime.evidence import EvidenceStore
from bartholomew.runtime.evidence_report import REPORT_SCHEMA_VERSION, build_report, freeze


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "health.db")
    inbound_store.ensure_schema(path)
    store.ensure_schema(path)
    return path


def capture(db_path, event_id, *, event_type=OBSERVATION_NOTE):
    return inbound_store.capture_event(
        db_path,
        source_id="src",
        event_id=event_id,
        event_type=event_type,
        occurred_at=None,
        payload={"body": f"content {event_id}"},
        outcome=inbound_store.OUTCOME_CAPTURED,
        governance_reason=None,
        verified_by="test",
        runtime_id=None,
    )


def claim_one(db_path, max_attempts=3):
    claimed = store.claim_batch(
        db_path,
        runtime_id=None,
        limit=1,
        lease_seconds=60,
        max_attempts=max_attempts,
    )
    assert claimed
    return claimed[0]


# ------------------------------------------------------------- truthfulness


def test_a_database_with_no_processing_table_reports_unknown_not_zero(tmp_path):
    path = str(tmp_path / "bare.db")
    inbound_store.ensure_schema(path)

    snapshot = processing_health(path)
    assert snapshot["available"] is False
    assert "does not exist" in snapshot["reason"]
    # The load-bearing part: no count is offered at all, so a caller cannot
    # mistake "we could not tell" for "nothing happened".
    assert "backlog" not in snapshot
    assert "quarantined" not in snapshot
    assert health_component(path)["status"] == "unknown"


def test_an_empty_but_present_table_reports_zero_and_says_so(db):
    snapshot = processing_health(db)
    assert snapshot["available"] is True
    assert snapshot["backlog"] == 0
    assert snapshot["quarantined"] == 0
    assert snapshot["last_successful_processing_at"] is None
    assert health_component(db)["status"] == "ok"


# ------------------------------------------------------- the six questions


def test_health_reports_every_operational_number_the_contract_requires(db):
    capture(db, "old")
    time.sleep(1.05)  # capture timestamps are whole seconds
    capture(db, "recent")
    capture(db, "poison")
    store.sweep_captured(db, limit=10)

    processed = claim_one(db)
    assert processed.event_id == "old"
    assert store.settle(
        db,
        processed.row_id,
        processed.claim_token,
        state=store.STATE_PROCESSED,
        reason="evidence_recorded",
    )

    settled = claim_one(db)
    assert settled.event_id == "recent"
    store.settle(
        db,
        settled.row_id,
        settled.claim_token,
        state=store.STATE_IRRELEVANT,
        reason="no_matching_live_objective",
    )

    # "poison" is now the only claimable event: fail it twice, leaving it in
    # the backlog with a spent retry rather than quarantined, so the snapshot
    # below shows a backlog, an age, a retry count and a settled success all
    # at once.
    for _ in range(2):
        record = claim_one(db)
        assert record.event_id == "poison"
        store.fail(db, record.row_id, record.claim_token, error="boom", max_attempts=3)

    snapshot = processing_health(db)
    assert snapshot["available"] is True
    # 1. backlog size
    assert snapshot["backlog"] == snapshot["pending"] + snapshot["in_flight"]
    assert snapshot["backlog"] >= 1
    # 2. oldest unprocessed age
    assert snapshot["oldest_unprocessed_at"] is not None
    assert snapshot["oldest_unprocessed_age_seconds"] >= 0
    # 3. last successful processing
    assert snapshot["last_successful_processing_at"] is not None
    # 4. retry attempts
    assert snapshot["retry_attempts"] >= 1
    assert snapshot["events_retried"] >= 1
    # 5 + 6. quarantine count and reason
    assert snapshot["quarantined"] >= 0
    assert snapshot["states"]["processed"] == 1
    assert set(snapshot["states"]) == set(store.ALL_STATES)
    # Configuration an operator needs alongside the numbers.
    assert snapshot["enabled"] is True
    assert snapshot["backlog_limit"] >= 1
    assert OBSERVATION_NOTE in snapshot["registered_event_types"]


def test_a_quarantined_event_is_named_with_its_reason(db):
    capture(db, "poison")
    store.sweep_captured(db, limit=10)
    for _ in range(3):
        record = claim_one(db)
        store.fail(db, record.row_id, record.claim_token, error="handler exploded", max_attempts=3)

    snapshot = processing_health(db)
    assert snapshot["quarantined"] == 1
    sample = snapshot["quarantined_sample"]
    assert len(sample) == 1
    assert sample[0]["event_id"] == "poison"
    assert sample[0]["reason"] == "attempts_exhausted"
    assert "handler exploded" in sample[0]["last_error"]
    assert sample[0]["attempts"] == 3
    assert snapshot["quarantined_sample_truncated"] is False


def test_the_oldest_unprocessed_age_tracks_the_head_of_the_queue(db):
    capture(db, "head")
    store.sweep_captured(db, limit=10)
    snapshot = processing_health(db, now_ts=int(time.time()) + 3600)
    assert snapshot["oldest_unprocessed_age_seconds"] >= 3600


def test_a_full_backlog_is_reported_as_a_degraded_component(db):
    for i in range(3):
        capture(db, f"e{i}")
    store.sweep_captured(db, limit=10)

    healthy = processing_health(db, settings=EventProcessingSettings(backlog_max=100))
    assert healthy["backlog_full"] is False

    full = processing_health(db, settings=EventProcessingSettings(backlog_max=2))
    assert full["backlog_full"] is True
    # Genuinely degraded rather than merely busy: at this point capture starts
    # refusing, so an operator needs to see it as a fault.
    assert full["backlog"] >= full["backlog_limit"]


def test_a_disabled_backbone_says_so_without_claiming_a_fault(db):
    snapshot = processing_health(db, settings=EventProcessingSettings(enabled=False))
    assert snapshot["enabled"] is False
    assert snapshot["available"] is True


# ------------------------------------------------------ the frozen report


def test_the_frozen_report_carries_the_processing_section(db):
    run_id = "run-health-1"
    EvidenceStore(db).ensure_schema()
    EvidenceStore(db).open_incarnation(run_id, runtime_id="rt-1")

    capture(db, "done")
    capture(db, "poison")
    store.sweep_captured(db, limit=10)
    processed = claim_one(db)
    store.settle(
        db,
        processed.row_id,
        processed.claim_token,
        state=store.STATE_PROCESSED,
        reason="evidence_recorded",
    )
    for _ in range(3):
        record = claim_one(db)
        store.fail(db, record.row_id, record.claim_token, error="boom", max_attempts=3)

    report = build_report(db, run_id)
    section = report["sources"]["event_processing"]
    assert section["available"] is True
    assert section["count"] == 2
    assert section["states"]["processed"] == 1
    assert section["states"]["quarantined"] == 1
    assert section["quarantined"] == 1
    assert section["last_successful_processing_at"] is not None
    assert section["retry_attempts"] >= 1
    assert section["items"][0]["event_id"] == "poison"
    assert section["items"][0]["reason"] == "attempts_exhausted"

    summary = report["summary"]
    assert summary["event_processing_count"] == 2
    assert summary["event_processing_quarantined"] == 1
    assert summary["event_processing_backlog"] == 0
    assert summary["event_processing_retry_attempts"] >= 1
    assert report["report_schema_version"] == REPORT_SCHEMA_VERSION


def test_the_frozen_report_reports_a_missing_table_as_unreadable(tmp_path):
    path = str(tmp_path / "no-backbone.db")
    EvidenceStore(path).ensure_schema()
    EvidenceStore(path).open_incarnation("run-2", runtime_id="rt-1")

    report = build_report(path, "run-2")
    section = report["sources"]["event_processing"]
    assert section["available"] is False
    assert "count" not in section
    assert "event_processing" in report["summary"]["unreadable_sources"]
    assert report["summary"]["complete"] is False


def test_the_frozen_report_stays_deterministic_with_the_new_section(db):
    EvidenceStore(db).ensure_schema()
    EvidenceStore(db).open_incarnation("run-3", runtime_id="rt-1")
    capture(db, "e0")
    store.sweep_captured(db, limit=10)

    first = freeze(db, "run-3")
    second = freeze(db, "run-3")
    assert first["digest"] == second["digest"]
