"""
Route -> capability authorisation table.

A dict, reviewable on one screen. Deliberately **not** an RBAC engine: at
Alpha scale the entire policy is "a user may act on their own Bartholomew,
and platform administration is a different kind of principal". A rules engine
would be more code, more configuration, and harder to audit than the thing it
encodes.

Two properties matter more than the contents:

* **Default deny.** A path with no entry is refused. A new route added by any
  stream is therefore unreachable until someone classifies it, and the
  route-coverage test fails until they do. The failure mode of forgetting is
  "my route 403s", not "my route is open".
* **Capabilities are not Governance.** Holding `brake:disengage` means the
  request is *allowed to ask*. Whether the brake actually disengages is still
  GovernanceStore's revision-guarded decision, and holding any capability
  here never exempts a request from the brake, the consent gate or policy.
"""

from __future__ import annotations

from enum import Enum

from .principal import AuthorizationError, Principal


class Capability(str, Enum):
    """Named powers a request may require."""

    CHAT = "chat"
    SELF_READ = "self:read"
    SELF_WRITE = "self:write"
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    MEMORY_EXPORT = "memory:export"
    CONSENT_DECIDE = "consent:decide"
    BRAKE_READ = "brake:read"
    BRAKE_ENGAGE = "brake:engage"
    BRAKE_DISENGAGE = "brake:disengage"
    GOVERNANCE_AUDIT = "governance:audit"
    TRAINING_SUBMIT = "training:submit"
    KERNEL_COMMAND = "kernel:command"
    # Inbound capture (Session D). Two capabilities, not one: submitting
    # captured material into a personal Bartholomew and reading back what was
    # captured are different powers, and a capture client that can only write
    # should not be able to read the capture history back out.
    INBOUND_SUBMIT = "inbound:submit"
    INBOUND_READ = "inbound:read"
    # Governed Windows actuation (Session B). Four capabilities, not one, and
    # the split is the point: asking for an action, approving one, reading the
    # audit, and being the device that carries actions out are four different
    # powers. In particular ACTION_APPROVE is separate from ACTION_REQUEST so
    # that a future surface which may request work cannot also authorise it,
    # and DEVICE_ACTION_CHANNEL is separate from all three so that holding it
    # lets a device collect work without letting it request or approve any.
    #
    # None of these is Governance. Holding ACTION_APPROVE means the request is
    # allowed to ask; whether the action then runs is still the Parking Brake,
    # the Identity policy and the action-bound approval's decision.
    ACTION_REQUEST = "action:request"
    ACTION_APPROVE = "action:approve"
    ACTION_READ = "action:read"
    DEVICE_ACTION_CHANNEL = "device_action:channel"

    # Learning and Memory Control Centre (Package D). Five capabilities, not
    # one, and the split follows the architecture rather than the screen: the
    # thing that must stay hardest to hold is `LEARNING_APPROVE`, because
    # granting a candidate-bound acceptance approval is the one act that can
    # turn "I may have learned something" into retrievable knowledge.
    #
    # A future read-only reviewer holds LEARNING_READ alone and can inspect
    # every candidate, its provenance and every shadow evaluation without
    # being able to change a word of it. LEARNING_REVIEW adds editing,
    # rejecting and previewing. LEARNING_APPROVE is the deliberate step up.
    LEARNING_READ = "learning:read"
    LEARNING_REVIEW = "learning:review"
    LEARNING_APPROVE = "learning:approve"
    LEARNING_POLICY = "learning:policy"
    # Exporting selected learning records is its own power for the same
    # reason MEMORY_EXPORT is: reading one candidate and taking a copy of a
    # selection out of the runtime are different things.
    LEARNING_EXPORT = "learning:export"
    NOTIFICATIONS = "notifications"
    AWAITING_RESPONSE = "awaiting_response"
    REFLECTION = "reflection"
    LIVENESS = "liveness"
    METRICS = "metrics"
    PLATFORM_ADMIN = "platform:admin"


# Capabilities an ordinary authenticated user holds over *their own*
# Bartholomew. "Their own" is enforced by runtime resolution, not by this
# set -- see runtime_registry: there is no code path by which a capability
# here can be exercised against another user's runtime.
_USER_CAPABILITIES = frozenset(
    {
        Capability.CHAT,
        Capability.SELF_READ,
        Capability.SELF_WRITE,
        Capability.MEMORY_READ,
        Capability.MEMORY_WRITE,
        Capability.MEMORY_EXPORT,
        Capability.CONSENT_DECIDE,
        Capability.BRAKE_READ,
        Capability.BRAKE_ENGAGE,
        Capability.BRAKE_DISENGAGE,
        Capability.GOVERNANCE_AUDIT,
        Capability.TRAINING_SUBMIT,
        Capability.KERNEL_COMMAND,
        Capability.INBOUND_SUBMIT,
        Capability.INBOUND_READ,
        Capability.ACTION_REQUEST,
        Capability.ACTION_APPROVE,
        Capability.ACTION_READ,
        Capability.DEVICE_ACTION_CHANNEL,

        Capability.LEARNING_READ,
        Capability.LEARNING_REVIEW,
        # A person holds this over their own Bartholomew: reviewing what it
        # believes it learned about *their* life is theirs to do, and there
        # is nobody else to delegate it to at Alpha scale. It is separate
        # from LEARNING_REVIEW so that a future delegated reviewer -- a
        # household member, a support session -- can be given the review
        # surface without being given the power to make a lesson trusted.
        Capability.LEARNING_APPROVE,
        Capability.LEARNING_POLICY,
        Capability.LEARNING_EXPORT,
        Capability.NOTIFICATIONS,
        Capability.AWAITING_RESPONSE,
        Capability.REFLECTION,
        Capability.LIVENESS,
    },
)

# A platform administrator is a *different kind of principal*, not a user
# with extras. It deliberately does NOT inherit the user set: administering
# the platform is not the same authority as reading someone's memory, and an
# admin has no personal runtime of their own to read. Anything an admin
# should be able to do to a specific user's data needs its own decision and
# its own audited capability -- not an inherited one.
_ADMIN_CAPABILITIES = frozenset(
    {
        Capability.PLATFORM_ADMIN,
        Capability.METRICS,
        Capability.LIVENESS,
        Capability.BRAKE_READ,
        Capability.GOVERNANCE_AUDIT,
    },
)


def capabilities_for(principal: Principal) -> frozenset[Capability]:
    if principal.is_platform_admin:
        return _ADMIN_CAPABILITIES
    return _USER_CAPABILITIES


def require_capability(principal: Principal, capability: Capability) -> None:
    """Raise `AuthorizationError` unless the principal holds `capability`."""
    if capability not in capabilities_for(principal):
        raise AuthorizationError(
            f"principal kind {principal.kind.value!r} is not authorised for "
            f"{capability.value!r}",
        )
