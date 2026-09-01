"""Durable evidence that an unattended run actually happened, and what it did.

Band A's last open item is *reliable evidence/logging* -- "Test #1's
shutdown-capture gap (OP-W005) must not recur" (`ROADMAP.md`). Test #1 could
not say, afterwards, what the running system had done, because the record it
would have been reconstructed from did not survive the way the run ended. An
unattended test whose evidence is destroyed by the end of the test proves
nothing at all.

What was actually missing is narrow. The runtime already records plenty:
`ticks` (scheduler activity), `governance_audit` (governed decisions),
`skill_action_audit` (governed action outcomes), `inbound_events` (capture
outcomes), `startup_incidents` (a start that went wrong), `brake_runtime`
(this runtime's id, and whether *this* shutdown was clean). Every one of
those is written by an existing authority and none of them is reimplemented
here.

Two things are missing, and this module adds exactly those two:

* **A run identity that outlives a process.** `brake_runtime.runtime_id`
  identifies one *process incarnation* and is overwritten by the next one, so
  after a restart there is no way to say "these ticks are from before the
  restart and those are from after" -- or even that a restart happened. An
  unattended run spans restarts by definition.
* **A durable incarnation ledger.** `brake_runtime` holds one row. When a
  process is killed rather than stopped, the fact that it was running at all
  is lost as soon as the next one starts, which is precisely how a
  shutdown-capture gap becomes invisible.

Deliberate non-goals, because this observes the runtime and must never become
another runtime authority:

* It schedules nothing, restarts nothing, and decides nothing about health.
  Health is `bartholomew.runtime.health` and `/api/health`; lifecycle is
  `KernelDaemon`; restart policy is the service supervisor's.
* It never writes to, corrects, or re-derives another authority's records. A
  report *reads* `ticks`/`governance_audit`/`skill_action_audit`, joined on
  time, and says so.
* It is **inert unless asked for**. Nothing is recorded unless
  `BARTH_UNATTENDED_RUN_ID` is set in the process's environment, so a normal
  deployment gains no new writer and no new table. This is test-harness
  machinery, and it does not widen what Bartholomew may do unattended.

Truthfulness rule, which the schema enforces rather than merely documents: an
incarnation that never recorded its own end is closed by the *next* one as
``lost``, with ``inferred = 1``. "We cannot tell how this process ended" is a
recorded outcome. It is never upgraded to "it stopped cleanly".
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import socket
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: Set this to a run identifier to turn evidence recording on for a process.
RUN_ID_ENV = "BARTH_UNATTENDED_RUN_ID"

#: Run ids appear in filenames and report headers, so they are kept to a
#: conservative, obviously-safe alphabet rather than sanitised after the fact.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: How an incarnation ended.
END_CLEAN = "clean"  # the process recorded its own shutdown
END_FAILED = "failed"  # the process recorded a shutdown after a fatal failure
END_LOST = "lost"  # it never recorded one; killed, crashed, or power-cut

SCHEMA = """
CREATE TABLE IF NOT EXISTS unattended_run_incarnations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    runtime_id TEXT,
    pid INTEGER NOT NULL,
    host TEXT,
    started_ts INTEGER NOT NULL,
    started_iso TEXT NOT NULL,
    ended_ts INTEGER,
    ended_iso TEXT,
    end_kind TEXT,
    end_detail TEXT,
    inferred INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_unattended_incarnations_run
    ON unattended_run_incarnations(run_id, started_ts);

CREATE TABLE IF NOT EXISTS unattended_run_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    incarnation_id INTEGER,
    ts INTEGER NOT NULL,
    iso TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_unattended_observations_run
    ON unattended_run_observations(run_id, ts, id);
"""


def _now() -> tuple[int, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return int(now.timestamp()), now.isoformat().replace("+00:00", "Z")


def validate_run_id(run_id: str) -> str:
    """Return `run_id` if it is a usable run identifier, else raise."""
    if not _RUN_ID_RE.match(run_id or ""):
        raise ValueError(
            f"{RUN_ID_ENV}={run_id!r} is not a valid run id. Use 1-64 characters "
            "of A-Z a-z 0-9 . _ - starting with a letter or digit.",
        )
    return run_id


def active_run_id(env: dict[str, str] | None = None) -> str | None:
    """The unattended run this process belongs to, or None if it is not in one.

    An invalid value is refused rather than ignored: a run id that silently
    became something else would attribute evidence to the wrong run, which is
    worse than not recording it.
    """
    raw = (env if env is not None else os.environ).get(RUN_ID_ENV, "").strip()
    if not raw:
        return None
    return validate_run_id(raw)


@dataclass(frozen=True)
class Incarnation:
    """One process's participation in a run, as recorded."""

    id: int
    run_id: str
    runtime_id: str | None
    pid: int
    host: str | None
    started_iso: str
    ended_iso: str | None
    end_kind: str | None
    end_detail: str | None
    inferred: bool

    @property
    def open(self) -> bool:
        return self.end_kind is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "runtime_id": self.runtime_id,
            "pid": self.pid,
            "host": self.host,
            "started_at": self.started_iso,
            "ended_at": self.ended_iso,
            "end_kind": self.end_kind,
            "end_detail": self.end_detail,
            "end_inferred": self.inferred,
        }


class EvidenceStore:
    """The run ledger. Plain SQLite in the runtime's own database.

    Same file as everything else it will be correlated against, so a frozen
    report is one file's worth of consistent state rather than several that
    have to be trusted to line up. Its own connection per call, briefly held:
    this is written a handful of times per process and read after the fact,
    and it must never contend with the runtime it is observing.
    """

    def __init__(self, db_path: str):
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # -- writing ----------------------------------------------------------

    def open_incarnation(
        self,
        run_id: str,
        *,
        runtime_id: str | None,
        pid: int | None = None,
        host: str | None = None,
    ) -> int:
        """Record that this process has joined `run_id`, and return its row id.

        Closes any earlier incarnation left open against this database as
        ``lost``/inferred first. That is safe to do unconditionally because
        the kernel holds an exclusive process lock on the database file, so a
        row still open when a new process gets this far belongs to a process
        that is gone -- and the *reason* it is gone was never recorded, which
        is exactly what ``lost`` says.
        """
        validate_run_id(run_id)
        started_ts, started_iso = _now()
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            conn.execute(
                "UPDATE unattended_run_incarnations "
                "SET ended_ts = ?, ended_iso = ?, end_kind = ?, inferred = 1, "
                "    end_detail = ? "
                "WHERE end_kind IS NULL",
                (
                    started_ts,
                    started_iso,
                    END_LOST,
                    "No end was recorded by the process itself. Closed when a "
                    "later process opened against this database; the end time "
                    "is that moment, not the moment the process actually died.",
                ),
            )
            cur = conn.execute(
                "INSERT INTO unattended_run_incarnations "
                "(run_id, runtime_id, pid, host, started_ts, started_iso) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    runtime_id,
                    int(pid if pid is not None else os.getpid()),
                    host if host is not None else _hostname(),
                    started_ts,
                    started_iso,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def close_incarnation(
        self,
        incarnation_id: int,
        *,
        end_kind: str = END_CLEAN,
        detail: str | None = None,
    ) -> None:
        """Record how this process ended. Only ever closes a still-open row.

        The ``end_kind IS NULL`` guard matters: if a later process already
        closed this row as ``lost``, that inferred verdict stands. A process
        that comes back to life after its record was written off is not a
        situation this should paper over.
        """
        if end_kind not in (END_CLEAN, END_FAILED, END_LOST):
            raise ValueError(f"unknown end_kind {end_kind!r}")
        ended_ts, ended_iso = _now()
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE unattended_run_incarnations "
                "SET ended_ts = ?, ended_iso = ?, end_kind = ?, end_detail = ?, inferred = 0 "
                "WHERE id = ? AND end_kind IS NULL",
                (ended_ts, ended_iso, end_kind, detail, int(incarnation_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def record_observation(
        self,
        run_id: str,
        *,
        kind: str,
        source: str,
        payload: dict[str, Any],
        incarnation_id: int | None = None,
    ) -> int:
        """Append one observation of the run. Append-only; never updated.

        `source` says who saw it, because that is the difference between
        evidence and assertion: ``"api:/api/health"`` is a reading taken from
        the running service, ``"harness"`` is something the test harness did.
        """
        validate_run_id(run_id)
        ts, iso = _now()
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            cur = conn.execute(
                "INSERT INTO unattended_run_observations "
                "(run_id, incarnation_id, ts, iso, kind, source, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    incarnation_id,
                    ts,
                    iso,
                    kind,
                    source,
                    json.dumps(payload, sort_keys=True, default=str),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    # -- reading ----------------------------------------------------------

    def incarnations(self, run_id: str | None = None) -> list[Incarnation]:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            sql = (
                "SELECT id, run_id, runtime_id, pid, host, started_iso, ended_iso, "
                "end_kind, end_detail, inferred FROM unattended_run_incarnations"
            )
            args: tuple = ()
            if run_id is not None:
                sql += " WHERE run_id = ?"
                args = (run_id,)
            sql += " ORDER BY started_ts, id"
            rows = conn.execute(sql, args).fetchall()
        finally:
            conn.close()
        return [
            Incarnation(
                id=r[0],
                run_id=r[1],
                runtime_id=r[2],
                pid=r[3],
                host=r[4],
                started_iso=r[5],
                ended_iso=r[6],
                end_kind=r[7],
                end_detail=r[8],
                inferred=bool(r[9]),
            )
            for r in rows
        ]

    def observations(self, run_id: str | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            sql = (
                "SELECT id, run_id, incarnation_id, iso, kind, source, payload "
                "FROM unattended_run_observations"
            )
            args: tuple = ()
            if run_id is not None:
                sql += " WHERE run_id = ?"
                args = (run_id,)
            sql += " ORDER BY ts, id"
            rows = conn.execute(sql, args).fetchall()
        finally:
            conn.close()
        return [
            {
                "id": r[0],
                "run_id": r[1],
                "incarnation_id": r[2],
                "at": r[3],
                "kind": r[4],
                "source": r[5],
                "payload": json.loads(r[6]),
            }
            for r in rows
        ]


def _hostname() -> str | None:
    try:
        return socket.gethostname()
    except Exception:  # pragma: no cover - hostname lookup is not critical
        return None


# ---------------------------------------------------------------------------
# The two hooks the service process calls
# ---------------------------------------------------------------------------
#
# Both are best-effort and never raise. Evidence recording is an observer: a
# failure to record must degrade the *evidence*, not the runtime. It is
# recorded as a missing incarnation, which a report reads as "we cannot tell",
# and that is the correct answer when the ledger was not written.

#: This process's open incarnation row, if it is in a run. Process-wide
#: because there is one runtime per process, which the kernel's process lock
#: enforces. A one-slot list rather than a rebound module name, so the two
#: hooks below mutate one object instead of each declaring `global`.
_current: list[tuple[str, int] | None] = [None]


def record_process_start(db_path: str, *, runtime_id: str | None) -> tuple[str, int] | None:
    """Called once the kernel is up. No-op outside an unattended run."""
    try:
        run_id = active_run_id()
        if run_id is None:
            return None
        store = EvidenceStore(db_path)
        incarnation_id = store.open_incarnation(run_id, runtime_id=runtime_id)
        _current[0] = (run_id, incarnation_id)
        logger.info(
            "Unattended run %s: incarnation %s open (runtime_id=%s, pid=%s)",
            run_id,
            incarnation_id,
            runtime_id,
            os.getpid(),
        )
        store.record_observation(
            run_id,
            kind="incarnation_started",
            source="runtime",
            payload={
                "runtime_id": runtime_id,
                "pid": os.getpid(),
                "python": platform.python_version(),
                "db_path": str(db_path),
            },
            incarnation_id=incarnation_id,
        )
        return _current[0]
    except Exception:
        logger.exception("Could not record the start of this unattended-run incarnation")
        return None


def record_process_stop(
    db_path: str,
    *,
    end_kind: str = END_CLEAN,
    detail: str | None = None,
) -> None:
    """Called after the kernel has stopped. No-op outside an unattended run."""
    try:
        if _current[0] is None:
            return
        run_id, incarnation_id = _current[0]
        EvidenceStore(db_path).close_incarnation(
            incarnation_id,
            end_kind=end_kind,
            detail=detail,
        )
        logger.info(
            "Unattended run %s: incarnation %s closed (%s)",
            run_id,
            incarnation_id,
            end_kind,
        )
    except Exception:
        logger.exception("Could not record the end of this unattended-run incarnation")
    finally:
        _current[0] = None


def current_incarnation() -> tuple[str, int] | None:
    """(run_id, incarnation_id) for this process, or None."""
    return _current[0]


def _reset_for_tests() -> None:
    _current[0] = None


__all__ = [
    "END_CLEAN",
    "END_FAILED",
    "END_LOST",
    "RUN_ID_ENV",
    "EvidenceStore",
    "Incarnation",
    "active_run_id",
    "current_incarnation",
    "record_process_start",
    "record_process_stop",
    "validate_run_id",
]
