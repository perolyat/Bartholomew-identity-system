"""
Golden Path first slice, end to end: an ordinary sentence reaches an external
capability provider and comes back as evidence Bartholomew uses.

The suite above (`test_forecast_external_capability.py`) proves the skill and
its governance. This one proves the loop the slice actually claims:

    user objective -> Executive -> governed runtime seam -> external provider
    -> provenance-bearing evidence -> Executive uses it toward the objective

and the properties that make the loop trustworthy rather than merely working:
the brake stops it at the chat surface too; the reply is built from what came
back rather than generated; the model is never asked to answer a question
about the future that only the provider can answer; and the turn's Reflection
records exactly what was disclosed to whom.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.planner import Planner
from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract
from bartholomew.kernel.skill_permissions import reset_permission_checker
from bartholomew.kernel.skill_registry import SkillRegistry, reset_skill_registry
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from bartholomew.skills.forecast import (
    FORECAST_API_URL_ENV,
    LATITUDE_ENV,
    LONGITUDE_ENV,
)
from identity_interpreter.identity_context import IdentityContext

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "config" / "skills" / "forecast.yaml"

# Identity.yaml's real allowlist, as amended for this capability.
ALLOW_FORECAST = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["web_fetch", "browser_action", "notify", "forecast"],
)
# The shape Identity.yaml had before it: "forecast" is not allowlisted.
DENY_FORECAST = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["web_fetch", "browser_action", "notify"],
)


class _Provider(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's required name
        parsed = urlparse(self.path)
        type(self).received.append({k: v[0] for k, v in parse_qs(parsed.query).items()})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "daily": {
                        "time": ["2026-08-28"],
                        "temperature_2m_max": [19.4],
                        "temperature_2m_min": [9.1],
                        "precipitation_sum": [11.0],
                        "precipitation_probability_max": [85],
                    },
                },
            ).encode("utf-8"),
        )

    def log_message(self, *args):
        pass


@pytest.fixture
def provider():
    _Provider.received = []
    server = HTTPServer(("127.0.0.1", 0), _Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, _Provider
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
    """The pieces the chat seam touches, plus the real Planner + registry."""

    def __init__(self, mem, registry, identity=ALLOW_FORECAST):
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

    def __init__(self, reply: str = "Probably sunny, I'd say!"):
        self.calls = 0
        self.reply = reply

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        return self.reply


@pytest.fixture
async def mem(tmp_path):
    store = MemoryStore(str(tmp_path / "forecast-chat.db"))
    await store.init()
    yield store
    await store.close()


@pytest.fixture
def skills_dir(tmp_path, provider):
    """The shipped manifest, with only its allowlisted host pointed at loopback."""
    server, _recorder = provider
    data = yaml.safe_load(MANIFEST_PATH.read_text())
    data["permissions"]["sandbox"]["network"] = ["127.0.0.1"]
    path = tmp_path / "skills"
    path.mkdir()
    (path / "forecast.yaml").write_text(yaml.safe_dump(data))
    return path


@pytest.fixture
async def daemon(mem, skills_dir, provider, monkeypatch):
    server, _recorder = provider
    monkeypatch.setenv(FORECAST_API_URL_ENV, f"http://127.0.0.1:{server.server_port}/v1/forecast")
    monkeypatch.setenv(LATITUDE_ENV, "-33.8688")
    monkeypatch.setenv(LONGITUDE_ENV, "151.2093")
    set_consent_handler(lambda _prompt: True)

    registry = SkillRegistry(
        skills_dir=skills_dir,
        db_path=mem.db_path,
        memory_store=mem,
        identity_context=ALLOW_FORECAST,
    )
    assert await registry.load_skill("forecast") is True
    return _Daemon(mem, registry)


async def _say(daemon, text: str, responder=None):
    responder = responder or _Responder()
    result = await run_chat_through_runtime_contract(daemon, text, responder)
    return result, responder


def _audit_rows(db_path: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT skill_id, action FROM skill_action_audit ORDER BY id",
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. The loop the slice claims.
# ---------------------------------------------------------------------------
class TestTheGoldenPathLoop:
    async def test_a_plain_question_reaches_the_provider_and_returns_evidence(
        self,
        daemon,
        provider,
    ):
        _server, recorder = provider

        result, responder = await _say(daemon, "will it rain tomorrow?")

        assert len(recorder.received) == 1, "the external capability was not used"
        assert result.forecast_action["outcome"] == rc.FORECAST_OUTCOME_OBTAINED
        # The answer is the provider's figure, attributed, not the model's.
        assert "85%" in result.response
        assert "127.0.0.1" in result.response
        assert responder.calls == 0, "the model must not answer a question only the provider can"

    async def test_the_executive_uses_the_evidence_toward_the_objective(self, daemon):
        """The user asked a decision question; they get a decision, plus the
        numbers it rests on and where those came from."""
        result, _responder = await _say(daemon, "will it rain tomorrow?")

        assert "plan for rain" in result.response
        assert "not something I know independently" in result.response

    async def test_ordinary_conversation_is_completely_unaffected(self, daemon, provider):
        _server, recorder = provider

        result, responder = await _say(daemon, "what did we decide about the fence?")

        assert result.forecast_action is None
        assert responder.calls == 1
        assert recorder.received == []

    async def test_a_task_instruction_still_routes_to_tasks_not_forecast(self, daemon, provider):
        """The two recognisers do not collide."""
        _server, recorder = provider

        result, _responder = await _say(daemon, "add a task to check the gutters")

        assert result.forecast_action is None
        assert recorder.received == []


# ---------------------------------------------------------------------------
# 2. Governance, at the chat surface.
# ---------------------------------------------------------------------------
class TestGovernanceAtTheChatSurface:
    async def test_parking_brake_means_zero_external_calls(self, daemon, provider):
        _server, recorder = provider
        GovernanceStore(daemon.mem.db_path).engage("skills")

        result, _responder = await _say(daemon, "will it rain tomorrow?")

        assert recorder.received == []
        assert result.governance_allowed is False

    async def test_a_denied_identity_policy_produces_a_truthful_reply(self, daemon, provider):
        _server, recorder = provider
        daemon.skill_registry._identity_context = DENY_FORECAST

        result, responder = await _say(daemon, "will it rain tomorrow?")

        assert recorder.received == []
        assert result.forecast_action["outcome"] == rc.FORECAST_OUTCOME_FAILED
        assert "not going to invent one" in result.response
        assert responder.calls == 0, "a denial must not fall through to the model"

    async def test_refused_consent_produces_a_truthful_reply(self, daemon, provider):
        _server, recorder = provider
        set_consent_handler(lambda _prompt: False)

        result, responder = await _say(daemon, "will it rain tomorrow?")

        assert recorder.received == []
        assert result.forecast_action["outcome"] == rc.FORECAST_OUTCOME_DENIED
        assert "Nothing was sent" in result.response
        assert responder.calls == 0

    async def test_a_named_place_never_reaches_the_provider(self, daemon, provider):
        _server, recorder = provider

        result, responder = await _say(daemon, "what's the weather in Brisbane this week?")

        assert recorder.received == [], "no disclosure for a question we cannot answer"
        assert result.forecast_action["outcome"] == rc.FORECAST_OUTCOME_UNSUPPORTED_PLACE
        assert "Brisbane" in result.response
        assert responder.calls == 0


# ---------------------------------------------------------------------------
# 3. The record of what happened -- audit and provenance.
# ---------------------------------------------------------------------------
class TestTheRecord:
    async def test_the_turn_is_audited_as_a_governed_skill_action(self, daemon):
        await _say(daemon, "will it rain tomorrow?")

        assert ("forecast", "lookup") in _audit_rows(daemon.mem.db_path)

    async def test_the_reflection_records_exactly_what_was_disclosed(self, daemon, provider):
        """An egress nobody recorded is an egress nobody can audit."""
        _server, recorder = provider

        result, _responder = await _say(daemon, "will it rain tomorrow?")

        disclosed = result.forecast_action["disclosed"]
        assert {k: str(v) for k, v in disclosed.items()} == recorder.received[0]
        assert result.forecast_action["provider_host"] == "127.0.0.1"

    async def test_the_reflection_is_persisted_for_the_turn(self, daemon):
        from bartholomew.kernel.reflection import REFLECTION_KIND

        import aiosqlite

        await _say(daemon, "will it rain tomorrow?")

        async with aiosqlite.connect(daemon.mem.db_path) as db:
            cur = await db.execute(
                "SELECT meta, content FROM reflections WHERE kind=?",
                (REFLECTION_KIND,),
            )
            rows = await cur.fetchall()

        assert rows, "the chat turn wrote no Reflection"
        assert any("forecast_action" in json.dumps(list(row), default=str) for row in rows)

    async def test_no_external_content_is_written_to_durable_memory(self, daemon):
        """External output is evidence for the turn, not knowledge.

        Checked by looking for the provider's own numbers anywhere in the
        durable stores this slice could plausibly have touched.
        """
        await _say(daemon, "will it rain tomorrow?")

        conn = sqlite3.connect(daemon.mem.db_path)
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            ]
            # The Reflection legitimately records that a lookup happened and
            # what was disclosed; it is the *forecast content* that must not
            # become durable knowledge.
            for table in tables:
                if table in ("reflections", "skill_action_audit"):
                    continue
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
                blob = json.dumps(rows, default=str)
                assert "19.4" not in blob, f"provider content leaked into {table}"
        finally:
            conn.close()
