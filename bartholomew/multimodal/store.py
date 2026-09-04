"""The registry of live sessions, and what happens to them after a crash.

**Why sessions live in process memory.** A multimodal session is only real
while the process that owns the microphone or the screen grabber is running.
If that process dies, the capture died with it -- the OS closed the stream.
So the authoritative answer to "is Bartholomew listening?" is held by the
process that would be doing the listening, and a session record that outlives
its process is a lie waiting to be told.

That makes restart cleanup a correctness requirement, not a nicety: acceptance
gate 9 is that a restart never leaves a session falsely marked active. Two
mechanisms give that:

* the in-memory registry is empty on a fresh process, so nothing survives to
  be falsely reported; and
* `reconcile_after_restart()` takes any *persisted* session snapshots (an
  operator's status file, or a future durable store) and terminally closes
  every one that claims to be live but whose owning pid is not this process
  and is not alive. They become `failed` with "process restart", which is
  visible and honest, rather than being deleted -- a person who saw
  "listening" before a crash deserves to see what became of it.

`SessionStore` also owns expiry sweeping and the brake stop, both of which
must act on every live session within a bounded interval.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .modality import Modality
from .session import LIVE_STATES, MultimodalSession, SessionState

logger = logging.getLogger(__name__)


def _pid_alive(pid: int | None) -> bool:
    """Whether a pid is a live process on this machine.

    A pid we cannot ask about is treated as *not* alive, because the safe
    error here is closing a session that might have been running (the process
    can start a new one) rather than reporting a capture that is not
    happening.
    """
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but belongs to another user -- it is not ours to own.
        return False
    except Exception:
        logger.exception("Could not determine whether pid %s is alive", pid)
        return False
    return True


class SessionStore:
    """Every session this process knows about, and the stop paths.

    Thread-safe: the brake sweep and the expiry sweep run from timers or
    scheduler threads while an adapter thread is mid-capture.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, MultimodalSession] = {}
        self._stoppers: dict[str, Callable[[], None]] = {}
        self._lock = threading.RLock()

    # -- membership ----------------------------------------------------------

    def add(self, session: MultimodalSession, stopper: Callable[[], None] | None = None) -> None:
        """Register a session and, optionally, how to stop its adapter."""
        with self._lock:
            self._sessions[session.session_id] = session
            if stopper is not None:
                self._stoppers[session.session_id] = stopper

    def get(self, session_id: str) -> MultimodalSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def all(self) -> list[MultimodalSession]:
        with self._lock:
            return list(self._sessions.values())

    def live(self, tenant_id: str | None = None) -> list[MultimodalSession]:
        """Sessions that are genuinely capturing or speaking right now."""
        with self._lock:
            return [
                s
                for s in self._sessions.values()
                if s.state in LIVE_STATES and (tenant_id is None or s.tenant_id == tenant_id)
            ]

    # -- ending --------------------------------------------------------------

    def stop(self, session_id: str, reason: str = "stopped by user") -> bool:
        """Explicit stop: signal the adapter, then close the record.

        Returns False for an unknown or already-terminal session, so a caller
        pressing stop twice gets a truthful "nothing to stop" rather than an
        error.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.is_terminal:
                return False
            stopper = self._stoppers.get(session_id)
            if session.state in (SessionState.ACTIVE, SessionState.APPROVED):
                session.transition(SessionState.STOPPING, reason)

        # Outside the lock: a stopper signals another thread and must not be
        # able to deadlock the registry.
        if stopper is not None:
            try:
                stopper()
            except Exception:
                logger.exception("Adapter stop hook failed for %s", session_id)

        with self._lock:
            if not session.is_terminal:
                session.transition(SessionState.STOPPED, reason)
            self._stoppers.pop(session_id, None)
        return True

    def terminate(self, session_id: str, state: SessionState, reason: str) -> bool:
        """Close a session into a specific terminal state (expiry, failure).

        Signals the adapter first, so an expiring session stops capturing
        rather than merely being relabelled.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.is_terminal:
                return False
            stopper = self._stoppers.get(session_id)

        if stopper is not None:
            try:
                stopper()
            except Exception:
                logger.exception("Adapter stop hook failed for %s", session_id)

        with self._lock:
            if session.is_terminal:
                return False
            if state not in (SessionState.STOPPED,) and session.state is SessionState.STOPPING:
                # STOPPING only leads to STOPPED/FAILED/EXPIRED; anything else
                # would be an illegal edge, and the state machine would refuse.
                pass
            try:
                session.transition(state, reason)
            except Exception:
                logger.exception("Could not terminate %s into %s", session_id, state)
                return False
            self._stoppers.pop(session_id, None)
        return True

    def stop_all_for_brake(self, scope: str, reason: str | None = None) -> list[str]:
        """Stop every live session the given brake scope covers.

        Called when the Parking Brake engages. Returns the ids stopped, so the
        caller can record exactly what the brake ended.
        """
        from .modality import BRAKE_SCOPE

        detail = reason or f"stopped by parking brake (scope={scope})"
        with self._lock:
            targets = [
                s.session_id
                for s in self._sessions.values()
                if s.state in LIVE_STATES
                and (scope == "global" or BRAKE_SCOPE[s.modality] == scope)
            ]
        stopped: list[str] = []
        for session_id in targets:
            if self.terminate(session_id, SessionState.STOPPED, detail):
                stopped.append(session_id)
        return stopped

    def sweep_expired(self, now: datetime | None = None) -> list[str]:
        """Terminate every live session past its expiry. Returns the ids."""
        moment = now or datetime.now(timezone.utc)
        with self._lock:
            targets = [
                s.session_id
                for s in self._sessions.values()
                if s.state in LIVE_STATES and s.is_expired(moment)
            ]
        expired: list[str] = []
        for session_id in targets:
            if self.terminate(
                session_id,
                SessionState.EXPIRED,
                "maximum session duration reached",
            ):
                expired.append(session_id)
        return expired

    # -- restart -------------------------------------------------------------

    def reconcile_after_restart(
        self,
        snapshots: Iterable[dict[str, Any]],
        this_pid: int | None = None,
    ) -> list[dict[str, Any]]:
        """Close out sessions that a previous process left claiming to be live.

        Takes persisted snapshots (see `write_status_file`/`read_status_file`)
        and returns the reconciled versions. A snapshot whose owner pid is
        this process is left alone -- that is a genuinely live session in a
        process that merely re-read its own file. Everything else that claims
        a live state becomes `failed`, because the process that owned the
        device is gone and whatever it was capturing stopped with it.
        """
        pid = this_pid if this_pid is not None else os.getpid()
        reconciled: list[dict[str, Any]] = []
        for snapshot in snapshots:
            record = dict(snapshot)
            state = record.get("state")
            owner = record.get("owner_pid")
            if state in {s.value for s in LIVE_STATES}:
                if owner == pid and _pid_alive(owner):
                    reconciled.append(record)
                    continue
                record["state"] = SessionState.FAILED.value
                record["is_live"] = False
                record["seconds_remaining"] = 0.0
                record["ended_at"] = datetime.now(timezone.utc).isoformat()
                record["outcome_reason"] = (
                    "process restart: the process that owned this session is no "
                    "longer running, so capture had already stopped"
                )
                record["reconciled_after_restart"] = True
            reconciled.append(record)
        return reconciled


def write_status_file(path: str | Path, sessions: list[MultimodalSession]) -> None:
    """Persist a snapshot for an operator and for restart reconciliation.

    Snapshots only -- state, timing, provenance. No transcript, no
    description, no image, nothing captured. Written atomically so a crash
    mid-write cannot leave a half-parsed file that reads as a live session.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "owner_pid": os.getpid(),
        "sessions": [{**s.snapshot(), "owner_pid": s.owner_pid or os.getpid()} for s in sessions],
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(target)


def read_status_file(path: str | Path) -> list[dict[str, Any]]:
    """Read persisted snapshots. A missing or corrupt file yields nothing.

    Corrupt is deliberately "no sessions" rather than an exception: the file
    is an aid to honesty, and an unreadable one must not stop a process from
    starting -- nor be interpreted as evidence that something is listening.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:
        logger.exception("Unreadable multimodal status file at %s; treating as empty", path)
        return []
    sessions = raw.get("sessions")
    return list(sessions) if isinstance(sessions, list) else []


def modality_of(snapshot: dict[str, Any]) -> Modality | None:
    """Best-effort modality from a snapshot, for reporting only."""
    try:
        return Modality(snapshot.get("modality"))
    except Exception:
        return None
