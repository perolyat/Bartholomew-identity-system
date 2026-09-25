"""
Who initialises a live app's database: the app, once.

A module that runs the real app under `TestClient` hands the app its database
path, and the app's own startup (`KernelDaemon.start()`) creates every schema
on it -- MemoryStore's, the objective store's -- exactly once. Tests then
seed records into that same file while the app's kernel is running.

Those seeds used to open a fresh `MemoryStore` and call `init()` (and build
their own `ObjectiveStore`, whose constructor runs `ensure_schema()`) on every
call: 28 full schema/migration/FTS-heal passes against `learning-api.db` in
one module, 14 against the memory-agency database, each racing the live
kernel from a second, uncoordinated lifecycle. Production never does that --
one process initialises its database once -- and on Windows Merge Candidate
run 35971892908 a seed's `init()` is where `database is locked` was raised.

`forbid_schema_work_from_the_test_thread()` makes the invariant enforced
rather than remembered: while it is active, any `MemoryStore.init()` or
objective-store `ensure_schema()` against the owned path, called from the
test's own thread, fails at once. The app's kernel runs on TestClient's
portal thread and is not affected.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Iterator

from bartholomew.kernel import memory_store as _memory_store
from bartholomew.kernel import objective_store as _objective_store


class SchemaWorkAgainstALiveAppDatabaseError(AssertionError):
    """A seed tried to initialise a database the running app already owns."""


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


@contextlib.contextmanager
def forbid_schema_work_from_the_test_thread(owned_db_path: str) -> Iterator[None]:
    test_thread = threading.current_thread()
    original_init = _memory_store.MemoryStore.init
    original_ensure = _objective_store.ensure_schema

    def _refuse(what: str) -> None:
        raise SchemaWorkAgainstALiveAppDatabaseError(
            f"{what} was called against {owned_db_path} from the test thread while the "
            "app that owns that database is running. The app initialised it at startup; "
            "seed through the established state (a MemoryStore without init(), the "
            "kernel's own objective_store) instead of re-running schema work.",
        )

    async def guarded_init(self, *args, **kwargs):
        if threading.current_thread() is test_thread and _same_file(self.db_path, owned_db_path):
            _refuse("MemoryStore.init()")
        return await original_init(self, *args, **kwargs)

    def guarded_ensure_schema(db_path, *args, **kwargs):
        if threading.current_thread() is test_thread and _same_file(db_path, owned_db_path):
            _refuse("objective_store.ensure_schema()")
        return original_ensure(db_path, *args, **kwargs)

    _memory_store.MemoryStore.init = guarded_init
    _objective_store.ensure_schema = guarded_ensure_schema
    try:
        yield
    finally:
        _memory_store.MemoryStore.init = original_init
        _objective_store.ensure_schema = original_ensure
