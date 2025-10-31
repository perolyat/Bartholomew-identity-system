from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone


@dataclass
class WorldState:
    now: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_water_ts: Optional[datetime] = None
    user_activity: Optional[str] = None  # e.g., "driving", "cooking", etc.
