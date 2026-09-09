"""The action companion's lifecycle: lease, check, dispatch, record, report. In that order.

The `check` is W03-C's addition and it sits where it does on purpose. A lease
is not a standing permission -- it is a statement about a moment, and by the
time a handler is about to touch the machine that moment has passed. So between
the lease and the handler the companion re-reads the server's abort signal, and
a halt engaged in that gap stops the action before anything happens. `dispatch`
is also handed a gate it can call inside a long wait, so a halt engaged while
`launch_app` is waiting for a window stops it there too.

This is a **cooperative** stop, and it is worth being exact about what that
does not cover: a companion that has crashed, wedged, or been stopped from
polling is not stopped by any of it. The independent out-of-process emergency
stop is a separate, deferred package (`docs/waves/W03/W03_DEFERRALS.md` #8),
and building a weaker thing and calling it that would be worse than building
the weaker thing and saying so.

The order is the whole design, and the interesting part is that **recording
comes before reporting**. The ledger entry for an executed action is written to
disk before the result is sent, so a companion that dies between acting and
reporting comes back knowing it already acted. The alternative order -- report,
then record -- loses exactly that, and loses it in the case that matters:
a crash right after a `launch_app` would otherwise launch it again on restart.

An unreported result is retried at the top of the next cycle from the ledger,
so an outcome is not lost either. Both properties come from the same file, and
`poll_once` is where the retry happens -- doing it once at start-up instead
would lose every outcome whose refusal cleared without the process restarting.

Nothing else happens here. There is no branch in this module driven by anything
in a response body other than the typed `LeasedAction` list, and every one of
those goes through `dispatch.check()`'s four refusals first.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from bartholomew.actuation.result import ActionResultStatus, ErrorCategory, HandlerOutcome

from .channel import ActionChannelClient, ChannelStatus
from .config import ActionCompanionConfig
from .dispatch import AbortSignal, LeasedAction, abort_deadline_passed, dispatch
from .handlers import HandlerContext
from .state import ActionCompanionState, ActionStateFile, ExecutedEntry, LedgerUnreadableError

logger = logging.getLogger(__name__)

#: Backoff between retryable channel attempts. Fixed and short: the companion
#: is talking to a service on the same machine or the same network.
RETRY_BACKOFF_SECONDS = (2.0, 5.0, 10.0)

#: Shortest gap between two mid-flight abort reads. The gate a handler holds is
#: called inside a wait loop, so without a floor a four-second `launch_app`
#: wait would issue a dozen HTTP calls. One second is far inside the server's
#: own `ABORT_CLEARANCE_SECONDS`, so the refresh is never the reason a
#: clearance goes stale.
ABORT_REFRESH_MIN_SECONDS = 1.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class ActionRunSummary:
    """What one run did. Counted separately, because they are different things."""

    leased: int = 0
    succeeded: int = 0
    failed: int = 0
    unknown: int = 0
    refused_locally: int = 0
    replayed: int = 0
    unreported: int = 0
    channel_refusals: int = 0
    #: Actions a halt stopped after they had been leased. Counted separately
    #: from `failed` because they are not failures: nothing was wrong with the
    #: action, and an operator reading a run summary wants to see that the
    #: brake did something rather than that four actions broke.
    aborted: int = 0


class ActionCompanionRunner:
    """Drives one action companion process. One direction per phase, no loops back."""

    def __init__(
        self,
        config: ActionCompanionConfig,
        *,
        client: ActionChannelClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.client = client or ActionChannelClient(
            config.base_url,
            device_id=config.device_id,
            credential_headers=config.credential_headers,
        )
        self._sleep = sleep
        self._state_file = ActionStateFile(config.state_path)
        self.handler_context = HandlerContext(config=config)
        self.summary = ActionRunSummary()
        # Deliberately not caught: an unreadable ledger must stop the companion
        # starting, not be worked around. See `state.LedgerUnreadableError`.
        self.state: ActionCompanionState = self._state_file.load()

    # -- ledger ------------------------------------------------------------

    def _record_executed(self, action_id: str, outcome: HandlerOutcome) -> None:
        """Write the ledger entry. Before reporting, always."""
        self.state.executed[action_id] = ExecutedEntry(
            action_id=action_id,
            status=outcome.status.value,
            observed_at=_utc_now_iso(),
            reported=False,
        )
        self._state_file.save(self.state)

    def _mark_reported(self, action_id: str) -> None:
        entry = self.state.executed.get(action_id)
        if entry is None:
            return
        entry.reported = True
        self._state_file.save(self.state)

    # -- reporting ---------------------------------------------------------

    def report(self, action_id: str, outcome: HandlerOutcome, observed_at: str) -> bool:
        """Send one outcome, retrying only what is worth retrying.

        Two terminal answers, and they are **not** the same:

        * **409 REJECTED** -- the server already ended this action and declined
          the result. That is the replay protection working, the server has an
          opinion and it will not change, so the outcome is marked reported and
          the loop stops.
        * **401/403 REFUSED** -- this companion is not authenticated for the
          action channel. Retrying now is pointless, so the loop stops -- but
          the outcome is deliberately **left unreported**, because the server
          never heard it and a rotated credential will be fixed. Marking it
          reported here would have discarded, permanently and invisibly, the
          record of an action that really ran: the device's ledger would say
          "reported", `resend_unreported()` would find nothing, and the server's
          row would sit at `leased` until it was swept to `cancelled/expired` --
          an action that happened, recorded as one that never did.
        """
        for attempt in range(self.config.max_attempts):
            result = self.client.report(
                action_id=action_id,
                outcome=outcome,
                observed_at=observed_at,
            )
            if result.ok:
                self._mark_reported(action_id)
                return True
            if result.status is ChannelStatus.REJECTED:
                logger.error(
                    "The action channel declined the result for %s (%s): %s",
                    action_id,
                    result.http_status,
                    result.detail,
                )
                self._mark_reported(action_id)
                return True
            if result.status is ChannelStatus.REFUSED:
                self.summary.channel_refusals += 1
                self.summary.unreported += 1
                logger.error(
                    "The action channel refused this companion while reporting %s "
                    "(%s): %s. The outcome is kept for re-delivery once the "
                    "credential works; it is NOT discarded.",
                    action_id,
                    result.http_status,
                    result.detail,
                )
                return False
            if attempt + 1 < self.config.max_attempts:
                self._sleep(
                    RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)],
                )
        self.summary.unreported += 1
        return False

    def resend_unreported(self) -> int:
        """Re-report outcomes this companion observed but never got to send.

        The other half of recording-before-reporting: an outcome recorded by a
        process that then died is still delivered, once, by whichever process
        comes back. The stored status is re-sent verbatim -- never upgraded,
        and in particular never turned from `unknown` into `succeeded` by a
        later process that has no more information than the one that recorded
        it.
        """
        pending = [entry for entry in self.state.executed.values() if not entry.reported]
        for entry in pending:
            try:
                status = ActionResultStatus(entry.status)
            except ValueError:  # pragma: no cover - the ledger writes valid values
                status = ActionResultStatus.UNKNOWN
            if status is ActionResultStatus.SUCCEEDED:
                outcome = HandlerOutcome.succeeded("recorded before this companion restarted")
            elif status is ActionResultStatus.UNKNOWN:
                outcome = HandlerOutcome(
                    status,
                    ErrorCategory.EFFECT_UNVERIFIABLE,
                    "recorded before this companion restarted",
                )
            elif status is ActionResultStatus.ABORTED_BY_BRAKE:
                # Re-sent as an abort, with the same reason it was recorded
                # with. Turning it into an `INTERNAL_ERROR` would have told the
                # audit that a halt working correctly was a fault.
                outcome = HandlerOutcome.aborted_by_brake(
                    "recorded before this companion restarted",
                )
            else:
                outcome = HandlerOutcome(
                    status,
                    ErrorCategory.INTERNAL_ERROR,
                    "recorded before this companion restarted",
                )
            self.report(entry.action_id, outcome, entry.observed_at)
        return len(pending)

    # -- stop after lease --------------------------------------------------

    def abort_signal(self, action_ids: list[str]) -> AbortSignal:
        """Read the server's abort signal for the actions this companion holds.

        The device half of stop-after-lease, and the one call in this class
        whose failure mode is "act less". `ActionChannelClient.abort_check`
        returns an unreadable signal for every failure, and an unreadable
        signal permits nothing, so a companion that cannot reach the server
        stops rather than proceeding on the strength of a lease it was granted
        some seconds ago.
        """
        _result, signal = self.client.abort_check(action_ids=action_ids)
        if not signal.readable:
            logger.warning(
                "The abort signal could not be read (%s); nothing this companion "
                "holds will be run until it can be.",
                signal.reason,
            )
        elif signal.halted:
            logger.warning("A halt is engaged: %s", signal.reason)
        return signal

    def _abort_gate(self, action_id: str) -> Callable[[], str | None]:
        """A callable a handler may use to ask whether it should stop mid-flight.

        Rate-limited to `ABORT_REFRESH_MIN_SECONDS` so a handler's wait loop
        does not become a request loop, and closed over exactly one action id
        so a handler cannot use it to ask about -- let alone influence --
        anything else. It returns a reason string or None and has no other
        return value, which is what keeps a mid-flight hook from becoming a
        mid-flight instruction.
        """
        last: dict[str, float] = {}

        def gate() -> str | None:
            now = time.monotonic()
            previous = last.get("at")
            if previous is not None and now - previous < ABORT_REFRESH_MIN_SECONDS:
                return None
            last["at"] = now
            signal = self.abort_signal([action_id])
            if signal.may_run(action_id):
                return None
            return signal.reason or "this action is no longer runnable"

        return gate

    def _record_abort(self, action: LeasedAction, reason: str) -> HandlerOutcome:
        """Record and report one action a halt stopped before its handler ran."""
        outcome = HandlerOutcome.aborted_by_brake(
            reason or "a halt was engaged after this action was leased",
            steps_completed=0,
        )
        self.summary.aborted += 1
        observed_at = _utc_now_iso()
        self._record_executed(action.action_id, outcome)
        self.report(action.action_id, outcome, observed_at)
        return outcome

    # -- one action --------------------------------------------------------

    def run_action(self, action: LeasedAction, signal: AbortSignal | None = None) -> HandlerOutcome:
        """Dispatch one leased action and record what happened, in that order.

        The abort read comes **first**, before `dispatch`, and that placement is
        the whole of acceptance criterion 2: between the moment the server
        leased this action and the moment its handler touches the machine,
        there is now one point at which an engaged brake stops it. `signal` is
        passed in when a batch was read once for all of them; a caller that
        passes nothing gets a read for this action alone rather than a default
        of "carry on".

        **A stale clearance is refreshed, not treated as a halt.** The deadline
        bounds how long a halt may go unhonoured on this device -- it is not a
        budget for how long a batch may take. A lease of ten actions where the
        first spends five seconds waiting for a window would otherwise leave
        every later action past the clearance the *lease* stamped, and
        `dispatch.check()` would refuse them with `PARKING_BRAKE` while no
        brake was engaged anywhere: a halt that never happened, written into a
        durable audit row. So the signal is re-read when its clearance has
        expired, and the action carries the server's newest stamp into the
        check. If that re-read fails or says stop, this stops -- the refresh
        can only ever confirm, never extend on the device's own authority.
        """
        established = signal if signal is not None else self.abort_signal([action.action_id])
        if established.readable and abort_deadline_passed(established.clearance_deadline):
            established = self.abort_signal([action.action_id])
        if not established.may_run(action.action_id):
            return self._record_abort(action, established.reason)

        cleared = (
            replace(action, abort_deadline=established.clearance_deadline)
            if established.clearance_deadline
            else action
        )
        context = HandlerContext(
            config=self.config,
            abort_gate=self._abort_gate(action.action_id),
        )
        outcome = dispatch(cleared, context, self.state)
        observed_at = _utc_now_iso()

        if outcome.status is ActionResultStatus.SUCCEEDED:
            self.summary.succeeded += 1
        elif outcome.status is ActionResultStatus.UNKNOWN:
            self.summary.unknown += 1
        elif outcome.status is ActionResultStatus.ABORTED_BY_BRAKE:
            self.summary.aborted += 1
        else:
            self.summary.failed += 1
            if outcome.error_category is ErrorCategory.REPLAY_REFUSED:
                self.summary.replayed += 1
            elif outcome.error_category in (
                ErrorCategory.PARAMETERS_INVALID,
                ErrorCategory.CAPABILITY_NOT_DECLARED,
                ErrorCategory.CAPABILITY_UNSUPPORTED,
                ErrorCategory.DEVICE_NOT_ENROLLED,
                ErrorCategory.SENSITIVE_CONTENT,
                ErrorCategory.SENSITIVE_FIELD,
                ErrorCategory.EXPIRED,
            ):
                self.summary.refused_locally += 1

        self._record_executed(action.action_id, outcome)
        self.report(action.action_id, outcome, observed_at)
        return outcome

    # -- lifecycle ---------------------------------------------------------

    def poll_once(self) -> list[HandlerOutcome]:
        """One cycle: re-send what is owed, then lease what is waiting.

        The re-send comes **first, every cycle**, not once at start-up. A
        refused report is deliberately left unreported so it can be delivered
        when the credential works again -- and a companion that only retried on
        process start never delivered it, because the condition that refused it
        (a rotated credential, a resolver that came back) clears server-side
        without the client restarting. The outcome sat in the ledger for days
        while the server's row stayed `leased`: exactly the "an action that
        happened, recorded as one that never did" this was supposed to prevent.
        Cheap when there is nothing owed -- it iterates an empty list.
        """
        self.resend_unreported()
        result, actions, malformed = self.client.lease(limit=self.config.lease_batch)
        if malformed:
            logger.warning(
                "The action channel returned %d entries this companion could not read: %s",
                len(malformed),
                malformed,
            )
        if not result.ok:
            if result.status is ChannelStatus.REFUSED:
                self.summary.channel_refusals += 1
                logger.error(
                    "The action channel refused this companion (%s): %s. This is a "
                    "credential or enrolment problem, not a transient one.",
                    result.http_status,
                    result.detail,
                )
            return []

        self.summary.leased += len(actions)
        if not actions:
            return []
        # One abort read for the whole batch rather than one per action: the
        # question "is a halt engaged" has the same answer for all of them, and
        # asking it once keeps the gap between the read and the first handler
        # as small as it can be.
        signal = self.abort_signal([a.action_id for a in actions])
        return [self.run_action(action, signal) for action in actions]

    def run(self, *, cycles: int | None = None) -> ActionRunSummary:
        """Run the companion: resend, then poll until interrupted.

        `cycles=None` runs until interrupted, which is the real deployment.
        A bounded `cycles` is what the tests use, so the same code path is
        exercised rather than a test-only variant of it.

        The re-send at the top of every cycle lives in `poll_once`, so it
        happens on each pass rather than only here.
        """
        completed = 0
        try:
            while cycles is None or completed < cycles:
                self.poll_once()
                completed += 1
                if cycles is None or completed < cycles:
                    self._sleep(self.config.poll_seconds)
        except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
            logger.info("Action companion interrupted")
        return self.summary


def load_state_or_explain(config: ActionCompanionConfig) -> tuple[ActionCompanionState | None, str]:
    """Read the ledger, or return the operator-readable reason it could not be.

    Used by the diagnostics command so `status` can explain a refusing
    companion without starting one.
    """
    try:
        return ActionStateFile(config.state_path).load(), ""
    except LedgerUnreadableError as e:
        return None, str(e)
