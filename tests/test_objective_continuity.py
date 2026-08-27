"""
Golden Path slice 2 -- Objective Continuity, end to end.

This is the slice's acceptance suite, and the bar is behavioural rather than
architectural. `TestTheGoldenPath` walks the whole promise in one test:

    the user establishes an objective in one ordinary sentence -> it is
    persisted -> the process that heard it goes away entirely -> evidence
    arrives -> Bartholomew raises it unprompted, saying what has changed
    since it last mentioned it -> the user says it's done -> it never comes
    back.

The strategic test the slice was specified against is:

    «The user should not have to remember the objective for Bartholomew.»
    «Once the objective is complete, the user should not have to tell
      Bartholomew to stop remembering it.»

so the two halves that matter most are the restart (the objective survives
the process that heard it) and the silence after completion (three
independent stops, each asserted separately below, because one filter someone
later forgets is not enough).

Real components throughout: a real MemoryStore, a real SkillRegistry running
the real NotifySkill, a real SQLite nudge queue, a real loopback HTTP
endpoint. Same "not a mock" posture as the two slices before it, for the same
reason -- a mocked delivery proves only that the code called itself.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bartholomew.kernel import objective_intents, objective_store
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.objective_store import (
    EVENT_ACTION,
    EVENT_DECISION,
    EVENT_FACT,
    EVENT_PROPOSAL,
    ObjectiveStore,
)
from bartholomew.kernel.runtime_contract import (
    run_objective_through_runtime_contract,
)
from bartholomew.kernel.scheduler import drives as drives_module
from bartholomew.kernel.scheduler import persistence as sp
from bartholomew.kernel.scheduler.drives import (
    OBJECTIVE_CONTINUITY_DRIVE,
    drive_objective_continuity_check,
    resolve_registry,
)
from bartholomew.kernel.scheduler.store import SchedulerStore
from bartholomew.kernel.skill_registry import SkillRegistry
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from bartholomew.skills.notify import WEBHOOK_URL_ENV
from identity_interpreter.identity_context import IdentityContext

OBJECTIVE_KINDS = [
    "objective_open",
    "objective_record",
    "objective_surface",
    "objective_block",
    "objective_unblock",
    "objective_complete",
    "objective_abandon",
]

#: Identity.yaml's real allowlist, as amended by this slice.
REAL_ALLOW_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=[
        "web_fetch",
        "browser_action",
        "notify",
        OBJECTIVE_CONTINUITY_DRIVE,
        *OBJECTIVE_KINDS,
    ],
)
#: The shape Identity.yaml had BEFORE this slice.
PRE_SLICE_DENY_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["web_fetch", "browser_action", "notify"],
)


class _Recorder(BaseHTTPRequestHandler):
    received: list[dict] = []
    status = 200

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's required name
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")
        type(self).received.append({"path": self.path, "body": body})
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


@pytest.fixture
def webhook_server():
    _Recorder.received = []
    _Recorder.status = 200
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, _Recorder
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _url(server) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}/objectives"


class _Ctx:
    """A context with exactly the attributes the seam and the drive read.

    Duck-typed rather than a KernelDaemon, following the precedent the
    scheduler suites set: a full daemon would drag its background loops into
    every case here.
    """

    def __init__(
        self,
        mem,
        objective_store_,
        scheduler_store=None,
        skill_registry=None,
        cfg=None,
        identity=REAL_ALLOW_CONTEXT,
        governance_store=None,
    ):
        self.mem = mem
        self.objective_store = objective_store_
        self.scheduler_store = scheduler_store
        self.skill_registry = skill_registry
        self.cfg = cfg if cfg is not None else {"proactive": {"objective_continuity": True}}
        self.tz = timezone.utc
        self.identity_context = identity
        self.blocking_executor = None
        self.governance_store = governance_store


@pytest.fixture
async def mem(tmp_path):
    store = MemoryStore(str(tmp_path / "objectives_e2e.db"))
    await store.init()
    yield store
    await store.close()


@pytest.fixture
def objectives(mem):
    return ObjectiveStore(mem.db_path)


@pytest.fixture
async def scheduler_store(mem):
    sp.ensure_schema(mem.db_path)
    store = SchedulerStore(mem.db_path)
    yield store
    await store.close()


async def _registry(mem, monkeypatch, webhook_url, identity=REAL_ALLOW_CONTEXT):
    monkeypatch.setenv(WEBHOOK_URL_ENV, webhook_url)
    registry = SkillRegistry(db_path=mem.db_path, memory_store=mem, identity_context=identity)
    await registry.load_skill("notify")
    # Pin suppression, for the reason test_schedule_reminder_drive.py records:
    # NotifySkill's default quiet hours are read from the wall clock, so an
    # unpinned delivery assertion silently depends on what time the suite runs.
    notify = registry._loaded["notify"].instance
    notify._quiet_hours_start = "00:00"
    notify._quiet_hours_end = "00:00"
    notify._muted = False
    notify._muted_until = None
    assert not notify._is_quiet_hours(), "quiet hours were not successfully pinned off"
    assert not notify._is_muted(), "mute was not successfully pinned off"
    return registry


def _age_objective(store, objective_id: int, days: int) -> None:
    """Move an objective's clock into the past.

    Reaches the row directly, and only to move time -- the alternative is a
    test that has to wait three real days. Everything the assertions are
    about still goes through the governed path.
    """
    past = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = store._connect()
    try:
        conn.execute(
            "UPDATE objectives SET opened_at = ?, last_surfaced_at = "
            "CASE WHEN last_surfaced_at IS NULL THEN NULL ELSE ? END WHERE id = ?",
            (past, past, objective_id),
        )
        conn.commit()
    finally:
        conn.close()


def _delivered_bodies(recorder) -> list[str]:
    return [r["body"] for r in recorder.received]


@pytest.mark.asyncio
class TestTheGoldenPath:
    async def test_the_whole_promise_in_one_scenario(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """Establish -> persist -> restart -> evidence -> resurface with
        continuity -> complete -> permanent silence."""
        server, recorder = webhook_server
        registry = await _registry(mem, monkeypatch, _url(server))

        # --- 1. The user establishes an objective, once. ------------------
        establishing = ObjectiveStore(mem.db_path)
        ctx = _Ctx(mem, establishing, scheduler_store, registry)
        intent = objective_intents.parse_intent(
            "The roofer needs to come this week",
            date.today(),
        )
        assert intent is not None and intent.action == objective_intents.INTENT_OPEN

        opened = await run_objective_through_runtime_contract(
            ctx,
            "open",
            title=intent.title,
            outcome_statement=intent.outcome_statement,
            horizon_kind=intent.horizon_kind,
            horizon_date=intent.horizon_date,
            actor="chat",
        )
        assert opened.outcome == "opened"
        objective_id = opened.objective.id

        # --- 2. The process that heard it goes away entirely. -------------
        # A wholly separate store object over the same database, as a
        # restarted process constructs. Nothing is carried across in memory.
        del establishing, ctx
        after_restart = ObjectiveStore(mem.db_path)
        ctx = _Ctx(mem, after_restart, scheduler_store, registry)

        reloaded = after_restart.get(objective_id)
        assert reloaded is not None, "the objective did not survive the restart"
        assert "roofer" in reloaded.title
        assert reloaded.status == objective_store.STATUS_ACTIVE

        # --- 3. Things happen. Each classified for what it is. ------------
        await run_objective_through_runtime_contract(
            ctx,
            "record",
            objective_id=objective_id,
            event_kind=EVENT_DECISION,
            summary="going with the second quote",
            actor="chat",
        )
        await run_objective_through_runtime_contract(
            ctx,
            "record",
            objective_id=objective_id,
            event_kind=EVENT_FACT,
            summary="rain likely Thursday",
            provenance={
                "source_kind": "external_capability",
                "provider_host": "api.open-meteo.com",
                "evidence": True,
            },
            actor="chat:forecast",
        )
        # A considered next step. Recorded, and never usable as evidence.
        await run_objective_through_runtime_contract(
            ctx,
            "record",
            objective_id=objective_id,
            event_kind=EVENT_PROPOSAL,
            summary="could ask them to move to Wednesday",
            actor="chat",
        )

        # --- 4. Time passes, and Bartholomew raises it unprompted. --------
        _age_objective(after_restart, objective_id, days=5)

        result = await drive_objective_continuity_check(ctx)
        assert result is None  # the drive's contract

        assert len(recorder.received) == 1, "expected exactly one re-engagement"
        body = _delivered_bodies(recorder)[0]

        # It carries continuity: what it is, and what has happened.
        assert "roofer" in body
        assert "going with the second quote" in body
        assert "rain likely Thursday" in body
        # External content is attributed, never asserted as Bartholomew's
        # own knowledge.
        assert "api.open-meteo.com" in body
        # The proposal is NOT presented as something that happened.
        assert "could ask them to move to Wednesday" not in body

        # It recorded that it raised it, so the next window is honest.
        surfaced = after_restart.get(objective_id)
        assert surfaced.last_surfaced_at is not None
        assert surfaced.last_surfaced_event_id is not None

        # --- 5. It does not nag. -----------------------------------------
        await drive_objective_continuity_check(ctx)
        assert len(recorder.received) == 1, "the same objective was raised twice"

        # --- 6. The user says it's done. ---------------------------------
        closing = objective_intents.parse_intent("the roofer is sorted", date.today())
        assert closing is not None
        assert closing.action == objective_intents.INTENT_COMPLETE

        matched = objective_intents.match_objective(
            closing.subject,
            after_restart.list_live(),
        ) or after_restart.get(objective_id)
        completed = await run_objective_through_runtime_contract(
            ctx,
            "complete",
            objective_id=matched.id,
            resolution=objective_store.RESOLUTION_ACHIEVED,
            outcome_note="the roofer is sorted",
            actor="chat",
        )
        assert completed.outcome == "completed"
        assert completed.objective.resolution == objective_store.RESOLUTION_ACHIEVED
        assert completed.objective.outcome_note == "the roofer is sorted"

        # --- 7. And it never comes back. ---------------------------------
        # Not "not for a while" -- ever. Aged well past every interval.
        _age_objective(after_restart, objective_id, days=400)
        recorder.received.clear()
        for _ in range(5):
            await drive_objective_continuity_check(ctx)
        assert recorder.received == [], "a completed objective was resurfaced"

        # And a wholly fresh process agrees.
        fresh = ObjectiveStore(mem.db_path)
        assert fresh.list_live() == []
        assert fresh.get(objective_id).status == objective_store.STATUS_COMPLETED


@pytest.mark.asyncio
class TestCompletionIsThreeIndependentStops:
    """A completed objective that keeps resurfacing is the worst outcome
    this slice can produce, so it is stopped in three unrelated places and
    each is asserted on its own."""

    async def test_stop_one_the_store_refuses_every_transition(self, mem, objectives):
        from bartholomew.kernel.objective_store import InvalidTransitionError

        objective = objectives.open(title="get the roof repaired")
        objectives.complete(objective.id)
        with pytest.raises(InvalidTransitionError):
            objectives.surface(objective.id)

    async def test_stop_two_the_drive_cannot_see_it(
        self,
        mem,
        objectives,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, objectives, scheduler_store, registry)

        objective = objectives.open(title="get the roof repaired")
        objectives.complete(objective.id)
        _age_objective(objectives, objective.id, days=400)

        await drive_objective_continuity_check(ctx)
        assert recorder.received == []

    async def test_stop_three_the_prompt_block_cannot_mention_it(self, mem, objectives):
        from bartholomew.kernel.runtime_contract import render_objectives_for_prompt

        objective = objectives.open(title="get the roof repaired")
        objectives.complete(objective.id)
        assert render_objectives_for_prompt(objectives.list_live()) == ""

    async def test_abandonment_is_just_as_permanent(
        self,
        mem,
        objectives,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, objectives, scheduler_store, registry)

        objective = objectives.open(title="repaint the fence")
        objectives.abandon(objective.id)
        _age_objective(objectives, objective.id, days=400)

        await drive_objective_continuity_check(ctx)
        assert recorder.received == []


@pytest.mark.asyncio
class TestGovernance:
    async def test_an_engaged_brake_records_nothing(self, mem, objectives):
        """Objective state is governed state. A braked Bartholomew does not
        quietly keep bookkeeping."""
        governance = GovernanceStore(mem.db_path)
        governance.engage("skills", reason="test", actor="test")
        ctx = _Ctx(mem, objectives, governance_store=governance)

        result = await run_objective_through_runtime_contract(
            ctx,
            "open",
            title="get the roof repaired",
        )
        assert result.governance_allowed is False
        assert result.outcome == "parking_brake_denied"
        assert objectives.list() == [], "an objective was written despite the brake"

    async def test_an_engaged_brake_raises_nothing(
        self,
        mem,
        objectives,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        registry = await _registry(mem, monkeypatch, _url(server))
        objective = objectives.open(title="get the roof repaired")
        _age_objective(objectives, objective.id, days=10)

        governance = GovernanceStore(mem.db_path)
        governance.engage("skills", reason="test", actor="test")
        ctx = _Ctx(mem, objectives, scheduler_store, registry, governance_store=governance)

        await drive_objective_continuity_check(ctx)
        assert recorder.received == []
        assert objectives.get(objective.id).last_surfaced_at is None

    async def test_identity_policy_denial_writes_nothing(self, mem, objectives):
        """The shape Identity.yaml had before this slice: every transition
        denied at the Identity gate, and no state change."""
        ctx = _Ctx(mem, objectives, identity=PRE_SLICE_DENY_CONTEXT)
        result = await run_objective_through_runtime_contract(
            ctx,
            "open",
            title="get the roof repaired",
        )
        assert result.governance_allowed is False
        assert result.outcome == "governance_denied"
        assert "Identity policy" in result.reason
        assert objectives.list() == []

    async def test_every_transition_kind_is_governed_at_its_own_grain(self, mem, objectives):
        """Each transition is evaluated separately, so allowing Bartholomew
        to record an objective is not the same as allowing it to close one."""
        record_only = IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=["objective_open"],
        )
        ctx = _Ctx(mem, objectives, identity=record_only)

        opened = await run_objective_through_runtime_contract(
            ctx,
            "open",
            title="get the roof repaired",
        )
        assert opened.governance_allowed is True

        completed = await run_objective_through_runtime_contract(
            ctx,
            "complete",
            objective_id=opened.objective.id,
        )
        assert completed.governance_allowed is False
        assert objectives.get(opened.objective.id).status == objective_store.STATUS_ACTIVE

    async def test_the_seam_reaches_no_external_provider_and_sends_nothing(
        self,
        mem,
        objectives,
        monkeypatch,
        webhook_server,
    ):
        """An objective existing authorises nothing.

        Recording, updating and completing an objective contacts nobody. The
        only outbound act anywhere in this slice is the re-engagement
        notification, which is the drive's, gated separately, and off by
        default."""
        server, recorder = webhook_server
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, objectives, skill_registry=registry)

        opened = await run_objective_through_runtime_contract(
            ctx,
            "open",
            title="get the roof repaired",
        )
        await run_objective_through_runtime_contract(
            ctx,
            "record",
            objective_id=opened.objective.id,
            event_kind=EVENT_ACTION,
            summary="rang the roofer",
        )
        await run_objective_through_runtime_contract(
            ctx,
            "complete",
            objective_id=opened.objective.id,
        )
        assert recorder.received == [], "the objective seam contacted something"

    async def test_a_reflection_is_written_for_every_transition(self, mem, objectives):
        """Into the one shared sink every other surface writes to -- not a
        bespoke audit mechanism competing with it."""
        from bartholomew.kernel.reflection import REFLECTION_KIND

        ctx = _Ctx(mem, objectives)
        await run_objective_through_runtime_contract(
            ctx,
            "open",
            title="get the roof repaired",
        )
        latest = await mem.latest_reflection(REFLECTION_KIND)
        assert latest is not None, "no Reflection was written for the objective transition"
        meta = latest["meta"] or {}
        assert meta.get("surface") == "objective"
        assert meta.get("action") == "objective_open"
        assert meta.get("outcome") == "opened"


@pytest.mark.asyncio
class TestDefaultOff:
    async def test_the_drive_is_not_registered_by_default(self, mem, objectives):
        """Off must mean zero ticks, not a registered drive that decides to
        do nothing."""
        ctx = _Ctx(mem, objectives, cfg={})
        assert OBJECTIVE_CONTINUITY_DRIVE not in resolve_registry(ctx)

    async def test_the_flag_is_the_only_authority(self, mem, objectives):
        ctx = _Ctx(mem, objectives, cfg={"proactive": {"objective_continuity": True}})
        assert OBJECTIVE_CONTINUITY_DRIVE in resolve_registry(ctx)

    async def test_it_is_absent_from_the_always_on_registry(self):
        assert OBJECTIVE_CONTINUITY_DRIVE not in drives_module.REGISTRY
        assert OBJECTIVE_CONTINUITY_DRIVE in drives_module.OPTIONAL_REGISTRY


@pytest.mark.asyncio
class TestWhenItRaisesThings:
    async def test_a_brand_new_objective_is_not_raised_back_immediately(
        self,
        mem,
        objectives,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """Establishing an objective and being told about it in the same
        breath is exactly the burden this slice removes."""
        server, recorder = webhook_server
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, objectives, scheduler_store, registry)

        objectives.open(title="get the roof repaired")
        await drive_objective_continuity_check(ctx)
        assert recorder.received == []

    async def test_a_near_horizon_is_raised_even_inside_the_quiet_interval(
        self,
        mem,
        objectives,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, objectives, scheduler_store, registry)

        soon = (date.today() + timedelta(days=1)).isoformat()
        objectives.open(
            title="get the roof repaired",
            horizon_kind=objective_store.HORIZON_BY_DATE,
            horizon_date=soon,
        )
        await drive_objective_continuity_check(ctx)
        assert len(recorder.received) == 1

    async def test_a_distant_horizon_is_left_alone(
        self,
        mem,
        objectives,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, objectives, scheduler_store, registry)

        far = (date.today() + timedelta(days=90)).isoformat()
        objectives.open(
            title="get the roof repaired",
            horizon_kind=objective_store.HORIZON_BY_DATE,
            horizon_date=far,
        )
        await drive_objective_continuity_check(ctx)
        assert recorder.received == []

    async def test_evidence_arriving_is_not_by_itself_a_reason_to_interrupt(
        self,
        mem,
        objectives,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, objectives, scheduler_store, registry)

        objective = objectives.open(title="get the roof repaired")
        objectives.record(objective.id, event_kind=EVENT_FACT, summary="rain Thursday")
        await drive_objective_continuity_check(ctx)
        assert recorder.received == []

    async def test_only_a_bounded_number_are_raised_per_tick(
        self,
        mem,
        objectives,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(
            mem,
            objectives,
            scheduler_store,
            registry,
            cfg={"proactive": {"objective_continuity": True, "objective_max_per_tick": 2}},
        )
        for i in range(5):
            objective = objectives.open(title=f"objective number {i}")
            _age_objective(objectives, objective.id, days=10)

        await drive_objective_continuity_check(ctx)
        assert len(recorder.received) == 2


@pytest.mark.asyncio
class TestContinuityContent:
    async def test_the_second_re_engagement_reports_only_what_is_new(
        self,
        mem,
        objectives,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """The whole point of the event window: the user is told what has
        changed, not handed the entire history again."""
        server, recorder = webhook_server
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, objectives, scheduler_store, registry)

        objective = objectives.open(title="get the roof repaired")
        objectives.record(objective.id, event_kind=EVENT_DECISION, summary="chose a roofer")
        _age_objective(objectives, objective.id, days=10)

        await drive_objective_continuity_check(ctx)
        assert "chose a roofer" in _delivered_bodies(recorder)[0]

        # New evidence, and the quiet interval elapses again.
        objectives.record(objective.id, event_kind=EVENT_ACTION, summary="paid the deposit")
        _age_objective(objectives, objective.id, days=10)
        recorder.received.clear()

        await drive_objective_continuity_check(ctx)
        second = _delivered_bodies(recorder)[0]
        assert "paid the deposit" in second
        assert "chose a roofer" not in second, "the old history was repeated"

    async def test_nothing_new_is_said_plainly_rather_than_padded(
        self,
        mem,
        objectives,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, objectives, scheduler_store, registry)

        objective = objectives.open(title="get the roof repaired")
        _age_objective(objectives, objective.id, days=10)
        await drive_objective_continuity_check(ctx)
        recorder.received.clear()

        objectives.record(objective.id, event_kind=EVENT_PROPOSAL, summary="could chase them")
        _age_objective(objectives, objective.id, days=10)
        await drive_objective_continuity_check(ctx)

        body = _delivered_bodies(recorder)[0]
        assert "nothing has changed" in body.lower()
        assert "could chase them" not in body
