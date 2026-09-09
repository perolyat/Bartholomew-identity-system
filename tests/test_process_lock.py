"""
Tests for bartholomew.kernel.process_lock (Phase B stage B6): the
cross-process exclusivity guard KernelDaemon uses to refuse starting a
second time against the same db_path, and that `embeddings rebuild-vss`
uses to refuse running while a daemon holds it. Isolated from
KernelDaemon -- daemon-level integration (acquire-first-in-start(),
release-last-in-stop()) is tested separately in
tests/test_daemon_lifecycle_integrity.py.
"""

from __future__ import annotations

import os
import sys

import pytest

from bartholomew.kernel.process_lock import ProcessLock, ProcessLockHeldError


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


def test_acquire_then_release_leaves_lock_unheld(db_path):
    lock = ProcessLock(db_path)
    assert lock.held is False
    lock.acquire()
    assert lock.held is True
    lock.release()
    assert lock.held is False


def test_lock_file_created_alongside_db_path(db_path):
    lock = ProcessLock(db_path)
    lock.acquire()
    try:
        assert lock.lock_path == f"{db_path}.lock"
        assert os.path.exists(lock.lock_path)
    finally:
        lock.release()


def test_second_lock_on_same_path_conflicts(db_path):
    """Two independent ProcessLock instances (simulating two processes,
    or a daemon and a CLI command) pointed at the same db_path must not
    both hold the lock -- flock()/msvcrt.locking() associate the lock
    with the open file description, so a second, independently-opened
    fd on the same file conflicts even within a single test process."""
    first = ProcessLock(db_path)
    second = ProcessLock(db_path)

    first.acquire()
    try:
        with pytest.raises(ProcessLockHeldError):
            second.acquire()
        assert second.held is False
    finally:
        first.release()

    # Once released, a fresh acquire attempt succeeds.
    second.acquire()
    try:
        assert second.held is True
    finally:
        second.release()


def test_release_is_idempotent(db_path):
    lock = ProcessLock(db_path)
    lock.acquire()
    lock.release()
    lock.release()  # must not raise
    assert lock.held is False


def test_release_without_acquire_is_a_safe_noop(db_path):
    lock = ProcessLock(db_path)
    lock.release()  # must not raise
    assert lock.held is False


def test_double_acquire_on_same_instance_raises(db_path):
    lock = ProcessLock(db_path)
    lock.acquire()
    try:
        with pytest.raises(ProcessLockHeldError):
            lock.acquire()
    finally:
        lock.release()


def test_context_manager_acquires_and_releases(db_path):
    lock = ProcessLock(db_path)
    with lock:
        assert lock.held is True
    assert lock.held is False


def test_context_manager_releases_on_exception(db_path):
    lock = ProcessLock(db_path)
    with pytest.raises(RuntimeError):
        with lock:
            assert lock.held is True
            raise RuntimeError("boom")
    assert lock.held is False


def test_lock_released_by_one_process_is_acquirable_by_another(db_path):
    """After a full acquire/release cycle, a completely independent
    ProcessLock instance can acquire cleanly -- releasing genuinely frees
    the lock rather than leaving it in a half-held state."""
    first = ProcessLock(db_path)
    first.acquire()
    first.release()

    second = ProcessLock(db_path)
    second.acquire()
    try:
        assert second.held is True
    finally:
        second.release()


def test_lock_messages_show_the_path_verbatim_not_escaped(tmp_path):
    """The message exists for a person to act on, so it must carry the path
    as they would type it. Both messages used `!r`, which doubles every
    backslash -- on Windows the path in the message was therefore not the
    path, and `db_path in message` was false there. A backslash is a legal
    filename character on POSIX, which lets the property be asserted on
    every platform rather than only where it once failed."""
    name = "test.db" if sys.platform == "win32" else "back\\slash.db"
    db_path = str(tmp_path / name)
    assert "\\" in db_path, "the fixture path must contain a backslash for this to prove anything"

    first = ProcessLock(db_path)
    second = ProcessLock(db_path)
    first.acquire()
    try:
        with pytest.raises(ProcessLockHeldError) as contended:
            second.acquire()
        with pytest.raises(ProcessLockHeldError) as reentered:
            first.acquire()
    finally:
        first.release()

    for message in (str(contended.value), str(reentered.value)):
        assert db_path in message
        assert repr(db_path) not in message, "the path was rendered escaped, not verbatim"


def test_process_lock_held_error_message_is_actionable(db_path):
    first = ProcessLock(db_path)
    second = ProcessLock(db_path)
    first.acquire()
    try:
        with pytest.raises(ProcessLockHeldError) as excinfo:
            second.acquire()
        message = str(excinfo.value)
        assert db_path in message
        assert "another process" in message.lower()
    finally:
        first.release()
