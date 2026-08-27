"""
Authentication endpoints: login, logout, whoami.

Three routes, deliberately. There is **no self-registration, no password
reset, no email verification and no account-management surface** here
(approved decision, 2026-08-27): Alpha participants are provisioned by an
operator with `bartholomew accounts create`. Every additional endpoint on
this router would be another unauthenticated entry point into the control
plane, and none of them is needed to let a handful of trusted people log in.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from bartholomew.platform import accounts, sessions
from bartholomew.platform.exposure import auth_enforced, non_loopback_enabled
from bartholomew.platform.http_identity import (
    PRINCIPAL_STATE_ATTR,
    request_fingerprint,
)
from bartholomew.platform.sessions import SESSION_COOKIE_NAME

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict:
    """
    Exchange a username and password for a session.

    Returns an identical 401 for an unknown username, a wrong password and a
    disabled account, so the endpoint cannot be used to enumerate who has an
    Alpha account. `accounts.authenticate` performs a dummy hash for the
    unknown-user case so the timings do not separate them either.
    """
    if not auth_enforced():
        # Refuse rather than issue a session that nothing will check: a
        # credential that appears to work but authorises nothing is worse
        # than a clear refusal, because it looks like security.
        raise HTTPException(
            409,
            "Authentication is not enforced in this deployment "
            "(BARTH_AUTH_MODE=disabled); login would issue a session that "
            "no request boundary consults.",
        )

    account = accounts.authenticate(body.username, body.password)
    if account is None:
        raise HTTPException(401, "Invalid username or password.")

    _session_id, token = sessions.create_session(
        account["user_id"],
        fingerprint=request_fingerprint(request),
    )

    # HttpOnly: unreachable from page JavaScript, so an XSS in the UI cannot
    # read it. SameSite=strict: not sent on cross-site requests, which is the
    # CSRF defence at this scale -- cheaper and harder to get wrong than a
    # token-pair scheme. Secure: only ever sent over TLS, which is mandatory
    # wherever this deployment is non-loopback.
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        secure=non_loopback_enabled(),
        max_age=sessions.DEFAULT_ABSOLUTE_TTL_S,
        path="/",
    )
    # Also returned in the body for non-browser clients (CLI, future device),
    # which have no cookie jar. Same server-side session either way.
    return {
        "username": account["username"],
        "kind": account["kind"],
        "token": token,
        "expires_in": sessions.DEFAULT_ABSOLUTE_TTL_S,
    }


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    """
    End this session.

    Deletes the server-side session record rather than only clearing the
    cookie: a logout that leaves the session valid is not a logout, and a
    copy of the token may already be elsewhere.
    """
    principal = getattr(request.state, PRINCIPAL_STATE_ATTR, None)
    if principal is None:
        raise HTTPException(401, "Not authenticated.")
    sessions.revoke_session(principal.session_id)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@router.get("/whoami")
async def whoami(request: Request) -> dict:
    """The verified identity behind this request. Useful for testing the boundary."""
    principal = getattr(request.state, PRINCIPAL_STATE_ATTR, None)
    if principal is None:
        raise HTTPException(401, "Not authenticated.")
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "kind": principal.kind.value,
        "session_id": principal.session_id,
    }
