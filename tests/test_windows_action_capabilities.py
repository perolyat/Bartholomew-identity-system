"""The capability vocabulary is closed, versioned, and validates before anything runs.

Acceptance requirements 1-3: every permitted capability validates its
parameters before OS access; unknown capabilities and versions are refused;
malformed and untrusted requests are refused.

These are unit-level assertions against the real validators, because the thing
being proven is what the validators do -- nothing here is mocked, and nothing
here reaches an operating system, which is itself part of the point.
"""

from __future__ import annotations

import pytest

from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import (
    ALL_CAPABILITIES,
    ALWAYS_APPROVAL,
    CURRENT_CAPABILITY_VERSION,
    TRUSTED_AUTONOMY_ELIGIBLE,
    ApprovalRequirement,
    CapabilityKind,
    UnsupportedCapabilityError,
    describe,
    parse_kind,
    require_supported,
)
from bartholomew.actuation.parameters import (
    _VALIDATORS,
    ACCESSIBILITY_OPERATIONS,
    WINDOW_OPERATIONS,
    ParameterError,
    ValidationContext,
    validate,
)

#: The vocabulary the contract permits, written out here rather than imported,
#: so that widening the enum fails this test instead of silently agreeing
#: with itself.
PERMITTED = {
    "windows.open_url",
    "windows.open_path",
    "windows.launch_app",
    "windows.focus_window",
    "windows.manage_window",
    "windows.clipboard_read",
    "windows.clipboard_write",
    "windows.type_text",
    "windows.accessibility_action",
}


@pytest.fixture
def ctx(tmp_path):
    """A context with all three allowlists populated and a real filesystem."""
    allowed_dir = tmp_path / "documents"
    allowed_dir.mkdir()
    (allowed_dir / "notes.txt").write_text("hello", encoding="utf-8")
    (allowed_dir / "installer.exe").write_text("not really", encoding="utf-8")
    return ValidationContext(
        applications=ApplicationAllowlist.from_pairs(
            {"notepad": "C:\\Windows\\System32\\notepad.exe"},
        ),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com", "docs.python.org"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable([str(allowed_dir)]),
        filesystem_available=True,
    )


# --- 1. the vocabulary is exactly the permitted one ---------------------------


def test_the_capability_vocabulary_is_exactly_the_permitted_set():
    assert {k.value for k in ALL_CAPABILITIES} == PERMITTED


def test_every_capability_has_a_validator_and_a_descriptor():
    """A capability with no validator could never be checked before running."""
    for kind in ALL_CAPABILITIES:
        assert kind in _VALIDATORS, f"{kind.value} has no parameter validator"
        assert describe(kind).kind is kind


def test_the_risk_posture_matches_the_contract():
    """Approval requirements, written out so a weakening edit fails here."""
    expected = {
        CapabilityKind.OPEN_URL: ApprovalRequirement.REQUIRED,
        CapabilityKind.OPEN_PATH: ApprovalRequirement.REQUIRED,
        CapabilityKind.CLIPBOARD_WRITE: ApprovalRequirement.REQUIRED,
        CapabilityKind.LAUNCH_APP: ApprovalRequirement.REQUIRED_AUTONOMY_ELIGIBLE,
        CapabilityKind.FOCUS_WINDOW: ApprovalRequirement.REQUIRED_AUTONOMY_ELIGIBLE,
        CapabilityKind.MANAGE_WINDOW: ApprovalRequirement.REQUIRED_AUTONOMY_ELIGIBLE,
        CapabilityKind.CLIPBOARD_READ: ApprovalRequirement.ALWAYS,
        CapabilityKind.TYPE_TEXT: ApprovalRequirement.ALWAYS,
        CapabilityKind.ACCESSIBILITY_ACTION: ApprovalRequirement.ALWAYS,
    }
    assert {k: describe(k).approval for k in ALL_CAPABILITIES} == expected


def test_the_always_approval_capabilities_can_never_be_made_autonomous():
    """The three most consequential kinds are ineligible, structurally."""
    assert ALWAYS_APPROVAL == {
        CapabilityKind.CLIPBOARD_READ,
        CapabilityKind.TYPE_TEXT,
        CapabilityKind.ACCESSIBILITY_ACTION,
    }
    assert not (ALWAYS_APPROVAL & TRUSTED_AUTONOMY_ELIGIBLE)


# --- 2. unknown kinds and versions are refused, not approximated --------------


@pytest.mark.parametrize(
    "unknown",
    [
        "windows.run",
        "windows.execute",
        "windows.shell",
        "windows.open_url ",  # a trailing space is a different string
        "WINDOWS.OPEN_URL",  # no case folding
        "windows.open",  # no prefix matching
        "open_url",
        "",
        "macos.open_url",
        "linux.open_url",
        "android.open_url",
    ],
)
def test_an_unknown_capability_kind_is_refused(unknown):
    with pytest.raises(UnsupportedCapabilityError):
        parse_kind(unknown)


@pytest.mark.parametrize("bad_type", [None, 1, [], {}, object()])
def test_a_non_string_capability_is_refused(bad_type):
    with pytest.raises(UnsupportedCapabilityError):
        parse_kind(bad_type)


@pytest.mark.parametrize("version", [0, 2, 99, -1, CURRENT_CAPABILITY_VERSION + 1])
def test_an_unimplemented_version_is_refused_never_downgraded(version):
    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        require_supported("windows.open_url", version)
    assert "approximated" in str(excinfo.value)


@pytest.mark.parametrize("version", ["1", 1.0, None, True, [1]])
def test_a_non_integer_version_is_refused(version):
    with pytest.raises(UnsupportedCapabilityError):
        require_supported("windows.open_url", version)


def test_the_supported_version_resolves(ctx):
    descriptor = require_supported("windows.open_url", CURRENT_CAPABILITY_VERSION)
    assert descriptor.kind is CapabilityKind.OPEN_URL


# --- 3. every capability validates its parameters -----------------------------


def test_an_unknown_parameter_key_is_refused_not_ignored(ctx):
    """Refused, because an ignored key is a field a later refactor may read."""
    with pytest.raises(ParameterError) as excinfo:
        validate(
            CapabilityKind.LAUNCH_APP,
            {"app_id": "notepad", "args": "--do-something"},
            ctx,
        )
    assert "args" in str(excinfo.value)
    assert "refused, not ignored" in str(excinfo.value)


@pytest.mark.parametrize(
    "kind,params",
    [
        (CapabilityKind.OPEN_URL, {"url": "https://example.com/page"}),
        (CapabilityKind.LAUNCH_APP, {"app_id": "notepad"}),
        (CapabilityKind.FOCUS_WINDOW, {"app_id": "notepad"}),
        (CapabilityKind.MANAGE_WINDOW, {"app_id": "notepad", "operation": "maximize"}),
        (CapabilityKind.CLIPBOARD_READ, {}),
        (CapabilityKind.CLIPBOARD_WRITE, {"text": "an ordinary sentence"}),
        (CapabilityKind.TYPE_TEXT, {"text": "an ordinary sentence"}),
        (
            CapabilityKind.ACCESSIBILITY_ACTION,
            {"app_id": "notepad", "operation": "expand", "element_name": "Details"},
        ),
    ],
)
def test_valid_parameters_produce_a_stable_fingerprint(kind, params, ctx):
    first = validate(kind, params, ctx)
    second = validate(kind, dict(reversed(list(params.items()))), ctx)
    assert first.fingerprint() == second.fingerprint()
    assert len(first.fingerprint()) == 64


def test_open_path_validates_against_the_real_filesystem(ctx, tmp_path):
    validated = validate(
        CapabilityKind.OPEN_PATH,
        {"path": str(tmp_path / "documents" / "notes.txt")},
        ctx,
    )
    assert validated.canonical["path"].endswith("notes.txt")


@pytest.mark.parametrize(
    "bad",
    [None, "a string", 42, [], ["url"], True],
)
def test_parameters_must_be_an_object(bad, ctx):
    with pytest.raises(ParameterError):
        validate(CapabilityKind.CLIPBOARD_READ, bad, ctx)


def test_clipboard_read_takes_no_parameters_at_all(ctx):
    assert validate(CapabilityKind.CLIPBOARD_READ, {}, ctx).canonical == {}
    with pytest.raises(ParameterError):
        validate(CapabilityKind.CLIPBOARD_READ, {"anything": 1}, ctx)


# --- URLs ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Windows/System32/config/SAM",
        "javascript:alert(1)",
        "ms-settings:privacy",
        "search-ms:query=password",
        "shell:startup",
        "vbscript:msgbox(1)",
        "data:text/html,<script>x</script>",
        "ftp://example.com/x",
        "//example.com/x",
        "example.com",
        "",
    ],
)
def test_only_safe_http_and_https_urls_are_accepted(url, ctx):
    with pytest.raises(ParameterError):
        validate(CapabilityKind.OPEN_URL, {"url": url}, ctx)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.com/",
        "https://user@example.com/",
        "http://admin:hunter2@example.com/page",
    ],
)
def test_a_url_with_embedded_credentials_is_refused(url, ctx):
    with pytest.raises(ParameterError) as excinfo:
        validate(CapabilityKind.OPEN_URL, {"url": url}, ctx)
    assert "credential" in str(excinfo.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com/",
        "https://notexample.com/",  # not a suffix on a label boundary
        "https://example.com.attacker.net/",  # the allowlisted name is not the host
        "https://xn--exmple-cua.com/",  # a homograph is a different host
    ],
)
def test_a_url_outside_the_domain_allowlist_is_refused(url, ctx):
    with pytest.raises(ParameterError):
        validate(CapabilityKind.OPEN_URL, {"url": url}, ctx)


def test_a_subdomain_of_an_allowlisted_domain_is_accepted(ctx):
    validated = validate(
        CapabilityKind.OPEN_URL,
        {"url": "https://docs.example.com/guide"},
        ctx,
    )
    assert validated.canonical["url"] == "https://docs.example.com/guide"


def test_an_empty_url_allowlist_permits_nothing(tmp_path):
    empty = ValidationContext(url_domains=UrlDomainAllowlist.from_iterable([]))
    with pytest.raises(ParameterError) as excinfo:
        validate(CapabilityKind.OPEN_URL, {"url": "https://example.com/"}, empty)
    assert "no URL domains are allowlisted" in str(excinfo.value)


# --- paths --------------------------------------------------------------------


def test_a_path_outside_the_allowlisted_roots_is_refused(ctx, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ParameterError):
        validate(CapabilityKind.OPEN_PATH, {"path": str(outside / "secret.txt")}, ctx)


def test_a_symlink_out_of_an_allowlisted_root_does_not_escape(ctx, tmp_path):
    """Resolution happens before the comparison, so the link is followed first."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "documents" / "innocent.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):  # pragma: no cover - platform without symlinks
        pytest.skip("this platform does not support symbolic links")
    with pytest.raises(ParameterError):
        validate(CapabilityKind.OPEN_PATH, {"path": str(link)}, ctx)


@pytest.mark.parametrize(
    "suffix",
    [".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".lnk", ".msi", ".reg", ".hta", ".scr"],
)
def test_opening_an_executable_or_script_is_refused(suffix, ctx, tmp_path):
    """Opening one of these with the shell *is* execution."""
    candidate = tmp_path / "documents" / f"payload{suffix}"
    candidate.write_text("x", encoding="utf-8")
    with pytest.raises(ParameterError) as excinfo:
        validate(CapabilityKind.OPEN_PATH, {"path": str(candidate)}, ctx)
    assert "executable" in str(excinfo.value) or "script" in str(excinfo.value)


@pytest.mark.parametrize(
    "path",
    [
        "documents/notes.txt",  # relative
        "../../etc/passwd",
        "\\\\remote-host\\share\\file.txt",  # UNC names another machine
    ],
)
def test_relative_traversing_and_remote_paths_are_refused(path, ctx):
    with pytest.raises(ParameterError):
        validate(CapabilityKind.OPEN_PATH, {"path": path}, ctx)


def test_the_governing_process_still_checks_a_path_it_cannot_resolve(tmp_path):
    """The server has no filesystem to stat, so it does the lexical half."""
    server_side = ValidationContext(
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["/srv/allowed"]),
        filesystem_available=False,
    )
    validate(CapabilityKind.OPEN_PATH, {"path": "/srv/allowed/report.pdf"}, server_side)
    with pytest.raises(ParameterError):
        validate(CapabilityKind.OPEN_PATH, {"path": "/srv/other/report.pdf"}, server_side)


# --- applications -------------------------------------------------------------


def test_launch_app_takes_a_key_and_has_nowhere_to_put_a_path(ctx):
    validated = validate(CapabilityKind.LAUNCH_APP, {"app_id": "notepad"}, ctx)
    assert validated.canonical == {"app_id": "notepad"}
    for forbidden in ("path", "executable", "args", "argv", "command", "cmd", "shell"):
        with pytest.raises(ParameterError):
            validate(
                CapabilityKind.LAUNCH_APP,
                {"app_id": "notepad", forbidden: "anything"},
                ctx,
            )


def test_an_application_outside_the_allowlist_is_refused(ctx):
    with pytest.raises(ParameterError):
        validate(CapabilityKind.LAUNCH_APP, {"app_id": "cmd"}, ctx)


def test_an_empty_application_allowlist_permits_nothing():
    empty = ValidationContext(applications=ApplicationAllowlist.from_pairs({}))
    with pytest.raises(ParameterError) as excinfo:
        validate(CapabilityKind.LAUNCH_APP, {"app_id": "notepad"}, empty)
    assert "no applications are allowlisted" in str(excinfo.value)


# --- windows ------------------------------------------------------------------


@pytest.mark.parametrize("operation", WINDOW_OPERATIONS)
def test_every_documented_window_operation_validates(operation, ctx):
    params = {"app_id": "notepad", "operation": operation}
    if operation == "move":
        params.update({"x": 100, "y": 100})
    if operation == "resize":
        params.update({"width": 800, "height": 600})
    validate(CapabilityKind.MANAGE_WINDOW, params, ctx)


@pytest.mark.parametrize("operation", ["close", "kill", "destroy", "hide", "screenshot", ""])
def test_an_undocumented_window_operation_is_refused(operation, ctx):
    with pytest.raises(ParameterError):
        validate(
            CapabilityKind.MANAGE_WINDOW,
            {"app_id": "notepad", "operation": operation},
            ctx,
        )


@pytest.mark.parametrize("extent", [0, 1, 63, 40000, -100])
def test_a_resize_outside_the_bounds_is_refused(extent, ctx):
    with pytest.raises(ParameterError):
        validate(
            CapabilityKind.MANAGE_WINDOW,
            {"app_id": "notepad", "operation": "resize", "width": extent, "height": 600},
            ctx,
        )


def test_a_move_needs_integer_coordinates(ctx):
    with pytest.raises(ParameterError):
        validate(
            CapabilityKind.MANAGE_WINDOW,
            {"app_id": "notepad", "operation": "move", "x": "100", "y": 100},
            ctx,
        )


# --- text ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", [CapabilityKind.TYPE_TEXT, CapabilityKind.CLIPBOARD_WRITE])
@pytest.mark.parametrize(
    "text",
    ["press\nenter", "tab\there", "carriage\rreturn", "vertical\x0btab", "null\x00byte"],
)
def test_text_may_not_contain_a_newline_tab_or_control_character(kind, text, ctx):
    """This is what makes 'cannot press Send' structural for type_text."""
    with pytest.raises(ParameterError) as excinfo:
        validate(kind, {"text": text}, ctx)
    assert "newline" in str(excinfo.value) or "control" in str(excinfo.value)


@pytest.mark.parametrize("kind", [CapabilityKind.TYPE_TEXT, CapabilityKind.CLIPBOARD_WRITE])
def test_over_long_text_is_refused_rather_than_truncated(kind, ctx):
    with pytest.raises(ParameterError) as excinfo:
        validate(kind, {"text": "a" * 100_000}, ctx)
    assert "refused rather than truncated" in str(excinfo.value)


@pytest.mark.parametrize("kind", [CapabilityKind.TYPE_TEXT, CapabilityKind.CLIPBOARD_WRITE])
def test_sensitive_text_never_appears_in_the_redacted_view(kind, ctx):
    validated = validate(kind, {"text": "an ordinary sentence"}, ctx)
    assert "text" in validated.canonical
    assert "text" not in validated.redacted
    assert validated.redacted["text_length"] == len("an ordinary sentence")
    assert len(validated.redacted["text_sha256"]) == 64
    assert validated.sensitive_keys == {"text"}


# --- accessibility ------------------------------------------------------------


@pytest.mark.parametrize("operation", ACCESSIBILITY_OPERATIONS)
def test_every_permitted_accessibility_operation_validates(operation, ctx):
    validate(
        CapabilityKind.ACCESSIBILITY_ACTION,
        {"app_id": "notepad", "operation": operation, "element_name": "Details panel"},
        ctx,
    )


@pytest.mark.parametrize(
    "operation",
    ["invoke", "click", "press", "toggle", "select", "activate", "close", ""],
)
def test_pressing_a_control_is_not_an_accessibility_operation(operation, ctx):
    """`invoke` is absent, and that absence is the design of this capability."""
    with pytest.raises(ParameterError) as excinfo:
        validate(
            CapabilityKind.ACCESSIBILITY_ACTION,
            {"app_id": "notepad", "operation": operation, "element_name": "Details"},
            ctx,
        )
    assert "not a permitted accessibility operation" in str(excinfo.value)


def test_invoke_is_absent_from_the_operation_vocabulary():
    assert "invoke" not in ACCESSIBILITY_OPERATIONS
    assert set(ACCESSIBILITY_OPERATIONS) == {
        "expand",
        "collapse",
        "scroll_up",
        "scroll_down",
        "focus_element",
    }


@pytest.mark.parametrize(
    "element",
    ["Send", "Submit order", "Confirm and pay", "Delete account", "Buy now", "Purchase"],
)
def test_an_element_named_like_a_final_action_is_refused(element, ctx):
    with pytest.raises(ParameterError) as excinfo:
        validate(
            CapabilityKind.ACCESSIBILITY_ACTION,
            {"app_id": "notepad", "operation": "expand", "element_name": element},
            ctx,
        )
    assert "final action" in str(excinfo.value)
