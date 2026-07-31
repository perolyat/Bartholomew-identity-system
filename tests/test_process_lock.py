"""
Isolated tests for bartholomew.kernel.process_lock (Phase B, stage B6).

Not daemon-integration tests -- those live in
tests/test_daemon_lifecycle_b6.py. These exercise ProcessLock itself.
"""

from __future__ import annotations

import multiprocessing
import os

import pytest

from bartholomew.kernel.process_lock import ProcessLock


@pytest.fixture
def lock_path(tmp_path):
    return str(tmp_path / "test.db.lock")


def test_try_acquire_succeeds_on_fresh_lock(lock_path):
    lock = ProcessLock(lock_path)
    assert lock.try_acquire() is True
    lock.release()


def test_try_acquire_is_idempotent_for_same_instance(lock_path):
    lock = ProcessLock(lock_path)
    assert lock.try_acquire() is True
    assert lock.try_acquire() is True  # already held by us -- not an error
    lock.release()


def test_second_instance_cannot_acquire_while_first_holds(lock_path):
    first = ProcessLock(lock_path)
    second = ProcessLock(lock_path)

    assert first.try_acquire() is True
    assert second.try_acquire() is False

    first.release()


def test_second_instance_can_acquire_after_release(lock_path):
    first = ProcessLock(lock_path)
    second = ProcessLock(lock_path)

    assert first.try_acquire() is True
    first.release()
    assert second.try_acquire() is True
    second.release()


def test_release_is_idempotent(lock_path):
    lock = ProcessLock(lock_path)
    lock.try_acquire()
    lock.release()
    lock.release()  # must not raise


def test_release_without_ever_acquiring_is_a_no_op(lock_path):
    lock = ProcessLock(lock_path)
    lock.release()  # must not raise


def test_is_held_by_other_false_when_lock_is_free(lock_path):
    lock = ProcessLock(lock_path)
    assert lock.is_held_by_other() is False


def test_is_held_by_other_true_while_another_holds_it(lock_path):
    holder = ProcessLock(lock_path)
    holder.try_acquire()

    checker = ProcessLock(lock_path)
    assert checker.is_held_by_other() is True

    holder.release()
    assert checker.is_held_by_other() is False


def test_is_held_by_other_false_for_the_holder_itself(lock_path):
    lock = ProcessLock(lock_path)
    lock.try_acquire()
    assert lock.is_held_by_other() is False  # we hold it ourselves, not an "other"
    lock.release()


def test_is_held_by_other_does_not_itself_acquire_the_lock(lock_path):
    """The probe in is_held_by_other() must release what it acquires --
    otherwise calling it would itself change lock state as a side effect."""
    checker = ProcessLock(lock_path)
    checker.is_held_by_other()

    someone_else = ProcessLock(lock_path)
    assert someone_else.try_acquire() is True  # still free
    someone_else.release()


def test_owner_pid_reports_the_acquiring_process(lock_path):
    lock = ProcessLock(lock_path)
    lock.try_acquire()

    reader = ProcessLock(lock_path)
    assert reader.owner_pid() == os.getpid()

    lock.release()


def test_owner_pid_none_when_lock_file_absent(lock_path):
    reader = ProcessLock(lock_path)
    assert reader.owner_pid() is None


def _acquire_and_report(lock_path: str, result_queue) -> None:
    lock = ProcessLock(lock_path)
    result_queue.put(lock.try_acquire())
    if lock._fd is not None:
        # Hold it briefly so the parent process's assertion below observes
        # a genuinely cross-process held lock, not a race.
        import time

        time.sleep(0.3)
        lock.release()


def test_lock_is_exclusive_across_real_processes(lock_path):
    """
    A real cross-process check, not just cross-instance-in-one-process:
    fcntl.flock/msvcrt.locking semantics differ from a simple in-process
    mutex (e.g. flock is per-open-file-description, not per-process, and
    some platforms release locks on any fd close from the same process) --
    this is the test that would catch that class of bug.
    """
    ctx = multiprocessing.get_context("spawn")
    result_queue: multiprocessing.Queue = ctx.Queue()
    proc = ctx.Process(target=_acquire_and_report, args=(lock_path, result_queue))
    proc.start()

    # Give the child a moment to acquire before we try, to keep the test
    # deterministic rather than racing scheduler order.
    child_acquired = result_queue.get(timeout=5.0)
    assert child_acquired is True

    checker = ProcessLock(lock_path)
    assert checker.is_held_by_other() is True

    proc.join(timeout=5.0)
    assert not proc.is_alive()

    # Child released on exit -- now free.
    assert checker.is_held_by_other() is False
