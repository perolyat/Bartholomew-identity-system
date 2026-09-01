"""The companion's own tiny durable state: a sequence number and a cursor.

Deliberately a single small JSON file rather than a database. The companion is a
client, not a second store of Bartholomew's truth; the authoritative record of
what was observed lives in `inbound_events` on the Bartholomew side. All this
file has to do is let a restarted companion (a) not reuse a sequence number, and
(b) finish delivering the one observation that may have been in flight when the
process died.

Restart behaviour is the reason `pending` exists. Without it, a companion that
crashed between "submitted" and "acknowledged" would either drop the observation
or, if it retried with a fresh sequence, deliver it twice as two distinct
events. With it, the retry reuses the same sequence, derives the same
`event_id`, and the inbound seam's UNIQUE constraint collapses it onto the
existing row.

A corrupt or unreadable file is not fatal and is not silently trusted either:
the companion starts from a fresh, *higher* sequence origin so it cannot collide
with ids it may already have delivered under the lost state.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CompanionState:
    """What survives a companion restart."""

    #: Next sequence number to assign. Monotonic within a state file.
    sequence: int = 0
    #: An envelope that was built and possibly submitted, but not confirmed.
    pending: dict[str, Any] | None = None
    #: Purely informational, for an operator reading the file.
    updated_at: float = field(default_factory=time.time)


class StateFile:
    """Load/save `CompanionState`, atomically."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    def load(self) -> CompanionState:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return CompanionState()
        except (OSError, ValueError):
            # Unreadable or corrupt. Jump the sequence origin forward rather
            # than restarting at 0: ids derived from a reused sequence would
            # be indistinguishable from ids already delivered.
            logger.warning(
                "Companion state at %s is unreadable; starting from a fresh sequence origin",
                self.path,
            )
            return CompanionState(sequence=int(time.time()))
        if not isinstance(raw, dict):
            return CompanionState(sequence=int(time.time()))
        pending = raw.get("pending")
        return CompanionState(
            sequence=int(raw.get("sequence", 0) or 0),
            pending=pending if isinstance(pending, dict) else None,
            updated_at=float(raw.get("updated_at", 0.0) or 0.0),
        )

    def save(self, state: CompanionState) -> None:
        """Write via a temp file and rename, so a crash never leaves a half file."""
        state.updated_at = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".companion-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(asdict(state), fh, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
