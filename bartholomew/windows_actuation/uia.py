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

import hashlib
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
#: `ValueValue`. The current contents of an editable control, and the one
#: property this module reads that is *content* rather than a label. It is read
#: only by `focused_field_text()`, only between the two halves of a
#: `windows.type_text` the person already approved, and it never leaves that
#: function as anything but a length and a digest -- see its docstring.
UIA_VALUE_VALUE_PROPERTY_ID = 30045

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

#: UIA's `ScrollAmount` enum, verbatim:
#: `LargeDecrement=0, SmallDecrement=1, NoAmount=2, LargeIncrement=3,
#: SmallIncrement=4`.
#:
#: These were wrong, and wrongly in a way that looked right: `NoAmount` was 3,
#: which is `LargeIncrement`, so every call asked for a large *horizontal*
#: scroll. On a vertically-scrollable-only pane -- the common case --
#: `IScrollProvider::Scroll` rejects a non-`NoAmount` horizontal request
#: outright, and the blanket `except` in `perform()` reported that as the
#: accessibility adapter being unavailable rather than as a bad argument.
_SCROLL_AMOUNT_SMALL_DECREMENT = 1
_SCROLL_AMOUNT_SMALL_INCREMENT = 4
_SCROLL_AMOUNT_NO_AMOUNT = 2


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


@dataclass(frozen=True)
class FieldText:
    """What is in the focused field, as a fingerprint rather than as the text.

    The Verify half of `windows.type_text` has an obvious tension: confirming
    that typed characters landed means looking at what is now in the field, and
    what is now in the field is a person's writing. This type is where that
    tension is resolved. `focused_field_text()` reads the value, measures it,
    digests it, and lets the string go; a `FieldText` carries a length and a
    SHA-256 and there is no attribute on it that holds the content.

    So the strongest thing any caller of this module -- including a compromised
    one -- can learn about a field's contents is how long they are and whether
    they hash to something it already knows. That is enough to verify an effect
    and not enough to exfiltrate one, which is exactly the trade the
    digest-only evidence rule makes everywhere else in this package.

    `readable is False` is the honest unavailable state: no provider, no
    `ValuePattern` on this control, or a COM call that failed. The caller
    reports `unknown`, never a verdict.
    """

    readable: bool
    length: int | None = None
    digest: str | None = None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class TypedTextVerification:
    """Whether the characters that were sent are now in the field they were sent to.

    `observed` and `matched` are separate on purpose, and collapsing them is
    the bug this type exists to prevent: `observed=False` means nobody could
    look, which must become `unknown`, while `observed=True, matched=False`
    means somebody looked and the text is not there, which must become
    `failed`. One boolean cannot say both.
    """

    observed: bool
    matched: bool
    before_length: int | None = None
    after_length: int | None = None
    reason: str = ""


def focused_field_text() -> FieldText:
    """Measure and digest the focused element's value. Never returns the value.

    Reads `ValueValue`, which is the documented, read-only way to ask an
    editable control what it currently contains -- the same property-read
    mechanism `focused_field()` already uses for the field's name and its
    password flag, asking a different question of the same element.

    Never raises: unavailability is a value here for the reason it is a value
    in `focused_field()`. A control with no value property, an element that
    went away, a COM failure and an absent adapter all come back as
    `readable=False` with a reason, and every one of them makes the caller
    report `unknown`.
    """
    try:
        automation = _automation()
    except AccessibilityUnavailableError as e:
        return FieldText(readable=False, unavailable_reason=str(e))

    try:  # pragma: no cover - Windows + comtypes only
        element = automation.GetFocusedElement()
        if element is None:
            return FieldText(
                readable=False,
                unavailable_reason="no element currently has keyboard focus",
            )
        raw = element.GetCurrentPropertyValue(UIA_VALUE_VALUE_PROPERTY_ID)
    except Exception as e:  # noqa: BLE001 - any COM failure is unreadable, not empty
        logger.warning("Could not read the focused field's value: %s", type(e).__name__)
        return FieldText(
            readable=False,
            unavailable_reason=f"the field's value could not be read: {type(e).__name__}",
        )
    if raw is None:  # pragma: no cover - Windows only
        # An element with no `ValuePattern` -- a plain document surface, for
        # instance -- answers `None`. That is "this control does not expose its
        # contents", not "this control is empty", and reporting it as an empty
        # string would manufacture a length of zero to compare against.
        return FieldText(
            readable=False,
            unavailable_reason=(
                "the focused control does not expose its contents through UI "
                "Automation, so what it now holds cannot be read back"
            ),
        )
    return _measure(str(raw))  # pragma: no cover - Windows only


def _measure(value: str) -> FieldText:
    """Reduce a field's contents to a length and a digest, and drop the string.

    Split out from `focused_field_text()` so the reduction is testable off
    Windows: the platform-specific half is the COM read, and this half -- the
    half that must not leak -- is ordinary Python that a test can call.
    """
    text = str(value or "")
    return FieldText(
        readable=True,
        length=len(text),
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def verify_typed_text(
    *,
    expected: str,
    before: FieldText,
    after: FieldText,
) -> TypedTextVerification:
    """Decide whether `expected` landed, from two measurements of one field.

    The comparison is on **length**, because a length is all a `FieldText`
    carries, and that is deliberate rather than a limitation worked around: an
    equality test on the contents would need the contents.

    The rule is that the field grew by exactly the number of characters that
    were sent. It is a real observation -- a keystroke swallowed by a focus
    change makes the field grow by less, and the common failure this whole step
    exists to catch is the field not growing at all -- and it is honest about
    what it does not establish: a field that gained the right number of
    *different* characters would pass, so this confirms that the typing reached
    the field, not that Windows composed every glyph the way the caller
    imagined.

    A digest equality is reported when the field was empty beforehand, which is
    the strong case: then the whole field is the typed text and the digests can
    be compared outright.
    """
    if not before.readable or not after.readable:
        unreadable = before if not before.readable else after
        return TypedTextVerification(
            observed=False,
            matched=False,
            before_length=before.length,
            after_length=after.length,
            reason=(
                unreadable.unavailable_reason
                or "the field could not be read back, so whether the text landed is not known"
            ),
        )

    sent = len(expected)
    grew_by = (after.length or 0) - (before.length or 0)
    if before.length == 0:
        exact = after.digest == hashlib.sha256(expected.encode("utf-8")).hexdigest()
        return TypedTextVerification(
            observed=True,
            matched=exact,
            before_length=before.length,
            after_length=after.length,
            reason=(
                "the field was empty and now holds exactly the text that was sent"
                if exact
                else ("the field was empty and what it now holds is not the text that was sent")
            ),
        )
    matched = grew_by == sent
    return TypedTextVerification(
        observed=True,
        matched=matched,
        before_length=before.length,
        after_length=after.length,
        reason=(
            f"the field grew by exactly the {sent} characters that were sent"
            if matched
            else f"{sent} characters were sent and the field grew by {grew_by}"
        ),
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
