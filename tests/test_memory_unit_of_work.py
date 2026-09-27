"""
The bounded unit of work that holds a database open (`memory_unit_of_work`).

Why this file exists. Every storage operation in this repository owns its own
SQLite connection, and between operations nothing holds the file open -- so
every close is SQLite's *last* close of a WAL database, which checkpoints the
WAL and unlinks `-wal`/`-shm`, and the next operation recreates them. On the
Windows Merge Candidate (run 36153552522) that teardown was 91-98 % of every
heavy test's wall time, and three workers were killed inside it, through
three different connection seams.

`memory_unit_of_work()` lets a caller declare a burst as one unit of work: it
holds one idle, policy-configured connection for exactly the `async with`,
lends it to nobody, and closes it at the end. The operations inside are the
same operations. These tests pin both halves of that:

* the mechanism, against a causal control -- unscoped, the WAL is torn down
  after every operation; scoped, after none of them, through every seam;
* the forbidden states -- it must never become a pool, leak a handle, be
  shared across tasks, threads or loops, outlive its scope, span operations
  with a transaction or a read snapshot, lose a rollback, bypass the
  connection policy, or change what governance decides.

Linux figures in the record (docs/SQLITE_WAL_HEADROOM_REPAIR.md) are mechanism
evidence only; the Windows effect is measured on the Windows Merge Candidate.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import contextvars
import os
import pathlib
import sqlite3
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone

import aiosqlite
import pytest

from bartholomew.kernel import db_ctx
from bartholomew.kernel import memory_store as memory_store_module
from bartholomew.kernel import objective_store as objective_store_module
from bartholomew.kernel.memory_store import (
    MemoryStore,
    memory_unit_of_work,
    open_memory_db,
    open_memory_db_sync,
)
from bartholomew.kernel.objective_store import ObjectiveStore

REPO = pathlib.Path(__file__).resolve().parents[1]
MEMORY_STORE_SOURCE = pathlib.Path(memory_store_module.__file__)
SETUP_STATEMENTS = [*db_ctx.CONNECTION_SETUP_PRAGMAS, db_ctx.OPERATIONAL_BUSY_TIMEOUT_PRAGMA]
HELD_STATEMENTS = [
    *SETUP_STATEMENTS,
    memory_store_module._UNIT_OF_WORK_JOURNAL_SQL,
    memory_store_module._UNIT_OF_WORK_ATTACH_SQL,
]

#: Small on purpose: the unscoped control pays the very teardown this repair
#: removes (60-130 ms per close on a loaded Windows runner), and five
#: operations prove "after every one" as well as five hundred would.
N = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "unit_of_work.db")
    asyncio.run(MemoryStore(path).init())
    return path


class _CloseRecordingConnection(sqlite3.Connection):
    """Records its own close. Probing a connection from outside cannot tell
    open from closed here: aiosqlite's connections belong to its worker
    thread, and from any other thread `execute` raises the same
    ProgrammingError whether the connection is open or closed."""

    closed = False

    def close(self, *args, **kwargs):
        super().close(*args, **kwargs)
        self.closed = True


@pytest.fixture
def recorded_connections(monkeypatch):
    """Every sqlite3 connection opened while the test runs, with every
    statement it executed from its first and whether it has been closed.
    aiosqlite opens its connection by calling `sqlite3.connect` on its worker
    thread, so this sees every seam."""
    original = sqlite3.connect
    opened: list[dict] = []

    def recording_connect(database, *args, **kwargs):
        kwargs.setdefault("factory", _CloseRecordingConnection)
        conn = original(database, *args, **kwargs)
        entry = {
            "database": str(database),
            "timeout": kwargs.get("timeout"),
            "statements": [],
            "conn": conn,
        }
        conn.set_trace_callback(entry["statements"].append)
        opened.append(entry)
        return conn

    monkeypatch.setattr(sqlite3, "connect", recording_connect)
    return opened


def _ours(recorded: list[dict], path: str) -> list[dict]:
    return [c for c in recorded if c["database"] == path]


def _held(recorded: list[dict], path: str) -> list[dict]:
    """The scope's own connections: the only ones that run the attach read."""
    return [
        c
        for c in _ours(recorded, path)
        if memory_store_module._UNIT_OF_WORK_ATTACH_SQL in c["statements"]
    ]


def _is_closed(conn: sqlite3.Connection) -> bool:
    assert isinstance(conn, _CloseRecordingConnection), "not opened under recorded_connections"
    return conn.closed


def _handles_held_on(path: str) -> list[str]:
    fd_dir = pathlib.Path("/proc/self/fd")
    if not fd_dir.exists():
        return []
    held = []
    for fd in fd_dir.iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith(path):
            held.append(target)
    return held


def _assert_nothing_holds(path: str) -> None:
    assert _handles_held_on(path) == []
    # On Windows an open handle makes this raise; everywhere it must succeed.
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


def _wal_exists(path: str) -> bool:
    return os.path.exists(path + "-wal")


def _count(path: str, sql: str, params: tuple = ()) -> int:
    """Read through a connection that is deliberately none of the store's."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _hold_exclusive(path: str, seconds: float) -> threading.Thread:
    holder = sqlite3.connect(path, check_same_thread=False)
    holder.execute("PRAGMA locking_mode = EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    holder.execute("SELECT 1")

    def release():
        holder.rollback()
        holder.execute("PRAGMA locking_mode = NORMAL")
        holder.execute("SELECT count(*) FROM sqlite_master")
        holder.close()

    timer = threading.Timer(seconds, release)
    timer.start()
    return timer


# ---------------------------------------------------------------------------
# 1. The mechanism, with its causal control
# ---------------------------------------------------------------------------


def test_control_unscoped_every_operation_tears_the_wal_down(db_path):
    """The defect, reproduced: nothing holds the file open between governed
    writes, so each one's close is the last close and deletes the WAL."""

    async def _burst():
        store = MemoryStore(db_path)
        torn_down = []
        for i in range(N):
            await store.upsert_memory("fact", f"u{i}", f"my password is u{i}", _now())
            torn_down.append(not _wal_exists(db_path))
        return torn_down

    assert asyncio.run(_burst()) == [True] * N


def test_inside_a_unit_of_work_no_operation_tears_the_wal_down(db_path):
    """The same writes, declared as one unit of work: the WAL survives every
    operation, and is torn down exactly once -- when the scope ends."""

    async def _burst():
        store = MemoryStore(db_path)
        torn_down = []
        async with store.unit_of_work(label="test"):
            for i in range(N):
                await store.upsert_memory("fact", f"s{i}", f"my password is s{i}", _now())
                torn_down.append(not _wal_exists(db_path))
        return torn_down, not _wal_exists(db_path)

    torn_down, torn_down_at_end = asyncio.run(_burst())
    assert torn_down == [False] * N
    assert torn_down_at_end is True


def _every_seam(db_path: str) -> list[tuple[str, object]]:
    """One operation per connection seam that reaches this file, each on the
    thread it runs on in production (the sync ones off the event loop)."""
    objectives = ObjectiveStore(db_path)

    def _wal_db_write(i):
        with db_ctx.wal_db(db_path) as conn:
            conn.execute(
                "INSERT INTO system_flags(key, value, updated_at) VALUES (?, '1', '0')",
                (f"seam-{i}",),
            )
            conn.commit()

    def _sync_seam_read():
        with open_memory_db_sync(db_path) as conn:
            conn.execute("SELECT count(*) FROM memories").fetchall()

    def _objective_transition(i):
        objectives.open(title=f"objective {i}", outcome_statement="done")

    return [
        ("aiosqlite (MemoryStore)", "async"),
        ("open_memory_db_sync", _sync_seam_read),
        ("db_ctx.wal_db", _wal_db_write),
        ("db_ctx.connect (ObjectiveStore)", _objective_transition),
    ]


def _run_every_seam(db_path: str, scoped: bool) -> dict[str, bool]:
    objective_store_module.ensure_schema(db_path)

    async def _run():
        store = MemoryStore(db_path)
        torn_down: dict[str, bool] = {}
        scope = store.unit_of_work(label="seams") if scoped else contextlib.nullcontext()
        async with scope:
            for i, (name, op) in enumerate(_every_seam(db_path)):
                if op == "async":
                    await store.upsert_memory("fact", f"seam{i}", "a harmless fact", _now())
                elif name == "open_memory_db_sync":
                    await asyncio.to_thread(op)
                else:
                    await asyncio.to_thread(op, i)
                torn_down[name] = not _wal_exists(db_path)
        return torn_down

    return asyncio.run(_run())


def test_the_scope_covers_every_connection_seam_on_any_thread(db_path, tmp_path):
    """The shared defect behind all three Windows kills was reached through
    three different seams (aiosqlite, db_ctx.wal_db, db_ctx.connect). The
    scope lends nothing, so it covers them all -- on the event loop and on
    worker threads alike. Control first, on a separate file."""
    control_path = str(tmp_path / "control.db")
    asyncio.run(MemoryStore(control_path).init())
    control = _run_every_seam(control_path, scoped=False)
    assert all(control.values()), f"control: every seam should tear down: {control}"

    scoped = _run_every_seam(db_path, scoped=True)
    assert not any(scoped.values()), f"a seam still tore the WAL down in scope: {scoped}"


def test_a_scope_on_a_database_that_does_not_exist_yet_is_refused(tmp_path):
    """Entered before init(), the held connection would create an empty
    rollback-journal file and hold nothing open: a scope that silently does
    nothing. It refuses instead, and creates nothing."""
    path = str(tmp_path / "not_yet.db")

    async def _enter():
        async with memory_unit_of_work(path):
            pytest.fail("a scope on a missing database must not open")

    with pytest.raises(FileNotFoundError):
        asyncio.run(_enter())
    assert not os.path.exists(path)


def test_a_scope_on_a_non_wal_database_is_refused_and_released(tmp_path, recorded_connections):
    """On a rollback-journal file an idle connection holds no lock, so the
    scope could not work; it says so instead of pretending."""
    path = str(tmp_path / "rollback.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()

    async def _enter():
        with pytest.raises(RuntimeError, match="not WAL"):
            async with memory_unit_of_work(path):
                pytest.fail("a scope on a non-WAL database must not open")
        # The scope's connection is the one opened with the setup budget.
        return [_is_closed(c["conn"]) for c in _ours(recorded_connections, path) if c["timeout"]]

    assert asyncio.run(_enter()) == [True]
    _assert_nothing_holds(path)


def test_when_the_scope_ends_the_database_file_alone_holds_every_committed_row(
    db_path,
    tmp_path,
):
    """Inside a scope committed work lives in the WAL until a checkpoint --
    SQLite's documented synchronous=NORMAL behaviour. The scope's own close is
    the last close, so it checkpoints: afterwards a copy of the database file
    by itself, with no WAL beside it, contains everything the scope committed."""

    async def _burst():
        store = MemoryStore(db_path)
        async with store.unit_of_work():
            for i in range(N):
                await store.upsert_memory("fact", f"durable{i}", "a value", _now())

    asyncio.run(_burst())
    assert not _wal_exists(db_path)
    copy = tmp_path / "copy_of_db_file_only.db"
    copy.write_bytes(pathlib.Path(db_path).read_bytes())
    assert _count(str(copy), "SELECT count(*) FROM memories WHERE key LIKE 'durable%'") == N


# ---------------------------------------------------------------------------
# 2. It lends nothing: operations are unchanged
# ---------------------------------------------------------------------------


async def _ordinary_operations(store: MemoryStore) -> list:
    outcomes = []
    outcomes.append((await store.upsert_memory("fact", "a", "the bin goes out", _now())).outcome)
    outcomes.append(
        (await store.upsert_memory("fact", "b", "my password is x", _now())).outcome,
    )
    outcomes.append(bool(await store.get_memory("fact", "a")))
    outcomes.append(len(await store.list_pending_sensitive_writes()))
    outcomes.append(await store.delete_memory("fact", "a"))
    return outcomes


def test_every_operation_inside_still_owns_its_own_connection(
    db_path,
    tmp_path,
    recorded_connections,
):
    control_path = str(tmp_path / "control.db")
    asyncio.run(MemoryStore(control_path).init())
    recorded_connections.clear()

    async def _unscoped():
        return await _ordinary_operations(MemoryStore(control_path))

    async def _scoped():
        store = MemoryStore(db_path)
        async with store.unit_of_work(label="ownership") as bound:
            assert bound is None, "the scope must not hand its connection to anyone"
            return await _ordinary_operations(store)

    unscoped_outcomes = asyncio.run(_unscoped())
    scoped_outcomes = asyncio.run(_scoped())
    assert scoped_outcomes == unscoped_outcomes

    unscoped = _ours(recorded_connections, control_path)
    scoped = _ours(recorded_connections, db_path)
    held = _held(recorded_connections, db_path)

    # Exactly one extra connection: the scope's own. Every operation opened
    # its own, just as it does outside a scope.
    assert len(held) == 1
    assert len(scoped) == len(unscoped) + 1
    assert len({id(c["conn"]) for c in scoped}) == len(scoped)

    # The held connection ran its setup and its one attaching read, and
    # nothing else -- no operation ever executed a statement on it.
    assert held[0]["statements"] == HELD_STATEMENTS

    # No configuration bypass: every connection, held or not, was set up by
    # the shared policy under the setup budget before touching data.
    for entry in scoped:
        assert entry["statements"][: len(SETUP_STATEMENTS)] == SETUP_STATEMENTS
        assert entry["timeout"] == db_ctx.SETUP_LOCK_TIMEOUT_S


def test_an_operation_inside_the_scope_runs_under_the_shared_policy(db_path):
    async def _pragmas_inside():
        async with memory_unit_of_work(db_path):
            async with open_memory_db(db_path) as db:
                values = {}
                for name in ("synchronous", "foreign_keys", "busy_timeout"):
                    cursor = await db.execute(f"PRAGMA {name}")
                    values[name] = (await cursor.fetchone())[0]
                return values

    assert asyncio.run(_pragmas_inside()) == {
        "synchronous": 1,
        "foreign_keys": 1,
        "busy_timeout": db_ctx.OPERATIONAL_BUSY_TIMEOUT_MS,
    }


# ---------------------------------------------------------------------------
# 3. Not a pool, not unbounded, no leak
# ---------------------------------------------------------------------------


def _released_now(recorded: list[dict], path: str) -> bool:
    """Evaluated inside the event loop, straight after a scope has exited.

    Checking after `asyncio.run()` returns would prove nothing: an abandoned
    async context manager is finalised at loop shutdown, so a scope that
    forgot to close would look closed by then. The contract is that the
    handle is released when the scope ends, not eventually."""
    held = _held(recorded, path)
    return (
        bool(held) and all(_is_closed(c["conn"]) for c in held) and (_handles_held_on(path) == [])
    )


def test_nothing_survives_the_scope(db_path, recorded_connections):
    async def _work():
        store = MemoryStore(db_path)
        async with store.unit_of_work():
            await store.upsert_memory("fact", "x", "a value", _now())
            held_inside = not _released_now(recorded_connections, db_path)
        return held_inside, _released_now(recorded_connections, db_path)

    held_inside, released_at_exit = asyncio.run(_work())
    assert held_inside, "inside the scope its connection should be open"
    assert released_at_exit, "the scope ended with its connection or a handle still open"
    assert all(_is_closed(c["conn"]) for c in _ours(recorded_connections, db_path))
    _assert_nothing_holds(db_path)


def test_re_entering_opens_a_new_held_connection_rather_than_reusing_one(
    db_path,
    recorded_connections,
):
    async def _twice():
        released = []
        for _ in range(2):
            async with memory_unit_of_work(db_path):
                pass
            released.append(_released_now(recorded_connections, db_path))
        return released

    assert asyncio.run(_twice()) == [True, True]
    held = _held(recorded_connections, db_path)
    assert len(held) == 2
    assert held[0]["conn"] is not held[1]["conn"]


def _connection_like(value) -> bool:
    return isinstance(value, (aiosqlite.Connection, sqlite3.Connection))


def _connections_reachable_from(namespace: dict, depth: int = 3) -> list[str]:
    """Connection objects held by `namespace`, directly or inside containers
    (a registry dict, a list, a weak mapping), `depth` levels down. Modules,
    classes and functions are not entered: they are code, not storage."""
    found = []

    def _walk(value, where, level):
        if _connection_like(value):
            found.append(where)
            return
        if level == 0 or isinstance(value, (str, bytes, type)) or callable(value):
            return
        if isinstance(value, Mapping):
            items = list(value.items())
        elif isinstance(value, (list, tuple, set, frozenset)):
            items = list(enumerate(value))
        else:
            return
        for key, item in items:
            _walk(item, f"{where}[{key!r}]", level - 1)

    for name, value in list(namespace.items()):
        _walk(value, name, depth)
    return found


def test_nothing_anywhere_can_find_or_keep_the_held_connection(db_path):
    """A pool needs somewhere to keep connections. There is nowhere: no module
    state (not even inside a container), no context variable, no attribute on
    the store -- during the scope or after it."""
    namespaces = {"memory_store": memory_store_module, "db_ctx": db_ctx}
    for module in namespaces.values():
        assert not [n for n, v in vars(module).items() if isinstance(v, contextvars.ContextVar)]

    def _anywhere(store):
        hits = []
        for label, module in namespaces.items():
            hits += [f"{label}.{w}" for w in _connections_reachable_from(vars(module))]
        hits += [f"store.{w}" for w in _connections_reachable_from(vars(store))]
        return hits

    async def _look():
        store = MemoryStore(db_path)
        async with store.unit_of_work():
            during = _anywhere(store)
        return during, _anywhere(store)

    assert asyncio.run(_look()) == ([], [])


def _call_sites(name: str, root: pathlib.Path) -> list[str]:
    sites = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called == name:
                sites.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
    return sites


def test_no_production_code_opens_a_memory_unit_of_work_implicitly():
    """Where a unit of work begins and ends is a design statement its caller
    makes (DECISIONS.md, 2026-09-18). No MemoryStore method opens one, and no
    production caller holds one today; adopting it in the product is a
    separate, reviewed decision that must update this test on purpose."""
    product = REPO / "bartholomew"
    assert _call_sites("memory_unit_of_work", product) == [
        f"bartholomew/kernel/memory_store.py:{_delegation_line()}",
    ]
    # `unit_of_work` is also SchedulerStore's (db_session-based) scope, whose
    # one production caller is the scheduler tick.
    assert all(
        site.startswith("bartholomew/kernel/scheduler/")
        for site in _call_sites("unit_of_work", product)
    ), _call_sites("unit_of_work", product)


def _delegation_line() -> int:
    tree = ast.parse(MEMORY_STORE_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "unit_of_work":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == (
                    "memory_unit_of_work"
                ):
                    return inner.lineno
    raise AssertionError("MemoryStore.unit_of_work no longer delegates")


def test_the_held_connection_is_released_when_the_body_raises(db_path, recorded_connections):
    async def _raise():
        with pytest.raises(KeyError):
            async with memory_unit_of_work(db_path):
                raise KeyError("boom")
        return _released_now(recorded_connections, db_path)

    assert asyncio.run(_raise()) is True
    _assert_nothing_holds(db_path)


def test_the_held_connection_is_released_when_the_body_is_cancelled(
    db_path,
    recorded_connections,
):
    async def _cancelled():
        entered = asyncio.Event()

        async def _body():
            async with memory_unit_of_work(db_path):
                entered.set()
                await asyncio.sleep(60)

        task = asyncio.create_task(_body())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return _released_now(recorded_connections, db_path)

    assert asyncio.run(_cancelled()) is True
    _assert_nothing_holds(db_path)


def test_repeated_cancellation_cannot_end_the_scope_before_its_handle_is_released(
    db_path,
    recorded_connections,
    monkeypatch,
):
    """aiosqlite runs a queued close whether or not anyone still waits for it,
    so a cancellation that abandons the wait does not stop the close -- it
    lets the scope end while the handle is still open. Here the held
    connection's worker is kept busy for half a second behind its close, and
    the task is cancelled again and again until it ends: when it ends, the
    held connection must already be closed."""
    real_open = memory_store_module.open_memory_db

    @contextlib.asynccontextmanager
    async def busy_at_release(path):
        async with real_open(path) as db:
            await db.create_function("pause_ms", 1, lambda ms: time.sleep(ms / 1000) or 0)
            try:
                yield db
            finally:
                # Queue work ahead of the close the seam is about to queue.
                asyncio.ensure_future(db.execute_fetchall("SELECT pause_ms(500)"))
                await asyncio.sleep(0)

    monkeypatch.setattr(memory_store_module, "open_memory_db", busy_at_release)

    async def _storm():
        entered = asyncio.Event()

        async def _body():
            async with memory_unit_of_work(db_path):
                entered.set()
                await asyncio.sleep(60)

        task = asyncio.create_task(_body())
        await entered.wait()
        while not task.done():
            task.cancel()
            await asyncio.sleep(0.005)
        assert task.cancelled()
        return _released_now(recorded_connections, db_path)

    assert asyncio.run(_storm()) is True
    _assert_nothing_holds(db_path)


def test_a_held_connection_whose_setup_fails_is_closed_and_the_error_raised(
    db_path,
    recorded_connections,
    monkeypatch,
):
    monkeypatch.setattr(db_ctx, "SETUP_LOCK_TIMEOUT_S", 0.2)
    release = _hold_exclusive(db_path, 1.5)
    try:

        async def _enter():
            async with memory_unit_of_work(db_path):
                pytest.fail("setup cannot have succeeded under the hold")

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            asyncio.run(_enter())
    finally:
        release.join()
    # The holder is a plain connection (no timeout argument); the scope's is not.
    seam = [c for c in _ours(recorded_connections, db_path) if c["timeout"]]
    assert len(seam) == 1 and _is_closed(seam[0]["conn"])


# ---------------------------------------------------------------------------
# 4. No sharing across tasks, threads or event loops
# ---------------------------------------------------------------------------


def test_concurrent_tasks_threads_and_loops_in_the_scope_use_their_own_connections(
    db_path,
    recorded_connections,
):
    def _other_loop_on_another_thread():
        async def _writes():
            store = MemoryStore(db_path)
            for i in range(3):
                await store.upsert_memory("fact", f"thread{i}", "from another loop", _now())

        asyncio.run(_writes())

    async def _concurrently():
        store = MemoryStore(db_path)
        async with store.unit_of_work(label="concurrency"):
            thread = threading.Thread(target=_other_loop_on_another_thread)
            thread.start()
            results = await asyncio.gather(
                *(
                    store.upsert_memory("fact", f"task{i}", "from a child task", _now())
                    for i in range(5)
                ),
            )
            await asyncio.to_thread(thread.join)
        return results

    results = asyncio.run(_concurrently())
    assert all(r.stored for r in results)
    assert _count(db_path, "SELECT count(*) FROM memories WHERE key LIKE 'task%'") == 5
    assert _count(db_path, "SELECT count(*) FROM memories WHERE key LIKE 'thread%'") == 3

    (held,) = _held(recorded_connections, db_path)
    assert held["statements"] == HELD_STATEMENTS, "someone else used the held connection"
    assert all(_is_closed(c["conn"]) for c in _ours(recorded_connections, db_path))


# ---------------------------------------------------------------------------
# 5. No transaction or snapshot spans operations; rollback is not lost
# ---------------------------------------------------------------------------


def test_the_held_connection_pins_no_snapshot_and_blocks_no_one(db_path):
    """Idle means idle: another writer takes the write lock at once, an
    operation in the scope sees that write immediately, and a TRUNCATE
    checkpoint -- which waits for any reader holding a snapshot -- completes."""

    async def _probe():
        store = MemoryStore(db_path)
        async with store.unit_of_work():
            await store.upsert_memory("fact", "before", "a value", _now())

            other = sqlite3.connect(db_path, timeout=0)
            try:
                other.execute("BEGIN IMMEDIATE")
                other.execute(
                    "INSERT INTO memories(kind, key, value, ts) VALUES ('fact', 'outside', 'v', ?)",
                    (_now(),),
                )
                other.commit()
                busy, _log, _done = other.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                wal_size = os.path.getsize(db_path + "-wal")
            finally:
                other.close()
            seen = await store.get_memory("fact", "outside")
        return busy, wal_size, seen

    busy, wal_size, seen = asyncio.run(_probe())
    assert busy == 0, "something in the scope held a read snapshot"
    assert wal_size == 0, "the TRUNCATE checkpoint could not reset the WAL"
    assert seen is not None, "an operation in the scope read a stale snapshot"


def test_an_uncommitted_write_inside_the_scope_does_not_persist(db_path):
    async def _uncommitted():
        store = MemoryStore(db_path)
        async with store.unit_of_work():
            async with open_memory_db(db_path) as db:
                await db.execute(
                    "INSERT INTO memories(kind, key, value, ts) VALUES ('fact', 'ghost', 'v', ?)",
                    (_now(),),
                )
            # The next operation commits its own work and nothing else.
            await store.upsert_memory("fact", "real", "a value", _now())

    asyncio.run(_uncommitted())
    assert _count(db_path, "SELECT count(*) FROM memories WHERE key = 'ghost'") == 0
    assert _count(db_path, "SELECT count(*) FROM memories WHERE key = 'real'") == 1


def test_a_failed_operation_inside_the_scope_leaves_nothing_behind(db_path, monkeypatch):
    """A write that fails after its INSERT, before its commit: the row must not
    land, and the next operation in the same scope must not commit it."""
    real_reindex = memory_store_module.reindex_memory_fts_async

    async def failing_reindex(db, memory_id, text):
        raise RuntimeError("index write failed")

    async def _fail_then_succeed():
        store = MemoryStore(db_path)
        async with store.unit_of_work():
            monkeypatch.setattr(memory_store_module, "reindex_memory_fts_async", failing_reindex)
            with pytest.raises(RuntimeError):
                await store.upsert_memory("fact", "half", "a value", _now())
            monkeypatch.setattr(memory_store_module, "reindex_memory_fts_async", real_reindex)
            await store.upsert_memory("fact", "whole", "a value", _now())

    asyncio.run(_fail_then_succeed())
    assert _count(db_path, "SELECT count(*) FROM memories WHERE key = 'half'") == 0
    assert _count(db_path, "SELECT count(*) FROM memories WHERE key = 'whole'") == 1


# ---------------------------------------------------------------------------
# 6. Governance decides exactly as it does outside a scope
# ---------------------------------------------------------------------------


async def _governed_script(store: MemoryStore) -> list:
    """Every gate the write path has, in an order that makes each depend on
    the database state the previous operations left behind."""
    ts = _now()
    seen = []
    seen.append((await store.upsert_memory("fact", "plain", "the bin goes out", ts)).outcome)
    seen.append((await store.upsert_memory("fact", "pw", "my password is hunter2", ts)).outcome)
    seen.append((await store.upsert_memory("fact", "blocked", "csam", ts)).outcome)
    seen.append(await store.forget_memory("fact", "plain"))
    # The tombstone written by the previous operation must be read fresh.
    seen.append((await store.upsert_memory("fact", "plain", "relearned", ts)).outcome)
    await store.upsert_memory("fact", "target", "harmless original", ts)
    correction = await store.correct_memory("fact", "target", "my password is s3cret")
    seen.append((correction.stored, correction.queued_for_consent))
    pending = await store.list_pending_sensitive_writes(limit=50)
    seen.append(sorted((p["kind"], p["key"], p["reason"], p["readable"]) for p in pending))
    return seen


def _stored_state(path: str) -> dict:
    conn = sqlite3.connect(path)
    try:
        return {
            "memories": sorted(conn.execute("SELECT kind, key FROM memories").fetchall()),
            "pending": sorted(
                conn.execute(
                    "SELECT kind, key, reason, status, value LIKE '%bartholomew.enc.v1%' "
                    "FROM pending_sensitive_writes",
                ).fetchall(),
            ),
            "revocations": sorted(
                conn.execute("SELECT kind, key FROM memory_revocations").fetchall(),
            ),
        }
    finally:
        conn.close()


def test_governed_outcomes_are_identical_inside_and_outside_a_scope(db_path, tmp_path):
    control_path = str(tmp_path / "control.db")
    asyncio.run(MemoryStore(control_path).init())

    async def _unscoped():
        return await _governed_script(MemoryStore(control_path))

    async def _scoped():
        store = MemoryStore(db_path)
        async with store.unit_of_work(label="governance"):
            return await _governed_script(store)

    unscoped = asyncio.run(_unscoped())
    scoped = asyncio.run(_scoped())
    assert scoped == unscoped
    assert unscoped[:5] == ["stored", "queued_for_consent", "refused", True, "refused_revoked"]
    assert _stored_state(db_path) == _stored_state(control_path)
    # The consent inbox is still encrypted at rest inside a scope.
    assert all(row[4] for row in _stored_state(db_path)["pending"])


# ---------------------------------------------------------------------------
# 7. The repair is declared where the evidence put it
# ---------------------------------------------------------------------------

ADOPTED = {
    "tests/test_memory_agency_review_fixes.py": "test_queued_outcome_is_independent_of_inbox_size",
    "tests/integration/test_fts_unavailable_vector_quality.py": (
        "test_vector_quality_maintained_when_fts_unavailable"
    ),
    "tests/test_learning_memory_control_centre.py": (
        "test_b6d_the_material_field_vocabulary_is_enforced_not_documented"
    ),
}


@pytest.mark.parametrize(
    "relative, test_name",
    sorted(ADOPTED.items()),
    # Explicit ids: conftest marks any test whose name contains "integration"
    # as an integration test, which the default suite deselects.
    ids=lambda value: pathlib.Path(value).stem if value.endswith(".py") else value[:24],
)
def test_each_burst_killed_on_windows_declares_its_unit_of_work(relative, test_name):
    """The three tests whose workers were killed inside a last-close teardown
    on Merge Candidate 36153552522. Removing the scope reinstates the defect
    silently -- the tests would still pass locally -- so it is pinned here."""
    tree = ast.parse((REPO / relative).read_text(encoding="utf-8"))
    (function,) = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == test_name
    ]
    scopes = [
        n
        for n in ast.walk(function)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", getattr(n.func, "id", None)) == "unit_of_work"
    ]
    assert scopes, f"{relative}::{test_name} no longer declares its unit of work"
