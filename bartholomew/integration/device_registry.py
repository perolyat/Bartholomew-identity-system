"""One device truth: E's registry answers B's and C's device questions.

Before this module there were three device stories in the tree. Package E
owns a real registry -- enrolment ceremony, credentials, manifests, operator
approval ceilings, disable and revoke. Package B shipped
`StaticDeviceRegistry`, an operator-provisioned JSON file, explicitly labelled
"Session E replaces this". Package C shipped `StaticCapabilityResolver`, an
in-memory dict, explicitly labelled the same. Both said, in their closeouts,
that F was to replace them with E.

This module does that, and it is deliberately an adapter rather than a merge:
E's schema is untouched, B's and C's call sites are untouched, and neither B
nor C imports anything from E. Each gets the interface it declared, answered
from the one store that actually knows.

What comes from where
---------------------
E is authoritative for everything about *the device*: whether the tenant owns
it, whether it is enrolled right now, and which `(kind, version)` capabilities
it may be asked to perform (manifest **and** the operator's approval ceiling,
both -- `VerifiedDevice.authorizes` semantics, reproduced here from the same
stored row).

B's operator enrolment file remains authoritative for the three *parameter
allowlists* (`applications`, `url_domains`, `filesystem_roots`) and for
`trusted_autonomy`, because E's manifest has no concept of any of them. That
is not a second device truth: the file cannot enrol a device, cannot make a
revoked device usable, and cannot grant a capability E has not approved. It
can only narrow what an already-enrolled device's parameters may name. A
device with no entry in it gets empty allowlists, which refuse everything
(see `allowlists`' module docstring), so the failure direction is closed.

Fail-closed, in the four ways B's contract names
-----------------------------------------------
* **Tenant-qualified.** `tenant_id` is passed to E's `get_device`, which
  returns None for another tenant's device. A device id known in one tenant is
  simply unknown in another.
* **Revoked is not absent.** A device row that exists for this tenant but is
  not `ACTIVE` returns `EnrolledDevice(enrolled=False)`, never None, so the
  refusal can say "that device is not enrolled here" rather than "unknown
  device". `PENDING`, `APPROVED`, `DISABLED` and `REVOKED` all land here --
  E's `_AUTHENTICABLE` is the single source of what "enrolled" means, and it
  is read, not restated.
* **A lookup that cannot answer raises.** Any store or parse failure becomes
  `DeviceRegistryError`, which `actuation.seam` treats as a denial. An
  unreachable registry refuses every device; it never reports "not enrolled",
  because that would be a truthful-sounding answer to a question nobody could
  answer.
* **No request-body authority.** Nothing here reads a request, a header or a
  device's own claim about who it is. `tenant_id` arrives from the caller's
  resolved runtime identity and is used as a query predicate only.
"""

from __future__ import annotations

import logging
from typing import Any

from bartholomew.actuation.allowlists import (
    AllowlistError,
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import UnsupportedCapabilityError, parse_kind
from bartholomew.actuation.devices import (
    SUPPORTED_PLATFORM,
    DeclaredCapability,
    DeviceRegistryError,
    EnrolledDevice,
    StaticDeviceRegistry,
)
from bartholomew.multimodal.devices import DeviceCapability

logger = logging.getLogger(__name__)

#: The one status E permits a device to act from. Imported rather than
#: restated so that widening it is a single edit in E, not two.
_ACTIVE = "active"

#: Verification level this adapter reports to Package C. C never raises
#: `verification` above "claimed" and says so; E's registry is the only thing
#: entitled to, and it does so here on its own authority: a device resolved
#: from an enrolment row with an approved manifest has been verified by an
#: enrolment ceremony, which is exactly what "registered" means.
VERIFICATION_REGISTERED = "registered"


def _allowlist_source(enrolment_path: str | None) -> StaticDeviceRegistry | None:
    """B's operator file, used *only* for parameter allowlists. May be absent."""
    if enrolment_path:
        return StaticDeviceRegistry(enrolment_path)
    return StaticDeviceRegistry()


class RegistryBackedDeviceRegistry:
    """Package B's `DeviceCapabilityRegistry`, answered from Package E.

    Satisfies B's Protocol structurally: one method, `lookup`. B imports
    nothing from here and E imports nothing from B.
    """

    LABEL = "platform-device-registry (Session E, enrolled devices)"

    def __init__(
        self,
        *,
        db_path: str | None = None,
        enrolment_path: str | None = None,
    ) -> None:
        self._db_path = db_path
        self._allowlists = _allowlist_source(enrolment_path)

    # -- the interface ---------------------------------------------------

    def lookup(self, *, tenant_id: str, device_id: str) -> EnrolledDevice | None:
        tenant = str(tenant_id or "").strip()
        device = str(device_id or "").strip()
        if not tenant or not device:
            # Not "unknown device" -- an unnamed tenant or device is a caller
            # bug, and answering None would let it read as a clean miss.
            raise DeviceRegistryError(
                "a device lookup must name both a tenant_id and a device_id",
            )

        from bartholomew.platform import devices as platform_devices

        try:
            row = platform_devices.get_device(device, user_id=tenant, db_path=self._db_path)
        except Exception as e:  # noqa: BLE001 - every failure is a denial
            raise DeviceRegistryError(
                f"the device registry could not answer for {device!r} "
                f"({type(e).__name__}); refusing the device until it can.",
            ) from e

        if row is None:
            # Unknown to this tenant. E's `get_device` applies the tenant
            # predicate itself, so another tenant's device arrives here as
            # None and is indistinguishable from one that does not exist --
            # which is the containment property, not a loss of detail.
            return None

        enrolled = str(row.get("status") or "") == _ACTIVE
        platform = str(row.get("platform") or "").strip().lower()

        # `authorised_capabilities` is E's intersection of the device's
        # manifest with the operator's approval ceiling, restricted to
        # capabilities this deployment understands. It is exactly
        # `VerifiedDevice.authorizes`, computed from the same row.
        capabilities: list[DeclaredCapability] = []
        for entry in row.get("authorised_capabilities") or ():
            kind_name = str((entry or {}).get("kind") or "")
            try:
                kind = parse_kind(kind_name)
            except UnsupportedCapabilityError:
                # A capability E understands but B does not actuate -- C's
                # three multimodal kinds live here. Not an error: it is simply
                # not an action kind, so it is not a declared action capability.
                continue
            try:
                version = int(entry.get("version"))
            except (TypeError, ValueError):
                continue
            capabilities.append(DeclaredCapability(kind=kind, version=version))

        overlay = self._overlay(tenant, device)

        try:
            return EnrolledDevice(
                device_id=device,
                tenant_id=tenant,
                platform=platform or SUPPORTED_PLATFORM,
                enrolled=enrolled,
                capabilities=tuple(capabilities),
                applications=overlay.applications,
                url_domains=overlay.url_domains,
                filesystem_roots=overlay.filesystem_roots,
                # Trusted autonomy can only ever *narrow* to capabilities the
                # device actually holds through E. A file naming a capability
                # E has not approved would make `EnrolledDevice.__post_init__`
                # refuse the whole device, so it is intersected here instead:
                # the operator's file is not allowed to deny a device its
                # enrolment by being out of date.
                trusted_autonomy=frozenset(
                    k for k in overlay.trusted_autonomy if any(c.kind is k for c in capabilities)
                ),
            )
        except DeviceRegistryError:
            raise
        except (AllowlistError, UnsupportedCapabilityError) as e:
            raise DeviceRegistryError(
                f"the enrolment overlay for {device!r} is invalid: {e}",
            ) from e

    # -- the parameter-allowlist overlay ---------------------------------

    def _overlay(self, tenant: str, device: str) -> EnrolledDevice:
        """B's operator file entry for this device, or an empty (refusing) one."""
        empty = EnrolledDevice(
            device_id=device,
            tenant_id=tenant,
            platform=SUPPORTED_PLATFORM,
            enrolled=False,
            applications=ApplicationAllowlist.from_pairs({}),
            url_domains=UrlDomainAllowlist.from_iterable(()),
            filesystem_roots=FilesystemRootAllowlist.from_iterable(()),
        )
        if self._allowlists is None:
            return empty
        try:
            found = self._allowlists.lookup(tenant_id=tenant, device_id=device)
        except DeviceRegistryError:
            # A configured-but-unreadable allowlist file is "we cannot tell
            # what this device's parameters may name", and the closed answer
            # is to name nothing -- not to refuse the device outright, which
            # would let a typo in an optional file revoke a real enrolment.
            logger.warning(
                "the actuation allowlist file could not be read; device %r "
                "gets empty allowlists, which refuse every parameter",
                device,
            )
            return empty
        return found or empty

    def describe(self) -> dict[str, Any]:
        """What the health surface says about which registry is running."""
        overlay: dict[str, Any] = {"configured": False}
        if self._allowlists is not None:
            described = self._allowlists.describe()
            overlay = {
                "configured": bool(described.get("enrolment_file")),
                "file": described.get("enrolment_file"),
                "readable": described.get("readable"),
                "error": described.get("error"),
            }
        return {
            "registry": self.LABEL,
            "interim": False,
            "source": "platform_devices (Session E)",
            "allowlist_overlay": overlay,
        }


class RegistryBackedCapabilityResolver:
    """Package C's `DeviceCapabilityResolver`, answered from Package E.

    **Tenant-bound at construction.** C's declared Protocol is
    `resolve(device_id, kind, version)` with no tenant argument, because
    Package C runs inside a runtime that already serves exactly one person.
    Rather than widen C's frozen interface, the tenant is fixed when the
    resolver is installed, so there is no call path by which a device id can
    be resolved against a tenant the caller did not already have.

    Unknown is unsupported and never approximated, per C's invariant: an
    unenrolled device, a revoked one, a capability outside the operator's
    ceiling, and a resolver that cannot read the store all produce
    `supported=False` with a reason that says which.
    """

    def __init__(self, *, tenant_id: str, db_path: str | None = None) -> None:
        tenant = str(tenant_id or "").strip()
        if not tenant:
            raise ValueError("a capability resolver must be bound to a tenant")
        self._tenant = tenant
        self._db_path = db_path

    @property
    def tenant_id(self) -> str:
        return self._tenant

    def resolve(self, device_id: str, kind: str, version: int) -> DeviceCapability:
        device = str(device_id or "").strip()

        def refuse(reason: str, verification: str = "claimed") -> DeviceCapability:
            return DeviceCapability(
                device_id=device,
                kind=kind,
                version=version,
                supported=False,
                reason=reason,
                verification=verification,
            )

        if not device:
            return refuse("no device was named")

        from bartholomew.platform import devices as platform_devices

        try:
            row = platform_devices.get_device(
                device,
                user_id=self._tenant,
                db_path=self._db_path,
            )
        except Exception as e:  # noqa: BLE001 - unreadable device state denies
            return refuse(
                f"the device registry could not be read ({type(e).__name__}); "
                "refusing the capability rather than guessing at it",
            )

        if row is None:
            return refuse(f"device {device!r} is not enrolled for this account")

        status = str(row.get("status") or "unknown")
        if status != _ACTIVE:
            # Distinct from "not enrolled": the device is known, and saying
            # which lifecycle state it is in is the difference between "you
            # have no such device" and "that device is revoked".
            return refuse(
                f"device {device!r} is enrolled but not active (status: {status})",
                verification=VERIFICATION_REGISTERED,
            )

        for entry in row.get("authorised_capabilities") or ():
            if str((entry or {}).get("kind") or "") != kind:
                continue
            try:
                if int(entry.get("version")) != int(version):
                    continue
            except (TypeError, ValueError):
                continue
            return DeviceCapability(
                device_id=device,
                kind=kind,
                version=version,
                supported=True,
                verification=VERIFICATION_REGISTERED,
            )

        declared = sorted(
            f"{(e or {}).get('kind')}@v{(e or {}).get('version')}"
            for e in (row.get("authorised_capabilities") or ())
        )
        return refuse(
            f"device {device!r} is not authorised for {kind} v{version} "
            f"(authorised: {declared or 'nothing'})",
            verification=VERIFICATION_REGISTERED,
        )


__all__ = [
    "VERIFICATION_REGISTERED",
    "RegistryBackedCapabilityResolver",
    "RegistryBackedDeviceRegistry",
]
