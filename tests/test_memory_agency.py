"""
Capability C: Memory Agency -- the user can see, understand and control what
Bartholomew remembers about them.

These are HTTP-level tests against the real app, plus structural assertions
on the page (see tests/test_ui_test1_defect_regressions.py for why the
page-side ones are structural).

The governance properties are the point of this file. Memory Agency must not
become a second memory authority or a way around the first one, so the tests
below pin that:

* a correction re-enters the single governed write path and is therefore
  still subject to never_store and ask_before_store;
* a correction that was queued for consent is reported as queued, not as
  applied;
* reading is allowed under the Parking Brake and mutating is not;
* deletion requires explicit confirmation.
"""

from __future__ import annotations

import os
import pathlib
import re
import tempfile

import pytest
from fastapi.testclient import TestClient

_db_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
_db_dir.mkdir(parents=True, exist_ok=True)
_DB_PATH = str(_db_dir / "test.db")
os.environ["BARTH_DB_PATH"] = _DB_PATH

from bartholomew_api_bridge_v0_1.services.api import app as app_module  # noqa: E402

UI_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "bartholomew_api_bridge_v0_1"
    / "ui"
    / "minimal"
    / "index.html"
)


@pytest.fixture(scope="module")
def client():
    # See tests/test_self_state_api.py's client fixture for why this is
    # re-asserted immediately before the app starts.
    os.environ["BARTH_DB_PATH"] = _DB_PATH
    with TestClient(app_module.app) as c:
        yield c


def _seed_memory(kind: str, key: str, value: str) -> None:
    """
    Store a memory directly, for tests that need one to already exist.

    Deliberately a *standalone* MemoryStore against the same database file
    rather than `app_module._kernel.mem`. The kernel's store is bound to the
    daemon's own event loop through its blocking executor, so awaiting it from
    a second loop (which is what `asyncio.run()` in a sync test creates)
    deadlocks rather than failing. A separate connection to the same file is
    how the seeding scripts already do it, and SQLite's busy timeout covers
    the overlap with the running daemon.
    """
    import asyncio
    from datetime import datetime, timezone

    from bartholomew.kernel.memory_store import MemoryStore

    async def _run():
        store = MemoryStore(_DB_PATH)
        await store.init()
        try:
            await store.upsert_memory(
                kind,
                key,
                value,
                datetime.now(timezone.utc).isoformat(),
            )
        finally:
            await store.close(checkpoint=False)

    asyncio.run(_run())


@pytest.fixture
def stored(client):
    """Store a plain, ungoverned memory and clean it up afterwards."""
    kind, key = "fact", "agency_probe"
    _seed_memory(kind, key, "the bin goes out on Thursdays")
    yield kind, key
    client.delete(f"/api/memory/{kind}/{key}", params={"confirm": True})


# ---------------------------------------------------------------------------
# Seeing what is stored
# ---------------------------------------------------------------------------


def test_memories_can_be_listed(client, stored):
    kind, key = stored
    response = client.get("/api/memory", params={"limit": 50})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert any(e["kind"] == kind and e["key"] == key for e in body["entries"])


def test_listed_memories_carry_human_readable_governance_metadata(client, stored):
    """A row is not enough: the user needs category, class and provenance."""
    entry = _find(client, *stored)
    for field in ("category", "privacy_class", "recall_policy", "always_keep", "readable"):
        assert field in entry, f"listing must expose {field}"
    assert "consent_at" in entry and "consent_source" in entry


def test_always_keep_material_is_distinguished(client):
    """
    `user_profile` matches memory_rules.yaml's always_keep category, which is
    the one governed classification of this sort that already exists.
    """
    _seed_memory("user_profile", "agency_always_keep", "Taylor")
    try:
        entry = _find(client, "user_profile", "agency_always_keep")
        assert entry["always_keep"] is True
        assert entry["recall_policy"] == "always"
    finally:
        client.delete(
            "/api/memory/user_profile/agency_always_keep",
            params={"confirm": True},
        )


def test_kinds_endpoint_reports_counts(client, stored):
    response = client.get("/api/memory/kinds")
    assert response.status_code == 200
    kinds = {k["kind"]: k["count"] for k in response.json()["kinds"]}
    assert kinds.get("fact", 0) >= 1


def test_search_does_not_misreport_the_total(client, stored):
    """
    Filtering happens after decryption, so `total` is the unfiltered count.
    The response must flag that rather than letting a client present a
    filtered page as the whole store.
    """
    response = client.get("/api/memory", params={"search": "Thursdays"})
    assert response.status_code == 200
    assert response.json()["filtered"] is True


# ---------------------------------------------------------------------------
# Correcting -- still governed
# ---------------------------------------------------------------------------


def test_a_correction_is_applied(client, stored):
    kind, key = stored
    response = client.put(
        f"/api/memory/{kind}/{key}",
        json={"value": "the bin goes out on Fridays"},
    )

    assert response.status_code == 200
    assert response.json()["stored"] is True
    assert _find(client, kind, key)["value"] == "the bin goes out on Fridays"


def test_a_correction_that_needs_consent_is_queued_not_applied(client, stored):
    """
    The governance property that matters most here. `upsert_memory()` queues
    an ask_before_store value into the pending inbox and does not store it, so
    the API must say so -- reporting this as a success would be exactly the
    fabricated-success class of defect Test #1 was built to catch.
    """
    kind, key = stored
    before = _find(client, kind, key)["value"]

    response = client.put(
        f"/api/memory/{kind}/{key}",
        json={"value": "my bank account number is 12345"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["stored"] is False
    assert body["queued_for_consent"] is True
    assert "consent" in body["detail"].lower()

    # The stored value is genuinely unchanged.
    assert _find(client, kind, key)["value"] == before

    # And it really is waiting in the existing inbox -- one authority, not two.
    pending = client.get("/api/consent/pending-writes").json()["entries"]
    assert any(p["kind"] == kind and p["key"] == key for p in pending)


def test_a_correction_that_governance_refuses_is_reported_as_refused(client, stored):
    """never_store is an unconditional block with no promotion path."""
    kind, key = stored
    before = _find(client, kind, key)["value"]

    response = client.put(f"/api/memory/{kind}/{key}", json={"value": "csam"})

    assert response.status_code == 200
    body = response.json()
    assert body["stored"] is False
    assert body["queued_for_consent"] is False
    assert _find(client, kind, key)["value"] == before


def test_a_refusal_is_not_misreported_as_queued_by_a_stale_pending_row(client, stored):
    """
    Regression. The queued/refused distinction was first inferred by scanning
    the pending inbox for any row matching this (kind, key) after the write.
    An older, still-unresolved request for the same record therefore made a
    flatly refused correction report itself as "waiting for your consent" --
    telling the user their edit was recoverable when it never would be. It is
    now decided by what this specific call added to the inbox.
    """
    kind, key = stored

    # Leave a pending request for this record sitting in the inbox.
    queued = client.put(f"/api/memory/{kind}/{key}", json={"value": "my bank account number is 7"})
    assert queued.json()["queued_for_consent"] is True

    # A refusal for the same record must still report itself as a refusal.
    refused = client.put(f"/api/memory/{kind}/{key}", json={"value": "csam"})
    assert refused.json()["stored"] is False
    assert refused.json()["queued_for_consent"] is False


def test_correcting_a_memory_that_does_not_exist_is_404(client):
    response = client.put("/api/memory/fact/definitely-not-here", json={"value": "x"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Forgetting -- explicit only
# ---------------------------------------------------------------------------


def test_deletion_requires_explicit_confirmation(client, stored):
    kind, key = stored
    response = client.delete(f"/api/memory/{kind}/{key}")

    assert response.status_code == 400
    assert "confirm=true" in response.json()["detail"]
    assert _find(client, kind, key) is not None, "the memory must still be there"


def test_confirmed_deletion_removes_the_memory(client):
    _seed_memory("fact", "agency_delete_me", "temporary")

    response = client.delete("/api/memory/fact/agency_delete_me", params={"confirm": True})
    assert response.status_code == 200
    assert response.json()["forgotten"] is True
    assert _find(client, "fact", "agency_delete_me") is None

    # Deleting it again is a 404, not a silent success.
    assert (
        client.delete("/api/memory/fact/agency_delete_me", params={"confirm": True}).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Parking Brake: inspect, but do not mutate
# ---------------------------------------------------------------------------


def test_brake_allows_reading_and_refuses_mutating(client, stored):
    """
    The same semantics routes/consent.py already implements, enforced by
    MemoryStore rather than by the route, so bypassing this API cannot bypass
    the halt.
    """
    kind, key = stored
    engaged = client.post(
        "/api/governance/brake/engage",
        json={"scopes": [], "reason": "memory agency test", "actor": "test"},
    )
    assert engaged.status_code == 200
    revision = engaged.json()["revision"]

    try:
        # Inspection stays available: a halt must not hide what is stored.
        assert client.get("/api/memory").status_code == 200
        assert client.get(f"/api/memory/{kind}/{key}").status_code == 200

        # Mutation is refused.
        assert client.put(f"/api/memory/{kind}/{key}", json={"value": "x"}).status_code == 503
        assert (
            client.delete(f"/api/memory/{kind}/{key}", params={"confirm": True}).status_code == 503
        )
    finally:
        client.post(
            "/api/governance/brake/disengage",
            json={"reason": "test done", "actor": "test", "expected_revision": revision},
        )

    # And the memory survived the attempt.
    assert _find(client, kind, key) is not None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_returns_a_truthful_download(client, stored):
    response = client.get("/api/memory/export")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    body = response.json()
    assert body["exported_count"] == len(body["memories"])
    # The export now pages to completion rather than returning one page, so a
    # normal-sized store exports in full and says so.
    assert body["exported_count"] == body["total_stored"]
    assert body["complete"] is True


# ---------------------------------------------------------------------------
# Not a second authority
# ---------------------------------------------------------------------------


def test_memory_routes_never_open_their_own_database_connection():
    """
    MemoryStore is the single memory authority. A route reaching past it would
    be a second persistence access point beside it.
    """
    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "bartholomew_api_bridge_v0_1"
        / "services"
        / "api"
        / "routes"
        / "memory.py"
    ).read_text(encoding="utf-8")

    for forbidden in ("aiosqlite.connect", "sqlite3.connect", "SELECT ", "DELETE FROM"):
        assert forbidden not in source, f"memory routes must not contain {forbidden!r}"


def test_memory_correction_goes_through_the_governed_write_path():
    """correct_memory() must delegate to upsert_memory(), not UPDATE directly."""
    import inspect

    from bartholomew.kernel.memory_store import MemoryStore

    source = inspect.getsource(MemoryStore.correct_memory)
    body = source.split('"""')[-1]  # drop the docstring, which discusses UPDATE
    assert "upsert_memory" in body
    assert "UPDATE" not in body


# ---------------------------------------------------------------------------
# Page-side
# ---------------------------------------------------------------------------


def test_page_offers_memory_inspection_and_control():
    source = UI_PATH.read_text(encoding="utf-8")
    assert 'id="memory-list"' in source
    for fn in ("refreshMemories", "saveMemory", "forgetMemory", "exportMemories"):
        assert f"function {fn}" in source or f"async function {fn}" in source


def test_page_confirms_before_forgetting():
    """A destructive action must be explicit at the point of use as well."""
    source = UI_PATH.read_text(encoding="utf-8")
    body = re.search(r"async function forgetMemory\(.*?\n    \}", source, re.S)
    assert body, "forgetMemory() not found"
    assert "confirm(" in body.group(0)
    assert "confirm=true" in body.group(0)


def _find(client, kind: str, key: str):
    """Return one listed memory entry, or None."""
    entries = client.get("/api/memory", params={"limit": 500}).json()["entries"]
    for entry in entries:
        if entry["kind"] == kind and entry["key"] == key:
            return entry
    return None


# ---------------------------------------------------------------------------
# Review findings (Codex, 2026-08-25)
# ---------------------------------------------------------------------------


def test_a_correction_never_resurrects_a_deleted_memory(client):
    """
    P1. The existence check and the governed upsert are separate statements on
    separate connections, so a confirmed DELETE can land between them.
    `upsert_memory()` is an upsert: with the row gone it INSERTs, recreating
    the record under a new id. The user was told `forgotten: true` while the
    memory sat there again holding the corrected value.

    Simulated deterministically by deleting the row from inside the write, at
    exactly the moment the race would land.
    """
    _seed_memory("fact", "agency_race", "original value")
    store = app_module._kernel.mem
    original = _find(client, "fact", "agency_race")
    assert original is not None

    real_upsert = type(store).upsert_memory
    deleted: list[bool] = []

    async def _delete_then_upsert(self, kind, key, value, ts, **kwargs):
        if kind == "fact" and key == "agency_race" and not deleted:
            deleted.append(True)
            # The user's confirmed delete lands here.
            await real_delete(self, kind, key)
        return await real_upsert(self, kind, key, value, ts, **kwargs)

    real_delete = type(store).delete_memory

    type(store).upsert_memory = _delete_then_upsert
    try:
        response = client.put("/api/memory/fact/agency_race", json={"value": "corrected value"})
    finally:
        type(store).upsert_memory = real_upsert

    assert deleted, "the simulated delete never ran"
    assert response.status_code == 200
    body = response.json()

    # The deletion must win. It now wins by the conditional write refusing to
    # land at all, rather than by a compensating delete after the fact --
    # see test_memory_agency_review_fixes.py for why that distinction matters.
    assert body["stored"] is False
    assert body["target_changed"] is True
    assert body["queued_for_consent"] is False, "this is not a governance refusal"

    # And the memory must actually still be gone.
    assert (
        _find(client, "fact", "agency_race") is None
    ), "the correction resurrected a memory the user deleted"


def test_correction_outcome_distinguishes_a_race_from_a_refusal():
    """The three not-stored outcomes must stay tellable apart."""
    from bartholomew.kernel.memory_store import CorrectionOutcome

    queued = CorrectionOutcome(stored=False, queued_for_consent=True)
    refused = CorrectionOutcome(stored=False)
    raced = CorrectionOutcome(stored=False, target_changed=True)

    assert queued.queued_for_consent and not queued.target_changed
    assert not refused.queued_for_consent and not refused.target_changed
    assert raced.target_changed and not raced.queued_for_consent
