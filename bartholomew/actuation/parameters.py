"""Canonical typed parameters for each capability, validated before anything runs.

This module is the argument that the action vocabulary cannot be widened by a
clever request. Each capability has exactly one validator, each validator
builds its result key-by-key from a fixed set of names, and an input carrying a
key no validator names is **refused** rather than ignored -- an ignored extra
key is a field that a later refactor might start reading.

Grep this file for `command`, `cmd`, `shell`, `script`, `args`, `argv`,
`executable` or `exec`: there is nothing to find, and
`tests/test_windows_action_prohibitions.py` asserts that over the source so it
stays that way. `windows.launch_app` takes an *allowlist key*, never a path and
never an argument, so "run this program with these arguments" has nowhere to
live in the wire format at all.

Validated twice, on purpose
---------------------------
The server validates before it stores or dispatches anything, and the device
validates again before it touches the operating system. They are not the same
check: the server cannot resolve a filesystem path that exists on someone
else's machine, and the device does not know which principal asked. Both must
pass, so the *stricter* of the two allowlists always wins and neither side has
to trust the other's diligence. `ValidationContext.filesystem_available` is how
one validator serves both callers honestly rather than by pretending.

Canonical, redacted, and why there are two
------------------------------------------
`ValidatedParameters.canonical` is the exact form that is fingerprinted,
approved, stored and dispatched: sorted keys, normalised values, no ambiguity.
`ValidatedParameters.redacted` is the form that may appear in a list endpoint,
a Reflection or an evidence row -- the text a person asked to be typed is
replaced by its digest and length there, so the audit trail can prove *which*
text was approved without keeping a second copy of it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any
from urllib.parse import urlsplit

from .allowlists import (
    AllowlistError,
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
    is_absolute_path,
    normalise_path,
    path_parts,
)
from .capabilities import CapabilityKind
from .sensitive import detect_secrets, final_action_reason

#: Bounds. Each is a refusal threshold, not a truncation point: an over-long
#: value is rejected so the person sees that their input did not fit, rather
#: than silently having half of it typed.
MAX_URL_CHARS = 2048
MAX_PATH_CHARS = 1024
MAX_CLIPBOARD_CHARS = 4096
MAX_TYPED_CHARS = 1024
MAX_ELEMENT_NAME_CHARS = 128

#: URL schemes that may be opened. Two, and both of them fetch a document.
#: `file:` is absent because it is a filesystem read wearing a URL's clothes,
#: `javascript:` because it is code, `ms-*`/`search-ms:`/`shell:` and every
#: other registered custom scheme because a scheme handler is an arbitrary
#: program chosen by whatever installed it.
PERMITTED_URL_SCHEMES = frozenset({"http", "https"})

#: File extensions `windows.open_path` refuses. Opening one of these with the
#: shell *is* execution: a `.bat` runs, a `.lnk` runs whatever it points at, a
#: `.reg` edits the registry on a double-click, an `.hta` is a script host.
#: The capability opens documents, so anything that runs is out.
EXECUTABLE_EXTENSIONS = frozenset(
    {
        ".ade",
        ".adp",
        ".app",
        ".application",
        ".appref-ms",
        ".asp",
        ".aspx",
        ".bat",
        ".bas",
        ".cer",
        ".chm",
        ".cmd",
        ".cnt",
        ".com",
        ".cpl",
        ".crt",
        ".csh",
        ".der",
        ".diagcab",
        ".exe",
        ".fxp",
        ".gadget",
        ".grp",
        ".hlp",
        ".hpj",
        ".hta",
        ".htc",
        ".inf",
        ".ins",
        ".isp",
        ".its",
        ".jar",
        ".jnlp",
        ".js",
        ".jse",
        ".ksh",
        ".lnk",
        ".mad",
        ".maf",
        ".mag",
        ".mam",
        ".maq",
        ".mar",
        ".mas",
        ".mat",
        ".mau",
        ".mav",
        ".maw",
        ".mcf",
        ".mda",
        ".mdb",
        ".mde",
        ".mdt",
        ".mdw",
        ".mdz",
        ".msc",
        ".msh",
        ".msh1",
        ".msh2",
        ".mshxml",
        ".msh1xml",
        ".msh2xml",
        ".msi",
        ".msp",
        ".mst",
        ".msu",
        ".ops",
        ".osd",
        ".pcd",
        ".pif",
        ".pl",
        ".plg",
        ".prf",
        ".prg",
        ".ps1",
        ".ps1xml",
        ".ps2",
        ".ps2xml",
        ".psc1",
        ".psc2",
        ".psd1",
        ".psm1",
        ".pst",
        ".py",
        ".pyc",
        ".pyo",
        ".pyw",
        ".pyz",
        ".reg",
        ".scf",
        ".scr",
        ".sct",
        ".searchConnector-ms",
        ".settingcontent-ms",
        ".sh",
        ".shb",
        ".shs",
        ".theme",
        ".tmp",
        ".url",
        ".vb",
        ".vbe",
        ".vbp",
        ".vbs",
        ".vsmacros",
        ".vsw",
        ".webpnp",
        ".website",
        ".ws",
        ".wsc",
        ".wsf",
        ".wsh",
        ".xbap",
        ".xll",
        ".xnk",
    },
)

#: The operations `windows.manage_window` implements. A closed set: focus,
#: minimise, maximise, restore, and a bounded move/resize.
WINDOW_OPERATIONS = ("focus", "minimize", "maximize", "restore", "move", "resize")

#: The semantic operations `windows.accessibility_action` implements.
#:
#: `invoke` is deliberately absent, and its absence is the whole design of this
#: capability. Invoking an arbitrary control is how "press a button" is
#: spelled, and Send, Submit, Confirm, Purchase and Delete are all buttons.
#: What remains changes what is *visible*, never what has *happened*.
ACCESSIBILITY_OPERATIONS = ("expand", "collapse", "scroll_up", "scroll_down", "focus_element")

#: The furthest a window may be moved or the largest it may be sized, in
#: pixels. Bounds the request; the device bounds it again against the actual
#: virtual desktop, which the server cannot know.
MAX_WINDOW_COORDINATE = 32767
MIN_WINDOW_EXTENT = 64
MAX_WINDOW_EXTENT = 32767

#: Characters that may appear in typed or copied text. Everything else -- every
#: control character, and notably every newline, carriage return and tab -- is
#: refused. This is what makes "cannot press Send" structural for
#: `windows.type_text`: there is no Enter to type.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_NEWLINE_OR_TAB = re.compile(r"[\t\n\r\x0b\x0c  ]")


class ParameterError(ValueError):
    """Parameters that this build refuses to act on. Always a refusal."""


class SensitiveContentError(ParameterError):
    """The parameters were refused because they look like credential material.

    A subclass rather than a flag, so a caller that catches `ParameterError`
    keeps refusing exactly as before while one that wants to *count* this cause
    can tell it apart. That distinction matters for the audit: "somebody tried
    to have a secret typed" and "somebody sent a malformed request" are two
    different things to see a lot of.
    """


@dataclass(frozen=True)
class ValidationContext:
    """What a validator needs beyond the parameters themselves.

    The three allowlists come from the device's enrolment on the server side
    and from local configuration on the device side. Both are consulted, in
    their own process, against their own copy -- see the module docstring.
    """

    applications: ApplicationAllowlist = field(
        default_factory=lambda: ApplicationAllowlist.from_pairs({}),
    )
    url_domains: UrlDomainAllowlist = field(
        default_factory=lambda: UrlDomainAllowlist.from_iterable(()),
    )
    filesystem_roots: FilesystemRootAllowlist = field(
        default_factory=lambda: FilesystemRootAllowlist.from_iterable(()),
    )
    #: True only in the process that will actually open the path. The server
    #: cannot stat a file on someone else's machine, so it performs the
    #: lexical half of the check and says so rather than skipping it silently.
    filesystem_available: bool = False


@dataclass(frozen=True)
class ValidatedParameters:
    """One capability's parameters, after validation. Two views of the same thing."""

    kind: CapabilityKind
    #: Exactly what will be fingerprinted, approved, stored and dispatched.
    canonical: dict[str, Any]
    #: Safe to put in a list endpoint, a Reflection or an evidence row.
    redacted: dict[str, Any]
    #: Keys whose values must never leave this process in cleartext.
    sensitive_keys: frozenset[str] = frozenset()

    def fingerprint(self) -> str:
        """A stable digest of the canonical parameters.

        This is the value an approval binds to, so changing any parameter --
        one character of a URL, one pixel of a window position -- produces a
        different digest and invalidates the approval. Sorted keys and
        separator-free JSON so two encodings of the same parameters can never
        produce two digests.
        """
        encoded = json.dumps(
            self.canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_mapping(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ParameterError(
            f"parameters must be a JSON object, not {type(raw).__name__}",
        )
    for key in raw:
        if not isinstance(key, str):
            raise ParameterError("every parameter name must be a string")
    return dict(raw)


def _closed(raw: dict[str, Any], permitted: tuple[str, ...], kind: CapabilityKind) -> None:
    """Refuse any key the capability does not name. Refuse, never ignore."""
    extra = sorted(set(raw) - set(permitted))
    if extra:
        raise ParameterError(
            f"{kind.value} does not accept {extra}; the permitted parameters are "
            f"{sorted(permitted)}. Unknown parameters are refused, not ignored.",
        )


def _text(raw: dict[str, Any], name: str, *, maximum: int, required: bool = True) -> str:
    value = raw.get(name)
    if value is None:
        if required:
            raise ParameterError(f"{name!r} is required")
        return ""
    if not isinstance(value, str):
        raise ParameterError(f"{name!r} must be a string, not {type(value).__name__}")
    if len(value) > maximum:
        raise ParameterError(
            f"{name!r} is {len(value)} characters; the limit is {maximum}. Over-long "
            "input is refused rather than truncated.",
        )
    return value


def _ordinary_text(value: str, name: str) -> str:
    """Normalise and refuse anything that is not ordinary printable text.

    NFC first, so two spellings of the same accented character are one string
    (and so a fingerprint over the text is stable). Then every control
    character is refused -- including Enter, Tab and the Unicode line and
    paragraph separators -- because a capability that can type Enter can press
    the button the field is attached to.
    """
    normalised = unicodedata.normalize("NFC", value)
    if not normalised.strip():
        raise ParameterError(f"{name!r} must not be blank")
    if _NEWLINE_OR_TAB.search(normalised):
        raise ParameterError(
            f"{name!r} may not contain a newline, carriage return or tab. Those keys "
            "submit forms and move between fields, so they are refused outright "
            "rather than filtered.",
        )
    if _CONTROL_CHARACTERS.search(normalised):
        raise ParameterError(f"{name!r} may not contain control characters")
    astral = next((c for c in normalised if ord(c) > 0xFFFF), None)
    if astral is not None:
        # Refusing the whole plane, and this is not fussiness about emoji.
        #
        # Win32's `KEYBDINPUT.wScan` is a 16-bit field and `ctypes` *truncates*
        # rather than raising, so a character above U+FFFF arrives at the
        # operating system as its low sixteen bits. U+1000D becomes 0x000D --
        # Enter. U+10009 becomes Tab. Every refusal above this line, and the
        # whole "cannot press Send" property, is defeated by one character that
        # looks like an ancient syllabary and types a carriage return.
        #
        # The correct way to type an astral character is a surrogate pair, and
        # this build deliberately does not: a capability whose entire point is
        # that it cannot reach a control cannot also be the place where the
        # subtlest encoding bug in the codebase lives. The digest an approver
        # binds to must be the text the OS receives, and only the Basic
        # Multilingual Plane guarantees that here.
        #
        # `windows_actuation/win32.py:send_unicode_text` refuses the same thing
        # again immediately before the call, so this is not the only fence.
        raise ParameterError(
            f"{name!r} contains {astral!r} (U+{ord(astral):04X}), which is outside the "
            "Basic Multilingual Plane. Windows carries a typed character in a 16-bit "
            "field, so such a character would arrive truncated -- and some of them "
            "truncate to Enter or Tab. It is refused rather than sent as something "
            "other than what was approved.",
        )
    return normalised


def _refuse_secrets(value: str, name: str) -> None:
    findings = detect_secrets(value)
    if findings:
        raise SensitiveContentError(
            f"{name!r} was refused because it looks like credential material "
            f"({', '.join(f.category for f in findings)}). Bartholomew does not type "
            "or copy secrets, and this refusal is deliberate rather than a "
            "detection to be worked around.",
        )


# ---------------------------------------------------------------------------
# One validator per capability
# ---------------------------------------------------------------------------


def _validate_open_url(raw: dict[str, Any], ctx: ValidationContext) -> ValidatedParameters:
    _closed(raw, ("url",), CapabilityKind.OPEN_URL)
    url = _text(raw, "url", maximum=MAX_URL_CHARS).strip()
    if _CONTROL_CHARACTERS.search(url) or any(c.isspace() for c in url):
        raise ParameterError("the URL contains whitespace or control characters")

    try:
        parts = urlsplit(url)
    except ValueError as e:
        raise ParameterError(f"the URL could not be parsed: {e}") from e

    scheme = (parts.scheme or "").lower()
    if scheme not in PERMITTED_URL_SCHEMES:
        raise ParameterError(
            f"{scheme or '(no scheme)'!r} is not an openable scheme. Only "
            f"{sorted(PERMITTED_URL_SCHEMES)} are permitted: 'file:' is a filesystem "
            "read, 'javascript:' is code, and a custom scheme runs whichever program "
            "registered it.",
        )
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise ParameterError(
            "the URL carries embedded credentials, which are refused. A URL that "
            "contains a username or password would put one in an audit row and in "
            "the browser's history.",
        )
    if not parts.hostname:
        raise ParameterError("the URL has no host")
    try:
        # `urlsplit(...).port` is a property that raises a *bare* `ValueError`
        # for a non-numeric or out-of-range port. `ParameterError` subclasses
        # `ValueError`, so an uncaught one here escaped every refusal path in
        # the seam and surfaced as a 500 with no Reflection written -- which
        # let an allowlist-probing caller avoid the audit trail entirely.
        port = parts.port
    except ValueError as e:
        raise ParameterError(f"the URL's port could not be read: {e}") from e
    if port is not None and not (1 <= port <= 65535):
        raise ParameterError("the URL port is out of range")

    try:
        host = ctx.url_domains.require(parts.hostname)
    except AllowlistError as e:
        raise ParameterError(str(e)) from e

    # Rebuilt from the parsed parts rather than passed through, so what is
    # approved and what is opened are the same string and no encoding trick
    # survives the round trip.
    authority = host if port is None else f"{host}:{port}"
    canonical_url = f"{scheme}://{authority}{parts.path or '/'}"
    if parts.query:
        canonical_url += f"?{parts.query}"
    if parts.fragment:
        canonical_url += f"#{parts.fragment}"
    if len(canonical_url) > MAX_URL_CHARS:
        raise ParameterError("the canonical URL exceeds the length limit")

    # The same detector `type_text` and `clipboard_write` use. A URL is not
    # obviously "text somebody typed", which is why this was missed -- but
    # `https://host/callback?access_token=...` is a credential travelling
    # through a field this capability opens in a browser and writes into an
    # audit row, and refusing embedded userinfo while waving through a token in
    # the query was a distinction without a difference.
    _refuse_secrets(canonical_url, "url")

    canonical = {"url": canonical_url}
    # **The redacted view drops the query and the fragment.** That view is the
    # one kept for the life of the database -- no code path ever nulls
    # `parameters_redacted_json` -- so it must be the form that is safe to keep
    # forever, and a query string is where a URL carries its content. The
    # digest still ties the audit row to the exact URL that was approved.
    redacted: dict[str, Any] = {
        "url": f"{scheme}://{authority}{parts.path or '/'}",
        "host": host,
        "url_sha256": hashlib.sha256(canonical_url.encode("utf-8")).hexdigest(),
    }
    if parts.query or parts.fragment:
        redacted["has_query"] = True
    return ValidatedParameters(
        kind=CapabilityKind.OPEN_URL,
        canonical=canonical,
        redacted=redacted,
        sensitive_keys=frozenset({"url"}) if (parts.query or parts.fragment) else frozenset(),
    )


def _validate_open_path(raw: dict[str, Any], ctx: ValidationContext) -> ValidatedParameters:
    _closed(raw, ("path",), CapabilityKind.OPEN_PATH)
    path = _text(raw, "path", maximum=MAX_PATH_CHARS).strip()
    if _CONTROL_CHARACTERS.search(path):
        raise ParameterError("the path contains control characters")
    if "\x00" in path:
        raise ParameterError("the path contains a null byte")

    # Parsed in the path's *own* syntax, not the host's: the governing process
    # may be a Linux service validating a path that a Windows desk PC will
    # open, and `os.path` would answer for the wrong machine.
    if not is_absolute_path(path):
        raise ParameterError(
            "the path must be absolute. A relative path would resolve against "
            "whatever directory the companion happened to be started in.",
        )
    if ".." in path_parts(path):
        raise ParameterError(
            "the path contains a '..' component. Traversal is refused before "
            "resolution rather than resolved and then checked.",
        )
    # A UNC path names someone else's machine, which is outside the enrolled
    # device this capability is scoped to.
    if path.startswith(("\\\\", "//")):
        raise ParameterError(
            "a UNC path names a remote host; this capability opens files on the "
            "enrolled device only.",
        )

    suffix = PurePath(path.replace("\\", "/")).suffix.lower()
    if suffix in EXECUTABLE_EXTENSIONS:
        raise ParameterError(
            f"{suffix!r} is an executable or script extension. Opening one runs it, "
            "and this capability opens documents and folders only.",
        )

    if ctx.filesystem_available:
        # The process that will actually open it: fully resolve, follow every
        # link, and require the *resolved* path to be inside a root.
        try:
            resolved = ctx.filesystem_roots.require_within(path)
        except AllowlistError as e:
            raise ParameterError(str(e)) from e
        if not (os.path.isfile(resolved) or os.path.isdir(resolved)):
            raise ParameterError("the path is neither a file nor a folder")
        # Re-checked after resolution: a link may point at an executable.
        if PurePath(resolved).suffix.lower() in EXECUTABLE_EXTENSIONS:
            raise ParameterError(
                "the resolved path is an executable or script; this capability opens "
                "documents and folders only.",
            )
        canonical_path = normalise_path(resolved)
    else:
        # The governing process, which cannot see the device's filesystem. It
        # performs the lexical half and refuses anything that fails it, and
        # the device performs the resolving half before it opens anything.
        lexical = normalise_path(path)
        if not ctx.filesystem_roots.contains(lexical):
            raise ParameterError(
                "the path is not inside any filesystem root this device is enrolled "
                "with. The device re-checks the fully resolved path before opening "
                "it, so a symbolic link cannot escape either.",
            )
        canonical_path = lexical

    canonical = {"path": canonical_path}
    return ValidatedParameters(
        kind=CapabilityKind.OPEN_PATH,
        canonical=canonical,
        redacted={"path": canonical_path},
    )


def _validate_launch_app(raw: dict[str, Any], ctx: ValidationContext) -> ValidatedParameters:
    _closed(raw, ("app_id",), CapabilityKind.LAUNCH_APP)
    app_id = _text(raw, "app_id", maximum=64).strip().lower()
    if not app_id:
        raise ParameterError("'app_id' must not be blank")
    try:
        ctx.applications.resolve(app_id)
    except AllowlistError as e:
        raise ParameterError(str(e)) from e
    # Note what is *not* here: no path, no arguments, no working directory, no
    # environment. The executable comes from the allowlist and is started with
    # nothing after it -- see windows_actuation/win32.py's one-parameter
    # process starter.
    canonical = {"app_id": app_id}
    return ValidatedParameters(
        kind=CapabilityKind.LAUNCH_APP,
        canonical=canonical,
        redacted={"app_id": app_id},
    )


def _validate_focus_window(raw: dict[str, Any], ctx: ValidationContext) -> ValidatedParameters:
    _closed(raw, ("app_id",), CapabilityKind.FOCUS_WINDOW)
    app_id = _text(raw, "app_id", maximum=64).strip().lower()
    try:
        ctx.applications.resolve(app_id)
    except AllowlistError as e:
        raise ParameterError(str(e)) from e
    # Window identity is bounded by the application allowlist and resolved on
    # the device by enumerating that application's visible top-level windows.
    # Ambiguity is a refusal there, not a guess: see handlers.focus_window.
    canonical = {"app_id": app_id}
    return ValidatedParameters(
        kind=CapabilityKind.FOCUS_WINDOW,
        canonical=canonical,
        redacted={"app_id": app_id},
    )


def _validate_manage_window(raw: dict[str, Any], ctx: ValidationContext) -> ValidatedParameters:
    _closed(raw, ("app_id", "operation", "x", "y", "width", "height"), CapabilityKind.MANAGE_WINDOW)
    app_id = _text(raw, "app_id", maximum=64).strip().lower()
    try:
        ctx.applications.resolve(app_id)
    except AllowlistError as e:
        raise ParameterError(str(e)) from e

    operation = _text(raw, "operation", maximum=16).strip().lower()
    if operation not in WINDOW_OPERATIONS:
        raise ParameterError(
            f"{operation!r} is not a window operation. The permitted operations are "
            f"{list(WINDOW_OPERATIONS)}.",
        )

    canonical: dict[str, Any] = {"app_id": app_id, "operation": operation}

    def _coordinate(name: str, low: int, high: int) -> int:
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ParameterError(
                f"{name!r} must be an integer for a {operation!r} operation",
            )
        if not (low <= value <= high):
            raise ParameterError(f"{name!r} must be between {low} and {high}")
        return value

    if operation == "move":
        canonical["x"] = _coordinate("x", -MAX_WINDOW_COORDINATE, MAX_WINDOW_COORDINATE)
        canonical["y"] = _coordinate("y", -MAX_WINDOW_COORDINATE, MAX_WINDOW_COORDINATE)
    elif operation == "resize":
        canonical["width"] = _coordinate("width", MIN_WINDOW_EXTENT, MAX_WINDOW_EXTENT)
        canonical["height"] = _coordinate("height", MIN_WINDOW_EXTENT, MAX_WINDOW_EXTENT)

    unexpected = sorted(set(raw) - set(canonical) - {"app_id", "operation"})
    if unexpected:
        raise ParameterError(
            f"a {operation!r} operation does not take {unexpected}",
        )
    return ValidatedParameters(
        kind=CapabilityKind.MANAGE_WINDOW,
        canonical=canonical,
        redacted=dict(canonical),
    )


def _validate_clipboard_read(raw: dict[str, Any], ctx: ValidationContext) -> ValidatedParameters:
    _closed(raw, (), CapabilityKind.CLIPBOARD_READ)
    # No parameters at all. There is nothing to select, filter or target: the
    # capability reads the clipboard once, and a parameter would only be a way
    # to make it read something else.
    return ValidatedParameters(
        kind=CapabilityKind.CLIPBOARD_READ,
        canonical={},
        redacted={},
    )


def _validate_clipboard_write(raw: dict[str, Any], ctx: ValidationContext) -> ValidatedParameters:
    _closed(raw, ("text",), CapabilityKind.CLIPBOARD_WRITE)
    text = _ordinary_text(_text(raw, "text", maximum=MAX_CLIPBOARD_CHARS), "text")
    _refuse_secrets(text, "text")
    canonical = {"text": text}
    return ValidatedParameters(
        kind=CapabilityKind.CLIPBOARD_WRITE,
        canonical=canonical,
        redacted={"text_sha256": _digest(text), "text_length": len(text)},
        sensitive_keys=frozenset({"text"}),
    )


def _validate_type_text(raw: dict[str, Any], ctx: ValidationContext) -> ValidatedParameters:
    _closed(raw, ("text",), CapabilityKind.TYPE_TEXT)
    text = _ordinary_text(_text(raw, "text", maximum=MAX_TYPED_CHARS), "text")
    _refuse_secrets(text, "text")
    canonical = {"text": text}
    return ValidatedParameters(
        kind=CapabilityKind.TYPE_TEXT,
        canonical=canonical,
        # The typed text never reaches an audit row in cleartext. The digest is
        # enough to prove that what was typed is what was approved.
        redacted={"text_sha256": _digest(text), "text_length": len(text)},
        sensitive_keys=frozenset({"text"}),
    )


def _validate_accessibility_action(
    raw: dict[str, Any],
    ctx: ValidationContext,
) -> ValidatedParameters:
    _closed(raw, ("app_id", "operation", "element_name"), CapabilityKind.ACCESSIBILITY_ACTION)
    app_id = _text(raw, "app_id", maximum=64).strip().lower()
    try:
        ctx.applications.resolve(app_id)
    except AllowlistError as e:
        raise ParameterError(str(e)) from e

    operation = _text(raw, "operation", maximum=32).strip().lower()
    if operation not in ACCESSIBILITY_OPERATIONS:
        raise ParameterError(
            f"{operation!r} is not a permitted accessibility operation. The permitted "
            f"operations are {list(ACCESSIBILITY_OPERATIONS)}. Invoking a control is "
            "deliberately absent: Send, Submit, Confirm, Purchase and Delete are all "
            "controls, and this capability may not press any of them.",
        )

    element = _ordinary_text(
        _text(raw, "element_name", maximum=MAX_ELEMENT_NAME_CHARS),
        "element_name",
    )
    final = final_action_reason(element)
    if final is not None:
        raise ParameterError(
            f"the target element is named like a final action ({final!r}). Expanding "
            "or scrolling has no business naming a control of that kind, so the "
            "request is refused.",
        )

    canonical = {"app_id": app_id, "operation": operation, "element_name": element}
    return ValidatedParameters(
        kind=CapabilityKind.ACCESSIBILITY_ACTION,
        canonical=canonical,
        redacted=dict(canonical),
    )


#: The one dispatch table for validation: a literal mapping from a closed enum
#: to a named function. No `getattr`, no name mangling, no registry a plugin
#: could add to. A capability with no entry here cannot be validated and
#: therefore cannot be executed.
_VALIDATORS = {
    CapabilityKind.OPEN_URL: _validate_open_url,
    CapabilityKind.OPEN_PATH: _validate_open_path,
    CapabilityKind.LAUNCH_APP: _validate_launch_app,
    CapabilityKind.FOCUS_WINDOW: _validate_focus_window,
    CapabilityKind.MANAGE_WINDOW: _validate_manage_window,
    CapabilityKind.CLIPBOARD_READ: _validate_clipboard_read,
    CapabilityKind.CLIPBOARD_WRITE: _validate_clipboard_write,
    CapabilityKind.TYPE_TEXT: _validate_type_text,
    CapabilityKind.ACCESSIBILITY_ACTION: _validate_accessibility_action,
}


def validate(
    kind: CapabilityKind,
    raw: Any,
    ctx: ValidationContext | None = None,
) -> ValidatedParameters:
    """Validate one capability's parameters, or refuse them.

    Raises `ParameterError` for anything that does not fit the capability's
    exact shape. There is no partial success and no coercion: a request either
    produces canonical parameters or produces a refusal.
    """
    validator = _VALIDATORS.get(kind)
    if validator is None:  # pragma: no cover - unreachable while the enum is closed
        raise ParameterError(f"{kind!r} has no validator and cannot be executed")
    return validator(_require_mapping(raw), ctx or ValidationContext())
