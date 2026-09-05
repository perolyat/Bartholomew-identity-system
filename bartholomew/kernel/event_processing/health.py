"""What the event backbone is actually doing, for operators and for evidence.

One function, `processing_health()`, answering the six questions an operator
needs and nothing else. It is a read: it never writes, never repairs, never
re-derives another authority's numbers, and never decides what "healthy"
means -- it reports state and lets the reader judge.

The six, and why each is here rather than inferable from the others:

* **backlog size** -- how much work is waiting. Zero is the normal state; a
  number that only grows is the signal that processing has stopped while
  capture has not.
* **oldest unprocessed age** -- head-of-line latency. A small backlog that is
  three days old is a stall; a large one that is thirty seconds old is a
  burst. Backlog size alone cannot tell them apart.
* **last successful processing** -- the positive signal. "Backlog zero" is
  equally true of a healthy idle system and one that has never processed
  anything.
* **retry attempts** -- work being redone. Rising retries with a flat backlog
  is a failing handler, which no other number shows.
* **quarantine count and reason** -- what has been given up on, and why, so
  the recovery action has something to act on.
* **dispositions** -- the terminal tally, so "refused" events (an unknown
  type, a policy denial) are visible instead of merely absent from the
  backlog.

Truthfulness rule, borrowed from `evidence_report` because it is the same
rule: a table that does not exist is reported as ``available: false``, never
as zero rows. "Nothing has been processed" and "we cannot tell what has been
processed" are opposite findings.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from bartholomew.kernel.db_ctx import wal_db

from . import store
from .config import EventProcessingSettings, resolve_settings
from .registry import registered_types

#: How many quarantined events are named in a health snapshot. The count is
#: always exact; the list is a sample, and says so.
QUARANTINE_SAMPLE = 10


def _unavailable(reason: str) -> dict[str, Any]:
    """The shape every unreadable answer takes.

    `backlog` and the rest are deliberately absent rather than zero: a caller
    that reaches for a number it was not given gets a KeyError, which is a far
    better outcome than quietly treating "unknown" as "none".
    """
    return {
        "available": False,
        "table": "event_processing",
        "reason": reason,
        "states": {},
        "quarantined_sample": [],
    }


def processing_health(
    db_path: str,
    *,
    settings: EventProcessingSettings | None = None,
    cfg: dict[str, Any] | None = None,
    now_ts: int | None = None,
) -> dict[str, Any]:
    """A snapshot of the backbone's operational state. Read-only, never raises."""
    resolved = settings or resolve_settings(cfg)
    now = int(time.time()) if now_ts is None else int(now_ts)
    base: dict[str, Any] = {
        "enabled": resolved.enabled,
        "backlog_limit": resolved.backlog_max,
        "batch_limit": resolved.batch_limit,
        "max_attempts": resolved.max_attempts,
        "lease_seconds": resolved.lease_seconds,
        "registered_event_types": list(registered_types()),
    }

    try:
        with wal_db(db_path, timeout=5.0, label="event_processing_health") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_processing'",
            ).fetchone()
            if not exists:
                return {
                    **base,
                    **_unavailable(
                        "The event_processing table does not exist in this database, "
                        "so nothing can be said about processing -- which is not the "
                        "same as nothing having been processed.",
                    ),
                }

            states = {
                row[0]: int(row[1])
                for row in conn.execute(
                    "SELECT state, COUNT(*) FROM event_processing GROUP BY state",
                ).fetchall()
            }
            oldest = conn.execute(
                "SELECT received_at, received_ts FROM event_processing "
                "WHERE state IN (?, ?) ORDER BY received_ts ASC, id ASC LIMIT 1",
                (store.STATE_CAPTURED, store.STATE_CLAIMED),
            ).fetchone()
            last_success = conn.execute(
                "SELECT settled_at FROM event_processing WHERE state = ? "
                "ORDER BY settled_at DESC LIMIT 1",
                (store.STATE_PROCESSED,),
            ).fetchone()
            # Plain aggregates rather than a FILTER clause: this has to run on
            # whatever SQLite the host Python was built against, and a health
            # surface that errors on an older build tells an operator nothing.
            retry_row = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN attempts > 1 THEN attempts - 1 ELSE 0 END), 0), "
                "COALESCE(SUM(CASE WHEN attempts > 1 THEN 1 ELSE 0 END), 0) "
                "FROM event_processing",
            ).fetchone()
            # How many distinct tenant bindings are represented in the
            # backlog. Normally exactly one (or zero, when it is empty). More
            # than one means some pending events belong to a runtime this
            # process does not claim for -- they will never drain here, and
            # the backlog and age numbers alone would look like a stall with
            # no cause. See "Known limitations" in
            # docs/EVENT_PROCESSING_BACKBONE.md.
            pending_runtimes = conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(runtime_id, '')) FROM event_processing "
                "WHERE state IN (?, ?)",
                (store.STATE_CAPTURED, store.STATE_CLAIMED),
            ).fetchone()
            quarantined = conn.execute(
                "SELECT source_id, event_id, event_type, attempts, "
                "disposition_reason, last_error, settled_at "
                "FROM event_processing WHERE state = ? "
                "ORDER BY settled_at DESC, id DESC LIMIT ?",
                (store.STATE_QUARANTINED, QUARANTINE_SAMPLE),
            ).fetchall()
    except sqlite3.Error as e:
        return {
            **base,
            **_unavailable(f"Processing state could not be read: {type(e).__name__}: {e}"),
        }

    backlog = states.get(store.STATE_CAPTURED, 0) + states.get(store.STATE_CLAIMED, 0)
    oldest_ts = int(oldest[1]) if oldest and oldest[1] else None
    quarantine_count = states.get(store.STATE_QUARANTINED, 0)

    return {
        **base,
        "available": True,
        "table": "event_processing",
        "backlog": backlog,
        "backlog_full": backlog >= resolved.backlog_max,
        "pending": states.get(store.STATE_CAPTURED, 0),
        "in_flight": states.get(store.STATE_CLAIMED, 0),
        "oldest_unprocessed_at": oldest[0] if oldest else None,
        "oldest_unprocessed_age_seconds": (
            max(0, now - oldest_ts) if oldest_ts is not None else None
        ),
        "last_successful_processing_at": last_success[0] if last_success else None,
        "pending_runtime_bindings": int(pending_runtimes[0]) if pending_runtimes else 0,
        "retry_attempts": int(retry_row[0]) if retry_row else 0,
        "events_retried": int(retry_row[1]) if retry_row else 0,
        "quarantined": quarantine_count,
        "quarantined_sample_truncated": quarantine_count > len(quarantined),
        "quarantined_sample": [
            {
                "source_id": r[0],
                "event_id": r[1],
                "event_type": r[2],
                "attempts": r[3],
                "reason": r[4],
                "last_error": r[5],
                "quarantined_at": r[6],
            }
            for r in quarantined
        ],
        "states": {name: states.get(name, 0) for name in sorted(store.ALL_STATES)},
        "processed": states.get(store.STATE_PROCESSED, 0),
        "irrelevant": states.get(store.STATE_IRRELEVANT, 0),
        "refused": states.get(store.STATE_REFUSED, 0),
    }


def health_component(db_path: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """The `/api/health` component shape for event processing.

    ``status`` is deliberately conservative about what counts as a fault. A
    disabled backbone is working correctly, and so is one with a backlog --
    those are states, not failures. Only two things are reported as failed:
    processing state that cannot be read at all, and a backlog at its limit
    (which is the point at which capture starts refusing, so it is genuinely
    a degraded service rather than a busy one).
    """
    snapshot = processing_health(db_path, cfg=cfg)
    if not snapshot.get("available"):
        return {"status": "unknown", **snapshot}
    status = "failed" if snapshot.get("backlog_full") else "ok"
    return {"status": status, **snapshot}


__all__ = ["QUARANTINE_SAMPLE", "health_component", "processing_health"]
