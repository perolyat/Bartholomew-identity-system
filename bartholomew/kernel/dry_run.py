"""
Stage 5, S5.5: the dry-run provenance sink.

See docs/S5_5_DRY_RUN_MODE_DESIGN.md Sec 7/8. A `DryRunResult` is the
structured record of what a real action *would* have done, produced
instead of a real store write or capability call whenever a Runtime
Contract seam determines a call is (or has been forced into) dry-run
mode. Deliberately isolated from every real ground-truth table
(`initiatives`, `initiative_audit`, `MemoryStore.reflections`,
`skill_action_audit`, Working Memory): `dry_run_results` is its own table,
never read by any real query, so no future drive or report can mistake a
simulation for something that actually happened -- structural separation,
not a `WHERE dry_run = 0` convention any caller could forget.

Deliberately synchronous, mirroring `initiative_store.py`'s/
`governance_store.py`'s own module docstring rationale: nothing here is
called from the event loop directly -- every real call site routes
through `bartholomew.kernel.blocking_executor.run_off_loop()`. No class
wrapping a persistent instance/cache is needed (unlike `InitiativeStore`/
`GovernanceStore`): callers already have a `db_path` in hand at every real
call site (`ctx.mem.db_path`), and there is no per-instance state worth
caching here, so this module exposes plain functions instead.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from bartholomew.kernel.db_ctx import connect, set_wal_pragmas


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True)
class DryRunResult:
    """S5.5 design doc Sec 7. `dry_run_id` defaults to a fresh UUID and
    `ts` to now, so most callers only need to supply the fields that
    actually vary per simulation."""

    surface: str
    proposed_action: str
    target: str
    parameters: dict[str, Any]
    expected_effects: dict[str, Any]
    governance_decision: str
    approval_requirements: dict[str, Any]
    would_execute: bool
    actor: str | None = None
    dry_run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run_id": self.dry_run_id,
            "ts": self.ts,
            "surface": self.surface,
            "proposed_action": self.proposed_action,
            "target": self.target,
            "parameters": self.parameters,
            "expected_effects": self.expected_effects,
            "governance_decision": self.governance_decision,
            "approval_requirements": self.approval_requirements,
            "would_execute": self.would_execute,
            "actor": self.actor,
        }


def ensure_schema(db_path: str) -> None:
    """Create `dry_run_results` if missing. Safe to call every time -- an
    idempotent `CREATE TABLE IF NOT EXISTS`, matching every other store's
    own `ensure_schema()` convention in this codebase."""
    conn = connect(db_path)
    try:
        set_wal_pragmas(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dry_run_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dry_run_id TEXT NOT NULL UNIQUE,
                ts TEXT NOT NULL,
                surface TEXT NOT NULL,
                proposed_action TEXT NOT NULL,
                target TEXT,
                parameters TEXT,
                expected_effects TEXT,
                governance_decision TEXT NOT NULL,
                approval_requirements TEXT,
                would_execute INTEGER NOT NULL,
                actor TEXT
            )
            """,
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dry_run_results_surface "
            "ON dry_run_results(surface, ts)",
        )
        conn.commit()
    finally:
        conn.close()


def record_dry_run_result(db_path: str, result: DryRunResult) -> None:
    """Persist one `DryRunResult`. Never touches, and has no path to
    touch, any real ground-truth table -- this function's only write
    target is `dry_run_results`."""
    ensure_schema(db_path)
    conn = connect(db_path)
    try:
        set_wal_pragmas(conn)
        conn.execute(
            "INSERT INTO dry_run_results "
            "(dry_run_id, ts, surface, proposed_action, target, parameters, "
            " expected_effects, governance_decision, approval_requirements, "
            " would_execute, actor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.dry_run_id,
                result.ts,
                result.surface,
                result.proposed_action,
                result.target,
                json.dumps(result.parameters),
                json.dumps(result.expected_effects),
                result.governance_decision,
                json.dumps(result.approval_requirements),
                1 if result.would_execute else 0,
                result.actor,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_dry_run_results(
    db_path: str,
    *,
    surface: str | None = None,
    target: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Most recent dry-run results, newest first, optionally filtered by
    surface and/or target. Read-only; never joined against or unioned
    with any real audit table."""
    ensure_schema(db_path)
    conn = connect(db_path)
    try:
        clauses = []
        params: list[Any] = []
        if surface is not None:
            clauses.append("surface = ?")
            params.append(surface)
        if target is not None:
            clauses.append("target = ?")
            params.append(target)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = conn.execute(
            "SELECT dry_run_id, ts, surface, proposed_action, target, parameters, "
            "expected_effects, governance_decision, approval_requirements, "
            "would_execute, actor "
            f"FROM dry_run_results {where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "dry_run_id": row[0],
            "ts": row[1],
            "surface": row[2],
            "proposed_action": row[3],
            "target": row[4],
            "parameters": json.loads(row[5]) if row[5] else {},
            "expected_effects": json.loads(row[6]) if row[6] else {},
            "governance_decision": row[7],
            "approval_requirements": json.loads(row[8]) if row[8] else {},
            "would_execute": bool(row[9]),
            "actor": row[10],
        }
        for row in rows
    ]
