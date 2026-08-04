"""
S1.2: the promotion path for memory_rules.yaml's ask_before_store category
(requires_consent=true).

`MemoryRulesEngine.should_store()`'s own docstring has always said
requires_consent content should be blocked "until explicit consent is
captured and a separate promotion path is used" -- but no such path
existed. `upsert_memory()` discarded it identically to never_store
(allow_store=false) content: `StoreResult(stored=False)`, nothing
persisted, no record anywhere.

This reuses the consent-handler fix's pending_sensitive_writes inbox
(reason='rule_consent') rather than a parallel one. never_store must stay
an unconditional hard block with no promotion path -- these tests also
guard that the split didn't accidentally start queuing it too.
"""

from __future__ import annotations

import sqlite3

import pytest

from bartholomew.kernel.consent_gate import ConsentGate
from bartholomew.kernel.memory_rules import _rules_engine
from bartholomew.kernel.memory_store import MemoryStore

# Matches bartholomew/config/memory_rules.yaml's live ask_before_store
# content pattern "(?i)(password|bank|account number|two-factor|auth code)"
# -> requires_consent = true, privacy_class = "user.secure". Deliberately
# picked to avoid three separate collisions confirmed by direct testing:
# (1) privacy_guard.SENSITIVE_KEYWORDS ("name","address","location","phone",
# "email","bank","password","routine","health","private","account") --
# overlap would make skip_rule_consent's isolated effect untestable (content
# would still queue, just under reason='privacy_guard' instead); (2) the
# live rules file's OWN never_store category also has a
# "(?i)(personal data|personal information)" content rule (higher
# priority, added for a benchmark dataset) that would silently make this
# allow_store=false instead of requires_consent=true -- "personal
# information" is exactly the kind of natural phrasing that looks safe but
# isn't; (3) "account" (from "accounts") is itself a privacy_guard keyword
# substring match.
ASK_BEFORE_STORE_CONTENT = "Please remember my auth code for later."

# Matches memory_rules.yaml's never_store content pattern
# "(?i)(child sexual abuse|csam|illegal content)" -> allow_store = false.
NEVER_STORE_CONTENT = "This contains illegal content that should be blocked"

TS = "2026-01-01T00:00:00Z"


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "rule_consent.db"))
    await s.init()
    return s


def test_fixture_content_actually_reaches_the_rule_consent_gate() -> None:
    """Guard the guard: if this stops holding, the tests below prove nothing."""
    evaluated = _rules_engine.evaluate(
        {"kind": "chat", "key": "k", "value": ASK_BEFORE_STORE_CONTENT, "ts": TS},
    )
    assert evaluated.get("allow_store", True) is True
    assert evaluated.get("requires_consent") is True
    assert evaluated.get("privacy_class") == "user.secure"

    never_store_evaluated = _rules_engine.evaluate(
        {"kind": "chat", "key": "k", "value": NEVER_STORE_CONTENT, "ts": TS},
    )
    assert never_store_evaluated.get("allow_store") is False


@pytest.mark.asyncio
async def test_ask_before_store_content_is_queued_for_review(store) -> None:
    result = await store.upsert_memory(
        kind="chat",
        key="secret",
        value=ASK_BEFORE_STORE_CONTENT,
        ts=TS,
    )
    assert result.stored is False

    pending = await store.list_pending_sensitive_writes()
    assert len(pending) == 1
    assert pending[0]["kind"] == "chat"
    assert pending[0]["key"] == "secret"
    assert pending[0]["value"] == ASK_BEFORE_STORE_CONTENT
    assert pending[0]["ts"] == TS
    assert pending[0]["status"] == "pending"
    assert pending[0]["reason"] == "rule_consent"
    assert pending[0]["privacy_class"] == "user.secure"

    # The ask_before_store rule this content matches sets encrypt: strong --
    # the raw row must not hold the plaintext (Codex review finding: the
    # pending payload previously bypassed the redaction/encryption pipeline
    # entirely). list_pending_sensitive_writes() decrypts for display above;
    # this checks what's actually on disk.
    conn = sqlite3.connect(store.db_path)
    try:
        raw_value = conn.execute(
            "SELECT value FROM pending_sensitive_writes WHERE id = ?",
            (pending[0]["id"],),
        ).fetchone()[0]
    finally:
        conn.close()
    assert raw_value != ASK_BEFORE_STORE_CONTENT
    assert ASK_BEFORE_STORE_CONTENT not in raw_value


@pytest.mark.asyncio
async def test_never_store_content_is_not_queued(store) -> None:
    """Regression guard on the allow_store/requires_consent split: the
    hard-blocked never_store category must never get a promotion path."""
    result = await store.upsert_memory(
        kind="chat",
        key="blocked",
        value=NEVER_STORE_CONTENT,
        ts=TS,
    )
    assert result.stored is False
    assert (await store.list_pending_sensitive_writes()) == []


@pytest.mark.asyncio
async def test_skip_rule_consent_bypasses_the_gate(store) -> None:
    result = await store.upsert_memory(
        kind="chat",
        key="bypassed",
        value=ASK_BEFORE_STORE_CONTENT,
        ts=TS,
        skip_rule_consent=True,
    )

    assert result.stored is True
    assert (await store.list_pending_sensitive_writes()) == []


@pytest.mark.asyncio
async def test_approve_rule_consent_write_stores_and_grants_retrieval_consent(store) -> None:
    """End-to-end proof the promotion path is complete: approval must not
    just insert a memories row, it must also make the memory actually
    retrievable -- ConsentGate re-evaluates requires_consent at retrieval
    time and only includes memories with a real memory_consent row."""
    await store.upsert_memory(
        kind="user_profile",
        key="bank_note",
        value=ASK_BEFORE_STORE_CONTENT,
        ts=TS,
    )
    pending = await store.list_pending_sensitive_writes()
    assert len(pending) == 1
    pending_id = pending[0]["id"]

    result = await store.approve_pending_sensitive_write(pending_id)

    assert result.stored is True
    assert result.memory_id is not None
    assert (await store.list_pending_sensitive_writes()) == []

    conn = sqlite3.connect(store.db_path)
    try:
        mem_row = conn.execute(
            "SELECT id FROM memories WHERE kind = 'user_profile' AND key = 'bank_note'",
        ).fetchone()
        assert mem_row is not None
        assert mem_row[0] == result.memory_id

        consent_row = conn.execute(
            "SELECT source FROM memory_consent WHERE memory_id = ?",
            (result.memory_id,),
        ).fetchone()
        assert consent_row is not None
        assert consent_row[0] == "consent_approval"
    finally:
        conn.close()

    gate = ConsentGate(store.db_path)
    policy = gate.get_memory_policy(result.memory_id)
    assert policy["include"] is True


@pytest.mark.asyncio
async def test_deny_rule_consent_write_stores_nothing_and_clears_pending(store) -> None:
    await store.upsert_memory(
        kind="chat",
        key="to_deny",
        value=ASK_BEFORE_STORE_CONTENT,
        ts=TS,
    )
    pending_id = (await store.list_pending_sensitive_writes())[0]["id"]

    await store.deny_pending_sensitive_write(pending_id)

    assert (await store.list_pending_sensitive_writes()) == []
    conn = sqlite3.connect(store.db_path)
    try:
        mem_row = conn.execute(
            "SELECT id FROM memories WHERE kind = 'chat' AND key = 'to_deny'",
        ).fetchone()
        assert mem_row is None
        consent_rows = conn.execute("SELECT * FROM memory_consent").fetchall()
        assert consent_rows == []

        # Codex review finding: denial must not leave the sensitive payload
        # recoverable in the (kept, for audit purposes) pending row.
        denied_value = conn.execute(
            "SELECT value FROM pending_sensitive_writes WHERE id = ?",
            (pending_id,),
        ).fetchone()[0]
        assert denied_value == ""
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_deny_after_approve_raises_and_does_not_undo_the_memory(store) -> None:
    """Codex review finding: approve/deny used to race -- a denial that won
    after storage had already completed would report success without
    actually reverting anything. Approve now atomically claims the row
    before storage runs, so a subsequent deny on an already-approved row
    must cleanly fail instead of silently "succeeding" over a stored
    memory."""
    await store.upsert_memory(
        kind="chat",
        key="approve_then_deny",
        value=ASK_BEFORE_STORE_CONTENT,
        ts=TS,
    )
    pending_id = (await store.list_pending_sensitive_writes())[0]["id"]

    approved = await store.approve_pending_sensitive_write(pending_id)
    assert approved.stored is True

    with pytest.raises(ValueError):
        await store.deny_pending_sensitive_write(pending_id)

    conn = sqlite3.connect(store.db_path)
    try:
        mem_row = conn.execute(
            "SELECT id FROM memories WHERE kind = 'chat' AND key = 'approve_then_deny'",
        ).fetchone()
        assert mem_row is not None
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_approve_after_deny_raises_and_does_not_store(store) -> None:
    await store.upsert_memory(
        kind="chat",
        key="deny_then_approve",
        value=ASK_BEFORE_STORE_CONTENT,
        ts=TS,
    )
    pending_id = (await store.list_pending_sensitive_writes())[0]["id"]

    await store.deny_pending_sensitive_write(pending_id)

    with pytest.raises(ValueError):
        await store.approve_pending_sensitive_write(pending_id)

    conn = sqlite3.connect(store.db_path)
    try:
        mem_row = conn.execute(
            "SELECT id FROM memories WHERE kind = 'chat' AND key = 'deny_then_approve'",
        ).fetchone()
        assert mem_row is None
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_privacy_guard_pending_write_reason_defaults_correctly(store) -> None:
    """The original (consent-handler fix) call site must still default to
    reason='privacy_guard', unaffected by the new rule_consent path."""
    pending_id = await store.record_pending_sensitive_write(
        kind="chat",
        key="legacy",
        value="my daily routine starts at 6am",
        ts=TS,
    )
    pending = await store.list_pending_sensitive_writes()
    entry = next(p for p in pending if p["id"] == pending_id)
    assert entry["reason"] == "privacy_guard"
    assert entry["privacy_class"] is None
