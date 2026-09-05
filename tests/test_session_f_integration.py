"""Session F: the seams between Packages A-E, tested across the packages.

Each package's own suite proves that package in isolation. Nothing there can
prove that B's governance actually asks E's registry, that C's events actually
reach A's processor, or that D's control centre shows E's real sharing state
-- those are properties of the *connection*, and before this file nothing
tested them because no connection existed.

Everything here runs against real stores: a real control-plane SQLite database
with real accounts and a real enrolment ceremony, and a real `inbound_events`
table. Nothing that could be asserted about persistence is asserted against a
mock, for the reason Package E's suite gives: a mock would assert only that
the test author remembered the property.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from bartholomew.platform import accounts, devices
from bartholomew.platform.principal import PrincipalKind
from bartholomew.platform.store import init_platform_schema

PASSWORD = "session-f-integration-password"

WINDOWS_MANIFEST = {
    "platform": "windows",
    "companion_version": "0.1.0-prototype",
    "capabilities": [
        {"kind": "windows.open_url", "version": 1},
        {"kind": "windows.launch_app", "version": 1},
        {"kind": "multimodal.screen_capture", "version": 1},
        {"kind": "multimodal.spoken_output", "version": 1},
    ],
}


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
    """This module's own control plane, restored afterwards.

    A module-scoped MonkeyPatch rather than `os.environ[...]`, so the paths do
    not leak into every other file in the same pytest session.
    """
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="session-f-")
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
    for name in ("fiona", "gregor"):
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


# ===========================================================================
# A + E -> B: one device truth answers governed actuation
# ===========================================================================


def test_b_governance_reads_es_enrolled_device(users):
    """An actively enrolled device in E is an enrolled device to B.

    The connection under test: B's `DeviceCapabilityRegistry.lookup` is
    answered from `platform_devices`, not from B's interim JSON file. Before
    Session F these were two unrelated device stories and a device enrolled
    through the real ceremony was invisible to actuation.
    """
    from bartholomew.actuation.capabilities import CapabilityKind
    from bartholomew.integration.device_registry import RegistryBackedDeviceRegistry

    device_id, _ = _enrol(users["fiona"], "fiona-desk")
    registry = RegistryBackedDeviceRegistry()

    found = registry.lookup(tenant_id=users["fiona"], device_id=device_id)
    assert found is not None
    assert found.enrolled is True
    assert found.tenant_id == users["fiona"]
    assert found.platform == "windows"
    # The windows capabilities crossed over; C's multimodal kinds are not
    # action capabilities and are correctly absent from an action registry.
    assert found.declares(CapabilityKind.OPEN_URL, 1)
    assert found.declares(CapabilityKind.LAUNCH_APP, 1)


def test_a_revoked_device_is_not_enrolled_rather_than_absent(users):
    """B's contract: revoked must be `enrolled=False`, never an ambiguous None.

    The distinction is what lets a refusal say "that device is not enrolled
    here" rather than the less useful, and less true, "unknown device".
    """
    from bartholomew.integration.device_registry import RegistryBackedDeviceRegistry

    device_id, _ = _enrol(users["fiona"], "fiona-revoked")
    devices.revoke_device(device_id, actor="ops")

    found = RegistryBackedDeviceRegistry().lookup(
        tenant_id=users["fiona"],
        device_id=device_id,
    )
    assert found is not None, "a revoked device must not vanish into None"
    assert found.enrolled is False
    # And it authorises nothing, whatever it once declared.
    assert found.trusted_autonomy == frozenset()


def test_a_disabled_device_is_also_not_enrolled(users):
    """Only ACTIVE is enrolled. DISABLED is a known device that may not act."""
    from bartholomew.integration.device_registry import RegistryBackedDeviceRegistry

    device_id, _ = _enrol(users["fiona"], "fiona-disabled")
    devices.set_device_disabled(device_id, disabled=True, actor="ops")

    found = RegistryBackedDeviceRegistry().lookup(
        tenant_id=users["fiona"],
        device_id=device_id,
    )
    assert found is not None
    assert found.enrolled is False


def test_a_device_is_unknown_in_another_tenant(users):
    """Cross-tenant containment: Gregor cannot look up Fiona's device.

    Not "refused with a reason" -- *unknown*, which is the containment
    property. A reason would confirm the device exists.
    """
    from bartholomew.integration.device_registry import RegistryBackedDeviceRegistry

    device_id, _ = _enrol(users["fiona"], "fiona-private")
    registry = RegistryBackedDeviceRegistry()

    assert registry.lookup(tenant_id=users["gregor"], device_id=device_id) is None
    assert registry.lookup(tenant_id=users["fiona"], device_id=device_id) is not None


def test_an_unanswerable_lookup_fails_closed(users, monkeypatch):
    """A registry that cannot answer raises; `actuation.seam` treats that as denial.

    The failure this rules out is the dangerous one: an unreachable store
    returning None, which reads as a clean "not enrolled" and would let a
    broken control plane look like an empty one.
    """
    from bartholomew.actuation.devices import DeviceRegistryError
    from bartholomew.integration import device_registry as dr

    registry = dr.RegistryBackedDeviceRegistry()

    def _explode(*_a, **_kw):
        raise sqlite3.OperationalError("control plane unavailable")

    monkeypatch.setattr(devices, "get_device", _explode)
    with pytest.raises(DeviceRegistryError):
        registry.lookup(tenant_id=users["fiona"], device_id="anything")


def test_a_lookup_without_a_tenant_is_refused(users):
    """No request-body authority: an unnamed tenant is a caller bug, not a miss."""
    from bartholomew.actuation.devices import DeviceRegistryError
    from bartholomew.integration.device_registry import RegistryBackedDeviceRegistry

    registry = RegistryBackedDeviceRegistry()
    with pytest.raises(DeviceRegistryError):
        registry.lookup(tenant_id="", device_id="some-device")


def test_parameter_allowlists_default_to_refusing_everything(users):
    """A device with no operator allowlist entry may name no application or path.

    E's manifest has no concept of parameter allowlists, so an enrolled device
    with no entry in B's operator file gets empty ones -- and empty refuses
    everything. The failure this rules out is the permissive reading, where an
    absent allowlist means "no restriction".
    """
    from bartholomew.actuation.allowlists import AllowlistError
    from bartholomew.integration.device_registry import RegistryBackedDeviceRegistry

    device_id, _ = _enrol(users["fiona"], "fiona-noallow")
    found = RegistryBackedDeviceRegistry().lookup(
        tenant_id=users["fiona"],
        device_id=device_id,
    )
    assert found is not None and found.enrolled is True
    with pytest.raises(AllowlistError):
        found.applications.resolve("notepad")
    assert found.url_domains.permits("example.com") is False


# ===========================================================================
# E -> C: the same device truth answers multimodal capability
# ===========================================================================


def test_c_capability_resolver_reads_the_same_registry(users):
    """C's resolver and B's registry answer from one store, not two.

    This is the "no second device registry" invariant made testable: the same
    enrolment that made the device actuable is what makes it observable.
    """
    from bartholomew.integration.device_registry import (
        VERIFICATION_REGISTERED,
        RegistryBackedCapabilityResolver,
    )

    device_id, _ = _enrol(users["fiona"], "fiona-multimodal")
    resolver = RegistryBackedCapabilityResolver(tenant_id=users["fiona"])

    ok = resolver.resolve(device_id, "multimodal.screen_capture", 1)
    assert ok.supported is True
    # Only E's registry may raise verification above "claimed", and this is it.
    assert ok.verification == VERIFICATION_REGISTERED


def test_c_refuses_an_undeclared_capability_and_says_why(users):
    """Unknown is unsupported and never approximated (C's invariant)."""
    from bartholomew.integration.device_registry import RegistryBackedCapabilityResolver

    device_id, _ = _enrol(users["fiona"], "fiona-partial")
    resolver = RegistryBackedCapabilityResolver(tenant_id=users["fiona"])

    # Declared at v1 only; v2 is a different contract, not v1 with extras.
    assert resolver.resolve(device_id, "multimodal.screen_capture", 2).supported is False
    # Never declared at all.
    missing = resolver.resolve(device_id, "multimodal.microphone_session", 1)
    assert missing.supported is False
    assert "not authorised" in (missing.reason or "")


def test_c_distinguishes_revoked_from_unenrolled(users):
    """ "That device is revoked" and "you have no such device" stay different.

    C's contract requires truthful state distinctions, and collapsing these
    two into one refusal would lose the one an operator needs.
    """
    from bartholomew.integration.device_registry import RegistryBackedCapabilityResolver

    device_id, _ = _enrol(users["fiona"], "fiona-mm-revoked")
    devices.revoke_device(device_id, actor="ops")
    resolver = RegistryBackedCapabilityResolver(tenant_id=users["fiona"])

    revoked = resolver.resolve(device_id, "multimodal.screen_capture", 1)
    assert revoked.supported is False
    assert "not active" in (revoked.reason or "")

    absent = resolver.resolve("no-such-device", "multimodal.screen_capture", 1)
    assert absent.supported is False
    assert "not enrolled" in (absent.reason or "")


def test_c_resolver_cannot_reach_another_tenants_device(users):
    """The resolver is tenant-bound at construction; C's Protocol has no tenant."""
    from bartholomew.integration.device_registry import RegistryBackedCapabilityResolver

    device_id, _ = _enrol(users["fiona"], "fiona-mm-private")
    other = RegistryBackedCapabilityResolver(tenant_id=users["gregor"])
    assert other.resolve(device_id, "multimodal.screen_capture", 1).supported is False


def test_a_resolver_cannot_be_built_without_a_tenant():
    """There is no unbound resolver to leak into a downstream `if`."""
    from bartholomew.integration.device_registry import RegistryBackedCapabilityResolver

    with pytest.raises(ValueError, match="bound to a tenant"):
        RegistryBackedCapabilityResolver(tenant_id="")


# ===========================================================================
# C -> A: one event bus
# ===========================================================================


@pytest.fixture
def event_db(tmp_path):
    """A real `inbound_events` table -- the one ingress, not a stand-in."""
    from bartholomew.kernel.inbound_store import INBOUND_SCHEMA

    path = str(tmp_path / "events.db")
    conn = sqlite3.connect(path)
    conn.executescript(INBOUND_SCHEMA)
    conn.commit()
    conn.close()
    return path


def _envelope(**overrides):
    body = {
        "schema_version": 1,
        "event_id": "multimodal:abc123",
        "event_type": "multimodal.screen.observation",
        "tenant_id": "tenant-1",
        "source": {
            "source_id": "multimodal.screen",
            "device_id": "device-1",
            "principal_id": "p1",
            "verification": "claimed",
        },
        "occurred_at": "2026-09-04T10:00:00+00:00",
        "captured_at": None,
        "correlation_id": "corr-1",
        "causation_id": "cause-1",
        "payload": {"session_id": "s1", "modality": "screen", "application": "notepad.exe"},
        "payload_sha256": "deadbeef",
        "privacy_class": "sensitive",
        "retention_class": "short",
    }
    body.update(overrides)
    return body


def test_multimodal_events_land_in_the_one_ingress_table(event_db):
    """C's envelope becomes a row in `inbound_events` -- A's table, not a new one.

    The invariant this defends is "no second event bus": a multimodal
    observation and an externally-POSTed one are rows in the same table, swept
    by the same processor.
    """
    from bartholomew.integration.multimodal_events import (
        VERIFIED_BY_MULTIMODAL,
        CanonicalIngressSink,
    )

    sink = CanonicalIngressSink(db_path=event_db, runtime_id="tenant-1")
    sink.submit(_envelope())

    conn = sqlite3.connect(event_db)
    rows = conn.execute(
        "SELECT source_id, event_id, event_type, occurred_at, received_at, "
        "payload_json, outcome, verified_by, runtime_id FROM inbound_events",
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    (
        source_id,
        event_id,
        event_type,
        occurred_at,
        received_at,
        payload,
        outcome,
        verified_by,
        runtime_id,
    ) = rows[0]
    assert source_id == "multimodal.screen"
    assert event_id == "multimodal:abc123"
    assert event_type == "multimodal.screen.observation"
    assert outcome == "captured"
    assert verified_by == VERIFIED_BY_MULTIMODAL
    assert runtime_id == "tenant-1"
    # C's own record of when it happened survives...
    assert occurred_at == "2026-09-04T10:00:00+00:00"
    # ...and ingress assigned the captured-at, which C deliberately left None.
    assert received_at and received_at.endswith("Z")


def test_correlation_causation_and_privacy_survive_ingress(event_db):
    """Provenance is not dropped on the way into the event table.

    `inbound_events` has no column for correlation, causation or privacy
    class, so they travel in the stored payload and are covered by its digest.
    Losing them would break both the audit chain and D's affected-application
    resolver, which reads them back.
    """
    from bartholomew.integration.multimodal_events import CanonicalIngressSink

    CanonicalIngressSink(db_path=event_db, runtime_id="tenant-1").submit(_envelope())

    conn = sqlite3.connect(event_db)
    (raw,) = conn.execute("SELECT payload_json FROM inbound_events").fetchone()
    conn.close()

    stored = json.loads(raw)
    assert stored["correlation_id"] == "corr-1"
    assert stored["causation_id"] == "cause-1"
    assert stored["privacy_class"] == "sensitive"
    assert stored["retention_class"] == "short"
    assert stored["source"]["verification"] == "claimed"


def test_a_retried_observation_is_one_logical_event(event_db):
    """C's content-derived id plus A's UNIQUE constraint: a retry is a retry."""
    from bartholomew.integration.multimodal_events import CanonicalIngressSink

    sink = CanonicalIngressSink(db_path=event_db, runtime_id="tenant-1")
    sink.submit(_envelope())
    sink.submit(_envelope())

    conn = sqlite3.connect(event_db)
    (count,) = conn.execute("SELECT COUNT(*) FROM inbound_events").fetchone()
    conn.close()
    assert count == 1


def test_an_envelope_claiming_another_tenant_is_refused(event_db):
    """The envelope is not an authority on whose observation it is.

    Refused rather than re-attributed: writing it under either id would
    attribute one person's observation using the other's claim.
    """
    from bartholomew.integration.multimodal_events import (
        CanonicalIngressSink,
        MultimodalIngressError,
    )

    sink = CanonicalIngressSink(db_path=event_db, runtime_id="tenant-1")
    with pytest.raises(MultimodalIngressError):
        sink.submit(_envelope(tenant_id="tenant-2"))

    conn = sqlite3.connect(event_db)
    (count,) = conn.execute("SELECT COUNT(*) FROM inbound_events").fetchone()
    conn.close()
    assert count == 0


def test_an_unregistered_event_type_is_refused(event_db):
    """Only the five declared multimodal types may enter under a multimodal source."""
    from bartholomew.integration.multimodal_events import (
        CanonicalIngressSink,
        MultimodalIngressError,
    )

    sink = CanonicalIngressSink(db_path=event_db, runtime_id="tenant-1")
    with pytest.raises(MultimodalIngressError):
        sink.submit(_envelope(event_type="multimodal.something.invented"))


def test_a_failed_write_is_raised_not_swallowed(tmp_path):
    """A sink that could not capture must never look like one that did."""
    from bartholomew.integration.multimodal_events import (
        CanonicalIngressSink,
        MultimodalIngressError,
    )

    # No schema: the table does not exist.
    sink = CanonicalIngressSink(db_path=str(tmp_path / "empty.db"), runtime_id="tenant-1")
    with pytest.raises(MultimodalIngressError):
        sink.submit(_envelope())


def test_all_five_multimodal_types_are_registered_with_a():
    """C's five event classes route through A's one registry.

    A type registered twice would raise at import, so this also proves nobody
    registered a competing handler for them.
    """
    import bartholomew.integration.multimodal_events  # noqa: F401
    from bartholomew.kernel.event_processing.registry import lookup, registered_types

    expected = {
        "multimodal.microphone.transcript",
        "multimodal.screen.observation",
        "multimodal.accessibility.observation",
        "multimodal.spoken_output.utterance",
        "multimodal.session.state",
    }
    assert expected <= set(registered_types())
    for event_type in expected:
        assert lookup(event_type) is not None


@pytest.mark.asyncio
async def test_bartholomews_own_speech_is_not_evidence_about_the_person():
    """A spoken-output record settles as `irrelevant`, not as an observation.

    The failure this rules out is the system treating its own output as an
    observation of the world, which would let it learn from what it said.
    """
    from bartholomew.integration.multimodal_events import (
        MultimodalObservationPayload,
        handle_multimodal_record,
    )
    from bartholomew.kernel.event_processing.store import STATE_IRRELEVANT

    envelope = _envelope(event_type="multimodal.spoken_output.utterance")
    payload = MultimodalObservationPayload.parse(envelope)
    result = await handle_multimodal_record(None, None, payload)

    assert result.disposition == STATE_IRRELEVANT
    assert result.detail["multimodal"]["correlation_id"] == "corr-1"


def test_a_multimodal_payload_must_carry_an_object_body():
    """A's payload bound is reused, not restated."""
    from bartholomew.integration.multimodal_events import MultimodalObservationPayload
    from bartholomew.kernel.event_processing.registry import PayloadValidationError

    with pytest.raises(PayloadValidationError):
        MultimodalObservationPayload.parse({"payload": "not an object"})
    with pytest.raises(PayloadValidationError):
        MultimodalObservationPayload.parse("not an envelope")


# ===========================================================================
# D -> E: the control centre shows real sharing state
# ===========================================================================


def test_sharing_says_no_transport_when_the_person_is_in_no_group(users):
    """D's "not connected in this release" is replaced by what is actually true."""
    from bartholomew.integration.learning_adapters import resolve_sharing

    sharing = resolve_sharing(
        user_id=users["fiona"],
        eligible=True,
        source_kind="competency_procedure",
    ).to_dict()

    assert sharing["transport_available"] is False
    assert sharing["state"] == "not_shared"
    assert "trusted group" in sharing["detail"]


def test_sharing_reports_transport_once_a_trusted_group_exists(users):
    """Joining a group is what makes sharing available -- opt-in, and visible."""
    from bartholomew.integration.learning_adapters import resolve_sharing
    from bartholomew.platform import trusted_groups

    trusted_groups.create_group(users["gregor"], "gregor's household")
    sharing = resolve_sharing(
        user_id=users["gregor"],
        eligible=True,
        source_kind="competency_procedure",
    ).to_dict()

    assert sharing["transport_available"] is True
    assert sharing["eligible"] is True
    # Transport existing is not a share having happened.
    assert sharing["state"] == "not_shared"


def test_a_candidate_is_never_an_eligible_share_source(users):
    """Publishing an unreviewed inference would export a guess as a conviction.

    D would call a candidate eligible on classification alone; E's sanitizer
    refuses the kind outright. The projection now shows E's answer, so the
    control centre cannot offer a share that would be refused.
    """
    from bartholomew.integration.learning_adapters import resolve_sharing
    from bartholomew.kernel import candidate_learning

    sharing = resolve_sharing(
        user_id=users["gregor"],
        eligible=True,
        source_kind=candidate_learning.KIND,
    ).to_dict()
    assert sharing["eligible"] is False


# ===========================================================================
# D: what is measured, and what is honestly not
# ===========================================================================


def test_contradiction_is_declared_unmeasured_rather_than_guessed():
    """An invented contradiction count would make previews look less conservative.

    The strict default stands, and the control centre can say it is a default
    rather than a measurement.
    """
    from bartholomew.integration.learning_adapters import (
        DEFAULT_CONTRADICTING_EVIDENCE_COUNT,
        describe_unmeasured,
    )

    described = describe_unmeasured()["contradicting_evidence"]
    assert described["measured"] is False
    assert DEFAULT_CONTRADICTING_EVIDENCE_COUNT == 0


def test_the_risk_assessor_never_loosens_and_never_writes():
    """An automated assessor must not spend a reviewer's standing edit grant.

    Applying an assessment is a material edit, which re-fingerprints the
    candidate and invalidates any approval standing against it. An unattended
    assessor doing that would silently revoke a person's considered approval,
    so the assessment is a proposal a reviewer applies.
    """
    from bartholomew.integration.learning_adapters import assess_risk

    class _Lesson:
        competency_id = "morning-routine"
        inferred_rule = "open the calendar first"
        conditions = ()
        risk_class = "unassessed"
        reversible = None

    assessment = assess_risk(_Lesson())
    assert assessment.confident is False
    assert assessment.risk_class is None
    assert assessment.to_dict()["applied"] is False


def test_a_lesson_touching_actuation_is_assessed_irreversible():
    """Where the assessor *can* tell, it tells conservatively."""
    from bartholomew.integration.learning_adapters import assess_risk

    class _Lesson:
        competency_id = "typing"
        inferred_rule = "use windows.type_text to fill the form"
        conditions = ()
        risk_class = "unassessed"
        reversible = None

    assessment = assess_risk(_Lesson())
    assert assessment.risk_class == "critical"
    assert assessment.reversible is False


def test_affected_applications_are_read_from_real_observations(event_db):
    """D's reviewer-supplied field is now observable -- through A's ingress.

    This is a three-package property: C produced the observation, A stored it,
    D reads it back. It could not have been tested inside any one package.
    """
    from bartholomew.integration.learning_adapters import resolve_affected_applications
    from bartholomew.integration.multimodal_events import CanonicalIngressSink

    sink = CanonicalIngressSink(db_path=event_db, runtime_id="tenant-1")
    sink.submit(_envelope())
    sink.submit(
        _envelope(
            event_id="multimodal:def456",
            event_type="multimodal.accessibility.observation",
            payload={"session_id": "s1", "modality": "accessibility", "application": "excel.exe"},
        ),
    )
    # A different correlation must not leak in.
    sink.submit(
        _envelope(
            event_id="multimodal:ghi789",
            correlation_id="corr-other",
            payload={"session_id": "s2", "modality": "screen", "application": "secret.exe"},
        ),
    )

    proposal = resolve_affected_applications(
        correlation_id="corr-1",
        db_path=event_db,
        runtime_id="tenant-1",
    )
    assert proposal.applications == ("excel.exe", "notepad.exe")
    assert proposal.observed_events == 2
    assert proposal.to_dict()["applied"] is False


def test_affected_applications_are_tenant_scoped(event_db):
    """A correlation id alone must never reach across runtimes."""
    from bartholomew.integration.learning_adapters import resolve_affected_applications
    from bartholomew.integration.multimodal_events import CanonicalIngressSink

    CanonicalIngressSink(db_path=event_db, runtime_id="tenant-1").submit(_envelope())

    assert (
        resolve_affected_applications(
            correlation_id="corr-1",
            db_path=event_db,
            runtime_id="tenant-2",
        ).applications
        == ()
    )


# ===========================================================================
# Governance invariants that only hold across the whole integration
# ===========================================================================


def test_learning_accept_has_no_standing_permission_after_integration():
    """No candidate gains authority merely by existing, in the merged Identity.

    Five packages appended to one allowlist. The thing that must remain absent
    is the one that would turn "I may have learned something" into retrievable
    knowledge without a candidate-bound approval.
    """
    import yaml

    identity = yaml.safe_load(Path("Identity.yaml").read_text(encoding="utf-8"))
    allowlist = set(identity["tool_use"]["allowlist"])

    for forbidden in ("learning_accept", "share_accept", "windows_action_approve"):
        assert forbidden not in allowlist, f"{forbidden} must not hold standing permission"

    # And every package's own kinds did survive the five-way merge.
    for expected in (
        "inbound_event_process",
        "windows_action_request",
        "multimodal_screen_capture",
        "learning_propose",
        "share_adopt",
    ):
        assert expected in allowlist


def test_automatic_learning_acceptance_is_not_on_by_default():
    """The policy infrastructure may exist; the switch stays off.

    `execution_mode` is a property returning a module constant, so a user who
    records a preference for automatic acceptance has changed nothing about
    this build. Learning does not silently become execution.
    """
    from bartholomew.kernel import learning_policy

    policy = learning_policy.default_policy()
    assert policy.execution_mode == "shadow"


def test_there_is_exactly_one_installed_device_registry():
    """No second device registry: installing E's replaces, never shadows, B's."""
    from bartholomew.actuation import devices as actuation_devices
    from bartholomew.integration.device_registry import RegistryBackedDeviceRegistry

    previous = actuation_devices.get_registry()
    try:
        registry = RegistryBackedDeviceRegistry()
        actuation_devices.install_registry(registry)
        assert actuation_devices.get_registry() is registry
        assert actuation_devices.get_registry().describe()["interim"] is False
    finally:
        actuation_devices.install_registry(None if previous is None else previous)


def test_the_action_channel_stays_closed_unless_explicitly_opened(monkeypatch):
    """Integrating the system does not open actuation.

    Enabling observation must not enable actuation, so the action resolver has
    its own environment gate and installing the seams does not spend it.
    """
    from bartholomew.integration.device_action_resolver import (
        DEVICE_ACTION_AUTH_ENV,
        maybe_install_action_resolver_from_env,
    )

    monkeypatch.delenv(DEVICE_ACTION_AUTH_ENV, raising=False)
    assert maybe_install_action_resolver_from_env() is False


def test_an_explicitly_configured_interim_registry_is_not_overridden(tmp_path, monkeypatch):
    """One device truth per deployment, chosen by the operator -- not two.

    Package B ships a real, supported alpha configuration in which
    `BARTH_ACTION_DEVICE_ENROLMENT` names a file that *is* the registry.
    Installing Session E's registry over it would silently unenrol every
    device configured that way: the file would still be read for allowlists,
    so the deployment would look configured while refusing everything, and
    every refusal would say "device not enrolled" about a device the operator
    had enrolled.

    This is a regression test for exactly that -- it is what took Package B's
    real-HTTP suite from green to eight failures during this integration.
    """
    from bartholomew.actuation import devices as actuation_devices
    from bartholomew.actuation.devices import ENROLMENT_PATH_ENV
    from bartholomew.integration.device_action_resolver import DEVICE_ACTION_AUTH_ENV
    from bartholomew.integration.install import install_seams

    enrolment = tmp_path / "enrolment.json"
    enrolment.write_text(
        json.dumps(
            {
                "devices": [
                    {
                        "device_id": "alpha-pc",
                        "tenant_id": "local",
                        "platform": "windows",
                        "capabilities": ["windows.focus_window"],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv(DEVICE_ACTION_AUTH_ENV, raising=False)
    monkeypatch.setenv(ENROLMENT_PATH_ENV, str(enrolment))
    actuation_devices.install_registry(None)
    try:
        report = install_seams(db_path=str(tmp_path / "k.db"), tenant_id="local")

        # The operator's registry survived, and still enrols their device.
        registry = actuation_devices.get_registry()
        found = registry.lookup(tenant_id="local", device_id="alpha-pc")
        assert found is not None and found.enrolled is True
        # And the health surface says plainly which registry is running.
        assert registry.describe()["interim"] is True
        assert ENROLMENT_PATH_ENV in report.device_registry
    finally:
        actuation_devices.install_registry(None)


def test_installing_the_seams_reports_what_is_live(tmp_path, monkeypatch):
    """An operator can tell an integrated deployment from one on stand-ins."""
    from bartholomew.integration.device_action_resolver import DEVICE_ACTION_AUTH_ENV
    from bartholomew.integration.install import install_seams, last_report

    monkeypatch.delenv(DEVICE_ACTION_AUTH_ENV, raising=False)
    report = install_seams(db_path=str(tmp_path / "k.db"), tenant_id="tenant-1")

    assert report.errors == []
    assert report.to_dict()["integrated"] is True
    assert "platform-device-registry" in report.device_registry
    assert report.multimodal_sink == "canonical ingress (inbound_events)"
    assert len(report.event_types) == 5
    assert report.action_resolver.startswith("closed")
    assert last_report() is report
