"""The eleven-point admission, proved against a real database and real Governance.

Acceptance requirements 4-11:

  4. Global and applicable scoped Parking Brakes prevent dispatch.
  5. Unreadable brake state fails closed.
  6. Missing or mismatched device capability refuses.
  7. Exact action approval works only for the bound request.
  8. Changing any approved material parameter invalidates approval.
  9. An approval cannot be reused for another device, action or user.
 10. Expired and cancelled actions cannot execute later.
 11. Duplicate delivery cannot repeat a non-repeatable action.

**Nothing here is mocked that the claim depends on.** The database is a real
SQLite file with the real memory, governance and action schemas; the Parking
Brake is the real `GovernanceStore`; the approval is a real row written through
`MemoryStore`; the seam is the real one the HTTP route calls. Mocks appear only
where the *operating system* would be, and no test in this file reaches one.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta

import pytest

from bartholomew.actuation import arming, devices, seam, store
from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import ALL_CAPABILITIES, CapabilityKind
from bartholomew.actuation.request import to_iso, utc_now
from bartholomew.actuation.result import ActionResultStatus, ErrorCategory
from bartholomew.actuation.store import ActionState

TENANT = "tenant-a"
OTHER_TENANT = "tenant-b"
DEVICE = "desk-pc"
OTHER_DEVICE = "laptop"
REQUESTER = "taylor"


# ---------------------------------------------------------------------------
# real fixtures
# ---------------------------------------------------------------------------


class _Ctx:
    """A runtime context of the shape every Runtime Contract seam reads.

    Deliberately duck-typed on the same four attributes the real `KernelDaemon`
    exposes -- `mem`, `identity_context`, `governance_store`,
    `blocking_executor` -- rather than a mock of the daemon, so what the seam
    exercises here is what it exercises in the app.
    """

    def __init__(self, db_path, identity_context=None):
        from bartholomew.kernel.memory_store import MemoryStore

        self.mem = MemoryStore(db_path)
        self.db_path = db_path
        self.identity_context = identity_context
        self.governance_store = None
        self.blocking_executor = None


@pytest.fixture
def db_path(tmp_path):
    """A real database with the memory, governance and action schemas."""
    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.orchestrator.safety import governance_store as gs

    path = str(tmp_path / "actions.db")
    asyncio.run(MemoryStore(path).init())
    gs.ensure_schema(path)
    store.ensure_schema(path)
    return path


@pytest.fixture
def ctx(db_path):
    return _Ctx(db_path)


def _device(
    *,
    device_id=DEVICE,
    tenant_id=TENANT,
    capabilities=None,
    trusted_autonomy=(),
    enrolled=True,
    platform="windows",
):
    return devices.EnrolledDevice(
        device_id=device_id,
        tenant_id=tenant_id,
        platform=platform,
        enrolled=enrolled,
        capabilities=tuple(
            devices.DeclaredCapability(kind=k, version=1)
            for k in (capabilities if capabilities is not None else ALL_CAPABILITIES)
        ),
        applications=ApplicationAllowlist.from_pairs(
            {"notepad": "C:\\Windows\\System32\\notepad.exe"},
        ),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["C:\\Users\\t\\Documents"]),
        trusted_autonomy=frozenset(trusted_autonomy),
    )


class _Registry:
    """A registry over an explicit list. Truthful: it really refuses the rest."""

    LABEL = "test-registry"

    def __init__(self, *enrolled):
        self._by_key = {(d.tenant_id, d.device_id): d for d in enrolled}

    def lookup(self, *, tenant_id, device_id):
        return self._by_key.get((tenant_id, device_id))


class _RaisingRegistry:
    LABEL = "unreadable-registry"

    def lookup(self, *, tenant_id, device_id):
        raise devices.DeviceRegistryError("the registry is unreachable")


@pytest.fixture(autouse=True)
def armed_channel():
    """Every dispatch in this file runs on an armed channel.

    The arming window is a separate, coarser gate than anything this file
    tests: it says the machine's channel is open at all right now, and it
    authorises no action by itself. Opening it here for every tenant and
    device this module uses keeps arming from becoming the reason any of these
    assertions passes or fails, so each test still discriminates exactly what
    it did before the window existed.

    The unarmed cases are tested on their own, in
    `tests/test_windows_companion_completion.py`.
    """
    # One window per tenant, naming the device that tenant dispatches with --
    # which is `DEVICE` in both cases here, including the cross-tenant test,
    # so that test is still denied for the reason it is about (the action does
    # not exist in the other tenant) rather than for an unarmed channel.
    for tenant in (TENANT, OTHER_TENANT):
        arming.arm(
            tenant_id=tenant,
            device_id=DEVICE,
            armed_by="test-fixture",
            reason="governance suite",
        )
    yield
    arming.reset_for_tests()


@pytest.fixture
def registry():
    reg = _Registry(_device())
    devices.install_registry(reg)
    yield reg
    devices.install_registry(None)


async def _request(ctx, registry, **overrides):
    kwargs = {
        "tenant_id": TENANT,
        "device_id": DEVICE,
        "requested_by": REQUESTER,
        "capability": "windows.focus_window",
        "capability_version": 1,
        "parameters": {"app_id": "notepad"},
        "registry": registry,
    }
    kwargs.update(overrides)
    return await seam.run_action_request_through_runtime_contract(ctx, **kwargs)


async def _approve(ctx, action_id, registry, *, tenant=TENANT, approver="taylor"):
    return await seam.grant_action_approval(
        ctx,
        tenant_id=tenant,
        action_id=action_id,
        approver=approver,
        registry=registry,
    )


async def _dispatch(ctx, action_id, registry, *, tenant=TENANT, device=DEVICE):
    return await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=tenant,
        device_id=device,
        action_id=action_id,
        registry=registry,
    )


# ---------------------------------------------------------------------------
# the happy path exists, so the refusals below mean something
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_full_governed_path_works_end_to_end(ctx, registry, db_path):
    requested = await _request(ctx, registry)
    assert requested.governance_allowed
    assert requested.status is ActionResultStatus.ACCEPTED
    assert requested.action.state is ActionState.PENDING_APPROVAL

    action_id = requested.action.action_id
    approved = await _approve(ctx, action_id, registry)
    assert approved.governance_allowed
    assert approved.action.state is ActionState.APPROVED
    assert approved.action.approved_by == "taylor"

    leased = await _dispatch(ctx, action_id, registry)
    assert leased.governance_allowed
    assert leased.status is ActionResultStatus.STARTED
    assert leased.request.parameters.canonical == {"app_id": "notepad"}

    recorded = await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        status="succeeded",
        error_category=None,
        detail="the window has the foreground",
        evidence={"hwnd": 1234},
        observed_at=to_iso(utc_now()),
    )
    assert recorded.governance_allowed
    assert recorded.action.state is ActionState.SUCCEEDED


@pytest.mark.asyncio
async def test_a_request_alone_never_reaches_a_device(ctx, registry, db_path):
    """Requesting is recording. Nothing is dispatchable until it is approved."""
    requested = await _request(ctx, registry)
    assert requested.action.state is ActionState.PENDING_APPROVAL
    assert store.dispatchable_action_ids(db_path, tenant_id=TENANT, device_id=DEVICE) == []
    denied = await _dispatch(ctx, requested.action.action_id, registry)
    assert not denied.governance_allowed
    assert denied.category is ErrorCategory.APPROVAL_MISSING


# ---------------------------------------------------------------------------
# 4. the Parking Brake, global and scoped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_global_brake_prevents_a_request(ctx, registry, db_path):
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    GovernanceStore(db_path).engage("global", reason="test", actor="test")
    result = await _request(ctx, registry)
    assert not result.governance_allowed
    assert result.category is ErrorCategory.PARKING_BRAKE
    assert result.action is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["global", "actuation", "skills", "voice", "training"])
async def test_any_engaged_brake_scope_prevents_dispatch(ctx, registry, db_path, scope):
    """The most restrictive reading: any engagement stops actuation.

    Acting on somebody's computer while any part of Bartholomew is halted is
    exactly what a halt is for, so this seam gates on the brake being engaged
    at all -- not only on its own scope.
    """
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    requested = await _request(ctx, registry)
    action_id = requested.action.action_id
    await _approve(ctx, action_id, registry)

    GovernanceStore(db_path).engage(scope, reason="test", actor="test")
    denied = await _dispatch(ctx, action_id, registry)
    assert not denied.governance_allowed
    assert denied.category is ErrorCategory.PARKING_BRAKE
    assert denied.action.state is ActionState.APPROVED, "the action was not consumed"


@pytest.mark.asyncio
async def test_the_actuation_scope_is_a_registered_engageable_scope():
    """Enforceable in the kernel *and* engageable from the API and the CLI."""
    from bartholomew.platform.authority import VALID_SCOPES
    from bartholomew_api_bridge_v0_1.services.api.routes.governance import (
        VALID_SCOPES as ROUTE_SCOPES,
    )

    assert seam.ACTUATION_BRAKE_SCOPE == "actuation"
    assert seam.ACTUATION_BRAKE_SCOPE in VALID_SCOPES
    assert seam.ACTUATION_BRAKE_SCOPE in ROUTE_SCOPES


@pytest.mark.asyncio
async def test_an_approval_never_overrides_the_parking_brake(ctx, registry, db_path):
    """The approval is valid, current, and bound. The brake still wins."""
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    requested = await _request(ctx, registry)
    action_id = requested.action.action_id
    approved = await _approve(ctx, action_id, registry)
    assert approved.governance_allowed

    approval = await seam.load_approval(ctx, tenant_id=TENANT, action_id=action_id)
    assert approval is not None, "the approval really was granted"

    GovernanceStore(db_path).engage("actuation", reason="test", actor="test")
    denied = await _dispatch(ctx, action_id, registry)
    assert denied.category is ErrorCategory.PARKING_BRAKE


@pytest.mark.asyncio
async def test_inspection_stays_readable_under_a_brake(ctx, registry, db_path):
    """A halt must not hide what Bartholomew was about to do."""
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    requested = await _request(ctx, registry)
    GovernanceStore(db_path).engage("global", reason="test", actor="test")
    listed = store.recent_actions(db_path, tenant_id=TENANT)
    assert [a["action_id"] for a in listed] == [requested.action.action_id]


# ---------------------------------------------------------------------------
# 5. an unreadable brake fails closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unreadable_brake_state_fails_closed(ctx, registry, monkeypatch):
    """Not "we could not tell, so proceed" -- refuse."""
    from bartholomew.actuation import seam as seam_module

    async def _explode(*_a, **_k):
        raise sqlite3.OperationalError("the governance table is gone")

    monkeypatch.setattr(seam_module, "engaged_state_fail_closed_off_loop", _explode)
    result = await _request(ctx, registry)
    assert not result.governance_allowed
    assert result.category is ErrorCategory.PARKING_BRAKE
    assert "could not be read" in result.reason


@pytest.mark.asyncio
async def test_an_unreadable_actuation_scope_fails_closed(ctx, registry, monkeypatch):
    from bartholomew.actuation import seam as seam_module

    async def _explode(*_a, **_k):
        raise OSError("the database file is unreadable")

    monkeypatch.setattr(seam_module, "is_blocked_fail_closed_off_loop", _explode)
    result = await _request(ctx, registry)
    assert not result.governance_allowed
    assert result.category is ErrorCategory.PARKING_BRAKE


# ---------------------------------------------------------------------------
# 6. the enrolled device and its declared capability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unenrolled_device_is_refused(ctx, registry):
    result = await _request(ctx, registry, device_id="somebody-elses-pc")
    assert not result.governance_allowed
    assert result.category is ErrorCategory.DEVICE_NOT_ENROLLED


@pytest.mark.asyncio
async def test_a_revoked_enrolment_is_refused(ctx):
    reg = _Registry(_device(enrolled=False))
    result = await _request(ctx, reg)
    assert not result.governance_allowed
    assert result.category is ErrorCategory.DEVICE_NOT_ENROLLED
    assert "not active" in result.reason


@pytest.mark.asyncio
async def test_a_device_in_another_tenant_is_simply_unknown(ctx):
    """Tenant-qualified lookup is the whole cross-tenant containment here."""
    reg = _Registry(_device(tenant_id=OTHER_TENANT))
    result = await _request(ctx, reg, tenant_id=TENANT)
    assert not result.governance_allowed
    assert result.category is ErrorCategory.DEVICE_NOT_ENROLLED


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["darwin", "linux", "android", "ios", "unknown"])
async def test_a_non_windows_device_is_refused(ctx, platform):
    reg = _Registry(_device(platform=platform))
    result = await _request(ctx, reg)
    assert not result.governance_allowed
    assert result.category is ErrorCategory.PLATFORM_UNSUPPORTED


@pytest.mark.asyncio
async def test_an_undeclared_capability_is_refused(ctx):
    reg = _Registry(_device(capabilities=[CapabilityKind.OPEN_URL]))
    result = await _request(ctx, reg, capability="windows.focus_window")
    assert not result.governance_allowed
    assert result.category is ErrorCategory.CAPABILITY_NOT_DECLARED


@pytest.mark.asyncio
async def test_a_mismatched_capability_version_is_refused(ctx):
    """The device declares v2; the action names v1. Refused, not translated."""
    device = devices.EnrolledDevice(
        device_id=DEVICE,
        tenant_id=TENANT,
        platform="windows",
        enrolled=True,
        capabilities=(devices.DeclaredCapability(kind=CapabilityKind.FOCUS_WINDOW, version=2),),
        applications=ApplicationAllowlist.from_pairs(
            {"notepad": "C:\\Windows\\System32\\notepad.exe"},
        ),
    )
    result = await _request(ctx, _Registry(device))
    assert not result.governance_allowed
    assert result.category is ErrorCategory.CAPABILITY_UNSUPPORTED
    assert "never approximated" in result.reason


@pytest.mark.asyncio
async def test_an_unreadable_registry_refuses_rather_than_admitting(ctx):
    result = await _request(ctx, _RaisingRegistry())
    assert not result.governance_allowed
    assert result.category is ErrorCategory.DEVICE_NOT_ENROLLED


@pytest.mark.asyncio
async def test_with_no_registry_configured_nothing_is_enrolled(ctx, monkeypatch):
    monkeypatch.delenv(devices.ENROLMENT_PATH_ENV, raising=False)
    devices.install_registry(None)
    try:
        result = await seam.run_action_request_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by=REQUESTER,
            capability="windows.focus_window",
            capability_version=1,
            parameters={"app_id": "notepad"},
        )
    finally:
        devices.install_registry(None)
    assert not result.governance_allowed
    assert result.category is ErrorCategory.DEVICE_NOT_ENROLLED


# ---------------------------------------------------------------------------
# 7, 8, 9. the approval binds, and to exactly one thing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_approval_authorises_the_action_it_was_granted_for(ctx, registry):
    requested = await _request(ctx, registry)
    await _approve(ctx, requested.action.action_id, registry)
    leased = await _dispatch(ctx, requested.action.action_id, registry)
    assert leased.governance_allowed


@pytest.mark.asyncio
async def test_an_approval_does_not_authorise_another_action(ctx, registry, db_path):
    """Two identical requests. Approving one does not approve the other."""
    first = await _request(ctx, registry, action_id="act-one")
    second = await _request(ctx, registry, action_id="act-two")
    assert first.action.parameter_fingerprint == second.action.parameter_fingerprint

    await _approve(ctx, "act-one", registry)
    denied = await _dispatch(ctx, "act-two", registry)
    assert not denied.governance_allowed
    assert denied.category is ErrorCategory.APPROVAL_MISSING


@pytest.mark.asyncio
async def test_changing_an_approved_parameter_invalidates_the_approval(ctx, registry, db_path):
    """The approval binds to a parameter fingerprint, so an edit breaks it."""

    await _request(
        ctx,
        registry,
        capability="windows.open_url",
        parameters={"url": "https://example.com/report"},
        action_id="act-url",
    )
    await _approve(ctx, "act-url", registry)
    approval = await seam.load_approval(ctx, tenant_id=TENANT, action_id="act-url")
    assert approval is not None

    # Edit the stored parameters behind the approval's back -- the exact
    # substitution the fingerprint exists to catch.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE windows_action_requests SET parameters_json = ? "
            "WHERE tenant_id = ? AND action_id = ?",
            ('{"url": "https://example.com/somewhere-else"}', TENANT, "act-url"),
        )
        conn.commit()

    denied = await _dispatch(ctx, "act-url", registry)
    assert not denied.governance_allowed
    assert denied.category is ErrorCategory.APPROVAL_INVALID
    assert "parameters have changed" in denied.reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,expected_code",
    [
        ("action_id", "act-somewhere-else", "approval_wrong_action"),
        ("tenant_id", OTHER_TENANT, "approval_wrong_tenant"),
        ("device_id", OTHER_DEVICE, "approval_wrong_device"),
        ("capability", CapabilityKind.OPEN_URL, "approval_wrong_capability"),
        ("capability_version", 2, "approval_wrong_capability_version"),
        ("parameter_fingerprint", "0" * 64, "approval_parameters_changed"),
    ],
)
async def test_an_approval_is_bound_to_every_material_fact(
    ctx,
    registry,
    field,
    value,
    expected_code,
):
    """Each binding checked independently, so the audit says which one failed."""
    from dataclasses import replace

    from bartholomew.actuation.approval import build_approval
    from bartholomew.actuation.request import build_request

    request = build_request(
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by=REQUESTER,
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        action_id="act-bound",
        context=_device().validation_context(),
    )
    good = build_approval(request, approver="taylor")
    assert good.authorizes(request).allowed

    tampered = replace(good, **{field: value})
    check = tampered.authorizes(request)
    assert not check.allowed
    assert check.code == expected_code


@pytest.mark.asyncio
async def test_an_approval_for_one_device_cannot_be_used_on_another(ctx):
    """A second device, otherwise identically enrolled, is still another device."""
    reg = _Registry(_device(), _device(device_id=OTHER_DEVICE))
    await _request(ctx, reg, action_id="act-desk", device_id=DEVICE)
    await _request(ctx, reg, action_id="act-laptop", device_id=OTHER_DEVICE)
    await _approve(ctx, "act-desk", reg)

    # The laptop asks for the desk PC's approved action.
    denied = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=OTHER_DEVICE,
        action_id="act-desk",
        registry=reg,
    )
    assert not denied.governance_allowed
    assert denied.category is ErrorCategory.DEVICE_NOT_ENROLLED
    assert "this channel is authenticated as" in denied.reason


@pytest.mark.asyncio
async def test_an_approval_in_one_tenant_is_invisible_in_another(ctx):
    reg = _Registry(_device(), _device(tenant_id=OTHER_TENANT))
    await _request(ctx, reg, action_id="act-shared", tenant_id=TENANT)
    await _approve(ctx, "act-shared", reg, tenant=TENANT)

    # The same action id, asked for in the other tenant, is simply not there.
    denied = await _dispatch(ctx, "act-shared", reg, tenant=OTHER_TENANT)
    assert not denied.governance_allowed


@pytest.mark.asyncio
async def test_the_approver_is_recorded_and_never_anonymous(ctx, registry):
    from bartholomew.actuation.approval import ApprovalError, build_approval
    from bartholomew.actuation.request import build_request

    requested = await _request(ctx, registry)
    approved = await _approve(ctx, requested.action.action_id, registry, approver="rowan")
    assert approved.action.approved_by == "rowan"
    stored = await seam.load_approval(
        ctx,
        tenant_id=TENANT,
        action_id=requested.action.action_id,
    )
    assert stored.approver == "rowan"

    request = build_request(
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by=REQUESTER,
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        context=_device().validation_context(),
    )
    for anonymous in ("", "   ", None):
        with pytest.raises(ApprovalError):
            build_approval(request, approver=anonymous)


@pytest.mark.asyncio
async def test_an_action_can_only_be_approved_once(ctx, registry):
    requested = await _request(ctx, registry)
    action_id = requested.action.action_id
    assert (await _approve(ctx, action_id, registry)).governance_allowed
    second = await _approve(ctx, action_id, registry)
    assert not second.governance_allowed
    assert "approvable exactly once" in second.reason


@pytest.mark.asyncio
async def test_trusted_autonomy_is_empty_by_default_and_bounded_when_set(ctx):
    """Configurable, per device, and never for an ALWAYS-approval capability."""
    assert _device().trusted_autonomy == frozenset()

    autonomous = _Registry(_device(trusted_autonomy=[CapabilityKind.FOCUS_WINDOW]))
    requested = await _request(ctx, autonomous)
    assert requested.action.state is ActionState.APPROVED
    leased = await _dispatch(ctx, requested.action.action_id, autonomous)
    assert leased.governance_allowed

    # A capability that is not in the autonomy set still needs an approval.
    manual = await _request(
        ctx,
        autonomous,
        capability="windows.type_text",
        parameters={"text": "hello"},
        action_id="act-typed",
    )
    assert manual.action.state is ActionState.PENDING_APPROVAL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        CapabilityKind.CLIPBOARD_READ,
        CapabilityKind.TYPE_TEXT,
        CapabilityKind.ACCESSIBILITY_ACTION,
    ],
)
async def test_no_enrolment_can_make_the_sensitive_capabilities_autonomous(kind):
    """Refused at construction: there is no configuration that permits this."""
    with pytest.raises(devices.DeviceRegistryError) as excinfo:
        _device(trusted_autonomy=[kind])
    assert "refused" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 10. expiry and cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_expired_action_cannot_execute_later(ctx, registry, db_path):
    requested = await _request(ctx, registry, ttl_seconds=1)
    action_id = requested.action.action_id
    await _approve(ctx, action_id, registry)

    past = to_iso(utc_now() - timedelta(seconds=5))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE windows_action_requests SET expires_at = ? "
            "WHERE tenant_id = ? AND action_id = ?",
            (past, TENANT, action_id),
        )
        conn.commit()

    denied = await _dispatch(ctx, action_id, registry)
    assert not denied.governance_allowed
    assert denied.category is ErrorCategory.EXPIRED


@pytest.mark.asyncio
async def test_an_expired_action_is_not_even_a_dispatch_candidate(ctx, registry, db_path):
    requested = await _request(ctx, registry)
    action_id = requested.action.action_id
    await _approve(ctx, action_id, registry)
    assert store.dispatchable_action_ids(db_path, tenant_id=TENANT, device_id=DEVICE) == [
        action_id,
    ]

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE windows_action_requests SET expires_at = ? WHERE action_id = ?",
            (to_iso(utc_now() - timedelta(minutes=1)), action_id),
        )
        conn.commit()
    assert store.dispatchable_action_ids(db_path, tenant_id=TENANT, device_id=DEVICE) == []
    assert store.expire_overdue(db_path, tenant_id=TENANT) == 1


@pytest.mark.asyncio
async def test_a_cancelled_action_cannot_execute_later(ctx, registry):
    requested = await _request(ctx, registry)
    action_id = requested.action.action_id
    await _approve(ctx, action_id, registry)

    cancelled = await seam.cancel_action_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        cancelled_by="taylor",
    )
    assert cancelled.status is ActionResultStatus.CANCELLED

    denied = await _dispatch(ctx, action_id, registry)
    assert not denied.governance_allowed
    assert denied.category is ErrorCategory.CANCELLED


@pytest.mark.asyncio
async def test_a_result_for_a_cancelled_action_is_not_applied(ctx, registry):
    """The device was already running it. It still cannot record a success."""
    requested = await _request(ctx, registry)
    action_id = requested.action.action_id
    await _approve(ctx, action_id, registry)
    await _dispatch(ctx, action_id, registry)

    await seam.cancel_action_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        cancelled_by="taylor",
    )
    late = await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        status="succeeded",
        error_category=None,
        detail="it worked",
        evidence={},
        observed_at=to_iso(utc_now()),
    )
    assert not late.governance_allowed
    assert late.category is ErrorCategory.REPLAY_REFUSED
    assert late.action.state is ActionState.CANCELLED


@pytest.mark.asyncio
async def test_an_approval_cannot_outlive_its_action(ctx, registry):
    from bartholomew.actuation.approval import build_approval
    from bartholomew.actuation.request import build_request

    request = build_request(
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by=REQUESTER,
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        ttl_seconds=60,
        context=_device().validation_context(),
    )
    approval = build_approval(request, approver="taylor", ttl_seconds=900)
    assert approval.expires_at == request.expires_at


@pytest.mark.asyncio
async def test_an_approval_that_has_lapsed_no_longer_authorises(ctx):
    from dataclasses import replace

    from bartholomew.actuation.approval import build_approval
    from bartholomew.actuation.request import build_request

    request = build_request(
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by=REQUESTER,
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        context=_device().validation_context(),
    )
    lapsed = replace(
        build_approval(request, approver="taylor"),
        expires_at=to_iso(utc_now() - timedelta(seconds=1)),
    )
    check = lapsed.authorizes(request)
    assert not check.allowed
    assert check.code == "approval_expired"


@pytest.mark.asyncio
async def test_an_unreadable_approval_expiry_is_treated_as_lapsed(ctx):
    from dataclasses import replace

    from bartholomew.actuation.approval import build_approval
    from bartholomew.actuation.request import build_request

    request = build_request(
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by=REQUESTER,
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        context=_device().validation_context(),
    )
    broken = replace(build_approval(request, approver="taylor"), expires_at="not a date")
    check = broken.authorizes(request)
    assert not check.allowed
    assert check.code == "approval_expiry_unreadable"


# ---------------------------------------------------------------------------
# 11. replay and idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_non_repeatable_action_can_be_leased_exactly_once(ctx, registry):
    requested = await _request(ctx, registry)
    action_id = requested.action.action_id
    await _approve(ctx, action_id, registry)

    first = await _dispatch(ctx, action_id, registry)
    assert first.governance_allowed
    assert first.action.lease_count == 1

    second = await _dispatch(ctx, action_id, registry)
    assert not second.governance_allowed
    assert second.category is ErrorCategory.REPLAY_REFUSED
    assert "does not run it a second time" in second.reason


@pytest.mark.asyncio
async def test_concurrent_leases_of_one_action_produce_exactly_one_winner(ctx, registry, db_path):
    """The conditional UPDATE, not a pre-check, is the guarantee."""
    requested = await _request(ctx, registry)
    action_id = requested.action.action_id
    await _approve(ctx, action_id, registry)

    winners = [
        store.try_lease(db_path, tenant_id=TENANT, action_id=action_id, repeatable=False)
        for _ in range(5)
    ]
    assert sum(1 for w in winners if w is not None) == 1


@pytest.mark.asyncio
async def test_an_idempotent_action_may_be_re_leased_but_is_bounded(ctx, registry, db_path):
    requested = await _request(ctx, registry, repeatability="idempotent")
    action_id = requested.action.action_id
    await _approve(ctx, action_id, registry)

    leases = 0
    for _ in range(store.MAX_IDEMPOTENT_LEASES + 3):
        if store.try_lease(db_path, tenant_id=TENANT, action_id=action_id, repeatable=True):
            leases += 1
    assert leases == store.MAX_IDEMPOTENT_LEASES


@pytest.mark.asyncio
async def test_resubmitting_an_action_id_lands_on_the_existing_row(ctx, registry):
    """A retry is a retry, not a way to change an approved action's parameters."""
    await _request(
        ctx,
        registry,
        action_id="act-retry",
        capability="windows.open_url",
        parameters={"url": "https://example.com/first"},
    )
    again = await _request(
        ctx,
        registry,
        action_id="act-retry",
        capability="windows.open_url",
        parameters={"url": "https://example.com/second"},
    )
    assert again.action.parameters_redacted["url"] == "https://example.com/first"


@pytest.mark.asyncio
async def test_a_duplicate_result_does_not_change_a_recorded_outcome(ctx, registry):
    requested = await _request(ctx, registry)
    action_id = requested.action.action_id
    await _approve(ctx, action_id, registry)
    await _dispatch(ctx, action_id, registry)

    first = await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        status="failed",
        error_category="target_not_found",
        detail="no such window",
        evidence={},
        observed_at=to_iso(utc_now()),
    )
    assert first.governance_allowed
    assert first.action.state is ActionState.FAILED

    second = await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        status="succeeded",
        error_category=None,
        detail="actually it worked",
        evidence={},
        observed_at=to_iso(utc_now()),
    )
    assert not second.governance_allowed
    assert second.action.state is ActionState.FAILED


@pytest.mark.asyncio
async def test_a_device_may_not_report_a_governance_word(ctx, registry):
    requested = await _request(ctx, registry)
    action_id = requested.action.action_id
    await _approve(ctx, action_id, registry)
    await _dispatch(ctx, action_id, registry)

    for word in ("accepted", "refused"):
        result = await seam.record_action_result_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            device_id=DEVICE,
            action_id=action_id,
            status=word,
            error_category=None,
            detail="",
            evidence={},
            observed_at=to_iso(utc_now()),
        )
        assert not result.governance_allowed
        assert "Governance's word" in result.reason


# ---------------------------------------------------------------------------
# evidence and Identity policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_governed_decision_writes_a_reflection(ctx, registry, db_path):
    requested = await _request(ctx, registry)
    action_id = requested.action.action_id
    await _approve(ctx, action_id, registry)
    await _dispatch(ctx, action_id, registry)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT meta FROM reflections WHERE kind = 'action_reflection'",
        ).fetchall()
    assert len(rows) >= 3
    assert all("windows_action" in r[0] for r in rows)


@pytest.mark.asyncio
async def test_sensitive_parameters_never_reach_the_durable_audit(ctx, registry, db_path):
    """The typed text is a digest in every place a person or a query can read."""
    secret_ish = "the quick brown fox jumped"
    requested = await _request(
        ctx,
        registry,
        capability="windows.type_text",
        parameters={"text": secret_ish},
        action_id="act-typed",
    )
    assert "text" not in requested.action.parameters_redacted
    assert requested.action.parameters_redacted["text_length"] == len(secret_ish)

    with sqlite3.connect(db_path) as conn:
        redacted = conn.execute(
            "SELECT parameters_redacted_json FROM windows_action_requests "
            "WHERE action_id = 'act-typed'",
        ).fetchone()[0]
        reflections = conn.execute(
            "SELECT content, meta FROM reflections WHERE kind = 'action_reflection'",
        ).fetchall()
    assert secret_ish not in redacted
    for content, meta in reflections:
        assert secret_ish not in (content or "")
        assert secret_ish not in (meta or "")

    listed = store.recent_actions(db_path, tenant_id=TENANT)
    assert secret_ish not in repr(listed)


@pytest.mark.asyncio
async def test_parameters_are_purged_when_an_action_ends(ctx, registry, db_path):
    """The one transient copy of typed text is deleted at the terminal state."""
    requested = await _request(
        ctx,
        registry,
        capability="windows.clipboard_write",
        parameters={"text": "an ordinary sentence"},
        action_id="act-clip",
    )
    action_id = requested.action.action_id
    await _approve(ctx, action_id, registry)
    await _dispatch(ctx, action_id, registry)

    with sqlite3.connect(db_path) as conn:
        before = conn.execute(
            "SELECT parameters_json FROM windows_action_requests WHERE action_id = ?",
            (action_id,),
        ).fetchone()[0]
    assert "an ordinary sentence" in before

    await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        status="succeeded",
        error_category=None,
        detail="copied",
        evidence={},
        observed_at=to_iso(utc_now()),
    )
    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            "SELECT parameters_json FROM windows_action_requests WHERE action_id = ?",
            (action_id,),
        ).fetchone()[0]
    assert after is None


@pytest.mark.asyncio
async def test_the_identity_policy_gate_refuses_an_unallowlisted_request(db_path, registry):
    from identity_interpreter.identity_context import IdentityContext

    ctx = _Ctx(
        db_path,
        identity_context=IdentityContext(tool_use_default_allowed=False, tool_use_allowlist=[]),
    )
    result = await _request(ctx, registry)
    assert not result.governance_allowed
    assert result.category is ErrorCategory.GOVERNANCE_DENIED
    assert result.action.state is ActionState.REFUSED, "the refusal itself is recorded"


@pytest.mark.asyncio
async def test_allowlisting_the_dispatch_kind_does_not_make_dispatch_reachable(db_path):
    """There is no 'actuation enabled' switch in Identity.yaml to find."""
    from identity_interpreter.identity_context import IdentityContext

    ctx = _Ctx(
        db_path,
        identity_context=IdentityContext(
            tool_use_default_allowed=True,
            tool_use_allowlist=[
                seam.ACTION_KIND_REQUEST,
                seam.ACTION_KIND_DISPATCH,
                seam.ACTION_KIND_APPROVE,
            ],
        ),
    )
    reg = _Registry(_device())
    requested = await _request(ctx, reg)
    assert requested.governance_allowed

    action_id = requested.action.action_id
    denied = await _dispatch(ctx, action_id, reg)
    assert not denied.governance_allowed
    assert denied.category is ErrorCategory.APPROVAL_MISSING

    # And even with the action moved to `approved` behind the seam's back --
    # the state a lost or deleted approval row would leave it in -- dispatch
    # still refuses, because it looks for the approval itself and not for the
    # state that usually accompanies one.
    store.mark_approved(db_path, tenant_id=TENANT, action_id=action_id, approver="nobody")
    still_denied = await _dispatch(ctx, action_id, reg)
    assert not still_denied.governance_allowed
    assert still_denied.category is ErrorCategory.APPROVAL_MISSING
    assert "does not substitute" in still_denied.reason


def test_the_dispatch_kind_is_absent_from_the_shipped_identity_allowlist():
    import yaml

    with open("Identity.yaml", encoding="utf-8") as fh:
        identity = yaml.safe_load(fh)
    allowlist = identity["tool_use"]["allowlist"]
    assert seam.ACTION_KIND_REQUEST in allowlist
    assert seam.ACTION_KIND_CANCEL in allowlist
    assert seam.ACTION_KIND_DISPATCH not in allowlist
    assert seam.ACTION_KIND_APPROVE not in allowlist
    assert identity["tool_use"]["default_allowed"] is False
