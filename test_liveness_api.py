import sqlite3

import pytest
from fastapi.testclient import TestClient

from bartholomew_api_bridge_v0_1.services.api.app import app
from bartholomew_api_bridge_v0_1.services.api.routes import liveness


@pytest.fixture(scope="module")
def client():
    # Must enter as a context manager to run the app's startup/shutdown
    # lifespan -- that's what starts the kernel daemon and creates its
    # schema (reflections, nudges, etc tables). A bare TestClient(app)
    # never triggers it, so every query below would 404/error against a
    # schema-less DB.
    with TestClient(app) as c:
        yield c


def test_liveness_ticks_endpoint(client):
    r = client.get("/api/liveness/ticks?limit=5&offset=0")
    assert r.status_code == 200, f"Unexpected status: {r.status_code}"
    data = r.json()
    assert isinstance(data, list)


def test_get_ticks_returns_empty_when_table_missing(tmp_path, monkeypatch):
    """Regression guard for the startup race: the `ticks` table is created by
    the scheduler's ensure_schema() as a fire-and-forget task that
    KernelDaemon.start() does not await, so the API can serve a request before
    the table exists. The endpoint must return an empty list (200), not 500
    with "no such table: ticks".

    Exercises the endpoint function directly against a DB that has no `ticks`
    table, so it's deterministic (no reliance on winning/losing the real
    startup race) and version-independent.
    """
    db = tmp_path / "no_ticks.db"
    sqlite3.connect(str(db)).close()  # empty DB: no `ticks` table
    # liveness.py resolves its db path fresh on every call via
    # resolve_db_path() (see db.resolve_db_path()'s docstring for why it's
    # no longer a frozen DB_PATH constant) -- patch that instead.
    monkeypatch.setattr(liveness, "resolve_db_path", lambda: str(db))

    assert liveness.get_ticks(limit=5, offset=0) == []


def test_liveness_nudges_endpoint(client):
    r = client.get("/api/liveness/nudges?limit=5&offset=0")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_liveness_reflections_endpoint(client):
    r = client.get("/api/liveness/reflections?limit=5&offset=0")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
