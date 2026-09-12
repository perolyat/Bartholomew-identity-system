"""
FND-03: the consent inbox's privacy and lifecycle contract.

`pending_sensitive_writes` deliberately holds the ORIGINAL, pre-redaction
payload of a write Bartholomew wants a human decision about. That makes it
the single most sensitive persistence surface in the system: the content
there has not been through redaction, and it exists precisely because
something classified it as needing consent.

Three defects, each reproduced here before it was fixed:

A. protection was a function of the matched rule's `encrypt:` policy, so a
   privacy_guard-only write -- or an ask_before_store category with no
   explicit `encrypt:` (trauma/confessional, third-party private) -- parked
   its raw payload in the table as plaintext;
B. `deny` cleared the payload but `approve` did not, so approval left a MORE
   sensitive copy behind than denial did;
C. `forget_memory()` / `revoke_memory()` erased the governed copy and
   ignored the inbox entirely -- the rawest copy survived a "forget this",
   and an unresolved request could later be approved to resurrect it.

Every at-rest claim here is checked against the actual SQLite row, not
against a helper's return value.
"""

from __future__ import annotations

import sqlite3

import pytest

from bartholomew.kernel import encryption_engine as _enc
from bartholomew.kernel.memory.privacy_guard import is_sensitive, set_consent_handler
from bartholomew.kernel.memory_rules import _rules_engine
from bartholomew.kernel.memory_store import (
    ConsentInboxProtectionError,
    MemoryStore,
)

TS = "2026-01-01T00:00:00Z"

# Flagged by privacy_guard.is_sensitive() ("routine"), and NOT matched by any
# ask_before_store rule -- so it reaches the privacy_guard gate, whose queued
# payload carried no encryption policy at all before FND-03.
PRIVACY_GUARD_CONTENT = "my daily routine starts at 6am at 12 Rowan Street"

# The trauma/confessional and third-party ask_before_store categories match on
# `tags` / `speaker`, which upsert_memory()'s memory_dict does not carry, so
# they are exercised at record_pending_write() -- the real call site a future
# classifier would use, and the one that decides protection.
TRAUMA_CONTENT = "what happened to me when I was nine, in full"
THIRDPARTY_CONTENT = "Dana told me in confidence about her diagnosis"


@pytest.fixture(autouse=True)
def _reset_consent_handler():
    set_consent_handler(None)
    yield
    set_consent_handler(None)


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "fnd03.db"))
    await s.init()
    return s


def _raw_rows(store, **where) -> list[tuple]:
    clause = " AND ".join(f"{k} = ?" for k in where) or "1=1"
    conn = sqlite3.connect(store.db_path)
    try:
        return conn.execute(
            "SELECT id, kind, key, value, status, reason, privacy_class, "
            "requested_at, resolved_at, resolved_memory_id, resolution_note "
            f"FROM pending_sensitive_writes WHERE {clause} ORDER BY id",  # noqa: S608
            tuple(where.values()),
        ).fetchall()
    finally:
        conn.close()


def _raw_value(store, pending_id: int) -> str:
    conn = sqlite3.connect(store.db_path)
    try:
        return conn.execute(
            "SELECT value FROM pending_sensitive_writes WHERE id = ?",
            (pending_id,),
        ).fetchone()[0]
    finally:
        conn.close()


def _unopenable_envelope(plaintext: str = "unreachable") -> str:
    """A structurally valid envelope whose key id nothing can resolve.

    Built from a REAL envelope and then re-pointed at a missing key, because
    a hand-written dict with the wrong `scheme` is not an envelope at all --
    `Envelope.from_json()` rejects it and the value would be treated as
    plaintext, which would make this test prove the opposite of what it says.
    """
    real = _enc._encryption_engine.encrypt_for_policy(
        plaintext,
        {"encrypt": "strong"},
        {"kind": "chat", "key": "k", "ts": TS},
    )
    env = _enc.Envelope.from_json(real)
    assert env is not None
    return _enc.Envelope(
        scheme=env.scheme,
        alg=env.alg,
        kid="nonexistent-kid",
        nonce=env.nonce,
        aad=env.aad,
        ct=env.ct,
    ).to_json()


def _pending_table_bytes(store) -> bytes:
    """Every byte the consent inbox currently persists, concatenated.

    Scoped to the table on purpose: a whole-file scan also sees the governed
    `memories` row an approval legitimately creates (and freed pages SQLite
    has not yet reused), neither of which is a claim this contract makes.
    """
    conn = sqlite3.connect(store.db_path)
    try:
        rows = conn.execute("SELECT * FROM pending_sensitive_writes").fetchall()
    finally:
        conn.close()
    return repr(rows).encode()


def _whole_db_bytes(store) -> bytes:
    with open(store.db_path, "rb") as fh:
        return fh.read()


def _assert_protected(store, pending_id: int, plaintext: str) -> None:
    """The at-rest claim, checked the only way that proves anything."""
    raw = _raw_value(store, pending_id)
    assert raw != plaintext
    assert plaintext not in raw
    assert _enc.is_envelope(raw), f"pending payload is not encrypted at rest: {raw!r}"


# ----------------------------------------------------------------------
# Defect A -- pending means sensitive, whatever the rule said
# ----------------------------------------------------------------------


def test_fixtures_reach_the_gates_they_claim_to() -> None:
    """Guard the guard: if this stops holding, the tests below prove nothing."""
    assert is_sensitive(PRIVACY_GUARD_CONTENT)
    evaluated = _rules_engine.evaluate(
        {"kind": "chat", "key": "k", "value": PRIVACY_GUARD_CONTENT, "ts": TS},
    )
    assert evaluated.get("allow_store", True) is True
    assert evaluated.get("requires_consent", False) is False
    # The point of the defect: this content carries no encryption policy.
    assert evaluated.get("encrypt") in (None, False)


@pytest.mark.asyncio
async def test_privacy_guard_pending_content_is_encrypted_at_rest(store) -> None:
    result = await store.upsert_memory(
        kind="chat",
        key="guarded",
        value=PRIVACY_GUARD_CONTENT,
        ts=TS,
    )
    assert result.stored is False

    pending = await store.list_pending_sensitive_writes()
    assert len(pending) == 1
    assert pending[0]["reason"] == "privacy_guard"
    # The authorised review path still shows the reviewer what they are
    # deciding about...
    assert pending[0]["value"] == PRIVACY_GUARD_CONTENT
    assert pending[0]["readable"] is True
    # ...and the file on disk does not contain it anywhere.
    _assert_protected(store, pending[0]["id"], PRIVACY_GUARD_CONTENT)
    assert PRIVACY_GUARD_CONTENT.encode() not in _whole_db_bytes(store)


@pytest.mark.asyncio
async def test_rule_consent_without_explicit_encrypt_is_encrypted_at_rest(store) -> None:
    """A rule that never asked for encryption no longer decides the inbox's
    protection: membership of the inbox is the policy."""
    pending_id = await store.record_pending_write(
        "rule_consent",
        "chat",
        "unencrypted_rule",
        PRIVACY_GUARD_CONTENT,
        TS,
        privacy_class="user.sensitive",
        evaluated={"requires_consent": True},  # no `encrypt` key at all
    )
    _assert_protected(store, pending_id, PRIVACY_GUARD_CONTENT)


@pytest.mark.asyncio
async def test_trauma_confessional_pending_content_is_encrypted_at_rest(store) -> None:
    pending_id = await store.record_pending_write(
        "chat",
        "chat",
        "trauma",
        TRAUMA_CONTENT,
        TS,
        privacy_class="user.emotional",
        evaluated={"requires_consent": True, "privacy_class": "user.emotional"},
    )
    _assert_protected(store, pending_id, TRAUMA_CONTENT)


@pytest.mark.asyncio
async def test_thirdparty_private_pending_content_is_encrypted_at_rest(store) -> None:
    pending_id = await store.record_pending_write(
        "rule_consent",
        "chat",
        "thirdparty",
        THIRDPARTY_CONTENT,
        TS,
        privacy_class="thirdparty.private",
        evaluated={"requires_consent": True, "privacy_class": "thirdparty.private"},
    )
    _assert_protected(store, pending_id, THIRDPARTY_CONTENT)


@pytest.mark.asyncio
async def test_no_evaluated_metadata_is_still_encrypted_at_rest(store) -> None:
    """Any future caller of this inbox inherits the protection without
    having to remember to ask for it."""
    pending_id = await store.record_pending_write(
        "privacy_guard",
        "chat",
        "no_meta",
        THIRDPARTY_CONTENT,
        TS,
    )
    _assert_protected(store, pending_id, THIRDPARTY_CONTENT)


@pytest.mark.asyncio
async def test_inbox_fails_closed_when_it_cannot_protect(store, monkeypatch) -> None:
    """F: no plaintext fallback. If the payload cannot be encrypted, the
    write is refused and nothing is queued."""

    real = _enc._encryption_engine.encrypt_for_policy

    def _no_consent_inbox_key(plaintext, meta, context):
        # Break ONLY the inbox's own policy: upsert_memory() also encrypts
        # governed content through this engine, and failing that instead
        # would test a different path entirely.
        if meta is MemoryStore.CONSENT_INBOX_ENCRYPTION_POLICY:
            raise RuntimeError("key material unavailable")
        return real(plaintext, meta, context)

    monkeypatch.setattr(
        _enc._encryption_engine,
        "encrypt_for_policy",
        _no_consent_inbox_key,
    )

    with pytest.raises(ConsentInboxProtectionError):
        await store.record_pending_write(
            "privacy_guard",
            "chat",
            "unprotectable",
            PRIVACY_GUARD_CONTENT,
            TS,
        )
    assert _raw_rows(store) == []

    result = await store.upsert_memory(
        kind="chat",
        key="unprotectable",
        value=PRIVACY_GUARD_CONTENT,
        ts=TS,
    )
    assert result.stored is False
    assert result.outcome == "refused_consent_inbox_unprotected"
    assert _raw_rows(store) == []
    assert PRIVACY_GUARD_CONTENT.encode() not in _whole_db_bytes(store)


# ----------------------------------------------------------------------
# Defects B/C -- resolution and forget/revoke scrub the payload
# ----------------------------------------------------------------------


async def _queue(store, key: str, value: str = PRIVACY_GUARD_CONTENT) -> int:
    await store.upsert_memory(kind="chat", key=key, value=value, ts=TS)
    rows = _raw_rows(store, key=key, status="pending")
    assert rows, f"nothing queued for {key}"
    return rows[-1][0]


@pytest.mark.asyncio
async def test_approval_scrubs_the_inbox_payload(store) -> None:
    pending_id = await _queue(store, "approve_me")

    result = await store.approve_pending_sensitive_write(pending_id)
    assert result.stored is True

    row = _raw_rows(store, id=pending_id)[0]
    assert row[3] == ""  # value
    assert row[4] == "approved"
    # C: approval must not leave a more sensitive copy than denial. The
    # governed memory it legitimately created is a separate surface with its
    # own (FND-02) contract; what must not survive is the raw pre-redaction
    # copy in the inbox.
    assert PRIVACY_GUARD_CONTENT.encode() not in _pending_table_bytes(store)
    # ...but the audit metadata survives.
    assert row[1] == "chat" and row[2] == "approve_me"
    assert row[5] == "privacy_guard"
    assert row[7] is not None and row[8] is not None  # requested_at/resolved_at
    assert row[9] == result.memory_id


@pytest.mark.asyncio
async def test_denial_scrubs_the_inbox_payload(store) -> None:
    pending_id = await _queue(store, "deny_me")
    await store.deny_pending_sensitive_write(pending_id)

    row = _raw_rows(store, id=pending_id)[0]
    assert row[3] == ""
    assert row[4] == "denied"
    assert PRIVACY_GUARD_CONTENT.encode() not in _whole_db_bytes(store)


@pytest.mark.asyncio
async def test_failed_approval_keeps_the_payload_decidable(store, monkeypatch) -> None:
    """Approval claims the row before storing. A storage failure must give
    the row back, not consume the only copy of an undecided request."""
    pending_id = await _queue(store, "approve_fails")

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("storage exploded")

    monkeypatch.setattr(MemoryStore, "upsert_memory", _boom)

    with pytest.raises(RuntimeError):
        await store.approve_pending_sensitive_write(pending_id)

    monkeypatch.undo()

    row = _raw_rows(store, id=pending_id)[0]
    assert row[4] == "pending"
    assert row[3] != ""
    _assert_protected(store, pending_id, PRIVACY_GUARD_CONTENT)

    # Still reviewable, still decidable -- and a retry now works.
    pending = await store.list_pending_sensitive_writes()
    assert [e["id"] for e in pending] == [pending_id]
    assert pending[0]["value"] == PRIVACY_GUARD_CONTENT
    assert (await store.approve_pending_sensitive_write(pending_id)).stored is True


@pytest.mark.asyncio
async def test_refused_governed_write_releases_the_claim(store, monkeypatch) -> None:
    """A governed refusal (stored=False, no exception) is also a failed
    approval: the request stays pending rather than silently vanishing."""
    pending_id = await _queue(store, "approve_refused")

    from bartholomew.kernel.memory_store import StoreResult

    async def _refuse(*_args, **_kwargs):
        return StoreResult(stored=False, outcome="refused")

    monkeypatch.setattr(MemoryStore, "upsert_memory", _refuse)
    result = await store.approve_pending_sensitive_write(pending_id)
    assert result.stored is False
    monkeypatch.undo()

    row = _raw_rows(store, id=pending_id)[0]
    assert row[4] == "pending"
    _assert_protected(store, pending_id, PRIVACY_GUARD_CONTENT)


@pytest.mark.asyncio
async def test_unreadable_pending_payload_fails_safely_and_truthfully(store) -> None:
    """A payload encrypted under a key this process does not hold cannot be
    reviewed and must not be approved -- approving it would store ciphertext
    JSON as if it were the reviewed content."""
    pending_id = await _queue(store, "unreadable")
    conn = sqlite3.connect(store.db_path)
    try:
        broken = _unopenable_envelope()
        conn.execute(
            "UPDATE pending_sensitive_writes SET value = ? WHERE id = ?",
            (broken, pending_id),
        )
        conn.commit()
    finally:
        conn.close()

    entries = await store.list_pending_sensitive_writes()
    entry = next(e for e in entries if e["id"] == pending_id)
    assert entry["readable"] is False
    assert entry["value"] == ""  # never hand a reviewer ciphertext as content

    with pytest.raises(ConsentInboxProtectionError):
        await store.approve_pending_sensitive_write(pending_id)

    # Still pending, so it can still be denied and cleared.
    assert _raw_rows(store, id=pending_id)[0][4] == "pending"
    await store.deny_pending_sensitive_write(pending_id)
    assert _raw_rows(store, id=pending_id)[0][3] == ""


@pytest.mark.asyncio
async def test_forget_scrubs_related_consent_payloads(store) -> None:
    pending_id = await _queue(store, "forget_me")
    result = await store.approve_pending_sensitive_write(pending_id)
    assert result.stored is True
    # Re-queue a second, still-unresolved request at the same identity.
    second = await store.record_pending_write(
        "privacy_guard",
        "chat",
        "forget_me",
        PRIVACY_GUARD_CONTENT,
        TS,
    )

    assert await store.forget_memory("chat", "forget_me") is True

    rows = _raw_rows(store, kind="chat", key="forget_me")
    assert len(rows) == 2
    assert all(r[3] == "" for r in rows), "a raw consent payload survived a forget"
    assert PRIVACY_GUARD_CONTENT.encode() not in _pending_table_bytes(store)

    # D: the unresolved request can never be approved to resurrect it.
    assert _raw_rows(store, id=second)[0][4] == "denied"
    assert _raw_rows(store, id=second)[0][10] == "forgotten_by_user"
    with pytest.raises(ValueError):
        await store.approve_pending_sensitive_write(second)


@pytest.mark.asyncio
async def test_revoke_scrubs_related_consent_payloads(store) -> None:
    pending_id = await _queue(store, "revoke_me")
    assert (await store.approve_pending_sensitive_write(pending_id)).stored is True
    second = await store.record_pending_write(
        "rule_consent",
        "chat",
        "revoke_me",
        THIRDPARTY_CONTENT,
        TS,
        privacy_class="thirdparty.private",
    )

    outcome = await store.revoke_memory("chat", "revoke_me", revoked_by="user")
    assert outcome.tombstoned is True

    rows = _raw_rows(store, kind="chat", key="revoke_me")
    assert all(r[3] == "" for r in rows)
    assert THIRDPARTY_CONTENT.encode() not in _pending_table_bytes(store)
    assert _raw_rows(store, id=second)[0][4] == "denied"
    with pytest.raises(ValueError):
        await store.approve_pending_sensitive_write(second)


@pytest.mark.asyncio
async def test_many_consent_rows_for_one_identity_all_scrubbed(store) -> None:
    ids = [
        await store.record_pending_write(
            "privacy_guard",
            "chat",
            "crowded",
            f"{PRIVACY_GUARD_CONTENT} #{n}",
            TS,
        )
        for n in range(4)
    ]
    await store.deny_pending_sensitive_write(ids[0])

    await store.forget_memory("chat", "crowded")

    rows = _raw_rows(store, kind="chat", key="crowded")
    assert len(rows) == 4
    assert all(r[3] == "" for r in rows)
    assert all(r[4] == "denied" for r in rows)
    assert PRIVACY_GUARD_CONTENT.encode() not in _pending_table_bytes(store)
    assert await store.list_pending_sensitive_writes() == []


@pytest.mark.asyncio
async def test_reinstate_does_not_restore_scrubbed_consent_payloads(store) -> None:
    pending_id = await _queue(store, "reinstate_me")
    assert (await store.approve_pending_sensitive_write(pending_id)).stored is True
    await store.revoke_memory("chat", "reinstate_me", revoked_by="user")

    await store.reinstate_memory("chat", "reinstate_me", reinstated_by="user")

    assert _raw_rows(store, id=pending_id)[0][3] == ""
    assert await store.list_pending_sensitive_writes() == []


# ----------------------------------------------------------------------
# Migration of historical rows
# ----------------------------------------------------------------------


def _legacy_row(db_path: str, *, key: str, value: str, status: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO pending_sensitive_writes "
            "(kind, key, value, ts, requested_at, status, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("chat", key, value, TS, TS, status, "privacy_guard"),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_migration_encrypts_old_plaintext_pending_rows(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "legacy.db"))
    await store.init()
    row_id = _legacy_row(store.db_path, key="legacy", value=PRIVACY_GUARD_CONTENT, status="pending")
    assert _raw_value(store, row_id) == PRIVACY_GUARD_CONTENT  # the unsafe state

    await store.init()  # restart

    _assert_protected(store, row_id, PRIVACY_GUARD_CONTENT)
    # Still reviewable through the application path.
    entry = next(e for e in await store.list_pending_sensitive_writes() if e["id"] == row_id)
    assert entry["value"] == PRIVACY_GUARD_CONTENT
    assert entry["readable"] is True


@pytest.mark.asyncio
async def test_migration_is_idempotent_and_never_double_encrypts(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "idem.db"))
    await store.init()
    row_id = _legacy_row(store.db_path, key="legacy", value=PRIVACY_GUARD_CONTENT, status="pending")
    await store.init()
    once = _raw_value(store, row_id)

    await store.init()
    await store.init()
    assert _raw_value(store, row_id) == once, "the payload was re-encrypted"

    # One decrypt, not two: a double-encrypted value would come back as an
    # envelope rather than as the content.
    plain = _enc.decrypt_if_envelope(_raw_value(store, row_id))
    assert plain == PRIVACY_GUARD_CONTENT
    assert not _enc.is_envelope(plain)


@pytest.mark.asyncio
async def test_migration_scrubs_old_resolved_rows(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "resolved.db"))
    await store.init()
    approved = _legacy_row(
        store.db_path, key="old_approved", value=PRIVACY_GUARD_CONTENT, status="approved",
    )
    denied = _legacy_row(store.db_path, key="old_denied", value=THIRDPARTY_CONTENT, status="denied")

    await store.init()

    assert _raw_value(store, approved) == ""
    assert _raw_value(store, denied) == ""
    assert PRIVACY_GUARD_CONTENT.encode() not in _pending_table_bytes(store)
    # Content-free audit metadata survives.
    row = _raw_rows(store, id=approved)[0]
    assert row[1] == "chat" and row[2] == "old_approved" and row[4] == "approved"


@pytest.mark.asyncio
async def test_migration_leaves_unopenable_envelopes_alone(tmp_path) -> None:
    """An envelope this process cannot open is already protected. Replacing
    it would destroy content rather than protect it."""
    store = MemoryStore(str(tmp_path / "unopenable.db"))
    await store.init()
    broken = _unopenable_envelope()
    row_id = _legacy_row(store.db_path, key="broken", value=broken, status="pending")

    await store.init()

    assert _raw_value(store, row_id) == broken


@pytest.mark.asyncio
async def test_migration_fails_closed_rather_than_leaving_plaintext(tmp_path, monkeypatch) -> None:
    store = MemoryStore(str(tmp_path / "failclosed.db"))
    await store.init()
    row_id = _legacy_row(store.db_path, key="legacy", value=PRIVACY_GUARD_CONTENT, status="pending")

    real = _enc._encryption_engine.encrypt_for_policy

    def _no_consent_inbox_key(plaintext, meta, context):
        if meta is MemoryStore.CONSENT_INBOX_ENCRYPTION_POLICY:
            raise RuntimeError("key material unavailable")
        return real(plaintext, meta, context)

    monkeypatch.setattr(_enc._encryption_engine, "encrypt_for_policy", _no_consent_inbox_key)

    with pytest.raises(ConsentInboxProtectionError):
        await store.init()

    monkeypatch.undo()
    # Truthful: the row is still plaintext and init() said so rather than
    # reporting a migration that did not happen.
    assert _raw_value(store, row_id) == PRIVACY_GUARD_CONTENT


@pytest.mark.asyncio
async def test_forget_then_reinstate_cannot_resurrect_via_an_old_request(store) -> None:
    """The sharpest form of defect C, reproduced on pre-fix main:

        forget("chat", "x")      -- content erased, tombstone laid
        reinstate("chat", "x")   -- the user allows the identity again
        approve(old_pending_id)  -- the FORGOTTEN content comes back verbatim

    The tombstone alone did not close this: lifting it (which reinstating is
    for) re-opened the path, and the old inbox row still held the original
    payload. Scrubbing at forget time is what makes it structurally
    impossible rather than incidentally blocked.
    """
    pending_id = await _queue(store, "resurrect")

    await store.forget_memory("chat", "resurrect")
    await store.reinstate_memory("chat", "resurrect", reinstated_by="user")

    with pytest.raises(ValueError):
        await store.approve_pending_sensitive_write(pending_id)
    assert await store.get_memory("chat", "resurrect") is None
    assert _raw_value(store, pending_id) == ""


@pytest.mark.asyncio
async def test_a_withdrawal_racing_an_approval_wins(store, monkeypatch) -> None:
    """The approve/forget race: approval claims the row, then the user
    forgets the identity before the governed write lands. Releasing the
    claim must not hand an emptied request back to the inbox as approvable."""
    pending_id = await _queue(store, "raced")

    real_upsert = MemoryStore.upsert_memory

    async def _forget_midway(self, *args, **kwargs):
        await self.forget_memory("chat", "raced")
        return await real_upsert(self, *args, **kwargs)

    monkeypatch.setattr(MemoryStore, "upsert_memory", _forget_midway)
    result = await store.approve_pending_sensitive_write(pending_id)
    monkeypatch.undo()

    assert result.stored is False
    assert await store.get_memory("chat", "raced") is None
    row = _raw_rows(store, id=pending_id)[0]
    assert row[3] == ""
    assert row[4] != "pending", "an emptied request was handed back as approvable"
    assert await store.list_pending_sensitive_writes() == []
