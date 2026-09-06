"""Did it actually happen? The server-side half of Verify, and W03-A's seam into it.

Issuance is not effect. `windows_actuation/handlers.py` makes that argument on
the device -- every handler observes its own result before it may claim one --
and this module makes the same argument on the server, for the cases where the
device cannot see far enough on its own.

Why there are two halves at all
-------------------------------
The device can read back what the device can reach: the field it just typed
into, the window it just focused, the clipboard it just wrote. That is the
strongest observation available and it is the primary path -- it is the one
that turns `windows.type_text` from a permanent `unknown` into a real verdict.

But some effects are only legible from the *observation* side of the loop, and
that side is W03-A's: the governed read of PC state, under the same consent,
brake and identity gates as any other capture. W03-C must not build a second
one. So this module defines the narrowest possible interface W03-C needs from
it, and W03-A (or the integration session) installs a provider that satisfies
it.

Why the interface is installed rather than imported
---------------------------------------------------
`bartholomew/actuation/` deliberately imports nothing from
`bartholomew/multimodal/`. The two are different trust channels -- one decides
whether a machine may be acted on, the other watches a person's screen -- and
an import edge between them is exactly the coupling
`tests/test_windows_action_channel_separation.py` exists to refuse. A holder
that a provider is installed into keeps the dependency pointing one way and
keeps this package testable with a fake, which is the same pattern
`actuation/devices.py` uses for the device registry.

It also means W03-C does not have to guess W03-A's signature while W03-A is
still being written. The Protocol below is the whole contract; anything that
satisfies it can be installed, and until something is, verification degrades
to the honest answer.

The degradation is the important part
-------------------------------------
With no provider installed, `verify_effect()` returns `UNVERIFIABLE`. Not
`CONFIRMED`, and not `CONTRADICTED`. A verification path whose failure mode was
"assume it worked" would be worse than having none, because it would launder
an assumption into a durable row that reads like an observation. Every branch
here that cannot see returns the same value, and
`tests/test_windows_action_verification.py` asserts that removing the provider
never turns a verdict affirmative.

**A server verdict never overwrites a device's status.** The device is the only
party that saw the machine; `record_action_result_through_runtime_contract`
keeps taking the status from it and records this verdict alongside, as the
`server_verified` evidence key. A server that could rewrite a device's honest
`unknown` into `succeeded` would be the fiction this whole package is built to
refuse, just relocated one process further away.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: Longest text this module will digest. A verification read is a fingerprint
#: comparison, not a transfer: a provider that returned a megabyte of window
#: text would be hashed to the same 64 characters, and bounding the input keeps
#: the work bounded too.
MAX_VERIFIED_TEXT_CHARS = 8192


class Verdict(str, Enum):
    """What the read-back established. Three values, and `UNVERIFIABLE` is a real one."""

    #: The observed state agrees with the effect the action was supposed to have.
    CONFIRMED = "confirmed"
    #: The observed state disagrees with it. The action demonstrably did not land.
    CONTRADICTED = "contradicted"
    #: Nothing could be observed. Never rounded to either neighbour.
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class ReadBack:
    """One governed read of current UI state, as this package needs to see it.

    Deliberately carries a **digest and a length, not the text**. A provider
    may hand over `text` for the comparison, and `from_text()` reduces it here,
    at the boundary, so no caller in this package ever holds screen content and
    nothing downstream can put it in an evidence row by accident.

    `available=False` with a `reason` is the honest unavailable state, and it
    is a value rather than an exception for the reason `uia.focused_field()`
    gives: the caller's job is to refuse on unknown, and a value it must
    inspect is harder to skip than an exception it might catch too broadly.
    """

    available: bool
    digest: str | None = None
    length: int | None = None
    #: True when the read-back's text contained the text the action carried.
    #: `None` when no comparison was possible.
    contains_expected: bool | None = None
    reason: str = ""

    @classmethod
    def unavailable(cls, reason: str) -> ReadBack:
        return cls(available=False, reason=str(reason or "")[:200])

    @classmethod
    def from_text(cls, text: str, *, expected: str | None = None) -> ReadBack:
        """Reduce an observed string to a digest, a length and a comparison.

        The observed text is not stored on the returned object and is not
        logged. `expected` is compared here, inside this constructor, precisely
        so that the comparison can happen without either string leaving.
        """
        observed = str(text or "")[:MAX_VERIFIED_TEXT_CHARS]
        contains: bool | None = None
        if expected:
            contains = str(expected)[:MAX_VERIFIED_TEXT_CHARS] in observed
        return cls(
            available=True,
            digest=hashlib.sha256(observed.encode("utf-8")).hexdigest(),
            length=len(observed),
            contains_expected=contains,
        )


@runtime_checkable
class ReadBackProvider(Protocol):
    """The whole of what W03-C asks of W03-A's read-back primitive.

    One method, and it returns a `ReadBack` or something that can be coerced
    into one. A provider is expected to apply its own governance -- consent,
    brake, identity, scope -- before reading anything; this package neither
    duplicates those checks nor bypasses them, because the read belongs to the
    observation channel and its gates are that channel's.

    A provider that cannot read must return an unavailable `ReadBack` rather
    than raising, but a provider that raises anyway is handled: `verify_effect`
    treats an exception as unavailability, never as a confirmation.
    """

    def read_back(
        self,
        *,
        tenant_id: str,
        device_id: str,
        target: str,
        expected: str | None = None,
    ) -> ReadBack:  # pragma: no cover - Protocol declaration
        ...


class _ProviderHolder:
    """A holder rather than a bare module global.

    The reason `actuation.devices._INSTALLED` gives: a rebound module attribute
    is invisible to a module that already did `from . import provider`, so the
    indirection is what makes installation actually take effect everywhere.
    """

    def __init__(self) -> None:
        self._provider: ReadBackProvider | None = None
        self._lock = threading.Lock()

    def install(self, provider: ReadBackProvider | None) -> None:
        with self._lock:
            self._provider = provider

    def get(self) -> ReadBackProvider | None:
        with self._lock:
            return self._provider


_INSTALLED = _ProviderHolder()


def install_read_back_provider(provider: ReadBackProvider | None) -> None:
    """Install (or, with `None`, remove) the governed read-back provider.

    Called by whoever owns the observation channel -- W03-A's package at
    start-up, or `bartholomew/integration/` at wiring time -- and by test
    fixtures. Passing `None` restores the un-provided state, which is what a
    fixture must do on teardown so one test's fake cannot silently verify
    another test's action.
    """
    _INSTALLED.install(provider)


def get_read_back_provider() -> ReadBackProvider | None:
    """The installed provider, or None. Nothing here installs a default."""
    return _INSTALLED.get()


@dataclass(frozen=True)
class Verification:
    """A verdict about one action's effect, and how it was reached."""

    verdict: Verdict
    #: One of `result.VERIFY_METHODS`. Says how the observation was made, so an
    #: audit counting confirmations can tell a field read from a nothing.
    method: str
    detail: str = ""

    @property
    def confirmed(self) -> bool:
        """True only for an observed agreement. `UNVERIFIABLE` is False.

        The property exists so no caller writes `verdict is not CONTRADICTED`,
        which is the expression that turns "nobody looked" into a success.
        """
        return self.verdict is Verdict.CONFIRMED

    def as_evidence(self) -> dict[str, Any]:
        """The bounded, non-content facts this verdict contributes to a result."""
        return {"server_verified": self.confirmed, "verify_method": self.method}


def verify_effect(
    *,
    tenant_id: str,
    device_id: str,
    target: str,
    expected_text: str | None = None,
) -> Verification:
    """Ask the governed read-back whether an action's effect is visible now.

    Returns `UNVERIFIABLE` -- never anything affirmative -- when there is no
    provider, when the provider says it could not read, when it raises, and
    when it answers with something that is not a `ReadBack`. Those are four
    different reasons and one verdict, because the verdict is a statement about
    what is known and in all four cases the answer is "nothing".
    """
    provider = get_read_back_provider()
    if provider is None:
        return Verification(
            Verdict.UNVERIFIABLE,
            "unavailable",
            "no governed read-back provider is installed, so the effect was not observed",
        )
    try:
        observed = provider.read_back(
            tenant_id=tenant_id,
            device_id=device_id,
            target=target,
            expected=expected_text,
        )
    except Exception as e:  # noqa: BLE001 - an errored reader observed nothing
        logger.warning("The read-back provider raised: %s", type(e).__name__)
        return Verification(
            Verdict.UNVERIFIABLE,
            "unavailable",
            f"the read-back provider raised {type(e).__name__}, so nothing was observed",
        )
    if not isinstance(observed, ReadBack):
        return Verification(
            Verdict.UNVERIFIABLE,
            "unavailable",
            "the read-back provider did not answer with a ReadBack",
        )
    if not observed.available:
        return Verification(
            Verdict.UNVERIFIABLE,
            "unavailable",
            observed.reason or "the read-back provider could not read the target",
        )
    if expected_text is None:
        # A read happened and there was nothing to compare it against. That is
        # a successful *read* and an absent *verdict*, and conflating them
        # would let "the window is still there" stand in for "the text landed".
        return Verification(
            Verdict.UNVERIFIABLE,
            "os_state_read_back",
            "the target was read but the action carried no text to compare it against",
        )
    if observed.contains_expected is None:
        return Verification(
            Verdict.UNVERIFIABLE,
            "os_state_read_back",
            "the read-back made no comparison against what the action carried",
        )
    if observed.contains_expected:
        return Verification(
            Verdict.CONFIRMED,
            "os_state_read_back",
            "the governed read-back found what the action carried in the target",
        )
    return Verification(
        Verdict.CONTRADICTED,
        "os_state_read_back",
        "the governed read-back did not find what the action carried in the target",
    )


__all__ = [
    "MAX_VERIFIED_TEXT_CHARS",
    "ReadBack",
    "ReadBackProvider",
    "Verdict",
    "Verification",
    "get_read_back_provider",
    "install_read_back_provider",
    "verify_effect",
]
