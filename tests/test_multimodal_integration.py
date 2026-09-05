"""End-to-end through the real Runtime Contract, brake and database.

The other multimodal test modules use controlled doubles for the *seam* so
they can isolate one behaviour. This module deliberately does not: it runs the
real `run_multimodal_session_through_runtime_contract()` against a real
GovernanceStore-backed database, so the architectural claims -- that the
brake genuinely stops a session, that consent is genuinely required, that
Identity policy genuinely decides -- are proven against real paths rather than
against a mock's behaviour.

Hardware remains simulated: CI has no microphone, no display and no Windows
accessibility tree. That separation is recorded in the closeout.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel.runtime_contract import (
    run_multimodal_session_through_runtime_contract,
)
from bartholomew.multimodal.devices import StaticCapabilityResolver
from bartholomew.multimodal.microphone import (
    NullAudioBackend,
)
from bartholomew.multimodal.modality import CAPABILITY_KIND, Modality
from bartholomew.multimodal.runtime import SessionRequest, start_session
from bartholomew.multimodal.session import SessionState
from bartholomew.multimodal.status import status_snapshot
from bartholomew.multimodal.store import SessionStore

pytestmark = pytest.mark.integration


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "multimodal.db")


@pytest.fixture
def granting_consent(monkeypatch):
    """A consent handler that grants, so gates after it can be reached."""
    import bartholomew.kernel.runtime_contract as rc

    prompts: list[str] = []

    def handler(prompt):
        prompts.append(prompt)
        return True

    monkeypatch.setattr(rc, "get_consent_handler", lambda: handler)
    return prompts


@pytest.fixture
def resolver():
    r = StaticCapabilityResolver()
    r.declare("device-1", list(CAPABILITY_KIND.values()))
    return r


def _engage_brake(db_path: str, *scopes: str) -> None:
    """Engage the real Parking Brake through its real store."""
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    GovernanceStore(db_path).engage(*scopes, reason="multimodal test", actor="test")


class TestRealSeamGates:
    async def test_full_grant_path_reaches_started(self, db_path, granting_consent):
        result = await run_multimodal_session_through_runtime_contract(
            "microphone",
            db_path=db_path,
            capability_supported=True,
        )
        assert result.governance_allowed is True
        assert result.outcome == "started"
        assert result.started is False, "the seam authorises; it does not capture"
        assert len(granting_consent) == 1

    async def test_the_consent_prompt_names_one_modality(self, db_path, granting_consent):
        await run_multimodal_session_through_runtime_contract(
            "microphone",
            db_path=db_path,
            capability_supported=True,
        )
        prompt = granting_consent[0]
        assert "LISTEN" in prompt
        assert "does not permit" in prompt

    async def test_each_modality_prompts_separately(self, db_path, granting_consent):
        for modality in ("microphone", "screen", "spoken_output"):
            await run_multimodal_session_through_runtime_contract(
                modality,
                db_path=db_path,
                capability_supported=True,
            )
        assert len(granting_consent) == 3
        assert len(set(granting_consent)) == 3, "three distinct prompts"

    async def test_identity_policy_denies_an_unallowlisted_kind(
        self,
        db_path,
        granting_consent,
        monkeypatch,
    ):
        """Policy is consulted, and a denial there stops the session."""
        import bartholomew.kernel.runtime_contract as rc

        class Decision:
            allowed = False
            reason = "not in allowlist"

        monkeypatch.setattr(
            rc.policy_engine,
            "evaluate_tool_policy",
            lambda ctx, kind: Decision(),
        )
        result = await run_multimodal_session_through_runtime_contract(
            "screen",
            db_path=db_path,
            capability_supported=True,
            identity_context=object(),
        )
        assert result.governance_allowed is False
        assert result.outcome == "governance_denied"

    async def test_identity_policy_receives_the_modality_specific_kind(
        self,
        db_path,
        granting_consent,
        monkeypatch,
    ):
        import bartholomew.kernel.runtime_contract as rc

        seen = []

        class Decision:
            allowed = True
            reason = None

        monkeypatch.setattr(
            rc.policy_engine,
            "evaluate_tool_policy",
            lambda ctx, kind: seen.append(kind) or Decision(),
        )
        for modality in ("microphone", "screen", "spoken_output"):
            await run_multimodal_session_through_runtime_contract(
                modality,
                db_path=db_path,
                capability_supported=True,
                identity_context=object(),
            )
        assert seen == [
            "multimodal_microphone_session",
            "multimodal_screen_capture",
            "multimodal_spoken_output",
        ]

    async def test_the_real_allowlist_permits_all_three_kinds(self):
        """Against the shipped Identity.yaml, not a fixture."""
        from bartholomew.kernel import policy_engine
        from identity_interpreter.identity_context import build_identity_context
        from identity_interpreter.loader import load_identity

        context = build_identity_context(load_identity("Identity.yaml"))
        for kind in (
            "multimodal_microphone_session",
            "multimodal_screen_capture",
            "multimodal_spoken_output",
        ):
            assert policy_engine.evaluate_tool_policy(context, kind).allowed, kind


class TestRealBrake:
    async def test_engaged_voice_brake_denies_a_microphone_session(
        self,
        db_path,
        granting_consent,
    ):
        _engage_brake(db_path, "voice")
        result = await run_multimodal_session_through_runtime_contract(
            "microphone",
            db_path=db_path,
            capability_supported=True,
        )
        assert result.governance_allowed is False
        assert result.outcome == "parking_brake_denied"

    async def test_engaged_global_brake_denies_every_modality(
        self,
        db_path,
        granting_consent,
    ):
        _engage_brake(db_path, "global")
        for modality in ("microphone", "screen", "spoken_output"):
            result = await run_multimodal_session_through_runtime_contract(
                modality,
                db_path=db_path,
                capability_supported=True,
            )
            assert result.outcome == "parking_brake_denied", modality

    async def test_a_sight_brake_does_not_stop_speech(self, db_path, granting_consent):
        """Scoped brakes stay scoped: stopping the screen must not silence speech."""
        _engage_brake(db_path, "sight")
        screen = await run_multimodal_session_through_runtime_contract(
            "screen",
            db_path=db_path,
            capability_supported=True,
        )
        speech = await run_multimodal_session_through_runtime_contract(
            "spoken_output",
            db_path=db_path,
            capability_supported=True,
        )
        assert screen.outcome == "parking_brake_denied"
        assert speech.outcome == "started"


class TestFullSessionLifecycle:
    async def test_a_denied_session_never_reports_as_active(
        self,
        db_path,
        resolver,
        monkeypatch,
    ):
        import bartholomew.kernel.runtime_contract as rc

        monkeypatch.setattr(rc, "get_consent_handler", lambda: (lambda p: False))
        store = SessionStore()
        result = await start_session(
            SessionRequest(
                tenant_id="tenant-1",
                principal_id="user:taylor",
                device_id="device-1",
                modality=Modality.MICROPHONE,
                correlation_id="corr-1",
            ),
            store=store,
            capability_resolver=resolver,
            db_path=db_path,
        )
        assert result.allowed is False
        assert result.session.state is SessionState.REFUSED
        snapshot = status_snapshot(store, microphone_backend=NullAudioBackend())
        assert snapshot["listening"] is False

    async def test_an_approved_session_on_a_machine_without_a_microphone(
        self,
        db_path,
        resolver,
        granting_consent,
    ):
        """The user's own likely case: every gate passes, hardware does not exist."""
        store = SessionStore()
        result = await start_session(
            SessionRequest(
                tenant_id="tenant-1",
                principal_id="user:taylor",
                device_id="device-1",
                modality=Modality.MICROPHONE,
                correlation_id="corr-1",
            ),
            store=store,
            capability_resolver=resolver,
            db_path=db_path,
            microphone_backend=NullAudioBackend(),
        )
        assert result.allowed is True
        assert result.outcome == "unavailable"
        assert result.session.state is SessionState.UNAVAILABLE
        assert status_snapshot(store, microphone_backend=NullAudioBackend())["listening"] is False

    async def test_a_screen_session_becomes_active_and_is_stoppable(
        self,
        db_path,
        resolver,
        granting_consent,
    ):
        from bartholomew.multimodal.modality import CaptureScope, ScopeKind

        store = SessionStore()
        result = await start_session(
            SessionRequest(
                tenant_id="tenant-1",
                principal_id="user:taylor",
                device_id="device-1",
                modality=Modality.SCREEN,
                correlation_id="corr-1",
                scope=CaptureScope(ScopeKind.WINDOW, window_id="w1", window_title="Notes"),
            ),
            store=store,
            capability_resolver=resolver,
            db_path=db_path,
        )
        assert result.allowed is True
        assert result.session.state is SessionState.ACTIVE
        assert status_snapshot(store)["observing_screen"] is True

        assert store.stop(result.session.session_id) is True
        assert result.session.state is SessionState.STOPPED
        assert status_snapshot(store)["observing_screen"] is False

    async def test_a_live_session_is_stopped_by_the_brake_sweep(
        self,
        db_path,
        resolver,
        granting_consent,
    ):
        from bartholomew.multimodal.modality import CaptureScope, ScopeKind

        store = SessionStore()
        result = await start_session(
            SessionRequest(
                tenant_id="tenant-1",
                principal_id="user:taylor",
                device_id="device-1",
                modality=Modality.SCREEN,
                correlation_id="corr-1",
                scope=CaptureScope(ScopeKind.DISPLAY, display_id="1"),
            ),
            store=store,
            capability_resolver=resolver,
            db_path=db_path,
        )
        assert store.stop_all_for_brake("sight") == [result.session.session_id]
        assert result.session.state is SessionState.STOPPED
        assert "parking brake" in result.session.outcome_reason
