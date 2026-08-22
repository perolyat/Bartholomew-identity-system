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


@dataclass
class ReflectionWriteOutcome:
    """What became of one Reflection write through the shared sink.

    WP-A2b. `row_id` is the persisted `reflections` row id; `error` is the
    verbatim failure when the write was attempted and did not persist. The
    two are mutually exclusive; both are None when no store was wired in
    (an accepted configuration for duck-typed contexts, not a failure).

    Which callers must *act* on `error` is a per-surface decision, recorded
    in `DECISIONS.md` ("One Reflection sink, two semantic roles"): on the
    provenance-bearing surfaces (chat, training, sight/voice) the Reflection
    is the sole durable record of the governed decision, so a lost write
    must reach the caller's result contract; on the additive surfaces
    (skill, awaiting_response, scheduler) another authoritative durable
    record exists and ignoring this outcome is the approved behaviour.
    """

    row_id: int | None = None
    error: str | None = None

    @property
    def persisted(self) -> bool:
        return self.error is None


async def record_action_reflection(
    memory_store: MemoryStore | None,
    reflection: ActionReflection,
) -> ReflectionWriteOutcome:
    """
    Persist a Reflection through the single shared Memory sink
    (`MemoryStore.reflections`), and report what happened.

    Never raises: a reflection-write failure must never break the action
    that produced it, on any surface. What changed in WP-A2b is only the
    *silence* — the failure used to be logged and discarded here, which on
    the provenance-bearing surfaces meant the sole durable record of a
    governed decision could be lost with the caller none the wiser. The
    outcome now says truthfully whether the row persisted; each surface's
    seam decides (per the recorded per-surface classification) whether that
    outcome must reach its result contract.

    A ``memory_store`` of None is reported as neither persisted-with-id nor
    errored — no write was attempted. That is the pre-existing accepted
    configuration for duck-typed test contexts; a provenance surface that
    can reach this state with real data at stake should treat constructing
    its store as part of its own persistence responsibility (see
    `_record_device_reflection`).
    """
    if memory_store is None:
        return ReflectionWriteOutcome()

    row = reflection.to_memory_row()
    try:
        row_id = await memory_store.insert_reflection(
            kind=row["kind"],
            content=row["content"],
            meta=row["meta"],
            ts=row["ts"],
        )
    except Exception as exc:
        # ERROR naming surface and action: on the provenance surfaces this
        # is a lost sole record, not a background hiccup.
        logger.error(
            "REFLECTION WRITE FAILED for surface=%s action=%s: %s",
            reflection.surface,
            reflection.action,
            exc,
        )
        return ReflectionWriteOutcome(
            error=f"reflection write failed ({reflection.surface}): {exc}",
        )
    return ReflectionWriteOutcome(row_id=row_id)
