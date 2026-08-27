"""
The HTTP authentication boundary: the only place a request becomes an identity.

This is the seam Session D builds deployment plumbing *around* rather than
through. D owns routing, supervision and inbound transport; this module owns
the question of who is asking.

Ordering, which matters as much as the checks themselves. Per request:

  1. the API bridge's **network boundary** (loopback, unless deliberately
     opened) -- may this peer reach this process at all;
  2. **authentication** (here) -- who is asking;
  3. **authorisation** (here) -- what may this identity request;
  4. the existing **admission gate** -- is the kernel ready to take work;
  5. the route handler, and below it **Governance** -- may Bartholomew
     actually do this.

Steps 2 and 3 are additional boundaries. They never substitute for step 5: an
authorised principal is still refused by the Parking Brake, the consent gate
and policy, and nothing in this module can relax any of them.
"""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse
from starlette.routing import Match

from .capabilities import require_capability
from .exposure import auth_enforced
from .principal import (
    AuthenticationError,
    AuthorizationError,
    AuthUnavailableError,
    Principal,
)
from .route_policy import UnclassifiedRouteError, capability_for, is_public_path
from .runtime_registry import assert_principal_owns_this_process
from .sessions import SESSION_COOKIE_NAME, client_fingerprint, verify_session

# The attribute the verified principal is published on. Handlers read
# `request.state.principal`; nothing reads a header, and there is no helper
# that accepts a user id from request data.
PRINCIPAL_STATE_ATTR = "principal"

# Header names a client might supply hoping to name a user. None of them are
# read for identity anywhere in this codebase; they are listed so the
# adversarial test can assert on the full set and so a future reader can see
# that the omission is deliberate rather than accidental.
CLIENT_SUPPLIED_IDENTITY_HEADERS = (
    "x-user-id",
    "x-user",
    "x-username",
    "x-tenant-id",
    "x-tenant",
    "x-principal",
    "x-forwarded-user",
    "x-authenticated-user",
    "x-on-behalf-of",
)


def _iter_routes(routes: Any):
    """
    Flatten the app's route table.

    FastAPI wraps included routers in an `_IncludedRouter` object whose own
    `routes` live on `original_router`, so a plain iteration over
    `app.routes` sees the wrappers rather than the endpoints. Recursing is
    what makes the policy lookup -- and the coverage test that guards it --
    see every route rather than only the ones declared on the app directly.
    """
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _iter_routes(inner.routes)
            continue
        yield route


def _route_template(request: Any) -> str | None:
    """
    The route template that will handle this request, e.g.
    `/api/memory/{kind}/{key}`.

    Resolved by running the app's own matcher rather than reading
    `request.scope["route"]`. That key is populated by the router, which runs
    *after* HTTP middleware -- so at this point it is always absent, and
    reading it would make the policy lookup silently conclude "no route" and
    skip enforcement on every request. That is precisely the fail-open shape
    this boundary exists to prevent, so the match is done explicitly here.

    Matching the template rather than the raw URL also means no
    path-normalisation trick (`//api/..`, `%2e%2e`, a trailing slash) can
    select a different policy entry than the handler that actually runs: this
    asks the same matcher, with the same scope, that routing will use.

    Returns None only when genuinely nothing matches -- a 404, where no
    handler will run.
    """
    scope = request.scope
    partial: str | None = None
    for route in _iter_routes(request.app.routes):
        try:
            match, _child = route.matches(scope)
        except Exception:
            continue
        if match is Match.FULL:
            return getattr(route, "path", None)
        if match is Match.PARTIAL and partial is None:
            # Path matches but the method does not: a 405. Remember it so a
            # wrong-method request is still policed rather than waved through
            # as "no route".
            partial = getattr(route, "path", None)
    return partial


def request_fingerprint(request: Any) -> str:
    peer = request.client.host if request.client else None
    return client_fingerprint(peer, request.headers.get("user-agent"))


def resolve_principal(request: Any, *, db_path: str | None = None) -> Principal:
    """
    Establish the identity behind a request, from the session cookie alone.

    The **only** construction site for a `Principal` reachable from HTTP.
    Identity comes from the verified session record and nothing else: not a
    header, not a query parameter, not a body field, not a path segment. A
    client may send `X-User-Id: someone-else` all day; it is never read.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        # Bearer is accepted for non-browser clients (the CLI, a future
        # device). Same server-side session, same expiry and revocation --
        # only the transport of the token differs.
        auth_header = request.headers.get("authorization") or ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
    return verify_session(token, fingerprint=request_fingerprint(request), db_path=db_path)


def authenticate_and_authorize(request: Any, *, db_path: str | None = None) -> Principal | None:
    """
    Run the whole boundary for one request.

    Returns the verified `Principal`, or None when authentication is not
    enforced (loopback-only development) and the path is exempt. Raises the
    typed failures for the caller to translate; it never returns a
    placeholder or degraded identity, because none exists.
    """
    path = request.url.path
    if is_public_path(path):
        return None

    template = _route_template(request)
    if template is None:
        # Nothing matched: let the 404 happen rather than inventing a policy
        # decision about a route that does not exist. No handler will run.
        return None

    if not auth_enforced():
        return None

    principal = resolve_principal(request, db_path=db_path)
    require_capability(principal, capability_for(request.method, template))
    # Last: is this even the right process for this identity? Checked after
    # authorisation so a wrong-runtime request cannot be used to probe which
    # capabilities exist.
    assert_principal_owns_this_process(principal)
    return principal


def error_response(exc: Exception) -> JSONResponse:
    """
    Translate a boundary failure into a response. Fail-closed by construction:
    every branch is a refusal, and there is no branch that admits.

    The bodies are deliberately uninformative about *why* authentication
    failed -- "expired" and "no such session" look identical to a caller --
    so the endpoint is not an oracle for probing valid credentials.
    """
    if isinstance(exc, AuthUnavailableError):
        # 503, never 200 and never anonymous. The authentication subsystem
        # being unable to answer is the case a permissive default would
        # silently convert into an open door.
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication is temporarily unavailable."},
        )
    if isinstance(exc, AuthenticationError):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if isinstance(exc, (AuthorizationError, UnclassifiedRouteError)):
        return JSONResponse(
            status_code=403,
            content={"detail": "Not authorised for this operation."},
        )
    raise exc
