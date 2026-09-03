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
        # Reading personal memories through this surface is the same power
        # `GET /api/memory` grants, so it takes the same capability rather
        # than letting learning:read become a way around memory:read.
        "memory:read",
    }
    assert ROUTE_CAPABILITIES[("GET", "/api/learning/memories")].value == "memory:read"


def test_only_the_operations_that_change_what_he_knows_need_learning_approve():
    """
    The split is the architecture, not the screen layout.

    Three acts can change what Bartholomew actually recalls: granting a
    candidate-bound approval, consolidating, and correcting a record the
    retrieval seam already serves. Everything else is review or reading, and a
    delegated reviewer who may triage a queue must not thereby be able to
    rewrite accepted knowledge.

    Revoking is deliberately *not* here: like `learning_reject`, it can only
    reduce what he recalls, and the audit of what was once accepted survives
    it.
    """
    approve_routes = {
        path
        for (method, path), capability in ROUTE_CAPABILITIES.items()
        if capability.value == "learning:approve"
    }
    assert approve_routes == {
        "/api/learning/candidates/{competency_id}/{slug}/approve",
        "/api/learning/candidates/{competency_id}/{slug}/accept",
        "/api/learning/competencies/{kind}/{key}/correct",
    }
    assert (
        ROUTE_CAPABILITIES[("POST", "/api/learning/competencies/{kind}/{key}/revoke")].value
        == "learning:review"
    )


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
    """
    Acceptance requirement 2: a view must not present a row without its
    classification -- and must show the classification the memory authority
    actually assigned, not a placeholder.

    Asserting key *presence* would pass against a projection that hard-coded
    every one of them to None, which is exactly the failure this is meant to
    catch. So each value is checked against what the rules engine says about
    that row.
    """
    from bartholomew.kernel.memory_rules import MemoryRulesEngine

    competency_id, slug = candidate
    response = client.get("/api/learning/candidates")
    assert response.status_code == 200
    entries = response.json()["candidates"]
    entry = next(e for e in entries if e["slug"] == slug)

    expected = MemoryRulesEngine(watch_file=False).evaluate(
        {
            "kind": candidate_learning.KIND,
            "key": candidate_learning.key_for(competency_id, slug),
            "value": "{}",
        },
    )
    assert entry["privacy_class"] == expected["privacy_class"] == "user.competency"
    assert entry["recall_policy"] == expected["recall_policy"] == "context_only"
    assert entry["governance_known"] is True
    assert entry["readable"] is True
    assert entry["retention"], "retention must be described, not just classified"

    # And the candidate's own epistemic fields, which the review depends on.
    assert entry["classification"] == "personal"
    assert entry["epistemic_status"] == "inference"
    assert entry["confidence"] == candidate_learning.SINGLE_EXPERIENCE_CONFIDENCE
    assert entry["review_state"] == "proposed"


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
    """
    Acceptance requirements 19 and 24, on the policy surface.

    A policy is saved first, deliberately. An earlier version computed the
    stale revision as `max(0, revision - 1)`, which on a database where nothing
    had been saved is 0 -- the *correct* revision for an unconfigured runtime,
    so the request would have succeeded. It only passed because another test
    happened to run first and leave a revision behind, which is a test that
    passes for a reason unrelated to what it claims.
    """
    baseline = client.get("/api/learning/policy").json()["policy"]
    saved = client.put(
        "/api/learning/policy",
        json={
            "expected_revision": baseline["revision"],
            "updated_by": "first-tab",
            "enabled_categories": [],
            "excluded_privacy_classes": [],
        },
    )
    assert saved.status_code == 200, saved.text
    current = saved.json()["policy"]
    assert current["revision"] > baseline["revision"]

    response = client.put(
        "/api/learning/policy",
        json={
            "expected_revision": baseline["revision"],
            "updated_by": "second-tab",
            "enabled_categories": [],
            "excluded_privacy_classes": [],
        },
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["outcome"] == "revision_conflict"
    assert detail["stored_policy"]["revision"] == current["revision"]
    assert detail["your_expected_revision"] == baseline["revision"]

    unchanged = client.get("/api/learning/policy").json()["policy"]
    assert unchanged["revision"] == current["revision"]
    assert unchanged["updated_by"] == "first-tab"


def test_omitting_a_policy_field_does_not_drop_its_conservative_default(client):
    """
    A request body is where defaults get weakened by accident.

    An earlier version restated the engine's defaults in the Pydantic model and
    got three wrong, so a client that omitted `excluded_privacy_classes`
    silently saved a policy with no privacy exclusions at all. The body now
    derives them, and this pins that it still does.
    """
    from bartholomew_api_bridge_v0_1.services.api.routes.learning import PolicyUpdate

    engine_default = learning_policy.default_policy()
    body = PolicyUpdate(expected_revision=0, updated_by="you")

    assert body.excluded_privacy_classes == engine_default.excluded_privacy_classes
    assert body.excluded_classifications == engine_default.excluded_classifications
    assert body.expires_after_days == engine_default.expires_after_days
    assert body.review_interval_days == engine_default.review_interval_days
    assert body.min_confidence == engine_default.min_confidence
    assert body.min_supporting_experiences == engine_default.min_supporting_experiences
    assert body.max_risk == engine_default.max_risk
    assert body.require_reversible == engine_default.require_reversible
    assert body.exclude_sharing_eligible == engine_default.exclude_sharing_eligible


def test_excluded_categories_survive_a_save(client):
    """
    The defect this pins on the API side: the form sent an empty list.

    Whatever the UI does, the endpoint must round-trip the field.
    """
    current = client.get("/api/learning/policy").json()["policy"]
    response = client.put(
        "/api/learning/policy",
        json={
            "expected_revision": current["revision"],
            "updated_by": "you",
            "enabled_categories": [],
            "excluded_categories": ["procedural"],
            "excluded_privacy_classes": [],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["policy"]["excluded_categories"] == ["procedural"]
    assert client.get("/api/learning/policy").json()["policy"]["excluded_categories"] == [
        "procedural",
    ]


def test_a_correction_refused_by_governance_is_not_reported_as_success(client):
    """
    A halted system is not "waiting on consent".

    The training seam reports a Parking Brake refusal as
    `governance_allowed=False` with a per-record outcome and *no* error, so a
    route that raised only on `errors` returned HTTP 200 with a detail saying
    the correction was waiting for the user. It was refused, and the user
    would have gone looking in an inbox that never receives it.
    """
    from bartholomew.kernel import training as training_module

    class _Blocked:
        competency_id = "c"
        governance_allowed = False
        governance_reason = "parking brake engaged for scope 'training'"
        errors: list[str] = []
        outcomes = [
            training_module.TrainingRecordOutcome(
                kind="competency_heuristic",
                key="c.s",
                outcome=training_module.OUTCOME_BLOCKED_BY_GOVERNANCE,
                detail="parking brake engaged for scope 'training'",
            ),
        ]
        stored_count = 0

        def to_dict(self):
            return {"competency_id": self.competency_id}

    from bartholomew_api_bridge_v0_1.services.api.routes import learning as learning_routes

    async def _blocked(*args, **kwargs):
        return _Blocked()

    original = learning_routes.run_competency_correction_through_runtime_contract
    learning_routes.run_competency_correction_through_runtime_contract = _blocked
    try:
        response = client.post(
            "/api/learning/competencies/competency_heuristic/c.s/correct",
            json={"corrected_by": REVIEWER, "expected_revision": 1, "updates": {"rule": "x"}},
        )
    finally:
        learning_routes.run_competency_correction_through_runtime_contract = original

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["outcome"] == "blocked_by_governance"
    assert "parking brake" in detail["detail"]


def test_preferences_are_a_separate_area_from_other_memories(client):
    """
    Two required areas, one store.

    `personal_facts` writes a preference as a `user_profile` row keyed
    `preference.<slug>`, so the split comes from the key convention the writer
    already uses rather than a second classification invented here.
    """
    assert _seed_memory("user_profile", "preference.tea", "prefers tea to coffee")
    assert _seed_memory("user_profile", "given_name", "Taylor")

    preferences = client.get("/api/learning/memories", params={"area": "preferences"}).json()
    keys = {m["key"] for m in preferences["memories"]}
    assert "preference.tea" in keys
    assert "given_name" not in keys
    assert all(m["area"] == "preference" for m in preferences["memories"])

    facts = client.get("/api/learning/memories", params={"area": "facts"}).json()
    fact_keys = {m["key"] for m in facts["memories"]}
    assert "given_name" in fact_keys
    assert "preference.tea" not in fact_keys


def test_memories_carry_their_retention_and_exportability(client):
    """
    "Privacy and retention classifications" is a required area, and a record
    the export would refuse says so before anyone ticks it.
    """
    assert _seed_memory("user_profile", "retention_probe", "Taylor")
    body = client.get("/api/learning/memories", params={"search": "retention_probe"}).json()
    entry = next(m for m in body["memories"] if m["key"] == "retention_probe")

    assert entry["privacy_class"] == "user.identity"
    assert entry["recall_policy"] == "always"
    assert entry["always_keep"] is True
    assert "always bring this up" in entry["retention"]
    assert "not set to expire" in entry["retention"]
    assert entry["exportable"] is True
    assert entry["export_blocked_reason"] is None
    assert entry["last_updated"]


def test_a_correction_keeps_what_the_record_used_to_say(client):
    """
    Acceptance requirement 20, on the half that was missing.

    S5.2's training seam records *that* a supersession happened, not what the
    superseded record said -- a correction is an in-place upsert. Without
    archiving the prior record, "what did he believe before I corrected this?"
    is unanswerable, and the contract names superseded and corrected knowledge
    as something a person must be able to see.
    """
    competency_id, slug = _seed_candidate(title="An objective to accept then correct twice")
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

    before = next(
        c
        for c in client.get("/api/learning/competencies").json()["competencies"]
        if c["key"] == key and c["kind"] == kind
    )
    original_rule = before["rule"]

    corrected = client.post(
        f"/api/learning/competencies/{kind}/{key}/correct",
        json={
            "corrected_by": REVIEWER,
            "expected_revision": before["revision"],
            "updates": {"rule": "The corrected wording"},
        },
    )
    assert corrected.status_code == 200, corrected.text

    superseded = client.get("/api/learning/superseded").json()["superseded"]
    archived = next(x for x in superseded if x["supersedes"] == key)
    assert archived["text"] == original_rule
    assert archived["revision"] == before["revision"]
    assert archived["retrievable"] is False
    assert "before it was corrected" in archived["what_it_is"]

    # And the superseded belief is not retrievable as a current one.
    live = client.get("/api/learning/competencies").json()["competencies"]
    assert all(c["kind"] != learning_policy.COMPETENCY_REVISION_KIND for c in live)


def test_an_edited_candidate_keeps_what_it_used_to_say(client, candidate):
    """The same guarantee on the candidate side, through the superseded view."""
    competency_id, slug = candidate
    detail = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    original = detail["rule"]

    client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/edit",
        json={
            "expected_revision": detail["revision"],
            "editor": REVIEWER,
            "inferred_rule": "Rewritten",
        },
    )
    superseded = client.get("/api/learning/superseded").json()["superseded"]
    archived = next(x for x in superseded if x["supersedes"] == detail["key"])
    assert archived["text"] == original
    assert archived["retrievable"] is False
    assert "before it was edited" in archived["what_it_is"]


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

    response = client.post(
        "/api/learning/export",
        json={"records": [{"kind": "fact", "key": key}]},
    )
    assert response.status_code == 200
    export = response.json()
    # Whatever the gate decides about this row, the number is not in the file.
    assert "12345678" not in json.dumps(export)
    assert export["selection_size"] == 1
    assert export["exported_count"] + len(export["skipped"]) == 1
    if export["exported_count"]:
        exported_value = json.dumps(export["records"][0])
        assert "12345678" not in exported_value
        assert "*" in exported_value, "the stored value must be the masked one"


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


def test_the_export_gate_refuses_a_real_restricted_row(client, tmp_path, monkeypatch):
    """
    The privacy gate, on the real data path rather than a hand-built dict.

    The shipped rules redact every consent-gated class before storing it, so a
    restricted class never survives to be read back -- which is why the
    parametrised test above has to call the gate directly. That leaves the
    branch unproven end to end, and "unreachable with these rules" is not the
    same as "works when reached": `memory_rules.yaml` is user-editable, and a
    deployment that classifies without redacting is exactly the case the gate
    exists for.

    So this test *is* that deployment. It points the one rules engine at a
    configuration that assigns `user.health` to a kind and does not redact it,
    stores a real record through the real store, reads it back through the real
    API, and requires the export to refuse it.
    """
    import yaml

    from bartholomew.kernel import memory_rules as memory_rules_module
    from bartholomew.kernel import memory_store as memory_store_module
    from bartholomew.kernel.memory_rules import MemoryRulesEngine

    rules_path = tmp_path / "memory_rules.yaml"
    rules_path.write_text(
        yaml.safe_dump(
            {
                "always_keep": [
                    {
                        "match": {"kind": "health_probe_kind"},
                        "metadata": {"privacy_class": "user.health", "recall_policy": "always"},
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    engine = MemoryRulesEngine(config_path=str(rules_path), watch_file=False)
    # Both module globals, deliberately. `memory_store` holds a by-value
    # reference to the singleton, and `MemoryRulesEngine.evaluate()` delegates
    # to `memory_rules._rules_engine` whenever `self` is not it -- so patching
    # only one of the two leaves the real shipped rules in force and the test
    # silently proves nothing.
    monkeypatch.setattr(memory_rules_module, "_rules_engine", engine)
    monkeypatch.setattr(memory_store_module, "_rules_engine", engine)
    assert (
        engine.evaluate({"kind": "health_probe_kind", "key": "k", "value": "v"})["privacy_class"]
        == "user.health"
    ), "the test rules must actually be in force"

    key = "export_gate_real_row"
    assert _seed_memory(
        "health_probe_kind",
        key,
        "a plainly stored sentence",
    ), "the probe must store, or the gate is not what is being tested"

    listed = client.get("/api/memory", params={"kind": "health_probe_kind"}).json()
    row = next(item for item in listed["entries"] if item["key"] == key)
    assert row["privacy_class"] == "user.health"
    assert row["value"] == "a plainly stored sentence", "this row is deliberately not redacted"

    export = client.post(
        "/api/learning/export",
        json={"records": [{"kind": "health_probe_kind", "key": key}]},
    ).json()
    assert export["exported_count"] == 0
    assert export["records"] == []
    assert "user.health" in export["skipped"][0]["reason"]
    assert "a plainly stored sentence" not in json.dumps(export)


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


def test_the_approvals_evaluations_and_history_endpoints_answer(client):
    """
    The four read endpoints nothing else exercised.

    `/approvals` in particular: it is a required area, it sits at the lowest
    capability, and it returns approver identities and notes -- so it needs a
    test that it answers and that it reports validity through the same
    authority acceptance uses, rather than being assumed to work because the
    UI calls it.
    """
    competency_id, slug = _seed_candidate(title="An objective for the read endpoints")
    detail = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/approve",
        json={"approver": REVIEWER, "expected_revision": detail["revision"]},
    )
    client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/shadow-evaluate",
        json={},
    )

    approvals = client.get("/api/learning/approvals")
    assert approvals.status_code == 200
    entry = next(a for a in approvals.json()["approvals"] if a["candidate_key"] == detail["key"])
    assert entry["approver"] == REVIEWER
    assert entry["valid_for_current_revision"] is True
    assert entry["candidate_review_state"] == "proposed"

    evaluations = client.get("/api/learning/evaluations")
    assert evaluations.status_code == 200
    body = evaluations.json()
    assert body["shadow_mode"]["automatic_acceptance_enabled"] is False
    assert any(e["candidate_key"] == detail["key"] for e in body["evaluations"])

    superseded = client.get("/api/learning/superseded")
    assert superseded.status_code == 200
    assert "superseded" in superseded.json()

    history = client.get("/api/learning/policy/history")
    assert history.status_code == 200
    assert history.json()["shadow_mode"]["execution_mode"] == "shadow"
    # Only archives, never the live policy row.
    assert all(
        h.get("revision") is not None for h in history.json()["history"]
    ), "the history must not include the live policy row"


def test_an_approval_stops_being_offered_the_moment_it_stops_authorising(client):
    """
    A control that offers an action the system will refuse is worse than none.

    `can_accept_now` and `valid_for_current_revision` come from
    `LearningAcceptanceApproval.authorizes()` -- the same function acceptance
    calls -- rather than a fingerprint comparison of their own. Comparing
    fingerprints drifted the moment approvals also bound to the revision:
    after an edit-and-revert the digests matched, so the button lit up and
    acceptance refused.
    """
    competency_id, slug = _seed_candidate(title="An objective to edit and revert")
    detail = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    original_rule = detail["rule"]

    client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/approve",
        json={"approver": REVIEWER, "expected_revision": detail["revision"]},
    )
    away = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/edit",
        json={
            "expected_revision": detail["revision"],
            "editor": REVIEWER,
            "inferred_rule": "Something else entirely",
        },
    ).json()
    back = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/edit",
        json={
            "expected_revision": away["revision"],
            "editor": REVIEWER,
            "inferred_rule": original_rule,
        },
    ).json()
    assert (
        back["fingerprint_after"] == away["fingerprint_before"]
    ), "the digest must be back to what was approved, or this proves nothing"

    reloaded = client.get(f"/api/learning/candidates/{competency_id}/{slug}").json()["candidate"]
    assert reloaded["approval"]["valid_for_current_revision"] is False
    assert reloaded["can_accept_now"] is False
    assert "edited since" in reloaded["approval"]["detail"]

    listed = client.get("/api/learning/approvals").json()["approvals"]
    listed_entry = next(a for a in listed if a["candidate_key"] == detail["key"])
    assert listed_entry["valid_for_current_revision"] is False

    refused = client.post(
        f"/api/learning/candidates/{competency_id}/{slug}/accept",
        json={"reviewer": REVIEWER},
    )
    assert refused.status_code == 403
    assert refused.json()["detail"]["outcome"] == "acceptance_approval_required"


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
