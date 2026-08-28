"""
The verified answer to "who is asking".

A `Principal` is only ever constructed from a verified session record. There
is deliberately **no anonymous principal kind**: an unauthenticated request
cannot be represented in this type system at all, which makes fail-open
unrepresentable rather than merely unlikely. Code that needs a principal
either has one or raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PrincipalKind(str, Enum):
    """
    The authority kind of a principal.

    `PLATFORM_ADMIN` is a distinct kind, **not** an `admin=true` flag on an
    ordinary user account (canonical decision, approved 2026-08-27). The
    difference is load-bearing: a flag invites "user OR admin" checks that
    accidentally grant a user administrative authority over their own
    account's platform surface, and it makes the platform tier something a
    user row can be edited into. A separate kind means platform authority is
    a property of *which account you are*, checked explicitly.
    """

    USER = "user"
    PLATFORM_ADMIN = "platform_admin"


@dataclass(frozen=True)
class Principal:
    """
    A verified identity for one request.

    Frozen on purpose: a request handler must not be able to widen its own
    authority by mutating the principal it was handed.

    `user_id` is a server-generated opaque identifier. It never comes from a
    header, query parameter, body field or path segment -- see
    `http_identity.resolve_principal`, which is the only construction site
    reachable from HTTP.
    """

    user_id: str
    username: str
    kind: PrincipalKind
    session_id: str

    @property
    def is_platform_admin(self) -> bool:
        return self.kind is PrincipalKind.PLATFORM_ADMIN


class AuthenticationError(Exception):
    """
    Authentication could not establish an identity: missing, malformed,
    expired, revoked or unknown credential, or an identity/persistence
    mismatch. Always maps to 401. Never maps to "anonymous".
    """


class AuthorizationError(Exception):
    """
    An identity was established but is not authorised for the requested
    capability. Always maps to 403. Distinct from `AuthenticationError` so
    the two can never be collapsed into one permissive branch.
    """


class AuthUnavailableError(Exception):
    """
    The authentication subsystem itself could not answer -- the control-plane
    store is unreachable or corrupt.

    Maps to **503, never 200 and never "anonymous"**. This exception exists
    precisely so that the unavailable-authentication case has a name and a
    fail-closed handler, instead of being an unhandled path that some future
    `except Exception:` swallows into a permissive default.
    """
