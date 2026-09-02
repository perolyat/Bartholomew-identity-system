"""
The Learning and Memory Control Centre over HTTP.

These are HTTP-level tests against the real app, with a real kernel and a real
database -- the same shape as `tests/test_memory_agency.py`, and for the same
reason: the claims here are about what a client can actually reach, and a
mocked store cannot answer that.

What this module pins that the seam-level suite cannot:

  * every route is classified and none of them is public;
  * no endpoint accepts an identity, a tenant or a database path, so there is
    no surface through which another runtime's records could be named;
  * a stale edit, a stale approval and a stale policy update all come back as
    409 with both versions, across a reload;
  * the shadow-mode statement is on every response that mentions a policy, and
    a `would_accept` never arrives without it;
  * export takes an explicit selection, refuses sensitive and unreadable
    material, refuses approvals, and reports every refusal.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

_db_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
_db_dir.mkdir(parents=True, exist_ok=True)
_DB_PATH = str(_db_dir / "learning-api.db")
os.environ["BARTH_DB_PATH"] = _DB_PATH

from bartholomew.kernel import (  # noqa: E402
    candidate_learning,
    learning_authorization,
    learning_policy,
)
from bartholomew.kernel import objective_store as os_mod  # noqa: E402
from bartholomew.platform.route_policy import (  # noqa: E402
    ROUTE_CAPABILITIES,
    is_public_path,
)
from bartholomew_api_bridge_v0_1.services.api import app as app_module  # noqa: E402

COMPETENCY_ID = "estate_management"
REVIEWER = "taylor"


@pytest.fixture(scope="module")
def client():
    # Re-asserted immediately before the app starts, for the reason
    # tests/test_self_state_api.py's client fixture documents.
    os.environ["BARTH_DB_PATH"] = _DB_PATH
    with TestClient(app_module.app) as c:
        yield c


def _seed_candidate(**overrides) -> tuple[str, str]:
    """
    Put one real candidate lesson in the database, through the real seam.

    A standalone `MemoryStore`/`ObjectiveStore` against the same file rather
    than the running kernel's -- the kernel's store is bound to the daemon's
    own event loop, so awaiting it from `asyncio.run()` deadlocks. Same
    approach, and same reason, as `tests/test_memory_agency.py`'s seeding.
    """
    import asyncio

    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.kernel.objective_store import ObjectiveStore
    from bartholomew.kernel.runtime_contract import (
        run_candidate_lesson_through_runtime_contract,
    )

    class _Ctx:
        def __init__(self, mem, objectives):
            self.mem = mem
            self.objective_store = objectives
            self.identity_context = None
            self.governance_store = None
            self.blocking_executor = None

    async def _run():
        os_mod.ensure_schema(_DB_PATH)
        store = ObjectiveStore(_DB_PATH)
        objective = store.open(
            title=overrides.get("title", "Get the boiler serviced before winter"),
            outcome_statement="A working boiler with a valid service record",
        )
        store.record(
            objective.id,
            event_kind=os_mod.EVENT_FACT,
            summary="The boiler is still inside its manufacturer warranty period",
        )
        store.record(
            objective.id,
            event_kind=os_mod.EVENT_ACTION,
            summary="Called the boiler warranty line and booked a free service visit",
        )
        store.complete(
            objective.id,
            resolution=os_mod.RESOLUTION_ACHIEVED,
            outcome_note="Serviced free under warranty",
        )

        mem = MemoryStore(_DB_PATH)
        await mem.init()
        try:
            result = await run_candidate_lesson_through_runtime_contract(
                _Ctx(mem, store),
                "learning_propose",
                objective_id=objective.id,
                competency_id=COMPETENCY_ID,
            )
            assert result.outcome == "proposed", result.reason
            return COMPETENCY_ID, result.lesson.slug
        finally:
            await mem.close(checkpoint=False)

    return asyncio.run(_run())


def _seed_memory(kind: str, key: str, value: str) -> bool:
    import asyncio
    from datetime import datetime, timezone

    from bartholomew.kernel.memory_store import MemoryStore

    async def _run():
        store = MemoryStore(_DB_PATH)
        await store.init()
        try:
            result = await store.upsert_memory(
                kind,
                key,
                value,
                datetime.now(timezone.utc).isoformat(),
            )
            return result.stored
        finally:
            await store.close(checkpoint=False)

    return asyncio.run(_run())


@pytest.fixture
def candidate(client):
    competency_id, slug = _seed_candidate()
    yield competency_id, slug


# ===========================================================================
# The boundary
# ===========================================================================


def test_every_learning_route_is_classified_and_none_is_public():
    """
    Acceptance requirement 1, at the authorisation boundary.

    Routes are default-deny, so an unclassified one would 403 rather than
    open -- but a *public* one would be a real hole, and the control centre
    shows a person's whole memory.
    """
    learning_routes = [
        (method, path) for (method, path) in ROUTE_CAPABILITIES if path.startswith("/api/learning")
    ]
    assert len(learning_routes) >= 17

    for _method, path in learning_routes:
        assert not is_public_path(path), f"{path} must not be public"

    capabilities = {ROUTE_CAPABILITIES[route].value for route in learning_routes}
    assert capabilities == {
        "learning:read",
        "learning:review",
        "learning:approve",
        "learning:policy",
        "learning:export",
    }


def test_only_approving_and_accepting_sit_behind_the_approve_capability():
    """
    The split is the architecture, not the screen layout.

    Granting a candidate-bound approval and consolidating are the two acts
    that can make a lesson trusted; everything else is review or reading.
    """
    approve_routes = {
        path
        for (method, path), capability in ROUTE_CAPABILITIES.items()
        if capability.value == "learning:approve"
    }
    assert approve_routes == {
        "/api/learning/candidates/{competency_id}/{slug}/approve",
        "/api/learning/candidates/{competency_id}/{slug}/accept",
    }


def test_no_learning_endpoint_accepts_an_identity_or_a_database_path(client):
    """
    Acceptance requirement 1.

    Tenancy is the process (`bartholomew.platform.runtime_registry`), so the
    isolation property that matters at this layer is negative: there must be
    no parameter through which a caller could name a different runtime, a
    different user, or a different file.
    """
    schema = client.get("/openapi.json").json()
    forbidden = {
        "user_id",
        "userid",
        "tenant",
        "tenant_id",
        "account",
        "account_id",
        "db_path",
        "database",
        "principal",
        "runtime_id",
        "data_root",
    }

    for path, operations in schema["paths"].items():
        if not path.startswith("/api/learning"):
            continue
        for method, operation in operations.items():
            if method not in ("get", "post", "put", "delete"):
                continue
            names = {
                str(param.get("name", "")).lower() for param in operation.get("parameters", [])
            }
            assert not (names & forbidden), f"{method.upper()} {path} accepts {names & forbidden}"

            body = operation.get("requestBody")
            if not body:
                continue
            body_text = json.dumps(body).lower()
            for name in forbidden:
                assert (
                    f'"{name}"' not in body_text
                ), f"{method.upper()} {path} accepts a {name} field in its body"


# ===========================================================================
# Reading
# ===========================================================================


def test_the_overview_leads_with_the_shadow_mode_statement(client):
    response = client.get("/api/learning/overview")
    assert response.status_code == 200
    body = response.json()

    assert body["shadow_mode"]["execution_mode"] == "shadow"
    assert body["shadow_mode"]["automatic_acceptance_enabled"] is False
    assert "cannot act on it" in body["shadow_mode"]["notice"]
    assert body["policy"]["auto_acceptance_enabled"] is False


def test_a_candidate_detail_exposes_provenance_and_supporting_experience(client, candidate):
    """Acceptance requirement 3, over HTTP."""
    competency_id, slug = candidate
    response = client.get(f"/api/learning/candidates/{competency_id}/{slug}")
    assert response.status_code == 200
    detail = response.json()["candidate"]

    experience = detail["supporting_experience"]
    assert experience["objective_id"]
    assert experience["objective_title"]
    assert len(experience["supporting_event_ids"]) == 2
    assert len(experience["observations"]) == 2

    provenance = detail["provenance"]
    assert provenance["source_type"] == "experience"
    assert provenance["recorded_by"] == "reflection"
    assert provenance["reflection_row_id"] is not None

    # Confidence, epistemic status, privacy and sharing, all in one place.
    assert detail["epistemic_status"] == "inference"
    assert detail["confidence"] == candidate_learning.SINGLE_EXPERIENCE_CONFIDENCE
    assert "privacy_class" in detail
    assert detail["sharing"]["transport_available"] is False
    assert detail["sharing"]["eligible"] is False
    assert detail["can_accept_now"] is False
    assert detail["approval"] is None


def test_listing_candidates_carries_governance_metadata(client, candidate):
    """Acceptance requirement 2: a view must not present a row without its
    classification."""
    response = client.get("/api/learning/candidates")
    assert response.status_code == 200
    entries = response.json()["candidates"]
    assert entries

    entry = entries[0]
    for field in (
        "privacy_class",
        "category",
        "recall_policy",
        "governance_known",
        "readable",
        "classification",
        "confidence",
        "epistemic_status",
        "review_state",
    ):
        assert field in entry, f"a candidate listing must expose {field}"


# ===========================================================================
# Editing, approving, accepting: the stale-state paths
# ===========================================================================


def test_a_material_edit_reports_the_new_fingerprint_and_revision(client, candidate):
    """Acceptance requirements 4 and 24."""
    competency_id, slug = candidate
    before = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]

    response = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/edit",
        json={
            "expected_revision": before["revision"],
            "editor": REVIEWER,
            "inferred_rule": "Ring the warranty line before booking a paid engineer",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "edited"
    assert body["material_change"] is True
    assert body["fingerprint_after"] != body["fingerprint_before"]
    assert body["revision"] == before["revision"] + 1

    # And it survives a reload: the detail endpoint reports the new state.
    after = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()
    assert after["candidate"]["revision"] == before["revision"] + 1
    assert after["candidate"]["rule"].startswith("Ring the warranty line")
    assert after["superseded_revisions"], "the prior revision must be readable afterwards"
    assert after["superseded_revisions"][0]["rule"] == before["rule"]


def test_an_administrative_edit_reports_that_nothing_changed(client, candidate):
    """Acceptance requirement 6, over HTTP."""
    competency_id, slug = candidate
    before = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]

    response = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/edit",
        json={
            "expected_revision": before["revision"],
            "editor": REVIEWER,
            "display_state": "pinned",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "unchanged"
    assert body["material_change"] is False
    assert body["fingerprint_after"] == body["fingerprint_before"]
    assert body["revision"] == before["revision"]
    assert "Nothing about what this lesson claims has changed" in body["detail"]


def test_a_stale_edit_returns_409_with_both_versions(client, candidate):
    """
    Acceptance requirements 19 and 24.

    The second request is what a user's second browser tab sends after the
    first tab saved. It must be refused with enough information to reconcile,
    not merged and not applied.
    """
    competency_id, slug = candidate
    stale = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]

    first = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/edit",
        json={
            "expected_revision": stale["revision"],
            "editor": "first",
            "inferred_rule": "The first tab's wording",
        },
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/edit",
        json={
            "expected_revision": stale["revision"],
            "editor": "second",
            "inferred_rule": "The second tab's wording",
        },
    )
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["outcome"] == "revision_conflict"
    assert detail["your_expected_revision"] == stale["revision"]
    assert detail["stored_revision"] > stale["revision"]
    assert detail["stored_rule"] == "The first tab's wording"

    live = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    assert live["rule"] == "The first tab's wording"


def test_approving_from_a_stale_screen_is_refused(client, candidate):
    """
    Acceptance requirement 24.

    Approving is the act that must not happen against something the approver
    did not read, so a stale revision is refused before the approval exists.
    """
    competency_id, slug = candidate
    stale = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]

    client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/edit",
        json={
            "expected_revision": stale["revision"],
            "editor": "someone-else",
            "inferred_rule": "Changed while the approver was reading",
        },
    )

    response = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/approve",
        json={"approver": REVIEWER, "expected_revision": stale["revision"]},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["outcome"] == "revision_conflict"

    detail = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    assert detail["approval"] is None, "nothing may have been approved"


def test_approving_does_not_accept(client, candidate):
    """Two acts, two endpoints. Approving consolidates nothing."""
    competency_id, slug = candidate
    detail = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]

    response = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/approve",
        json={"approver": REVIEWER, "expected_revision": detail["revision"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["consolidated"] is False
    assert "Nothing has been learned yet" in body["detail"]

    after = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    assert after["review_state"] == "proposed"
    assert after["approval"]["valid_for_current_revision"] is True
    assert after["can_accept_now"] is True
    assert after["consolidated_key"] is None


def test_accepting_without_an_approval_is_refused(client, candidate):
    """Acceptance requirement 7, over HTTP."""
    competency_id, slug = candidate
    response = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/accept",
        json={"reviewer": REVIEWER},
    )
    # 403, not 400: "nobody has approved this specific lesson" is an
    # authorisation refusal, and the seam reports it as one
    # (`governance_allowed=False`). The outcome names which refusal it was, so
    # an operator can tell it from "this deployment does not permit learning".
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["outcome"] == "acceptance_approval_required"
    assert detail["consolidated"] is False


def test_an_edit_after_approval_makes_acceptance_fail(client, candidate):
    """Acceptance requirements 5 and 8, over HTTP."""
    competency_id, slug = candidate
    detail = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]

    client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/approve",
        json={"approver": REVIEWER, "expected_revision": detail["revision"]},
    )
    edited = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/edit",
        json={
            "expected_revision": detail["revision"],
            "editor": REVIEWER,
            "confidence": 0.99,
        },
    )
    assert edited.json()["approval_invalidated"] is True

    after = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    assert after["approval"]["valid_for_current_revision"] is False
    assert "no longer authorises" in after["approval"]["detail"]
    assert after["can_accept_now"] is False

    response = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/accept",
        json={"reviewer": REVIEWER},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["outcome"] == "acceptance_approval_required"


def test_the_full_approve_then_accept_path_produces_knowledge(client, candidate):
    """Acceptance requirement 9, over HTTP and across a reload."""
    competency_id, slug = candidate
    detail = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]

    client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/approve",
        json={"approver": REVIEWER, "expected_revision": detail["revision"]},
    )
    response = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/accept",
        json={"reviewer": REVIEWER},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["consolidated"] is True
    assert body["consolidated_kind"] == "competency_heuristic"

    competencies = client.get("/api/learning/competencies").json()["competencies"]
    match = next(c for c in competencies if c["key"] == body["consolidated_key"])
    assert match["epistemic_status"] == "inference"
    assert match["retrievable"] is True


def test_rejection_is_reported_as_final(client, candidate):
    """Acceptance requirement 10, over HTTP."""
    competency_id, slug = candidate
    response = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/reject",
        json={"reviewer": REVIEWER, "note": "Not a rule I want followed"},
    )
    assert response.status_code == 200
    assert response.json()["consolidated"] is False
    assert "cannot be accepted later" in response.json()["detail"]

    # Still listed -- a rejection is a record, not an erasure.
    listed = client.get("/api/learning/candidates", params={"review_state": "rejected"}).json()
    assert any(c["slug"] == slug for c in listed["candidates"])


# ===========================================================================
# Shadow mode
# ===========================================================================


def test_a_shadow_evaluation_never_arrives_without_its_statement(client, candidate):
    """Acceptance requirements 11, 12 and 24."""
    competency_id, slug = candidate
    response = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/shadow-evaluate",
        json={"requested_by": REVIEWER},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["shadow_mode"]["execution_mode"] == "shadow"
    assert body["consolidated"] is False
    assert body["authorizes_acceptance"] is False

    evaluation = body["evaluation"]
    assert evaluation["decision"] in learning_policy.DECISIONS
    assert evaluation["matched_rules"]
    assert evaluation["reasons"]
    assert evaluation["authorizes_acceptance"] is False
    assert "cannot act on it" in evaluation["shadow_mode_notice"]

    # Unchanged afterwards, across a reload.
    after = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()
    assert after["candidate"]["review_state"] == "proposed"
    assert after["candidate"]["approval"] is None
    assert after["shadow_evaluations"], "the preview must be readable afterwards"


def test_a_configured_auto_accept_policy_is_stored_but_never_enabled(client):
    """
    Acceptance requirements 17 and 18, at the surface a user actually touches.

    The form is filled in the way someone would fill it if they wanted
    automatic acceptance, and every response still says it is off.
    """
    current = client.get("/api/learning/policy").json()
    assert current["shadow_mode"]["automatic_acceptance_enabled"] is False

    response = client.put(
        "/api/learning/policy",
        json={
            "expected_revision": current["policy"]["revision"],
            "updated_by": REVIEWER,
            "enabled_categories": ["procedural"],
            "max_risk": "critical",
            "require_reversible": False,
            "min_supporting_experiences": 1,
            "min_confidence": 0.0,
            "max_affected_capabilities": 99,
            "max_affected_applications": 99,
            "excluded_privacy_classes": [],
            "excluded_classifications": [],
            "exclude_sharing_eligible": False,
            "requested_execution_mode": "auto",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["auto_acceptance_enabled"] is False
    assert body["policy"]["requested_execution_mode"] == "auto"
    assert body["policy"]["execution_mode"] == "shadow"
    assert "It does not accept anything" in body["detail"]

    reloaded = client.get("/api/learning/policy").json()
    assert reloaded["policy"]["execution_mode"] == "shadow"
    assert reloaded["shadow_mode"]["automatic_acceptance_enabled"] is False

    # And a candidate the policy would accept is still only previewed.
    competency_id, slug = _seed_candidate(title="A second boiler objective")
    preview = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/shadow-evaluate",
        json={},
    ).json()
    assert preview["evaluation"]["decision"] == "would_accept"
    assert preview["consolidated"] is False

    detail = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    assert detail["review_state"] == "proposed"
    assert detail["approval"] is None
    assert detail["can_accept_now"] is False


def test_a_stale_policy_update_returns_409_with_the_stored_policy(client):
    """Acceptance requirements 19 and 24, on the policy surface."""
    current = client.get("/api/learning/policy").json()["policy"]
    response = client.put(
        "/api/learning/policy",
        json={
            "expected_revision": max(0, current["revision"] - 1),
            "updated_by": "second-tab",
            "enabled_categories": [],
        },
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["outcome"] == "revision_conflict"
    assert detail["stored_policy"]["revision"] == current["revision"]

    unchanged = client.get("/api/learning/policy").json()["policy"]
    assert unchanged["revision"] == current["revision"]


# ===========================================================================
# Export
# ===========================================================================


def test_export_takes_an_explicit_selection_and_never_bulk_dumps(client, candidate):
    """
    Acceptance requirement: "not bulk-export the complete memory database by
    default".

    An empty selection exports nothing. There is no argument that means
    "everything", and the endpoint is a POST precisely so a bare GET cannot
    become one.
    """
    response = client.post("/api/learning/export", json={"records": []})
    assert response.status_code == 200
    payload = response.json()
    assert payload["exported_count"] == 0
    assert payload["records"] == []
    assert payload["schema_version"] == 1
    assert payload["schema"] == "bartholomew.learning_memory_export"


def test_export_preserves_provenance_and_classification(client, candidate):
    competency_id, slug = candidate
    key = candidate_learning.key_for(competency_id, slug)

    response = client.post(
        "/api/learning/export",
        json={
            "records": [{"kind": candidate_learning.KIND, "key": key}],
            "requested_by": REVIEWER,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["exported_count"] == 1

    record = payload["records"][0]
    assert record["key"] == key
    assert record["value"]["provenance"]["source_type"] == "experience"
    assert record["value"]["source"]["supporting_event_ids"]
    assert "privacy_class" in record["classification"]
    assert "consent_at" in record["consent"]


def test_sensitive_content_is_redacted_before_it_can_ever_be_exported(client):
    """
    Acceptance requirement 21, on the path that actually occurs.

    A `user.secure` value is not stored on the way in: it is held for consent,
    and when the person approves it the governed write applies the rules'
    redaction before the row lands. So the sensitive text never reaches the
    database, and the control centre cannot show it -- let alone export it.

    This is the strong half of the guarantee. `_export_blocked_reason` below
    is the second, independent half, for a row that carries a restricted
    classification anyway.
    """
    key = "learning_export_secure_probe"
    stored = _seed_memory("fact", key, "my bank account number is 12345678")
    assert stored is False, "a user.secure value must be queued, not stored outright"

    pending = client.get("/api/consent/pending-writes").json()
    entry = next(item for item in pending["entries"] if item["key"] == key)
    assert entry["privacy_class"] == "user.secure"
    approved = client.post(f"/api/consent/pending-writes/{entry['id']}/approve")
    assert approved.status_code == 200

    listed = client.get("/api/memory", params={"kind": "fact", "search": key}).json()
    row = next(item for item in listed["entries"] if item["key"] == key)
    assert "12345678" not in row["value"], "the account number must not have been stored"

    export = client.post(
        "/api/learning/export",
        json={"records": [{"kind": "fact", "key": key}]},
    ).json()
    assert "12345678" not in json.dumps(export)


@pytest.mark.parametrize(
    ("entry", "expected_fragment"),
    [
        (
            {"readable": False, "governance_known": False, "privacy_class": None},
            "cannot be read by this process",
        ),
        (
            {"readable": True, "governance_known": False, "privacy_class": None},
            "could not work out how this record is classified",
        ),
        (
            {"readable": True, "governance_known": True, "privacy_class": "user.secure"},
            "user.secure",
        ),
        (
            {"readable": True, "governance_known": True, "privacy_class": "user.health"},
            "user.health",
        ),
        (
            {"readable": True, "governance_known": True, "privacy_class": "user.sensitive"},
            "user.sensitive",
        ),
        (
            {"readable": True, "governance_known": True, "privacy_class": "thirdparty.private"},
            "thirdparty.private",
        ),
        (
            {"readable": True, "governance_known": True, "privacy_class": "user.emotional"},
            "user.emotional",
        ),
    ],
)
def test_the_export_gate_refuses_restricted_and_unreadable_records(entry, expected_fragment):
    """
    Acceptance requirement 21, as the rule rather than as one path through it.

    The shipped memory rules redact `user.secure` content before storage, so
    the integration test above cannot exercise every restricted class. This
    one does, against the function that actually decides -- because a
    deployment whose `config/memory_rules.yaml` assigns one of these classes
    without redaction must still be refused, and "we never reached that case
    in practice" is not a guarantee.
    """
    from bartholomew_api_bridge_v0_1.services.api.routes.learning import (
        _export_blocked_reason,
    )

    reason = _export_blocked_reason(entry)
    assert reason is not None
    assert expected_fragment in reason


def test_every_consent_requiring_privacy_class_is_export_blocked():
    """
    The decision-forcing test.

    `_NEVER_EXPORT_PRIVACY_CLASSES` is hard-coded rather than derived, because
    the rules file is user-editable and deriving would let editing it silently
    remove an export restriction. The cost of hard-coding is drift, and this is
    what stops it: every class the *shipped* rules gate on consent must be
    named in the block list.

    An earlier version of that list missed `user.sensitive` -- the broadest
    consent-gated class the shipped rules define, covering bank, medical,
    address, phone and email content -- which would have made most of what a
    person would call private exportable. If this test fails, do not delete
    it: decide about the new class.
    """
    import yaml

    from bartholomew_api_bridge_v0_1.services.api.routes.learning import (
        _NEVER_EXPORT_PRIVACY_CLASSES,
    )

    rules_path = pathlib.Path(__file__).resolve().parents[1] / "bartholomew" / "config"
    rules = yaml.safe_load((rules_path / "memory_rules.yaml").read_text(encoding="utf-8"))

    consent_gated = {
        entry["metadata"]["privacy_class"]
        for entry in rules.get("ask_before_store", [])
        if entry.get("metadata", {}).get("privacy_class")
    }
    assert consent_gated, "the shipped rules must define at least one consent-gated class"

    missing = consent_gated - _NEVER_EXPORT_PRIVACY_CLASSES
    assert not missing, (
        f"these consent-gated privacy classes are exportable: {sorted(missing)}. "
        "Add them to _NEVER_EXPORT_PRIVACY_CLASSES, or record why they may leave "
        "the runtime."
    )


def test_the_default_policy_excludes_the_same_classes_the_export_blocks():
    """
    One vocabulary, two places that must agree.

    A class the export refuses but the policy would happily auto-accept would
    be an incoherent stance about the same material.
    """
    from bartholomew_api_bridge_v0_1.services.api.routes.learning import (
        _NEVER_EXPORT_PRIVACY_CLASSES,
    )

    excluded = set(learning_policy.default_policy().excluded_privacy_classes)
    assert _NEVER_EXPORT_PRIVACY_CLASSES <= excluded, (
        "the default policy must exclude at least what the export refuses; "
        f"missing: {sorted(_NEVER_EXPORT_PRIVACY_CLASSES - excluded)}"
    )


def test_the_export_gate_allows_an_ordinary_classified_record():
    """The gate must not refuse everything -- that would be a different bug."""
    from bartholomew_api_bridge_v0_1.services.api.routes.learning import (
        _export_blocked_reason,
    )

    assert (
        _export_blocked_reason(
            {"readable": True, "governance_known": True, "privacy_class": "user.identity"},
        )
        is None
    )


def test_never_store_material_has_no_record_to_export(client):
    """
    Acceptance requirement 21, at the other end.

    `never_store` is enforced at the write, so there is nothing for an export
    to find -- and the export says "no such record" rather than inventing one.
    """
    stored = _seed_memory(
        "fact",
        "learning_export_never_store_probe",
        "instructions for obtaining illegal content",
    )
    assert stored is False

    response = client.post(
        "/api/learning/export",
        json={"records": [{"kind": "fact", "key": "learning_export_never_store_probe"}]},
    )
    payload = response.json()
    assert payload["exported_count"] == 0
    assert payload["skipped"][0]["reason"] == "no such record"


def test_export_refuses_acceptance_approvals(client, candidate):
    """
    Internal approval material is not disclosed by the export.

    What an approval authorised is visible on the candidate itself; the set of
    approvals is governance material and a different disclosure.
    """
    competency_id, slug = candidate
    detail = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/approve",
        json={"approver": REVIEWER, "expected_revision": detail["revision"]},
    )
    key = candidate_learning.key_for(competency_id, slug)

    response = client.post(
        "/api/learning/export",
        json={"records": [{"kind": learning_authorization.KIND, "key": key}]},
    )
    payload = response.json()
    assert payload["exported_count"] == 0
    assert "not exportable" in payload["skipped"][0]["reason"]


def test_export_reports_a_record_it_could_not_find(client):
    response = client.post(
        "/api/learning/export",
        json={"records": [{"kind": candidate_learning.KIND, "key": "nothing.here"}]},
    )
    payload = response.json()
    assert payload["exported_count"] == 0
    assert payload["skipped"] == [
        {"kind": candidate_learning.KIND, "key": "nothing.here", "reason": "no such record"},
    ]


# ===========================================================================
# Revocation
# ===========================================================================


def test_revocation_requires_confirmation_and_removes_retrieval_eligibility(client):
    """Acceptance requirement 20, over HTTP."""
    competency_id, slug = _seed_candidate(title="An objective to accept then revoke")
    detail = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/approve",
        json={"approver": REVIEWER, "expected_revision": detail["revision"]},
    )
    accepted = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/accept",
        json={"reviewer": REVIEWER},
    ).json()
    kind, key = accepted["consolidated_kind"], accepted["consolidated_key"]

    unconfirmed = client.post(
        f"/api/learning/competencies/{kind}/{key}/revoke",
        json={"revoked_by": REVIEWER},
    )
    assert unconfirmed.status_code == 400
    assert "confirm=true" in unconfirmed.json()["detail"]

    confirmed = client.post(
        f"/api/learning/competencies/{kind}/{key}/revoke",
        params={"confirm": True},
        json={"revoked_by": REVIEWER, "reason": "The warranty expired"},
    )
    assert confirmed.status_code == 200
    assert "will not recall this again" in confirmed.json()["detail"]

    competencies = client.get("/api/learning/competencies").json()["competencies"]
    assert not any(c["key"] == key and c["kind"] == kind for c in competencies)

    # The audit survives: the candidate still records that it was accepted.
    after = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    assert after["review_state"] == "accepted"
    assert after["consolidated_key"] == key


def test_a_correction_supersedes_and_a_stale_one_conflicts(client):
    """Acceptance requirements 19 and 20, over HTTP."""
    competency_id, slug = _seed_candidate(title="An objective to accept then correct")
    detail = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/approve",
        json={"approver": REVIEWER, "expected_revision": detail["revision"]},
    )
    accepted = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/accept",
        json={"reviewer": REVIEWER},
    ).json()
    kind, key = accepted["consolidated_kind"], accepted["consolidated_key"]

    stored = next(
        c
        for c in client.get("/api/learning/competencies").json()["competencies"]
        if c["key"] == key and c["kind"] == kind
    )
    stale_revision = stored["revision"]

    first = client.post(
        f"/api/learning/competencies/{kind}/{key}/correct",
        json={
            "corrected_by": REVIEWER,
            "expected_revision": stale_revision,
            "updates": {"rule": "Corrected once"},
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["summary"]["stored"] == 1

    second = client.post(
        f"/api/learning/competencies/{kind}/{key}/correct",
        json={
            "corrected_by": REVIEWER,
            "expected_revision": stale_revision,
            "updates": {"rule": "Corrected from a stale screen"},
        },
    )
    assert second.status_code == 409
    assert "changed since you opened it" in second.json()["detail"]["detail"]

    live = next(
        c
        for c in client.get("/api/learning/competencies").json()["competencies"]
        if c["key"] == key and c["kind"] == kind
    )
    assert live["rule"] == "Corrected once"
    assert live["revision"] == stale_revision + 1
