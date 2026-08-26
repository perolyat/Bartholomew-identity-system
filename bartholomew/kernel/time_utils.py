"""
Time helpers.

`configured_timezone()` is the answer to "what timezone is Bartholomew in"
for code that has no kernel context to take it from. `KernelDaemon` builds
its own `self.tz` from the same `config/kernel.yaml` key and passes it down
where it can; this reads that same key for callers it cannot reach. It is the
same source of truth, not a competing one.

The fallback is UTC, never the host's local time. That is deliberate, and is
the same rule `bartholomew/kernel/scheduler/drives.py::_today_for()` states
for itself: behaviour must never depend on an unstated machine setting. A
server whose clock is in UTC while the user lives in Australia/Brisbane is
ordinary, and a component that silently used the host's zone would be ten
hours out without anything looking wrong.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, tzinfo

try:  # pragma: no cover - exercised implicitly; absence is handled below
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

try:  # pragma: no cover
    from dateutil import tz as _dateutil_tz
except ImportError:  # pragma: no cover
    _dateutil_tz = None

DEFAULT_TIMEZONE = timezone.utc

_CONFIG_CANDIDATES = (
    os.path.join("config", "kernel.yaml"),
    os.path.join(os.path.dirname(__file__), "..", "..", "config", "kernel.yaml"),
)

_cached_tz: tzinfo | None = None
_cached_name: str | None = None


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string, seconds precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _find_config() -> str | None:
    for candidate in _CONFIG_CANDIDATES:
        path = os.path.abspath(candidate)
        if os.path.exists(path):
            return path
    return None


def configured_timezone_name() -> str | None:
    """The `timezone` value in `config/kernel.yaml`, or None if unavailable."""
    if yaml is None:
        return None
    path = _find_config()
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle) or {}
    except (OSError, ValueError):
        return None
    name = cfg.get("timezone")
    return name if isinstance(name, str) and name.strip() else None


def configured_timezone() -> tzinfo:
    """
    Bartholomew's configured timezone, falling back to UTC.

    Never falls back to the host's local zone: an unreadable config must
    produce a stated, predictable answer rather than one that silently varies
    with the machine.
    """
    global _cached_tz, _cached_name  # noqa: PLW0603 - module-level memo

    name = configured_timezone_name()
    if name is not None and name == _cached_name and _cached_tz is not None:
        return _cached_tz

    resolved: tzinfo | None = None
    if name and _dateutil_tz is not None:
        resolved = _dateutil_tz.gettz(name)

    _cached_name = name
    _cached_tz = resolved or DEFAULT_TIMEZONE
    return _cached_tz


def now_in_configured_timezone() -> datetime:
    """Current time as an aware datetime in Bartholomew's configured zone."""
    return datetime.now(tz=configured_timezone())


def reset_timezone_cache() -> None:
    """Drop the memoised zone. For tests, and for a config change at runtime."""
    global _cached_tz, _cached_name  # noqa: PLW0603 - module-level memo
    _cached_tz = None
    _cached_name = None
