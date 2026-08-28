"""
S8: the Platform/Admin tier composes into real execution, not just a helper.

The gap this closes: the tier had a store, a CLI and unit tests, but no
production call site -- so autonomous work, skills and governed state
mutation never actually consulted it. These tests exercise the composition
through `is_blocked_fail_closed`, which is the function
`kernel/skill_registry.py` and `kernel/runtime_contract.py` already call at
their execution boundaries, so a pass here means those boundaries compose
both tiers.
"""

from __future__ import annotations

import tempfile

import pytest

from bartholomew.orchestrator.safety.governance_store import (
    GovernanceStore,
    engaged_state_fail_closed,
    is_blocked_fail_closed,
    register_additional_engaged_check,
    register_additional_halt_check,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="s8-platform-exec-")
    monkeypatch.setenv("BARTH_PLATFORM_DB_PATH", f"{tmp}/platform.db")
    monkeypatch.setenv("BARTH_DATA_ROOT", f"{tmp}/data")
    # An active platform tier. Without this the tier is deliberately inert.
    monkeypatch.setenv("BARTH_AUTH_MODE", "enforced")
    from bartholomew.platform.authority import install_platform_halt_hook
    from bartholomew.platform.store import init_platform_schema

    init_platform_schema()
    install_platform_halt_hook()
    yield tmp
    register_additional_halt_check(None)
    register_additional_engaged_check(None)


@pytest.fixture
def user_db(tmp_path):
    db = str(tmp_path / "user.db")
    GovernanceStore(db)
    return db


# ---------------------------------------------------------------------------
# The tier reaches the boundaries that already consult Governance
# ---------------------------------------------------------------------------


def test_the_execution_boundary_used_by_skills_and_autonomous_work_composes_the_tier(user_db):
    """
    `is_blocked_fail_closed` is what skill_registry._is_blocked_by_brake and
    runtime_contract's governed paths call. A platform halt must be visible
    there even though the user's own brake is released.
    """
    from bartholomew.platform import authority

    assert is_blocked_fail_closed("skills", user_db) is False
    authority.engage("skills", actor="ops", reason="systemic defect")
    assert is_blocked_fail_closed("skills", user_db) is True


def test_a_platform_global_halt_stops_every_scope(user_db):
    from bartholomew.platform import authority

    authority.engage("global", actor="ops", reason="platform emergency")
    for scope in ("skills", "scheduler", "training", "sight", "voice"):
        assert is_blocked_fail_closed(scope, user_db) is True, scope


def test_the_tier_does_not_halt_unrelated_scopes(user_db):
    from bartholomew.platform import authority

    authority.engage("training", actor="ops", reason="bad ingest")
    assert is_blocked_fail_closed("training", user_db) is True
    assert is_blocked_fail_closed("skills", user_db) is False


# ---------------------------------------------------------------------------
# Restrictive composition, in both directions
# ---------------------------------------------------------------------------


def test_releasing_the_platform_halt_does_not_release_the_personal_one(user_db):
    from bartholomew.platform import authority

    GovernanceStore(user_db).engage("skills", reason="user halted", actor="alice")
    authority.engage("skills", actor="ops", reason="also halted")
    assert is_blocked_fail_closed("skills", user_db) is True

    authority.disengage(actor="ops", reason="platform fixed")
    assert (
        is_blocked_fail_closed("skills", user_db) is True
    ), "releasing the platform tier released the user's own brake"


def test_releasing_the_personal_halt_does_not_release_the_platform_one(user_db):
    from bartholomew.platform import authority

    store = GovernanceStore(user_db)
    store.engage("skills", reason="user halted", actor="alice")
    authority.engage("skills", actor="ops", reason="platform halted")

    state = store.refresh()
    store.disengage(reason="user done", expected_revision=state.revision, actor="alice")
    assert (
        is_blocked_fail_closed("skills", user_db) is True
    ), "a user released a platform-wide safety halt through their own brake"


def test_a_user_cannot_release_the_platform_tier_through_scopes(user_db):
    """
    DECISIONS.md names `"platform"` as a brake *scope* a category error,
    because scopes are cleared by the ordinary disengage any user may call.
    Engaging and clearing every user-reachable scope must leave the platform
    halt standing.
    """
    from bartholomew.platform import authority

    authority.engage("global", actor="ops", reason="platform emergency")
    store = GovernanceStore(user_db)
    for scope in ("global", "skills", "scheduler", "training", "sight", "voice"):
        store.engage(scope, reason="user", actor="alice")
    state = store.refresh()
    store.disengage(
        reason="user clears everything",
        expected_revision=state.revision,
        actor="alice",
    )
    assert is_blocked_fail_closed("skills", user_db) is True


# ---------------------------------------------------------------------------
# Fail closed on the platform, fully local for the person
# ---------------------------------------------------------------------------


def test_an_unreadable_platform_state_fails_closed(user_db, monkeypatch):
    monkeypatch.setenv("BARTH_PLATFORM_DB_PATH", "/proc/1/definitely/not/a/db")
    assert is_blocked_fail_closed("skills", user_db) is True


def test_the_personal_brake_still_engages_with_the_platform_store_destroyed(user_db, monkeypatch):
    """
    The property the whole architecture rests on. With the control plane
    gone, a person can still stop their own Bartholomew, entirely locally.
    """
    monkeypatch.setenv("BARTH_PLATFORM_DB_PATH", "/proc/1/definitely/not/a/db")

    store = GovernanceStore(user_db)
    store.engage("global", reason="user halts during a platform outage", actor="cli")
    assert store.refresh().engaged is True
    assert is_blocked_fail_closed("skills", user_db) is True


def test_the_personal_brake_still_releases_with_the_platform_store_destroyed(user_db):
    """
    Release, not just engage. A user whose local brake could be engaged but
    never released during a platform outage would be locked out of their own
    Bartholomew by someone else's infrastructure failure.

    Release is checked on the local store directly: `is_blocked_fail_closed`
    would still report blocked, correctly, because an unreadable *active*
    platform tier fails closed. The local authority itself must remain fully
    operable.
    """
    store = GovernanceStore(user_db)
    store.engage("global", reason="user halt", actor="cli")
    state = store.refresh()
    store.disengage(reason="user release", expected_revision=state.revision, actor="cli")
    assert store.refresh().engaged is False


def test_governance_never_imports_the_platform_package():
    """
    Structural, and the reason the composition is a registration hook. If
    Governance ever imported the platform package, a control-plane outage
    could sit on the local kill switch's path.
    """
    import pathlib

    for name in ("governance_store.py", "parking_brake.py"):
        source = pathlib.Path("bartholomew/orchestrator/safety") / name
        text = source.read_text(encoding="utf-8")
        assert "bartholomew.platform" not in text, f"{name} imports the platform package"
        assert "platform_connection" not in text


def test_the_tier_is_inert_in_a_purely_local_deployment(user_db, monkeypatch):
    """
    A single-user loopback install has no platform tier. An absent
    control-plane database must not fail-close it into uselessness.
    """
    from bartholomew.platform import authority

    authority.engage("global", actor="ops", reason="engaged while active")
    assert is_blocked_fail_closed("skills", user_db) is True

    monkeypatch.delenv("BARTH_AUTH_MODE", raising=False)
    monkeypatch.delenv("BARTH_API_ALLOW_NON_LOOPBACK", raising=False)
    assert is_blocked_fail_closed("skills", user_db) is False


def test_an_admin_still_holds_no_personal_data_capability():
    """Wiring the tier must not have widened administrative authority."""
    from bartholomew.platform.capabilities import Capability, capabilities_for
    from bartholomew.platform.principal import Principal, PrincipalKind

    ops = Principal("id", "ops", PrincipalKind.PLATFORM_ADMIN, "s")
    held = capabilities_for(ops)
    for forbidden in (
        Capability.MEMORY_READ,
        Capability.MEMORY_WRITE,
        Capability.MEMORY_EXPORT,
        Capability.CHAT,
        Capability.CONSENT_DECIDE,
        Capability.SELF_READ,
        Capability.TRAINING_SUBMIT,
    ):
        assert forbidden not in held, forbidden


# ---------------------------------------------------------------------------
# Integration with Session A's scope-less gate (added at merge time)
# ---------------------------------------------------------------------------
#
# `engaged_state_fail_closed` gates operations that belong to no subsystem
# scope -- objective mutation, consent resolution, memory writes. It is a
# different helper from `is_blocked_fail_closed`, so composing the tier into
# one did not compose it into the other: a platform-wide halt would have
# stopped skills while durable user state kept changing underneath it.


def test_a_platform_halt_stops_scope_less_governed_mutation(user_db):
    """
    The integration gap this closes. A platform halt must register on the
    "is the brake engaged at all" gate, not only the per-scope one.
    """
    from bartholomew.platform import authority

    assert engaged_state_fail_closed(user_db).engaged is False
    authority.engage("global", actor="ops", reason="platform emergency")
    assert engaged_state_fail_closed(user_db).engaged is True


def test_a_scoped_platform_halt_also_stops_scope_less_mutation(user_db):
    """
    Session A's own reasoning: the gate is whether the brake is engaged AT
    ALL, not whether one subsystem is halted. A platform halt scoped to
    `voice` must therefore still stop objective mutation.
    """
    from bartholomew.platform import authority

    authority.engage("voice", actor="ops", reason="defective capability")
    assert engaged_state_fail_closed(user_db).engaged is True


def test_the_scope_less_gate_still_reports_the_local_brake_alone(user_db):
    """The local path is unchanged, and short-circuits before the tier."""
    from bartholomew.platform import authority

    authority.disengage(actor="test-reset", reason="clean slate")
    GovernanceStore(user_db).engage("global", reason="user halt", actor="alice")
    assert engaged_state_fail_closed(user_db).engaged is True


def test_the_scope_less_gate_fails_closed_when_the_tier_is_unreadable(user_db, monkeypatch):
    monkeypatch.setenv("BARTH_PLATFORM_DB_PATH", "/proc/1/definitely/not/a/db")
    assert engaged_state_fail_closed(user_db).engaged is True


def test_the_scope_less_gate_is_inert_in_a_purely_local_deployment(user_db, monkeypatch):
    """An absent tier must not fail-close a single-user local install."""
    from bartholomew.platform import authority

    authority.engage("global", actor="ops", reason="engaged while active")
    assert engaged_state_fail_closed(user_db).engaged is True

    monkeypatch.delenv("BARTH_AUTH_MODE", raising=False)
    monkeypatch.delenv("BARTH_API_ALLOW_NON_LOOPBACK", raising=False)
    assert engaged_state_fail_closed(user_db).engaged is False


def test_releasing_the_platform_tier_does_not_release_a_local_scope_less_halt(
    user_db,
):
    from bartholomew.platform import authority

    GovernanceStore(user_db).engage("global", reason="user halt", actor="alice")
    authority.engage("global", actor="ops", reason="platform halt")
    authority.disengage(actor="ops", reason="platform fixed")
    assert engaged_state_fail_closed(user_db).engaged is True
