"""
HTTP-level tests for bartholomew_api_bridge_v0_1's onboarding router
(/api/onboarding/deployment-guide) -- Stage 1, S1.6. See
docs/S1_6_HOST_DEVICE_ONBOARDING_DESIGN.md (approved 2026-08-05) for the
design this verifies: no target is presented as the only supported option
(Sec 7), the choosing-guide table maps five distinct priorities to five
distinct targets rather than issuing one verdict, and the "this choice is
not permanent" migration note is present (Sec 4/9).

Follows the same pattern as tests/test_awaiting_response_api.py: a
module-scoped TestClient over the real app.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

_db_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
_db_dir.mkdir(parents=True, exist_ok=True)
os.environ["BARTH_DB_PATH"] = str(_db_dir / "test.db")

from bartholomew_api_bridge_v0_1.services.api import app as app_module  # noqa: E402
from bartholomew_api_bridge_v0_1.services.api.onboarding_content import (  # noqa: E402
    CHOOSING_GUIDE,
    DEPLOYMENT_TARGETS,
    MIGRATION_NOTE,
)

EXPECTED_TARGET_IDS = {
    "phone",
    "personal_computer",
    "home_server_hub",
    "hosted_cloud_service",
    "hybrid_local_plus_cloud",
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app_module.app) as c:
        yield c


def test_content_module_has_five_targets_matching_roadmap_list():
    assert len(DEPLOYMENT_TARGETS) == 5
    assert {t.target_id for t in DEPLOYMENT_TARGETS} == EXPECTED_TARGET_IDS


def test_content_module_choosing_guide_covers_five_distinct_priorities_and_targets():
    """Design doc Sec 7's neutrality mechanism: priority-conditional, not a
    single verdict -- five distinct priorities, mapped to five distinct
    targets, not one target repeated."""
    assert len(CHOOSING_GUIDE) == 5
    priorities = {g.priority for g in CHOOSING_GUIDE}
    target_ids = {g.target_id for g in CHOOSING_GUIDE}
    assert len(priorities) == 5
    assert target_ids == EXPECTED_TARGET_IDS


def test_migration_note_states_choice_is_not_permanent():
    assert "not permanent" in MIGRATION_NOTE.lower()


def test_deployment_guide_endpoint_returns_all_five_targets(client):
    response = client.get("/api/onboarding/deployment-guide")
    assert response.status_code == 200
    body = response.json()

    assert {t["target_id"] for t in body["targets"]} == EXPECTED_TARGET_IDS


@pytest.mark.parametrize("field_name", ["target_id", "name", "feel", "upgrade_path"])
def test_every_target_has_non_empty_string_fields(client, field_name):
    body = client.get("/api/onboarding/deployment-guide").json()
    for target in body["targets"]:
        assert isinstance(target[field_name], str)
        assert target[field_name].strip() != ""


@pytest.mark.parametrize("field_name", ["advantages", "limitations"])
def test_every_target_has_non_empty_list_fields(client, field_name):
    body = client.get("/api/onboarding/deployment-guide").json()
    for target in body["targets"]:
        assert isinstance(target[field_name], list)
        assert len(target[field_name]) > 0
        assert all(isinstance(item, str) and item.strip() for item in target[field_name])


def test_every_target_has_a_boolean_available_today_flag(client):
    body = client.get("/api/onboarding/deployment-guide").json()
    for target in body["targets"]:
        assert isinstance(target["available_today"], bool)


def test_hosted_cloud_service_is_flagged_not_available_today(client):
    """Design doc Sec 2/3: no first-party hosted runtime exists -- this is
    the one target that must not claim to be available today."""
    body = client.get("/api/onboarding/deployment-guide").json()
    hosted = next(t for t in body["targets"] if t["target_id"] == "hosted_cloud_service")
    assert hosted["available_today"] is False


def test_choosing_guide_returned_with_five_distinct_priorities_and_targets(client):
    body = client.get("/api/onboarding/deployment-guide").json()
    guide = body["choosing_guide"]
    assert len(guide) == 5
    assert len({g["priority"] for g in guide}) == 5
    assert {g["target_id"] for g in guide} == EXPECTED_TARGET_IDS
    for entry in guide:
        assert entry["rationale"].strip() != ""


def test_migration_note_present_and_prominent_in_response(client):
    body = client.get("/api/onboarding/deployment-guide").json()
    assert "not permanent" in body["migration_note"].lower()


def test_choosing_guide_target_ids_reference_real_targets(client):
    """Every choosing-guide row must point at a target that's actually in
    the response -- no dangling reference."""
    body = client.get("/api/onboarding/deployment-guide").json()
    target_ids = {t["target_id"] for t in body["targets"]}
    for entry in body["choosing_guide"]:
        assert entry["target_id"] in target_ids


def test_no_target_copy_uses_recommended_language(client):
    """Design doc Sec 7's neutrality requirement: no call-to-action or
    "recommended" language anywhere in the per-target content."""
    body = client.get("/api/onboarding/deployment-guide").json()
    banned_terms = ["recommended", "best choice", "get started with"]
    for target in body["targets"]:
        combined = " ".join(
            [target["feel"], target["upgrade_path"], *target["advantages"], *target["limitations"]],
        ).lower()
        for term in banned_terms:
            assert term not in combined, f"{target['target_id']!r} copy contains {term!r}"


def test_deployment_guide_reachable_without_auth_headers(client):
    """This bridge has no auth on any route today (INTERFACES.md); this
    route additionally needs no _kernel at all (see routes/onboarding.py's
    docstring) -- a plain unauthenticated GET must succeed."""
    response = client.get("/api/onboarding/deployment-guide")
    assert response.status_code == 200
