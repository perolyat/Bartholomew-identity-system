"""The bounded screenshot fallback -- used only when structure is not enough.

**This is the second choice, and it has to prove it.** `capture_with_fallback()`
reads the accessibility tree first, every time. It reaches the pixel path only
when all three of the following hold, and it records all three:

1. the accessibility reading was genuinely insufficient (the reason is stored
   verbatim in `fallback_reason`);
2. the approved session explicitly permits the screenshot fallback
   (`allow_screenshot_fallback`, which is a separate decision made when the
   session is requested -- approving screen observation does not by itself
   approve pixels);
3. the requested capture target is inside the scope that was approved.

If any fails, no image is taken and the accessibility observation is returned
on its own, with `fallback_refused_reason` saying which.

**Scope cannot widen silently.** The requested target is checked against the
frozen `CaptureScope` with `covers()`. A request for a different window, a
different display, or a larger region than approved is refused -- it does not
capture "the nearest approved thing" and it does not fall back to the full
desktop. `tests/test_multimodal_scope.py` covers each widening attempt.

**Raw images are not persisted, and there is no switch here that changes
that.** The backend hands back an image object; `derive_description()` turns
it into a bounded text description and the reference to the image is dropped
before the function returns. `ScreenObservation` has no image field, no path
field and no bytes field, so there is nothing for a caller to write out.
Contract §7 puts raw retention behind a separate governed policy, which this
package does not implement and cannot be configured into.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .accessibility import (
    AccessibilityObservation,
    AccessibilityProvider,
    observe_active_window,
)
from .modality import CaptureScope
from .privacy import (
    MAX_OBSERVATION_CHARS,
    Classification,
    PrivacyClass,
    RetentionClass,
    sanitise,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureRefusedError(Exception):
    """A capture was asked for outside what the session approved."""

    reason: str

    def __str__(self) -> str:
        return self.reason


@runtime_checkable
class ScreenBackend(Protocol):
    """Grabs one bounded image, and describes it. No file paths anywhere."""

    def available(self) -> tuple[bool, str]: ...

    def grab(self, scope: CaptureScope) -> Any: ...

    def describe(self, image: Any) -> str: ...


class NullScreenBackend:
    """For a machine with no capture support. Truthfully unavailable."""

    def __init__(self, reason: str = "no screen capture backend is installed") -> None:
        self._reason = reason

    def available(self) -> tuple[bool, str]:
        return False, self._reason

    def grab(self, scope: CaptureScope) -> Any:
        raise RuntimeError(self._reason)

    def describe(self, image: Any) -> str:
        raise RuntimeError(self._reason)


def default_backend() -> ScreenBackend:
    """`mss` if it is installed, otherwise an honest Null backend."""
    try:
        import mss  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on host packages
        return NullScreenBackend(f"optional dependency 'mss' is unavailable: {exc}")
    return _MssScreenBackend()  # pragma: no cover - requires a display


class _MssScreenBackend:  # pragma: no cover - requires a display
    """A thin `mss` wrapper. Not exercised by CI; see closeout."""

    def available(self) -> tuple[bool, str]:
        try:
            import mss  # noqa: F401
        except Exception as exc:
            return False, str(exc)
        return True, "mss present"

    def grab(self, scope: CaptureScope) -> Any:
        try:
            import mss
        except Exception as exc:
            # Importable at availability-check time and not now: the caller
            # records this as a refused fallback, never as a silent no-op.
            raise RuntimeError(f"the screen capture backend became unavailable: {exc}") from exc

        from .modality import ScopeKind

        with mss.mss() as sct:
            if scope.kind is ScopeKind.REGION and scope.rect:
                left, top, width, height = scope.rect
                return sct.grab(
                    {"left": left, "top": top, "width": width, "height": height},
                )
            index = int(scope.display_id or 1)
            return sct.grab(sct.monitors[index])

    def describe(self, image: Any) -> str:
        # No vision model ships with this package. Reporting the bounded
        # geometric facts is honest; inventing a description of contents this
        # code has not analysed would be exactly the truth inflation
        # invariant 15 forbids.
        return f"image {getattr(image, 'width', '?')}x{getattr(image, 'height', '?')}"


@dataclass
class ScreenObservation:
    """A derived, bounded, classified description. Never an image.

    `evidence_reference` is a digest of the derived description plus the scope
    -- enough to prove later that this record is the one that was produced,
    without retaining anything that was on the screen.
    """

    scope_description: str
    accessibility: AccessibilityObservation
    used_screenshot: bool
    description: str | None = None
    fallback_reason: str | None = None
    fallback_refused_reason: str | None = None
    classification: Classification = field(default_factory=Classification)
    evidence_reference: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope_description,
            "used_screenshot": self.used_screenshot,
            "description": self.description,
            "fallback_reason": self.fallback_reason,
            "fallback_refused_reason": self.fallback_refused_reason,
            "evidence_reference": self.evidence_reference,
            "detail": self.detail,
            "accessibility": self.accessibility.as_dict(),
            **self.classification.as_dict(),
        }


def _evidence_reference(scope: CaptureScope, description: str) -> str:
    """A digest binding the derived description to the scope it came from."""
    digest = hashlib.sha256(f"{scope.describe()}|{description}".encode()).hexdigest()
    return f"sha256:{digest[:32]}"


def capture_with_fallback(
    *,
    approved_scope: CaptureScope,
    requested_scope: CaptureScope | None = None,
    allow_screenshot_fallback: bool = False,
    accessibility_provider: AccessibilityProvider | None = None,
    screen_backend: ScreenBackend | None = None,
) -> ScreenObservation:
    """Observe one approved target: structure first, pixels only if justified.

    `requested_scope` defaults to the approved scope. Passing a different one
    is how a caller asks for something narrower -- and how a caller asking for
    something *wider* gets refused.
    """
    target = requested_scope or approved_scope
    if not approved_scope.covers(target):
        raise CaptureRefusedError(
            f"requested capture of {target.describe()} is outside the approved "
            f"scope ({approved_scope.describe()}); a wider scope needs a new "
            f"session and a new consent decision",
        )

    accessibility = observe_active_window(accessibility_provider)

    # Preference, enforced: if structure sufficed, the pixel path is not
    # reached at all and nothing is captured.
    if accessibility.sufficient_for():
        return ScreenObservation(
            scope_description=target.describe(),
            accessibility=accessibility,
            used_screenshot=False,
            classification=accessibility.classification,
            detail="accessibility observation was sufficient; no image was captured",
        )

    reason = accessibility.insufficiency_reason() or "accessibility observation insufficient"

    if not allow_screenshot_fallback:
        return ScreenObservation(
            scope_description=target.describe(),
            accessibility=accessibility,
            used_screenshot=False,
            fallback_reason=reason,
            fallback_refused_reason=(
                "this session did not authorise the screenshot fallback; no image was captured"
            ),
            classification=accessibility.classification,
        )

    backend = screen_backend or default_backend()
    try:
        ok, backend_detail = backend.available()
    except Exception as exc:
        logger.exception("Screen backend availability check failed")
        ok, backend_detail = False, str(exc)
    if not ok:
        return ScreenObservation(
            scope_description=target.describe(),
            accessibility=accessibility,
            used_screenshot=False,
            fallback_reason=reason,
            fallback_refused_reason=f"screen capture unavailable: {backend_detail}",
            classification=accessibility.classification,
        )

    classification = Classification(
        privacy_class=PrivacyClass.SENSITIVE,
        retention_class=RetentionClass.EPHEMERAL,
    )
    try:
        # The image exists only inside this block. Nothing outside it holds a
        # reference, and no path is ever opened for writing.
        image = backend.grab(target)
        try:
            raw_description = backend.describe(image)
        finally:
            del image
    except Exception as exc:
        logger.exception("Screen capture failed")
        return ScreenObservation(
            scope_description=target.describe(),
            accessibility=accessibility,
            used_screenshot=False,
            fallback_reason=reason,
            fallback_refused_reason=f"screen capture failed: {exc}",
            classification=accessibility.classification,
        )

    description = sanitise(
        raw_description or "",
        "screen.description",
        classification,
        MAX_OBSERVATION_CHARS,
    )
    return ScreenObservation(
        scope_description=target.describe(),
        accessibility=accessibility,
        used_screenshot=True,
        description=description,
        fallback_reason=reason,
        classification=classification,
        evidence_reference=_evidence_reference(target, description),
        detail="screenshot fallback was used because accessibility was insufficient",
    )
