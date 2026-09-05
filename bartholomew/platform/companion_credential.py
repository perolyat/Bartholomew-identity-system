"""Where the Windows companion keeps its device credential.

Session E issues a long-lived device credential once, at the end of enrolment,
and never shows it again -- the server keeps only a digest. That leaves the
companion holding the one copy, which has to live somewhere between runs. This
module is that somewhere, and it is deliberately thin: it stores and retrieves
one secret, and it does not decide anything.

OS-protected, not file-protected
--------------------------------
Storage is `keyring`, which the project already depends on and already uses
for the memory keyring namespace. On Windows its backend is the Credential
Manager, which protects the blob with DPAPI under the logged-in user's
profile -- so the credential is readable by that user on that machine and is
not sitting in a dotfile that a backup, a screen-share or a sync client would
carry off. No new dependency, no new key material, no new format.

**A plaintext credential is never written to disk by this module**, and the
server never stores one at all: `platform_device_credentials` holds a digest,
which is why a lost companion credential is re-issued by rotation rather than
recovered.

The environment-variable fallback is for a headless run
--------------------------------------------------------
`BARTH_COMPANION_CREDENTIAL` is read when no keyring entry exists, so a
container or a CI harness can run the companion without a desktop keyring. It
is a deliberate downgrade and is reported as one by `describe()`: an
environment variable is visible to anything that can read the process's
environment, which the Credential Manager entry is not.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

#: Keyring service name. Namespaced per device so one machine enrolled twice
#: (a re-enrolment after revocation) does not overwrite the wrong entry.
KEYRING_SERVICE = "bartholomew_companion"

#: Read when no keyring entry exists. See the module docstring: a downgrade,
#: and reported as one.
CREDENTIAL_ENV = "BARTH_COMPANION_CREDENTIAL"

#: Names the device the stored credential belongs to, for the same reason.
DEVICE_ID_ENV = "BARTH_COMPANION_DEVICE_ID"


class CredentialStoreError(RuntimeError):
    """The credential could not be stored. Never swallowed: see `store()`."""


def _keyring():
    try:
        import keyring  # noqa: PLC0415 - optional backend, probed at call time
    except Exception:  # pragma: no cover - keyring is a declared dependency
        return None
    return keyring


def store(*, device_id: str, secret: str) -> None:
    """Persist one device credential under the OS's protected store.

    Raises rather than falling back to a file. A companion that believes its
    credential is protected when it is sitting in plaintext somewhere is worse
    off than one that knows the store failed.
    """
    device = str(device_id or "").strip()
    if not device or not str(secret or "").strip():
        raise CredentialStoreError("a companion credential needs both a device id and a secret")

    kr = _keyring()
    if kr is None:
        raise CredentialStoreError(
            "no keyring backend is available; refusing to store the device "
            "credential in plaintext. Install a keyring backend, or supply the "
            f"credential through {CREDENTIAL_ENV} for this run only.",
        )
    try:
        kr.set_password(KEYRING_SERVICE, device, secret)
    except Exception as e:  # noqa: BLE001 - reported, never silently downgraded
        raise CredentialStoreError(
            f"the device credential could not be stored ({type(e).__name__}: {e}); "
            "nothing was written.",
        ) from e
    logger.info("Stored the companion device credential for %s in the OS keyring", device)


def load(*, device_id: str | None = None) -> tuple[str, str] | None:
    """`(device_id, secret)` for this machine's companion, or None.

    Keyring first, environment second. Returns None rather than raising when
    nothing is configured: "this machine has no companion credential" is a
    normal state, and the caller turns it into a refusal with its own wording.
    """
    device = str(device_id or os.getenv(DEVICE_ID_ENV) or "").strip()

    if device:
        kr = _keyring()
        if kr is not None:
            try:
                secret = kr.get_password(KEYRING_SERVICE, device)
            except Exception:  # noqa: BLE001 - an unreadable store is "not configured"
                logger.warning("The OS keyring could not be read for device %s", device)
                secret = None
            if secret:
                return device, secret

    env_secret = (os.getenv(CREDENTIAL_ENV) or "").strip()
    if device and env_secret:
        logger.warning(
            "Using the companion credential from %s; it is visible to anything that "
            "can read this process's environment, unlike the OS keyring entry.",
            CREDENTIAL_ENV,
        )
        return device, env_secret
    return None


def forget(*, device_id: str) -> bool:
    """Remove this device's stored credential. True if one was there."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        if kr.get_password(KEYRING_SERVICE, device_id) is None:
            return False
        kr.delete_password(KEYRING_SERVICE, device_id)
    except Exception:  # noqa: BLE001 - nothing to remove is not a failure
        return False
    return True


def describe(*, device_id: str | None = None) -> dict[str, Any]:
    """Where this machine's credential is kept, without revealing it.

    Never returns the secret, or any part of it. An operator needs to know
    *whether* a credential is configured and *how well protected* it is; the
    value itself is the one thing a diagnostic surface must not print.
    """
    device = str(device_id or os.getenv(DEVICE_ID_ENV) or "").strip()
    kr = _keyring()
    backend = None
    if kr is not None:
        try:
            backend = type(kr.get_keyring()).__name__
        except Exception:  # noqa: BLE001
            backend = None

    in_keyring = False
    if device and kr is not None:
        try:
            in_keyring = kr.get_password(KEYRING_SERVICE, device) is not None
        except Exception:  # noqa: BLE001
            in_keyring = False

    from_env = bool((os.getenv(CREDENTIAL_ENV) or "").strip())
    return {
        "device_id": device or None,
        "configured": bool(in_keyring or (device and from_env)),
        "source": ("os_keyring" if in_keyring else ("environment" if from_env else None)),
        "keyring_backend": backend,
        "protection": (
            "OS keyring (Windows Credential Manager / DPAPI on Windows)"
            if in_keyring
            else (
                "environment variable -- visible to anything that can read this "
                "process's environment"
                if from_env
                else "not configured"
            )
        ),
    }


__all__ = [
    "CREDENTIAL_ENV",
    "DEVICE_ID_ENV",
    "KEYRING_SERVICE",
    "CredentialStoreError",
    "describe",
    "forget",
    "load",
    "store",
]
