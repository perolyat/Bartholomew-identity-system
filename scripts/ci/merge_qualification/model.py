"""Value types for deterministic merge qualification.

Nothing in this module reasons. It names the states that the evaluator in
`evaluate.py` is allowed to distinguish, and it fixes the vocabulary the
evidence file is written in, so that a future refactor cannot quietly
collapse two states that the AUDIT-CTRL-1 audit proved must stay apart.

The three that matter most:

* `CheckOutcome` separates **failing** from **not proven green**. PR #101 was
  merged on a head whose Merge Candidate workflow was failing; PR #108 was
  merged on a head the tier had never run against at all. Those are different
  facts, and both must stop a merge.
* `FindingClassification` separates a finding that is **currently
  unresolved** from one **resolved by a later commit**, one **against a
  superseded head**, one that was **never substantive**, and one whose state
  is **unknown**. PR #91 and PR #120 are the two halves of that: real findings
  treated as absent. `UNKNOWN` is a blocking state, never a benign one.
* `Verdict` has exactly two members. There is no "ready with caveats".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

SCHEMA_VERSION = 1

_FULL_SHA_LENGTH = 40


class CheckOutcome(str, Enum):
    """What a required check proves about the exact head being qualified."""

    GREEN = "green"
    """Completed successfully, against this exact head."""

    RED = "red"
    """Completed with a failure, timeout or action-required, against this head."""

    INCOMPLETE = "incomplete"
    """Registered but not finished: queued, in progress, waiting."""

    CANCELLED = "cancelled"
    """Ended without a verdict. Never green."""

    SKIPPED = "skipped"
    """Registered and deliberately not executed. Never green."""

    MISSING = "missing"
    """No run of this required check exists for this head at all."""

    STALE = "stale"
    """A run exists, but only against a different (older) head."""

    UNKNOWN = "unknown"
    """State could not be established. Fails closed."""


class FindingClassification(str, Enum):
    """The truthful state of one review finding against the head being qualified."""

    UNRESOLVED_SUBSTANTIVE = "unresolved_substantive"
    RESOLVED_BY_LATER_COMMIT = "resolved_by_later_commit"
    STALE_SUPERSEDED_HEAD = "stale_superseded_head"
    NON_SUBSTANTIVE = "non_substantive"
    UNKNOWN = "unknown"


#: Classifications that do not, on their own, stop a merge. Everything not
#: listed here blocks. The set is deliberately expressed as an allowlist: a
#: new classification added later blocks until someone decides otherwise.
NON_BLOCKING_CLASSIFICATIONS = frozenset(
    {
        FindingClassification.RESOLVED_BY_LATER_COMMIT,
        FindingClassification.STALE_SUPERSEDED_HEAD,
        FindingClassification.NON_SUBSTANTIVE,
    },
)


class Verdict(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"


class ReasonCode(str, Enum):
    """Why qualification refused. One code per distinguishable failure class."""

    HEAD_SHA_UNKNOWN = "head_sha_unknown"
    NO_REQUIRED_TIERS_CONFIGURED = "no_required_tiers_configured"
    CHECK_STATE_INDETERMINATE = "check_state_indeterminate"
    REVIEW_STATE_INDETERMINATE = "review_state_indeterminate"
    REQUIRED_CHECK_FAILING = "required_check_failing"
    REQUIRED_CHECK_NOT_PROVEN_GREEN = "required_check_not_proven_green"
    UNRESOLVED_SUBSTANTIVE_FINDING = "unresolved_substantive_finding"
    INDETERMINATE_FINDING = "indeterminate_finding"
    INVALID_DISPOSITION = "invalid_disposition"
    EVIDENCE_HEAD_MISMATCH = "evidence_head_mismatch"


def is_full_sha(value: str | None) -> bool:
    """True only for a full 40-character hexadecimal commit id.

    Abbreviated shas are rejected on purpose: qualification binds to an exact
    commit, and a 7-character prefix is not an exact commit.
    """
    if not value or len(value) != _FULL_SHA_LENGTH:
        return False
    return all(character in "0123456789abcdef" for character in value.lower())


@dataclass(frozen=True)
class RequiredCheck:
    """One check that must be proven green for the head under qualification."""

    workflow: str
    job: str

    @property
    def key(self) -> str:
        return f"{self.workflow} / {self.job}"


@dataclass(frozen=True)
class CheckObservation:
    """One observed check run, as reported by the forge.

    `head_sha` is the commit the run actually executed against — not the
    commit someone wishes it had executed against.
    """

    workflow: str
    job: str
    head_sha: str | None
    status: str | None
    conclusion: str | None
    url: str | None = None
    #: The forge's own ordinals for the workflow run this job belongs to.
    #: They exist so that a run *superseded on the same head* — which this
    #: repository produces routinely, because every tier sets
    #: `cancel-in-progress: true` and a label event re-triggers it — can be
    #: told from the run that actually decided that head. `None` means the
    #: forge gave no ordering, and unordered observations are all treated as
    #: equally current.
    run_number: int | None = None
    run_attempt: int | None = None

    @property
    def key(self) -> str:
        return f"{self.workflow} / {self.job}"

    @property
    def recency(self) -> tuple[int, int]:
        """Ordering key within one (workflow, job, head). Higher is later."""
        return (
            self.run_number if self.run_number is not None else -1,
            self.run_attempt if self.run_attempt is not None else -1,
        )


@dataclass(frozen=True)
class ReviewFinding:
    """One review comment or thread observed against the pull request."""

    finding_id: str
    author: str
    #: The commit the comment was written against, where the forge reports it.
    commit_sha: str | None
    #: Forge-side thread resolution. `None` means "could not be determined".
    thread_resolved: bool | None
    excerpt: str = ""
    url: str | None = None


@dataclass(frozen=True)
class DispositionRecord:
    """An explicit, committed decision about one finding.

    This is the only thing that can turn a finding from blocking into
    non-blocking. It lives in the repository (see
    `.github/merge-qualification/dispositions.yml`), so it arrives through a
    reviewed commit rather than through a narrative assertion.
    """

    finding_id: str
    classification: FindingClassification
    rationale: str
    recorded_by: str
    resolved_by_commit: str | None = None


@dataclass(frozen=True)
class QualificationInput:
    """Everything the evaluator is allowed to look at.

    `check_state_determined` / `review_state_determined` are how collection
    failure reaches the evaluator. A collector that could not read the forge
    sets them false; it never emits an empty list and calls it clean.
    """

    repo: str
    pr_number: int
    head_sha: str | None
    #: Commits belonging to this pull request, oldest first, full shas.
    pr_commits: tuple[str, ...] = ()
    required_checks: tuple[RequiredCheck, ...] = ()
    checks: tuple[CheckObservation, ...] = ()
    findings: tuple[ReviewFinding, ...] = ()
    dispositions: tuple[DispositionRecord, ...] = ()
    check_state_determined: bool = True
    review_state_determined: bool = True


@dataclass(frozen=True)
class Reason:
    code: ReasonCode
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "detail": self.detail}


@dataclass(frozen=True)
class CheckResult:
    required: RequiredCheck
    outcome: CheckOutcome
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "workflow": self.required.workflow,
            "job": self.required.job,
            "outcome": self.outcome.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class FindingResult:
    finding: ReviewFinding
    classification: FindingClassification
    blocking: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding.finding_id,
            "author": self.finding.author,
            "commit_sha": self.finding.commit_sha,
            "thread_resolved": self.finding.thread_resolved,
            "classification": self.classification.value,
            "blocking": self.blocking,
            "detail": self.detail,
            "excerpt": self.finding.excerpt,
        }


@dataclass(frozen=True)
class QualificationReport:
    """Head-bound evidence. It authorises exactly one commit and no other."""

    repo: str
    pr_number: int
    head_sha: str | None
    verdict: Verdict
    reasons: tuple[Reason, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    findings: tuple[FindingResult, ...] = ()
    generated_at: str = ""
    schema_version: int = SCHEMA_VERSION
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return self.verdict is Verdict.READY

    @property
    def reason_codes(self) -> tuple[ReasonCode, ...]:
        return tuple(reason.code for reason in self.reasons)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "repo": self.repo,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "verdict": self.verdict.value,
            "generated_at": self.generated_at,
            "reasons": [reason.to_dict() for reason in self.reasons],
            "checks": [check.to_dict() for check in self.checks],
            "findings": [finding.to_dict() for finding in self.findings],
            "notes": list(self.notes),
        }
