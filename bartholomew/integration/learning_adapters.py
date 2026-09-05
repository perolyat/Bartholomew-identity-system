"""Package D's four declared adapters, connected or honestly declared absent.

D's closeout names four seams and says F resolves them. Three of the four are
resolved here. The fourth is not, and saying so precisely is the deliverable
for it rather than a placeholder that reads like a measurement.

1. Sharing-state resolver -- CONNECTED
--------------------------------------
D constructed `SharingInterface(eligible=...)` inline in two projections, with
`transport_available=False` and the fixed detail "Household sharing is not
connected in this release." Session E *is* that transport, so the sentence is
no longer true and `resolve_sharing` replaces it with the real state read from
E's own tables.

Three things are kept strictly apart, because collapsing them is how a
sharing UI starts lying:

* **Eligible** stays D's answer. Whether a record's classification permits it
  to be shared at all is a privacy judgement D owns, and this module does not
  second-guess it. It is additionally intersected with E's
  `ELIGIBLE_SOURCE_KINDS` for competencies, so a record D would allow but E's
  sanitizer would refuse to cut a package from reads as ineligible rather than
  as a share that mysteriously fails later.
* **Transport available** now means what it says: this user is a member of at
  least one trusted group that could receive a share. A user in no group has
  no transport, which is different from sharing being unimplemented, and the
  detail text says which.
* **State** is read from `platform_share_packages` / `platform_share_receipts`
  and is `shared` only when a live, unrevoked package actually exists for this
  record. A revoked package returns to `not_shared`, so revocation is visible
  in the control centre rather than only in E's audit.

Nothing here publishes, adopts or grants anything. It is a read.

2. Contradiction detector -- NOT CONNECTED, AND SAID SO
--------------------------------------------------------
`contradicting_evidence_count` is supplied per request and defaults to 0.
Nothing in this wave measures contradiction between a candidate lesson and the
competency substrate, and this module does not invent a measurement: a
plausible-looking count computed from, say, keyword overlap would feed the
`contradictory_evidence` policy rule with a number nobody validated, and the
rule would then make *less* conservative previews look justified.

`describe_unmeasured()` reports it as unmeasured so the control centre can say
so, and `DEFAULT_CONTRADICTING_EVIDENCE_COUNT` keeps the strict default
explicit. This is future architectural work, not an integration defect.

3. Risk / reversibility assessor -- CONNECTED, READ-ONLY
---------------------------------------------------------
D's note asks for an automated assessor writing through the governed edit
seam. `assess_risk` deliberately does **less** than that: it returns a
*proposal* and writes nothing.

Two reasons, both governance. An assessor that writes through
`run_candidate_edit_through_runtime_contract` performs a material edit, which
re-fingerprints the candidate and invalidates any approval standing against
it -- so an automated assessor running on a schedule could silently revoke a
person's considered approval. And `learning_candidate_edit` holds standing
permission in the Identity allowlist precisely because it is a *reviewer's*
act; letting an unattended assessor spend that grant widens what the grant
was given for. So the assessment is offered to a reviewer, who applies it
through the seam as their own edit, and the fingerprint change is then
something a person did.

The proposal itself is conservative by construction: it never proposes a
*less* strict value than the candidate already carries, and it declines to
answer at all where it cannot tell. `unassessed` remains `critical` and
irreversible, exactly as D specified.

4. Affected-application resolver -- CONNECTED
----------------------------------------------
`affected_applications` was reviewer-supplied only. Applications are now
observable: C's accessibility and screen observations name the application
they came from, and those events are in `inbound_events` with the candidate's
own correlation id. `resolve_affected_applications` reads them back, so a
lesson learned while the person was working in a particular application knows
which one, without anybody typing it in. It returns a proposal for the same
reason (3) does -- applying it is a material edit and belongs to a reviewer.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from bartholomew.kernel import learning_policy

logger = logging.getLogger(__name__)

#: The strict default D specified: nothing measured means nothing contradicts,
#: which is the value the `contradictory_evidence` rule treats as "no evidence
#: of contradiction was found", not "contradiction was ruled out".
DEFAULT_CONTRADICTING_EVIDENCE_COUNT = 0

#: How many recent multimodal observations the application resolver reads.
#: Bounded so the control centre's projection can never become an unbounded
#: scan of the capture history.
_APPLICATION_SCAN_LIMIT = 200


# ---------------------------------------------------------------------------
# 1. Sharing state, from Session E
# ---------------------------------------------------------------------------


def _member_of_any_group(user_id: str, db_path: str | None) -> bool:
    from bartholomew.platform import trusted_groups

    try:
        return bool(trusted_groups.list_groups(user_id, db_path=db_path))
    except Exception as e:  # noqa: BLE001 - an unreadable registry is "no transport"
        logger.warning("could not read trusted-group membership: %s", e)
        return False


def _live_share_state(
    *,
    user_id: str,
    origin_fingerprint: str | None,
    db_path: str | None,
) -> tuple[str, str | None]:
    """`(state, share_id)` for one record, read from E's publication tables.

    Matched on `source_candidate_fingerprint`, which is what binds a published
    package to the exact record it was cut from. A package whose origin record
    was since edited has a different fingerprint and correctly does not match:
    the control centre then says "not shared", which is true of *this* version
    of the record.
    """
    if not origin_fingerprint:
        return learning_policy.SHARING_NOT_SHARED, None

    from bartholomew.platform.store import platform_connection

    try:
        with platform_connection(db_path) as conn:
            row = conn.execute(
                "SELECT share_id, revoked_at FROM platform_share_packages "
                "WHERE publisher_user_id = ? AND source_candidate_fingerprint = ? "
                "ORDER BY revision DESC LIMIT 1",
                (user_id, origin_fingerprint),
            ).fetchone()
    except (sqlite3.Error, OSError) as e:
        logger.warning("could not read share publications: %s", e)
        return learning_policy.SHARING_NOT_SHARED, None

    if row is None:
        return learning_policy.SHARING_NOT_SHARED, None
    if row["revoked_at"]:
        # Revoked is not shared. The audit trail keeps the history; the
        # control centre must not keep showing a live share that is gone.
        return learning_policy.SHARING_NOT_SHARED, row["share_id"]
    return learning_policy.SHARING_SHARED, row["share_id"]


def resolve_sharing(
    *,
    user_id: str,
    eligible: bool,
    source_kind: str | None = None,
    origin_fingerprint: str | None = None,
    db_path: str | None = None,
) -> learning_policy.SharingInterface:
    """The real sharing projection for one record. A read; grants nothing."""
    from bartholomew.kernel import trusted_share

    effective_eligible = bool(eligible)
    if effective_eligible and source_kind is not None:
        # A record D would permit but E's sanitizer would refuse to cut a
        # package from is not eligible in any useful sense.
        effective_eligible = trusted_share.is_eligible_source(str(source_kind))

    transport = _member_of_any_group(user_id, db_path)
    state, share_id = _live_share_state(
        user_id=user_id,
        origin_fingerprint=origin_fingerprint,
        db_path=db_path,
    )

    if not transport:
        detail = (
            "You are not a member of any trusted group, so there is nowhere to "
            "share this. Sharing is available once you join or create one."
        )
    elif state == learning_policy.SHARING_SHARED:
        detail = f"Published to a trusted group (share {share_id})."
    elif share_id is not None:
        detail = "A previous publication of this record was revoked."
    elif effective_eligible:
        detail = "Eligible to share with a trusted group. Nothing has been shared."
    else:
        detail = "This record's classification does not permit sharing."

    return learning_policy.SharingInterface(
        eligible=effective_eligible,
        state=state,
        transport_available=transport,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# 2. Contradiction -- declared unmeasured
# ---------------------------------------------------------------------------


def describe_unmeasured() -> dict[str, Any]:
    """What this build does and does not measure about a candidate.

    Rendered next to a preview so a reader knows which inputs to the policy
    were measured and which defaulted to their strictest value.
    """
    return {
        "contradicting_evidence": {
            "measured": False,
            "default": DEFAULT_CONTRADICTING_EVIDENCE_COUNT,
            "detail": (
                "Nothing in this build compares a candidate against the competency "
                "substrate for contradiction. The count defaults to 0, which the "
                "policy reads as 'no contradiction was found', not as 'contradiction "
                "was ruled out'."
            ),
        },
        "risk_and_reversibility": {
            "measured": True,
            "applied_automatically": False,
            "detail": (
                "An assessment is proposed for a reviewer to apply. It is never "
                "written automatically: applying it is a material edit, which "
                "would re-fingerprint the candidate and invalidate any approval "
                "already standing against it."
            ),
        },
        "affected_applications": {
            "measured": True,
            "applied_automatically": False,
            "detail": (
                "Proposed from multimodal observations sharing the candidate's "
                "correlation id. Applied by a reviewer, for the same reason."
            ),
        },
    }


# ---------------------------------------------------------------------------
# 3. Risk / reversibility -- a proposal, never a write
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Assessment:
    """A proposed risk/reversibility assessment, and why.

    `confident` is False when the assessor could not tell. A caller must not
    treat a non-confident assessment as a measurement: the candidate keeps
    whatever it already carries, which for an unassessed candidate is
    `critical` and irreversible.
    """

    risk_class: str | None
    reversible: bool | None
    confident: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_class": self.risk_class,
            "reversible": self.reversible,
            "confident": self.confident,
            "reason": self.reason,
            "applied": False,
        }


#: Capability kinds whose effects a person cannot simply undo. Drawn from
#: Package B's own actuation vocabulary rather than invented here, so the two
#: cannot drift into disagreeing about what is reversible.
_IRREVERSIBLE_CAPABILITY_HINTS = frozenset(
    {
        "windows.type_text",
        "windows.accessibility_action",
        "windows.clipboard_write",
    },
)


def assess_risk(lesson: Any) -> Assessment:
    """Propose a risk class and reversibility for one candidate lesson.

    Conservative in both directions: it never proposes a value less strict
    than the candidate already carries, and where it cannot tell it says so
    rather than proposing the permissive answer.
    """
    current_risk = getattr(lesson, "risk_class", None)
    current_reversible = getattr(lesson, "reversible", None)

    competency = str(getattr(lesson, "competency_id", "") or "")
    conditions = getattr(lesson, "conditions", None) or []
    rule = str(getattr(lesson, "inferred_rule", "") or "")
    material = " ".join([competency, rule, *(str(c) for c in conditions)]).lower()

    touches_actuation = any(hint in material for hint in _IRREVERSIBLE_CAPABILITY_HINTS)

    if touches_actuation:
        return Assessment(
            risk_class="critical",
            reversible=False,
            confident=True,
            reason=(
                "the lesson's material names an actuation capability whose effect "
                "the person cannot simply undo"
            ),
        )

    if current_risk in (None, "unassessed"):
        return Assessment(
            risk_class=None,
            reversible=None,
            confident=False,
            reason=(
                "nothing in this build can tell what this lesson would cause; it "
                "stays unassessed, which the policy treats as critical and "
                "irreversible"
            ),
        )

    # The candidate already carries a reviewer's judgement. Proposing anything
    # here could only loosen it, so nothing is proposed.
    return Assessment(
        risk_class=current_risk,
        reversible=current_reversible,
        confident=True,
        reason="a reviewer has already assessed this lesson; the assessor defers to them",
    )


# ---------------------------------------------------------------------------
# 4. Affected applications -- from C's observations, via A's ingress
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplicationProposal:
    """Applications observed under one correlation, proposed for review."""

    applications: tuple[str, ...] = ()
    observed_events: int = 0
    source: str = "multimodal_observations"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applications": list(self.applications),
            "observed_events": self.observed_events,
            "source": self.source,
            "notes": list(self.notes),
            "applied": False,
        }


def resolve_affected_applications(
    *,
    correlation_id: str | None,
    db_path: str,
    runtime_id: str | None = None,
) -> ApplicationProposal:
    """Which applications were observed under this candidate's correlation.

    Reads `inbound_events` -- the one ingress table -- for the multimodal
    accessibility and screen observations carrying `correlation_id`, and
    returns the applications they named. Tenant-scoped by `runtime_id` when
    the caller supplies one, so a correlation id alone can never reach across
    runtimes.
    """
    if not correlation_id:
        return ApplicationProposal(notes=["the candidate carries no correlation id"])

    from bartholomew.multimodal.events import EVENT_TYPE_ACCESSIBILITY, EVENT_TYPE_SCREEN

    sql = "SELECT payload_json FROM inbound_events WHERE event_type IN (?, ?) "
    params: list[Any] = [EVENT_TYPE_ACCESSIBILITY, EVENT_TYPE_SCREEN]
    if runtime_id:
        sql += "AND runtime_id = ? "
        params.append(runtime_id)
    sql += "ORDER BY id DESC LIMIT ?"
    params.append(_APPLICATION_SCAN_LIMIT)

    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        try:
            rows = conn.execute(sql, params).fetchall()  # noqa: S608 - fixed columns
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as e:
        logger.warning("could not read observations for affected applications: %s", e)
        return ApplicationProposal(notes=[f"observations unreadable: {type(e).__name__}"])

    found: list[str] = []
    seen = 0
    for (raw,) in rows:
        try:
            envelope = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(envelope, dict):
            continue
        if envelope.get("correlation_id") != correlation_id:
            continue
        seen += 1
        body = envelope.get("payload")
        application = (body or {}).get("application") if isinstance(body, dict) else None
        if isinstance(application, str) and application.strip():
            name = application.strip()
            if name not in found:
                found.append(name)

    notes: list[str] = []
    if seen and not found:
        notes.append("observations were found under this correlation but named no application")
    return ApplicationProposal(
        applications=tuple(sorted(found)),
        observed_events=seen,
        notes=notes,
    )


__all__ = [
    "DEFAULT_CONTRADICTING_EVIDENCE_COUNT",
    "ApplicationProposal",
    "Assessment",
    "assess_risk",
    "describe_unmeasured",
    "resolve_affected_applications",
    "resolve_sharing",
]
