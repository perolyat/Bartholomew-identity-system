"""
Device credentials plugged into the existing inbound-authentication seam.

`services/api/inbound_auth.py` was written with a hole in it, deliberately and
in as many words: *"The intended production call site: the authenticated
control plane installs its own verifier at startup, and inbound capture
opens."* Until now the only thing that fitted the hole was the double-gated
test resolver, which is *"a static token with no signature, no rotation and no
replay window"*. This module is the real one.

It is a **narrow adapter, not a second authority.** It contributes one thing
-- turning a presented device credential into a verified source id -- and
reuses everything else unchanged: the route's fail-closed default, its
`source_id` comparison, the Parking Brake, the Identity policy gate, capture,
idempotency and provenance. It adds no field to the inbound envelope, no
column to `inbound_events`, and no branch to the route.

The two `device_id`s, which are not the same thing
---------------------------------------------------
The companion's `payload["device_id"]` is, and remains, an operator-chosen
label and *claimed* provenance -- `bartholomew/companion/envelope.py` says so
and it is still true. This module introduces a second, different value: the
registry's server-generated `device_id`, which the platform issued and can
revoke. They live in different namespaces and this module never converts one
into the other. A companion that puts another machine's label in its payload
changes nothing about which device the platform verified: the verified
identity comes from the credential, and the route already refuses a submitted
`source_id` that does not match the verified one.

Off by default
--------------
`maybe_install_device_resolver_from_env()` installs nothing unless
`BARTH_DEVICE_INBOUND_AUTH` is set. Opening a capture surface is a deployment
decision, not an import side effect. It also refuses to run alongside the
test resolver: a deployment configured with both has said two contradictory
things about how it authenticates, and the safe reading of a contradiction is
to stop.

What this does not close
------------------------
A device credential is still a **bearer** credential. It is not a per-request
signature, and it gives no replay resistance beyond what TLS provides on the
wire -- exactly the limitation `sessions.py` records for session tokens. What
it does close is enrolment, per-device identity, capability declaration,
rotation and immediate revocation. Those are real, and they are not the same
as request signing; see `docs/E_DEVICE_TRUST_AND_TRUSTED_GROUPS.md`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .devices import DeviceAuthenticationError, VerifiedDevice, verify_device_credential
from .runtime_registry import bound_runtime_user_id

logger = logging.getLogger(__name__)

#: The header a companion presents its device credential in. A dedicated
#: header rather than `Authorization`, because the two answer different
#: questions on the same request: `Authorization` carries the *account's*
#: session, this carries the *machine's* identity, and collapsing them would
#: make one indistinguishable from the other at the boundary.
DEVICE_CREDENTIAL_HEADER = "x-bartholomew-device-credential"

#: Set to a truthy value to install this resolver at startup.
DEVICE_INBOUND_AUTH_ENV = "BARTH_DEVICE_INBOUND_AUTH"

#: `verified_by` stamped on every event this resolver admits, recorded
#: verbatim in the durable row so a row always says truthfully what verified
#: it -- and so events admitted on a test token stay distinguishable from
#: events admitted on a real device credential, forever.
DEVICE_VERIFIED_BY = "device-credential"

#: The `source_id` prefix a verified device is addressed by. Prefixed so an
#: inbound row's provenance is legible without a join, and so a device source
#: can never collide with a webhook source id somebody configures by hand.
DEVICE_SOURCE_PREFIX = "device:"


def source_id_for(device_id: str) -> str:
    """The verified inbound `source_id` for one enrolled device."""
    return f"{DEVICE_SOURCE_PREFIX}{device_id}"


class VerifiedDeviceSource:
    """A `VerifiedInboundSource` backed by a registry-verified device.

    Structurally conformant to the protocol in `inbound_auth` -- `source_id`
    and `verified_by`, and deliberately nothing more. It carries **no**
    `runtime_id`: which isolated runtime an event belongs to is decided by the
    authenticated principal and this process's own binding, never by the
    sender. A device belonging to Taylor is evidence that Taylor's machine
    sent something, not permission to write into anybody's Bartholomew.

    The `VerifiedDevice` itself is kept on `device` so a future capability
    check has it to hand, and is not part of the protocol surface.
    """

    __slots__ = ("device",)

    def __init__(self, device: VerifiedDevice) -> None:
        self.device = device

    @property
    def source_id(self) -> str:
        return source_id_for(self.device.device_id)

    @property
    def verified_by(self) -> str:
        return DEVICE_VERIFIED_BY


class DeviceCredentialResolver:
    """Resolves an inbound request to the device that presented a credential.

    Returns `None` for every failure -- absent header, unknown, rotated,
    revoked, expired credential, disabled device, disabled account, or a
    credential belonging to another tenant. The route turns `None` into a 401
    and captures nothing.

    `expected_user_id` is this process's runtime binding when it has one. It
    is passed to `verify_device_credential`, so a credential enrolled under
    one account cannot authenticate against a process serving another -- the
    check happens at the verification boundary rather than at whichever call
    site remembered to compare.
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        self._db_path = db_path

    async def resolve(self, request: Any, body: bytes) -> VerifiedDeviceSource | None:
        # `body` is accepted because the protocol offers it -- a signature
        # scheme over the raw bytes is expressible without changing the
        # interface. This resolver does not read it, and reading a
        # caller-supplied body to establish identity is exactly what it must
        # not do.
        del body

        secret = _header(request, DEVICE_CREDENTIAL_HEADER)
        if not secret:
            return None

        try:
            device = verify_device_credential(
                secret,
                expected_user_id=bound_runtime_user_id(),
                db_path=self._db_path,
            )
        except DeviceAuthenticationError:
            # Refused, not errored: one message, no branch on why, nothing
            # logged that would let a prober distinguish the cases.
            logger.info("Inbound device credential did not verify; refusing the request")
            return None
        except Exception:
            # The registry could not answer. An unverified caller either way.
            logger.exception("Device credential verification failed; refusing the request")
            return None
        return VerifiedDeviceSource(device)


def _header(request: Any, name: str) -> str:
    headers = getattr(request, "headers", None)
    if headers is None:
        return ""
    try:
        return (headers.get(name) or "").strip()
    except Exception:
        return ""


def install_device_resolver(*, db_path: str | None = None) -> DeviceCredentialResolver:
    """Install the device-credential resolver on the inbound boundary.

    Refuses when the test-only resolver is already installed. A deployment
    carrying both has stated two contradictory things about how it
    authenticates its inbound surface, and a contradiction about
    authentication is resolved by stopping, not by picking one.
    """
    # Imported lazily and deliberately: the control plane must stay importable
    # by the CLI and by operator tooling without pulling FastAPI in behind it.
    from bartholomew_api_bridge_v0_1.services.api import inbound_auth  # noqa: PLC0415

    if inbound_auth.resolver_is_test_only():
        raise RuntimeError(
            "Refusing to install the device-credential inbound resolver while the "
            "test-only resolver is active. A deployment must not be configured with "
            "both: unset BARTH_INBOUND_ALLOW_TEST_RESOLVER, or unset "
            f"{DEVICE_INBOUND_AUTH_ENV}.",
        )
    resolver = DeviceCredentialResolver(db_path=db_path)
    inbound_auth.install_resolver(resolver)
    logger.info(
        "Inbound capture is open to enrolled devices: events are admitted only on a "
        "registry-verified device credential and are recorded as verified_by=%r.",
        DEVICE_VERIFIED_BY,
    )
    return resolver


def maybe_install_device_resolver_from_env() -> bool:
    """Install the resolver if `BARTH_DEVICE_INBOUND_AUTH` is set. Returns True if so.

    Off by default: opening a capture surface is a deployment decision, and
    `inbound_auth`'s fail-closed default is the correct state for a
    deployment that has not made it.
    """
    enabled = (os.getenv(DEVICE_INBOUND_AUTH_ENV) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not enabled:
        return False
    install_device_resolver()
    return True


__all__ = [
    "DEVICE_CREDENTIAL_HEADER",
    "DEVICE_INBOUND_AUTH_ENV",
    "DEVICE_SOURCE_PREFIX",
    "DEVICE_VERIFIED_BY",
    "DeviceCredentialResolver",
    "VerifiedDeviceSource",
    "install_device_resolver",
    "maybe_install_device_resolver_from_env",
    "source_id_for",
]
