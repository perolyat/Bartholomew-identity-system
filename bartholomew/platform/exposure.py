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


def assert_exposure_is_safe() -> None:
    """
    Validate the whole exposure posture. Call once at startup, before serving.

    Raises `ExposureConfigurationError` on any unsafe combination. Calling it
    early means an unsafe deployment fails at launch rather than on the first
    request -- by which point it is already listening.
    """
    resolve_auth_mode()
    resolve_tls_material()


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
