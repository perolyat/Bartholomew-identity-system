"""The External Capability Interface core: one admission order, no decisions of its own.

This is the governed boundary itself, and the most important thing to say
about it is what it does **not** do.

It does not decide what Taylor wants. It does not decide what Bartholomew
should prioritise, which objective matters, whether an endpoint's suggestion
should override anything, or what the system should do next. There is no
branch below that reads an endpoint's payload for content and chooses a code
path from it. Those are executive questions and they are answered in
`bartholomew/kernel/runtime_contract.py` and the seams that compose it.

What this module does is narrow and mechanical: it validates, it consults
authorities that already exist, it records, it correlates, and it carries.
A boundary is allowed to refuse; it is not allowed to decide.

The admission order, which is the contract
------------------------------------------
Every submission passes these in this order, and each step is either an
authority already in use elsewhere or a check this boundary owns:

===  ==============================  ==========================================
 #   check                            authority
===  ==============================  ==========================================
 1   authenticated principal          the API boundary, before this is reached
                                      (`platform/http_identity.py`)
 2   verified endpoint identity       `platform/devices.verify_device_credential`
                                      -- resolved by the transport and passed
                                      in. A boundary that cannot say who is
                                      speaking cannot construct an
                                      `EndpointIdentity` at all.
 3   runtime binding                  the platform's, never the endpoint's
                                      claim (`platform/device_inbound.py`)
 4   contract + envelope shape        `contract.InboundExchange.__post_init__`
 5   Parking Brake, both tiers        `governance_store`, read fail-closed
                                      inside (7) -- one brake, one read
 6   event-backbone backpressure      ditto
 7   Identity policy + durable        `runtime_contract.
     capture + provenance              run_inbound_through_runtime_contract()`
 8   correlation (results only)       `store.settle_directive()`, exactly-once
 9   capability standing              `capabilities.resolve_standing()`
10   the Bartholomew responder        a registered Bartholomew-owned seam,
                                      which runs its **own** governance
===  ==============================  ==========================================

Steps 5-7 are one call on purpose. `run_inbound_through_runtime_contract()`
already reads the Parking Brake fail-closed through `GovernanceStore`,
composing the Personal brake and the S8 Platform/Admin halt at Governance's
own composition point; already applies backpressure; already evaluates the
Identity policy; already captures idempotently; and already writes the
`ActionReflection` that is this exchange's provenance. Reading the brake again
here would be a *second* place that believes it knows the halt rule, which is
exactly the duplication this work package forbids. There is no brake in this
package, no second consent gate, no second audit store and no second identity.

The responder seam, and why it is empty by default
--------------------------------------------------
Bartholomew issues directives; the ECI carries them. That separation is held
by a registration hook rather than by a convention: this module declares an
`ExchangeResponder` protocol and installs **nothing**. With no responder, an
exchange is captured and the endpoint is told so, and no directive is ever
produced -- the honest state for a deployment that has not wired an executive
in. The real responder lives in `bartholomew/integration/`, the repository's
adapter layer, and is installed at startup like every other seam.

A responder cannot smuggle authority through this seam either. It is handed a
`DirectiveIssuer` that refuses to mint a directive for a capability the
endpoint has not declared, is not authorised for, or has not reported itself
able to serve -- so the answer to "may this be asked of this endpoint" is
given by the platform's registration state, not by the component that wants
the answer to be yes.

Inbound content is data
-----------------------
Nothing below reads `payload` for instructions. An external web page, tool
response, device or person can put any words they like inside an exchange;
those words become a captured row and a digest, and they never become
authority. There is no path from payload content to a governance decision, a
capability grant, an identity, or a directive's parameters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from bartholomew.kernel.event_processing.adapters import (
    ECI_OBSERVATION,
    ECI_REQUEST,
    ECI_RESULT,
)

from .capabilities import AvailabilityReport, CapabilityDecision, resolve_standing
from .contract import (
    CapabilityRef,
    ContractError,
    EndpointIdentity,
    ExchangeKind,
    ExchangeOutcome,
    ExchangeReceipt,
    GovernedDirective,
    InboundExchange,
    new_correlation_id,
    new_directive_id,
    utc_now,
)
from .store import (
    EciPersistenceError,
    SettleOutcome,
    ensure_schema,
    get_availability,
    record_availability,
    record_directive,
    settle_directive,
)

logger = logging.getLogger(__name__)

#: The captured `event_type` for each exchange kind. The names are imported
#: from the event backbone rather than defined here: `event_processing/
#: adapters.py` is "the only place the backbone learns a type", and the
#: dependency points from this consumer to that owner rather than the reverse.
_EVENT_TYPES: dict[ExchangeKind, str] = {
    ExchangeKind.OBSERVATION: ECI_OBSERVATION,
    ExchangeKind.REQUEST: ECI_REQUEST,
    ExchangeKind.RESULT: ECI_RESULT,
}


class CapabilityRefusedError(RuntimeError):
    """A directive was asked for that this endpoint may not be given.

    Raised by `DirectiveIssuer.issue()` rather than returned, so that a
    Bartholomew seam calling it from inside its own execution step fails
    honestly: the seam records an unsuccessful outcome, and nothing reports a
    capability as exercised when it was refused.
    """

    def __init__(self, decision: CapabilityDecision):
        super().__init__(decision.reason)
        self.decision = decision


class ExchangeResponder(Protocol):
    """A Bartholomew-owned seam that may answer an exchange with a directive.

    Returns `None` whenever Bartholomew has nothing to say, which is the
    ordinary case and not a failure. Raising is also "nothing to say": the
    boundary logs it and reports the exchange as accepted, because the
    exchange genuinely was captured and a responder fault must not be
    reported to an endpoint as a capture failure.

    **An implementation is expected to run its own governance.** The reference
    responder calls `run_spoken_output_through_runtime_contract()`, whose
    enablement, `ParkingBrake("voice")` and Identity-policy gates all decide
    before it produces anything. The boundary does not govern on a responder's
    behalf and must not be mistaken for a component that has.
    """

    async def respond(
        self,
        exchange: InboundExchange,
        issuer: DirectiveIssuer,
    ) -> GovernedDirective | None: ...


_RESPONDER: dict[str, ExchangeResponder | None] = {"responder": None}


def install_responder(responder: ExchangeResponder) -> None:
    """Install the Bartholomew-owned responder. Called from startup wiring."""
    _RESPONDER["responder"] = responder
    logger.info(
        "External Capability Interface: responder installed (%s). Bartholomew may now "
        "issue governed directives through this boundary.",
        type(responder).__name__,
    )


def clear_responder() -> None:
    """Remove the responder. The fail-closed state, and the default."""
    _RESPONDER["responder"] = None


def responder_installed() -> bool:
    return _RESPONDER["responder"] is not None


@dataclass
class DirectiveIssuer:
    """The only way a directive can come into existence, and its guard rail.

    Handed to a responder for the duration of one exchange. It answers
    `standing()` cheaply so a seam can decline before doing work, and
    `issue()` mints, records and returns a directive -- refusing outright if
    the endpoint's registration state does not admit the capability.

    A directive is written to the correlation ledger **before** it is
    returned, never after. A directive an endpoint holds but the ledger does
    not know about is one whose result could never be correlated, so the write
    is what makes handing it over safe.
    """

    endpoint: EndpointIdentity
    device: Any
    db_path: str
    now: datetime
    issued: GovernedDirective | None = None

    def standing(self, capability: CapabilityRef) -> CapabilityDecision:
        """Where a capability stands for this endpoint. Reads, decides nothing."""
        try:
            report = get_availability(self.db_path, self.endpoint.endpoint_id, capability)
        except EciPersistenceError:
            logger.exception("Availability could not be read; treating it as unreported")
            report = None
        return resolve_standing(self.device, capability, report, now=self.now)

    def issue(
        self,
        capability: CapabilityRef,
        *,
        parameters: dict[str, Any] | None = None,
        issued_by: str,
        expires_at: datetime | None = None,
    ) -> GovernedDirective:
        """Mint one governed directive, or refuse.

        Refusal is `CapabilityRefusedError`, never a silently-dropped directive:
        a seam that believed it had spoken must be told that it had not.

        One directive per exchange. A responder that tries to issue a second
        is refused -- an exchange is a single turn, and a boundary that could
        return several would be deciding how much to do, which is an executive
        question it has no business answering.
        """
        if self.issued is not None:
            raise CapabilityRefusedError(
                CapabilityDecision(
                    capability=capability,
                    standing=self.standing(capability).standing,
                    reason=(
                        "A directive has already been issued for this exchange. One "
                        "exchange carries at most one directive."
                    ),
                ),
            )

        decision = self.standing(capability)
        if not decision.directable:
            raise CapabilityRefusedError(decision)

        directive = GovernedDirective(
            directive_id=new_directive_id(),
            correlation_id=new_correlation_id(),
            endpoint_id=self.endpoint.endpoint_id,
            capability=capability,
            parameters=dict(parameters or {}),
            issued_at=self.now,
            expires_at=expires_at,
            issued_by=issued_by,
        )
        record_directive(self.db_path, directive, user_id=self.endpoint.user_id)
        self.issued = directive
        return directive


async def submit(
    exchange: InboundExchange,
    *,
    device: Any,
    db_path: str,
    identity_context: Any | None = None,
    runtime_cfg: dict[str, Any] | None = None,
    runtime_id: str | None = None,
    now: datetime | None = None,
) -> ExchangeReceipt:
    """Admit one exchange across the boundary. The single core entry point.

    Transport-independent by construction: it takes values, not a request, and
    returns a receipt, not a response. An HTTP route, a WebSocket frame
    handler or a local-IPC adapter each translate their own wire format into
    an `InboundExchange` and render the `ExchangeReceipt` back; none of them
    re-implements anything below.

    `device` is the `platform.devices.VerifiedDevice` the transport verified.
    It is typed loosely so this module stays importable without pulling the
    control plane in behind it, and because the only things asked of it are
    the capability methods the platform already owns.

    Never raises for a governance outcome. Every refusal is a receipt naming
    which of them it was, because an endpoint that cannot tell a halt from a
    malformed message from an unauthorised capability will retry the wrong one.
    """
    moment = now or utc_now()

    from bartholomew.kernel.inbound_store import InboundPersistenceError
    from bartholomew.kernel.runtime_contract import (
        EventBacklogFullError,
        run_inbound_through_runtime_contract,
    )
    from bartholomew.orchestrator.safety.governance_store import ParkingBrakeEngagedError

    try:
        ensure_schema(db_path)
    except EciPersistenceError as exc:
        logger.error("ECI schema unavailable: %s", exc)
        return ExchangeReceipt(
            outcome=ExchangeOutcome.UNAVAILABLE,
            exchange_id=exchange.exchange_id,
            detail="Nothing was recorded. The boundary's own state is unavailable.",
            reason=str(exc),
        )

    # Steps 5-7: the Parking Brake (both tiers, fail-closed), backpressure,
    # the Identity policy, durable idempotent capture and the provenance
    # record -- one existing governed call, not a re-implementation.
    try:
        captured = await run_inbound_through_runtime_contract(
            db_path=db_path,
            source_id=exchange.endpoint.source_id,
            event_id=exchange.exchange_id,
            event_type=_EVENT_TYPES[exchange.kind],
            payload=exchange.payload,
            verified_by=exchange.endpoint.verified_by,
            occurred_at=exchange.occurred_at_iso,
            runtime_id=runtime_id,
            identity_context=identity_context,
            runtime_cfg=runtime_cfg,
        )
    except ParkingBrakeEngagedError as exc:
        # "Inspect, but do not mutate." Nothing was written, no directive was
        # produced, and the refusal is retryable once the brake is released.
        return ExchangeReceipt(
            outcome=ExchangeOutcome.REFUSED_BRAKE,
            exchange_id=exchange.exchange_id,
            detail=(
                "Nothing was recorded and no directive was issued: a parking brake or "
                "platform halt is engaged. The submission may be retried once it is "
                "released."
            ),
            reason=str(exc),
        )
    except EventBacklogFullError as exc:
        return ExchangeReceipt(
            outcome=ExchangeOutcome.UNAVAILABLE,
            exchange_id=exchange.exchange_id,
            detail=(
                "Nothing was recorded: Bartholomew's event-processing backlog is full. "
                "This is backpressure, not a fault; retry once it drains."
            ),
            reason=str(exc),
        )
    except InboundPersistenceError as exc:
        return ExchangeReceipt(
            outcome=ExchangeOutcome.UNAVAILABLE,
            exchange_id=exchange.exchange_id,
            detail="Nothing was recorded: the exchange could not be persisted.",
            reason=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - an unexplained failure is never a success
        logger.exception("ECI capture failed")
        return ExchangeReceipt(
            outcome=ExchangeOutcome.UNAVAILABLE,
            exchange_id=exchange.exchange_id,
            detail="Nothing was recorded: the exchange could not be admitted.",
            reason=f"{type(exc).__name__}: {exc}",
        )

    if not captured.captured and not captured.duplicate:
        # An Identity policy decision refused it. The refusal itself is on the
        # record -- a policy denial is not a brake halt -- but a refusal is not
        # a capture and must not read like one.
        return ExchangeReceipt(
            outcome=ExchangeOutcome.REFUSED_GOVERNANCE,
            exchange_id=exchange.exchange_id,
            detail=(
                "Recorded as refused by Governance. The exchange was not accepted and "
                "no directive was issued."
            ),
            reason=captured.reason,
        )

    if captured.duplicate:
        # A redelivery. Deliberately returns before the responder: re-running
        # it would issue a *second* directive for one logical exchange, which
        # is how one utterance becomes two. The original exchange stands.
        return ExchangeReceipt(
            outcome=ExchangeOutcome.DUPLICATE,
            exchange_id=exchange.exchange_id,
            detail=(
                "A duplicate delivery of an exchange already recorded. No second "
                "exchange exists and no second directive was issued."
            ),
        )

    degraded = bool(getattr(captured, "provenance_degraded", False))
    degraded_error = getattr(captured, "provenance_error", None)

    if exchange.kind is ExchangeKind.RESULT:
        return _settle(
            exchange,
            db_path=db_path,
            moment=moment,
            degraded=degraded,
            degraded_error=degraded_error,
        )

    return await _respond(
        exchange,
        device=device,
        db_path=db_path,
        moment=moment,
        degraded=degraded,
        degraded_error=degraded_error,
    )


def _settle(
    exchange: InboundExchange,
    *,
    db_path: str,
    moment: datetime,
    degraded: bool,
    degraded_error: str | None,
) -> ExchangeReceipt:
    """Correlate a returning result to the directive that caused it."""
    reason = None
    raw_reason = exchange.payload.get("reason")
    if isinstance(raw_reason, str):
        reason = raw_reason[:500]

    try:
        settled = settle_directive(
            db_path,
            correlation_id=exchange.correlation_id or "",
            endpoint_id=exchange.endpoint.endpoint_id,
            status=exchange.status,
            reason=reason,
            result_exchange_id=exchange.exchange_id,
            now=moment,
        )
    except EciPersistenceError as exc:
        return ExchangeReceipt(
            outcome=ExchangeOutcome.UNAVAILABLE,
            exchange_id=exchange.exchange_id,
            detail="The result could not be correlated: the ledger is unavailable.",
            reason=str(exc),
        )

    mapped = {
        SettleOutcome.SETTLED: ExchangeOutcome.ACCEPTED,
        SettleOutcome.UNCORRELATED: ExchangeOutcome.UNCORRELATED,
        SettleOutcome.WRONG_ENDPOINT: ExchangeOutcome.UNCORRELATED,
        SettleOutcome.EXPIRED: ExchangeOutcome.EXPIRED,
        SettleOutcome.ALREADY_SETTLED: ExchangeOutcome.ALREADY_SETTLED,
    }[settled.outcome]

    detail = {
        SettleOutcome.SETTLED: (
            "The result was correlated to the directive that caused it and recorded."
        ),
        SettleOutcome.UNCORRELATED: (
            "The result was NOT applied: it could not be correlated to any directive "
            "this runtime issued to this endpoint."
        ),
        SettleOutcome.WRONG_ENDPOINT: (
            "The result was NOT applied: it could not be correlated to any directive "
            "this runtime issued to this endpoint."
        ),
        SettleOutcome.EXPIRED: (
            "The directive's window had already closed. The result is recorded as "
            "timed out rather than as the outcome reported."
        ),
        SettleOutcome.ALREADY_SETTLED: (
            "A result for this directive was already recorded. The first one stands."
        ),
    }[settled.outcome]

    return ExchangeReceipt(
        outcome=mapped,
        exchange_id=exchange.exchange_id,
        detail=detail,
        reason=settled.reason,
        provenance_degraded=degraded,
        provenance_error=degraded_error,
    )


async def _respond(
    exchange: InboundExchange,
    *,
    device: Any,
    db_path: str,
    moment: datetime,
    degraded: bool,
    degraded_error: str | None,
) -> ExchangeReceipt:
    """Give a Bartholomew-owned responder the chance to answer this exchange."""
    accepted_detail = (
        "Received and durably recorded. NOT acted upon: nothing has interpreted, "
        "believed or acted on this exchange."
    )

    responder = _RESPONDER["responder"]
    if responder is None:
        return ExchangeReceipt(
            outcome=ExchangeOutcome.ACCEPTED,
            exchange_id=exchange.exchange_id,
            detail=accepted_detail,
            provenance_degraded=degraded,
            provenance_error=degraded_error,
        )

    issuer = DirectiveIssuer(
        endpoint=exchange.endpoint,
        device=device,
        db_path=db_path,
        now=moment,
    )

    try:
        directive = await responder.respond(exchange, issuer)
    except CapabilityRefusedError as exc:
        # Bartholomew wanted to direct this endpoint and its registration
        # state did not admit it. Said plainly, because the fix is an
        # operator's (declare, approve, or report availability) and a vague
        # refusal would hide which.
        return ExchangeReceipt(
            outcome=ExchangeOutcome.REFUSED_CAPABILITY,
            exchange_id=exchange.exchange_id,
            detail=(
                "The exchange was recorded. Bartholomew had something to ask of this "
                "endpoint and the endpoint's capability standing did not admit it, so "
                "no directive was issued."
            ),
            reason=exc.decision.reason,
            provenance_degraded=degraded,
            provenance_error=degraded_error,
        )
    except Exception as exc:  # noqa: BLE001 - a responder fault is not a capture failure
        logger.exception("The ECI responder raised; the exchange stands as recorded")
        return ExchangeReceipt(
            outcome=ExchangeOutcome.ACCEPTED,
            exchange_id=exchange.exchange_id,
            detail=accepted_detail,
            reason=(
                f"Bartholomew produced no directive for this exchange: the responder "
                f"failed ({type(exc).__name__})."
            ),
            provenance_degraded=degraded,
            provenance_error=degraded_error,
        )

    if directive is None:
        return ExchangeReceipt(
            outcome=ExchangeOutcome.ACCEPTED,
            exchange_id=exchange.exchange_id,
            detail=accepted_detail,
            provenance_degraded=degraded,
            provenance_error=degraded_error,
        )

    return ExchangeReceipt(
        outcome=ExchangeOutcome.ACCEPTED,
        exchange_id=exchange.exchange_id,
        detail=(
            "Received and durably recorded, and Bartholomew has issued one governed "
            "directive in response. Carrying it out is the endpoint's to do; report "
            "the result against its correlation id."
        ),
        directive=directive,
        provenance_degraded=degraded,
        provenance_error=degraded_error,
    )


def report_availability(
    db_path: str,
    endpoint: EndpointIdentity,
    capability: CapabilityRef,
    *,
    device: Any,
    available: bool,
    detail: str = "",
    now: datetime | None = None,
) -> CapabilityDecision:
    """Record an endpoint's statement that it can (or cannot) serve a capability.

    Returns where the capability now *stands*, which is deliberately not the
    same thing as what was reported. An endpoint reporting itself ready for
    something it never declared is told `undeclared`, and nothing it said
    changed that: reporting is provenance about the endpoint's condition, not
    a way to acquire a capability. The report is still recorded -- what an
    endpoint claimed is worth keeping even when it grants nothing.

    `device` is the verified `platform.devices.VerifiedDevice`, so the
    standing returned is the real one rather than an optimistic echo.
    """
    moment = now or utc_now()
    if not isinstance(capability, CapabilityRef):
        raise ContractError("capability must be a CapabilityRef")

    ensure_schema(db_path)
    report = AvailabilityReport(
        capability=capability,
        available=available,
        reported_at=moment,
        detail=detail,
    )
    record_availability(db_path, endpoint.endpoint_id, report)
    return resolve_standing(device, capability, report, now=moment)


__all__ = [
    "CapabilityRefusedError",
    "DirectiveIssuer",
    "ExchangeResponder",
    "clear_responder",
    "install_responder",
    "report_availability",
    "responder_installed",
    "submit",
]
