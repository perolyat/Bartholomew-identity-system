"""The canonical event envelope: one versioned shape for a captured event.

Capture already records everything an envelope needs -- `inbound_events` has
the source, the event id, the opaque type, both timestamps, the digest of
exactly what was accepted, what verified the sender, and which runtime it
belongs to. This module does **not** add a second record of any of that. It
gives those columns one named, versioned, in-memory shape so that processing
code has a single thing to accept, and so a future change to what processing
needs is a version bump rather than a silent reinterpretation of old rows.

Compatibility with existing records is a requirement, not an aspiration:
`from_inbound_row()` reads a row written before this module existed and
produces a valid envelope. `envelope_version` is therefore *derived*, not
stored on the inbound row -- version 1 is defined as "exactly the columns
`inbound_events` already had".

Deliberately duck-type-compatible with `StoredInboundEvent`
--------------------------------------------------------
`bartholomew.kernel.inbound_interpretation.interpret_captured_event()` takes
a `stored` object and reads it entirely through `getattr`. A `CanonicalEvent`
satisfies that contract (`row_id`, `outcome`, `duplicate`, and the rest), so
the backbone hands the existing interpretation seam its own canonical
envelope rather than reconstructing a second object that could drift from it.
The two synthesised attributes are honest:

* `outcome` is `captured` because only durably-captured rows are ever
  enqueued for processing -- a refused row has no processing state at all.
* `duplicate` is False because "duplicate" there means *this capture call was
  a redelivery*, which is a property of a capture call and not of a row read
  back later. Whether a *processing* pass is a repeat is the backbone's own
  state machine's question, and it answers it from `event_processing`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: The envelope shape this build writes and understands.
ENVELOPE_VERSION = 1

#: Every version this build can read. An envelope stamped with anything else
#: is refused rather than guessed at -- see `is_supported_version`.
SUPPORTED_ENVELOPE_VERSIONS = frozenset({1})


class EnvelopeVersionError(ValueError):
    """A stored envelope version this build does not understand.

    Raised rather than defaulted: reading a future envelope as if it were
    version 1 is how a field that changed meaning becomes a silent data bug.
    """


def is_supported_version(version: Any) -> bool:
    try:
        return int(version) in SUPPORTED_ENVELOPE_VERSIONS
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class CanonicalEvent:
    """One captured external event, in the shape processing consumes.

    Immutable. Nothing in the processing path may edit an event in flight --
    the durable record of what arrived is `inbound_events`, and this is a
    reading of it, not a working copy.
    """

    envelope_version: int
    source_id: str
    event_id: str
    event_type: str
    payload: Any
    payload_sha256: str
    received_at: str
    verified_by: str
    occurred_at: str | None = None
    runtime_id: str | None = None
    inbound_row_id: int | None = None

    # -- identity ---------------------------------------------------------

    @property
    def idempotency_key(self) -> str:
        """`(source_id, event_id)`, the same pair capture made UNIQUE.

        One key, defined once, so capture's idempotency and processing's
        idempotency cannot disagree about what "the same event" means.
        """
        return f"{self.source_id}\x1f{self.event_id}"

    # -- StoredInboundEvent duck-type (see the module docstring) ----------

    @property
    def row_id(self) -> int | None:
        return self.inbound_row_id

    @property
    def outcome(self) -> str:
        from bartholomew.kernel.inbound_store import OUTCOME_CAPTURED

        return OUTCOME_CAPTURED

    @property
    def duplicate(self) -> bool:
        return False

    # -- rendering --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The envelope without its payload.

        The payload is deliberately omitted: this shape is used in logs, tick
        metadata and health output, and none of those is a place to re-emit
        third-party content. `payload_sha256` identifies what was accepted.
        """
        return {
            "envelope_version": self.envelope_version,
            "source_id": self.source_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "received_at": self.received_at,
            "payload_sha256": self.payload_sha256,
            "verified_by": self.verified_by,
            "runtime_id": self.runtime_id,
            "inbound_row_id": self.inbound_row_id,
        }

    # -- construction -----------------------------------------------------

    @classmethod
    def from_inbound_row(
        cls,
        row: Any,
        payload: Any,
        *,
        envelope_version: int = ENVELOPE_VERSION,
    ) -> CanonicalEvent:
        """Build an envelope from an `inbound_events` row and its payload.

        `row` may be a `StoredInboundEvent`, a mapping, or anything else with
        the same attribute names -- capture is the authority on that record's
        shape and this reads it rather than re-deriving it.
        """
        if not is_supported_version(envelope_version):
            raise EnvelopeVersionError(
                f"envelope version {envelope_version!r} is not one this build "
                f"understands ({sorted(SUPPORTED_ENVELOPE_VERSIONS)})",
            )

        def field(name: str, *aliases: str) -> Any:
            for candidate in (name, *aliases):
                if isinstance(row, dict):
                    if candidate in row:
                        return row[candidate]
                elif hasattr(row, candidate):
                    return getattr(row, candidate)
            return None

        source_id = field("source_id")
        event_id = field("event_id")
        event_type = field("event_type")
        payload_sha256 = field("payload_sha256")
        received_at = field("received_at")
        verified_by = field("verified_by")
        missing = [
            name
            for name, value in (
                ("source_id", source_id),
                ("event_id", event_id),
                ("event_type", event_type),
                ("payload_sha256", payload_sha256),
                ("received_at", received_at),
                ("verified_by", verified_by),
            )
            if value in (None, "")
        ]
        if missing:
            raise ValueError(
                "an inbound record is missing the fields a canonical envelope "
                f"requires: {', '.join(missing)}",
            )

        return cls(
            envelope_version=int(envelope_version),
            source_id=str(source_id),
            event_id=str(event_id),
            event_type=str(event_type),
            payload=payload,
            payload_sha256=str(payload_sha256),
            received_at=str(received_at),
            verified_by=str(verified_by),
            occurred_at=field("occurred_at"),
            runtime_id=field("runtime_id"),
            inbound_row_id=field("row_id", "id", "inbound_row_id"),
        )


def payload_matches_digest(event: CanonicalEvent) -> bool:
    """Whether the loaded payload still hashes to the digest capture recorded.

    Cheap tamper/corruption check on the one thing processing actually reads.
    A mismatch means the row was altered after capture recorded what it
    accepted, which is a refusal condition rather than something to interpret.
    """
    from bartholomew.kernel.inbound_store import payload_digest

    try:
        _, digest = payload_digest(event.payload)
    except (TypeError, ValueError):
        return False
    return digest == event.payload_sha256


def canonical_payload_json(payload: Any) -> str:
    """The payload as capture stores it. Reused so there is one canonicaliser."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "ENVELOPE_VERSION",
    "SUPPORTED_ENVELOPE_VERSIONS",
    "CanonicalEvent",
    "EnvelopeVersionError",
    "canonical_payload_json",
    "is_supported_version",
    "payload_matches_digest",
]
