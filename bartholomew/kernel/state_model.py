from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class WorldState:
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_water_ts: datetime | None = None
    user_activity: str | None = None  # e.g., "driving", "cooking", etc.
