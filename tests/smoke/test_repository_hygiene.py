"""
Repository hygiene contract.

Runtime state must never be tracked in Git. This repository is public, and
until 2026-08-17 it carried two live SQLite databases -- `data/barth.db` and
`data/memory.db` -- committed in PR #5 and never removed. `.gitignore` has
excluded `data/**/*.db` the whole time, which had no effect: ignore rules
only govern *untracked* files, so files already in the index stay there
silently.

The audit found no credentials or secrets in them (their nine episodic rows
were Fernet-encrypted with a key held in an OS keystore that is not in the
repository), so the exposure was not the harm. The harm was that ordinary
use mutated tracked files:

  - `ModelRouter` builds a `BudgetLedger` at `data/barth.db` and creates its
    `cloud_spend` table on the first routing decision, so simply chatting
    produced a modified tracked database;
  - `test_memory_functionality.py` constructed a `MemoryManager` against the
    default `./data`, whose `_cleanup_expired_memories()` DELETEd all nine
    rows because they expired in January 2026 -- running the test suite
    destroyed repository content.

Either one is a single `git commit -a` away from pushing personal runtime
state to a public repository. This test is the standing guard against a
database being re-added.

Deliberately cheap: one `git ls-files` call, no network, no DB, no server.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Suffixes that indicate SQLite runtime state rather than source.
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm")


@pytest.mark.smoke
def test_no_sqlite_databases_are_tracked() -> None:
    """No SQLite database (or WAL/SHM sidecar) is tracked in Git."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"git ls-files unavailable: {result.stderr.strip()}")

    tracked = [p for p in result.stdout.split("\0") if p]
    offenders = sorted(p for p in tracked if p.endswith(_DB_SUFFIXES))

    assert not offenders, (
        "SQLite runtime state is tracked in Git: "
        f"{offenders}. Databases are created at runtime and are excluded by "
        ".gitignore -- but .gitignore does not untrack a file that is already "
        "in the index. Remove it with `git rm --cached <path>` (which keeps "
        "your local copy) rather than relaxing this test."
    )


@pytest.mark.smoke
def test_memory_manager_data_dir_follows_barth_db_path(monkeypatch, tmp_path):
    """`BARTH_DB_PATH` must isolate `MemoryManager` too, not just the kernel.

    `MemoryManager`'s `data_dir` used to default to a hardcoded `"./data"`,
    which `BARTH_DB_PATH` did not influence. Constructing one runs
    `_init_database()` -> `_cleanup_expired_memories()`, which DELETEs every
    row past its TTL -- so a test that merely built a `ReflectionGenerator`
    (which builds a `ContextBuilder`, which builds a `MemoryManager`) emptied
    the developer's live `data/memory.db` while `BARTH_DB_PATH` pointed
    somewhere else entirely. Measured: 9 rows -> 0 from
    `tests/test_reflection_generation.py` alone.

    This asserts the resolver, not the filesystem, so it stays fast and does
    not need a keystore.
    """
    from identity_interpreter.adapters.memory_manager import resolve_memory_dir

    monkeypatch.setenv("BARTH_DB_PATH", str(tmp_path / "state" / "barth.db"))
    assert (
        Path(resolve_memory_dir()) == tmp_path / "state"
    ), "MemoryManager would write outside the configured runtime directory."

    monkeypatch.delenv("BARTH_DB_PATH", raising=False)
    assert (
        resolve_memory_dir() == "./data"
    ), "Unset BARTH_DB_PATH must keep the historical default for ordinary local deployments."
