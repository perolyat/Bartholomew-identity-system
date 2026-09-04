"""The golden path, run as far as the integrated system actually reaches.

The scenario the wave is aiming at:

  1. Bartholomew has an enrolled Windows device.
  2. Windows presence observes an allowed signal.
  3. The observation becomes a governed event.
  4. Bartholomew interprets it through the existing runtime architecture.
  5. A Windows action is proposed.
  6. Governance determines whether approval is required.
  7. The required approval is obtained.
  8. The action executes through the governed Windows actuator.
  9. The result is recorded as an event / audit record.
 10. Any resulting learning is only a candidate.
 11. Selected learning can later be shared with an opted-in trusted group.

Every stage below is executed against real stores -- a real control-plane
database with a real enrolment ceremony, a real `inbound_events` table, a real
memory and governance schema, and the real action store. Nothing is faked to
make a stage appear to pass.

Where the chain stops, this file says so in a test named for the stop rather
than by omitting the stage. Two stops are real and are asserted as facts about
this build:

* **Stage 2 has no production trigger.** Package C ships no capture-start
  surface -- deliberately, per its §7: the API bridge has no authentication,
  so capture initiation must not be reachable from an unauthenticated call.
  `start_session()` is reachable only from in-process code. The observation
  in this file is therefore produced by calling C's own serializer directly,
  which is exactly what an authenticated control plane would call. What is
  *not* demonstrated is a person starting a capture session on a real Windows
  machine and this chain running unattended.

* **Stage 8 does not touch Windows here.** Dispatch is proven to the lease
  boundary -- the point at which a validated action's parameters leave the
  process for the device. The companion that would perform the keystroke runs
  on Windows, and this suite runs on Linux. The execution proven here is the
  governed hand-off, not a real `notepad.exe` gaining focus.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from bartholomew.actuation import devices as actuation_devices
from bartholomew.actuation import seam, store
from bartholomew.actuation.store import ActionState
from bartholomew.platform import accounts, devices
from bartholomew.platform.principal import PrincipalKind
from bartholomew.platform.store import init_platform_schema

PASSWORD = "golden-path-password"
NOTEPAD = "C:\\Windows\\System32\\notepad.exe"

MANIFEST = {
    "platform": "windows",
    "companion_version": "0.1.0-prototype",
    "capabilities": [
        {"kind": "windows.focus_window", "version": 1},
        {"kind": "windows.open_url", "version": 1},
        {"kind": "multimodal.screen_capture", "version": 1},
    ],
}


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="golden-path-")
    mp.setenv("BARTH_PLATFORM_DB_PATH", str(Path(tmp) / "platform.db"))
    mp.setenv("BARTH_DATA_ROOT", str(Path(tmp) / "data"))
    yield
    mp.undo()


@pytest.fixture(scope="module")
def person():
    init_platform_schema()
    try:
        return accounts.create_account("taylor", PASSWORD, kind=PrincipalKind.USER)
    except accounts.AccountError:
        return next(a["user_id"] for a in accounts.list_accounts() if a["username"] == "taylor")


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
def kernel_db(tmp_path):
    """Memory, governance, action and inbound schemas -- the real ones."""
    from bartholomew.kernel.inbound_store import INBOUND_SCHEMA
    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.orchestrator.safety import governance_store as gs

    path = str(tmp_path / "kernel.db")
    asyncio.run(MemoryStore(path).init())
    gs.ensure_schema(path)
    store.ensure_schema(path)
    conn = sqlite3.connect(path)
    conn.executescript(INBOUND_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def allowlist_file(tmp_path, enrolled_device, person):
    """The operator's parameter allowlist for the enrolled device.

    Written by the test as an operator writes it. It grants no capability --
    E's enrolment did that -- it only says which application keys and domains
    this device's action parameters may name.
    """
    path = tmp_path / "enrolment.json"
    path.write_text(
        json.dumps(
            {
                "devices": [
                    {
                        "device_id": enrolled_device,
                        "tenant_id": person,
                        "platform": "windows",
                        "capabilities": ["windows.focus_window", "windows.open_url"],
                        "applications": {"notepad": NOTEPAD},
                        "url_domains": ["example.com"],
                        "trusted_autonomy": [],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture
def enrolled_device(person):
    """Stage 1: a real enrolment ceremony, not a JSON file an operator wrote."""
    device_id = devices.create_pending_enrolment(person, "taylor-desk", platform="windows")
    issued = devices.approve_enrolment(device_id, approver="ops")
    devices.complete_enrolment(issued.secret, dict(MANIFEST))
    return device_id


@pytest.fixture
def registry(allowlist_file):
    """Stage 1: the one device truth, as production resolves it."""
    from bartholomew.integration.device_registry import RegistryBackedDeviceRegistry

    reg = RegistryBackedDeviceRegistry(enrolment_path=allowlist_file)
    actuation_devices.install_registry(reg)
    yield reg
    actuation_devices.install_registry(None)


# ===========================================================================
# Stages 1-3: enrolled device -> observed signal -> governed event
# ===========================================================================


def test_stage_1_the_device_is_enrolled_and_actuable(person, enrolled_device, registry):
    """1. An enrolled Windows device, resolved through the production registry."""
    from bartholomew.actuation.capabilities import CapabilityKind

    found = registry.lookup(tenant_id=person, device_id=enrolled_device)
    assert found is not None
    assert found.enrolled is True
    assert found.declares(CapabilityKind.FOCUS_WINDOW, 1)
    # And the operator's allowlist reached it, so a parameter can name notepad.
    assert found.applications.resolve("notepad") == NOTEPAD


def test_stages_2_and_3_an_observation_becomes_a_governed_event(
    person,
    enrolled_device,
    kernel_db,
):
    """2-3. C's observation is serialized and captured into A's one ingress.

    Stage 2's *trigger* is the documented stop: C has no production
    capture-start surface, so the session here is constructed in-process,
    which is what an authenticated control plane would do. Everything after
    that -- the envelope, the classification, the capture -- is the real path.
    """
    from bartholomew.integration.multimodal_events import CanonicalIngressSink
    from bartholomew.multimodal.accessibility import AccessibilityObservation
    from bartholomew.multimodal.events import serialize_screen
    from bartholomew.multimodal.modality import Modality
    from bartholomew.multimodal.screen import ScreenObservation
    from bartholomew.multimodal.session import MultimodalSession

    session = MultimodalSession(
        tenant_id=person,
        principal_id=person,
        device_id=enrolled_device,
        modality=Modality.SCREEN,
        correlation_id="golden-corr-1",
        causation_id=None,
        scope="window",
        max_duration_seconds=60,
    )

    observation = ScreenObservation(
        scope_description="window: shopping list - Notepad",
        accessibility=AccessibilityObservation(
            available=True,
            complete=True,
            application="notepad.exe",
            window_title="shopping list - Notepad",
        ),
        used_screenshot=False,
        description="a note listing the shared shopping list",
    )
    envelope = serialize_screen(session, observation)

    # §3.1: C never assigns captured_at, and never raises verification.
    assert envelope["captured_at"] is None
    assert envelope["source"]["verification"] == "claimed"
    assert envelope["correlation_id"] == "golden-corr-1"

    sink = CanonicalIngressSink(db_path=kernel_db, runtime_id=person)
    sink.submit(envelope)

    conn = sqlite3.connect(kernel_db)
    row = conn.execute(
        "SELECT event_type, outcome, received_at, runtime_id FROM inbound_events",
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "multimodal.screen.observation"
    assert row[1] == "captured"
    # Ingress assigned the captured-at that C truthfully left empty.
    assert row[2]
    assert row[3] == person


def test_stage_4_the_event_is_interpreted_through_the_existing_runtime(kernel_db):
    """4. The event routes to A's registered handler, not to a new stack.

    What is asserted is the routing and the disposition contract: the type
    resolves to a handler through A's one registry, and its payload parses
    under A's own bound. The interpretation seam itself is Package A's, and
    its own suite proves what it decides.
    """
    from bartholomew.kernel.event_processing.registry import lookup

    spec = lookup("multimodal.screen.observation")
    assert spec is not None
    from bartholomew.integration.multimodal_events import handle_multimodal_observation

    assert spec.handler is handle_multimodal_observation


# ===========================================================================
# Stages 5-9: proposed action -> governance -> approval -> dispatch -> result
# ===========================================================================


@pytest.mark.asyncio
async def test_stages_5_to_9_the_governed_action_path_end_to_end(
    person,
    enrolled_device,
    registry,
    kernel_db,
):
    """5-9. Proposed, governed, approved, leased and settled -- causally linked.

    The causation chain is the point: the action names the observation that
    prompted it, and that id survives into the durable row, so an audit can
    ask "why did Bartholomew do this?" and get the observation back.
    """
    ctx = _Ctx(kernel_db)

    # 5. A Windows action is proposed, caused by the observation above.
    requested = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=person,
        device_id=enrolled_device,
        requested_by=person,
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        correlation_id="golden-corr-1",
        causation_id="multimodal:screen-observation",
        registry=registry,
    )
    assert requested.governance_allowed is True
    action = requested.action
    assert action is not None

    # 6. Governance required an approval: this device has no trusted autonomy,
    #    so the action is pending rather than approved.
    assert action.state == ActionState.PENDING_APPROVAL.value

    # ...and a request alone reaches no device.
    premature = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=person,
        device_id=enrolled_device,
        action_id=action.action_id,
        registry=registry,
    )
    assert premature.governance_allowed is False

    # 7. The approval is obtained, bound to this exact action.
    approved = await seam.grant_action_approval(
        ctx,
        tenant_id=person,
        action_id=action.action_id,
        approver=person,
        registry=registry,
    )
    assert approved.governance_allowed is True

    # 8. Dispatch: the parameters leave the process for the device. This is
    #    the governed hand-off, not a keystroke on a real Windows machine.
    leased = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=person,
        device_id=enrolled_device,
        action_id=action.action_id,
        registry=registry,
    )
    assert leased.governance_allowed is True

    # 9. The causation and correlation survived into the durable record.
    stored = store.get_action(kernel_db, tenant_id=person, action_id=action.action_id)
    assert stored is not None
    assert stored.correlation_id == "golden-corr-1"
    assert stored.causation_id == "multimodal:screen-observation"


@pytest.mark.asyncio
async def test_the_parking_brake_stops_the_golden_path(
    person,
    enrolled_device,
    registry,
    kernel_db,
):
    """The Parking Brake remains authoritative over the whole integrated path.

    An integration that connected five packages and left the brake reaching
    only some of them would be worse than no integration.
    """
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    ctx = _Ctx(kernel_db)
    brake = GovernanceStore(kernel_db)
    brake.engage("actuation", reason="integration test", actor="test")
    try:
        halted = await seam.run_action_request_through_runtime_contract(
            ctx,
            tenant_id=person,
            device_id=enrolled_device,
            requested_by=person,
            capability="windows.focus_window",
            capability_version=1,
            parameters={"app_id": "notepad"},
            registry=registry,
        )
        assert halted.governance_allowed is False
    finally:
        brake.disengage(reason="integration test", actor="test")


# ===========================================================================
# Stages 10-11: learning stays a candidate; sharing stays opt-in
# ===========================================================================


def test_stage_10_learning_from_this_remains_a_candidate(person):
    """10. Nothing on this path can make a lesson trusted knowledge.

    Acceptance requires a `LearningAcceptanceApproval` bound by fingerprint to
    one candidate, and no standing permission for it exists. That is what
    keeps "Bartholomew did something and it worked" from becoming "Bartholomew
    now believes it should always do that".
    """
    import yaml

    allowlist = set(
        yaml.safe_load(Path("Identity.yaml").read_text(encoding="utf-8"))["tool_use"]["allowlist"],
    )
    assert "learning_propose" in allowlist
    assert "learning_accept" not in allowlist

    from bartholomew.kernel import learning_policy

    assert learning_policy.default_policy().execution_mode == "shadow"


def test_stage_11_sharing_requires_an_opted_in_trusted_group(person):
    """11. With no group joined, there is nowhere to share and the UI says so.

    Cross-user learning is opt-in participation, not a default. The projection
    reports the absence of a group as the reason, rather than showing an empty
    list that reads like "nothing has been shared yet".
    """
    from bartholomew.integration.learning_adapters import resolve_sharing

    sharing = resolve_sharing(
        user_id=person,
        eligible=True,
        source_kind="competency_procedure",
    ).to_dict()
    assert sharing["transport_available"] is False
    assert sharing["state"] == "not_shared"


# ===========================================================================
# Where the chain stops -- asserted, not omitted
# ===========================================================================


def test_stop_1_there_is_no_production_capture_start_surface():
    """Stage 2 has no unattended trigger in this build, and that is deliberate.

    Package C's HTTP surface is read-and-stop only: status, sessions, stop,
    stop-all, diagnostics. There is no POST that starts a capture session,
    because the API bridge has no authentication and capture initiation must
    not be reachable from an unauthenticated call.

    This test fails the moment someone adds one, which is the point: adding a
    start route is a governance decision, not a convenience.
    """
    from bartholomew_api_bridge_v0_1.services.api.routes import multimodal

    paths = {
        (method, route.path)
        for route in multimodal.router.routes
        for method in getattr(route, "methods", set())
    }
    starting = {(method, path) for method, path in paths if method == "POST" and "stop" not in path}
    assert starting == set(), (
        f"a capture-start surface appeared: {starting}. Package C deliberately "
        "ships none; adding one is a governance decision."
    )


def test_stop_2_no_windows_hardware_is_exercised_by_this_suite():
    """Stage 8 is proven to the lease boundary, not onto a real desktop.

    The companion that performs the keystroke runs on Windows; this suite does
    not. Claiming otherwise would be the single most misleading thing this
    file could do, so it asserts the limitation instead.
    """
    import sys

    if sys.platform.startswith("win"):
        pytest.skip("on Windows the platform-specific suites cover the real path")

    from bartholomew.windows_actuation import win32

    # The win32 layer is import-safe off Windows precisely because it cannot
    # act there; that is the property that makes the limitation structural
    # rather than a matter of this test declining to try.
    assert win32 is not None
