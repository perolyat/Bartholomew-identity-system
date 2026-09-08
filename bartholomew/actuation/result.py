"""What happened, said truthfully -- including when the honest answer is "I don't know".

Eight statuses, and the distinctions between them are the point. The one that
matters most is `UNKNOWN`: when the device cannot observe whether an action
took effect, that is what it must report. A companion that reported `SUCCEEDED`
because a Win32 call returned without error would be reporting the absence of
an error as the presence of an effect, and everything downstream -- an audit, a
person deciding whether to try again, a future policy -- would inherit the
fiction.

So each handler in `bartholomew/windows_actuation/handlers.py` is required to
*observe* its own effect before it may claim one: a launched application is
looked for by process image, a focused window is read back from
`GetForegroundWindow`, a clipboard write is read back and compared, and typed
text is read back out of the field it was typed into. Where the observation
cannot be made, the result is `UNKNOWN` and says which observation was missing.

The eighth status, `ABORTED_BY_BRAKE`, is the one W03-C added, and it is a
status rather than an error category on `CANCELLED` for an audit reason: "how
many actions did a halt stop after they had been leased" is a question an
operator asks about a safety control, and it should be one query over one
column rather than a join on a reason string. A person withdrawing an action
and a brake stopping one are different events with different follow-ups.

Evidence
--------
`ActionResult.evidence` is a small, bounded, non-sensitive dictionary -- the
window handle that was focused, the process id that appeared, the digest of the
text that was written. It is what an audit reads.

`bounded_evidence()` enforces a **key allowlist** as well as a size bound, and
it runs on the server over whatever a device sent, so it is a boundary rather
than a convenience: a device cannot put typed text, a document's contents or
anything read off the screen into a durable row by inventing a key for it. The
single content-bearing name on the allowlist is `text`, which
`windows.clipboard_read` fills only when a device operator has explicitly opted
in to the clipboard's contents leaving the machine at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Longest a single evidence value may be once rendered. Evidence is a
#: fingerprint of what happened, not a copy of it.
MAX_EVIDENCE_VALUE_CHARS = 200

#: Most evidence keys one result may carry.
MAX_EVIDENCE_KEYS = 12

#: The complete set of evidence keys any handler may emit.
#:
#: A **key allowlist**, not just a size bound, and the difference matters: a
#: compromised or impersonating device -- which is the threat the device-side
#: checks exist for -- would otherwise be able to POST twelve keys of screen or
#: clipboard content to the result endpoint and have every character written
#: into `windows_action_results`, a table nothing ever deletes from. Bounding
#: the *size* of arbitrary content is not the same as refusing content.
#:
#: Every name here is a fact about what happened, never a piece of what was
#: read or typed. `text` is deliberately present and deliberately the only
#: content-bearing one: `windows.clipboard_read` may return the clipboard, and
#: only when a device operator has explicitly turned that on. Everything else
#: is a handle, a count, a code, a boolean or a digest.
PERMITTED_EVIDENCE_KEYS: frozenset[str] = frozenset(
    {
        # what was targeted
        "app_id",
        "hwnd",
        "process_id",
        "operation",
        "url_host",
        "suffix",
        "is_directory",
        # what was observed
        "window_observed",
        "window_count",
        "foreground_hwnd",
        "minimized",
        "maximized",
        "left",
        "top",
        "width",
        "height",
        "clamped",
        "has_text",
        "text_length",
        "text_sha256",
        "events_sent",
        "field_readable",
        "control_type",
        # what the Verify step observed. Facts about an observation, never any
        # part of what was observed: whether a read-back happened, whether it
        # agreed, how the read was made, and how long the field was before and
        # after. `verify_method` is a fixed vocabulary
        # (`VERIFY_METHODS`), not free text a device chooses.
        "verified",
        "verify_method",
        "text_length_before",
        "text_length_after",
        "server_verified",
        # how far a multi-step handler got before an abort. 0 means nothing
        # ran, which is what makes an abort-before-the-handler legible.
        "steps_completed",
        "sensitive",
        "sensitive_categories",
        "content_returned",
        # what the OS said
        "shell_execute_code",
        "create_process_error",
        # the one content-bearing key, off by default; see above
        "text",
        # set by `bounded_evidence` itself when it drops something
        "dropped_keys",
    },
)


#: The complete vocabulary for the `verify_method` evidence key. A closed set
#: for the same reason the capability kinds are one: an audit counting how
#: effects were confirmed cannot do it over free text a device invented, and a
#: device that could name its own method could name a flattering one.
#:
#: * `uia_value_read_back` -- the device read the field's value back through UI
#:   Automation and compared it against what it had been asked to type.
#: * `os_state_read_back`  -- the device read the operating system's own state
#:   back (a foreground window handle, a running process image, the clipboard).
#: * `unavailable`         -- no read-back provider existed, so the effect was
#:   not observed and the status is `unknown`. Recorded rather than omitted,
#:   because "nobody looked" and "somebody looked and saw nothing" are
#:   different facts and an absent key conflates them.
VERIFY_METHODS: frozenset[str] = frozenset(
    {"uia_value_read_back", "os_state_read_back", "unavailable"},
)


class ActionResultStatus(str, Enum):
    """The complete outcome vocabulary. Seven values, none of them overlapping."""

    #: The request passed admission and is recorded. Nothing has run.
    ACCEPTED = "accepted"
    #: Governance refused it. Nothing ran, and nothing will.
    REFUSED = "refused"
    #: A device has leased it and is running it now.
    STARTED = "started"
    #: It ran, and the device observed the effect it was supposed to have.
    SUCCEEDED = "succeeded"
    #: It ran and did not take effect, and the device knows that it did not.
    FAILED = "failed"
    #: It was withdrawn before it ran, or a lease was abandoned.
    CANCELLED = "cancelled"
    #: It may or may not have taken effect and the device cannot tell. Never a
    #: synonym for failure, and never rounded up to success.
    UNKNOWN = "unknown"
    #: The Parking Brake stopped it after it had been leased and before its
    #: handler completed. Distinct from `CANCELLED`, which is a person
    #: withdrawing an action, and distinct from `UNKNOWN`, which is nobody
    #: knowing: here it is known *why* it did not complete, and the answer is
    #: that a halt was engaged. `steps_completed` in the evidence says whether
    #: anything ran at all before the abort.
    ABORTED_BY_BRAKE = "aborted_by_brake"


#: Statuses after which nothing further happens to an action.
TERMINAL_STATUSES: frozenset[ActionResultStatus] = frozenset(
    {
        ActionResultStatus.REFUSED,
        ActionResultStatus.SUCCEEDED,
        ActionResultStatus.FAILED,
        ActionResultStatus.CANCELLED,
        ActionResultStatus.UNKNOWN,
        ActionResultStatus.ABORTED_BY_BRAKE,
    },
)

#: Statuses a *device* is permitted to report back. A device cannot declare an
#: action accepted or refused -- those are Governance's words, and a device
#: that could say them could talk its way past the gate.
DEVICE_REPORTABLE_STATUSES: frozenset[ActionResultStatus] = frozenset(
    {
        ActionResultStatus.STARTED,
        ActionResultStatus.SUCCEEDED,
        ActionResultStatus.FAILED,
        ActionResultStatus.CANCELLED,
        ActionResultStatus.UNKNOWN,
        # The device is the party that stops: it polls the abort signal
        # between its lease and its handler, and between the steps of a
        # multi-step one. Reporting the abort is therefore an observation it
        # made, not a governance word it is borrowing -- and the server still
        # refuses the report if the row says the action already ended.
        ActionResultStatus.ABORTED_BY_BRAKE,
    },
)


class ErrorCategory(str, Enum):
    """Why an action did not succeed, in categories an audit can count.

    Categories rather than messages, because a free-text reason is impossible
    to aggregate and tempting to fill with the very content that must not be
    stored. A human-readable `detail` sits alongside, bounded and redacted.
    """

    #: The request never passed admission.
    GOVERNANCE_DENIED = "governance_denied"
    PARKING_BRAKE = "parking_brake"
    APPROVAL_MISSING = "approval_missing"
    APPROVAL_INVALID = "approval_invalid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REPLAY_REFUSED = "replay_refused"
    DEVICE_NOT_ENROLLED = "device_not_enrolled"
    CAPABILITY_NOT_DECLARED = "capability_not_declared"
    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    PARAMETERS_INVALID = "parameters_invalid"
    #: The request was fine; the machine did not cooperate.
    TARGET_NOT_FOUND = "target_not_found"
    TARGET_AMBIGUOUS = "target_ambiguous"
    PERMISSION_DENIED = "permission_denied"
    SENSITIVE_CONTENT = "sensitive_content"
    SENSITIVE_FIELD = "sensitive_field"
    ACCESSIBILITY_UNAVAILABLE = "accessibility_unavailable"
    PLATFORM_UNSUPPORTED = "platform_unsupported"
    OS_CALL_FAILED = "os_call_failed"
    #: The effect could not be observed either way.
    EFFECT_UNVERIFIABLE = "effect_unverifiable"
    TIMED_OUT = "timed_out"
    INTERNAL_ERROR = "internal_error"


def bounded_evidence(raw: Any) -> dict[str, Any]:
    """Coerce a handler's evidence into something safe to store.

    Four rules, applied here rather than trusted to each handler -- and to
    every *device*, which is the point: this function runs on the server, over
    whatever a device POSTed, so it is the enforcement boundary and not a
    convenience for well-behaved callers.

    1. **A key allowlist.** Anything not in `PERMITTED_EVIDENCE_KEYS` is
       dropped, and `dropped_keys` records that it was, so a device stuffing
       screen content into a made-up key gets nothing stored and leaves a trace
       of having tried.
    2. Only `str`, `int`, `float`, `bool` and `None` values survive. A nested
       structure is rendered to a bounded string, so nothing can be smuggled
       out inside a list.
    3. Every rendered value is truncated to `MAX_EVIDENCE_VALUE_CHARS`.
    4. At most `MAX_EVIDENCE_KEYS` keys, so the row size is bounded whatever
       arrives.

    Nothing here redacts *meaning* within a permitted key -- that is the
    allowlist's job, and it is why `text` is the only content-bearing name on
    it.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    dropped: list[str] = []
    for key in sorted(str(k) for k in raw):
        if key not in PERMITTED_EVIDENCE_KEYS or key == "dropped_keys":
            dropped.append(key[:64])
            continue
        if len(out) >= MAX_EVIDENCE_KEYS:
            dropped.append(key[:64])
            continue
        value = raw[key] if key in raw else raw.get(key)
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            out[key[:64]] = value
            continue
        if isinstance(value, str):
            out[key[:64]] = value[:MAX_EVIDENCE_VALUE_CHARS]
            continue
        try:
            rendered = json.dumps(value, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            rendered = repr(value)
        out[key[:64]] = rendered[:MAX_EVIDENCE_VALUE_CHARS]
    if dropped:
        # The names only -- never the values, which are the thing being
        # refused. Truncated so a device cannot make this the smuggling
        # channel by putting content in the key.
        #
        # It costs a slot rather than being added on top: appending it after
        # the cap let a full evidence map come out at MAX_EVIDENCE_KEYS + 1,
        # which is a bound that does not bound.
        while len(out) >= MAX_EVIDENCE_KEYS:
            out.pop(next(reversed(out)))
        out["dropped_keys"] = ",".join(sorted(dropped))[:MAX_EVIDENCE_VALUE_CHARS]
    # W03-F integration repair: make the closure this module DECLARES real.
    #
    # `PERMITTED_EVIDENCE_KEYS` says `verify_method` "is a fixed vocabulary
    # (`VERIFY_METHODS`), not free text a device chooses", and `VERIFY_METHODS`
    # says "a device that could name its own method could name a flattering
    # one". Nothing enforced either sentence: the allowlist admits the KEY, and
    # the value went through untouched.
    #
    # W03-E is the first consumer that depends on the closure --- its console
    # renders a confirmed-success sentence for a device's `succeeded` when the
    # method is not one it recognises as unverified --- so an invented method
    # read as confirmation on the operator surface. Neither package could see
    # that alone. This function already calls itself "the enforcement boundary
    # and not a convenience for well-behaved callers", so the enforcement
    # belongs here, over whatever a device POSTed.
    method = out.get("verify_method")
    if method is not None and method not in VERIFY_METHODS:
        out["verify_method"] = "unavailable"
        dropped = out.get("dropped_keys")
        marker = "verify_method:not_in_vocabulary"
        out["dropped_keys"] = f"{dropped},{marker}" if dropped else marker

    return out


@dataclass(frozen=True)
class ActionResult:
    """The outcome of one action, as it is stored and as it is reported."""

    action_id: str
    tenant_id: str
    device_id: str
    status: ActionResultStatus
    #: Populated for every status except `SUCCEEDED` and `ACCEPTED`; None
    #: means "nothing went wrong", not "nobody looked".
    error_category: ErrorCategory | None = None
    #: One short, redacted, human-readable line. Never content.
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    observed_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", bounded_evidence(self.evidence))
        object.__setattr__(self, "detail", str(self.detail or "")[:500])

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def succeeded(self) -> bool:
        """True only for an observed success. `UNKNOWN` is deliberately False.

        This property exists so no caller has to write `status != FAILED`,
        which is the expression that turns an unknown outcome into a success.
        """
        return self.status is ActionResultStatus.SUCCEEDED

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "tenant_id": self.tenant_id,
            "device_id": self.device_id,
            "status": self.status.value,
            "error_category": self.error_category.value if self.error_category else None,
            "detail": self.detail,
            "evidence": dict(self.evidence),
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class HandlerOutcome:
    """What one capability handler observed. The device-side half of a result.

    Deliberately a smaller type than `ActionResult`: a handler knows what it
    saw, and knows nothing about tenants, action ids or governance. The channel
    turns one of these into an `ActionResult`.

    A handler that constructs `HandlerOutcome(SUCCEEDED, ...)` is making a
    claim about something it observed, and the reviewer of that handler should
    be able to point at the observation. Where it cannot, the correct
    construction is `HandlerOutcome.unverifiable(...)`.
    """

    status: ActionResultStatus
    error_category: ErrorCategory | None = None
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in DEVICE_REPORTABLE_STATUSES:
            raise ValueError(
                f"a handler may not report {self.status.value!r}; a device reports what "
                f"it observed, and {sorted(s.value for s in DEVICE_REPORTABLE_STATUSES)} "
                "are the observations it can make.",
            )
        if self.status is not ActionResultStatus.SUCCEEDED and self.error_category is None:
            raise ValueError(
                f"a {self.status.value!r} outcome must name an error category, so an "
                "audit can count causes rather than parse prose",
            )
        object.__setattr__(self, "evidence", bounded_evidence(self.evidence))
        object.__setattr__(self, "detail", str(self.detail or "")[:500])

    @classmethod
    def succeeded(cls, detail: str = "", **evidence: Any) -> HandlerOutcome:
        """An observed success. Only construct this having read the effect back."""
        return cls(ActionResultStatus.SUCCEEDED, None, detail, evidence)

    @classmethod
    def failed(cls, category: ErrorCategory, detail: str = "", **evidence: Any) -> HandlerOutcome:
        """An observed non-effect: the action ran and demonstrably did not work."""
        return cls(ActionResultStatus.FAILED, category, detail, evidence)

    @classmethod
    def unverifiable(cls, detail: str, **evidence: Any) -> HandlerOutcome:
        """The effect could not be observed either way.

        The honest answer when a call returned without error but the effect
        could not be read back -- which is a different thing from failure and
        must never be recorded as either success or failure.
        """
        return cls(
            ActionResultStatus.UNKNOWN,
            ErrorCategory.EFFECT_UNVERIFIABLE,
            detail,
            evidence,
        )

    @classmethod
    def aborted_by_brake(
        cls,
        detail: str,
        *,
        steps_completed: int = 0,
        **evidence: Any,
    ) -> HandlerOutcome:
        """A halt stopped this action after it was leased. Never a success.

        `steps_completed` is the honest part: `0` means the abort signal was
        read before the handler was entered and nothing at all happened, which
        is the case the abort poll exists to produce. A non-zero count means a
        multi-step handler stopped part-way and some of its steps did take
        effect -- still an abort, but not an untouched machine, and an operator
        reading the row needs to be able to tell those apart.
        """
        return cls(
            ActionResultStatus.ABORTED_BY_BRAKE,
            ErrorCategory.PARKING_BRAKE,
            detail,
            {**evidence, "steps_completed": int(steps_completed)},
        )

    @classmethod
    def refused(cls, category: ErrorCategory, detail: str = "", **evidence: Any) -> HandlerOutcome:
        """The device itself refused before touching anything.

        Reported as `FAILED` with the refusing category rather than as
        `REFUSED`: `REFUSED` is Governance's word for its own decision, and a
        device that could use it would be claiming an authority it does not
        have.
        """
        return cls(ActionResultStatus.FAILED, category, detail, evidence)
