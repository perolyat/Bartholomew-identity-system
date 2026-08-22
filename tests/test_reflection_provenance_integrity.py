"""
WP-A2b -- provenance-bearing Reflection surfaces (seam level).

Authority: `DECISIONS.md`, "One Reflection sink, two semantic roles" -- the
shared sink (`record_action_reflection`) is **additive** on the skill /
awaiting_response / scheduler surfaces (another authoritative durable record
exists) and **sole required provenance** on the chat / training / sight+voice
surfaces (S5.3 E.2's explanation-grade chat context; S5.2 Sec.13.4's
supersession history; the only record of a device start's governance
outcome).

What this file pins, per the approved WP-A2b semantics:

* successful action + persisted provenance -> normal, undegraded success;
* successful action + failed provenance write -> the action's own outcome is
  reported truthfully (never a false failure), the loss is caller-visible
  (`provenance_degraded` / `provenance_error`), and full success is not
  presented;
* no retry of the already-completed real-world effect;
* pre-action governance stays fail-closed and a denied action is never
  misrepresented as provenance degradation;
* the additive surfaces keep their existing semantics unchanged.

Failure injection is a real SQLite failure path: a `RAISE(ABORT)` trigger on
the `reflections` table, so the production INSERT genuinely executes and is
genuinely rejected. Nothing is monkeypatched to fake an exception.

HTTP-level exposure (chat and training routes) is covered separately in
`tests/test_api_provenance_degradation.py`, which needs its own module-scoped
live app.
"""

from __future__ import annotations

import contextlib
import sqlite3

import pytest

from bartholomew.kernel import training
from bartholomew.kernel.competency import CompetencyEnvelope, CompetencyHeuristic
from bartholomew.kernel.db_ctx import connect, set_wal_pragmas
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.reflection import (
    REFLECTION_KIND,
    ActionReflection,
    record_action_reflection,
)
from bartholomew.kernel.runtime_contract import (
    run_chat_through_runtime_contract,
    run_sight_through_runtime_contract,
    run_training_through_runtime_contract,
    run_voice_through_runtime_contract,
)
from bartholomew.kernel.skill_permissions import reset_permission_checker
from bartholomew.kernel.skill_registry import SkillRegistry, reset_skill_registry
from identity_interpreter.identity_context import IdentityContext

ALLOW_CONTEXT = IdentityContext(tool_use_default_allowed=True, tool_use_allowlist=[])


@pytest.fixture(autouse=True)
def _reset_singletons():
    reset_skill_registry()
    reset_permission_checker()
    set_consent_handler(None)
    yield
    reset_skill_registry()
    reset_permission_checker()
    set_consent_handler(None)


@contextlib.contextmanager
def _reflection_writes_fail(db_path: str):
    """Make every INSERT into `reflections` fail, and nothing else.

    A real SQLite ABORT trigger: the production write runs and is rejected
    by the database itself.
    """

    def run(sql: str) -> None:
        conn = connect(db_path)
        try:
            set_wal_pragmas(conn)
            conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    run(
        "CREATE TRIGGER wp_a2b_block_reflections BEFORE INSERT ON reflections "
        "BEGIN SELECT RAISE(ABORT, 'injected reflection failure'); END",
    )
    try:
        yield
    finally:
        run("DROP TRIGGER IF EXISTS wp_a2b_block_reflections")


def _count_reflections(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM reflections WHERE kind = ?",
            (REFLECTION_KIND,),
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The sink itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSinkOutcome:
    async def test_persisted_write_reports_row_id_and_no_error(self, tmp_path):
        mem = MemoryStore(str(tmp_path / "t.db"))
        await mem.init()
        try:
            outcome = await record_action_reflection(
                mem,
                ActionReflection("chat", "chat_response", "responded", "hi"),
            )
            assert outcome.error is None
            assert outcome.row_id is not None
            assert outcome.persisted is True
        finally:
            await mem.close()

    async def test_failed_write_reports_a_useful_error_and_never_raises(self, tmp_path):
        db = str(tmp_path / "t.db")
        mem = MemoryStore(db)
        await mem.init()
        try:
            with _reflection_writes_fail(db):
                outcome = await record_action_reflection(
                    mem,
                    ActionReflection("chat", "chat_response", "responded", "hi"),
                )
            assert outcome.persisted is False
            assert outcome.row_id is None
            # A useful failure indication: which surface, and what happened.
            assert "chat" in outcome.error
            assert "injected reflection failure" in outcome.error
        finally:
            await mem.close()


# ---------------------------------------------------------------------------
# Chat surface (provenance-bearing)
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_daemon(tmp_path):
    """A minimal real KernelDaemon (mirrors test_reflection_unification.py)."""
    (tmp_path / "kernel.yaml").write_text(
        'timezone: "Australia/Brisbane"\nloop_interval_seconds: 1\n',
    )
    (tmp_path / "persona.yaml").write_text('name: "Test Bartholomew"\n')
    (tmp_path / "policy.yaml").write_text("policies: []\n")
    (tmp_path / "drives.yaml").write_text("drives: []\n")

    from bartholomew.kernel.daemon import KernelDaemon

    return KernelDaemon(
        cfg_path=str(tmp_path / "kernel.yaml"),
        db_path=str(tmp_path / "test.db"),
        persona_path=str(tmp_path / "persona.yaml"),
        policy_path=str(tmp_path / "policy.yaml"),
        drives_path=str(tmp_path / "drives.yaml"),
    )


async def _stub_respond(prompt: str) -> str:
    return f"echo: {prompt}"


@pytest.mark.asyncio
class TestChatProvenance:
    async def test_healthy_turn_is_not_degraded(self, chat_daemon):
        await chat_daemon.mem.init()
        try:
            result = await run_chat_through_runtime_contract(
                chat_daemon,
                "hello",
                _stub_respond,
            )
            assert result.governance_allowed is True
            # _build_interpretation enriches the prompt with Experience
            # Kernel state before respond_fn sees it, so assert containment.
            assert "hello" in result.response
            assert result.provenance_degraded is False
            assert result.provenance_error is None
        finally:
            await chat_daemon.mem.close()

    async def test_lost_reflection_degrades_without_failing_the_turn(self, chat_daemon):
        await chat_daemon.mem.init()
        try:
            with _reflection_writes_fail(chat_daemon.mem.db_path):
                result = await run_chat_through_runtime_contract(
                    chat_daemon,
                    "remember this",
                    _stub_respond,
                )

            # The turn itself genuinely happened and says so.
            assert result.governance_allowed is True
            assert (
                result.response is not None and "remember this" in result.response
            ), "a produced response must not be reported as failed"
            assert result.working_memory_item_id is not None, "the turn's real effects stand"

            # The lost provenance record is caller-visible.
            assert result.provenance_degraded is True
            assert "chat" in result.provenance_error

            # And really lost -- the degradation is not cosmetic.
            assert _count_reflections(chat_daemon.mem.db_path) == 0
        finally:
            await chat_daemon.mem.close()

    async def test_the_turn_is_not_retried_for_a_lost_reflection(self, chat_daemon):
        """One turn, one respond_fn call, one Working Memory item -- even
        when the provenance write fails."""
        await chat_daemon.mem.init()
        calls = []

        async def counting_respond(prompt: str) -> str:
            calls.append(prompt)
            return "ok"

        try:
            with _reflection_writes_fail(chat_daemon.mem.db_path):
                await run_chat_through_runtime_contract(
                    chat_daemon,
                    "once only",
                    counting_respond,
                )
            assert len(calls) == 1, "a lost provenance write must never re-run the turn"
            assert len(chat_daemon.working_memory.get_all()) == 1
        finally:
            await chat_daemon.mem.close()

    async def test_governance_denied_is_fail_closed_not_provenance_degraded(self, chat_daemon):
        """A pre-action denial executes nothing, and is reported as a
        denial -- never dressed up as a degraded success."""
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        await chat_daemon.mem.init()
        try:
            GovernanceStore(chat_daemon.mem.db_path).engage("global")

            result = await run_chat_through_runtime_contract(
                chat_daemon,
                "blocked",
                _stub_respond,
            )

            assert result.governance_allowed is False
            assert result.response is None, "a denied turn must not execute"
            # The denial reflection persisted fine, so nothing is degraded --
            # the denial is the whole truthful story.
            assert result.provenance_degraded is False
        finally:
            await chat_daemon.mem.close()


# ---------------------------------------------------------------------------
# Training surface (provenance-bearing)
# ---------------------------------------------------------------------------


class _FakeExperience:
    def get_active_goals(self):
        return []


class _FakePersona:
    def get_active_pack_id(self):
        return "default"


class _FakeWorkingMemory:
    def get_context_string(self):
        return ""


class _FakeDaemon:
    """Minimal duck-typed daemon (mirrors test_training_runtime_contract_seam.py)."""

    def __init__(self, mem: MemoryStore):
        self.mem = mem
        self.experience = _FakeExperience()
        self.persona_manager = _FakePersona()
        self.working_memory = _FakeWorkingMemory()


def _training_submission() -> training.TrainingSubmission:
    return training.TrainingSubmission(
        competency_id="estate_management",
        source_type="user_instruction",
        source_detail="user stated this in conversation",
        records=[
            CompetencyHeuristic(
                envelope=CompetencyEnvelope(competency_id="estate_management"),
                slug="check_warranty_before_replace",
                rule="Check warranty before recommending replacement",
                conditions="Any repair-vs-replace decision",
            ),
        ],
    )


@pytest.mark.asyncio
class TestTrainingProvenance:
    async def test_healthy_submission_is_not_degraded(self, tmp_path):
        mem = MemoryStore(str(tmp_path / "training.db"))
        await mem.init()
        try:
            result = await run_training_through_runtime_contract(
                _FakeDaemon(mem),
                _training_submission(),
            )
            assert result.governance_allowed is True
            assert result.stored_count == 1
            assert result.provenance_degraded is False
            assert result.to_dict()["provenance_degraded"] is False
        finally:
            await mem.close()

    async def test_lost_supersession_provenance_degrades_without_unstoring(self, tmp_path):
        db = str(tmp_path / "training.db")
        mem = MemoryStore(db)
        await mem.init()
        try:
            with _reflection_writes_fail(db):
                result = await run_training_through_runtime_contract(
                    _FakeDaemon(mem),
                    _training_submission(),
                )

            # The record writes genuinely happened and are reported as such.
            assert result.governance_allowed is True
            assert (
                result.stored_count == 1
            ), "stored records must not be reported unstored for a lost reflection"
            stored = await mem.get_memory(
                "competency_heuristic",
                "estate_management.check_warranty_before_replace",
            )
            assert stored is not None, "the durable store write really happened"

            # The lost provenance record is caller-visible, including over HTTP
            # (the route returns to_dict() verbatim).
            assert result.provenance_degraded is True
            assert "training" in result.provenance_error
            payload = result.to_dict()
            assert payload["provenance_degraded"] is True
            assert "training" in payload["provenance_error"]
        finally:
            await mem.close()

    async def test_the_submission_is_not_retried_for_a_lost_reflection(self, tmp_path):
        """Exactly one durable row for the record -- no re-ingestion."""
        db = str(tmp_path / "training.db")
        mem = MemoryStore(db)
        await mem.init()
        try:
            with _reflection_writes_fail(db):
                await run_training_through_runtime_contract(
                    _FakeDaemon(mem),
                    _training_submission(),
                )
            conn = sqlite3.connect(db)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE kind = ? AND key = ?",
                    ("competency_heuristic", "estate_management.check_warranty_before_replace"),
                ).fetchone()[0]
            finally:
                conn.close()
            assert count == 1
        finally:
            await mem.close()

    async def test_brake_blocked_submission_is_fail_closed_not_degraded(self, tmp_path):
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        db = str(tmp_path / "training.db")
        mem = MemoryStore(db)
        await mem.init()
        try:
            GovernanceStore(db).engage("training")

            result = await run_training_through_runtime_contract(
                _FakeDaemon(mem),
                _training_submission(),
            )

            assert result.governance_allowed is False
            assert result.stored_count == 0, "a blocked submission must write nothing"
            # The blocked reflection persisted fine: no degradation to report.
            assert result.provenance_degraded is False
        finally:
            await mem.close()


# ---------------------------------------------------------------------------
# Device surfaces (provenance-bearing)
# ---------------------------------------------------------------------------


def _approve_everything(_prompt: str) -> bool:
    return True


@pytest.mark.asyncio
class TestDeviceProvenance:
    @pytest.mark.parametrize(
        ("seam", "cap_kw"),
        [
            (run_sight_through_runtime_contract, "capture_fn"),
            (run_voice_through_runtime_contract, "stream_fn"),
        ],
        ids=["sight", "voice"],
    )
    async def test_lost_sole_record_degrades_without_unstarting(self, tmp_path, seam, cap_kw):
        db = str(tmp_path / "device.db")
        mem = MemoryStore(db)
        await mem.init()
        await mem.close()
        set_consent_handler(_approve_everything)

        calls = []

        def capability():
            calls.append("start")
            return "started-handle"

        # Healthy first: started, fully recorded.
        healthy = await seam(
            db_path=db,
            identity_context=ALLOW_CONTEXT,
            **{cap_kw: capability},
        )
        assert healthy.started is True
        assert healthy.provenance_degraded is False

        # Now the sole record is lost: still started, still exactly one more
        # capability call (no retry), and the loss is on the result.
        with _reflection_writes_fail(db):
            degraded = await seam(
                db_path=db,
                identity_context=ALLOW_CONTEXT,
                **{cap_kw: capability},
            )

        assert (
            degraded.started is True
        ), "a capability that genuinely started must not be reported as failed"
        assert degraded.outcome == "started"
        assert degraded.provenance_degraded is True
        assert "injected reflection failure" in degraded.provenance_error
        assert calls == ["start", "start"], "no retry for a lost provenance record"

    async def test_brake_denied_start_is_fail_closed_not_degraded(self, tmp_path):
        # The device seams still check the LEGACY ParkingBrake/BrakeStorage
        # authority -- the documented, still-open C6 split (Band B
        # prerequisite; see RISKS.md "Parking Brake read/write authority is
        # split"). Engage through that authority, as the existing
        # voice/sight seam tests do.
        from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

        db = str(tmp_path / "device.db")
        mem = MemoryStore(db)
        await mem.init()
        await mem.close()
        set_consent_handler(_approve_everything)
        ParkingBrake(BrakeStorage(db)).engage("sight")

        calls = []

        def capability():
            calls.append("start")

        result = await run_sight_through_runtime_contract(
            db_path=db,
            identity_context=ALLOW_CONTEXT,
            capture_fn=capability,
        )

        assert result.started is False
        assert result.governance_allowed is False
        assert calls == [], "a denied start must not execute"
        # The denial reflection persisted: the denial is the whole story.
        assert result.provenance_degraded is False


# ---------------------------------------------------------------------------
# Additive surfaces: unchanged semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAdditiveSurfacesUnchanged:
    async def test_skill_action_ignores_a_lost_reflection(self, tmp_path):
        """The skill surface's required record is `skill_action_audit`
        (WP-A2). A lost *reflection* -- the additive stream -- must not
        degrade the action, and `SkillResult` carries no provenance fields
        at all."""
        db = str(tmp_path / "skill.db")
        mem = MemoryStore(db)
        await mem.init()
        try:
            registry = SkillRegistry(db_path=db, memory_store=mem)
            assert await registry.load_skill("tasks") is True

            with _reflection_writes_fail(db):
                result = await registry.execute_action(
                    "tasks",
                    "create",
                    {"title": "additive stays additive"},
                )

            assert result.success is True
            assert (
                result.audit_degraded is False
            ), "a lost additive reflection must not mark the action degraded"
            assert result.fully_successful is True
            assert not hasattr(result, "provenance_degraded")

            # The required record persisted normally.
            conn = sqlite3.connect(db)
            try:
                audits = conn.execute(
                    "SELECT COUNT(*) FROM skill_action_audit WHERE status = 'success'",
                ).fetchone()[0]
            finally:
                conn.close()
            assert audits == 1
        finally:
            await mem.close()

    async def test_drive_and_awaiting_reflection_tails_never_raise(self, tmp_path):
        """The scheduler and awaiting_response tails call the same sink and
        deliberately ignore its outcome -- a failing sink must stay
        invisible to them (their own durable records are elsewhere)."""
        from bartholomew.kernel import runtime_contract as rc

        db = str(tmp_path / "drive.db")
        mem = MemoryStore(db)
        await mem.init()
        try:

            class _Ctx:
                pass

            ctx = _Ctx()
            ctx.mem = mem

            observation = rc.Observation(source="scheduler", raw_content="self_check")
            interpretation = rc.Interpretation(observation=observation, prompt="self_check")
            candidate = rc.CandidateAction(kind="self_check", interpretation=interpretation)

            with _reflection_writes_fail(db):
                await rc._record_drive_reflection(ctx, candidate, "completed")
                await rc._record_awaiting_response_reflection(
                    ctx,
                    candidate,
                    "opened",
                    None,
                    None,
                )
        finally:
            await mem.close()
