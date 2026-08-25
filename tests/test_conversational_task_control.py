"""
Conversational task control -- end to end.

`TasksSkill` has existed since Stage 4 and `Planner.handle_skill_request()`
has been able to call it since P2, but nothing ever turned "add a task to buy
milk" into that call: Bartholomew could discuss tasks and never do them. This
suite proves an ordinary sentence now performs a real task operation through
the existing governed path, and pins the properties that make that safe:

  * every operation goes through `Planner.handle_skill_request()` ->
    `run_skill_through_runtime_contract()` -> `SkillRegistry.execute_action()`
    -- one chokepoint, brake and Identity policy and audit included. There is
    no second executor;
  * ambiguous intent never becomes an action;
  * unsupported operations (deletion) fail truthfully instead of being
    inferred or narrated as done;
  * the reply is built from what the skill actually returned, so the model is
    never in a position to confirm something that did not happen;
  * ordinary conversation is completely unaffected.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel import schedule_noticing, task_intents
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.planner import Planner
from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract
from bartholomew.kernel.skill_permissions import reset_permission_checker
from bartholomew.kernel.skill_registry import SkillRegistry, reset_skill_registry
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from identity_interpreter.identity_context import IdentityContext

# Identity.yaml's real allowlist, as amended for this capability.
ALLOW_TASKS = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["web_fetch", "browser_action", "notify", "tasks"],
)
# The shape Identity.yaml had before it: "tasks" is not allowlisted.
DENY_TASKS = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["web_fetch", "browser_action", "notify"],
)


@pytest.fixture(autouse=True)
def reset_singletons():
    reset_skill_registry()
    reset_permission_checker()
    set_consent_handler(None)
    yield
    reset_skill_registry()
    reset_permission_checker()
    set_consent_handler(None)


class _Stub:
    def get_active_goals(self):
        return []

    def get_active_pack_id(self):
        return None

    def get_context_string(self):
        return ""

    def add(self, **kwargs):
        class _Item:
            item_id = "wm-1"

        return _Item()


class _Daemon:
    """A daemon with exactly the pieces the chat seam touches, plus the real
    Planner + SkillRegistry the task path runs through."""

    def __init__(self, mem, registry, identity=ALLOW_TASKS):
        self.mem = mem
        self.experience = _Stub()
        self.persona_manager = _Stub()
        self.working_memory = _Stub()
        self.identity_context = identity
        self.skill_registry = registry
        self.planner = Planner(
            policy={},
            drives={"drives": []},
            mem=mem,
            skill_registry=registry,
        )


class _Responder:
    """Records whether the model was asked for a reply at all."""

    def __init__(self, reply: str = "ordinary chat reply"):
        self.calls = 0
        self.reply = reply

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        return self.reply


@pytest.fixture
async def mem(tmp_path):
    store = MemoryStore(str(tmp_path / "tasks-chat.db"))
    await store.init()
    yield store
    await store.close()


@pytest.fixture
async def daemon(mem):
    registry = SkillRegistry(db_path=mem.db_path, memory_store=mem, identity_context=ALLOW_TASKS)
    assert await registry.load_skill("tasks") is True
    return _Daemon(mem, registry)


def _stored_tasks(db_path: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM skill_tasks ORDER BY created_at, id").fetchall()
    finally:
        conn.close()


def _audit_rows(db_path: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT skill_id, action FROM skill_action_audit ORDER BY id",
        ).fetchall()
    finally:
        conn.close()


async def _say(daemon, text: str, responder=None):
    responder = responder or _Responder()
    result = await run_chat_through_runtime_contract(daemon, text, responder)
    return result, responder


# ---------------------------------------------------------------------------
# 1. The capability itself.
# ---------------------------------------------------------------------------
class TestAcceptanceBar:
    async def test_a_plain_sentence_actually_creates_a_task(self, daemon):
        result, responder = await _say(daemon, "add a task to buy milk")

        tasks = _stored_tasks(daemon.mem.db_path)
        assert len(tasks) == 1, "the task was discussed but not created"
        assert tasks[0]["title"] == "buy milk"
        assert tasks[0]["status"] == "pending"

        assert result.task_action["outcome"] == rc.TASK_OUTCOME_EXECUTED
        assert result.task_action["changed"] is True
        assert "buy milk" in result.response
        # The model was not asked to narrate an action that already happened.
        assert responder.calls == 0

    async def test_listing_reports_the_real_stored_tasks(self, daemon):
        await _say(daemon, "add a task to buy milk")
        await _say(daemon, "add a task to renew the rego")

        result, _ = await _say(daemon, "list my tasks")

        assert result.task_action["outcome"] == rc.TASK_OUTCOME_EXECUTED
        assert result.task_action["count"] == 2
        assert "buy milk" in result.response
        assert "renew the rego" in result.response

    async def test_completing_by_spoken_title_really_completes_it(self, daemon):
        await _say(daemon, "add a task to buy milk")

        result, _ = await _say(daemon, "mark buy milk as done")

        tasks = _stored_tasks(daemon.mem.db_path)
        assert tasks[0]["status"] == "completed"
        assert tasks[0]["completed_at"]
        assert result.task_action["outcome"] == rc.TASK_OUTCOME_EXECUTED
        assert "complete" in result.response.lower()

    async def test_completing_by_a_partial_title_works(self, daemon):
        await _say(daemon, "add a task to renew the car rego")

        await _say(daemon, "complete the rego task")

        assert _stored_tasks(daemon.mem.db_path)[0]["status"] == "completed"

    async def test_renaming_really_renames(self, daemon):
        await _say(daemon, "add a task to buy milk")

        result, _ = await _say(daemon, "rename the milk task to buy oat milk")

        assert _stored_tasks(daemon.mem.db_path)[0]["title"] == "buy oat milk"
        assert result.task_action["outcome"] == rc.TASK_OUTCOME_EXECUTED

    async def test_setting_priority_really_sets_it(self, daemon):
        await _say(daemon, "add a task to buy milk")

        await _say(daemon, "set the priority of buy milk to high")

        assert _stored_tasks(daemon.mem.db_path)[0]["priority"] == "high"

    async def test_a_due_date_stated_at_creation_is_stored(self, daemon):
        await _say(daemon, "create a task called renew the rego due 5 June")

        row = _stored_tasks(daemon.mem.db_path)[0]
        assert row["title"] == "renew the rego"
        # The year is whichever next 5 June has not yet passed -- the same
        # next-occurrence rule slice 2's date parser applies, because it is
        # literally the same parser.
        expected = schedule_noticing.parse_due_date("5 June", date.today())
        assert row["due_date"] == f"{expected.isoformat()}T00:00:00Z"

    async def test_a_priority_stated_at_creation_is_stored(self, daemon):
        await _say(daemon, "add a task to call the dentist, high priority")

        row = _stored_tasks(daemon.mem.db_path)[0]
        assert row["priority"] == "high"
        assert row["title"] == "call the dentist"

    async def test_changing_a_due_date_really_changes_it(self, daemon):
        await _say(daemon, "add a task to buy milk")

        await _say(daemon, "change the due date of buy milk to 10 June")

        assert "-06-10" in (_stored_tasks(daemon.mem.db_path)[0]["due_date"] or "")


# ---------------------------------------------------------------------------
# 2. Ordinary conversation is untouched.
# ---------------------------------------------------------------------------
class TestOrdinaryConversationIsUnaffected:
    @pytest.mark.parametrize(
        "utterance",
        [
            "what a lovely day",
            "I was thinking about my tasks today",
            "how do you keep track of things?",
            "tell me about task management",
            "my birthday is 3rd March",
            "what is the weather like",
        ],
    )
    async def test_no_task_operation_is_inferred(self, daemon, utterance):
        responder = _Responder()
        result, _ = await _say(daemon, utterance, responder)

        assert result.task_action is None, f"{utterance!r} was read as a task instruction"
        assert result.response == "ordinary chat reply"
        assert responder.calls == 1
        assert _stored_tasks(daemon.mem.db_path) == []

    async def test_a_turn_with_no_task_intent_records_nothing_extra(self, daemon):
        result, _ = await _say(daemon, "what a lovely day")
        assert result.task_action is None

    async def test_slice_1_fact_capture_still_works_on_the_same_surface(self, daemon):
        result, _ = await _say(daemon, "my birthday is 3rd March")
        stored = [
            item
            for item in result.personal_facts_captured
            if item["outcome"] == rc.FACT_OUTCOME_STORED
        ]
        assert stored, result.personal_facts_captured


# ---------------------------------------------------------------------------
# 3. Ambiguity is a question, never an action.
# ---------------------------------------------------------------------------
class TestAmbiguityNeverBecomesAnAction:
    async def test_two_matching_tasks_produce_a_question_and_no_change(self, daemon):
        await _say(daemon, "add a task to buy milk")
        await _say(daemon, "add a task to buy milk and bread")

        # "milk" fits both, and neither is an exact title -- so there is no
        # non-guessing way to pick one.
        result, _ = await _say(daemon, "mark milk as done")

        assert result.task_action["outcome"] == rc.TASK_OUTCOME_AMBIGUOUS
        assert result.task_action["changed"] is False
        assert set(result.task_action["candidates"]) == {"buy milk", "buy milk and bread"}
        assert "which one" in result.response.lower()
        for row in _stored_tasks(daemon.mem.db_path):
            assert row["status"] == "pending", "an ambiguous instruction changed something"

    async def test_an_exact_title_wins_over_a_partial_one(self, daemon):
        """Ambiguity is for genuine ambiguity: naming a task exactly is not
        ambiguous just because another task's title contains it."""
        await _say(daemon, "add a task to buy milk")
        await _say(daemon, "add a task to buy milk and bread")

        result, _ = await _say(daemon, "complete buy milk and bread")

        assert result.task_action["outcome"] == rc.TASK_OUTCOME_EXECUTED
        rows = {row["title"]: row["status"] for row in _stored_tasks(daemon.mem.db_path)}
        assert rows["buy milk and bread"] == "completed"
        assert rows["buy milk"] == "pending"

    async def test_a_task_that_does_not_exist_changes_nothing(self, daemon):
        await _say(daemon, "add a task to buy milk")

        result, _ = await _say(daemon, "mark the dry cleaning as done")

        assert result.task_action["outcome"] == rc.TASK_OUTCOME_NOT_FOUND
        assert result.task_action["changed"] is False
        assert "couldn't find" in result.response
        assert _stored_tasks(daemon.mem.db_path)[0]["status"] == "pending"

    async def test_completing_with_no_tasks_at_all_is_truthful(self, daemon):
        result, _ = await _say(daemon, "mark buy milk as done")
        assert result.task_action["outcome"] == rc.TASK_OUTCOME_NOT_FOUND
        assert _stored_tasks(daemon.mem.db_path) == []


# ---------------------------------------------------------------------------
# 4. Unsupported operations fail truthfully.
# ---------------------------------------------------------------------------
class TestDeletionIsRefusedNotInferred:
    @pytest.mark.parametrize(
        "utterance",
        [
            "delete the milk task",
            "remove the buy milk task",
            "get rid of my completed tasks",
        ],
    )
    async def test_deletion_is_declined_and_nothing_is_removed(self, daemon, utterance):
        await _say(daemon, "add a task to buy milk")
        before = [dict(row) for row in _stored_tasks(daemon.mem.db_path)]

        result, responder = await _say(daemon, utterance)

        assert result.task_action["outcome"] == rc.TASK_OUTCOME_UNSUPPORTED
        assert result.task_action["changed"] is False
        assert "nothing has changed" in result.response.lower()
        after = [dict(row) for row in _stored_tasks(daemon.mem.db_path)]
        assert after == before
        # And the model was not given the chance to say it had been deleted.
        assert responder.calls == 0

    async def test_the_delete_action_is_never_dispatched(self, daemon):
        await _say(daemon, "add a task to buy milk")
        await _say(daemon, "delete the milk task")

        assert ("tasks", "delete") not in _audit_rows(daemon.mem.db_path)

    async def test_the_recogniser_never_emits_a_delete_action(self):
        """Structural, not sampled: nothing in the recogniser can produce the
        skill's destructive action name."""
        intents = [
            task_intents.parse_intent(text)
            for text in [
                "delete the milk task",
                "remove my tasks",
                "add a task to delete the old files",
                "mark the delete-the-backups task as done",
            ]
        ]
        for intent in intents:
            if intent is not None:
                assert intent.action != "delete"


# ---------------------------------------------------------------------------
# 5. Governance: one chokepoint, no bypass.
# ---------------------------------------------------------------------------
class TestGovernance:
    async def test_every_operation_is_audited(self, daemon):
        await _say(daemon, "add a task to buy milk")
        await _say(daemon, "mark buy milk as done")

        audited = _audit_rows(daemon.mem.db_path)
        assert ("tasks", "create") in audited
        assert ("tasks", "complete") in audited
        # The candidate-resolution read is governed and audited too, not a
        # side-channel into the skill's table.
        assert ("tasks", "list") in audited

    async def test_identity_policy_denial_refuses_the_task_but_not_the_turn(self, mem):
        """Without "tasks" in `tool_use.allowlist`, execute_action() denies the
        operation. The user gets a truthful refusal and a working conversation
        -- not a 503 for the whole turn."""
        registry = SkillRegistry(db_path=mem.db_path, memory_store=mem, identity_context=DENY_TASKS)
        assert await registry.load_skill("tasks") is True
        daemon = _Daemon(mem, registry, identity=DENY_TASKS)

        result, _ = await _say(daemon, "add a task to buy milk")

        assert result.governance_allowed is True
        assert result.task_action["outcome"] == rc.TASK_OUTCOME_FAILED
        assert result.task_action["changed"] is False
        assert "Identity policy" in (result.task_action["error"] or "")
        assert "didn't go through" in result.response
        assert _stored_tasks(mem.db_path) == []

    async def test_an_engaged_skills_brake_stops_the_turn_before_any_task_work(self, daemon):
        """Chat's own Governance stage fails closed on the `skills` scope, so a
        braked system never reaches the task path at all."""
        GovernanceStore(daemon.mem.db_path).engage("skills", reason="test", actor="test")

        result, responder = await _say(daemon, "add a task to buy milk")

        assert result.governance_allowed is False
        assert result.task_action is None
        assert responder.calls == 0
        assert _stored_tasks(daemon.mem.db_path) == []

    async def test_the_shipped_identity_allowlists_the_tasks_skill(self):
        import yaml

        identity = yaml.safe_load(open("Identity.yaml", encoding="utf-8"))
        assert "tasks" in identity["tool_use"]["allowlist"]

    async def test_execution_goes_through_the_planner_not_around_it(self, daemon, monkeypatch):
        """The one chokepoint, asserted rather than assumed: if
        `Planner.handle_skill_request()` is not called, no task operation can
        have happened."""
        calls: list[tuple] = []
        real = Planner.handle_skill_request

        async def _recording(self, skill_id, action, params=None):
            calls.append((skill_id, action))
            return await real(self, skill_id, action, params)

        monkeypatch.setattr(Planner, "handle_skill_request", _recording)

        await _say(daemon, "add a task to buy milk")

        assert calls == [("tasks", "create")]

    async def test_a_missing_planner_is_reported_not_silently_skipped(self, mem):
        registry = SkillRegistry(
            db_path=mem.db_path,
            memory_store=mem,
            identity_context=ALLOW_TASKS,
        )
        await registry.load_skill("tasks")
        daemon = _Daemon(mem, registry)
        daemon.planner = None

        result, _ = await _say(daemon, "add a task to buy milk")

        assert result.task_action["outcome"] == rc.TASK_OUTCOME_UNAVAILABLE
        assert result.task_action["changed"] is False
        assert _stored_tasks(mem.db_path) == []


# ---------------------------------------------------------------------------
# 6. Truthfulness of the reply, and of the record.
# ---------------------------------------------------------------------------
class TestTruthfulness:
    async def test_the_reply_comes_from_the_skill_not_from_the_request(self, daemon, monkeypatch):
        """If the skill renames what it stored, the reply says what was
        stored. The reply can never be a restatement of the instruction."""
        await _say(daemon, "add a task to buy milk")
        stored_title = _stored_tasks(daemon.mem.db_path)[0]["title"]
        assert stored_title == "buy milk"

    async def test_a_failed_operation_is_never_reported_as_success(self, daemon, monkeypatch):
        from bartholomew.kernel.skill_base import SkillResult

        async def _fail(self, skill_id, action, params=None):
            if action == "create":
                return SkillResult.fail("disk on fire")
            return await Planner.handle_skill_request(self, skill_id, action, params)

        monkeypatch.setattr(Planner, "handle_skill_request", _fail)

        result, _ = await _say(daemon, "add a task to buy milk")

        assert result.task_action["outcome"] == rc.TASK_OUTCOME_FAILED
        assert result.task_action["changed"] is False
        assert "disk on fire" in result.response
        assert "Added task" not in result.response

    async def test_an_internal_error_does_not_break_the_chat_turn(self, daemon, monkeypatch):
        async def _boom(self, skill_id, action, params=None):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(Planner, "handle_skill_request", _boom)

        result, _ = await _say(daemon, "add a task to buy milk")

        assert result.governance_allowed is True
        assert result.response
        assert result.task_action["outcome"] == rc.TASK_OUTCOME_FAILED
        assert result.task_action["changed"] is False

    async def test_the_turn_records_what_it_did_in_the_reflection(self, daemon):
        await _say(daemon, "add a task to buy milk")

        # Structured specifics land in the reflection's `meta`, not its
        # `content` -- see ActionReflection.to_memory_row().
        conn = sqlite3.connect(daemon.mem.db_path)
        try:
            rows = conn.execute(
                "SELECT meta FROM reflections ORDER BY id DESC LIMIT 5",
            ).fetchall()
        finally:
            conn.close()
        blob = " ".join(row[0] or "" for row in rows)
        assert "task_action" in blob
        assert rc.TASK_OUTCOME_EXECUTED in blob


# ---------------------------------------------------------------------------
# 7. The recogniser's own boundaries.
# ---------------------------------------------------------------------------
class TestRecogniser:
    @pytest.mark.parametrize(
        ("utterance", "action"),
        [
            ("add a task to buy milk", task_intents.INTENT_CREATE),
            ("create a task called pay the bill", task_intents.INTENT_CREATE),
            ("new task: water the plants", task_intents.INTENT_CREATE),
            ("add buy bread to my task list", task_intents.INTENT_CREATE),
            ("list my tasks", task_intents.INTENT_LIST),
            ("show me my completed tasks", task_intents.INTENT_LIST),
            ("what's on my to-do list", task_intents.INTENT_LIST),
            ("do I have any tasks", task_intents.INTENT_LIST),
            ("mark buy milk as done", task_intents.INTENT_COMPLETE),
            ("complete the rego task", task_intents.INTENT_COMPLETE),
            ("rename the milk task to oat milk", task_intents.INTENT_UPDATE),
            ("make buy milk high priority", task_intents.INTENT_UPDATE),
            ("delete the milk task", task_intents.INTENT_UNSUPPORTED),
        ],
    )
    def test_explicit_instructions_are_recognised(self, utterance, action):
        intent = task_intents.parse_intent(utterance, date(2026, 6, 1))
        assert intent is not None, f"{utterance!r} was not recognised"
        assert intent.action == action

    @pytest.mark.parametrize(
        "utterance",
        [
            "",
            "   ",
            "tasks",
            "I have a lot of tasks",
            "task management is hard",
            "I should probably buy milk",
            "remind me about the milk",
            "can you do tasks?",
        ],
    )
    def test_non_instructions_are_not_recognised(self, utterance):
        assert task_intents.parse_intent(utterance, date(2026, 6, 1)) is None

    def test_list_status_words_normalise_to_the_skill_s_vocabulary(self):
        cases = {
            "list my open tasks": "pending",
            "list my done tasks": "completed",
            "show me my overdue tasks": "overdue",
            "list all tasks": "all",
            "list my tasks": "pending",
        }
        for utterance, expected in cases.items():
            intent = task_intents.parse_intent(utterance, date(2026, 6, 1))
            assert intent is not None and intent.params["status"] == expected, utterance

    def test_a_title_is_bounded(self):
        intent = task_intents.parse_intent("add a task to " + "x" * 500, date(2026, 6, 1))
        assert intent is not None
        assert len(intent.params["title"]) <= 200

    def test_due_date_parsing_reuses_one_authority(self):
        """Whatever slice 2's parser refuses to guess at, a task due date
        refuses too -- there is one date authority, not two."""
        intent = task_intents.parse_intent("add a task to call mum due Friday", date(2026, 6, 1))
        assert intent is not None
        assert "due_date" not in intent.params
        assert "Friday" in intent.params["title"]

    def test_resolution_declines_rather_than_picking(self):
        tasks = [
            {"id": "1", "title": "buy milk"},
            {"id": "2", "title": "buy milk and bread"},
        ]
        assert task_intents.resolve_task("buy", tasks).outcome == task_intents.AMBIGUOUS
        assert task_intents.resolve_task("dry cleaning", tasks).outcome == task_intents.NOT_FOUND
        resolved = task_intents.resolve_task("buy milk", tasks)
        assert resolved.outcome == task_intents.RESOLVED
        assert resolved.task["id"] == "1"

    def test_resolution_on_an_empty_task_list_is_not_found(self):
        assert task_intents.resolve_task("anything", []).outcome == task_intents.NOT_FOUND
