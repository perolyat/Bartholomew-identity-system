"""
Package E: the device registry, tested against the real control-plane database.

Every claim here is asserted through `bartholomew.platform.devices` writing to
and reading from a real SQLite control plane, with real accounts provisioned
through `bartholomew.platform.accounts`. Nothing is mocked, because the
properties under test -- "a rotated credential stops working", "a credential
does not cross tenants", "no plaintext survives in the database" -- are
properties of the persistence, and a mock would assert only that the test
author remembered them.

The threat statements each test proves are named in its docstring, in the
style of the S8 suite next door.
"""

from __future__ import annotations

import logging
import tempfile

import pytest

from bartholomew.platform import accounts, device_capabilities, devices  # noqa: E402
from bartholomew.platform.principal import PrincipalKind  # noqa: E402
from bartholomew.platform.store import (  # noqa: E402
    init_platform_schema,
    platform_connection,
)

PASSWORD = "alpha-participant-password"


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
    """
    Set this module's environment for its own duration and restore it after.

    Module-level `os.environ[...]` assignment would leak the control-plane
    path into every other test file in the same pytest session. A
    module-scoped MonkeyPatch keeps the change contained to this file --
    the same pattern the S8 suite uses, and for the same reason.
    """
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="e-devices-")
    for var, value in {
        "BARTH_PLATFORM_DB_PATH": "<tmp>/platform.db",
        "BARTH_DATA_ROOT": "<tmp>/data",
    }.items():
        mp.setenv(var, value.replace("<tmp>", tmp))
    yield
    mp.undo()


@pytest.fixture(scope="module", autouse=True)
def _schema():
    init_platform_schema()


@pytest.fixture(scope="module")
def users():
    """Two ordinary participants. Idempotent, like the S8 suite's fixture."""
    init_platform_schema()
    made = {}
    for name in ("alice", "bob"):
        try:
            made[name] = accounts.create_account(name, PASSWORD, kind=PrincipalKind.USER)
        except accounts.AccountError:
            made[name] = next(
                a["user_id"] for a in accounts.list_accounts() if a["username"] == name
            )
    return made


WINDOWS_MANIFEST = {
    "platform": "windows",
    "companion_version": "0.1.0-prototype",
    "capabilities": [
        {"kind": "windows.open_url", "version": 1},
        {"kind": "windows.launch_app", "version": 1},
        {"kind": "multimodal.spoken_output", "version": 1},
    ],
}


def _enrolled(user_id, name="desk-pc", manifest=None):
    """Take one device all the way through enrolment. Returns (device_id, secret)."""
    device_id = devices.create_pending_enrolment(user_id, name, platform="windows")
    issued = devices.approve_enrolment(device_id, approver="ops")
    _verified, credential = devices.complete_enrolment(
        issued.secret,
        dict(manifest or WINDOWS_MANIFEST),
    )
    return device_id, credential.secret


# ---------------------------------------------------------------------------
# 1-3: enrolment states, and what each may authenticate as
# ---------------------------------------------------------------------------


def test_an_unenrolled_device_cannot_authenticate(users):
    """1. A credential the registry never issued authenticates nothing.

    Asserted against the real table: there is no row whose digest matches, and
    the absence is what refuses -- not a check somewhere upstream.
    """
    for candidate in ("", "   ", "not-a-real-credential", "a" * 600):
        with pytest.raises(devices.DeviceAuthenticationError):
            devices.verify_device_credential(candidate)


def test_a_pending_device_has_no_credential_at_all(users):
    """2. A pending device is an operator's note, and is exactly that powerful."""
    device_id = devices.create_pending_enrolment(users["alice"], "pending-pc", platform="windows")
    assert devices.get_device(device_id)["status"] == devices.DeviceStatus.PENDING.value
    with platform_connection() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM platform_device_credentials WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    assert rows["n"] == 0


def test_an_approved_device_cannot_authenticate_as_active(users):
    """2. An enrolment secret completes enrolment and does nothing else.

    The interesting failure this rules out is an enrolment secret quietly
    working as an ordinary credential, which would make "one-time" a
    documentation claim rather than a property.
    """
    device_id = devices.create_pending_enrolment(users["alice"], "approved-pc", platform="windows")
    issued = devices.approve_enrolment(device_id, approver="ops")
    assert devices.get_device(device_id)["status"] == devices.DeviceStatus.APPROVED.value

    with pytest.raises(devices.DeviceAuthenticationError):
        devices.verify_device_credential(issued.secret)

    verified, credential = devices.complete_enrolment(issued.secret, dict(WINDOWS_MANIFEST))
    assert verified.device_id == device_id
    assert devices.get_device(device_id)["status"] == devices.DeviceStatus.ACTIVE.value
    # And the enrolment secret is spent: a replayed first contact finds it revoked.
    with pytest.raises(devices.DeviceAuthenticationError):
        devices.complete_enrolment(issued.secret, dict(WINDOWS_MANIFEST))
    assert devices.verify_device_credential(credential.secret).device_id == device_id


def test_an_enrolled_device_authenticates_only_as_itself(users):
    """3. Two enrolled devices never resolve to each other.

    The identity comes from the credential row, so there is no code path by
    which presenting device A's credential yields device B -- and no field a
    caller can send to suggest otherwise.
    """
    first_id, first_secret = _enrolled(users["alice"], "first-pc")
    second_id, second_secret = _enrolled(users["alice"], "second-pc")
    assert first_id != second_id

    assert devices.verify_device_credential(first_secret).device_id == first_id
    assert devices.verify_device_credential(second_secret).device_id == second_id


# ---------------------------------------------------------------------------
# 4: tenant isolation
# ---------------------------------------------------------------------------


def test_a_device_credential_cannot_cross_tenants(users):
    """4. Alice's device credential is refused when Bob's tenant is expected.

    Refused at the verification boundary, not handed back for the caller to
    compare: a check every call site has to remember is a check some call site
    will forget.
    """
    device_id, secret = _enrolled(users["alice"], "alice-pc")

    assert (
        devices.verify_device_credential(
            secret,
            expected_user_id=users["alice"],
        ).device_id
        == device_id
    )

    with pytest.raises(devices.DeviceAuthenticationError):
        devices.verify_device_credential(secret, expected_user_id=users["bob"])


def test_a_tenants_read_surface_does_not_reach_another_tenants_devices(users):
    """4. `list_devices` and `get_device` are scoped by predicate.

    There is deliberately no "list all devices" helper: the shape of the
    function is the isolation, so a caller cannot reach another tenant's
    devices by omitting an argument.
    """
    alice_device, _ = _enrolled(users["alice"], "alice-only-pc")
    bob_ids = {row["device_id"] for row in devices.list_devices(users["bob"])}
    assert alice_device not in bob_ids
    assert devices.get_device(alice_device, user_id=users["bob"]) is None
    assert devices.get_device(alice_device, user_id=users["alice"]) is not None


# ---------------------------------------------------------------------------
# 5-7: rotation, revocation, disable
# ---------------------------------------------------------------------------


def test_rotating_a_credential_invalidates_the_previous_one(users):
    """5. Rotation is not "add another credential"."""
    device_id, old_secret = _enrolled(users["alice"], "rotate-pc")
    assert devices.verify_device_credential(old_secret).device_id == device_id

    issued = devices.rotate_device_credential(device_id, actor="ops")

    with pytest.raises(devices.DeviceAuthenticationError):
        devices.verify_device_credential(old_secret)
    assert devices.verify_device_credential(issued.secret).device_id == device_id
    assert issued.secret != old_secret


def test_revocation_takes_effect_immediately(users):
    """6. The next presentation fails. Not at the end of a session, not on a TTL."""
    device_id, secret = _enrolled(users["alice"], "revoke-pc")
    assert devices.verify_device_credential(secret).device_id == device_id

    devices.revoke_device(device_id, actor="ops", reason="left on a train")

    with pytest.raises(devices.DeviceAuthenticationError):
        devices.verify_device_credential(secret)
    assert devices.get_device(device_id)["status"] == devices.DeviceStatus.REVOKED.value
    # Terminal: there is no path back.
    with pytest.raises(devices.DeviceError):
        devices.set_device_disabled(device_id, False, actor="ops")


def test_a_disabled_device_cannot_authenticate_and_can_be_re_enabled(users):
    """7. Disable is reversible and credential-preserving; it still refuses."""
    device_id, secret = _enrolled(users["alice"], "disable-pc")

    devices.set_device_disabled(device_id, True, actor="ops")
    with pytest.raises(devices.DeviceAuthenticationError):
        devices.verify_device_credential(secret)

    devices.set_device_disabled(device_id, False, actor="ops")
    assert devices.verify_device_credential(secret).device_id == device_id


def test_a_device_belonging_to_a_disabled_account_cannot_authenticate(users):
    """6/7. A device is only ever as live as the account it belongs to."""
    carol = accounts.create_account("carol-devices", PASSWORD)
    device_id, secret = _enrolled(carol, "carol-pc")
    assert devices.verify_device_credential(secret).device_id == device_id

    accounts.set_account_disabled(carol, True)
    with pytest.raises(devices.DeviceAuthenticationError):
        devices.verify_device_credential(secret)


# ---------------------------------------------------------------------------
# 8: no plaintext credential is retained anywhere
# ---------------------------------------------------------------------------


def test_no_plaintext_credential_appears_in_any_control_plane_row(users):
    """8. After issuance, the secret exists only in the caller's hand.

    Scans **every column of every table** in the control plane, discovered
    from `sqlite_master` rather than from a hardcoded list, so a table added
    later is covered by this test on the day it is added rather than on the
    day someone remembers to extend it.
    """
    device_id = devices.create_pending_enrolment(users["bob"], "hygiene-pc", platform="windows")
    enrolment = devices.approve_enrolment(device_id, approver="ops")
    _verified, credential = devices.complete_enrolment(enrolment.secret, dict(WINDOWS_MANIFEST))
    rotated = devices.rotate_device_credential(device_id, actor="ops")
    secrets = (enrolment.secret, credential.secret, rotated.secret)

    haystack: list[str] = []
    with platform_connection() as conn:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'",
            ).fetchall()
        ]
        assert "platform_device_credentials" in tables, "the scan must cover the credential table"
        for table in tables:
            for row in conn.execute(f"SELECT * FROM {table}").fetchall():  # noqa: S608
                haystack.extend("" if value is None else str(value) for value in tuple(row))

    blob = "\n".join(haystack)
    for secret in secrets:
        assert secret not in blob, "a plaintext device credential was retained in the database"

    # The digest is what is stored, and it is not reversible to the secret.
    with platform_connection() as conn:
        stored = {
            row["secret_hash"]
            for row in conn.execute(
                "SELECT secret_hash FROM platform_device_credentials WHERE device_id = ?",
                (device_id,),
            ).fetchall()
        }
    assert stored and all(len(digest) == 64 for digest in stored)


def test_no_read_surface_returns_credential_material(users):
    """8. Nothing an operator or a UI can call hands back a credential.

    Covers the registry's whole public read surface at once, including the
    audit trail -- which records credential *ids*, never secrets, exactly as
    `sessions.create_session` records a session id and never its token.
    """
    device_id = devices.create_pending_enrolment(users["bob"], "read-pc", platform="windows")
    enrolment = devices.approve_enrolment(device_id, approver="ops")
    _verified, credential = devices.complete_enrolment(enrolment.secret, dict(WINDOWS_MANIFEST))

    surfaces = [
        str(devices.get_device(device_id)),
        str(devices.list_devices(users["bob"])),
        str(devices.describe_manifest(device_id)),
        str(devices.manifest_json(device_id)),
        str(devices.device_audit(user_id=users["bob"], limit=200)),
    ]
    for rendered in surfaces:
        assert enrolment.secret not in rendered
        assert credential.secret not in rendered
        assert "secret_hash" not in rendered


def test_no_plaintext_credential_is_logged(users, caplog):
    """8. Not in the happy path, and not in the refusal path either.

    A refusal that echoed the presented credential would put every guess an
    attacker made into the operator's log file.
    """
    device_id = devices.create_pending_enrolment(users["bob"], "log-pc", platform="windows")
    with caplog.at_level(logging.DEBUG):
        enrolment = devices.approve_enrolment(device_id, approver="ops")
        _v, credential = devices.complete_enrolment(enrolment.secret, dict(WINDOWS_MANIFEST))
        devices.verify_device_credential(credential.secret)
        with pytest.raises(devices.DeviceAuthenticationError):
            devices.verify_device_credential("a-guess-that-should-never-be-logged")

    captured = caplog.text
    assert enrolment.secret not in captured
    assert credential.secret not in captured
    assert "a-guess-that-should-never-be-logged" not in captured


# ---------------------------------------------------------------------------
# 9-10: capabilities
# ---------------------------------------------------------------------------


def test_the_capability_vocabulary_is_the_frozen_set():
    """9/10. The vocabulary is pinned, so widening it cannot happen incidentally.

    An extra kind here is a new thing that becomes sayable about a device.
    That is a decision, and this test is what makes it one.
    """
    assert set(device_capabilities.CAPABILITY_KINDS) == {
        "windows.open_url",
        "windows.open_path",
        "windows.launch_app",
        "windows.focus_window",
        "windows.manage_window",
        "windows.clipboard_read",
        "windows.clipboard_write",
        "windows.type_text",
        "windows.accessibility_action",
        "multimodal.microphone_session",
        "multimodal.screen_capture",
        "multimodal.spoken_output",
    }
    for kind in device_capabilities.CAPABILITY_KINDS:
        assert device_capabilities.CAPABILITY_VERSIONS[kind] == frozenset({1})


def test_a_device_cannot_advertise_capabilities_outside_its_manifest(users):
    """9. Declaring is not authorising, and neither is the vocabulary alone.

    `windows.open_path` is a kind this deployment understands perfectly well.
    The device did not declare it, so it is refused -- understanding a
    capability is not a licence to use a device that never claimed it.
    """
    _device_id, secret = _enrolled(users["alice"], "manifest-pc")
    verified = devices.verify_device_credential(secret)

    assert verified.authorizes("windows.open_url", 1)
    verified.require_capability("windows.open_url", 1)

    assert not verified.authorizes("windows.open_path", 1)
    with pytest.raises(devices.DeviceCapabilityError):
        verified.require_capability("windows.open_path", 1)


def test_unknown_capability_kinds_and_versions_are_unsupported_not_approximated(users):
    """10. `open_url` v2 is not v1 with extras; it is an unknown contract.

    Both halves are proven: an unknown *kind* and a known kind at an unknown
    *version* are each recorded as declared and each authorise nothing. There
    is no nearest-match and no highest-version-we-know fallback.
    """
    manifest = {
        "platform": "windows",
        "companion_version": "9.9.9-future",
        "capabilities": [
            {"kind": "windows.open_url", "version": 1},
            {"kind": "windows.open_url", "version": 2},
            {"kind": "windows.summon_daemon", "version": 1},
        ],
    }
    _device_id, secret = _enrolled(users["alice"], "future-pc", manifest=manifest)
    verified = devices.verify_device_credential(secret)

    assert verified.authorizes("windows.open_url", 1)
    assert not verified.authorizes("windows.open_url", 2)
    assert not verified.authorizes("windows.summon_daemon", 1)
    assert not device_capabilities.supports("windows.open_url", 2)
    assert not device_capabilities.supports("windows.summon_daemon", 1)

    # Declared-but-unsupported entries are recorded, so an operator can see
    # what the device claims this deployment cannot act on.
    unsupported = {str(c) for c in verified.manifest.unknown}
    assert unsupported == {"windows.open_url@v2", "windows.summon_daemon@v1"}
    for kind, version in (("windows.open_url", 2), ("windows.summon_daemon", 1)):
        with pytest.raises(devices.DeviceCapabilityError):
            verified.require_capability(kind, version)


def test_a_declaration_with_a_boolean_version_is_refused(users):
    """10. `{"version": true}` must not become version 1 by way of Python.

    `bool` is an `int` in Python, so a capability granted by a typo is a real
    shape of mistake rather than a hypothetical one.
    """
    device_id = devices.create_pending_enrolment(users["alice"], "typo-pc", platform="windows")
    issued = devices.approve_enrolment(device_id, approver="ops")
    with pytest.raises(device_capabilities.ManifestError):
        devices.complete_enrolment(
            issued.secret,
            {
                "platform": "windows",
                "companion_version": "0.1.0",
                "capabilities": [{"kind": "windows.open_url", "version": True}],
            },
        )
    # The secret was not consumed: a malformed manifest is the companion's to
    # fix and retry, not the operator's to re-approve.
    assert devices.get_device(device_id)["status"] == devices.DeviceStatus.APPROVED.value


# ---------------------------------------------------------------------------
# 11-12: last-seen, and claimed vs verified identity
# ---------------------------------------------------------------------------


def test_last_seen_updates_only_after_verified_contact(users):
    """11. The column means "this device was here", not "somebody guessed at it"."""
    device_id = devices.create_pending_enrolment(users["alice"], "seen-pc", platform="windows")
    assert devices.get_device(device_id)["last_seen_at"] is None

    issued = devices.approve_enrolment(device_id, approver="ops")
    assert devices.get_device(device_id)["last_seen_at"] is None

    _v, credential = devices.complete_enrolment(issued.secret, dict(WINDOWS_MANIFEST))
    enrolled_at = devices.get_device(device_id)["last_seen_at"]
    assert enrolled_at is not None

    with pytest.raises(devices.DeviceAuthenticationError):
        devices.verify_device_credential("a-credential-that-does-not-exist")
    assert devices.get_device(device_id)["last_seen_at"] == enrolled_at

    devices.verify_device_credential(credential.secret, now=enrolled_at + 500)
    assert devices.get_device(device_id)["last_seen_at"] == enrolled_at + 500

    # A refused presentation of a *real but revoked* credential must not
    # refresh it either -- the check that fails must fail before the write.
    devices.revoke_device(device_id, actor="ops")
    with pytest.raises(devices.DeviceAuthenticationError):
        devices.verify_device_credential(credential.secret, now=enrolled_at + 9000)
    assert devices.get_device(device_id)["last_seen_at"] == enrolled_at + 500


def test_a_claimed_device_id_in_the_manifest_cannot_override_the_verified_one(users):
    """12. The companion is describing itself; which self is not its decision.

    The manifest is taken with `device_id` supplied separately from verified
    state, and any `device_id` in the declared body is ignored rather than
    overwritten afterwards -- so there is no window in which the claimed value
    is the live one.
    """
    victim_id, _victim_secret = _enrolled(users["bob"], "victim-pc")

    device_id = devices.create_pending_enrolment(users["alice"], "liar-pc", platform="windows")
    issued = devices.approve_enrolment(device_id, approver="ops")
    verified, credential = devices.complete_enrolment(
        issued.secret,
        {**WINDOWS_MANIFEST, "device_id": victim_id},
    )

    assert verified.device_id == device_id
    assert verified.user_id == users["alice"]
    assert devices.describe_manifest(device_id)["device_id"] == device_id
    assert devices.verify_device_credential(credential.secret).device_id == device_id
    # And the victim's row is untouched.
    assert devices.get_device(victim_id)["user_id"] == users["bob"]


def test_enrolment_and_revocation_are_audited(users):
    """The operator's record of how a device came to be trusted, and stopped being.

    Uses the existing `platform_audit` table rather than a second log: one
    place to look is worth more than a purpose-built one.
    """
    device_id = devices.create_pending_enrolment(users["bob"], "audit-pc", platform="windows")
    issued = devices.approve_enrolment(device_id, approver="ops")
    devices.complete_enrolment(issued.secret, dict(WINDOWS_MANIFEST))
    devices.rotate_device_credential(device_id, actor="ops")
    devices.revoke_device(device_id, actor="ops", reason="compromised")

    events = [
        row["event"]
        for row in devices.device_audit(user_id=users["bob"], limit=500)
        if device_id in (row["detail"] or "")
    ]
    assert events[:5] == [
        "device.revoked",
        "device.credential_rotated",
        "device.enrolled",
        "device.enrolment_approved",
        "device.enrolment_pending",
    ]


def test_a_platform_administrator_has_no_devices(users):
    """An administrator has no personal Bartholomew for a device to speak to.

    Mirrors `runtime_registry.runtime_handle_for`, which refuses an admin a
    personal runtime rather than silently giving them one.
    """
    ops = accounts.create_account("ops-devices", PASSWORD, kind=PrincipalKind.PLATFORM_ADMIN)
    with pytest.raises(devices.DeviceError):
        devices.create_pending_enrolment(ops, "admin-pc", platform="windows")
