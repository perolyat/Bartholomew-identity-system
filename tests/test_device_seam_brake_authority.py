"""
The sight/voice device seams read the brake the *production* writers write.

**The defect this pins.** Both brake writers -- `bartholomew brake on` and
the API's `POST /governance/brake/engage` -- write `GovernanceStore`
(`parking_brake_state`). Phase B6 retired the dual-check bridge that used to
keep the legacy `system_flags` "parking_brake" row in step with it. But
`run_sight_through_runtime_contract()` and
`run_voice_through_runtime_contract()` still read that legacy row through
`ParkingBrake(BrakeStorage(...))` -- so engaging the brake did not stop
either seam.

**Why the existing suite did not catch it.**
`tests/test_voice_sight_runtime_contract_seam.py` engages the brake through
`ParkingBrake(BrakeStorage(...))` -- the same legacy store the seams read.
Those tests genuinely prove the gate *works*; they cannot prove it is wired
to the brake anyone actually engages, because the test and the code shared
the same wrong store. Every test here engages through `GovernanceStore`
instead, which is what the CLI and the API do.

**Severity, stated honestly.** The capabilities behind these two seams are
inert stubs today (`_perform_capture`/`_perform_stream` print a line), so
nothing live was failing to stop. This is a latent governance defect: it
would first matter when Stage 6 puts a real camera or microphone behind the
seam, and it would have matter*ed* silently, because the existing tests would
still have passed. Found while implementing the spoken-output capability,
which shares the `voice` scope.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.runtime_contract import (
    run_sight_through_runtime_contract,
    run_spoken_output_through_runtime_contract,
    run_voice_through_runtime_contract,
)
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake


@pytest.fixture
def db_path(tmp_path):
    from bartholomew.kernel.scheduler import persistence as sp

    path = str(tmp_path / "brake.db")
    sp.ensure_schema(path)
    return path


@pytest.fixture(autouse=True)
def _consent_always_granted():
    """The device seams' consent gate is not what these tests are about; grant
    it so a denial can only come from the brake."""
    set_consent_handler(lambda _prompt: True)
    yield
    set_consent_handler(None)


class _Ran:
    def __init__(self):
        self.calls = 0

    def __call__(self, *args):
        self.calls += 1


class TestBrakeEngagedTheWayProductionEngagesIt:
    async def test_sight_is_blocked_by_a_governance_store_engage(self, db_path):
        GovernanceStore(db_path).engage("sight", reason="cli: brake on", actor="cli")
        capture = _Ran()

        result = await run_sight_through_runtime_contract(
            db_path=db_path,
            capture_fn=capture,
        )

        assert result.governance_allowed is False
        assert result.outcome == "parking_brake_denied"
        assert capture.calls == 0, "the brake was engaged and the capability ran anyway"

    async def test_voice_stream_is_blocked_by_a_governance_store_engage(self, db_path):
        GovernanceStore(db_path).engage("voice", reason="cli: brake on", actor="cli")
        stream = _Ran()

        result = await run_voice_through_runtime_contract(
            db_path=db_path,
            stream_fn=stream,
        )

        assert result.governance_allowed is False
        assert result.outcome == "parking_brake_denied"
        assert stream.calls == 0

    async def test_a_global_engage_blocks_both(self, db_path):
        GovernanceStore(db_path).engage("global", reason="cli: brake on", actor="cli")
        capture, stream = _Ran(), _Ran()

        sight = await run_sight_through_runtime_contract(db_path=db_path, capture_fn=capture)
        voice = await run_voice_through_runtime_contract(db_path=db_path, stream_fn=stream)

        assert sight.outcome == "parking_brake_denied"
        assert voice.outcome == "parking_brake_denied"
        assert capture.calls == 0
        assert stream.calls == 0

    async def test_every_device_surface_answers_to_one_brake(self, db_path):
        """Sight, voice streaming and spoken output all consult the same
        authority, so `brake on --scope global` means the same thing to all
        three. Before this fix, the first two did not."""
        GovernanceStore(db_path).engage("global", reason="cli: brake on", actor="cli")

        outcomes = [
            (await run_sight_through_runtime_contract(db_path=db_path, capture_fn=_Ran())).outcome,
            (await run_voice_through_runtime_contract(db_path=db_path, stream_fn=_Ran())).outcome,
            (
                await run_spoken_output_through_runtime_contract(
                    "hello",
                    enabled=True,
                    db_path=db_path,
                    speak_fn=_Ran(),
                )
            ).outcome,
        ]
        assert outcomes == ["parking_brake_denied"] * 3

    async def test_disengaging_restores_normal_operation(self, db_path):
        store = GovernanceStore(db_path)
        store.engage("sight", reason="cli: brake on", actor="cli")
        store.refresh()
        store.disengage(reason="cli: brake off", actor="cli")
        capture = _Ran()

        result = await run_sight_through_runtime_contract(db_path=db_path, capture_fn=capture)

        assert result.governance_allowed is True
        assert capture.calls == 1

    async def test_an_unrelated_scope_does_not_block(self, db_path):
        GovernanceStore(db_path).engage("skills", reason="cli: brake on", actor="cli")
        capture = _Ran()

        result = await run_sight_through_runtime_contract(db_path=db_path, capture_fn=capture)

        assert result.governance_allowed is True
        assert capture.calls == 1


class TestTheTwoStoresHaveNotSilentlyBeenReUnified:
    def test_the_legacy_store_still_does_not_see_a_governance_engage(self, db_path):
        """Not a wish -- a recorded fact about the current system, and the
        reason these tests exist. If this ever starts failing, the two stores
        have been bridged again and the fix above can be revisited; until then
        it documents exactly why reading the legacy row was wrong."""
        GovernanceStore(db_path).engage("voice", reason="cli: brake on", actor="cli")

        legacy = ParkingBrake(BrakeStorage(db_path))
        assert legacy.is_blocked("voice") is False

    def test_no_device_seam_reads_the_legacy_store_any_more(self):
        import inspect

        from bartholomew.kernel import runtime_contract

        for seam in (
            runtime_contract.run_sight_through_runtime_contract,
            runtime_contract.run_voice_through_runtime_contract,
            runtime_contract.run_spoken_output_through_runtime_contract,
        ):
            source = inspect.getsource(seam)
            # Against code, not prose: each gate's comment names the legacy
            # store while explaining that it deliberately no longer uses it.
            code = "\n".join(
                line for line in source.splitlines() if not line.lstrip().startswith("#")
            )
            assert "BrakeStorage" not in code, f"{seam.__name__} still reads the legacy store"
            assert "construct_parking_brake_off_loop" not in code
            assert "is_blocked_fail_closed_off_loop" in code


class TestTheGateFailsClosed:
    async def test_an_unreadable_brake_denies_rather_than_allows(self, db_path, monkeypatch):
        """The previous `except ImportError: pass` let an unreadable gate fall
        through to "allowed". A device surface cannot afford that."""
        from bartholomew.orchestrator.safety import governance_store

        async def _explode(*args, **kwargs):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(governance_store, "is_blocked_fail_closed_off_loop", _explode)
        capture = _Ran()

        result = await run_sight_through_runtime_contract(db_path=db_path, capture_fn=capture)

        assert result.governance_allowed is False
        assert result.outcome == "parking_brake_denied"
        assert capture.calls == 0
