"""The deterministic merge-qualification evaluator.

This is the control boundary AUDIT-CTRL-1 exists to repair. It is a pure
function of observed forge state and committed disposition records: same
input, same verdict, no network, no clock, no model. A language model may
help a person *write* a disposition record or read this report; it is never
consulted here, and nothing it says can reach `Verdict.READY`.

Three rules hold the whole thing up.

1. **Everything binds to one exact head.** A check run is evidence only for
   the commit it ran against. A finding is classified relative to the commit
   it was written against. A report authorises the head named in it.
2. **Green must be proven, not assumed.** The absence of a failure is not a
   pass. Missing, incomplete, cancelled, skipped, stale and unreadable all
   refuse.
3. **Unknown fails closed.** Every branch that cannot establish a fact ends
   in a blocking reason, not in a shrug.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .model import (
    NON_BLOCKING_CLASSIFICATIONS,
    CheckObservation,
    CheckOutcome,
    CheckResult,
    DispositionRecord,
    FindingClassification,
    FindingResult,
    QualificationInput,
    QualificationReport,
    Reason,
    ReasonCode,
    RequiredCheck,
    ReviewFinding,
    Verdict,
    is_full_sha,
)

#: Forge conclusions that mean "ran, and failed".
_RED_CONCLUSIONS = frozenset({"failure", "timed_out", "action_required", "startup_failure"})

#: Outcomes that are not `GREEN` but are also not a *failure*, so that the
#: report can say which of the two PR #101 / PR #108 shapes it is looking at.
_NOT_PROVEN_GREEN = frozenset(
    {
        CheckOutcome.INCOMPLETE,
        CheckOutcome.CANCELLED,
        CheckOutcome.SKIPPED,
        CheckOutcome.MISSING,
        CheckOutcome.STALE,
        CheckOutcome.UNKNOWN,
    },
)


def classify_check_run(status: str | None, conclusion: str | None) -> CheckOutcome:
    """Map one forge check run onto a `CheckOutcome`, fail-closed.

    `neutral` is deliberately `UNKNOWN` rather than green: it means the check
    declined to judge, which is not the same as judging the head sound. Any
    conclusion this function has never heard of is `UNKNOWN` for the same
    reason — a forge that grows a new outcome must not silently grow a new
    way to pass.
    """
    normalised_status = (status or "").strip().lower()
    normalised_conclusion = (conclusion or "").strip().lower()

    if normalised_status and normalised_status != "completed":
        return CheckOutcome.INCOMPLETE
    if not normalised_conclusion:
        # Completed (or unreported) with no conclusion at all: unreadable, not green.
        return CheckOutcome.UNKNOWN
    if normalised_conclusion == "success":
        return CheckOutcome.GREEN
    if normalised_conclusion in _RED_CONCLUSIONS:
        return CheckOutcome.RED
    if normalised_conclusion == "cancelled":
        return CheckOutcome.CANCELLED
    if normalised_conclusion == "skipped":
        return CheckOutcome.SKIPPED
    if normalised_conclusion == "stale":
        return CheckOutcome.STALE
    return CheckOutcome.UNKNOWN


def _evaluate_one_check(
    required: RequiredCheck,
    head_sha: str,
    observations: tuple[CheckObservation, ...],
) -> CheckResult:
    for_this_check = [
        observation for observation in observations if observation.key == required.key
    ]
    for_this_head = [
        observation
        for observation in for_this_check
        if (observation.head_sha or "").lower() == head_sha.lower()
    ]

    if not for_this_head:
        if for_this_check:
            other_heads = sorted(
                {(observation.head_sha or "unknown")[:12] for observation in for_this_check},
            )
            return CheckResult(
                required,
                CheckOutcome.STALE,
                "the only runs of this required check belong to other heads "
                f"({', '.join(other_heads)}); nothing proves {head_sha[:12]}",
            )
        return CheckResult(
            required,
            CheckOutcome.MISSING,
            f"no run of this required check exists for {head_sha[:12]}",
        )

    # Only the latest run of this workflow on this head is evidence about it.
    # Earlier runs on the same head were superseded — every tier here sets
    # `cancel-in-progress: true`, so a second trigger (a label, a ready-for-
    # review) cancels the first, and reading that cancellation as this head's
    # fate would make the gate permanently unsatisfiable. Observations the
    # forge gave no ordinals for are all equally current, which is why the
    # rule below still applies inside the selected group.
    latest = max(observation.recency for observation in for_this_head)
    current = [observation for observation in for_this_head if observation.recency == latest]

    outcomes = [
        (observation, classify_check_run(observation.status, observation.conclusion))
        for observation in current
    ]
    # Worst outcome wins within that run: one red job is not cancelled out by
    # a green one reported alongside it.
    for wanted in (
        CheckOutcome.RED,
        CheckOutcome.UNKNOWN,
        CheckOutcome.CANCELLED,
        CheckOutcome.SKIPPED,
        CheckOutcome.INCOMPLETE,
        CheckOutcome.STALE,
    ):
        for observation, outcome in outcomes:
            if outcome is wanted:
                return CheckResult(
                    required,
                    outcome,
                    f"run against {head_sha[:12]} reports "
                    f"status={observation.status!r} conclusion={observation.conclusion!r}",
                )
    return CheckResult(
        required,
        CheckOutcome.GREEN,
        f"completed successfully against {head_sha[:12]}",
    )


def _commit_index(pr_commits: tuple[str, ...], sha: str | None) -> int | None:
    if not sha:
        return None
    lowered = [commit.lower() for commit in pr_commits]
    try:
        return lowered.index(sha.lower())
    except ValueError:
        return None


def _classify_finding(
    finding: ReviewFinding,
    head_sha: str,
    pr_commits: tuple[str, ...],
    disposition: DispositionRecord | None,
    github_resolution_satisfies_disposition: bool,
) -> FindingResult:
    def result(
        classification: FindingClassification,
        detail: str,
        *,
        blocking: bool | None = None,
    ) -> FindingResult:
        is_blocking = (
            classification not in NON_BLOCKING_CLASSIFICATIONS if blocking is None else blocking
        )
        return FindingResult(finding, classification, is_blocking, detail)

    against_current_head = (finding.commit_sha or "").lower() == head_sha.lower()

    if disposition is not None:
        classification = disposition.classification

        if classification is FindingClassification.RESOLVED_BY_LATER_COMMIT:
            resolving = disposition.resolved_by_commit
            if not is_full_sha(resolving):
                return result(
                    FindingClassification.UNKNOWN,
                    "disposition claims a later commit resolved this finding but names "
                    f"no full commit id ({resolving!r})",
                )
            resolving_index = _commit_index(pr_commits, resolving)
            if resolving_index is None:
                return result(
                    FindingClassification.UNKNOWN,
                    f"resolving commit {resolving[:12]} is not one of this pull "
                    "request's commits, so the claim cannot be checked",
                )
            finding_index = _commit_index(pr_commits, finding.commit_sha)
            if finding_index is None:
                return result(
                    FindingClassification.UNKNOWN,
                    "the commit this finding was written against is not known, so "
                    "'resolved by a later commit' cannot be checked",
                )
            if resolving_index <= finding_index:
                return result(
                    FindingClassification.UNKNOWN,
                    f"resolving commit {resolving[:12]} is not later than the commit "
                    f"the finding was written against ({(finding.commit_sha or '')[:12]})",
                )
            return result(
                FindingClassification.RESOLVED_BY_LATER_COMMIT,
                f"resolved by {resolving[:12]}, which follows "
                f"{(finding.commit_sha or '')[:12]} in this pull request",
            )

        if classification is FindingClassification.STALE_SUPERSEDED_HEAD:
            if not finding.commit_sha:
                return result(
                    FindingClassification.UNKNOWN,
                    "disposition claims a superseded head but the finding's own "
                    "commit is unknown",
                )
            if against_current_head:
                return result(
                    FindingClassification.UNRESOLVED_SUBSTANTIVE,
                    "disposition claims a superseded head, but the finding is "
                    f"against the current head {head_sha[:12]}",
                )
            return result(
                FindingClassification.STALE_SUPERSEDED_HEAD,
                f"written against {(finding.commit_sha or '')[:12]}, which is not the "
                f"current head {head_sha[:12]}",
            )

        if classification is FindingClassification.NON_SUBSTANTIVE:
            return result(
                FindingClassification.NON_SUBSTANTIVE,
                f"explicitly recorded as non-substantive by {disposition.recorded_by}",
            )

        if classification is FindingClassification.UNRESOLVED_SUBSTANTIVE:
            return result(
                FindingClassification.UNRESOLVED_SUBSTANTIVE,
                "explicitly recorded as an open substantive finding",
            )

        return result(
            FindingClassification.UNKNOWN,
            f"disposition carries an unusable classification {classification!r}",
        )

    if finding.thread_resolved is None:
        return result(
            FindingClassification.UNKNOWN,
            "thread resolution state could not be determined",
        )

    if finding.thread_resolved:
        if github_resolution_satisfies_disposition:
            return result(
                FindingClassification.RESOLVED_BY_LATER_COMMIT,
                "thread is marked resolved on the forge, and this repository "
                "accepts forge resolution as a disposition",
            )
        return result(
            FindingClassification.UNKNOWN,
            "thread is marked resolved on the forge, but this repository requires "
            "an explicit disposition record; forge resolution alone is not evidence "
            "that the code changed",
        )

    if against_current_head:
        return result(
            FindingClassification.UNRESOLVED_SUBSTANTIVE,
            f"open thread against the current head {head_sha[:12]} with no disposition",
        )

    return result(
        FindingClassification.UNKNOWN,
        "open thread against a commit that is not the current head; it must be "
        "classified explicitly as resolved, stale or non-substantive",
    )


def evaluate(
    qualification_input: QualificationInput,
    *,
    github_resolution_satisfies_disposition: bool = False,
    generated_at: str | None = None,
) -> QualificationReport:
    """Decide whether one pull request head is genuinely merge-qualified."""

    reasons: list[Reason] = []
    check_results: tuple[CheckResult, ...] = ()
    finding_results: tuple[FindingResult, ...] = ()
    head_sha = qualification_input.head_sha

    stamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    if not is_full_sha(head_sha):
        reasons.append(
            Reason(
                ReasonCode.HEAD_SHA_UNKNOWN,
                "the exact head commit could not be established as a full 40-character "
                f"sha (got {head_sha!r}); nothing can be qualified against it",
            ),
        )
        return QualificationReport(
            repo=qualification_input.repo,
            pr_number=qualification_input.pr_number,
            head_sha=head_sha,
            verdict=Verdict.NOT_READY,
            reasons=tuple(reasons),
            generated_at=stamp,
        )

    assert head_sha is not None  # narrowed by is_full_sha

    if not qualification_input.check_state_determined:
        reasons.append(
            Reason(
                ReasonCode.CHECK_STATE_INDETERMINATE,
                "check state for this head could not be read; qualification fails closed",
            ),
        )
    if not qualification_input.review_state_determined:
        reasons.append(
            Reason(
                ReasonCode.REVIEW_STATE_INDETERMINATE,
                "review state for this head could not be read; qualification fails closed",
            ),
        )

    if not qualification_input.required_checks:
        reasons.append(
            Reason(
                ReasonCode.NO_REQUIRED_TIERS_CONFIGURED,
                "no required CI tier is configured, so no head can be proven green",
            ),
        )
    elif qualification_input.check_state_determined:
        check_results = tuple(
            _evaluate_one_check(required, head_sha, qualification_input.checks)
            for required in qualification_input.required_checks
        )
        for check_result in check_results:
            if check_result.outcome is CheckOutcome.RED:
                reasons.append(
                    Reason(
                        ReasonCode.REQUIRED_CHECK_FAILING,
                        f"{check_result.required.key}: {check_result.detail}",
                    ),
                )
            elif check_result.outcome in _NOT_PROVEN_GREEN:
                reasons.append(
                    Reason(
                        ReasonCode.REQUIRED_CHECK_NOT_PROVEN_GREEN,
                        f"{check_result.required.key} is {check_result.outcome.value}: "
                        f"{check_result.detail}",
                    ),
                )

    if qualification_input.review_state_determined:
        by_id = {
            disposition.finding_id: disposition for disposition in qualification_input.dispositions
        }
        finding_results = tuple(
            _classify_finding(
                finding,
                head_sha,
                qualification_input.pr_commits,
                by_id.get(finding.finding_id),
                github_resolution_satisfies_disposition,
            )
            for finding in qualification_input.findings
        )
        for finding_result in finding_results:
            if not finding_result.blocking:
                continue
            if finding_result.classification is FindingClassification.UNRESOLVED_SUBSTANTIVE:
                code = ReasonCode.UNRESOLVED_SUBSTANTIVE_FINDING
            elif by_id.get(finding_result.finding.finding_id) is not None:
                code = ReasonCode.INVALID_DISPOSITION
            else:
                code = ReasonCode.INDETERMINATE_FINDING
            reasons.append(
                Reason(
                    code,
                    f"{finding_result.finding.finding_id} "
                    f"(by {finding_result.finding.author}): {finding_result.detail}",
                ),
            )

    verdict = Verdict.NOT_READY if reasons else Verdict.READY
    return QualificationReport(
        repo=qualification_input.repo,
        pr_number=qualification_input.pr_number,
        head_sha=head_sha,
        verdict=verdict,
        reasons=tuple(reasons),
        checks=check_results,
        findings=finding_results,
        generated_at=stamp,
    )


def verify_evidence_applies_to(
    evidence: dict[str, object],
    current_head_sha: str | None,
) -> tuple[bool, Reason | None]:
    """Refuse to let a report generated for commit A authorise commit B.

    This is the second half of head binding. `evaluate()` binds the report to
    the head it was computed from; this function is what a merge gate calls
    later, when the head may have moved underneath the report.
    """
    evidence_head = evidence.get("head_sha")
    if not isinstance(evidence_head, str) or not is_full_sha(evidence_head):
        return False, Reason(
            ReasonCode.HEAD_SHA_UNKNOWN,
            f"qualification evidence names no usable head ({evidence_head!r})",
        )
    if not is_full_sha(current_head_sha):
        return False, Reason(
            ReasonCode.HEAD_SHA_UNKNOWN,
            f"the current head could not be established ({current_head_sha!r})",
        )
    assert current_head_sha is not None
    if evidence_head.lower() != current_head_sha.lower():
        return False, Reason(
            ReasonCode.EVIDENCE_HEAD_MISMATCH,
            f"qualification evidence was generated for {evidence_head[:12]} but the "
            f"current head is {current_head_sha[:12]}; it must be regenerated",
        )
    return True, None
