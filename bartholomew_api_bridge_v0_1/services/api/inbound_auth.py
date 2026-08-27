"""The authentication seam for inbound capture (Session D).

**This module does not authenticate anything, and must never grow code that
does.** Authentication, the `Principal` type, sessions, identity-to-runtime
resolution and per-user isolation are owned by the authenticated control
plane. This file is the narrow hole that plugs into, plus the fail-closed
default that holds until it lands.

Three rules, all load-bearing:

1. **Default deny.** With no resolver installed, every inbound request is
   refused. Not "allowed from loopback", not "allowed in development" --
   refused. A capture path that authenticates nobody must capture nothing.

2. **Local-peer status is reachability, never authority.** The API's existing
   loopback boundary (`app.resolve_bind_host()` / `_is_local_peer()`) decides
   *where this process can be reached from*. It does not decide *who is
   asking*, and nothing in this module consults it. An unsigned or unresolved
   request from 127.0.0.1 fails exactly as one from anywhere else. Setting
   `BARTH_API_ALLOW_NON_LOOPBACK=1` likewise authorises nothing here.

3. **Test-only auth cannot enable itself.** The test resolver exists so the
   end-to-end HTTP path is provable against a real server before the control
   plane merges. It refuses to install unless
   `BARTH_INBOUND_ALLOW_TEST_RESOLVER=1` is set in the environment, it
   announces itself in the logs and on the health surface, and every event it
   admits is stamped `verified_by="test-resolver"` in the durable record --
   so an Alpha-like configuration can neither turn it on by accident nor hide
   that it was on.

Source IP, LAN membership, arbitrary headers and caller-supplied identifiers
are not authentication and are not consulted as such anywhere below.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: Set to a truthy value to permit `install_test_resolver()`. Absent in any
#: real configuration, which is what makes test-only auth un-self-enabling.
ALLOW_TEST_RESOLVER_ENV = "BARTH_INBOUND_ALLOW_TEST_RESOLVER"

#: `verified_by` stamped on every event admitted by the test resolver, so a
#: durable row always says truthfully what verified it.
TEST_RESOLVER_LABEL = "test-resolver"


@runtime_checkable
class VerifiedInboundSource(Protocol):
    """What the inbound route needs to know about a verified caller.

    Structural, not nominal, and deliberately minimal -- three attributes.
    The control plane's own `Principal` is expected to satisfy this shape
    directly or through a thin adapter it owns; this is not a second principal
    type and nothing here should be constructed as one in production code.

    * `source_id`  -- which external source this event is from. Provenance,
                      and half of the idempotency key.
    * `runtime_id` -- which isolated runtime the event belongs to, resolved by
                      the control plane. `None` while a single runtime is the
                      only one that exists.
    * `verified_by`-- what actually verified this, recorded verbatim in the
                      durable row. Never a value the caller supplied.
    """

    @property
    def source_id(self) -> str: ...

    @property
    def runtime_id(self) -> str | None: ...

    @property
    def verified_by(self) -> str: ...


class InboundPrincipalResolver(Protocol):
    """Verifies the caller of an inbound request, or refuses it.

    Returns `None` for any request it cannot verify -- the route turns that
    into a 401 and captures nothing. Raising is also a refusal: the route
    fails closed on an errored resolver rather than admitting the request.

    Implementations receive the Starlette `Request` and may read the body
    (the route reads it first and the body is cached), so a signature scheme
    over the raw bytes is expressible without changing this interface.
    """

    async def resolve(self, request: Any, body: bytes) -> VerifiedInboundSource | None: ...


class _TestOnlySource:
    """A verified source produced by the test resolver. Never used in production."""

    def __init__(self, source_id: str, runtime_id: str | None = None):
        self._source_id = source_id
        self._runtime_id = runtime_id

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def runtime_id(self) -> str | None:
        return self._runtime_id

    @property
    def verified_by(self) -> str:
        return TEST_RESOLVER_LABEL


class _TestResolver:
    """Admits requests carrying an exact, pre-agreed token.

    Not a production authentication scheme and not a stand-in for one: there
    is no key rotation, no signature over the body, no replay window, and the
    token lives in the test process. Its only job is to make the *rest* of the
    path -- HTTP boundary, validation, governance, capture, idempotency,
    provenance -- provable end-to-end before real authentication exists.
    """

    def __init__(self, token: str, *, source_id: str, runtime_id: str | None = None):
        self._token = token
        self._source_id = source_id
        self._runtime_id = runtime_id

    async def resolve(self, request: Any, body: bytes) -> VerifiedInboundSource | None:
        import hmac

        presented = request.headers.get("x-bartholomew-test-token", "")
        # compare_digest even here: a test double that teaches a sloppy
        # comparison pattern is a test double that gets copied into the real
        # resolver later.
        if not presented or not hmac.compare_digest(presented, self._token):
            return None
        return _TestOnlySource(self._source_id, self._runtime_id)


#: The installed resolver. `None` -- fail closed -- is the only default, and
#: the only state any real configuration ever reaches today.
_resolver: InboundPrincipalResolver | None = None
_resolver_is_test_only: bool = False


def get_resolver() -> InboundPrincipalResolver | None:
    """The installed resolver, or None when inbound capture is closed."""
    return _resolver


def resolver_is_test_only() -> bool:
    """True when the currently installed resolver is the test double.

    Surfaced on the health endpoint so an operator can never be unsure whether
    a running service is admitting inbound events on test credentials.
    """
    return _resolver_is_test_only


def install_resolver(resolver: InboundPrincipalResolver) -> None:
    """Install the control plane's resolver.

    The intended production call site: the authenticated control plane
    installs its own verifier at startup, and inbound capture opens.
    """
    global _resolver, _resolver_is_test_only
    _resolver = resolver
    _resolver_is_test_only = False
    logger.info("Inbound capture is open: principal resolver installed")


def install_test_resolver(
    token: str,
    *,
    source_id: str = "test-source",
    runtime_id: str | None = None,
) -> None:
    """Install the test-only resolver. Refuses unless explicitly permitted.

    Raises `RuntimeError` when `BARTH_INBOUND_ALLOW_TEST_RESOLVER` is not set,
    so this cannot be reached by an Alpha-like configuration, by an import
    side effect, or by a stray call in production code.
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
            "Refusing to install the test-only inbound resolver: "
            f"{ALLOW_TEST_RESOLVER_ENV} is not set. This resolver is not "
            "authentication and must never be reachable in a deployed "
            "configuration.",
        )
    if not token:
        raise RuntimeError("The test-only inbound resolver requires a non-empty token.")
    _resolver = _TestResolver(token, source_id=source_id, runtime_id=runtime_id)
    _resolver_is_test_only = True
    logger.warning(
        "TEST-ONLY inbound resolver installed. Inbound events will be admitted on a "
        "static test token and recorded as verified_by=%r. This is not authentication.",
        TEST_RESOLVER_LABEL,
    )


#: Token for the test resolver, read only when the gate above is also set.
TEST_RESOLVER_TOKEN_ENV = "BARTH_INBOUND_TEST_TOKEN"


def maybe_install_test_resolver_from_env() -> bool:
    """Install the test resolver if -- and only if -- both gates are set.

    Called once at application startup so an integration test can prove the
    full HTTP path against a real server process, which is not otherwise
    reachable: the test runs in a different process from the server, so it
    cannot call `install_test_resolver()` directly.

    Two independent environment variables are required, neither of which
    exists in any deployed configuration, and turning it on is loud in three
    places at once: a warning log at startup, `test_resolver_active` on the
    health endpoint, and `verified_by="test-resolver"` on every durable row it
    admits. It cannot enable itself silently, and it cannot enable itself at
    all from a single stray variable.

    Returns True if the test resolver was installed.
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
    install_test_resolver(token)
    return True


def clear_resolver() -> None:
    """Remove any installed resolver, returning inbound capture to fail-closed."""
    global _resolver, _resolver_is_test_only
    _resolver = None
    _resolver_is_test_only = False
