"""
Regression tests for the sensitive-content consent path in
`MemoryStore.upsert_memory()` (Phase A finding).

`upsert_memory` is `async def`, so the previous implementation's
`asyncio.run(request_permission_to_store(...))` raised RuntimeError on *every*
call ("asyncio.run() cannot be called from a running event loop") and always
fell through to an `import nest_asyncio` fallback. `nest_asyncio` is not a
declared dependency, so any write of content that `is_sensitive()` flags -- and
that the rules engine does not block earlier -- raised ModuleNotFoundError,
even when the user explicitly approved storing it.

These tests pin the corrected behaviour: the coroutine is awaited directly, and
consent stays fail-closed.
"""

from __future__ import annotations

import sqlite3

import pytest

from bartholomew.kernel.memory.privacy_guard import is_sensitive, set_consent_handler
from bartholomew.kernel.memory_rules import _rules_engine
from bartholomew.kernel.memory_store import MemoryStore

# Content that is_sensitive() flags (contains "routine") but that the memory
# rules engine does not block outright -- i.e. it genuinely reaches the privacy
# guard rather than being rejected earlier by should_store().
SENSITIVE_BUT_STORABLE = "my daily routine starts at 6am"
TS = "2026-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _reset_consent_handler():
    set_consent_handler(None)
    yield
    set_consent_handler(None)


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "sensitive.db"))
    await s.init()
    return s


def test_fixture_content_actually_reaches_the_privacy_guard() -> None:
    """Guard the guard: if this stops holding, the tests below prove nothing."""
    assert is_sensitive(SENSITIVE_BUT_STORABLE), "content must trip the privacy guard"
    evaluated = _rules_engine.evaluate(
        {"kind": "chat", "key": "k", "value": SENSITIVE_BUT_STORABLE, "ts": TS},
    )
    assert _rules_engine.should_store(
        evaluated,
    ), "content must not be blocked by rules before the privacy guard"


@pytest.mark.asyncio
async def test_sensitive_write_stores_when_consent_granted(store) -> None:
    """The regression: this raised ModuleNotFoundError('nest_asyncio') before."""
    set_consent_handler(lambda _prompt: True)

    result = await store.upsert_memory(
        kind="chat",
        key="approved",
        value=SENSITIVE_BUT_STORABLE,
        ts=TS,
    )

    assert result.stored is True
    assert result.memory_id is not None


@pytest.mark.asyncio
async def test_sensitive_write_fails_closed_without_consent_handler(store) -> None:
    """No registered handler must deny, not crash and not silently allow."""
    result = await store.upsert_memory(
        kind="chat",
        key="no_handler",
        value=SENSITIVE_BUT_STORABLE,
        ts=TS,
    )

    assert result.stored is False


@pytest.mark.asyncio
async def test_sensitive_write_denied_when_consent_declined(store) -> None:
    set_consent_handler(lambda _prompt: False)

    result = await store.upsert_memory(
        kind="chat",
        key="declined",
        value=SENSITIVE_BUT_STORABLE,
        ts=TS,
    )

    assert result.stored is False


@pytest.mark.asyncio
async def test_async_consent_handler_is_awaited(store) -> None:
    """An async consent handler resolves correctly (privacy_guard awaits it)."""

    async def approving_handler(_prompt: str) -> bool:
        return True

    set_consent_handler(approving_handler)

    result = await store.upsert_memory(
        kind="chat",
        key="async_handler",
        value=SENSITIVE_BUT_STORABLE,
        ts=TS,
    )

    assert result.stored is True


# -- Consent-handler fix: pending sensitive writes (2026-08) -----------------


@pytest.mark.asyncio
async def test_no_handler_write_is_queued_instead_of_discarded(store) -> None:
    """The actual fix: with no handler, the content must be preserved for
    later human review, not silently and permanently dropped."""
    result = await store.upsert_memory(
        kind="chat",
        key="queued",
        value=SENSITIVE_BUT_STORABLE,
        ts=TS,
    )
    assert result.stored is False

    pending = await store.list_pending_sensitive_writes()
    assert len(pending) == 1
    assert pending[0]["kind"] == "chat"
    assert pending[0]["key"] == "queued"
    assert pending[0]["value"] == SENSITIVE_BUT_STORABLE
    assert pending[0]["ts"] == TS
    assert pending[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_explicit_decline_is_not_queued(store) -> None:
    """An explicit human 'no' must never be second-guessed or re-queued --
    only the true no-handler (headless) case queues."""
    set_consent_handler(lambda _prompt: False)

    result = await store.upsert_memory(
        kind="chat",
        key="declined_not_queued",
        value=SENSITIVE_BUT_STORABLE,
        ts=TS,
    )
    assert result.stored is False

    pending = await store.list_pending_sensitive_writes()
    assert pending == []


@pytest.mark.asyncio
async def test_skip_privacy_guard_bypasses_the_gate(store) -> None:
    """skip_privacy_guard=True must store sensitive content directly, with
    no handler needed and nothing queued."""
    result = await store.upsert_memory(
        kind="chat",
        key="bypassed",
        value=SENSITIVE_BUT_STORABLE,
        ts=TS,
        skip_privacy_guard=True,
    )

    assert result.stored is True
    assert (await store.list_pending_sensitive_writes()) == []


@pytest.mark.asyncio
async def test_approve_pending_sensitive_write_stores_with_original_context(store) -> None:
    await store.upsert_memory(
        kind="user_profile",
        key="favorite_food",
        value=SENSITIVE_BUT_STORABLE,
        ts=TS,
    )
    pending = await store.list_pending_sensitive_writes()
    assert len(pending) == 1
    pending_id = pending[0]["id"]

    result = await store.approve_pending_sensitive_write(pending_id)

    assert result.stored is True
    assert result.memory_id is not None
    # No longer pending.
    assert (await store.list_pending_sensitive_writes()) == []
    # Stored under the *original* kind/key, not some generic bucket.
    conn = sqlite3.connect(store.db_path)
    try:
        row = conn.execute(
            "SELECT id FROM memories WHERE kind = 'user_profile' AND key = 'favorite_food'",
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == result.memory_id


@pytest.mark.asyncio
async def test_deny_pending_sensitive_write_stores_nothing_and_clears_pending(store) -> None:
    await store.upsert_memory(
        kind="chat",
        key="to_deny",
        value=SENSITIVE_BUT_STORABLE,
        ts=TS,
    )
    pending = await store.list_pending_sensitive_writes()
    pending_id = pending[0]["id"]

    await store.deny_pending_sensitive_write(pending_id)

    assert (await store.list_pending_sensitive_writes()) == []
    conn = sqlite3.connect(store.db_path)
    try:
        row = conn.execute(
            "SELECT id FROM memories WHERE kind = 'chat' AND key = 'to_deny'",
        ).fetchone()
    finally:
        conn.close()
    assert row is None


@pytest.mark.asyncio
async def test_approve_unknown_pending_id_raises(store) -> None:
    with pytest.raises(ValueError):
        await store.approve_pending_sensitive_write(99999)


@pytest.mark.asyncio
async def test_deny_unknown_pending_id_raises(store) -> None:
    with pytest.raises(ValueError):
        await store.deny_pending_sensitive_write(99999)


@pytest.mark.asyncio
async def test_approve_already_resolved_pending_write_raises(store) -> None:
    await store.upsert_memory(
        kind="chat",
        key="double_approve",
        value=SENSITIVE_BUT_STORABLE,
        ts=TS,
    )
    pending_id = (await store.list_pending_sensitive_writes())[0]["id"]
    await store.approve_pending_sensitive_write(pending_id)

    with pytest.raises(ValueError):
        await store.approve_pending_sensitive_write(pending_id)
