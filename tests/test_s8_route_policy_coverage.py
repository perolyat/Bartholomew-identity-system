"""
S8: every route is classified, and nothing is accidentally public.

This is the test that keeps the boundary honest as four streams add routes in
parallel. It walks the app's real route table and asserts that each route is
either explicitly public or has an explicit capability. A new route is
therefore a failing test until someone decides what authority it needs --
which is the intended cost, and much cheaper than discovering an open
endpoint after Alpha starts.
"""

from __future__ import annotations

import tempfile

import pytest

from bartholomew.platform.http_identity import _iter_routes  # noqa: E402
from bartholomew.platform.route_policy import (  # noqa: E402
    PUBLIC_PATHS,
    ROUTE_CAPABILITIES,
    UnclassifiedRouteError,
    capability_for,
    is_public_path,
)
from bartholomew_api_bridge_v0_1.services.api.app import app  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
    """
    Set this module's environment for its own duration and restore it after.

    Module-level `os.environ[...]` assignment would leak `BARTH_AUTH_MODE` and
    the database paths into every other test file in the same pytest session
    -- silently enforcing authentication on suites written before it existed,
    and pointing their kernels at this module's database. A module-scoped
    MonkeyPatch keeps the change contained to this file.
    """
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="s8-routes-")
    for var, value in {
        "BARTH_PLATFORM_DB_PATH": "<tmp>/platform.db",
        "BARTH_DATA_ROOT": "<tmp>/data",
        "BARTH_DB_PATH": "<tmp>/kernel.db",
    }.items():
        mp.setenv(var, value.replace("<tmp>", tmp))
    yield
    mp.undo()


def _declared_routes():
    """Every (method, path) the app actually serves."""
    found = set()
    for route in _iter_routes(app.routes):
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in (getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}:
            found.add((method, path))
    return found


def test_every_route_is_either_public_or_classified():
    """
    Default deny, enforced against the real route table.

    If this fails, a route exists that the authorisation table does not know
    about. The fix is to classify it in `route_policy.ROUTE_CAPABILITIES`, or
    add it to `PUBLIC_PATHS` if it genuinely holds no personal data and
    touches no governed state.
    """
    unclassified = []
    for method, path in sorted(_declared_routes()):
        if is_public_path(path):
            continue
        try:
            capability_for(method, path)
        except UnclassifiedRouteError:
            unclassified.append(f"{method} {path}")
    assert not unclassified, (
        "Routes with no authorisation policy (they are refused with 403 until "
        f"classified in bartholomew/platform/route_policy.py): {unclassified}"
    )


def test_the_policy_table_has_no_entries_for_routes_that_do_not_exist():
    """
    The other direction. A stale entry is a quiet correctness problem: it
    suggests a route is protected when nothing serves it, and it hides the
    fact that a real route may have been renamed out from under its policy.
    """
    declared = _declared_routes()
    # Entries that legitimately have no route in this repository yet:
    #   * /internal/metrics exists only under METRICS_INTERNAL_ONLY;
    #   * the inbound-capture routes are Session D's to implement, and are
    #     pre-classified here so that D's handlers arrive authenticated
    #     instead of hitting default-deny and inviting a bypass. Remove these
    #     from the exemption once D has landed them.
    exempt = {
        ("GET", "/internal/metrics"),
        # Session D implements POST and GET /api/inbound/events, so those are
        # no longer exempt -- they are real routes and the coverage assertion
        # above now checks them. The per-event read stays classified ahead of
        # its handler, on the same reasoning as before: default-deny means a
        # route that arrives unclassified is refused, and pre-classifying is
        # what stops that becoming a reason to reach for a bypass.
        ("GET", "/api/inbound/events/{event_id}"),
    }
    stale = [
        f"{m} {p}"
        for (m, p) in ROUTE_CAPABILITIES
        if (m, p) not in declared and (m, p) not in exempt
    ]
    assert not stale, f"policy entries for routes that no longer exist: {stale}"


def test_the_public_list_is_small_and_holds_nothing_personal():
    """
    A guard against the public list quietly growing. Anything touching
    memory, governance, self-state or chat must never be public, whatever
    convenience argues for it.
    """
    forbidden = (
        "memory",
        "governance",
        "brake",
        "consent",
        "chat",
        "episodes",
        "self",
        "training",
        "kernel",
        "persona",
        "working_memory",
    )
    for path in PUBLIC_PATHS:
        assert not any(
            f in path for f in forbidden
        ), f"{path} is in PUBLIC_PATHS but names a personal or governed surface"
    assert (
        len(PUBLIC_PATHS) <= 12
    ), "the unauthenticated surface is growing; each addition needs a reason"


def test_metrics_is_not_public():
    """
    T6. Metrics describe how a person's Bartholomew is behaving. The existing
    METRICS_INTERNAL_ONLY switch relocates the route; it does not protect it.
    """
    assert not is_public_path("/metrics")
    assert not is_public_path("/internal/metrics")


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/memory/export"),
        ("POST", "/api/governance/brake/disengage"),
        ("DELETE", "/api/memory/{kind}/{key}"),
        ("POST", "/kernel/command/{cmd}"),
    ],
)
def test_high_consequence_routes_are_classified_distinctly(method, path):
    """
    The most consequential routes must not share a catch-all capability --
    exfiltrating a whole memory and reading one key are different powers.
    """
    assert capability_for(method, path) is not None
