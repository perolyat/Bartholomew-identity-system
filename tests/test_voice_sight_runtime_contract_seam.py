"""
Tests for MASTER_PLAN.md's "P2.5 -- Runtime Convergence" item 11.21:
voice/sight convergence (Exit Gate questions #1-3 for the two remaining
surfaces).

Before this, the voice/sight adapters (`identity_interpreter/adapters/
voice_io/stream_bridge.py`, `identity_interpreter/adapters/sight/pipeline.py`)
had only their own `ParkingBrake` check, constructed no Observation, were
never modeled as a CandidateAction, and never consulted Identity Policy or any
consent gate -- `COGNITIVE_RUNTIME.md`'s Exit Gate table named them as the last
remaining "Partial" surfaces.

Scope note: real microphone/camera/streaming/transcription/CV/device-lifecycle
work is Stage 6 and is NOT implemented here. These tests exercise only the
mandatory *architectural* invariants of the governed seam:
  - every executable start creates the right Observation + CandidateAction;
  - consent/policy/parking-brake decisions happen BEFORE any capability call;
  - denied starts produce no underlying side effect;
  - approved starts execute exactly once;
  - exactly one Reflection per attempt, into the shared sink;
  - production callers cannot bypass the governed seam.

The `TestMutationNonVacuity...` classes are the required non-vacuity controls:
they deliberately neutralise a governance authority (in the test only, never in
production code) and assert the placeholder then DOES execute -- proving the
denial tests above would fail if governance were bypassed.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.reflection import REFLECTION_KIND
from bartholomew.kernel.runtime_contract import (
    CandidateAction,
    DeviceRuntimeResult,
    run_sight_through_runtime_contract,
    run_voice_through_runtime_contract,
)
from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake
from identity_interpreter.identity_context import IdentityContext


ALLOW_CONTEXT = IdentityContext(tool_use_default_allowed=True, tool_use_allowlist=[])
DENY_CONTEXT = IdentityContext(tool_use_default_allowed=False, tool_use_allowlist=[])
# Allowlists an unrelated kind -- if governance ever stopped evaluating the
# real CandidateAction.kind, this would wrongly permit the device start too.
UNRELATED_ALLOW_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["web_fetch"],
)


# =============================================================================
# Fixtures + spies
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_consent_handler():
    set_consent_handler(None)
    yield
    set_consent_handler(None)


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "t.db")
    mem = MemoryStore(path)
    await mem.init()
    return path


class Spy:
    """A capability spy recording call order into a shared log and counting
    invocations, so tests can prove exactly-once execution and governance
    ordering. Optionally raises, to exercise the post-approval error path."""

    def __init__(self, log: list[str], name: str, raises: bool = False, ret=None):
        self.log = log
        self.name = name
        self.raises = raises
        self.ret = ret
        self.calls = 0

    def __call__(self):
        self.calls += 1
        self.log.append(self.name)
        if self.raises:
            raise RuntimeError(f"{self.name} boom")
        return self.ret


def _approving_handler(log: list[str] | None = None):
    def handler(_prompt: str) -> bool:
        if log is not None:
            log.append("consent")
        return True

    return handler


def _reflection_rows(path: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT kind, content, meta FROM reflections WHERE kind=?",
            (REFLECTION_KIND,),
        ).fetchall()
    finally:
        conn.close()


# Each surface parametrized through one place, so every invariant is proven for
# BOTH voice and sight without copy-paste. `seam` is the async entry point;
# `cap_kw` is its capability keyword; `kind`/`surface`/`scope` are the expected
# per-surface identifiers.
SURFACES = [
    pytest.param(run_sight_through_runtime_contract, "capture_fn", "sight_capture_start", "sight", "sight", id="sight"),
    pytest.param(run_voice_through_runtime_contract, "stream_fn", "voice_stream_start", "voice", "voice", id="voice"),
]


# =============================================================================
# 1. Observation / CandidateAction genuinely constructed and consumed.
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("seam,cap_kw,kind,surface,scope", SURFACES)
class TestGovernanceGenuinelyGatesExecution:
    async def test_approved_creates_observation_and_candidate_action(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        log: list[str] = []
        spy = Spy(log, "capability")
        set_consent_handler(_approving_handler())

        result: DeviceRuntimeResult = await seam(
            db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: spy}
        )

        assert result.observation.source == surface
        assert result.candidate_action.kind == kind
        assert result.candidate_action.interpretation.observation is result.observation
        assert result.started is True

    async def test_policy_authority_receives_the_candidate_action(
        self, db_path, seam, cap_kw, kind, surface, scope, monkeypatch
    ):
        """The exact CandidateAction the seam builds is what the policy
        authority evaluates -- captured by spying on both the CandidateAction
        constructor and evaluate_tool_policy()."""
        set_consent_handler(_approving_handler())
        constructed: list[str] = []
        real_ca = CandidateAction

        def spy_ca(*, kind, interpretation):
            constructed.append(kind)
            return real_ca(kind=kind, interpretation=interpretation)

        monkeypatch.setattr(rc, "CandidateAction", spy_ca)

        evaluated: list[str] = []
        real_eval = rc.policy_engine.evaluate_tool_policy

        def spy_eval(context, tool_name):
            evaluated.append(tool_name)
            return real_eval(context, tool_name)

        monkeypatch.setattr(rc.policy_engine, "evaluate_tool_policy", spy_eval)

        await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: Spy([], "c")})

        assert constructed == [kind]
        assert evaluated == constructed  # governance evaluated the built CA

    async def test_policy_denied_never_executes(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        set_consent_handler(_approving_handler())  # consent would allow
        spy = Spy([], "capability")

        result = await seam(db_path=db_path, identity_context=DENY_CONTEXT, **{cap_kw: spy})

        assert result.started is False
        assert result.governance_allowed is False
        assert result.outcome == "governance_denied"
        assert spy.calls == 0

    async def test_unrelated_allowlist_entry_still_denies(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        set_consent_handler(_approving_handler())
        spy = Spy([], "capability")

        result = await seam(
            db_path=db_path, identity_context=UNRELATED_ALLOW_CONTEXT, **{cap_kw: spy}
        )

        assert result.outcome == "governance_denied"
        assert spy.calls == 0

    async def test_consent_absent_never_executes(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        # No consent handler registered (fail-closed), policy would allow.
        spy = Spy([], "capability")

        result = await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: spy})

        assert result.started is False
        assert result.outcome == "consent_denied"
        assert spy.calls == 0

    async def test_consent_declined_never_executes(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        set_consent_handler(lambda _prompt: False)
        spy = Spy([], "capability")

        result = await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: spy})

        assert result.outcome == "consent_denied"
        assert spy.calls == 0

    async def test_consent_unresolved_falsy_never_executes(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        set_consent_handler(lambda _prompt: None)  # unresolved ask -> falsy
        spy = Spy([], "capability")

        result = await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: spy})

        assert result.outcome == "consent_denied"
        assert spy.calls == 0

    async def test_parking_brake_denied_never_executes(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        set_consent_handler(_approving_handler())  # consent would allow
        brake = ParkingBrake(BrakeStorage(db_path))
        brake.engage(scope)
        try:
            spy = Spy([], "capability")
            result = await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: spy})
            assert result.outcome == "parking_brake_denied"
            assert spy.calls == 0
        finally:
            brake.disengage()

    async def test_approved_executes_exactly_once(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        set_consent_handler(_approving_handler())
        spy = Spy([], "capability")

        result = await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: spy})

        assert result.started is True
        assert spy.calls == 1

    async def test_execution_occurs_only_after_governance(
        self, db_path, seam, cap_kw, kind, surface, scope, monkeypatch
    ):
        """Ordering proof: policy check and consent both complete strictly
        before the capability runs."""
        log: list[str] = []
        set_consent_handler(_approving_handler(log))

        real_eval = rc.policy_engine.evaluate_tool_policy

        def spy_eval(context, tool_name):
            log.append("policy")
            return real_eval(context, tool_name)

        monkeypatch.setattr(rc.policy_engine, "evaluate_tool_policy", spy_eval)

        spy = Spy(log, "capability")
        await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: spy})

        assert log == ["policy", "consent", "capability"]

    async def test_no_identity_context_skips_policy_but_still_needs_consent(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        """Additive policy (skipped when no IdentityContext), matching
        chat/scheduler/skill -- but consent still fail-closes, so a start with
        neither still cannot execute."""
        spy = Spy([], "capability")

        # No identity_context, no consent handler -> consent denies.
        denied = await seam(db_path=db_path, **{cap_kw: spy})
        assert denied.outcome == "consent_denied"
        assert spy.calls == 0

        # No identity_context, consent granted -> executes (policy skipped).
        set_consent_handler(_approving_handler())
        allowed = await seam(db_path=db_path, **{cap_kw: spy})
        assert allowed.started is True
        assert spy.calls == 1


# =============================================================================
# 2. Exactly one Reflection per attempt, into the shared sink, every outcome.
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("seam,cap_kw,kind,surface,scope", SURFACES)
class TestExactlyOneReflectionPerAttempt:
    async def test_success_records_one_reflection(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        set_consent_handler(_approving_handler())
        await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: Spy([], "c")})

        rows = _reflection_rows(db_path)
        assert len(rows) == 1
        mem = await MemoryStore(db_path).latest_reflection(REFLECTION_KIND)
        assert mem["meta"]["surface"] == surface
        assert mem["meta"]["action"] == kind
        assert mem["meta"]["outcome"] == "started"

    async def test_policy_denial_records_one_reflection(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        set_consent_handler(_approving_handler())
        await seam(db_path=db_path, identity_context=DENY_CONTEXT, **{cap_kw: Spy([], "c")})

        rows = _reflection_rows(db_path)
        assert len(rows) == 1
        mem = await MemoryStore(db_path).latest_reflection(REFLECTION_KIND)
        assert mem["meta"]["outcome"] == "governance_denied"

    async def test_consent_denial_records_one_reflection(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: Spy([], "c")})

        rows = _reflection_rows(db_path)
        assert len(rows) == 1
        mem = await MemoryStore(db_path).latest_reflection(REFLECTION_KIND)
        assert mem["meta"]["outcome"] == "consent_denied"

    async def test_parking_brake_denial_records_one_reflection(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        set_consent_handler(_approving_handler())
        brake = ParkingBrake(BrakeStorage(db_path))
        brake.engage(scope)
        try:
            await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: Spy([], "c")})
        finally:
            brake.disengage()

        rows = _reflection_rows(db_path)
        assert len(rows) == 1
        mem = await MemoryStore(db_path).latest_reflection(REFLECTION_KIND)
        assert mem["meta"]["outcome"] == "parking_brake_denied"

    async def test_execution_error_records_one_reflection_and_ran_once(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        set_consent_handler(_approving_handler())
        spy = Spy([], "capability", raises=True)

        result = await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: spy})

        assert result.governance_allowed is True  # governance passed...
        assert result.started is False  # ...but execution raised
        assert result.outcome == "error"
        assert spy.calls == 1  # the capability genuinely ran once

        rows = _reflection_rows(db_path)
        assert len(rows) == 1
        mem = await MemoryStore(db_path).latest_reflection(REFLECTION_KIND)
        assert mem["meta"]["outcome"] == "error"

    async def test_multiple_attempts_each_add_exactly_one(
        self, db_path, seam, cap_kw, kind, surface, scope
    ):
        set_consent_handler(_approving_handler())
        await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: Spy([], "c")})
        await seam(db_path=db_path, identity_context=DENY_CONTEXT, **{cap_kw: Spy([], "c")})
        set_consent_handler(None)
        await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: Spy([], "c")})

        assert len(_reflection_rows(db_path)) == 3


# =============================================================================
# 3. Compat wrappers delegate exclusively to the governed seam.
# =============================================================================


class TestCompatWrappersDelegateToSeam:
    """These wrappers are sync entry points (they drive the async seam via
    asyncio.run internally), so the tests are sync too -- calling start_capture
    from inside an async test's running loop would itself raise, which is
    exactly why production must call these from sync context (Stage 6's real
    async caller uses the seam directly)."""

    def test_start_capture_calls_the_seam(self, monkeypatch):
        import identity_interpreter.adapters.sight.pipeline as sight

        spy = AsyncMock(
            return_value=DeviceRuntimeResult(
                observation=None,
                candidate_action=None,
                governance_allowed=True,
                started=True,
                outcome="started",
                reason=None,
                result={"status": "capturing"},
            )
        )
        monkeypatch.setattr(rc, "run_sight_through_runtime_contract", spy)

        result = sight.start_capture("/tmp/whatever.db")

        spy.assert_awaited_once()
        assert spy.await_args.kwargs["capture_fn"] is sight._perform_capture
        assert result == {"blocked": False, "status": "capturing"}

    def test_start_capture_maps_denial_to_blocked(self, monkeypatch):
        import identity_interpreter.adapters.sight.pipeline as sight

        spy = AsyncMock(
            return_value=DeviceRuntimeResult(
                observation=None,
                candidate_action=None,
                governance_allowed=False,
                started=False,
                outcome="consent_denied",
                reason="x",
                result=None,
            )
        )
        monkeypatch.setattr(rc, "run_sight_through_runtime_contract", spy)

        assert sight.start_capture("/tmp/whatever.db") == {"blocked": True}

    def test_start_stream_calls_the_seam(self, monkeypatch):
        import identity_interpreter.adapters.voice_io.stream_bridge as voice

        spy = AsyncMock(
            return_value=DeviceRuntimeResult(
                observation=None,
                candidate_action=None,
                governance_allowed=True,
                started=True,
                outcome="started",
                reason=None,
                result=None,
            )
        )
        monkeypatch.setattr(rc, "run_voice_through_runtime_contract", spy)

        assert voice.start_stream("/tmp/whatever.db") is None
        spy.assert_awaited_once()
        assert spy.await_args.kwargs["stream_fn"] is voice._perform_stream


# =============================================================================
# 4. Structural: production callers cannot bypass the governed seam.
# =============================================================================


class TestStructuralNoBypass:
    def _production_py_files(self) -> list[Path]:
        repo_root = Path(__file__).resolve().parent.parent
        roots = [
            repo_root / "bartholomew",
            repo_root / "bartholomew_api_bridge_v0_1",
            repo_root / "identity_interpreter",
        ]
        files: list[Path] = []
        for root in roots:
            if root.exists():
                files.extend(root.rglob("*.py"))
        return files

    def test_placeholder_capabilities_are_never_called_directly(self):
        """`_perform_capture`/`_perform_stream` may only be *referenced* (passed
        to the seam as capture_fn/stream_fn), never *invoked* by name anywhere
        in production -- the only invocation happens inside the seam via its
        injected parameter. An `_perform_capture()`/`_perform_stream()` call
        expression appearing anywhere would be a bypass of governance; this
        fails the moment one is introduced."""
        offenders = []
        for py_file in self._production_py_files():
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"_perform_capture", "_perform_stream"}
                ):
                    offenders.append(f"{py_file}:{node.lineno}")
        assert offenders == [], f"Placeholder invoked directly (bypassing seam): {offenders}"

    def test_start_functions_delegate_to_the_named_seam(self):
        """AST proof that start_capture()/start_stream() each reference their
        governed seam function by name (delegation), not the placeholder."""
        repo_root = Path(__file__).resolve().parent.parent
        checks = [
            (
                repo_root / "identity_interpreter/adapters/sight/pipeline.py",
                "start_capture",
                "run_sight_through_runtime_contract",
            ),
            (
                repo_root / "identity_interpreter/adapters/voice_io/stream_bridge.py",
                "start_stream",
                "run_voice_through_runtime_contract",
            ),
        ]
        for path, func_name, seam_name in checks:
            tree = ast.parse(path.read_text(), filename=str(path))
            func = next(
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == func_name
            )
            referenced = {
                n.id for n in ast.walk(func) if isinstance(n, ast.Name)
            } | {
                n.attr for n in ast.walk(func) if isinstance(n, ast.Attribute)
            }
            assert seam_name in referenced, f"{func_name} does not delegate to {seam_name}"


# =============================================================================
# 5. Non-vacuity controls (test 10): neutralise a governance authority in the
#    test only, and prove the placeholder THEN executes -- so the denial tests
#    above are demonstrably not passing vacuously. Production policy is never
#    weakened; these monkeypatch a force-allow double for the duration only.
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("seam,cap_kw,kind,surface,scope", SURFACES)
class TestMutationNonVacuity:
    async def test_policy_denial_would_pass_if_policy_were_neutralised(
        self, db_path, seam, cap_kw, kind, surface, scope, monkeypatch
    ):
        """Mirror of test_policy_denied_never_executes: under DENY_CONTEXT the
        capability must NOT run. Here we force evaluate_tool_policy to allow --
        the capability now DOES run, proving the denial was really enforced by
        the policy gate and the test isn't vacuous."""
        set_consent_handler(_approving_handler())
        from bartholomew.kernel.policy_engine import PolicyDecision

        monkeypatch.setattr(
            rc.policy_engine,
            "evaluate_tool_policy",
            lambda ctx, name: PolicyDecision(allowed=True),
        )
        spy = Spy([], "capability")

        result = await seam(db_path=db_path, identity_context=DENY_CONTEXT, **{cap_kw: spy})

        assert result.started is True
        assert spy.calls == 1

    async def test_consent_denial_would_pass_if_consent_were_neutralised(
        self, db_path, seam, cap_kw, kind, surface, scope, monkeypatch
    ):
        """Mirror of test_consent_absent_never_executes: with no handler the
        capability must NOT run. Here we force the seam's consent lookup to a
        force-allow handler -- the capability now runs, proving consent really
        gated it."""
        monkeypatch.setattr(rc, "get_consent_handler", lambda: (lambda _p: True))
        spy = Spy([], "capability")

        result = await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: spy})

        assert result.started is True
        assert spy.calls == 1

    async def test_brake_denial_would_pass_if_brake_were_neutralised(
        self, db_path, seam, cap_kw, kind, surface, scope, monkeypatch
    ):
        """Mirror of test_parking_brake_denied_never_executes: with the brake
        engaged the capability must NOT run. Here we force is_blocked to False
        while engaged -- the capability runs, proving the brake really gated
        it."""
        set_consent_handler(_approving_handler())
        brake = ParkingBrake(BrakeStorage(db_path))
        brake.engage(scope)
        monkeypatch.setattr(ParkingBrake, "is_blocked", lambda self, s: False)
        try:
            spy = Spy([], "capability")
            result = await seam(db_path=db_path, identity_context=ALLOW_CONTEXT, **{cap_kw: spy})
            assert result.started is True
            assert spy.calls == 1
        finally:
            brake.disengage()
