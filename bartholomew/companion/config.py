"""How a companion is configured. Environment only, and nothing derived.

Every value an operator must supply is named here. There is no discovery, no
registry, no provider table and no auto-configuration: this is one companion
talking to one Bartholomew, and a mechanism for finding others would be a
platform, which this is not.

`source_id` is not a free choice. It must be the source id the deployment's
inbound resolver actually issues to this companion, because the route compares
the two and refuses on a mismatch. Getting it wrong produces a 403 and no
capture -- a visible failure, not a silent misattribution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Prefix for every environment variable this package reads.
ENV_PREFIX = "BARTH_COMPANION_"

#: Version stamped into the `system_state` observation. Bumped by hand; this is
#: a prototype's build marker, not a negotiated protocol version.
COMPANION_VERSION = "0.1.0-prototype"

DEFAULT_POLL_SECONDS = 15.0
DEFAULT_MAX_ATTEMPTS = 5


class ConfigError(ValueError):
    """The companion cannot start with the configuration it was given."""


@dataclass(frozen=True)
class CompanionConfig:
    """Everything the companion needs, resolved once at start."""

    base_url: str
    source_id: str
    device_id: str
    state_path: Path
    poll_seconds: float = DEFAULT_POLL_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    #: Headers carried verbatim so the deployment's resolver can verify the
    #: source. The companion defines no scheme of its own; see `client.py`.
    credential_headers: dict[str, str] = field(default_factory=dict)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(f"{ENV_PREFIX}{name}")
    return value.strip() if value and value.strip() else default


def _credential_headers() -> dict[str, str]:
    """Parse `BARTH_COMPANION_CREDENTIAL_HEADERS` -- `Name: value` per line.

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
            raise ConfigError(f"{ENV_PREFIX}CREDENTIAL_HEADERS has a line with no header name")
        headers[name.strip()] = value.strip()
    return headers


def load_config() -> CompanionConfig:
    """Build the configuration, or fail loudly before anything is observed."""
    base_url = _env("BASE_URL")
    if not base_url:
        raise ConfigError(f"{ENV_PREFIX}BASE_URL is required (e.g. https://127.0.0.1:8765)")
    source_id = _env("SOURCE_ID")
    if not source_id:
        raise ConfigError(
            f"{ENV_PREFIX}SOURCE_ID is required, and must be the source id this "
            "deployment's inbound resolver issues to this companion.",
        )
    device_id = _env("DEVICE_ID")
    if not device_id:
        raise ConfigError(
            f"{ENV_PREFIX}DEVICE_ID is required. Choose a label for this machine "
            "(e.g. 'desk-pc'); it is recorded as claimed, unauthenticated provenance, "
            "so a personal name adds nothing but exposure.",
        )

    state_path = Path(
        _env("STATE_PATH") or Path.home() / ".bartholomew" / "companion-state.json",
    )
    try:
        poll_seconds = float(_env("POLL_SECONDS") or DEFAULT_POLL_SECONDS)
        max_attempts = int(_env("MAX_ATTEMPTS") or DEFAULT_MAX_ATTEMPTS)
    except ValueError as e:
        raise ConfigError(f"Invalid numeric companion setting: {e}") from e
    if poll_seconds <= 0:
        raise ConfigError(f"{ENV_PREFIX}POLL_SECONDS must be positive")
    if max_attempts < 1:
        raise ConfigError(f"{ENV_PREFIX}MAX_ATTEMPTS must be at least 1")

    return CompanionConfig(
        base_url=base_url,
        source_id=source_id,
        device_id=device_id,
        state_path=state_path,
        poll_seconds=poll_seconds,
        max_attempts=max_attempts,
        credential_headers=_credential_headers(),
    )
