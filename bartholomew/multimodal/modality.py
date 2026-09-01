"""The three multimodal capabilities, and the bounded scope a capture may have.

**Why three constants and not one flag.** The single most likely way this
package could betray a user is by collapsing "Bartholomew may talk to me" into
"Bartholomew may listen to me" into "Bartholomew may watch my screen". So the
three are separate values here, separate `Identity.yaml` allowlist entries,
separate consent prompts, separate policy decisions and separate brake checks.
There is deliberately no `MULTIMODAL_ENABLED`, no `Modality.ALL`, and no
helper that turns one modality into another -- `tests/test_multimodal_separation.py`
asserts the absence.

The capability kind strings are exactly the `kind` values of the frozen device
capability declaration (contract §3.3). They are matched against a device's
declared manifest, never approximated: an unknown kind or an unknown version is
unsupported, which denies.

**Capture scope is a closed shape.** A screen capture is bounded to exactly one
display, one window, or one rectangular region -- chosen when the session is
requested and frozen for the session's life. `CaptureScope` is frozen and
`covers()` is a containment test, so widening a scope means requesting a new
session with a new consent, not mutating an approved one. There is no
`FULL_DESKTOP` shortcut that skips naming a display.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Modality(str, Enum):
    """The three separately-permissioned multimodal capabilities."""

    #: Listening on a microphone for a bounded, explicit session.
    MICROPHONE = "microphone"
    #: Observing one bounded screen/window/region.
    SCREEN = "screen"
    #: Speaking text aloud on this machine's default output device.
    SPOKEN_OUTPUT = "spoken_output"


#: Capability kinds from the frozen device declaration (contract §3.3).
#: These are the strings a device manifest must declare, and the strings the
#: Identity policy allowlist is keyed on.
CAPABILITY_KIND: dict[Modality, str] = {
    Modality.MICROPHONE: "multimodal.microphone_session",
    Modality.SCREEN: "multimodal.screen_capture",
    Modality.SPOKEN_OUTPUT: "multimodal.spoken_output",
}

#: The capability version this package implements. A device declaring any
#: other version for one of these kinds is unsupported, not approximated.
CAPABILITY_VERSION = 1

#: Parking Brake scope per modality.
#:
#: These reuse the two device scopes the repository already registers
#: (`VALID_SCOPES` in the governance route) rather than inventing new brake
#: scopes, because the brake is an existing authority and this package is
#: forbidden from creating a second one. The mapping is deliberately
#: asymmetric in the safe direction: engaging "voice" stops both listening and
#: speaking, and engaging "sight" stops screen observation. A brake is a stop,
#: never a permission, so sharing a scope between two modalities can only ever
#: stop more than asked -- it can never let one modality authorise another.
#: Permission separation lives in consent and policy, which are per-kind.
BRAKE_SCOPE: dict[Modality, str] = {
    Modality.MICROPHONE: "voice",
    Modality.SCREEN: "sight",
    Modality.SPOKEN_OUTPUT: "voice",
}

#: The human sentence shown in the consent prompt for each modality. Each says
#: exactly what the modality does and nothing broader, so a person is never
#: asked to approve "multimodal access".
CONSENT_PROMPT: dict[Modality, str] = {
    Modality.MICROPHONE: (
        "Bartholomew requests to LISTEN on the microphone for one bounded "
        "session. This does not permit screen capture or speaking."
    ),
    Modality.SCREEN: (
        "Bartholomew requests to OBSERVE one specified screen, window or "
        "region for one bounded session. This does not permit listening or "
        "speaking."
    ),
    Modality.SPOKEN_OUTPUT: (
        "Bartholomew requests to SPEAK aloud on this machine. This does not "
        "permit listening or screen capture."
    ),
}


class ScopeKind(str, Enum):
    """What a screen capture is bounded to. Exactly one of three."""

    DISPLAY = "display"
    WINDOW = "window"
    REGION = "region"


@dataclass(frozen=True)
class CaptureScope:
    """One approved capture boundary, frozen for the life of its session.

    Exactly one target: a display index, a window handle/title, or a rectangle
    on a named display. `covers()` is how a later capture attempt proves it is
    inside what was approved; there is no setter, so an approved scope cannot
    be widened in place. Requesting a different scope means requesting a new
    session, with its own consent decision.
    """

    kind: ScopeKind
    #: Display identifier. Required for DISPLAY and REGION.
    display_id: str | None = None
    #: Opaque window identity. Required for WINDOW.
    window_id: str | None = None
    #: Window title recorded at approval time, for the visible status surface.
    window_title: str | None = None
    #: Rectangle (left, top, width, height). Required for REGION.
    rect: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        if self.kind is ScopeKind.DISPLAY and not self.display_id:
            raise ValueError("display scope requires display_id")
        if self.kind is ScopeKind.WINDOW and not self.window_id:
            raise ValueError("window scope requires window_id")
        if self.kind is ScopeKind.REGION:
            if not self.display_id:
                raise ValueError("region scope requires display_id")
            if self.rect is None:
                raise ValueError("region scope requires rect")
            if len(self.rect) != 4 or any(not isinstance(v, int) for v in self.rect):
                raise ValueError("region rect must be four ints (left, top, width, height)")
            if self.rect[2] <= 0 or self.rect[3] <= 0:
                raise ValueError("region rect must have positive width and height")

    def describe(self) -> str:
        """A short phrase an ordinary user can read on the status surface."""
        if self.kind is ScopeKind.DISPLAY:
            return f"display {self.display_id}"
        if self.kind is ScopeKind.WINDOW:
            title = self.window_title or self.window_id
            return f"window {title!r}"
        left, top, width, height = self.rect or (0, 0, 0, 0)
        return f"region {width}x{height} at ({left},{top}) on display {self.display_id}"

    def covers(self, other: CaptureScope) -> bool:
        """Whether `other` is inside this approved scope.

        Deliberately strict and deliberately not clever. A display does not
        cover a window (a window can be dragged to another display, so the
        containment cannot be proven from the scope alone), and a region only
        covers a region on the same display that is geometrically inside it.
        Anything not provably inside is not covered, so the answer to an
        ambiguous case is "no" -- which denies.
        """
        if self.kind is not other.kind:
            return False
        if self.kind is ScopeKind.DISPLAY:
            return self.display_id == other.display_id
        if self.kind is ScopeKind.WINDOW:
            return self.window_id == other.window_id
        if self.display_id != other.display_id:
            return False
        sl, st, sw, sh = self.rect or (0, 0, 0, 0)
        ol, ot, ow, oh = other.rect or (0, 0, 0, 0)
        return sl <= ol and st <= ot and (ol + ow) <= (sl + sw) and (ot + oh) <= (st + sh)
