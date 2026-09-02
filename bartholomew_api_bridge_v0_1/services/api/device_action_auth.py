"""The authentication seam for the device action channel. Separate, and fail-closed.

**This is not `inbound_auth`, and installing one does not open the other.** The
two modules hold two independent resolvers in two independent module globals,
and the observation companion's credentials verify a *source* of observations
while these verify a *device* that may be handed actions. A deployment that has
opened inbound capture has not thereby opened actuation, and
`tests/test_windows_action_channel_separation.py` asserts exactly that by
installing the inbound test resolver and showing the action channel still
refuses.

Like `inbound_auth`, this module does not authenticate anything and must never
grow code that does. Authentication, the `Principal` type and per-user
isolation are owned by the authenticated control plane; this is the narrow hole
that plugs into, plus the fail-closed default that holds until it lands.

Three rules, all load-bearing and all identical in shape to the inbound seam's,
because the reasoning is the same and two different shapes would be two things
to audit:

1. **Default deny.** With no resolver installed, every action-channel request
   is refused. Not "allowed from loopback", not "allowed in development" --
   refused. A channel that authenticates nobody must dispatch nothing.

2. **Local-peer status is reachability, never authority.** Nothing here
   consults the loopback boundary. An unverified request from 127.0.0.1 fails
   exactly as one from anywhere else.

3. **Test-only auth cannot enable itself.** The test resolver refuses to
   install unless `BARTH_ACTION_ALLOW_TEST_RESOLVER` is set, announces itself
   in the logs and on the health surface, and stamps every action it leases
   with `verified_by="action-test-resolver"` -- so an alpha-like configuration
   can neither turn it on by accident nor hide that it was on.

**A device cannot choose its tenant.** There is no `tenant_id` on
`VerifiedDevice`, and if a resolver grows one it is ignored:
`resolved_tenant_id()` never reads the device. Which tenant an action belongs
to is decided by the authenticated principal and this process's own runtime
binding -- the platform's authority. A device that could name its tenant would
be a cross-tenant read primitive dressed as provenance.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: Set to a truthy value to permit `install_test_resolver()`. Absent in any
#: real configuration, which is what makes test-only auth un-self-enabling.
ALLOW_TEST_RESOLVER_ENV = "BARTH_ACTION_ALLOW_TEST_RESOLVER"

#: Token for the test resolver, read only when the gate above is also set.
TEST_RESOLVER_TOKEN_ENV = "BARTH_ACTION_TEST_TOKEN"

#: The device id the test resolver verifies as.
TEST_RESOLVER_DEVICE_ENV = "BARTH_ACTION_TEST_DEVICE_ID"

#: `verified_by` stamped wherever the test resolver admitted a call.
TEST_RESOLVER_LABEL = "action-test-resolver"

#: The single-tenant sentinel for a deployment that serves one personal
#: Bartholomew and is not bound to a user id -- the loopback development
#: install. Named rather than left as an empty string so an audit row says
#: which tenant an action belonged to even there.
LOCAL_TENANT = "local"


@runtime_checkable
class VerifiedDevice(Protocol):
    """What a verified device is, and deliberately nothing more.

    Two attributes, both about provenance:

    * `device_id`   -- which enrolled device this channel is. Half of what the
                       dispatch path checks; the other half is the enrolment.
    * `verified_by` -- what actually verified it, recorded verbatim in the
                       durable row. Never a value the caller supplied.

    Structural, not nominal, and not a principal type.
    """

    @property
    def device_id(self) -> str: ...

    @property
    def verified_by(self) -> str: ...


class DeviceActionResolver(Protocol):
    """Verifies the device on the other end of an action-channel call, or refuses.

    Returns `None` for anything it cannot verify -- the route turns that into a
    401 and dispatches nothing. Raising is also a refusal: the route fails
    closed on an errored resolver rather than admitting the call.
    """

    async def resolve(self, request: Any, body: bytes) -> VerifiedDevice | None: ...


class _TestOnlyDevice:
    """A verified device produced by the test resolver. Never used in production.

    Carries a `tenant_id` attribute *on purpose* even though the contract has
    none: it is what the separation test uses to prove that a resolver claiming
    a tenant has no effect on which tenant's actions it can lease. A spoofing
    attempt that no code path can express is not evidence.
    """

    def __init__(self, device_id: str, claimed_tenant_id: str | None = None):
        self._device_id = device_id
        #: Deliberately ignored by everything. See the class docstring.
        self.tenant_id = claimed_tenant_id

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def verified_by(self) -> str:
        return TEST_RESOLVER_LABEL


class _TestResolver:
    """Admits calls carrying an exact, pre-agreed token.

    Not a production authentication scheme and not a stand-in for one: no key
    rotation, no signature over the body, no replay window, no device identity
    material. Its only job is to make the *rest* of the path -- HTTP boundary,
    validation, governance, approval, lease, result -- provable end to end
    before real device enrolment exists.
    """

    def __init__(self, token: str, *, device_id: str, claimed_tenant_id: str | None = None):
        self._token = token
        self._device_id = device_id
        self._claimed_tenant_id = claimed_tenant_id

    async def resolve(self, request: Any, body: bytes) -> VerifiedDevice | None:
        import hmac

        presented = request.headers.get("x-bartholomew-device-token", "")
        # compare_digest even here: a test double that teaches a sloppy
        # comparison pattern is a test double that gets copied into the real
        # resolver later.
        if not presented or not hmac.compare_digest(presented, self._token):
            return None
        return _TestOnlyDevice(self._device_id, self._claimed_tenant_id)


#: The installed resolver. `None` -- fail closed -- is the only default, and
#: the only state any real configuration reaches today.
#:
#: Deliberately a *different* global from `inbound_auth._resolver`. Sharing one
#: would mean opening observation capture also opened actuation, which is the
#: single most important thing this file exists to prevent.
_resolver: DeviceActionResolver | None = None
_resolver_is_test_only: bool = False


def get_resolver() -> DeviceActionResolver | None:
    """The installed resolver, or None when the action channel is closed."""
    return _resolver


def resolver_is_test_only() -> bool:
    """True when the currently installed resolver is the test double.

    Surfaced on the health endpoint so an operator can never be unsure whether
    a running service is dispatching actions on test credentials.
    """
    return _resolver_is_test_only


def install_resolver(resolver: DeviceActionResolver) -> None:
    """Install the control plane's device resolver, opening the action channel."""
    global _resolver, _resolver_is_test_only
    _resolver = resolver
    _resolver_is_test_only = False
    logger.info("The device action channel is open: a device resolver is installed")


def install_test_resolver(
    token: str,
    *,
    device_id: str = "test-device",
    claimed_tenant_id: str | None = None,
) -> None:
    """Install the test-only resolver. Refuses unless explicitly permitted.

    Raises `RuntimeError` when `BARTH_ACTION_ALLOW_TEST_RESOLVER` is not set,
    so this cannot be reached by an alpha-like configuration, by an import side
    effect, or by a stray call in production code.
    """
    global _resolver, _resolver_is_test_only
    allowed = (os.getenv(ALLOW_TEST_RESOLVER_ENV) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not allowed:
        raise RuntimeError(
            "Refusing to install the test-only device action resolver: "
            f"{ALLOW_TEST_RESOLVER_ENV} is not set. This resolver is not "
            "authentication and must never be reachable in a deployed configuration.",
        )
    if not token:
        raise RuntimeError("The test-only device action resolver requires a token.")
    _resolver = _TestResolver(
        token,
        device_id=device_id,
        claimed_tenant_id=claimed_tenant_id,
    )
    _resolver_is_test_only = True
    logger.warning(
        "TEST-ONLY device action resolver installed. Actions will be leased to a "
        "device authenticated by a static test token and recorded as verified_by=%r. "
        "This is not authentication.",
        TEST_RESOLVER_LABEL,
    )


def maybe_install_test_resolver_from_env() -> bool:
    """Install the test resolver if -- and only if -- both gates are set.

    Called once at application startup so an integration test can prove the
    full HTTP path against a real server, which is not otherwise reachable: the
    test runs in a different process from the server.

    Two independent environment variables are required, neither of which exists
    in any deployed configuration, and turning it on is loud in three places at
    once: a warning log at startup, `action_test_resolver_active` on the health
    endpoint, and `verified_by` on the durable rows.
    """
    if (os.getenv(ALLOW_TEST_RESOLVER_ENV) or "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False
    token = (os.getenv(TEST_RESOLVER_TOKEN_ENV) or "").strip()
    if not token:
        return False
    device_id = (os.getenv(TEST_RESOLVER_DEVICE_ENV) or "").strip() or "test-device"
    install_test_resolver(token, device_id=device_id)
    return True


def clear_resolver() -> None:
    """Remove any installed resolver, returning the action channel to fail-closed."""
    global _resolver, _resolver_is_test_only
    _resolver = None
    _resolver_is_test_only = False


def principal_required() -> bool:
    """True when an action-channel call must carry a verified principal.

    Delegates to the platform's own answer rather than keeping a second copy of
    the rule: whenever authentication is enforced -- which a non-loopback bind
    forces on and no variable can turn off -- an unauthenticated action-channel
    request is refused before it can lease anything.
    """
    from bartholomew.platform.exposure import auth_enforced

    return auth_enforced()


def resolved_tenant_id(request: Any) -> str:
    """Which tenant an action belongs to. **The single authority on this surface.**

    Reads exactly two things, both owned by the platform:

    1. the verified principal the control plane put on `request.state`, and
    2. this process's own runtime binding.

    It does not read the device, the body, a header or a query parameter. That
    is the point: verifying *which machine* is calling tells you nothing about
    *whose Bartholomew it belongs to*, and a claim travelling with the call is
    not evidence of anything.

    Falls back to the named `LOCAL_TENANT` sentinel only when the process is
    unbound and no principal exists -- the single-runtime local development
    deployment, where there is exactly one tenant. Named rather than blank, so
    every durable row says which tenant it belonged to.
    """
    principal = getattr(getattr(request, "state", None), "principal", None)
    if principal is not None:
        user_id = getattr(principal, "user_id", None)
        if user_id:
            return str(user_id)

    from bartholomew.platform.runtime_registry import bound_runtime_user_id

    return bound_runtime_user_id() or LOCAL_TENANT
