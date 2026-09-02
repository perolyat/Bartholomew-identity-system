"""How a Windows action companion is configured. Environment only, and explicit.

Every value an operator must supply is named here, and the three allowlists are
required rather than optional: a companion with no application allowlist can
launch nothing, a companion with no URL allowlist can open nothing, and a
companion with no filesystem-root allowlist can open no path. Those are the
correct behaviours, not degraded ones, and they are what a misconfigured
install gets -- never "unrestricted".

Nothing here is discovered, defaulted from the machine, or inherited from the
observation companion. In particular `BARTH_COMPANION_*` is a different prefix
belonging to a different process with a different trust channel; this package
reads only `BARTH_ACTION_*` and shares no configuration with it. That is the
structural separation showing up in the environment as well as in the code.

Credentials are carried, never invented: the companion sends whatever headers
the deployment's operator configures, so whichever device resolver the
deployment installed can verify it. With no resolver installed -- the
repository default -- the action channel refuses with 401 and nothing is ever
dispatched. That is correct, and this package does not work around it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from bartholomew.actuation.allowlists import (
    AllowlistError,
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import ALL_CAPABILITIES, CapabilityKind, parse_kind

#: Prefix for every environment variable this package reads. Deliberately not
#: `BARTH_COMPANION_`: that belongs to the observation-only companion, and the
#: two processes must not be configurable into each other.
ENV_PREFIX = "BARTH_ACTION_"

#: Version stamped on the action channel's handshake. A build marker.
ACTION_COMPANION_VERSION = "0.1.0-prototype"

DEFAULT_POLL_SECONDS = 10.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LEASE_BATCH = 5


class ConfigError(ValueError):
    """The action companion cannot start with the configuration it was given."""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(f"{ENV_PREFIX}{name}")
    return value.strip() if value and value.strip() else default


def _flag(name: str, *, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _credential_headers() -> dict[str, str]:
    """Parse `BARTH_ACTION_CREDENTIAL_HEADERS` -- `Name: value` per line.

    Values are carried, never logged and never interpreted. A malformed line is
    an error rather than a skipped line: a companion that silently dropped its
    credential header would fail as a mysterious 401.
    """
    raw = _env("CREDENTIAL_HEADERS")
    if not raw:
        return {}
    headers: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if ":" not in line:
            raise ConfigError(
                f"{ENV_PREFIX}CREDENTIAL_HEADERS lines must be 'Header-Name: value'",
            )
        name, _, value = line.partition(":")
        if not name.strip():
            raise ConfigError(
                f"{ENV_PREFIX}CREDENTIAL_HEADERS has a line with no header name",
            )
        headers[name.strip()] = value.strip()
    return headers


def _application_allowlist() -> ApplicationAllowlist:
    """`BARTH_ACTION_APP_ALLOWLIST` -- `key=C:\\absolute\\path.exe` per line.

    A key is what a request names; the path is what gets started. A request
    cannot name a path, which is what makes "no arbitrary executable" a
    property of the wire format rather than of a check.
    """
    raw = _env("APP_ALLOWLIST")
    if not raw:
        return ApplicationAllowlist.from_pairs({})
    pairs: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(
                f"{ENV_PREFIX}APP_ALLOWLIST lines must be 'key=C:\\\\path\\\\to\\\\app.exe'",
            )
        key, _, path = line.partition("=")
        pairs[key.strip()] = path.strip()
    try:
        return ApplicationAllowlist.from_pairs(pairs)
    except AllowlistError as e:
        raise ConfigError(f"{ENV_PREFIX}APP_ALLOWLIST: {e}") from e


def _url_allowlist() -> UrlDomainAllowlist:
    raw = _env("URL_ALLOWLIST") or ""
    try:
        return UrlDomainAllowlist.from_iterable(
            part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()
        )
    except AllowlistError as e:
        raise ConfigError(f"{ENV_PREFIX}URL_ALLOWLIST: {e}") from e


def _path_allowlist() -> FilesystemRootAllowlist:
    raw = _env("PATH_ALLOWLIST") or ""
    entries = [
        part.strip() for part in raw.replace("\n", os.pathsep).split(os.pathsep) if part.strip()
    ]
    try:
        return FilesystemRootAllowlist.from_iterable(entries)
    except AllowlistError as e:
        raise ConfigError(f"{ENV_PREFIX}PATH_ALLOWLIST: {e}") from e


def _capabilities() -> tuple[CapabilityKind, ...]:
    """`BARTH_ACTION_CAPABILITIES` -- which capabilities this install offers.

    Absent means **none**. An action companion that has not been told what it
    may do may do nothing; it still runs, still authenticates and still reports
    honestly that it supports nothing, which is a legible state rather than a
    silent one.
    """
    raw = _env("CAPABILITIES")
    if not raw:
        return ()
    if raw.strip().lower() == "all":
        return tuple(ALL_CAPABILITIES)
    try:
        return tuple(
            parse_kind(part.strip()) for part in raw.replace("\n", ",").split(",") if part.strip()
        )
    except Exception as e:
        raise ConfigError(f"{ENV_PREFIX}CAPABILITIES: {e}") from e


@dataclass(frozen=True)
class ActionCompanionConfig:
    """Everything the action companion needs, resolved once at start."""

    base_url: str
    device_id: str
    state_path: Path
    applications: ApplicationAllowlist
    url_domains: UrlDomainAllowlist
    filesystem_roots: FilesystemRootAllowlist
    capabilities: tuple[CapabilityKind, ...]
    poll_seconds: float = DEFAULT_POLL_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    lease_batch: int = DEFAULT_LEASE_BATCH
    credential_headers: dict[str, str] = field(default_factory=dict)
    #: Whether a `windows.clipboard_read` result may carry the clipboard's
    #: text back to Bartholomew at all. **Off by default**: with it off the
    #: content never leaves this machine and the result carries a digest, a
    #: length and whether the secret detector fired. Turning it on is an
    #: explicit operator decision recorded in the environment.
    clipboard_return_content: bool = False

    def supports(self, kind: CapabilityKind) -> bool:
        return kind in self.capabilities

    def describe(self) -> dict[str, object]:
        """The non-sensitive summary the diagnostics command prints.

        Credential header *values* are never included -- only the header names,
        so an operator can see that a credential is configured without the
        diagnostics output becoming somewhere the credential lives.
        """
        return {
            "base_url": self.base_url,
            "device_id": self.device_id,
            "state_path": str(self.state_path),
            "capabilities": [k.value for k in self.capabilities],
            "application_keys": list(self.applications.keys),
            "url_domains": sorted(self.url_domains.hosts),
            "filesystem_roots": list(self.filesystem_roots.roots),
            "credential_header_names": sorted(self.credential_headers),
            "clipboard_return_content": self.clipboard_return_content,
            "poll_seconds": self.poll_seconds,
            "version": ACTION_COMPANION_VERSION,
        }

    def enrolment_template(self) -> str:
        """The enrolment record an operator pastes into the server's registry.

        Generated from this machine's actual configuration so the two copies of
        the allowlists agree. They are still both enforced independently -- see
        `bartholomew/actuation/parameters.py` -- so a mismatch narrows what is
        possible rather than widening it.
        """
        return json.dumps(
            {
                "devices": [
                    {
                        "device_id": self.device_id,
                        "tenant_id": "<the tenant this device belongs to>",
                        "platform": "windows",
                        "enrolled": True,
                        "capabilities": [k.value for k in self.capabilities],
                        "applications": dict(self.applications.entries),
                        "url_domains": sorted(self.url_domains.hosts),
                        "filesystem_roots": list(self.filesystem_roots.roots),
                        "trusted_autonomy": [],
                    },
                ],
            },
            indent=2,
        )


def load_config() -> ActionCompanionConfig:
    """Build the configuration, or fail loudly before anything is dispatched."""
    base_url = _env("BASE_URL")
    if not base_url:
        raise ConfigError(f"{ENV_PREFIX}BASE_URL is required (e.g. https://127.0.0.1:5173)")
    if not base_url.lower().startswith(("http://", "https://")):
        raise ConfigError(f"{ENV_PREFIX}BASE_URL must be an http or https URL")

    device_id = _env("DEVICE_ID")
    if not device_id:
        raise ConfigError(
            f"{ENV_PREFIX}DEVICE_ID is required, and must be the device id this "
            "machine is enrolled under on the Bartholomew side. A mismatch produces "
            "a refusal and no action, which is a visible failure rather than a "
            "misdirected one.",
        )

    state_path = Path(
        _env("STATE_PATH") or Path.home() / ".bartholomew" / "action-state.json",
    )
    try:
        poll_seconds = float(_env("POLL_SECONDS") or DEFAULT_POLL_SECONDS)
        max_attempts = int(_env("MAX_ATTEMPTS") or DEFAULT_MAX_ATTEMPTS)
        lease_batch = int(_env("LEASE_BATCH") or DEFAULT_LEASE_BATCH)
    except ValueError as e:
        raise ConfigError(f"Invalid numeric action-companion setting: {e}") from e
    if poll_seconds <= 0:
        raise ConfigError(f"{ENV_PREFIX}POLL_SECONDS must be positive")
    if max_attempts < 1:
        raise ConfigError(f"{ENV_PREFIX}MAX_ATTEMPTS must be at least 1")
    if not (1 <= lease_batch <= 20):
        raise ConfigError(f"{ENV_PREFIX}LEASE_BATCH must be between 1 and 20")

    return ActionCompanionConfig(
        base_url=base_url,
        device_id=device_id,
        state_path=state_path,
        applications=_application_allowlist(),
        url_domains=_url_allowlist(),
        filesystem_roots=_path_allowlist(),
        capabilities=_capabilities(),
        poll_seconds=poll_seconds,
        max_attempts=max_attempts,
        lease_batch=lease_batch,
        credential_headers=_credential_headers(),
        clipboard_return_content=_flag("CLIPBOARD_RETURN_CONTENT", default=False),
    )
