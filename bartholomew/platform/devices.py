"""
The device registry: which machines may speak for a personal Bartholomew.

Before this module, a companion's `device_id` was a **claim**. The inbound
boundary verified that an event came from a configured source and recorded
the device label verbatim, and `bartholomew/companion/envelope.py` says so in
as many words: "`payload['device_id']` is *claimed* provenance ... it is not
authenticated, and nothing in Bartholomew may treat it as though it were."
This module is what makes a device identity something the platform issued and
can withdraw, so that a later "which device asked for this?" has an answer.

Where this sits
---------------
It is a **control-plane** module. Devices belong to accounts, and an account
is a control-plane object, so the registry lives in `platform.db` beside
accounts and sessions rather than inside any user's kernel database. Two
consequences, both intended:

* revoking a device does not require the user's runtime to be running, which
  is precisely the state a lost laptop tends to be discovered in; and
* no per-user kernel is handed a write path to the credential table, so a
  defect in kernel code cannot mint or read another tenant's device
  credential.

Tenant isolation here is by predicate, not by file
--------------------------------------------------
`runtime_registry` isolates *personal memory* by giving each user their own
database file. The control plane cannot do that -- it is the shared store, by
design -- so every query below is scoped by `user_id`, and the tenant binding
is carried on the credential row itself rather than being re-derived. That is
a weaker isolation shape than the kernel's and it is stated plainly rather
than implied: see `docs/E_DEVICE_TRUST_AND_TRUSTED_GROUPS.md` for the
threat-model consequences.

Credentials
-----------
A device credential is 256 bits of `secrets` randomness. **Only its SHA-256
digest is stored**, exactly as `sessions.py` stores session tokens and for
the same reason: read access to the table must not yield a usable credential.
A plain digest rather than a password KDF is correct here -- the secret has
no guessable structure for a KDF to protect. The plaintext is returned once,
from the function that mints it, and never logged, never audited, never
returned by any read path. `tests/test_device_registry_trust.py` asserts that
against the real database, the real audit table and the captured logs.

Verification is fail-closed on every axis: unknown digest, wrong purpose,
expired, superseded by rotation, revoked, device not `active`, device's
account disabled or gone, and -- when the caller names an expected tenant --
a tenant mismatch. There is no branch that returns a degraded or partially
trusted device, because no such value exists in this module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import Enum

from .device_capabilities import (
    MANIFEST_VERSION_NONE,
    Capability,
    DeviceCapabilityManifest,
    ManifestError,
    parse_capability,
)
from .store import platform_connection, record_platform_audit

logger = logging.getLogger(__name__)

#: How long an unused enrolment secret stays usable. Short on purpose: it is
#: a one-time secret an operator hand-carries to a machine, so its useful
#: life is minutes to hours, and every hour past that is a window in which a
#: leaked note is still a working credential.
DEFAULT_ENROLMENT_TTL_S = 24 * 60 * 60

#: Long-lived device credentials do not expire on a clock. They end by
#: rotation or revocation, both of which are immediate and both of which are
#: audited -- an expiry would add a failure mode (a device that silently
#: stops working at 3am) without adding a control the operator does not
#: already have.
_TOKEN_BYTES = 32  # 256 bits

#: Bounds the work an unauthenticated caller can force on the database.
_MAX_TOKEN_LENGTH = 512

_MAX_LABEL_LENGTH = 128


class DeviceStatus(str, Enum):
    """Where a device is in its lifecycle.

    The distinction between `PENDING`, `APPROVED` and `ACTIVE` is the whole
    of "enrolment is explicit and auditable":

    * `PENDING`   -- an operator has written down that a device is expected.
                     No credential exists. It can authenticate nothing.
    * `APPROVED`  -- the operator has approved it and a **one-time enrolment
                     secret** has been issued. That secret completes
                     enrolment and does nothing else: it is not accepted by
                     `verify_device_credential`, so an approved device still
                     cannot act as an authenticated source.
    * `ACTIVE`    -- the companion has made its first authenticated contact,
                     declared its manifest, and been issued a long-lived
                     device credential. Only here can it act.
    * `DISABLED`  -- temporarily refused. Credentials survive, so re-enabling
                     does not require re-enrolment; nothing authenticates
                     while it holds.
    * `REVOKED`   -- terminal. Every credential is revoked in the same
                     transaction, and no path re-activates the row.
    """

    PENDING = "pending"
    APPROVED = "approved"
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


#: Statuses from which a device may still complete enrolment.
_ENROLLABLE = frozenset({DeviceStatus.APPROVED})

#: The single status from which a device may authenticate. A frozenset of one
#: rather than a `==` so that widening it is a visible, greppable edit.
_AUTHENTICABLE = frozenset({DeviceStatus.ACTIVE})


class CredentialPurpose(str, Enum):
    """What a credential may be used for -- checked, never inferred.

    Two purposes, and they are not interchangeable. An enrolment secret that
    could authenticate ordinary traffic would make "one-time" a documentation
    claim rather than a property; a device credential that could re-run
    enrolment would let a compromised device re-declare its own capabilities.
    """

    ENROLMENT = "enrolment"
    DEVICE = "device"


class DeviceError(Exception):
    """A device registry operation could not be performed as asked.

    Operator-facing. Never raised on a credential presentation -- see
    `DeviceAuthenticationError` for that, so a lifecycle mistake and a failed
    authentication can never be handled by one permissive branch.
    """


class DeviceAuthenticationError(Exception):
    """A presented device credential did not authenticate.

    Deliberately one exception for every failure mode -- absent, malformed,
    unknown, wrong purpose, expired, rotated, revoked, disabled, orphaned or
    belonging to another tenant. Callers must not be able to branch on *why*,
    because a caller that can branch is an oracle a prober can query.
    """


class DeviceCapabilityError(Exception):
    """A device attempted, or was asked for, a capability it does not hold.

    Distinct from an authentication failure: the device is who it says it is
    and simply may not do this. Mapping to 403 rather than 401 is the point.
    """


# ---------------------------------------------------------------------------
# Verified identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifiedDevice:
    """A device identity established from a presented credential.

    Frozen, and constructible in practice only by `verify_device_credential`
    -- the same discipline `platform.principal.Principal` holds to. There is
    no anonymous or provisional device, so "we could not tell which device
    this is" cannot be represented and cannot leak into a downstream `if`.

    `user_id` is the **owning tenant as recorded at enrolment**. It is never
    read from a request, a header, a payload or the device's own claim.
    """

    device_id: str
    user_id: str
    platform: str
    companion_version: str
    manifest_version: int
    manifest: DeviceCapabilityManifest
    credential_id: str
    #: The operator's ceiling, recorded at approval. `None` means the operator
    #: set none, and the ceiling is whatever this deployment understands.
    approved_capabilities: tuple[Capability, ...] | None = None

    def authorizes(self, kind: str, version: int) -> bool:
        """Whether this device may be asked to perform `(kind, version)`.

        Three things must hold, not two: this deployment understands the
        capability, the device declared it, **and** the operator's ceiling
        admits it. The third exists because a manifest is the device's own
        claim -- approving a machine and believing everything it later says it
        can do are different acts, and only the first is something an operator
        did.
        """
        if not self.manifest.authorizes(kind, version):
            return False
        if self.approved_capabilities is None:
            return True
        return any(c.kind == kind and c.version == version for c in self.approved_capabilities)

    def require_capability(self, kind: str, version: int) -> None:
        """Raise `DeviceCapabilityError` unless this device holds the capability.

        The check Sessions B and C call before writing anything that would
        act. It refuses three different mistakes with one branch: a capability
        the device never declared, a capability at a version this deployment
        does not understand (see `device_capabilities.supports`, which never
        approximates), and one outside the operator's approval ceiling.
        """
        if not self.authorizes(kind, version):
            raise DeviceCapabilityError(
                f"device {self.device_id} is not authorised for {kind!r} v{version}: "
                "it is absent from the device's registered manifest or from the "
                "operator's approved capability set, or this deployment does not "
                "understand that capability version",
            )


@dataclass(frozen=True)
class IssuedCredential:
    """A freshly minted credential and its plaintext -- returned exactly once.

    The plaintext lives in this object and nowhere else. It is not stored, not
    logged, not audited and not recoverable: losing it means rotating, which
    is cheap and is the intended failure mode.
    """

    credential_id: str
    device_id: str
    user_id: str
    purpose: CredentialPurpose
    secret: str
    expires_at: int | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_secret(secret: str) -> str:
    """SHA-256 of a device secret, as stored.

    Matches `sessions._hash_token` deliberately, including the reasoning: the
    secret is 256 bits of `secrets` randomness, so there is no guessable
    structure for a memory-hard KDF to defend, and the digest exists so that
    read access to the table yields nothing usable.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _mint_secret() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _clean_label(value: str | None, field: str) -> str:
    text = (value or "").strip()
    if not text:
        raise DeviceError(f"{field} must not be empty")
    if len(text) > _MAX_LABEL_LENGTH:
        raise DeviceError(f"{field} exceeds {_MAX_LABEL_LENGTH} characters")
    return text


def _approved_capabilities(row: sqlite3.Row) -> tuple[Capability, ...] | None:
    """The operator's approved capability ceiling, or None when unrestricted.

    An unparseable ceiling yields an **empty** tuple rather than None: "we
    cannot tell what the operator approved" must authorise nothing, not
    everything. The same fail-closed reading `DeviceCapabilityManifest.from_row`
    applies to a corrupt manifest.
    """
    try:
        raw = row["approved_capabilities"]
    except (IndexError, KeyError):
        return None
    if raw is None:
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    if not isinstance(decoded, list):
        return ()
    parsed: list[Capability] = []
    for entry in decoded:
        try:
            parsed.append(parse_capability(entry))
        except ManifestError:
            continue
    return tuple(parsed)


def _status(row: sqlite3.Row) -> DeviceStatus:
    try:
        return DeviceStatus(row["status"])
    except ValueError as exc:
        # A hand-edited or corrupt status must not default to anything
        # usable. Same rule as `sessions._verify_session_locked`'s unknown
        # principal kind.
        raise DeviceAuthenticationError("device row has an unrecognised status") from exc


def _now(now: int | None) -> int:
    return int(now if now is not None else time.time())


# ---------------------------------------------------------------------------
# Enrolment
# ---------------------------------------------------------------------------


def create_pending_enrolment(
    user_id: str,
    display_name: str,
    *,
    platform: str,
    db_path: str | None = None,
    now: int | None = None,
) -> str:
    """Record that a device is expected. Returns the server-generated `device_id`.

    Creates no credential and authorises nothing: a `PENDING` row is an
    operator's note that a machine is about to be enrolled, and is exactly as
    powerful as such a note should be. The id is a fresh UUID4 rather than
    anything derived from the label, for the same reason `user_id` is: it is
    the key downstream authorisation is written against, so it must not be
    guessable from something a person chose.
    """
    display_name = _clean_label(display_name, "display_name")
    platform = _clean_label(platform, "platform")
    if not (user_id or "").strip():
        raise DeviceError("user_id is required -- a device always belongs to one account")

    device_id = str(uuid.uuid4())
    ts = _now(now)
    with platform_connection(db_path) as conn:
        account = conn.execute(
            "SELECT kind, disabled_at FROM platform_accounts WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if account is None:
            raise DeviceError(f"no account {user_id!r}; provision one before enrolling a device")
        if account["disabled_at"] is not None:
            raise DeviceError(f"account {user_id!r} is disabled")
        if account["kind"] != "user":
            raise DeviceError(
                f"account {user_id!r} is a {account['kind']}, which has no personal "
                "Bartholomew for a device to speak to",
            )
        conn.execute(
            "INSERT INTO platform_devices"
            "(device_id, user_id, display_name, platform, companion_version,"
            " manifest_version, capabilities, status, created_at) "
            "VALUES (?, ?, ?, ?, NULL, ?, '[]', ?, ?)",
            (
                device_id,
                user_id,
                display_name,
                platform,
                MANIFEST_VERSION_NONE,
                DeviceStatus.PENDING.value,
                ts,
            ),
        )
        record_platform_audit(
            conn,
            "device.enrolment_pending",
            user_id=user_id,
            detail=f"device={device_id} name={display_name} platform={platform}",
            ts=ts,
        )
    return device_id


def approve_enrolment(
    device_id: str,
    *,
    approver: str,
    permitted_capabilities: list[dict] | None = None,
    db_path: str | None = None,
    ttl_s: int = DEFAULT_ENROLMENT_TTL_S,
    now: int | None = None,
) -> IssuedCredential:
    """Approve a pending device and mint its **one-time** enrolment secret.

    The returned plaintext is the only copy. It is not stored, and the audit
    row records the credential *id* and the approver, never the secret -- the
    same split `sessions.create_session` makes between a session id and its
    token.

    Approval is not activation: the device moves to `APPROVED`, and an
    approved device still cannot authenticate ordinary traffic. Only
    `complete_enrolment` -- which consumes this secret exactly once -- makes
    it `ACTIVE`.

    `permitted_capabilities` is the operator's **ceiling** on what this
    machine may ever be authorised for, as `[{"kind": ..., "version": ...}]`.
    It exists because approving a *device* and believing its *capability
    declaration* are two different acts: without a ceiling, a companion that
    declared `windows.type_text` and `multimodal.screen_capture` would be
    authorised for both the moment it enrolled, on nothing but its own say-so.
    `None` keeps the previous behaviour -- the ceiling is whatever this
    deployment understands -- which is the right default for a machine the
    operator is standing in front of, and the wrong one for a machine they are
    not.

    Re-approving an already-approved device is permitted and is the documented
    recovery path for a lost secret: the previous one is revoked in the same
    transaction, so there is never a second live enrolment secret.
    """
    if not (approver or "").strip():
        raise DeviceError("an approver is required -- enrolment is never anonymous")
    ceiling: str | None = None
    if permitted_capabilities is not None:
        ceiling = json.dumps(
            [parse_capability(entry).as_dict() for entry in permitted_capabilities],
            separators=(",", ":"),
        )
    ts = _now(now)
    secret = _mint_secret()
    credential_id = str(uuid.uuid4())
    expires_at = ts + int(ttl_s)

    with platform_connection(db_path) as conn:
        row = _load_device_row(conn, device_id)
        status = DeviceStatus(row["status"])
        if status not in (DeviceStatus.PENDING, DeviceStatus.APPROVED):
            # `APPROVED` is included so the documented recovery path -- "if the
            # secret is lost before use, approve again" -- actually exists.
            # Refusing it left a device that had been approved once, whose
            # secret had gone astray, permanently unenrollable.
            raise DeviceError(
                f"device {device_id} is {status.value}; only a pending or "
                "already-approved device may be issued an enrolment secret",
            )
        # Any earlier enrolment secret for this device stops working now, so
        # re-approving after a lost note does not leave two live secrets.
        conn.execute(
            "UPDATE platform_device_credentials SET revoked_at = ? "
            "WHERE device_id = ? AND revoked_at IS NULL",
            (ts, device_id),
        )
        conn.execute(
            "INSERT INTO platform_device_credentials"
            "(credential_id, device_id, user_id, secret_hash, purpose,"
            " created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                credential_id,
                device_id,
                row["user_id"],
                _hash_secret(secret),
                CredentialPurpose.ENROLMENT.value,
                ts,
                expires_at,
            ),
        )
        conn.execute(
            "UPDATE platform_devices SET status = ?, approved_at = ?, "
            "approved_capabilities = ? WHERE device_id = ?",
            (DeviceStatus.APPROVED.value, ts, ceiling, device_id),
        )
        record_platform_audit(
            conn,
            "device.enrolment_approved",
            user_id=row["user_id"],
            detail=(
                f"device={device_id} credential={credential_id} "
                f"approver={approver} expires_at={expires_at} "
                f"ceiling={'unrestricted' if ceiling is None else ceiling}"
            ),
            ts=ts,
        )
    return IssuedCredential(
        credential_id=credential_id,
        device_id=device_id,
        user_id=row["user_id"],
        purpose=CredentialPurpose.ENROLMENT,
        secret=secret,
        expires_at=expires_at,
    )


def complete_enrolment(
    enrolment_secret: str,
    declaration: dict,
    *,
    db_path: str | None = None,
    now: int | None = None,
) -> tuple[VerifiedDevice, IssuedCredential]:
    """The companion's first authenticated contact. Consumes the secret once.

    Takes the enrolment secret and the device's declared capability manifest,
    and returns the verified device plus its long-lived credential -- whose
    plaintext, again, is returned once and stored nowhere.

    The manifest's `device_id` is taken from the credential row, never from
    the declaration: the companion is describing itself, and which self it is
    describing is not its decision. A malformed declaration refuses the whole
    enrolment (`ManifestError`) rather than activating a device with an empty
    manifest, because a device that enrolled with nothing recorded is a
    device nobody can reason about afterwards.

    The enrolment secret is revoked in the same transaction that activates
    the device, so a replayed first contact finds a revoked credential.
    """
    ts = _now(now)
    with platform_connection(db_path) as conn:
        credential = _lookup_credential(conn, enrolment_secret)
        if credential["purpose"] != CredentialPurpose.ENROLMENT.value:
            raise DeviceAuthenticationError("credential is not an enrolment secret")
        if credential["revoked_at"] is not None:
            raise DeviceAuthenticationError("enrolment secret has been used or revoked")
        if credential["expires_at"] is not None and ts >= credential["expires_at"]:
            raise DeviceAuthenticationError("enrolment secret has expired")

        row = _load_device_row(conn, credential["device_id"], error=DeviceAuthenticationError)
        status = _status(row)
        if status not in _ENROLLABLE:
            raise DeviceAuthenticationError(
                f"device is {status.value} and cannot complete enrolment",
            )
        _require_live_account(conn, row["user_id"])

        try:
            manifest = DeviceCapabilityManifest.from_declaration(
                declaration,
                device_id=row["device_id"],
            )
        except ManifestError:
            # Not counted as a use of the secret: a companion that sent a
            # malformed manifest should be able to fix it and retry rather
            # than needing the operator to re-approve. The transaction is
            # rolled back by the raise, so nothing was consumed.
            raise

        device_secret = _mint_secret()
        device_credential_id = str(uuid.uuid4())

        conn.execute(
            "UPDATE platform_device_credentials SET revoked_at = ?, first_used_at = ? "
            "WHERE credential_id = ?",
            (ts, ts, credential["credential_id"]),
        )
        conn.execute(
            "INSERT INTO platform_device_credentials"
            "(credential_id, device_id, user_id, secret_hash, purpose, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                device_credential_id,
                row["device_id"],
                row["user_id"],
                _hash_secret(device_secret),
                CredentialPurpose.DEVICE.value,
                ts,
            ),
        )
        ceiling = _approved_capabilities(row)
        verified_capabilities = tuple(
            c
            for c in manifest.known
            if ceiling is None or any(p.kind == c.kind and p.version == c.version for p in ceiling)
        )
        manifest_version = int(row["manifest_version"] or 0) + 1
        conn.execute(
            "UPDATE platform_devices SET status = ?, enrolled_at = ?, last_seen_at = ?, "
            "platform = ?, companion_version = ?, capabilities = ?, manifest_version = ? "
            "WHERE device_id = ?",
            (
                DeviceStatus.ACTIVE.value,
                ts,
                ts,
                manifest.platform,
                manifest.companion_version,
                manifest.capabilities_json(),
                manifest_version,
                row["device_id"],
            ),
        )
        record_platform_audit(
            conn,
            "device.enrolled",
            user_id=row["user_id"],
            detail=(
                f"device={row['device_id']} credential={device_credential_id} "
                f"manifest_version={manifest_version} "
                f"declared={len(manifest.capabilities)} "
                f"supported={len(manifest.known)} "
                f"unsupported={sorted(str(c) for c in manifest.unknown)} "
                f"authorised={sorted(str(c) for c in verified_capabilities)}"
            ),
            ts=ts,
        )

    verified = VerifiedDevice(
        device_id=row["device_id"],
        user_id=row["user_id"],
        platform=manifest.platform,
        companion_version=manifest.companion_version,
        manifest_version=manifest_version,
        manifest=manifest,
        credential_id=device_credential_id,
        approved_capabilities=_approved_capabilities(row),
    )
    issued = IssuedCredential(
        credential_id=device_credential_id,
        device_id=row["device_id"],
        user_id=row["user_id"],
        purpose=CredentialPurpose.DEVICE,
        secret=device_secret,
        expires_at=None,
    )
    return verified, issued


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _lookup_credential(conn: sqlite3.Connection, secret: str) -> sqlite3.Row:
    if not secret or not isinstance(secret, str):
        raise DeviceAuthenticationError("no device credential presented")
    if len(secret) > _MAX_TOKEN_LENGTH:
        raise DeviceAuthenticationError("malformed device credential")
    row = conn.execute(
        "SELECT * FROM platform_device_credentials WHERE secret_hash = ?",
        (_hash_secret(secret),),
    ).fetchone()
    if row is None:
        raise DeviceAuthenticationError("unknown device credential")
    return row


def _load_device_row(
    conn: sqlite3.Connection,
    device_id: str,
    *,
    error: type[Exception] = DeviceError,
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM platform_devices WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    if row is None:
        raise error(f"no device {device_id!r}")
    return row


def _require_live_account(conn: sqlite3.Connection, user_id: str) -> None:
    """A device is only as live as the account it belongs to.

    An orphaned device row -- one whose account was deleted -- is the state in
    which a recycled identifier could hand one person another's runtime, so it
    is a hard failure rather than a stale-data curiosity. Same rule, and same
    reasoning, as `sessions._verify_session_locked`'s account checks.
    """
    account = conn.execute(
        "SELECT disabled_at FROM platform_accounts WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if account is None:
        raise DeviceAuthenticationError("device does not name a live account")
    if account["disabled_at"] is not None:
        raise DeviceAuthenticationError("account disabled")


def verify_device_credential(
    secret: str | None,
    *,
    expected_user_id: str | None = None,
    db_path: str | None = None,
    now: int | None = None,
    record_contact: bool = True,
) -> VerifiedDevice:
    """Establish which device is speaking, or refuse.

    The only construction site for a `VerifiedDevice`. Every failure raises
    `DeviceAuthenticationError`; nothing returns a partially trusted device,
    because no such value exists.

    `expected_user_id`, when supplied, is the tenant the *caller* already
    established by other means -- the authenticated principal, or this
    process's runtime binding. A credential belonging to another tenant is
    refused here rather than being handed back for the caller to check, so
    "one tenant using another tenant's device credential" fails at the
    verification boundary and not at whichever call site remembered to
    compare.

    `record_contact` updates `last_seen_at`, and it happens **only on this
    path** -- after every check has passed. A presentation that failed
    verification leaves last-seen untouched, so the column means "this device
    was genuinely here", not "someone guessed at this device's id".
    """
    ts = _now(now)
    with platform_connection(db_path) as conn:
        credential = _lookup_credential(conn, secret or "")
        if credential["purpose"] != CredentialPurpose.DEVICE.value:
            # An enrolment secret is not an authentication credential. Refused
            # explicitly rather than falling through to the status checks,
            # which an approved device would otherwise pass.
            raise DeviceAuthenticationError("credential is not a device credential")
        if credential["revoked_at"] is not None:
            raise DeviceAuthenticationError("device credential revoked")
        if credential["expires_at"] is not None and ts >= credential["expires_at"]:
            raise DeviceAuthenticationError("device credential expired")

        row = _load_device_row(conn, credential["device_id"], error=DeviceAuthenticationError)
        status = _status(row)
        if status not in _AUTHENTICABLE:
            raise DeviceAuthenticationError(f"device is {status.value}")

        # The tenant binding recorded on the credential and the one recorded
        # on the device must agree. They are written together and never
        # separately, so a disagreement means the rows were tampered with --
        # which is a refusal, not something to reconcile.
        if not hmac.compare_digest(str(credential["user_id"]), str(row["user_id"])):
            raise DeviceAuthenticationError("device credential tenant binding is inconsistent")
        if expected_user_id is not None and not hmac.compare_digest(
            str(row["user_id"]),
            str(expected_user_id),
        ):
            raise DeviceAuthenticationError("device credential belongs to another tenant")

        _require_live_account(conn, row["user_id"])

        if record_contact:
            conn.execute(
                "UPDATE platform_devices SET last_seen_at = ? WHERE device_id = ?",
                (ts, row["device_id"]),
            )

        manifest = DeviceCapabilityManifest.from_row(
            device_id=row["device_id"],
            platform=row["platform"],
            companion_version=row["companion_version"],
            capabilities_json=row["capabilities"],
        )
        return VerifiedDevice(
            device_id=row["device_id"],
            user_id=row["user_id"],
            platform=row["platform"],
            companion_version=row["companion_version"] or "",
            manifest_version=int(row["manifest_version"] or 0),
            manifest=manifest,
            credential_id=credential["credential_id"],
            approved_capabilities=_approved_capabilities(row),
        )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def rotate_device_credential(
    device_id: str,
    *,
    actor: str,
    db_path: str | None = None,
    now: int | None = None,
) -> IssuedCredential:
    """Issue a new device credential and invalidate every previous one.

    Rotation is not "add another credential": the revocation of the old and
    the insertion of the new happen in one transaction, so there is no window
    in which both work and no state in which the operator believes they have
    rotated while the old secret still authenticates.
    """
    if not (actor or "").strip():
        raise DeviceError("an actor is required -- rotation is never anonymous")
    ts = _now(now)
    secret = _mint_secret()
    credential_id = str(uuid.uuid4())

    with platform_connection(db_path) as conn:
        row = _load_device_row(conn, device_id)
        status = DeviceStatus(row["status"])
        if status in (DeviceStatus.REVOKED, DeviceStatus.PENDING):
            raise DeviceError(
                f"device {device_id} is {status.value}; there is nothing to rotate "
                "(a revoked device is terminal, a pending device has no credential)",
            )
        conn.execute(
            "UPDATE platform_device_credentials SET revoked_at = ?, rotated_at = ? "
            "WHERE device_id = ? AND revoked_at IS NULL",
            (ts, ts, device_id),
        )
        conn.execute(
            "INSERT INTO platform_device_credentials"
            "(credential_id, device_id, user_id, secret_hash, purpose, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                credential_id,
                device_id,
                row["user_id"],
                _hash_secret(secret),
                CredentialPurpose.DEVICE.value,
                ts,
            ),
        )
        record_platform_audit(
            conn,
            "device.credential_rotated",
            user_id=row["user_id"],
            detail=f"device={device_id} credential={credential_id} actor={actor}",
            ts=ts,
        )
    return IssuedCredential(
        credential_id=credential_id,
        device_id=device_id,
        user_id=row["user_id"],
        purpose=CredentialPurpose.DEVICE,
        secret=secret,
        expires_at=None,
    )


def set_device_disabled(
    device_id: str,
    disabled: bool,
    *,
    actor: str,
    db_path: str | None = None,
    now: int | None = None,
) -> None:
    """Disable or re-enable a device.

    Reversible and credential-preserving, which is what makes it the right
    response to "I have left the laptop at the office" and the wrong response
    to "I have lost the laptop". Re-enabling an `ACTIVE`-once device returns
    it to `ACTIVE`; a revoked device is never re-enabled here.
    """
    ts = _now(now)
    with platform_connection(db_path) as conn:
        row = _load_device_row(conn, device_id)
        status = DeviceStatus(row["status"])
        if status is DeviceStatus.REVOKED:
            raise DeviceError(
                f"device {device_id} is revoked; revocation is terminal and a new "
                "enrolment is required",
            )
        if disabled:
            conn.execute(
                "UPDATE platform_devices SET status = ?, disabled_at = ? WHERE device_id = ?",
                (DeviceStatus.DISABLED.value, ts, device_id),
            )
        else:
            if status is not DeviceStatus.DISABLED:
                raise DeviceError(f"device {device_id} is {status.value}, not disabled")
            # Restore to where the device actually got to, not to a status it
            # never reached. Re-enabling a never-approved device as APPROVED
            # bricked it: `approve_enrolment` would then refuse it, so it could
            # never be issued the enrolment secret it had never had.
            if row["enrolled_at"] is not None:
                restored = DeviceStatus.ACTIVE
            elif row["approved_at"] is not None:
                restored = DeviceStatus.APPROVED
            else:
                restored = DeviceStatus.PENDING
            conn.execute(
                "UPDATE platform_devices SET status = ?, disabled_at = NULL WHERE device_id = ?",
                (restored.value, device_id),
            )
        record_platform_audit(
            conn,
            "device.disabled" if disabled else "device.enabled",
            user_id=row["user_id"],
            detail=f"device={device_id} actor={actor}",
            ts=ts,
        )


def revoke_device(
    device_id: str,
    *,
    actor: str,
    reason: str | None = None,
    db_path: str | None = None,
    now: int | None = None,
) -> None:
    """Revoke a device and every credential it holds. Terminal and immediate.

    One transaction, so there is no interval in which the device is revoked
    but a credential still verifies. The response to a lost or compromised
    machine; `docs/E_DEVICE_TRUST_AND_TRUSTED_GROUPS.md` carries the full
    procedure, including what revocation does *not* undo.
    """
    ts = _now(now)
    with platform_connection(db_path) as conn:
        row = _load_device_row(conn, device_id)
        conn.execute(
            "UPDATE platform_device_credentials SET revoked_at = ? "
            "WHERE device_id = ? AND revoked_at IS NULL",
            (ts, device_id),
        )
        conn.execute(
            "UPDATE platform_devices SET status = ?, revoked_at = ? WHERE device_id = ?",
            (DeviceStatus.REVOKED.value, ts, device_id),
        )
        record_platform_audit(
            conn,
            "device.revoked",
            user_id=row["user_id"],
            detail=f"device={device_id} actor={actor}" + (f" reason={reason}" if reason else ""),
            ts=ts,
        )


def redeclare_manifest(
    device_id: str,
    declaration: dict,
    *,
    actor: str,
    db_path: str | None = None,
    now: int | None = None,
) -> DeviceCapabilityManifest:
    """Record an updated manifest for an already-enrolled device.

    Called after a companion upgrade. `manifest_version` increments on every
    accepted declaration, so "which manifest was live when that happened?" is
    answerable from the audit trail rather than inferred from a timestamp.

    `actor` is required, and required for the same reason every other
    lifecycle function here requires one: this rewrites what a device is
    authorised for, so it is never anonymous and never unattributed. An
    earlier cut took no actor at all and asserted in its docstring that it was
    "reachable only with a verified device credential" -- an assertion nothing
    in the signature or the body enforced, which is exactly the shape of claim
    a future route wires straight to a request body.

    Callers must have established the device's identity first: either the
    operator, or a caller holding a `VerifiedDevice` from
    `verify_device_credential`. `device_id` comes from that verified state,
    never from the declaration -- see `DeviceCapabilityManifest.from_declaration`.
    """
    if not (actor or "").strip():
        raise DeviceError("an actor is required -- a manifest change is never anonymous")
    ts = _now(now)
    with platform_connection(db_path) as conn:
        row = _load_device_row(conn, device_id)
        status = DeviceStatus(row["status"])
        if status is not DeviceStatus.ACTIVE:
            raise DeviceError(f"device {device_id} is {status.value}, not active")
        manifest = DeviceCapabilityManifest.from_declaration(declaration, device_id=device_id)
        manifest_version = int(row["manifest_version"] or 0) + 1
        conn.execute(
            "UPDATE platform_devices SET platform = ?, companion_version = ?, "
            "capabilities = ?, manifest_version = ? WHERE device_id = ?",
            (
                manifest.platform,
                manifest.companion_version,
                manifest.capabilities_json(),
                manifest_version,
                device_id,
            ),
        )
        record_platform_audit(
            conn,
            "device.manifest_updated",
            user_id=row["user_id"],
            detail=(
                f"device={device_id} manifest_version={manifest_version} "
                f"declared={len(manifest.capabilities)} supported={len(manifest.known)} "
                f"actor={actor}"
            ),
            ts=ts,
        )
    return manifest


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def get_device(
    device_id: str,
    *,
    user_id: str | None = None,
    db_path: str | None = None,
) -> dict | None:
    """One device's registry row, or None.

    Never returns credential material of any kind -- the credentials table is
    not joined here, and `secret_hash` has no read path in this module's
    public surface at all.

    `user_id`, when supplied, scopes the read to one tenant: a caller acting
    for a user gets None for another user's device rather than a row it must
    remember not to use.
    """
    with platform_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM platform_devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    if row is None:
        return None
    if user_id is not None and row["user_id"] != user_id:
        return None
    return _public_device(row)


def list_devices(user_id: str, *, db_path: str | None = None) -> list[dict]:
    """Every device belonging to one account. Tenant-scoped by predicate.

    There is deliberately no "list all devices" helper: the shape of the
    function is the isolation, so a caller cannot reach another tenant's
    devices by omitting an argument.
    """
    with platform_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM platform_devices WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()
    return [_public_device(row) for row in rows]


def _public_device(row: sqlite3.Row) -> dict:
    manifest = DeviceCapabilityManifest.from_row(
        device_id=row["device_id"],
        platform=row["platform"],
        companion_version=row["companion_version"],
        capabilities_json=row["capabilities"],
    )
    ceiling = _approved_capabilities(row)
    authorised = [
        c.as_dict()
        for c in manifest.known
        if ceiling is None or any(p.kind == c.kind and p.version == c.version for p in ceiling)
    ]
    return {
        "device_id": row["device_id"],
        "user_id": row["user_id"],
        "display_name": row["display_name"],
        "platform": row["platform"],
        "companion_version": row["companion_version"],
        "status": row["status"],
        "manifest_version": int(row["manifest_version"] or 0),
        "created_at": row["created_at"],
        "approved_at": row["approved_at"],
        "enrolled_at": row["enrolled_at"],
        "disabled_at": row["disabled_at"],
        "revoked_at": row["revoked_at"],
        "last_seen_at": row["last_seen_at"],
        "capabilities": [c.as_dict() for c in manifest.capabilities],
        "supported_capabilities": [c.as_dict() for c in manifest.known],
        "unsupported_capabilities": [c.as_dict() for c in manifest.unknown],
        "approved_capabilities": (None if ceiling is None else [c.as_dict() for c in ceiling]),
        "authorised_capabilities": authorised,
    }


def device_audit(
    *,
    user_id: str | None = None,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    """Enrolment, rotation, disable and revocation events, newest first.

    Reads the existing `platform_audit` table rather than adding a second
    audit log -- one place to look is worth more than a purpose-built one.
    """
    clauses = ["event LIKE 'device.%'"]
    params: list[object] = []
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    params.append(max(1, min(int(limit), 1000)))
    with platform_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT ts, event, user_id, detail FROM platform_audit "  # noqa: S608
            f"WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def describe_manifest(device_id: str, *, db_path: str | None = None) -> dict | None:
    """The registered manifest in its frozen logical shape, or None."""
    with platform_connection(db_path) as conn:
        row = conn.execute(
            "SELECT device_id, platform, companion_version, capabilities "
            "FROM platform_devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    if row is None:
        return None
    manifest = DeviceCapabilityManifest.from_row(
        device_id=row["device_id"],
        platform=row["platform"],
        companion_version=row["companion_version"],
        capabilities_json=row["capabilities"],
    )
    return manifest.as_dict()


def manifest_json(device_id: str, *, db_path: str | None = None) -> str | None:
    """`describe_manifest` as canonical JSON, for operator output."""
    described = describe_manifest(device_id, db_path=db_path)
    return None if described is None else json.dumps(described, indent=2)


__all__ = [
    "DEFAULT_ENROLMENT_TTL_S",
    "CredentialPurpose",
    "DeviceAuthenticationError",
    "DeviceCapabilityError",
    "DeviceError",
    "DeviceStatus",
    "IssuedCredential",
    "VerifiedDevice",
    "approve_enrolment",
    "complete_enrolment",
    "create_pending_enrolment",
    "describe_manifest",
    "device_audit",
    "get_device",
    "list_devices",
    "manifest_json",
    "redeclare_manifest",
    "revoke_device",
    "rotate_device_credential",
    "set_device_disabled",
    "verify_device_credential",
]
