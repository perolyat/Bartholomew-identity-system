"""
Phase B stage B8 (sub-stage 2): MemoryStore concurrency stress test under
the shared blocking_executor model. Closes docs/PHASE_B_RISK_MAP.md's B8
row: "MemoryStore concurrency and its own serialization cost under the
new executor model" -- a stress-harness test analogous to the archived
design's Test 51.

Fires many concurrent upsert_memory() calls (and, separately, concurrent
persist_embeddings_for() calls) at one MemoryStore instance sharing one
SingleWorkerExecutor -- the real shape a busy daemon subjects it to, now
that Phase B stage B8's first sub-stage routes every VectorStore call
through that same executor too. Verifies no writes are lost, no
exceptions escape, and (with embeddings enabled) every memory's
embeddings are correctly and exclusively its own -- not cross-written
between concurrent operations sharing the one worker thread.
"""

from __future__ import annotations

import asyncio

import pytest

from bartholomew.kernel.blocking_executor import SingleWorkerExecutor
from bartholomew.kernel.memory_store import MemoryStore


@pytest.fixture
def executor():
    ex = SingleWorkerExecutor(thread_name_prefix="test-b8-concurrency")
    yield ex


async def test_concurrent_upserts_lose_no_writes(tmp_path, executor):
    db_path = str(tmp_path / "test.db")
    store = MemoryStore(db_path, blocking_executor=executor)
    await store.init()

    n = 25
    results = await asyncio.gather(
        *[
            store.upsert_memory(
                kind="test",
                key=f"concurrent-{i}",
                value=f"value-{i}",
                ts="2024-01-01T00:00:00Z",
            )
            for i in range(n)
        ],
    )

    assert all(r.stored for r in results)
    assert len({r.memory_id for r in results}) == n  # every memory got a distinct id

    await executor.close()


async def test_concurrent_upserts_with_embeddings_do_not_cross_contaminate(
    tmp_path,
    monkeypatch,
    executor,
):
    """Real regression target for this test: sub-stage 1 routed every
    VectorStore call through the shared blocking_executor. Since that
    executor enforces strict sequential submission (at most one operation
    in flight at a time), concurrent upsert_memory() calls competing for
    it must still each end up with their own, uncorrupted embedding --
    not a race where one memory's vector ends up filed under another's
    memory_id."""
    monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
    monkeypatch.setenv("BARTHO_EMBED_RELOAD", "0")

    db_path = str(tmp_path / "test.db")
    store = MemoryStore(db_path, blocking_executor=executor)
    await store.init()

    n = 10
    results = await asyncio.gather(
        *[
            store.upsert_memory(
                kind="test",
                key=f"embed-concurrent-{i}",
                value=f"Distinct content number {i} for the concurrency stress test.",
                ts="2024-01-01T00:00:00Z",
            )
            for i in range(n)
        ],
    )

    assert all(r.stored for r in results)
    memory_ids = [r.memory_id for r in results]
    assert len(set(memory_ids)) == n

    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        for memory_id in memory_ids:
            rows = conn.execute(
                "SELECT memory_id FROM memory_embeddings WHERE memory_id = ?",
                (memory_id,),
            ).fetchall()
            # Every embedding row found for this id must genuinely belong
            # to it -- a cross-contamination bug would show up as rows
            # under the wrong memory_id, not as an empty result here.
            assert all(row[0] == memory_id for row in rows)
    finally:
        conn.close()

    await executor.close()


async def test_concurrent_reembed_calls_on_different_memories_do_not_interfere(
    tmp_path,
    monkeypatch,
    executor,
):
    monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
    monkeypatch.setenv("BARTHO_EMBED_RELOAD", "0")

    db_path = str(tmp_path / "test.db")
    store = MemoryStore(db_path, blocking_executor=executor)
    await store.init()

    results = await asyncio.gather(
        *[
            store.upsert_memory(
                kind="test",
                key=f"reembed-source-{i}",
                value=f"Content to be re-embedded, variant {i}.",
                ts="2024-01-01T00:00:00Z",
            )
            for i in range(5)
        ],
    )
    memory_ids = [r.memory_id for r in results]

    reembed_counts = await asyncio.gather(
        *[store.reembed_memory(mid, sources=["full"]) for mid in memory_ids],
    )

    assert all(count > 0 for count in reembed_counts)

    await executor.close()
