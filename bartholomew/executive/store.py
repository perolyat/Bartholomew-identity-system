"""Durable state for executive tasks. One table pair, and it is not Memory.

`executive_tasks` holds one row per task attempt and `executive_task_steps` one
row per step. What lives here is *what Bartholomew was asked to do and what it
proposed* --- never something it believes. Nothing in this module writes to
`memories`, `nudges`, `objectives`, `competencies` or the governance tables,
and nothing here decides anything: the pacing rule is in `plan.py`, governance
is in the envelope, and this is the place a plan survives between the moment it
was proposed and the moment a human at the host boundary approves a step.

It has to survive that gap. A proposal waits for an approval that arrives over
HTTP minutes later, and an executive whose plan lived only in one process's
memory would lose the second step of every two-step task the first time the
server restarted.

Deliberately **not** an authority. The action's own state --- whether it may
run --- is `windows_action_requests`, W03-C's table, and this one never
duplicates or second-guesses it: a step row records the `action_id` and reads
the truth from there. Two stores that both thought they knew whether an action
was approved would be one store too many.

Synchronous `sqlite3`, called through `run_off_loop()` from the async seam,
matching `actuation/store.py` and every other persistence surface here. No new
connection policy, no second writer.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bartholomew.kernel.db_ctx import wal_db

from .plan import Plan, PlanStep, StepStatus, TaskStatus
from .selection import CapabilitySelection
from .verification import Verification

EXECUTIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS executive_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    instruction TEXT NOT NULL,
    status TEXT NOT NULL,
    clarification TEXT,
    clarification_entry_id INTEGER,
    notes_json TEXT NOT NULL DEFAULT '[]',
    cautions_json TEXT NOT NULL DEFAULT '[]',
    evidence_refused_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_executive_tasks_open
    ON executive_tasks(tenant_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS executive_task_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    capability TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    described_as TEXT NOT NULL,
    selection_json TEXT NOT NULL,
    status TEXT NOT NULL,
    action_id TEXT,
    refusal_category TEXT,
    refusal_reason TEXT,
    verification_json TEXT,
    recoveries_json TEXT NOT NULL DEFAULT '[]',
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, task_id, step_index)
);
CREATE INDEX IF NOT EXISTS idx_executive_task_steps_action
    ON executive_task_steps(tenant_id, action_id);
"""


class ExecutivePersistenceError(RuntimeError):
    """The task state could not be written or read.

    Every caller treats this as a refusal. A plan whose state cannot be read is
    a plan whose pacing rule cannot be checked, and advancing one would be
    advancing without the check.
    """


def ensure_schema(db_path: str) -> None:
    """Create the executive tables if they do not exist. Idempotent."""
    try:
        with wal_db(db_path, timeout=30.0, label="executive_ensure_schema") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executescript(EXECUTIVE_SCHEMA)
            conn.commit()
    except sqlite3.Error as e:
        raise ExecutivePersistenceError(
            f"the executive task schema is unavailable: {type(e).__name__}: {e}",
        ) from e


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def save_plan(db_path: str, plan: Plan) -> None:
    """Write the whole plan. Upsert, so a save is idempotent within a task."""
    plan.touch()
    try:
        with wal_db(db_path, timeout=30.0, label="executive_save_plan") as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute(
                """
                INSERT INTO executive_tasks (
                    tenant_id, task_id, device_id, requested_by, instruction, status,
                    clarification, clarification_entry_id, notes_json, cautions_json,
                    evidence_refused_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, task_id) DO UPDATE SET
                    status = excluded.status,
                    clarification = excluded.clarification,
                    clarification_entry_id = excluded.clarification_entry_id,
                    notes_json = excluded.notes_json,
                    cautions_json = excluded.cautions_json,
                    evidence_refused_json = excluded.evidence_refused_json,
                    updated_at = excluded.updated_at
                """,
                (
                    plan.tenant_id,
                    plan.task_id,
                    plan.device_id,
                    plan.requested_by,
                    plan.instruction,
                    plan.status.value,
                    plan.clarification,
                    plan.clarification_entry_id,
                    _dumps(plan.notes),
                    _dumps(plan.cautions),
                    _dumps(plan.evidence_refused),
                    plan.created_at,
                    plan.updated_at,
                ),
            )
            for step in plan.steps:
                conn.execute(
                    """
                    INSERT INTO executive_task_steps (
                        tenant_id, task_id, step_index, capability, parameters_json,
                        described_as, selection_json, status, action_id,
                        refusal_category, refusal_reason, verification_json,
                        recoveries_json, attempts, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, task_id, step_index) DO UPDATE SET
                        status = excluded.status,
                        action_id = excluded.action_id,
                        refusal_category = excluded.refusal_category,
                        refusal_reason = excluded.refusal_reason,
                        verification_json = excluded.verification_json,
                        recoveries_json = excluded.recoveries_json,
                        attempts = excluded.attempts,
                        updated_at = excluded.updated_at
                    """,
                    (
                        plan.tenant_id,
                        plan.task_id,
                        step.index,
                        step.capability,
                        _dumps(step.parameters),
                        step.described_as,
                        _dumps(step.selection.as_dict()),
                        step.status.value,
                        step.action_id,
                        step.refusal_category,
                        step.refusal_reason,
                        _dumps(step.verification.as_dict()) if step.verification else None,
                        _dumps(step.recoveries),
                        step.attempts,
                        plan.updated_at,
                    ),
                )
            conn.commit()
    except sqlite3.Error as e:
        raise ExecutivePersistenceError(
            f"the executive task state could not be written: {type(e).__name__}: {e}",
        ) from e


def load_plan(db_path: str, *, tenant_id: str, task_id: str) -> Plan | None:
    """One plan, or None. Tenant-qualified: another tenant's id is unknown."""
    try:
        with wal_db(db_path, timeout=10.0, label="executive_load_plan") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            row = conn.execute(
                """
                SELECT device_id, requested_by, instruction, status, clarification,
                       clarification_entry_id, notes_json, cautions_json,
                       evidence_refused_json, created_at, updated_at
                FROM executive_tasks WHERE tenant_id = ? AND task_id = ?
                """,
                (tenant_id, task_id),
            ).fetchone()
            if row is None:
                return None
            plan = Plan(
                task_id=task_id,
                tenant_id=tenant_id,
                device_id=row[0],
                requested_by=row[1],
                instruction=row[2],
                status=TaskStatus(row[3]),
                clarification=row[4],
                clarification_entry_id=row[5],
                notes=list(json.loads(row[6] or "[]")),
                cautions=list(json.loads(row[7] or "[]")),
                evidence_refused=list(json.loads(row[8] or "[]")),
                created_at=row[9],
                updated_at=row[10],
            )
            for step_row in conn.execute(
                """
                SELECT step_index, capability, parameters_json, described_as,
                       selection_json, status, action_id, refusal_category,
                       refusal_reason, verification_json, recoveries_json, attempts
                FROM executive_task_steps
                WHERE tenant_id = ? AND task_id = ?
                ORDER BY step_index ASC
                """,
                (tenant_id, task_id),
            ).fetchall():
                plan.steps.append(_step(step_row))
            return plan
    except sqlite3.Error as e:
        raise ExecutivePersistenceError(
            f"the executive task state could not be read: {type(e).__name__}: {e}",
        ) from e


def _step(row: tuple[Any, ...]) -> PlanStep:
    verification = None
    if row[9]:
        raw = json.loads(row[9])
        verification = Verification(
            verdict=raw.get("verdict", "unknown"),
            source=raw.get("source", "absent"),
            detail=raw.get("detail", ""),
            device_status=raw.get("device_status"),
            read_back_code=raw.get("read_back_code"),
            read_back_event_id=raw.get("read_back_event_id"),
            evidence=dict(raw.get("evidence") or {}),
        )
    selection_raw = json.loads(row[4] or "{}")
    return PlanStep(
        index=int(row[0]),
        capability=row[1],
        parameters=dict(json.loads(row[2] or "{}")),
        described_as=row[3],
        selection=CapabilitySelection(
            capability=selection_raw.get("capability", row[1]),
            available=bool(selection_raw.get("available")),
            version=selection_raw.get("version"),
            risk=selection_raw.get("risk"),
            approval_requirement=selection_raw.get("approval_requirement"),
            device_autonomous=bool(selection_raw.get("device_autonomous")),
            refusal_code=selection_raw.get("refusal_code"),
            refusal_reason=selection_raw.get("refusal_reason"),
        ),
        status=StepStatus(row[5]),
        action_id=row[6],
        refusal_category=row[7],
        refusal_reason=row[8],
        verification=verification,
        recoveries=list(json.loads(row[10] or "[]")),
        attempts=int(row[11] or 0),
    )


def list_open_tasks(db_path: str, *, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Task summaries that are not finished. Bounded; a read, not an authority."""
    try:
        with wal_db(db_path, timeout=10.0, label="executive_list_tasks") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            rows = conn.execute(
                """
                SELECT task_id, device_id, instruction, status, updated_at
                FROM executive_tasks
                WHERE tenant_id = ? AND status IN (?, ?)
                ORDER BY updated_at DESC LIMIT ?
                """,
                (
                    tenant_id,
                    TaskStatus.IN_PROGRESS.value,
                    TaskStatus.AWAITING_CLARIFICATION.value,
                    max(1, min(int(limit), 200)),
                ),
            ).fetchall()
    except sqlite3.Error as e:
        raise ExecutivePersistenceError(
            f"the executive task list could not be read: {type(e).__name__}: {e}",
        ) from e
    return [
        {
            "task_id": r[0],
            "device_id": r[1],
            "instruction": r[2],
            "status": r[3],
            "updated_at": r[4],
        }
        for r in rows
    ]


def find_task_for_action(db_path: str, *, tenant_id: str, action_id: str) -> str | None:
    """Which task proposed one action, if any. Used to advance from a result."""
    try:
        with wal_db(db_path, timeout=10.0, label="executive_find_task") as conn:
            conn.execute("PRAGMA busy_timeout = 3000")
            row = conn.execute(
                "SELECT task_id FROM executive_task_steps "
                "WHERE tenant_id = ? AND action_id = ? ORDER BY id DESC LIMIT 1",
                (tenant_id, action_id),
            ).fetchone()
    except sqlite3.Error as e:
        raise ExecutivePersistenceError(
            f"the executive task state could not be read: {type(e).__name__}: {e}",
        ) from e
    return row[0] if row else None


__all__ = [
    "EXECUTIVE_SCHEMA",
    "ExecutivePersistenceError",
    "ensure_schema",
    "find_task_for_action",
    "list_open_tasks",
    "load_plan",
    "save_plan",
]
