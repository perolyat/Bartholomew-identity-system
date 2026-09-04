"""The Windows companion completion package: authentication, start/stop, arming.

Three surfaces are crossed here for the first time, and each one is a place a
mistake would be expensive: a companion authenticating to the control plane, a
production way to begin observing, and a bounded window in which real Windows
actuation is possible at all.

Everything runs against real stores -- a real control-plane database, a real
enrolment ceremony, a real Parking Brake -- because the properties under test
are properties of those, and a mock would assert only that the test author
remembered them.
"""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bartholomew.actuation import arming
from bartholomew.platform import accounts, devices
from bartholomew.platform.device_inbound import DEVICE_CREDENTIAL_HEADER
from bartholomew.platform.principal import PrincipalKind
from bartholomew.platform.store import init_platform_schema

PASSWORD = "completion-package-password"

WINDOWS_MANIFEST = {
    "platform": "windows",
    "companion_version": "0.1.0-prototype",
    "capabilities": [
        {"kind": "windows.focus_window", "version": 1},
        {"kind": "windows.type_text", "version": 1},
        {"kind": "multimodal.screen_capture", "version": 1},
    ],
}

#: A device that is enrolled and healthy but declares no Windows actuation at
#: all -- the "incapable device" case.
OBSERVE_ONLY_MANIFEST = {
    "platform": "windows",
    "companion_version": "0.1.0-prototype",
    "capabilities": [{"kind": "multimodal.screen_capture", "version": 1}],
}


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="completion-pkg-")
    mp.setenv("BARTH_PLATFORM_DB_PATH", str(Path(tmp) / "platform.db"))
    mp.setenv("BARTH_DATA_ROOT", str(Path(tmp) / "data"))
    yield
    mp.undo()


@pytest.fixture(scope="module", autouse=True)
def _schema():
    init_platform_schema()


@pytest.fixture(scope="module")
def users():
    init_platform_schema()
    made = {}
    for name in ("hilda", "ivan"):
        try:
            made[name] = accounts.create_account(name, PASSWORD, kind=PrincipalKind.USER)
        except accounts.AccountError:
            made[name] = next(
                a["user_id"] for a in accounts.list_accounts() if a["username"] == name
            )
    return made


def _enrol(user_id: str, name: str, manifest: dict | None = None) -> tuple[str, str]:
    device_id = devices.create_pending_enrolment(user_id, name, platform="windows")
    issued = devices.approve_enrolment(device_id, approver="ops")
    _verified, credential = devices.complete_enrolment(
        issued.secret,
        dict(manifest or WINDOWS_MANIFEST),
    )
    return device_id, credential.secret


@pytest.fixture(autouse=True)
def _disarmed():
    """Every test starts and ends with a closed channel."""
    arming.reset_for_tests()
    yield
    arming.reset_for_tests()


# ===========================================================================
# Authentication -- the companion proves which machine, and only that
# ===========================================================================


def _companion(request_headers: dict | None = None):
    """Run `require_companion` against a stub request carrying these headers."""
    from bartholomew_api_bridge_v0_1.services.api.companion_auth import require_companion

    class _Req:
        def __init__(self, headers):
            self.headers = headers or {}

    return require_companion(_Req(request_headers))


def test_a_correct_device_credential_authenticates(users):
    """The happy path exists, so the refusals below mean something."""
    device_id, secret = _enrol(users["hilda"], "hilda-desk")

    companion = _companion({DEVICE_CREDENTIAL_HEADER: secret})
    assert companion.device_id == device_id
    assert companion.owner_user_id == users["hilda"]
    assert companion.platform == "windows"
    # The tenant came from the enrolment row, not from anything the caller said.
    assert companion.describe()["verified_by"] == "device_credential"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {DEVICE_CREDENTIAL_HEADER: ""},
        {DEVICE_CREDENTIAL_HEADER: "   "},
        {DEVICE_CREDENTIAL_HEADER: "not-a-real-credential"},
        {DEVICE_CREDENTIAL_HEADER: "a" * 600},
    ],
)
def test_missing_or_wrong_credentials_fail_closed(users, headers):
    """Absent, blank and wrong all refuse, and refuse identically.

    One message for every case on purpose: a prober must not be able to tell
    "no such device" from "wrong secret" by reading the refusal.
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        _companion(headers)
    assert caught.value.status_code == 401


def test_a_revoked_device_cannot_authenticate(users):
    """Revocation is terminal, and it closes the control surface with it."""
    from fastapi import HTTPException

    device_id, secret = _enrol(users["hilda"], "hilda-revoked")
    assert _companion({DEVICE_CREDENTIAL_HEADER: secret}).device_id == device_id

    devices.revoke_device(device_id, actor="ops")
    with pytest.raises(HTTPException) as caught:
        _companion({DEVICE_CREDENTIAL_HEADER: secret})
    assert caught.value.status_code == 401


def test_a_disabled_device_cannot_authenticate(users):
    """Disabled is temporary, and it still authenticates nothing while it holds."""
    from fastapi import HTTPException

    device_id, secret = _enrol(users["hilda"], "hilda-disabled")
    devices.set_device_disabled(device_id, disabled=True, actor="ops")
    with pytest.raises(HTTPException) as caught:
        _companion({DEVICE_CREDENTIAL_HEADER: secret})
    assert caught.value.status_code == 401


def test_a_credential_does_not_cross_accounts(users, monkeypatch):
    """Ivan's credential cannot authenticate against a process serving Hilda.

    The check happens at Session E's verification boundary, not at whichever
    call site remembered it, which is why binding the process is enough.
    """
    from fastapi import HTTPException

    from bartholomew.platform import runtime_registry

    _device_id, ivan_secret = _enrol(users["ivan"], "ivan-desk")

    monkeypatch.setattr(runtime_registry, "bound_runtime_user_id", lambda: users["hilda"])
    with pytest.raises(HTTPException) as caught:
        _companion({DEVICE_CREDENTIAL_HEADER: ivan_secret})
    assert caught.value.status_code == 401


def test_authentication_alone_grants_no_capability(users):
    """Being enrolled is not being capable, and the difference is enforced."""
    from fastapi import HTTPException

    _device_id, secret = _enrol(users["hilda"], "hilda-observe-only", OBSERVE_ONLY_MANIFEST)
    companion = _companion({DEVICE_CREDENTIAL_HEADER: secret})

    # It authenticated perfectly well...
    assert companion.owner_user_id == users["hilda"]
    # ...and still may not be asked to act on Windows.
    with pytest.raises(HTTPException) as caught:
        companion.require_capability("windows.focus_window", 1)
    assert caught.value.status_code == 403


# ===========================================================================
# Observation start / stop
# ===========================================================================


@pytest.fixture
def multimodal_client():
    from bartholomew_api_bridge_v0_1.services.api.routes import multimodal

    app = FastAPI()
    app.include_router(multimodal.router)
    with TestClient(app) as client:
        yield client


SCREEN_BODY = {
    "modality": "screen",
    "scope": {"kind": "display", "display_id": "1"},
}


def test_an_unauthenticated_start_is_refused(multimodal_client):
    """Capture initiation is never reachable without a credential."""
    assert multimodal_client.post("/api/multimodal/sessions", json=SCREEN_BODY).status_code == 401


def test_a_wrong_credential_start_is_refused(multimodal_client):
    response = multimodal_client.post(
        "/api/multimodal/sessions",
        json=SCREEN_BODY,
        headers={DEVICE_CREDENTIAL_HEADER: "wrong"},
    )
    assert response.status_code == 401


def test_a_device_that_does_not_declare_the_modality_is_refused_truthfully(
    users,
    multimodal_client,
    monkeypatch,
):
    """An unavailable modality denies, and says which -- never approximates.

    Package C's rule: unknown is unsupported. The device here is enrolled,
    healthy and authenticates fine; it simply does not declare microphone
    capture, and the refusal says so rather than failing vaguely.
    """
    from bartholomew.integration.device_registry import RegistryBackedCapabilityResolver
    from bartholomew.multimodal import runtime as mm_runtime

    _device_id, secret = _enrol(users["hilda"], "hilda-no-mic")
    mm_runtime.install_capability_resolver(
        RegistryBackedCapabilityResolver(tenant_id=users["hilda"]),
    )
    try:
        response = multimodal_client.post(
            "/api/multimodal/sessions",
            json={"modality": "microphone"},
            headers={DEVICE_CREDENTIAL_HEADER: secret},
        )
    finally:
        mm_runtime.install_capability_resolver(None)

    assert response.status_code == 403
    body = response.json()
    assert body["outcome"] == "capability_denied"
    assert "microphone_session" in (body.get("reason") or "")


def test_a_start_with_no_consent_handler_fails_closed(users, multimodal_client):
    """No one to ask means no observation. This is the anti-autonomy gate.

    A companion holding a valid credential can *ask* to observe. It cannot
    answer for the person, and with no consent channel registered there is
    nobody to answer at all -- so the start is refused. This is what stops an
    authenticated machine from beginning to watch a person on its own.
    """
    from bartholomew.integration.device_registry import RegistryBackedCapabilityResolver
    from bartholomew.kernel.memory import privacy_guard
    from bartholomew.multimodal import runtime as mm_runtime

    _device_id, secret = _enrol(users["hilda"], "hilda-noconsent")
    previous = privacy_guard.get_consent_handler()
    privacy_guard.set_consent_handler(None)
    mm_runtime.install_capability_resolver(
        RegistryBackedCapabilityResolver(tenant_id=users["hilda"]),
    )
    try:
        response = multimodal_client.post(
            "/api/multimodal/sessions",
            json=SCREEN_BODY,
            headers={DEVICE_CREDENTIAL_HEADER: secret},
        )
    finally:
        mm_runtime.install_capability_resolver(None)
        privacy_guard.set_consent_handler(previous)

    assert response.status_code == 403
    assert response.json()["outcome"] == "consent_denied"


def test_the_session_principal_is_the_person_never_the_companion(users, multimodal_client):
    """The device authenticates; the human owns the session.

    Package C refuses to build a `SessionRequest` whose principal begins
    `companion:`. This asserts the route honours that rather than working
    around it: the principal on the session is the account the enrolment row
    names.
    """
    from bartholomew.integration.device_registry import RegistryBackedCapabilityResolver
    from bartholomew.kernel.memory import privacy_guard
    from bartholomew.multimodal import runtime as mm_runtime

    device_id, secret = _enrol(users["hilda"], "hilda-principal")
    previous = privacy_guard.get_consent_handler()
    privacy_guard.set_consent_handler(lambda _prompt: True)
    mm_runtime.install_capability_resolver(
        RegistryBackedCapabilityResolver(tenant_id=users["hilda"]),
    )
    try:
        response = multimodal_client.post(
            "/api/multimodal/sessions",
            json=SCREEN_BODY,
            headers={DEVICE_CREDENTIAL_HEADER: secret},
        )
    finally:
        mm_runtime.install_capability_resolver(None)
        privacy_guard.set_consent_handler(previous)

    session = response.json()["session"]
    assert session["principal_id"] == users["hilda"]
    assert not session["principal_id"].startswith("companion:")
    assert session["device_id"] == device_id


def test_stop_needs_no_credential_and_is_idempotent(multimodal_client):
    """Stopping fails safe, so it is never behind a gate that could jam.

    The worst an unauthenticated stop can do is end a session the person could
    have ended anyway. Pressing it twice is not a fault, and a session this
    process never heard of is a 404 rather than a pretended success.
    """
    missing = multimodal_client.post("/api/multimodal/sessions/no-such-session/stop")
    assert missing.status_code == 404

    # Reachable with no credential, and idempotent: whatever was live is
    # stopped, and a second press stops nothing rather than erroring.
    assert multimodal_client.post("/api/multimodal/sessions/stop-all").status_code == 200
    again = multimodal_client.post("/api/multimodal/sessions/stop-all")
    assert again.status_code == 200
    assert again.json()["count"] == 0


# ===========================================================================
# Action channel arming
# ===========================================================================


def test_the_channel_is_disarmed_by_default(users):
    """Off unless somebody said otherwise. The single most important default."""
    described = arming.describe(tenant_id=users["hilda"])
    assert described["armed"] is False
    assert described["seconds_remaining"] == 0
    assert arming.check(tenant_id=users["hilda"], device_id="anything").allowed is False


def test_arming_is_bounded_to_fifteen_minutes(users):
    """A caller cannot ask for a longer window than the design allows."""
    window = arming.arm(
        tenant_id=users["hilda"],
        device_id="desk",
        armed_by=users["hilda"],
        seconds=24 * 60 * 60,
    )
    assert window.seconds_remaining() <= arming.MAX_ARM_SECONDS == 15 * 60


def test_an_expired_window_fails_closed(users):
    """Expiry is evaluated on read, so a window is never briefly usable late."""
    window = arming.arm(tenant_id=users["hilda"], device_id="desk", armed_by=users["hilda"])
    later = window.expires_at + timedelta(seconds=1)

    assert arming.check(tenant_id=users["hilda"], device_id="desk", now=later).allowed is False
    assert arming.current(tenant_id=users["hilda"], now=later) is None


def test_a_window_names_exactly_one_device(users):
    """Arming the desk PC does not arm the laptop."""
    arming.arm(tenant_id=users["hilda"], device_id="desk", armed_by=users["hilda"])
    assert arming.check(tenant_id=users["hilda"], device_id="desk").allowed is True
    assert arming.check(tenant_id=users["hilda"], device_id="laptop").allowed is False


def test_a_window_does_not_cross_accounts(users):
    """Hilda arming her machine does not open Ivan's channel."""
    arming.arm(tenant_id=users["hilda"], device_id="desk", armed_by=users["hilda"])
    assert arming.check(tenant_id=users["ivan"], device_id="desk").allowed is False


def test_explicit_disarm_closes_the_channel_immediately(users):
    arming.arm(tenant_id=users["hilda"], device_id="desk", armed_by=users["hilda"])
    assert arming.disarm(tenant_id=users["hilda"]) is not None
    assert arming.check(tenant_id=users["hilda"], device_id="desk").allowed is False
    # Idempotent: disarming a closed channel is not a fault.
    assert arming.disarm(tenant_id=users["hilda"]) is None


def test_a_restart_cannot_leave_a_machine_armed(users):
    """The window lives in this process and nowhere else.

    Deliberate, not a gap: a crash at minute two of fifteen must not hand the
    next process thirteen minutes of authority nobody re-granted. Simulated by
    dropping the in-process state exactly as a restart would.
    """
    arming.arm(tenant_id=users["hilda"], device_id="desk", armed_by=users["hilda"])
    arming.reset_for_tests()  # what a fresh process starts with
    assert arming.check(tenant_id=users["hilda"], device_id="desk").allowed is False


# ===========================================================================
# Arming through the control surface, with its gates
# ===========================================================================


@pytest.fixture
def actions_client(tmp_path, monkeypatch):
    """The actions router over a real app, with a real brake database."""
    import asyncio

    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.orchestrator.safety import governance_store as gs
    from bartholomew_api_bridge_v0_1.services.api import db as api_db
    from bartholomew_api_bridge_v0_1.services.api.routes import actions

    path = str(tmp_path / "kernel.db")
    asyncio.run(MemoryStore(path).init())
    gs.ensure_schema(path)
    monkeypatch.setattr(api_db, "resolve_db_path", lambda: path)
    monkeypatch.setattr(actions, "resolve_db_path", lambda: path)

    app = FastAPI()
    app.include_router(actions.router)
    with TestClient(app) as client:
        yield client, path


def test_arming_requires_an_authenticated_device(actions_client):
    client, _ = actions_client
    response = client.post("/api/actions/channel/arm", json={"device_id": "desk"})
    assert response.status_code == 401


def test_a_revoked_device_cannot_arm(users, actions_client):
    client, _ = actions_client
    device_id, secret = _enrol(users["hilda"], "hilda-arm-revoked")
    devices.revoke_device(device_id, actor="ops")

    response = client.post(
        "/api/actions/channel/arm",
        json={"device_id": device_id},
        headers={DEVICE_CREDENTIAL_HEADER: secret},
    )
    assert response.status_code == 401


def test_a_device_without_windows_actuation_cannot_arm(users, actions_client):
    """Enrolled and healthy, but it cannot act on Windows -- so it cannot arm."""
    client, _ = actions_client
    device_id, secret = _enrol(users["hilda"], "hilda-arm-incapable", OBSERVE_ONLY_MANIFEST)

    response = client.post(
        "/api/actions/channel/arm",
        json={"device_id": device_id},
        headers={DEVICE_CREDENTIAL_HEADER: secret},
    )
    assert response.status_code == 403


def test_a_credential_cannot_arm_another_devices_channel(users, actions_client):
    """The body cannot name a device other than the one that authenticated."""
    client, _ = actions_client
    _device_id, secret = _enrol(users["hilda"], "hilda-arm-self")

    response = client.post(
        "/api/actions/channel/arm",
        json={"device_id": "some-other-machine"},
        headers={DEVICE_CREDENTIAL_HEADER: secret},
    )
    assert response.status_code == 403


def test_the_parking_brake_prevents_arming(users, actions_client):
    """A halted Bartholomew does not open its action channel."""
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    client, db_path = actions_client
    device_id, secret = _enrol(users["hilda"], "hilda-arm-braked")

    GovernanceStore(db_path).engage("actuation", reason="test", actor="test")
    response = client.post(
        "/api/actions/channel/arm",
        json={"device_id": device_id},
        headers={DEVICE_CREDENTIAL_HEADER: secret},
    )
    assert response.status_code == 409


def test_an_authorized_arm_opens_the_channel_and_status_shows_it(users, actions_client):
    client, _ = actions_client
    device_id, secret = _enrol(users["hilda"], "hilda-arm-ok")

    armed = client.post(
        "/api/actions/channel/arm",
        json={"device_id": device_id, "reason": "live test"},
        headers={DEVICE_CREDENTIAL_HEADER: secret},
    )
    assert armed.status_code == 200
    channel = armed.json()["channel"]
    assert channel["armed"] is True
    assert channel["device_id"] == device_id
    assert 0 < channel["seconds_remaining"] <= 15 * 60

    status = client.get("/api/actions/channel")
    assert status.json()["channel"]["armed"] is True

    disarmed = client.post("/api/actions/channel/disarm")
    assert disarmed.json()["disarmed"] is True
    assert client.get("/api/actions/channel").json()["channel"]["armed"] is False


def test_the_brake_overrides_an_already_armed_channel(users, actions_client):
    """Time left on the window is irrelevant once the brake is engaged."""
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    client, db_path = actions_client
    device_id, secret = _enrol(users["hilda"], "hilda-arm-override")

    client.post(
        "/api/actions/channel/arm",
        json={"device_id": device_id},
        headers={DEVICE_CREDENTIAL_HEADER: secret},
    )
    assert client.get("/api/actions/channel").json()["channel"]["armed"] is True

    GovernanceStore(db_path).engage("global", reason="test", actor="test")
    reported = client.get("/api/actions/channel").json()["channel"]
    assert reported["armed"] is False
    assert reported["brake_engaged"] is True


def test_disarm_stays_reachable_under_a_brake(users, actions_client):
    """A control that removes authority must never be the one that jams."""
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    client, db_path = actions_client
    device_id, secret = _enrol(users["hilda"], "hilda-arm-jam")
    client.post(
        "/api/actions/channel/arm",
        json={"device_id": device_id},
        headers={DEVICE_CREDENTIAL_HEADER: secret},
    )

    GovernanceStore(db_path).engage("global", reason="test", actor="test")
    assert client.post("/api/actions/channel/disarm").status_code == 200


# ===========================================================================
# Arming is not approval -- the two are independent and both required
# ===========================================================================


def test_arming_grants_no_standing_permission_in_identity(users):
    """No allowlist entry appeared for arming, approving or accepting learning."""
    import yaml

    identity = yaml.safe_load(Path("Identity.yaml").read_text(encoding="utf-8"))
    allowlist = set(identity["tool_use"]["allowlist"])
    for forbidden in (
        "windows_action_approve",
        "windows_action_arm",
        "learning_accept",
        "share_accept",
    ):
        assert forbidden not in allowlist


def test_arming_and_approving_are_different_capabilities():
    """A surface that may approve must not thereby be able to open the channel."""
    from bartholomew.platform.capabilities import Capability
    from bartholomew.platform.route_policy import ROUTE_CAPABILITIES

    assert Capability.ACTION_ARM is not Capability.ACTION_APPROVE
    assert ROUTE_CAPABILITIES[("POST", "/api/actions/channel/arm")] is Capability.ACTION_ARM
    assert (
        ROUTE_CAPABILITIES[("POST", "/api/actions/{action_id}/approve")]
        is Capability.ACTION_APPROVE
    )
    # Disarming is strictly tightening, classified with the brake.
    assert ROUTE_CAPABILITIES[("POST", "/api/actions/channel/disarm")] is Capability.BRAKE_ENGAGE


# ===========================================================================
# The two gates, end to end: both required, neither sufficient
# ===========================================================================


class _Ctx:
    """The four attributes every Runtime Contract seam reads off the daemon."""

    def __init__(self, db_path):
        from bartholomew.kernel.memory_store import MemoryStore

        self.mem = MemoryStore(db_path)
        self.db_path = db_path
        self.identity_context = None
        self.governance_store = None
        self.blocking_executor = None


@pytest.fixture
def action_ctx(tmp_path):
    """Memory, governance and action schemas, and B's registry over one device."""
    import asyncio

    from bartholomew.actuation import devices as actuation_devices
    from bartholomew.actuation import store
    from bartholomew.actuation.allowlists import (
        ApplicationAllowlist,
        FilesystemRootAllowlist,
        UrlDomainAllowlist,
    )
    from bartholomew.actuation.capabilities import ALL_CAPABILITIES
    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.orchestrator.safety import governance_store as gs

    path = str(tmp_path / "actions.db")
    asyncio.run(MemoryStore(path).init())
    gs.ensure_schema(path)
    store.ensure_schema(path)

    device = actuation_devices.EnrolledDevice(
        device_id="desk-pc",
        tenant_id="tenant-x",
        platform="windows",
        enrolled=True,
        capabilities=tuple(
            actuation_devices.DeclaredCapability(kind=k, version=1) for k in ALL_CAPABILITIES
        ),
        applications=ApplicationAllowlist.from_pairs(
            {"notepad": "C:\\Windows\\System32\\notepad.exe"},
        ),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["C:\\Users\\t\\Documents"]),
    )

    class _Registry:
        LABEL = "completion-test-registry"

        def lookup(self, *, tenant_id, device_id):
            if (tenant_id, device_id) == ("tenant-x", "desk-pc"):
                return device
            return None

    registry = _Registry()
    actuation_devices.install_registry(registry)
    try:
        yield _Ctx(path), registry, path
    finally:
        actuation_devices.install_registry(None)


async def _propose(ctx, registry):
    from bartholomew.actuation import seam

    return await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id="tenant-x",
        device_id="desk-pc",
        requested_by="tenant-x",
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        registry=registry,
    )


@pytest.mark.asyncio
async def test_an_armed_channel_does_not_run_an_unapproved_action(action_ctx):
    """Arming authorises nothing. The action still needs its own approval."""
    from bartholomew.actuation import seam

    ctx, registry, _ = action_ctx
    requested = await _propose(ctx, registry)
    arming.arm(tenant_id="tenant-x", device_id="desk-pc", armed_by="tenant-x")

    leased = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id="tenant-x",
        device_id="desk-pc",
        action_id=requested.action.action_id,
        registry=registry,
    )
    assert leased.governance_allowed is False


@pytest.mark.asyncio
async def test_an_approved_action_does_not_run_on_a_closed_channel(action_ctx):
    """Approval authorises the action. It does not open the machine."""
    from bartholomew.actuation import seam

    ctx, registry, _ = action_ctx
    requested = await _propose(ctx, registry)
    approved = await seam.grant_action_approval(
        ctx,
        tenant_id="tenant-x",
        action_id=requested.action.action_id,
        approver="tenant-x",
        registry=registry,
    )
    assert approved.governance_allowed is True

    leased = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id="tenant-x",
        device_id="desk-pc",
        action_id=requested.action.action_id,
        registry=registry,
    )
    assert leased.governance_allowed is False
    assert "not armed" in (leased.reason or "")


@pytest.mark.asyncio
async def test_approval_plus_an_armed_channel_permits_the_action(action_ctx):
    """Both together, and only both. The happy path the other two bound."""
    from bartholomew.actuation import seam

    ctx, registry, _ = action_ctx
    requested = await _propose(ctx, registry)
    await seam.grant_action_approval(
        ctx,
        tenant_id="tenant-x",
        action_id=requested.action.action_id,
        approver="tenant-x",
        registry=registry,
    )
    arming.arm(tenant_id="tenant-x", device_id="desk-pc", armed_by="tenant-x")

    leased = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id="tenant-x",
        device_id="desk-pc",
        action_id=requested.action.action_id,
        registry=registry,
    )
    assert leased.governance_allowed is True


@pytest.mark.asyncio
async def test_the_brake_beats_an_armed_and_approved_action(action_ctx):
    """The brake is read before the window, so time left never matters."""
    from bartholomew.actuation import seam
    from bartholomew.actuation.result import ErrorCategory
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    ctx, registry, db_path = action_ctx
    requested = await _propose(ctx, registry)
    await seam.grant_action_approval(
        ctx,
        tenant_id="tenant-x",
        action_id=requested.action.action_id,
        approver="tenant-x",
        registry=registry,
    )
    arming.arm(tenant_id="tenant-x", device_id="desk-pc", armed_by="tenant-x")

    GovernanceStore(db_path).engage("actuation", reason="test", actor="test")
    leased = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id="tenant-x",
        device_id="desk-pc",
        action_id=requested.action.action_id,
        registry=registry,
    )
    assert leased.governance_allowed is False
    assert leased.category is ErrorCategory.PARKING_BRAKE


@pytest.mark.asyncio
async def test_an_expired_window_stops_a_previously_permitted_action(action_ctx):
    """Fifteen minutes later the same approved action no longer runs."""
    from bartholomew.actuation import seam

    ctx, registry, _ = action_ctx
    requested = await _propose(ctx, registry)
    await seam.grant_action_approval(
        ctx,
        tenant_id="tenant-x",
        action_id=requested.action.action_id,
        approver="tenant-x",
        registry=registry,
    )
    window = arming.arm(tenant_id="tenant-x", device_id="desk-pc", armed_by="tenant-x")

    # Move past the window exactly as the clock would.
    arming.reset_for_tests()
    assert window.expired(now=window.expires_at + timedelta(seconds=1))

    leased = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id="tenant-x",
        device_id="desk-pc",
        action_id=requested.action.action_id,
        registry=registry,
    )
    assert leased.governance_allowed is False


@pytest.mark.asyncio
async def test_a_successful_action_does_not_become_accepted_learning(action_ctx):
    """Carrying something out does not make Bartholomew believe it should.

    Acceptance needs a candidate-bound `LearningAcceptanceApproval`, and no
    standing permission for it exists. A dispatched action produces audit and
    at most a candidate; it never produces trusted knowledge.
    """
    import yaml

    from bartholomew.kernel import learning_policy

    allowlist = set(
        yaml.safe_load(Path("Identity.yaml").read_text(encoding="utf-8"))["tool_use"]["allowlist"],
    )
    assert "learning_accept" not in allowlist
    assert learning_policy.default_policy().execution_mode == "shadow"
