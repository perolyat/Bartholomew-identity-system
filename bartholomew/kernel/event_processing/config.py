"""Settings for the event-processing backbone, resolved in one place.

Three sources, in increasing precedence: built-in defaults, the
`event_processing:` block in `config/kernel.yaml`, and environment variables.

One asymmetry is deliberate and is the whole point of `enabled`:

* **The config file is the only authority that can turn processing ON.**
* **The environment variable can only turn it OFF.**

That is not a second consent authority (the shape
`drives.proactive_schedule_reminders_enabled()` deliberately refuses); it is
a kill switch. Processing captured events is not an outbound capability -- it
sends nothing, contacts nobody, and can only attach evidence to an objective
the user already opened -- so it defaults ON, and the thing an operator needs
in a hurry is a way to stop it without editing a file. Turning it back on
still requires the file.

Numeric settings take environment overrides freely: batch size, lease length
and retry bounds are operational tuning, not consent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

#: `config/kernel.yaml` block this reads.
CONFIG_SECTION = "event_processing"

#: Kill switch. Only ever turns processing off; a truthy value here cannot
#: turn it on when the config file has it disabled.
ENV_ENABLED = "BARTH_EVENT_PROCESSING_ENABLED"

ENV_BATCH_LIMIT = "BARTH_EVENT_PROCESSING_BATCH"
ENV_SWEEP_LIMIT = "BARTH_EVENT_PROCESSING_SWEEP"
ENV_LEASE_SECONDS = "BARTH_EVENT_PROCESSING_LEASE_S"
ENV_MAX_ATTEMPTS = "BARTH_EVENT_PROCESSING_MAX_ATTEMPTS"
ENV_BACKLOG_MAX = "BARTH_EVENT_PROCESSING_BACKLOG_MAX"
ENV_DEADLINE_SECONDS = "BARTH_EVENT_PROCESSING_DEADLINE_S"

#: Events claimed per drive tick. Small on purpose: one tick runs inside the
#: scheduler's `BARTH_DRIVE_TIMEOUT` budget (5s by default), and a batch that
#: cannot finish inside it leaves leases to expire rather than doing useful
#: work. Raising this means raising that timeout too.
DEFAULT_BATCH_LIMIT = 5

#: Captured rows swept into the processing table per tick. Larger than the
#: batch limit so a backlog inherited from an upgrade drains steadily even
#: while processing is slower than capture.
DEFAULT_SWEEP_LIMIT = 200

#: How long a claim is held before another pass may take the event back.
#: Comfortably longer than one bounded batch, so a live worker is never
#: raced by the recovery path; short enough that a killed process's work is
#: picked up within a couple of minutes.
DEFAULT_LEASE_SECONDS = 120

#: Attempts before an event is quarantined. Three is enough to ride out a
#: transient fault and few enough that a genuinely poisonous event stops
#: consuming the batch quickly.
DEFAULT_MAX_ATTEMPTS = 3

#: Non-terminal events allowed to exist before capture pushes back. A door
#: that keeps accepting into a queue nothing is draining is how a disk fills
#: silently; refusing with a retryable 503 is the honest alternative.
DEFAULT_BACKLOG_MAX = 1000

#: Wall-clock budget for one batch. Anything still claimed when this passes
#: is released with its attempt refunded, so a slow batch costs latency and
#: never a retry.
DEFAULT_DEADLINE_SECONDS = 3.0


class EventProcessingConfigError(ValueError):
    """A configured value is not usable. Raised rather than silently defaulted:
    an operator who set a limit deserves to know it was rejected."""


@dataclass(frozen=True)
class EventProcessingSettings:
    """Resolved settings for one process."""

    enabled: bool = True
    batch_limit: int = DEFAULT_BATCH_LIMIT
    sweep_limit: int = DEFAULT_SWEEP_LIMIT
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backlog_max: int = DEFAULT_BACKLOG_MAX
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "batch_limit": self.batch_limit,
            "sweep_limit": self.sweep_limit,
            "lease_seconds": self.lease_seconds,
            "max_attempts": self.max_attempts,
            "backlog_max": self.backlog_max,
            "deadline_seconds": self.deadline_seconds,
        }


def _truthy(raw: str | None) -> bool | None:
    """Tri-state: True, False, or None for "not set / not a boolean"."""
    if raw is None:
        return None
    text = raw.strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return None


def _env_or(environ: Any, key: str, fallback: Any) -> Any:
    """The environment value for `key`, or `fallback` when it is absent or blank.

    An empty environment variable is treated as unset rather than as the
    number zero: `FOO=` in a shell script is how a variable gets accidentally
    cleared, and reading it as a configured value would turn a typo into a
    rejected startup.
    """
    raw = environ.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return fallback
    return raw


def _positive_int(raw: Any, *, name: str, default: int, minimum: int = 1) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as e:
        raise EventProcessingConfigError(f"{name} must be an integer, got {raw!r}") from e
    if value < minimum:
        raise EventProcessingConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _positive_float(raw: Any, *, name: str, default: float, minimum: float = 0.1) -> float:
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as e:
        raise EventProcessingConfigError(f"{name} must be a number, got {raw!r}") from e
    if value < minimum:
        raise EventProcessingConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def resolve_settings(
    cfg: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> EventProcessingSettings:
    """Defaults, then `config/kernel.yaml`, then the environment.

    `cfg` is the whole loaded kernel config (a `KernelDaemon.cfg`), not the
    section -- callers pass what they already have rather than reaching into
    it and getting the key wrong.
    """
    environ = os.environ if env is None else env
    section: dict[str, Any] = {}
    if isinstance(cfg, dict):
        raw_section = cfg.get(CONFIG_SECTION)
        if isinstance(raw_section, dict):
            section = raw_section

    enabled = bool(section.get("enabled", True))
    # Disable-only: an env value of "1" on a config-disabled deployment
    # changes nothing. See the module docstring.
    if _truthy(environ.get(ENV_ENABLED)) is False:
        enabled = False

    return EventProcessingSettings(
        enabled=enabled,
        batch_limit=_positive_int(
            _env_or(environ, ENV_BATCH_LIMIT, section.get("batch_limit")),
            name="event_processing.batch_limit",
            default=DEFAULT_BATCH_LIMIT,
        ),
        sweep_limit=_positive_int(
            _env_or(environ, ENV_SWEEP_LIMIT, section.get("sweep_limit")),
            name="event_processing.sweep_limit",
            default=DEFAULT_SWEEP_LIMIT,
        ),
        lease_seconds=_positive_int(
            _env_or(environ, ENV_LEASE_SECONDS, section.get("lease_seconds")),
            name="event_processing.lease_seconds",
            default=DEFAULT_LEASE_SECONDS,
        ),
        max_attempts=_positive_int(
            _env_or(environ, ENV_MAX_ATTEMPTS, section.get("max_attempts")),
            name="event_processing.max_attempts",
            default=DEFAULT_MAX_ATTEMPTS,
        ),
        backlog_max=_positive_int(
            _env_or(environ, ENV_BACKLOG_MAX, section.get("backlog_max")),
            name="event_processing.backlog_max",
            default=DEFAULT_BACKLOG_MAX,
        ),
        deadline_seconds=_positive_float(
            _env_or(environ, ENV_DEADLINE_SECONDS, section.get("deadline_seconds")),
            name="event_processing.deadline_seconds",
            default=DEFAULT_DEADLINE_SECONDS,
        ),
    )


__all__ = [
    "CONFIG_SECTION",
    "DEFAULT_BACKLOG_MAX",
    "DEFAULT_BATCH_LIMIT",
    "DEFAULT_DEADLINE_SECONDS",
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_SWEEP_LIMIT",
    "ENV_ENABLED",
    "EventProcessingConfigError",
    "EventProcessingSettings",
    "resolve_settings",
]
