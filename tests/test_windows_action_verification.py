"""Act -> Verify: an issued action is not an action that happened.

W03-C acceptance criterion 1. Before this wave `windows.type_text` was a
permanent `effect_unverifiable`: the handler sent keystrokes, Windows accepted
them, and the honest answer stopped there because reading the field back was
treated as reading the person's writing.

It is. So the read is reduced to a length and a digest inside
`uia.focused_field_text()` and the contents never reach the handler at all --
and with that, a verdict distinct from issuance becomes possible without a
second copy of anybody's text existing anywhere.

What each half of this file proves
----------------------------------
* **The device half** (`type_text` + `uia`): where a provider exists, a full
  type is `succeeded` because the field was read back and agreed; where one
  does not, it is still `unknown`, for a stated reason.
* **The server half** (`actuation/verification.py`): the adapter W03-A's
  governed read-back is installed into. Its whole failure mode is
  `UNVERIFIABLE`, and the non-vacuity tests below remove the provider, break
  it, and make it lie about its type, and none of those produces a
  confirmation.

**Non-vacuity throughout.** Every affirmative assertion here has a paired one
that removes the thing being asserted and shows the verdict stops being
affirmative. A verification test that cannot fail when verification is deleted
would be worse than no test, because it would read like coverage.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from bartholomew.actuation import devices, seam, store, verification
from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import ALL_CAPABILITIES
from bartholomew.actuation.result import (
    PERMITTED_EVIDENCE_KEYS,
    VERIFY_METHODS,
    ActionResultStatus,
    ErrorCategory,
)
from bartholomew.actuation.verification import ReadBack, Verdict
from bartholomew.windows_actuation import handlers as handlers_module
from bartholomew.windows_actuation import uia, win32
from bartholomew.windows_actuation.config import ActionCompanionConfig
from bartholomew.windows_actuation.handlers import HandlerContext

TENANT = "tenant-a"
DEVICE = "desk-pc"
TYPED = "an ordinary sentence"


# ---------------------------------------------------------------------------
# the device half
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx(tmp_path):
    return HandlerContext(
        config=ActionCompanionConfig(
            base_url="http://127.0.0.1:8000",
            device_id=DEVICE,
            state_path=tmp_path / "action-state.json",
            capabilities=frozenset(ALL_CAPABILITIES),
            applications=ApplicationAllowlist.from_pairs({"notepad": "C:\\notepad.exe"}),
            url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
            filesystem_roots=FilesystemRootAllowlist.from_iterable([str(tmp_path)]),
        ),
    )


def _typeable_field(monkeypatch):
    """A focused element the sensitive-field checks let through."""
    monkeypatch.setattr(
        uia,
        "focused_field",
        lambda: uia.FocusedField(
            is_password=False,
            name="Message body",
            automation_id="body",
            help_text=None,
            control_type=uia.UIA_EDIT_CONTROL_TYPE,
        ),
    )


def _readbacks(monkeypatch, *values):
    """Make `focused_field_text()` return these measurements, in order.

    Measured through `uia._measure` rather than constructed by hand, so the
    test exercises the same reduction the real read uses -- including the fact
    that it produces a digest and a length and drops the string.
    """
    queue = [uia._measure(v) if isinstance(v, str) else v for v in values]
    monkeypatch.setattr(uia, "focused_field_text", lambda: queue.pop(0))


def test_a_verified_type_is_a_success_not_an_unknown(ctx, monkeypatch):
    """The criterion, in one assertion: read back, agreed, therefore succeeded."""
    _typeable_field(monkeypatch)
    monkeypatch.setattr(win32, "send_unicode_text", lambda t: len(t) * 2)
    _readbacks(monkeypatch, "Dear Sam, ", "Dear Sam, " + TYPED)

    outcome = handlers_module.type_text({"text": TYPED}, ctx)

    assert outcome.status is ActionResultStatus.SUCCEEDED
    assert outcome.evidence["verified"] is True
    assert outcome.evidence["verify_method"] == "uia_value_read_back"
    assert outcome.evidence["text_length_before"] == 10
    assert outcome.evidence["text_length_after"] == 10 + len(TYPED)


def test_the_verdict_is_distinct_from_issuance(ctx, monkeypatch):
    """Every event accepted, and the field does not hold them. Not a success.

    This is the case that makes the whole step worth having: `events_sent`
    says the injection was complete, and the read-back says the characters are
    not there -- a focus change between the check and the injection. Before
    W03-C both of these were reported identically.
    """
    _typeable_field(monkeypatch)
    monkeypatch.setattr(win32, "send_unicode_text", lambda t: len(t) * 2)
    _readbacks(monkeypatch, "Dear Sam, ", "Dear Sam, ")  # nothing landed

    outcome = handlers_module.type_text({"text": TYPED}, ctx)

    assert outcome.evidence["events_sent"] == len(TYPED) * 2, "issuance was complete"
    assert outcome.status is ActionResultStatus.FAILED, "and the effect was not"
    assert outcome.evidence["verified"] is False
    assert outcome.error_category is ErrorCategory.OS_CALL_FAILED


def test_an_unreadable_field_is_still_honestly_unknown(ctx, monkeypatch):
    """Where no provider exists, the answer stays `unknown` and says why.

    The half of the criterion that is about not over-claiming. A build without
    `comtypes`, or a control with no value property, must not become a success
    just because a verification step now exists.
    """
    _typeable_field(monkeypatch)
    monkeypatch.setattr(win32, "send_unicode_text", lambda t: len(t) * 2)
    _readbacks(
        monkeypatch,
        uia.FieldText(readable=False, unavailable_reason="comtypes is absent"),
        uia.FieldText(readable=False, unavailable_reason="comtypes is absent"),
    )

    outcome = handlers_module.type_text({"text": TYPED}, ctx)

    assert outcome.status is ActionResultStatus.UNKNOWN
    assert outcome.error_category is ErrorCategory.EFFECT_UNVERIFIABLE
    assert outcome.evidence["verified"] is False
    assert outcome.evidence["verify_method"] == "unavailable"
    assert "comtypes is absent" in outcome.detail


def test_verification_never_puts_the_field_contents_in_the_evidence(ctx, monkeypatch):
    """The privacy condition the whole design is built around.

    The field is read back, and what is stored is a length and a digest of the
    *typed* text. No part of what was already in the field, and no part of what
    it holds afterwards, appears anywhere in the outcome.
    """
    private = "my private notes about the medical appointment"
    _typeable_field(monkeypatch)
    monkeypatch.setattr(win32, "send_unicode_text", lambda t: len(t) * 2)
    _readbacks(monkeypatch, private, private + TYPED)

    outcome = handlers_module.type_text({"text": TYPED}, ctx)

    assert outcome.status is ActionResultStatus.SUCCEEDED
    rendered = repr(outcome.evidence) + outcome.detail
    assert private not in rendered
    assert "medical" not in rendered
    for token in private.split():
        assert token not in outcome.evidence.values()
    # The one digest present is of the text the action carried, which the
    # approver already read at the approval surface -- not of the field.
    assert outcome.evidence["text_sha256"] == hashlib.sha256(TYPED.encode()).hexdigest()


def test_a_field_text_object_cannot_carry_the_text(monkeypatch):
    """Structural, not behavioural: there is no attribute to leak it through."""
    import dataclasses

    fields = {f.name for f in dataclasses.fields(uia.FieldText)}
    assert fields == {"readable", "length", "digest", "unavailable_reason"}
    measured = uia._measure("something private")
    assert "something private" not in repr(measured)


def test_every_verify_method_a_handler_emits_is_in_the_closed_vocabulary(ctx, monkeypatch):
    """A device cannot name its own, more flattering, verification method."""
    _typeable_field(monkeypatch)
    monkeypatch.setattr(win32, "send_unicode_text", lambda t: len(t) * 2)
    seen = set()
    for before, after in (
        ("", TYPED),
        ("", "nothing like it"),
        (uia.FieldText(readable=False, unavailable_reason="no adapter"),) * 2,
    ):
        _readbacks(monkeypatch, before, after)
        seen.add(handlers_module.type_text({"text": TYPED}, ctx).evidence["verify_method"])
    assert seen
    assert seen <= VERIFY_METHODS


def test_the_new_evidence_keys_are_on_the_allowlist():
    """A key the allowlist does not carry is silently dropped, so pin them."""
    for key in (
        "verified",
        "verify_method",
        "text_length_before",
        "text_length_after",
        "server_verified",
        "steps_completed",
    ):
        assert key in PERMITTED_EVIDENCE_KEYS


# ---------------------------------------------------------------------------
# the comparison itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "expected", "matched"),
    [
        ("", "hello", "hello", True),
        ("", "hell", "hello", False),
        ("", "olleh", "hello", False),
        ("prefix ", "prefix hello", "hello", True),
        ("prefix ", "prefix hel", "hello", False),
        ("prefix ", "prefix ", "hello", False),
    ],
)
def test_the_typed_text_comparison_discriminates(before, after, expected, matched):
    verdict = uia.verify_typed_text(
        expected=expected,
        before=uia._measure(before),
        after=uia._measure(after),
    )
    assert verdict.observed is True
    assert verdict.matched is matched


def test_an_unreadable_measurement_is_observed_false_not_matched_false():
    """Two different facts, and one boolean cannot carry both."""
    verdict = uia.verify_typed_text(
        expected="hello",
        before=uia.FieldText(readable=False, unavailable_reason="no value property"),
        after=uia._measure("hello"),
    )
    assert verdict.observed is False
    assert verdict.matched is False
    assert "no value property" in verdict.reason


# ---------------------------------------------------------------------------
# the server half: W03-A's read-back, consumed defensively
# ---------------------------------------------------------------------------


class _Provider:
    """A stand-in for W03-A's governed read-back primitive.

    Satisfies `ReadBackProvider` and nothing more, which is the point of the
    Protocol: W03-C depends on one method with one shape, so W03-A can build
    whatever it needs behind it.
    """

    def __init__(self, text=None, *, available=True, raises=False, answer=None):
        self.text = text
        self.available = available
        self.raises = raises
        self.answer = answer
        self.calls: list[dict] = []

    def read_back(self, *, tenant_id, device_id, target, expected=None):
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "device_id": device_id,
                "target": target,
                "expected": expected,
            },
        )
        if self.raises:
            raise RuntimeError("the observation channel is unavailable")
        if self.answer is not None:
            return self.answer
        if not self.available:
            return ReadBack.unavailable("no consented capture session covers this target")
        return ReadBack.from_text(self.text or "", expected=expected)


@pytest.fixture
def no_provider():
    """Every test in this file starts and ends with nothing installed."""
    verification.install_read_back_provider(None)
    yield
    verification.install_read_back_provider(None)


def test_with_no_provider_the_verdict_is_unverifiable(no_provider):
    """The degradation that makes this adapter safe to ship before W03-A lands."""
    verdict = verification.verify_effect(
        tenant_id=TENANT,
        device_id=DEVICE,
        target="windows.type_text",
        expected_text=TYPED,
    )
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.confirmed is False
    assert verdict.method == "unavailable"


def test_an_installed_provider_can_confirm(no_provider):
    provider = _Provider(text="Dear Sam, " + TYPED)
    verification.install_read_back_provider(provider)

    verdict = verification.verify_effect(
        tenant_id=TENANT,
        device_id=DEVICE,
        target="windows.type_text",
        expected_text=TYPED,
    )
    assert verdict.verdict is Verdict.CONFIRMED
    assert verdict.confirmed is True
    assert provider.calls == [
        {
            "tenant_id": TENANT,
            "device_id": DEVICE,
            "target": "windows.type_text",
            "expected": TYPED,
        },
    ]


def test_an_installed_provider_can_contradict(no_provider):
    verification.install_read_back_provider(_Provider(text="something else entirely"))
    verdict = verification.verify_effect(
        tenant_id=TENANT,
        device_id=DEVICE,
        target="windows.type_text",
        expected_text=TYPED,
    )
    assert verdict.verdict is Verdict.CONTRADICTED
    assert verdict.confirmed is False


@pytest.mark.parametrize(
    ("provider", "expected_text"),
    [
        (_Provider(available=False), TYPED),
        (_Provider(raises=True), TYPED),
        (_Provider(answer={"available": True, "contains_expected": True}), TYPED),
        (_Provider(answer="confirmed, honestly"), TYPED),
        (_Provider(text=TYPED), None),
    ],
    ids=["unavailable", "raises", "wrong-type-dict", "wrong-type-str", "nothing-to-compare"],
)
def test_every_way_a_provider_can_fail_produces_unverifiable(no_provider, provider, expected_text):
    """Five failure modes, one verdict, and it is never affirmative.

    The last case is the subtle one: the provider answered perfectly well and
    there was simply nothing to compare its answer against. "The window is
    still there" must not be allowed to stand in for "the text landed".
    """
    verification.install_read_back_provider(provider)
    verdict = verification.verify_effect(
        tenant_id=TENANT,
        device_id=DEVICE,
        target="windows.type_text",
        expected_text=expected_text,
    )
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.confirmed is False


def test_a_read_back_never_carries_the_text_it_read(no_provider):
    """`ReadBack.from_text` reduces at the boundary, like `uia.FieldText`."""
    import dataclasses

    private = "the diagnosis and the appointment time"
    reduced = ReadBack.from_text(private, expected="appointment")
    assert reduced.contains_expected is True
    assert private not in repr(reduced)
    assert reduced.length == len(private)
    fields = {f.name for f in dataclasses.fields(ReadBack)}
    assert "text" not in fields and "value" not in fields and "contents" not in fields


# ---------------------------------------------------------------------------
# the server verdict reaches the durable row, and never overwrites the device
# ---------------------------------------------------------------------------


class _Ctx:
    """The duck-typed runtime context every seam in this repository reads."""

    def __init__(self, db_path):
        from bartholomew.kernel.memory_store import MemoryStore

        self.mem = MemoryStore(db_path)
        self.db_path = db_path
        self.identity_context = None
        self.governance_store = None
        self.blocking_executor = None


@pytest.fixture
def db_path(tmp_path):
    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.orchestrator.safety import governance_store as gs

    path = str(tmp_path / "verify.db")
    asyncio.run(MemoryStore(path).init())
    gs.ensure_schema(path)
    store.ensure_schema(path)
    return path


@pytest.fixture
def registry():
    """One enrolled Windows device, and genuinely no others."""
    device = devices.EnrolledDevice(
        device_id=DEVICE,
        tenant_id=TENANT,
        platform="windows",
        enrolled=True,
        capabilities=tuple(devices.DeclaredCapability(kind=k, version=1) for k in ALL_CAPABILITIES),
        applications=ApplicationAllowlist.from_pairs({"notepad": "C:\\notepad.exe"}),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["C:\\Users\\t\\Documents"]),
        trusted_autonomy=frozenset(),
    )

    class _Registry:
        LABEL = "verification-test-registry"

        def lookup(self, *, tenant_id, device_id):
            return device if (tenant_id, device_id) == (TENANT, DEVICE) else None

    reg = _Registry()
    devices.install_registry(reg)
    yield reg
    devices.install_registry(None)


@pytest.fixture(autouse=True)
def armed_channel():
    """The coarse gate, opened, so it is not what any assertion here turns on."""
    from bartholomew.actuation import arming

    arming.arm(tenant_id=TENANT, device_id=DEVICE, armed_by="test-fixture", reason="verify suite")
    yield
    arming.reset_for_tests()


@pytest.fixture
def leased(db_path, registry):
    """One action in `leased`, driven there through the real envelope.

    Requested, approved and dispatched through the seam rather than inserted,
    so the row a result is reported against is one that genuinely passed the
    eleven checks -- the same thing the HTTP route produces.
    """
    ctx = _Ctx(db_path)

    async def _drive():
        requested = await seam.run_action_request_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            device_id=DEVICE,
            requested_by="taylor",
            capability="windows.type_text",
            capability_version=1,
            parameters={"text": TYPED},
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
        leased_result = await seam.run_action_dispatch_through_runtime_contract(
            ctx,
            tenant_id=TENANT,
            device_id=DEVICE,
            action_id=action_id,
            registry=registry,
        )
        assert leased_result.governance_allowed
        return action_id

    return asyncio.run(_drive())


async def _report(ctx, action_id, status, category):
    return await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        status=status,
        error_category=category,
        detail="",
        evidence={"events_sent": 2},
        observed_at="",
    )


@pytest.mark.asyncio
async def test_a_server_verdict_is_recorded_beside_an_unknown(db_path, leased, no_provider):
    verification.install_read_back_provider(_Provider(text="Dear Sam, " + TYPED))
    ctx = _Ctx(db_path)

    result = await _report(ctx, leased, "unknown", "effect_unverifiable")

    assert result.governance_allowed
    rows = store.results_for(db_path, tenant_id=TENANT, action_id=leased)
    assert rows[0]["evidence"]["server_verified"] is True


@pytest.mark.asyncio
async def test_a_server_verdict_never_overwrites_the_devices_status(db_path, leased, no_provider):
    """The device was on the machine. The server records beside it, not over it.

    A confirming read-back does **not** turn the device's honest `unknown` into
    a `succeeded`. That would be the same fiction the result vocabulary exists
    to refuse, relocated one process away -- and it would make a governed
    observation channel into an authority over what a device claims to have
    seen.
    """
    verification.install_read_back_provider(_Provider(text="Dear Sam, " + TYPED))
    ctx = _Ctx(db_path)

    result = await _report(ctx, leased, "unknown", "effect_unverifiable")

    assert result.status is ActionResultStatus.UNKNOWN
    action = store.get_action(db_path, tenant_id=TENANT, action_id=leased)
    assert action.state is store.ActionState.UNKNOWN


@pytest.mark.asyncio
async def test_without_a_provider_no_server_verification_key_is_written(
    db_path,
    leased,
    no_provider,
):
    """Absent is not the same as False, so the key is simply not there.

    "Nobody asked" and "somebody asked and could not confirm" are different
    facts about an audit row, and padding every ordinary result with a
    `server_verified: false` would erase the difference.
    """
    ctx = _Ctx(db_path)
    await _report(ctx, leased, "unknown", "effect_unverifiable")

    rows = store.results_for(db_path, tenant_id=TENANT, action_id=leased)
    assert "server_verified" not in rows[0]["evidence"]


@pytest.mark.asyncio
async def test_a_device_that_saw_its_own_effect_is_not_second_guessed(db_path, leased, no_provider):
    """A `succeeded` is the stronger observation; the server does not re-open it."""
    provider = _Provider(text="nothing like the text at all")
    verification.install_read_back_provider(provider)
    ctx = _Ctx(db_path)

    result = await _report(ctx, leased, "succeeded", None)

    assert result.status is ActionResultStatus.SUCCEEDED
    assert provider.calls == [], "the provider was never consulted"
    rows = store.results_for(db_path, tenant_id=TENANT, action_id=leased)
    assert "server_verified" not in rows[0]["evidence"]


def test_installing_and_removing_a_provider_is_visible_to_an_importer(no_provider):
    """The holder exists so a rebound module global is not the mechanism."""
    assert verification.get_read_back_provider() is None
    provider = _Provider(text="x")
    verification.install_read_back_provider(provider)
    assert verification.get_read_back_provider() is provider
    verification.install_read_back_provider(None)
    assert verification.get_read_back_provider() is None


def test_the_actuation_package_does_not_import_the_observation_package():
    """The adapter is installed, never imported. Two trust channels, one edge.

    `bartholomew/actuation/` deciding whether a machine may be acted on and
    `bartholomew/multimodal/` watching a person's screen are different
    channels, and an import edge between them is the coupling the channel
    separation tests exist to refuse. The Protocol plus the holder is how
    W03-C consumes W03-A's read-back without creating one.
    """
    import ast
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "bartholomew" / "actuation"
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                module = ",".join(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            if module:
                assert "multimodal" not in module, f"{path.name} imports {module}"
                assert "companion" not in module, f"{path.name} imports {module}"


def test_the_device_registry_and_the_provider_holder_are_separate_authorities():
    """One device truth, and a read-back provider that is not a second one."""
    assert verification.get_read_back_provider.__module__ == "bartholomew.actuation.verification"
    assert devices.get_registry.__module__ == "bartholomew.actuation.devices"
    verification.install_read_back_provider(None)
    devices.install_registry(None)
