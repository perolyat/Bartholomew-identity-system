"""Truthful liveness state for the always-on runtime.

Always-on software that silently dies is not always-on software. Before this
module, `/healthz` answered ``ok`` from a process whose scheduler task had
died, and `/api/liveness/self`'s ``last_tick`` was written only by
`set_last_tick()`, which the scheduler loop never called -- so it reported
process-start time forever.

Two small pieces, and nothing more (this is not an observability platform):

* `SchedulerHeartbeat` -- an in-memory record of "the autonomy loop got round
  the loop again", updated by the loop itself and by nothing else.
* `ComponentHealth` -- the shape the API renders, so "we could not tell" is
  never reported as "it works".

In-memory on purpose. This answers *is this process's scheduler running right
now*, which is not a durable fact and must not be read from a table that a
previous process wrote. Durable scheduler activity already has a home in the
`ticks` table and `/api/liveness/ticks`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: Loop states, in the order they can be reached.
SCHEDULER_STARTING = "starting"
SCHEDULER_RUNNING = "running"
SCHEDULER_STOPPED = "stopped"
SCHEDULER_FAILED = "failed"

#: How long the loop may go without a beat before it is reported as stalled.
#:
#: The loop sleeps 5s when nothing is due, and a drive is bounded by
#: `DRIVE_TIMEOUT` (5s) plus `DRIVE_PACE_S` pacing, so a healthy loop beats
#: well inside a minute even when every registered drive is overdue at once.
#: Generous rather than tight: a false "stalled" would be exactly the kind of
#: untrue health signal this module exists to remove.
STALL_AFTER_SECONDS = 120.0


def _now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class SchedulerHeartbeat:
    """What the scheduler loop is doing, as far as this process can tell.

    `beat()` is called once per loop iteration -- not once per executed drive.
    An idle loop with nothing due is alive, and a heartbeat that only advanced
    when a drive ran would report a healthy idle scheduler as dead.
    """

    state: str = SCHEDULER_STARTING
    error: str | None = None

    #: `time.monotonic()` of the last beat. Monotonic so a wall-clock jump
    #: (NTP step, laptop resume) cannot fabricate or hide a stall.
    last_beat_monotonic: float | None = None

    #: Wall-clock of the last beat, for humans reading the health endpoint.
    last_beat_iso: str | None = None

    #: The last drive this loop actually executed, when it has run one.
    last_drive: str | None = None
    last_drive_iso: str | None = None

    _started_monotonic: float = field(default_factory=time.monotonic)

    def beat(self, *, drive: str | None = None) -> None:
        """Record one loop iteration; optionally the drive it just ran."""
        self.state = SCHEDULER_RUNNING
        self.error = None
        self.last_beat_monotonic = time.monotonic()
        self.last_beat_iso = _now_z()
        if drive:
            self.last_drive = drive
            self.last_drive_iso = self.last_beat_iso

    def mark_stopped(self) -> None:
        """The loop exited as intended (cancellation during shutdown)."""
        self.state = SCHEDULER_STOPPED
        self.error = None

    def mark_failed(self, error: BaseException | str) -> None:
        """The loop exited on its own, which it must never do while running."""
        self.state = SCHEDULER_FAILED
        self.error = str(error) if error else "scheduler loop exited unexpectedly"

    @property
    def seconds_since_beat(self) -> float | None:
        if self.last_beat_monotonic is None:
            return None
        return max(0.0, time.monotonic() - self.last_beat_monotonic)

    @property
    def stalled(self) -> bool:
        """True when a loop that claims to be running has stopped beating.

        A loop that has never beaten is measured from process start, so a
        scheduler that hangs before its first iteration is not reported
        healthy forever on the strength of having no evidence either way.
        """
        if self.state is not SCHEDULER_RUNNING:
            return False
        since = self.seconds_since_beat
        if since is None:
            since = time.monotonic() - self._started_monotonic
        return since > STALL_AFTER_SECONDS

    @property
    def healthy(self) -> bool:
        return self.state == SCHEDULER_RUNNING and not self.stalled

    def snapshot(self) -> dict[str, Any]:
        since = self.seconds_since_beat
        return {
            "state": SCHEDULER_FAILED if self.stalled else self.state,
            "healthy": self.healthy,
            "last_beat": self.last_beat_iso,
            "seconds_since_beat": round(since, 1) if since is not None else None,
            "last_drive": self.last_drive,
            "last_drive_at": self.last_drive_iso,
            "stalled": self.stalled,
            "error": self.error
            or (
                f"no scheduler activity for over {STALL_AFTER_SECONDS:.0f}s"
                if self.stalled
                else None
            ),
        }


@dataclass(frozen=True)
class ComponentHealth:
    """One named component's state, as rendered by the health endpoint.

    `ok` is tri-state on purpose: ``None`` means *unknown*, which is neither
    healthy nor a failure, and must never be flattened into either.
    """

    name: str
    ok: bool | None
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        status = "ok" if self.ok else ("unknown" if self.ok is None else "failed")
        return {"status": status, **self.detail}
