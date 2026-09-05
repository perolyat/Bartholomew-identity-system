"""The event backbone against a real service process (Package A).

The acceptance claim, in its strongest form: an authenticated event submitted
through the real HTTP inbound endpoint is processed by the live scheduler,
and nothing in this test calls the interpretation function, the handler, the
processor or the drive. It posts, and it waits.

Everything is real -- a `python -m bartholomew serve` subprocess, real HTTP,
the real double-gated test resolver, the real database, the real autonomy
loop -- for the same reason `test_always_on_service.py` is: the claim is
about composition, and composition is the one thing a test double cannot
stand in for.

Marked `integration`; the default marker expression excludes these and CI's
`critical` job runs them explicitly.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from bartholomew.kernel import inbound_store, objective_store
from bartholomew.kernel.event_processing import store
from bartholomew.kernel.event_processing.adapters import OBSERVATION_NOTE
from bartholomew.kernel.objective_store import ObjectiveStore
from bartholomew.kernel.scheduler.drives import INBOUND_EVENT_PROCESSING_DRIVE
from tests.integration.test_always_on_service import (
    TEST_RESOLVER_ENV,
    ServeProcess,
    _get,
    _post,
)

pytestmark = [pytest.mark.integration]

#: The drive's cadence for this run, set through the ordinary
#: `DRIVE_<TASK_ID>` override the scheduler already supports -- not a test
#: hook. A deployment that wanted tighter latency would set exactly this.
FAST_CADENCE_ENV = {
    f"DRIVE_{INBOUND_EVENT_PROCESSING_DRIVE.upper()}": "every:2",
    "BARTH_DRIVE_PACE_S": "0.1",
}

#: Generous: the loop has to get round every registered drive before ours
#: comes due again, on a CI runner that may be loaded.
PROCESSING_TIMEOUT = 90.0


def _prepare(db_path):
    """A database with one live objective, before any service touches it.

    Created here rather than through the running service because there is no
    objectives HTTP route to create one with, and reaching into a database a
    daemon holds would be a worse test than seeding it first.
    """
    inbound_store.ensure_schema(str(db_path))
    objective_store.ensure_schema(str(db_path))
    store.ensure_schema(str(db_path))
    objectives = ObjectiveStore(str(db_path))
    return objectives.open(
        title="Get the roof repaired",
        outcome_statement="The roof is repaired and no longer leaking",
    )


@pytest.fixture
def service(tmp_path):
    db_path = tmp_path / "event_backbone.db"
    objective = _prepare(db_path)
    svc = ServeProcess(db_path, env_extra={**TEST_RESOLVER_ENV, **FAST_CADENCE_ENV})
    svc.objective_id = objective.id
    try:
        yield svc.start()
    finally:
        svc.kill()


def submit(svc, event_id, payload, *, event_type=OBSERVATION_NOTE, source_id="test-source"):
    return _post(
        svc.port,
        "/api/inbound/events",
        {
            "source_id": source_id,
            "event_id": event_id,
            "event_type": event_type,
            "payload": payload,
        },
        headers={"X-Bartholomew-Test-Token": "integration-only-token"},
    )


def evidence_for(svc):
    """Read the objective's evidence directly, without disturbing the service."""
    conn = sqlite3.connect(f"file:{svc.db_path}?mode=ro", uri=True, timeout=10.0)
    try:
        return conn.execute(
            "SELECT summary, provenance_json, actor, event_kind FROM objective_events "
            "WHERE objective_id = ? AND event_kind = 'fact' ORDER BY id",
            (svc.objective_id,),
        ).fetchall()
    finally:
        conn.close()


def wait_for(predicate, *, timeout=PROCESSING_TIMEOUT, what="the expected state"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.5)
    raise AssertionError(f"the live scheduler never reached {what} within {timeout}s. Last: {last}")


def processing_state(svc, event_id, source_id="test-source"):
    record = store.get(str(svc.db_path), source_id, event_id)
    return record.state if record else None


# ---------------------------------------------------------------------------
# The acceptance scenario
# ---------------------------------------------------------------------------


def test_an_authenticated_event_is_processed_by_the_live_scheduler(service):
    """Post, then wait. Nothing here calls the interpreter."""
    status, body = submit(
        service,
        "evt-roofer-1",
        {"subject": "Roof repair", "body": "Roofer confirmed attendance Tuesday."},
    )
    assert status == 202, body
    assert body["captured"] is True
    # The acknowledgement is still honest: capture never claims processing.
    assert "Not processed" in body["detail"]

    rows = wait_for(
        lambda: evidence_for(service),
        what="the event being attached as evidence by the running scheduler",
    )
    assert len(rows) == 1
    summary, provenance_json, actor, event_kind = rows[0]
    assert "Roofer confirmed attendance Tuesday." in summary
    assert event_kind == "fact"
    assert actor == "inbound:test-source"

    import json

    provenance = json.loads(provenance_json)
    assert provenance["source_kind"] == "inbound_event"
    assert provenance["source_id"] == "test-source"
    assert provenance["event_id"] == "evt-roofer-1"
    assert provenance["verified_by"] == "test-resolver"
    assert provenance["payload_sha256"]
    assert provenance["evidence"] is True
    assert provenance["inbound_row_id"]

    assert processing_state(service, "evt-roofer-1") == store.STATE_PROCESSED


def test_a_duplicate_delivery_produces_no_second_evidence(service):
    payload = {"subject": "Roof repair", "body": "Roofer confirmed attendance Tuesday."}
    assert submit(service, "evt-dup", payload)[0] == 202
    wait_for(lambda: evidence_for(service), what="the first attachment")

    status, body = submit(service, "evt-dup", payload)
    assert status == 200, body
    assert body["duplicate"] is True

    # Give the loop several more cadence periods to do the wrong thing.
    time.sleep(6)
    assert len(evidence_for(service)) == 1


def test_an_irrelevant_event_reaches_a_terminal_irrelevant_disposition(service):
    assert (
        submit(
            service,
            "evt-parcel",
            {"body": "Your parcel was delivered to the front door."},
        )[0]
        == 202
    )

    wait_for(
        lambda: processing_state(service, "evt-parcel") == store.STATE_IRRELEVANT,
        what="an irrelevant disposition",
    )
    assert evidence_for(service) == []


def test_an_unknown_event_type_fails_safely_and_visibly(service):
    assert (
        submit(
            service,
            "evt-unknown",
            {"body": "Roofer confirmed attendance Tuesday."},
            event_type="mail.received",
        )[0]
        == 202
    )

    wait_for(
        lambda: processing_state(service, "evt-unknown") == store.STATE_REFUSED,
        what="a refused disposition for an unregistered type",
    )
    record = store.get(str(service.db_path), "test-source", "evt-unknown")
    assert record.disposition_reason == "unknown_event_type"
    assert evidence_for(service) == []


# ---------------------------------------------------------------------------
# Operational visibility on the live service
# ---------------------------------------------------------------------------


def test_the_health_endpoint_exposes_the_required_processing_state(service):
    status, body = _get(service.port, "/api/health")
    assert status == 200
    component = body["components"]["event_processing"]
    assert component["status"] == "ok"
    assert component["available"] is True
    assert component["enabled"] is True
    for field in (
        "backlog",
        "oldest_unprocessed_age_seconds",
        "last_successful_processing_at",
        "retry_attempts",
        "quarantined",
        "quarantined_sample",
        "states",
        "backlog_limit",
        "registered_event_types",
    ):
        assert field in component, field
    assert OBSERVATION_NOTE in component["registered_event_types"]

    submit(
        service,
        "evt-health",
        {"subject": "Roof repair", "body": "Roofer confirmed attendance Tuesday."},
    )
    processed = wait_for(
        lambda: _get(service.port, "/api/health")[1]["components"]["event_processing"][
            "last_successful_processing_at"
        ],
        what="a recorded successful processing on the health surface",
    )
    assert processed

    final = _get(service.port, "/api/health")[1]["components"]["event_processing"]
    assert final["backlog"] == 0
    assert final["states"]["processed"] >= 1
    # The service as a whole is still ok: a working backbone is not a fault.
    assert body["status"] in ("ok", "degraded")


def test_the_scheduler_records_ticks_for_the_processing_drive(service):
    def ticks():
        conn = sqlite3.connect(f"file:{service.db_path}?mode=ro", uri=True, timeout=10.0)
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM ticks WHERE task_id = ? AND success = 1",
                (INBOUND_EVENT_PROCESSING_DRIVE,),
            ).fetchone()[0]
        finally:
            conn.close()

    assert wait_for(ticks, what="a successful tick for the processing drive") >= 1


def test_the_frozen_evidence_report_covers_processing(service, tmp_path):
    from bartholomew.runtime.evidence import EvidenceStore
    from bartholomew.runtime.evidence_report import build_report

    submit(
        service,
        "evt-frozen",
        {"subject": "Roof repair", "body": "Roofer confirmed attendance Tuesday."},
    )
    wait_for(
        lambda: processing_state(service, "evt-frozen") == store.STATE_PROCESSED,
        what="the event being processed before the report is frozen",
    )

    run_id = "run-backbone-report"
    EvidenceStore(str(service.db_path)).open_incarnation(run_id, runtime_id="observer")
    report = build_report(str(service.db_path), run_id)
    section = report["sources"]["event_processing"]
    assert section["available"] is True
    assert section["states"]["processed"] >= 1
    assert report["summary"]["event_processing_count"] >= 1
    assert report["summary"]["event_processing_quarantined"] == 0
