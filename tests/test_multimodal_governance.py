"""No capture without every gate, and unreadable governance fails closed.

Acceptance gates covered: 1 (no capture without resolved tenant, principal,
device, policy, brake and explicit consent), 2 (missing or unreadable
governance fails closed), 4 (the brake stops an active session within a
bounded interval), 16 (a model response or inbound event cannot start capture).
"""

from __future__ import annotations

import time

import pytest

from bartholomew.kernel.runtime_contract import (
    run_multimodal_session_through_runtime_contract,
)
from bartholomew.multimodal.devices import StaticCapabilityResolver
from bartholomew.multimodal.modality import (
    BRAKE_SCOPE,
    CAPABILITY_KIND,
    CaptureScope,
    Modality,
    ScopeKind,
)
from bartholomew.multimodal.runtime import (
    AutonomousStartRefusedError,
    SessionRequest,
    start_session,
)
from bartholomew.multimodal.session import SessionState
from bartholomew.multimodal.store import SessionStore


@pytest.fixture
def resolver():
    r = StaticCapabilityResolver()
    r.declare("device-1", list(CAPABILITY_KIND.values()))
    return r


def _request(modality=Modality.MICROPHONE, **kwargs):
    scope = (
        CaptureScope(ScopeKind.WINDOW, window_id="w1", window_title="Notes")
        if modality is Modality.SCREEN
        else None
    )
    return SessionRequest(
        tenant_id=kwargs.pop("tenant_id", "tenant-1"),
        principal_id=kwargs.pop("principal_id", "user:taylor"),
        device_id=kwargs.pop("device_id", "device-1"),
        modality=modality,
        correlation_id=kwargs.pop("correlation_id", "corr-1"),
        scope=kwargs.pop("scope", scope),
        **kwargs,
    )


def _seam(outcome="started", allowed=True, reason=None):
    """A controlled stand-in for the governed seam."""

    async def seam(modality, **kwargs):
        from bartholomew.kernel.runtime_contract import DeviceRuntimeResult

        seam.calls.append({"modality": modality, **kwargs})
        return DeviceRuntimeResult(
            observation=None,
            candidate_action=None,
            governance_allowed=allowed,
            started=False,
            outcome=outcome,
            reason=reason,
            result=None,
        )

    seam.calls = []
    return seam


class TestNoCaptureWithoutEveryGate:
    """Gate 1."""

    @pytest.mark.parametrize(
        "missing",
        ["tenant_id", "principal_id", "device_id", "correlation_id"],
    )
    def test_unresolved_binding_cannot_even_build_a_request(self, missing):
        with pytest.raises(ValueError, match=missing):
            _request(**{missing: ""})

    async def test_undeclared_device_capability_denies(self, resolver):
        """An unknown capability is unsupported, never approximated (§3.3)."""
        store = SessionStore()
        seam = _seam(outcome="capability_denied", allowed=False, reason="not declared")
        result = await start_session(
            _request(device_id="unenrolled-device"),
            store=store,
            capability_resolver=resolver,
            seam=seam,
        )
        assert result.allowed is False
        assert result.session.state is SessionState.REFUSED
        assert seam.calls[0]["capability_supported"] is False
        assert "not enrolled" in (seam.calls[0]["capability_reason"] or "")

    async def test_absent_resolver_denies(self):
        """No resolver means no assumption that the device can do it."""
        store = SessionStore()
        seam = _seam(outcome="capability_denied", allowed=False)
        await start_session(
            _request(),
            store=store,
            capability_resolver=None,
            seam=seam,
        )
        assert seam.calls[0]["capability_supported"] is False

    async def test_exploding_resolver_denies(self, resolver):
        """Unreadable device state fails closed (gate 2)."""

        class Exploding:
            def resolve(self, *a, **k):
                raise RuntimeError("registry unreachable")

        store = SessionStore()
        seam = _seam(outcome="capability_denied", allowed=False)
        await start_session(
            _request(),
            store=store,
            capability_resolver=Exploding(),
            seam=seam,
        )
        assert seam.calls[0]["capability_supported"] is False
        assert "errored" in seam.calls[0]["capability_reason"]

    @pytest.mark.parametrize(
        "outcome",
        ["parking_brake_denied", "governance_denied", "consent_denied"],
    )
    async def test_any_denied_gate_refuses_the_session(self, resolver, outcome):
        store = SessionStore()
        result = await start_session(
            _request(),
            store=store,
            capability_resolver=resolver,
            seam=_seam(outcome=outcome, allowed=False, reason=outcome),
        )
        assert result.allowed is False
        assert result.session.state is SessionState.REFUSED
        assert store.live() == []

    async def test_consent_denial_is_recorded_on_the_session(self, resolver):
        result = await start_session(
            _request(),
            store=SessionStore(),
            capability_resolver=resolver,
            seam=_seam(outcome="consent_denied", allowed=False),
        )
        assert result.session.consent_decision is False


class TestFailClosed:
    """Gate 2, at the real seam rather than through a double."""

    async def test_unreadable_brake_denies(self, monkeypatch):
        import bartholomew.orchestrator.safety.governance_store as gs

        async def explode(*args, **kwargs):
            raise RuntimeError("governance database unreadable")

        monkeypatch.setattr(gs, "is_blocked_fail_closed_off_loop", explode)
        result = await run_multimodal_session_through_runtime_contract(
            "microphone",
            db_path=":memory:",
            capability_supported=True,
        )
        assert result.governance_allowed is False
        assert result.outcome == "parking_brake_denied"

    async def test_absent_consent_handler_denies(self, monkeypatch, tmp_path):
        """No consent handler registered means no capture."""
        import bartholomew.kernel.runtime_contract as rc

        monkeypatch.setattr(rc, "get_consent_handler", lambda: None)
        result = await run_multimodal_session_through_runtime_contract(
            "microphone",
            db_path=str(tmp_path / "b.db"),
            capability_supported=True,
        )
        assert result.governance_allowed is False
        assert result.outcome == "consent_denied"

    async def test_declined_consent_denies(self, monkeypatch, tmp_path):
        import bartholomew.kernel.runtime_contract as rc

        monkeypatch.setattr(rc, "get_consent_handler", lambda: (lambda prompt: False))
        result = await run_multimodal_session_through_runtime_contract(
            "microphone",
            db_path=str(tmp_path / "b.db"),
            capability_supported=True,
        )
        assert result.outcome == "consent_denied"

    async def test_undeclared_capability_denies_before_any_other_gate(self, tmp_path):
        """Capability is gate 1: a denial here needs no consent handler at all."""
        result = await run_multimodal_session_through_runtime_contract(
            "screen",
            db_path=str(tmp_path / "b.db"),
            capability_supported=False,
        )
        assert result.outcome == "capability_denied"

    async def test_unknown_modality_is_refused(self):
        with pytest.raises(ValueError, match="unknown multimodal modality"):
            await run_multimodal_session_through_runtime_contract("webcam")

    async def test_denials_are_recorded_through_the_existing_reflection_sink(
        self,
        monkeypatch,
        tmp_path,
    ):
        import bartholomew.kernel.runtime_contract as rc

        recorded = []

        async def capture(db_path, surface, kind, outcome, reason):
            recorded.append((surface, kind, outcome))
            from bartholomew.kernel.reflection import ReflectionWriteOutcome

            return ReflectionWriteOutcome()

        monkeypatch.setattr(rc, "_record_device_reflection", capture)
        monkeypatch.setattr(rc, "get_consent_handler", lambda: None)
        await run_multimodal_session_through_runtime_contract(
            "microphone",
            db_path=str(tmp_path / "b.db"),
            capability_supported=True,
        )
        assert recorded == [
            ("multimodal.microphone", "multimodal_microphone_session", "consent_denied"),
        ]


class TestBrakeStopsActiveSessions:
    """Gate 4: engaging the brake stops an active session promptly."""

    def test_brake_stops_a_live_session_within_a_bounded_interval(self):
        from tests.test_multimodal_session_state import _activate, _session

        store = SessionStore()
        session = _activate(_session(Modality.MICROPHONE))
        stopped_flags = []
        store.add(session, stopper=lambda: stopped_flags.append(True))

        began = time.monotonic()
        stopped = store.stop_all_for_brake("voice")
        elapsed = time.monotonic() - began

        assert stopped == [session.session_id]
        assert session.state is SessionState.STOPPED
        assert "parking brake" in session.outcome_reason
        assert stopped_flags == [True]
        assert elapsed < 2.0, "brake stop must be bounded"

    def test_global_brake_stops_every_modality(self):
        from tests.test_multimodal_session_state import _activate, _session

        store = SessionStore()
        sessions = [
            _activate(_session(m))
            for m in (Modality.MICROPHONE, Modality.SCREEN, Modality.SPOKEN_OUTPUT)
        ]
        for s in sessions:
            store.add(s)
        assert len(store.stop_all_for_brake("global")) == 3
        assert store.live() == []

    def test_scoped_brake_only_stops_its_own_scope(self):
        from tests.test_multimodal_session_state import _activate, _session

        store = SessionStore()
        microphone = _activate(_session(Modality.MICROPHONE))
        screen = _activate(_session(Modality.SCREEN))
        store.add(microphone)
        store.add(screen)

        assert store.stop_all_for_brake("sight") == [screen.session_id]
        assert microphone.state is SessionState.ACTIVE
        assert screen.state is SessionState.STOPPED

    def test_brake_scopes_are_ones_the_repository_already_registers(self):
        """No new brake scope: this package adds no governance authority."""
        from bartholomew_api_bridge_v0_1.services.api.routes.governance import (
            VALID_SCOPES,
        )

        for scope in BRAKE_SCOPE.values():
            assert scope in VALID_SCOPES


class TestContentCannotStartCapture:
    """Gate 16: a model response or inbound event cannot start capture."""

    @pytest.mark.parametrize(
        "principal",
        [
            "model:claude",
            "assistant:bartholomew",
            "event:inbound-123",
            "inbound:webhook",
            "companion:windows-pc",
            "system:scheduler",
            "MODEL:Claude",
            "  model:claude  ",
        ],
    )
    def test_non_human_principals_are_refused(self, principal):
        with pytest.raises(AutonomousStartRefusedError):
            _request(principal_id=principal)

    def test_a_human_principal_is_accepted(self):
        assert _request(principal_id="user:taylor").principal_id == "user:taylor"

    def test_the_api_exposes_no_start_endpoint(self):
        """Capture initiation is not reachable over the unauthenticated bridge."""
        from bartholomew_api_bridge_v0_1.services.api.routes import multimodal

        paths = {r.path for r in multimodal.router.routes}
        methods = {
            (r.path, method)
            for r in multimodal.router.routes
            for method in getattr(r, "methods", set())
        }
        assert not any("start" in p for p in paths)
        assert ("/api/multimodal/sessions", "POST") not in methods
        for path, method in methods:
            if method == "POST":
                assert "stop" in path, f"the only POST routes may be stops: {path}"
