"""The Runtime Contract seam for governed Windows actuation.

Every action passes through here, and it passes through in one order. That
order is the governance contract, and each step is either a repository
authority already in use elsewhere or a check this package owns:

===  ==========================  ==========================================
 #   check                        authority
===  ==========================  ==========================================
 1   tenant                       resolved by the API boundary from the
                                  platform's own authority, never from a body
 2   requesting principal         ditto
 3   enrolled target device       `devices.DeviceCapabilityRegistry`
                                  (Session E replaces the implementation)
 4   declared capability+version  `EnrolledDevice.declares()`
 5   typed canonical parameters   `parameters.validate()`
 6   risk classification          `capabilities.describe()`
 7   expiry                       `ActionRequest.has_expired()`
 8   Parking Brake                `governance_store`, both tiers, fail-closed
 9   Identity policy              `policy_engine.evaluate_tool_policy`, on the
                                  request and cancel kinds. Deliberately not on
                                  a human approval -- see `grant_action_approval`
10   exact action approval        `approval.ActionApproval.authorizes()`
11   replay / idempotency         `store.try_lease()`, a conditional UPDATE
===  ==========================  ==========================================

**Missing, stale, mismatched or unreadable state denies.** Every `except` below
returns a denial rather than proceeding, and the two brake reads propagate or
convert to a denial. There is no branch in this module that treats "we could
not tell" as "go ahead".

**The brake is read twice, and the second read is the load-bearing one.** Once
at admission, and again immediately before the lease. An approval granted while
the brake was clear does not survive the brake being engaged afterwards, which
is what "approval never overrides the Parking Brake" means operationally rather
than aspirationally.

Why this is not inside `runtime_contract.py`
--------------------------------------------
It composes the Runtime Contract's own primitives -- `Observation`,
`Interpretation`, `CandidateAction`, `ObjectiveAdmission`, the reflection sink,
the same fail-closed Governance helpers -- rather than duplicating them, so an
action travels the same governed path as every other seam. It lives in its own
module because `runtime_contract.py` is a 4,500-line file that five concurrent
streams are editing, and because actuation is the one seam whose review value
comes from being readable end to end in one sitting. The dependency points one
way: this module imports the Runtime Contract, and the Runtime Contract does
not import this one.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bartholomew.kernel import policy_engine
from bartholomew.kernel.blocking_executor import run_off_loop
from bartholomew.kernel.reflection import ActionReflection, record_action_reflection
from bartholomew.kernel.runtime_contract import (
    CandidateAction,
    Interpretation,
    Observation,
)
from bartholomew.orchestrator.safety.governance_store import (
    engaged_state_fail_closed_off_loop,
    is_blocked_fail_closed_off_loop,
)

from . import store
from .approval import KIND as APPROVAL_KIND
from .approval import ActionApproval, ApprovalError, build_approval
from .capabilities import UnsupportedCapabilityError
from .devices import (
    SUPPORTED_PLATFORM,
    DeviceCapabilityRegistry,
    DeviceRegistryError,
    EnrolledDevice,
    get_registry,
)
from .parameters import ParameterError, SensitiveContentError
from .request import (
    ActionRequest,
    RequestError,
    build_request,
    rebuild_request,
    utc_now,
)
from .result import (
    DEVICE_REPORTABLE_STATUSES,
    ActionResult,
    ActionResultStatus,
    ErrorCategory,
)
from .store import ActionPersistenceError, ActionState, StoredAction

logger = logging.getLogger(__name__)

#: The Parking Brake scope this seam is halted by, in addition to the brake
#: being engaged at all. Registered in `platform/authority.py`'s `VALID_SCOPES`
#: and in the governance route's, so an operator can halt actuation alone
#: without halting Bartholomew's ability to think.
ACTUATION_BRAKE_SCOPE = "actuation"

#: The Identity policy kinds. `windows_action_request` and
#: `windows_action_cancel` are allowlisted in `Identity.yaml`; requesting
#: creates a pending record that does nothing, and cancelling is conservative
#: by construction.
ACTION_KIND_REQUEST = "windows_action_request"
ACTION_KIND_CANCEL = "windows_action_cancel"

#: Deliberately **absent** from `Identity.yaml`'s allowlist, and adding it
#: would not make dispatch reachable: `evaluate_dispatch_admission()` requires
#: an approval bound to the exact action regardless of the allowlist. Named
#: here so the audit trail and the reflection have a kind to record.
ACTION_KIND_DISPATCH = "windows_action_dispatch"
ACTION_KIND_APPROVE = "windows_action_approve"

#: Surface recorded on every Reflection this seam writes.
REFLECTION_SURFACE = "windows_action"

#: Why an already-ended action is refused, by the state it ended in. Truthful
#: categories rather than one shared "already ended": an audit needs to tell a
#: withdrawn action apart from a repeated one.
_TERMINAL_CATEGORY: dict[ActionState, ErrorCategory] = {
    ActionState.CANCELLED: ErrorCategory.CANCELLED,
    ActionState.REFUSED: ErrorCategory.GOVERNANCE_DENIED,
    ActionState.SUCCEEDED: ErrorCategory.REPLAY_REFUSED,
    ActionState.FAILED: ErrorCategory.REPLAY_REFUSED,
    ActionState.UNKNOWN: ErrorCategory.REPLAY_REFUSED,
}


@dataclass(frozen=True)
class ActionAdmission:
    """Whether one action may proceed, and why not if not.

    Shaped like `runtime_contract.ObjectiveAdmission` on purpose -- allowed,
    an outcome the caller reports verbatim, and a reason -- but carries an
    `ErrorCategory` as well, because an action's refusal is counted in an audit
    by cause and a free-text reason cannot be aggregated.
    """

    allowed: bool
    category: ErrorCategory | None = None
    reason: str | None = None

    @classmethod
    def deny(cls, category: ErrorCategory, reason: str) -> ActionAdmission:
        return cls(False, category, reason)

    @classmethod
    def allow(cls) -> ActionAdmission:
        return cls(True)


@dataclass
class ActionSeamResult:
    """The outcome of one trip through this seam.

    `status` is always one of the contract's seven values, and `action` is the
    durable row as it stands afterwards. `provenance_degraded` mirrors the
    posture the device and inbound seams already take: the governed decision
    happened and the row exists, but its Reflection did not persist, so the
    caller must not present it as fully recorded.
    """

    observation: Observation
    candidate_action: CandidateAction
    governance_allowed: bool
    status: ActionResultStatus
    category: ErrorCategory | None = None
    reason: str | None = None
    action: StoredAction | None = None
    request: ActionRequest | None = None
    #: True when this call landed on an action that already existed rather than
    #: creating one. The HTTP boundary needs it: a re-submission is not a
    #: creation, and one that lands on a finished action is a conflict.
    existing: bool = False
    provenance_degraded: bool = False
    provenance_error: str | None = None


def _observation(kind: str, request_like: Any) -> tuple[Observation, CandidateAction]:
    """The Runtime Contract preamble every seam builds before it decides."""
    subject = getattr(request_like, "capability", None)
    label = getattr(subject, "value", None) or str(subject or kind)
    observation = Observation(source=f"windows_action:{kind}", raw_content=label)
    interpretation = Interpretation(observation=observation, prompt=label)
    return observation, CandidateAction(kind=kind, interpretation=interpretation)


def _memory_store(db_path: str) -> Any:
    """A `MemoryStore` for a context that carries only a path.

    Imported here rather than at module scope because `memory_store` pulls in
    the encryption and privacy-guard stack, and this seam must stay importable
    by the structural tests without any of it.
    """
    from bartholomew.kernel.memory_store import MemoryStore  # noqa: PLC0415

    return MemoryStore(db_path)


def _ctx_db_path(ctx: Any) -> str:
    path = getattr(getattr(ctx, "mem", None), "db_path", None) or getattr(ctx, "db_path", None)
    if not path:
        raise ActionPersistenceError(
            "no database path is reachable from the runtime context; refusing rather "
            "than guessing where governed action state lives",
        )
    return str(path)


# ---------------------------------------------------------------------------
# Gate 8: the Parking Brake, both tiers, twice
# ---------------------------------------------------------------------------


async def evaluate_actuation_brake(ctx: Any, kind: str) -> ActionAdmission:
    """The fail-closed Parking Brake gate for actuation. Two reads, both composed.

    **Read one: engaged at all.** `engaged_state_fail_closed_off_loop()` is the
    helper objective mutation, consent resolution and inbound capture already
    use, and it composes the S8 Platform/Admin tier's "engaged at all" answer.
    Any engagement -- global, `skills`, `voice`, anything -- stops actuation.
    That is deliberately the most restrictive reading available: acting on
    somebody's computer while any part of Bartholomew is halted is exactly the
    thing a halt is for, and a scope-specific gate here would let a brake
    engaged for `voice` sit next to a companion typing into a window.

    **Read two: the `actuation` scope.** Also composed, through
    `is_blocked_fail_closed_off_loop()`, which consults the Platform tier's
    *scoped* check. Subsumed by read one for the local tier, and not redundant
    for the platform one: it is the named, greppable authority an operator
    engages to stop actuation and nothing else.

    Fails closed on either read erroring. An unreadable safety gate is not
    evidence of the absence of a safety gate.
    """
    db_path = _ctx_db_path(ctx)
    governance_store = getattr(ctx, "governance_store", None)
    executor = getattr(ctx, "blocking_executor", None)

    try:
        state = await engaged_state_fail_closed_off_loop(
            db_path,
            governance_store=governance_store,
            executor=executor,
        )
    except Exception:
        logger.exception("Governance check failed for %s; failing closed", kind)
        return ActionAdmission.deny(
            ErrorCategory.PARKING_BRAKE,
            "the parking brake state could not be read, so the action is refused",
        )
    if state.engaged:
        scopes = ", ".join(sorted(state.scopes)) or "global"
        return ActionAdmission.deny(
            ErrorCategory.PARKING_BRAKE,
            f"a parking brake or platform halt is engaged (scopes={scopes}); "
            "nothing is dispatched to any device while it is",
        )

    try:
        blocked = await is_blocked_fail_closed_off_loop(
            ACTUATION_BRAKE_SCOPE,
            db_path,
            governance_store=governance_store,
            executor=executor,
        )
    except Exception:
        logger.exception("Actuation scope check failed for %s; failing closed", kind)
        return ActionAdmission.deny(
            ErrorCategory.PARKING_BRAKE,
            "the actuation halt state could not be read, so the action is refused",
        )
    if blocked:
        return ActionAdmission.deny(
            ErrorCategory.PARKING_BRAKE,
            f"the {ACTUATION_BRAKE_SCOPE!r} parking brake scope is engaged",
        )
    return ActionAdmission.allow()


# ---------------------------------------------------------------------------
# Gates 3, 4: the enrolled device and its declared capability
# ---------------------------------------------------------------------------


def resolve_device(
    *,
    tenant_id: str,
    device_id: str,
    registry: DeviceCapabilityRegistry | None = None,
) -> tuple[EnrolledDevice | None, ActionAdmission]:
    """Look the device up, or deny. A registry that raises is a denial.

    Returns `(device, admission)`. `device` is None whenever the admission
    denies, so a caller cannot accidentally use a device it was told not to.
    """
    lookup = registry or get_registry()
    try:
        device = lookup.lookup(tenant_id=tenant_id, device_id=device_id)
    except DeviceRegistryError as e:
        return None, ActionAdmission.deny(
            ErrorCategory.DEVICE_NOT_ENROLLED,
            f"the device registry could not answer, so the device is treated as not "
            f"enrolled: {e}",
        )
    except Exception as e:  # noqa: BLE001 - an unreadable registry is a denial
        logger.exception("Device registry lookup failed; failing closed")
        return None, ActionAdmission.deny(
            ErrorCategory.DEVICE_NOT_ENROLLED,
            f"the device registry raised {type(e).__name__}; the device is treated as "
            "not enrolled",
        )

    if device is None:
        return None, ActionAdmission.deny(
            ErrorCategory.DEVICE_NOT_ENROLLED,
            f"device {device_id!r} is not enrolled in this tenant",
        )
    if not device.enrolled:
        return None, ActionAdmission.deny(
            ErrorCategory.DEVICE_NOT_ENROLLED,
            f"device {device_id!r} is known but its enrolment is not active",
        )
    if device.tenant_id != tenant_id:
        # Belt and braces: `lookup` is tenant-qualified, so this should be
        # unreachable. It is checked anyway because a registry is replaceable
        # and the next one might key on the device alone.
        return None, ActionAdmission.deny(
            ErrorCategory.DEVICE_NOT_ENROLLED,
            "the registry returned a device belonging to another tenant",
        )
    if device.platform != SUPPORTED_PLATFORM:
        return None, ActionAdmission.deny(
            ErrorCategory.PLATFORM_UNSUPPORTED,
            f"device {device_id!r} is a {device.platform!r} device; this build "
            f"actuates {SUPPORTED_PLATFORM} only",
        )
    return device, ActionAdmission.allow()


def check_declared_capability(device: EnrolledDevice, request: ActionRequest) -> ActionAdmission:
    """Gate 4. The device must declare this capability at this exact version."""
    if not device.declares(request.capability):
        return ActionAdmission.deny(
            ErrorCategory.CAPABILITY_NOT_DECLARED,
            f"device {device.device_id!r} does not declare {request.capability.value}",
        )
    declared = device.declared_version(request.capability)
    if declared != request.capability_version:
        return ActionAdmission.deny(
            ErrorCategory.CAPABILITY_UNSUPPORTED,
            f"device {device.device_id!r} declares {request.capability.value} version "
            f"{declared}, and this action names version {request.capability_version}. "
            "A version mismatch is refused, never approximated.",
        )
    return ActionAdmission.allow()


# ---------------------------------------------------------------------------
# Gate 9: Identity policy
# ---------------------------------------------------------------------------


def check_identity_policy(ctx: Any, kind: str) -> ActionAdmission:
    """Gate 9. Additive: skipped when no IdentityContext is wired in.

    Matches every other seam in the repository. The kinds evaluated here are
    the *seam* kinds, not the capabilities: `windows_action_dispatch` is
    deliberately not allowlisted and is not made reachable by allowlisting it,
    because the gate that governs dispatch is the bound approval below.
    """
    identity_context = getattr(ctx, "identity_context", None)
    if identity_context is None:
        return ActionAdmission.allow()
    try:
        decision = policy_engine.evaluate_tool_policy(identity_context, kind)
    except Exception:
        logger.exception("Identity policy evaluation failed for %s; failing closed", kind)
        return ActionAdmission.deny(
            ErrorCategory.GOVERNANCE_DENIED,
            "the Identity policy could not be evaluated, so the action is refused",
        )
    if not decision.allowed:
        return ActionAdmission.deny(
            ErrorCategory.GOVERNANCE_DENIED,
            f"denied by Identity policy: {decision.reason}",
        )
    return ActionAdmission.allow()


# ---------------------------------------------------------------------------
# Reflections
# ---------------------------------------------------------------------------


async def _record_reflection(
    ctx: Any,
    *,
    kind: str,
    outcome: str,
    request: ActionRequest | None,
    action: StoredAction | None,
    reason: str | None,
    category: ErrorCategory | None,
    extra: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """One `ActionReflection` per governed decision, through the shared sink.

    Redacted by construction: `to_dict()` on a request returns the *redacted*
    parameters, so the text somebody asked to have typed is a digest here and
    not a second copy of itself. Never raises -- a lost reflection must not
    break the decision it describes -- but the outcome is returned so the
    caller's result can say the record is incomplete.
    """
    mem = getattr(ctx, "mem", None)
    if mem is None:
        db_path = getattr(ctx, "db_path", None)
        if db_path:
            try:
                mem = _memory_store(str(db_path))
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                logger.exception("Could not construct MemoryStore for an action reflection")
                return False, f"reflection write failed (windows_action): {exc}"

    source = request or action
    details: dict[str, Any] = {
        "action_id": getattr(source, "action_id", "") or "",
        "tenant_id": getattr(source, "tenant_id", "") or "",
        "device_id": getattr(source, "device_id", "") or "",
        "capability": (
            request.capability.value
            if request is not None
            else (action.capability if action is not None else "")
        ),
        "capability_version": (
            request.capability_version
            if request is not None
            else (action.capability_version if action is not None else 0)
        ),
        "risk_class": (
            request.risk_class.value
            if request is not None
            else (action.risk_class if action is not None else "")
        ),
        "parameter_fingerprint": (
            request.parameter_fingerprint
            if request is not None
            else (action.parameter_fingerprint if action is not None else "")
        ),
        "correlation_id": getattr(source, "correlation_id", "") or "",
        **({"causation_id": source.causation_id} if getattr(source, "causation_id", None) else {}),
        **({"error_category": category.value} if category else {}),
        **({"reason": reason} if reason else {}),
        **(extra or {}),
    }
    reflection = ActionReflection(
        surface=REFLECTION_SURFACE,
        action=kind,
        outcome=outcome,
        summary=(
            f"{kind}: {details['capability'] or 'unknown capability'} on device "
            f"{details['device_id'] or 'unknown'} -> {outcome}"
        ),
        details=details,
    )
    written = await record_action_reflection(mem, reflection)
    return written.persisted, written.error


# ---------------------------------------------------------------------------
# Step 1: request an action
# ---------------------------------------------------------------------------


async def run_action_request_through_runtime_contract(
    ctx: Any,
    *,
    tenant_id: str,
    device_id: str,
    requested_by: str,
    capability: str,
    capability_version: Any,
    parameters: Any,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    action_id: str | None = None,
    repeatability: str | None = None,
    ttl_seconds: int | None = None,
    registry: DeviceCapabilityRegistry | None = None,
) -> ActionSeamResult:
    """Admit -- or refuse -- one action request. Nothing is dispatched here.

    A successful return means the action is durably recorded as
    `pending_approval` (or as `approved`, for a capability this device has been
    granted explicit trusted autonomy for). It does **not** mean anything will
    happen: dispatch is a separate trip through
    `run_action_dispatch_through_runtime_contract()`, over a separately
    authenticated channel, with the brake re-read.

    `tenant_id`, `device_id` and `requested_by` come from the API boundary's
    resolution of the platform's own authority. They are never read from a
    request body, and this function has no default for any of them.
    """
    kind = ACTION_KIND_REQUEST
    observation, candidate_action = _observation(kind, None)
    db_path = _ctx_db_path(ctx)

    # Gate 8 first, before anything is validated or written. A halted
    # Bartholomew should not be building action records either.
    brake = await evaluate_actuation_brake(ctx, kind)
    if not brake.allowed:
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=ActionResultStatus.REFUSED,
            category=brake.category,
            reason=brake.reason,
        )

    # Gates 1-4: tenant, principal (already resolved), enrolled device, and --
    # after the request exists -- its declared capability.
    device, admitted = resolve_device(
        tenant_id=tenant_id,
        device_id=device_id,
        registry=registry,
    )
    if device is None:
        return await _refuse_before_storage(
            ctx,
            kind=kind,
            observation=observation,
            candidate_action=candidate_action,
            admission=admitted,
        )

    # Gates 5, 6, 7: typed parameters (against this device's allowlists), risk,
    # and an expiry that is bounded whether or not the caller named one.
    try:
        request = build_request(
            tenant_id=tenant_id,
            device_id=device_id,
            requested_by=requested_by,
            capability=capability,
            capability_version=capability_version,
            parameters=parameters,
            correlation_id=correlation_id,
            causation_id=causation_id,
            action_id=action_id,
            repeatability=repeatability,
            ttl_seconds=ttl_seconds,
            context=device.validation_context(),
        )
    except UnsupportedCapabilityError as e:
        return await _refuse_before_storage(
            ctx,
            kind=kind,
            observation=observation,
            candidate_action=candidate_action,
            admission=ActionAdmission.deny(ErrorCategory.CAPABILITY_UNSUPPORTED, str(e)),
        )
    except SensitiveContentError as e:
        # Its own category, so an audit can count attempts to have a credential
        # typed or copied separately from ordinary malformed requests.
        return await _refuse_before_storage(
            ctx,
            kind=kind,
            observation=observation,
            candidate_action=candidate_action,
            admission=ActionAdmission.deny(ErrorCategory.SENSITIVE_CONTENT, str(e)),
        )
    except (ParameterError, RequestError) as e:
        return await _refuse_before_storage(
            ctx,
            kind=kind,
            observation=observation,
            candidate_action=candidate_action,
            admission=ActionAdmission.deny(ErrorCategory.PARAMETERS_INVALID, str(e)),
        )

    observation, candidate_action = _observation(kind, request)

    declared = check_declared_capability(device, request)
    if not declared.allowed:
        return await _record_refusal(
            ctx,
            db_path=db_path,
            kind=kind,
            observation=observation,
            candidate_action=candidate_action,
            request=request,
            admission=declared,
        )

    # Gate 9: Identity policy on the request kind.
    policy = check_identity_policy(ctx, kind)
    if not policy.allowed:
        return await _record_refusal(
            ctx,
            db_path=db_path,
            kind=kind,
            observation=observation,
            candidate_action=candidate_action,
            request=request,
            admission=policy,
        )

    # Gate 10, in its "does this need one at all" form. Trusted autonomy is
    # resolved per device, applies only to the three eligible kinds, and is
    # empty unless an operator wrote it into the enrolment.
    autonomous = device.autonomous_for(request.capability)
    initial_state = ActionState.APPROVED if autonomous else ActionState.PENDING_APPROVAL

    await run_off_loop(store.ensure_schema, db_path)

    # Sweep this tenant's overdue actions before adding another.
    #
    # The sweep is what purges `parameters_json` -- the one place a piece of
    # text somebody asked to have typed is stored in cleartext -- and it used
    # to run *only* inside the device lease endpoint. With no device resolver
    # installed, which is the shipped default, that endpoint refuses at 401
    # before its body runs, so the purge never happened at all: a `type_text`
    # that was requested, never approved and forgotten kept its cleartext
    # indefinitely, which is the single most likely lifecycle for one.
    #
    # Running it here makes it reachable in every configuration, on the one
    # path that is always exercised. Best effort: a sweep that fails must not
    # stop the request it was housekeeping for.
    try:
        await run_off_loop(store.expire_overdue, db_path, tenant_id=tenant_id)
    except ActionPersistenceError:
        logger.warning(
            "Could not sweep overdue actions for tenant %s; cleartext parameters may "
            "be retained past their expiry until the next successful sweep",
            tenant_id,
            exc_info=True,
        )

    stored, created = await run_off_loop(
        store.create_action,
        db_path,
        record=request.to_dict(redacted=True),
        canonical_parameters=dict(request.parameters.canonical),
        state=initial_state,
        state_reason=("trusted_autonomy" if autonomous else None),
    )

    # A re-submission of an existing action id landed on the row that already
    # exists -- `create_action` never overwrites one, which is what stops a
    # caller changing an approved action's parameters by asking again. What it
    # must not also do is *describe* the attempt as though the new parameters
    # were the ones on file: reporting `request`'s fingerprint next to the
    # stored row's state would put "fingerprint X is approved on device Y" into
    # the durable Reflection trail for a fingerprint nothing ever approved, and
    # anyone able to POST an action could plant that record against any pending
    # id. So the audit reports what is *stored*, and records the mismatch as
    # its own fact -- because a caller re-submitting an id with different
    # parameters is worth being able to see.
    # The **whole** binding, not just the parameters. Keying on the fingerprint
    # alone left every other axis open: re-POSTing a known id with identical
    # parameters but a different capability, version or device produced an
    # identical digest, so the request was not treated as superseding -- and
    # `_record_reflection` then took `capability`, `capability_version`,
    # `device_id` and `risk_class` from the *request* beside the stored row's
    # state. "windows.focus_window on desk-pc -> approved" entered the audit
    # for a row that was `windows.launch_app`.
    superseded = stored.parameter_fingerprint != request.parameter_fingerprint or (
        stored.capability,
        stored.capability_version,
        stored.device_id,
    ) != (
        request.capability.value,
        request.capability_version,
        request.device_id,
    )
    persisted, error = await _record_reflection(
        ctx,
        kind=kind,
        # Both drawn from the stored row when they disagree, so no field of
        # this record describes an action that does not exist.
        outcome=("resubmission_ignored" if superseded else stored.state.value),
        request=(None if superseded else request),
        action=stored,
        reason=(
            "an action with this id already exists; its recorded parameters are "
            "unchanged and the re-submitted ones were not used"
            if superseded
            else None
        ),
        category=None,
        extra={
            "trusted_autonomy": autonomous,
            **(
                {"rejected_resubmission_fingerprint": request.parameter_fingerprint}
                if superseded
                else {}
            ),
        },
    )
    return ActionSeamResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=True,
        status=stored.status,
        reason=(
            "an action with this id already exists; its recorded parameters are "
            "unchanged and the parameters in this request were not used"
            if superseded
            else ("granted trusted autonomy by this device's enrolment" if autonomous else None)
        ),
        action=stored,
        # The stored action is what exists, so that is what the caller is
        # handed. Returning the un-stored request here would let a caller read
        # back its own rejected parameters as though they were on file.
        request=(None if superseded else request),
        existing=not created,
        provenance_degraded=not persisted,
        provenance_error=error,
    )


async def _refuse_before_storage(
    ctx: Any,
    *,
    kind: str,
    observation: Observation,
    candidate_action: CandidateAction,
    admission: ActionAdmission,
) -> ActionSeamResult:
    """A refusal with no action row: the request never became one.

    Reached when the device is unknown or the parameters could not be
    validated, so there is nothing well-formed enough to store. The Reflection
    still records that a refusal happened, which is the durable trace.
    """
    persisted, error = await _record_reflection(
        ctx,
        kind=kind,
        outcome="refused",
        request=None,
        action=None,
        reason=admission.reason,
        category=admission.category,
    )
    return ActionSeamResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=False,
        status=ActionResultStatus.REFUSED,
        category=admission.category,
        reason=admission.reason,
        provenance_degraded=not persisted,
        provenance_error=error,
    )


async def _record_refusal(
    ctx: Any,
    *,
    db_path: str,
    kind: str,
    observation: Observation,
    candidate_action: CandidateAction,
    request: ActionRequest,
    admission: ActionAdmission,
) -> ActionSeamResult:
    """A refusal, durably recorded as a refused action -- when there is one to record.

    A policy denial is not a brake halt: mutation is not forbidden, this
    particular action is -- so recording the refusal is both permitted and
    useful. It stays a refusal, and the row's parameters are never stored,
    because a refused action never needs handing to anything.

    When the id already exists, nothing is recorded and nothing may be
    *described* from the request either: see the comment at the call to
    `_record_reflection` below.
    """
    await run_off_loop(store.ensure_schema, db_path)
    stored, created = await run_off_loop(
        store.create_action,
        db_path,
        record=request.to_dict(redacted=True),
        canonical_parameters={},
        state=ActionState.REFUSED,
        state_reason=admission.reason,
    )
    # A refusal can land on a row that already exists -- re-POSTing a known
    # action id with a capability the device does not declare gets here, and
    # `create_action` returns the existing row rather than writing a refusal.
    # When that happens neither the Reflection nor the response may describe
    # the *request*: doing so wrote the caller's capability and device beside
    # somebody else's approved action, and returned that action's full record
    # from a verb that only requires `action:request`.
    persisted, error = await _record_reflection(
        ctx,
        kind=kind,
        outcome="refused" if created else "refusal_on_existing_action",
        request=request if created else None,
        action=stored,
        reason=admission.reason,
        category=admission.category,
    )
    return ActionSeamResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=False,
        status=ActionResultStatus.REFUSED,
        category=admission.category,
        reason=admission.reason,
        action=stored,
        request=request if created else None,
        existing=not created,
        provenance_degraded=not persisted,
        provenance_error=error,
    )


# ---------------------------------------------------------------------------
# Step 2: approve one exact action
# ---------------------------------------------------------------------------


async def grant_action_approval(
    ctx: Any,
    *,
    tenant_id: str,
    action_id: str,
    approver: str,
    note: str | None = None,
    ttl_seconds: int | None = None,
    registry: DeviceCapabilityRegistry | None = None,
) -> ActionSeamResult:
    """Grant an approval bound to one action exactly as it stands.

    The approval is built **from the stored action**, re-validated against the
    device's current allowlists, so an approver cannot approve parameters that
    no longer pass validation and a caller cannot supply the fingerprint it
    wants approved. Written through `MemoryStore.upsert_memory()` under
    `approval.KIND` -- the same shape the learning-acceptance approval uses --
    so there is no second store and no second audit log.

    Approving does not run anything. It moves the action from
    `pending_approval` to `approved`, which makes it *eligible* to be leased by
    its device, at which point the whole admission runs again.

    **Gate 9 -- the Identity policy -- deliberately does not apply here, and
    that is not an omission.** `tool_use.allowlist` governs what *Bartholomew*
    may do. A person authorising one specific action is not Bartholomew using a
    tool, and gating it on that allowlist would mean an Identity that forbids
    autonomous actuation also forbids a human from approving anything -- the
    exact inversion of what the allowlist is for. `windows_action_approve` is
    therefore absent from it, and adding it would change nothing here.
    `tests/test_windows_action_governance.py` pins that reasoning, so a later
    reading of the gate table cannot "fix" it into a deadlock.

    What does gate an approval: the platform capability `action:approve` at the
    HTTP boundary, the Parking Brake above, the action being in
    `pending_approval`, and the action still passing validation against the
    device's *current* allowlists. And an approval authorises nothing on its
    own -- every gate, gate 9 on the dispatch kind included, runs again at
    lease.
    """
    kind = ACTION_KIND_APPROVE
    observation, candidate_action = _observation(kind, None)
    db_path = _ctx_db_path(ctx)

    brake = await evaluate_actuation_brake(ctx, kind)
    if not brake.allowed:
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=ActionResultStatus.REFUSED,
            category=brake.category,
            reason=brake.reason,
        )

    stored, request, denial = await _load_and_revalidate(
        db_path,
        tenant_id=tenant_id,
        action_id=action_id,
        registry=registry,
    )
    if denial is not None:
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=ActionResultStatus.REFUSED,
            category=denial.category,
            reason=denial.reason,
            action=stored,
        )
    assert stored is not None and request is not None  # noqa: S101 - narrowed above
    observation, candidate_action = _observation(kind, request)

    if stored.state is not ActionState.PENDING_APPROVAL:
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=stored.status,
            category=ErrorCategory.APPROVAL_INVALID,
            reason=(
                f"action {action_id} is {stored.state.value}, so there is nothing "
                "awaiting approval. An action is approvable exactly once."
            ),
            action=stored,
            request=request,
        )

    try:
        approval = build_approval(
            request,
            approver=approver,
            note=note,
            ttl_seconds=ttl_seconds,
        )
    except ApprovalError as e:
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=ActionResultStatus.REFUSED,
            category=ErrorCategory.APPROVAL_INVALID,
            reason=str(e),
            action=stored,
            request=request,
        )

    # **Claim the transition first, then write the approval.** The order is the
    # whole of the concurrency correctness here.
    #
    # Writing the approval first looked harmless: it is keyed on the action, so
    # a second approver simply overwrote the first. But only one of them then
    # won `mark_approved`, and it was not necessarily the one whose approval
    # was left on disk -- so the durable row and the Reflection named one
    # approver while the object `evaluate_dispatch_admission` actually calls
    # `authorizes()` on, expiry window and all, belonged to somebody who had
    # been told they were refused.
    #
    # `mark_approved` is a conditional UPDATE from `pending_approval`, so
    # exactly one caller wins it. The winner is then the only one that writes
    # an approval, and the two can no longer disagree.
    moved = await run_off_loop(
        store.mark_approved,
        db_path,
        tenant_id=tenant_id,
        action_id=action_id,
        approver=approval.approver,
    )
    if moved is None:
        current = await run_off_loop(
            store.get_action,
            db_path,
            tenant_id=tenant_id,
            action_id=action_id,
        )
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=(current.status if current else ActionResultStatus.REFUSED),
            category=ErrorCategory.APPROVAL_INVALID,
            reason=(
                "the action was approved or withdrawn by somebody else while this "
                "approval was being granted"
            ),
            action=current,
            request=request,
        )

    written = await _write_approval(ctx, approval)
    if written is not None:
        # **Compensate, or the action is stuck forever.**
        #
        # `mark_approved` only moves `pending_approval -> approved`, so an
        # action left at `approved` with no approval on file can never be
        # approved again: the retry is refused for not awaiting approval, and
        # dispatch refuses for the missing approval, until expiry sweeps it.
        # Claiming a failed write was "safe to retry" while leaving the action
        # in the one state that guarantees the retry fails is worse than the
        # original bug this ordering fixed.
        #
        # Moving it back is safe: nothing can have leased it in between, because
        # `evaluate_dispatch_admission` reads the approval itself and refuses
        # with `APPROVAL_MISSING` before `try_lease` is ever reached.
        restored = await run_off_loop(
            store.mark_pending_again,
            db_path,
            tenant_id=tenant_id,
            action_id=action_id,
        )
        if restored is None:
            logger.error(
                "Action %s could not be returned to pending after its approval failed "
                "to record; it is approved with no approval on file and can neither "
                "be dispatched nor re-approved",
                action_id,
            )
        current = restored or moved
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=current.status,
            category=ErrorCategory.INTERNAL_ERROR,
            reason=(
                f"{written}. The action was returned to pending approval and can be "
                "approved again."
                if restored is not None
                else (
                    f"{written}. It could not be returned to pending approval either, "
                    "so it can no longer be approved or dispatched and will expire."
                )
            ),
            action=current,
            request=request,
        )

    persisted, error = await _record_reflection(
        ctx,
        kind=kind,
        outcome="approved",
        request=request,
        action=moved,
        reason=None,
        category=None,
        extra={"approver": approval.approver, "approval_expires_at": approval.expires_at},
    )
    return ActionSeamResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=True,
        status=moved.status,
        action=moved,
        request=request,
        provenance_degraded=not persisted,
        provenance_error=error,
    )


async def _write_approval(ctx: Any, approval: ActionApproval) -> str | None:
    """Persist the approval through `MemoryStore`. Returns an error, or None.

    The same authority and the same call shape the learning-acceptance
    approval uses -- no second store, no second audit log. A `StoreResult`
    that does not report `stored` is reported as a failure rather than
    ignored: an approval that was not written must not be treated as one that
    was, or the dispatch path would look for it, not find it, and refuse
    *after* the caller had been told the approval succeeded.
    """
    mem = getattr(ctx, "mem", None)
    if mem is None:
        return "no memory store is reachable, so the approval cannot be recorded"
    try:
        result = await mem.upsert_memory(
            APPROVAL_KIND,
            approval.key(),
            json.dumps(approval.to_dict(), sort_keys=True),
            approval.granted_at,
            summary=approval.to_summary_text(),
        )
    except Exception as e:  # noqa: BLE001 - reported, never swallowed
        logger.exception("Could not record an action approval")
        return f"the approval could not be recorded: {type(e).__name__}: {e}"
    if not getattr(result, "stored", False):
        return (
            "the approval was not stored (refused by the memory store's own policy or "
            "consent gate); nothing is approved"
        )
    return None


async def load_approval(ctx: Any, *, tenant_id: str, action_id: str) -> ActionApproval | None:
    """The recorded approval for one action, or None.

    A read that fails is None -- "no approval" -- which denies. An approval
    that cannot be read is not an approval.
    """
    mem = getattr(ctx, "mem", None)
    if mem is None:
        return None
    key = f"{tenant_id}::{action_id}"
    try:
        row = await mem.get_memory(APPROVAL_KIND, key)
    except Exception:
        logger.exception("Could not read the action approval for %s", action_id)
        return None
    if not row:
        return None
    try:
        return ActionApproval.from_dict(json.loads(row["value"]))
    except (TypeError, ValueError, KeyError):
        logger.warning("The recorded approval for %s is not parseable", action_id)
        return None


# ---------------------------------------------------------------------------
# Step 3: dispatch (the lease), over the device channel
# ---------------------------------------------------------------------------


async def _load_and_revalidate(
    db_path: str,
    *,
    tenant_id: str,
    action_id: str,
    registry: DeviceCapabilityRegistry | None,
) -> tuple[StoredAction | None, ActionRequest | None, ActionAdmission | None]:
    """Read the action back and rebuild it under the device's *current* rules.

    Re-validation is the point: allowlists may have been tightened since the
    request was written, and an action that would no longer be accepted must no
    longer be executable. A row that fails re-validation is a denial, not a
    degraded pass.
    """
    stored = await run_off_loop(
        store.get_action,
        db_path,
        tenant_id=tenant_id,
        action_id=action_id,
    )
    if stored is None:
        return (
            None,
            None,
            ActionAdmission.deny(
                ErrorCategory.GOVERNANCE_DENIED,
                f"no action {action_id!r} exists in this tenant",
            ),
        )

    # A terminal action is answered here, before re-validation, and answered
    # with the reason it actually ended. Its parameters were purged when it
    # ended, so re-validating it would refuse for the true-but-useless reason
    # "the parameters are no longer stored" -- which would tell an audit that
    # a cancelled action failed validation rather than that it was cancelled.
    if stored.terminal:
        return (
            stored,
            None,
            ActionAdmission.deny(
                _TERMINAL_CATEGORY.get(stored.state, ErrorCategory.REPLAY_REFUSED),
                f"action {action_id} already ended as {stored.state.value}",
            ),
        )

    device, admitted = resolve_device(
        tenant_id=tenant_id,
        device_id=stored.device_id,
        registry=registry,
    )
    if device is None:
        return stored, None, admitted

    payload = stored.as_dict()
    payload["parameters"] = dict(stored.parameters or {})
    # `is None`, not falsiness. Purging sets `parameters_json` to NULL, which
    # reads back as `None`; `windows.clipboard_read` legitimately has *no*
    # parameters and stores `{}`, which is falsy but is not purged. Testing
    # truthiness conflated the two and made one of the nine capabilities
    # permanently un-approvable and un-dispatchable. It failed closed, so
    # nothing unsafe happened -- the capability was simply dead.
    if stored.parameters is None and not stored.terminal:
        return (
            stored,
            None,
            ActionAdmission.deny(
                ErrorCategory.PARAMETERS_INVALID,
                "the action's parameters are no longer stored, so it cannot be "
                "re-validated and will not be dispatched",
            ),
        )
    try:
        request = rebuild_request(payload, context=device.validation_context())
    except UnsupportedCapabilityError as e:
        return stored, None, ActionAdmission.deny(ErrorCategory.CAPABILITY_UNSUPPORTED, str(e))
    except (ParameterError, RequestError) as e:
        return (
            stored,
            None,
            ActionAdmission.deny(
                ErrorCategory.PARAMETERS_INVALID,
                f"the action no longer passes validation and will not be dispatched: {e}",
            ),
        )

    declared = check_declared_capability(device, request)
    if not declared.allowed:
        return stored, request, declared
    return stored, request, None


async def evaluate_dispatch_admission(
    ctx: Any,
    *,
    stored: StoredAction,
    request: ActionRequest,
    device: EnrolledDevice,
    now: datetime | None = None,
) -> ActionAdmission:
    """The complete admission, re-run immediately before a device may act.

    Everything is checked again here rather than trusted from admission time,
    because everything can have changed since: the brake may have been engaged,
    the enrolment revoked, the allowlist tightened, the action cancelled, the
    approval lapsed. This is the check that actually guards the operating
    system, and it assumes nothing.
    """
    moment = now or utc_now()

    # 8. The brake, again, and before the approval is even read. An approval is
    #    never a brake override, and the ordering here is what says so.
    brake = await evaluate_actuation_brake(ctx, ACTION_KIND_DISPATCH)
    if not brake.allowed:
        return brake

    # 7. Expiry, on both the action and (below) its approval.
    if request.has_expired(now=moment):
        return ActionAdmission.deny(
            ErrorCategory.EXPIRED,
            f"action {request.action_id} expired at {request.expires_at}",
        )

    # 11a. Cancelled and already-terminal actions never execute later.
    if stored.terminal:
        return ActionAdmission.deny(
            (
                ErrorCategory.CANCELLED
                if stored.state is ActionState.CANCELLED
                else ErrorCategory.REPLAY_REFUSED
            ),
            f"action {request.action_id} already ended as {stored.state.value}",
        )
    if stored.state is ActionState.PENDING_APPROVAL:
        return ActionAdmission.deny(
            ErrorCategory.APPROVAL_MISSING,
            f"action {request.action_id} has not been approved",
        )

    # 10. The exact, bound approval -- unless this device has been granted
    #     explicit trusted autonomy for this capability, which is empty by
    #     default and can never contain an ALWAYS-approval capability.
    if not device.autonomous_for(request.capability):
        approval = await load_approval(
            ctx,
            tenant_id=request.tenant_id,
            action_id=request.action_id,
        )
        if approval is None:
            return ActionAdmission.deny(
                ErrorCategory.APPROVAL_MISSING,
                "no approval bound to this action is recorded, and dispatch requires "
                "one. Allowlisting the dispatch kind does not substitute for it.",
            )
        check = approval.authorizes(request, now=moment)
        if not check.allowed:
            return ActionAdmission.deny(
                (
                    ErrorCategory.EXPIRED
                    if check.code in ("approval_expired", "action_expired")
                    else ErrorCategory.APPROVAL_INVALID
                ),
                check.reason or "the recorded approval does not authorise this action",
            )
    return ActionAdmission.allow()


async def run_action_dispatch_through_runtime_contract(
    ctx: Any,
    *,
    tenant_id: str,
    device_id: str,
    action_id: str,
    registry: DeviceCapabilityRegistry | None = None,
) -> ActionSeamResult:
    """Lease one action to the device that asked for it, or refuse.

    Called only from the device action channel, which is separately
    authenticated and cannot be reached through the observation response path.
    A successful return hands the caller the action's canonical parameters --
    the one moment they leave the server -- and moves the row to `leased`,
    which a non-repeatable action can enter exactly once.
    """
    kind = ACTION_KIND_DISPATCH
    observation, candidate_action = _observation(kind, None)
    db_path = _ctx_db_path(ctx)

    stored, request, denial = await _load_and_revalidate(
        db_path,
        tenant_id=tenant_id,
        action_id=action_id,
        registry=registry,
    )
    if denial is not None:
        return await _dispatch_refusal(
            ctx,
            observation=observation,
            candidate_action=candidate_action,
            stored=stored,
            request=request,
            admission=denial,
        )
    assert stored is not None and request is not None  # noqa: S101 - narrowed above
    observation, candidate_action = _observation(kind, request)

    # 3. The device asking must be the device the action targets. A device that
    #    could lease another device's action would be a lateral-movement
    #    primitive dressed as a poll.
    if stored.device_id != device_id:
        return await _dispatch_refusal(
            ctx,
            observation=observation,
            candidate_action=candidate_action,
            stored=stored,
            request=request,
            admission=ActionAdmission.deny(
                ErrorCategory.DEVICE_NOT_ENROLLED,
                f"action {action_id} targets device {stored.device_id!r}, and this "
                f"channel is authenticated as {device_id!r}",
            ),
        )

    device, device_admission = resolve_device(
        tenant_id=tenant_id,
        device_id=device_id,
        registry=registry,
    )
    if device is None:
        return await _dispatch_refusal(
            ctx,
            observation=observation,
            candidate_action=candidate_action,
            stored=stored,
            request=request,
            admission=device_admission,
        )

    admission = await evaluate_dispatch_admission(
        ctx,
        stored=stored,
        request=request,
        device=device,
    )
    if not admission.allowed:
        return await _dispatch_refusal(
            ctx,
            observation=observation,
            candidate_action=candidate_action,
            stored=stored,
            request=request,
            admission=admission,
        )

    # 11b. The lease itself: a conditional UPDATE that exactly one caller wins.
    leased = await run_off_loop(
        store.try_lease,
        db_path,
        tenant_id=tenant_id,
        action_id=action_id,
        repeatable=(request.repeatability.value == "idempotent"),
    )
    if leased is None:
        current = await run_off_loop(
            store.get_action,
            db_path,
            tenant_id=tenant_id,
            action_id=action_id,
        )
        # `try_lease` now refuses for expiry as well as for replay -- the
        # expiry guard was added to its `WHERE` to close a time-of-check gap
        # that spans two brake reads and a memory read. Hard-coding "already
        # leased" here wrote a clock event into the audit as a replay attempt,
        # in a sentence that was false on both clauses. Re-read and say which.
        expired = current is not None and request.has_expired()
        return await _dispatch_refusal(
            ctx,
            observation=observation,
            candidate_action=candidate_action,
            stored=current,
            request=request,
            admission=ActionAdmission.deny(
                ErrorCategory.EXPIRED if expired else ErrorCategory.REPLAY_REFUSED,
                (
                    f"action {action_id} expired at {request.expires_at} between its "
                    "admission and the lease"
                    if expired
                    else (
                        f"action {action_id} has already been leased and is "
                        "non-repeatable; a duplicate delivery does not run it a "
                        "second time"
                    )
                ),
            ),
        )

    persisted, error = await _record_reflection(
        ctx,
        kind=kind,
        outcome="leased",
        request=request,
        action=leased,
        reason=None,
        category=None,
        extra={"lease_count": leased.lease_count},
    )
    return ActionSeamResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=True,
        status=ActionResultStatus.STARTED,
        action=leased,
        request=request,
        provenance_degraded=not persisted,
        provenance_error=error,
    )


async def _dispatch_refusal(
    ctx: Any,
    *,
    observation: Observation,
    candidate_action: CandidateAction,
    stored: StoredAction | None,
    request: ActionRequest | None,
    admission: ActionAdmission,
) -> ActionSeamResult:
    persisted, error = await _record_reflection(
        ctx,
        kind=ACTION_KIND_DISPATCH,
        outcome="refused",
        request=request,
        action=stored,
        reason=admission.reason,
        category=admission.category,
    )
    return ActionSeamResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=False,
        status=ActionResultStatus.REFUSED,
        category=admission.category,
        reason=admission.reason,
        action=stored,
        request=request,
        provenance_degraded=not persisted,
        provenance_error=error,
    )


# ---------------------------------------------------------------------------
# Step 4: record what the device observed
# ---------------------------------------------------------------------------


async def record_action_result_through_runtime_contract(
    ctx: Any,
    *,
    tenant_id: str,
    device_id: str,
    action_id: str,
    status: str,
    error_category: str | None,
    detail: str,
    evidence: Any,
    observed_at: str,
) -> ActionSeamResult:
    """Record one device-observed outcome, truthfully.

    The status is taken from the device -- it is the only party that saw what
    happened -- but the vocabulary is constrained: a device may report
    `started`, `succeeded`, `failed`, `cancelled` or `unknown`, and may not
    report `accepted` or `refused`, which are Governance's words about its own
    decision.

    A result for an action that already ended is **not** written and is
    reported as such, so a late or duplicate result cannot overwrite an
    outcome. A cancelled action can therefore never acquire a success.
    """
    kind = ACTION_KIND_DISPATCH
    observation, candidate_action = _observation(kind, None)
    db_path = _ctx_db_path(ctx)

    try:
        reported = ActionResultStatus(status)
    except ValueError:
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=ActionResultStatus.REFUSED,
            category=ErrorCategory.PARAMETERS_INVALID,
            reason=f"{status!r} is not a result status",
        )
    if reported not in DEVICE_REPORTABLE_STATUSES:
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=ActionResultStatus.REFUSED,
            category=ErrorCategory.GOVERNANCE_DENIED,
            reason=(
                f"a device may not report {reported.value!r}; that is Governance's "
                "word about its own decision"
            ),
        )

    category: ErrorCategory | None = None
    if error_category:
        try:
            category = ErrorCategory(error_category)
        except ValueError:
            category = ErrorCategory.INTERNAL_ERROR

    stored = await run_off_loop(
        store.get_action,
        db_path,
        tenant_id=tenant_id,
        action_id=action_id,
    )
    if stored is None or stored.device_id != device_id:
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=ActionResultStatus.REFUSED,
            category=ErrorCategory.DEVICE_NOT_ENROLLED,
            reason="no such action for this device",
            action=stored,
        )

    result = ActionResult(
        action_id=action_id,
        tenant_id=tenant_id,
        device_id=device_id,
        status=reported,
        error_category=category,
        detail=str(detail or ""),
        evidence=evidence if isinstance(evidence, dict) else {},
        observed_at=str(observed_at or ""),
    )
    updated, recorded = await run_off_loop(store.record_result, db_path, result=result)

    if not recorded:
        # Two different situations, and calling both "this action had already
        # ended" was false for one of them: a repeated `started` note on an
        # action that is still `leased` is a duplicate *observation*, not a
        # late result, and the action is very much live. Saying it ended --
        # in a body whose own `state` field reads `leased` -- told a conforming
        # client to stop reporting on an action still in flight.
        still_live = (
            updated is not None
            and updated.state is ActionState.LEASED
            and reported is ActionResultStatus.STARTED
        )
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=(updated.status if updated else ActionResultStatus.REFUSED),
            category=ErrorCategory.REPLAY_REFUSED,
            reason=(
                f"this progress note for action {action_id} was already recorded; the "
                "action is still running and its outcome is still awaited"
                if still_live
                else (
                    f"action {action_id} is "
                    f"{updated.state.value if updated else 'unknown'}; a late or "
                    "duplicate result does not change an outcome that already happened"
                )
            ),
            action=updated,
        )

    persisted, error = await _record_reflection(
        ctx,
        kind=kind,
        outcome=reported.value,
        request=None,
        action=updated,
        reason=detail or None,
        category=category,
        # The evidence **keys**, not the evidence. `ActionReflection` redacts
        # top-level string values in `details`; a nested dict passes through
        # its redaction untouched, so putting the evidence map in here would
        # have written whatever a device sent -- including `clipboard_read`'s
        # returned text under the opt-in -- straight into Memory unredacted.
        # The typed row in `windows_action_results` is where evidence lives;
        # duplicating it into the Reflection bought nothing and cost the one
        # guarantee that sink makes.
        extra={"evidence_keys": ",".join(sorted(result.evidence))},
    )
    return ActionSeamResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=True,
        status=reported,
        category=category,
        reason=result.detail or None,
        action=updated,
        provenance_degraded=not persisted,
        provenance_error=error,
    )


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


async def cancel_action_through_runtime_contract(
    ctx: Any,
    *,
    tenant_id: str,
    action_id: str,
    cancelled_by: str,
    reason: str | None = None,
) -> ActionSeamResult:
    """Withdraw an action so it can never run.

    Deliberately **not** gated on the Parking Brake. Cancelling only ever
    removes an action's ability to happen, and a halt that prevented somebody
    from withdrawing a pending action would be a halt that made things less
    safe. The Identity gate still applies, as it does to every seam kind.
    """
    kind = ACTION_KIND_CANCEL
    observation, candidate_action = _observation(kind, None)
    db_path = _ctx_db_path(ctx)

    policy = check_identity_policy(ctx, kind)
    if not policy.allowed:
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=ActionResultStatus.REFUSED,
            category=policy.category,
            reason=policy.reason,
        )

    moved = await run_off_loop(
        store.mark_cancelled,
        db_path,
        tenant_id=tenant_id,
        action_id=action_id,
        reason=(reason or f"cancelled by {cancelled_by}")[:200],
    )
    if moved is None:
        current = await run_off_loop(
            store.get_action,
            db_path,
            tenant_id=tenant_id,
            action_id=action_id,
        )
        return ActionSeamResult(
            observation=observation,
            candidate_action=candidate_action,
            governance_allowed=False,
            status=(current.status if current else ActionResultStatus.REFUSED),
            category=ErrorCategory.REPLAY_REFUSED,
            reason=(
                f"action {action_id} is {current.state.value}; it has already ended"
                if current
                else f"no action {action_id!r} exists in this tenant"
            ),
            action=current,
        )

    persisted, error = await _record_reflection(
        ctx,
        kind=kind,
        outcome="cancelled",
        request=None,
        action=moved,
        reason=reason,
        category=ErrorCategory.CANCELLED,
        extra={"cancelled_by": cancelled_by},
    )
    return ActionSeamResult(
        observation=observation,
        candidate_action=candidate_action,
        governance_allowed=True,
        status=ActionResultStatus.CANCELLED,
        category=ErrorCategory.CANCELLED,
        action=moved,
        provenance_degraded=not persisted,
        provenance_error=error,
    )
