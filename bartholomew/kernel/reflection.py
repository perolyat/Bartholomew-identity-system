"""
Unified Reflection record (Runtime Contract stage 7: "Reflection")
==================================================================

Every action that traverses the Runtime Contract -- a chat turn, a skill
execution, and future sensor/scheduler surfaces -- produces the *same*
Reflection shape, persisted through the *same* Memory sink. Before this,
chat's Reflection stage wrote a Working Memory item while skill execution
wrote a `skill_action_audit` row: both durable, both audited, but two
structurally different records rather than one `Reflection` type flowing into
one sink (see `COGNITIVE_RUNTIME.md`'s "Two different Reflection mechanisms"
and Exit Gate question #4).

This module is that single type + sink. `ActionReflection` is the canonical
record; `record_action_reflection()` writes it to `MemoryStore`'s existing
`reflections` table under the `action_reflection` kind, alongside the
daily/weekly narrative reflections already stored there.

Deliberately additive: the surface-specific stores are unchanged. Working
Memory remains chat's short-term context buffer (it still feeds
`get_context_string()` for prior-turn content); `skill_action_audit` remains
the detailed, immediate compliance audit. What is now unified is the canonical
Reflection *shape* and its durable Memory *sink* -- the thing Exit Gate #4
asked for.

Privacy: reflections persist durably, so this record is PII-safe by
construction -- `to_memory_row()` runs `redact_pii()` over the summary and
every string value in `details`, matching the redaction `skill_action_audit`
already applies to its params.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .redaction_engine import redact_pii


if TYPE_CHECKING:
    from .memory_store import MemoryStore


logger = logging.getLogger(__name__)

# The `reflections`-table `kind` under which every per-action Reflection is
# stored, distinct from the "daily"/"weekly"/drive kinds already in that table.
REFLECTION_KIND = "action_reflection"


@dataclass
class ActionReflection:
    """
    One canonical Reflection for any action that passes through the Runtime
    Contract, regardless of surface.
    """

    surface: str
    """Where the action came from: "chat", "skill", ... (future: "voice", "sight")."""

    action: str
    """What was attempted: a chat CandidateAction kind, or "<skill_id>.<action>"."""

    outcome: str
    """How it ended: "responded", "governance_denied", a SkillResult status, ..."""

    summary: str
    """Short human-readable line. Redacted before persistence; keep it PII-light."""

    details: dict[str, Any] = field(default_factory=dict)
    """Structured specifics. String values are redacted before persistence."""

    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """When the action completed."""

    def to_memory_row(self) -> dict[str, Any]:
        """
        Map to the `reflections` table shape (kind/content/meta/ts), with PII
        redacted from the summary and every string value in details.
        """
        redacted_details = {
            key: (redact_pii(value) if isinstance(value, str) else value)
            for key, value in self.details.items()
        }
        meta = {
            "surface": self.surface,
            "action": self.action,
            "outcome": self.outcome,
            **redacted_details,
        }
        return {
            "kind": REFLECTION_KIND,
            "content": redact_pii(self.summary) or "",
            "meta": meta,
            "ts": self.ts.isoformat(),
        }


async def record_action_reflection(
    memory_store: MemoryStore | None,
    reflection: ActionReflection,
) -> int | None:
    """
    Persist a Reflection through the single shared Memory sink
    (`MemoryStore.reflections`). Returns the row id, or None if there is no
    store wired in.

    Best-effort by design: a reflection-write failure must never break the
    action that produced it (mirrors `SkillRegistry._audit_execution`'s own
    swallow-and-log posture), so any exception is logged and swallowed.
    """
    if memory_store is None:
        return None

    row = reflection.to_memory_row()
    try:
        return await memory_store.insert_reflection(
            kind=row["kind"],
            content=row["content"],
            meta=row["meta"],
            ts=row["ts"],
        )
    except Exception:
        logger.exception("Failed to record action reflection")
        return None
