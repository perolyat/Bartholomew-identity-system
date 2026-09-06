"""Recovery: `failed` and `unknown` are different, and the difference does something.

W03-C acceptance criterion 5. The result vocabulary has always insisted that a
device which could not observe its effect must say `unknown` rather than round
it to either neighbour. This is what makes that insistence pay: the outcome
turns into one of three defined next steps, computed once on the server, so the
executive, an operator surface and a later audit all read the same answer
instead of each deciding for itself whether an `unknown` is worth another go.

Two properties, and the second is the one that matters
------------------------------------------------------
1. The **policy** discriminates: a refusal never becomes retry-eligible, a
   non-repeatable `unknown` never becomes retry-eligible, and an abort never
   does however idempotent the capability is.
2. `RETRY_ELIGIBLE` is **advice and cannot be acted on**. A retry is a new
   action through the whole envelope -- a new request, a new fingerprint, a new
   approval -- and the state machine makes that structural rather than
   aspirational: a terminal row has no transition out of it, so the action a
   plan describes can never be re-leased. The tests at the bottom of this file
   prove the second property against the real database, because a policy that
   said "retry" while something could quietly re-run a finished action would be
   worse than no policy.

And the sweep: an abandoned lease reaches an honest terminal state on its own,
which is the other half of "a failed or unknown result is distinguishable" --
an action nobody ever reported must not sit at `leased` forever.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta

import pytest

from bartholomew.actuation import arming, devices, recovery, seam, store
from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import ALL_CAPABILITIES
from bartholomew.actuation.recovery import RecoveryOutcome, plan_recovery
from bartholomew.actuation.request import to_iso, utc_now
from bartholomew.actuation.result import ActionResultStatus, ErrorCategory
from bartholomew.actuation.store import ActionState

TENANT = "tenant-a"
DEVICE = "desk-pc"


# ---------------------------------------------------------------------------
# the policy
# ---------------------------------------------------------------------------


def test_a_success_needs_no_recovery():
    plan = plan_recovery(status=ActionResultStatus.SUCCEEDED)
    assert plan.outcome is RecoveryOutcome.NONE
    assert plan.retryable is False


def test_a_withdrawal_is_a_decision_not_a_fault():
    plan = plan_recovery(
        status=ActionResultStatus.CANCELLED,
        error_category=ErrorCategory.CANCELLED,
    )
    assert plan.outcome is RecoveryOutcome.NONE


@pytest.mark.parametrize(
    "category",
    sorted(recovery.REFUSAL_CATEGORIES, key=lambda c: c.value),
)
@pytest.mark.parametrize("status", [ActionResultStatus.FAILED, ActionResultStatus.UNKNOWN])
def test_no_refusal_is_ever_retry_eligible_however_idempotent(status, category):
    """Re-proposing the same thing after a refusal is how a refusal becomes a rate limit."""
    plan = plan_recovery(status=status, error_category=category, repeatability="idempotent")
    assert plan.outcome is RecoveryOutcome.SURFACE
    assert plan.retryable is False


@pytest.mark.parametrize(
    "category",
    sorted(recovery.TRANSIENT_CATEGORIES, key=lambda c: c.value),
)
def test_a_transient_failure_on_an_idempotent_capability_may_be_proposed_again(category):
    plan = plan_recovery(
        status=ActionResultStatus.FAILED,
        error_category=category,
        repeatability="idempotent",
    )
    assert plan.outcome is RecoveryOutcome.RETRY_ELIGIBLE
    assert plan.requires_new_approval is True


@pytest.mark.parametrize(
    "category",
    sorted(recovery.TRANSIENT_CATEGORIES, key=lambda c: c.value),
)
def test_the_same_failure_on_a_non_repeatable_capability_surfaces(category):
    """The non-vacuity pair: one field differs, and the answer inverts.

    Non-repeatable is a claim that running this twice could compound an effect.
    A policy that retried it anyway would be reading the declaration and then
    ignoring it.
    """
    plan = plan_recovery(
        status=ActionResultStatus.FAILED,
        error_category=category,
        repeatability="non_repeatable",
    )
    assert plan.outcome is RecoveryOutcome.SURFACE


def test_an_unknown_on_a_non_repeatable_capability_is_never_retried():
    """The case the whole `unknown` status exists for.

    The effect may or may not have landed. Retrying a non-repeatable action in
    that state is how one message gets sent twice, and "I do not know whether
    this happened" is more useful than a second attempt that might double it.
    """
    plan = plan_recovery(
        status=ActionResultStatus.UNKNOWN,
        error_category=ErrorCategory.EFFECT_UNVERIFIABLE,
        repeatability="non_repeatable",
    )
    assert plan.outcome is RecoveryOutcome.SURFACE
    assert "may already have landed" in plan.reason


def test_an_unknown_on_an_idempotent_capability_may_be_proposed_again():
    """Idempotent is precisely the declaration that this uncertainty is harmless."""
    plan = plan_recovery(
        status=ActionResultStatus.UNKNOWN,
        error_category=ErrorCategory.EFFECT_UNVERIFIABLE,
        repeatability="idempotent",
    )
    assert plan.outcome is RecoveryOutcome.RETRY_ELIGIBLE


def test_an_abort_always_surfaces_however_idempotent():
    """Proposing a fresh attempt because a safety control fired is the loop it breaks."""
    plan = plan_recovery(
        status=ActionResultStatus.ABORTED_BY_BRAKE,
        error_category=ErrorCategory.PARKING_BRAKE,
        repeatability="idempotent",
    )
    assert plan.outcome is RecoveryOutcome.SURFACE
    assert plan.retryable is False


def test_the_two_category_sets_partition_the_whole_vocabulary():
    """Every error category is classified exactly once, and the check is live.

    This is the test that fails when somebody adds an `ErrorCategory` and does
    not decide what recovering from it means -- which is the moment to decide
    it, rather than after a new category has silently inherited whichever
    default a denylist would have given it.
    """
    assert not (recovery.TRANSIENT_CATEGORIES & recovery.REFUSAL_CATEGORIES)
    unclassified = set(ErrorCategory) - recovery.TRANSIENT_CATEGORIES - recovery.REFUSAL_CATEGORIES
    assert unclassified == set(), sorted(c.value for c in unclassified)


def test_an_unclassified_category_would_surface_rather_than_retry():
    """An allowlist, not a denylist -- proven against a category that is not one.

    `TRANSIENT_CATEGORIES` is consulted positively, so anything outside it
    surfaces. Demonstrated with a stand-in rather than a real member, because
    the two real sets are exhaustive (above) and a test needs a case to point
    at to mean anything.
    """

    class _Invented:
        value = "something_nobody_classified"

    plan = plan_recovery(
        status=ActionResultStatus.FAILED,
        error_category=_Invented(),
        repeatability="idempotent",
    )
    assert plan.outcome is RecoveryOutcome.SURFACE


def test_every_status_yields_a_plan():
    """Closed vocabulary in, closed vocabulary out. No status falls through."""
    for status in ActionResultStatus:
        plan = plan_recovery(status=status, error_category=None, repeatability="idempotent")
        assert plan.outcome in set(RecoveryOutcome)
        assert plan.reason


def test_every_retry_eligible_plan_demands_a_new_approval():
    """There is no re-authorisation shortcut, and the payload says so."""
    for status in ActionResultStatus:
        for category in [None, *ErrorCategory]:
            plan = plan_recovery(
                status=status,
                error_category=category,
                repeatability="idempotent",
            )
            if plan.retryable:
                assert plan.requires_new_approval is True


def test_the_policy_module_cannot_act_on_its_own_advice():
    """Structural: nothing in `recovery.py` can dispatch, write or re-lease.

    The property that makes `RETRY_ELIGIBLE` safe to publish. A module that
    both decided a retry was warranted and could carry one out would be an
    autonomous retry loop wearing a policy's name.
    """
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "bartholomew" / "actuation" / "recovery.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "dataclasses", "enum", "result"}, sorted(imported)

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for forbidden in ("try_lease", "create_action", "mark_approved", "execute", "dispatch"):
        assert forbidden not in called


# ---------------------------------------------------------------------------
# and it reaches the surfaces
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self, db_path):
        from bartholomew.kernel.memory_store import MemoryStore

        self.mem = MemoryStore(db_path)
        self.db_path = db_path
        self.identity_context = None
        self.governance_store = None
        self.blocking_executor = None


@pytest.fixture
def db_path(tmp_path):
    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.orchestrator.safety import governance_store as gs

    path = str(tmp_path / "recovery.db")
    asyncio.run(MemoryStore(path).init())
    gs.ensure_schema(path)
    store.ensure_schema(path)
    return path


@pytest.fixture
def ctx(db_path):
    return _Ctx(db_path)


@pytest.fixture
def registry():
    device = devices.EnrolledDevice(
        device_id=DEVICE,
        tenant_id=TENANT,
        platform="windows",
        enrolled=True,
        capabilities=tuple(devices.DeclaredCapability(kind=k, version=1) for k in ALL_CAPABILITIES),
        applications=ApplicationAllowlist.from_pairs({"notepad": "C:\\notepad.exe"}),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["C:\\Users\\t\\Documents"]),
        trusted_autonomy=frozenset(),
    )

    class _Registry:
        LABEL = "recovery-test-registry"

        def lookup(self, *, tenant_id, device_id):
            return device if (tenant_id, device_id) == (TENANT, DEVICE) else None

    reg = _Registry()
    devices.install_registry(reg)
    yield reg
    devices.install_registry(None)


@pytest.fixture(autouse=True)
def armed_channel():
    arming.arm(tenant_id=TENANT, device_id=DEVICE, armed_by="test-fixture", reason="recovery suite")
    yield
    arming.reset_for_tests()


async def _lease_one(ctx, registry, **overrides):
    kwargs = {
        "tenant_id": TENANT,
        "device_id": DEVICE,
        "requested_by": "taylor",
        "capability": "windows.focus_window",
        "capability_version": 1,
        "parameters": {"app_id": "notepad"},
        "registry": registry,
    }
    kwargs.update(overrides)
    requested = await seam.run_action_request_through_runtime_contract(ctx, **kwargs)
    action_id = requested.action.action_id
    await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        approver="taylor",
        registry=registry,
    )
    leased = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        registry=registry,
    )
    assert leased.governance_allowed, leased.reason
    return action_id


@pytest.mark.asyncio
async def test_the_seam_returns_a_plan_with_every_outcome(ctx, registry):
    action_id = await _lease_one(ctx, registry)

    result = await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        status="unknown",
        error_category="effect_unverifiable",
        detail="the effect could not be read back",
        evidence={},
        observed_at="",
    )

    assert result.recovery is not None
    assert result.recovery.outcome is RecoveryOutcome.SURFACE
    assert result.recovery.as_dict()["requires_new_approval"] is False


@pytest.mark.asyncio
async def test_a_retry_eligible_plan_still_cannot_re_lease_the_action(ctx, registry, db_path):
    """The second property, against the real state machine.

    `windows.focus_window` is idempotent-eligible and this request declares it
    so, which makes an `unknown` on it genuinely retry-eligible. The plan says
    so -- and the action it describes is terminal, and terminal has no
    transition out of it. Re-dispatching is refused; the row is unchanged; the
    honest way to try again is a new request through the whole envelope.
    """
    action_id = await _lease_one(ctx, registry, repeatability="idempotent")

    result = await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        status="unknown",
        error_category="effect_unverifiable",
        detail="whether a page rendered is not observable",
        evidence={},
        observed_at="",
    )
    assert result.recovery.outcome is RecoveryOutcome.RETRY_ELIGIBLE
    assert result.recovery.requires_new_approval is True

    again = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        registry=registry,
    )
    assert again.governance_allowed is False
    assert store.get_action(db_path, tenant_id=TENANT, action_id=action_id).state is (
        ActionState.UNKNOWN
    )


@pytest.mark.asyncio
async def test_a_fresh_request_for_the_same_thing_starts_at_pending_approval(ctx, registry):
    """What "retry" actually means: a new action, unapproved, from the top."""
    first = await _lease_one(ctx, registry, repeatability="idempotent")
    await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=first,
        status="unknown",
        error_category="effect_unverifiable",
        detail="",
        evidence={},
        observed_at="",
    )

    retry = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        registry=registry,
    )
    assert retry.governance_allowed
    assert retry.action.action_id != first
    assert retry.action.state is ActionState.PENDING_APPROVAL


# ---------------------------------------------------------------------------
# the abandoned lease
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_abandoned_lease_sweeps_to_unknown_not_to_cancelled(ctx, registry, db_path):
    """A device took the action and we never heard back. That is `unknown`.

    Not `cancelled`: an action that really launched a program, recorded as one
    that never ran, is the specific fiction this sweep was rewritten to avoid.
    """
    action_id = await _lease_one(ctx, registry)

    stale = to_iso(utc_now() - timedelta(seconds=store.LEASE_GRACE_SECONDS + 60))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE windows_action_requests SET expires_at = ? WHERE action_id = ?",
            (stale, action_id),
        )
        conn.commit()

    assert store.expire_overdue(db_path, tenant_id=TENANT) == 1

    action = store.get_action(db_path, tenant_id=TENANT, action_id=action_id)
    assert action.state is ActionState.UNKNOWN
    assert action.terminal is True
    assert action.parameters is None


@pytest.mark.asyncio
async def test_a_lease_inside_its_grace_period_is_left_alone(ctx, registry, db_path):
    """The non-vacuity pair: a device mid-flight is not swept out from under."""
    action_id = await _lease_one(ctx, registry)

    just_expired = to_iso(utc_now() - timedelta(seconds=5))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE windows_action_requests SET expires_at = ? WHERE action_id = ?",
            (just_expired, action_id),
        )
        conn.commit()

    store.expire_overdue(db_path, tenant_id=TENANT)

    assert store.get_action(db_path, tenant_id=TENANT, action_id=action_id).state is (
        ActionState.LEASED
    )


@pytest.mark.asyncio
async def test_a_swept_lease_yields_a_recovery_plan_too(ctx, registry, db_path):
    """An action nobody reported still has a defined next step."""
    action_id = await _lease_one(ctx, registry)
    stale = to_iso(utc_now() - timedelta(seconds=store.LEASE_GRACE_SECONDS + 60))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE windows_action_requests SET expires_at = ? WHERE action_id = ?",
            (stale, action_id),
        )
        conn.commit()
    store.expire_overdue(db_path, tenant_id=TENANT)

    action = store.get_action(db_path, tenant_id=TENANT, action_id=action_id)
    plan = plan_recovery(
        status=action.status,
        error_category=ErrorCategory(action.state_reason),
        repeatability=action.repeatability,
    )
    assert plan.outcome is RecoveryOutcome.SURFACE
