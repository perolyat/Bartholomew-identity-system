"""
FND-02: the memory redaction contract, proven end to end.

WHAT WAS BROKEN
---------------
`MemoryRulesEngine.evaluate()` returns an *enriched memory* dict: the
memory's own normalised fields (`kind`, `key`, `content`, `tags`, ...)
with the matched rules' `metadata` merged on top. Its `content` key
therefore holds **the user's memory text**. No rule's `metadata` block
ever sets `content` -- that key only exists under a rule's `match:`
block, which `evaluate()` deliberately does not copy into its result.

`redaction_engine.apply_redaction(text, rule)` read its regex pattern
from `rule["content"]`.

`memory_store.upsert_memory()` passed the evaluated dict straight in:

    redacted_value = apply_redaction(value, evaluated)

So the memory's own text became the regular expression used to redact
itself. Three distinct failure modes followed, all reproduced below:

  * text that happens to be a self-matching regex was replaced *in its
    entirety* ("my email is a@b.com" -> "****"): total data loss, and
    the sensitive span was never specifically targeted;
  * text containing regex metacharacters did not match itself
    ("bank details: acct 12345 (branch)" -- the literal parentheses are
    a group in the pattern), so redaction silently no-opped and the raw
    account number reached durable storage, the FTS index and the
    embedding source text;
  * `memory_rules.yaml`'s entire `redact:` category was never loaded
    (`MemoryRulesEngine.PRIORITY` omits it), so the one rule that did
    carry a genuinely value-shaped pattern -- the SSN rule -- was dead
    config.

THE SECOND, DEEPER PROBLEM
--------------------------
Simply recovering the matched rule's `match.content` regex would NOT
have made the privacy promise true. The rules that actually trigger
redaction are *sensitivity classifiers*:

    (?i)(bank|medical|address|phone|email)

Using that as a redaction pattern masks the word "email" and leaves
`a@b.com` in place. A test that only asserted "the rule's own regex is
now used" would have passed while the account number still leaked.
`test_classifier_word_match_does_not_leave_the_value_exposed` below is
the test that distinguishes these two things.

WHAT THIS FILE ASSERTS
----------------------
The repaired contract separates three things that were sharing one
ambiguous dict field:

  A. memory data            -> the value being stored
  B. rule match condition   -> `match.content` (what made a rule fire)
  C. redaction instruction  -> `RedactionInstruction(patterns, strategy)`,
                               resolved explicitly from rule metadata

and it fails closed: when policy requires redaction and no valid
instruction can be resolved, the write is refused rather than stored raw.
"""

from __future__ import annotations

import base64
import os
import sqlite3

import pytest

from bartholomew.kernel import memory_store as memory_store_module
from bartholomew.kernel.memory_rules import MemoryRulesEngine, _rules_engine
from bartholomew.kernel.memory_store import MemoryStore

# The FND-02 API (`RedactionInstruction`, `RedactionPolicyError`,
# `redaction_required`, `resolve_redaction_instruction`) is imported inside
# the individual tests that need it, NOT at module scope.
#
# That is deliberate. A regression test is only worth what it detects, and
# a module that cannot be imported against the pre-fix tree detects
# nothing -- it reports a collection error for every test including the
# behavioural ones. Keeping module-level imports to symbols that already
# existed means this file still collects on pre-FND-02 code, so the
# end-to-end leak tests below genuinely run there and genuinely fail:
#
#   test_regex_metacharacters_cannot_defeat_redaction
#   test_sensitive_value_never_reaches_the_fts_index
#   test_embedding_source_text_is_redacted
#   test_memory_text_is_never_used_as_its_own_regex
#   ... and the rest of the storage-path tests
#
# See BARTHOLOMEW_FND_02_MEMORY_REDACTION_HANDOFF.md for the recorded
# pre-fix run.

TS = "2026-09-12T00:00:00Z"

# The two strings the FND-02 brief reproduced by hand. Kept verbatim.
EMAIL_MEMORY = "my email is a@b.com"
BANK_MEMORY = "bank details: acct 12345 (branch)"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "fnd02.db"))
    await s.init()
    return s


def _evaluate(value: str, kind: str = "user", key: str = "k") -> dict:
    return _rules_engine.evaluate({"kind": kind, "key": key, "value": value, "ts": TS})


def _stored_plaintext(store: MemoryStore, memory_id: int) -> tuple[str, str | None]:
    """The stored value/summary, decrypted if they are encryption envelopes.

    Redaction and encryption are separate controls: this test file is about
    redaction, so it looks *through* encryption deliberately. A value that
    is only protected because it happens to be encrypted has not been
    redacted, and FND-02 is about the redaction promise specifically.
    """
    from bartholomew.kernel import encryption_engine as enc

    conn = sqlite3.connect(store.db_path)
    try:
        row = conn.execute(
            "SELECT value, summary FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
    finally:
        conn.close()
    value = enc._encryption_engine.try_decrypt_if_envelope(row[0])
    summary = enc._encryption_engine.try_decrypt_if_envelope(row[1]) if row[1] else row[1]
    return value, summary


def _fts_matches(store: MemoryStore, term: str) -> list:
    """Query the real FTS index with MATCH.

    `memory_fts` is an FTS5 *external content* table (`content='memories'`),
    so selecting a column back by rowid proxies through to the live
    `memories` row and proves nothing about what was tokenised into the
    index. Only a MATCH query exercises the actual index.
    """
    conn = sqlite3.connect(store.db_path)
    try:
        return conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?",
            (term,),
        ).fetchall()
    finally:
        conn.close()


def _fts_index_text(store: MemoryStore, memory_id: int) -> str | None:
    """`memory_fts_map.last_index_text` -- a **plaintext** column recording
    exactly what was handed to the index, kept in the same database as the
    encrypted row. It is the most direct evidence of what leaked."""
    conn = sqlite3.connect(store.db_path)
    try:
        row = conn.execute(
            "SELECT last_index_text FROM memory_fts_map WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def _chunk_texts(store: MemoryStore, memory_id: int) -> list[str]:
    conn = sqlite3.connect(store.db_path)
    try:
        return [
            r[0]
            for r in conn.execute(
                "SELECT text FROM memory_chunks WHERE memory_id = ? ORDER BY seq",
                (memory_id,),
            )
        ]
    finally:
        conn.close()


class _RecordingEmbedder:
    """Captures every text handed to the embedder, so a test can assert on
    the embedding *source text* rather than on the opaque vectors."""

    storage_identity = ("test", "recording", "deterministic-hash")

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed_texts(self, texts):
        import numpy as np

        self.texts.extend(texts)
        return [np.zeros(4, dtype=np.float32) for _ in texts]


class _NullVectorStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def upsert(self, *args, **kwargs) -> None:  # pragma: no cover - inert
        return None


@pytest.fixture
def recording_embedder(monkeypatch):
    embedder = _RecordingEmbedder()
    monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")

    def _components(db_path: str):
        return embedder, _NullVectorStore(db_path)

    monkeypatch.setattr(memory_store_module, "_get_embedding_components", _components)
    return embedder


async def _store_with_consent(store: MemoryStore, kind: str, key: str, value: str):
    """Write content through the *real* governed path including the consent
    gate: the first write queues it for review, then a human approves it.

    Nothing here uses `skip_rule_consent` -- the point is that the promoted,
    human-approved write is redacted too.
    """
    first = await store.upsert_memory(kind, key, value, TS)
    if first.stored:
        return first
    assert first.outcome == "queued_for_consent", first.outcome
    pending = await store.list_pending_sensitive_writes()
    match = [p for p in pending if p["kind"] == kind and p["key"] == key]
    assert match, f"expected a pending row for {kind}/{key}"
    return await store.approve_pending_sensitive_write(match[0]["id"])


# ---------------------------------------------------------------------------
# 1. Memory content can never become the regex that redacts it
# ---------------------------------------------------------------------------


def test_evaluated_content_is_memory_data_not_a_redaction_pattern() -> None:
    """The root cause, pinned directly.

    `evaluate()` legitimately returns the memory's own text under
    `content` -- that is memory DATA (concept A). This test asserts that
    fact, so that anyone who later tries to "fix" redaction by reading
    `evaluated['content']` as a pattern is confronted with what that key
    actually holds.
    """
    evaluated = _evaluate(EMAIL_MEMORY)
    assert evaluated["content"] == EMAIL_MEMORY

    # And the matched rule's regex is a *separate* thing, reachable only
    # via matched_rules -- concept B, never merged into the result.
    assert evaluated["matched_rules"], "expected the classifier rule to fire"
    for _category, match in evaluated["matched_rules"]:
        assert match["content"] != EMAIL_MEMORY


def test_apply_redaction_refuses_a_raw_policy_dict() -> None:
    """apply_redaction() must not accept a mapping at all.

    This is the structural half of the repair. Renaming the key would have
    left the same category of bug one refactor away; refusing the *shape*
    that carried memory data means an evaluated-memory dict can never again
    be mistaken for a redaction instruction.
    """
    from bartholomew.kernel.redaction_engine import apply_redaction

    with pytest.raises(TypeError):
        apply_redaction(EMAIL_MEMORY, {"content": EMAIL_MEMORY, "redact_strategy": "mask"})

    with pytest.raises(TypeError):
        apply_redaction(EMAIL_MEMORY, _evaluate(EMAIL_MEMORY))


@pytest.mark.asyncio
async def test_memory_text_is_never_used_as_its_own_regex(store) -> None:
    """End-to-end: a memory whose text is a *valid, self-matching* regex
    must not be wholesale replaced.

    On the pre-FND-02 code this stored "****" -- every word of the user's
    memory destroyed, including the parts that were not sensitive at all.
    """
    result = await _store_with_consent(store, "user", "email_note", EMAIL_MEMORY)
    assert result.stored is True

    value, _ = _stored_plaintext(store, result.memory_id)
    assert value != "****", "the whole memory was replaced by its own regex match"
    assert "my email is" in value, "non-sensitive words must survive redaction"


# ---------------------------------------------------------------------------
# 2. Regex-significant punctuation cannot defeat redaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regex_metacharacters_cannot_defeat_redaction(store) -> None:
    """The privacy failure, in its sharpest form.

    "bank details: acct 12345 (branch)" is not a regex that matches itself:
    the literal parentheses become a capture group. Pre-fix, redaction
    silently no-opped and the account number was stored and indexed raw.
    """
    result = await _store_with_consent(store, "user", "bank_note", BANK_MEMORY)
    assert result.stored is True

    value, _ = _stored_plaintext(store, result.memory_id)
    assert "12345" not in value, (
        "regex metacharacters in the memory defeated redaction; "
        f"stored value still contains the account number: {value!r}"
    )


def test_redaction_instruction_rejects_an_uncompilable_pattern() -> None:
    """An instruction is validated when it is constructed, not when it is
    applied, so an unusable policy can never reach `re.sub` and silently
    return the text unchanged."""
    from bartholomew.kernel.redaction_engine import (
        RedactionInstruction,
        RedactionPolicyError,
    )

    with pytest.raises(RedactionPolicyError):
        RedactionInstruction(patterns=("(unclosed",), strategy="mask")


def test_redaction_instruction_rejects_an_unknown_strategy() -> None:
    from bartholomew.kernel.redaction_engine import (
        RedactionInstruction,
        RedactionPolicyError,
    )

    with pytest.raises(RedactionPolicyError):
        RedactionInstruction(patterns=(r"\d+",), strategy="obliterate")


def test_redaction_instruction_rejects_an_empty_pattern_set() -> None:
    from bartholomew.kernel.redaction_engine import (
        RedactionInstruction,
        RedactionPolicyError,
    )

    with pytest.raises(RedactionPolicyError):
        RedactionInstruction(patterns=(), strategy="mask")


# ---------------------------------------------------------------------------
# 3. An ordinary sensitive case is redacted precisely
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ordinary_sensitive_value_is_redacted_not_the_whole_memory(store) -> None:
    result = await _store_with_consent(store, "user", "email_note", EMAIL_MEMORY)
    value, _ = _stored_plaintext(store, result.memory_id)

    assert "a@b.com" not in value, f"the email address survived redaction: {value!r}"
    assert "my email is" in value, f"redaction destroyed benign text: {value!r}"


def test_classifier_word_match_does_not_leave_the_value_exposed() -> None:
    """The test that distinguishes a *classifier* regex from a redaction
    instruction.

    The rule that fires for "my email is a@b.com" matches on the literal
    word `email`. Had FND-02 stopped at "pass the matched rule's own regex
    to apply_redaction", this would produce "my **** is a@b.com" -- the
    classifier word masked, the actual address untouched. The repair is
    only real if the resolved instruction targets the *value*.
    """
    from bartholomew.kernel.memory_rules import (
        redaction_required,
        resolve_redaction_instruction,
    )
    from bartholomew.kernel.redaction_engine import apply_redaction

    evaluated = _evaluate(EMAIL_MEMORY)
    assert redaction_required(evaluated), "expected policy to require redaction here"

    instruction = resolve_redaction_instruction(evaluated)
    assert instruction is not None

    redacted = apply_redaction(EMAIL_MEMORY, instruction)
    assert "a@b.com" not in redacted
    # The classifier word itself is not the sensitive material and must not
    # be collateral damage -- see redact_pii()'s note in redaction_engine.py
    # about "Answer health question" becoming "Answer **** question".
    assert "email" in redacted


# ---------------------------------------------------------------------------
# 4. FTS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sensitive_value_never_reaches_the_fts_index(store) -> None:
    result = await _store_with_consent(store, "user", "bank_note", BANK_MEMORY)
    assert result.stored is True

    assert (
        _fts_matches(store, "12345") == []
    ), "the raw account number is searchable in the plaintext FTS index"
    index_text = _fts_index_text(store, result.memory_id)
    assert (
        index_text is None or "12345" not in index_text
    ), f"memory_fts_map.last_index_text holds the raw account number: {index_text!r}"


@pytest.mark.asyncio
async def test_email_address_never_reaches_the_fts_index(store) -> None:
    result = await _store_with_consent(store, "user", "email_note", EMAIL_MEMORY)
    assert result.stored is True

    # FTS5's default tokenizer splits on '@' and '.', so the address is
    # indexed as the adjacent tokens a / b / com and is searchable as a
    # phrase. ('a@b.com' unquoted is not valid FTS5 query syntax.)
    assert _fts_matches(store, '"a b com"') == []
    index_text = _fts_index_text(store, result.memory_id)
    assert index_text is None or "a@b.com" not in index_text


# ---------------------------------------------------------------------------
# 5. Chunks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sensitive_value_never_reaches_chunk_text(store) -> None:
    """Chunking runs on `redacted_value`, so it inherits whatever redaction
    produced. Long transcript content carrying an account number must not
    leave that number sitting in `memory_chunks.text`, which is stored
    un-encrypted by design (see the schema comment: "redacted,
    pre-encryption")."""
    filler = " ".join(f"word{i}" for i in range(900))
    value = f"bank details: acct 12345 (branch) {filler}"
    result = await _store_with_consent(store, "conversation.transcript", "t1", value)
    assert result.stored is True

    chunks = _chunk_texts(store, result.memory_id)
    assert chunks, "expected this content to be chunked"
    joined = "\n".join(chunks)
    assert "12345" not in joined, "the raw account number leaked into memory_chunks.text"


# ---------------------------------------------------------------------------
# 6. Embeddings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_source_text_is_redacted(store, recording_embedder) -> None:
    """`_handle_embeddings()` used to rebuild its text from the *original*
    `memory_dict["value"]` and re-apply redaction itself -- a second,
    independent copy of the same broken call. It must now receive the
    already-governed text instead of re-deriving it from raw."""
    result = await _store_with_consent(store, "user", "bank_note", BANK_MEMORY)
    assert result.stored is True

    assert recording_embedder.texts, "expected the embedder to be called"
    for text in recording_embedder.texts:
        assert "12345" not in text, f"unredacted text reached the embedder: {text!r}"


# ---------------------------------------------------------------------------
# 7. Caller-supplied summaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caller_supplied_summary_obeys_the_same_contract(store) -> None:
    """A caller-supplied summary is built from the caller's own
    pre-redaction representation, so it must be redacted with the same
    instruction rather than trusted."""
    result = await _store_with_consent(store, "user", "bank_note", BANK_MEMORY)
    assert result.stored is True

    # Now the same content with an explicit summary that re-states it.
    second = await store.upsert_memory(
        "user",
        "bank_note_2",
        BANK_MEMORY,
        TS,
        summary="Summary: bank acct 12345 for the branch.",
        skip_rule_consent=True,
        skip_privacy_guard=True,
    )
    assert second.stored is True

    value, summary = _stored_plaintext(store, second.memory_id)
    assert "12345" not in value
    if summary is not None:
        assert "12345" not in summary, f"caller summary bypassed redaction: {summary!r}"
    assert _fts_matches(store, "12345") == []


# ---------------------------------------------------------------------------
# 8. Consent promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consent_approval_does_not_bypass_redaction(store) -> None:
    """`approve_pending_sensitive_write()` re-runs the pipeline with both
    gate-skip flags set. Those flags must skip the *gates*, never the
    redaction."""
    first = await store.upsert_memory("user", "bank_note", BANK_MEMORY, TS)
    assert first.stored is False
    assert first.outcome == "queued_for_consent"

    pending = await store.list_pending_sensitive_writes()
    assert len(pending) == 1
    approved = await store.approve_pending_sensitive_write(pending[0]["id"])
    assert approved.stored is True

    value, _ = _stored_plaintext(store, approved.memory_id)
    assert "12345" not in value
    assert _fts_matches(store, "12345") == []

    # And consent itself still works: the promoted row is recorded as
    # human-approved, not as an embedding side effect.
    conn = sqlite3.connect(store.db_path)
    try:
        source = conn.execute(
            "SELECT source FROM memory_consent WHERE memory_id = ?",
            (approved.memory_id,),
        ).fetchone()
    finally:
        conn.close()
    assert source is not None and source[0] == "consent_approval"


# ---------------------------------------------------------------------------
# 9. Deterministic resolution across multiple matching rules
# ---------------------------------------------------------------------------


def test_multiple_matching_rules_resolve_deterministically() -> None:
    """ "bank details: ..." matches two ask_before_store rules at once. The
    resolved instruction must be a single, stable, order-independent
    answer -- not "whichever rule happened to be merged first"."""
    from bartholomew.kernel.memory_rules import resolve_redaction_instruction

    evaluated = _evaluate(BANK_MEMORY)
    assert len(evaluated["matched_rules"]) >= 2, "expected >1 matching rule"

    first = resolve_redaction_instruction(evaluated)
    assert first is not None
    for _ in range(5):
        again = resolve_redaction_instruction(_evaluate(BANK_MEMORY))
        assert again == first, "redaction resolution is not deterministic"


def test_multiple_matching_rules_union_their_patterns() -> None:
    """When two rules both demand redaction, the safe resolution is the
    union of what they each protect -- never a subset."""
    from bartholomew.kernel.memory_rules import resolve_redaction_instruction
    from bartholomew.kernel.redaction_engine import apply_redaction

    evaluated = {
        "allow_store": True,
        "redact": True,
        "redact_strategy": "mask",
        "matched_rules": [
            ("ask_before_store", {"content": "x"}),
            ("ask_before_store", {"content": "y"}),
        ],
        "redact_patterns": ["email", "ssn"],
    }
    instruction = resolve_redaction_instruction(evaluated)
    assert instruction is not None
    redacted = apply_redaction("mail a@b.com and ssn 123-45-6789", instruction)
    assert "a@b.com" not in redacted
    assert "123-45-6789" not in redacted


# ---------------------------------------------------------------------------
# 10. Fail closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_required_redaction_with_no_patterns_refuses_the_write(
    store,
    monkeypatch,
) -> None:
    """Policy says "redact this" and names nothing to redact.

    The pre-FND-02 behaviour for every unusable pattern was
    `return text` -- store and index the raw material. That is the exact
    silent fallback FND-02 forbids.
    """

    def _evaluate_no_patterns(memory: dict) -> dict:
        result = dict(memory)
        result.update(
            {
                "allow_store": True,
                "requires_consent": False,
                "redact": True,
                "redact_strategy": "mask",
                "matched_rules": [],
            },
        )
        return result

    monkeypatch.setattr(memory_store_module._rules_engine, "evaluate", _evaluate_no_patterns)

    result = await store.upsert_memory("user", "x", "acct 12345 here", TS)
    assert result.stored is False
    assert result.outcome == "refused_redaction_unavailable"

    conn = sqlite3.connect(store.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM memory_chunks").fetchone()[0] == 0
    finally:
        conn.close()
    assert _fts_matches(store, "12345") == []


@pytest.mark.asyncio
async def test_required_redaction_with_invalid_pattern_refuses_the_write(
    store,
    monkeypatch,
) -> None:
    def _evaluate_bad_pattern(memory: dict) -> dict:
        result = dict(memory)
        result.update(
            {
                "allow_store": True,
                "requires_consent": False,
                "redact": True,
                "redact_strategy": "mask",
                "redact_patterns": ["(unclosed"],
                "matched_rules": [],
            },
        )
        return result

    monkeypatch.setattr(memory_store_module._rules_engine, "evaluate", _evaluate_bad_pattern)

    result = await store.upsert_memory("user", "x", "acct 12345 here", TS)
    assert result.stored is False
    assert result.outcome == "refused_redaction_unavailable"
    assert _fts_matches(store, "12345") == []


@pytest.mark.asyncio
async def test_unknown_named_pattern_refuses_the_write(store, monkeypatch) -> None:
    """A typo in a pattern name must not read as "nothing to redact"."""

    def _evaluate_unknown_name(memory: dict) -> dict:
        result = dict(memory)
        result.update(
            {
                "allow_store": True,
                "requires_consent": False,
                "redact": True,
                "redact_patterns": ["emial"],
                "matched_rules": [],
            },
        )
        return result

    monkeypatch.setattr(memory_store_module._rules_engine, "evaluate", _evaluate_unknown_name)

    result = await store.upsert_memory("user", "x", "mail a@b.com", TS)
    assert result.stored is False
    assert result.outcome == "refused_redaction_unavailable"


def test_compute_governed_index_text_fails_closed(monkeypatch) -> None:
    """The self-heal / backfill reindex path recomputes index text from
    stored plaintext. If it cannot resolve the redaction policy that
    applies, it must decline to index rather than index raw."""

    def _evaluate_broken(memory: dict) -> dict:
        result = dict(memory)
        result.update(
            {
                "allow_store": True,
                "redact": True,
                "redact_patterns": ["(unclosed"],
                "matched_rules": [],
            },
        )
        return result

    monkeypatch.setattr(memory_store_module._rules_engine, "evaluate", _evaluate_broken)

    assert (
        memory_store_module.compute_governed_index_text(
            "user",
            "k",
            "acct 12345 here",
            None,
            TS,
        )
        is None
    )


# ---------------------------------------------------------------------------
# 11. Unrelated, non-sensitive memory is untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_sensitive_memory_is_stored_and_indexed_unchanged(store) -> None:
    benign = "Remember to water the plants on Tuesday."
    result = await store.upsert_memory("note", "plants", benign, TS)
    assert result.stored is True
    assert result.outcome == "stored"

    value, _ = _stored_plaintext(store, result.memory_id)
    assert value == benign, "a memory no rule targets must be stored byte-for-byte"
    assert _fts_matches(store, "plants"), "benign content must remain searchable"


def test_no_redaction_required_resolves_to_no_instruction() -> None:
    from bartholomew.kernel.memory_rules import (
        redaction_required,
        resolve_redaction_instruction,
    )

    evaluated = _evaluate("Remember to water the plants on Tuesday.")
    assert redaction_required(evaluated) is False
    assert resolve_redaction_instruction(evaluated) is None


@pytest.mark.asyncio
async def test_benign_text_containing_a_classifier_word_keeps_its_words(store) -> None:
    """ "Answer health question" must not become "Answer **** question".

    redaction_engine.redact_pii() already documents this exact regression
    from an earlier attempt at keyword-based redaction. The rule-driven
    path must not reintroduce it: a classifier word is what *selects* a
    policy, never what the policy removes.
    """
    value = "Ask the clinic about my medical appointment booking process."
    result = await _store_with_consent(store, "user", "appt", value)
    assert result.stored is True

    stored, _ = _stored_plaintext(store, result.memory_id)
    assert "medical" in stored
    assert "appointment" in stored
    assert "****" not in stored


# ---------------------------------------------------------------------------
# 12. Encryption and consent are untouched, separate controls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_encryption_still_applies_to_redacted_content(store, monkeypatch) -> None:
    """Redaction is not a substitute for encryption. The bank rule sets
    `encrypt: strong`; the stored row must still be an envelope, and what
    is inside it must be the redacted text."""
    key_b64 = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("BME_KEY_STRONG", key_b64)

    result = await _store_with_consent(store, "user", "bank_note", BANK_MEMORY)
    assert result.stored is True

    conn = sqlite3.connect(store.db_path)
    try:
        raw = conn.execute(
            "SELECT value FROM memories WHERE id = ?",
            (result.memory_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    from bartholomew.kernel import encryption_engine as enc

    decrypted = enc._encryption_engine.try_decrypt_if_envelope(raw)
    assert decrypted != raw, "encryption stopped applying"
    assert "12345" not in decrypted, "redaction did not apply underneath encryption"


@pytest.mark.asyncio
async def test_never_store_rule_still_blocks_outright(store) -> None:
    """`never_store` is a hard block with no promotion path, and redaction
    must not become an excuse to store what policy says never to store."""
    result = await store.upsert_memory("user", "bad", "this is illegal content", TS)
    assert result.stored is False
    assert result.outcome == "refused"


@pytest.mark.asyncio
async def test_consent_gate_still_queues_before_storing(store) -> None:
    first = await store.upsert_memory("user", "bank_note", BANK_MEMORY, TS)
    assert first.stored is False
    assert first.outcome == "queued_for_consent"

    conn = sqlite3.connect(store.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The previously-dead `redact:` config category
# ---------------------------------------------------------------------------


def test_yaml_redact_category_is_loaded() -> None:
    """`memory_rules.yaml` has carried a `redact:` section since v2.1 and
    `MemoryRulesEngine.PRIORITY` omitted it, so every rule under it was
    dead config that looked like live policy. A governance file whose
    rules do nothing is worse than no rule at all."""
    assert "redact" in MemoryRulesEngine.PRIORITY
    assert _rules_engine.rules_by_category.get("redact"), "the redact: category loaded no rules"


@pytest.mark.asyncio
async def test_ssn_is_redacted_end_to_end(store) -> None:
    """The SSN rule lives in that previously-dead `redact:` category. Until
    FND-02 an SSN matched no loaded rule at all: stored raw, indexed raw,
    and not even encrypted."""
    value = "ssn: 123-45-6789"
    result = await _store_with_consent(store, "note", "ssn", value)
    assert result.stored is True

    stored, _ = _stored_plaintext(store, result.memory_id)
    assert "123-45-6789" not in stored, f"the SSN was stored raw: {stored!r}"
    assert _fts_matches(store, "6789") == []
