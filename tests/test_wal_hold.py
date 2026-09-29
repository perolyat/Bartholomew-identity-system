"""
Holding a WAL database open for a declared burst (`db_ctx.hold_wal_open`).

Why this file exists. Every storage operation in this repository owns its own
SQLite connection, and between operations nothing holds the file open -- so
every close is SQLite's *last* close of a WAL database, which checkpoints the
WAL and unlinks `-wal`/`-shm`, and the next operation recreates them. On the
Windows Merge Candidate (run 36153552522) that teardown was 91-98 % of every
heavy test's wall time, and three workers were killed inside it, through three
different connection seams.

`hold_wal_open()` lets a caller declare a burst: it holds one idle,
policy-configured connection -- on a daemon thread of its own -- for exactly
the `with`/`async with`, lends it to nobody, and closes it at the end. The
operations inside are the same operations. These tests pin both halves:

* the mechanism, against a causal control -- unheld, the WAL is torn down after
  every operation; held, after none of them, through every seam;
* the forbidden states -- it must never become a pool, leak a handle or a
  thread, be shared across tasks, threads or loops, outlive its declaration,
  span operations with a transaction or a read snapshot, lose a rollback,
  bypass the connection policy, keep a process from exiting, or change what
  governance decides.

Linux figures in the record (docs/SQLITE_WAL_HEADROOM_REPAIR.md) are mechanism
evidence only; the Windows effect is measured on the Windows Merge Candidate.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import contextvars
import gc
import os
import pathlib
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
import types
import urllib.parse
from collections.abc import Mapping
from datetime import datetime, timezone

import aiosqlite
import pytest

from bartholomew.kernel import db_ctx
from bartholomew.kernel import memory_store as memory_store_module
from bartholomew.kernel import objective_store as objective_store_module
from bartholomew.kernel.db_ctx import hold_wal_open
from bartholomew.kernel.memory_store import MemoryStore, open_memory_db, open_memory_db_sync
from bartholomew.kernel.objective_store import ObjectiveStore

REPO = pathlib.Path(__file__).resolve().parents[1]
SETUP_STATEMENTS = [*db_ctx.CONNECTION_SETUP_PRAGMAS, db_ctx.OPERATIONAL_BUSY_TIMEOUT_PRAGMA]
HELD_STATEMENTS = [*SETUP_STATEMENTS, db_ctx._HOLD_JOURNAL_SQL, db_ctx._HOLD_ATTACH_SQL]

#: Small on purpose: the unheld control pays the very teardown this repair
#: removes (60-130 ms per close on a loaded Windows runner), and five
#: operations prove "after every one" as well as five hundred would.
N = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "held.db")
    asyncio.run(MemoryStore(path).init())
    return path


class _CloseRecordingConnection(sqlite3.Connection):
    """Records its own close. Probing a connection from outside cannot tell
    open from closed: a connection that belongs to another thread (aiosqlite's
    worker, a hold's thread) raises the same ProgrammingError from `execute`
    whether it is open or closed."""

    closed = False

    def close(self, *args, **kwargs):
        super().close(*args, **kwargs)
        self.closed = True


@pytest.fixture
def recorded_connections(monkeypatch):
    """Every sqlite3 connection opened while the test runs, with every
    statement it executed from its first and whether it has been closed.
    aiosqlite, `db_ctx.connect()` and the hold all go through
    `sqlite3.connect`, so this sees every seam."""
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


def _uri_path(uri: str) -> str:
    """The path SQLite reads from a `file:` URI the hold wrote: an empty
    authority dropped, the query dropped, `%HH` escapes decoded."""
    rest = uri[len("file:") :]
    if rest.startswith("//"):
        rest = rest[2:]
    return urllib.parse.unquote(rest.split("?", 1)[0])


def _file_of(database: str) -> str:
    """The file a connection was opened on. The hold opens by `file:` URI
    (`mode=rw`, never create); every other seam opens by path."""
    if database.startswith("file:"):
        database = _uri_path(database)
    return os.path.normcase(os.path.abspath(database))


def _ours(recorded: list[dict], path: str) -> list[dict]:
    return [c for c in recorded if _file_of(c["database"]) == _file_of(path)]


def _held(recorded: list[dict], path: str) -> list[dict]:
    """The holds' own connections: the only ones that run the attach read."""
    return [c for c in _ours(recorded, path) if db_ctx._HOLD_ATTACH_SQL in c["statements"]]


def _is_closed(conn: sqlite3.Connection) -> bool:
    assert isinstance(conn, _CloseRecordingConnection), "not opened under recorded_connections"
    return conn.closed


def _hold_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("wal-hold:")]


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


def _released_now(recorded: list[dict], path: str) -> bool:
    """Evaluated straight after a hold has ended, inside the event loop.

    Checking after `asyncio.run()` returns would prove less: loop shutdown
    finalises abandoned async context managers, so a hold that forgot to close
    could look closed by then. The contract is that the connection is closed
    and its thread gone when the hold ends, not eventually."""
    held = _held(recorded, path)
    return (
        bool(held)
        and all(_is_closed(c["conn"]) for c in held)
        and not _hold_threads()
        and _handles_held_on(path) == []
    )


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


def test_control_unheld_every_operation_tears_the_wal_down(db_path):
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


def test_while_held_no_operation_tears_the_wal_down(db_path):
    """The same writes, held: the WAL survives every operation, and is torn
    down exactly once -- when the hold ends."""

    async def _burst():
        store = MemoryStore(db_path)
        torn_down = []
        async with hold_wal_open(db_path, label="test"):
            for i in range(N):
                await store.upsert_memory("fact", f"s{i}", f"my password is s{i}", _now())
                torn_down.append(not _wal_exists(db_path))
        return torn_down, not _wal_exists(db_path)

    torn_down, torn_down_at_end = asyncio.run(_burst())
    assert torn_down == [False] * N
    assert torn_down_at_end is True


def _run_every_seam(db_path: str, held: bool) -> dict[str, bool]:
    """One operation per connection seam that reaches this file, the sync
    ones off the event loop as in production. Returns, per seam, whether that
    operation's close tore the WAL down."""
    objective_store_module.ensure_schema(db_path)
    objectives = ObjectiveStore(db_path)

    def _wal_db_write():
        with db_ctx.wal_db(db_path) as conn:
            conn.execute(
                "INSERT INTO system_flags(key, value, updated_at) VALUES ('seam', '1', '0')",
            )
            conn.commit()

    def _sync_seam_read():
        with open_memory_db_sync(db_path) as conn:
            conn.execute("SELECT count(*) FROM memories").fetchall()

    def _objective_transition():
        objectives.open(title="an objective", outcome_statement="done")

    async def _run():
        store = MemoryStore(db_path)
        torn_down: dict[str, bool] = {}
        hold = hold_wal_open(db_path, label="seams") if held else contextlib.nullcontext()
        async with hold:
            await store.upsert_memory("fact", "seam", "a harmless fact", _now())
            torn_down["aiosqlite (MemoryStore)"] = not _wal_exists(db_path)
            for name, op in (
                ("open_memory_db_sync", _sync_seam_read),
                ("db_ctx.wal_db", _wal_db_write),
                ("db_ctx.connect (ObjectiveStore)", _objective_transition),
            ):
                await asyncio.to_thread(op)
                torn_down[name] = not _wal_exists(db_path)
        return torn_down

    return asyncio.run(_run())


def test_the_hold_covers_every_connection_seam_on_any_thread(db_path, tmp_path):
    """The shared defect behind all three Windows kills was reached through
    three different seams (aiosqlite, db_ctx.wal_db, db_ctx.connect). The hold
    lends nothing, so it covers them all -- on the event loop and on worker
    threads alike. Control first, on a separate file."""
    control_path = str(tmp_path / "control.db")
    asyncio.run(MemoryStore(control_path).init())
    control = _run_every_seam(control_path, held=False)
    assert all(control.values()), f"control: every seam should tear down: {control}"

    held = _run_every_seam(db_path, held=True)
    assert not any(held.values()), f"a seam still tore the WAL down while held: {held}"


def test_the_synchronous_form_holds_the_file_for_sync_callers(db_path):
    objective_store_module.ensure_schema(db_path)
    objectives = ObjectiveStore(db_path)
    torn_down = []
    with hold_wal_open(db_path, label="sync burst"):
        for i in range(N):
            objectives.open(title=f"objective {i}", outcome_statement="done")
            torn_down.append(not _wal_exists(db_path))
    assert torn_down == [False] * N
    assert not _wal_exists(db_path)


def test_the_synchronous_form_refuses_an_event_loop_thread(db_path):
    """Entering waits for the hold's setup, which can wait on a lock for the
    whole setup budget -- never on an event-loop thread."""

    async def _on_the_loop():
        with pytest.raises(RuntimeError, match="async with"):
            with hold_wal_open(db_path):
                pass

    asyncio.run(_on_the_loop())
    assert not _hold_threads()


def test_a_hold_on_a_database_that_does_not_exist_yet_is_refused(tmp_path):
    """Entered before the database is initialised, an idle connection would
    create an empty rollback-journal file and hold nothing open: a hold that
    silently does nothing. It refuses instead, and creates nothing."""
    path = str(tmp_path / "not_yet.db")

    async def _enter():
        async with hold_wal_open(path):
            pytest.fail("a hold on a missing database must not open")

    with pytest.raises(FileNotFoundError):
        asyncio.run(_enter())
    assert not os.path.exists(path)


def test_a_database_that_vanishes_after_the_entry_check_is_refused_not_recreated(
    tmp_path,
    monkeypatch,
):
    """The entry check and the hold's open are two steps. If the file is
    removed between them -- by its owner's cleanup, say -- the open must not
    create a new, empty database in its place: it opens read-write without
    create, so the hold is refused and nothing is left behind (Codex
    review_comment:4129475818)."""
    path = str(tmp_path / "vanishing.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    real_exists = os.path.exists

    def seen_then_removed(candidate):
        present = real_exists(candidate)
        if present and os.fspath(candidate) == path:
            # The entry check sees the file; it is gone before the hold opens it.
            for suffix in ("", "-wal", "-shm"):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(path + suffix)
        return present

    monkeypatch.setattr(os.path, "exists", seen_then_removed)

    async def _enter():
        async with hold_wal_open(path):
            pytest.fail("a hold whose file vanished must not open")

    with pytest.raises(FileNotFoundError):
        asyncio.run(_enter())
    assert not real_exists(path), "the hold created the database it was refused"
    assert not _hold_threads()


def _wal_database(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()


def _holds(hold_path: str, real_path: str) -> bool:
    """Whether a hold declared on `hold_path` holds `real_path` open: a write
    and close through `hold_path` inside it must leave the WAL of `real_path`
    in place (not its last close)."""

    async def _probe():
        async with hold_wal_open(hold_path):
            conn = sqlite3.connect(hold_path)
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
            conn.close()
            return os.path.exists(real_path + "-wal")

    return asyncio.run(_probe())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink-then-'..' resolution")
def test_the_hold_opens_the_file_every_other_seam_opens_through_a_symlink(tmp_path):
    """The path must reach SQLite as given. `link/../app.db` names the file
    beside the symlink's *target*; collapsing the `..` textually would hold a
    different file -- here a decoy -- while the caller's database kept paying
    its teardown on every close."""
    (tmp_path / "elsewhere" / "sub").mkdir(parents=True)
    (tmp_path / "data").mkdir()
    try:
        os.symlink(tmp_path / "elsewhere" / "sub", tmp_path / "data" / "link")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    real = str(tmp_path / "elsewhere" / "app.db")
    decoy = str(tmp_path / "data" / "app.db")
    _wal_database(real)
    _wal_database(decoy)
    via_link = str(tmp_path / "data" / "link" / ".." / "app.db")

    assert _holds(via_link, real), "the hold did not hold the file the path names"
    assert not os.path.exists(decoy + "-wal"), "the hold held a different file"
    _assert_nothing_holds(real)


def test_a_path_with_uri_special_characters_holds_that_file(tmp_path):
    """The hold opens by URI, so `%`, `#` (and `?` where the OS allows it)
    must name the file itself, not an escape, a fragment or a query."""
    name = "a b%41#c é" + ("?" if sys.platform != "win32" else "") + ".db"
    path = str(tmp_path / name)
    _wal_database(path)
    before = sorted(os.listdir(tmp_path))

    assert _holds(path, path)
    assert sorted(os.listdir(tmp_path)) == before, "the hold created another file"
    _assert_nothing_holds(path)


@pytest.mark.parametrize(
    "given",
    [
        "/abs/dir/x.db",
        "//server/share/x.db",
        "rel/dir/x.db",
        "C:\\dir\\x.db",
        "C:/dir/x.db",
        "\\\\server\\share\\x.db",
        "\\\\?\\C:\\long\\x.db",
        "/p/link/../a%b#c?d.db",
    ],
)
def test_the_hold_uri_never_names_a_host_and_carries_the_path_verbatim(given):
    """SQLite refuses a URI authority (`invalid uri authority`) unless built
    with SQLITE_ALLOW_URI_AUTHORITY, which CPython's bundled SQLite is not; a
    Windows UNC or `\\\\?\\` path must therefore never read as a host. And
    what SQLite reads back must be the caller's path, unnormalised."""
    uri = db_ctx._hold_open_uri(given)
    assert uri.startswith("file:") and uri.endswith("?mode=rw")
    after_scheme = uri[len("file:") :]
    if after_scheme.startswith("//"):
        authority = after_scheme[2:].split("/", 1)[0]
        assert authority == "", f"{given!r} became a URI with host {authority!r}"
    assert _uri_path(uri) == given


def test_an_open_that_fails_on_an_existing_database_is_not_reported_as_missing(
    tmp_path,
    monkeypatch,
):
    """Only a missing file becomes the 'does not exist' refusal. Any other
    failure to open an existing database -- an I/O or permission error -- is
    raised as itself, and the hold ends with nothing running."""
    path = str(tmp_path / "exists.db")
    _wal_database(path)

    def failing_connect(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db_ctx, "connect", failing_connect)

    async def _enter():
        async with hold_wal_open(path):
            pytest.fail("the open failed; the hold must not have entered")

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        asyncio.run(_enter())
    assert os.path.exists(path)
    assert not _hold_threads()


def test_a_hold_on_a_non_wal_database_is_refused_and_released(tmp_path, recorded_connections):
    """On a rollback-journal file an idle connection holds no lock, so the
    hold could not work; it says so instead of pretending."""
    path = str(tmp_path / "rollback.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()

    async def _enter():
        with pytest.raises(RuntimeError, match="not WAL"):
            async with hold_wal_open(path):
                pytest.fail("a hold on a non-WAL database must not open")
        # The hold's connection is the one opened with the setup budget.
        return [
            _is_closed(c["conn"]) for c in _ours(recorded_connections, path) if c["timeout"]
        ], _hold_threads()

    assert asyncio.run(_enter()) == ([True], [])
    _assert_nothing_holds(path)


def test_when_the_hold_ends_the_database_file_alone_holds_every_committed_row(
    db_path,
    tmp_path,
):
    """While held, committed work lives in the WAL until a checkpoint --
    SQLite's documented synchronous=NORMAL behaviour. The hold's own close is
    the last close, so it checkpoints: afterwards a copy of the database file
    by itself, with no WAL beside it, contains everything committed."""

    async def _burst():
        store = MemoryStore(db_path)
        async with hold_wal_open(db_path):
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

    async def _unheld():
        return await _ordinary_operations(MemoryStore(control_path))

    async def _held_burst():
        store = MemoryStore(db_path)
        async with hold_wal_open(db_path, label="ownership") as bound:
            assert bound is None, "the hold must not hand its connection to anyone"
            return await _ordinary_operations(store)

    unheld_outcomes = asyncio.run(_unheld())
    held_outcomes = asyncio.run(_held_burst())
    assert held_outcomes == unheld_outcomes

    unheld = _ours(recorded_connections, control_path)
    held_ops = _ours(recorded_connections, db_path)
    held = _held(recorded_connections, db_path)

    # Exactly one extra connection: the hold's own. Every operation opened its
    # own, just as it does outside a hold.
    assert len(held) == 1
    assert len(held_ops) == len(unheld) + 1
    assert len({id(c["conn"]) for c in held_ops}) == len(held_ops)

    # The held connection ran its setup and its two consumed reads, and
    # nothing else -- no operation ever executed a statement on it.
    assert held[0]["statements"] == HELD_STATEMENTS

    # No configuration bypass: every connection, held or not, was set up by
    # the shared policy under the setup budget before touching data.
    for entry in held_ops:
        assert entry["statements"][: len(SETUP_STATEMENTS)] == SETUP_STATEMENTS
        assert entry["timeout"] == db_ctx.SETUP_LOCK_TIMEOUT_S


def test_an_operation_inside_the_hold_runs_under_the_shared_policy(db_path):
    async def _pragmas_inside():
        async with hold_wal_open(db_path):
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


def test_nothing_survives_the_hold(db_path, recorded_connections):
    async def _work():
        store = MemoryStore(db_path)
        async with hold_wal_open(db_path):
            await store.upsert_memory("fact", "x", "a value", _now())
            held_inside = not _released_now(recorded_connections, db_path)
        return held_inside, _released_now(recorded_connections, db_path)

    held_inside, released_at_exit = asyncio.run(_work())
    assert held_inside, "inside the hold its connection should be open"
    assert released_at_exit, "the hold ended with its connection, thread or a handle still open"
    assert all(_is_closed(c["conn"]) for c in _ours(recorded_connections, db_path))
    _assert_nothing_holds(db_path)


def test_re_entering_opens_a_new_held_connection_rather_than_reusing_one(
    db_path,
    recorded_connections,
):
    async def _twice():
        released = []
        for _ in range(2):
            async with hold_wal_open(db_path):
                pass
            released.append(_released_now(recorded_connections, db_path))
        return released

    assert asyncio.run(_twice()) == [True, True]
    held = _held(recorded_connections, db_path)
    assert len(held) == 2
    assert held[0]["conn"] is not held[1]["conn"]


def test_a_hold_cannot_be_entered_twice(db_path):
    hold = hold_wal_open(db_path)
    with hold:
        pass
    with pytest.raises(RuntimeError, match="entered once"):
        with hold:
            pass


def _connection_like(value) -> bool:
    return isinstance(value, (aiosqlite.Connection, sqlite3.Connection))


def _connections_reachable_from(namespace: dict, depth: int = 3) -> list[str]:
    """Connection objects held by `namespace`, directly, inside containers (a
    registry dict, a list, a weak mapping) or on plain objects' attributes,
    `depth` levels down. Modules, classes and functions are not entered: they
    are code, not storage."""
    found = []

    def _walk(value, where, level):
        if _connection_like(value):
            found.append(where)
            return
        if level == 0 or isinstance(value, (str, bytes, type, types.ModuleType)) or callable(value):
            return
        if isinstance(value, Mapping):
            items = list(value.items())
        elif isinstance(value, (list, tuple, set, frozenset)):
            items = list(enumerate(value))
        elif hasattr(value, "__dict__"):
            # A plain object's attributes: a connection parked on an Event, a
            # thread or any helper the hold owns is just as reachable.
            items = list(vars(value).items())
        else:
            return
        for key, item in items:
            _walk(item, f"{where}[{key!r}]", level - 1)

    for name, value in list(namespace.items()):
        _walk(value, name, depth)
    return found


def test_nothing_anywhere_can_find_or_keep_the_held_connection(db_path):
    """A pool needs somewhere to keep connections. There is nowhere: not in
    module state (not even inside a container), not in a context variable, not
    on the hold object or the store -- during the hold or after it. The
    connection lives only on the hold's own thread's stack."""
    namespaces = {"db_ctx": db_ctx, "memory_store": memory_store_module}
    for module in namespaces.values():
        assert not [n for n, v in vars(module).items() if isinstance(v, contextvars.ContextVar)]

    def _anywhere(*objects):
        hits = []
        for label, module in namespaces.items():
            hits += [f"{label}.{w}" for w in _connections_reachable_from(vars(module))]
        for obj in objects:
            hits += [f"{type(obj).__name__}.{w}" for w in _connections_reachable_from(vars(obj))]
        return hits

    async def _look():
        store = MemoryStore(db_path)
        hold = hold_wal_open(db_path)
        async with hold:
            during = _anywhere(store, hold)
        return during, _anywhere(store, hold)

    assert asyncio.run(_look()) == ([], [])


def _call_sites(name: str, root: pathlib.Path) -> list[str]:
    sites = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called == name:
                sites.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
    return sites


#: Every production call site of `hold_wal_open`, exactly. Empty today:
#: adopting a hold in the product is its own reviewed decision (durability,
#: POSIX locks, multi-process access -- docs/SQLITE_WAL_HEADROOM_REPAIR.md
#: §3.3, §8), and whoever makes it must edit this list on purpose.
PRODUCTION_HOLD_SITES: list[str] = []


def test_no_production_code_holds_a_database_open():
    """An exact allowlist, not a pattern: a hold wrapped around a daemon's or a
    scheduler's lifetime would be the process-lifetime connection both
    2026-09-17 and 2026-09-18 decisions reject, and must not pass unnoticed."""
    assert _call_sites("hold_wal_open", REPO / "bartholomew") == PRODUCTION_HOLD_SITES
    assert _call_sites("WalHold", REPO / "bartholomew") == [
        f"bartholomew/kernel/db_ctx.py:{_line_of('return WalHold(')}",
    ]


def _line_of(fragment: str) -> int:
    lines = (REPO / "bartholomew/kernel/db_ctx.py").read_text(encoding="utf-8").splitlines()
    (number,) = [i for i, line in enumerate(lines, start=1) if fragment in line]
    return number


def test_the_hold_is_released_when_the_body_raises(db_path, recorded_connections):
    async def _raise():
        with pytest.raises(KeyError):
            async with hold_wal_open(db_path):
                raise KeyError("boom")
        return _released_now(recorded_connections, db_path)

    assert asyncio.run(_raise()) is True
    _assert_nothing_holds(db_path)


def test_the_hold_is_released_when_the_body_is_cancelled(db_path, recorded_connections):
    async def _cancelled():
        entered = asyncio.Event()

        async def _body():
            async with hold_wal_open(db_path):
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


class _SlowToCloseConnection(_CloseRecordingConnection):
    def close(self, *args, **kwargs):
        time.sleep(0.5)
        super().close(*args, **kwargs)


def test_repeated_cancellation_cannot_end_the_hold_before_its_connection_is_closed(
    db_path,
    recorded_connections,
    monkeypatch,
):
    """A cancellation that abandoned the wait would not stop the close; it
    would only let the hold end while the connection is still open. Here the
    held connection takes half a second to close and the task is cancelled
    again and again until it ends: when it ends, the connection is closed and
    its thread gone."""
    recording_connect = sqlite3.connect

    def slow_close_for_the_hold(database, *args, **kwargs):
        if kwargs.get("timeout") == db_ctx.SETUP_LOCK_TIMEOUT_S and _file_of(
            str(database),
        ) == _file_of(db_path):
            kwargs["factory"] = _SlowToCloseConnection
        return recording_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", slow_close_for_the_hold)

    async def _storm():
        entered = asyncio.Event()

        async def _body():
            async with hold_wal_open(db_path):
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


def test_a_cancellation_during_entry_waits_for_the_connection_it_started(
    db_path,
    recorded_connections,
):
    """Cancelled while its connection is still waiting on a lock to set up,
    the hold must not end with that connection left to open behind it. Cyclic
    GC is off, so nothing but the hold itself can release it."""
    release = _hold_exclusive(db_path, 1.0)
    gc.disable()
    try:

        async def _cancel_while_entering():
            async def _enter():
                async with hold_wal_open(db_path):
                    pytest.fail("the body must not run")

            task = asyncio.create_task(_enter())
            await asyncio.sleep(0.2)
            assert _hold_threads(), "the hold's setup should still be waiting on the lock"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return _released_now(recorded_connections, db_path)

        assert asyncio.run(_cancel_while_entering()) is True
    finally:
        gc.enable()
        release.join()
    _assert_nothing_holds(db_path)


def test_a_hold_whose_setup_fails_is_closed_and_the_error_raised(
    db_path,
    recorded_connections,
    monkeypatch,
):
    monkeypatch.setattr(db_ctx, "SETUP_LOCK_TIMEOUT_S", 0.2)
    release = _hold_exclusive(db_path, 1.5)
    try:

        async def _enter():
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                async with hold_wal_open(db_path):
                    pytest.fail("setup cannot have succeeded under the hold")
            # The exclusive holder is a plain connection (no timeout); the hold's is not.
            return [c for c in _ours(recorded_connections, db_path) if c["timeout"]]

        (seam,) = asyncio.run(_enter())
        assert _is_closed(seam["conn"]) and not _hold_threads()
    finally:
        release.join()


def test_a_hold_can_be_ended_from_another_task(db_path, recorded_connections):
    async def _two_tasks():
        hold = hold_wal_open(db_path)
        await asyncio.create_task(hold.__aenter__())
        assert _wal_exists(db_path)
        await asyncio.create_task(hold.__aexit__(None, None, None))
        return _released_now(recorded_connections, db_path)

    assert asyncio.run(_two_tasks()) is True


def test_an_abandoned_hold_never_keeps_the_process_alive_and_is_released_when_collected(
    db_path,
):
    """If a hold is never exited -- its event loop closed or its task destroyed
    while it is open -- nothing may keep the process from exiting (a worker that
    cannot exit is exactly the "not properly terminated" this repair is about),
    and the file must be released once the hold is collected."""
    script = textwrap.dedent(
        f"""
        import asyncio, gc, os, sys, time
        sys.path.insert(0, {str(REPO)!r})
        from bartholomew.kernel.db_ctx import hold_wal_open

        path = {db_path!r}

        async def enter():
            hold = hold_wal_open(path, label="abandoned")
            await hold.__aenter__()
            return hold

        loop = asyncio.new_event_loop()
        hold = loop.run_until_complete(enter())
        loop.close()  # the hold is never exited
        assert os.path.exists(path + "-wal"), "the hold should be holding the file"
        del hold
        gc.collect()
        deadline = time.monotonic() + 10
        while os.path.exists(path + "-wal") and time.monotonic() < deadline:
            time.sleep(0.02)
        print("RELEASED" if not os.path.exists(path + "-wal") else "STILL-HELD")
        """,
    )
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "RELEASED" in result.stdout, result.stdout + result.stderr
    assert time.monotonic() - started < 60


def test_a_hold_still_open_at_interpreter_exit_does_not_block_the_exit(db_path):
    """The case collection cannot rescue: a hold that is never exited and is
    still referenced when the interpreter shuts down. Python joins non-daemon
    threads before it runs any finalizer, so only a daemon thread lets such a
    process end -- an xdist worker that cannot end is the failure this repair
    exists to remove."""
    script = textwrap.dedent(
        f"""
        import asyncio, sys
        sys.path.insert(0, {str(REPO)!r})
        from bartholomew.kernel.db_ctx import hold_wal_open

        HOLD = hold_wal_open({db_path!r}, label="open at exit")

        async def enter():
            await HOLD.__aenter__()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(enter())
        loop.close()
        print("EXITING")
        """,
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "EXITING" in result.stdout


# ---------------------------------------------------------------------------
# 4. No sharing across tasks, threads or event loops
# ---------------------------------------------------------------------------


def test_concurrent_tasks_threads_and_loops_while_held_use_their_own_connections(
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
        async with hold_wal_open(db_path, label="concurrency"):
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
    operation inside the hold sees that write immediately, and a TRUNCATE
    checkpoint -- which waits for any reader holding a snapshot -- completes
    and empties the WAL."""

    async def _probe():
        store = MemoryStore(db_path)
        async with hold_wal_open(db_path):
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
    assert busy == 0, "something in the hold held a read snapshot"
    assert wal_size == 0, "the TRUNCATE checkpoint could not reset the WAL"
    assert seen is not None, "an operation inside the hold read a stale snapshot"


def test_an_uncommitted_write_inside_the_hold_does_not_persist(db_path):
    async def _uncommitted():
        store = MemoryStore(db_path)
        async with hold_wal_open(db_path):
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


def test_a_failed_operation_inside_the_hold_leaves_nothing_behind(db_path, monkeypatch):
    """A write that fails after its INSERT, before its commit: the row must not
    land, and the next operation inside the same hold must not commit it."""
    real_reindex = memory_store_module.reindex_memory_fts_async

    async def failing_reindex(db, memory_id, text):
        raise RuntimeError("index write failed")

    async def _fail_then_succeed():
        store = MemoryStore(db_path)
        async with hold_wal_open(db_path):
            monkeypatch.setattr(memory_store_module, "reindex_memory_fts_async", failing_reindex)
            with pytest.raises(RuntimeError):
                await store.upsert_memory("fact", "half", "a value", _now())
            monkeypatch.setattr(memory_store_module, "reindex_memory_fts_async", real_reindex)
            await store.upsert_memory("fact", "whole", "a value", _now())

    asyncio.run(_fail_then_succeed())
    assert _count(db_path, "SELECT count(*) FROM memories WHERE key = 'half'") == 0
    assert _count(db_path, "SELECT count(*) FROM memories WHERE key = 'whole'") == 1


# ---------------------------------------------------------------------------
# 6. Governance decides exactly as it does without a hold
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


def test_governed_outcomes_are_identical_with_and_without_a_hold(db_path, tmp_path):
    control_path = str(tmp_path / "control.db")
    asyncio.run(MemoryStore(control_path).init())

    async def _unheld():
        return await _governed_script(MemoryStore(control_path))

    async def _held_script():
        store = MemoryStore(db_path)
        async with hold_wal_open(db_path, label="governance"):
            return await _governed_script(store)

    unheld = asyncio.run(_unheld())
    held = asyncio.run(_held_script())
    assert held == unheld
    assert unheld[:5] == ["stored", "queued_for_consent", "refused", True, "refused_revoked"]
    assert _stored_state(db_path) == _stored_state(control_path)
    # The consent inbox is still encrypted at rest while held.
    assert all(row[4] for row in _stored_state(db_path)["pending"])


# ---------------------------------------------------------------------------
# 7. The repair is declared where the evidence put it -- structurally
# ---------------------------------------------------------------------------

#: (file, test, the call that marks the burst loop, the scope that must enclose it)
ADOPTED = [
    (
        "tests/test_memory_agency_review_fixes.py",
        "test_queued_outcome_is_independent_of_inbox_size",
        "upsert_memory",
        "hold_wal_open",
    ),
    (
        "tests/integration/test_fts_unavailable_vector_quality.py",
        "test_vector_quality_maintained_when_fts_unavailable",
        "upsert_memory",
        "hold_wal_open",
    ),
    (
        "tests/integration/test_fts_unavailable_vector_quality.py",
        "test_vector_quality_maintained_when_fts_unavailable",
        "upsert",
        "db_session",
    ),
    (
        "tests/test_learning_memory_control_centre.py",
        "test_b6d_the_material_field_vocabulary_is_enforced_not_documented",
        "run_candidate_edit_through_runtime_contract",
        "hold_wal_open",
    ),
]


def _called_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


@pytest.mark.parametrize(
    "relative, test_name, burst_call, scope",
    ADOPTED,
    # Explicit ids: conftest marks any test whose name contains "integration"
    # as an integration test, which the default suite deselects.
    ids=[f"{pathlib.Path(a[0]).stem}-{a[2]}" for a in ADOPTED],
)
def test_each_burst_killed_on_windows_is_declared(relative, test_name, burst_call, scope):
    """The three tests whose workers were killed inside a last-close teardown
    on Merge Candidate 36153552522. Removing the declaration reinstates the
    defect silently -- the tests still pass locally -- so it is pinned here,
    structurally: the burst's loop must sit lexically inside a `with` or
    `async with` whose context expression is the scope call. A scope that is
    created but never entered does not count."""
    tree = ast.parse((REPO / relative).read_text(encoding="utf-8"))
    (function,) = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == test_name
    ]
    parents = {
        child: parent for parent in ast.walk(function) for child in ast.iter_child_nodes(parent)
    }

    loops = [
        n
        for n in ast.walk(function)
        if isinstance(n, (ast.For, ast.AsyncFor))
        and any(_called_name(c) == burst_call for c in ast.walk(n))
    ]
    assert loops, f"no burst loop calling {burst_call}() in {relative}::{test_name}"

    def _enclosed(node: ast.AST) -> bool:
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.With, ast.AsyncWith)) and any(
                _called_name(item.context_expr) == scope for item in node.items
            ):
                return True
        return False

    outermost = [
        loop for loop in loops if not any(loop is not o and loop in ast.walk(o) for o in loops)
    ]
    assert all(
        _enclosed(loop) for loop in outermost
    ), f"{relative}::{test_name}: the {burst_call}() burst is not inside `with {scope}(...)`"
