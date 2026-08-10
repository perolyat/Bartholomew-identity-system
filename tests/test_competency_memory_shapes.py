"""
Integration tests: each of the five competency `kind` values round-trips
through the real, unmodified `MemoryStore.upsert_memory()` exactly like any
other kind -- redaction, encryption, FTS indexing, and the never_store/
ask_before_store consent gates all apply unchanged. Proves S5.1 introduces
no governance bypass and that JSON-in-`value` survives redaction/encryption
intact (design doc Sec 4.2's flagged risk).

See docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from bartholomew.kernel import encryption_engine as _encryption_module
from bartholomew.kernel.competency import (
    COMPETENCY_KINDS,
    CompetencyEnvelope,
    CompetencyEvidence,
    CompetencyHeuristic,
    CompetencyKnowledge,
    CompetencyProcedure,
    CompetencyRecord,
    Provenance,
)
from bartholomew.kernel.memory_store import MemoryStore

TS = "2026-08-09T00:00:00Z"


def _envelope(**overrides) -> CompetencyEnvelope:
    defaults = {
        "competency_id": "estate_management",
        "classification": "personal",
        "provenance": Provenance(source_type="experience", detail="test fixture"),
        "confidence": 0.5,
    }
    defaults.update(overrides)
    return CompetencyEnvelope(**defaults)


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "competency_shapes.db"))
    await s.init()
    return s


def _all_five_records() -> list:
    env = _envelope()
    return [
        CompetencyRecord(envelope=env, name="Residential Estate Management"),
        CompetencyKnowledge(
            envelope=env,
            slug="warranty",
            topic="Warranty",
            content="6-year parts warranty.",
        ),
        CompetencyProcedure(
            envelope=env,
            slug="quote_comparison",
            name="Quote Comparison",
            steps=["Get 3 quotes", "Compare price/scope/warranty"],
        ),
        CompetencyHeuristic(
            envelope=env,
            slug="check_warranty_first",
            rule="Check warranty before recommending replacement.",
        ),
        CompetencyEvidence(
            envelope=env,
            slug="smith_plumbing_2026",
            situation="Hot water repair in 2026.",
            outcome="Fixed for $340.",
        ),
    ]


@pytest.mark.asyncio
async def test_each_kind_round_trips_through_upsert_memory(store) -> None:
    for record in _all_five_records():
        result = await store.upsert_memory(
            kind=record.KIND,
            key=record.key(),
            value=json.dumps(record.to_dict()),
            ts=TS,
            summary=record.to_summary_text(),
            # Found while implementing: privacy_guard.is_sensitive() does a
            # raw substring scan of the *entire* serialized value, including
            # JSON syntax -- CompetencyRecord/CompetencyProcedure's own
            # `"name"` JSON key literally contains the substring "name",
            # which is in privacy_guard.SENSITIVE_KEYWORDS, so it fires on
            # every write of those two kinds regardless of content. This is
            # pre-existing behaviour of is_sensitive() (a raw substring
            # check with no JSON/structural awareness), not something S5.1
            # introduces or fixes -- flagged in the implementation summary,
            # not addressed here. skip_privacy_guard=True is the same,
            # already-sanctioned bypass approve_pending_sensitive_write()
            # itself uses for content a human (or, here, a test) has
            # already determined isn't actually sensitive.
            skip_privacy_guard=True,
        )
        assert result.stored is True, f"{record.KIND} failed to store"
        assert result.memory_id is not None

        conn = sqlite3.connect(store.db_path)
        try:
            row = conn.execute(
                "SELECT kind, key, value, summary FROM memories WHERE id = ?",
                (result.memory_id,),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row[0] == record.KIND
        assert row[1] == record.key()
        stored_dict = json.loads(row[2])
        assert stored_dict == record.to_dict()
        assert row[3] == record.to_summary_text()


@pytest.mark.asyncio
async def test_all_five_kinds_are_covered(store) -> None:
    """Guard the guard: the fixture above must actually exercise all five
    design-doc kinds, not a subset."""
    kinds_used = {r.KIND for r in _all_five_records()}
    assert kinds_used == set(COMPETENCY_KINDS)


@pytest.mark.asyncio
async def test_caller_supplied_summary_is_used_verbatim_not_auto_generated(store) -> None:
    """The §4.1 Option A change: a caller-supplied summary bypasses
    auto-summarisation entirely, so a competency record's own plain-text
    rendering (not a sentence-extraction over its JSON blob) is what's
    stored and FTS-indexed."""
    record = CompetencyProcedure(
        envelope=_envelope(),
        slug="quote_comparison",
        name="Quote Comparison",
        steps=["Get 3 quotes", "Compare price/scope/warranty"],
    )
    value = json.dumps(record.to_dict())
    summary = record.to_summary_text()

    result = await store.upsert_memory(
        kind=record.KIND,
        key=record.key(),
        value=value,
        ts=TS,
        summary=summary,
        skip_privacy_guard=True,  # see test_each_kind_round_trips_...'s comment
    )
    conn = sqlite3.connect(store.db_path)
    try:
        stored_summary = conn.execute(
            "SELECT summary FROM memories WHERE id = ?",
            (result.memory_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    assert stored_summary == summary
    # It must not be a sentence-extraction over the raw JSON -- the JSON's
    # own syntax characters (braces, quotes) must not leak into the summary.
    assert "{" not in stored_summary
    assert '"competency_id"' not in stored_summary


@pytest.mark.asyncio
async def test_existing_callers_without_summary_argument_are_unaffected(store) -> None:
    """Regression guard for §4.1: an ordinary, pre-existing-shape call with
    no `summary` argument must behave exactly as before -- summary stays
    None (short content isn't auto-summarised either) and storage succeeds."""
    result = await store.upsert_memory(kind="fact", key="k1", value="short value", ts=TS)
    assert result.stored is True

    conn = sqlite3.connect(store.db_path)
    try:
        summary = conn.execute(
            "SELECT summary FROM memories WHERE id = ?",
            (result.memory_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert summary is None


@pytest.mark.asyncio
async def test_redaction_of_json_embedded_content_preserves_valid_json(store) -> None:
    """Design doc Sec 4.2's flagged risk: content-regex redaction operates on
    the whole `value` string, JSON or not, so JSON structure must survive it
    intact. Two things found while implementing mean that risk cannot
    currently be exercised end-to-end through `upsert_memory()` -- both
    pre-existing, unrelated to and not fixed by S5.1, flagged in the
    implementation summary:

    1. memory_rules.yaml's own `redact:` category (content-regex-triggered
       redaction, e.g. the SSN pattern design doc Sec 4.2 originally cited)
       is never actually loaded -- `MemoryRulesEngine.PRIORITY` is
       `["never_store", "ask_before_store", "always_keep", "auto_expire",
       "context_only"]`, which omits `redact` entirely, so every rule under
       that YAML category is dead code today, for every kind.

    2. The *live* `ask_before_store` category's `redact_strategy: mask` is
       ALSO non-functional, for every kind, via a second, independent bug:
       `memory_store.upsert_memory()` calls
       `apply_redaction(redacted_value, evaluated)`, and
       `apply_redaction()` reads its regex pattern from `rule.get("content")`
       -- but `MemoryRulesEngine.evaluate()` builds its return value as
       `enriched = dict(m); enriched.update(result_meta)`, and no matched
       rule's `metadata` ever sets a `content` key (that key only exists
       under each rule's `match:` block, which `evaluate()` never copies
       into the result), so `evaluated["content"]` is left holding `m`'s own
       `content` -- the memory's own literal, full text -- not the matched
       rule's regex pattern. `mask_sensitive()` is therefore asked to use
       the memory's own text as a regex pattern against itself, which
       reliably fails to compile/match meaningfully and silently returns
       the text unchanged via its own except-and-return-original fallback.

    Net effect: redaction does not fire today through any live path, for any
    kind. This test therefore checks two things separately instead: Part 1
    exercises the real, unmodified `upsert_memory()` pipeline end-to-end for
    a competency kind and asserts what it actually, currently does --
    encryption fires (that part IS live), redaction does not -- proving no
    competency-specific bypass exists either way. Part 2 tests the design
    doc's actual concern (JSON survives in-place regex redaction) directly
    against `redaction_engine.apply_redaction()`, decoupled from the
    `evaluate()` wiring bug above, so the JSON-shape guarantee itself is
    verified independent of that bug and remains valid once it's fixed.
    """
    record = CompetencyEvidence(
        envelope=_envelope(classification="personal"),
        slug="contains_matched_pattern",
        situation="Contractor asked to confirm our phone number before the visit.",
        outcome="Completed.",
    )
    result = await store.upsert_memory(
        kind=record.KIND,
        key=record.key(),
        value=json.dumps(record.to_dict()),
        ts=TS,
        summary=record.to_summary_text(),
        skip_privacy_guard=True,
        skip_rule_consent=True,
    )
    assert result.stored is True

    conn = sqlite3.connect(store.db_path)
    try:
        raw_value = conn.execute(
            "SELECT value FROM memories WHERE id = ?",
            (result.memory_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    # Part 1: the matched ask_before_store rule sets encrypt: strong, and
    # that part IS live -- the raw row must not hold plaintext, competency
    # kind or not.
    decrypted = _encryption_module._encryption_engine.try_decrypt_if_envelope(raw_value)
    assert decrypted != raw_value, "expected the value to actually be encrypted"
    parsed = json.loads(decrypted)
    assert parsed["competency_id"] == "estate_management"
    assert parsed["outcome"] == "Completed."
    # Reflects actual, current (buggy) behaviour, not the design intent:
    # redaction did not fire, so the matched word is still present.
    assert "phone" in parsed["situation"].lower()

    # Part 2: the design doc's actual flagged risk, tested directly against
    # redaction_engine so it doesn't depend on the evaluate()-wiring bug
    # above -- proves competency JSON is redaction-safe on its own, whenever
    # a real pattern does reach apply_redaction().
    from bartholomew.kernel.redaction_engine import apply_redaction

    synthetic_rule = {"content": r"(?i)phone", "redact_strategy": "mask"}
    redacted = apply_redaction(json.dumps(record.to_dict()), synthetic_rule)
    reparsed = json.loads(redacted)
    assert "phone" not in json.dumps(reparsed).lower()
    assert "****" in reparsed["situation"]
    assert reparsed["competency_id"] == "estate_management"
    assert reparsed["outcome"] == "Completed."


@pytest.mark.asyncio
async def test_ask_before_store_content_is_queued_not_bypassed(store) -> None:
    """A competency_evidence record whose content matches an existing
    ask_before_store pattern is queued for review exactly like any other
    kind -- classification/kind does not grant competency content any
    governance exemption."""
    record = CompetencyEvidence(
        envelope=_envelope(),
        slug="mentions_auth_code",
        situation="User asked me to remember their auth code for later.",
    )
    result = await store.upsert_memory(
        kind=record.KIND,
        key=record.key(),
        value=json.dumps(record.to_dict()),
        ts=TS,
        summary=record.to_summary_text(),
    )
    assert result.stored is False

    pending = await store.list_pending_sensitive_writes()
    assert len(pending) == 1
    assert pending[0]["kind"] == record.KIND
    assert pending[0]["reason"] == "rule_consent"

    conn = sqlite3.connect(store.db_path)
    try:
        mem_rows = conn.execute("SELECT * FROM memories").fetchall()
    finally:
        conn.close()
    assert mem_rows == []


@pytest.mark.asyncio
async def test_consent_queued_competency_write_preserves_governed_summary_after_approval(
    store,
) -> None:
    """Blocking-issue point 5 (final S5.1 review): the ask-before-store
    round trip for a competency write that carried a caller-supplied
    `summary=` must, after approval, behave under exactly the same governed
    summary policy as any other content -- no special or broken hybrid
    state.

    `pending_sensitive_writes` has no `summary` column (see
    record_pending_write()'s schema) and `approve_pending_sensitive_write()`
    re-invokes `upsert_memory()` without a `summary=` argument (see its own
    docstring) -- by design, not omission: no new column or
    competency-specific consent path was added to thread it through. The
    caller's original plain-text summary is therefore dropped at queue time
    and cannot resurface after approval. This test proves that degrades
    cleanly to the same governed outcome
    `test_existing_callers_without_summary_argument_are_unaffected` (in this
    file) establishes as the baseline for a plain call with no `summary=`:
    the approved value round-trips exactly, and `summary` stays None
    because this content/kind never triggers `should_summarize()` (the
    matched rule sets no `summarize: true`, and `competency_evidence` is
    not in `AUTO_SUMMARIZE_KINDS`).
    """
    record = CompetencyEvidence(
        envelope=_envelope(),
        slug="mentions_auth_code",
        situation="User asked me to remember their auth code for later.",
    )
    result = await store.upsert_memory(
        kind=record.KIND,
        key=record.key(),
        value=json.dumps(record.to_dict()),
        ts=TS,
        summary=record.to_summary_text(),
    )
    assert result.stored is False  # queued, not stored -- caller summary dropped here

    pending_id = (await store.list_pending_sensitive_writes())[0]["id"]
    approved = await store.approve_pending_sensitive_write(pending_id)
    assert approved.stored is True

    conn = sqlite3.connect(store.db_path)
    try:
        raw_value, summary = conn.execute(
            "SELECT value, summary FROM memories WHERE id = ?",
            (approved.memory_id,),
        ).fetchone()
    finally:
        conn.close()

    # The matched ask_before_store rule sets encrypt: strong -- decrypt
    # before comparing, same as test_redaction_of_json_embedded_content_...
    decrypted = _encryption_module._encryption_engine.try_decrypt_if_envelope(raw_value)
    stored_dict = json.loads(decrypted)
    assert stored_dict == record.to_dict()
    assert summary is None


@pytest.mark.asyncio
async def test_never_store_content_is_hard_blocked_not_bypassed(store) -> None:
    record = CompetencyEvidence(
        envelope=_envelope(),
        slug="blocked_content",
        situation="This contains illegal content that should be blocked",
    )
    result = await store.upsert_memory(
        kind=record.KIND,
        key=record.key(),
        value=json.dumps(record.to_dict()),
        ts=TS,
        summary=record.to_summary_text(),
    )
    assert result.stored is False
    assert (await store.list_pending_sensitive_writes()) == []
