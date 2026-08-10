"""
Regression tests for the S5.1 fix to MemoryStore.upsert_memory()'s
caller-supplied `summary` parameter.

Blocking issue this closes (final S5.1 review, 2026-08-10): the original
S5.1 diff added `summary: str | None = None` and, when supplied, skipped
the auto-summarisation branch entirely -- which also skipped every policy
check that branch carried: `summary_only` substitution, `full_always`/
`summarize: false` authority, and (since a caller-supplied summary isn't
guaranteed to derive from the already-redacted value the way an
auto-generated one always is) redaction before storage/FTS.

Deliberately generic -- no competency awareness. bartholomew.kernel.
competency is not imported by this file at all; `kind="test_kind"` is used
throughout to prove the fix is not competency-specific. The rules engine's
`evaluate()` is monkeypatched to return controlled policy dicts (mirrors
tests/test_retrieval_consent_enforcement.py's established
`_consenting_rules_engine()` Mock pattern) so these tests don't depend on
memory_rules.yaml content and don't require editing it.

See docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md.
"""

from __future__ import annotations

import sqlite3

import pytest

from bartholomew.kernel import memory_store as memory_store_module
from bartholomew.kernel.memory_store import MemoryStore

TS = "2026-08-10T00:00:00Z"


def _fixed_evaluate(**policy):
    """A rules_engine.evaluate() replacement returning a fixed policy dict
    for every call, independent of memory_rules.yaml content. `allow_store`
    and `requires_consent` default open so these tests reach the storage
    path being exercised."""
    base = {"allow_store": True, "requires_consent": False}
    base.update(policy)

    def _evaluate(memory: dict) -> dict:
        result = dict(memory)
        result.update(base)
        return result

    return _evaluate


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "summary_policy.db"))
    await s.init()
    return s


def _row(store, memory_id):
    conn = sqlite3.connect(store.db_path)
    try:
        return conn.execute(
            "SELECT value, summary FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_caller_supplied_summary_cannot_bypass_summary_only(store, monkeypatch) -> None:
    """summary_mode: summary_only means only the summary is ever retained as
    the stored value -- a caller-supplied summary must trigger exactly that
    substitution, the same as an auto-generated one already does."""
    monkeypatch.setattr(
        memory_store_module._rules_engine,
        "evaluate",
        _fixed_evaluate(summary_mode="summary_only"),
    )
    full_value = "This is the full original content that must not survive storage."
    caller_summary = "Short summary."

    result = await store.upsert_memory(
        kind="test_kind",
        key="k1",
        value=full_value,
        ts=TS,
        summary=caller_summary,
    )
    assert result.stored is True

    value, summary = _row(store, result.memory_id)
    assert value == caller_summary
    assert summary is None
    assert full_value not in value


@pytest.mark.asyncio
async def test_full_always_policy_discards_caller_supplied_summary(store, monkeypatch) -> None:
    """summary_mode: full_always means no summary is ever recorded -- a
    caller-supplied summary must not silently override that."""
    monkeypatch.setattr(
        memory_store_module._rules_engine,
        "evaluate",
        _fixed_evaluate(summary_mode="full_always"),
    )
    full_value = "Full content that must be retained exactly as given."

    result = await store.upsert_memory(
        kind="test_kind",
        key="k2",
        value=full_value,
        ts=TS,
        summary="This summary must be discarded.",
    )
    assert result.stored is True

    value, summary = _row(store, result.memory_id)
    assert value == full_value
    assert summary is None


@pytest.mark.asyncio
async def test_explicit_summarize_false_discards_caller_supplied_summary(
    store,
    monkeypatch,
) -> None:
    """An explicit `summarize: false` rule is as authoritative as
    full_always for a caller-supplied summary, even under the default
    summary_also mode -- it must not silently override that policy."""
    monkeypatch.setattr(
        memory_store_module._rules_engine,
        "evaluate",
        _fixed_evaluate(summarize=False),
    )
    result = await store.upsert_memory(
        kind="test_kind",
        key="k3",
        value="Full content.",
        ts=TS,
        summary="Must be discarded too.",
    )
    assert result.stored is True

    _, summary = _row(store, result.memory_id)
    assert summary is None


@pytest.mark.asyncio
async def test_caller_supplied_summary_is_redacted_before_storage_and_fts(
    store,
    monkeypatch,
) -> None:
    """A caller-supplied summary containing sensitive text the matched
    rule's redact_strategy targets must be redacted before it can reach
    storage or FTS -- exactly like the `value` it accompanies. Proves the
    fix threads the summary through the same apply_redaction() call the
    value already goes through, closing the asymmetry where `value` was
    redacted but a caller-supplied `summary` never was.

    Genuinely queries `memory_fts` via MATCH -- not by reading its `value`
    column back directly. `memory_fts` is an FTS5 *external content* table
    (`content='memories'`, confirmed via its schema): reading a column back
    by rowid proxies through to the live `memories` row rather than
    reflecting what was tokenized into the search index at insert time, so
    it cannot prove anything about what's actually indexed/searchable --
    only a MATCH query exercises the real index.
    """
    monkeypatch.setattr(
        memory_store_module._rules_engine,
        "evaluate",
        _fixed_evaluate(redact_strategy="mask", content=r"(?i)secretword"),
    )
    result = await store.upsert_memory(
        kind="test_kind",
        key="k4",
        value="Value without the trigger word.",
        ts=TS,
        summary="Summary containing secretword that must not leak.",
    )
    assert result.stored is True

    _, summary = _row(store, result.memory_id)
    assert "secretword" not in summary.lower()
    assert "****" in summary

    conn = sqlite3.connect(store.db_path)
    try:
        sensitive_matches = conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?",
            ("secretword",),
        ).fetchall()
        redacted_matches = conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?",
            ("summary",),
        ).fetchall()
    finally:
        conn.close()

    # Searching for the raw sensitive token finds nothing, anywhere in FTS.
    assert sensitive_matches == []
    # Searching for a word from the *redacted* summary finds this row -- the
    # governed text is genuinely what's indexed/retrievable.
    assert any(r[0] == result.memory_id for r in redacted_matches)


@pytest.mark.asyncio
async def test_ordinary_calls_without_summary_argument_are_unaffected(store, monkeypatch) -> None:
    """Baseline: a plain call with no `summary=` behaves identically to
    before this fix. summary_only/full_always/summarize:false handling is
    all gated on a caller-supplied summary being present and never touches
    this case -- content too short to auto-summarise is retained in full,
    exactly as should_summarize()-gated behaviour has always worked."""
    monkeypatch.setattr(
        memory_store_module._rules_engine,
        "evaluate",
        _fixed_evaluate(summary_mode="summary_only"),
    )
    result = await store.upsert_memory(kind="test_kind", key="k5", value="short", ts=TS)
    assert result.stored is True

    value, summary = _row(store, result.memory_id)
    assert value == "short"
    assert summary is None


@pytest.mark.asyncio
async def test_auto_generated_summary_path_is_unaffected_by_the_fix(store, monkeypatch) -> None:
    """Baseline: when the caller supplies no summary but content is long
    enough / of a kind that auto-summarisation would trigger, behaviour is
    unchanged -- summary is still auto-generated and summary_only
    substitution still applies exactly as before."""
    monkeypatch.setattr(
        memory_store_module._rules_engine,
        "evaluate",
        _fixed_evaluate(summarize=True, summary_mode="summary_only"),
    )
    long_value = "Sentence one is here. " * 40  # comfortably over the length threshold
    result = await store.upsert_memory(kind="test_kind", key="k6", value=long_value, ts=TS)
    assert result.stored is True

    value, summary = _row(store, result.memory_id)
    assert value != long_value  # replaced by the auto-generated summary
    assert len(value) < len(long_value)
    assert summary is None


class _FakeEmbedEngine:
    """Records exactly what text upsert_memory() asked to be embedded, so a
    test can assert on it directly instead of inferring it from a real
    vector's contents (which isn't otherwise inspectable)."""

    def __init__(self) -> None:
        self.embedded_texts: list[str] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.extend(texts)
        return [[0.0] for _ in texts]


@pytest.mark.asyncio
async def test_caller_supplied_summary_under_summary_only_flows_to_embeddings(
    store,
    monkeypatch,
) -> None:
    """Blocker (final S5.1 v2 review, 2026-08-10): `resolved_summary` was
    captured *after* the summary_only substitution cleared `summary`, so
    under summary_only it was always None by the time `_handle_embeddings()`
    received it -- which then fell back to independently re-summarising the
    ORIGINAL full value, producing an embedding inconsistent with what was
    actually stored and FTS-indexed (the governed caller-supplied summary),
    and capable of encoding content summary_only exists to remove.

    Distinguishes the two possible embedding texts deliberately:
    `_summarization_engine.summarize()` is a naive extractive summarizer
    that returns the first N sentences of its input verbatim -- run over
    `full_value` (what the buggy fallback path would embed), its output is
    `full_value`'s own first sentence, chosen here to share no words with
    `caller_summary`. This test fails under the pre-fix implementation,
    which would record that first sentence instead of `caller_summary`.

    `_get_embedding_components` (not `BARTHO_EMBED_ENABLED`) is
    monkeypatched directly, deliberately sidestepping unrelated governance
    bug #4 (that env var's inconsistent truthiness handling) rather than
    depending on or triggering it.
    """
    monkeypatch.setattr(
        memory_store_module._rules_engine,
        "evaluate",
        _fixed_evaluate(
            summary_mode="summary_only",
            embed="summary",
            embed_store=False,  # compute-only: populates result.ephemeral_embeddings
        ),
    )
    fake_engine = _FakeEmbedEngine()
    monkeypatch.setattr(
        memory_store_module,
        "_get_embedding_components",
        lambda db_path: (fake_engine, object()),
    )

    full_value = (
        "This sentence must never appear in any embedding for this write. "
        "Filler content follows so the naive summarizer has enough length "
        "to trigger and something to extract a first sentence from, padding "
        "well past the summarization engine's own length threshold with "
        "entirely unrelated words repeated here for bulk. "
    ) * 3
    caller_summary = "Governed distinct summary text unrelated to the padding above."

    result = await store.upsert_memory(
        kind="test_kind",
        key="k7",
        value=full_value,
        ts=TS,
        summary=caller_summary,
    )
    assert result.stored is True

    # Sanity: summary_only did substitute as expected (matches the other
    # summary_only test above) -- the stored value is the governed summary.
    value, summary = _row(store, result.memory_id)
    assert value == caller_summary
    assert summary is None

    assert fake_engine.embedded_texts, "test precondition not met -- embed_texts() never called"
    assert fake_engine.embedded_texts == [caller_summary]
    for text in fake_engine.embedded_texts:
        assert "must never appear" not in text
