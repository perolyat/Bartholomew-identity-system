"""
Phase B stage B9: concurrent CLI-vs-daemon adversarial validation against
the integrated B3/B5/B6 result (GovernanceStore's revision guard, the
write fence, and ProcessLock together). Uses real OS threads (not
sequential calls) so ProcessLock's fcntl.flock/msvcrt.locking exclusion
and GovernanceStore's BEGIN IMMEDIATE transaction locking are exercised
under genuine concurrency -- the same cross-process shape a real CLI
invocation racing a running daemon has, since both primitives operate at
the OS/file-descriptor level where threads and processes behave
identically. See docs/B9_RECOVERY_ROLLBACK_ADVERSARIAL_VALIDATION.md.
"""

from __future__ import annotations

import threading
import time

import pytest

from bartholomew.kernel.process_lock import ProcessLock, ProcessLockHeldError
from bartholomew.orchestrator.safety.governance_store import (
    GovernanceStore,
    StaleGovernanceWriteError,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


def test_concurrent_process_lock_attempts_from_real_threads_exactly_one_wins(db_path):
    """20 threads race to acquire the same ProcessLock; exactly one must
    succeed, the rest must fail with ProcessLockHeldError -- no double
    grant, no silent corruption of the lock's own bookkeeping."""
    n = 20
    barrier = threading.Barrier(n)
    results: list[bool] = [False] * n
    errors: list[Exception | None] = [None] * n

    def attempt(i: int) -> None:
        lock = ProcessLock(db_path)
        barrier.wait()  # maximize actual concurrent contention
        try:
            lock.acquire()
            results[i] = True
            time.sleep(0.05)
            lock.release()
        except ProcessLockHeldError as e:
            errors[i] = e

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    succeeded = sum(results)
    failed = sum(1 for e in errors if e is not None)
    assert succeeded == 1
    assert failed == n - 1


def test_concurrent_governance_engage_calls_from_real_threads_lose_no_writes(db_path):
    """20 threads each independently engage() a distinct scope via their
    own GovernanceStore instance -- BEGIN IMMEDIATE must serialize these
    correctly (no lost update: SQLite either queues or raises, but the
    revision counter must end up incremented exactly once per successful
    write, with the final state reflecting the last writer, not a torn
    or dropped one)."""
    GovernanceStore(db_path)  # ensure schema exists before threads race

    n = 20
    barrier = threading.Barrier(n)
    outcomes: list[str] = [""] * n

    def attempt(i: int) -> None:
        store = GovernanceStore(db_path)
        barrier.wait()
        try:
            store.engage(f"scope-{i}", reason=f"thread-{i}")
            outcomes[i] = "ok"
        except Exception as e:  # sqlite3.OperationalError on lock contention, if any
            outcomes[i] = f"error: {e}"

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # db_ctx.connect()'s busy_timeout gives SQLite room to serialize
    # rather than immediately raising "database is locked" -- every
    # attempt should succeed, not just "the process didn't crash".
    assert outcomes.count("ok") == n, outcomes

    final = GovernanceStore(db_path).state()
    assert final.revision == n  # one successful write per thread, none lost


def test_concurrent_disengage_with_stale_revision_is_refused_not_corrupted(db_path):
    """Two threads both read the current revision, then both attempt
    disengage() with that same expected_revision -- exactly one must
    succeed, the other must be refused with StaleGovernanceWriteError,
    never both silently "succeeding" against a state that only one of
    them actually observed."""
    seed = GovernanceStore(db_path)
    seed.engage("global", reason="seed")
    revision = seed.state().revision

    outcomes: list[str] = [""] * 2
    barrier = threading.Barrier(2)

    def attempt(i: int) -> None:
        store = GovernanceStore(db_path)
        barrier.wait()
        try:
            store.disengage(reason=f"race-{i}", expected_revision=revision)
            outcomes[i] = "ok"
        except StaleGovernanceWriteError:
            outcomes[i] = "stale"

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert sorted(outcomes) == ["ok", "stale"]
