"""
MemoryStore's connection contract (Windows reliability incident, 2026-09-24).

`synchronous`, `foreign_keys` and `busy_timeout` are connection-local in
SQLite. MemoryStore declared `synchronous=NORMAL` and `foreign_keys=ON` in its
SCHEMA, which configured the init() connection only; every operational
connection was a bare `aiosqlite.connect()` and ran on SQLite's defaults --
synchronous=FULL, foreign_keys=OFF, and a 5 s lock budget from its very first
statement. A connection's first lock acquisition is the one statement a
concurrent last-close checkpoint can block, and on Windows Merge Candidate run
35971892908 that is where a seed's `database is locked` was raised.

The contract these tests hold MemoryStore to is the shared authority's
(`bartholomew.kernel.db_ctx`):

* setup runs under the 30 s setup lock budget, including the one setup
  statement that needs a database lock (`synchronous`);
* only once setup has succeeded does the connection drop to the 5 s
  operational `busy_timeout`;
* `foreign_keys=ON` and `synchronous=NORMAL` are in force before any data
  operation;
* there is no other way for MemoryStore to open a connection;
* a connection is still owned by one unit of work and closed when it ends.
"""

from __future__ import annotations

import ast
import asyncio
import os
import pathlib
import sqlite3
import threading
import time
from datetime import datetime, timezone

import aiosqlite
import pytest

from bartholomew.kernel import db_ctx
from bartholomew.kernel import memory_store as memory_store_module
from bartholomew.kernel.memory_store import MemoryStore, open_memory_db, open_memory_db_sync

MEMORY_STORE_SOURCE = pathlib.Path(memory_store_module.__file__)

#: Longer than the operational budget, well inside the setup budget. The
#: margin over 5 s is deliberate: SQLite's busy handler budgets its *intended*
#: sleeps, not wall time, so on a loaded Windows runner a 5 s handler was seen
#: to give up at 6.3 s (Merge Candidate 36118579395). 9 s keeps a bare
#: connection's failure well before the release.
HOLD_BEYOND_OPERATIONAL_S = 9.0

SETUP_STATEMENTS = [*db_ctx.CONNECTION_SETUP_PRAGMAS, db_ctx.OPERATIONAL_BUSY_TIMEOUT_PRAGMA]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "contract.db")
    asyncio.run(MemoryStore(path).init())
    return path


@pytest.fixture
def recorded_connections(monkeypatch):
    """Every sqlite3 connection opened while the test runs, and every statement
    it executed from its very first one.

    aiosqlite opens its connection by calling `sqlite3.connect` on its worker
    thread, so patching that one function sees both of MemoryStore's seams. The
    trace callback is installed before the connection is returned to anyone,
    so nothing the seam runs can precede the recording.
    """
    original = sqlite3.connect
    opened: list[dict] = []

    def recording_connect(database, *args, **kwargs):
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


def _hold_exclusive(path: str, seconds: float) -> threading.Thread:
    """Hold an EXCLUSIVE lock on the database file -- the lock a last-close
    checkpoint takes -- and release it after `seconds`."""
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


async def _pragmas(db) -> dict[str, int]:
    values = {}
    for name in ("synchronous", "foreign_keys", "busy_timeout"):
        cursor = await db.execute(f"PRAGMA {name}")
        values[name] = (await cursor.fetchone())[0]
    return values


# ---------------------------------------------------------------------------
# 1. The contending setup statement has the setup budget.
# ---------------------------------------------------------------------------


def test_the_contending_setup_statement_waits_on_the_setup_budget(db_path):
    """A lock held longer than the operational budget, shorter than the setup
    one, is waited out during setup -- which is exactly the window the
    Merge Candidate seed failed in after 5 s."""
    release = _hold_exclusive(db_path, HOLD_BEYOND_OPERATIONAL_S)
    started = time.monotonic()
    try:

        async def _open():
            async with open_memory_db(db_path) as db:
                return await _pragmas(db)

        values = asyncio.run(_open())
    finally:
        release.join()
    waited = time.monotonic() - started

    assert waited >= HOLD_BEYOND_OPERATIONAL_S - 0.5, (
        f"setup returned after {waited:.2f}s: the hold was not in force, so this "
        "test proved nothing"
    )
    assert values == {"synchronous": 1, "foreign_keys": 1, "busy_timeout": 5000}


def test_a_default_connection_would_have_failed_under_the_same_hold(db_path):
    """The control for the test above: the connection MemoryStore used to open
    gives up at 5 s under the identical hold. Without this, a hold that never
    engaged would let the setup-budget test pass for the wrong reason."""
    release = _hold_exclusive(db_path, HOLD_BEYOND_OPERATIONAL_S)
    try:

        async def _open_bare():
            async with aiosqlite.connect(db_path) as db:
                await db.execute("PRAGMA synchronous = NORMAL")

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            asyncio.run(_open_bare())
        # Causal, not a stopwatch: the bare connection gave up while the hold
        # was still in force. (A wall-clock ceiling here failed on a loaded
        # Windows runner whose 5 s busy handler ran to 6.3 s.)
        hold_still_in_force = release.is_alive()
    finally:
        release.join()
    assert hold_still_in_force, "the hold was released before the bare connection gave up"


# ---------------------------------------------------------------------------
# 2. After setup, the connection runs on the operational budget.
# ---------------------------------------------------------------------------


def test_after_setup_the_connection_runs_on_the_operational_budget(db_path):
    """Reported *and* behaved: a write that meets another writer gives up at
    the 5 s operational budget, not the 30 s setup one."""
    other_writer = sqlite3.connect(db_path, check_same_thread=False)

    async def _write_behind_another_writer():
        async with open_memory_db(db_path) as db:
            reported = (await _pragmas(db))["busy_timeout"]
            other_writer.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    await db.execute(
                        "INSERT INTO system_flags(key, value, updated_at) VALUES ('probe','1','0')",
                    )
                return reported, time.monotonic() - started
            finally:
                other_writer.rollback()

    try:
        reported, waited = asyncio.run(_write_behind_another_writer())
    finally:
        other_writer.close()

    assert reported == db_ctx.OPERATIONAL_BUSY_TIMEOUT_MS == 5000
    # Tells the 5 s operational budget from the 30 s setup one, with room for a
    # loaded runner's busy-handler overshoot on either side of the midpoint.
    assert (
        4.0 <= waited < db_ctx.SETUP_LOCK_TIMEOUT_S / 2
    ), f"an operational statement waited {waited:.2f}s"


# ---------------------------------------------------------------------------
# 3. Settings are effective before any data operation, in the authority's order.
# ---------------------------------------------------------------------------


def test_setup_runs_first_and_drops_to_the_operational_budget_last(db_path, recorded_connections):
    async def _one_operation():
        async with open_memory_db(db_path) as db:
            await db.execute("SELECT count(*) FROM memories")

    asyncio.run(_one_operation())

    (conn,) = recorded_connections
    assert conn["timeout"] == db_ctx.SETUP_LOCK_TIMEOUT_S == 30.0
    assert conn["statements"][: len(SETUP_STATEMENTS)] == SETUP_STATEMENTS
    assert conn["statements"][len(SETUP_STATEMENTS)] == "SELECT count(*) FROM memories"


def test_the_sync_seam_has_the_same_contract(db_path, recorded_connections):
    with open_memory_db_sync(db_path) as conn:
        values = {
            name: conn.execute(f"PRAGMA {name}").fetchone()[0]
            for name in ("synchronous", "foreign_keys", "busy_timeout")
        }

    (entry,) = recorded_connections
    assert entry["timeout"] == db_ctx.SETUP_LOCK_TIMEOUT_S
    assert entry["statements"][: len(SETUP_STATEMENTS)] == SETUP_STATEMENTS
    assert values == {"synchronous": 1, "foreign_keys": 1, "busy_timeout": 5000}


def test_the_seams_apply_exactly_the_shared_authoritys_setup(db_path):
    """One policy, not two: a MemoryStore connection and a db_ctx connection
    report the same connection-local configuration."""
    with db_ctx.wal_db(db_path) as authority:
        expected = {
            name: authority.execute(f"PRAGMA {name}").fetchone()[0]
            for name in ("synchronous", "foreign_keys", "busy_timeout")
        }

    async def _seam():
        async with open_memory_db(db_path) as db:
            return await _pragmas(db)

    assert asyncio.run(_seam()) == expected


def test_every_connection_memory_store_opens_is_configured_first(db_path, recorded_connections):
    """Across ordinary MemoryStore operations -- async and the off-loop sync
    chunk path alike -- every connection to the store's file begins with the
    setup and ends it on the operational budget before touching data."""

    async def _ordinary_use():
        store = MemoryStore(db_path)
        long_text = " ".join(f"sentence {i} about the garden shed." for i in range(400))
        await store.upsert_memory("fact", "contract_short", "the bin goes out on Thursdays", _now())
        await store.upsert_memory("fact", "contract_long", long_text, _now())
        await store.get_memory("fact", "contract_short")
        await store.list_memories()
        await store.create_nudge("check", "a nudge", [], "reason", _now())
        await store.list_pending_nudges()
        await store.delete_memory("fact", "contract_short")

    asyncio.run(_ordinary_use())

    ours = [c for c in recorded_connections if c["database"] == db_path]
    assert len(ours) >= 6, "the operations above should open several connections"
    for conn in ours:
        assert conn["statements"][: len(SETUP_STATEMENTS)] == SETUP_STATEMENTS, conn["statements"][
            :5
        ]
        assert conn["timeout"] == db_ctx.SETUP_LOCK_TIMEOUT_S


# ---------------------------------------------------------------------------
# 4. No raw MemoryStore connection path can bypass the seam.
# ---------------------------------------------------------------------------


def _enclosing_function(tree: ast.AST, target: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(child is target for child in ast.walk(node)):
                inner = [
                    n
                    for n in ast.walk(node)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n is not node
                    and any(c is target for c in ast.walk(n))
                ]
                if not inner:
                    return node.name
    return None


def test_memory_store_has_no_connection_path_outside_the_seam():
    tree = ast.parse(MEMORY_STORE_SOURCE.read_text(encoding="utf-8"))
    seams = {"open_memory_db", "open_memory_db_sync"}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("aiosqlite", "sqlite3"):
            if any(alias.name == "connect" for alias in node.names):
                offenders.append(f"line {node.lineno}: from {node.module} import connect")
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "connect"
                and isinstance(func.value, ast.Name)
                and func.value.id in ("aiosqlite", "sqlite3")
            ):
                owner = _enclosing_function(tree, node)
                if owner not in seams:
                    offenders.append(f"line {node.lineno}: {func.value.id}.connect in {owner}()")
    assert not offenders, (
        "MemoryStore opened a connection outside open_memory_db()/open_memory_db_sync(); "
        "it would run on SQLite's defaults (synchronous=FULL, foreign_keys=OFF, a 5 s "
        "lock budget from its first statement): " + "; ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 5. Ownership and explicit close are unchanged.
# ---------------------------------------------------------------------------


def _is_closed(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def _open_handles_to(path: str) -> list[str]:
    fd_dir = pathlib.Path("/proc/self/fd")
    if not fd_dir.exists():
        return []
    handles = []
    for fd in fd_dir.iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith(path):
            handles.append(target)
    return handles


def test_every_connection_is_closed_when_its_unit_of_work_ends(db_path, recorded_connections):
    async def _work():
        store = MemoryStore(db_path)
        await store.upsert_memory("fact", "closing", "a value", _now())
        await store.get_memory("fact", "closing")
        await store.delete_memory("fact", "closing")

    asyncio.run(_work())
    ours = [c for c in recorded_connections if c["database"] == db_path]
    assert ours
    assert all(_is_closed(c["conn"]) for c in ours)
    assert _open_handles_to(db_path) == []
    # On Windows an open handle makes this raise; everywhere it must succeed.
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(db_path + suffix):
            os.remove(db_path + suffix)


def test_a_connection_whose_setup_fails_is_closed_and_the_error_raised(
    db_path,
    recorded_connections,
    monkeypatch,
):
    """Setup has a bound: past it the caller gets the error, and the half-set-up
    connection does not linger holding a handle."""
    monkeypatch.setattr(db_ctx, "SETUP_LOCK_TIMEOUT_S", 0.2)
    release = _hold_exclusive(db_path, 3.0)
    try:

        async def _open():
            async with open_memory_db(db_path):
                pytest.fail("setup cannot have succeeded under the hold")

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            asyncio.run(_open())
    finally:
        release.join()
    # The holder is a plain connection (no timeout argument); the seam's is not.
    seam = [c for c in recorded_connections if c["database"] == db_path and c["timeout"]]
    assert len(seam) == 1 and _is_closed(seam[0]["conn"])


def test_the_sync_seam_closes_on_error_too(db_path, recorded_connections):
    with pytest.raises(sqlite3.OperationalError):
        with open_memory_db_sync(db_path) as conn:
            conn.execute("SELECT * FROM no_such_table")
    (entry,) = recorded_connections
    assert _is_closed(entry["conn"])


# ---------------------------------------------------------------------------
# Foreign keys: enforced, compatible, and not retroactive.
# ---------------------------------------------------------------------------


def test_an_invalid_relationship_is_refused_on_an_operational_connection(db_path):
    """memory_consent and memory_chunks reference memories(id). An operational
    connection used to accept a row pointing at no memory at all."""

    async def _orphan(table_sql):
        async with open_memory_db(db_path) as db:
            await db.execute(table_sql)
            await db.commit()

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        asyncio.run(
            _orphan("INSERT INTO memory_consent (memory_id, source) VALUES (987654, 'probe')"),
        )
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        asyncio.run(
            _orphan(
                "INSERT INTO memory_chunks (memory_id, seq, token_start, token_end, text) "
                "VALUES (987654, 0, 0, 1, 'x')",
            ),
        )


def test_deleting_a_memory_still_removes_its_dependent_rows(db_path):
    async def _scenario():
        store = MemoryStore(db_path)
        result = await store.upsert_memory("fact", "cascade", "a fact to delete", _now())
        async with open_memory_db(db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO memory_consent (memory_id, source) VALUES (?, 'probe')",
                (result.memory_id,),
            )
            await db.commit()
        assert await store.delete_memory("fact", "cascade")
        async with open_memory_db(db_path) as db:
            cursor = await db.execute(
                "SELECT count(*) FROM memory_consent WHERE memory_id = ?",
                (result.memory_id,),
            )
            return (await cursor.fetchone())[0]

    assert asyncio.run(_scenario()) == 0


def test_a_database_that_already_holds_an_orphan_still_works(db_path):
    """Enforcement is not retroactive, and must not become an outage: a file
    written while operational connections ran with foreign_keys=OFF may hold
    orphans. It still opens and serves ordinary operations, and SQLite's own
    check still reports the orphan."""
    legacy = sqlite3.connect(db_path)
    legacy.execute("PRAGMA foreign_keys = OFF")
    legacy.execute("INSERT INTO memory_consent (memory_id, source) VALUES (424242, 'legacy')")
    legacy.commit()
    legacy.close()

    async def _ordinary_use():
        store = MemoryStore(db_path)
        await store.init()
        await store.upsert_memory("fact", "after_orphan", "still works", _now())
        found = await store.get_memory("fact", "after_orphan")
        assert await store.delete_memory("fact", "after_orphan")
        async with open_memory_db(db_path) as db:
            cursor = await db.execute("PRAGMA foreign_key_check(memory_consent)")
            return found, await cursor.fetchall()

    found, violations = asyncio.run(_ordinary_use())
    assert found is not None
    assert [row[0] for row in violations] == ["memory_consent"]
