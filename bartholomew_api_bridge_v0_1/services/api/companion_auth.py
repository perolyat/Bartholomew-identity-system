"""Authenticating the Windows companion, as a device, on the control surface.

The companion proves **which machine is calling**. That is all this module
does, and saying so precisely matters more than the code: authenticating a
device establishes identity and nothing else. It does not grant permission to
observe, to act, to accept learning, or to speak for the person who owns the
machine. Every one of those is decided after this, by the authority that owns
it -- the multimodal consent gate, the action approval, the Parking Brake.

One credential authority, reused
--------------------------------
Verification is Session E's `verify_device_credential`, unchanged: the same
digest-compared, high-entropy device secret that the observation channel
already uses. No second registry, no second identity authority, no new secret
shape, and no plaintext credential stored server-side -- E persists a digest
and this module never sees anything but the presented secret.

Where the tenant comes from
---------------------------
Never from the request. Two server-side sources, in order:

1. this process's runtime binding, when it has one -- passed to
   `verify_device_credential` as `expected_user_id`, so a credential enrolled
   under one account cannot authenticate against a process serving another,
   and the check happens at the verification boundary rather than at whichever
   call site remembered it;
2. otherwise the owning account **recorded on the device's own enrolment row**
   -- server-side data an operator wrote at enrolment, not a claim travelling
   with the call. This is the single-runtime local deployment, where there is
   exactly one account and the process is unbound.

A body, header or query parameter naming a tenant is ignored in both cases.

Why this is not a human principal
---------------------------------
Package C refuses to build a `SessionRequest` whose `principal_id` begins
`companion:` (or `model:`, `event:`, `inbound:`, ...), because a machine is
not a person and capture must not be reachable from something that is not one.
This module honours that rather than working around it: it returns the device
*and* the owning human account separately, and the caller uses the account --
a real row in `platform_accounts` -- as the principal. The device id goes
where device ids go.

That distinction is the whole reason a companion cannot start observing on its
own: being the machine is not being the person, and the person is still asked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from bartholomew.platform.device_inbound import DEVICE_CREDENTIAL_HEADER, _header
from bartholomew.platform.devices import (
    DeviceAuthenticationError,
    DeviceCapabilityError,
    verify_device_credential,
)

logger = logging.getLogger(__name__)

#: What the audit records as having established a control-surface caller.
COMPANION_VERIFIED_BY = "device_credential"


@dataclass(frozen=True)
class AuthenticatedCompanion:
    """One verified device, and the account whose enrolment record names it.

    `owner_user_id` is the tenant. It is read from the verified enrolment row,
    never from the caller, and it is a human account id -- which is what makes
    it usable as a `principal_id` where the device id is not.
    """

    device_id: str
    owner_user_id: str
    platform: str
    companion_version: str
    #: Session E's `VerifiedDevice`, kept so a capability check has it to hand.
    device: Any

    @property
    def verified_by(self) -> str:
        return COMPANION_VERIFIED_BY

    def require_capability(self, kind: str, version: int) -> None:
        """Raise `HTTPException(403)` unless this device may be asked for `kind`.

        Delegates to E's `VerifiedDevice.require_capability`, which refuses
        three different mistakes with one branch: a capability the device never
        declared, one at a version this deployment does not understand, and one
        outside the operator's approval ceiling. Being enrolled is not being
        capable, and this is where the difference is enforced.
        """
        try:
            self.device.require_capability(kind, version)
        except DeviceCapabilityError as e:
            raise HTTPException(403, str(e)) from None

    def describe(self) -> dict[str, Any]:
        """Non-sensitive summary for a status surface. Never credential material."""
        return {
            "device_id": self.device_id,
            "platform": self.platform,
            "companion_version": self.companion_version,
            "verified_by": self.verified_by,
        }


def require_companion(request: Any) -> AuthenticatedCompanion:
    """The verified companion behind this request, or 401.

    Fails closed in every direction: absent header, unknown credential,
    rotated or revoked credential, disabled device, disabled account, a
    credential belonging to another account than this process serves, and a
    registry that cannot answer. Every one of them is the same 401 with the
    same wording, so a prober cannot tell which of them it hit.
    """
    secret = _header(request, DEVICE_CREDENTIAL_HEADER)
    if not secret:
        raise HTTPException(
            401,
            "This operation requires an enrolled device credential; nothing was done.",
        )

    from bartholomew.platform.runtime_registry import bound_runtime_user_id

    try:
        device = verify_device_credential(
            secret,
            expected_user_id=bound_runtime_user_id(),
        )
    except DeviceAuthenticationError:
        logger.info("Companion device credential did not verify; refusing the request")
        raise HTTPException(401, "Device credential could not be verified.") from None
    except Exception:
        # The registry could not answer. An unverified caller either way.
        logger.exception("Device credential verification failed; refusing the request")
        raise HTTPException(401, "Device credential could not be verified.") from None

    owner = str(getattr(device, "user_id", "") or "").strip()
    if not owner:
        # A verified device with no owning account is not a tenant we can
        # attribute anything to. Refused rather than defaulted.
        raise HTTPException(401, "Device credential could not be verified.")

    return AuthenticatedCompanion(
        device_id=device.device_id,
        owner_user_id=owner,
        platform=str(getattr(device, "platform", "") or ""),
        companion_version=str(getattr(device, "companion_version", "") or ""),
        device=device,
    )


__all__ = [
    "COMPANION_VERIFIED_BY",
    "AuthenticatedCompanion",
    "require_companion",
]
