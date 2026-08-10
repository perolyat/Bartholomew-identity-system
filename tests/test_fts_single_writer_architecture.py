"""
Regression/invariant tests for the single-writer memory_fts architecture
(see FTS5_SINGLE_WRITER_ARCHITECTURE_ASSESSMENT.md's Sec.11).

Covers the specific failure modes the trigger-plus-manual-override design
could not survive -- repeated updates to a memory with a non-NULL summary
corrupting the database being the headline one -- plus the invariants the
new architecture (no memory_fts triggers; memory_fts_map.last_index_text as
the sole authoritative record of what's indexed; one shared primitive
every writer calls) is required to hold.
"""

import os
import sqlite3
import tempfile

import aiosqlite
import pytest

from bartholomew.kernel import memory_store
from bartholomew.kernel.fts_client import FTSClient
from bartholomew.kernel.memory_store import MemoryStore, compute_governed_index_text


def _integrity_ok(db_path: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row is not None and row[0] == "ok"
    finally:
        conn.close()


def _match_ids(fts: FTSClient, query: str) -> set[int]:
    results = fts.search(query, limit=50, apply_consent_gate=False)
    return {r["id"] for r in results}


@pytest.fixture
async def store_and_db():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = MemoryStore(db_path)
    await store.init()
    try:
        yield store, db_path
    finally:
        await store.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 1. Insert, then repeated updates, with a non-NULL summary throughout --
#    the exact condition that crashed every trigger-based variant.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_repeated_updates_with_summary_no_corruption(store_and_db):
    store, db_path = store_and_db
    fts = FTSClient(db_path)

    generations = [
        ("value about zeppelins", "summary mentions zeppelinsalpha"),
        ("value about narwhals", "summary mentions narwhalsbeta"),
        ("value about kumquats", "summary mentions kumquatsgamma"),
    ]

    for value, summary in generations:
        result = await store.upsert_memory(
            "fact",
            "repeated_update_target",
            value,
            "2025-01-01T00:00:00Z",
            summary=summary,
        )
        assert result.stored is True
        assert _integrity_ok(db_path), "database corrupted after write"

    memory_id = result.memory_id

    # Only the final generation's summary term (summary_preferred mode
    # picks the summary as index_text) is MATCH-able.
    assert _match_ids(fts, "kumquatsgamma") == {memory_id}
    assert _match_ids(fts, "zeppelinsalpha") == set()
    assert _match_ids(fts, "narwhalsbeta") == set()


# ---------------------------------------------------------------------------
# 2. Replaced/stale tokens cease to MATCH (value-only, no summary).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stale_tokens_not_matchable(store_and_db):
    store, db_path = store_and_db
    fts = FTSClient(db_path)

    result1 = await store.upsert_memory(
        "fact",
        "stale_token_target",
        "the first distinctive term is quixoticorange",
        "2025-01-01T00:00:00Z",
    )
    memory_id = result1.memory_id
    assert _match_ids(fts, "quixoticorange") == {memory_id}

    result2 = await store.upsert_memory(
        "fact",
        "stale_token_target",
        "the second distinctive term is mercurialviolet",
        "2025-01-01T01:00:00Z",
    )
    assert result2.memory_id == memory_id
    assert _match_ids(fts, "mercurialviolet") == {memory_id}
    assert _match_ids(fts, "quixoticorange") == set()


# ---------------------------------------------------------------------------
# 3. Only the currently governed representation MATCHes -- the raw stored
#    memories.value is never MATCH-able when summary_preferred selects the
#    summary instead.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_raw_value_not_matchable_when_summary_preferred(store_and_db):
    store, db_path = store_and_db
    fts = FTSClient(db_path)

    result = await store.upsert_memory(
        "fact",
        "summary_preferred_target",
        "raw value contains rawonlytermxyz never meant for the index",
        "2025-01-01T00:00:00Z",
        summary="summary contains summaryonlytermxyz",
    )
    memory_id = result.memory_id

    assert _match_ids(fts, "summaryonlytermxyz") == {memory_id}
    assert _match_ids(fts, "rawonlytermxyz") == set()


# ---------------------------------------------------------------------------
# 5. delete_memory()'s explicit FTS step removes retrieval.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_memory_removes_from_fts(store_and_db):
    store, db_path = store_and_db
    fts = FTSClient(db_path)

    result = await store.upsert_memory(
        "fact",
        "delete_target",
        "content with distinctive term deletionmarkerxyz",
        "2025-01-01T00:00:00Z",
    )
    memory_id = result.memory_id
    assert _match_ids(fts, "deletionmarkerxyz") == {memory_id}

    deleted = await store.delete_memory("fact", "delete_target")
    assert deleted is True
    assert _match_ids(fts, "deletionmarkerxyz") == set()
    assert _integrity_ok(db_path)

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM memory_fts_map WHERE memory_id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == 0


# ---------------------------------------------------------------------------
# 6. A write bypassing MemoryStore degrades safely and is healed by the
#    next MemoryStore.init().
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bypass_write_degrades_safely_and_self_heals(store_and_db):
    store, db_path = store_and_db
    fts = FTSClient(db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO memories(kind, key, value, summary, ts) VALUES (?, ?, ?, ?, ?)",
            (
                "fact",
                "bypass_target",
                "content with term bypasstermxyz",
                None,
                "2025-01-01T00:00:00Z",
            ),
        )
        conn.commit()
        memory_id = conn.execute(
            "SELECT id FROM memories WHERE kind=? AND key=?",
            ("fact", "bypass_target"),
        ).fetchone()[0]
    finally:
        conn.close()

    assert _integrity_ok(db_path)
    assert _match_ids(fts, "bypasstermxyz") == set()

    # Re-running init() (idempotent) runs the self-heal pass.
    await store.init()

    assert _match_ids(fts, "bypasstermxyz") == {memory_id}
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT last_index_text FROM memory_fts_map WHERE memory_id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "content with term bypasstermxyz"
    assert _integrity_ok(db_path)


# ---------------------------------------------------------------------------
# 6b. Self-heal must not defeat governance: a policy-denied memory has no
#     memory_fts_map row by design (not a bug), so init()'s self-heal must
#     never mistake that for a bypass write and index it anyway. Found
#     empirically while wiring self-heal up to init(): FTSClient.migrate_
#     schema()'s rowid-consistency check can't tell the two cases apart,
#     and its rebuild_index() repair is a raw, ungoverned mirror -- calling
#     it automatically on every init() would re-index a policy-denied
#     memory's raw content the moment the app restarts. init() no longer
#     calls migrate_schema() automatically (only the governance-aware heal
#     below); this pins that behavior down.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_self_heal_does_not_reindex_policy_denied_memory(store_and_db):
    store, db_path = store_and_db
    fts = FTSClient(db_path)

    original_evaluate = memory_store._rules_engine.evaluate

    def deny_one_key(memory_dict):
        evaluated = original_evaluate(memory_dict)
        if memory_dict.get("key") == "persistently_denied":
            evaluated["fts_index"] = False
        return evaluated

    memory_store._rules_engine.evaluate = deny_one_key
    try:
        await store.upsert_memory(
            "fact",
            "persistently_denied",
            "denied content term shouldneverbematchable",
            "2025-01-01T00:00:00Z",
        )
        assert _match_ids(fts, "shouldneverbematchable") == set()

        # Repeated restarts must not resurrect it into the index.
        await store.init()
        await store.init()
        await store.init()

        assert _match_ids(fts, "shouldneverbematchable") == set()
    finally:
        memory_store._rules_engine.evaluate = original_evaluate

    assert _integrity_ok(db_path)


# ---------------------------------------------------------------------------
# 7. last_index_text bookkeeping round-trips correctly, including the
#    pre-migration NULL case.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_last_index_text_round_trip(store_and_db):
    store, db_path = store_and_db

    result = await store.upsert_memory(
        "fact",
        "tracking_target",
        "first generation content trackingalpha",
        "2025-01-01T00:00:00Z",
    )
    memory_id = result.memory_id

    async def get_last_index_text():
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT last_index_text FROM memory_fts_map WHERE memory_id = ?",
                (memory_id,),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    assert await get_last_index_text() == "first generation content trackingalpha"

    await store.upsert_memory(
        "fact",
        "tracking_target",
        "second generation content trackingbeta",
        "2025-01-01T01:00:00Z",
    )
    assert await get_last_index_text() == "second generation content trackingbeta"

    # Policy-denied update removes the memory_fts_map row entirely.
    original_evaluate = memory_store._rules_engine.evaluate

    def deny_fts(memory_dict):
        evaluated = original_evaluate(memory_dict)
        evaluated["fts_index"] = False
        return evaluated

    memory_store._rules_engine.evaluate = deny_fts
    try:
        await store.upsert_memory(
            "fact",
            "tracking_target",
            "third generation content trackinggamma",
            "2025-01-01T02:00:00Z",
        )
    finally:
        memory_store._rules_engine.evaluate = original_evaluate

    assert await get_last_index_text() is None

    # Pre-migration NULL last_index_text: a row present in memory_fts_map
    # with no tracked text (simulating a database migrated before this
    # column existed, or one healed from a bypass write mid-flight) must
    # not crash on the next write's delete step (NULL matches zero rows).
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO memory_fts_map(memory_id, last_index_text) VALUES (?, NULL)",
            (memory_id,),
        )
        await db.commit()

    await store.upsert_memory(
        "fact",
        "tracking_target",
        "fourth generation content trackingdelta",
        "2025-01-01T03:00:00Z",
    )
    assert await get_last_index_text() == "fourth generation content trackingdelta"
    assert _integrity_ok(db_path)


# ---------------------------------------------------------------------------
# 8. A failure inside the FTS write step rolls back the whole transaction
#    (same-transaction atomicity).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fts_failure_rolls_back_whole_transaction(store_and_db, monkeypatch):
    store, db_path = store_and_db

    async def boom(db, memory_id, index_text):
        raise RuntimeError("simulated FTS failure")

    monkeypatch.setattr(memory_store, "reindex_memory_fts_async", boom)

    with pytest.raises(RuntimeError):
        await store.upsert_memory(
            "fact",
            "rollback_target",
            "content that must never be persisted",
            "2025-01-01T00:00:00Z",
        )

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM memories WHERE kind=? AND key=?",
            ("fact", "rollback_target"),
        )
        row = await cursor.fetchone()
        assert row[0] == 0, "base row persisted despite FTS write failure"

    assert _integrity_ok(db_path)


# ---------------------------------------------------------------------------
# 11. Backfill orchestration produces a clean, fully-tracked rebuild.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backfill_orchestration_clean_tracked_rebuild(store_and_db):
    store, db_path = store_and_db

    await store.upsert_memory(
        "fact",
        "backfill_one",
        "backfill content term backfillalpha",
        "2025-01-01T00:00:00Z",
    )
    await store.upsert_memory(
        "fact",
        "backfill_two",
        "backfill raw value backfillrawterm",
        "2025-01-01T00:01:00Z",
        summary="backfill summary term backfillsummaryterm",
    )

    # Corrupt the index state so the rebuild has something real to fix:
    # a stale last_index_text that no longer matches what would be
    # (re)computed, and an FTS entry for content that shouldn't exist.
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE memory_fts_map SET last_index_text = 'stale text' WHERE memory_id = "
            "(SELECT id FROM memories WHERE key = 'backfill_one')",
        )
        await db.commit()

    from scripts.backfill_fts import backfill_fts

    exit_code = backfill_fts(db_path, batch_size=10, optimize=False, dry_run=False)
    assert exit_code == 0

    fts = FTSClient(db_path)
    assert _match_ids(fts, "backfillalpha") != set()
    assert _match_ids(fts, "backfillsummaryterm") != set()
    assert _match_ids(fts, "backfillrawterm") == set()

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM memories")
        total_memories = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM memory_fts_map WHERE last_index_text IS NOT NULL",
        )
        tracked = (await cursor.fetchone())[0]
        assert tracked == total_memories

    assert _integrity_ok(db_path)


# ---------------------------------------------------------------------------
# compute_governed_index_text() itself: the shared pure function backing
# both self-heal and backfill_fts.py.
# ---------------------------------------------------------------------------
def test_compute_governed_index_text_summary_preferred():
    text = compute_governed_index_text(
        "fact",
        "some_key",
        "raw value content",
        "raw summary content",
        "2025-01-01T00:00:00Z",
    )
    assert text == "raw summary content"


def test_compute_governed_index_text_no_summary_falls_back_to_value():
    text = compute_governed_index_text(
        "fact",
        "some_key",
        "raw value content",
        None,
        "2025-01-01T00:00:00Z",
    )
    assert text == "raw value content"


def test_compute_governed_index_text_denied_returns_none(monkeypatch):
    original_evaluate = memory_store._rules_engine.evaluate

    def deny_fts(memory_dict):
        evaluated = original_evaluate(memory_dict)
        evaluated["fts_index"] = False
        return evaluated

    monkeypatch.setattr(memory_store._rules_engine, "evaluate", deny_fts)

    text = compute_governed_index_text(
        "fact",
        "some_key",
        "raw value content",
        None,
        "2025-01-01T00:00:00Z",
    )
    assert text is None


def test_compute_governed_index_text_empty_returns_none():
    text = compute_governed_index_text(
        "fact",
        "some_key",
        "   ",
        None,
        "2025-01-01T00:00:00Z",
    )
    assert text is None


# ---------------------------------------------------------------------------
# Governance invariant: no supported FTS repair/rebuild/migration operation
# may silently make content searchable when current governance says it
# should not be. Pins down the fix to the foot-gun found in review: calling
# FTSClient.migrate_schema() directly (its own public API, independent of
# MemoryStore) used to repair any "memory has no memory_fts_map entry"
# mismatch via rebuild_index() -- a raw, ungoverned mirror -- which cannot
# tell a genuine bypass write apart from content policy correctly excludes.
# migrate_schema() now fails closed: it reports the mismatch and repairs
# only the governance-neutral half (orphaned tracking rows), never calling
# rebuild_index() itself at all.
# ---------------------------------------------------------------------------
def test_migrate_schema_never_reindexes_policy_denied_content(monkeypatch):
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    async def setup():
        store = MemoryStore(db_path)
        await store.init()

        original_evaluate = memory_store._rules_engine.evaluate

        def deny_one_key(memory_dict):
            evaluated = original_evaluate(memory_dict)
            if memory_dict.get("key") == "denied_via_migrate_schema":
                evaluated["fts_index"] = False
            return evaluated

        memory_store._rules_engine.evaluate = deny_one_key
        try:
            await store.upsert_memory(
                "fact",
                "denied_via_migrate_schema",
                "denied content term migrateschematermxyz",
                "2025-01-01T00:00:00Z",
            )
        finally:
            memory_store._rules_engine.evaluate = original_evaluate
        await store.close()

    import asyncio

    asyncio.run(setup())

    try:
        fts = FTSClient(db_path)
        assert _match_ids(fts, "migrateschematermxyz") == set()

        # Calling migrate_schema() directly, repeatedly, on FTSClient's own
        # public API (no MemoryStore governance heal involved at all) must
        # never resurrect the denied memory into the index.
        fts.migrate_schema()
        fts.migrate_schema()
        fts.migrate_schema()

        assert _match_ids(fts, "migrateschematermxyz") == set()
        assert _integrity_ok(db_path)
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_migrate_schema_still_repairs_orphaned_entries_only():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    async def setup():
        store = MemoryStore(db_path)
        await store.init()
        await store.upsert_memory(
            "fact",
            "legit_memory",
            "legitimate content orphantestxyz",
            "2025-01-01T00:00:00Z",
        )
        await store.close()

    import asyncio

    asyncio.run(setup())

    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("INSERT INTO memory_fts_map(memory_id) VALUES (99999)")
            conn.commit()
            orphaned_before = conn.execute(
                "SELECT COUNT(*) FROM memory_fts_map WHERE memory_id = 99999",
            ).fetchone()[0]
        finally:
            conn.close()
        assert orphaned_before == 1

        fts = FTSClient(db_path)
        fts.migrate_schema()

        conn = sqlite3.connect(db_path)
        try:
            orphaned_after = conn.execute(
                "SELECT COUNT(*) FROM memory_fts_map WHERE memory_id = 99999",
            ).fetchone()[0]
        finally:
            conn.close()
        assert orphaned_after == 0

        # Legitimate, correctly-tracked content is untouched.
        assert _match_ids(fts, "orphantestxyz") != set()
        assert _integrity_ok(db_path)
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_rebuild_index_never_called_automatically(store_and_db, monkeypatch):
    store, db_path = store_and_db

    def boom(self):
        raise AssertionError(
            "rebuild_index() must never be invoked automatically by any "
            "repair/migration path -- it is raw and ungoverned",
        )

    monkeypatch.setattr(FTSClient, "rebuild_index", boom)

    original_evaluate = memory_store._rules_engine.evaluate

    def deny_one_key(memory_dict):
        evaluated = original_evaluate(memory_dict)
        if memory_dict.get("key") == "denied_key":
            evaluated["fts_index"] = False
        return evaluated

    memory_store._rules_engine.evaluate = deny_one_key
    try:
        await store.upsert_memory(
            "fact",
            "normal_key",
            "normal content rebuildguardxyz",
            "2025-01-01T00:00:00Z",
        )
        await store.upsert_memory(
            "fact",
            "denied_key",
            "denied content rebuildguardxyz2",
            "2025-01-01T00:01:00Z",
        )
    finally:
        memory_store._rules_engine.evaluate = original_evaluate

    # A bypass write too, so both mismatch kinds migrate_schema() checks
    # for are present when init() runs its safety net below.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO memories(kind, key, value, summary, ts) VALUES (?, ?, ?, ?, ?)",
            ("fact", "bypass_key", "bypass content rebuildguardxyz3", None, "2025-01-01T00:02:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    # Would raise via the monkeypatch above if rebuild_index() were ever
    # reached, whether through init_schema()'s own auto_heal, the
    # MemoryStore self-heal safety net, or migrate_schema() itself.
    await store.init()

    fts = FTSClient(db_path)
    fts.migrate_schema()
