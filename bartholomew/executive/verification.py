"""The Verify leg, from the executive's side: issued is not succeeded.

Two sources of truth about what happened, and they are not interchangeable:

* **The device's own report**, recorded through W03-C's
  `record_action_result_through_runtime_contract` and readable as the action
  row's terminal state. The device is the only party that saw the call return.
  It is evidence that something was *attempted* and how it ended.
* **A read-back of the resulting Windows state**, through W03-A's published
  `read_back()` primitive (shared contract `observation-event`). It is evidence
  about the machine as it now stands, from a live consented capture session.

The rule this module holds is the one `W03_TEST_CONTRACTS.md` §5 requires:

    A device report of `succeeded`, on its own, is **not** a verification.

Where a read-back is available and confirms the expected state, the step is
`verified`. Where the read-back is unavailable --- no consented session, brake
engaged, provider missing, or the primitive not present in this tree at all ---
the step is `unknown`, and `unknown` is carried forward as `unknown`. It is
never rendered as success, and `recovery.py` treats it differently from a
failure precisely because "we cannot tell" and "it did not happen" call for
different next moves.

The read-back port
------------------
`read_back` is W03-A's, and its signature is the published one:

    read_back(*, tenant_id, device_id, target, requested_by, db_path, store)
      -> ReadBackResult(available, text, code, reason, event, event_id,
                        provenance_degraded, provenance_error)

It is resolved **by name at call time**, not imported at module scope, for one
reason that matters to Wave 3's sequencing: W03-B branches from `main` against
the *published signature*, while W03-A's implementation lives on its own frozen
head until W03-F integrates. A missing primitive therefore degrades to an
honest `unknown` rather than an import error --- which is also exactly the
behaviour a deployment with no live capture session must have.
"""

from __future__ import annotations

import importlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

# W03-C's result vocabulary, imported rather than restated. The defect this
# import prevents is exactly the one that restating it caused.
from bartholomew.actuation.result import ActionResultStatus

logger = logging.getLogger(__name__)

#: The verdicts. Three, and the third is not a soft second.
VERIFIED = "verified"
FAILED = "failed"
UNKNOWN = "unknown"

#: Where the verdict came from.
SOURCE_READ_BACK = "read_back"
SOURCE_DEVICE_RESULT = "device_result"
SOURCE_ABSENT = "absent"

# ---------------------------------------------------------------------------
# W03-F integration repair: which device statuses may reach a read-back
# ---------------------------------------------------------------------------
#
# W03-C added `ActionResultStatus.ABORTED_BY_BRAKE` as its eighth member, and
# its handoff §7.5 recorded the consequence: "Anything in W03-B or W03-E that
# exhausts either enum needs the new member." W03-B was written against W03-C's
# six-member vocabulary and never saw the addition, because no tree carried
# both packages until the W03-F integration head.
#
# The result was a governance defect, reproduced before this repair: a
# `aborted_by_brake` status matched none of the branches below, fell through to
# the read-back, and a read-back that happened to show the expected state
# returned VERIFIED --- so a Parking-Brake-aborted action reported success,
# advanced the plan, and had the next Windows action proposed for it while the
# brake was still engaged. `refused` had the same shape: an action the envelope
# never dispatched could verify because some other cause had left the machine in
# the expected state.
#
# So the rule is inverted. Rather than listing the statuses that are terminal
# and hoping the list keeps up with W03-C, this names the only two that may
# consult a read-back at all, and everything else fails closed. A ninth member
# added later reports `unknown` --- never a success --- without this file
# changing.
_ABORTED_BY_BRAKE = ActionResultStatus.ABORTED_BY_BRAKE.value
_REFUSED = ActionResultStatus.REFUSED.value

#: Statuses that settle the step from the device's own report. Mapping to the
#: detail each one contributes, so an explanation says which it was.
_TERMINAL_NOT_SUCCESS: dict[str, str] = {
    ActionResultStatus.FAILED.value: (
        "the device reported 'failed'; nothing is assumed beyond that"
    ),
    ActionResultStatus.CANCELLED.value: (
        "the device reported 'cancelled'; nothing is assumed beyond that"
    ),
    _REFUSED: (
        "the action was refused and never dispatched, so nothing was done and "
        "nothing about the machine's current state is attributable to it"
    ),
    _ABORTED_BY_BRAKE: (
        "the Parking Brake stopped this action after it was leased. That is "
        "terminal: it is not a success, and it is not a reason to propose it again"
    ),
}

#: The only two statuses a read-back may be consulted for. `succeeded` so the
#: machine's state can contradict the claim, and `unknown` so it can settle it.
_MAY_CONSULT_READ_BACK: frozenset[str] = frozenset(
    {ActionResultStatus.SUCCEEDED.value, ActionResultStatus.UNKNOWN.value},
)

#: The module path of W03-A's read-back primitive, resolved by name so this
#: package builds and runs on a tree where W03-A is not yet integrated.
READ_BACK_MODULE = "bartholomew.multimodal.readback"
READ_BACK_ATTRIBUTE = "read_back"


class ReadBackPort(Protocol):
    """W03-A's published read-back surface, as this package calls it."""

    def __call__(
        self,
        *,
        tenant_id: str,
        device_id: str,
        target: Any = None,
        requested_by: str,
        db_path: str | None = None,
        store: Any = None,
    ) -> Any: ...


def resolve_read_back_port() -> ReadBackPort | None:
    """W03-A's `read_back`, or `None` when this tree does not carry it.

    Never raises. An absent perception package is a reason to report `unknown`,
    not a reason for a governed task to crash.
    """
    try:
        module = importlib.import_module(READ_BACK_MODULE)
    except Exception:  # pragma: no cover - exercised by the absent-port test
        return None
    port = getattr(module, READ_BACK_ATTRIBUTE, None)
    return port if callable(port) else None


@dataclass(frozen=True)
class Verification:
    """What was established about one step, and on what evidence."""

    verdict: str
    source: str
    detail: str
    #: The device's reported terminal status, when there was one.
    device_status: str | None = None
    #: W03-A's unavailability code when a read-back could not be served ---
    #: `no_consented_session`, `parking_brake_engaged`, `outside_consented_scope`,
    #: and the rest of the published vocabulary. Carried through verbatim so an
    #: explanation can say why verification was not possible.
    read_back_code: str | None = None
    #: The backbone event id the read-back was itself recorded under, when one
    #: exists. A reference, never content.
    read_back_event_id: Any = None
    #: Digest-scale references only. This package never stores observed text.
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.verdict == VERIFIED

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "source": self.source,
            "detail": self.detail,
            "device_status": self.device_status,
            "read_back_code": self.read_back_code,
            "read_back_event_id": self.read_back_event_id,
            "evidence": dict(self.evidence),
        }


def _used_screenshot_fallback(result: Any) -> bool:
    """Whether W03-A served this read from a screenshot rather than the UI tree.

    Read defensively off the observation event W03-A attaches, because it is the
    only place the distinction is published and a port that omits it must not be
    read as "the UI tree was available".
    """
    event = getattr(result, "event", None)
    observed = getattr(event, "observed_event", None)
    facts = getattr(observed, "facts", None)
    if not isinstance(facts, dict):
        return False
    # Only a fallback when the accessibility read did NOT succeed: a read that
    # got both is real UI text.
    return bool(facts.get("used_screenshot")) and not bool(facts.get("available"))


def _observed_application(result: Any, summary: str) -> str | None:
    """Which application the read-back actually observed, if it says.

    W03-A publishes it two ways, and both are read here because a port may
    supply either: `observed_event.facts["application"]`, and --- when only the
    rendered text is available --- the `(app)` group of the summary line
    `loop.py` builds, `active window '<title>' (<app>): N readable control(s)`.

    `None` means the observation did not identify an application at all.
    """
    event = getattr(result, "event", None)
    observed = getattr(event, "observed_event", None)
    facts = getattr(observed, "facts", None)
    if isinstance(facts, dict):
        application = facts.get("application")
        if application:
            return str(application)
    match = re.search(r"active window .*?\(([^)]+)\)", summary)
    return match.group(1) if match else None


def _foreground_claim_holds(token: str, summary: str, result: Any) -> bool:
    """Whether the read-back shows the ACTION'S TARGET in the foreground.

    W03-F integration repair, and the one an adversarial audit of the composed
    head caught after the earlier `MATCH_IN_SUMMARY` narrowing. That narrowing
    confined a foreground claim to the summary line --- but the summary line
    embeds the observed window's TITLE, so an entirely unrelated window whose
    title merely mentions the app still satisfied the claim. Reproduced against
    the real W03-A read-back:

        device reports `unknown` (it honestly could not tell);
        the live consented session is observing Firefox, titled
        "How to use Notepad on Windows 11 - Mozilla Firefox";
        -> "notepad is in the foreground" returned VERIFIED.

    That is the wave's central rule inverted: an `effect_unverifiable` outcome
    became a confirmed one, on evidence about a different window. Nothing tied
    the read-back's scope to the action's target, and nothing could --- the
    executive supplies no `read_back_target`, so W03-A reads "whatever the live
    session is consented to observe", which need not be what the action touched.

    So the claim is now settled by the observed APPLICATION rather than by text
    anywhere in the summary. Where the observation names no application at all
    the older summary match stands, because a port that identifies nothing
    cannot be interrogated further; with the real W03-A provider that case does
    not arise on this path (an unavailable accessibility read emits no "active
    window" part and is already routed to `unknown` as a screenshot fallback).
    """
    needle = token.strip().lower()
    application = _observed_application(result, summary)
    if application is not None:
        # The app id and the observed image name are compared both ways so
        # "notepad" matches "notepad.exe" without "calc" matching "calculator".
        observed = application.strip().lower()
        stem = observed.rsplit(".", 1)[0] if "." in observed else observed
        return needle in {stem, observed}
    return needle in summary.lower()


def _read_back_evidence(result: Any, base: dict[str, Any]) -> dict[str, Any]:
    """`base`, plus W03-A's provenance degradation when it reported one.

    W03-A publishes `provenance_degraded` for a read that happened but could not
    be written to the backbone. W03-F carries it into the verdict's evidence
    rather than dropping it: the read stands, so the verdict stands, but a
    confirmation whose observation was never recorded cannot be reconstructed
    from the audit trail later, and an account that did not say so would be
    claiming a stronger record than exists.
    """
    evidence = dict(base)
    if bool(getattr(result, "provenance_degraded", False)):
        evidence["read_back_provenance_degraded"] = True
        error = getattr(result, "provenance_error", None)
        if error:
            evidence["read_back_provenance_error"] = str(error)[:200]
    return evidence


#: Where a token may be looked for in W03-A's read-back text.
#:
#: `observed_text()` builds one block: the observed summary on the first line,
#: then one line per observed UI element (`role: name = value [focused]`). Those
#: are different kinds of evidence and a foreground claim must not be settled by
#: the second kind.
MATCH_IN_SUMMARY = "summary"
MATCH_ANYWHERE = "anywhere"


def _expectation(capability: str, parameters: dict[str, Any]) -> tuple[str, str | None, str]:
    """What a successful step should have made true, the token, and where to look.

    Deliberately modest. The executive checks that the machine now shows
    something consistent with the action --- the application in the foreground,
    the typed text present in the read-back's observed half --- and reports
    `unknown` when it has no such check for a capability rather than inventing
    one. An expectation that could not fail would be worse than no expectation,
    because it would manufacture verifications. An expectation that is
    *backwards* is worse still, because it manufactures both verifications and
    contradictions --- which is what W03-F found here (see `manage_window`).

    The third element is the match scope. A "the app is in the foreground" claim
    is only evidenced by the read-back's own summary of what it observed;
    matching it against the element dump lets an app name in a taskbar button or
    another window's control settle it. A "the typed text is present" claim is
    the opposite: the text lives in a control, so element lines are exactly where
    it belongs.
    """
    if capability in ("windows.focus_window", "windows.launch_app"):
        app = str(parameters.get("app_id") or "").strip()
        return (f"{app} is in the foreground", app or None, MATCH_IN_SUMMARY)
    if capability == "windows.manage_window":
        # W03-F integration repair. This used to return the app id as the token
        # for EVERY operation, ignoring `operation` entirely --- so for
        # `minimize` the check was inverted: a minimize that worked leaves the
        # app out of the foreground, which read as `failed`, and a minimize that
        # did nothing left it there, which read as `verified`. For `move` and
        # `resize` the app's presence says nothing about whether it moved, so
        # the check was vacuous rather than backwards.
        #
        # Only the operations whose success is genuinely CONSISTENT with the app
        # being what the read-back describes keep a token. The rest report
        # `unknown`, which is what this function's own docstring prescribes for a
        # capability it has no check for. Narrowing a claim to the truth is a
        # repair; inventing a minimize-detector from a text dump would not be.
        app = str(parameters.get("app_id") or "").strip()
        operation = str(parameters.get("operation") or "").strip().lower()
        described = f"{app}'s window was {operation or parameters.get('operation')}d"
        if operation in ("focus", "maximize", "restore"):
            return (described, app or None, MATCH_IN_SUMMARY)
        return (
            f"{described} (no read-back check is defined for {operation or 'this'}: the "
            "app being on screen is not evidence either way)",
            None,
            MATCH_IN_SUMMARY,
        )
    if capability == "windows.type_text":
        text = str(parameters.get("text") or "")
        return ("the typed text is present in the focused control", text or None, MATCH_ANYWHERE)
    if capability == "windows.open_url":
        return ("the URL was opened in the default browser", None, MATCH_ANYWHERE)
    if capability == "windows.open_path":
        return ("the file or folder was opened", None, MATCH_ANYWHERE)
    if capability == "windows.accessibility_action":
        app = str(parameters.get("app_id") or "").strip()
        return (f"the control in {app} changed as asked", app or None, MATCH_ANYWHERE)
    return ("no read-back check is defined for this capability", None, MATCH_ANYWHERE)


def verify_step(
    *,
    capability: str,
    parameters: dict[str, Any],
    device_status: str | None,
    tenant_id: str,
    device_id: str,
    requested_by: str,
    db_path: str | None = None,
    read_back_port: ReadBackPort | None = None,
    read_back_target: Any = None,
    store: Any = None,
) -> Verification:
    """Establish what actually happened. Never optimistic, never fabricated.

    `device_status` is the action row's status from W03-C's result path --- one
    of `ActionResultStatus`'s values, or `None` when nothing has been reported
    yet.

    **Only `succeeded` and `unknown` are allowed to consult a read-back.** Every
    other status is settled here, from the device's own report, and can never
    become `VERIFIED`. See `_TERMINAL_NOT_SUCCESS` for why that is a
    fail-closed default rather than a list to keep up to date.
    """
    status = (device_status or "").strip().lower() or None

    if status is None:
        return Verification(
            verdict=UNKNOWN,
            source=SOURCE_ABSENT,
            detail="no device result has been recorded for this action yet",
            device_status=None,
        )
    if status in _TERMINAL_NOT_SUCCESS:
        return Verification(
            verdict=FAILED,
            source=SOURCE_DEVICE_RESULT,
            detail=_TERMINAL_NOT_SUCCESS[status],
            device_status=status,
        )
    if status not in _MAY_CONSULT_READ_BACK:
        # Fail closed on a status this build does not recognise --- including a
        # ninth `ActionResultStatus` member added after this line was written.
        # The alternative is what this repair exists to remove: an unrecognised
        # status falling through to the read-back, where a read-back that
        # happened to show the expected state returned VERIFIED for an action
        # that never ran.
        return Verification(
            verdict=UNKNOWN,
            source=SOURCE_DEVICE_RESULT,
            detail=(
                f"the device reported {status!r}, which this build does not recognise as "
                "a settled outcome; nothing is claimed about what happened"
            ),
            device_status=status,
        )
    if status == "unknown":
        # The envelope's honest "we cannot tell". A read-back may still settle
        # it, and that is the one case where a read-back can *upgrade* a
        # verdict rather than only confirm one.
        pass

    port = read_back_port if read_back_port is not None else resolve_read_back_port()
    expectation, token, match_scope = _expectation(capability, parameters)
    if port is None:
        return Verification(
            verdict=UNKNOWN,
            source=SOURCE_ABSENT,
            detail=(
                f"the device reported {status!r}, but no read-back primitive is "
                "available in this deployment, so the resulting state was not "
                f"observed. Expected: {expectation}."
            ),
            device_status=status,
        )

    try:
        result = port(
            tenant_id=tenant_id,
            device_id=device_id,
            target=read_back_target,
            requested_by=requested_by,
            db_path=db_path,
            store=store,
        )
    except Exception as exc:  # pragma: no cover - defensive; a read is not a decision
        logger.exception("Read-back raised while verifying %s", capability)
        return Verification(
            verdict=UNKNOWN,
            source=SOURCE_ABSENT,
            detail=(
                f"the device reported {status!r}; the read-back could not be performed "
                f"({type(exc).__name__}), so the resulting state was not observed"
            ),
            device_status=status,
        )

    available = bool(getattr(result, "available", False))
    code = getattr(result, "code", None)
    reason = getattr(result, "reason", None)
    event_id = getattr(result, "event_id", None)
    if not available:
        return Verification(
            verdict=UNKNOWN,
            source=SOURCE_READ_BACK,
            detail=(
                f"the device reported {status!r}; the state could not be read back "
                f"({code or 'unavailable'}: {reason or 'no reason given'}), so nothing "
                f"is claimed about whether {expectation}"
            ),
            device_status=status,
            read_back_code=code,
            read_back_event_id=event_id,
        )

    text = str(getattr(result, "text", "") or "")

    # W03-F integration repair: a screenshot-fallback read is not UI text.
    #
    # W03-A returns `available=True` when the accessibility read failed but a
    # screenshot fallback was used (`readback.py`: `readable = available or
    # used_screenshot`). In that case `text` is a prose description of pixels and
    # contains no control text at all --- so a token that is genuinely on screen
    # will not be in it, and the comparison below would have turned a truthful
    # `succeeded` into `FAILED` ("the state wins"). That is a manufactured
    # contradiction, which is the same untruth as a manufactured confirmation
    # pointed the other way. Nothing was read in the sense this check needs, so
    # the answer is `unknown`.
    if _used_screenshot_fallback(result):
        return Verification(
            verdict=UNKNOWN,
            source=SOURCE_READ_BACK,
            detail=(
                f"the device reported {status!r} and the target was read, but only by "
                "screenshot fallback --- no UI text was available, so nothing is claimed "
                f"about whether {expectation}"
            ),
            device_status=status,
            read_back_code=code,
            read_back_event_id=event_id,
            evidence=_read_back_evidence(result, {"read_back_chars": len(text)}),
        )

    if token is None:
        return Verification(
            verdict=UNKNOWN,
            source=SOURCE_READ_BACK,
            detail=(
                f"the device reported {status!r} and the state was read back, but this "
                f"capability has no defined read-back check ({expectation}), so the "
                "outcome is recorded as unverified rather than assumed"
            ),
            device_status=status,
            read_back_code=code,
            read_back_event_id=event_id,
            evidence=_read_back_evidence(result, {"read_back_chars": len(text)}),
        )

    # W03-F integration repair: match in the scope the claim is about.
    #
    # This used to be a bare substring test against the WHOLE observed block ---
    # the summary plus every element's `role: name = value`. For a foreground
    # claim that is far too wide: an app id appearing in a taskbar button, a
    # shortcut name or another window's control settles "X is in the foreground".
    # Foreground claims are now settled by the read-back's own summary line, and
    # content claims (the typed text) still match anywhere, because that is where
    # a control's value legitimately appears.
    if match_scope == MATCH_ANYWHERE:
        matched = token.strip().lower() in text.lower()
    else:
        # A foreground claim is about WHICH APPLICATION is in front, so it is
        # settled by the observed application --- not by the window title, which
        # is what let an unrelated window satisfy it. See
        # `_foreground_claim_holds`.
        matched = _foreground_claim_holds(token, text.split("\n", 1)[0], result)
    if matched:
        return Verification(
            verdict=VERIFIED,
            source=SOURCE_READ_BACK,
            detail=f"read back from the live session: {expectation}",
            device_status=status,
            read_back_event_id=event_id,
            evidence=_read_back_evidence(
                result,
                {"read_back_chars": len(text), "expectation": expectation},
            ),
        )
    if status == "succeeded":
        return Verification(
            verdict=FAILED,
            source=SOURCE_READ_BACK,
            detail=(
                f"the device reported 'succeeded', but the read-back does not show that "
                f"{expectation}. The read-back is the state of the machine and the "
                "report is a claim about it; the state wins."
            ),
            device_status=status,
            read_back_event_id=event_id,
            evidence=_read_back_evidence(
                result,
                {"read_back_chars": len(text), "expectation": expectation},
            ),
        )
    return Verification(
        verdict=UNKNOWN,
        source=SOURCE_READ_BACK,
        detail=(
            f"the device reported {status!r} and the read-back does not show that "
            f"{expectation}; neither establishes what happened"
        ),
        device_status=status,
        read_back_event_id=event_id,
        evidence=_read_back_evidence(
            result,
            {"read_back_chars": len(text), "expectation": expectation},
        ),
    )


__all__ = [
    "FAILED",
    "READ_BACK_ATTRIBUTE",
    "READ_BACK_MODULE",
    "SOURCE_ABSENT",
    "SOURCE_DEVICE_RESULT",
    "SOURCE_READ_BACK",
    "UNKNOWN",
    "VERIFIED",
    "ReadBackPort",
    "Verification",
    "resolve_read_back_port",
    "verify_step",
]
