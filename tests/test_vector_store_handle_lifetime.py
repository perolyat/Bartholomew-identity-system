"""VectorStore releases every database handle when a call returns.

Why this exists. On CPython 3.11 a `sqlite3.Connection` sits in a reference
cycle with its own statement cache, so `with sqlite3.connect(...) as conn:`
-- which commits on exit but never closes -- leaves the connection, and the
`.db` / `-wal` / `-shm` handles under it, open until the cyclic garbage
collector happens to run. POSIX lets a process delete a file it still has
open; Windows does not. Every `TemporaryDirectory` teardown that followed a
VectorStore call therefore raced the collector, and on Windows lost:
`WinError 32`, ten tests, all in files this store's callers never touched.

The store now goes through `db_ctx.wal_db()`, the repository's own pattern,
which closes in `finally`. These tests disable the cyclic collector so that
only prompt release counts -- the collector is exactly what used to hide the
defect on Linux.
"""

from __future__ import annotations

import contextlib
import gc
import os
import re
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from bartholomew.kernel import vector_store as vector_store_module
from bartholomew.kernel.embedding_engine import KIND_DETERMINISTIC
from bartholomew.kernel.vector_store import VectorStore


def _handles_held_on(db_path: str) -> list[str]:
    """Which of `db_path`'s files this process still holds open.

    On Linux the kernel says so directly. Elsewhere the question is asked the
    way Windows answers it: a file that is still open cannot be renamed. That
    probe is vacuous on macOS, where renaming an open file is legal, so there
    the first test below passes without proving anything; the Linux and
    Windows runners are the ones that carry the assertion.
    """
    stem = os.path.basename(db_path)
    if os.path.isdir("/proc/self/fd"):
        held = []
        for fd in os.listdir("/proc/self/fd"):
            try:
                target = os.readlink(f"/proc/self/fd/{fd}")
            except OSError:
                continue
            if os.path.basename(target).startswith(stem):
                held.append(os.path.basename(target))
        return sorted(held)

    held = []
    for candidate in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
        if not os.path.exists(candidate):
            continue
        probe = f"{candidate}.probe"
        try:
            os.rename(candidate, probe)
        except OSError:
            held.append(os.path.basename(candidate))
        else:
            os.rename(probe, candidate)
    return sorted(held)


@pytest.fixture
def db_path(tmp_path) -> str:
    """A database with the one parent row an embedding's foreign key needs.

    Opened and closed explicitly, so the fixture cannot itself be the holder.
    """
    path = str(tmp_path / "vectors.db")
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO memories (id) VALUES (1)")
    return path


def test_every_call_releases_its_handles_before_returning(db_path):
    """Construction, a write, a delete and both reads -- six of the store's
    seven connection sites -- with the collector off, so a connection that
    is merely *unreachable* still counts as held."""
    vec = np.ones(384, dtype=np.float32)
    vec /= np.linalg.norm(vec)

    was_enabled = gc.isenabled()
    gc.disable()
    try:
        store = VectorStore(db_path)
        assert _handles_held_on(db_path) == [], "construction left a handle open"

        store.upsert(
            memory_id=1,
            vec=vec,
            source="full",
            provider="deterministic-hash",
            model="deterministic-hash",
            embedder_kind=KIND_DETERMINISTIC,
        )
        assert _handles_held_on(db_path) == [], "upsert left a handle open"

        assert store.count() == 1
        assert store.count_by_kind() == {KIND_DETERMINISTIC: 1}
        assert _handles_held_on(db_path) == [], "a read left a handle open"

        store.delete_for_memory(1)
        assert store.count() == 0
        assert _handles_held_on(db_path) == [], "delete left a handle open"
    finally:
        if was_enabled:
            gc.enable()


def test_the_store_opens_no_raw_sqlite_connection():
    """Structural, mirroring the API layer's guard: the only way this module
    reaches the database is `db_ctx.wal_db()`, which is what closes it. A
    `sqlite3.Connection` type hint is fine; a `sqlite3.connect(` call is the
    defect coming back."""
    source = Path(vector_store_module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"sqlite3\s*\.\s*connect\s*\(", source)
    assert "wal_db(" in source


def test_the_directory_can_be_removed_the_moment_the_store_is_done(tmp_path):
    """The symptom itself: after using the store, its directory is deletable
    without waiting for the collector. Only Windows can fail this -- POSIX
    deletes open files without complaint -- so it runs everywhere and is
    load-bearing on the one platform that showed the defect."""
    import shutil

    workdir = tmp_path / "work"
    workdir.mkdir()
    path = str(workdir / "vectors.db")
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY)")

    was_enabled = gc.isenabled()
    gc.disable()
    try:
        store = VectorStore(path)
        store.count()
        shutil.rmtree(workdir)
    finally:
        if was_enabled:
            gc.enable()
    assert not workdir.exists()
