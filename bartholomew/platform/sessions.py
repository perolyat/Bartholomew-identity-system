"""
Server-side opaque sessions.

Server-side rather than JWT, and that is the whole argument: **revocation
must be effective on the very next request.** A self-contained signed token
cannot be revoked without a server-side denylist, at which point it is a
session store with extra cryptography. A row we can delete is simpler and
strictly safer at Alpha scale.

Recorded S8 replay review (approved 2026-08-27). A bearer session cookie is
**replayable by anyone who captures it**; this is stated rather than
implied, because S8 requires the "a simple token is enough" assumption be
reviewed rather than assumed. The Alpha defences are, in order of
importance:

1. **Transport.** TLS is mandatory for any non-loopback bind and the process
   refuses to start without it (see `exposure.py`). This, not the token
   shape, is what stops capture.
2. **Short idle timeout plus absolute expiry**, both checked server-side on
   every request, bounding the value of a captured token.
3. **Client binding.** The session is bound to a fingerprint of the client
   that created it; a mismatch revokes the session rather than merely
   refusing the request, on the reasoning that a fingerprint mismatch is
   either a theft or a network change, and forcing a re-login is cheap in
   both cases.
4. **Immediate revocation**, per-session or per-account.

Per-request signing and device-bound keys -- which would give genuine replay
resistance rather than replay *containment* -- are deliberately deferred to
the device/client authentication layer, where the key material has somewhere
to live. That deferral is a decision, not an oversight.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid

from .principal import (
    AuthenticationError,
    AuthUnavailableError,
    Principal,
    PrincipalKind,
)
from .store import platform_connection, record_platform_audit

SESSION_COOKIE_NAME = "barth_session"

# Absolute lifetime and idle timeout. Short by ordinary web standards on
# purpose: the asset behind this cookie is a person's entire personal memory
# plus their Parking Brake, not a shopping cart.
DEFAULT_ABSOLUTE_TTL_S = 12 * 60 * 60
DEFAULT_IDLE_TIMEOUT_S = 60 * 60

_TOKEN_BYTES = 32  # 256 bits


def _hash_token(token: str) -> str:
    """
    SHA-256 of the token as stored.

    A plain digest rather than a password KDF is correct here and elsewhere
    would not be: the token is 256 bits of `secrets` randomness, so it has no
    guessable structure for a KDF to protect. The digest exists so that read
    access to the sessions table does not yield usable live credentials.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def client_fingerprint(ip: str | None, user_agent: str | None) -> str:
    """
    A stable fingerprint of the client a session was issued to.

    Deliberately coarse -- peer IP plus User-Agent -- because it is a theft
    *tripwire*, not an authentication factor. It is hashed rather than stored
    raw so the sessions table does not accumulate a browsing-metadata trail
    about Alpha participants.
    """
    return hashlib.sha256(f"{ip or ''}|{user_agent or ''}".encode()).hexdigest()


def create_session(
    user_id: str,
    *,
    fingerprint: str,
    db_path: str | None = None,
    absolute_ttl_s: int = DEFAULT_ABSOLUTE_TTL_S,
    now: int | None = None,
) -> tuple[str, str]:
    """
    Issue a session. Returns `(session_id, token)`.

    The plaintext token is returned exactly once, here, and never stored or
    logged. If the caller loses it, the session is unusable -- which is the
    intended property.
    """
    now = int(now if now is not None else time.time())
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    session_id = str(uuid.uuid4())
    with platform_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO platform_sessions"
            "(session_id, token_hash, user_id, created_at, expires_at,"
            " last_seen_at, client_fingerprint, revoked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                session_id,
                _hash_token(token),
                user_id,
                now,
                now + absolute_ttl_s,
                now,
                fingerprint,
            ),
        )
        # Note the audit detail records the session id, never the token.
        record_platform_audit(
            conn, "session.created", user_id=user_id, detail=f"session={session_id}", ts=now
        )
    return session_id, token


def verify_session(
    token: str | None,
    *,
    fingerprint: str,
    db_path: str | None = None,
    idle_timeout_s: int = DEFAULT_IDLE_TIMEOUT_S,
    now: int | None = None,
) -> Principal:
    """
    Verify a session token and return the `Principal` it names.

    Raises `AuthenticationError` for every failure -- missing, malformed,
    unknown, expired, idle-timed-out, revoked, fingerprint-mismatched, or
    naming an account that no longer exists or has been disabled. It never
    returns a degraded or anonymous principal, because none exists.

    An identity/persistence mismatch -- a session row whose account row is
    gone -- is treated as a hard failure rather than a stale-data curiosity:
    it is exactly the state in which a recycled `user_id` could hand one
    person another person's runtime.
    """
    if not token or not isinstance(token, str):
        raise AuthenticationError("no session credential presented")
    # Bound the work an unauthenticated caller can force us to do, and reject
    # obviously malformed input before it reaches the database.
    if len(token) > 512:
        raise AuthenticationError("malformed session credential")

    now = int(now if now is not None else time.time())
    token_hash = _hash_token(token)

    try:
        return _verify_session_locked(
            token_hash, fingerprint=fingerprint, db_path=db_path,
            idle_timeout_s=idle_timeout_s, now=now,
        )
    except sqlite3.Error as exc:
        # The control-plane store itself could not answer. This is the case a
        # permissive default would silently turn into an open door, so it has
        # its own exception and its own 503 -- never a fallback identity.
        raise AuthUnavailableError("control-plane session store unavailable") from exc


def _verify_session_locked(
    token_hash: str,
    *,
    fingerprint: str,
    db_path: str | None,
    idle_timeout_s: int,
    now: int,
) -> Principal:
    with platform_connection(db_path) as conn:
        row = conn.execute(
            "SELECT s.*, a.username, a.kind, a.disabled_at "
            "FROM platform_sessions s "
            "LEFT JOIN platform_accounts a ON a.user_id = s.user_id "
            "WHERE s.token_hash = ?",
            (token_hash,),
        ).fetchone()

        if row is None:
            raise AuthenticationError("unknown session credential")
        if row["revoked_at"] is not None:
            raise AuthenticationError("session revoked")
        if now >= row["expires_at"]:
            raise AuthenticationError("session expired")
        if now - row["last_seen_at"] > idle_timeout_s:
            # Idle-expired sessions are revoked, not merely refused, so a
            # captured-but-idle token cannot be resurrected by a later
            # legitimate login refreshing the row.
            conn.execute(
                "UPDATE platform_sessions SET revoked_at = ? WHERE session_id = ?",
                (now, row["session_id"]),
            )
            # Committed explicitly before raising: `with conn` rolls the
            # transaction back on exception, which would silently undo the
            # revocation this branch exists to perform.
            conn.commit()
            raise AuthenticationError("session idle timeout")
        if row["username"] is None:
            raise AuthenticationError("session does not name a live account")
        if row["disabled_at"] is not None:
            raise AuthenticationError("account disabled")
        if not hmac.compare_digest(row["client_fingerprint"], fingerprint):
            conn.execute(
                "UPDATE platform_sessions SET revoked_at = ? WHERE session_id = ?",
                (now, row["session_id"]),
            )
            record_platform_audit(
                conn,
                "session.fingerprint_mismatch",
                user_id=row["user_id"],
                detail=f"session={row['session_id']}",
                ts=now,
            )
            # See the idle-timeout branch: commit before raising, or the
            # rollback on exception discards the revocation and the audit row.
            conn.commit()
            raise AuthenticationError("session client mismatch")

        conn.execute(
            "UPDATE platform_sessions SET last_seen_at = ? WHERE session_id = ?",
            (now, row["session_id"]),
        )

        try:
            kind = PrincipalKind(row["kind"])
        except ValueError as exc:
            # An unrecognised authority kind fails closed rather than
            # defaulting to USER: a corrupt or hand-edited row must not be
            # able to silently become an ordinary valid identity.
            raise AuthenticationError("unknown principal kind") from exc

        return Principal(
            user_id=row["user_id"],
            username=row["username"],
            kind=kind,
            session_id=row["session_id"],
        )


def revoke_session(session_id: str, *, db_path: str | None = None, now: int | None = None) -> None:
    now = int(now if now is not None else time.time())
    with platform_connection(db_path) as conn:
        conn.execute(
            "UPDATE platform_sessions SET revoked_at = ? "
            "WHERE session_id = ? AND revoked_at IS NULL",
            (now, session_id),
        )
        record_platform_audit(conn, "session.revoked", detail=f"session={session_id}", ts=now)


def revoke_all_sessions(user_id: str, *, db_path: str | None = None, now: int | None = None) -> int:
    """Log out everywhere. Returns the number of sessions revoked."""
    now = int(now if now is not None else time.time())
    with platform_connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE platform_sessions SET revoked_at = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (now, user_id),
        )
        record_platform_audit(
            conn, "session.revoked_all", user_id=user_id, detail=f"count={cur.rowcount}", ts=now
        )
        return cur.rowcount


def purge_expired(*, db_path: str | None = None, now: int | None = None) -> int:
    """Delete session rows that can no longer authenticate anything."""
    now = int(now if now is not None else time.time())
    with platform_connection(db_path) as conn:
        cur = conn.execute("DELETE FROM platform_sessions WHERE expires_at < ?", (now,))
        return cur.rowcount
