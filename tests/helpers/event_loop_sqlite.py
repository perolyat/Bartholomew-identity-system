"""
Detect SQLite statements that wait for a lock while running on the event-loop
thread.

Why this is worth detecting (Windows writer-lock / WAL reliability repair,
2026-09): the kernel's `MemoryStore` writes through aiosqlite, whose `execute`
and `commit` are two separate event-loop round trips. Between them the write
lock is held by aiosqlite's worker thread, and the only thing that can release
it is the event loop submitting the commit. A synchronous sqlite3 write made
*on* the event-loop thread inside that window blocks the loop while waiting for
a lock whose release needs the loop it is blocking -- so it waits its whole
`busy_timeout` and then fails with ``database is locked``. The same write made
off the loop waits a few milliseconds.

The rule this helper enforces is therefore structural, not statistical: a
statement that can wait for the write lock must never execute on a thread that
is running an event loop. Wrap a flow in `forbid_sqlite_writes_on_event_loop()`
and assert that `log.production_writes()` is empty.

What counts as a write: INSERT/UPDATE/DELETE/REPLACE, DDL, `BEGIN IMMEDIATE`/
`EXCLUSIVE`, any `executemany`/`executescript` carrying one of those, and
`PRAGMA wal_checkpoint` (which waits for other connections). `PRAGMA
journal_mode` is deliberately not counted: on a database already in WAL mode it
is answered from a read transaction (the fresh-file conversion race is a
separate defect with its own repair in `db_ctx.set_wal_pragmas`).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sqlite3
import traceback
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_WRITE_RE = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|VACUUM|REINDEX|"
    r"BEGIN\s+(IMMEDIATE|EXCLUSIVE)|PRAGMA\s+wal_checkpoint)\b",
    re.IGNORECASE,
)
_SCRIPT_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OnLoopSqliteWrite:
    """One statement that waited (or could have waited) for a lock on a loop thread."""

    kind: str
    sql: str
    frames: tuple[str, ...]

    def __str__(self) -> str:
        where = " <- ".join(reversed(self.frames)) if self.frames else "(no production frame)"
        return f"[{self.kind}] {self.sql[:90]!r}\n      via {where}"


@dataclass
class OnLoopWriteLog:
    writes: list[OnLoopSqliteWrite] = field(default_factory=list)

    def production_writes(self) -> list[OnLoopSqliteWrite]:
        """Writes issued from production code (a test calling a store directly
        on the loop is the test's business, not the product's)."""
        return [w for w in self.writes if w.frames]

    def production_writes_through(self, *path_fragments: str) -> list[OnLoopSqliteWrite]:
        """Writes whose call chain passes through any of the given path fragments."""
        return [
            w
            for w in self.production_writes()
            if any(fragment in frame for fragment in path_fragments for frame in w.frames)
        ]

    def production_writes_from(self, *path_fragments: str) -> list[OnLoopSqliteWrite]:
        """Writes whose innermost production frame -- the writer itself -- is in
        one of the given modules, whatever called it."""
        return [
            w
            for w in self.production_writes()
            if any(fragment in w.frames[-1] for fragment in path_fragments)
        ]

    def describe(self) -> str:
        return "\n".join(str(w) for w in self.production_writes()) or "(none)"


def _on_event_loop_thread() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _production_frames() -> tuple[str, ...]:
    frames: list[str] = []
    for frame in traceback.extract_stack()[:-3]:
        if "site-packages" in frame.filename:
            continue
        try:
            rel = Path(frame.filename).resolve().relative_to(REPO_ROOT)
        except (ValueError, OSError):
            continue
        parts = rel.parts
        if not parts or parts[0] in {"tests", "conftest.py"} or rel.name.startswith("test_"):
            continue
        frames.append(f"{rel.as_posix()}:{frame.lineno} {frame.name}")
    return tuple(frames)


@contextlib.contextmanager
def forbid_sqlite_writes_on_event_loop() -> Iterator[OnLoopWriteLog]:
    """Record every lock-waiting SQLite statement executed on an event-loop thread.

    Patches `sqlite3.connect` so every connection opened inside the block --
    through `db_ctx.connect()`, a bare `sqlite3.connect()`, or aiosqlite's own
    connector -- reports the statements it executes while the calling thread
    has a running event loop. aiosqlite's statements run on its worker thread
    and are never reported; `asyncio.to_thread` / `SingleWorkerExecutor` work
    likewise. Only the loop thread itself is watched.
    """
    log = OnLoopWriteLog()
    original_connect = sqlite3.connect

    class _Watching(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):  # type: ignore[override]
            if isinstance(sql, str) and _WRITE_RE.match(sql) and _on_event_loop_thread():
                log.writes.append(OnLoopSqliteWrite("execute", sql, _production_frames()))
            return super().execute(sql, *args, **kwargs)

        def executemany(self, sql, *args, **kwargs):  # type: ignore[override]
            if _on_event_loop_thread():
                log.writes.append(
                    OnLoopSqliteWrite("executemany", str(sql), _production_frames()),
                )
            return super().executemany(sql, *args, **kwargs)

        def executescript(self, sql, *args, **kwargs):  # type: ignore[override]
            if isinstance(sql, str) and _SCRIPT_WRITE_RE.search(sql) and _on_event_loop_thread():
                log.writes.append(OnLoopSqliteWrite("executescript", sql, _production_frames()))
            return super().executescript(sql, *args, **kwargs)

    def watching_connect(*args, **kwargs):
        kwargs.setdefault("factory", _Watching)
        return original_connect(*args, **kwargs)

    sqlite3.connect = watching_connect  # type: ignore[assignment]
    try:
        yield log
    finally:
        sqlite3.connect = original_connect  # type: ignore[assignment]
