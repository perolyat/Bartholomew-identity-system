"""AUDIT-EXEC-1: three ways the Executive told itself something it had not established.

Each class below is one finding, and each is written to the repository's
non-vacuity rule: a test that only pins one bad string proves that string was
caught, not that the property holds. So every class carries a *forbidden-state*
test alongside the happy path, and several carry the pre-repair input verbatim
so that a regression restores a failure rather than merely a gap in coverage.

The three findings share a shape, which is worth naming once because it is what
the repair is aimed at and not at the three symptoms:

    **The Executive treated "I did not recognise this" as "this is fine."**

C06 let an identifier skip the contract every other model-authored string
obeyed, because the contract was a convention rather than something a test
could interrogate. C07 read confidence through a blocklist, so a word nobody had
enumerated bought more authority than the word `"low"` did. C08 printed a
sentence about approval that was decided when the sentence was written rather
than by the state it described. None of them is a filter that missed a case;
all of them are a default that pointed the wrong way.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

from bartholomew.actuation import devices
from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import ALL_CAPABILITIES, ApprovalRequirement, CapabilityKind
from bartholomew.executive import store
from bartholomew.executive.deliberation import (
    LOW_CONFIDENCE,
    MAX_IDENTIFIER_CHARS,
    SUFFICIENT_CONFIDENCE,
    ConfidenceState,
    DeliberationRecord,
    classify_confidence,
    deliberate_task,
    is_storable_text,
    parse_deliberation,
)
from bartholomew.executive.explanation import (
    _runs_without_further_approval,  # noqa: PLC2701 - the predicate under test
    explain_task,
    explanation_details,
)
from bartholomew.executive.plan import Plan, PlanStep, StepStatus, TaskStatus
from bartholomew.executive.selection import CapabilitySelection

GOAL = "Start a shopping list for me."

#: A lone UTF-16 surrogate. Valid in a Python `str`, valid in JSON input, and
#: **not encodable as UTF-8** --- so sqlite raises on it. This is the character
#: `deliberation._SURROGATES` was introduced for; C06 is that character arriving
#: through the one field the strip was not fitted to.
SURROGATE = "\ud800"


def _device(capabilities=None, autonomy=()):
    kinds = capabilities if capabilities is not None else ALL_CAPABILITIES
    return devices.EnrolledDevice(
        device_id="desk-pc",
        tenant_id="tenant-a",
        platform="windows",
        enrolled=True,
        capabilities=tuple(devices.DeclaredCapability(kind=k, version=1) for k in kinds),
        applications=ApplicationAllowlist.from_pairs(
            {"notepad": "C:\\Windows\\System32\\notepad.exe"},
        ),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["C:\\Users\\t\\Documents"]),
        trusted_autonomy=frozenset(autonomy),
    )


class _Port:
    def __init__(self, payload):
        self.payload = payload

    def deliberate(self, prompt: str) -> str:
        return self.payload


def _answer(steps, **overrides):
    payload = {
        "objective": "an editable note holds the list",
        "situation": "nothing is open",
        "sub_goals": ["a surface exists"],
        "steps": list(steps),
        "clarification": None,
        "refusal": None,
        "confidence": "high",
    }
    payload.update(overrides)
    return json.dumps(payload)


LAUNCH = {
    "capability": "windows.launch_app",
    "parameters": {"app_id": "notepad"},
    "purpose": "start an editor",
    "necessary_because": "there is nowhere to write",
}


def _deliberated(payload, *, device=None, record=None):
    return deliberate_task(
        GOAL,
        device=device if device is not None else _device(),
        port=_Port(payload),
        record=record,
    )


def _asked(intent) -> bool:
    """The safe non-outcome: a question was raised and nothing was proposed."""
    return not intent.actionable and bool(intent.ambiguities or intent.unsupported)


def _every_string(value):
    """Every string anywhere inside `value`, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _every_string(key)
            yield from _every_string(item)
    elif isinstance(value, list):
        for item in value:
            yield from _every_string(item)


# ---------------------------------------------------------------------------
# C06 --- the capability identifier skipped the storable-text contract
# ---------------------------------------------------------------------------


class TestC06CapabilityIdentifiersAreStorableText:
    """A model-authored identifier is model-authored text, and is bounded and storable.

    The bypass was not that identifiers were unchecked against the *vocabulary*
    --- `CapabilityKind(...)` has always refused an invented one. It was that
    the raw string survived long enough to be written down. `Deliberation.as_dict`
    and `DeliberationRecord.rejected_steps` both carry it unescaped into the
    plan's `deliberation` record, `explanation_details` carries that into the
    `ActionReflection` this package audits itself through, and a lone surrogate
    there raises `UnicodeEncodeError` out of sqlite --- losing the record of why
    the executive refused, which is the one thing a refusal is for.

    Verified against `origin/main` at `9d1d61e`: the surrogate survives the
    parse, `Deliberation.as_dict()` and `DeliberationRecord.as_dict()` are both
    un-encodable as UTF-8 and both are rejected by sqlite, and a 5000-character
    identifier is carried at full length.
    """

    def test_a_surrogate_in_an_identifier_does_not_survive_the_parse(self):
        parsed = parse_deliberation(
            json.dumps(
                {"steps": [{"capability": f"windows.{SURROGATE}launch_app", "parameters": {}}]},
            ),
        )
        assert parsed is not None
        assert SURROGATE not in parsed.steps[0].capability

    def test_an_unbounded_identifier_is_bounded(self):
        parsed = parse_deliberation(
            json.dumps({"steps": [{"capability": "x" * 5000, "parameters": {}}]}),
        )
        assert parsed is not None
        assert len(parsed.steps[0].capability) <= MAX_IDENTIFIER_CHARS

    @pytest.mark.parametrize(
        "identifier",
        [
            f"windows.launch_app{SURROGATE}",
            "windows.launch_app\r\n\tinjected",
            " " * 40 + "windows.launch_app" + " " * 40,
            "x" * 5000,
            "",
            "\ud800\udc00",
        ],
    )
    def test_every_identifier_shape_leaves_the_parse_storable(self, identifier):
        parsed = parse_deliberation(
            json.dumps({"steps": [{"capability": identifier, "parameters": {}}]}),
        )
        assert parsed is not None
        assert is_storable_text(parsed.steps[0].capability, maximum=MAX_IDENTIFIER_CHARS)

    def test_the_whole_persisted_reading_is_storable_at_every_depth(self):
        """The invariant asserted over the structure, not field by field.

        This is the test that would have caught C06 when it was written, and it
        is the reason the contract now exists as a predicate: `as_dict` is the
        boundary at which a reading becomes durable, and nothing that leaves it
        may need a second sanitising pass.
        """
        parsed = parse_deliberation(
            json.dumps(
                {
                    "objective": f"a note{SURROGATE} holds it",
                    "situation": SURROGATE * 3,
                    "sub_goals": [f"surface{SURROGATE}"],
                    "clarification": f"which one{SURROGATE}?",
                    "refusal": None,
                    "confidence": f"high{SURROGATE}",
                    "steps": [
                        {
                            "capability": f"windows.launch_app{SURROGATE}",
                            "parameters": {f"app{SURROGATE}_id": "notepad"},
                            "purpose": f"start{SURROGATE}",
                            "necessary_because": f"because{SURROGATE}",
                        },
                    ],
                },
            ),
        )
        assert parsed is not None
        rendered = parsed.as_dict()
        for text in _every_string(rendered):
            assert is_storable_text(text, maximum=500), repr(text)
        # And the whole thing really is encodable, which is what "storable" is for.
        json.dumps(rendered, ensure_ascii=False).encode("utf-8")

    def test_the_audit_record_of_a_bad_identifier_reaches_the_database(self, tmp_path):
        """End to end, at the boundary that actually broke.

        The deliberation record is what `explanation_details` puts in the
        Reflection, and on `main` this exact value cannot be written: sqlite
        refuses the surrogate. The assertion is not that the text is pretty; it
        is that the executive can write down why it refused.
        """
        record = DeliberationRecord()
        intent = _deliberated(
            _answer([{"capability": f"windows.launch_app{SURROGATE}", "parameters": {}}]),
            record=record,
        )
        assert _asked(intent), "an identifier that is not in the vocabulary must be refused"

        plan = Plan(
            task_id="t-c06",
            tenant_id="tenant-a",
            device_id="desk-pc",
            requested_by="taylor",
            instruction=GOAL,
            status=TaskStatus.AWAITING_CLARIFICATION,
            clarification=intent.ambiguities[0].question,
            deliberation=record.as_dict(),
        )
        blob = json.dumps(explanation_details(plan), ensure_ascii=False)
        assert SURROGATE not in blob
        blob.encode("utf-8")

        db = str(tmp_path / "exec.db")
        store.ensure_schema(db)
        store.save_plan(db, plan)
        # `closing`, not a bare `with`: a `with` on a sqlite3 connection commits
        # the transaction and leaves the connection **open**. On Windows the
        # surviving handle makes the database file undeletable, which is how
        # this test first failed there while passing on Linux.
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("CREATE TABLE audit(details TEXT)")
            # The shape `ActionReflection` writes: the details blob, as text.
            conn.execute("INSERT INTO audit VALUES (?)", (blob,))
            (stored,) = conn.execute(
                "SELECT clarification FROM executive_tasks WHERE task_id = ?",
                ("t-c06",),
            ).fetchone()
        assert stored
        assert SURROGATE not in stored

    def test_the_audit_record_of_a_rejected_step_is_storable(self):
        record = DeliberationRecord()
        _deliberated(
            _answer([{"capability": "x" * 4000 + SURROGATE, "parameters": {}}]),
            record=record,
        )
        for text in _every_string(record.as_dict()):
            assert is_storable_text(text, maximum=500), repr(text)

    def test_sanitisation_cannot_manufacture_a_valid_capability(self):
        """The forbidden state, **isolated** so only the property can explain it.

        This is the AUDIT-EXEC-1 pre-merge review's Finding 3, and the previous
        version of this test did not prove it: it used a device that declared
        no capabilities, so the refusal it observed came from enrolment and
        would have been identical had the bypass been wide open.

        Here every unrelated gate is satisfied on purpose --- the device
        declares `windows.launch_app`, the parameters are the real allowlisted
        ones, `launch_app` is in `INFERABLE_CAPABILITIES`, and the plan is one
        step --- so the *only* thing that can refuse this is the property under
        test. The model's identifier is `"windows.\ud800launch_app"`, which no
        vocabulary contains; removing the lone surrogate to make it storable
        produces exactly `"windows.launch_app"`, which the vocabulary does
        contain. Reproduced on head `8f17c07`: it proposed a governed
        `windows.launch_app` step.
        """
        injected = dict(LAUNCH)
        injected["capability"] = f"windows.{SURROGATE}launch_app"
        intent = _deliberated(_answer([injected]))

        assert _asked(intent), "a repaired identifier must not become an action"
        assert not intent.steps

    def test_the_control_proves_every_other_gate_would_have_passed(self):
        """Non-vacuity for the isolation itself.

        The only difference between this and the test above is the surrogate.
        If this one did not produce a step, the test above would prove nothing,
        because some unrelated condition would be doing the refusing.
        """
        intent = _deliberated(_answer([dict(LAUNCH)]))
        assert intent.actionable
        assert [s.capability for s in intent.steps] == ["windows.launch_app"]

    @pytest.mark.parametrize(
        "identifier",
        [
            f"windows.launch_app{SURROGATE}",
            f"{SURROGATE}windows.launch_app",
            "windows.launch_app ",
            " windows.launch_app",
            "windows.launch\r\n_app",
            "windows.launch_app" + "x" * 5000,
        ],
    )
    def test_no_repaired_identifier_is_actionable_however_it_cleans_up(self, identifier):
        """The property over shapes, not one string: repair is refused, full stop.

        Truncation counts too --- an identifier bounded down to something valid
        was still not what the model wrote.
        """
        injected = dict(LAUNCH)
        injected["capability"] = identifier
        assert _asked(_deliberated(_answer([injected])))

    def test_a_repaired_identifier_is_refused_before_the_vocabulary_is_consulted(self):
        """The ordering, which is where the guarantee actually lives.

        Storability is decided first and meaning second, so the repair can
        never supply the meaning. Asserted on the parse result rather than on
        the refusal text: `capability_repaired` is the fact, and it is set for
        an identifier that cleans into a real capability exactly as it is for
        one that cleans into nonsense.
        """
        parsed = parse_deliberation(
            json.dumps(
                {
                    "steps": [
                        {
                            "capability": f"windows.{SURROGATE}launch_app",
                            "parameters": {"app_id": "notepad"},
                        },
                    ],
                },
            ),
        )
        assert parsed is not None
        step = parsed.steps[0]
        # Storable, and it now reads as a real capability --- which is precisely
        # why the flag, and not the text, has to be what decides.
        assert step.capability == "windows.launch_app"
        assert step.capability in {k.value for k in CapabilityKind}
        assert step.capability_repaired is True

    def test_the_repair_flag_reaches_the_audit_record(self):
        """Provenance: an auditor can see why a step naming a real capability was refused."""
        parsed = parse_deliberation(
            json.dumps(
                {"steps": [{"capability": f"windows.{SURROGATE}launch_app", "parameters": {}}]},
            ),
        )
        assert parsed is not None
        rendered = parsed.as_dict()
        assert rendered["steps"][0]["capability_repaired"] is True
        # Still storable at every depth --- the C06 guarantee is not weakened.
        for text in _every_string(rendered):
            assert is_storable_text(text, maximum=500), repr(text)

    def test_a_clean_identifier_is_not_flagged_as_repaired(self):
        """Non-vacuity: the flag must mean something, not be always-on."""
        parsed = parse_deliberation(json.dumps({"steps": [LAUNCH]}))
        assert parsed is not None
        assert parsed.steps[0].capability_repaired is False

    def test_non_vacuity_a_clean_identifier_is_untouched(self):
        parsed = parse_deliberation(json.dumps({"steps": [LAUNCH]}))
        assert parsed is not None
        assert parsed.steps[0].capability == "windows.launch_app"

    def test_non_vacuity_a_clean_plan_still_plans(self):
        intent = _deliberated(_answer([LAUNCH]))
        assert intent.actionable
        assert [s.capability for s in intent.steps] == ["windows.launch_app"]


# ---------------------------------------------------------------------------
# C07 --- unknown confidence failed open
# ---------------------------------------------------------------------------


class TestC07UnknownConfidenceFailsCautious:
    """Confidence is read through an allowlist of confident states, not a blocklist.

    The pre-repair implementation asked `confidence in LOW_CONFIDENCE` against
    four words. Every other string a model can produce --- including every
    natural way of expressing doubt that nobody happened to enumerate --- was
    therefore treated exactly as `"high"` was. The unrecognised values below are
    the evidence: each one proposed a governed action on `main`.
    """

    #: Ways of saying "I am not sure" that no hand-written list contained.
    UNRECOGNISED_DOUBT = [
        "very low",
        "uncertain",
        "not sure",
        "no idea",
        "tentative",
        "best guess",
        "LOW-ish",
        "0.1",
        "\N{SHRUG}",
    ]

    #: Values that are not a claim about confidence at all.
    MALFORMED = ["", "   ", "\t\n", SURROGATE, "!!!", "high low", "null"]

    @pytest.mark.parametrize("value", UNRECOGNISED_DOUBT)
    def test_unrecognised_doubt_is_cautious(self, value):
        assert classify_confidence(value) is ConfidenceState.CAUTIOUS

    @pytest.mark.parametrize("value", MALFORMED)
    def test_malformed_or_empty_is_cautious(self, value):
        assert classify_confidence(value) is ConfidenceState.CAUTIOUS

    @pytest.mark.parametrize("value", [0.01, 0.99, 1, True, False, [], {}, ["high"], {"c": "high"}])
    def test_non_strings_are_cautious(self, value):
        """Including `0.99`. A number is not a recognised state, however large."""
        assert classify_confidence(value) is ConfidenceState.CAUTIOUS

    @pytest.mark.parametrize("value", sorted(LOW_CONFIDENCE))
    def test_the_recognised_low_vocabulary_is_still_cautious(self, value):
        assert classify_confidence(value) is ConfidenceState.CAUTIOUS

    @pytest.mark.parametrize("value", sorted(SUFFICIENT_CONFIDENCE))
    def test_the_recognised_confident_vocabulary_proceeds(self, value):
        assert classify_confidence(value) is ConfidenceState.SUFFICIENT
        assert classify_confidence(value.upper()) is ConfidenceState.SUFFICIENT
        assert classify_confidence(f"  {value} ") is ConfidenceState.SUFFICIENT

    def test_absent_is_classified_as_unstated_and_is_not_a_licence(self):
        """`UNSTATED` is a classification, not a permission. See the gate tests."""
        assert classify_confidence(None) is ConfidenceState.UNSTATED
        parsed = parse_deliberation(json.dumps({"steps": []}))
        assert parsed is not None
        assert parsed.confidence_state is ConfidenceState.UNSTATED

    @pytest.mark.parametrize("value", [*UNRECOGNISED_DOUBT, *MALFORMED, 0.01, 0.99, True, []])
    def test_an_unrecognised_confidence_proposes_nothing(self, value):
        """The property, at the level that matters: no action is proposed."""
        intent = _deliberated(_answer([LAUNCH], confidence=value))
        assert _asked(intent), f"{value!r} produced a proposal"
        assert not intent.steps

    @pytest.mark.parametrize("value", sorted(LOW_CONFIDENCE))
    def test_recognised_low_confidence_still_proposes_nothing(self, value):
        intent = _deliberated(_answer([LAUNCH], confidence=value))
        assert _asked(intent)

    @pytest.mark.parametrize("value", sorted(SUFFICIENT_CONFIDENCE))
    def test_recognised_confidence_proposes_normally(self, value):
        """Non-vacuity. The repair must not have made everything a question."""
        intent = _deliberated(_answer([LAUNCH], confidence=value))
        assert intent.actionable
        assert [s.capability for s in intent.steps] == ["windows.launch_app"]

    def test_an_omitted_confidence_proposes_nothing(self):
        """Forbidden state: omission must not be a way round the allowlist.

        **Amended by the AUDIT-EXEC-1 pre-merge review**, which found this
        asserting the opposite. An allowlist that a model can skip by leaving
        the field out is not an allowlist; `build_prompt` asks for `confidence`
        explicitly, so an answer without it did not follow the contract, and
        the contract is the only reason the recognised values mean anything.
        Reproduced on head `8f17c07`: this produced a governed proposal with a
        real `windows.launch_app` step.
        """
        payload = json.loads(_answer([LAUNCH]))
        del payload["confidence"]
        intent = _deliberated(json.dumps(payload))
        assert _asked(intent)
        assert not intent.steps

    def test_an_explicit_null_confidence_proposes_nothing(self):
        """The JSON spelling of the same omission, and the same forbidden state."""
        intent = _deliberated(_answer([LAUNCH], confidence=None))
        assert _asked(intent)
        assert not intent.steps

    def test_only_a_recognised_sufficient_value_satisfies_the_gate(self):
        """The invariant as one statement over every state the classifier has.

        Written against `ConfidenceState` itself rather than a list of inputs,
        so a fourth state added tomorrow has to be classified deliberately
        instead of inheriting a pass. This is the test that fails if the gate
        is ever rewritten back into an exclusion.
        """
        proceeds = {state: state is ConfidenceState.SUFFICIENT for state in ConfidenceState}
        assert proceeds == {
            ConfidenceState.SUFFICIENT: True,
            ConfidenceState.UNSTATED: False,
            ConfidenceState.CAUTIOUS: False,
        }
        # And end to end, one representative input per state.
        omitted = json.loads(_answer([LAUNCH]))
        del omitted["confidence"]
        assert _deliberated(json.dumps(omitted)).actionable is False
        assert _deliberated(_answer([LAUNCH], confidence="zorp")).actionable is False
        assert _deliberated(_answer([LAUNCH], confidence="high")).actionable is True

    def test_unstated_is_still_told_apart_from_cautious_in_the_question(self):
        """`UNSTATED` stays a distinct audit classification; it just buys nothing.

        Both refuse. The person is told the true reason rather than a single
        flattened one, which is the same truthfulness rule C08 is about.
        """
        omitted = json.loads(_answer([LAUNCH]))
        del omitted["confidence"]
        silent = _deliberated(json.dumps(omitted))
        spoke = _deliberated(_answer([LAUNCH], confidence="low"))
        assert _asked(silent) and _asked(spoke)
        assert silent.ambiguities[0].question != spoke.ambiguities[0].question

    def test_no_unrecognised_value_outranks_a_recognised_cautious_one(self):
        """The forbidden state, stated as the ordering it violates.

        C07 in one assertion: on `main`, `"uncertain"` was treated better than
        `"low"`. Nothing unrecognised may ever be treated better than the most
        cautious recognised state.
        """
        cautious_outcome = _asked(_deliberated(_answer([LAUNCH], confidence="low")))
        for value in [*self.UNRECOGNISED_DOUBT, *self.MALFORMED]:
            assert _asked(_deliberated(_answer([LAUNCH], confidence=value))) == cautious_outcome

    def test_the_model_word_is_recorded_faithfully_beside_the_verdict(self):
        """The audit keeps what was said; the gate acts on what it is worth."""
        parsed = parse_deliberation(json.dumps({"confidence": "pretty sure", "steps": []}))
        assert parsed is not None
        assert parsed.confidence == "pretty sure"
        assert parsed.confidence_state is ConfidenceState.CAUTIOUS
        assert parsed.as_dict()["confidence_state"] == "cautious"

    def test_a_numeric_confidence_is_not_rewritten_as_a_word_nobody_said(self):
        parsed = parse_deliberation(json.dumps({"confidence": 0.01, "steps": []}))
        assert parsed is not None
        assert parsed.confidence == "0.01"
        assert parsed.confidence_state is ConfidenceState.CAUTIOUS

    def test_confidence_cannot_grant_anything(self):
        """Confidence only ever makes the executive more careful.

        A device that declares nothing refuses the step regardless of how
        certain the model claims to be. Confidence is not consulted in
        validation, and this is the assertion that says so.
        """
        for value in ["high", "medium", "absolutely certain", 1.0]:
            intent = _deliberated(
                _answer([LAUNCH], confidence=value),
                device=_device(capabilities=()),
            )
            assert _asked(intent)


# ---------------------------------------------------------------------------
# C08 --- the account made a blanket approval claim
# ---------------------------------------------------------------------------


def _selection(*, autonomous=False, approval=ApprovalRequirement.REQUIRED_AUTONOMY_ELIGIBLE):
    return CapabilitySelection(
        capability="windows.launch_app",
        available=True,
        version=1,
        risk="medium",
        approval_requirement=approval.value,
        device_autonomous=autonomous,
    )


def _step(
    index,
    status,
    *,
    autonomous=False,
    approval=ApprovalRequirement.REQUIRED_AUTONOMY_ELIGIBLE,
):
    return PlanStep(
        index=index,
        capability="windows.launch_app",
        parameters={"app_id": "notepad"},
        described_as=f"step {index}",
        selection=_selection(autonomous=autonomous, approval=approval),
        status=status,
        action_id=f"act-{index}",
    )


def _deliberated_plan(steps, status=TaskStatus.IN_PROGRESS):
    return Plan(
        task_id="t-c08",
        tenant_id="tenant-a",
        device_id="desk-pc",
        requested_by="taylor",
        instruction=GOAL,
        status=status,
        steps=list(steps),
        deliberation={
            "used": True,
            "deliberation": {"objective": "an editable note holds the list", "sub_goals": []},
        },
    )


#: The sentence the account used to print on every deliberated plan, regardless
#: of what the steps said. Kept verbatim so a regression restores a failure, and
#: asserted absent from *every* case below --- including the one it happened to
#: be right about, because a constant that is sometimes true is still a constant.
BLANKET_CLAIM = "every one of them still needs your approval"


class TestC08ApprovalTextFollowsGovernanceState:
    """What the account says about approval is read off the plan, not off a constant."""

    def test_a_plan_whose_steps_all_await_approval_says_so(self):
        """Non-vacuity, and the case the blanket claim was right about.

        It is still said --- derived rather than asserted, but said --- because
        a repair that made the account stop mentioning approval would close the
        finding by concealing the requirement, which is the other half of what
        C08 forbids.
        """
        text = explain_task(
            _deliberated_plan(
                [_step(0, StepStatus.AWAITING_AUTHORIZATION), _step(1, StepStatus.PLANNED)],
            ),
        )
        assert "all of them still need your approval" in text
        assert "Waiting on you: action act-0" in text

    def test_the_blanket_claim_is_gone_from_every_governance_state(self):
        """The constant itself, as a forbidden state, across the whole matrix."""
        states = [
            StepStatus.PLANNED,
            StepStatus.AWAITING_AUTHORIZATION,
            StepStatus.DISPATCHED,
            StepStatus.VERIFIED,
            StepStatus.FAILED,
            StepStatus.UNKNOWN,
            StepStatus.BLOCKED,
        ]
        for status in states:
            for autonomous in (True, False):
                text = explain_task(
                    _deliberated_plan([_step(0, status, autonomous=autonomous)]),
                )
                assert BLANKET_CLAIM not in text, (status, autonomous)

    def test_a_completed_plan_does_not_claim_approval_is_still_needed(self):
        text = explain_task(
            _deliberated_plan(
                [_step(0, StepStatus.VERIFIED), _step(1, StepStatus.VERIFIED)],
                status=TaskStatus.COMPLETED,
            ),
        )
        assert BLANKET_CLAIM not in text
        assert "still needs your approval" not in text
        assert "already been sent to your computer" in text

    def test_a_trusted_autonomy_plan_does_not_claim_approval_is_needed(self):
        text = explain_task(
            _deliberated_plan([_step(0, StepStatus.AWAITING_AUTHORIZATION, autonomous=True)]),
        )
        assert BLANKET_CLAIM not in text
        assert "still needs your approval" not in text
        assert "without asking you again" in text
        assert "Waiting on you" not in text

    def test_a_mixed_plan_tells_the_truth_about_each_part(self):
        text = explain_task(
            _deliberated_plan(
                [
                    _step(0, StepStatus.VERIFIED),
                    _step(1, StepStatus.AWAITING_AUTHORIZATION, autonomous=True),
                    _step(2, StepStatus.AWAITING_AUTHORIZATION),
                    _step(3, StepStatus.BLOCKED),
                ],
            ),
        )
        assert BLANKET_CLAIM not in text
        assert "one of them still needs your approval" in text
        assert "without asking you again" in text
        assert "already been sent to your computer" in text
        assert "never proposed at all" in text
        # And the one genuinely pending action, and only it, is named.
        assert "action act-2" in text
        assert "Waiting on you: action act-2." in text

    @pytest.mark.parametrize(
        "approval",
        [ApprovalRequirement.ALWAYS, ApprovalRequirement.REQUIRED],
    )
    def test_a_genuine_approval_requirement_is_never_concealed(self, approval):
        """Both ineligible requirements, and the reason it is an allowlist.

        `ApprovalRequirement` has three values and exactly one of them,
        `REQUIRED_AUTONOMY_ELIGIBLE`, is a kind an enrolment may be granted
        autonomy over. `ALWAYS` is "never eligible at any configuration";
        `REQUIRED` is "an approval is required, and this build offers no
        autonomy path for it".

        **Amended by the AUDIT-EXEC-1 pre-merge review**, which found the
        predicate written as `!= ALWAYS`. That is not the same test: it missed
        `REQUIRED` entirely, so a step whose capability has no autonomy path in
        this build at all was described to the person as running unattended.
        Reproduced on head `8f17c07` for `REQUIRED`. An exclusion would also
        silently admit whatever value is added to the enum next.

        `EnrolledDevice.__post_init__` refuses ineligible trusted autonomy at
        construction, so a real enrolled device cannot produce this selection ---
        but `select_capability` accepts "anything with the same `declares` /
        `declared_version` / `autonomous_for` surface", and `autonomous_for`
        alone does not consult the descriptor. The claim must not be made
        whatever produced the selection.
        """
        step = _step(
            0,
            StepStatus.AWAITING_AUTHORIZATION,
            autonomous=True,
            approval=approval,
        )
        assert _runs_without_further_approval(step) is False

        text = explain_task(_deliberated_plan([step]))
        assert "without asking you again" not in text
        assert "eligible to run without a further approval" not in text
        assert "trusted autonomy" not in text
        # And the requirement is stated, not merely left unmentioned.
        assert "still needs your approval" in text
        assert "Waiting on you: action act-0" in text

    def test_the_only_eligible_requirement_is_described_as_autonomous(self):
        """Non-vacuity for the allowlist: the one eligible value still reads as eligible."""
        step = _step(
            0,
            StepStatus.AWAITING_AUTHORIZATION,
            autonomous=True,
            approval=ApprovalRequirement.REQUIRED_AUTONOMY_ELIGIBLE,
        )
        assert _runs_without_further_approval(step) is True
        text = explain_task(_deliberated_plan([step]))
        assert "trusted autonomy" in text
        assert "without asking you again" in text
        assert "Waiting on you" not in text

    def test_autonomy_requires_the_device_grant_as_well_as_eligibility(self):
        """The other half of the conjunction: eligibility alone is not autonomy."""
        step = _step(
            0,
            StepStatus.AWAITING_AUTHORIZATION,
            autonomous=False,
            approval=ApprovalRequirement.REQUIRED_AUTONOMY_ELIGIBLE,
        )
        assert _runs_without_further_approval(step) is False
        assert "still needs your approval" in explain_task(_deliberated_plan([step]))

    def test_every_approval_requirement_is_classified_deliberately(self):
        """No value of the enum inherits an autonomy claim by not being excluded."""
        eligible = {
            approval: _runs_without_further_approval(
                _step(0, StepStatus.AWAITING_AUTHORIZATION, autonomous=True, approval=approval),
            )
            for approval in ApprovalRequirement
        }
        assert eligible == {
            ApprovalRequirement.ALWAYS: False,
            ApprovalRequirement.REQUIRED: False,
            ApprovalRequirement.REQUIRED_AUTONOMY_ELIGIBLE: True,
        }

    @pytest.mark.parametrize(
        "status",
        [StepStatus.VERIFIED, StepStatus.FAILED, StepStatus.UNKNOWN, StepStatus.DISPATCHED],
    )
    def test_a_step_that_reached_the_machine_is_never_called_pending(self, status):
        text = explain_task(_deliberated_plan([_step(0, status)]))
        assert "still needs your approval" not in text
        assert "Waiting on you" not in text

    def test_the_account_and_the_step_lines_cannot_disagree(self):
        """The contradiction itself, as a forbidden state.

        Pre-repair, a trusted-autonomy plan printed both "every one of them
        still needs your approval" and "eligible to run without a further
        approval" in the same account. Whatever the wording becomes, the two
        halves must not say opposite things.
        """
        for autonomous in (True, False):
            text = explain_task(
                _deliberated_plan(
                    [_step(0, StepStatus.AWAITING_AUTHORIZATION, autonomous=autonomous)],
                ),
            )
            claims_autonomy = "eligible to run without a further approval" in text
            claims_approval = "still needs your approval" in text
            assert claims_autonomy != claims_approval, text

    def test_the_task_line_is_not_a_blanket_claim_either(self):
        """The same defect one level up, in `_TASK_PHRASES[IN_PROGRESS]`."""
        autonomous = explain_task(
            _deliberated_plan([_step(0, StepStatus.AWAITING_AUTHORIZATION, autonomous=True)]),
        )
        assert "Nothing runs without your approval." not in autonomous
        pending = explain_task(_deliberated_plan([_step(0, StepStatus.AWAITING_AUTHORIZATION)]))
        assert "Nothing that is left runs without your approval." in pending

    def test_a_recognised_plan_gets_no_deliberation_account_at_all(self):
        """Unchanged behaviour: this section is only for steps nobody named."""
        plan = _deliberated_plan([_step(0, StepStatus.AWAITING_AUTHORIZATION)])
        plan.deliberation = None
        text = explain_task(plan)
        assert "I worked the steps out" not in text
        assert "Of these steps," not in text
        # The step-level truth is still told.
        assert "Waiting on you: action act-0" in text

    def test_the_reasoning_section_still_says_the_steps_were_inferred(self):
        text = explain_task(_deliberated_plan([_step(0, StepStatus.AWAITING_AUTHORIZATION)]))
        assert "Nothing here is something you named." in text

    def test_the_structured_details_are_unchanged_in_shape(self):
        details = explanation_details(
            _deliberated_plan([_step(0, StepStatus.AWAITING_AUTHORIZATION)]),
        )
        assert details["steps"][0]["capability"] == "windows.launch_app"
        assert details["status"] == TaskStatus.IN_PROGRESS.value


class TestC08AgainstARealDeliberatedPlan:
    """The same property through the real builder, not a hand-assembled plan."""

    def _plan(self, autonomy=()):
        from bartholomew.executive.plan import build_plan

        record = DeliberationRecord()
        intent = _deliberated(
            _answer([LAUNCH]),
            device=_device(autonomy=autonomy),
            record=record,
        )
        assert intent.actionable
        return build_plan(
            intent=intent,
            tenant_id="tenant-a",
            device_id="desk-pc",
            requested_by="taylor",
            device=_device(autonomy=autonomy),
            deliberation=record.as_dict(),
        )

    def test_an_ordinary_device_is_told_approval_is_needed(self):
        assert "still needs your approval" in explain_task(self._plan())

    def test_a_device_with_trusted_autonomy_is_not_told_a_falsehood(self):
        autonomy = (CapabilityKind.LAUNCH_APP,)
        if CapabilityKind.LAUNCH_APP not in devices.TRUSTED_AUTONOMY_ELIGIBLE:
            pytest.skip("launch_app is not autonomy-eligible in this build")
        text = explain_task(self._plan(autonomy=autonomy))
        assert BLANKET_CLAIM not in text
        assert "without asking you again" in text
