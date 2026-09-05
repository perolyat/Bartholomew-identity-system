"""The action channel's production resolver: E's device credentials, B's channel.

Package B ships a fail-closed default (`None`, nothing dispatches) and a
double-gated test resolver, and its closeout asks F to install "the control
plane's" resolver here. Session E is that control plane, and it already
verifies device credentials for the *observation* channel in
`platform.device_inbound`.

This resolver is a deliberate near-duplicate of that one rather than a shared
object, and the duplication is the security property. B's own comment on
`device_action_auth._resolver` says it: the action resolver is a different
module global from the observation resolver's, so that opening observation
capture does not open actuation. Installing one object into both would undo
exactly that, and no amount of care at the call sites would put it back.
Two installs, two decisions, two things an operator has to mean.

What is verified, and what is refused
-------------------------------------
Everything is `verify_device_credential`'s: an absent header, an unknown,
rotated, revoked or expired credential, a disabled device, a disabled account,
and a credential enrolled under a different account than this process is bound
to all return `None`, which B's route turns into a 401 that dispatches
nothing. A credential is never read from the body -- the body is accepted
because the protocol offers it and is explicitly discarded, because a
caller-supplied body must never establish identity.

`tenant_id` is deliberately absent from what this returns. B's contract is
that the channel establishes *which device*, and which tenant's actions that
device may lease is decided from the enrolment record on the server side --
never from anything the device says. E's `VerifiedDevice.user_id` is the
enrolment-time owner and is kept on the returned object for a capability check
to reach, exactly as `device_inbound.VerifiedDeviceSource` keeps it, and it is
not part of the protocol surface.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from bartholomew.platform.device_inbound import DEVICE_CREDENTIAL_HEADER, _header
from bartholomew.platform.devices import DeviceAuthenticationError, verify_device_credential

logger = logging.getLogger(__name__)

#: Set this to enable the production action-channel resolver. Separate from
#: `BARTH_DEVICE_INBOUND_AUTH` on purpose: enabling observation must not
#: enable actuation, and one variable for both would make it do so.
DEVICE_ACTION_AUTH_ENV = "BARTH_DEVICE_ACTION_AUTH"

#: What the audit records as having verified an action-channel call.
DEVICE_ACTION_VERIFIED_BY = "device_credential"


class VerifiedActionDevice:
    """B's `VerifiedDevice` duck-type, backed by E's registry verification."""

    __slots__ = ("device",)

    def __init__(self, device: Any) -> None:
        self.device = device

    @property
    def device_id(self) -> str:
        return self.device.device_id

    @property
    def verified_by(self) -> str:
        return DEVICE_ACTION_VERIFIED_BY


class DeviceActionCredentialResolver:
    """Verifies an action-channel call against E's device credentials."""

    def __init__(self, *, db_path: str | None = None) -> None:
        self._db_path = db_path

    async def resolve(self, request: Any, body: bytes) -> VerifiedActionDevice | None:
        del body  # identity never comes from a caller-supplied body

        secret = _header(request, DEVICE_CREDENTIAL_HEADER)
        if not secret:
            return None

        from bartholomew.platform.runtime_registry import bound_runtime_user_id

        try:
            device = verify_device_credential(
                secret,
                expected_user_id=bound_runtime_user_id(),
                db_path=self._db_path,
            )
        except DeviceAuthenticationError:
            logger.info("Action-channel device credential did not verify; refusing")
            return None
        except Exception:
            logger.exception("Device credential verification failed; refusing the request")
            return None
        return VerifiedActionDevice(device)


def install_action_resolver(*, db_path: str | None = None) -> DeviceActionCredentialResolver:
    """Install the production resolver on B's action channel."""
    from bartholomew_api_bridge_v0_1.services.api import device_action_auth

    resolver = DeviceActionCredentialResolver(db_path=db_path)
    device_action_auth.install_resolver(resolver)
    logger.info("Device action channel opened with the registry credential resolver")
    return resolver


def maybe_install_action_resolver_from_env() -> bool:
    """Install it only when this deployment has explicitly asked for it.

    Off by default. A closed action channel is the correct state for a
    deployment that has not decided to actuate anything, and this function
    installing nothing is that decision being respected rather than a gap.
    """
    enabled = (os.getenv(DEVICE_ACTION_AUTH_ENV) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not enabled:
        return False

    from bartholomew_api_bridge_v0_1.services.api import device_action_auth

    if device_action_auth.get_resolver() is not None:
        # Refuse rather than replace: a deployment that configured both the
        # test resolver and this one has said two contradictory things about
        # how it authenticates actuation, and startup is where that stops.
        raise RuntimeError(
            "an action-channel resolver is already installed; refusing to replace "
            f"it from {DEVICE_ACTION_AUTH_ENV}. Configure exactly one.",
        )
    install_action_resolver()
    return True


__all__ = [
    "DEVICE_ACTION_AUTH_ENV",
    "DEVICE_ACTION_VERIFIED_BY",
    "DeviceActionCredentialResolver",
    "VerifiedActionDevice",
    "install_action_resolver",
    "maybe_install_action_resolver_from_env",
]
