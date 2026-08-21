"""
Queue containment policy for scheduler-emitted nudges (WP-A1).

Closes the two Test #1 queue failure modes recorded in the Post-Test #1
Decision Register v2.2 and propagated into `RISKS.md` / `DECISIONS.md`:

  * **B-F001** -- self-sustaining queue growth. `drive_self_check` warned
    about a high pending-nudge count by *creating another pending nudge*,
    so the warning incremented the very condition it reported.
  * **NUDGE-F001** -- semantically equivalent unresolved system-generated
    items (recurring curiosity prompts) persisting as separate rows.

and implements the queue-containment half of safety gate **S1**, under the
obligation-preservation constraint of decision **D2**.

Two independent mechanisms, deliberately kept separate:

1. **Non-recursive health accounting** (`health.py`). The drift check that
   produces the `system_health` nudge counts only pending nudges that are
   *not* themselves self-check drift warnings. A queue-size warning can
   therefore never increment its own trigger. The true total is still
   reported (`pending_nudges`) so the underlying condition stays fully
   observable -- this is containment of the feedback loop, not of the
   signal.

2. **Deterministic equivalence keys** (this module). A policy-eligible
   system-generated nudge carries a `dedup_key`. A partial UNIQUE index
   over `(dedup_key) WHERE dedup_key IS NOT NULL AND status='pending'`
   makes "at most one equivalent *unresolved* item" a property enforced by
   SQLite itself, inside the single INSERT statement -- not an
   application-level read-then-write that could race two writers (see
   `persistence.insert_nudge_contained`).

Scope discipline (D2 / S1 / requirement C of WP-A1):

  * Eligibility is an explicit **allowlist keyed on `reason`**, not a
    default. Anything the policy does not recognise is inserted verbatim,
    unkeyed, exactly as before this module existed.
  * Equivalence is **deterministic string identity**, derived from fields
    the emitting drive controls. There is deliberately no fuzzy, embedding
    or LLM-based semantic matching over obligations here: a wrong match
    would silently merge two genuine commitments.
  * Containment **never deletes, resolves, expires or sheds** anything. The
    only outcomes are "inserted" and "not inserted because an equivalent
    unresolved item already exists" -- and the second is recorded as
    evidence, with an occurrence counter on the surviving row. The
    system-generated queue is bounded by the finite number of distinct
    equivalence keys, so no capacity-shedding mechanism is needed, and
    none is provided.
  * `awaiting_response` is a different table and a different authority
    (`docs/S1_4_AWAITING_RESPONSE_DESIGN.md`). Nothing here reads or
    writes it. Its exemption from generic expiry/cap shedding is
    structural: there is no generic expiry or cap shedding.

Escalation: a `Nudge` may carry an explicit `escalation` label. It is part
of the equivalence key, so an explicitly distinct escalation is a distinct
unresolved item by construction -- while an ordinary repeat firing is not.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# Reasons whose emissions are recurring, system-generated, user-facing
# housekeeping -- the only material this policy is permitted to contain.
# Keyed on `reason` (the emitting drive's own label), not on `kind` or on
# message text, so a future drive reusing a generic kind does not silently
# inherit containment.
_CURIOSITY_PROBE = "curiosity_probe"
_SELF_CHECK_DRIFT = "self_check_drift"

# A self-check drift string is "<class>" or "<class>:<detail>", where the
# detail is a volatile measurement (a count, an age in hours). Equivalence
# must be on the *class*: keying on the whole string would make every new
# pending-nudge count a "new" warning, which is precisely the unbounded
# growth B-F001 recorded.
_DRIFT_CLASS_RE = re.compile(r"^([a-z_]+)")


def _drift_class(message: str, reason: str) -> str:
    """
    Extract the stable drift class from a self-check nudge.

    `drive_self_check` formats its message as
    ``System drift detected: <drift>`` where ``<drift>`` is
    ``check_drift()``'s return value, e.g. ``high_pending_nudges:37`` or
    ``stale_daily_reflection:41h``. The class (``high_pending_nudges``) is
    the identity; the measurement is not.
    """
    _, _, drift = message.partition("System drift detected: ")
    candidate = (drift or message).strip()
    match = _DRIFT_CLASS_RE.match(candidate)
    return match.group(1) if match else reason


def _curiosity_identity(message: str, reason: str) -> str:
    """
    Equivalence for the curiosity probe.

    The drive rotates between a small fixed set of prompt strings purely
    as presentation; the obligation they create is the same one -- "an
    open invitation to reflect is waiting for you". Two of them unresolved
    at once is NUDGE-F001, not two commitments. The identity is therefore
    the drive itself, and the message text is deliberately not part of it.

    Note the direction of the safety argument: this collapses *the
    system's own recurring invitation*, never anything the user or an
    external party is owed. Genuine external-reply obligations live in
    `awaiting_response`, which this module does not touch.
    """
    return reason


# reason -> (kind_scope, identity_fn). `kind_scope` is included in the key
# so a reason accidentally reused under a different kind cannot collide.
_POLICY: dict[str, Callable[[str, str], str]] = {
    _CURIOSITY_PROBE: _curiosity_identity,
    _SELF_CHECK_DRIFT: _drift_class,
}


def is_policy_eligible(reason: str | None) -> bool:
    """True if `reason` names recurring system-generated material this
    policy is allowed to contain. Everything else is out of scope."""
    return reason in _POLICY


def eligible_reasons() -> frozenset[str]:
    """The allowlist itself, for tests and for machine inspection."""
    return frozenset(_POLICY)


def dedup_key_for(
    kind: str,
    message: str,
    reason: str | None,
    escalation: str | None = None,
) -> str | None:
    """
    Deterministic equivalence key for a nudge, or None if the nudge is not
    policy-eligible (in which case it is persisted unconditionally and
    unkeyed).

    Args:
        kind: Nudge kind, e.g. "curiosity", "system_health".
        message: Rendered nudge message.
        reason: Emitting drive's reason label -- the allowlist key.
        escalation: Optional explicit escalation label. When set, it is
            part of the key, so an explicitly distinct escalation is
            always a distinct unresolved item.

    Returns:
        A stable key string, or None when containment does not apply.
    """
    if reason is None or reason not in _POLICY:
        return None

    identity = _POLICY[reason](message, reason)
    key = f"{kind}:{reason}:{identity}"
    if escalation:
        key = f"{key}#escalation:{escalation}"
    return key


def dedup_key_for_nudge(nudge: Any) -> str | None:
    """`dedup_key_for()` applied to a `models.Nudge` (or any object with
    the same `kind`/`message`/`reason`/`escalation` attributes)."""
    return dedup_key_for(
        nudge.kind,
        nudge.message,
        nudge.reason,
        getattr(nudge, "escalation", None),
    )
