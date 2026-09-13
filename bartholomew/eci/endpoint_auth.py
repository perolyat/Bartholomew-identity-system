"""Which endpoint is speaking, established the platform's way and nowhere else.

**This module authenticates nothing itself, and must never grow code that
does.** Credentials, enrolment, rotation and revocation belong to
`bartholomew/platform/devices.py`; the account session and the `Principal`
belong to `bartholomew/platform/http_identity.py`. This is the narrow adapter
between an incoming request and `verify_device_credential()`, plus the
fail-closed default that holds until a deployment opens the channel.

Two independent questions, both of which must be answered
---------------------------------------------------------
* **Whose Bartholomew is this?** The control plane's verified `Principal`,
  plus this process's runtime binding. Enforced ahead of this module by the
  platform's own chokepoint and by `route_policy`'s default-deny table.
* **Which endpoint is on the other end?** A registry-issued device credential,
  verified here. Provenance and capability standing -- never authority over
  whose runtime is written.

Neither substitutes for the other. A verified endpoint says *this really is
the machine the platform enrolled*; it says nothing whatever about whose
Bartholomew a message belongs in. `bartholomew/platform/device_inbound.py`
sets out that reasoning at length and this module holds to it exactly: there
is no `runtime_id` or `user_id` read from any request, header or body
anywhere below, and `EndpointIdentity.user_id` is the enrolment's tenant as
`verify_device_credential()` returned it.

A third channel, and deliberately a separate one
-------------------------------------------------
`inbound_auth` opens capture; `device_action_auth` opens the Windows action
channel; this opens the External Capability Interface. Three independent
module globals, and **installing one does not open another**. A deployment
that has opened inbound capture has not thereby opened this boundary, and a
deployment that has opened this boundary has not thereby opened actuation --
which remains behind its own gate, its own resolver, its own eleven-point
admission and its own human-bound approvals.
`tests/test_fnd04_eci_boundary.py` asserts the separation rather than
assuming it.

Off by default
--------------
`maybe_install_from_env()` installs nothing unless `BARTH_ECI_ENDPOINT_AUTH`
is set. Opening a boundary to external endpoints is a deployment decision, not
an import side effect, and a channel that authenticates nobody must carry
nothing.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .contract import EndpointIdentity

logger = logging.getLogger(__name__)

#: The header an endpoint presents its device credential in. The same header
#: the companion already uses, because it is the same credential answering the
#: same question -- *which machine is this* -- and minting a second credential
#: type for a second channel would mean two things to enrol, rotate and revoke
#: where the platform deliberately has one.
ENDPOINT_CREDENTIAL_HEADER = "x-bartholomew-device-credential"

#: Set to a truthy value to open this channel at startup.
ENDPOINT_AUTH_ENV = "BARTH_ECI_ENDPOINT_AUTH"

#: `verified_by` stamped on every exchange this resolver admits, recorded
#: verbatim on the durable row. Distinct from the inbound seam's
#: `device-credential` so that a row always says truthfully which boundary
#: admitted it, forever.
ENDPOINT_VERIFIED_BY = "eci-device-credential"

_RESOLVER: dict[str, Any] = {"resolver": None}


class EndpointResolutionError(Exception):
    """The endpoint could not be established. Always a refusal, never anonymous.

    One exception for every cause -- absent header, unknown, rotated, revoked
    or expired credential, disabled device, disabled account, a credential
    belonging to another tenant -- so that no branch on *why* can leak which,
    and a prober learns nothing from the difference.
    """


class DeviceEndpointResolver:
    """Resolves a request to the enrolled device that presented a credential.

    Returns a `(EndpointIdentity, VerifiedDevice)` pair, or raises. The
    `VerifiedDevice` is carried alongside rather than folded in because
    capability standing is asked of it directly -- the platform owns that
    answer and this boundary does not copy it.
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        self._db_path = db_path

    def resolve(self, request: Any) -> tuple[EndpointIdentity, Any]:
        from bartholomew.platform.devices import (
            DeviceAuthenticationError,
            verify_device_credential,
        )
        from bartholomew.platform.runtime_registry import bound_runtime_user_id

        secret = _header(request, ENDPOINT_CREDENTIAL_HEADER)
        if not secret:
            raise EndpointResolutionError(
                "No endpoint credential was presented; nothing was recorded.",
            )

        try:
            device = verify_device_credential(
                secret,
                # This process's runtime binding, so a credential enrolled
                # under one account cannot authenticate against a process
                # serving another. Checked at the verification boundary rather
                # than at whichever call site remembered to compare.
                expected_user_id=bound_runtime_user_id(),
                db_path=self._db_path,
            )
        except DeviceAuthenticationError:
            logger.info("An ECI endpoint credential did not verify; refusing the request")
            raise EndpointResolutionError(
                "The endpoint credential did not verify; nothing was recorded.",
            ) from None
        except Exception:
            # The registry could not answer. An unverified caller either way.
            logger.exception("ECI endpoint verification failed; refusing the request")
            raise EndpointResolutionError(
                "The endpoint could not be verified; nothing was recorded.",
            ) from None

        identity = EndpointIdentity(
            endpoint_id=device.device_id,
            user_id=device.user_id,
            verified_by=ENDPOINT_VERIFIED_BY,
            endpoint_kind=getattr(device, "platform", "unspecified") or "unspecified",
        )
        return identity, device


def install_resolver(resolver: Any) -> None:
    """Open the boundary to enrolled endpoints."""
    _RESOLVER["resolver"] = resolver
    logger.info(
        "The External Capability Interface is open to enrolled endpoints: exchanges "
        "are admitted only on a registry-verified device credential and are recorded "
        "as verified_by=%r.",
        ENDPOINT_VERIFIED_BY,
    )


def clear_resolver() -> None:
    """Close it. The fail-closed state, and the default."""
    _RESOLVER["resolver"] = None


def get_resolver() -> Any:
    return _RESOLVER["resolver"]


def maybe_install_from_env(*, db_path: str | None = None) -> bool:
    """Open the channel if `BARTH_ECI_ENDPOINT_AUTH` is set. True if it did."""
    enabled = (os.getenv(ENDPOINT_AUTH_ENV) or "").strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        return False
    install_resolver(DeviceEndpointResolver(db_path=db_path))
    return True


def _header(request: Any, name: str) -> str:
    headers = getattr(request, "headers", None)
    if headers is None:
        return ""
    try:
        return (headers.get(name) or "").strip()
    except Exception:  # noqa: BLE001 - an unreadable header is an absent one
        return ""


__all__ = [
    "ENDPOINT_AUTH_ENV",
    "ENDPOINT_CREDENTIAL_HEADER",
    "ENDPOINT_VERIFIED_BY",
    "DeviceEndpointResolver",
    "EndpointResolutionError",
    "clear_resolver",
    "get_resolver",
    "install_resolver",
    "maybe_install_from_env",
]
