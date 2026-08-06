"""
Tests for MASTER_PLAN.md-style Runtime Contract convergence, Stage 1 S1.4:
bartholomew.kernel.runtime_contract.run_awaiting_response_through_runtime_contract()
and its chat-side auto-resolution hook. See
docs/S1_4_AWAITING_RESPONSE_DESIGN.md for the design this implements, and
tests/test_awaiting_response_store.py for the isolated store-level tests
this seam builds on.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel.awaiting_response_store import (
    AwaitingResponseNotFoundError,
    AwaitingResponseStore,
    InvalidTransitionError,
)
from bartholomew.kernel.reflection import REFLECTION_KIND
from bartholomew.kernel.runtime_contract import (
    run_awaiting_response_through_runtime_contract,
    run_chat_through_runtime_contract,
)
from identity_interpreter.identity_context import IdentityContext

# Identity.yaml's real S1.4 additions, reproduced exactly.
REAL_PROD_ALLOW_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=[
        "awaiting_response_open",
        "awaiting_response_remind",
        "awaiting_response_escalate",
        "awaiting_response_resolve",
    ],
)
DENY_CONTEXT = IdentityContext(tool_use_default_allowed=False, tool_use_allowlist=[])
ALLOW_CONTEXT = IdentityContext(tool_use_default_allowed=True, tool_use_allowlist=[])


@pytest.fixture
def mock_config_files(tmp_path):
    """Mirrors test_scheduler_drive_convergence.py's fixture of the same name."""
    cfg_path = tmp_path / "kernel.yaml"
    cfg_path.write_text(
        """
timezone: "Australia/Brisbane"
loop_interval_seconds: 1
quiet_hours:
  start: "23:00"
  end: "06:00"
dreaming:
  nightly_window: "21:00-23:00"
  weekly:
    weekday: "Sun"
    time: "21:30"
""",
    )
    persona_path = tmp_path / "persona.yaml"
    persona_path.write_text('name: "Test Bartholomew"\n')
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("policies: []\n")
    drives_path = tmp_path / "drives.yaml"
    drives_path.write_text("drives: []\n")
    db_path = tmp_path / "test.db"

    return {
        "cfg_path": str(cfg_path),
        "db_path": str(db_path),
        "persona_path": str(persona_path),
        "policy_path": str(policy_path),
        "drives_path": str(drives_path),
    }


@pytest.fixture
def daemon(mock_config_files):
    from bartholomew.kernel.daemon import KernelDaemon

    return KernelDaemon(**mock_config_files)


async def _ready(daemon):
    """Prepares a not-start()ed daemon fixture for these tests: ensures
    MemoryStore's schema (for the reflection sink) and wires an
    AwaitingResponseStore the way KernelDaemon.start() normally would."""
    await daemon.mem.init()
    daemon.awaiting_response_store = AwaitingResponseStore(daemon.mem.db_path)
    return daemon


async def _noop_respond(prompt: str) -> str:
    return "a reply"


@pytest.mark.asyncio
class TestOpenTransition:
    async def test_open_creates_entry_when_no_identity_context(self, daemon):
        daemon = await _ready(daemon)
        result = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="reply about the trip",
            origin_surface="chat",
            actor="user",
        )
        assert result.governance_allowed is True
        assert result.outcome == "opened"
        assert result.entry.subject == "reply about the trip"
        assert result.entry.status == "open"

    async def test_open_denied_by_restrictive_identity_policy(self, daemon):
        daemon = await _ready(daemon)
        daemon.identity_context = DENY_CONTEXT

        result = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="x",
            origin_surface="chat",
        )
        assert result.governance_allowed is False
        assert result.outcome == "governance_denied"
        assert result.entry is None

    async def test_open_allowed_once_allowlisted_matching_real_prod_config(self, daemon):
        daemon = await _ready(daemon)
        daemon.identity_context = REAL_PROD_ALLOW_CONTEXT

        result = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="x",
            origin_surface="chat",
        )
        assert result.governance_allowed is True
        assert result.entry.status == "open"

    async def test_open_allowed_by_permissive_policy(self, daemon):
        daemon = await _ready(daemon)
        daemon.identity_context = ALLOW_CONTEXT

        result = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="x",
            origin_surface="chat",
        )
        assert result.governance_allowed is True

    async def test_open_without_subject_raises(self, daemon):
        daemon = await _ready(daemon)
        with pytest.raises(ValueError):
            await run_awaiting_response_through_runtime_contract(
                daemon,
                "open",
                origin_surface="chat",
            )


@pytest.mark.asyncio
class TestRemindEscalateResolveTransitions:
    async def test_remind_transitions_and_survives_best_effort_notify_failure(self, daemon):
        daemon = await _ready(daemon)
        opened = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="x",
            origin_surface="chat",
        )

        # No skill loaded (start() was never called) -- proves delivery is
        # genuinely best-effort and never blocks the state transition itself.
        result = await run_awaiting_response_through_runtime_contract(
            daemon,
            "remind",
            entry_id=opened.entry.id,
        )
        assert result.governance_allowed is True
        assert result.outcome == "reminded"
        assert result.entry.reminder_count == 1

    async def test_escalate_transitions(self, daemon):
        daemon = await _ready(daemon)
        opened = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="x",
            origin_surface="chat",
        )
        result = await run_awaiting_response_through_runtime_contract(
            daemon,
            "escalate",
            entry_id=opened.entry.id,
        )
        assert result.outcome == "escalated"
        assert result.entry.status == "escalated"

    async def test_resolve_transitions(self, daemon):
        daemon = await _ready(daemon)
        opened = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="x",
            origin_surface="chat",
        )
        result = await run_awaiting_response_through_runtime_contract(
            daemon,
            "resolve",
            entry_id=opened.entry.id,
            resolution="user_dismissed",
        )
        assert result.outcome == "resolved"
        assert result.entry.resolution == "user_dismissed"

    async def test_remind_denied_by_restrictive_policy_leaves_entry_unchanged(self, daemon):
        daemon = await _ready(daemon)
        opened = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="x",
            origin_surface="chat",
        )
        daemon.identity_context = DENY_CONTEXT

        result = await run_awaiting_response_through_runtime_contract(
            daemon,
            "remind",
            entry_id=opened.entry.id,
        )
        assert result.governance_allowed is False
        assert result.outcome == "governance_denied"

        stored = daemon.awaiting_response_store.get(opened.entry.id)
        assert stored.status == "open"
        assert stored.reminder_count == 0


@pytest.mark.asyncio
class TestParkingBrakeDeniesEveryTransition:
    async def test_engaged_skills_brake_denies_open(self, daemon):
        daemon = await _ready(daemon)
        from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

        brake = ParkingBrake(BrakeStorage(daemon.mem.db_path))
        brake.engage("skills")
        try:
            result = await run_awaiting_response_through_runtime_contract(
                daemon,
                "open",
                subject="x",
                origin_surface="chat",
            )
            assert result.governance_allowed is False
            assert result.outcome == "parking_brake_denied"
        finally:
            brake.disengage()


@pytest.mark.asyncio
class TestCallerInputErrorsPropagate:
    """Unlike a governance denial, a bad entry_id or an already-resolved
    entry is the caller's mistake -- the seam raises rather than folding it
    into `outcome`."""

    async def test_remind_unknown_entry_raises(self, daemon):
        daemon = await _ready(daemon)
        with pytest.raises(AwaitingResponseNotFoundError):
            await run_awaiting_response_through_runtime_contract(daemon, "remind", entry_id=999)

    async def test_transition_after_resolve_raises(self, daemon):
        daemon = await _ready(daemon)
        opened = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="x",
            origin_surface="chat",
        )
        await run_awaiting_response_through_runtime_contract(
            daemon,
            "resolve",
            entry_id=opened.entry.id,
            resolution="manual",
        )
        with pytest.raises(InvalidTransitionError):
            await run_awaiting_response_through_runtime_contract(
                daemon,
                "remind",
                entry_id=opened.entry.id,
            )


@pytest.mark.asyncio
class TestReflectionRecordedIntoSharedSink:
    async def test_opened_entry_records_a_reflection(self, daemon):
        daemon = await _ready(daemon)
        result = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="x",
            origin_surface="chat",
        )
        row = await daemon.mem.latest_reflection(REFLECTION_KIND)
        assert row is not None
        assert row["meta"]["surface"] == "awaiting_response"
        assert row["meta"]["action"] == "awaiting_response_open"
        assert row["meta"]["outcome"] == "opened"
        assert row["meta"]["entry_id"] == result.entry.id

    async def test_denied_transition_records_a_reflection_with_reason(self, daemon):
        daemon = await _ready(daemon)
        daemon.identity_context = DENY_CONTEXT

        await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="x",
            origin_surface="chat",
        )
        row = await daemon.mem.latest_reflection(REFLECTION_KIND)
        assert row is not None
        assert row["meta"]["outcome"] == "governance_denied"
        assert row["meta"]["reason"]


@pytest.mark.asyncio
class TestChatAutoResolve:
    """Design doc Sec 7's narrow MVP resolution-on-reply path."""

    async def test_single_open_chat_entry_auto_resolves_on_next_reply(self, daemon):
        daemon = await _ready(daemon)
        opened = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="waiting on your answer",
            origin_surface="chat",
        )

        await run_chat_through_runtime_contract(daemon, "here is my reply", _noop_respond)

        resolved = daemon.awaiting_response_store.get(opened.entry.id)
        assert resolved.status == "resolved"
        assert resolved.resolution == "reply_received"

    async def test_ambiguous_multiple_open_entries_stays_open(self, daemon):
        """ "never guesses" (design doc's non-negotiable invariants)."""
        daemon = await _ready(daemon)
        first = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="first",
            origin_surface="chat",
        )
        second = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="second",
            origin_surface="chat",
        )

        await run_chat_through_runtime_contract(daemon, "a reply", _noop_respond)

        assert daemon.awaiting_response_store.get(first.entry.id).status == "open"
        assert daemon.awaiting_response_store.get(second.entry.id).status == "open"

    async def test_no_open_entries_is_a_no_op_and_chat_still_succeeds(self, daemon):
        daemon = await _ready(daemon)
        result = await run_chat_through_runtime_contract(daemon, "hi", _noop_respond)
        assert result.governance_allowed is True

    async def test_a_later_unrelated_reply_does_not_resolve_a_stale_entry(self, daemon):
        """PR #38 review finding: without an adjacency check, a chat turn
        arbitrarily later than the entry's opening -- not the entry's own
        actual reply -- could still resolve it, as long as it happened to
        be the sole open entry at that moment. Reproduces the exact
        mechanism: two entries open (so the first reply resolves neither,
        per the ambiguous-match test above), one gets resolved manually
        (not via chat), leaving exactly one open entry whose real reply
        already happened turns ago -- the next chat turn must NOT resolve
        it, since it demonstrably isn't the entry's own reply."""
        daemon = await _ready(daemon)
        stale = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="stale entry -- already replied to, long ago",
            origin_surface="chat",
        )
        other = await run_awaiting_response_through_runtime_contract(
            daemon,
            "open",
            subject="other entry",
            origin_surface="chat",
        )

        # This is "stale"'s real reply -- but auto-resolve is skipped here
        # because two entries are open (ambiguous).
        await run_chat_through_runtime_contract(daemon, "the actual reply", _noop_respond)
        assert daemon.awaiting_response_store.get(stale.entry.id).status == "open"

        # Resolve "other" out-of-band (not via chat), leaving "stale" as the
        # sole open entry -- but its own reply already happened above.
        await run_awaiting_response_through_runtime_contract(
            daemon,
            "resolve",
            entry_id=other.entry.id,
            resolution="manual",
        )

        # An unrelated later message must not resolve "stale".
        await run_chat_through_runtime_contract(daemon, "hello, unrelated", _noop_respond)

        assert daemon.awaiting_response_store.get(stale.entry.id).status == "open"

    async def test_chat_unaffected_when_no_store_wired(self, mock_config_files):
        """A daemon that never went through S1.4's start() wiring (the
        pre-existing shape every other chat seam test uses) must see no
        behavior change -- additive only."""
        from bartholomew.kernel.daemon import KernelDaemon

        daemon = KernelDaemon(**mock_config_files)
        await daemon.mem.init()
        assert daemon.awaiting_response_store is None

        result = await run_chat_through_runtime_contract(daemon, "hi", _noop_respond)
        assert result.governance_allowed is True
