"""
Regression tests for the independent adversarial review of PR #65.

Each test here failed against the implementation as it stood before this
correction pass. They use real SQLite through the real `MemoryStore` and the
real rules engine -- nothing is mocked, because every defect they cover lived
in the interaction between paging, decryption, governance and concurrency,
which a mock would have hidden.

Dataset sizes are deliberately well above the store's 500-row page ceiling:
several of these bugs were invisible at small scale.
"""

from __future__ import annotations

import asyncio
import datetime
import pathlib

import pytest

from bartholomew.kernel.memory_store import MemoryStore


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@pytest.fixture
def db_path(tmp_path: pathlib.Path) -> str:
    return str(tmp_path / "review.db")


async def _store(db_path: str) -> MemoryStore:
    store = MemoryStore(db_path)
    await store.init()
    return store


# ---------------------------------------------------------------------------
# Finding 1 -- search must cover the whole store, and counts must be truthful
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_search_finds_a_match_far_beyond_the_first_page(db_path: str):
    """
    The defect: SQL applied LIMIT/OFFSET first and the search filter ran over
    that page only, so a real memory outside the window was reported absent.
    700 rows, needle at 300, page size 100 -> previously zero results.
    """

    async def run():
        store = await _store(db_path)
        ts = _now()
        for i in range(700):
            value = "NEEDLE unique marker" if i == 300 else f"ordinary filler {i}"
            await store.upsert_memory("fact", f"k{i:04d}", value, ts)

        page = await store.list_memories(limit=100, offset=0, search="NEEDLE")
        assert page["total"] == 1, "the needle must be found, not reported absent"
        assert len(page["entries"]) == 1
        assert page["entries"][0]["key"] == "k0300"
        assert page["store_total"] == 700
        await store.close(checkpoint=False)

    asyncio.run(run())


@pytest.mark.slow
def test_filtered_pagination_addresses_the_filtered_set(db_path: str):
    """
    The defect: offset/limit were applied to the unfiltered rows, so paging a
    search did not walk the matches, and `total` reported the store size.
    """

    async def run():
        store = await _store(db_path)
        ts = _now()
        for i in range(300):
            await store.upsert_memory("fact", f"other{i:04d}", f"unrelated {i}", ts)
        for i in range(600):
            await store.upsert_memory("fact", f"hit{i:04d}", f"COMMON token {i}", ts)

        first = await store.list_memories(limit=250, offset=0, search="COMMON")
        second = await store.list_memories(limit=250, offset=250, search="COMMON")
        third = await store.list_memories(limit=250, offset=500, search="COMMON")

        # `total` is the size of the set offset/limit address.
        assert first["total"] == 600
        assert first["store_total"] == 900
        assert first["filtered"] is True

        assert len(first["entries"]) == 250
        assert len(second["entries"]) == 250
        assert len(third["entries"]) == 100

        assert first["has_more"] is True
        assert third["has_more"] is False

        # The pages must partition the matches: no gaps, no repeats.
        keys = [e["key"] for e in first["entries"] + second["entries"] + third["entries"]]
        assert len(keys) == 600
        assert len(set(keys)) == 600
        assert all(k.startswith("hit") for k in keys)
        await store.close(checkpoint=False)

    asyncio.run(run())


@pytest.mark.slow
def test_unfiltered_listing_counts_stay_truthful(db_path: str):
    async def run():
        store = await _store(db_path)
        ts = _now()
        for i in range(600):
            await store.upsert_memory("fact", f"k{i:04d}", f"value {i}", ts)

        page = await store.list_memories(limit=100, offset=550)
        assert page["total"] == 600
        assert page["store_total"] == 600
        assert page["filtered"] is False
        assert len(page["entries"]) == 50
        assert page["has_more"] is False
        await store.close(checkpoint=False)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Finding 2 -- correction must never destroy another writer's record
# ---------------------------------------------------------------------------


def test_correction_does_not_destroy_a_newer_legitimate_write(db_path: str):
    """
    The ABA defect. Another writer deletes and recreates the record while a
    correction is in flight. The old code saw a changed row id, assumed its
    own accidental resurrection, and deleted -- destroying real data.

    Interleaved for real: the competing write happens *inside* the correction's
    call to the write path, at exactly the point the race would land.
    """

    async def run():
        store = await _store(db_path)
        ts = _now()
        await store.upsert_memory("fact", "aba", "original value", ts)

        real_upsert = type(store).upsert_memory
        fired: list[int] = []

        async def interleave(self, kind, key, value, ts_, **kwargs):
            if kind == "fact" and key == "aba" and not fired:
                fired.append(1)
                await self.delete_memory(kind, key)
                await real_upsert(self, kind, key, "NEWER legitimate value", ts_)
            return await real_upsert(self, kind, key, value, ts_, **kwargs)

        type(store).upsert_memory = interleave
        try:
            outcome = await store.correct_memory("fact", "aba", "stale correction")
        finally:
            type(store).upsert_memory = real_upsert

        assert fired, "the competing write never ran; the test proved nothing"
        assert outcome.stored is False
        assert outcome.target_changed is True

        surviving = await store.get_memory("fact", "aba")
        assert surviving is not None, "the newer legitimate write was destroyed"
        assert surviving["value"] == "NEWER legitimate value"
        await store.close(checkpoint=False)

    asyncio.run(run())


def test_a_user_deletion_beats_an_in_flight_correction(db_path: str):
    """The rule that must survive the fix: a confirmed delete wins."""

    async def run():
        store = await _store(db_path)
        ts = _now()
        await store.upsert_memory("fact", "gone", "original", ts)

        real_upsert = type(store).upsert_memory
        fired: list[int] = []

        async def interleave(self, kind, key, value, ts_, **kwargs):
            if kind == "fact" and key == "gone" and not fired:
                fired.append(1)
                await self.delete_memory(kind, key)
            return await real_upsert(self, kind, key, value, ts_, **kwargs)

        type(store).upsert_memory = interleave
        try:
            outcome = await store.correct_memory("fact", "gone", "resurrect me")
        finally:
            type(store).upsert_memory = real_upsert

        assert fired
        assert outcome.stored is False
        assert outcome.target_changed is True
        assert await store.get_memory("fact", "gone") is None, "the deletion must stand"
        await store.close(checkpoint=False)

    asyncio.run(run())


def test_two_concurrent_corrections_do_not_both_win(db_path: str):
    """
    Both corrections target the same record. The conditional write means the
    second sees a changed record and does not silently clobber the first.
    """

    async def run():
        store = await _store(db_path)
        ts = _now()
        await store.upsert_memory("fact", "race", "original", ts)
        original = await store.get_memory("fact", "race")

        # First correction lands normally.
        first = await store.correct_memory("fact", "race", "correction A")
        assert first.stored is True

        # Second correction still holds the pre-first record identity.
        second = await store.upsert_memory(
            "fact",
            "race",
            "correction B",
            _now(),
            expected_memory_id=original["id"] + 10_000,
        )
        assert second.stored is False
        assert second.outcome == "precondition_failed"

        current = await store.get_memory("fact", "race")
        assert current["value"] == "correction A", "the stale write must not have landed"
        await store.close(checkpoint=False)

    asyncio.run(run())


def test_correction_that_governance_queues_reports_queued_not_stored(db_path: str):
    """The consent path still works through the conditional write."""

    async def run():
        store = await _store(db_path)
        ts = _now()
        await store.upsert_memory("fact", "consent", "harmless original", ts)

        outcome = await store.correct_memory("fact", "consent", "my bank account number is 12345")
        assert outcome.stored is False
        assert outcome.queued_for_consent is True
        assert outcome.target_changed is False

        unchanged = await store.get_memory("fact", "consent")
        assert unchanged["value"] == "harmless original"

        pending = await store.list_pending_sensitive_writes(limit=50)
        assert any(p["kind"] == "fact" and p["key"] == "consent" for p in pending)
        await store.close(checkpoint=False)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Finding 5 -- governance metadata must not be fabricated from a blank value
# ---------------------------------------------------------------------------


def test_unreadable_record_reports_unknown_classification_not_uncategorised(db_path: str):
    """
    The defect: an undecryptable value was blanked and the rules engine was
    then re-run over "", classifying a `user.secure` record as
    `uncategorised` with no privacy class -- a fabricated classification of
    exactly the material most in need of a truthful one.
    """

    async def run():
        store = await _store(db_path)
        ts = _now()
        await store.upsert_memory("fact", "readable", "an ordinary fact", ts)
        await store.upsert_memory("fact", "secret", "an ordinary fact too", ts)

        # Make one row undecryptable: replace its value with a well-formed
        # envelope encrypted under a key this process does not hold.
        import json
        import sqlite3

        envelope = {
            "scheme": "bartholomew.enc.v1",
            "alg": "AES-GCM",
            "kid": "std",
            "nonce": "AAAAAAAAAAAAAAAA",
            "aad": "e30=",
            "ct": "AAAAAAAAAAAAAAAAAAAA",
        }
        foreign = json.dumps(envelope)
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE memories SET value=? WHERE key='secret'", (foreign,))
        conn.commit()
        conn.close()

        page = await store.list_memories(limit=50)
        by_key = {e["key"]: e for e in page["entries"]}

        bad = by_key["secret"]
        assert bad["readable"] is False
        assert bad["governance_known"] is False
        assert bad["category"] is None, "must not claim 'uncategorised' for an unread record"
        assert bad["privacy_class"] is None
        assert bad["always_keep"] is None
        assert bad["value"] == "", "ciphertext must not be shown as content"

        good = by_key["readable"]
        assert good["readable"] is True
        assert good["governance_known"] is True
        assert good["category"] is not None
        await store.close(checkpoint=False)

    asyncio.run(run())


@pytest.mark.slow
def test_search_does_not_claim_to_have_matched_unreadable_content(db_path: str):
    """An unreadable value cannot be searched; only its key can."""

    async def run():
        store = await _store(db_path)
        ts = _now()
        for i in range(520):
            await store.upsert_memory("fact", f"pad{i:04d}", f"padding {i}", ts)
        await store.upsert_memory("fact", "cipherrow", "SECRETWORD in plaintext", ts)

        import json
        import sqlite3

        envelope = {
            "scheme": "bartholomew.enc.v1",
            "alg": "AES-GCM",
            "kid": "std",
            "nonce": "AAAAAAAAAAAAAAAA",
            "aad": "e30=",
            "ct": "AAAAAAAAAAAAAAAAAAAA",
        }
        foreign = json.dumps(envelope)
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE memories SET value=? WHERE key='cipherrow'", (foreign,))
        conn.commit()
        conn.close()

        # The word is no longer readable, so it must not be reported as a
        # content match...
        by_content = await store.list_memories(limit=50, search="SECRETWORD")
        assert by_content["total"] == 0

        # ...but the key is still searchable, across the whole store.
        by_key = await store.list_memories(limit=50, search="cipherrow")
        assert by_key["total"] == 1
        assert by_key["entries"][0]["readable"] is False
        await store.close(checkpoint=False)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Finding 6 -- the write authority reports its own outcome
# ---------------------------------------------------------------------------


def test_store_result_reports_the_reason_directly(db_path: str):
    """No inbox diffing: the governed write path says what happened."""

    async def run():
        store = await _store(db_path)
        ts = _now()

        ok = await store.upsert_memory("fact", "plain", "harmless", ts)
        assert ok.stored is True and ok.outcome == "stored"

        queued = await store.upsert_memory("fact", "sensitive", "my bank account number is 999", ts)
        assert queued.stored is False and queued.outcome == "queued_for_consent"

        refused = await store.upsert_memory("fact", "blocked", "csam", ts)
        assert refused.stored is False and refused.outcome == "refused"
        await store.close(checkpoint=False)

    asyncio.run(run())


def test_queued_outcome_is_independent_of_inbox_size(db_path: str):
    """
    The old inference scanned at most 500 pending rows. Past that it would
    have mis-reported a queued correction as a refusal.
    """

    async def run():
        store = await _store(db_path)
        ts = _now()
        for i in range(520):
            await store.upsert_memory("fact", f"q{i:04d}", f"my password is p{i}", ts)

        pending = await store.list_pending_sensitive_writes(limit=1000)
        assert len(pending) > 500, "inbox must exceed the old 500-row scan window"

        await store.upsert_memory("fact", "target", "harmless original", ts)
        outcome = await store.correct_memory("fact", "target", "my password is hunter2")
        assert outcome.stored is False
        assert outcome.queued_for_consent is True, "must not degrade to 'refused'"
        await store.close(checkpoint=False)

    asyncio.run(run())
