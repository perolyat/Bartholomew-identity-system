"""
Golden Path first slice -- the first external capability provider.

This suite is the evidence for the slice's claim, which is **not** "Bartholomew
can tell you the weather". It is: *Bartholomew can use an external capability
provider while remaining the governed Executive above it.* Each class below
pins one of the properties that claim rests on, and each is written so that
losing the property fails a test rather than merely reading worse:

1.  Governed chokepoint -- brake engaged means **zero** provider requests.
2.  Governed egress -- only the declared fields leave, verbatim.
3.  Provenance on return -- evidence names its source.
4.  Truthful degradation -- five distinct failure shapes, no fabricated data.
5.  Executive authority -- the objective and the judgement stay Bartholomew's.
6.  Replaceability -- provider knowledge stays confined.

Delivery is exercised against a real `http.server` bound to loopback, not a
mock, for the same reason `tests/test_notify_webhook_delivery.py` does it:
a mocked client would prove only that the code called itself. What loopback
**cannot** prove is that the real Open-Meteo endpoint behaves as expected --
that step is documented in `docs/GOLDEN_PATH_SLICE_1_EXTERNAL_FORECAST.md` and has
deliberately not been claimed here.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from bartholomew.kernel import forecast_intents
from bartholomew.kernel.memory.privacy_guard import set_consent_handler
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.reflection import REFLECTION_KIND
from bartholomew.kernel.skill_base import SkillContext, SkillResultStatus
from bartholomew.kernel.skill_manifest import SkillManifest
from bartholomew.kernel.skill_permissions import reset_permission_checker
from bartholomew.kernel.skill_registry import SkillRegistry, reset_skill_registry
from bartholomew.skills import forecast as forecast_module
from bartholomew.skills.forecast import (
    EGRESS_FIELDS,
    FORECAST_API_URL_ENV,
    LATITUDE_ENV,
    LONGITUDE_ENV,
    OUTCOME_HOST_NOT_ALLOWED,
    OUTCOME_MALFORMED,
    OUTCOME_PROVIDER_ERROR,
    OUTCOME_UNCONFIGURED,
    ForecastSkill,
)
from identity_interpreter.identity_context import IdentityContext

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "config" / "skills" / "forecast.yaml"


# =============================================================================
# A real provider, on loopback. It records every request it receives -- which
# is what makes "zero requests" an assertion about the network rather than
# about our own bookkeeping.
# =============================================================================


def _sample_payload(days: int = 1) -> dict:
    dates = [f"2026-08-{27 + i:02d}" for i in range(days)]
    return {
        "latitude": -33.87,
        "longitude": 151.21,
        "daily": {
            "time": dates,
            "temperature_2m_max": [22.5 + i for i in range(days)],
            "temperature_2m_min": [12.0 + i for i in range(days)],
            "precipitation_sum": [4.2 for _ in range(days)],
            "precipitation_probability_max": [80 for _ in range(days)],
        },
    }


class _Provider(BaseHTTPRequestHandler):
    received: list[dict] = []
    status = 200
    body: str | None = None
    days = 1

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's required name
        parsed = urlparse(self.path)
        type(self).received.append(
            {
                "path": parsed.path,
                "query": {k: v[0] for k, v in parse_qs(parsed.query).items()},
                "headers": dict(self.headers),
            },
        )
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = type(self).body
        if body is None:
            body = json.dumps(_sample_payload(type(self).days))
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args):
        pass


@pytest.fixture
def provider():
    _Provider.received = []
    _Provider.status = 200
    _Provider.body = None
    _Provider.days = 1
    server = HTTPServer(("127.0.0.1", 0), _Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, _Provider
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _provider_url(server) -> str:
    return f"http://127.0.0.1:{server.server_port}/v1/forecast"


def _manifest_allowing(hosts: list[str]) -> SkillManifest:
    """The shipped manifest with only its network allowlist substituted.

    Everything else -- permission level, required permissions, actions -- is
    the real manifest, so these tests cannot pass against a configuration the
    repository does not actually ship.
    """
    data = yaml.safe_load(MANIFEST_PATH.read_text())
    data["permissions"]["sandbox"]["network"] = hosts
    return SkillManifest.from_dict(data)


async def _skill(
    monkeypatch,
    url: str | None,
    *,
    allowed_hosts: list[str] | None = None,
    latitude: str = "-33.8688",
    longitude: str = "151.2093",
    permitted: bool = True,
) -> ForecastSkill:
    """A directly-driven skill, for the properties that live inside it.

    The registry-level tests below drive the *governed* path instead; this
    helper deliberately does not stand in for those.
    """
    monkeypatch.setenv(FORECAST_API_URL_ENV, url or "")
    monkeypatch.setenv(LATITUDE_ENV, latitude)
    monkeypatch.setenv(LONGITUDE_ENV, longitude)

    host = urlparse(url).hostname if url else "api.open-meteo.com"
    manifest = _manifest_allowing(
        allowed_hosts if allowed_hosts is not None else [host or "api.open-meteo.com"],
    )
    skill = ForecastSkill()
    await skill.initialize(
        SkillContext(manifest=manifest, check_permission=lambda _p: permitted),
    )
    return skill


# =============================================================================
# 1. Governed chokepoint: the brake stops the packets, not the answer.
# =============================================================================


@pytest.fixture(autouse=True)
def reset_singletons():
    reset_skill_registry()
    reset_permission_checker()
    set_consent_handler(None)
    yield
    reset_skill_registry()
    reset_permission_checker()
    set_consent_handler(None)


@pytest.fixture
def skills_dir(tmp_path):
    """The shipped forecast manifest, in a temp skills directory."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "forecast.yaml").write_text(MANIFEST_PATH.read_text())
    return skills_dir


@pytest.fixture
async def governed(tmp_path, skills_dir, provider, monkeypatch):
    """A real SkillRegistry with the real forecast skill and a real provider.

    This is the fixture the governance claims are tested through: nothing is
    stubbed between `execute_action()` and the socket.
    """
    server, recorder = provider
    monkeypatch.setenv(FORECAST_API_URL_ENV, _provider_url(server))
    monkeypatch.setenv(LATITUDE_ENV, "-33.8688")
    monkeypatch.setenv(LONGITUDE_ENV, "151.2093")

    # The manifest's allowlist names Open-Meteo; loopback stands in for it
    # here, so the shipped manifest is used with only the host substituted.
    manifest_data = yaml.safe_load(MANIFEST_PATH.read_text())
    manifest_data["permissions"]["sandbox"]["network"] = ["127.0.0.1"]
    (skills_dir / "forecast.yaml").write_text(yaml.safe_dump(manifest_data))

    db_path = str(tmp_path / "governed.db")
    mem = MemoryStore(db_path)
    await mem.init()
    registry = SkillRegistry(skills_dir=skills_dir, db_path=db_path, memory_store=mem)
    registry._identity_context = IdentityContext(
        tool_use_default_allowed=False,
        tool_use_allowlist=["forecast"],
    )
    await registry.load_skill("forecast")
    yield registry, recorder, mem, db_path
    await mem.close()


def _grant_consent(monkeypatch, approve: bool = True):
    """Register a consent handler for the manifest's `ask`-level permission.

    The real registration path (`privacy_guard.set_consent_handler`), not a
    patched lookup -- so these tests exercise the same consent plumbing the
    running system uses. `ask` fails closed with no handler at all, which is
    itself pinned below.
    """
    set_consent_handler(lambda _prompt: approve)


@pytest.mark.asyncio
class TestGovernedChokepoint:
    async def test_successful_lookup_through_the_governed_path(self, governed, monkeypatch):
        registry, recorder, _mem, _db = governed
        _grant_consent(monkeypatch)

        result = await registry.execute_action("forecast", "lookup", {"days": 1})

        assert result.success, result.error
        assert len(recorder.received) == 1
        assert result.data["days"][0]["temperature_max_c"] == 22.5

    async def test_parking_brake_engaged_means_zero_external_requests(
        self,
        governed,
        monkeypatch,
    ):
        """The property the whole slice rests on.

        Not "the result is suppressed" -- *the provider is never contacted*.
        The recorder is the real server's own list of requests, so this
        asserts about the network, not about our bookkeeping.
        """
        registry, recorder, _mem, db_path = governed
        _grant_consent(monkeypatch)

        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        GovernanceStore(db_path).engage("skills")

        result = await registry.execute_action("forecast", "lookup", {"days": 1})

        assert not result.success
        assert "parking brake" in (result.error or "").lower()
        assert recorder.received == [], "the brake must stop the packets, not just the answer"

    async def test_identity_policy_denial_means_zero_external_requests(
        self,
        governed,
        monkeypatch,
    ):
        registry, recorder, _mem, _db = governed
        _grant_consent(monkeypatch)
        registry._identity_context = IdentityContext(
            tool_use_default_allowed=False,
            tool_use_allowlist=["some_other_skill"],
        )

        result = await registry.execute_action("forecast", "lookup", {"days": 1})

        assert not result.success
        assert "Identity policy" in (result.error or "")
        assert recorder.received == []

    async def test_refused_consent_means_zero_external_requests(self, governed, monkeypatch):
        registry, recorder, _mem, _db = governed
        _grant_consent(monkeypatch, approve=False)

        result = await registry.execute_action("forecast", "lookup", {"days": 1})

        assert result.status == SkillResultStatus.PERMISSION_DENIED
        assert recorder.received == []

    async def test_no_consent_handler_fails_closed(self, governed, monkeypatch):
        """An `ask` permission with nobody to ask is a denial, never a grant."""
        registry, recorder, _mem, _db = governed
        set_consent_handler(None)

        result = await registry.execute_action("forecast", "lookup", {"days": 1})

        assert result.status == SkillResultStatus.PERMISSION_DENIED
        assert recorder.received == []

    async def test_manifest_ships_ask_level_and_network_permission(self):
        """The shipped configuration, not a test fixture's version of it."""
        manifest = SkillManifest.from_dict(yaml.safe_load(MANIFEST_PATH.read_text()))
        assert manifest.permissions.level == "ask"
        assert manifest.permissions.requires == ["network.fetch"]
        assert manifest.permissions.sandbox.network == ["api.open-meteo.com"]
        assert [a.name for a in manifest.actions] == ["lookup"]

    async def test_identity_allowlists_the_skill_id(self):
        """Without this entry the Governance stage denies every lookup."""
        identity = yaml.safe_load((REPO_ROOT / "Identity.yaml").read_text())
        allowlist = identity["tool_use"]["allowlist"]
        assert "forecast" in allowlist


# =============================================================================
# 2. Governed egress: exactly the declared fields, and nothing else.
# =============================================================================


@pytest.mark.asyncio
class TestGovernedEgress:
    async def test_only_declared_fields_leave(self, provider, monkeypatch):
        server, recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server))

        await skill.execute("lookup", {"days": 2, "place_label": "the back paddock"})

        assert len(recorder.received) == 1
        sent = set(recorder.received[0]["query"])
        assert sent == set(EGRESS_FIELDS), f"undeclared egress: {sent - set(EGRESS_FIELDS)}"

    async def test_the_local_only_label_is_never_sent(self, provider, monkeypatch):
        """A `place_label` is for Bartholomew's reply, not for the provider."""
        server, recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server))

        result = await skill.execute(
            "lookup",
            {"days": 1, "place_label": "Taylor's house on Wattle Street"},
        )

        raw = json.dumps(recorder.received[0])
        assert "Wattle" not in raw and "Taylor" not in raw
        assert result.data["place_label"] == "Taylor's house on Wattle Street"

    async def test_no_request_body_is_sent(self, provider, monkeypatch):
        """A GET with typed query params has no body to smuggle context in."""
        server, recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server))

        await skill.execute("lookup", {"days": 1})

        assert recorder.received[0]["headers"].get("Content-Length") in (None, "0")

    async def test_coordinates_are_rounded_before_egress(self, provider, monkeypatch):
        """The disclosure is a locality, not an address."""
        server, recorder = provider
        skill = await _skill(
            monkeypatch,
            _provider_url(server),
            latitude="-33.8688197",
            longitude="151.2092955",
        )

        await skill.execute("lookup", {"days": 1})

        query = recorder.received[0]["query"]
        assert query["latitude"] == "-33.87"
        assert query["longitude"] == "151.21"

    async def test_arbitrary_extra_params_are_not_forwarded(self, provider, monkeypatch):
        """Egress is *constructed*, not copied -- so extra params go nowhere."""
        server, recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server))

        await skill.execute(
            "lookup",
            {"days": 1, "user_id": "taylor", "notes": "recent chat context"},
        )

        raw = json.dumps(recorder.received[0])
        assert "taylor" not in raw and "recent chat context" not in raw

    async def test_non_allowlisted_host_is_refused_before_any_request(
        self,
        provider,
        monkeypatch,
    ):
        server, recorder = provider
        skill = await _skill(
            monkeypatch,
            _provider_url(server),
            allowed_hosts=["api.open-meteo.com"],
        )

        result = await skill.execute("lookup", {"days": 1})

        assert not result.success
        assert result.data["outcome"] == OUTCOME_HOST_NOT_ALLOWED
        assert result.data["attempted"] is False
        assert recorder.received == []

    async def test_empty_allowlist_permits_nothing(self, provider, monkeypatch):
        server, recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server), allowed_hosts=[])

        result = await skill.execute("lookup", {"days": 1})

        assert result.data["outcome"] == OUTCOME_HOST_NOT_ALLOWED
        assert recorder.received == []

    async def test_skill_declines_when_no_location_is_configured(self, provider, monkeypatch):
        """No location, no guess, no call."""
        server, recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server), latitude="", longitude="")

        result = await skill.execute("lookup", {"days": 1})

        assert not result.success
        assert "latitude" in (result.error or "")
        assert recorder.received == []

    async def test_the_declared_egress_set_is_what_it_says(self):
        """A guard on the constant itself: widening it must be deliberate."""
        assert set(EGRESS_FIELDS) == {
            "latitude",
            "longitude",
            "start_date",
            "end_date",
            "daily",
            "timezone",
        }


# =============================================================================
# 3. Provenance on return.
# =============================================================================


@pytest.mark.asyncio
class TestProvenance:
    async def test_successful_evidence_names_its_source(self, provider, monkeypatch):
        server, recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server))

        result = await skill.execute("lookup", {"days": 1})

        prov = result.data["provenance"]
        assert prov["source_kind"] == "external_provider"
        assert prov["provider_host"] == "127.0.0.1"
        assert prov["evidence"] is True
        assert prov["succeeded"] is True
        assert prov["requested_at"].endswith("Z")

    async def test_provenance_records_exactly_what_was_disclosed(self, provider, monkeypatch):
        """The record of what was sent *is* what was sent."""
        server, recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server))

        result = await skill.execute("lookup", {"days": 1})

        disclosed = result.data["provenance"]["disclosed"]
        assert set(disclosed) == set(EGRESS_FIELDS)
        assert {k: str(v) for k, v in disclosed.items()} == recorder.received[0]["query"]

    async def test_failure_retains_provenance(self, provider, monkeypatch):
        """"We asked this provider and got nothing" is evidence too."""
        server, recorder = provider
        recorder.status = 500
        skill = await _skill(monkeypatch, _provider_url(server))

        result = await skill.execute("lookup", {"days": 1})

        assert not result.success
        prov = result.data["provenance"]
        assert prov["succeeded"] is False
        assert prov["provider_host"] == "127.0.0.1"
        assert result.data["attempted"] is True

    async def test_metadata_reports_attempted_separately_from_succeeded(
        self,
        provider,
        monkeypatch,
    ):
        server, _recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server))

        result = await skill.execute("lookup", {"days": 1})

        assert result.metadata["lookup"] == {
            "attempted": True,
            "succeeded": True,
            "outcome": "ok",
            "provider_host": "127.0.0.1",
        }


# =============================================================================
# 4. Truthful degradation: five distinct shapes, none of them a number.
# =============================================================================


@pytest.mark.asyncio
class TestTruthfulDegradation:
    async def test_unconfigured_provider_is_a_clean_unavailable(self, monkeypatch):
        skill = await _skill(monkeypatch, None)

        result = await skill.execute("lookup", {"days": 1})

        assert not result.success
        assert result.data["outcome"] == OUTCOME_UNCONFIGURED
        assert result.data["attempted"] is False
        assert skill.is_available is False

    async def test_declaration_and_availability_are_separate(self, provider, monkeypatch):
        """The capability exists whether or not it can be performed."""
        server, _recorder = provider
        unconfigured = await _skill(monkeypatch, None)
        configured = await _skill(monkeypatch, _provider_url(server))

        assert unconfigured.skill_id == configured.skill_id == "forecast"
        assert unconfigured.get_status()["provider_configured"] is False
        assert configured.get_status()["provider_configured"] is True

    async def test_provider_500_is_a_truthful_failure(self, provider, monkeypatch):
        server, recorder = provider
        recorder.status = 500
        skill = await _skill(monkeypatch, _provider_url(server))

        result = await skill.execute("lookup", {"days": 1})

        assert not result.success
        assert result.data["outcome"] == OUTCOME_PROVIDER_ERROR
        assert "500" in (result.error or "")
        assert result.data["days"] == []

    async def test_malformed_json_is_a_truthful_failure(self, provider, monkeypatch):
        server, recorder = provider
        recorder.body = "<html>not json</html>"
        skill = await _skill(monkeypatch, _provider_url(server))

        result = await skill.execute("lookup", {"days": 1})

        assert not result.success
        assert result.data["outcome"] == OUTCOME_MALFORMED

    async def test_inconsistent_arrays_are_refused_not_partially_used(
        self,
        provider,
        monkeypatch,
    ):
        """A half-assembled forecast is a fabricated forecast."""
        server, recorder = provider
        payload = _sample_payload(3)
        payload["daily"]["precipitation_sum"] = [1.0]  # shorter than `time`
        recorder.body = json.dumps(payload)
        skill = await _skill(monkeypatch, _provider_url(server))

        result = await skill.execute("lookup", {"days": 3})

        assert not result.success
        assert result.data["outcome"] == OUTCOME_MALFORMED
        assert result.data["days"] == []

    async def test_missing_daily_block_is_refused(self, provider, monkeypatch):
        server, recorder = provider
        recorder.body = json.dumps({"latitude": -33.87})
        skill = await _skill(monkeypatch, _provider_url(server))

        result = await skill.execute("lookup", {"days": 1})

        assert result.data["outcome"] == OUTCOME_MALFORMED

    async def test_unreachable_provider_is_a_truthful_failure(self, monkeypatch):
        # Port 1 on loopback: nothing listens, connection refused immediately.
        skill = await _skill(monkeypatch, "http://127.0.0.1:1/v1/forecast")

        result = await skill.execute("lookup", {"days": 1})

        assert not result.success
        assert result.data["outcome"] == OUTCOME_PROVIDER_ERROR
        assert result.data["days"] == []

    async def test_timeout_is_a_truthful_failure_not_an_estimate(self, monkeypatch):
        import requests

        def _timeout(*args, **kwargs):
            raise requests.Timeout("too slow")

        monkeypatch.setattr(forecast_module.requests, "get", _timeout)
        skill = await _skill(monkeypatch, "http://127.0.0.1:9/v1/forecast")

        result = await skill.execute("lookup", {"days": 1})

        assert not result.success
        assert result.data["outcome"] == OUTCOME_PROVIDER_ERROR
        assert "did not respond" in (result.error or "")

    async def test_no_result_shape_carries_a_number_on_failure(self, provider, monkeypatch):
        """Across every failure shape: no temperature, ever."""
        server, recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server))

        recorder.status = 503
        failure = await skill.execute("lookup", {"days": 1})
        recorder.status = 200
        recorder.body = "{}"
        malformed = await skill.execute("lookup", {"days": 1})

        for result in (failure, malformed):
            assert "temperature_max_c" not in json.dumps(result.data)

    async def test_permission_self_check_denies_without_the_permission(
        self,
        provider,
        monkeypatch,
    ):
        server, recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server), permitted=False)

        result = await skill.execute("lookup", {"days": 1})

        assert result.status == SkillResultStatus.PERMISSION_DENIED
        assert recorder.received == []

    async def test_unknown_action_is_refused(self, provider, monkeypatch):
        server, recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server))

        result = await skill.execute("nowcast", {})

        assert not result.success
        assert recorder.received == []

    async def test_out_of_range_horizon_is_refused_before_any_call(self, provider, monkeypatch):
        server, recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server))

        result = await skill.execute("lookup", {"days": 99})

        assert not result.success
        assert recorder.received == []

    async def test_skill_writes_nothing_durable(self, provider, monkeypatch, tmp_path):
        """External content has no path into governed memory in this slice."""
        server, _recorder = provider
        skill = await _skill(monkeypatch, _provider_url(server))
        before = set(tmp_path.iterdir())

        await skill.execute("lookup", {"days": 1})

        assert set(tmp_path.iterdir()) == before
        assert not hasattr(skill, "_db_path")


# =============================================================================
# 5. Executive authority: Bartholomew keeps the objective.
# =============================================================================


class TestExecutiveAuthority:
    def test_only_explicit_requests_reach_the_provider(self):
        """A false positive here is a disclosure, not a wasted call."""
        for utterance in [
            "it's freezing in here",
            "add a task to renew the rego",
            "I hate rainy days",
            "remember that the roof guy comes Thursday",
            "",
        ]:
            assert forecast_intents.parse_intent(utterance) is None, utterance

    def test_a_statement_about_forecasts_is_not_a_request(self):
        """A false positive here is a disclosure, not a wasted call.

        Found by sweeping the existing test corpus through the recogniser:
        a declarative sentence that merely contains "forecast" was matching,
        which would have sent a location outward on a sentence nobody
        intended as a request.
        """
        for utterance in [
            "the forecast yesterday was wrong",
            "I love a good rain forecast.",
            "a half-assembled forecast is a fabricated forecast",
            "we should talk about the weather outlook for the project.",
        ]:
            assert forecast_intents.parse_intent(utterance) is None, utterance

    def test_explicit_requests_are_recognised(self):
        today = date(2026, 8, 27)
        intent = forecast_intents.parse_intent("will it rain tomorrow?", today)
        assert intent.action == forecast_intents.INTENT_LOOKUP
        assert intent.params == {"start_date": "2026-08-28", "days": 1}
        assert intent.rain_focused is True

    def test_a_week_request_is_bounded(self):
        intent = forecast_intents.parse_intent("what's the weather this week", date(2026, 8, 27))
        assert intent.params["days"] == 7
        assert forecast_intents.parse_intent(
            "check the forecast for the next 40 days",
            date(2026, 8, 27),
        ).params["days"] == forecast_intents.MAX_DAYS

    def test_a_named_place_is_declined_not_silently_redirected(self):
        intent = forecast_intents.parse_intent("what's the weather in Melbourne?")
        assert intent.action == forecast_intents.INTENT_UNSUPPORTED_PLACE
        assert intent.place == "Melbourne"
        reply = forecast_intents.render_unsupported_place(intent)
        assert "Melbourne" in reply and "can't" in reply

    def test_the_reply_attributes_the_evidence_to_the_provider(self):
        intent = forecast_intents.parse_intent("will it rain tomorrow?", date(2026, 8, 27))
        evidence = {
            "days": [
                {
                    "date": "2026-08-28",
                    "temperature_min_c": 12.0,
                    "temperature_max_c": 22.5,
                    "precipitation_mm": 4.2,
                    "precipitation_probability_pct": 80,
                },
            ],
            "provenance": {"provider_host": "api.open-meteo.com"},
        }

        reply = forecast_intents.render_forecast(evidence, intent)

        assert "api.open-meteo.com" in reply
        assert "not something I know independently" in reply
        assert "80%" in reply

    def test_the_executive_draws_the_conclusion_the_user_asked_for(self):
        """The provider supplied a probability; the judgement is Bartholomew's."""
        intent = forecast_intents.parse_intent("will it rain tomorrow?", date(2026, 8, 27))
        wet = forecast_intents.render_forecast(
            {
                "days": [{"date": "d", "precipitation_probability_pct": 90}],
                "provenance": {"provider_host": "h"},
            },
            intent,
        )
        dry = forecast_intents.render_forecast(
            {
                "days": [{"date": "d", "precipitation_probability_pct": 5}],
                "provenance": {"provider_host": "h"},
            },
            intent,
        )
        borderline = forecast_intents.render_forecast(
            {
                "days": [{"date": "d", "precipitation_probability_pct": 45}],
                "provenance": {"provider_host": "h"},
            },
            intent,
        )

        assert "plan for rain" in wet
        assert "unlikely" in dry
        # No verdict where the evidence does not support one.
        assert "plan for rain" not in borderline and "unlikely" not in borderline

    def test_failure_renderings_never_imply_a_forecast(self):
        intent = forecast_intents.parse_intent("will it rain tomorrow?", date(2026, 8, 27))
        for reply in (
            forecast_intents.render_failure(intent, "the provider returned HTTP 500"),
            forecast_intents.render_unavailable("no provider is configured."),
            forecast_intents.render_denied("permission was refused."),
        ):
            assert "°C" not in reply and "%" not in reply

    def test_the_recogniser_is_pure(self):
        """No I/O, no execution, no network -- the governed seam owns those.

        Asserted over the parsed module, not its text, so the docstrings that
        *describe* the governed seam do not read as calls into it.
        """
        import ast

        tree = ast.parse(
            (REPO_ROOT / "bartholomew" / "kernel" / "forecast_intents.py").read_text(),
        )
        imported = {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert imported <= {"__future__", "re", "dataclasses", "datetime", "typing"}, imported

        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        } | {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        # `get` is excluded deliberately: it is dict.get here, and the
        # import assertion above already proves no HTTP client is reachable.
        for forbidden in ("execute_action", "open", "connect", "post", "request"):
            assert forbidden not in called, forbidden


# =============================================================================
# 6. Replaceability: provider knowledge stays where it was put.
# =============================================================================


class TestReplaceability:
    def test_no_provider_name_leaks_into_the_architecture(self):
        """`DECISIONS.md` clause (b): no provider-named cognitive authority."""
        for path in [
            REPO_ROOT / "bartholomew" / "kernel" / "forecast_intents.py",
            REPO_ROOT / "bartholomew" / "kernel" / "runtime_contract.py",
            REPO_ROOT / "bartholomew" / "kernel" / "skill_registry.py",
        ]:
            source = path.read_text().lower()
            assert "open-meteo" not in source and "open_meteo" not in source, path

    def test_the_endpoint_is_configuration_not_a_default(self):
        """An unconfigured Bartholomew makes no outbound calls at all."""
        source = (REPO_ROOT / "bartholomew" / "skills" / "forecast.py").read_text()
        assert "OPEN_METEO_FORECAST_URL" in source
        # The reference constant must not be wired in as a fallback.
        assert "or OPEN_METEO_FORECAST_URL" not in source
        assert "= OPEN_METEO_FORECAST_URL" not in source

    def test_provider_knowledge_is_confined_to_two_places(self):
        """Swapping providers means editing `_DAILY_VARIABLES` and `_map_response`."""
        source = (REPO_ROOT / "bartholomew" / "skills" / "forecast.py").read_text()
        for name in ("temperature_2m_max", "precipitation_probability_max"):
            occurrences = source.count(name)
            # Once in _DAILY_VARIABLES, once in _map_response's mapping.
            assert occurrences <= 2, f"{name} appears {occurrences} times"

    def test_no_broker_or_registry_was_introduced(self):
        """Clause (f): a selection mechanism with one option is not a mechanism."""
        source = (REPO_ROOT / "bartholomew" / "skills" / "forecast.py").read_text().lower()
        for forbidden in ("class providerregistry", "class capabilitybroker", "def select_provider"):
            assert forbidden not in source


# =============================================================================
# 7. The full Runtime Contract path: audit row and Reflection.
# =============================================================================


@pytest.mark.asyncio
class TestRuntimeContractEvidence:
    async def _rows(self, db_path: str, table: str, where: str = "") -> list:
        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(f"SELECT * FROM {table} {where}")
            return [dict(r) for r in await cur.fetchall()]

    async def test_a_lookup_writes_an_audit_row(self, governed, monkeypatch):
        registry, _recorder, _mem, db_path = governed
        _grant_consent(monkeypatch)

        await registry.execute_action("forecast", "lookup", {"days": 1})

        rows = await self._rows(db_path, "skill_action_audit", "WHERE skill_id='forecast'")
        assert len(rows) == 1
        assert rows[0]["action"] == "lookup"

    async def test_a_braked_lookup_is_still_audited(self, governed, monkeypatch):
        """The refusal is on the record, not only the success."""
        registry, recorder, _mem, db_path = governed
        _grant_consent(monkeypatch)
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        GovernanceStore(db_path).engage("skills")

        await registry.execute_action("forecast", "lookup", {"days": 1})

        rows = await self._rows(db_path, "skill_action_audit", "WHERE skill_id='forecast'")
        assert len(rows) == 1
        assert recorder.received == []

    async def test_a_lookup_writes_the_unified_reflection(self, governed, monkeypatch):
        registry, _recorder, mem, _db = governed
        _grant_consent(monkeypatch)

        await registry.execute_action("forecast", "lookup", {"days": 1})

        import aiosqlite

        async with aiosqlite.connect(mem.db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM reflections WHERE kind=?",
                (REFLECTION_KIND,),
            )
            (count,) = await cur.fetchone()
        assert count >= 1

    async def test_the_production_entry_point_reaches_the_provider(self, governed, monkeypatch):
        """`run_skill_through_runtime_contract()` -- the named seam, not a
        direct skill call -- is a working route to the external capability."""
        from bartholomew.kernel.runtime_contract import run_skill_through_runtime_contract

        registry, recorder, _mem, _db = governed
        _grant_consent(monkeypatch)

        result = await run_skill_through_runtime_contract(
            registry,
            "forecast",
            "lookup",
            {"days": 1},
        )

        assert result.success
        assert len(recorder.received) == 1
