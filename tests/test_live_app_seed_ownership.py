"""
A seed never re-initialises a database the running app owns.

See tests/helpers/live_app_db.py. The guard is only worth having if it bites,
and only if every module that seeds into a live app actually uses it, so both
are pinned here.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import threading

import pytest

from bartholomew.kernel import objective_store as objective_store_module
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.objective_store import ObjectiveStore
from tests.helpers.live_app_db import (
    SchemaWorkAgainstALiveAppDatabaseError,
    forbid_schema_work_from_the_test_thread,
)

TESTS_DIR = pathlib.Path(__file__).resolve().parent


@pytest.fixture
def owned(tmp_path) -> str:
    """A database already initialised by its owner, as the app does at startup."""
    path = str(tmp_path / "owned.db")
    asyncio.run(MemoryStore(path).init())
    ObjectiveStore(path)
    return path


def test_a_memory_store_init_against_the_owned_database_is_refused(owned):
    with forbid_schema_work_from_the_test_thread(owned):
        with pytest.raises(SchemaWorkAgainstALiveAppDatabaseError, match=r"MemoryStore\.init"):
            asyncio.run(MemoryStore(owned).init())


def test_an_objective_store_schema_pass_against_the_owned_database_is_refused(owned):
    with forbid_schema_work_from_the_test_thread(owned):
        with pytest.raises(SchemaWorkAgainstALiveAppDatabaseError, match="ensure_schema"):
            ObjectiveStore(owned)


def test_seeding_through_the_established_state_is_allowed(owned):
    """What the seeds now do: an uninitialised MemoryStore, writing."""
    from datetime import datetime, timezone

    async def _seed():
        store = MemoryStore(owned)
        result = await store.upsert_memory(
            "fact",
            "seeded",
            "a value",
            datetime.now(timezone.utc).isoformat(),
        )
        return result.stored

    with forbid_schema_work_from_the_test_thread(owned):
        assert asyncio.run(_seed()) is True


def test_the_owner_itself_and_other_databases_are_unaffected(owned, tmp_path):
    """The app's kernel runs on TestClient's portal thread; other files are
    none of the guard's business."""
    other = str(tmp_path / "other.db")
    errors: list[BaseException] = []

    def _as_the_app():
        try:
            asyncio.run(MemoryStore(owned).init())
            ObjectiveStore(owned)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    with forbid_schema_work_from_the_test_thread(owned):
        asyncio.run(MemoryStore(other).init())
        ObjectiveStore(other)
        app_thread = threading.Thread(target=_as_the_app, name="asyncio-portal-probe")
        app_thread.start()
        app_thread.join()
    assert errors == []


def test_the_guard_leaves_nothing_behind(owned):
    before = (MemoryStore.init, objective_store_module.ensure_schema)
    with forbid_schema_work_from_the_test_thread(owned):
        assert MemoryStore.init is not before[0]
    assert (MemoryStore.init, objective_store_module.ensure_schema) == before


# ---------------------------------------------------------------------------
# Structural: every module that seeds into a live app is guarded, and no seed
# helper does schema work of its own.
# ---------------------------------------------------------------------------

_SCHEMA_WORK = {"init", "ensure_schema", "ObjectiveStore", "executescript"}


def _live_app_modules_with_seeds() -> list[pathlib.Path]:
    found = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        if "TestClient(" not in text:
            continue
        tree = ast.parse(text)
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_seed")
            for node in ast.walk(tree)
        ):
            found.append(path)
    return found


def test_the_modules_that_seed_into_a_live_app_are_the_ones_known_here():
    """If this list grows, the new module is checked by the two tests below;
    if it shrinks to nothing, those tests would pass vacuously -- so say so."""
    names = {path.name for path in _live_app_modules_with_seeds()}
    assert {"test_learning_control_centre_api.py", "test_memory_agency.py"} <= names


@pytest.mark.parametrize(
    "module",
    _live_app_modules_with_seeds(),
    ids=lambda path: path.name,
)
def test_a_live_app_module_activates_the_guard(module):
    assert "forbid_schema_work_from_the_test_thread(" in module.read_text(encoding="utf-8"), (
        f"{module.name} seeds into a live app but never activates "
        "tests/helpers/live_app_db.forbid_schema_work_from_the_test_thread()"
    )


@pytest.mark.parametrize(
    "module",
    _live_app_modules_with_seeds(),
    ids=lambda path: path.name,
)
def test_no_seed_helper_does_schema_work(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    offenders = []
    for helper in ast.walk(tree):
        if not (
            isinstance(helper, (ast.FunctionDef, ast.AsyncFunctionDef))
            and helper.name.startswith("_seed")
        ):
            continue
        for node in ast.walk(helper):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name in _SCHEMA_WORK:
                    offenders.append(f"{helper.name}() calls {name}() at line {node.lineno}")
    assert not offenders, "; ".join(offenders)
