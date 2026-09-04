"""Regressions for defects an adversarial review of this package found.

Every test here failed before the fix it names. They are collected in one file
rather than scattered because they share a shape worth seeing together: each
was a place where a check existed and was *almost* right -- a truthiness test
where an identity test was needed, a re-check that repeated one of the two
things it should have repeated, a bound computed in the wrong unit, an error
class caught one level too broadly. None was a missing control. All of them
were a control that did not quite hold.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import sqlite3
from pathlib import Path

import pytest

from bartholomew.actuation import devices, seam, store
from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
    is_absolute_path,
    normalise_path,
)
from bartholomew.actuation.capabilities import ALL_CAPABILITIES, CapabilityKind
from bartholomew.actuation.parameters import (
    ParameterError,
    ValidationContext,
    validate,
)
from bartholomew.actuation.result import ActionResultStatus, ErrorCategory
from bartholomew.actuation.store import ActionPersistenceError, ActionState

TENANT = "tenant-a"
DEVICE = "desk-pc"


# ---------------------------------------------------------------------------
# 1. A character outside the BMP truncated into a control code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "codepoint,becomes",
    [
        (0x1000D, "Enter"),
        (0x10009, "Tab"),
        (0x1001B, "Escape"),
        (0x10008, "Backspace"),
        (0x1001A, "Substitute"),
    ],
)
def test_a_character_that_would_truncate_to_a_control_code_is_refused(codepoint, becomes):
    """`wScan` is 16 bits and ctypes truncates into it silently.

    U+1000D is a Linear B syllable. Its low sixteen bits are 0x0D, which is
    Enter -- so before this fix, a `type_text` of `"ok\\U0001000D"` passed every
    refusal in the validator and pressed Return at the operating system. That
    defeats the capability's central guarantee, and it defeats the approval
    binding too: the fingerprint was over the syllable and the OS received the
    carriage return.
    """
    char = chr(codepoint)
    assert ord(char) & 0xFFFF < 0x20, f"{becomes} is a control code"
    with pytest.raises(ParameterError) as excinfo:
        validate(CapabilityKind.TYPE_TEXT, {"text": f"ok{char}rest"})
    assert "Basic Multilingual Plane" in str(excinfo.value)


def test_the_truncation_is_a_real_property_of_the_field_not_a_theory():
    """The premise, checked: ctypes masks rather than raising."""
    from ctypes import wintypes

    class _Keyboard(ctypes.Structure):
        _fields_ = [("wScan", wintypes.WORD)]

    item = _Keyboard()
    item.wScan = 0x1000D
    assert item.wScan == 0x0D, "a WORD field silently keeps the low sixteen bits"


@pytest.mark.parametrize("kind", [CapabilityKind.TYPE_TEXT, CapabilityKind.CLIPBOARD_WRITE])
def test_ordinary_bmp_text_is_still_accepted(kind):
    """The refusal is of one plane, not of anything that looks unusual."""
    for text in (
        "an ordinary sentence",
        "caf\u00e9 na\u00efve",
        "\u4f60\u597d",
        "\u0645\u0631\u062d\u0628\u0627",
    ):
        validate(kind, {"text": text})


def test_the_win32_layer_refuses_the_same_thing_again():
    """Two fences, so a future caller reaching win32 by another route is safe."""
    from bartholomew.windows_actuation import win32

    with pytest.raises((win32.Win32CallError, win32.PlatformUnsupportedError)) as excinfo:
        win32.send_unicode_text("ok\U0001000d")
    # Off Windows the platform guard fires first, which is also a refusal.
    if isinstance(excinfo.value, win32.Win32CallError):
        assert "Basic Multilingual Plane" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 2. The clipboard buffer was sized in code points, not UTF-16 units
# ---------------------------------------------------------------------------


def test_the_clipboard_buffer_is_sized_from_the_buffer_itself():
    """`(len(text) + 1) * 2` under-allocates for any astral character.

    Under-allocating and then `memmove`-ing the surrogate pair drops the NUL
    terminator, publishing a clipboard block that every reader on the machine
    walks past the end of. Unreachable now that astral text is refused
    upstream, which is exactly why the arithmetic is pinned here rather than
    left to depend on that.
    """
    source = Path("bartholomew/windows_actuation/win32.py").read_text(encoding="utf-8")
    assert "buffer = ctypes.create_unicode_buffer(encoded)" in source
    assert "size = ctypes.sizeof(buffer)" in source
    assert "(len(encoded) + 1)" not in source

    # And the premise: a code-point count really does under-count here.
    astral = "\U0001f600"
    assert ctypes.sizeof(ctypes.create_unicode_buffer(astral)) > (len(astral) + 1) * 2


# ---------------------------------------------------------------------------
# 3. The open_path re-check repeated containment but not the extension test
# ---------------------------------------------------------------------------


def test_a_path_swapped_for_an_executable_after_validation_is_refused(tmp_path, monkeypatch):
    """The window between the validator's resolution and the handler's.

    `require_within` re-resolves symlinks from scratch, so the second
    resolution can land somewhere the first refused. An attacker who can write
    into an allowlisted root and win that window would otherwise have got
    `ShellExecuteW("open", "...payload.exe")` -- which is precisely the
    "replaced by a link" case the re-check exists for.
    """
    from bartholomew.windows_actuation import handlers as handlers_module
    from bartholomew.windows_actuation import win32
    from bartholomew.windows_actuation.config import ActionCompanionConfig
    from bartholomew.windows_actuation.handlers import HandlerContext

    root = tmp_path / "Documents"
    root.mkdir()
    document = root / "report.pdf"
    document.write_text("a real document", encoding="utf-8")
    payload = root / "payload.exe"
    payload.write_text("not really an executable", encoding="utf-8")

    ctx = HandlerContext(
        config=ActionCompanionConfig(
            base_url="https://127.0.0.1:5173",
            device_id=DEVICE,
            state_path=tmp_path / "state.json",
            applications=ApplicationAllowlist.from_pairs({}),
            url_domains=UrlDomainAllowlist.from_iterable(()),
            filesystem_roots=FilesystemRootAllowlist.from_iterable([str(root)]),
            capabilities=tuple(ALL_CAPABILITIES),
        ),
    )

    opened: list[str] = []
    monkeypatch.setattr(win32, "shell_open", lambda t: opened.append(t) or 42)

    # The document validates.
    validate(CapabilityKind.OPEN_PATH, {"path": str(document)}, ctx.validation_context())

    # ...and is then swapped for a link to the executable, inside the same root.
    document.unlink()
    try:
        document.symlink_to(payload)
    except (OSError, NotImplementedError):  # pragma: no cover - no symlink support
        pytest.skip("this platform does not support symbolic links")

    outcome = handlers_module.open_path({"path": str(document)}, ctx)
    assert outcome.status is ActionResultStatus.FAILED
    assert outcome.error_category is ErrorCategory.PARAMETERS_INVALID
    assert opened == [], "the shell was never asked to open anything"


# ---------------------------------------------------------------------------
# 4. Empty canonical parameters were read as purged ones
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self, db_path):
        from bartholomew.kernel.memory_store import MemoryStore

        self.mem = MemoryStore(db_path)
        self.db_path = db_path
        self.identity_context = None
        self.governance_store = None
        self.blocking_executor = None


def _device(**overrides):
    """An enrolled device. `capabilities=` accepts bare kinds or declarations."""
    if "capabilities" in overrides:
        overrides["capabilities"] = tuple(
            (
                c
                if isinstance(c, devices.DeclaredCapability)
                else devices.DeclaredCapability(kind=c, version=1)
            )
            for c in overrides["capabilities"]
        )
    fields = {
        "device_id": DEVICE,
        "tenant_id": TENANT,
        "platform": "windows",
        "enrolled": True,
        "capabilities": tuple(
            devices.DeclaredCapability(kind=k, version=1) for k in ALL_CAPABILITIES
        ),
        "applications": ApplicationAllowlist.from_pairs(
            {"notepad": "C:\\Windows\\System32\\notepad.exe"},
        ),
        "url_domains": UrlDomainAllowlist.from_iterable(["example.com"]),
        "filesystem_roots": FilesystemRootAllowlist.from_iterable(["C:\\Users\\t\\Documents"]),
    }
    fields.update(overrides)
    return devices.EnrolledDevice(**fields)


class _Registry:
    LABEL = "regression-registry"

    def __init__(self, *enrolled):
        self._by_key = {(d.tenant_id, d.device_id): d for d in enrolled}

    def lookup(self, *, tenant_id, device_id):
        return self._by_key.get((tenant_id, device_id))


@pytest.fixture
def db_path(tmp_path):
    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.orchestrator.safety import governance_store as gs

    path = str(tmp_path / "regressions.db")
    asyncio.run(MemoryStore(path).init())
    gs.ensure_schema(path)
    store.ensure_schema(path)
    return path


@pytest.mark.asyncio
async def test_clipboard_read_can_be_approved_and_dispatched(db_path):
    """It has no parameters by design, and `{}` is not `None`.

    Before the fix, `if not stored.parameters` treated a legitimately empty
    canonical parameter set as a purged one, so `windows.clipboard_read` --
    one of the nine -- could never leave `pending_approval`. It failed closed,
    so nothing unsafe happened; the capability was simply dead.
    """
    ctx = _Ctx(db_path)
    registry = _Registry(_device())

    requested = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.clipboard_read",
        capability_version=1,
        parameters={},
        registry=registry,
    )
    assert requested.governance_allowed
    action_id = requested.action.action_id
    assert requested.action.parameters == {}

    approved = await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        approver="taylor",
        registry=registry,
    )
    assert approved.governance_allowed, approved.reason
    assert approved.action.state is ActionState.APPROVED

    leased = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        registry=registry,
    )
    assert leased.governance_allowed, leased.reason
    assert leased.request.parameters.canonical == {}


@pytest.mark.asyncio
async def test_a_genuinely_purged_action_is_still_refused(db_path):
    """The other half: `None` must still deny, or the fix would be a hole."""
    ctx = _Ctx(db_path)
    registry = _Registry(_device())
    requested = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        registry=registry,
    )
    action_id = requested.action.action_id
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE windows_action_requests SET parameters_json = NULL WHERE action_id = ?",
            (action_id,),
        )
        conn.commit()
    denied = await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        approver="taylor",
        registry=registry,
    )
    assert not denied.governance_allowed
    assert denied.category is ErrorCategory.PARAMETERS_INVALID


# ---------------------------------------------------------------------------
# 5. Approval and the Identity policy: deliberate, and pinned as deliberate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_human_approval_is_not_gated_on_the_tool_use_allowlist(db_path):
    """`tool_use.allowlist` governs what *Bartholomew* may do, not a person.

    Pinned so that a later reading of the gate table cannot "fix" the absence
    of gate 9 here into a deadlock: `windows_action_approve` is absent from the
    allowlist and `default_allowed` is false, so gating approval on it would
    mean an Identity that forbids autonomous actuation also forbids a human
    from approving anything -- the exact inversion of the allowlist's purpose.
    """
    from identity_interpreter.identity_context import IdentityContext

    registry = _Registry(_device())
    permissive = _Ctx(db_path)
    permissive.identity_context = IdentityContext(
        tool_use_default_allowed=False,
        tool_use_allowlist=[seam.ACTION_KIND_REQUEST],
    )
    requested = await seam.run_action_request_through_runtime_contract(
        permissive,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        registry=registry,
    )
    assert requested.governance_allowed
    action_id = requested.action.action_id

    # Now the Identity forbids every actuation kind. The person can still
    # decide about the action that already exists.
    restrictive = _Ctx(db_path)
    restrictive.identity_context = IdentityContext(
        tool_use_default_allowed=False,
        tool_use_allowlist=[],
    )
    approved = await seam.grant_action_approval(
        restrictive,
        tenant_id=TENANT,
        action_id=action_id,
        approver="taylor",
        registry=registry,
    )
    assert approved.governance_allowed, approved.reason

    # And the gates that *do* apply still apply: dispatch needs the approval,
    # and a brake still stops it.
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    GovernanceStore(db_path).engage("actuation", reason="test", actor="test")
    try:
        denied = await seam.run_action_dispatch_through_runtime_contract(
            restrictive,
            tenant_id=TENANT,
            device_id=DEVICE,
            action_id=action_id,
            registry=registry,
        )
        assert denied.category is ErrorCategory.PARKING_BRAKE
    finally:
        GovernanceStore(db_path).disengage()


# ---------------------------------------------------------------------------
# 6. A re-submission could plant a fingerprint in the durable audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resubmitting_an_action_id_cannot_plant_a_fingerprint(db_path):
    """`create_action` never overwrote a row; the Reflection described the new one.

    So a caller who re-POSTed an approved action id with different parameters
    got a durable record reading "approved, fingerprint X" for a fingerprint
    nothing had ever approved -- and any caller able to POST an action could
    plant it against any pending id.
    """
    ctx = _Ctx(db_path)
    registry = _Registry(_device())

    original = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.clipboard_write",
        capability_version=1,
        parameters={"text": "the agreed sentence"},
        action_id="act-fixed",
        registry=registry,
    )
    stored_fingerprint = original.action.parameter_fingerprint
    await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id="act-fixed",
        approver="taylor",
        registry=registry,
    )

    resubmitted = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.clipboard_write",
        capability_version=1,
        parameters={"text": "something else entirely"},
        action_id="act-fixed",
        registry=registry,
    )
    # The stored action is unchanged, and the caller is told so.
    assert resubmitted.action.parameter_fingerprint == stored_fingerprint
    assert "were not used" in resubmitted.reason
    assert resubmitted.request is None, "the rejected request is not handed back"

    with sqlite3.connect(db_path) as conn:
        metas = [
            row[0]
            for row in conn.execute(
                "SELECT meta FROM reflections WHERE kind = 'action_reflection'",
            ).fetchall()
        ]

    approved_records = [m for m in metas if '"outcome": "approved"' in m]
    for meta in approved_records:
        assert stored_fingerprint in meta
    # The rejected fingerprint appears only as a rejected re-submission, never
    # attached to an approval.
    rejected = [m for m in metas if "rejected_resubmission_fingerprint" in m]
    assert len(rejected) == 1
    assert '"outcome": "resubmission_ignored"' in rejected[0]


# ---------------------------------------------------------------------------
# 7. A URL with a malformed port escaped every refusal path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:99999999999999999999/x",
        "https://example.com:abc/x",
        "https://example.com:-1/x",
    ],
)
def test_a_malformed_url_port_is_refused_and_not_raised(url):
    """`urlsplit(...).port` raises a *bare* ValueError.

    `ParameterError` subclasses `ValueError`, so an uncaught one escaped the
    seam's `except (ParameterError, RequestError)` entirely: HTTP 500, no
    Reflection, and therefore no durable trace of an allowlist-probing caller.
    """
    ctx = ValidationContext(url_domains=UrlDomainAllowlist.from_iterable(["example.com"]))
    try:
        validate(CapabilityKind.OPEN_URL, {"url": url}, ctx)
    except ParameterError:
        return
    except Exception as e:  # noqa: BLE001 - the point of the test
        pytest.fail(f"{url!r} raised {type(e).__name__} instead of ParameterError: {e}")
    pytest.fail(f"{url!r} was accepted")


def test_an_empty_port_means_the_default_port_and_canonicalises_away():
    """Not a malformed port: `https://host:/path` is `https://host/path`.

    Kept next to the refusals above so the boundary is explicit -- the fix
    catches the port that cannot be read, not the port that is simply absent.
    """
    ctx = ValidationContext(url_domains=UrlDomainAllowlist.from_iterable(["example.com"]))
    validated = validate(CapabilityKind.OPEN_URL, {"url": "https://example.com:/x"}, ctx)
    assert validated.canonical == {"url": "https://example.com/x"}


@pytest.mark.asyncio
async def test_the_seam_records_a_refusal_for_a_malformed_port(db_path):
    """The audit trace the escape used to bypass."""
    ctx = _Ctx(db_path)
    result = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.open_url",
        capability_version=1,
        parameters={"url": "https://example.com:99999999999999999999/x"},
        registry=_Registry(_device()),
    )
    assert not result.governance_allowed
    assert result.category is ErrorCategory.PARAMETERS_INVALID
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM reflections WHERE kind = 'action_reflection'",
        ).fetchone()[0]
    assert count >= 1, "the refusal left a durable trace"


# ---------------------------------------------------------------------------
# 8. A drive-root allowlist entry matched nothing beneath it
# ---------------------------------------------------------------------------


def test_a_drive_root_allowlist_entry_permits_paths_beneath_it():
    """`C:\\` normalised to `c:`, which then compared with the wrong separator."""
    roots = FilesystemRootAllowlist.from_iterable(["C:\\"])
    assert roots.contains("C:\\Users\\bob\\notes.txt")
    assert roots.contains("C:\\")
    assert not roots.contains("D:\\other\\notes.txt")


def test_a_narrower_root_still_matches_on_a_separator_boundary():
    """The fix must not have widened anything: `Documents` is not `DocumentsOld`."""
    roots = FilesystemRootAllowlist.from_iterable(["C:\\Users\\t\\Documents"])
    assert roots.contains("C:\\Users\\t\\Documents\\a.txt")
    assert roots.contains("C:\\Users\\t\\Documents")
    assert not roots.contains("C:\\Users\\t\\DocumentsOld\\a.txt")
    assert not roots.contains("C:\\Users\\t\\Doc")


def test_a_drive_relative_path_is_still_not_absolute():
    """`C:foo` means "foo, relative to the current directory on C:"."""
    assert not is_absolute_path("C:foo")
    assert is_absolute_path("C:\\foo")
    assert is_absolute_path("C:\\")
    assert normalise_path("C:\\") == "c:"


# ---------------------------------------------------------------------------
# 9. An unreadable database read as "nothing has happened"
# ---------------------------------------------------------------------------


def test_an_unreadable_database_raises_rather_than_reporting_an_empty_list(tmp_path):
    """`OperationalError` is also "unable to open database file".

    Returning `[]` for that made `GET /api/actions` answer
    `200 {"actions": []}` -- indistinguishable from "this tenant has never
    requested anything" -- for a database the process could not read. The
    route's own contract is that inspection is what a halt must not hide.
    """
    missing = str(tmp_path / "no-such-directory" / "actions.db")
    for call in (
        lambda: store.recent_actions(missing, tenant_id=TENANT),
        lambda: store.results_for(missing, tenant_id=TENANT, action_id="act-1"),
        lambda: store.dispatchable_action_ids(missing, tenant_id=TENANT, device_id=DEVICE),
        lambda: store.expire_overdue(missing, tenant_id=TENANT),
    ):
        with pytest.raises((ActionPersistenceError, OSError, sqlite3.Error)):
            call()


def test_a_database_with_no_action_table_yet_still_reads_as_empty(tmp_path):
    """The other half: "nothing has ever been requested" is a real answer."""
    path = str(tmp_path / "bare.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
        conn.commit()
    assert store.recent_actions(path, tenant_id=TENANT) == []
    assert store.results_for(path, tenant_id=TENANT, action_id="act-1") == []
    assert store.dispatchable_action_ids(path, tenant_id=TENANT, device_id=DEVICE) == []
    assert store.expire_overdue(path, tenant_id=TENANT) == 0


# ---------------------------------------------------------------------------
# 10. A refused report discarded the outcome permanently
# ---------------------------------------------------------------------------


def test_a_refused_report_is_kept_for_redelivery_not_discarded(tmp_path):
    """401 means the server never heard it, so it is not "answered".

    Marking it reported discarded, permanently and invisibly, the record that
    an action really ran: the ledger said reported, `resend_unreported()` found
    nothing, and the server's row sat at `leased` until it was swept to
    cancelled.
    """
    from bartholomew.windows_actuation.channel import ChannelResult, ChannelStatus
    from bartholomew.windows_actuation.config import ActionCompanionConfig
    from bartholomew.windows_actuation.runner import ActionCompanionRunner
    from bartholomew.windows_actuation.state import ActionStateFile

    config = ActionCompanionConfig(
        base_url="https://127.0.0.1:5173",
        device_id=DEVICE,
        state_path=tmp_path / "action-state.json",
        applications=ApplicationAllowlist.from_pairs({"notepad": "C:\\notepad.exe"}),
        url_domains=UrlDomainAllowlist.from_iterable(()),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(()),
        capabilities=tuple(ALL_CAPABILITIES),
    )

    refusals: list[str] = []

    class _RefusingClient:
        device_id = DEVICE

        def lease(self, *, limit):
            return ChannelResult(ChannelStatus.OK, 200, {"actions": []}), [], []

        def report(self, *, action_id, outcome, observed_at):
            refusals.append(action_id)
            return ChannelResult(ChannelStatus.REFUSED, 401, None, "credential rotated")

    runner = ActionCompanionRunner(config, client=_RefusingClient(), sleep=lambda _s: None)
    from bartholomew.actuation.result import HandlerOutcome

    runner._record_executed("act-ran", HandlerOutcome.succeeded("it really ran"))
    assert runner.report("act-ran", HandlerOutcome.succeeded("it really ran"), "now") is False
    assert runner.summary.unreported == 1

    reloaded = ActionStateFile(config.state_path).load()
    assert reloaded.executed["act-ran"].reported is False, "kept for re-delivery"

    # And once the credential works, it is delivered -- with its original status.
    delivered: list = []

    class _WorkingClient(_RefusingClient):
        def report(self, *, action_id, outcome, observed_at):
            delivered.append((action_id, outcome.status))
            return ChannelResult(ChannelStatus.OK, 200, {}, "")

    recovered = ActionCompanionRunner(config, client=_WorkingClient(), sleep=lambda _s: None)
    assert recovered.resend_unreported() == 1
    assert delivered == [("act-ran", ActionResultStatus.SUCCEEDED)]


def test_a_declined_late_result_is_marked_reported(tmp_path):
    """409 is different: the server has an opinion and it will not change."""
    from bartholomew.actuation.result import HandlerOutcome
    from bartholomew.windows_actuation.channel import ChannelResult, ChannelStatus
    from bartholomew.windows_actuation.config import ActionCompanionConfig
    from bartholomew.windows_actuation.runner import ActionCompanionRunner
    from bartholomew.windows_actuation.state import ActionStateFile

    config = ActionCompanionConfig(
        base_url="https://127.0.0.1:5173",
        device_id=DEVICE,
        state_path=tmp_path / "action-state.json",
        applications=ApplicationAllowlist.from_pairs({}),
        url_domains=UrlDomainAllowlist.from_iterable(()),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(()),
        capabilities=(),
    )

    class _DecliningClient:
        device_id = DEVICE

        def lease(self, *, limit):
            return ChannelResult(ChannelStatus.OK, 200, {"actions": []}), [], []

        def report(self, *, action_id, outcome, observed_at):
            return ChannelResult(ChannelStatus.REJECTED, 409, None, "already ended")

    runner = ActionCompanionRunner(config, client=_DecliningClient(), sleep=lambda _s: None)
    runner._record_executed("act-late", HandlerOutcome.succeeded("ran"))
    assert runner.report("act-late", HandlerOutcome.succeeded("ran"), "now") is True
    assert ActionStateFile(config.state_path).load().executed["act-late"].reported is True


# ---------------------------------------------------------------------------
# 11. Ledger eviction was lexicographic after any restart
# ---------------------------------------------------------------------------


def test_the_ledger_evicts_oldest_first_and_keeps_unreported_entries(tmp_path):
    """`save()` writes with `sort_keys=True`, so insertion order does not survive.

    Ids are `act-<uuid4>`, so eviction after a restart was effectively random:
    a just-executed action could leave the replay ledger while it was still
    inside its own TTL, and an unreported outcome could be dropped before it
    was ever delivered.
    """
    from bartholomew.windows_actuation.state import (
        MAX_LEDGER_ENTRIES,
        ActionCompanionState,
        ActionStateFile,
        ExecutedEntry,
    )

    file = ActionStateFile(tmp_path / "ledger.json")
    state = ActionCompanionState()

    # One old unreported entry, and a full ledger of newer reported ones with
    # ids that sort *before* it -- the arrangement the old code got wrong.
    state.executed["act-zzz-unreported"] = ExecutedEntry(
        action_id="act-zzz-unreported",
        status="succeeded",
        observed_at="2020-01-01T00:00:00Z",
        reported=False,
    )
    for i in range(MAX_LEDGER_ENTRIES + 5):
        state.executed[f"act-aaa-{i:06d}"] = ExecutedEntry(
            action_id=f"act-aaa-{i:06d}",
            status="succeeded",
            observed_at=f"2026-01-01T00:00:{i % 60:02d}Z",
            reported=True,
        )

    file.save(state)
    reloaded = file.load()
    assert len(reloaded.executed) == MAX_LEDGER_ENTRIES
    assert (
        "act-zzz-unreported" in reloaded.executed
    ), "an undelivered outcome is the last thing that may be dropped"
    # And what was dropped is the oldest reported entries, not a random slice.
    assert "act-aaa-000000" not in reloaded.executed
    assert f"act-aaa-{MAX_LEDGER_ENTRIES + 4:06d}" in reloaded.executed


def test_eviction_order_survives_a_round_trip_through_the_file(tmp_path):
    """The premise: the file really does reorder the dict."""
    from bartholomew.windows_actuation.state import (
        ActionCompanionState,
        ActionStateFile,
        ExecutedEntry,
    )

    file = ActionStateFile(tmp_path / "ledger.json")
    state = ActionCompanionState()
    for action_id, observed in (
        ("act-zzz", "2020-01-01T00:00:00Z"),
        ("act-aaa", "2026-01-01T00:00:00Z"),
    ):
        state.executed[action_id] = ExecutedEntry(action_id, "succeeded", observed, True)
    file.save(state)
    reloaded = file.load()

    assert list(reloaded.executed) == ["act-aaa", "act-zzz"], "insertion order is gone"
    assert reloaded.eviction_order()[0] == "act-zzz", "but the oldest is still first out"


# ---------------------------------------------------------------------------
# 12. `repeatability` was caller-supplied and switched off both replay guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "capability,parameters",
    [
        ("windows.type_text", {"text": "transfer 5000 to account 12345"}),
        ("windows.launch_app", {"app_id": "notepad"}),
        ("windows.open_url", {"url": "https://example.com/x"}),
        ("windows.clipboard_write", {"text": "an ordinary sentence"}),
        ("windows.clipboard_read", {}),
        (
            "windows.accessibility_action",
            {"app_id": "notepad", "operation": "expand", "element_name": "Details"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_caller_cannot_declare_a_consequential_action_idempotent(
    db_path,
    capability,
    parameters,
):
    """One field on the wire switched off *both* replay defences.

    `idempotent` relaxes the server's one-lease guard AND is what the device's
    durable ledger checks before refusing a repeat. A caller who could set it
    on `windows.type_text` could have one human approval type the text twice.
    Idempotence is a property of the capability now, and an ineligible claim is
    refused rather than quietly downgraded.
    """
    result = await seam.run_action_request_through_runtime_contract(
        _Ctx(db_path),
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability=capability,
        capability_version=1,
        parameters=parameters,
        repeatability="idempotent",
        registry=_Registry(_device()),
    )
    assert not result.governance_allowed
    assert result.category is ErrorCategory.PARAMETERS_INVALID
    assert "may not be declared idempotent" in result.reason


@pytest.mark.asyncio
async def test_the_two_pure_state_setting_capabilities_may_be_idempotent(db_path):
    """Focusing an already-focused window twice really does change nothing."""
    from bartholomew.actuation.capabilities import IDEMPOTENT_ELIGIBLE, CapabilityKind

    assert IDEMPOTENT_ELIGIBLE == {
        CapabilityKind.FOCUS_WINDOW,
        CapabilityKind.MANAGE_WINDOW,
    }
    result = await seam.run_action_request_through_runtime_contract(
        _Ctx(db_path),
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        repeatability="idempotent",
        registry=_Registry(_device()),
    )
    assert result.governance_allowed


def test_a_stored_row_claiming_ineligible_idempotence_is_refused_on_read_back():
    """A row written before the rule does not get the relaxed guard either."""
    from bartholomew.actuation.request import RequestError, rebuild_request, to_iso, utc_now

    with pytest.raises(RequestError, match="not permitted"):
        rebuild_request(
            {
                "action_id": "act-legacy",
                "tenant_id": TENANT,
                "device_id": DEVICE,
                "capability": "windows.type_text",
                "capability_version": 1,
                "parameters": {"text": "hello"},
                "correlation_id": "cor-1",
                "requested_by": "taylor",
                "issued_at": to_iso(utc_now()),
                "expires_at": to_iso(utc_now()),
                "repeatability": "idempotent",
            },
        )


# ---------------------------------------------------------------------------
# 13. Expiry swept in-flight leases, recording actions that ran as never run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_in_flight_lease_is_not_swept_the_moment_its_window_closes(db_path):
    """The lease poll runs the sweep, so it cancelled actions under devices.

    An action legitimately leased, whose expiry passes while its handler is
    running, was moved `leased -> cancelled/expired` by the very next poll --
    and the honest result the device then reported was declined as late. An
    action that really launched a program was recorded as never having run.
    """
    ctx = _Ctx(db_path)
    registry = _Registry(_device())
    requested = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.launch_app",
        capability_version=1,
        parameters={"app_id": "notepad"},
        registry=registry,
    )
    action_id = requested.action.action_id
    await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        approver="taylor",
        registry=registry,
    )
    leased = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        registry=registry,
    )
    assert leased.governance_allowed

    # The window closes while the handler is running.
    from bartholomew.actuation.request import to_iso, utc_now

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE windows_action_requests SET expires_at = ? WHERE action_id = ?",
            (to_iso(utc_now()), action_id),
        )
        conn.commit()

    store.expire_overdue(db_path, tenant_id=TENANT)
    still = store.get_action(db_path, tenant_id=TENANT, action_id=action_id)
    assert still.state is ActionState.LEASED, "an in-flight lease is left alone"

    # And the device's honest outcome is still recordable.
    recorded = await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        status="succeeded",
        error_category=None,
        detail="notepad is running",
        evidence={"process_id": 4321},
        observed_at=to_iso(utc_now()),
    )
    assert recorded.governance_allowed
    assert recorded.action.state is ActionState.SUCCEEDED


def test_a_lease_abandoned_past_its_grace_becomes_unknown_not_cancelled(db_path):
    """ "The device took it and we never heard back" is `unknown`, truthfully."""
    from datetime import timedelta

    from bartholomew.actuation.request import to_iso, utc_now
    from bartholomew.actuation.store import LEASE_GRACE_SECONDS

    store.create_action(
        db_path,
        record={
            "tenant_id": TENANT,
            "action_id": "act-abandoned",
            "device_id": DEVICE,
            "capability": "windows.launch_app",
            "capability_version": 1,
            "parameters": {"app_id": "notepad"},
            "parameter_fingerprint": "f" * 64,
            "correlation_id": "cor-1",
            "requested_by": "taylor",
            "risk_class": "moderate",
            "approval_requirement": "required_autonomy_eligible",
            "repeatability": "non_repeatable",
            "issued_at": to_iso(utc_now()),
            "expires_at": to_iso(utc_now() - timedelta(seconds=LEASE_GRACE_SECONDS + 60)),
        },
        canonical_parameters={"app_id": "notepad"},
        state=ActionState.APPROVED,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE windows_action_requests SET state = ?, lease_count = 1 WHERE action_id = ?",
            (ActionState.LEASED.value, "act-abandoned"),
        )
        conn.commit()

    assert store.expire_overdue(db_path, tenant_id=TENANT) >= 1
    swept = store.get_action(db_path, tenant_id=TENANT, action_id="act-abandoned")
    assert swept.state is ActionState.UNKNOWN
    assert swept.state_reason == ErrorCategory.EFFECT_UNVERIFIABLE.value
    assert swept.parameters is None, "the cleartext is purged"


# ---------------------------------------------------------------------------
# 14. The cleartext purge was unreachable in the shipped default configuration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unapproved_expired_action_loses_its_cleartext(db_path):
    """The purge used to live only inside the device lease endpoint.

    With no device resolver installed -- the shipped default -- that endpoint
    refuses at 401 before its body runs, so a `type_text` that was requested,
    never approved and forgotten kept its cleartext parameters indefinitely.
    That is the most likely lifecycle for one.
    """
    from datetime import timedelta

    from bartholomew.actuation.request import to_iso, utc_now

    ctx = _Ctx(db_path)
    registry = _Registry(_device())
    private = "my private diary line"
    requested = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.type_text",
        capability_version=1,
        parameters={"text": private},
        registry=registry,
    )
    action_id = requested.action.action_id
    with sqlite3.connect(db_path) as conn:
        assert (
            private
            in conn.execute(
                "SELECT parameters_json FROM windows_action_requests WHERE action_id = ?",
                (action_id,),
            ).fetchone()[0]
        )
        conn.execute(
            "UPDATE windows_action_requests SET expires_at = ? WHERE action_id = ?",
            (to_iso(utc_now() - timedelta(minutes=1)), action_id),
        )
        conn.commit()

    # Nothing device-side runs. The next ordinary request sweeps it.
    await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        registry=registry,
    )
    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            "SELECT parameters_json, state FROM windows_action_requests WHERE action_id = ?",
            (action_id,),
        ).fetchone()
    assert after[0] is None, "the cleartext is gone"
    assert after[1] == ActionState.CANCELLED.value


# ---------------------------------------------------------------------------
# 15. Racing approvals wrote the loser's approval under the winner's name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_recorded_approver_is_the_one_whose_approval_authorises(db_path):
    """Two overlapping approvals must not disagree about who approved.

    The approval row was written before the conditional transition, keyed only
    on the action, so the second writer overwrote the first -- and then lost
    the transition. The durable row named the winner while the object dispatch
    actually checked, including its expiry window, was the loser's.
    """
    ctx = _Ctx(db_path)
    registry = _Registry(_device())
    requested = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        registry=registry,
    )
    action_id = requested.action.action_id

    results = await asyncio.gather(
        seam.grant_action_approval(
            ctx,
            tenant_id=TENANT,
            action_id=action_id,
            approver="alice",
            registry=registry,
        ),
        seam.grant_action_approval(
            ctx,
            tenant_id=TENANT,
            action_id=action_id,
            approver="mallory",
            registry=registry,
        ),
    )
    winners = [r for r in results if r.governance_allowed]
    assert len(winners) == 1, "exactly one approval succeeds"

    recorded_approver = winners[0].action.approved_by
    approval = await seam.load_approval(ctx, tenant_id=TENANT, action_id=action_id)
    assert approval is not None
    assert (
        approval.approver == recorded_approver
    ), "the approval that authorises dispatch is the one the row names"


# ---------------------------------------------------------------------------
# 16. A result and its evidence must land together, or not at all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_terminal_result_and_its_evidence_row_are_one_transaction(db_path):
    ctx = _Ctx(db_path)
    registry = _Registry(_device())
    from bartholomew.actuation.request import to_iso, utc_now

    requested = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        registry=registry,
    )
    action_id = requested.action.action_id
    await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        approver="taylor",
        registry=registry,
    )
    await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        registry=registry,
    )
    await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        status="succeeded",
        error_category=None,
        detail="the window has the foreground",
        evidence={"hwnd": 1234},
        observed_at=to_iso(utc_now()),
    )
    action = store.get_action(db_path, tenant_id=TENANT, action_id=action_id)
    history = store.results_for(db_path, tenant_id=TENANT, action_id=action_id)
    assert action.state is ActionState.SUCCEEDED
    assert [r["status"] for r in history] == [
        "succeeded",
    ], "a terminal state always has the row that explains it"
    assert history[0]["evidence"] == {"hwnd": 1234}


def test_a_duplicate_progress_note_is_reported_as_not_recorded(db_path):
    """`INSERT OR IGNORE` dropped it while the device was told it landed."""
    from bartholomew.actuation.request import to_iso, utc_now
    from bartholomew.actuation.result import ActionResult

    store.create_action(
        db_path,
        record={
            "tenant_id": TENANT,
            "action_id": "act-progress",
            "device_id": DEVICE,
            "capability": "windows.focus_window",
            "capability_version": 1,
            "parameters": {"app_id": "notepad"},
            "parameter_fingerprint": "f" * 64,
            "correlation_id": "cor-1",
            "requested_by": "taylor",
            "risk_class": "low",
            "approval_requirement": "required_autonomy_eligible",
            "repeatability": "idempotent",
            "issued_at": to_iso(utc_now()),
            "expires_at": "2099-01-01T00:00:00Z",
        },
        canonical_parameters={"app_id": "notepad"},
        state=ActionState.APPROVED,
    )
    assert store.try_lease(
        db_path,
        tenant_id=TENANT,
        action_id="act-progress",
        repeatable=True,
    )

    def _note(detail):
        return ActionResult(
            action_id="act-progress",
            tenant_id=TENANT,
            device_id=DEVICE,
            status=ActionResultStatus.STARTED,
            detail=detail,
            error_category=ErrorCategory.EFFECT_UNVERIFIABLE,
        )

    _, first = store.record_result(db_path, result=_note("run one"))
    _, second = store.record_result(db_path, result=_note("run two"))
    assert first is True
    assert second is False, "the device is told its second note was not recorded"
    history = store.results_for(db_path, tenant_id=TENANT, action_id="act-progress")
    assert len(history) == 1


# ---------------------------------------------------------------------------
# 17. Evidence has a key allowlist, and a Reflection never carries the values
# ---------------------------------------------------------------------------


def test_a_device_cannot_write_arbitrary_content_into_a_durable_evidence_row():
    """Bounding the *size* of arbitrary content is not refusing content.

    `windows_action_results` has no DELETE anywhere and is served back by the
    read endpoint, so twelve keys of screen contents would have been permanent.
    """
    from bartholomew.actuation.result import PERMITTED_EVIDENCE_KEYS, bounded_evidence

    hostile = {
        "screen_contents": "the whole of somebody's inbox",
        "keystrokes": "everything they typed today",
        "document": "x" * 5000,
        "hwnd": 4321,
    }
    kept = bounded_evidence(hostile)
    assert kept["hwnd"] == 4321
    for refused in ("screen_contents", "keystrokes", "document"):
        assert refused not in kept
    assert "the whole of somebody's inbox" not in repr(kept)
    assert kept["dropped_keys"] == "document,keystrokes,screen_contents"
    # And the allowlist itself carries exactly one content-bearing name.
    assert "text" in PERMITTED_EVIDENCE_KEYS


@pytest.mark.asyncio
async def test_evidence_values_never_reach_a_reflection(db_path):
    """A nested dict passes straight through the Reflection sink's redaction."""
    from bartholomew.actuation.request import to_iso, utc_now

    ctx = _Ctx(db_path)
    registry = _Registry(_device())
    requested = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.clipboard_read",
        capability_version=1,
        parameters={},
        registry=registry,
    )
    action_id = requested.action.action_id
    await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        approver="taylor",
        registry=registry,
    )
    await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        registry=registry,
    )

    secret_ish = "contact bob@example.com and call 555-123-4567"
    await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        status="succeeded",
        error_category=None,
        detail="the clipboard was read",
        evidence={"text": secret_ish, "has_text": True},
        observed_at=to_iso(utc_now()),
    )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT content, meta FROM reflections WHERE kind = 'action_reflection'",
        ).fetchall()
    for content, meta in rows:
        assert secret_ish not in (content or "")
        assert secret_ish not in (meta or "")
        assert "bob@example.com" not in (meta or "")
    # The keys are recorded; the values are not.
    assert any("evidence_keys" in (m or "") for _c, m in rows)


# ---------------------------------------------------------------------------
# 18. A URL's query is a place credentials travel
# ---------------------------------------------------------------------------


def test_a_url_carrying_a_token_in_its_query_is_refused():
    """`open_url` refused embedded userinfo but waved a token in the query."""
    ctx = ValidationContext(url_domains=UrlDomainAllowlist.from_iterable(["example.com"]))
    token = "gh" + "p_" + "a" * 36
    with pytest.raises(ParameterError) as excinfo:
        validate(
            CapabilityKind.OPEN_URL,
            {"url": f"https://example.com/callback?access_token={token}"},
            ctx,
        )
    assert "credential material" in str(excinfo.value)


def test_the_kept_view_of_a_url_drops_its_query():
    """`parameters_redacted_json` is never purged, so it must be safe to keep."""
    ctx = ValidationContext(url_domains=UrlDomainAllowlist.from_iterable(["example.com"]))
    validated = validate(
        CapabilityKind.OPEN_URL,
        {"url": "https://example.com/search?q=something+personal#section"},
        ctx,
    )
    assert validated.canonical["url"].endswith("#section")
    assert validated.redacted["url"] == "https://example.com/search"
    assert "something+personal" not in repr(validated.redacted)
    assert validated.redacted["has_query"] is True
    assert len(validated.redacted["url_sha256"]) == 64


# ---------------------------------------------------------------------------
# 19. Unicode normalisation walked past the length bound and the detector
# ---------------------------------------------------------------------------


def test_normalisation_cannot_expand_text_past_its_limit():
    """NFC *lengthens* some strings: U+FB2C becomes three characters.

    The bound was applied to the input and the canonical value was the
    normalised one, so 1,400 of those characters passed a 4,096 limit and came
    out at 4,200 -- which also pushed anything after them past the secret
    detector's scan window.
    """
    import unicodedata

    expanding = chr(0xFB2C)
    assert len(unicodedata.normalize("NFC", expanding)) == 3, "the premise"

    padded = expanding * 1400 + " " + "sk" + "_live_" + "ABCDEFGHIJKLMNOPQRSTUV"
    with pytest.raises(ParameterError) as excinfo:
        validate(CapabilityKind.CLIPBOARD_WRITE, {"text": padded})
    assert "once normalised" in str(excinfo.value)


def test_the_secret_detector_refuses_over_long_input_rather_than_scanning_a_prefix():
    """Padding a credential past the scan window is not a way past the detector."""
    from bartholomew.actuation.sensitive import MAX_SCANNED_CHARS, detect_secrets

    padded = "a" * (MAX_SCANNED_CHARS + 10) + "AKIA" + "IOSFODNN" + "7EXAMPLE"
    findings = detect_secrets(padded)
    assert findings, "over-long input is a finding, not a clean scan"
    assert findings[0].category == "unscannable_length"


def test_an_expanding_character_cannot_multiply_the_keystroke_count():
    """`type_text`'s bound is on what would actually be sent."""
    with pytest.raises(ParameterError):
        validate(CapabilityKind.TYPE_TEXT, {"text": chr(0xFB2C) * 1024})


# ---------------------------------------------------------------------------
# 20. An unpaired surrogate crashed the seam with no audit row
# ---------------------------------------------------------------------------


#: Every capability, with a lone surrogate in each of its string parameters.
#: The first fix for this covered only the three capabilities that go through
#: `_ordinary_text`, which left `open_url` escaping as a `UnicodeEncodeError`
#: and `open_path` accepted-then-exploding at fingerprint time. The refusal now
#: lives in `_text`, which every string parameter passes through -- so this is
#: parametrised over all nine rather than over the two that were obvious.
_SURROGATE = "\ud800"
_SURROGATE_CASES = [
    (CapabilityKind.OPEN_URL, {"url": f"https://example.com/{_SURROGATE}"}),
    (CapabilityKind.OPEN_PATH, {"path": "C:\\Docs\\" + _SURROGATE + ".pdf"}),
    (CapabilityKind.LAUNCH_APP, {"app_id": "notepad" + _SURROGATE}),
    (CapabilityKind.FOCUS_WINDOW, {"app_id": _SURROGATE}),
    (CapabilityKind.MANAGE_WINDOW, {"app_id": _SURROGATE, "operation": "maximize"}),
    (CapabilityKind.CLIPBOARD_WRITE, {"text": f"ok{_SURROGATE}"}),
    (CapabilityKind.TYPE_TEXT, {"text": f"ok{_SURROGATE}"}),
    (
        CapabilityKind.ACCESSIBILITY_ACTION,
        {"app_id": "notepad", "operation": "expand", "element_name": _SURROGATE},
    ),
]


@pytest.mark.parametrize(
    "kind,params",
    _SURROGATE_CASES,
    ids=lambda v: v.value if isinstance(v, CapabilityKind) else "",
)
def test_no_capability_lets_a_surrogate_reach_a_digest(kind, params):
    """Refused, and refused *before* anything tries to encode it.

    Accepting one and raising at `fingerprint()` is as bad as raising during
    validation: both escape the seam's `except (ParameterError, RequestError)`
    and surface as a 500 with no Reflection.
    """
    ctx = ValidationContext(
        applications=ApplicationAllowlist.from_pairs({"notepad": "C:\\Windows\\notepad.exe"}),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["C:\\Docs"]),
    )
    try:
        validated = validate(kind, params, ctx)
        validated.fingerprint()
    except ParameterError as e:
        assert "surrogate" in str(e) or "allowlist" in str(e).lower()
        return
    except Exception as e:  # noqa: BLE001 - the point of the test
        pytest.fail(f"{kind.value} raised {type(e).__name__} instead of refusing: {e}")
    pytest.fail(f"{kind.value} accepted a lone surrogate")


@pytest.mark.parametrize("kind", [CapabilityKind.TYPE_TEXT, CapabilityKind.CLIPBOARD_WRITE])
def test_an_unpaired_surrogate_is_refused_not_raised(kind):
    """`json.loads('"\\\\ud800"')` produces one, so it is reachable from the wire.

    It passed the control-character and astral checks, and then
    `text.encode("utf-8")` raised `UnicodeEncodeError` -- which is a
    `ValueError` but not a `ParameterError`, so it escaped every refusal path
    and surfaced as a 500 with no Reflection: the same audit-evasion shape as
    the URL-port escape above.
    """
    import json

    lone = json.loads('"ok\\ud800rest"')
    try:
        validate(kind, {"text": lone})
    except ParameterError as e:
        assert "surrogate" in str(e)
        return
    except Exception as e:  # noqa: BLE001 - the point of the test
        pytest.fail(f"raised {type(e).__name__} instead of ParameterError: {e}")
    pytest.fail("an unpaired surrogate was accepted")


@pytest.mark.asyncio
async def test_a_surrogate_leaves_a_durable_refusal(db_path):
    import json

    result = await seam.run_action_request_through_runtime_contract(
        _Ctx(db_path),
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.type_text",
        capability_version=1,
        parameters={"text": json.loads('"ok\\ud800"')},
        registry=_Registry(_device()),
    )
    assert not result.governance_allowed
    assert result.category is ErrorCategory.PARAMETERS_INVALID
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM reflections WHERE kind = 'action_reflection'",
        ).fetchone()[0]
    assert count >= 1


# ---------------------------------------------------------------------------
# 21. The UIA scroll constants named the wrong enum members
# ---------------------------------------------------------------------------


def test_the_scroll_amounts_are_uias_actual_enum_values():
    """`ScrollAmount` is LargeDecrement=0, SmallDecrement=1, NoAmount=2, ...

    `NoAmount` was 3 -- which is `LargeIncrement` -- so every scroll asked for a
    large *horizontal* movement. On a vertically-scrollable-only pane, the one
    that matters, UIA rejects that outright and the blanket handler reported it
    as the accessibility adapter being unavailable rather than a bad argument.
    """
    from bartholomew.windows_actuation import uia

    assert uia._SCROLL_AMOUNT_SMALL_DECREMENT == 1
    assert uia._SCROLL_AMOUNT_NO_AMOUNT == 2
    assert uia._SCROLL_AMOUNT_SMALL_INCREMENT == 4
    # And a scroll asks for no horizontal movement at all.
    assert uia._SCROLL_AMOUNT_NO_AMOUNT != uia._SCROLL_AMOUNT_SMALL_INCREMENT


# ---------------------------------------------------------------------------
# 22. OS error codes, and the clipboard buffer
# ---------------------------------------------------------------------------


def test_os_error_codes_are_captured_by_ctypes_not_read_back_later():
    """`GetLastError()` is itself a call, so anything in between overwrites it.

    The clipboard retry loop slept between the failing `OpenClipboard` and the
    read, so the code in the audit row was whatever `Sleep` left behind -- and
    `launch_app` chooses `PERMISSION_DENIED` over `OS_CALL_FAILED` from that
    code.
    """
    source = Path("bartholomew/windows_actuation/win32.py").read_text(encoding="utf-8")
    assert 'ctypes.WinDLL("user32", use_last_error=True)' in source
    assert 'ctypes.WinDLL("kernel32", use_last_error=True)' in source
    assert 'ctypes.WinDLL("shell32", use_last_error=True)' in source
    assert "ctypes.get_last_error()" in source
    # No live call to the API version remains.
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "GetLastError", "read the captured error, not a fresh call"


def test_a_failed_clipboard_write_frees_its_buffer():
    """Ownership transfers to the system only when `SetClipboardData` succeeds.

    Without a `GlobalFree` on the failure paths, a companion that polls forever
    leaked the buffer on every failed write.
    """
    source = Path("bartholomew/windows_actuation/win32.py").read_text(encoding="utf-8")
    assert "GlobalFree" in source
    assert "lib.kernel32.GlobalFree(handle)" in source
    # And it is not called on the success path, which would be a double free.
    assert "owned = False" in source


# ---------------------------------------------------------------------------
# 23. The Windows installer's two PowerShell bugs
# ---------------------------------------------------------------------------


def test_the_installer_parenthesises_its_command_call_in_a_boolean():
    """`if (Test-Installed -and (...))` parses as a *command invocation*.

    `-and` and the second test are swallowed into `$args` and discarded, so the
    condition reduced to `Test-Installed` alone -- and `status` then threw on
    any machine where the companion was installed but never configured.
    """
    script = Path("deploy/windows/bartholomew-action.ps1").read_text(encoding="utf-8")
    assert "if ((Test-Installed) -and (Test-Path $EnvFile))" in script
    assert "if (Test-Installed -and" not in script


def test_no_expandable_string_contains_a_lone_backtick():
    """A backtick inside a double-quoted string is an escape, not a quote.

    Two ways it bites, and the first fix only covered one: `` `rollback` ``
    rendered as a carriage return through the middle of the install consent
    text, and `` `disable-startup` `` in the *other* consent text lost both
    backticks silently. The first is corruption, the second is a person being
    shown different words from the ones in the source -- and both are in text
    somebody reads before approving a change to their machine.

    So this checks every lone backtick in expandable text, not only the escapes
    PowerShell happens to recognise, and it covers double-quoted lines as well
    as here-strings.
    """
    import re

    script = Path("deploy/windows/bartholomew-action.ps1").read_text(encoding="utf-8")
    inside_here_string = False
    offenders = []
    for number, line in enumerate(script.splitlines(), start=1):
        if line.strip().endswith('@"'):
            inside_here_string = True
            continue
        if line.strip() == '"@':
            inside_here_string = False
            continue
        expandable = inside_here_string or ('"' in line and not line.lstrip().startswith("#"))
        if not expandable:
            continue
        # What is being looked for is a word *quoted* with backticks in text a
        # person reads -- ``install`` rendered as `install`, or worse as a
        # carriage return. A single lone backtick is legitimate PowerShell: it
        # is either a deliberate escape (`n in a joined string) or a
        # line continuation at the end of a line. A *pair* around a word in an
        # expandable string is the mistake, every time.
        stripped = line.replace("``", "")
        if re.search(r"(?<!`)`[A-Za-z][\w.-]*`(?!`)", stripped):
            offenders.append((number, line.strip()))
    assert (
        not offenders
    ), "lone backticks in expandable strings render as escapes or vanish: " + "; ".join(
        f"line {n}: {t!r}" for n, t in offenders
    )


# ---------------------------------------------------------------------------
# 24. Defects introduced by the fixes above, found by reviewing the fixes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refusal_on_an_existing_action_discloses_nothing_about_it(db_path):
    """The write verb was still a read oracle, on the path nobody re-checked.

    `_record_refusal` threw away `create_action`'s `created` flag, so a refusal
    that landed on a pre-existing row reported `existing=False` and the route
    returned the victim's whole record -- `approved_by`, `approved_at`,
    `lease_count`, everything -- from a verb requiring only `action:request`.
    Re-POST a known id with a capability the device does not declare and the
    refusal carried the answer.
    """
    ctx = _Ctx(db_path)
    registry = _Registry(_device(capabilities=[CapabilityKind.FOCUS_WINDOW]))

    victim = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor.the.boss",
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        action_id="victim-1",
        registry=registry,
    )
    assert victim.governance_allowed
    await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id="victim-1",
        approver="taylor.the.boss",
        registry=registry,
    )

    probe = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="nosy",
        capability="windows.launch_app",
        capability_version=1,
        parameters={"app_id": "notepad"},
        action_id="victim-1",
        registry=registry,
    )
    assert not probe.governance_allowed
    assert probe.existing is True, "the refusal must know it landed on an existing row"
    assert probe.request is None, "and must not describe the probe's own request"

    # The route then answers with the minimal summary, not the record.
    from bartholomew_api_bridge_v0_1.services.api.routes.actions import _body

    rendered = json.dumps(_body(probe))
    assert "taylor.the.boss" not in rendered
    assert "approved_by" not in rendered
    assert "lease_count" not in rendered

    # And the durable audit does not gain the probe's capability either.
    with sqlite3.connect(db_path) as conn:
        metas = [
            r[0]
            for r in conn.execute(
                "SELECT meta FROM reflections WHERE kind = 'action_reflection'",
            ).fetchall()
        ]
    for meta in metas:
        if "victim-1" in meta and "approved" in meta:
            assert "windows.launch_app" not in meta


@pytest.mark.asyncio
async def test_a_resubmission_with_the_same_parameters_but_another_capability(db_path):
    """Keying `superseded` on the fingerprint alone left every other axis open.

    `{"app_id": "notepad"}` digests identically for `launch_app` and
    `focus_window`, so a re-POST that changed only the capability was not
    treated as superseding -- and the Reflection then took `capability`,
    `capability_version`, `device_id` and `risk_class` from the request beside
    the stored row's state.
    """
    ctx = _Ctx(db_path)
    registry = _Registry(_device(), _device(device_id="laptop"))

    await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.launch_app",
        capability_version=1,
        parameters={"app_id": "notepad"},
        action_id="act-C",
        registry=registry,
    )
    await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id="act-C",
        approver="taylor",
        registry=registry,
    )

    for label, overrides in (
        ("capability", {"capability": "windows.focus_window"}),
        ("device", {"device_id": "laptop"}),
    ):
        probe = await seam.run_action_request_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            device_id=overrides.get("device_id", DEVICE),
            requested_by="taylor",
            capability=overrides.get("capability", "windows.launch_app"),
            capability_version=1,
            parameters={"app_id": "notepad"},
            action_id="act-C",
            registry=registry,
        )
        assert probe.existing is True, f"{label}: the row already exists"
        assert probe.request is None, f"{label}: the probe's request describes nothing"
        assert probe.action.capability == "windows.launch_app"
        assert probe.action.device_id == DEVICE

    with sqlite3.connect(db_path) as conn:
        metas = [
            r[0]
            for r in conn.execute(
                "SELECT meta FROM reflections WHERE kind = 'action_reflection'",
            ).fetchall()
        ]
    for meta in metas:
        if '"outcome": "approved"' in meta:
            assert "windows.focus_window" not in meta
            assert '"device_id": "laptop"' not in meta


@pytest.mark.asyncio
async def test_an_approval_whose_write_fails_can_be_approved_again(db_path, monkeypatch):
    """Inverting the order fixed a race and created a permanent dead end.

    `mark_approved` moves only from `pending_approval`, so an action left at
    `approved` with no approval on file could never be approved again -- the
    retry was refused for not awaiting approval and dispatch for the missing
    approval, while the response said "safe to retry" and the row showed an
    approver who had been told they failed.
    """
    ctx = _Ctx(db_path)
    registry = _Registry(_device())
    requested = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        registry=registry,
    )
    action_id = requested.action.action_id

    async def _fail(*_a, **_k):
        return "the approval could not be recorded: OperationalError: disk I/O error"

    monkeypatch.setattr(seam, "_write_approval", _fail)
    failed = await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        approver="taylor",
        registry=registry,
    )
    assert not failed.governance_allowed
    assert "returned to pending approval" in failed.reason
    assert failed.action.state is ActionState.PENDING_APPROVAL
    assert failed.action.approved_by is None, "no approver is recorded for a failed grant"

    # And the retry, once the store works again, really does succeed.
    monkeypatch.undo()
    retried = await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        approver="taylor",
        registry=registry,
    )
    assert retried.governance_allowed, retried.reason
    assert retried.action.state is ActionState.APPROVED
    leased = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        registry=registry,
    )
    assert leased.governance_allowed


def test_a_terminal_result_really_is_atomic_with_its_evidence_row(db_path):
    """The test named for this fix used to pass against the pre-fix code.

    It drove the happy path, which the old two-transaction version also
    satisfied. The failure mode -- the evidence insert failing after the
    transition committed -- was never exercised. Here it is: drop the results
    table mid-flight and assert the action did NOT move to a terminal state.
    """
    from bartholomew.actuation.request import to_iso, utc_now
    from bartholomew.actuation.result import ActionResult

    store.create_action(
        db_path,
        record={
            "tenant_id": TENANT,
            "action_id": "act-atomic",
            "device_id": DEVICE,
            "capability": "windows.focus_window",
            "capability_version": 1,
            "parameters": {"app_id": "notepad"},
            "parameter_fingerprint": "f" * 64,
            "correlation_id": "cor-1",
            "requested_by": "taylor",
            "risk_class": "low",
            "approval_requirement": "required_autonomy_eligible",
            "repeatability": "non_repeatable",
            "issued_at": to_iso(utc_now()),
            "expires_at": "2099-01-01T00:00:00Z",
        },
        canonical_parameters={"app_id": "notepad"},
        state=ActionState.APPROVED,
    )
    assert store.try_lease(db_path, tenant_id=TENANT, action_id="act-atomic", repeatable=False)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE windows_action_results")
        conn.commit()

    result = ActionResult(
        action_id="act-atomic",
        tenant_id=TENANT,
        device_id=DEVICE,
        status=ActionResultStatus.SUCCEEDED,
        detail="it worked",
    )
    with pytest.raises(ActionPersistenceError):
        store.record_result(db_path, result=result)

    still = store.get_action(db_path, tenant_id=TENANT, action_id="act-atomic")
    assert still.state is ActionState.LEASED, (
        "the transition must roll back with the evidence insert, or the action ends "
        "with nothing in the history explaining how"
    )


@pytest.mark.asyncio
async def test_a_duplicate_progress_note_does_not_claim_the_action_ended(db_path):
    """It is still `leased`, and the body's own `state` field says so."""
    from bartholomew.actuation.request import to_iso, utc_now

    ctx = _Ctx(db_path)
    registry = _Registry(_device(trusted_autonomy=[CapabilityKind.FOCUS_WINDOW]))
    requested = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        repeatability="idempotent",
        registry=registry,
    )
    action_id = requested.action.action_id
    await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        registry=registry,
    )

    async def _note():
        return await seam.record_action_result_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            device_id=DEVICE,
            action_id=action_id,
            status="started",
            error_category=None,
            detail="running",
            evidence={},
            observed_at=to_iso(utc_now()),
        )

    first = await _note()
    assert first.governance_allowed
    second = await _note()
    assert not second.governance_allowed
    assert "still running" in second.reason
    assert "already happened" not in second.reason
    assert second.action.state is ActionState.LEASED


def test_the_device_channel_names_the_actual_cause_of_a_refusal():
    """One sentence per cause, and a status a device can act on.

    Everything used to be 403 "this action had already ended" -- about actions
    that were live -- and the companion maps 403 to "not authenticated", so a
    malformed payload sent an operator to check credentials.
    """
    from bartholomew_api_bridge_v0_1.services.api.routes.device_actions import (
        _refusal_response,
    )

    class _R:
        def __init__(self, category):
            self.category = category

    assert _refusal_response(_R(ErrorCategory.REPLAY_REFUSED))[0] == 409
    assert _refusal_response(_R(ErrorCategory.PARAMETERS_INVALID))[0] == 422
    assert _refusal_response(_R(ErrorCategory.GOVERNANCE_DENIED))[0] == 403
    assert _refusal_response(_R(ErrorCategory.DEVICE_NOT_ENROLLED))[0] == 404
    for category in (
        ErrorCategory.PARAMETERS_INVALID,
        ErrorCategory.GOVERNANCE_DENIED,
        ErrorCategory.DEVICE_NOT_ENROLLED,
    ):
        assert "already ended" not in _refusal_response(_R(category))[1]


def test_the_evidence_key_cap_actually_caps():
    """`dropped_keys` was appended after the cap, so the bound was cap + 1."""
    from bartholomew.actuation.result import (
        MAX_EVIDENCE_KEYS,
        PERMITTED_EVIDENCE_KEYS,
        bounded_evidence,
    )

    permitted = sorted(PERMITTED_EVIDENCE_KEYS - {"dropped_keys"})
    assert len(permitted) > MAX_EVIDENCE_KEYS, "the premise needs a full map"
    raw = dict.fromkeys(permitted, 1)
    raw["not_a_permitted_key"] = "screen contents"
    kept = bounded_evidence(raw)
    assert len(kept) <= MAX_EVIDENCE_KEYS
    assert "dropped_keys" in kept
    assert "screen contents" not in repr(kept)


@pytest.mark.asyncio
async def test_a_lease_that_expires_in_the_gap_is_reported_as_expired(db_path):
    """`try_lease` now refuses for expiry too, so `None` no longer means replay.

    Hard-coding "already leased and non-repeatable" wrote a clock event into
    the audit as a replay attempt, in a sentence false on both clauses.
    """
    from datetime import timedelta

    from bartholomew.actuation.request import to_iso, utc_now

    ctx = _Ctx(db_path)
    registry = _Registry(_device())
    requested = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability="windows.focus_window",
        capability_version=1,
        parameters={"app_id": "notepad"},
        registry=registry,
    )
    action_id = requested.action.action_id
    await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        approver="taylor",
        registry=registry,
    )

    # Expire it after admission would have passed but before the lease lands:
    # the row is still `approved` with `lease_count = 0`, so the old code
    # called it a replay.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE windows_action_requests SET expires_at = ? WHERE action_id = ?",
            (to_iso(utc_now() - timedelta(seconds=1)), action_id),
        )
        conn.commit()
    assert store.try_lease(db_path, tenant_id=TENANT, action_id=action_id, repeatable=False) is None
    fresh = store.get_action(db_path, tenant_id=TENANT, action_id=action_id)
    assert fresh.state is ActionState.APPROVED and fresh.lease_count == 0


# ---------------------------------------------------------------------------
# 25. The URL secret scan refused nearly every real URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/anthropics/claude-code/blob/main/README.md",
        "https://example.com/wiki/List_of_Unicode_characters",
        "https://example.com/products/winter-coats-for-children",
        "https://example.com/handbook/onboarding/first-week-checklist",
        "https://example.com/calendar/u/0/r/week/2026/9/3",
        "https://example.com/guide",
        "https://example.com/",
    ],
)
def test_an_ordinary_url_is_not_mistaken_for_credential_material(url):
    """Scanning the whole URL was worse than not scanning at all.

    The high-entropy candidate class includes `/`, so a path is one long token
    -- and ordinary English slugs score 3.8-4.3 bits per character against a
    3.5 threshold that is only comfortable for long prose. Every real URL with
    a path was refused, *and* recorded in the audit under the category that
    exists to count credential attempts, destroying that signal.
    """
    ctx = ValidationContext(url_domains=UrlDomainAllowlist.from_iterable(["example.com"]))
    validate(CapabilityKind.OPEN_URL, {"url": url}, ctx)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/callback?access_token={token}",
        "https://example.com/cb#id_token={token}",
        "https://example.com/x?password=hunter2correcthorse",
        "https://example.com/x?api_key={token}",
    ],
)
def test_a_credential_in_the_query_or_fragment_is_still_refused(url):
    """The true positive the scan exists for, still caught."""
    ctx = ValidationContext(url_domains=UrlDomainAllowlist.from_iterable(["example.com"]))
    token = "gh" + "p_" + "a" * 36
    with pytest.raises(ParameterError) as excinfo:
        validate(CapabilityKind.OPEN_URL, {"url": url.format(token=token)}, ctx)
    assert "credential material" in str(excinfo.value)


def test_a_fragment_is_not_reported_as_a_query():
    """`has_query` was set for a fragment-only URL, so the row said one existed."""
    ctx = ValidationContext(url_domains=UrlDomainAllowlist.from_iterable(["example.com"]))
    fragment_only = validate(
        CapabilityKind.OPEN_URL,
        {"url": "https://example.com/page#section"},
        ctx,
    )
    assert "has_query" not in fragment_only.redacted
    assert fragment_only.redacted["has_fragment"] is True

    query_only = validate(
        CapabilityKind.OPEN_URL,
        {"url": "https://example.com/page?a=b"},
        ctx,
    )
    assert query_only.redacted["has_query"] is True
    assert "has_fragment" not in query_only.redacted


# ---------------------------------------------------------------------------
# 26. A silent truncation walked the clipboard past the detector
# ---------------------------------------------------------------------------


def test_an_over_long_clipboard_is_refused_rather_than_scanned_clean():
    """`read_clipboard_text` used to cut at exactly the detector's window.

    So 4,096 filler characters followed by an API key was handed to the
    detector as a clean prefix and recorded as `sensitive: False` -- the very
    padding bypass the `unscannable_length` branch was added to prevent, one
    call frame away from it.
    """
    from bartholomew.actuation.sensitive import MAX_SCANNED_CHARS, secret_categories
    from bartholomew.windows_actuation.win32 import (
        MAX_CLIPBOARD_CHARS,
        MAX_CLIPBOARD_READ_CHARS,
    )

    assert MAX_CLIPBOARD_READ_CHARS > MAX_SCANNED_CHARS, (
        "the read cap must sit above the scan window, or an over-long clipboard "
        "arrives already trimmed to something that scans clean"
    )
    assert MAX_CLIPBOARD_READ_CHARS > MAX_CLIPBOARD_CHARS

    padded = "a" * (MAX_SCANNED_CHARS + 100) + "AKIA" + "IOSFODNN" + "7EXAMPLE"
    assert secret_categories(padded) == ("unscannable_length",)


# ---------------------------------------------------------------------------
# 27. An outcome "kept for re-delivery" was only ever re-delivered at start-up
# ---------------------------------------------------------------------------


def test_an_unreported_outcome_is_resent_on_a_later_cycle(tmp_path):
    """`resend_unreported()` ran once per process, not once per cycle.

    The condition that refuses a report -- a rotated credential, a resolver
    that comes back -- clears server-side without the client restarting, so a
    long-running companion never delivered the outcome it had deliberately
    kept. The server's row sat at `leased` until it was swept: exactly the
    "an action that happened, recorded as one that never did" the retention
    was for.
    """
    from bartholomew.actuation.result import HandlerOutcome
    from bartholomew.windows_actuation.channel import ChannelResult, ChannelStatus
    from bartholomew.windows_actuation.config import ActionCompanionConfig
    from bartholomew.windows_actuation.runner import ActionCompanionRunner
    from bartholomew.windows_actuation.state import ActionStateFile

    config = ActionCompanionConfig(
        base_url="https://127.0.0.1:5173",
        device_id=DEVICE,
        state_path=tmp_path / "action-state.json",
        applications=ApplicationAllowlist.from_pairs({}),
        url_domains=UrlDomainAllowlist.from_iterable(()),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(()),
        capabilities=(),
    )

    class _RecoveringClient:
        """Refuses the first report, then accepts everything."""

        device_id = DEVICE

        def __init__(self):
            self.reports: list[str] = []
            self.refusing = True

        def lease(self, *, limit):
            return ChannelResult(ChannelStatus.OK, 200, {"actions": []}), [], []

        def report(self, *, action_id, outcome, observed_at):
            self.reports.append(action_id)
            if self.refusing:
                return ChannelResult(ChannelStatus.REFUSED, 401, None, "credential rotated")
            return ChannelResult(ChannelStatus.OK, 200, {}, "")

    client = _RecoveringClient()
    runner = ActionCompanionRunner(config, client=client, sleep=lambda _s: None)
    runner._record_executed("act-ran", HandlerOutcome.succeeded("it really ran"))
    runner.report("act-ran", HandlerOutcome.succeeded("it really ran"), "now")
    assert ActionStateFile(config.state_path).load().executed["act-ran"].reported is False

    # The credential is fixed server-side. The process does NOT restart.
    client.refusing = False
    runner.run(cycles=1)

    assert client.reports.count("act-ran") == 2, "re-sent on the next cycle"
    assert ActionStateFile(config.state_path).load().executed["act-ran"].reported is True


# ---------------------------------------------------------------------------
# 28. An extension that could never match, and a test that could never fail
# ---------------------------------------------------------------------------


def test_every_executable_extension_can_actually_match():
    """Each is compared against `.suffix.lower()`, so a capital never matches.

    `.searchConnector-ms` was in the set with a capital C, which meant the one
    entry aimed at a Windows search-connector file was dead.
    """
    from bartholomew.actuation.parameters import EXECUTABLE_EXTENSIONS

    wrong_case = sorted(e for e in EXECUTABLE_EXTENSIONS if e != e.lower())
    assert not wrong_case, f"these can never match a lowercased suffix: {wrong_case}"
    assert all(e.startswith(".") for e in EXECUTABLE_EXTENSIONS)


def test_the_surrogate_regression_asserts_the_surrogate_and_nothing_else():
    """Three of its cases used to pass on the allowlist error instead.

    `assert "surrogate" in str(e) or "allowlist" in str(e).lower()` was
    satisfied by `AllowlistError` for `launch_app`, `focus_window` and
    `manage_window`, so deleting the surrogate check left them green. The
    refusal has to be *the* refusal, not any refusal.
    """
    ctx = ValidationContext(
        applications=ApplicationAllowlist.from_pairs({"notepad": "C:\\Windows\\notepad.exe"}),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["C:\\Docs"]),
    )
    lone = json.loads('"\\ud800"')
    # An app_id that is otherwise valid apart from the surrogate, so the
    # allowlist cannot be what refuses it.
    for kind, params in (
        (CapabilityKind.LAUNCH_APP, {"app_id": f"notepad{lone}"}),
        (CapabilityKind.FOCUS_WINDOW, {"app_id": f"notepad{lone}"}),
        (
            CapabilityKind.MANAGE_WINDOW,
            {"app_id": "notepad", "operation": f"maximize{lone}"},
        ),
        (
            CapabilityKind.ACCESSIBILITY_ACTION,
            {"app_id": f"notepad{lone}", "operation": "expand", "element_name": "D"},
        ),
        (
            CapabilityKind.ACCESSIBILITY_ACTION,
            {"app_id": "notepad", "operation": f"expand{lone}", "element_name": "D"},
        ),
    ):
        with pytest.raises(ParameterError) as excinfo:
            validate(kind, params, ctx)
        assert "surrogate" in str(
            excinfo.value,
        ), f"{kind.value} refused for the wrong reason: {excinfo.value}"
