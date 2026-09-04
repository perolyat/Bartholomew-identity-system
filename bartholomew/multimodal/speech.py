"""Spoken output -- a thin integration, deliberately not a second authority.

`bartholomew/kernel/spoken_output.py` is the repository's speech capability
and `runtime_contract.run_spoken_output_through_runtime_contract()` is its
governed seam. Contract §7 says to honour the existing explicit output setting
and the voice brake, and invariant 1 forbids a second authority for anything.
So this module adds exactly two things the existing seam does not have, and
nothing else:

* **visible speaking state** -- a session record, so the status surface can say
  "Bartholomew is speaking" and a person can stop it; and
* **cancellation** -- `SpeechHandle.cancel()`, so a long answer can be cut off.

Everything else is delegated. `enabled_for()` still reads
`config/kernel.yaml`'s `voice.spoken_output`; the brake check, the Identity
policy decision and the reflection all still happen in the existing seam; the
engine is still the existing adapter's. There is no config key here, no second
enablement flag, and no path that speaks without going through the seam.

**Speaking never opens a microphone.** This module imports nothing from
`microphone.py`, holds no reference to an audio input, and its session
requests a `Modality.SPOKEN_OUTPUT` capability which the resolver checks
against `multimodal.spoken_output` alone. The structural test in
`tests/test_multimodal_separation.py` asserts the absence of any import edge.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from bartholomew.kernel import spoken_output

from .privacy import Classification, PrivacyClass, RetentionClass, bound_text

logger = logging.getLogger(__name__)

#: Spoken text is bounded by the existing adapter's own limit. Restated here
#: only so the bound is visible at this seam too; the adapter remains the
#: authority that enforces it.
MAX_SPOKEN_CHARS = spoken_output.MAX_SPEECH_CHARS


@dataclass
class SpeechHandle:
    """A cancellable in-flight utterance.

    Cancellation is cooperative and bounded: it prevents an utterance that has
    not started and marks one in flight as cancelled. It cannot claw back
    sound already produced by the engine, and this does not pretend otherwise
    -- `cancelled_before_speaking` records which of the two happened.
    """

    text: str
    _cancelled: threading.Event

    @classmethod
    def create(cls, text: str) -> SpeechHandle:
        return cls(text=text, _cancelled=threading.Event())

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()


@dataclass
class SpeechOutcome:
    """What happened to one utterance. Silence is never reported as speech."""

    spoken: bool
    detail: str | None
    engine: str | None = None
    cancelled_before_speaking: bool = False
    classification: Classification | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "spoken": self.spoken,
            "detail": self.detail,
            "engine": self.engine,
            "cancelled_before_speaking": self.cancelled_before_speaking,
        }
        if self.classification:
            payload.update(self.classification.as_dict())
        return payload


def output_available() -> tuple[bool, str]:
    """Whether this machine has a speech engine at all.

    Delegates to the existing adapter's discovery so there is one answer to
    this question. On Windows the adapter finds no argv-based engine today
    (recorded in its own docstring), which this reports truthfully as
    unavailable rather than as speech that silently does nothing.
    """
    try:
        engine = spoken_output.available_engine()
    except Exception as exc:
        logger.exception("Speech engine discovery failed")
        return False, f"speech engine discovery failed: {exc}"
    if engine is None:
        return False, "no local speech engine is available on this machine"
    return True, f"speech engine '{engine.name}' available"


def prepare_text(text: str) -> tuple[str, Classification]:
    """Bound and classify text before it is spoken.

    Spoken output is classified `ordinary`/`ephemeral`: it is Bartholomew's own
    words leaving the machine as sound, not captured material about the user.
    It is still bounded, because an unbounded utterance is its own problem.
    """
    classification = Classification(
        privacy_class=PrivacyClass.ORDINARY,
        retention_class=RetentionClass.EPHEMERAL,
    )
    bounded, truncated = bound_text(text or "", MAX_SPOKEN_CHARS)
    if truncated:
        classification.truncated = True
        classification.note("speech.text", "length_bound")
    return bounded, classification


def speak_with_handle(handle: SpeechHandle) -> SpeechOutcome:
    """Speak one utterance through the existing adapter, unless cancelled.

    This is the `speak_fn` handed to the existing runtime-contract seam, so
    governance still runs before this is ever called. It does not check the
    brake or the config flag itself -- doing so would be a second authority
    disagreeing with the first.
    """
    text, classification = prepare_text(handle.text)
    if handle.cancelled:
        return SpeechOutcome(
            spoken=False,
            detail="cancelled before speaking",
            cancelled_before_speaking=True,
            classification=classification,
        )
    try:
        result = spoken_output.speak_text(text)
    except Exception as exc:
        logger.exception("Spoken output failed")
        return SpeechOutcome(spoken=False, detail=str(exc), classification=classification)
    return SpeechOutcome(
        spoken=bool(getattr(result, "spoken", False)),
        detail=getattr(result, "detail", None),
        engine=getattr(result, "engine", None),
        classification=classification,
    )
