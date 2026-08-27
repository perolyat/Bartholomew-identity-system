"""
Where Bartholomew is reachable from, and whether authentication is enforced.

These two questions are answered together, in one module, because the whole
S8 failure mode is answering them separately and getting an unauthenticated
service on a routable interface.

The rule, stated once:

    **A non-loopback bind forces authentication on and TLS on, and neither
    can be turned off while it is in effect.**

`BARTH_AUTH_MODE=disabled` exists for the current single-user localhost
development deployment -- the mode this repository has always run in, and the
mode its ~135 existing test files exercise. It is not a bypass that could
survive into Alpha, because Alpha is a hosted, non-loopback deployment
(approved decision, 2026-08-27) and this module **refuses to start the
process** if `disabled` is combined with a non-loopback bind. Development
convenience is explicit, and structurally incapable of enabling itself in a
production-like deployment, which is what the fail-closed requirement asks
for.

Nothing here is a substitute for the network boundary in the API bridge; it
sits alongside it. Locality bounds *who can reach the port*; authentication
answers *who is asking*. Alpha needs both.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

from .accounts import get_account
from .runtime_registry import (
    RUNTIME_USER_ID_ENV,
    RuntimeResolutionError,
    bound_runtime_user_id,
    runtime_handle_for_user_id,
)

AUTH_MODE_ENV = "BARTH_AUTH_MODE"
ALLOW_NON_LOOPBACK_ENV = "BARTH_API_ALLOW_NON_LOOPBACK"
TLS_CERT_ENV = "BARTH_API_TLS_CERTFILE"
TLS_KEY_ENV = "BARTH_API_TLS_KEYFILE"


class AuthMode(str, Enum):
    ENFORCED = "enforced"
    DISABLED = "disabled"


class ExposureConfigurationError(RuntimeError):
    """
    The requested combination of exposure and authentication is unsafe.

    Raised at startup, deliberately: a misconfiguration that would expose
    personal memory must stop the process, not log a warning that scrolls
    past. There is no environment variable that downgrades this to a warning.
    """


def _is_truthy(val: str | None) -> bool:
    return bool(val) and val.strip().lower() in ("1", "true", "yes", "on")


def non_loopback_enabled() -> bool:
    """
    True when a non-loopback bind has been deliberately enabled.

    Reads the same environment variable as the API bridge's own network
    boundary rather than importing it, so this module stays importable
    without pulling in FastAPI -- the CLI and the account tooling need it
    too. The variable, not the function, is the shared contract.
    """
    return _is_truthy(os.getenv(ALLOW_NON_LOOPBACK_ENV))


def resolve_auth_mode() -> AuthMode:
    """
    The effective authentication mode, or raise if the combination is unsafe.

    Resolution order:

    1. An unrecognised `BARTH_AUTH_MODE` value raises. A typo
       (`BARTH_AUTH_MODE=enfoced`) must not silently fall back to any mode,
       least of all the permissive one.
    2. A non-loopback bind forces `ENFORCED`, and an explicit `disabled`
       alongside it raises rather than being quietly upgraded -- the operator
       asked for something unsafe and needs to know their intent was wrong,
       not have it corrected behind their back.
    3. Otherwise the explicit setting wins; absent one, a loopback-only
       deployment defaults to `DISABLED`, preserving the documented
       single-user localhost behaviour.
    """
    raw = os.getenv(AUTH_MODE_ENV)
    requested: AuthMode | None = None
    if raw is not None and raw.strip() != "":
        try:
            requested = AuthMode(raw.strip().lower())
        except ValueError as exc:
            raise ExposureConfigurationError(
                f"{AUTH_MODE_ENV}={raw!r} is not a recognised authentication mode. "
                f"Valid values: {[m.value for m in AuthMode]}.",
            ) from exc

    if non_loopback_enabled():
        if requested is AuthMode.DISABLED:
            raise ExposureConfigurationError(
                f"{AUTH_MODE_ENV}=disabled cannot be combined with "
                f"{ALLOW_NON_LOOPBACK_ENV}=1. A non-loopback bind exposes personal "
                f"memory and the Parking Brake to anything that can reach the port; "
                f"authentication is not optional there.",
            )
        return AuthMode.ENFORCED

    return requested if requested is not None else AuthMode.DISABLED


def auth_enforced() -> bool:
    return resolve_auth_mode() is AuthMode.ENFORCED


def resolve_tls_material() -> tuple[str, str] | None:
    """
    The TLS certificate/key pair, or None when TLS is not required.

    Required whenever the bind is non-loopback. There is deliberately **no
    "TLS is terminated by a proxy" opt-out**: no trusted proxy is part of
    this architecture (the request boundary ignores `X-Forwarded-*` for the
    same reason), and an opt-out flag would be indistinguishable, from
    inside this process, from a plaintext deployment. If a terminating load
    balancer is ever genuinely wanted, it needs its own decision about how
    this process authenticates the hop -- not an environment variable.
    """
    if not non_loopback_enabled():
        return None

    cert = (os.getenv(TLS_CERT_ENV) or "").strip()
    key = (os.getenv(TLS_KEY_ENV) or "").strip()
    if not cert or not key:
        raise ExposureConfigurationError(
            f"A non-loopback bind requires TLS. Set {TLS_CERT_ENV} and {TLS_KEY_ENV} "
            f"to a certificate and private key. Session credentials are bearer "
            f"tokens: without TLS they are readable by anything on the path.",
        )
    for label, path in (("certificate", cert), ("private key", key)):
        if not Path(path).is_file():
            raise ExposureConfigurationError(
                f"TLS {label} {path!r} does not exist or is not a file.",
            )
    return cert, key


def uvicorn_tls_kwargs() -> dict:
    """
    TLS keyword arguments for `uvicorn.run()`, or `{}` when TLS is not required.

    Validating that certificate files *exist* is not the same as serving TLS,
    and the gap between the two was a real hole: a deployment could pass every
    exposure check and still speak plaintext, because nothing handed the
    material to the socket. This is the function that closes it, and every
    supported launch path must use it.
    """
    material = resolve_tls_material()
    if material is None:
        return {}
    cert, key = material
    return {"ssl_certfile": cert, "ssl_keyfile": key}


def require_bound_runtime_user() -> str:
    """
    The `user_id` this process serves, required whenever it is exposed.

    **There is no unbound remote personal-runtime mode.** An exposed process
    that does not know whose Bartholomew it is serving would hand every
    authenticated account the one global kernel it happens to be running --
    which is cross-user disclosure, not a configuration gap. So a non-loopback
    deployment must name its user, and the identity/persistence agreement is
    then verified rather than assumed.

    Returns the bound `user_id`. Raises `ExposureConfigurationError` if it is
    missing, malformed, names no account, or disagrees with the database and
    keyring namespace this process is actually configured to use.
    """
    user_id = bound_runtime_user_id()
    if not user_id:
        raise ExposureConfigurationError(
            f"An exposed deployment must name the personal Bartholomew it "
            f"serves. Set {RUNTIME_USER_ID_ENV} to a provisioned user_id "
            f"(see `bartholomew accounts list`). There is no unbound remote "
            f"mode: without it, every authenticated account would reach one "
            f"shared kernel.",
        )

    try:
        handle = runtime_handle_for_user_id(user_id)
    except RuntimeResolutionError as exc:
        raise ExposureConfigurationError(
            f"{RUNTIME_USER_ID_ENV}={user_id!r} is not a usable identifier: {exc}",
        ) from exc

    account = get_account(user_id)
    if account is None:
        raise ExposureConfigurationError(
            f"{RUNTIME_USER_ID_ENV}={user_id!r} names no provisioned account. "
            f"Provision one with `bartholomew accounts create`.",
        )
    if account["disabled_at"] is not None:
        raise ExposureConfigurationError(
            f"{RUNTIME_USER_ID_ENV}={user_id!r} names a disabled account.",
        )
    if account["kind"] != "user":
        raise ExposureConfigurationError(
            f"{RUNTIME_USER_ID_ENV}={user_id!r} names a {account['kind']}, which "
            f"has no personal Bartholomew runtime.",
        )

    # The agreement that actually matters: the database and keyring namespace
    # this process will use must be the bound user's own. A process bound to
    # one user while pointed at another's database would authenticate
    # correctly and serve the wrong person's memory.
    active_db = (os.getenv("BARTH_DB_PATH") or "").strip()
    if not active_db:
        raise ExposureConfigurationError(
            f"An exposed deployment must set BARTH_DB_PATH explicitly, to the "
            f"bound user's database ({handle.db_path}).",
        )
    if Path(active_db).resolve() != Path(handle.db_path).resolve():
        raise ExposureConfigurationError(
            f"BARTH_DB_PATH={active_db!r} is not the database of the bound user "
            f"{user_id!r} (expected {handle.db_path!r}). Refusing to serve one "
            f"identity from another identity's persistence.",
        )

    active_keyring = (os.getenv("BARTHO_MEMORY_KEYRING_SERVICE") or "").strip()
    if active_keyring != handle.keyring_service:
        raise ExposureConfigurationError(
            f"BARTHO_MEMORY_KEYRING_SERVICE={active_keyring!r} is not the bound "
            f"user's keyring namespace (expected {handle.keyring_service!r}). "
            f"Refusing to serve one identity's memory under another's key.",
        )
    return user_id


def platform_tier_active() -> bool:
    """
    True when this deployment has a platform tier at all.

    A single-user loopback development deployment has no platform to halt and
    no administrator distinct from the user, so the Platform/Admin brake is
    simply not part of it -- and must not be, or an absent control-plane
    database would fail-closed a purely local Bartholomew into uselessness.
    Where the tier *is* active, an unreadable platform state fails closed.
    """
    return non_loopback_enabled() or auth_enforced()


def assert_exposure_is_safe() -> None:
    """
    Validate the whole exposure posture. Call once at startup, before serving.

    raises `ExposureConfigurationError` on any unsafe combination. Calling it
    early means an unsafe deployment fails at launch rather than on the first
    request -- by which point it is already listening.
    """
    resolve_auth_mode()
    resolve_tls_material()
    if non_loopback_enabled():
        # An exposed deployment must know whose Bartholomew it serves, and
        # the database and keyring it is configured to use must agree. There
        # is no unbound remote personal-runtime mode.
        require_bound_runtime_user()


def describe_exposure() -> dict:
    """A structured, secret-free summary for startup logging and diagnostics."""
    try:
        mode = resolve_auth_mode().value
    except ExposureConfigurationError as exc:
        mode = f"invalid: {exc}"
    return {
        "auth_mode": mode,
        "non_loopback_enabled": non_loopback_enabled(),
        "tls_configured": bool(os.getenv(TLS_CERT_ENV) and os.getenv(TLS_KEY_ENV)),
    }
