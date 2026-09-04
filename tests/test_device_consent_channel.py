"""The operator-reachable consent channel for device observation starts.

On the live Windows golden path (docs/G_WINDOWS_COMPANION_COMPLETION.md §9,
Finding 1) every observation start refused with "No consent handler
registered (fail-closed)": the API server had no channel through which a
person could answer the Runtime Contract's consent gate. This suite holds
the repair to the properties that make the gate mean something:

* fail-closed: no channel, a timeout, a declined answer, or a channel that
  errors all deny -- and the existing wording for "no handler" is unchanged;
* one ask, one answer, one start attempt -- nothing is remembered;
* the companion cannot answer its own ask: the answer needs a nonce that
  lives only in the kernel database, the routes refuse the device
  credential, and no response the requester sees carries the nonce;
* registering the device channel does not register the memory-write consent
  handler, so queued sensitive writes keep queueing;
* the ask is bounded per tenant.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from bartholomew.kernel import runtime_contract as rc
from bartholomew.kernel.memory.privacy_guard import (
    DeviceConsentRequest,
    get_consent_handler,
    get_device_consent_handler,
    set_consent_handler,
    set_device_consent_handler,
)
from bartholomew.multimodal import device_consent
from bartholomew.platform.capabilities import Capability
from bartholomew.platform.device_inbound import DEVICE_CREDENTIAL_HEADER
from bartholomew.platform.route_policy import capability_for

TENANT = "user-1"
DEVICE = "device-1"


@pytest.fixture(autouse=True)
def _clean_handlers():
    set_consent_handler(None)
    set_device_consent_handler(None)
    device_consent.reset_for_tests()
    yield
    set_consent_handler(None)
    set_device_consent_handler(None)
    device_consent.reset_for_tests()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "barth.db")


def _request(**overrides: Any) -> DeviceConsentRequest:
    fields = {
        "prompt": "Bartholomew requests to start screen capture (single start attempt)",
        "tenant_id": TENANT,
        "principal_id": TENANT,
        "device_id": DEVICE,
        "modality": "screen",
        "correlation_id": "cor-1",
        "session_id": "mms_1",
    }
    fields.update(overrides)
    return DeviceConsentRequest(**fields)


# ===========================================================================
# The seam: which channel answers, and how it fails
# ===========================================================================


async def test_no_handler_of_either_kind_still_refuses_with_the_same_words():
    allowed, outcome, reason = await rc._resolve_device_consent("screen capture")
    assert (allowed, outcome) == (False, "consent_denied")
    assert reason == "No consent handler registered (fail-closed)"


async def test_the_plain_handler_still_serves_when_no_device_channel_exists():
    seen = []

    def handler(prompt: str) -> bool:
        seen.append(prompt)
        return True

    set_consent_handler(handler)
    allowed, outcome, _ = await rc._resolve_device_consent("screen capture")
    assert (allowed, outcome) == (True, "started")
    assert seen == ["Bartholomew requests to start screen capture (single start attempt)"]


async def test_the_device_channel_is_consulted_first_and_receives_context():
    plain_calls = []
    set_consent_handler(lambda prompt: plain_calls.append(prompt) or True)
    received = []

    async def device_handler(request: DeviceConsentRequest) -> bool:
        received.append(request)
        return True

    set_device_consent_handler(device_handler)
    allowed, outcome, _ = await rc._resolve_device_consent(
        "screen capture",
        context={
            "tenant_id": TENANT,
            "principal_id": TENANT,
            "device_id": DEVICE,
            "modality": "screen",
            "correlation_id": "cor-1",
            "session_id": "mms_1",
            "not_a_field": "ignored",
        },
    )
    assert (allowed, outcome) == (True, "started")
    assert plain_calls == [], "the plain handler must not be asked when the device channel answers"
    (request,) = received
    assert request.prompt == "Bartholomew requests to start screen capture (single start attempt)"
    assert (request.tenant_id, request.device_id, request.modality) == (TENANT, DEVICE, "screen")


async def test_a_declined_device_answer_denies():
    set_device_consent_handler(lambda request: False)
    allowed, outcome, reason = await rc._resolve_device_consent("screen capture")
    assert (allowed, outcome) == (False, "consent_denied")
    assert reason == "Device consent declined or unresolved"


async def test_a_device_channel_that_raises_denies_rather_than_raising():
    def broken(request: DeviceConsentRequest) -> bool:
        raise RuntimeError("channel exploded")

    set_device_consent_handler(broken)
    allowed, outcome, reason = await rc._resolve_device_consent("screen capture")
    assert (allowed, outcome) == (False, "consent_denied")
    assert "fail-closed" in reason


# ===========================================================================
# Installing the channel does not move memory consent
# ===========================================================================


def test_installing_the_channel_registers_only_the_device_handler(db_path):
    assert device_consent.install(db_path=db_path) is True
    assert get_device_consent_handler() is device_consent.ask
    # MemoryStore.upsert_memory branches on this being None: queued sensitive
    # writes must keep queueing, not start being asked-and-discarded.
    assert get_consent_handler() is None


def test_install_does_not_override_a_foreign_device_handler(db_path):
    mine = lambda request: True  # noqa: E731
    set_device_consent_handler(mine)
    assert device_consent.install(db_path=db_path) is False
    assert get_device_consent_handler() is mine


def test_uninstall_only_removes_its_own_handler(db_path):
    device_consent.install(db_path=db_path)
    device_consent.uninstall()
    assert get_device_consent_handler() is None
    other = lambda request: True  # noqa: E731
    set_device_consent_handler(other)
    device_consent.uninstall()
    assert get_device_consent_handler() is other


# ===========================================================================
# One ask, answered or not
# ===========================================================================


async def test_an_unanswered_ask_expires_and_denies(db_path):
    device_consent.configure(db_path=db_path, ttl_seconds=1)
    assert await device_consent.ask(_request()) is False
    rows = device_consent.list_pending(db_path)
    assert rows == [], "an expired ask must not remain listed as pending"


async def _open_ask(db_path: str, **overrides: Any):
    task = asyncio.create_task(device_consent.ask(_request(**overrides)))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if device_consent.list_pending(db_path):
            break
    (row,) = [
        r
        for r in device_consent.list_pending(db_path, include_nonce=True)
        if r["tenant_id"] == overrides.get("tenant_id", TENANT)
    ][-1:]
    return task, row


async def test_an_approved_ask_resolves_the_waiting_start_exactly_once(db_path):
    device_consent.configure(db_path=db_path, ttl_seconds=30)
    task, row = await _open_ask(db_path)

    outcome = device_consent.answer(
        db_path,
        row["request_id"],
        nonce=row["answer_nonce"],
        approve=True,
        decided_by="tester",
    )
    assert outcome.outcome == "approved"
    assert outcome.resolved_a_waiting_start is True
    assert await asyncio.wait_for(task, 5) is True

    # A second answer to the same ask is not retroactive and resolves nothing.
    again = device_consent.answer(
        db_path,
        row["request_id"],
        nonce=row["answer_nonce"],
        approve=False,
    )
    assert again.outcome == "already_decided"
    assert again.resolved_a_waiting_start is False
    assert device_consent.list_pending(db_path) == []


async def test_a_denied_ask_refuses_the_waiting_start(db_path):
    device_consent.configure(db_path=db_path, ttl_seconds=30)
    task, row = await _open_ask(db_path)
    outcome = device_consent.answer(
        db_path,
        row["request_id"],
        nonce=row["answer_nonce"],
        approve=False,
    )
    assert outcome.outcome == "denied"
    assert await asyncio.wait_for(task, 5) is False


async def test_a_wrong_nonce_is_refused_and_the_ask_stays_open(db_path):
    device_consent.configure(db_path=db_path, ttl_seconds=30)
    task, row = await _open_ask(db_path)
    outcome = device_consent.answer(
        db_path,
        row["request_id"],
        nonce="not-the-nonce",
        approve=True,
    )
    assert outcome.outcome == "refused"
    assert not task.done()
    assert device_consent.list_pending(db_path)[0]["request_id"] == row["request_id"]
    # Clean up: deny it properly.
    device_consent.answer(db_path, row["request_id"], nonce=row["answer_nonce"], approve=False)
    assert await asyncio.wait_for(task, 5) is False


async def test_an_answer_for_another_tenant_reads_as_unknown(db_path):
    device_consent.configure(db_path=db_path, ttl_seconds=30)
    task, row = await _open_ask(db_path)
    outcome = device_consent.answer(
        db_path,
        row["request_id"],
        nonce=row["answer_nonce"],
        approve=True,
        tenant_id="someone-else",
    )
    assert outcome.outcome == "unknown"
    assert not task.done()
    device_consent.answer(db_path, row["request_id"], nonce=row["answer_nonce"], approve=False)
    await asyncio.wait_for(task, 5)


async def test_a_late_answer_after_expiry_cannot_resurrect_the_start(db_path):
    device_consent.configure(db_path=db_path, ttl_seconds=1)
    task, row = await _open_ask(db_path)
    assert await asyncio.wait_for(task, 5) is False
    outcome = device_consent.answer(
        db_path,
        row["request_id"],
        nonce=row["answer_nonce"],
        approve=True,
    )
    assert outcome.outcome in ("expired", "already_decided")
    assert outcome.resolved_a_waiting_start is False


async def test_every_start_attempt_asks_again(db_path):
    """A grant is a single start attempt. The next attempt mints a new ask
    with a new nonce and waits for its own answer."""
    device_consent.configure(db_path=db_path, ttl_seconds=30)
    task1, row1 = await _open_ask(db_path)
    device_consent.answer(db_path, row1["request_id"], nonce=row1["answer_nonce"], approve=True)
    assert await asyncio.wait_for(task1, 5) is True

    task2, row2 = await _open_ask(db_path)
    assert row2["request_id"] != row1["request_id"]
    assert row2["answer_nonce"] != row1["answer_nonce"]
    assert not task2.done(), "the earlier grant must not answer the new ask"
    # The old nonce does not answer the new ask.
    assert (
        device_consent.answer(
            db_path,
            row2["request_id"],
            nonce=row1["answer_nonce"],
            approve=True,
        ).outcome
        == "refused"
    )
    device_consent.answer(db_path, row2["request_id"], nonce=row2["answer_nonce"], approve=False)
    assert await asyncio.wait_for(task2, 5) is False


async def test_open_asks_are_bounded_per_tenant(db_path):
    device_consent.configure(db_path=db_path, ttl_seconds=30)
    tasks = []
    rows = []
    for _ in range(device_consent.MAX_PENDING_PER_TENANT):
        task, row = await _open_ask(db_path)
        tasks.append(task)
        rows.append(row)
    assert await device_consent.ask(_request()) is False, "the cap denies immediately"
    for row in rows:
        device_consent.answer(db_path, row["request_id"], nonce=row["answer_nonce"], approve=False)
    for task in tasks:
        assert await asyncio.wait_for(task, 5) is False


async def test_an_ask_with_no_channel_configured_denies():
    assert await device_consent.ask(_request()) is False


async def test_an_ask_with_no_tenant_denies(db_path):
    device_consent.configure(db_path=db_path, ttl_seconds=30)
    assert await device_consent.ask(_request(tenant_id="")) is False


# ===========================================================================
# Through the Runtime Contract seam
# ===========================================================================


async def test_the_seam_refuses_on_timeout_and_the_result_carries_no_nonce(db_path, tmp_path):
    device_consent.install(db_path=db_path, ttl_seconds=1)
    result = await rc.run_multimodal_session_through_runtime_contract(
        "screen",
        db_path=str(tmp_path / "kernel.db"),
        capability_supported=True,
        consent_context={"tenant_id": TENANT, "principal_id": TENANT, "device_id": DEVICE},
    )
    assert result.governance_allowed is False
    assert result.outcome == "consent_denied"
    assert "dcr-" not in str(result.reason)


async def test_the_seam_starts_after_an_operator_approves(db_path, tmp_path):
    device_consent.install(db_path=db_path, ttl_seconds=30)

    async def operator_approves_when_asked():
        for _ in range(200):
            await asyncio.sleep(0.02)
            open_asks = device_consent.list_pending(db_path, include_nonce=True)
            if open_asks:
                row = open_asks[0]
                assert row["device_id"] == DEVICE and row["modality"] == "screen"
                assert "screen" in row["prompt"].lower() or "sight" in row["prompt"].lower()
                return device_consent.answer(
                    db_path,
                    row["request_id"],
                    nonce=row["answer_nonce"],
                    approve=True,
                    decided_by="tester",
                )
        raise AssertionError("no ask appeared")

    seam = rc.run_multimodal_session_through_runtime_contract(
        "screen",
        db_path=str(tmp_path / "kernel.db"),
        capability_supported=True,
        consent_context={
            "tenant_id": TENANT,
            "principal_id": TENANT,
            "device_id": DEVICE,
            "modality": "screen",
        },
    )
    result, answered = await asyncio.gather(seam, operator_approves_when_asked())
    assert answered.outcome == "approved"
    assert result.governance_allowed is True
    assert result.outcome == "started"


# ===========================================================================
# The HTTP surface: no nonce out, no device credential in
# ===========================================================================


@pytest.fixture
def consent_client(db_path, monkeypatch):
    from bartholomew_api_bridge_v0_1.services.api.routes import device_consent as routes

    monkeypatch.setattr(routes, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(routes, "resolved_tenant_id", lambda request: TENANT)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        yield client


async def test_pending_never_carries_the_nonce_and_refuses_the_device_credential(
    consent_client,
    db_path,
):
    device_consent.configure(db_path=db_path, ttl_seconds=30)
    task, row = await _open_ask(db_path)

    listed = await asyncio.to_thread(consent_client.get, "/api/device-consent/pending")
    assert listed.status_code == 200
    body = listed.json()
    assert body["pending"][0]["request_id"] == row["request_id"]
    assert "answer_nonce" not in body["pending"][0]
    assert row["answer_nonce"] not in listed.text

    refused = await asyncio.to_thread(
        consent_client.get,
        "/api/device-consent/pending",
        headers={DEVICE_CREDENTIAL_HEADER: "any"},
    )
    assert refused.status_code == 403

    device_consent.answer(db_path, row["request_id"], nonce=row["answer_nonce"], approve=False)
    await asyncio.wait_for(task, 5)


async def test_the_answer_route_refuses_the_device_credential_and_a_wrong_nonce(
    consent_client,
    db_path,
):
    device_consent.configure(db_path=db_path, ttl_seconds=30)
    task, row = await _open_ask(db_path)
    url = f"/api/device-consent/{row['request_id']}/answer"

    with_credential = await asyncio.to_thread(
        consent_client.post,
        url,
        json={"nonce": row["answer_nonce"], "approve": True},
        headers={DEVICE_CREDENTIAL_HEADER: "the-device"},
    )
    assert with_credential.status_code == 403
    assert not task.done()

    wrong = await asyncio.to_thread(
        consent_client.post,
        url,
        json={"nonce": "guess", "approve": True},
    )
    assert wrong.status_code == 403
    assert not task.done()

    unknown = await asyncio.to_thread(
        consent_client.post,
        "/api/device-consent/dcr-nope/answer",
        json={"nonce": "x", "approve": True},
    )
    assert unknown.status_code == 404

    right = await asyncio.to_thread(
        consent_client.post,
        url,
        json={"nonce": row["answer_nonce"], "approve": True, "note": "live test"},
    )
    assert right.status_code == 200, right.text
    assert right.json()["outcome"] == "approved"
    assert row["answer_nonce"] not in right.text
    assert await asyncio.wait_for(task, 5) is True

    again = await asyncio.to_thread(
        consent_client.post,
        url,
        json={"nonce": row["answer_nonce"], "approve": True},
    )
    assert again.status_code == 409


def test_the_routes_are_classified_for_the_person_not_the_device():
    assert capability_for("GET", "/api/device-consent/pending") is Capability.SELF_READ
    assert (
        capability_for("POST", "/api/device-consent/{request_id}/answer")
        is Capability.CONSENT_DECIDE
    )


# ===========================================================================
# The operator CLI reads the nonce from the database and sends no credential
# ===========================================================================


async def test_cli_approve_reads_the_nonce_from_the_database_and_sends_no_credential(
    db_path,
    monkeypatch,
):
    import requests

    from bartholomew.cli import app

    device_consent.configure(db_path=db_path, ttl_seconds=30)
    task, row = await _open_ask(db_path)
    captured: dict[str, Any] = {}

    class _Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {"outcome": "approved"}

    def fake_post(url, json=None, timeout=None, headers=None, **kwargs):
        captured.update(url=url, json=json, headers=headers)
        return _Response()

    monkeypatch.setattr(requests, "post", fake_post)
    runner = CliRunner()

    listed = runner.invoke(app, ["consent", "pending", "--db", db_path])
    assert listed.exit_code == 0, listed.output
    assert row["request_id"] in listed.output
    assert row["answer_nonce"] not in listed.output

    result = runner.invoke(app, ["consent", "approve", row["request_id"], "--db", db_path])
    assert result.exit_code == 0, result.output
    assert captured["url"].endswith(f"/api/device-consent/{row['request_id']}/answer")
    assert captured["json"] == {"nonce": row["answer_nonce"], "approve": True, "note": None}
    assert not captured["headers"], "the consent CLI must never send a device credential"

    device_consent.answer(db_path, row["request_id"], nonce=row["answer_nonce"], approve=False)
    await asyncio.wait_for(task, 5)


def test_cli_refuses_to_send_a_nonce_over_plaintext_off_loopback(db_path):
    from bartholomew.cli import app

    result = CliRunner().invoke(
        app,
        ["consent", "approve", "dcr-x", "--db", db_path, "--base-url", "http://10.0.0.5:8000"],
    )
    assert result.exit_code == 2
    assert "Refusing" in (result.output + str(result.stderr_bytes or b""))


# ===========================================================================
# A brake engaged while the person is deciding still stops the start
# ===========================================================================


async def test_a_brake_engaged_during_the_wait_denies_even_after_approval(db_path, tmp_path):
    """Consent can take minutes. The brake read at gate 2 is stale by the
    time the person answers; the seam re-reads it at the moment of action."""
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    kernel_db = str(tmp_path / "kernel.db")
    device_consent.install(db_path=db_path, ttl_seconds=30)

    async def engage_brake_then_approve():
        for _ in range(200):
            await asyncio.sleep(0.02)
            open_asks = device_consent.list_pending(db_path, include_nonce=True)
            if open_asks:
                GovernanceStore(kernel_db).engage("global", reason="test", actor="tester")
                row = open_asks[0]
                return device_consent.answer(
                    db_path,
                    row["request_id"],
                    nonce=row["answer_nonce"],
                    approve=True,
                )
        raise AssertionError("no ask appeared")

    seam = rc.run_multimodal_session_through_runtime_contract(
        "screen",
        db_path=kernel_db,
        capability_supported=True,
        consent_context={"tenant_id": TENANT, "principal_id": TENANT, "device_id": DEVICE},
    )
    result, answered = await asyncio.gather(seam, engage_brake_then_approve())
    assert answered.outcome == "approved", "the person did approve"
    assert result.governance_allowed is False
    assert result.outcome == "parking_brake_denied"
