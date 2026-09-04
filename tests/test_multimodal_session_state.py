"""The session state machine: legal edges, terminal states, expiry, restart.

Acceptance gates covered: 5 (explicit stop terminates and cleans up), 6
(expiry terminates and cleans up), 9 (process restart does not leave a session
falsely marked active).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from bartholomew.multimodal.modality import CaptureScope, Modality, ScopeKind
from bartholomew.multimodal.session import (
    LIVE_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    InvalidTransitionError,
    MultimodalSession,
    SessionState,
    validate_duration,
)
from bartholomew.multimodal.store import SessionStore, read_status_file, write_status_file


def _session(modality: Modality = Modality.MICROPHONE, **kwargs) -> MultimodalSession:
    scope = (
        CaptureScope(ScopeKind.WINDOW, window_id="w1", window_title="Notes")
        if modality is Modality.SCREEN
        else None
    )
    return MultimodalSession(
        tenant_id=kwargs.pop("tenant_id", "tenant-1"),
        principal_id=kwargs.pop("principal_id", "user:taylor"),
        device_id=kwargs.pop("device_id", "device-1"),
        modality=modality,
        correlation_id=kwargs.pop("correlation_id", "corr-1"),
        scope=kwargs.pop("scope", scope),
        **kwargs,
    )


def _activate(session: MultimodalSession) -> MultimodalSession:
    session.transition(SessionState.AWAITING_APPROVAL)
    session.governance_decision = True
    session.consent_decision = True
    session.approve()
    session.transition(SessionState.ACTIVE)
    return session


class TestBinding:
    """A session must name everything it is bound to, or refuse to exist."""

    @pytest.mark.parametrize(
        "missing",
        ["tenant_id", "principal_id", "device_id", "correlation_id"],
    )
    def test_missing_binding_is_refused(self, missing):
        with pytest.raises(ValueError, match=missing):
            _session(**{missing: ""})

    def test_screen_session_requires_a_scope(self):
        with pytest.raises(ValueError, match="capture scope"):
            MultimodalSession(
                tenant_id="t",
                principal_id="p",
                device_id="d",
                modality=Modality.SCREEN,
                correlation_id="c",
            )

    def test_non_screen_session_cannot_carry_a_scope(self):
        with pytest.raises(ValueError, match="cannot carry a capture scope"):
            MultimodalSession(
                tenant_id="t",
                principal_id="p",
                device_id="d",
                modality=Modality.MICROPHONE,
                correlation_id="c",
                scope=CaptureScope(ScopeKind.DISPLAY, display_id="1"),
            )

    def test_snapshot_carries_full_provenance(self):
        snapshot = _activate(_session()).snapshot()
        for field in (
            "tenant_id",
            "principal_id",
            "device_id",
            "modality",
            "correlation_id",
            "causation_id",
            "started_at",
            "expires_at",
            "consent_decision",
            "governance_decision",
        ):
            assert field in snapshot


class TestDuration:
    def test_default_is_bounded(self):
        assert validate_duration(None) > 0

    def test_over_ceiling_is_refused_not_clamped(self):
        with pytest.raises(ValueError, match="exceeds maximum"):
            validate_duration(60 * 60 * 8)

    @pytest.mark.parametrize("bad", [0, -1, True, 1.5])
    def test_nonsense_durations_refuse(self, bad):
        with pytest.raises(ValueError):
            validate_duration(bad)


class TestTransitions:
    def test_every_state_has_a_transition_entry(self):
        for state in SessionState:
            assert state in TRANSITIONS

    def test_terminal_states_have_no_exit(self):
        for state in TERMINAL_STATES:
            assert TRANSITIONS[state] == frozenset()

    def test_cannot_reach_active_without_approval(self):
        session = _session()
        with pytest.raises(InvalidTransitionError):
            session.transition(SessionState.ACTIVE)

    def test_cannot_approve_without_both_decisions(self):
        session = _session()
        session.transition(SessionState.AWAITING_APPROVAL)
        session.governance_decision = True
        with pytest.raises(InvalidTransitionError, match="explicit consent"):
            session.approve()

    def test_cannot_approve_without_governance(self):
        session = _session()
        session.transition(SessionState.AWAITING_APPROVAL)
        session.consent_decision = True
        with pytest.raises(InvalidTransitionError, match="governance"):
            session.approve()

    def test_terminal_session_cannot_restart(self):
        """No automatic session restart: a finished session stays finished."""
        session = _activate(_session())
        session.transition(SessionState.STOPPED)
        for target in SessionState:
            with pytest.raises(InvalidTransitionError):
                session.transition(target)

    def test_history_records_every_move(self):
        session = _activate(_session())
        moves = [(h.from_state, h.to_state) for h in session.history]
        assert moves == [
            (SessionState.REQUESTED, SessionState.AWAITING_APPROVAL),
            (SessionState.AWAITING_APPROVAL, SessionState.APPROVED),
            (SessionState.APPROVED, SessionState.ACTIVE),
        ]


class TestStopAndExpiry:
    def test_explicit_stop_terminates_and_cleans_up(self):
        """Gate 5."""
        store = SessionStore()
        session = _activate(_session())
        cleaned = []
        store.add(session, stopper=lambda: cleaned.append(True))

        assert store.stop(session.session_id) is True
        assert session.state is SessionState.STOPPED
        assert session.is_terminal
        assert cleaned == [True], "the adapter's cleanup hook must run"
        assert store.live() == []

    def test_stopping_twice_is_not_an_error(self):
        store = SessionStore()
        session = _activate(_session())
        store.add(session)
        assert store.stop(session.session_id) is True
        assert store.stop(session.session_id) is False

    def test_expiry_terminates_and_cleans_up(self):
        """Gate 6."""
        store = SessionStore()
        session = _activate(_session())
        cleaned = []
        store.add(session, stopper=lambda: cleaned.append(True))

        later = datetime.now(timezone.utc) + timedelta(
            seconds=session.max_duration_seconds + 1,
        )
        assert store.sweep_expired(later) == [session.session_id]
        assert session.state is SessionState.EXPIRED
        assert cleaned == [True]
        assert store.live() == []

    def test_unexpired_session_survives_a_sweep(self):
        store = SessionStore()
        session = _activate(_session())
        store.add(session)
        assert store.sweep_expired() == []
        assert session.state is SessionState.ACTIVE

    def test_seconds_remaining_never_negative(self):
        session = _activate(_session())
        far = datetime.now(timezone.utc) + timedelta(days=1)
        assert session.seconds_remaining(far) == 0.0


class TestRestartCleanup:
    """Gate 9: a restart must never leave a session falsely marked active."""

    def test_fresh_store_reports_nothing_live(self):
        assert SessionStore().live() == []

    def test_dead_owner_session_becomes_failed(self):
        store = SessionStore()
        reconciled = store.reconcile_after_restart(
            [{"session_id": "s1", "state": "active", "owner_pid": 0x7FFFFFFF}],
        )
        assert reconciled[0]["state"] == SessionState.FAILED.value
        assert reconciled[0]["is_live"] is False
        assert reconciled[0]["reconciled_after_restart"] is True
        assert "process restart" in reconciled[0]["outcome_reason"]

    @pytest.mark.parametrize("state", sorted(s.value for s in LIVE_STATES))
    def test_every_live_state_is_reconciled(self, state):
        store = SessionStore()
        reconciled = store.reconcile_after_restart(
            [{"session_id": "s", "state": state, "owner_pid": 0x7FFFFFFF}],
        )
        assert reconciled[0]["state"] == SessionState.FAILED.value

    def test_terminal_snapshots_are_left_alone(self):
        store = SessionStore()
        reconciled = store.reconcile_after_restart(
            [{"session_id": "s", "state": "stopped", "owner_pid": 0x7FFFFFFF}],
        )
        assert reconciled[0]["state"] == "stopped"
        assert "reconciled_after_restart" not in reconciled[0]

    def test_own_live_session_is_preserved(self):
        store = SessionStore()
        reconciled = store.reconcile_after_restart(
            [{"session_id": "s", "state": "active", "owner_pid": os.getpid()}],
        )
        assert reconciled[0]["state"] == "active"

    def test_status_file_round_trip_carries_no_captured_content(self, tmp_path):
        path = tmp_path / "multimodal-status.json"
        write_status_file(path, [_activate(_session())])
        snapshots = read_status_file(path)
        assert len(snapshots) == 1
        serialised = str(snapshots[0])
        for forbidden in ("transcript", "audio", "image", "screenshot", "description"):
            assert forbidden not in serialised.lower()

    def test_corrupt_status_file_reports_nothing_live(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert read_status_file(path) == []

    def test_missing_status_file_reports_nothing_live(self, tmp_path):
        assert read_status_file(tmp_path / "absent.json") == []
