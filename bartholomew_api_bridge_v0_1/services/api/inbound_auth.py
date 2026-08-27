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
    """What a verified inbound *source* is, and deliberately nothing more.

    Two attributes, both about provenance:

    * `source_id`   -- which external source this event is from. Provenance,
                       and half of the idempotency key.
    * `verified_by` -- what actually verified this, recorded verbatim in the
                       durable row. Never a value the caller supplied.

    **A source cannot choose a runtime.** There is no `runtime_id` here, and
    if a resolver grows one it is ignored (`resolved_runtime_id()` never reads
    the source). Which isolated runtime an event belongs to is decided by the
    authenticated principal and the process's own runtime binding -- the
    platform's authority, not a claim travelling with the event. A source
    that could name its target runtime would be a cross-user write primitive
    dressed as provenance: verifying *that a webhook is genuinely from Acme*
    says nothing whatever about *whose Bartholomew it belongs in*.

    Structural, not nominal, and not a principal type. The control plane owns
    identity; this describes only the sender.
    """

    @property
    def source_id(self) -> str: ...

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
    """A verified source produced by the test resolver. Never used in production.

    Carries a `runtime_id` attribute *on purpose* even though the contract has
    none: it is what `test_a_source_cannot_choose_its_target_runtime` uses to
    prove that a resolver claiming a runtime has no effect on where the event
    lands. A spoofing attempt that no code path can express is not evidence.
    """

    def __init__(self, source_id: str, claimed_runtime_id: str | None = None):
        self._source_id = source_id
        #: Deliberately ignored by everything. See the class docstring.
        self.runtime_id = claimed_runtime_id

    @property
    def source_id(self) -> str:
        return self._source_id

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

    def __init__(self, token: str, *, source_id: str, claimed_runtime_id: str | None = None):
        self._token = token
        self._source_id = source_id
        self._claimed_runtime_id = claimed_runtime_id

    async def resolve(self, request: Any, body: bytes) -> VerifiedInboundSource | None:
        import hmac

        presented = request.headers.get("x-bartholomew-test-token", "")
        # compare_digest even here: a test double that teaches a sloppy
        # comparison pattern is a test double that gets copied into the real
        # resolver later.
        if not presented or not hmac.compare_digest(presented, self._token):
            return None
        return _TestOnlySource(self._source_id, self._claimed_runtime_id)


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
    claimed_runtime_id: str | None = None,
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
    _resolver = _TestResolver(
        token,
        source_id=source_id,
        claimed_runtime_id=claimed_runtime_id,
    )
    _resolver_is_test_only = True
    logger.warning(
        "TEST-ONLY inbound resolver installed. Inbound events will be admitted on a "
        "static test token and recorded as verified_by=%r. This is not authentication.",
        TEST_RESOLVER_LABEL,
    )


#: Token for the test resolver, read only when the gate above is also set.
TEST_RESOLVER_TOKEN_ENV = "BARTH_INBOUND_TEST_TOKEN"

#: A runtime the test source claims to belong to. Read only alongside both
#: gates above, and ignored by everything -- it exists so a test can prove
#: that a source-claimed runtime has no effect on where an event lands.
TEST_RESOLVER_CLAIMED_RUNTIME_ENV = "BARTH_INBOUND_TEST_CLAIMED_RUNTIME_ID"


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
    # A runtime the test source will *claim*, so an integration test can prove
    # that a spoofing source is ignored rather than merely unexpressible.
    # Ignored by every code path; see `resolved_runtime_id`.
    claimed = (os.getenv(TEST_RESOLVER_CLAIMED_RUNTIME_ENV) or "").strip() or None
    install_test_resolver(token, claimed_runtime_id=claimed)
    return True


def resolved_runtime_id(request: Any) -> str | None:
    """Which isolated runtime an inbound event belongs to.

    **The single authority for that question on this surface**, and it reads
    exactly two things, both owned by the platform:

    1. the verified principal the control plane put on `request.state`, and
    2. this process's own runtime binding.

    It does not read the source, the body, a header, or a query parameter.
    That is the point: verifying who *sent* an event tells you nothing about
    whose Bartholomew it belongs in, and a claim that travelled with the event
    is not evidence of anything.

    Returns None only when the process is unbound and no principal exists --
    the single-runtime local development deployment, where there is exactly
    one runtime and naming it would be inventing precision.
    """
    principal = getattr(getattr(request, "state", None), "principal", None)
    if principal is not None:
        user_id = getattr(principal, "user_id", None)
        if user_id:
            return str(user_id)

    from bartholomew.platform.runtime_registry import bound_runtime_user_id

    return bound_runtime_user_id()


def principal_required() -> bool:
    """True when a request must carry a verified principal to capture anything.

    Delegates to the platform's own answer rather than keeping a second copy
    of the rule: whenever authentication is enforced -- which a non-loopback
    bind forces on and no variable can turn off -- an unauthenticated inbound
    request is refused before it can capture.
    """
    from bartholomew.platform.exposure import auth_enforced

    return auth_enforced()


def clear_resolver() -> None:
    """Remove any installed resolver, returning inbound capture to fail-closed."""
    global _resolver, _resolver_is_test_only
    _resolver = None
    _resolver_is_test_only = False
