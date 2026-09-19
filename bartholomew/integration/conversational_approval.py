"""
R-EXEC02-2: a proposal surfaced in conversation can be authorised or withdrawn
in that same conversation --- through the approval authority that already
exists, and through no other.

An adapter, like every module in this package. It owns no authority. It does
exactly three things:

1. **Records what was shown.** When the Executive proposes a step and chat
   presents it to the person, this module writes a *presentation record*: which
   action, on which device, under which tenant, at whose request, and --- the
   load-bearing part --- the `parameter_fingerprint` of the proposal **as the
   person saw it**.
2. **Resolves "yes" to one proposal, or refuses to.** A bare affirmative is
   turned into an action id only when exactly one live presentation exists, it
   belongs to this conversation, and the action it names is still, byte for
   byte, the action that was described. Anything else fails safe.
3. **Hands the decision to the authority.** `actuation.seam.grant_action_approval`
   for an approval; `actuation.seam.cancel_action_through_runtime_contract` for
   a withdrawal. Both are the same calls `POST /api/operator/actions/{id}/approve`
   and `.../cancel` make, with the same contracts and the same governance.

The presentation record is evidence. It is not an approval
-----------------------------------------------------------
This is the distinction the whole module turns on, so it is worth being blunt
about what the record can and cannot do.

**It cannot authorise anything.** Nothing downstream of here reads it. The
action envelope does not know it exists; `evaluate_dispatch_admission` loads an
`ActionApproval` written by the authority and checks *that*. Delete every
presentation record in the database and no approved action becomes
unapprovable, no pending action becomes dispatchable, and no lease changes.
Forge one --- write a row naming an action nobody was ever shown --- and the
only thing it buys is the right to *ask* the authority, which then re-reads the
action, re-validates it against the device's current allowlists, re-reads the
Parking Brake, checks the state is `pending_approval`, and refuses if any of
that fails. It is a pointer, held so that a pronoun in a sentence can be
resolved to a noun.

**What it does do is narrow.** It makes "yes" mean one specific proposal that
one specific person was actually shown, rather than "whatever is pending". That
is a restriction the operator console does not need, because the console shows
a list and the person clicks a row. Conversation has no row to click, so the
row has to be remembered.

One live presentation per conversational surface
-------------------------------------------------
Keyed on `tenant::device::requested_by` --- the three pieces of platform
authority `conversational_executive.ConversationalExecutive` holds, which are
the only ownership dimensions this deployment actually has. There is no
multi-user, multi-session or multi-device conversational routing in this build
(R-EXEC02-3), and this module does not invent one: it checks the dimensions
that exist and would refuse a record from a surface that did not match.

A single slot means a second proposal *supersedes* the first rather than
joining it, so "yes" is never ambiguous between two remembered proposals. When
a single Executive pass proposes more than one action --- which today it does
not, because a step is proposed only once its predecessor has verified --- no
presentation is armed at all and the person is sent to the console. Ambiguity
fails safe rather than picking the most recent.

What is impossible by construction
-----------------------------------
* A model cannot mint an approval: nothing in this module consults one, and the
  decision it acts on is a whole-utterance match performed by
  `kernel/approval_intents.py` on the *person's* words.
* Chat cannot execute anything: this module imports `seam.grant_action_approval`
  and `seam.cancel_action_through_runtime_contract` and nothing else from the
  actuation package. There is no import of `bartholomew.windows_actuation`, no
  call to `try_lease`, no call to any dispatch or result-recording path.
  `tests/test_conversational_approval_no_bypass.py` proves that from this
  module's syntax tree rather than from this paragraph.
* A decision cannot be replayed into a second execution: consumption is the
  authority's conditional `pending_approval -> approved` UPDATE, not anything
  here. If this module's own bookkeeping write fails after a successful
  approval, a repeated "yes" still refuses --- the action is no longer awaiting
  approval, and the authority says so.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, replace
from typing import Any

from bartholomew.actuation import seam as action_seam
from bartholomew.actuation import store as action_store
from bartholomew.actuation.approval import SURFACE_CONVERSATION
from bartholomew.actuation.capabilities import CONVERSATIONAL_APPROVAL_INELIGIBLE
from bartholomew.actuation.request import parse_iso, to_iso, utc_now
from bartholomew.actuation.result import ErrorCategory
from bartholomew.actuation.store import ActionState
from bartholomew.kernel import approval_intents
from bartholomew.kernel.blocking_executor import run_off_loop
from bartholomew.kernel.memory.privacy_guard import register_structural_schema

logger = logging.getLogger(__name__)

#: The `MemoryStore` kind a presentation record is stored under.
#:
#: Like `actuation.approval.KIND` and `learning_authorization.KIND`,
#: deliberately absent from the competency kinds: this is a governance-adjacent
#: record of what a person was shown, never reasoning material, and it must
#: never come back as recalled knowledge that could influence a plan.
KIND: str = "conversational_action_presentation"

#: A presentation is awaiting the person's decision.
STATE_AWAITING = "awaiting_decision"
#: The person approved it, and the authority recorded the approval.
STATE_APPROVED = "approved"
#: The person withdrew it. Kept, not deleted --- a decision to say no is
#: evidence of what happened, and erasing it would leave an audit unable to
#: tell a refusal from a conversation that never took place.
STATE_REJECTED = "rejected"
#: A newer proposal was put to the person, so this one is no longer what "yes"
#: means. Written **before** the replacement is attempted, so that a failure to
#: arm the new one cannot leave the old one answering for it. See
#: `present_proposal`.
STATE_SUPERSEDED = "superseded"

#: Outcome tokens this module reports to the chat surface. Distinct values,
#: because "nothing is pending", "it changed since I showed you" and "the brake
#: is on" are three different non-events and must never render alike.
OUTCOME_APPROVED = "approved"
OUTCOME_REJECTED = "rejected"
OUTCOME_NOT_ARMED = "not_armed"
OUTCOME_AMBIGUOUS = "ambiguous"
OUTCOME_STALE = "stale"
OUTCOME_REFUSED = "refused"
OUTCOME_BRAKE = "parking_brake_denied"
OUTCOME_INELIGIBLE = "ineligible"
OUTCOME_STATUS = "status"
OUTCOME_NOTHING_TO_REPORT = "nothing_to_report"
OUTCOME_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PresentedProposal:
    """One proposal, as it was described to one person, in one conversation.

    Every field except `presented_at`, `presentation_id` and `instruction` is
    copied from the stored action at the moment of presentation, so a later
    comparison is between what the person saw and what now exists --- never
    between what the person saw and what somebody says they saw.
    """

    presentation_id: str
    tenant_id: str
    device_id: str
    requested_by: str
    action_id: str
    capability: str
    capability_version: int
    parameter_fingerprint: str
    action_expires_at: str
    presented_at: str
    state: str = STATE_AWAITING
    #: The person's own words that opened this, bounded. Carried so an audit can
    #: read "they asked for X, they were shown Y, they said Z" as one story.
    instruction: str = ""
    #: Set once a decision is taken: the verbatim utterance, when, and what the
    #: authority did with it. Never overwritten by a later decision --- the slot
    #: is re-armed by a *new* presentation with a new `presentation_id`.
    decision: dict[str, Any] | None = None

    @staticmethod
    def slot(*, tenant_id: str, device_id: str, requested_by: str) -> str:
        """The conversational surface this presentation belongs to.

        The three ownership dimensions this build has. A presentation read back
        under a slot whose parts do not match the live activation is not this
        conversation's and is refused.
        """
        return f"{tenant_id}::{device_id}::{requested_by}"

    def key(self) -> str:
        return self.slot(
            tenant_id=self.tenant_id,
            device_id=self.device_id,
            requested_by=self.requested_by,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "presentation_id": self.presentation_id,
            "tenant_id": self.tenant_id,
            "device_id": self.device_id,
            "requested_by": self.requested_by,
            "action_id": self.action_id,
            "capability": self.capability,
            "capability_version": self.capability_version,
            "parameter_fingerprint": self.parameter_fingerprint,
            "action_expires_at": self.action_expires_at,
            "presented_at": self.presented_at,
            "state": self.state,
            "instruction": self.instruction,
            "decision": self.decision,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PresentedProposal:
        return cls(
            presentation_id=str(data["presentation_id"]),
            tenant_id=str(data["tenant_id"]),
            device_id=str(data["device_id"]),
            requested_by=str(data["requested_by"]),
            action_id=str(data["action_id"]),
            capability=str(data["capability"]),
            capability_version=int(data["capability_version"]),
            parameter_fingerprint=str(data["parameter_fingerprint"]),
            action_expires_at=str(data["action_expires_at"]),
            presented_at=str(data["presented_at"]),
            state=str(data.get("state") or STATE_AWAITING),
            instruction=str(data.get("instruction") or ""),
            decision=data.get("decision"),
        )

    def to_summary_text(self) -> str:
        return (
            f"Conversational presentation of {self.capability} "
            f"v{self.capability_version} as action {self.action_id} on device "
            f"{self.device_id}, state {self.state}"
        )

    def matches(self, stored: Any) -> str | None:
        """Why the stored action is no longer what was presented, or None.

        Compared field by field rather than on the fingerprint alone. The
        fingerprint covers the parameters; the other three cover the contract
        those parameters are read under, and an approval that crossed any of
        them would be an approval of something else.
        """
        if stored.action_id != self.action_id:
            return "that is a different action from the one I showed you."
        if stored.device_id != self.device_id:
            return "that proposal now names a different machine from the one I showed you."
        if stored.capability != self.capability:
            return "that proposal now names a different capability from the one I described."
        if stored.capability_version != self.capability_version:
            return (
                "that proposal is now under a different version of the capability "
                "contract than the one I described."
            )
        if stored.parameter_fingerprint != self.parameter_fingerprint:
            return (
                "the details of that proposal have changed since I described it to "
                "you, so your reply would be approving something you have not seen."
            )
        return None


@dataclass(frozen=True)
class DecisionResult:
    """What the conversational surface should record and say. Nothing more."""

    outcome: str
    reply: str
    action_id: str | None = None
    reason: str | None = None
    #: The action's state after the decision, read from the action store. Never
    #: inferred from the fact that an approval succeeded.
    action_state: str | None = None
    #: True only when a *human authority decision* was recorded by the
    #: authority on this pass. `executed` and `verified` are deliberately not
    #: fields here at all: this module cannot make either true and must not
    #: offer a shape that suggests it could.
    decision_recorded: bool = False
    #: True when the withdrawal landed *after* a device had already leased the
    #: action. The withdrawal still holds -- it can never be leased again and no
    #: result will be recorded -- but it did not prevent execution, and nothing
    #: on this surface may imply that it did.
    withdrawn_after_lease: bool = False
    provenance_degraded: bool = False
    provenance_error: str | None = None


PRESENTATION_SCHEMA_KEYS: frozenset[str] = frozenset(
    {
        "presentation_id",
        "tenant_id",
        "device_id",
        "requested_by",
        "action_id",
        "capability",
        "capability_version",
        "parameter_fingerprint",
        "action_expires_at",
        "presented_at",
        "state",
        "decision",
        "decided_at",
        "outcome",
    },
)
# `instruction` and `utterance` are deliberately *not* registered: they are the
# person's own words, so they are content and must be scanned by the privacy
# guard like any other content. A presentation that the guard refuses to store
# is a presentation that is not armed, which is the safe direction.
register_structural_schema(KIND, PRESENTATION_SCHEMA_KEYS)


# ---------------------------------------------------------------------------
# Persistence -- through `MemoryStore`, the same authority the approval uses
# ---------------------------------------------------------------------------


def _db_path(ctx: Any) -> str:
    path = getattr(getattr(ctx, "mem", None), "db_path", None) or getattr(ctx, "db_path", None)
    if not path:
        raise ValueError("no database path is reachable on this runtime context")
    return str(path)


async def _write(ctx: Any, record: PresentedProposal) -> str | None:
    """Persist a presentation. Returns an error string, or None on success.

    A `StoreResult` that does not report `stored` is an error, not a shrug: a
    presentation that was not written must never be treated as one that was, or
    the person would be told they can reply "yes" to something that will not be
    there when they do.
    """
    mem = getattr(ctx, "mem", None)
    if mem is None:
        return "no memory store is reachable, so what I showed you cannot be recorded"
    try:
        result = await mem.upsert_memory(
            KIND,
            record.key(),
            json.dumps(record.to_dict(), sort_keys=True),
            record.presented_at,
            summary=record.to_summary_text(),
        )
    except Exception as e:  # noqa: BLE001 - reported, never swallowed
        logger.exception("Could not record a conversational proposal presentation")
        return f"the proposal could not be recorded: {type(e).__name__}: {e}"
    if not getattr(result, "stored", False):
        return (
            "the proposal could not be recorded (refused by the memory store's own "
            "policy or consent gate)"
        )
    return None


async def load_presentation(ctx: Any, config: Any) -> PresentedProposal | None:
    """The live presentation for this conversational surface, or None.

    A read that fails is None --- "nothing is pending" --- which refuses. A
    presentation that cannot be read is not a presentation.
    """
    mem = getattr(ctx, "mem", None)
    if mem is None:
        return None
    key = PresentedProposal.slot(
        tenant_id=config.tenant_id,
        device_id=config.device_id,
        requested_by=config.requested_by,
    )
    try:
        row = await mem.get_memory(KIND, key)
    except Exception:
        logger.exception("Could not read the conversational proposal presentation")
        return None
    if not row:
        return None
    try:
        record = PresentedProposal.from_dict(json.loads(row["value"]))
    except (TypeError, ValueError, KeyError):
        logger.warning("The recorded conversational presentation is not parseable")
        return None
    # Belt and braces against a row read under the wrong slot: the ownership
    # dimensions are re-checked against the live activation rather than trusted
    # because the key matched.
    if (
        record.tenant_id != config.tenant_id
        or record.device_id != config.device_id
        or record.requested_by != config.requested_by
    ):
        logger.warning("A conversational presentation did not match its own slot; ignoring it")
        return None
    return record


# ---------------------------------------------------------------------------
# Arming: recording what the person was actually shown
# ---------------------------------------------------------------------------


async def present_proposal(
    ctx: Any,
    config: Any,
    *,
    action_ids: list[str],
    instruction: str,
) -> tuple[PresentedProposal | None, str | None]:
    """Record the proposal this turn put in front of the person.

    Returns `(record, reason_it_was_not_armed)`. Exactly one of the two is
    populated. A `None` record is not an error the person needs to see as a
    failure --- the proposal still exists and the operator console can still
    approve it --- but it does mean conversation cannot.

    **Not armed when the number of proposals is anything but one.** Zero is
    nothing to approve. Two or more is an ambiguity, and this seam resolves
    ambiguity by declining rather than by choosing.
    """
    # **Retire whatever was awaiting a decision first, before anything can fail.**
    #
    # Every exit below this point except a successful arm leaves the surface with
    # no live presentation, and that ordering is the whole of the correctness
    # here. An earlier cut returned early on each failure without touching the
    # existing record, so if proposal A was awaiting a decision and presenting
    # proposal B failed --- two steps at once, an unreadable row, a `MemoryStore`
    # write the consent gate refused --- the slot still pointed at A. The person,
    # having just been shown B, could then say "yes" and authorise A: an older
    # proposal answering for a newer one, which is precisely the binding failure
    # this module's docstring claims to prevent. Raised by automated review on
    # PR #117, confirmed, and pinned by
    # `TestBindingToWhatWasShown::test_a_failed_re_arm_retires_the_older_proposal`.
    #
    # Superseding is safe in every case, including the ones that go on to arm
    # successfully: the new record overwrites this one in the same slot. And it
    # takes nothing away --- the action itself is untouched and the operator
    # console can still approve either.
    retired = await _supersede_awaiting(ctx, config)

    ids = [str(a) for a in (action_ids or []) if a]
    if len(ids) != 1:
        return None, (
            "nothing was proposed" if not ids else f"{len(ids)} steps were proposed at once"
        )

    action_id = ids[0]
    if retired is not None:
        logger.info(
            "Conversational approval for %s was retired; %s is being presented instead",
            retired,
            action_id,
        )
    try:
        stored = await run_off_loop(
            action_store.get_action,
            _db_path(ctx),
            tenant_id=config.tenant_id,
            action_id=action_id,
        )
    except Exception:  # noqa: BLE001 - an unreadable action is an unarmed one
        logger.exception("Could not read the proposed action back to present it")
        return None, "the proposal could not be read back"
    if stored is None:
        return None, "the proposal could not be read back"
    if stored.state is not ActionState.PENDING_APPROVAL:
        # Nothing to authorise. Arming would promise the person a decision the
        # authority would then refuse.
        return None, f"the proposal is already {stored.state.value}"

    record = PresentedProposal(
        presentation_id=f"pres-{uuid.uuid4().hex[:16]}",
        tenant_id=config.tenant_id,
        device_id=config.device_id,
        requested_by=config.requested_by,
        action_id=stored.action_id,
        capability=stored.capability,
        capability_version=stored.capability_version,
        parameter_fingerprint=stored.parameter_fingerprint,
        action_expires_at=stored.expires_at,
        presented_at=to_iso(utc_now()),
        instruction=(instruction or "")[:400],
    )
    error = await _write(ctx, record)
    if error is not None:
        return None, error
    return record, None


async def _supersede_awaiting(ctx: Any, config: Any) -> str | None:
    """Retire the live presentation, if there is one. Returns its action id.

    Called at the top of `present_proposal`, so the window in which an old
    proposal can answer for a new one is closed before any of the ways of
    failing to present the new one can be reached.

    A failure to write the supersession is logged rather than raised, and the
    consequence is stated plainly because it is the one residual case: the older
    record stays awaiting. It is bounded by the action's own expiry, which
    `decide` re-checks, and it is still an action the person was genuinely shown
    --- but it is not the one they were shown last, so it is worth a loud log.
    """
    existing = await load_presentation(ctx, config)
    if existing is None or existing.state != STATE_AWAITING:
        return None
    superseded = replace(existing, state=STATE_SUPERSEDED)
    error = await _write(ctx, superseded)
    if error is not None:
        logger.error(
            "The conversational presentation of %s could not be retired (%s); it may "
            "still answer a bare 'yes' until the action expires",
            existing.action_id,
            error,
        )
        return None
    return existing.action_id


async def _record_decision(
    ctx: Any,
    record: PresentedProposal,
    *,
    state: str,
    decision: Any,
    outcome: str,
    reason: str | None,
) -> None:
    """Close the presentation with what the person decided. Best effort.

    Best effort **deliberately**, and it is worth saying why that is safe. This
    write is bookkeeping, not consumption: the thing that makes an approval
    one-shot is the authority's conditional `pending_approval -> approved`
    UPDATE, which has already happened by the time this runs. If this write is
    lost, a repeated "yes" reaches the authority again and is refused there for
    the action no longer awaiting approval. The record would be incomplete; it
    would never be permissive.
    """
    decided = PresentedProposal(
        presentation_id=record.presentation_id,
        tenant_id=record.tenant_id,
        device_id=record.device_id,
        requested_by=record.requested_by,
        action_id=record.action_id,
        capability=record.capability,
        capability_version=record.capability_version,
        parameter_fingerprint=record.parameter_fingerprint,
        action_expires_at=record.action_expires_at,
        presented_at=record.presented_at,
        state=state,
        instruction=record.instruction,
        decision={
            "decision": decision.kind,
            # The person's own words, verbatim and bounded. This is the evidence
            # of the human authority decision, and a paraphrase of it would be
            # Bartholomew's account of what he was told rather than what he was
            # told.
            "utterance": decision.utterance[:200],
            "matched": decision.matched,
            "decided_at": to_iso(utc_now()),
            "outcome": outcome,
            **({"reason": reason} if reason else {}),
        },
    )
    error = await _write(ctx, decided)
    if error is not None:
        logger.error(
            "The conversational decision on %s was carried out but not recorded: %s",
            record.action_id,
            error,
        )


# ---------------------------------------------------------------------------
# Deciding: the person's word, carried to the authority
# ---------------------------------------------------------------------------


def _expired(record: PresentedProposal, stored: Any) -> bool:
    """Whether the action has run out of time. Unreadable means expired."""
    for raw in (stored.expires_at, record.action_expires_at):
        try:
            if utc_now() >= parse_iso(str(raw)):
                return True
        except ValueError:
            return True
    return False


async def _resolve_target(
    ctx: Any,
    config: Any,
) -> tuple[PresentedProposal | None, Any, DecisionResult | None]:
    """The one proposal this conversation may decide about, or a refusal.

    Every path out of here that is not a resolved target is a refusal that
    explains itself. There is no branch that picks a plausible candidate.
    """
    record = await load_presentation(ctx, config)
    if record is None:
        return (
            None,
            None,
            DecisionResult(
                outcome=OUTCOME_NOT_ARMED,
                reply=approval_intents.render_not_armed(),
            ),
        )
    if record.state != STATE_AWAITING:
        already = {
            STATE_APPROVED: "you already approved that one",
            STATE_REJECTED: "you already told me to drop that one",
            STATE_SUPERSEDED: (
                "that proposal was replaced by a newer one, and the newer one is not "
                "available for approval here"
            ),
        }.get(record.state, f"that proposal is {record.state}")
        return (
            None,
            None,
            DecisionResult(
                outcome=OUTCOME_STALE,
                reply=approval_intents.render_stale(f"{already}."),
                action_id=record.action_id,
                reason=f"the presentation is already {record.state}",
            ),
        )

    try:
        stored = await run_off_loop(
            action_store.get_action,
            _db_path(ctx),
            tenant_id=config.tenant_id,
            action_id=record.action_id,
        )
    except Exception:  # noqa: BLE001 - an unreadable action refuses
        logger.exception("Could not read the presented action back")
        return (
            None,
            None,
            DecisionResult(
                outcome=OUTCOME_STALE,
                reply=approval_intents.render_stale(
                    "I could not read that proposal back, so I will not act on it.",
                ),
                action_id=record.action_id,
                reason="the action could not be read",
            ),
        )
    if stored is None:
        return (
            None,
            None,
            DecisionResult(
                outcome=OUTCOME_STALE,
                reply=approval_intents.render_stale("that proposal no longer exists."),
                action_id=record.action_id,
                reason="the action no longer exists",
            ),
        )

    changed = record.matches(stored)
    if changed is not None:
        return (
            None,
            stored,
            DecisionResult(
                outcome=OUTCOME_STALE,
                reply=approval_intents.render_stale(changed),
                action_id=record.action_id,
                action_state=stored.state.value,
                reason="the action is not the one that was presented",
            ),
        )
    return record, stored, None


async def decide(ctx: Any, config: Any, decision: Any) -> DecisionResult:
    """Carry one human decision to the one approval authority.

    The order below is the contract:

    1. resolve the target, or refuse --- ownership, existence, staleness;
    2. for an approval, check the action is still awaiting one and has not
       expired;
    3. call the authority, which re-reads the Parking Brake, re-validates the
       action against the device's *current* allowlists, enforces the surface's
       capability eligibility, and consumes the approval with a conditional
       UPDATE;
    4. record what the person said and what the authority did.

    Nothing between steps 3 and 4 can make an action run. Approval makes an
    action *eligible* to be leased by its device, at which point every gate in
    the envelope --- the brake included, read again --- runs from the start.
    """
    record, stored, refusal = await _resolve_target(ctx, config)
    if refusal is not None:
        return refusal
    assert record is not None and stored is not None  # noqa: S101 - narrowed above

    if decision.kind == approval_intents.DECISION_REJECT:
        return await _reject(ctx, config, record, decision)
    return await _approve(ctx, config, record, stored, decision)


async def _approve(
    ctx: Any,
    config: Any,
    record: PresentedProposal,
    stored: Any,
    decision: Any,
) -> DecisionResult:
    if stored.state is not ActionState.PENDING_APPROVAL:
        return DecisionResult(
            outcome=OUTCOME_STALE,
            reply=approval_intents.render_stale(
                f"that proposal is {stored.state.value} now, so there is nothing left "
                "awaiting your approval.",
            ),
            action_id=record.action_id,
            action_state=stored.state.value,
            reason=f"the action is {stored.state.value}",
        )
    if _expired(record, stored):
        return DecisionResult(
            outcome=OUTCOME_STALE,
            reply=approval_intents.render_stale(
                "that proposal expired before you replied. Ask me again and I'll "
                "work it out afresh.",
            ),
            action_id=record.action_id,
            action_state=stored.state.value,
            reason="the action expired",
        )

    result = await action_seam.grant_action_approval(
        ctx,
        tenant_id=config.tenant_id,
        action_id=record.action_id,
        # The configured principal for this conversational surface. Not "chat",
        # unless that is genuinely what the deployment configured, and never a
        # synthesised or inferred identity: `build_approval` refuses an
        # anonymous approver and this module has no other name to offer.
        approver=config.requested_by,
        # The person's own words, on the approval record itself. An audit
        # reading "approved by X, surface conversation, note 'yes, go ahead'"
        # can see the human act; it is not reconstructing it from a timestamp.
        note=f"said in conversation: {decision.utterance}"[:280],
        surface=SURFACE_CONVERSATION,
    )

    if result.governance_allowed:
        state = getattr(getattr(result, "action", None), "state", None)
        outcome_state = getattr(state, "value", None) or ActionState.APPROVED.value
        await _record_decision(
            ctx,
            record,
            state=STATE_APPROVED,
            decision=decision,
            outcome=OUTCOME_APPROVED,
            reason=None,
        )
        return DecisionResult(
            outcome=OUTCOME_APPROVED,
            reply=approval_intents.render_approved(record.action_id, record.device_id),
            action_id=record.action_id,
            action_state=outcome_state,
            decision_recorded=True,
            provenance_degraded=bool(getattr(result, "provenance_degraded", False)),
            provenance_error=getattr(result, "provenance_error", None),
        )

    reason = result.reason or "the approval authority refused it"
    category = getattr(result, "category", None)
    if category is ErrorCategory.PARKING_BRAKE:
        return DecisionResult(
            outcome=OUTCOME_BRAKE,
            reply=approval_intents.render_brake(),
            action_id=record.action_id,
            reason=reason,
        )
    # The authority is the enforcer of surface eligibility (see
    # `seam.grant_action_approval`); this branch only decides how its refusal
    # reads, and it is conditioned on the same closed set the authority uses
    # rather than on the text of the message.
    if stored.capability in {k.value for k in CONVERSATIONAL_APPROVAL_INELIGIBLE}:
        return DecisionResult(
            outcome=OUTCOME_INELIGIBLE,
            reply=approval_intents.render_ineligible(reason),
            action_id=record.action_id,
            reason=reason,
        )
    return DecisionResult(
        outcome=OUTCOME_REFUSED,
        reply=approval_intents.render_refused(reason),
        action_id=record.action_id,
        reason=reason,
    )


async def _reject(
    ctx: Any,
    config: Any,
    record: PresentedProposal,
    decision: Any,
) -> DecisionResult:
    """Withdraw the proposal, through the same seam the console cancels with.

    Deliberately not gated on anything the approval path is gated on beyond the
    authority's own Identity check. Withdrawing only ever removes an action's
    ability to happen, and a gate that stopped somebody saying no would be a
    gate that made things less safe.
    """
    # **Read the state before withdrawing it, because withdrawing overwrites it.**
    #
    # `store.mark_cancelled` accepts a `leased` action deliberately, and its own
    # docstring is explicit that cancelling one "does not reach out and stop a
    # device -- nothing here can". So a withdrawal that lands after a device has
    # taken the action is real but narrower than it sounds: the action can never
    # be leased again and any result it later reports is refused, but it may be
    # running as the person reads the reply.
    #
    # Once the row is `cancelled` the state column cannot say which of the two it
    # was, so the pre-state is captured here or not at all. Saying "nothing
    # happened" without it would be false reassurance about something possibly
    # underway on their machine. Raised by automated review on PR #117.
    before = await run_off_loop(
        action_store.get_action,
        _db_path(ctx),
        tenant_id=config.tenant_id,
        action_id=record.action_id,
    )
    was_leased = before is not None and before.state is ActionState.LEASED

    result = await action_seam.cancel_action_through_runtime_contract(
        ctx,
        tenant_id=config.tenant_id,
        action_id=record.action_id,
        cancelled_by=config.requested_by,
        reason=f"withdrawn in conversation: {decision.utterance}"[:200],
    )
    if result.governance_allowed:
        await _record_decision(
            ctx,
            record,
            state=STATE_REJECTED,
            decision=decision,
            outcome=OUTCOME_REJECTED,
            reason=None,
        )
        return DecisionResult(
            outcome=OUTCOME_REJECTED,
            reply=(
                approval_intents.render_rejected_after_lease(record.action_id)
                if was_leased
                else approval_intents.render_rejected(record.action_id)
            ),
            action_id=record.action_id,
            action_state=ActionState.CANCELLED.value,
            decision_recorded=True,
            #: Recorded because it changes what the withdrawal actually achieved,
            #: and an audit reader must be able to tell a withdrawal that
            #: prevented an action from one that only disowned its result.
            withdrawn_after_lease=was_leased,
            provenance_degraded=bool(getattr(result, "provenance_degraded", False)),
            provenance_error=getattr(result, "provenance_error", None),
        )

    reason = result.reason or "the action could not be withdrawn"
    return DecisionResult(
        outcome=OUTCOME_REFUSED,
        reply=approval_intents.render_refused(reason),
        action_id=record.action_id,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Reporting: what became of a decision the person already made
# ---------------------------------------------------------------------------


async def report_status(ctx: Any, config: Any) -> DecisionResult:
    """Read the action's current state back and say it. Never characterise it.

    The state comes from the action store --- the same column the operator
    console reads --- and is rendered by a table with one sentence per state.
    `unknown` has its own sentence and is never folded into either success or
    failure, because "the machine could not tell" is the honest answer and the
    whole result vocabulary exists to be able to give it.
    """
    record = await load_presentation(ctx, config)
    if record is None:
        return DecisionResult(
            outcome=OUTCOME_NOTHING_TO_REPORT,
            reply=approval_intents.render_nothing_to_report(),
        )
    try:
        stored = await run_off_loop(
            action_store.get_action,
            _db_path(ctx),
            tenant_id=config.tenant_id,
            action_id=record.action_id,
        )
    except Exception:  # noqa: BLE001 - an unreadable action is reported as such
        logger.exception("Could not read the presented action back for a status report")
        stored = None
    if stored is None:
        return DecisionResult(
            outcome=OUTCOME_NOTHING_TO_REPORT,
            reply=approval_intents.render_nothing_to_report(),
            action_id=record.action_id,
        )
    return DecisionResult(
        outcome=OUTCOME_STATUS,
        reply=approval_intents.render_status(record.action_id, stored.state.value),
        action_id=record.action_id,
        action_state=stored.state.value,
    )


__all__ = [
    "KIND",
    "OUTCOME_AMBIGUOUS",
    "OUTCOME_APPROVED",
    "OUTCOME_BRAKE",
    "OUTCOME_INELIGIBLE",
    "OUTCOME_NOTHING_TO_REPORT",
    "OUTCOME_NOT_ARMED",
    "OUTCOME_REFUSED",
    "OUTCOME_REJECTED",
    "OUTCOME_STALE",
    "OUTCOME_STATUS",
    "OUTCOME_UNAVAILABLE",
    "STATE_APPROVED",
    "STATE_AWAITING",
    "STATE_REJECTED",
    "STATE_SUPERSEDED",
    "DecisionResult",
    "PresentedProposal",
    "decide",
    "load_presentation",
    "present_proposal",
    "report_status",
]
