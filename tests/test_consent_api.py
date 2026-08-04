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
os.environ["BARTH_DB_PATH"] = str(_db_dir / "test.db")

from bartholomew_api_bridge_v0_1.services.api import app as app_module  # noqa: E402

SENSITIVE_BUT_STORABLE = "my daily routine starts at 6am"
TS = "2026-01-01T00:00:00Z"


@pytest.fixture(scope="module")
def client():
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
