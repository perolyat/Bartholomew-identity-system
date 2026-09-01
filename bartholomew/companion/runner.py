"""The companion loop: observe, envelope, deliver, record. In that order, only.

The whole of the companion's behaviour is here, and it is a loop with one
direction. It pulls observations from a source, turns each into the existing
inbound envelope, submits it, and records the outcome in its own state file.
Nothing flows the other way: `deliver()` looks at a `DeliveryStatus` and at
nothing else, so there is no expression in this module -- not a branch, not a
callback, not a handler table -- by which anything Bartholomew returns can reach
the machine the companion runs on.

**Delivery-before-advance.** The sequence number and the pending envelope are
written to the state file *before* the first submission attempt. A companion
killed mid-flight therefore restarts knowing exactly which envelope was in
doubt, and re-submits that same envelope -- same content, same derived
`event_id` -- so the inbound seam's `UNIQUE(source_id, event_id)` constraint
collapses the retry onto the existing row. Duplicate delivery is expected and
handled; duplicate *capture* is prevented by the boundary that already prevents
it, not by a second mechanism here.

**Retries stop where retrying is dishonest.** A 401/403 (unverified source) and
a 422 (malformed envelope) are terminal: repeating them cannot change the
answer, and a companion that hammered a closed door would look like a liveness
problem instead of the configuration problem it is. A 503 -- the Parking Brake
engaged, or persistence unavailable -- is retried with backoff, because that one
genuinely does resolve.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import observation as obs
from .client import DeliveryResult, DeliveryStatus, InboundSubmitClient
from .config import COMPANION_VERSION, CompanionConfig
from .envelope import to_inbound_envelope
from .observation import DeviceObservation
from .probes import platform_name
from .sources import ObservationSource
from .state import CompanionState, StateFile

logger = logging.getLogger(__name__)

#: Backoff between retryable attempts, in seconds. Fixed and short: the
#: companion is talking to a service on the same machine or the same network,
#: and an elaborate backoff policy would be more code than the problem.
RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0)


@dataclass
class RunSummary:
    """What one run did. Counted separately, because they are different things."""

    captured: int = 0
    duplicates: int = 0
    refused: int = 0
    invalid: int = 0
    undelivered: int = 0


class CompanionRunner:
    """Drives one companion process. Observation-only, one direction."""

    def __init__(
        self,
        config: CompanionConfig,
        source: ObservationSource,
        *,
        client: InboundSubmitClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.source = source
        self.client = client or InboundSubmitClient(
            config.base_url,
            credential_headers=config.credential_headers,
        )
        self._sleep = sleep
        self._state_file = StateFile(config.state_path)
        self.state: CompanionState = self._state_file.load()
        self.summary = RunSummary()

    # -- state -------------------------------------------------------------

    def _next_sequence(self) -> int:
        return self.state.sequence

    def _record(self, *, sequence: int, pending: dict[str, Any] | None) -> None:
        self.state.sequence = sequence
        self.state.pending = pending
        self._state_file.save(self.state)

    # -- delivery ----------------------------------------------------------

    def deliver(self, envelope: dict[str, Any]) -> DeliveryResult:
        """Submit one envelope, retrying only what is worth retrying."""
        result = DeliveryResult(DeliveryStatus.RETRYABLE, None, "not attempted")
        for attempt in range(self.config.max_attempts):
            result = self.client.submit(envelope)
            if result.delivered:
                return result
            if result.status in (DeliveryStatus.REFUSED, DeliveryStatus.INVALID):
                logger.error(
                    "Inbound refused event %s (%s): %s",
                    envelope.get("event_id"),
                    result.http_status,
                    result.detail,
                )
                return result
            if attempt + 1 < self.config.max_attempts:
                backoff = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "Inbound delivery of %s not completed (%s); retrying in %.0fs",
                    envelope.get("event_id"),
                    result.detail,
                    backoff,
                )
                self._sleep(backoff)
        return result

    def _tally(self, result: DeliveryResult) -> None:
        if result.status is DeliveryStatus.CAPTURED:
            self.summary.captured += 1
        elif result.status is DeliveryStatus.DUPLICATE:
            self.summary.duplicates += 1
        elif result.status is DeliveryStatus.REFUSED:
            self.summary.refused += 1
        elif result.status is DeliveryStatus.INVALID:
            self.summary.invalid += 1
        else:
            self.summary.undelivered += 1

    def submit_observation(self, observation: DeviceObservation) -> DeliveryResult:
        """Envelope one observation, persist it as pending, deliver it, clear it."""
        envelope = to_inbound_envelope(observation, source_id=self.config.source_id)
        # Pending is written first, so a crash after this point leaves a record
        # of exactly what was in flight.
        self._record(sequence=observation.sequence + 1, pending=envelope)
        result = self.deliver(envelope)
        self._tally(result)
        if result.delivered or result.status in (DeliveryStatus.REFUSED, DeliveryStatus.INVALID):
            # Recorded, or permanently refused. Either way it is no longer in
            # doubt, so it must not be re-sent on the next start.
            self._record(sequence=self.state.sequence, pending=None)
        return result

    # -- lifecycle ---------------------------------------------------------

    def resume_pending(self) -> DeliveryResult | None:
        """Re-deliver the envelope that was in flight when this companion died.

        Returns None when there was nothing in doubt. The re-delivery carries
        the original `event_id`, so a successful first attempt that was never
        acknowledged comes back as a 200 duplicate rather than a second event.
        """
        pending = self.state.pending
        if not pending:
            return None
        logger.info(
            "Resuming an in-flight observation from a previous run: %s",
            pending["event_id"],
        )
        result = self.deliver(pending)
        self._tally(result)
        if result.delivered or result.status in (DeliveryStatus.REFUSED, DeliveryStatus.INVALID):
            self._record(sequence=self.state.sequence, pending=None)
        return result

    def announce(self, *, online: bool) -> DeliveryResult:
        """Report that this companion came up, or is going down."""
        seq = self._next_sequence()
        return self.submit_observation(obs.presence(self.config.device_id, seq, online=online))

    def report_system_state(self) -> DeliveryResult:
        seq = self._next_sequence()
        return self.submit_observation(
            obs.system_state(
                self.config.device_id,
                seq,
                platform_name=platform_name(),
                companion_version=COMPANION_VERSION,
            ),
        )

    def poll_once(self) -> list[DeliveryResult]:
        """One observation cycle."""
        results = []
        for observation in self.source.poll(self._next_sequence()):
            results.append(self.submit_observation(observation))
        return results

    def run(self, *, cycles: int | None = None) -> RunSummary:
        """Run the companion: resume, announce, poll, and sign off.

        `cycles=None` runs until interrupted, which is the real deployment.
        A bounded `cycles` is what the tests use, so the same code path is
        exercised rather than a test-only variant of it.
        """
        self.resume_pending()
        self.announce(online=True)
        self.report_system_state()
        completed = 0
        try:
            while cycles is None or completed < cycles:
                self.poll_once()
                completed += 1
                if cycles is None or completed < cycles:
                    self._sleep(self.config.poll_seconds)
        except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
            logger.info("Companion interrupted; reporting offline")
        finally:
            # Best effort: an offline marker that could not be delivered is
            # simply absent. It is never faked, and nothing here reports an
            # undelivered observation as delivered.
            try:
                self.announce(online=False)
            except Exception:  # pragma: no cover - defensive
                logger.warning("Could not report the offline marker", exc_info=True)
        return self.summary
