"""Accessibility-tree observation -- the preferred way to understand a screen.

Contract §3.6: "Accessibility tree is preferred over pixels for UI
understanding." That preference is not a style note; it is a privacy control.
The accessibility tree gives structure -- this is a button, that is a text
box, this one is focused -- which is what an assistant actually needs, while a
screenshot gives every pixel that happened to be on the display, including the
ones next to the thing being looked at. Asking the tree first means the
screenshot path is rarely reached, and when it is reached the reason is
recorded.

**The observation is bounded by construction.** A tree can be enormous; this
module keeps a bounded number of elements, each with a bounded amount of text,
and records that it truncated. Every string passes through `privacy.sanitise`,
and any control that looks like a secret field has its value omitted entirely
rather than read and redacted -- `omitted_secret_fields` counts them so a
reader knows something was skipped.

**Incompleteness is reported, not smoothed over.** `AccessibilityObservation.
complete` is False whenever the provider could only partly read the tree, and
`sufficient_for()` is the honest test the screen path consults before falling
back to pixels. A provider that is not installed yields an unavailable
observation, not an empty one that looks like a blank screen.

The provider is injected for the same reason the audio backend is: importing
this module must not pull in `pywinauto`/`uiautomation` or any other native
dependency, and CI has no Windows UI to read.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .privacy import (
    MAX_ELEMENT_CHARS,
    MAX_TEXT_ELEMENTS,
    Classification,
    PrivacyClass,
    RetentionClass,
    is_secret_field,
    sanitise,
)

logger = logging.getLogger(__name__)


@dataclass
class AccessibleElement:
    """One control, reduced to what an assistant needs to understand it."""

    role: str
    name: str
    #: None when the control is a secret field: the value is never read.
    value: str | None = None
    focused: bool = False
    selected: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "name": self.name,
            "value": self.value,
            "focused": self.focused,
            "selected": self.selected,
        }


@dataclass
class AccessibilityObservation:
    """A bounded, classified reading of the accessibility tree."""

    available: bool
    complete: bool
    application: str | None = None
    window_id: str | None = None
    window_title: str | None = None
    elements: list[AccessibleElement] = field(default_factory=list)
    focused_element: AccessibleElement | None = None
    classification: Classification = field(default_factory=Classification)
    omitted_secret_fields: int = 0
    detail: str | None = None

    def sufficient_for(self, purpose: str | None = None) -> bool:
        """Whether this reading makes a screenshot unnecessary.

        The bar is deliberately concrete: the provider was available, the
        reading was complete, and it actually yielded something to reason
        about (a window identity and at least one element). Anything less is
        insufficient, which is what authorises considering the fallback -- and
        the reason is carried in `detail` so the fallback can record why.
        """
        return bool(
            self.available and self.complete and self.window_id and self.elements,
        )

    def insufficiency_reason(self) -> str | None:
        """Why a screenshot might be needed. None when the reading sufficed."""
        if not self.available:
            return f"accessibility provider unavailable: {self.detail or 'unknown'}"
        if not self.complete:
            return f"accessibility tree incomplete: {self.detail or 'partial read'}"
        if not self.window_id:
            return "accessibility tree yielded no window identity"
        if not self.elements:
            return "accessibility tree yielded no readable controls"
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "complete": self.complete,
            "application": self.application,
            "window_id": self.window_id,
            "window_title": self.window_title,
            "elements": [e.as_dict() for e in self.elements],
            "focused_element": self.focused_element.as_dict() if self.focused_element else None,
            "omitted_secret_fields": self.omitted_secret_fields,
            "detail": self.detail,
            **self.classification.as_dict(),
        }


@runtime_checkable
class AccessibilityProvider(Protocol):
    """Reads the raw accessibility tree for the active window.

    Returns a plain dict so the provider needs no knowledge of this package's
    types: `{"application", "window_id", "window_title", "complete",
    "elements": [{"role", "name", "value", "focused", "selected"}, ...]}`.
    """

    def available(self) -> tuple[bool, str]: ...

    def read_active_window(self) -> dict[str, Any]: ...


class NullAccessibilityProvider:
    """For a machine with no UI Automation. Reports unavailable, truthfully."""

    def __init__(self, reason: str = "no accessibility provider is installed") -> None:
        self._reason = reason

    def available(self) -> tuple[bool, str]:
        return False, self._reason

    def read_active_window(self) -> dict[str, Any]:
        raise RuntimeError(self._reason)


def default_provider() -> AccessibilityProvider:
    """The provider for this machine. Windows UI Automation, or nothing.

    `uiautomation` is an optional dependency; on any non-Windows machine, and
    on a Windows machine without it installed, this returns the Null provider
    with the reason, which makes every observation honestly unavailable.
    """
    try:
        import uiautomation  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on host packages
        return NullAccessibilityProvider(
            f"optional dependency 'uiautomation' is unavailable: {exc}",
        )
    return _UIAutomationProvider()  # pragma: no cover - requires Windows


class _UIAutomationProvider:  # pragma: no cover - requires Windows + UIA
    """A thin Windows UI Automation reader.

    Not exercised by CI (no Windows, no UI). Declared simulated-only in the
    closeout rather than claimed as hardware-verified.
    """

    def available(self) -> tuple[bool, str]:
        try:
            import uiautomation  # noqa: F401
        except Exception as exc:
            return False, str(exc)
        return True, "uiautomation present"

    def read_active_window(self) -> dict[str, Any]:
        try:
            import uiautomation
        except Exception as exc:
            # Importable at availability-check time and not now: an incomplete
            # read, which `observe_active_window` reports as unavailable.
            raise RuntimeError(f"the accessibility provider became unavailable: {exc}") from exc

        window = uiautomation.GetForegroundControl()
        if window is None:
            return {"complete": False, "elements": []}
        elements: list[dict[str, Any]] = []
        try:
            for child in window.GetChildren()[:MAX_TEXT_ELEMENTS]:
                elements.append(
                    {
                        "role": child.ControlTypeName,
                        "name": child.Name,
                        "value": getattr(child, "GetValuePattern", lambda: None)(),
                        "focused": bool(child.HasKeyboardFocus),
                        "selected": False,
                    },
                )
            complete = True
        except Exception as exc:
            logger.warning("Partial accessibility read: %s", exc)
            complete = False
        return {
            "application": getattr(window, "ProcessId", None) and window.ClassName,
            "window_id": str(getattr(window, "NativeWindowHandle", "")) or None,
            "window_title": window.Name,
            "complete": complete,
            "elements": elements,
        }


def observe_active_window(
    provider: AccessibilityProvider | None = None,
) -> AccessibilityObservation:
    """Read, bound, classify and redact the active window's accessibility tree.

    Never raises: a provider that explodes becomes an unavailable observation
    with the error as its detail, which the screen path then treats as a
    recorded reason for considering the fallback.
    """
    provider = provider or default_provider()
    classification = Classification(
        privacy_class=PrivacyClass.SENSITIVE,
        retention_class=RetentionClass.EPHEMERAL,
    )
    try:
        ok, reason = provider.available()
    except Exception as exc:
        logger.exception("Accessibility availability check failed")
        ok, reason = False, str(exc)
    if not ok:
        return AccessibilityObservation(
            available=False,
            complete=False,
            classification=classification,
            detail=reason,
        )

    try:
        raw = provider.read_active_window()
    except Exception as exc:
        logger.exception("Accessibility read failed")
        return AccessibilityObservation(
            available=False,
            complete=False,
            classification=classification,
            detail=str(exc),
        )

    elements: list[AccessibleElement] = []
    focused: AccessibleElement | None = None
    omitted = 0
    raw_elements = list(raw.get("elements") or [])
    truncated = len(raw_elements) > MAX_TEXT_ELEMENTS

    for index, item in enumerate(raw_elements[:MAX_TEXT_ELEMENTS]):
        role = str(item.get("role") or "unknown")
        name = sanitise(
            str(item.get("name") or ""),
            f"a11y.element[{index}].name",
            classification,
            MAX_ELEMENT_CHARS,
        )
        # A secret field's value is never read into the observation at all --
        # not read-then-redacted. There is nothing to leak downstream.
        if is_secret_field(item.get("name"), role):
            value = None
            omitted += 1
            classification.note(f"a11y.element[{index}].value", "secret_field_omitted")
            classification.escalate(PrivacyClass.RESTRICTED)
        else:
            raw_value = item.get("value")
            value = (
                sanitise(
                    str(raw_value),
                    f"a11y.element[{index}].value",
                    classification,
                    MAX_ELEMENT_CHARS,
                )
                if raw_value is not None
                else None
            )
        element = AccessibleElement(
            role=role,
            name=name,
            value=value,
            focused=bool(item.get("focused")),
            selected=bool(item.get("selected")),
        )
        elements.append(element)
        if element.focused and focused is None:
            focused = element

    if truncated:
        classification.truncated = True
        classification.note("a11y.elements", "element_count_bound")

    window_title = raw.get("window_title")
    return AccessibilityObservation(
        available=True,
        complete=bool(raw.get("complete")) and not truncated,
        application=(str(raw["application"]) if raw.get("application") else None),
        window_id=(str(raw["window_id"]) if raw.get("window_id") else None),
        window_title=(
            sanitise(str(window_title), "a11y.window_title", classification, MAX_ELEMENT_CHARS)
            if window_title
            else None
        ),
        elements=elements,
        focused_element=focused,
        classification=classification,
        omitted_secret_fields=omitted,
        detail=None if raw.get("complete") else "provider reported a partial read",
    )
