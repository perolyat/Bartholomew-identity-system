"""Bartholomew's own voice, reached through the External Capability Interface.

An adapter, like every module in this package: it connects a consumer that
declared a seam (`bartholomew/eci/boundary.py`'s `ExchangeResponder`) to the
producer that already owns the answer (`runtime_contract.
run_spoken_output_through_runtime_contract()`). It owns no policy, no store,
no authority and no vocabulary.

Why this is the right shape, and not a shortcut
-----------------------------------------------
The spoken-output seam takes its capability as an injected callable:

    speak_fn is injected, like capture_fn/stream_fn, so this seam owns
    Governance while spoken_output.py owns the capability -- and so the
    capability is reachable only through this path in production.

That sentence is the whole architecture of FND-04 stated by the repository
about itself, a wave before the boundary existed. **Bartholomew governs; the
capability is somebody else's to perform.** This adapter simply makes the
External Capability Interface be that somebody: `speak_fn` mints a governed
directive addressed to the endpoint instead of driving a local speech engine.

The consequence is that a directive **cannot exist unless Bartholomew's own
governance allowed it**, because the only place one is minted is inside the
callback the seam invokes after its three gates have passed:

  1. enablement -- `config/kernel.yaml`'s `voice.spoken_output`, default false
  2. `ParkingBrake("voice")`, read fail-closed through `GovernanceStore`
  3. the Identity policy decision on `voice_speak`

An engaged brake means no `speak_fn` call, so no directive, so nothing for the
endpoint to do. That is requirement (J) holding structurally rather than by
a check somebody remembered to write at the boundary.

What Bartholomew says, and why it is fixed
-------------------------------------------
This responder speaks **one fixed acknowledgement**. It does not read the
endpoint's payload, does not echo a word of it, and does not interpret what
the person asked for.

That is a security property, not a limitation to apologise for. An endpoint
that could put text into Bartholomew's mouth would be an endpoint that had
acquired Bartholomew's voice -- the clearest possible case of inbound content
becoming authority. `tests/test_fnd04_eci_vertical_slice.py` submits a request
whose payload is an instruction to say something else and proves the utterance
is unchanged.

It is also honest about its own smallness. **This adapter is not Bartholomew's
executive reasoning**, and FND-04 does not claim it is. It proves that a
governed directive can be produced by a real Bartholomew seam, carried across
the boundary, executed by an endpoint and correlated on return. A deployment
that wants Bartholomew to answer *what was actually asked* substitutes a
responder that calls the chat/executive path; the boundary it plugs into does
not change, which is the point of having one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The capability this responder asks an endpoint for. Already a member of the
#: platform's frozen capability vocabulary (`platform/device_capabilities.py`)
#: at version 1 -- FND-04 adds no capability kind and widens no vocabulary.
SPOKEN_OUTPUT_KIND = "multimodal.spoken_output"
SPOKEN_OUTPUT_VERSION = 1

#: Recorded verbatim on every directive this responder issues, so the audit
#: trail names the authority that decided rather than the boundary that
#: carried it.
ISSUED_BY = "runtime_contract.run_spoken_output_through_runtime_contract"

#: What Bartholomew says. Fixed, short, and containing nothing an endpoint
#: supplied. See the module docstring for why it is not the person's words.
ACKNOWLEDGEMENT = "I'm here."


@dataclass(frozen=True)
class DelegatedSpeech:
    """What `speak_fn` reports back, shaped as the seam reads it.

    Structurally what `spoken_output.SpeechResult` is -- `spoken`, `detail`,
    `text` -- because that is the contract the seam consumes (it reads
    `.spoken` and `.detail` by attribute). A distinct type rather than a
    borrowed one, because this is deliberately **not** a local speech result:
    no engine ran, and `engine` would have to be a lie.

    **What `spoken=True` means here, precisely.** The directive was issued to
    a capable endpoint and durably recorded. It does **not** mean sound was
    produced. The seam's own outcome for this is `"started"`, which is the
    accurate word: an utterance was started, and whether it finished is the
    endpoint's to report. That report arrives as a `result` exchange and
    settles the row in the directive ledger, which is the durable answer to
    "did Bartholomew actually get heard". Reading `started` as "spoke" would
    be the same mistake as reading inbound capture's 202 as "processed".
    """

    spoken: bool
    detail: str | None = None
    text: str = ""


class SpokenAcknowledgementResponder:
    """Answers a request exchange by speaking, through the existing governed seam.

    Observations are not answered at all: something happening near an endpoint
    is not a cue for Bartholomew to talk, and a boundary that replied to every
    observation would be one that had decided speaking was always appropriate.
    """

    def __init__(
        self,
        *,
        db_path: str,
        runtime_cfg: dict[str, Any] | None = None,
        identity_context: Any | None = None,
        text: str = ACKNOWLEDGEMENT,
    ) -> None:
        self._db_path = db_path
        self._runtime_cfg = runtime_cfg
        self._identity_context = identity_context
        self._text = text

    async def respond(self, exchange: Any, issuer: Any) -> Any:
        """Let Bartholomew decide whether to speak, and carry the answer if it does."""
        from bartholomew.eci.boundary import CapabilityRefusedError
        from bartholomew.eci.contract import CapabilityRef, ExchangeKind
        from bartholomew.kernel import spoken_output
        from bartholomew.kernel.runtime_contract import (
            run_spoken_output_through_runtime_contract,
        )

        if exchange.kind is not ExchangeKind.REQUEST:
            return None

        capability = CapabilityRef(kind=SPOKEN_OUTPUT_KIND, version=SPOKEN_OUTPUT_VERSION)

        # Asked before the seam runs, not after. A seam that ran its gates,
        # recorded a Reflection saying it spoke, and only then discovered the
        # endpoint could not speak would have written something untrue.
        decision = issuer.standing(capability)
        if not decision.directable:
            raise CapabilityRefusedError(decision)

        minted: dict[str, Any] = {}

        def speak_fn(text: str) -> DelegatedSpeech:
            """The capability, performed by an endpoint instead of a local engine.

            Reached only after the seam's enablement, brake and policy gates
            have all passed. Raising here is honest: the seam records an
            unsuccessful outcome rather than a success it did not have, and
            `issuer.issue()` raises whenever the endpoint's capability
            standing does not admit the directive.
            """
            directive = issuer.issue(
                capability,
                parameters={"text": text},
                issued_by=ISSUED_BY,
            )
            minted["directive"] = directive
            return DelegatedSpeech(
                spoken=True,
                detail=(
                    f"Delegated to endpoint {directive.endpoint_id} through the External "
                    f"Capability Interface as directive {directive.correlation_id}. No "
                    "local speech engine ran. Whether the words were actually said is "
                    "the endpoint's correlated result, recorded in the directive ledger."
                ),
                text=text,
            )

        result = await run_spoken_output_through_runtime_contract(
            self._text,
            enabled=spoken_output.enabled_for(self._runtime_cfg),
            db_path=self._db_path,
            identity_context=self._identity_context,
            speak_fn=speak_fn,
        )

        if not result.governance_allowed or not result.started:
            # Bartholomew declined, or execution failed after it allowed.
            # Either way there is nothing for the endpoint to carry out, and
            # the exchange is still a properly recorded exchange.
            logger.info(
                "ECI responder: Bartholomew issued no directive (outcome=%s, reason=%s)",
                result.outcome,
                result.reason,
            )
            return None

        return minted.get("directive")


def install_eci_responder(
    *,
    db_path: str,
    runtime_cfg: dict[str, Any] | None = None,
    identity_context: Any | None = None,
) -> str:
    """Put the responder in place. Returns a one-line description for the report.

    Installing it makes the boundary able to carry what Bartholomew decides.
    It does not make Bartholomew decide anything it would not otherwise have:
    every gate in the spoken-output seam still applies, and with
    `voice.spoken_output` off -- the default -- this responder produces no
    directive at all.
    """
    from bartholomew.eci.boundary import install_responder

    responder = SpokenAcknowledgementResponder(
        db_path=db_path,
        runtime_cfg=runtime_cfg,
        identity_context=identity_context,
    )
    install_responder(responder)
    from bartholomew.kernel import spoken_output

    enabled = spoken_output.enabled_for(runtime_cfg)
    return (
        f"spoken acknowledgement via {ISSUED_BY} "
        f"(voice.spoken_output={'on' if enabled else 'off'})"
    )


__all__ = [
    "ACKNOWLEDGEMENT",
    "DelegatedSpeech",
    "ISSUED_BY",
    "SPOKEN_OUTPUT_KIND",
    "SPOKEN_OUTPUT_VERSION",
    "SpokenAcknowledgementResponder",
    "install_eci_responder",
]
