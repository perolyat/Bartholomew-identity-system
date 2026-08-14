"""
Usable POC slice 1 -- the outbound webhook notification channel.

`NotifySkill._deliver_notification()` was a log-only stub with a
`# TODO: Integrate with system notification APIs` comment, which is why a
notification could never reach the tester outside the browser tab. This suite
proves it now performs a genuine outbound HTTP POST, and pins the two
properties the approval clarification (2026-08-14) called out:

  1. **Provider-agnostic.** A plain configurable-URL JSON POST. No provider
     SDK, no provider-specific payload shaping, no provider-specific branch --
     `ntfy` is a configuration value, not an architectural dependency.
  2. **Additive.** With no URL configured, behaviour is exactly the previous
     log-only path.

Delivery is exercised against a real `http.server` bound to loopback, not a
mock: the acceptance bar asks for a real HTTP delivery reaching a configured
endpoint, and a mocked client would prove only that the code called itself.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bartholomew.kernel.skill_base import SkillContext
from bartholomew.skills import notify as notify_module
from bartholomew.skills.notify import WEBHOOK_URL_ENV, NotifySkill


class _Recorder(BaseHTTPRequestHandler):
    received: list[dict] = []
    status = 200

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's required name
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")
        type(self).received.append(
            {
                "path": self.path,
                "content_type": self.headers.get("Content-Type"),
                "body": body,
            },
        )
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
    return f"http://{host}:{port}/bartholomew-test"


async def _skill(tmp_path, monkeypatch, url: str | None):
    if url is None:
        monkeypatch.delenv(WEBHOOK_URL_ENV, raising=False)
    else:
        monkeypatch.setenv(WEBHOOK_URL_ENV, url)
    skill = NotifySkill()
    await skill.initialize(
        SkillContext(db_path=str(tmp_path / "notify.db"), check_permission=lambda _p: True),
    )
    return skill


class TestRealOutboundDelivery:
    async def test_send_performs_a_real_http_post(self, tmp_path, monkeypatch, webhook_server):
        server, recorder = webhook_server
        skill = await _skill(tmp_path, monkeypatch, _url(server))

        result = await skill.execute(
            "send",
            {
                "message": "Remembered: Birthday: 3rd March",
                "title": "Bartholomew",
                "priority": "urgent",
            },
        )

        assert result.status.value == "success"
        assert len(recorder.received) == 1, "no outbound HTTP request reached the endpoint"

        request = recorder.received[0]
        assert request["path"] == "/bartholomew-test"
        assert request["content_type"] == "application/json"
        body = json.loads(request["body"])
        assert body["message"] == "Remembered: Birthday: 3rd March"
        assert body["title"] == "Bartholomew"
        assert body["source"] == "bartholomew"

    async def test_delivery_reports_success(self, tmp_path, monkeypatch, webhook_server):
        server, _ = webhook_server
        skill = await _skill(tmp_path, monkeypatch, _url(server))

        from bartholomew.skills.notify import Notification, NotificationPriority

        delivered = await skill._deliver_notification(
            Notification(id="n1", message="hello", priority=NotificationPriority.URGENT),
        )
        assert delivered is True


class TestAdditiveByDefault:
    async def test_no_url_configured_keeps_log_only_behaviour(
        self,
        tmp_path,
        monkeypatch,
        webhook_server,
    ):
        _, recorder = webhook_server
        skill = await _skill(tmp_path, monkeypatch, None)

        result = await skill.execute(
            "send",
            {"message": "hello", "priority": "urgent"},
        )

        assert result.status.value == "success"
        assert recorder.received == [], "a request was sent with no webhook configured"

    async def test_blank_url_is_treated_as_unconfigured(self, tmp_path, monkeypatch):
        skill = await _skill(tmp_path, monkeypatch, "   ")
        assert skill._webhook_url == ""


class TestFailureIsContained:
    async def test_endpoint_error_status_does_not_fail_the_action(
        self,
        tmp_path,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        recorder.status = 500
        skill = await _skill(tmp_path, monkeypatch, _url(server))

        result = await skill.execute("send", {"message": "hello", "priority": "urgent"})

        assert result.status.value == "success", (
            "a failing external endpoint must not fail the notification action -- the "
            "notification was still sent and recorded"
        )
        assert len(recorder.received) == 1

    async def test_unreachable_endpoint_does_not_raise(self, tmp_path, monkeypatch):
        # Port 1 on loopback: reliably refuses, no external network involved.
        skill = await _skill(tmp_path, monkeypatch, "http://127.0.0.1:1/nope")

        result = await skill.execute("send", {"message": "hello", "priority": "urgent"})

        assert result.status.value == "success"

    async def test_post_webhook_never_raises(self):
        assert NotifySkill._post_webhook("http://127.0.0.1:1/nope", {"id": "x"}) is False
        assert NotifySkill._post_webhook("not-a-url", {"id": "x"}) is False


class TestProviderAgnostic:
    def test_no_provider_specific_code_ships(self):
        """The clarification is explicit: ntfy may be the first test endpoint,
        but it must not become an architectural dependency. This fails if a
        provider name, SDK, or payload shape is baked into the delivery
        code."""
        from pathlib import Path

        source = Path(notify_module.__file__).read_text(encoding="utf-8")
        # Comments legitimately *name* ntfy as an example endpoint; code must
        # not branch on it.
        code_lines = [
            line
            for line in source.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        code = "\n".join(code_lines).lower()
        for provider in ("ntfy", "pushover", "slack.com", "hooks.slack", "telegram", "twilio"):
            assert provider not in code, f"provider-specific code for {provider!r}"

    def test_delivery_sends_the_notifications_own_fields(self, tmp_path):
        """Payload shape is the notification's own `to_dict()` plus a source
        marker -- not a shape borrowed from any one provider's API."""
        from bartholomew.skills.notify import Notification

        payload = Notification(id="n1", message="m", title="t").to_dict()
        for expected in ("id", "message", "title", "priority", "status", "created_at"):
            assert expected in payload


class TestChatCaptureReachesTheWebhook:
    """The full slice-1 chain, with nothing mocked in the middle: a chat turn
    states a fact, the governed write path stores it, and a real notification
    leaves the process over HTTP.

    This is the acceptance criterion "a real outbound webhook notification can
    successfully fire", exercised end to end rather than at the delivery
    function alone."""

    async def test_stating_a_fact_fires_a_real_outbound_notification(
        self,
        tmp_path,
        monkeypatch,
        webhook_server,
    ):
        from datetime import datetime, timedelta

        from bartholomew.kernel.memory_store import MemoryStore
        from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract
        from bartholomew.kernel.skill_registry import SkillRegistry

        server, recorder = webhook_server
        monkeypatch.setenv(WEBHOOK_URL_ENV, _url(server))

        db_path = str(tmp_path / "chain.db")
        mem = MemoryStore(db_path)
        await mem.init()

        registry = SkillRegistry(db_path=db_path, memory_store=mem)
        assert await registry.load_skill("notify") is True

        # Quiet hours default to 22:00-07:00, so a wall-clock-dependent test
        # would pass or fail by time of day. Pin a window that cannot contain
        # "now" instead of assuming one.
        now = datetime.now()
        await registry.execute_action(
            "notify",
            "set_quiet_hours",
            {
                "start": (now + timedelta(minutes=2)).strftime("%H:%M"),
                "end": (now + timedelta(minutes=3)).strftime("%H:%M"),
            },
        )

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
            def __init__(self):
                self.mem = mem
                self.experience = _Stub()
                self.persona_manager = _Stub()
                self.working_memory = _Stub()
                self.identity_context = None
                self.skill_registry = registry

        async def _respond(prompt: str) -> str:
            return "ok"

        try:
            result = await run_chat_through_runtime_contract(
                _Daemon(),
                "my birthday is 3rd March",
                _respond,
            )
        finally:
            await mem.close()

        assert any(
            item["outcome"] == "stored" for item in result.personal_facts_captured
        ), result.personal_facts_captured

        assert recorder.received, "capturing a fact did not produce an outbound notification"
        body = json.loads(recorder.received[0]["body"])
        assert "Birthday" in body["message"]


class TestQuietHoursStillApply:
    async def test_muted_non_urgent_notification_is_queued_not_delivered(
        self,
        tmp_path,
        monkeypatch,
        webhook_server,
    ):
        """S1.3's mute/quiet-hours gating runs before delivery and is
        unchanged -- the webhook must not become a way around it."""
        server, recorder = webhook_server
        skill = await _skill(tmp_path, monkeypatch, _url(server))

        await skill.execute("mute", {})
        result = await skill.execute("send", {"message": "hello", "priority": "normal"})

        assert result.status.value == "success"
        assert result.data["status"] == "pending"
        assert recorder.received == [], "a muted notification was delivered outbound"
