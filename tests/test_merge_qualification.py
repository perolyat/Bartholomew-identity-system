"""Regression coverage for the deterministic merge-qualification gate.

AUDIT-CTRL-1. The audit found four pull requests whose merge was allowed by a
qualification step that had not established what it claimed: a required tier
that never ran (#108), a required tier that ran and failed on the exact merged
head (#101), substantive findings left open and repaired only afterwards
(#91), and a qualification that reported zero unresolved threads while a
substantive finding was extant (#120).

These tests exist to make that class of false positive hard to recreate. Each
scenario asserts the *reason* the gate refused — the reason code, the outcome
of each required check, and the classification of each finding — not merely
that a boolean came out false. A refactor that returns `not_ready` for the
wrong reason fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.ci.merge_qualification.config import (
    ControlStateError,
    load_config,
    load_dispositions,
)
from scripts.ci.merge_qualification.evaluate import (
    classify_check_run,
    evaluate,
    verify_evidence_applies_to,
)
from scripts.ci.merge_qualification.model import (
    NON_BLOCKING_CLASSIFICATIONS,
    CheckObservation,
    CheckOutcome,
    DispositionRecord,
    FindingClassification,
    QualificationInput,
    ReasonCode,
    RequiredCheck,
    ReviewFinding,
    Verdict,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = Path(__file__).parent / "fixtures" / "merge_qualification"
CONFIG_FILE = REPO_ROOT / ".github" / "merge-qualification" / "config.yml"
DISPOSITIONS_FILE = REPO_ROOT / ".github" / "merge-qualification" / "dispositions.yml"

HEAD = "a" * 40
OLDER = "b" * 40
NEWER = "c" * 40


def _input_from(payload: dict) -> QualificationInput:
    return QualificationInput(
        repo=payload["repo"],
        pr_number=payload["pr_number"],
        head_sha=payload["head_sha"],
        pr_commits=tuple(payload.get("pr_commits", ())),
        required_checks=tuple(
            RequiredCheck(entry["workflow"], entry["job"])
            for entry in payload.get("required_checks", ())
        ),
        checks=tuple(
            CheckObservation(
                workflow=entry["workflow"],
                job=entry["job"],
                head_sha=entry.get("head_sha"),
                status=entry.get("status"),
                conclusion=entry.get("conclusion"),
            )
            for entry in payload.get("checks", ())
        ),
        findings=tuple(
            ReviewFinding(
                finding_id=entry["finding_id"],
                author=entry["author"],
                commit_sha=entry.get("commit_sha"),
                thread_resolved=entry.get("thread_resolved"),
                excerpt=entry.get("excerpt", ""),
            )
            for entry in payload.get("findings", ())
        ),
        dispositions=tuple(
            DispositionRecord(
                finding_id=entry["finding_id"],
                classification=FindingClassification(entry["classification"]),
                rationale=entry["rationale"],
                recorded_by=entry["recorded_by"],
                resolved_by_commit=entry.get("resolved_by_commit"),
            )
            for entry in payload.get("dispositions", ())
        ),
        check_state_determined=payload.get("check_state_determined", True),
        review_state_determined=payload.get("review_state_determined", True),
    )


SCENARIOS = sorted(SCENARIO_DIR.glob("*.json"))


def test_the_audit_scenarios_are_all_present():
    """The four audited pull requests each keep a fixture of their own.

    Deleting one of these is how the regression quietly comes back, so the
    set is asserted rather than merely iterated.
    """
    names = {path.name for path in SCENARIOS}
    for required in (
        "pr_108_required_tier_never_ran.json",
        "pr_101_required_tier_failing_on_exact_head.json",
        "pr_91_findings_repaired_only_in_a_later_pr.json",
        "pr_120_forge_resolution_is_not_a_disposition.json",
        "green_only_for_an_older_head.json",
        "required_checks_incomplete_and_indeterminate.json",
        "state_could_not_be_determined.json",
        "qualified_head.json",
    ):
        assert required in names, f"missing regression fixture {required}"


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda path: path.stem)
def test_scenario(path: Path):
    scenario = json.loads(path.read_text(encoding="utf-8"))
    expected = scenario["expect"]
    report = evaluate(_input_from(scenario["input"]), generated_at="2026-09-21T00:00:00+00:00")

    assert report.verdict is Verdict(expected["verdict"]), scenario["description"]

    # The reason, not just the verdict: how many refusals of each kind.
    actual_codes: dict[str, int] = {}
    for reason in report.reasons:
        actual_codes[reason.code.value] = actual_codes.get(reason.code.value, 0) + 1
    assert actual_codes == expected["reason_codes"]

    # The observed state of each named required check.
    by_check = {result.required.key: result.outcome.value for result in report.checks}
    for key, outcome in expected["check_outcomes"].items():
        assert by_check[key] == outcome, key

    # The truthful classification of each finding.
    by_finding = {
        result.finding.finding_id: result.classification.value for result in report.findings
    }
    assert by_finding == expected["finding_classifications"]

    # Every refusal is a refusal: nothing ready has reasons, nothing with
    # reasons is ready.
    assert report.ready == (not report.reasons)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda path: path.stem)
def test_evidence_is_bound_to_the_head_it_was_computed_for(path: Path):
    scenario = json.loads(path.read_text(encoding="utf-8"))
    report = evaluate(_input_from(scenario["input"]))
    evidence = report.to_dict()

    assert evidence["head_sha"] == scenario["input"]["head_sha"]

    applies, reason = verify_evidence_applies_to(evidence, scenario["input"]["head_sha"])
    assert applies and reason is None

    # A new commit lands. The evidence must stop applying, whatever it said.
    applies, reason = verify_evidence_applies_to(evidence, NEWER)
    assert not applies
    assert reason is not None
    assert reason.code is ReasonCode.EVIDENCE_HEAD_MISMATCH


# ---------------------------------------------------------------------------
# Forbidden states, asserted one at a time.
# ---------------------------------------------------------------------------

REQUIRED_ONE = (RequiredCheck("Merge Candidate", "smoke"),)


def _with_check(status: str | None, conclusion: str | None, sha: str = HEAD):
    return QualificationInput(
        repo="owner/name",
        pr_number=1,
        head_sha=HEAD,
        pr_commits=(OLDER, HEAD),
        required_checks=REQUIRED_ONE,
        checks=(CheckObservation("Merge Candidate", "smoke", sha, status, conclusion),),
    )


@pytest.mark.parametrize(
    ("status", "conclusion", "outcome"),
    [
        ("completed", "success", CheckOutcome.GREEN),
        ("completed", "failure", CheckOutcome.RED),
        ("completed", "timed_out", CheckOutcome.RED),
        ("completed", "action_required", CheckOutcome.RED),
        ("completed", "startup_failure", CheckOutcome.RED),
        ("completed", "cancelled", CheckOutcome.CANCELLED),
        ("completed", "skipped", CheckOutcome.SKIPPED),
        ("completed", "stale", CheckOutcome.STALE),
        ("completed", "neutral", CheckOutcome.UNKNOWN),
        ("completed", None, CheckOutcome.UNKNOWN),
        ("completed", "some_future_conclusion", CheckOutcome.UNKNOWN),
        ("in_progress", None, CheckOutcome.INCOMPLETE),
        ("queued", None, CheckOutcome.INCOMPLETE),
        ("waiting", None, CheckOutcome.INCOMPLETE),
    ],
)
def test_only_a_completed_success_is_green(status, conclusion, outcome):
    """`success` is the single green conclusion. Everything else refuses.

    `neutral` and any unrecognised conclusion are `UNKNOWN` on purpose: a
    forge that grows a new outcome must not grow a new way to pass.
    """
    assert classify_check_run(status, conclusion) is outcome

    report = evaluate(_with_check(status, conclusion))
    assert report.checks[0].outcome is outcome
    assert report.ready is (outcome is CheckOutcome.GREEN)


def test_a_required_check_that_never_ran_is_missing_not_absent_of_failures():
    """PR #108's shape, reduced to one check. Nothing red; still not ready."""
    report = evaluate(
        QualificationInput(
            repo="owner/name",
            pr_number=1,
            head_sha=HEAD,
            required_checks=REQUIRED_ONE,
            checks=(),
        ),
    )
    assert report.checks[0].outcome is CheckOutcome.MISSING
    assert report.reason_codes == (ReasonCode.REQUIRED_CHECK_NOT_PROVEN_GREEN,)
    assert not report.ready


def test_green_for_the_parent_commit_does_not_qualify_the_child():
    report = evaluate(_with_check("completed", "success", sha=OLDER))
    assert report.checks[0].outcome is CheckOutcome.STALE
    assert OLDER[:12] in report.checks[0].detail
    assert not report.ready


def test_a_red_run_is_not_cancelled_out_by_a_green_run_of_the_same_name():
    report = evaluate(
        QualificationInput(
            repo="owner/name",
            pr_number=1,
            head_sha=HEAD,
            required_checks=REQUIRED_ONE,
            checks=(
                CheckObservation("Merge Candidate", "smoke", HEAD, "completed", "success"),
                CheckObservation("Merge Candidate", "smoke", HEAD, "completed", "failure"),
            ),
        ),
    )
    assert report.checks[0].outcome is CheckOutcome.RED
    assert report.reason_codes == (ReasonCode.REQUIRED_CHECK_FAILING,)


def test_a_green_job_from_a_different_workflow_does_not_satisfy_the_tier():
    """Three workflows here publish a job called `smoke`. Only one counts."""
    report = evaluate(
        QualificationInput(
            repo="owner/name",
            pr_number=1,
            head_sha=HEAD,
            required_checks=REQUIRED_ONE,
            checks=(CheckObservation("CI", "smoke", HEAD, "completed", "success"),),
        ),
    )
    assert report.checks[0].outcome is CheckOutcome.MISSING
    assert not report.ready


def test_no_required_tier_configured_cannot_qualify_anything():
    report = evaluate(QualificationInput(repo="owner/name", pr_number=1, head_sha=HEAD))
    assert ReasonCode.NO_REQUIRED_TIERS_CONFIGURED in report.reason_codes
    assert not report.ready


@pytest.mark.parametrize("head", [None, "", "2813c7b", "zzzz" + "a" * 36, HEAD.upper() + "x"])
def test_an_inexact_head_cannot_be_qualified(head):
    report = evaluate(
        QualificationInput(
            repo="owner/name",
            pr_number=1,
            head_sha=head,
            required_checks=REQUIRED_ONE,
        ),
    )
    assert report.reason_codes == (ReasonCode.HEAD_SHA_UNKNOWN,)
    assert not report.ready


def _green_with_findings(findings, dispositions=()):
    return QualificationInput(
        repo="owner/name",
        pr_number=1,
        head_sha=HEAD,
        pr_commits=(OLDER, HEAD),
        required_checks=REQUIRED_ONE,
        checks=(CheckObservation("Merge Candidate", "smoke", HEAD, "completed", "success"),),
        findings=tuple(findings),
        dispositions=tuple(dispositions),
    )


def _finding(**kwargs):
    defaults = {
        "finding_id": "review_comment:1",
        "author": "codex-review",
        "commit_sha": HEAD,
        "thread_resolved": False,
        "excerpt": "This bypasses the allowlist.",
    }
    defaults.update(kwargs)
    return ReviewFinding(**defaults)


def test_an_open_finding_against_the_current_head_blocks_a_fully_green_head():
    report = evaluate(_green_with_findings([_finding()]))
    assert report.checks[0].outcome is CheckOutcome.GREEN
    assert report.findings[0].classification is FindingClassification.UNRESOLVED_SUBSTANTIVE
    assert report.reason_codes == (ReasonCode.UNRESOLVED_SUBSTANTIVE_FINDING,)


def test_forge_resolution_alone_is_recorded_but_does_not_dispose():
    """PR #120's shape. Resolution is a click, not evidence the code changed."""
    report = evaluate(_green_with_findings([_finding(thread_resolved=True)]))
    assert report.findings[0].finding.thread_resolved is True
    assert report.findings[0].classification is FindingClassification.UNKNOWN
    assert report.reason_codes == (ReasonCode.INDETERMINATE_FINDING,)


def test_forge_resolution_may_be_accepted_only_by_explicit_repository_policy():
    report = evaluate(
        _green_with_findings([_finding(thread_resolved=True)]),
        github_resolution_satisfies_disposition=True,
    )
    assert report.findings[0].classification is FindingClassification.RESOLVED_BY_LATER_COMMIT
    assert report.ready
    # ...and the repository this gate protects does not turn it on.
    assert load_config(CONFIG_FILE).github_resolution_satisfies_disposition is False


def test_unknown_thread_state_fails_closed():
    report = evaluate(_green_with_findings([_finding(thread_resolved=None)]))
    assert report.findings[0].classification is FindingClassification.UNKNOWN
    assert report.reason_codes == (ReasonCode.INDETERMINATE_FINDING,)


def test_an_open_finding_against_an_older_commit_must_still_be_classified():
    """Not silently ignored, and not permanently blocking either — classified."""
    report = evaluate(_green_with_findings([_finding(commit_sha=OLDER)]))
    assert report.findings[0].classification is FindingClassification.UNKNOWN
    assert report.reason_codes == (ReasonCode.INDETERMINATE_FINDING,)

    report = evaluate(
        _green_with_findings(
            [_finding(commit_sha=OLDER)],
            [
                DispositionRecord(
                    finding_id="review_comment:1",
                    classification=FindingClassification.STALE_SUPERSEDED_HEAD,
                    rationale="The code it points at no longer exists.",
                    recorded_by="taylor",
                ),
            ],
        ),
    )
    assert report.findings[0].classification is FindingClassification.STALE_SUPERSEDED_HEAD
    assert report.ready


def test_a_stale_disposition_cannot_be_used_on_a_current_head_finding():
    report = evaluate(
        _green_with_findings(
            [_finding(commit_sha=HEAD)],
            [
                DispositionRecord(
                    finding_id="review_comment:1",
                    classification=FindingClassification.STALE_SUPERSEDED_HEAD,
                    rationale="Claiming staleness that is not true.",
                    recorded_by="someone",
                ),
            ],
        ),
    )
    assert report.findings[0].classification is FindingClassification.UNRESOLVED_SUBSTANTIVE
    assert report.reason_codes == (ReasonCode.UNRESOLVED_SUBSTANTIVE_FINDING,)


@pytest.mark.parametrize(
    ("resolved_by", "detail_fragment"),
    [
        (None, "names no full commit id"),
        ("deadbee", "names no full commit id"),
        ("d" * 40, "not one of this pull request's commits"),
        (OLDER, "not later than the commit"),
    ],
)
def test_resolved_by_a_later_commit_is_checked_against_the_commit_graph(
    resolved_by,
    detail_fragment,
):
    """A claim that a later commit fixed it is verified, not believed.

    `OLDER` here is the very commit the finding was written against, so
    "resolved by it" is self-referential; `d * 40` is a real-looking sha that
    belongs to another pull request, which is PR #91's actual shape.
    """
    report = evaluate(
        _green_with_findings(
            [_finding(commit_sha=OLDER)],
            [
                DispositionRecord(
                    finding_id="review_comment:1",
                    classification=FindingClassification.RESOLVED_BY_LATER_COMMIT,
                    rationale="Fixed.",
                    recorded_by="someone",
                    resolved_by_commit=resolved_by,
                ),
            ],
        ),
    )
    assert report.findings[0].classification is FindingClassification.UNKNOWN
    assert detail_fragment in report.findings[0].detail
    assert report.reason_codes == (ReasonCode.INVALID_DISPOSITION,)


def test_a_genuine_later_commit_in_this_pull_request_does_dispose():
    report = evaluate(
        _green_with_findings(
            [_finding(commit_sha=OLDER)],
            [
                DispositionRecord(
                    finding_id="review_comment:1",
                    classification=FindingClassification.RESOLVED_BY_LATER_COMMIT,
                    rationale="Allowlist is now read before the port is installed.",
                    recorded_by="taylor",
                    resolved_by_commit=HEAD,
                ),
            ],
        ),
    )
    assert report.findings[0].classification is FindingClassification.RESOLVED_BY_LATER_COMMIT
    assert report.ready


def test_a_disposition_for_another_finding_does_not_travel():
    report = evaluate(
        _green_with_findings(
            [_finding(finding_id="review_comment:2")],
            [
                DispositionRecord(
                    finding_id="review_comment:1",
                    classification=FindingClassification.NON_SUBSTANTIVE,
                    rationale="Different finding entirely.",
                    recorded_by="taylor",
                ),
            ],
        ),
    )
    assert report.findings[0].classification is FindingClassification.UNRESOLVED_SUBSTANTIVE
    assert not report.ready


def test_indeterminate_check_state_blocks_even_with_no_observed_failure():
    report = evaluate(
        QualificationInput(
            repo="owner/name",
            pr_number=1,
            head_sha=HEAD,
            required_checks=REQUIRED_ONE,
            checks=(),
            check_state_determined=False,
        ),
    )
    assert ReasonCode.CHECK_STATE_INDETERMINATE in report.reason_codes
    # No check results are claimed at all: the gate does not invent a state
    # it could not read.
    assert report.checks == ()
    assert not report.ready


def test_indeterminate_review_state_blocks_even_with_no_observed_finding():
    report = evaluate(
        QualificationInput(
            repo="owner/name",
            pr_number=1,
            head_sha=HEAD,
            required_checks=REQUIRED_ONE,
            checks=(CheckObservation("Merge Candidate", "smoke", HEAD, "completed", "success"),),
            review_state_determined=False,
        ),
    )
    assert report.reason_codes == (ReasonCode.REVIEW_STATE_INDETERMINATE,)
    assert report.findings == ()
    assert not report.ready


def test_unknown_is_never_a_non_blocking_classification():
    """The one invariant a future refactor is most likely to break."""
    assert FindingClassification.UNKNOWN not in NON_BLOCKING_CLASSIFICATIONS
    assert FindingClassification.UNRESOLVED_SUBSTANTIVE not in NON_BLOCKING_CLASSIFICATIONS


def test_evidence_for_one_commit_never_authorises_another():
    report = evaluate(
        QualificationInput(
            repo="owner/name",
            pr_number=1,
            head_sha=HEAD,
            required_checks=REQUIRED_ONE,
            checks=(CheckObservation("Merge Candidate", "smoke", HEAD, "completed", "success"),),
        ),
    )
    assert report.ready
    evidence = report.to_dict()

    applies, _ = verify_evidence_applies_to(evidence, HEAD)
    assert applies

    for other in (NEWER, OLDER, None, "", "aaaaaaa"):
        applies, reason = verify_evidence_applies_to(evidence, other)
        assert not applies, other
        assert reason is not None

    tampered = dict(evidence, head_sha=None)
    applies, reason = verify_evidence_applies_to(tampered, HEAD)
    assert not applies
    assert reason is not None and reason.code is ReasonCode.HEAD_SHA_UNKNOWN


def test_a_new_commit_invalidates_previously_valid_qualification_evidence():
    """The PR #101 / stale-evidence failure, end to end.

    A head qualifies. A commit lands. The old evidence must not carry over,
    and re-running the gate against the new head must refuse, because nothing
    has run against it yet.
    """
    green_at_head = CheckObservation("Merge Candidate", "smoke", HEAD, "completed", "success")
    before = evaluate(
        QualificationInput(
            repo="owner/name",
            pr_number=1,
            head_sha=HEAD,
            pr_commits=(HEAD,),
            required_checks=REQUIRED_ONE,
            checks=(green_at_head,),
        ),
    )
    assert before.ready

    applies, reason = verify_evidence_applies_to(before.to_dict(), NEWER)
    assert not applies and reason is not None
    assert reason.code is ReasonCode.EVIDENCE_HEAD_MISMATCH

    after = evaluate(
        QualificationInput(
            repo="owner/name",
            pr_number=1,
            head_sha=NEWER,
            pr_commits=(HEAD, NEWER),
            required_checks=REQUIRED_ONE,
            # The same check run, unchanged: it still belongs to the old head.
            checks=(green_at_head,),
        ),
    )
    assert not after.ready
    assert after.checks[0].outcome is CheckOutcome.STALE


def test_the_evaluator_is_deterministic():
    """Same input, same verdict — the property that makes this a control."""
    scenario = json.loads(
        (SCENARIO_DIR / "pr_108_required_tier_never_ran.json").read_text(encoding="utf-8"),
    )
    first = evaluate(_input_from(scenario["input"]), generated_at="t")
    second = evaluate(_input_from(scenario["input"]), generated_at="t")
    assert first.to_dict() == second.to_dict()


# ---------------------------------------------------------------------------
# The committed control state itself.
# ---------------------------------------------------------------------------


def test_the_repository_requires_the_merge_candidate_tier():
    """The tier that never ran on PR #108 is required, job by job."""
    config = load_config(CONFIG_FILE)
    required = {check.key for check in config.required_checks}
    for job in (
        "Quality (format, lint, packaging contract)",
        "Tests + coverage (Ubuntu, py3.10)",
        "Tests + coverage (Ubuntu, py3.11)",
        "Critical integration + lifecycle (Ubuntu, py3.10)",
        "Critical integration + lifecycle (Ubuntu, py3.11)",
        "Windows full default suite + actuation (py3.11)",
        "smoke",
    ):
        assert f"Merge Candidate / {job}" in required
    assert any(check.workflow == "CI" for check in config.required_checks)
    assert any(check.workflow == "Integration" for check in config.required_checks)


def test_every_required_job_exists_in_a_workflow_file():
    """A required job that no workflow defines could never go green.

    Without this, a typo in the config would be indistinguishable from a tier
    that is permanently missing — the gate would block forever and someone
    would be tempted to weaken it.
    """
    defined: set[str] = set()
    for path in (REPO_ROOT / ".github" / "workflows").glob("*.yml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        workflow_name = document.get("name")
        for job in (document.get("jobs") or {}).values():
            name = job.get("name")
            if not name:
                continue
            matrix = ((job.get("strategy") or {}).get("matrix") or {}).get("python-version")
            if matrix and "${{ matrix.python-version }}" in name:
                for version in matrix:
                    defined.add(
                        f"{workflow_name} / "
                        + name.replace("${{ matrix.python-version }}", str(version)),
                    )
            else:
                defined.add(f"{workflow_name} / {name}")

    for check in load_config(CONFIG_FILE).required_checks:
        assert check.key in defined, f"{check.key} is required but no workflow defines it"


def test_the_gate_is_not_its_own_evidence():
    document = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "merge-qualification.yml").read_text(
            encoding="utf-8",
        ),
    )
    gate_workflow = document["name"]
    for check in load_config(CONFIG_FILE).required_checks:
        assert check.workflow != gate_workflow


def test_the_committed_dispositions_file_parses():
    assert load_dispositions(DISPOSITIONS_FILE) == ()


@pytest.mark.parametrize(
    "document",
    [
        "required_tiers: []",
        "required_tiers:\n  - workflow: CI\n",
        "required_tiers:\n  - jobs: [smoke]\n",
        "required_tiers:\n  - workflow: CI\n    jobs: [smoke]\ngithub_resolution_satisfies_disposition: maybe\n",
        "schema_version: 1",
    ],
)
def test_an_unusable_config_raises_rather_than_qualifying_nothing(document, tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ControlStateError):
        load_config(path)


def test_a_missing_config_raises():
    with pytest.raises(ControlStateError):
        load_config(Path("/nonexistent/merge-qualification/config.yml"))


@pytest.mark.parametrize(
    "document",
    [
        # No rationale.
        "dispositions:\n  - finding_id: review_comment:1\n    classification: non_substantive\n    recorded_by: taylor\n",
        # No recorded_by.
        "dispositions:\n  - finding_id: review_comment:1\n    classification: non_substantive\n    rationale: fine\n",
        # An invented classification.
        "dispositions:\n  - finding_id: review_comment:1\n    classification: probably_fine\n    rationale: fine\n    recorded_by: taylor\n",
        # 'unknown' is a state the evaluator assigns, never a decision anyone takes.
        "dispositions:\n  - finding_id: review_comment:1\n    classification: unknown\n    rationale: fine\n    recorded_by: taylor\n",
        # The same finding dispositioned twice, ambiguously.
        "dispositions:\n"
        "  - finding_id: review_comment:1\n    classification: non_substantive\n    rationale: a\n    recorded_by: taylor\n"
        "  - finding_id: review_comment:1\n    classification: unresolved_substantive\n    rationale: b\n    recorded_by: taylor\n",
    ],
)
def test_an_unusable_disposition_raises_rather_than_passing_silently(document, tmp_path):
    path = tmp_path / "dispositions.yml"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ControlStateError):
        load_dispositions(path)


# ---------------------------------------------------------------------------
# The command-line gate. Exit codes are the thing CI reads, so they are the
# thing asserted: there is no path on which "the gate broke" exits 0.
# ---------------------------------------------------------------------------


def _write_ready_evidence(tmp_path: Path, head: str = HEAD) -> Path:
    report = evaluate(
        QualificationInput(
            repo="owner/name",
            pr_number=1,
            head_sha=head,
            required_checks=REQUIRED_ONE,
            checks=(CheckObservation("Merge Candidate", "smoke", head, "completed", "success"),),
        ),
    )
    assert report.ready
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(report.to_dict()), encoding="utf-8")
    return path


def test_verify_accepts_evidence_for_the_head_it_names(tmp_path):
    from scripts.ci.merge_qualification.cli import main

    evidence = _write_ready_evidence(tmp_path)
    assert main(["verify", "--evidence", str(evidence), "--head", HEAD]) == 0


def test_verify_refuses_evidence_from_an_older_head(tmp_path):
    from scripts.ci.merge_qualification.cli import main

    evidence = _write_ready_evidence(tmp_path)
    assert main(["verify", "--evidence", str(evidence), "--head", NEWER]) == 1


def test_verify_refuses_evidence_whose_verdict_is_not_ready(tmp_path):

    report = evaluate(
        QualificationInput(
            repo="owner/name",
            pr_number=1,
            head_sha=HEAD,
            required_checks=REQUIRED_ONE,
            checks=(),
        ),
    )
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(report.to_dict()), encoding="utf-8")
    from scripts.ci.merge_qualification.cli import main as run

    assert run(["verify", "--evidence", str(path), "--head", HEAD]) == 1


def test_verify_refuses_unreadable_evidence(tmp_path):
    from scripts.ci.merge_qualification.cli import main

    path = tmp_path / "evidence.json"
    path.write_text("{not json", encoding="utf-8")
    assert main(["verify", "--evidence", str(path), "--head", HEAD]) == 2
    assert main(["verify", "--evidence", str(tmp_path / "absent.json"), "--head", HEAD]) == 2


def test_qualify_fails_closed_when_the_control_state_is_broken(tmp_path):
    from scripts.ci.merge_qualification.cli import main

    broken = tmp_path / "config.yml"
    broken.write_text("required_tiers: []", encoding="utf-8")
    exit_code = main(
        [
            "qualify",
            "--repo",
            "owner/name",
            "--pr",
            "1",
            "--config",
            str(broken),
            "--dispositions",
            str(DISPOSITIONS_FILE),
        ],
    )
    assert exit_code == 2


def test_qualify_fails_closed_when_the_forge_cannot_be_read(tmp_path, monkeypatch):
    """No token, no reads, no verdict but NOT READY — and no network touched."""
    from scripts.ci.merge_qualification import cli

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    out = tmp_path / "evidence.json"
    exit_code = cli.main(
        [
            "qualify",
            "--repo",
            "owner/name",
            "--pr",
            "1",
            "--config",
            str(CONFIG_FILE),
            "--dispositions",
            str(DISPOSITIONS_FILE),
            "--out",
            str(out),
        ],
    )
    assert exit_code == 1
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["verdict"] == "not_ready"
    assert {reason["code"] for reason in evidence["reasons"]} == {"head_sha_unknown"}


# ---------------------------------------------------------------------------
# Superseded runs on the same head.
#
# Every CI tier in this repository sets `cancel-in-progress: true`, so a
# second trigger on the same commit -- applying the `ci:merge-candidate`
# label to a PR that was just pushed, say -- cancels the first run of that
# workflow. Both runs belong to the same head. Reading the cancelled one as
# this head's fate would make the gate permanently unsatisfiable, and a gate
# that can never say yes is a gate people learn to route around.
#
# Observed live on PR #121: run 35599799592 (Integration) cancelled
# 35599799598 on head adb14fc while a later attempt was still running.
# ---------------------------------------------------------------------------


def _run(number, attempt, status, conclusion, sha=HEAD):
    return CheckObservation(
        "Merge Candidate",
        "smoke",
        sha,
        status,
        conclusion,
        run_number=number,
        run_attempt=attempt,
    )


def _one_required(*observations):
    return evaluate(
        QualificationInput(
            repo="owner/name",
            pr_number=1,
            head_sha=HEAD,
            required_checks=REQUIRED_ONE,
            checks=observations,
        ),
    )


def test_a_superseded_cancelled_run_does_not_veto_the_run_that_replaced_it():
    report = _one_required(
        _run(1, 1, "completed", "cancelled"),
        _run(2, 1, "completed", "success"),
    )
    assert report.checks[0].outcome is CheckOutcome.GREEN
    assert report.ready


def test_a_later_red_run_is_not_rescued_by_an_earlier_green_one():
    """The direction that matters: recency cannot launder a failure."""
    report = _one_required(
        _run(1, 1, "completed", "success"),
        _run(2, 1, "completed", "failure"),
    )
    assert report.checks[0].outcome is CheckOutcome.RED
    assert report.reason_codes == (ReasonCode.REQUIRED_CHECK_FAILING,)


def test_a_later_run_still_in_progress_is_not_proven_by_an_earlier_green_one():
    report = _one_required(
        _run(1, 1, "completed", "success"),
        _run(2, 1, "in_progress", None),
    )
    assert report.checks[0].outcome is CheckOutcome.INCOMPLETE
    assert not report.ready


def test_a_later_attempt_of_the_same_run_wins():
    report = _one_required(
        _run(7, 1, "completed", "failure"),
        _run(7, 2, "completed", "success"),
    )
    assert report.checks[0].outcome is CheckOutcome.GREEN
    assert report.ready

    report = _one_required(
        _run(7, 1, "completed", "success"),
        _run(7, 2, "completed", "cancelled"),
    )
    assert report.checks[0].outcome is CheckOutcome.CANCELLED
    assert not report.ready


def test_observations_the_forge_gave_no_ordinals_for_stay_worst_wins():
    """No ordering means no basis for preferring one. Fail closed."""
    report = _one_required(
        CheckObservation("Merge Candidate", "smoke", HEAD, "completed", "success"),
        CheckObservation("Merge Candidate", "smoke", HEAD, "completed", "failure"),
    )
    assert report.checks[0].outcome is CheckOutcome.RED
    assert not report.ready


def test_recency_never_reaches_across_heads():
    """A newer run of an older commit proves nothing about this head."""
    report = _one_required(
        _run(9, 1, "completed", "success", sha=OLDER),
        _run(2, 1, "completed", "failure"),
    )
    assert report.checks[0].outcome is CheckOutcome.RED
