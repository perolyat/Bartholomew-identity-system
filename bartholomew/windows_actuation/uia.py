"""Reading the accessibility tree, and the two capabilities that need it.

Windows UI Automation is how a program can tell that the caret is sitting in a
password box rather than in a search box. Two capabilities depend on that:

* `windows.type_text`, which must refuse to type into a password, PIN, token
  or payment field, and therefore must be able to *see* the field; and
* `windows.accessibility_action`, whose whole surface is the tree.

**Unavailable means refuse, not proceed.** UI Automation is reached through
COM, which needs the `comtypes` package (`pip install "bartholomew[windows]"`).
When it is not installed, `focused_field()` returns a descriptor whose
`is_password` is `None` -- "unknown" -- and
`bartholomew/actuation/sensitive.py:sensitive_field_reasons()` treats unknown
as a reason to refuse. A companion that cannot see where it is typing does not
type. That is a deliberate cost: the alternative is typing blind into whatever
happens to have focus.

Everything here is read-only except `perform()`, which is restricted to the
five non-consequential patterns listed in `ACTUATION_PATTERNS`. `Invoke` is
absent, and its absence is the design: invoking a control is how Send, Submit,
Confirm, Purchase and Delete are pressed.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: UI Automation property ids this module reads. All documented, all queries.
UIA_NAME_PROPERTY_ID = 30005
UIA_AUTOMATION_ID_PROPERTY_ID = 30011
UIA_CONTROL_TYPE_PROPERTY_ID = 30003
UIA_IS_PASSWORD_PROPERTY_ID = 30019
UIA_HELP_TEXT_PROPERTY_ID = 30013

#: Control types a caret may legitimately be in for `windows.type_text`.
#: Anything else -- a button, a menu item, a slider -- is refused, because
#: "typing" into one of those is really pressing it.
UIA_EDIT_CONTROL_TYPE = 50004
UIA_DOCUMENT_CONTROL_TYPE = 50030
UIA_COMBOBOX_CONTROL_TYPE = 50003
TYPEABLE_CONTROL_TYPES = frozenset(
    {UIA_EDIT_CONTROL_TYPE, UIA_DOCUMENT_CONTROL_TYPE, UIA_COMBOBOX_CONTROL_TYPE},
)

#: The only patterns `perform()` will use, mapped from the semantic operation
#: names in `bartholomew/actuation/parameters.py:ACCESSIBILITY_OPERATIONS`.
#:
#: `InvokePattern` and `TogglePattern` are deliberately absent. So is
#: `SelectionItemPattern`, which selects a radio button or a list item and can
#: change what a subsequent action would do. What remains changes what is
#: *visible* and nothing else.
UIA_EXPAND_COLLAPSE_PATTERN_ID = 10005
UIA_SCROLL_PATTERN_ID = 10004

ACTUATION_PATTERNS: dict[str, int | None] = {
    "expand": UIA_EXPAND_COLLAPSE_PATTERN_ID,
    "collapse": UIA_EXPAND_COLLAPSE_PATTERN_ID,
    "scroll_up": UIA_SCROLL_PATTERN_ID,
    "scroll_down": UIA_SCROLL_PATTERN_ID,
    #: Focus is a method on the element itself rather than a pattern.
    "focus_element": None,
}

#: `IUIAutomation`'s CLSID and IID. Named constants rather than literals in
#: the call, so what is being instantiated is legible.
CLSID_CUI_AUTOMATION = "{FF48DBA4-60EF-4201-AA87-54103EEF594E}"

_SCROLL_AMOUNT_SMALL_DECREMENT = 0
_SCROLL_AMOUNT_SMALL_INCREMENT = 2
_SCROLL_AMOUNT_NO_AMOUNT = 3


class AccessibilityUnavailableError(RuntimeError):
    """UI Automation could not be reached. Every caller must refuse."""


@dataclass(frozen=True)
class FocusedField:
    """What is known about the element that currently has keyboard focus.

    `is_password is None` is the important state and is never conflated with
    `False`: it means the tree could not be read, and the caller must refuse
    rather than assume the field is safe.
    """

    is_password: bool | None
    name: str | None = None
    automation_id: str | None = None
    help_text: str | None = None
    control_type: int | None = None
    #: Why the tree could not be read, when it could not be.
    unavailable_reason: str | None = None

    @property
    def readable(self) -> bool:
        return self.is_password is not None

    @property
    def typeable_control(self) -> bool:
        """Whether the focused control is one a person could type into."""
        return self.control_type in TYPEABLE_CONTROL_TYPES


def _automation() -> Any:
    """The `IUIAutomation` root, or raise.

    `comtypes` is an optional, Windows-only dependency. Imported here rather
    than at module scope so that importing this module -- which every test run
    on Linux does -- costs nothing and requires nothing.
    """
    if sys.platform != "win32":
        raise AccessibilityUnavailableError(
            "UI Automation is a Windows API; this build actuates Windows only",
        )
    try:
        import comtypes.client  # noqa: PLC0415 - optional dependency, by design
    except ImportError as e:
        raise AccessibilityUnavailableError(
            "the accessibility adapter needs the optional 'comtypes' package "
            '(pip install "bartholomew[windows]"). Without it Bartholomew cannot see '
            "what field the caret is in, so it refuses to type rather than typing "
            "blind.",
        ) from e
    try:
        return comtypes.client.CreateObject(
            CLSID_CUI_AUTOMATION,
            interface=comtypes.client.GetModule("UIAutomationCore.dll").IUIAutomation,
        )
    except Exception as e:  # noqa: BLE001 - any COM failure is unavailability
        raise AccessibilityUnavailableError(
            f"UI Automation could not be started: {type(e).__name__}: {e}",
        ) from e


def available() -> bool:
    """Whether the accessibility adapter can be used at all, right now."""
    try:
        _automation()
    except AccessibilityUnavailableError:
        return False
    except Exception:  # pragma: no cover - defensive
        return False
    return True


def describe() -> dict[str, Any]:
    """What the diagnostics command and the health surface report."""
    try:
        _automation()
        return {"accessibility": "available", "provider": "UIAutomationCore", "error": None}
    except AccessibilityUnavailableError as e:
        return {"accessibility": "unavailable", "provider": None, "error": str(e)}


def focused_field() -> FocusedField:
    """Read the focused element. Never raises: unknown is a value, not an error.

    Returning `FocusedField(is_password=None, unavailable_reason=...)` rather
    than raising is deliberate -- the caller's job is to refuse on unknown, and
    a value it must inspect is harder to skip than an exception it might catch
    too broadly.
    """
    try:
        automation = _automation()
    except AccessibilityUnavailableError as e:
        return FocusedField(is_password=None, unavailable_reason=str(e))

    try:  # pragma: no cover - Windows + comtypes only
        element = automation.GetFocusedElement()
        if element is None:
            return FocusedField(
                is_password=None,
                unavailable_reason="no element currently has keyboard focus",
            )
        return FocusedField(
            is_password=bool(element.GetCurrentPropertyValue(UIA_IS_PASSWORD_PROPERTY_ID)),
            name=_string(element, UIA_NAME_PROPERTY_ID),
            automation_id=_string(element, UIA_AUTOMATION_ID_PROPERTY_ID),
            help_text=_string(element, UIA_HELP_TEXT_PROPERTY_ID),
            control_type=_integer(element, UIA_CONTROL_TYPE_PROPERTY_ID),
        )
    except Exception as e:  # noqa: BLE001 - any COM failure is unknown, not safe
        logger.warning("Could not read the focused element: %s", type(e).__name__)
        return FocusedField(
            is_password=None,
            unavailable_reason=f"the focused element could not be read: {type(e).__name__}",
        )


def _string(element: Any, property_id: int) -> str | None:  # pragma: no cover - Windows only
    try:
        value = element.GetCurrentPropertyValue(property_id)
    except Exception:
        return None
    return str(value) if value else None


def _integer(element: Any, property_id: int) -> int | None:  # pragma: no cover - Windows only
    try:
        value = element.GetCurrentPropertyValue(property_id)
    except Exception:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def perform(*, hwnd: int, element_name: str, operation: str) -> tuple[bool, str]:
    """Perform one allowlisted, non-consequential operation. Returns `(done, detail)`.

    `done=False` with a detail is a truthful "it did not happen"; anything this
    cannot observe raises `AccessibilityUnavailableError`, which the handler
    turns into `unknown`.

    The element is found by name **within the given window's subtree**, so the
    search cannot wander into another application, and an ambiguous name is
    refused rather than resolved to the first match.
    """
    operation_key = str(operation)
    if operation_key not in ACTUATION_PATTERNS:
        raise AccessibilityUnavailableError(
            f"{operation_key!r} is not an operation this adapter implements",
        )
    automation = _automation()

    try:  # pragma: no cover - Windows + comtypes only
        root = automation.ElementFromHandle(hwnd)
        if root is None:
            return False, "the window could not be found in the accessibility tree"
        condition = automation.CreatePropertyCondition(UIA_NAME_PROPERTY_ID, element_name)
        # TreeScope_Subtree = 7. Bounded to this window, deliberately.
        matches = root.FindAll(7, condition)
        count = int(getattr(matches, "Length", 0))
        if count == 0:
            return False, f"no element named {element_name!r} in that window"
        if count > 1:
            return False, (
                f"{count} elements are named {element_name!r} in that window; an "
                "ambiguous target is refused rather than guessed at"
            )
        element = matches.GetElement(0)

        if operation_key == "focus_element":
            element.SetFocus()
            return True, "focus set"

        pattern_id = ACTUATION_PATTERNS[operation_key]
        pattern = element.GetCurrentPattern(pattern_id)
        if pattern is None:
            return False, f"that element does not support {operation_key}"

        if operation_key in ("expand", "collapse"):
            import comtypes.client  # noqa: PLC0415 - optional dependency

            expand = pattern.QueryInterface(
                comtypes.client.GetModule(
                    "UIAutomationCore.dll",
                ).IUIAutomationExpandCollapsePattern,
            )
            if operation_key == "expand":
                expand.Expand()
            else:
                expand.Collapse()
            return True, operation_key

        import comtypes.client  # noqa: PLC0415 - optional dependency

        scroll = pattern.QueryInterface(
            comtypes.client.GetModule("UIAutomationCore.dll").IUIAutomationScrollPattern,
        )
        amount = (
            _SCROLL_AMOUNT_SMALL_DECREMENT
            if operation_key == "scroll_up"
            else _SCROLL_AMOUNT_SMALL_INCREMENT
        )
        scroll.Scroll(_SCROLL_AMOUNT_NO_AMOUNT, amount)
        return True, operation_key
    except AccessibilityUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001 - a COM failure is an unverifiable outcome
        raise AccessibilityUnavailableError(
            f"the accessibility operation could not be completed or observed: "
            f"{type(e).__name__}: {e}",
        ) from e
