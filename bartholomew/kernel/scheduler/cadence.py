"""
Cadence parsing and next-run calculation.

Supports four cadence types (S5.2 Typed Cadence -- see
docs/S5_2_TYPED_CADENCE_DESIGN.md):
- every:<seconds> - Run every N seconds
- window:<window_seconds>:<max_runs> - Run K times within window W
- daily:<HH>:<MM> - Run once per wall-clock day at HH:MM, in the caller's tz
- weekly:<D>:<HH>:<MM> - Run once per wall-clock week on day D (0=Monday..
  6=Sunday, Python date.weekday() convention) at HH:MM, in the caller's tz
"""

import json
import os
import random
from datetime import datetime, timedelta
from datetime import timezone as _dt_timezone
from typing import Any

CadenceType = tuple[str, int] | tuple[str, int, int] | tuple[str, int, int, int]


def _speed_factor() -> float:
    """
    Get the speed factor from environment variable.

    Returns speed factor clamped to minimum 0.001.
    Defaults to 1.0 if not set or invalid.
    """
    try:
        return max(0.001, float(os.getenv("BARTH_SPEED_FACTOR", "1.0")))
    except Exception:
        return 1.0


def parse(cadence_str: str) -> CadenceType:
    """
    Parse a cadence string into a structured format.

    Args:
        cadence_str: Cadence string (e.g., "every:900", "window:3600:2")

    Returns:
        For "every:N": ("every", N)
        For "window:W:K": ("window", W, K)

    Raises:
        ValueError: If cadence string is malformed

    Examples:
        >>> parse("every:900")
        ("every", 900)
        >>> parse("window:3600:2")
        ("window", 3600, 2)
        >>> parse("daily:08:00")
        ("daily", 8, 0)
        >>> parse("weekly:0:08:00")
        ("weekly", 0, 8, 0)
    """
    if not cadence_str:
        raise ValueError("Cadence string cannot be empty")

    parts = cadence_str.split(":")

    if parts[0] == "every":
        if len(parts) != 2:
            raise ValueError(f"Invalid 'every' cadence: {cadence_str}")
        try:
            seconds = int(parts[1])
            if seconds <= 0:
                raise ValueError(f"Cadence seconds must be positive: {seconds}")
            return ("every", seconds)
        except ValueError as e:
            raise ValueError(f"Invalid 'every' cadence seconds: {parts[1]}") from e

    elif parts[0] == "window":
        if len(parts) != 3:
            raise ValueError(f"Invalid 'window' cadence: {cadence_str}")
        try:
            window_s = int(parts[1])
            max_runs = int(parts[2])
            if window_s <= 0 or max_runs <= 0:
                raise ValueError("Window seconds and max_runs must be positive")
            return ("window", window_s, max_runs)
        except ValueError as e:
            raise ValueError(f"Invalid 'window' cadence params: {parts[1]}, {parts[2]}") from e

    elif parts[0] == "daily":
        if len(parts) != 3:
            raise ValueError(f"Invalid 'daily' cadence: {cadence_str}")
        try:
            hour = int(parts[1])
            minute = int(parts[2])
        except ValueError as e:
            raise ValueError(f"Invalid 'daily' cadence hour/minute: {parts[1]}, {parts[2]}") from e
        if not (0 <= hour <= 23):
            raise ValueError(f"'daily' cadence hour must be 0-23: {hour}")
        if not (0 <= minute <= 59):
            raise ValueError(f"'daily' cadence minute must be 0-59: {minute}")
        return ("daily", hour, minute)

    elif parts[0] == "weekly":
        if len(parts) != 4:
            raise ValueError(f"Invalid 'weekly' cadence: {cadence_str}")
        try:
            day_of_week = int(parts[1])
            hour = int(parts[2])
            minute = int(parts[3])
        except ValueError as e:
            raise ValueError(
                f"Invalid 'weekly' cadence params: {parts[1]}, {parts[2]}, {parts[3]}",
            ) from e
        if not (0 <= day_of_week <= 6):
            raise ValueError(f"'weekly' cadence day_of_week must be 0-6: {day_of_week}")
        if not (0 <= hour <= 23):
            raise ValueError(f"'weekly' cadence hour must be 0-23: {hour}")
        if not (0 <= minute <= 59):
            raise ValueError(f"'weekly' cadence minute must be 0-59: {minute}")
        return ("weekly", day_of_week, hour, minute)

    else:
        raise ValueError(f"Unknown cadence type: {parts[0]}")


def _next_wall_clock_occurrence(
    reference_ts: int,
    hour: int,
    minute: int,
    tz: Any,
    *,
    day_of_week: int | None,
    is_first_run: bool,
) -> int:
    """Next daily/weekly wall-clock occurrence at hour:minute (and, for
    weekly, day_of_week) in `tz`, strictly after `reference_ts` -- see
    docs/S5_2_TYPED_CADENCE_DESIGN.md Sec 4's algorithm. `is_first_run`
    (no prior scheduled_ts) lands on the *next* eligible occurrence
    on-or-after the reference point; a subsequent run always steps exactly
    one calendar day (daily) or seven calendar days (weekly) forward from
    its own last target time, avoiding drift the same way the `every`
    branch anchors to `last_run_ts` rather than `now_ts`. Deliberately no
    jitter and no BARTH_SPEED_FACTOR scaling (see design doc Sec 4 for why
    neither has a coherent meaning against a wall-clock target); missed
    ticks are never caught up.
    """
    reference_local = datetime.fromtimestamp(reference_ts, tz=tz)

    if is_first_run:
        candidate = reference_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day_of_week is not None:
            days_ahead = (day_of_week - candidate.weekday()) % 7
            candidate = candidate + timedelta(days=days_ahead)
        if candidate <= reference_local:
            candidate = candidate + timedelta(days=7 if day_of_week is not None else 1)
    else:
        this_occurrence = reference_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        step = timedelta(days=7) if day_of_week is not None else timedelta(days=1)
        candidate = this_occurrence + step

    return int(candidate.astimezone(_dt_timezone.utc).timestamp())


def compute_next_run(
    last_run_ts: int | None,
    scheduled_ts: int | None,
    cadence_str: str,
    now_ts: int,
    window_state: str | None = None,
    tz: Any = None,
) -> tuple[int, str | None]:
    """
    Compute the next run timestamp for a task.

    Args:
        last_run_ts: Last successful run (UTC epoch seconds), or None
        scheduled_ts: The scheduled time for the last run (UTC epoch secs)
        cadence_str: Cadence string (e.g., "every:900", "window:3600:2",
            "daily:08:00", "weekly:0:08:00")
        now_ts: Current time (UTC epoch seconds)
        window_state: JSON string with window bookkeeping, or None
        tz: A tzinfo (e.g. KernelDaemon.tz, dateutil.tz.gettz(...)).
            Required only by `daily`/`weekly` cadences (S5.2 Typed
            Cadence) -- `None` is fine for `every`/`window`, which ignore
            it entirely. A `daily`/`weekly` cadence parsed with `tz=None`
            raises ValueError rather than silently falling back to UTC --
            see docs/S5_2_TYPED_CADENCE_DESIGN.md Sec 4 for why a silent
            timezone substitution is treated as a fail-closed violation,
            not a convenience default.

    Returns:
        Tuple of (next_run_ts, new_window_state_json). `daily`/`weekly`
        always return `None` for window state -- that bookkeeping is
        `window` cadence-specific.

    Window cadence logic:
        - Track window_start_ts and runs_in_window
        - If runs_in_window < max_runs: next = now + floor(W/K)
        - If runs_in_window >= max_runs: next = window_start + W, reset

    Examples:
        >>> compute_next_run(None, None, "every:900", 1000, None)
        (1900, None)
        >>> compute_next_run(1000, 1900, "every:900", 2000, None)
        (2800, None)
    """
    cadence = parse(cadence_str)

    if cadence[0] == "every":
        # Scale interval and add ~5% jitter
        seconds = max(1, int(cadence[1]))
        seconds = max(1, int(seconds * _speed_factor()))
        jitter = max(1, int(seconds * 0.05))
        delta = max(1, seconds + random.randint(-jitter, jitter))

        if last_run_ts is None:
            # First run: schedule for now + interval
            return (now_ts + delta, None)
        else:
            # Schedule relative to last run (not now) to avoid drift
            return (last_run_ts + delta, None)

    elif cadence[0] == "window":
        # Scale the window length; keep even-split scheduling
        window_s = max(1, int(cadence[1]))
        window_s = max(1, int(window_s * _speed_factor()))
        max_runs = cadence[2]

        # Parse window state
        state: dict[str, Any] = {}
        if window_state:
            try:
                state = json.loads(window_state)
            except json.JSONDecodeError:
                state = {}

        window_start = state.get("window_start_ts", now_ts)
        runs_in_window = state.get("runs_in_window", 0)

        # If this is a fresh task or window expired, reset
        if last_run_ts is None or (now_ts - window_start) >= window_s:
            window_start = now_ts
            runs_in_window = 0

        # If we've reached max runs, advance to next window
        if runs_in_window >= max_runs:
            window_start = window_start + window_s
            runs_in_window = 0

        # Schedule next run within current window
        interval = window_s // max_runs
        next_ts = window_start + (runs_in_window * interval)

        # If next_ts is in the past, schedule immediately
        next_ts = max(next_ts, now_ts)

        # Update state for after this run completes
        new_state = {"window_start_ts": window_start, "runs_in_window": runs_in_window + 1}

        return (next_ts, json.dumps(new_state))

    elif cadence[0] in ("daily", "weekly"):
        if tz is None:
            raise ValueError(
                f"'{cadence[0]}' cadence requires tz (see docs/S5_2_TYPED_CADENCE_DESIGN.md Sec 4 "
                "-- no silent UTC fallback)",
            )
        if cadence[0] == "daily":
            _, hour, minute = cadence
            day_of_week = None
        else:
            _, day_of_week, hour, minute = cadence

        is_first_run = scheduled_ts is None
        reference_ts = scheduled_ts if scheduled_ts is not None else now_ts
        next_ts = _next_wall_clock_occurrence(
            reference_ts,
            hour,
            minute,
            tz,
            day_of_week=day_of_week,
            is_first_run=is_first_run,
        )
        return (next_ts, None)

    else:
        raise ValueError(f"Unknown cadence type: {cadence[0]}")
