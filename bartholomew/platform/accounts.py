"""
Alpha accounts: operator-created only.

There is deliberately **no self-registration and no password-reset flow** in
this module or anywhere else in the codebase (approved decision, 2026-08-27).
Alpha is a handful of known, invited participants; an operator provisions
them with `bartholomew accounts create`. Those are product surfaces, not
security requirements, and each one is an additional unauthenticated entry
point into the control plane.

Password hashing uses **stdlib `hashlib.scrypt`** rather than the Argon2id
originally proposed. Both are memory-hard KDFs suitable for password storage;
scrypt is in the standard library, so this adds no dependency to a repository
whose CI enforces a packaging contract and a dependency-graph check. The
stored hash is self-describing (parameters are encoded in the string), so
raising the cost parameters later does not invalidate existing hashes -- they
verify under their own recorded parameters and can be re-hashed on next
successful login if we ever need to.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid

from .principal import PrincipalKind
from .store import platform_connection, record_platform_audit

# Cost parameters. n*r*128 bytes of memory: 2**15 * 8 * 128 = 32 MiB per
# hash. Comfortable for a login on a server, expensive in bulk for an
# attacker holding a stolen database.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

_MIN_PASSWORD_LENGTH = 12


class AccountError(Exception):
    """Provisioning or credential-shape failure. Never raised on a login attempt."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Hash a password into a self-describing `scrypt$n$r$p$salt$hash` string."""
    salt = salt if salt is not None else secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_N * _SCRYPT_R * 256,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """
    Constant-time verification against a stored self-describing hash.

    Returns False rather than raising on a malformed stored value: a corrupt
    row must fail the login closed, not produce a 500 that distinguishes it
    from a wrong password.
    """
    try:
        scheme, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except Exception:
        return False

    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=n * r * 256,
        )
    except Exception:
        return False
    return hmac.compare_digest(candidate, expected)


def create_account(
    username: str,
    password: str,
    *,
    kind: PrincipalKind = PrincipalKind.USER,
    db_path: str | None = None,
) -> str:
    """
    Provision an account. Returns the server-generated opaque `user_id`.

    The `user_id` is a fresh UUID4 and is **not** derived from the username:
    it is the key that per-user data isolation is built on, so it must not be
    guessable from, or collide with, anything a person chooses.
    """
    username = (username or "").strip()
    if not username:
        raise AccountError("username must not be empty")
    if len(password or "") < _MIN_PASSWORD_LENGTH:
        raise AccountError(f"password must be at least {_MIN_PASSWORD_LENGTH} characters")

    user_id = str(uuid.uuid4())
    now = int(time.time())
    with platform_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT 1 FROM platform_accounts WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
        if existing:
            raise AccountError(f"account {username!r} already exists")
        conn.execute(
            "INSERT INTO platform_accounts"
            "(user_id, username, kind, password_hash, created_at, disabled_at) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (user_id, username, kind.value, hash_password(password), now),
        )
        record_platform_audit(
            conn,
            "account.created",
            user_id=user_id,
            detail=f"username={username} kind={kind.value}",
            ts=now,
        )
    return user_id


def get_account(user_id: str, *, db_path: str | None = None) -> dict | None:
    with platform_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM platform_accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def list_accounts(*, db_path: str | None = None) -> list[dict]:
    """Account metadata for operator tooling. Never returns password hashes."""
    with platform_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT user_id, username, kind, created_at, disabled_at "
            "FROM platform_accounts ORDER BY created_at",
        ).fetchall()
    return [dict(r) for r in rows]


def set_account_disabled(
    user_id: str, disabled: bool, *, db_path: str | None = None
) -> None:
    """
    Disable or re-enable an account.

    Disabling revokes every live session for that account in the same
    transaction. A disabled account whose sessions kept working would be a
    revocation that does not revoke -- the property S8 asks us to test.
    """
    now = int(time.time())
    with platform_connection(db_path) as conn:
        conn.execute(
            "UPDATE platform_accounts SET disabled_at = ? WHERE user_id = ?",
            (now if disabled else None, user_id),
        )
        if disabled:
            conn.execute(
                "UPDATE platform_sessions SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id),
            )
        record_platform_audit(
            conn,
            "account.disabled" if disabled else "account.enabled",
            user_id=user_id,
            ts=now,
        )


def authenticate(username: str, password: str, *, db_path: str | None = None) -> dict | None:
    """
    Verify a username/password pair. Returns the account row, or None.

    Returns None for every failure mode -- unknown user, wrong password,
    disabled account -- and performs a dummy hash verification when the user
    does not exist, so that "no such account" and "wrong password" take
    comparable time and do not become a user-enumeration oracle.
    """
    with platform_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM platform_accounts WHERE username = ? COLLATE NOCASE",
            ((username or "").strip(),),
        ).fetchone()

    if row is None:
        verify_password(password or "", hash_password("dummy-account-does-not-exist"))
        return None
    if row["disabled_at"] is not None:
        verify_password(password or "", row["password_hash"])
        return None
    if not verify_password(password or "", row["password_hash"]):
        return None
    return dict(row)


def generate_password(length: int = 24) -> str:
    """A provisioning-time password for an operator to hand to a participant."""
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


__all__ = [
    "AccountError",
    "authenticate",
    "create_account",
    "generate_password",
    "get_account",
    "hash_password",
    "list_accounts",
    "set_account_disabled",
    "verify_password",
]
