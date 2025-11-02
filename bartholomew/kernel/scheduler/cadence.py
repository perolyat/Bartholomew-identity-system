"""
Cadence parsing and next-run calculation.

Supports two cadence types:
- every:<seconds> - Run every N seconds
- window:<window_seconds>:<max_runs> - Run K times within window W
"""

import json
import os
import random
from typing import Any, Union


CadenceType = Union[tuple[str, int], tuple[str, int, int]]


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

    else:
        raise ValueError(f"Unknown cadence type: {parts[0]}")


def compute_next_run(
    last_run_ts: int | None,
    scheduled_ts: int | None,
    cadence_str: str,
    now_ts: int,
    window_state: str | None = None,
) -> tuple[int, str | None]:
    """
    Compute the next run timestamp for a task.

    Args:
        last_run_ts: Last successful run (UTC epoch seconds), or None
        scheduled_ts: The scheduled time for the last run (UTC epoch secs)
        cadence_str: Cadence string (e.g., "every:900", "window:3600:2")
        now_ts: Current time (UTC epoch seconds)
        window_state: JSON string with window bookkeeping, or None

    Returns:
        Tuple of (next_run_ts, new_window_state_json)

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

    else:
        raise ValueError(f"Unknown cadence type: {cadence[0]}")
