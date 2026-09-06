"""W03-B: interpretation, capability selection, and the refusal to guess.

Acceptance criterion 3 in `W03_B_CONTRACT.md`, and `W03_TEST_CONTRACTS.md` §4:
when interpretation is materially ambiguous the executive raises a
clarification rather than guessing. The canonical case is "open it".

Nothing here touches a database, a device, or governance --- `intent.py` and
`selection.py` are pure, and these tests are the proof of that as much as of
what they recognise.
"""

from __future__ import annotations

import pytest

from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import ALL_CAPABILITIES, CapabilityKind
from bartholomew.actuation.devices import DeclaredCapability, EnrolledDevice
from bartholomew.executive import intent as intent_module
from bartholomew.executive.intent import (
    AMBIGUITY_NO_CAPABILITY,
    AMBIGUITY_PARAMETER_MISSING,
    AMBIGUITY_REFERENT,
    MAX_PLAN_STEPS,
    clarification_subject,
    parse_task,
)
from bartholomew.executive.selection import (
    NOT_DECLARED,
    NOT_IN_VOCABULARY,
    VERSION_MISMATCH,
    select_capability,
)


def _device(capabilities=None, *, versions=None, trusted_autonomy=()):
    kinds = capabilities if capabilities is not None else ALL_CAPABILITIES
    return EnrolledDevice(
        device_id="desk-pc",
        tenant_id="tenant-a",
        platform="windows",
        enrolled=True,
        capabilities=tuple(
            DeclaredCapability(kind=k, version=(versions or {}).get(k, 1)) for k in kinds
        ),
        applications=ApplicationAllowlist.from_pairs(
            {"notepad": "C:\\Windows\\System32\\notepad.exe"},
        ),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["C:\\Users\\t\\Documents"]),
        trusted_autonomy=frozenset(trusted_autonomy),
    )


class TestAmbiguityBecomesAQuestion:
    """The canonical case, and its neighbours."""

    def test_open_it_produces_a_clarification_and_no_step(self):
        result = parse_task("open it")
        assert result.steps == ()
        assert [a.code for a in result.ambiguities] == [AMBIGUITY_REFERENT]
        assert not result.actionable

    @pytest.mark.parametrize(
        "instruction",
        ["open it", "open that", "open this", "open the file", "open the thing", "open"],
    )
    def test_every_bare_referent_is_a_question(self, instruction):
        result = parse_task(instruction)
        assert result.steps == ()
        assert result.ambiguities

    @pytest.mark.parametrize(
        "instruction",
        ["open the report", "open my notepad", "open annual report", "open the quarterly file"],
    )
    def test_naming_a_thing_rather_than_a_key_is_a_question(self, instruction):
        """An article, or more than one word, means a *thing* --- which thing is open.

        "Open notepad" is an application key. "Open the report" is a document,
        a page or a program and the sentence does not say which. Taking the
        first word of a phrase as an allowlist key would be picking a target
        out of a sentence, so it asks instead. The friction on "open my
        notepad" is the price, and it is the right way round.
        """
        result = parse_task(instruction)
        assert result.steps == ()
        assert [a.code for a in result.ambiguities] == [AMBIGUITY_REFERENT]

    def test_a_url_or_a_path_is_never_ambiguous_however_it_is_introduced(self):
        assert parse_task("open the page at https://example.com/x").steps[0].capability == (
            "windows.open_url"
        )
        assert parse_task("open the file C:\\Users\\t\\Documents\\a.txt").steps[0].capability == (
            "windows.open_path"
        )

    def test_the_question_names_the_three_things_it_could_be(self):
        question = parse_task("open it").ambiguities[0].question
        assert "application" in question
        assert "file path" in question or "file" in question
        assert "URL" in question

    def test_typing_without_quoted_text_asks_rather_than_inventing_wording(self):
        result = parse_task("type my address")
        assert result.steps == ()
        assert [a.code for a in result.ambiguities] == [AMBIGUITY_PARAMETER_MISSING]

    def test_an_unrecognised_fragment_is_a_question_not_a_silent_drop(self):
        result = parse_task("dance a jig")
        assert result.steps == ()
        assert [a.code for a in result.ambiguities] == [AMBIGUITY_NO_CAPABILITY]

    def test_an_empty_instruction_asks_what_to_do(self):
        result = parse_task("   ")
        assert result.ambiguities and result.steps == ()

    def test_half_an_understood_instruction_is_still_not_actionable(self):
        result = parse_task("launch notepad and then open it")
        assert result.steps  # the first half was understood
        assert result.ambiguities  # the second was not
        assert not result.actionable
        assert any("whole" in note for note in result.notes)

    def test_a_clarification_subject_names_every_open_question(self):
        result = parse_task("open it and then type my address")
        subject = clarification_subject(result)
        assert "Open what" in subject
        assert "type" in subject
        assert len(subject) <= 500


class TestTheVocabularyIsClosed:
    def test_every_recognised_step_names_a_real_capability_kind(self):
        instructions = [
            "open notepad",
            "open https://example.com/x",
            "open C:\\Users\\t\\Documents\\a.txt",
            'type "hello"',
            "read the clipboard",
            'copy "hello" to the clipboard',
            "maximise notepad",
            "focus notepad",
            "scroll down in notepad",
        ]
        for instruction in instructions:
            for step in parse_task(instruction).steps:
                assert CapabilityKind(step.capability)

    @pytest.mark.parametrize(
        "instruction,described",
        [
            ("delete the report", "delete anything"),
            ("run a script that tidies my desktop", "run a command, script or shell"),
            ("install the new build", "install software"),
            ("send the report to Dana", "send or publish"),
            ("buy me a coffee subscription", "buy or pay for anything"),
            ("rename the file to final.docx", "create, move, rename or edit a file"),
            ("click the Send button", "press a button"),
        ],
    )
    def test_out_of_vocabulary_requests_are_declined_not_translated(self, instruction, described):
        result = parse_task(instruction)
        assert result.steps == ()
        assert [u.described_as for u in result.unsupported] == [described]

    def test_a_step_cannot_be_constructed_for_a_capability_that_does_not_exist(self):
        with pytest.raises(ValueError):
            intent_module.IntentStep(
                capability="windows.run_command",
                parameters={},
                described_as="run a command",
            )

    def test_an_app_id_is_never_a_path_or_an_executable(self):
        assert intent_module._app_id("C:\\Windows\\notepad.exe") is None
        assert intent_module._app_id("notepad.exe") is None
        assert intent_module._app_id("notepad") == "notepad"


class TestSequenceRecognition:
    def test_a_two_step_instruction_keeps_the_order_it_was_said_in(self):
        result = parse_task('focus notepad and then type "hello there"')
        assert [s.capability for s in result.steps] == [
            CapabilityKind.FOCUS_WINDOW.value,
            CapabilityKind.TYPE_TEXT.value,
        ]
        assert result.steps[1].parameters == {"text": "hello there"}

    def test_typing_a_word_that_is_also_an_app_name_is_still_typing(self):
        result = parse_task('type "notepad"')
        assert [s.capability for s in result.steps] == [CapabilityKind.TYPE_TEXT.value]

    def test_more_steps_than_the_bound_is_a_question_not_a_long_plan(self):
        instruction = " then ".join(["focus notepad"] * (MAX_PLAN_STEPS + 1))
        result = parse_task(instruction)
        assert result.steps == ()
        assert result.ambiguities


class TestCapabilitySelection:
    def test_a_declared_capability_is_available_with_its_risk_and_approval(self):
        selection = select_capability(CapabilityKind.TYPE_TEXT.value, _device())
        assert selection.available
        assert selection.risk == "sensitive"
        assert selection.approval_requirement == "always"

    def test_an_undeclared_capability_is_refused_and_nothing_is_substituted(self):
        device = _device(capabilities=[CapabilityKind.FOCUS_WINDOW])
        selection = select_capability(CapabilityKind.TYPE_TEXT.value, device)
        assert not selection.available
        assert selection.refusal_code == NOT_DECLARED
        assert "does not substitute" in (selection.refusal_reason or "")

    def test_a_capability_outside_the_vocabulary_is_inexpressible(self):
        selection = select_capability("windows.run_command", _device())
        assert not selection.available
        assert selection.refusal_code == NOT_IN_VOCABULARY

    def test_a_version_this_build_does_not_implement_is_refused_not_downgraded(self):
        device = _device(versions={CapabilityKind.FOCUS_WINDOW: 7})
        selection = select_capability(CapabilityKind.FOCUS_WINDOW.value, device)
        assert not selection.available
        assert selection.refusal_code == VERSION_MISMATCH

    def test_no_device_is_refused_rather_than_assumed_capable(self):
        selection = select_capability(CapabilityKind.FOCUS_WINDOW.value, None)
        assert not selection.available
        assert selection.refusal_code == NOT_DECLARED

    def test_trusted_autonomy_is_reported_but_never_invented(self):
        device = _device(trusted_autonomy=[CapabilityKind.FOCUS_WINDOW])
        assert select_capability(CapabilityKind.FOCUS_WINDOW.value, device).device_autonomous
        assert not select_capability(CapabilityKind.LAUNCH_APP.value, device).device_autonomous

    def test_a_sensitive_capability_can_never_be_autonomous_at_the_device(self):
        """Not this module's rule --- the envelope's --- and it still holds here."""
        from bartholomew.actuation.devices import DeviceRegistryError

        with pytest.raises(DeviceRegistryError):
            _device(trusted_autonomy=[CapabilityKind.TYPE_TEXT])
