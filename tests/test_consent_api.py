"""
HTTP-level tests for bartholomew_api_bridge_v0_1's consent router
(/api/consent/pending-writes, .../approve, .../deny) -- the consent-handler
fix (2026-08). Follows the same pattern as tests/test_governance_api.py: a
module-scoped TestClient over the real app (real KernelDaemon startup,
real kernel.mem = MemoryStore, no consent handler registered -- the true
headless case this fix addresses).
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

_db_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
_db_dir.mkdir(parents=True, exist_ok=True)
_DB_PATH = str(_db_dir / "test.db")
os.environ["BARTH_DB_PATH"] = _DB_PATH

from bartholomew_api_bridge_v0_1.services.api import app as app_module  # noqa: E402

SENSITIVE_BUT_STORABLE = "my daily routine starts at 6am"
# Matches the live ask_before_store content pattern -> a reason='rule_consent'
# pending write (S1.2), distinct from the privacy_guard-gated content above.
# See tests/test_memory_store_rule_consent.py for why this exact phrasing
# was chosen (avoids privacy_guard keyword overlap and a separate,
# higher-priority never_store rule that also matches "personal information").
ASK_BEFORE_STORE_CONTENT = "Please remember my auth code for later."
TS = "2026-01-01T00:00:00Z"


@pytest.fixture(scope="module")
def client():
    # Re-assert right before starting the app -- see
    # tests/test_api_admission_gate.py's client fixture for why (pytest
    # collects/imports every test module, running each one's own
    # os.environ[...] assignment, before running any test; a
    # later-collected module can overwrite this by the time this fixture
    # actually fires).
    os.environ["BARTH_DB_PATH"] = _DB_PATH
    with TestClient(app_module.app) as c:
        yield c


async def _queue_sensitive_write(key: str) -> None:
    """Seed a pending sensitive write the same way a real, unconsented
    write would produce one -- through upsert_memory() itself, not by
    inserting into pending_sensitive_writes directly."""
    result = await app_module._kernel.mem.upsert_memory(
        kind="chat",
        key=key,
        value=SENSITIVE_BUT_STORABLE,
        ts=TS,
    )
    assert result.stored is False


def test_pending_writes_starts_empty(client):
    response = client.get("/api/consent/pending-writes")
    assert response.status_code == 200
    body = response.json()
    assert body["entries"] == []
    assert body["count"] == 0


@pytest.mark.asyncio
async def test_pending_writes_reflects_a_queued_sensitive_write(client):
    await _queue_sensitive_write("api-queued-1")

    response = client.get("/api/consent/pending-writes")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["entries"][0]["kind"] == "chat"
    assert body["entries"][0]["key"] == "api-queued-1"
    assert body["entries"][0]["value"] == SENSITIVE_BUT_STORABLE


@pytest.mark.asyncio
async def test_approve_stores_and_clears_pending(client):
    await _queue_sensitive_write("api-approve-me")
    pending_id = client.get("/api/consent/pending-writes").json()["entries"][0]["id"]

    response = client.post(f"/api/consent/pending-writes/{pending_id}/approve")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["stored"] is True
    assert body["memory_id"] is not None

    remaining = client.get("/api/consent/pending-writes").json()["entries"]
    assert all(e["key"] != "api-approve-me" for e in remaining)


@pytest.mark.asyncio
async def test_deny_clears_pending_without_storing(client):
    await _queue_sensitive_write("api-deny-me")
    pending_id = client.get("/api/consent/pending-writes").json()["entries"][0]["id"]

    response = client.post(f"/api/consent/pending-writes/{pending_id}/deny")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["denied"] is True

    remaining = client.get("/api/consent/pending-writes").json()["entries"]
    assert all(e["key"] != "api-deny-me" for e in remaining)


def test_approve_unknown_pending_id_returns_404(client):
    response = client.post("/api/consent/pending-writes/999999/approve")
    assert response.status_code == 404


def test_deny_unknown_pending_id_returns_404(client):
    response = client.post("/api/consent/pending-writes/999999/deny")
    assert response.status_code == 404


def test_pending_writes_rejects_out_of_bounds_limit(client):
    assert client.get("/api/consent/pending-writes", params={"limit": 0}).status_code == 400
    assert client.get("/api/consent/pending-writes", params={"limit": 101}).status_code == 400


@pytest.mark.asyncio
async def test_rule_consent_pending_write_round_trips_through_the_same_endpoints(client):
    """S1.2: a requires_consent (ask_before_store) write lands in the same
    inbox, tagged with reason/privacy_class, and approve/deny work
    identically to the privacy_guard case."""
    result = await app_module._kernel.mem.upsert_memory(
        kind="chat",
        key="api-rule-consent",
        value=ASK_BEFORE_STORE_CONTENT,
        ts=TS,
    )
    assert result.stored is False

    entries = client.get("/api/consent/pending-writes").json()["entries"]
    entry = next(e for e in entries if e["key"] == "api-rule-consent")
    assert entry["reason"] == "rule_consent"
    assert entry["privacy_class"] == "user.secure"

    response = client.post(f"/api/consent/pending-writes/{entry['id']}/approve")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["stored"] is True
    assert body["memory_id"] is not None

    remaining = client.get("/api/consent/pending-writes").json()["entries"]
    assert all(e["key"] != "api-rule-consent" for e in remaining)
