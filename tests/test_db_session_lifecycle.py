"""The bounded SQLite connection scope (`db_ctx.db_session`) and its contract.

Why this file exists. Every persistence helper in the repository takes a
`db_path` and owns a whole connection lifecycle: open, do one small thing,
close. When that connection is the only connection to the database -- which,
between calls, it always is -- closing it is not a cheap handle release.
SQLite checkpoints the entire WAL and unlinks the `-wal` and `-shm` files, and
the next call recreates them. Measured on Linux at about 1.1 ms per close and
1.2 ms per commit against 0.02 ms each when another connection is open; on
Windows the same term was measured at roughly 70 ms per close, which is what
pushed the heavy-burst containment test to 108 s against a 120 s per-test
timeout.

`db_session()` gives a bounded unit of work one connection for its duration.
These tests pin the properties that make that safe, because the alternative
-- a process-wide pool or a permanently open handle -- would break the
handle-release contract that `tests/test_vector_store_handle_lifetime.py` and
`tests/test_sqlite_wal_cleanup.py` exist to defend.
"""

from __future__ import annotations

import gc
import os
import sqlite3
import threading
import time

import pytest

from bartholomew.kernel import db_ctx
from bartholomew.kernel.db_ctx import db_session, in_session, wal_db


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "session.db")
    with wal_db(path) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.commit()
    return path


def _rows(path: str) -> list[tuple]:
    """Read through a connection that is deliberately not the session's."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT id, v FROM t ORDER BY id").fetchall()
    finally:
        conn.close()


def _handles_held_on(path: str) -> list[str]:
    """Which of `path`'s files this process still holds open (Linux only).

    The same probe as tests/test_vector_store_handle_lifetime.py, kept local
    so this file states its own evidence.
    """
    if not os.path.isdir("/proc/self/fd"):
        pytest.skip("handle enumeration needs /proc")
    stem = os.path.basename(path)
    held = []
    for fd in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
        if os.path.basename(target).startswith(stem):
            held.append(os.path.basename(target))
    return sorted(held)


# ---------------------------------------------------------------------------
# 1. Reuse inside the scope
# ---------------------------------------------------------------------------


class TestReuseInsideTheScope:
    def test_wal_db_borrows_the_session_connection(self, db):
        with db_session(db) as bound:
            with wal_db(db) as a, wal_db(db) as b:
                assert a is bound and b is bound

    def test_outside_a_scope_every_call_still_owns_its_connection(self, db):
        with wal_db(db) as a:
            assert not in_session(db)
        with wal_db(db) as b:
            assert b is not a
        for conn in (a, b):
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_a_different_database_is_not_borrowed(self, db, tmp_path):
        other = str(tmp_path / "other.db")
        with db_session(db) as bound:
            with wal_db(other) as conn:
                assert conn is not bound
            assert not in_session(other)

    def test_the_same_file_under_a_different_path_spelling_shares_the_scope(
        self,
        db,
        tmp_path,
    ):
        link = str(tmp_path / "alias.db")
        os.symlink(db, link)
        with db_session(db) as bound:
            with wal_db(link) as conn:
                assert conn is bound

    def test_reuse_does_not_change_what_is_persisted(self, db):
        with db_session(db):
            for i in range(5):
                with wal_db(db) as conn:
                    conn.execute("INSERT INTO t (id, v) VALUES (?, ?)", (i, f"v{i}"))
                    conn.commit()
        assert _rows(db) == [(i, f"v{i}") for i in range(5)]


# ---------------------------------------------------------------------------
# 2. Release when the scope ends
# ---------------------------------------------------------------------------


class TestReleaseAtScopeEnd:
    def test_the_connection_is_closed_when_the_scope_exits(self, db):
        with db_session(db) as conn:
            conn.execute("SELECT 1")
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_the_connection_is_closed_when_the_body_raises(self, db):
        with pytest.raises(RuntimeError):
            with db_session(db) as conn:
                raise RuntimeError("boom")
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_no_file_handle_survives_the_scope(self, db):
        gc.disable()
        try:
            with db_session(db) as conn:
                conn.execute("INSERT INTO t (id, v) VALUES (1, 'x')")
                conn.commit()
                assert _handles_held_on(db), "the scope should hold its handles"
            assert _handles_held_on(db) == [], "a scope must release every handle"
        finally:
            gc.enable()

    def test_the_binding_is_gone_after_the_scope(self, db):
        with db_session(db):
            assert in_session(db)
        assert not in_session(db)
        assert db_ctx.active_session_connection(db) is None

    def test_re_entering_opens_a_new_connection_rather_than_reusing_one(self, db):
        with db_session(db) as first:
            pass
        with db_session(db) as second:
            assert second is not first

    def test_nothing_is_retained_between_scopes(self, db):
        with db_session(db):
            pass
        # No global registry of live connections is left behind.
        assert db_ctx._sessions.scopes == {}


# ---------------------------------------------------------------------------
# 3. Transactions: the scope must not change commit semantics
# ---------------------------------------------------------------------------


class TestTransactionSemanticsAreUnchanged:
    def test_an_uncommitted_write_does_not_survive_the_call(self, db):
        """Exactly what closing an unscoped connection would have done."""
        with db_session(db):
            with wal_db(db) as conn:
                conn.execute("INSERT INTO t (id, v) VALUES (1, 'never')")
                # deliberately no commit
            with wal_db(db) as conn:
                assert not conn.in_transaction
                conn.execute("INSERT INTO t (id, v) VALUES (2, 'kept')")
                conn.commit()
        assert _rows(db) == [(2, "kept")]

    def test_a_failure_inside_the_scope_does_not_leak_into_the_next_call(self, db):
        with db_session(db):
            with pytest.raises(RuntimeError):
                with wal_db(db) as conn:
                    conn.execute("INSERT INTO t (id, v) VALUES (1, 'rolled back')")
                    raise RuntimeError("mid-write failure")
            with wal_db(db) as conn:
                conn.execute("INSERT INTO t (id, v) VALUES (2, 'committed')")
                conn.commit()
        assert _rows(db) == [(2, "committed")], "a rolled-back write was committed later"

    def test_an_uncommitted_write_open_at_scope_exit_is_rolled_back(self, db):
        with db_session(db) as conn:
            conn.execute("INSERT INTO t (id, v) VALUES (1, 'never')")
            assert conn.in_transaction
        assert _rows(db) == []

    def test_a_committed_write_is_visible_to_another_connection_immediately(self, db):
        with db_session(db):
            with wal_db(db) as conn:
                conn.execute("INSERT INTO t (id, v) VALUES (1, 'now')")
                conn.commit()
            assert _rows(db) == [(1, "now")], "WAL readers must see a committed write"

    def test_the_scope_holds_no_transaction_between_operations(self, db):
        with db_session(db) as bound:
            with wal_db(db) as conn:
                conn.execute("INSERT INTO t (id, v) VALUES (1, 'a')")
                conn.commit()
            assert not bound.in_transaction, "an idle scope must hold no lock"


# ---------------------------------------------------------------------------
# 4. Nesting
# ---------------------------------------------------------------------------


class TestNesting:
    def test_an_inner_scope_reuses_the_outer_connection(self, db):
        with db_session(db) as outer:
            with db_session(db) as inner:
                assert inner is outer
            outer.execute("SELECT 1")  # the inner exit must not have closed it

    def test_the_outermost_scope_owns_the_close(self, db):
        with db_session(db) as outer:
            with db_session(db):
                pass
            assert in_session(db)
        assert not in_session(db)
        with pytest.raises(sqlite3.ProgrammingError):
            outer.execute("SELECT 1")

    def test_an_inner_failure_still_releases_the_outer_connection(self, db):
        with pytest.raises(ValueError):
            with db_session(db) as outer:
                with db_session(db):
                    raise ValueError("inner")
        with pytest.raises(sqlite3.ProgrammingError):
            outer.execute("SELECT 1")


# ---------------------------------------------------------------------------
# 5. Thread confinement
# ---------------------------------------------------------------------------


class TestThreadConfinement:
    def test_another_thread_does_not_see_this_thread_s_scope(self, db):
        seen: dict[str, object] = {}

        def probe():
            seen["in_session"] = in_session(db)
            with wal_db(db) as conn:
                seen["conn"] = conn

        with db_session(db) as bound:
            t = threading.Thread(target=probe)
            t.start()
            t.join()
            assert seen["in_session"] is False
            assert seen["conn"] is not bound, "a connection crossed a thread boundary"

    def test_two_threads_hold_independent_scopes_on_one_database(self, db):
        conns: list[object] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker(n: int):
            with db_session(db) as conn:
                with lock:
                    conns.append(conn)
                barrier.wait(timeout=10)
                with wal_db(db) as c:
                    c.execute("INSERT INTO t (id, v) VALUES (?, ?)", (n, f"t{n}"))
                    c.commit()

        threads = [threading.Thread(target=worker, args=(n,)) for n in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            assert not t.is_alive()

        assert len(conns) == 2 and conns[0] is not conns[1]
        assert _rows(db) == [(1, "t1"), (2, "t2")]
        assert not in_session(db), "a worker's scope leaked into the main thread"

    def test_every_thread_releases_its_own_handles(self, db):
        def worker():
            with db_session(db) as conn:
                conn.execute("SELECT 1")

        gc.disable()
        try:
            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=30)
            assert _handles_held_on(db) == []
        finally:
            gc.enable()


# ---------------------------------------------------------------------------
# 6. It is a scope, not a pool
# ---------------------------------------------------------------------------


class TestItIsAScopeNotAPool:
    def test_no_module_level_connection_cache_exists(self):
        """A regression guard: the repair must stay a scope.

        If a future change introduces a module-level dict of live connections
        keyed by path, this is where it should be argued for, not slipped in.
        """
        leftovers = [
            name for name, value in vars(db_ctx).items() if isinstance(value, sqlite3.Connection)
        ]
        assert leftovers == [], f"db_ctx must hold no connection at module level: {leftovers}"

    def test_the_binding_registry_is_thread_local(self):
        assert isinstance(db_ctx._sessions, threading.local)

    def test_a_scope_is_required_no_implicit_reuse_happens(self, db):
        """Two consecutive unscoped calls must not share a connection."""
        seen = []
        with wal_db(db) as a:
            seen.append(a)
        with wal_db(db) as b:
            seen.append(b)
        assert seen[0] is not seen[1]
        for conn in seen:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")


# ---------------------------------------------------------------------------
# 7. The cost this exists to remove
# ---------------------------------------------------------------------------


class TestTheCostIsActuallyRemoved:
    """The property, stated so it holds on any machine: inside a scope the
    per-operation work must not include an open and a close.

    Asserting wall-clock speedup directly would be a flaky benchmark, so the
    mechanism is asserted instead -- one connection object for the whole
    burst, and the WAL file surviving across operations rather than being
    unlinked and recreated by each close. The wall-clock figure is reported
    for the record.
    """

    def test_a_burst_inside_a_scope_opens_exactly_one_connection(self, db, monkeypatch):
        opened = []
        real_connect = db_ctx.connect

        def counting_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            opened.append(conn)
            return conn

        monkeypatch.setattr(db_ctx, "connect", counting_connect)

        with db_session(db):
            for i in range(200):
                with wal_db(db) as conn:
                    conn.execute("INSERT INTO t (id, v) VALUES (?, ?)", (i, "x"))
                    conn.commit()

        assert len(opened) == 1, f"a scoped burst opened {len(opened)} connections"

    def test_the_same_burst_unscoped_opens_one_connection_per_operation(
        self,
        db,
        monkeypatch,
    ):
        """The baseline, so the test above is a comparison and not an assertion
        about nothing."""
        opened = []
        real_connect = db_ctx.connect

        def counting_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            opened.append(conn)
            return conn

        monkeypatch.setattr(db_ctx, "connect", counting_connect)
        for i in range(20):
            with wal_db(db) as conn:
                conn.execute("INSERT INTO t (id, v) VALUES (?, ?)", (i, "x"))
                conn.commit()
        assert len(opened) == 20

    def test_the_wal_file_is_not_unlinked_between_operations_in_a_scope(self, db):
        """The mechanism behind the cost: each last-close checkpoints and
        unlinks the WAL, and the next call recreates it."""
        wal = db + "-wal"
        with db_session(db):
            inodes = set()
            for i in range(5):
                with wal_db(db) as conn:
                    conn.execute("INSERT INTO t (id, v) VALUES (?, ?)", (i, "x"))
                    conn.commit()
                inodes.add(os.stat(wal).st_ino)
            assert len(inodes) == 1, "the WAL was recreated inside a single scope"

    def test_a_scoped_burst_is_materially_faster_than_the_unscoped_one(self, db):
        """Reported, and asserted only at a margin no machine should miss.

        Measured on the Linux developer runner at 4.54 s unscoped against
        0.105 s scoped for the 1000-emission containment burst (43x). The
        assertion here is deliberately loose (2x) so it proves the direction
        without becoming a timing test.
        """
        n = 150

        def run(scoped: bool) -> float:
            start = time.perf_counter()
            if scoped:
                with db_session(db):
                    for _ in range(n):
                        with wal_db(db) as conn:
                            conn.execute("INSERT INTO t (v) VALUES ('x')")
                            conn.commit()
            else:
                for _ in range(n):
                    with wal_db(db) as conn:
                        conn.execute("INSERT INTO t (v) VALUES ('x')")
                        conn.commit()
            return time.perf_counter() - start

        unscoped = run(False)
        scoped = run(True)
        print(f"\nunscoped={unscoped * 1000:.1f}ms scoped={scoped * 1000:.1f}ms")
        assert scoped * 2 < unscoped, f"unscoped={unscoped:.3f}s scoped={scoped:.3f}s"


# ---------------------------------------------------------------------------
# 8. The scheduler's unit of work
# ---------------------------------------------------------------------------


class TestSchedulerStoreUnitOfWork:
    """`SchedulerStore` runs every persistence call on one dedicated worker
    thread, so a scope entered on that thread covers all of them. These pin
    that the scope really is entered there, and really is released."""

    @pytest.mark.asyncio
    async def test_operations_inside_the_scope_share_one_connection(self, tmp_path):
        from bartholomew.kernel.scheduler import persistence as sp
        from bartholomew.kernel.scheduler.store import SchedulerStore

        path = str(tmp_path / "sched.db")
        store = SchedulerStore(path)
        try:
            await store.ensure_schema()
            seen: list[object] = []

            def observe() -> None:
                seen.append(db_ctx.active_session_connection(path))

            async with store.unit_of_work(label="test-tick"):
                await store._call(observe)
                await store.insert_nudge("task", "one", [], "user_task", 1)
                await store._call(observe)

            assert seen[0] is not None
            assert seen[0] is seen[1], "the scope changed connection mid-unit"

            await store._call(observe)
            assert seen[2] is None, "the scope outlived its block"
            assert len(sp.list_containment_events(path, limit=10)) == 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_the_worker_thread_releases_the_connection_after_the_scope(
        self,
        tmp_path,
    ):
        from bartholomew.kernel.scheduler.store import SchedulerStore

        path = str(tmp_path / "sched.db")
        store = SchedulerStore(path)
        try:
            await store.ensure_schema()
            held: list[object] = []

            def grab() -> None:
                held.append(db_ctx.active_session_connection(path))

            async with store.unit_of_work():
                await store._call(grab)
            conn = held[0]
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_a_failure_inside_the_scope_still_releases_it(self, tmp_path):
        from bartholomew.kernel.scheduler.store import SchedulerStore

        path = str(tmp_path / "sched.db")
        store = SchedulerStore(path)
        try:
            await store.ensure_schema()
            held: list[object] = []

            with pytest.raises(RuntimeError):
                async with store.unit_of_work():
                    await store._call(lambda: held.append(db_ctx.active_session_connection(path)))
                    raise RuntimeError("drive blew up")

            with pytest.raises(sqlite3.ProgrammingError):
                held[0].execute("SELECT 1")
            # And the store is still usable afterwards.
            await store.insert_nudge("task", "after", [], "user_task", 2)
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_writes_inside_the_scope_are_durable(self, tmp_path):
        from bartholomew.kernel.scheduler.store import SchedulerStore

        path = str(tmp_path / "sched.db")
        store = SchedulerStore(path)
        try:
            await store.ensure_schema()
            async with store.unit_of_work():
                for i in range(20):
                    await store.insert_nudge("task", f"n{i}", [], "user_task", 100 + i)
        finally:
            await store.close()

        conn = sqlite3.connect(path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM nudges").fetchone()[0] == 20
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_it_is_not_held_across_the_store_s_lifetime(self, tmp_path):
        """The scope must not silently become a permanent connection."""
        from bartholomew.kernel.scheduler.store import SchedulerStore

        path = str(tmp_path / "sched.db")
        store = SchedulerStore(path)
        try:
            await store.ensure_schema()
            async with store.unit_of_work():
                pass
            bound: list[object] = []
            await store._call(lambda: bound.append(db_ctx.active_session_connection(path)))
            assert bound[0] is None
        finally:
            await store.close()
