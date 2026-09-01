"""The complete vocabulary of what a PC companion may observe.

This module is the structural argument that the companion is observation-only.
It defines a **closed** set of observation kinds and, for each kind, a **closed**
set of payload keys. `DeviceObservation.payload()` builds its dictionary from
that table and nothing else, so an observation carrying a key the table does
not name cannot be constructed -- not "is rejected downstream", cannot be
constructed.

Read the tables below and you have read every field the companion is capable of
sending. There is no free-form field, no passthrough dict, no `extra`, and no
`command`, `action` or `operation` of any kind. `tests/test_companion_no_actuation.py`
asserts those absences rather than trusting this docstring.

**Privacy minimisation is a property of the vocabulary, not of the collector.**
`FOREGROUND_APP` carries an application *name* and never a window title, URL,
document name or any content the application is displaying; `ACTIVITY` carries
active/idle and a coarse idle duration and never keystrokes, key counts or
input content. A collector that wanted to send more would have nowhere to put it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

#: Namespace for every `event_type` this companion emits. The inbound seam
#: treats event_type as an opaque string and never branches on it; this prefix
#: exists for human inspection and for provenance, not for dispatch.
EVENT_TYPE_PREFIX = "device.companion"

#: How long an application name may be. A name is an identifier, not content;
#: anything longer is a sign the collector picked up a window title, so it is
#: truncated rather than sent.
MAX_APPLICATION_NAME = 64


class ObservationKind(str, Enum):
    """The only things the companion may report. A closed set, by design."""

    #: The companion process itself is running / shutting down.
    PRESENCE = "presence"
    #: Whether the person is currently interacting with the machine.
    ACTIVITY = "activity"
    #: Which application currently has focus, by name only.
    FOREGROUND_APP = "foreground_app"
    #: A very small amount of static host state.
    SYSTEM_STATE = "system_state"


#: The exhaustive per-kind payload key allowlist. `payload()` iterates this;
#: adding a key anywhere else has no effect, and adding one here is a visible,
#: reviewable change to the privacy surface.
ALLOWED_PAYLOAD_KEYS: dict[ObservationKind, frozenset[str]] = {
    ObservationKind.PRESENCE: frozenset({"state"}),
    ObservationKind.ACTIVITY: frozenset({"state", "idle_seconds"}),
    ObservationKind.FOREGROUND_APP: frozenset({"application"}),
    ObservationKind.SYSTEM_STATE: frozenset({"platform", "companion_version"}),
}

#: Every payload key the companion can ever emit, across all kinds, plus the
#: provenance key added to all of them. The union is asserted in the tests, so
#: widening the surface cannot happen quietly.
ALL_PAYLOAD_KEYS: frozenset[str] = frozenset({"device_id"}).union(
    *ALLOWED_PAYLOAD_KEYS.values(),
)

PRESENCE_STATES = frozenset({"online", "offline"})
ACTIVITY_STATES = frozenset({"active", "idle"})
PLATFORMS = frozenset({"windows", "darwin", "linux", "unknown"})

#: Application names are normalised to this shape before they are sent: the
#: executable's base name, lowercased, extension dropped. `chrome`, never
#: `C:\Users\taylor\...\chrome.exe` (a path leaks a username) and never the
#: window title (which is content).
_APP_SAFE = re.compile(r"[^a-z0-9._+-]+")


class ObservationError(ValueError):
    """An observation could not be built within the permitted vocabulary."""


def normalise_application(raw: str) -> str:
    """Reduce whatever a platform probe reports to a bare application name.

    Deliberately lossy, and lossy in the privacy-preserving direction: the path
    is discarded (it usually contains the account name), the extension is
    discarded, and the result is truncated. If a probe hands this a window
    title, what survives is a short lowercased token, not a sentence.
    """
    name = str(raw).strip().replace("\\", "/").rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    name = _APP_SAFE.sub("-", name.lower()).strip("-")
    if not name:
        raise ObservationError("application name normalised to empty")
    return name[:MAX_APPLICATION_NAME]


def utc_now_iso() -> str:
    """Second-resolution UTC timestamp, matching the inbound store's format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class DeviceObservation:
    """One bounded observation, ready to be turned into an inbound envelope.

    Frozen: an observation is a record of something that was true at a moment,
    and nothing downstream may edit it before it is submitted.

    `sequence` is the companion's own monotonic counter. It is what makes the
    derived event id stable across a retry (same sequence, same id) and
    distinct across two genuinely different observations of the same kind.
    """

    kind: ObservationKind
    device_id: str
    sequence: int
    observed_at: str
    #: Kind-specific values, validated against `ALLOWED_PAYLOAD_KEYS`.
    values: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.device_id or not self.device_id.strip():
            raise ObservationError("device_id must not be blank")
        if self.sequence < 0:
            raise ObservationError("sequence must not be negative")
        allowed = ALLOWED_PAYLOAD_KEYS[self.kind]
        extra = set(self.values) - allowed
        if extra:
            raise ObservationError(
                f"{self.kind.value} may not carry {sorted(extra)}; "
                f"the permitted keys are {sorted(allowed)}",
            )
        self._validate_values()

    def _validate_values(self) -> None:
        v = self.values
        if self.kind is ObservationKind.PRESENCE and v.get("state") not in PRESENCE_STATES:
            raise ObservationError(f"presence.state must be one of {sorted(PRESENCE_STATES)}")
        if self.kind is ObservationKind.ACTIVITY:
            if v.get("state") not in ACTIVITY_STATES:
                raise ObservationError(f"activity.state must be one of {sorted(ACTIVITY_STATES)}")
            idle = v.get("idle_seconds")
            if idle is not None and (not isinstance(idle, int) or idle < 0):
                raise ObservationError("activity.idle_seconds must be a non-negative int or None")
        if self.kind is ObservationKind.FOREGROUND_APP:
            app = v.get("application")
            if not isinstance(app, str) or not app:
                raise ObservationError("foreground_app.application must be a non-empty string")
            if len(app) > MAX_APPLICATION_NAME:
                raise ObservationError("foreground_app.application is too long to be a name")
        if self.kind is ObservationKind.SYSTEM_STATE and v.get("platform") not in PLATFORMS:
            raise ObservationError(f"system_state.platform must be one of {sorted(PLATFORMS)}")

    @property
    def event_type(self) -> str:
        return f"{EVENT_TYPE_PREFIX}.{self.kind.value}"

    def payload(self) -> dict[str, Any]:
        """The payload as it will be submitted.

        Built key-by-key from the allowlist. `device_id` is included on every
        kind because it is this observation's *claimed* device provenance --
        see `envelope.py` for why "claimed" is the honest word for it.
        """
        allowed = ALLOWED_PAYLOAD_KEYS[self.kind]
        payload: dict[str, Any] = {"device_id": self.device_id}
        for key in sorted(allowed):
            if key in self.values:
                payload[key] = self.values[key]
        return payload


def presence(device_id: str, sequence: int, *, online: bool, observed_at: str | None = None):
    return DeviceObservation(
        kind=ObservationKind.PRESENCE,
        device_id=device_id,
        sequence=sequence,
        observed_at=observed_at or utc_now_iso(),
        values={"state": "online" if online else "offline"},
    )


def activity(
    device_id: str,
    sequence: int,
    *,
    active: bool,
    idle_seconds: int | None = None,
    observed_at: str | None = None,
):
    return DeviceObservation(
        kind=ObservationKind.ACTIVITY,
        device_id=device_id,
        sequence=sequence,
        observed_at=observed_at or utc_now_iso(),
        values={"state": "active" if active else "idle", "idle_seconds": idle_seconds},
    )


def foreground_app(
    device_id: str,
    sequence: int,
    *,
    application: str,
    observed_at: str | None = None,
):
    return DeviceObservation(
        kind=ObservationKind.FOREGROUND_APP,
        device_id=device_id,
        sequence=sequence,
        observed_at=observed_at or utc_now_iso(),
        values={"application": normalise_application(application)},
    )


def system_state(
    device_id: str,
    sequence: int,
    *,
    platform_name: str,
    companion_version: str,
    observed_at: str | None = None,
):
    return DeviceObservation(
        kind=ObservationKind.SYSTEM_STATE,
        device_id=device_id,
        sequence=sequence,
        observed_at=observed_at or utc_now_iso(),
        values={
            "platform": platform_name if platform_name in PLATFORMS else "unknown",
            "companion_version": companion_version,
        },
    )
