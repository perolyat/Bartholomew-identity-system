"""Freezing what actually happened during an unattended run.

The end of an unattended run is the moment its evidence is most likely to be
lost: the process is being stopped, the database is about to be reused or
deleted, and whoever reads the result will do so days later with nothing but
the artifacts. This builds that artifact -- one JSON document, assembled by
reading the records the runtime already wrote, plus the run ledger in
`bartholomew.runtime.evidence`.

Three properties it is built for, in order of importance:

* **Truthful.** Every section says where it came from and what it could not
  determine. A table that does not exist is reported as ``available: false``,
  not as zero rows -- "the scheduler ran nothing" and "we could not tell what
  the scheduler ran" are opposite conclusions and this must never blur them.
  A process that vanished is ``lost``, and never rounded up to ``clean``.
* **Deterministic.** The same database produces byte-identical `record`
  content, so a digest over it is a meaningful seal. The generation timestamp
  and the tool's own version sit *outside* the digested region for exactly
  that reason.
* **Derived, never authoritative.** Nothing here writes to the runtime's
  records or reconciles them. Where a fact is attributed by timestamp rather
  than by identity, the section names that as its attribution method, because
  a time-window attribution is weaker evidence than a recorded id and a
  reviewer is entitled to know which they are looking at.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from bartholomew.runtime.evidence import END_CLEAN, EvidenceStore, Incarnation

#: Bumped when the *shape* of a report changes, so an old artifact is never
#: silently read against new expectations.
#:
#: 2 -- Package A adds `sources.event_processing` and the four summary counts
#: that go with it. A version-1 artifact is still a valid report; it simply
#: predates the backbone, and a reader must not treat its silence about
#: processing as "nothing was processed".
REPORT_SCHEMA_VERSION = 2


#: The event backbone's states, named here rather than imported, so a frozen
#: report keeps rendering every state a *past* build could have written even
#: if a future build renames or adds one. An evidence document that silently
#: stopped showing a state would be worse than one that shows it as zero.
_PROCESSING_STATES = (
    "captured",
    "claimed",
    "processed",
    "irrelevant",
    "refused",
    "quarantined",
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def _unavailable(table: str, reason: str) -> dict[str, Any]:
    """The shape every source uses when it could not be read.

    `count` is deliberately absent rather than 0. A caller that reaches for a
    count it was not given gets a KeyError, which is a much better outcome
    than quietly treating "unknown" as "none".
    """
    return {"available": False, "table": table, "reason": reason, "items": []}


def _iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return (
        datetime.fromtimestamp(int(ts), tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _epoch(iso_text: str | None) -> int | None:
    """Whole seconds from an ISO-8601 timestamp, or None if it cannot be read.

    Several of the runtime's tables store text timestamps rather than integer
    epochs. An unparseable one becomes None, which attribution then counts as
    unattributed -- the honest outcome for a row whose time nobody can
    establish.
    """
    if not iso_text:
        return None
    text = str(iso_text).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _window_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    columns: str,
    ts_column: str,
    order: str,
    limit: int,
) -> dict[str, Any]:
    if not _table_exists(conn, table):
        return _unavailable(
            table,
            f"The {table} table does not exist in this database, so nothing "
            "can be said about it -- this is not the same as it being empty.",
        )
    rows = conn.execute(
        f"SELECT {columns} FROM {table} ORDER BY {order} LIMIT ?",  # noqa: S608 - fixed literals
        (limit,),
    ).fetchall()
    names = [c.strip().split(" ")[-1] for c in columns.split(",")]
    items = [dict(zip(names, r, strict=True)) for r in rows]
    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    return {
        "available": True,
        "table": table,
        "count": int(total),
        "truncated": len(items) < int(total),
        "attribution": f"time-ordered by {table}.{ts_column}",
        "items": items,
    }


def _attribute_to_incarnations(
    items: list[dict[str, Any]],
    *,
    ts_key: str,
    incarnations: list[Incarnation],
    starts: list[tuple[int, int, int | None]],
) -> dict[str, Any]:
    """Bucket timestamped rows into the incarnation whose window contains them.

    `starts` is (incarnation_id, started_ts, ended_ts_or_None) in start order.
    Each window runs from one incarnation's start to the *next* incarnation's
    start, and the last window is left open-ended. Deliberately not bounded by
    the recorded end: only one runtime can hold the database at a time, so
    nothing else was running in the gap between one process's shutdown and the
    next one's start, and closing the window at the end would drop rows into
    "unattributed" while implying nobody knows where they came from.

    A row before the first incarnation started is counted as *unattributed*
    rather than pushed into the nearest window: it belongs to some earlier run
    or to work done outside this one, and inventing a home for it would erase
    a real finding.

    Attribution is at whole-second resolution, which is what the underlying
    tables store. Two incarnations that start within the same second cannot be
    told apart by timestamp; identity-based attribution (`inbound_events`, via
    `runtime_id`) is not subject to that limit.
    """
    buckets: dict[str, int] = {str(i.id): 0 for i in incarnations}
    unattributed = 0
    for item in items:
        ts = item.get(ts_key)
        if ts is None:
            unattributed += 1
            continue
        placed = False
        for idx, (inc_id, start_ts, _end_ts) in enumerate(starts):
            upper = starts[idx + 1][1] if idx + 1 < len(starts) else None
            if ts >= start_ts and (upper is None or ts < upper):
                buckets[str(inc_id)] += 1
                placed = True
                break
        if not placed:
            unattributed += 1
    return {
        "per_incarnation": buckets,
        "unattributed": unattributed,
        "method": (
            "whole-second timestamp, bucketed into [incarnation start, next "
            "incarnation start); the last window is open-ended"
        ),
    }


def build_report(db_path: str, run_id: str, *, item_limit: int = 200) -> dict[str, Any]:
    """Assemble the frozen evidence record for one unattended run.

    `item_limit` bounds each source's inlined rows; the true total is always
    reported alongside, and `truncated` says when the inline list is a sample.
    A truncated section is still honest about scale.
    """
    store = EvidenceStore(db_path)
    incarnations = store.incarnations(run_id)
    observations = store.observations(run_id)

    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")

        # The scheduler's own durable activity record. Not the in-memory
        # heartbeat -- that answers "is it alive right now" and belongs to a
        # process that has, by the time this runs, exited.
        ticks = _window_rows(
            conn,
            table="ticks",
            columns="id, task_id, started_ts, finished_ts, success",
            ts_column="started_ts",
            order="started_ts ASC, id ASC",
            limit=item_limit,
        )
        governance = _window_rows(
            conn,
            table="governance_audit",
            columns="id, ts, action, scopes, reason, actor",
            ts_column="ts",
            order="ts ASC, id ASC",
            limit=item_limit,
        )
        skill_actions = _window_rows(
            conn,
            table="skill_action_audit",
            columns="id, skill_id, action, status, result_error, timestamp",
            ts_column="timestamp",
            order="timestamp ASC, id ASC",
            limit=item_limit,
        )
        incidents = _window_rows(
            conn,
            table="startup_incidents",
            columns="id, ts, runtime_id, lifecycle_state_reached, final_outcome",
            ts_column="ts",
            order="ts ASC, id ASC",
            limit=item_limit,
        )

        # Inbound capture is the one source that records `runtime_id`, so it
        # is attributed by identity rather than by clock. Kept separate from
        # the time-attributed sources for that reason.
        if _table_exists(conn, "inbound_events"):
            rows = conn.execute(
                "SELECT id, source_id, event_id, event_type, received_at, outcome, runtime_id "
                "FROM inbound_events ORDER BY id ASC LIMIT ?",
                (item_limit,),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM inbound_events").fetchone()[0]
            inbound = {
                "available": True,
                "table": "inbound_events",
                "count": int(total),
                "truncated": len(rows) < int(total),
                "attribution": "time-ordered by inbound_events.received_at",
                "items": [
                    {
                        "id": r[0],
                        "source_id": r[1],
                        "event_id": r[2],
                        "event_type": r[3],
                        "received_at": r[4],
                        "outcome": r[5],
                        # Deliberately *not* called runtime_id here. The column
                        # holds the platform's per-user runtime binding (see
                        # inbound_auth.resolved_runtime_id) -- whose Bartholomew
                        # the event belongs in -- which is a different thing
                        # from brake_runtime's process-incarnation id, and is
                        # None on a single-runtime local deployment. Naming
                        # them alike is how a report would end up attributing
                        # events to the wrong incarnation while looking precise.
                        "tenant_runtime_id": r[6],
                        "_received_epoch": _epoch(r[4]),
                    }
                    for r in rows
                ],
            }
        else:
            inbound = _unavailable(
                "inbound_events",
                "The inbound_events table does not exist in this database.",
            )

        # What became of the captured events (Package A). Attributed by
        # identity, like inbound capture and for the same reason: the rows
        # carry the tenant `runtime_id` capture recorded, and a time-window
        # attribution would be weaker evidence about the one thing this
        # section exists to show. Reported as a disposition tally rather than
        # row-by-row -- a reviewer needs to know that every captured event
        # reached an answer, and which answers, not to re-read third-party
        # content that `inbound_events` already accounts for.
        if _table_exists(conn, "event_processing"):
            states = {
                row[0]: int(row[1])
                for row in conn.execute(
                    "SELECT state, COUNT(*) FROM event_processing GROUP BY state",
                ).fetchall()
            }
            oldest_pending = conn.execute(
                "SELECT received_at FROM event_processing "
                "WHERE state IN ('captured', 'claimed') "
                "ORDER BY received_ts ASC, id ASC LIMIT 1",
            ).fetchone()
            last_processed = conn.execute(
                "SELECT settled_at FROM event_processing WHERE state = 'processed' "
                "ORDER BY settled_at DESC LIMIT 1",
            ).fetchone()
            retries = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN attempts > 1 THEN attempts - 1 ELSE 0 END), 0) "
                "FROM event_processing",
            ).fetchone()
            quarantined_rows = conn.execute(
                "SELECT source_id, event_id, event_type, attempts, disposition_reason, "
                "last_error, settled_at FROM event_processing WHERE state = 'quarantined' "
                "ORDER BY settled_at ASC, id ASC LIMIT ?",
                (item_limit,),
            ).fetchall()
            total_events = conn.execute("SELECT COUNT(*) FROM event_processing").fetchone()[0]
            event_processing = {
                "available": True,
                "table": "event_processing",
                "count": int(total_events),
                # `items` is the quarantined events specifically -- the ones a
                # reviewer has to look at -- not a sample of every processed
                # event, which `inbound_events` already accounts for. So
                # `truncated` describes that list, and the attribution line
                # says which list it is.
                "truncated": states.get("quarantined", 0) > len(quarantined_rows),
                "attribution": (
                    "tenant runtime_id recorded at capture; no time-window inference. "
                    "`items` lists the quarantined events, not every processed one."
                ),
                "states": {name: states.get(name, 0) for name in sorted(_PROCESSING_STATES)},
                "backlog": states.get("captured", 0) + states.get("claimed", 0),
                "oldest_unprocessed_at": oldest_pending[0] if oldest_pending else None,
                "last_successful_processing_at": last_processed[0] if last_processed else None,
                "retry_attempts": int(retries[0]) if retries else 0,
                "quarantined": states.get("quarantined", 0),
                "quarantined_truncated": states.get("quarantined", 0) > len(quarantined_rows),
                "items": [
                    {
                        "source_id": r[0],
                        "event_id": r[1],
                        "event_type": r[2],
                        "attempts": r[3],
                        "reason": r[4],
                        "last_error": r[5],
                        "quarantined_at": r[6],
                    }
                    for r in quarantined_rows
                ],
            }
        else:
            event_processing = _unavailable(
                "event_processing",
                "The event_processing table does not exist in this database, so "
                "nothing can be said about what became of captured events -- "
                "which is not the same as nothing having been processed.",
            )

        if _table_exists(conn, "brake_runtime"):
            row = conn.execute(
                "SELECT runtime_id, started_at, clean, write_fence_open FROM brake_runtime",
            ).fetchone()
            final_marker = (
                {
                    "available": True,
                    "runtime_id": row[0],
                    "started_at": _iso(row[1]),
                    "clean": bool(row[2]),
                    "write_fence_open": bool(row[3]),
                }
                if row
                else {
                    "available": False,
                    "reason": "brake_runtime exists but holds no runtime marker.",
                }
            )
        else:
            final_marker = {
                "available": False,
                "reason": "The brake_runtime table does not exist in this database.",
            }

        # Incarnation boundaries, from the ledger, for the time-attributed
        # sources above.
        id_and_bounds = conn.execute(
            "SELECT id, started_ts, ended_ts FROM unattended_run_incarnations "
            "WHERE run_id = ? ORDER BY started_ts, id",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()

    starts = [(int(r[0]), int(r[1]), None if r[2] is None else int(r[2])) for r in id_and_bounds]

    if ticks["available"]:
        ticks["attributed"] = _attribute_to_incarnations(
            ticks["items"],
            ts_key="started_ts",
            incarnations=incarnations,
            starts=starts,
        )
    if governance["available"]:
        governance["attributed"] = _attribute_to_incarnations(
            governance["items"],
            ts_key="ts",
            incarnations=incarnations,
            starts=starts,
        )

    if inbound["available"]:
        inbound["attributed"] = _attribute_to_incarnations(
            inbound["items"],
            ts_key="_received_epoch",
            incarnations=incarnations,
            starts=starts,
        )
        for item in inbound["items"]:
            item.pop("_received_epoch", None)
    if skill_actions["available"]:
        for item in skill_actions["items"]:
            item["_epoch"] = _epoch(item.get("timestamp"))
        skill_actions["attributed"] = _attribute_to_incarnations(
            skill_actions["items"],
            ts_key="_epoch",
            incarnations=incarnations,
            starts=starts,
        )
        for item in skill_actions["items"]:
            item.pop("_epoch", None)

    lost = [i for i in incarnations if i.end_kind is not None and i.end_kind != END_CLEAN]
    still_open = [i for i in incarnations if i.open]

    record: dict[str, Any] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "db_path": str(db_path),
        "incarnations": [i.as_dict() for i in incarnations],
        "observations": observations,
        "sources": {
            "scheduler_ticks": ticks,
            "governance_audit": governance,
            "governed_skill_actions": skill_actions,
            "inbound_events": inbound,
            "event_processing": event_processing,
            "startup_incidents": incidents,
            "final_runtime_marker": final_marker,
        },
        "summary": _summarise(
            run_id=run_id,
            incarnations=incarnations,
            lost=lost,
            still_open=still_open,
            ticks=ticks,
            governance=governance,
            skill_actions=skill_actions,
            inbound=inbound,
            incidents=incidents,
            event_processing=event_processing,
        ),
    }
    return record


def _summarise(
    *,
    run_id: str,
    incarnations: list[Incarnation],
    lost: list[Incarnation],
    still_open: list[Incarnation],
    ticks: dict[str, Any],
    governance: dict[str, Any],
    skill_actions: dict[str, Any],
    inbound: dict[str, Any],
    incidents: dict[str, Any],
    event_processing: dict[str, Any],
) -> dict[str, Any]:
    """The paragraph a reviewer reads first. Never more confident than the data.

    `complete` is the load-bearing field and it is tri-state on purpose:
    ``True`` only when every incarnation recorded its own end and every source
    could be read; ``False`` when something is known to be missing; ``None``
    when the run has no incarnations at all, because that could equally mean
    "never started" and "the ledger was lost", and this cannot tell which.
    """
    unreadable = [
        name
        for name, src in {
            "scheduler_ticks": ticks,
            "governance_audit": governance,
            "governed_skill_actions": skill_actions,
            "inbound_events": inbound,
            "event_processing": event_processing,
            "startup_incidents": incidents,
        }.items()
        if not src["available"]
    ]

    if not incarnations:
        complete: bool | None = None
        verdict = (
            f"No incarnation of run {run_id!r} was ever recorded. This report "
            "cannot distinguish a run that never started from one whose "
            "ledger was lost."
        )
    elif still_open:
        complete = False
        verdict = (
            f"{len(still_open)} incarnation(s) are still open: either a process "
            "is running now, or it ended without anything recording how."
        )
    elif lost or unreadable:
        complete = False
        parts = []
        if lost:
            parts.append(
                f"{len(lost)} incarnation(s) did not record their own end "
                "and were closed as lost by a later process",
            )
        if unreadable:
            parts.append("could not read: " + ", ".join(sorted(unreadable)))
        verdict = "Run recorded with gaps -- " + "; ".join(parts) + "."
    else:
        complete = True
        verdict = (
            f"All {len(incarnations)} incarnation(s) of run {run_id!r} recorded "
            "their own end, and every evidence source was readable."
        )

    return {
        "incarnation_count": len(incarnations),
        "clean_endings": len([i for i in incarnations if i.end_kind == END_CLEAN]),
        "lost_endings": len(lost),
        "open_incarnations": len(still_open),
        "unreadable_sources": sorted(unreadable),
        "scheduler_tick_count": ticks.get("count"),
        "governance_decision_count": governance.get("count"),
        "governed_skill_action_count": skill_actions.get("count"),
        "inbound_event_count": inbound.get("count"),
        # Package A. Four numbers rather than one, because they answer four
        # different questions and no one of them implies the others: how much
        # was accounted for, how much is still waiting, how much was given up
        # on, and how much work was redone.
        "event_processing_count": event_processing.get("count"),
        "event_processing_backlog": event_processing.get("backlog"),
        "event_processing_quarantined": event_processing.get("quarantined"),
        "event_processing_retry_attempts": event_processing.get("retry_attempts"),
        "startup_incident_count": incidents.get("count"),
        "complete": complete,
        "verdict": verdict,
    }


def canonical_json(record: dict[str, Any]) -> str:
    """The exact bytes the digest is taken over. Sorted keys, no whitespace drift."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)


def digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()


def freeze(db_path: str, run_id: str, *, item_limit: int = 200) -> dict[str, Any]:
    """Build the record and seal it.

    The envelope carries `generated_at`, which is *not* part of the digested
    record -- so freezing the same database twice yields the same `digest`,
    and a changed digest means the evidence changed rather than that the clock
    moved.
    """
    record = build_report(db_path, run_id, item_limit=item_limit)
    return {
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "digest_algorithm": "sha256",
        "digest": digest(record),
        "record": record,
    }


def write_frozen_report(db_path: str, run_id: str, out_path: str, *, item_limit: int = 200) -> dict:
    """Freeze to `out_path` and return the envelope."""
    envelope = freeze(db_path, run_id, item_limit=item_limit)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(envelope, fh, indent=2, sort_keys=True, default=str)
        fh.write("\n")
    return envelope


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "build_report",
    "canonical_json",
    "digest",
    "freeze",
    "write_frozen_report",
]
