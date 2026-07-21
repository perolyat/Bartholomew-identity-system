"""
HTTP-level tests for bartholomew_api_bridge_v0_1's self_state router
(/self, /self/goals, /persona/*, /self/drives, /self/attention).

This router had no dedicated HTTP-level test file at all -- everything
touching it elsewhere (tests/test_runtime_contract_chat_seam.py,
tests/test_scenario_replay.py) calls daemon.experience.add_goal() etc.
directly, never through the actual routes. Writing these found a real bug:
POST /self/goals returned "added": null unconditionally, because
ExperienceKernel.add_goal() had no return statement (always None) and the
route did `added = kernel.experience.add_goal(goal)` without checking
anything else -- fixed by giving add_goal() a real bool return (mirroring
complete_goal(), which already had one).
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


@pytest.fixture(scope="module")
def client():
    with TestClient(app_module.app) as c:
        yield c


class TestSelfSnapshot:
    def test_get_self_returns_active_persona_and_goals(self, client):
        response = client.get("/api/self")

        assert response.status_code == 200
        body = response.json()
        assert "active_persona" in body
        assert body["active_persona"] == app_module._kernel.persona_manager.get_active_pack_id()


class TestGoalsEndpoint:
    """
    app_module (and its module-level `_kernel`) is a process-wide singleton
    -- other test files (tests/test_api_chat_runtime_contract.py) import
    and manipulate the very same instance, and a chat prompt echoes
    whatever's in Working Memory/active goals. Every goal these tests add
    must be cleaned up, or it leaks into other test modules' assertions
    when the whole suite runs in one process (confirmed: this class
    originally broke test_chat_references_persisted_experience_kernel_state
    by leaving goals behind).
    """

    _TEST_GOALS = [
        "draft the quarterly summary",
        "a goal added twice",
        "visible via GET",
        "a goal to complete",
    ]

    @pytest.fixture(autouse=True)
    def _cleanup_goals(self, client):
        yield
        for goal in self._TEST_GOALS:
            client.delete(f"/api/self/goals/{goal}")

    def test_post_goal_reports_added_true_for_a_new_goal(self, client):
        response = client.post("/api/self/goals", params={"goal": "draft the quarterly summary"})

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["added"] is True
        assert "draft the quarterly summary" in body["goals"]

    def test_post_goal_reports_added_false_for_a_duplicate(self, client):
        client.post("/api/self/goals", params={"goal": "a goal added twice"})

        response = client.post("/api/self/goals", params={"goal": "a goal added twice"})

        assert response.status_code == 200
        assert response.json()["added"] is False

    def test_get_goals_reflects_what_was_posted(self, client):
        client.post("/api/self/goals", params={"goal": "visible via GET"})

        response = client.get("/api/self/goals")

        assert response.status_code == 200
        assert "visible via GET" in response.json()["goals"]

    def test_delete_goal_reports_completed_true_then_false(self, client):
        client.post("/api/self/goals", params={"goal": "a goal to complete"})

        first = client.delete("/api/self/goals/a goal to complete")
        assert first.status_code == 200
        assert first.json()["completed"] is True

        second = client.delete("/api/self/goals/a goal to complete")
        assert second.status_code == 200
        assert second.json()["completed"] is False


class TestPersonaEndpoints:
    def test_list_personas_includes_the_active_one(self, client):
        response = client.get("/api/persona/list")

        assert response.status_code == 200
        body = response.json()
        active_id = app_module._kernel.persona_manager.get_active_pack_id()
        assert any(p["pack_id"] == active_id for p in body["packs"])

    def test_switch_persona_is_reflected_by_current(self, client):
        # Restore whatever was active before this test -- app_module._kernel
        # is a process-wide singleton other test files also assert against.
        original = app_module._kernel.persona_manager.get_active_pack_id()
        try:
            packs = app_module._kernel.persona_manager.list_packs()
            other_pack = next(p for p in packs if p != original)

            response = client.post("/api/persona/switch", json={"pack_id": other_pack})
            assert response.status_code == 200

            current_response = client.get("/api/persona/current")
            assert current_response.json()["pack"]["pack_id"] == other_pack
        finally:
            client.post("/api/persona/switch", json={"pack_id": original})


class TestDrivesAndAttention:
    """
    Every route in this class raised a live 500 (TypeError/AttributeError)
    before this session's fixes -- none of them had ever been called
    through a real HTTP request in a test before. Each kernel call had
    drifted from its real ExperienceKernel/NarratorEngine signature:
    set_attention()'s attention_type/context_tags kwargs (should be
    focus_type/tags), get_top_drives()'s limit kwarg (should be n),
    activate_drive()'s amount kwarg (should be boost), satisfy_drive()
    being passed an amount it never accepted at all, and
    get_episodes_by_type() receiving a raw string where an EpisodeType
    enum was required.
    """

    def test_get_drives_returns_a_list(self, client):
        response = client.get("/api/self/drives")

        assert response.status_code == 200
        assert "drives" in response.json()

    def test_get_top_drives_returns_a_list(self, client):
        response = client.get("/api/self/drives/top", params={"limit": 2})

        assert response.status_code == 200
        assert len(response.json()["drives"]) <= 2

    def test_activate_and_satisfy_a_real_drive(self, client):
        # ExperienceKernel.__init__() is constructed by daemon.py with no
        # identity_path, so it always falls back to its own
        # DEFAULT_DRIVES list -- config/drives.yaml never reaches it (a
        # separate, pre-existing gap noted but not fixed here; out of
        # scope for this route-signature fix).
        drive_id = "protect_user_wellbeing"
        activate_response = client.post(
            f"/api/self/drives/{drive_id}/activate",
            params={"amount": 0.3},
        )
        assert activate_response.status_code == 200
        assert activate_response.json()["ok"] is True

        satisfy_response = client.post(f"/api/self/drives/{drive_id}/satisfy")
        assert satisfy_response.status_code == 200
        assert satisfy_response.json()["ok"] is True

    def test_activate_unknown_drive_returns_404_not_500(self, client):
        response = client.post("/api/self/drives/not-a-real-drive/activate")
        assert response.status_code == 404

    def test_set_and_clear_attention(self, client):
        set_response = client.put(
            "/api/self/attention",
            json={"target": "release notes", "attention_type": "task"},
        )
        assert set_response.status_code == 200
        assert set_response.json()["attention"]["focus_target"] == "release notes"

        clear_response = client.delete("/api/self/attention")
        assert clear_response.status_code == 200
        assert clear_response.json()["attention"]["focus_target"] is None


class TestEpisodesByType:
    def test_by_type_accepts_a_valid_episode_type(self, client):
        response = client.get("/api/episodes/by-type/goal_added")

        assert response.status_code == 200
        body = response.json()
        assert body["type"] == "goal_added"
        assert "episodes" in body

    def test_by_type_rejects_an_unknown_type_with_400_not_500(self, client):
        response = client.get("/api/episodes/by-type/not_a_real_type")

        assert response.status_code == 400
