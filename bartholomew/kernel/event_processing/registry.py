"""What event types this build can process, and what each one's payload is.

Capture is domain-blind and stays that way: `event_type` reaches
`inbound_events` as an opaque string, is never branched on there, and this
module does not change that. Meaning is assigned *here*, one step later, from
a static table -- which is what makes the boundary between "something
arrived" and "we know what it is" inspectable rather than implied.

Three properties, each of which is a rule rather than a convention.

**Registration is code, not configuration.** `register()` is called by
first-party modules at import time and by nothing else. There is no
discovery path, no entry point, no directory scan and no way for a payload,
a model, or an operator's config file to introduce a handler. "Arbitrary
plugin or model-selected handler execution" is not merely absent -- there is
no mechanism it could arrive through.

**A type's payload is typed.** Every registration names a parser that turns
the stored JSON into a declared shape or raises `PayloadValidationError`.
A handler therefore never inspects raw third-party JSON, and a payload that
does not match the type its sender claimed is refused with a reason rather
than half-interpreted.

**An unknown type is refused, visibly.** `lookup()` returns None and the
processor settles the event `refused` with `unknown_event_type`. It is not
dropped, not silently marked irrelevant, and not retried forever: it stays in
the record, counted on the health surface, and an operator who then registers
the type can requeue it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .envelope import CanonicalEvent
from .store import SETTLEABLE_STATES


class PayloadValidationError(ValueError):
    """The payload is not the shape its declared event type promises.

    Deterministic by definition, so the processor refuses rather than retries:
    the same bytes will fail the same way on every attempt, and burning
    attempts on it would push a merely-malformed event into quarantine
    alongside genuine faults.
    """


class HandlerRegistrationError(RuntimeError):
    """A registration is malformed or would replace an existing one."""


@dataclass(frozen=True)
class HandlerResult:
    """What a handler decided. Data only; the processor performs the write.

    `disposition` is one of `processed`, `irrelevant` or `refused`. A handler
    cannot elect `quarantined`: quarantine is what repeated failure produces,
    and letting a handler choose it would let a deliberate refusal be filed
    as a fault.
    """

    disposition: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.disposition not in SETTLEABLE_STATES:
            raise ValueError(
                f"disposition must be one of {sorted(SETTLEABLE_STATES)}, "
                f"got {self.disposition!r}",
            )
        if not self.reason or not str(self.reason).strip():
            raise ValueError("a handler result needs a machine-readable reason")


class EventHandler(Protocol):
    """Processes one typed event. Async, because every governed seam is."""

    async def __call__(
        self,
        ctx: Any,
        event: CanonicalEvent,
        payload: Any,
    ) -> HandlerResult: ...


@dataclass(frozen=True)
class RegisteredEventType:
    """One entry in the static event-type table."""

    event_type: str
    parse: Callable[[Any], Any]
    handler: Callable[..., Awaitable[HandlerResult]]
    description: str

    def as_dict(self) -> dict[str, Any]:
        return {"event_type": self.event_type, "description": self.description}


_REGISTRY: dict[str, RegisteredEventType] = {}


def register(spec: RegisteredEventType) -> RegisteredEventType:
    """Add one event type. Refuses to replace an existing registration.

    Refusing rather than overwriting is the point: two modules that both
    believe they own a type is a real defect, and a last-import-wins registry
    turns it into behaviour that depends on import order.
    """
    if not isinstance(spec, RegisteredEventType):
        raise HandlerRegistrationError("register() takes a RegisteredEventType")
    if not spec.event_type or not spec.event_type.strip():
        raise HandlerRegistrationError("an event type must be a non-empty string")
    if not callable(spec.parse) or not callable(spec.handler):
        raise HandlerRegistrationError(
            f"event type {spec.event_type!r} needs a callable parser and handler",
        )
    existing = _REGISTRY.get(spec.event_type)
    if existing is not None and existing is not spec:
        raise HandlerRegistrationError(
            f"event type {spec.event_type!r} is already registered to "
            f"{existing.handler!r}; a type has exactly one handler",
        )
    _REGISTRY[spec.event_type] = spec
    return spec


def lookup(event_type: str) -> RegisteredEventType | None:
    """The registration for `event_type`, or None when nothing handles it."""
    return _REGISTRY.get(event_type)


def registered_types() -> tuple[str, ...]:
    """Every event type this build can process, sorted.

    Exposed on the health surface so "why was my event refused?" is
    answerable without reading the source.
    """
    return tuple(sorted(_REGISTRY))


def describe_registry() -> list[dict[str, Any]]:
    return [_REGISTRY[name].as_dict() for name in sorted(_REGISTRY)]


def _reset_for_tests() -> None:
    """Empty the registry. Tests only -- there is no production caller."""
    _REGISTRY.clear()


__all__ = [
    "EventHandler",
    "HandlerRegistrationError",
    "HandlerResult",
    "PayloadValidationError",
    "RegisteredEventType",
    "describe_registry",
    "lookup",
    "register",
    "registered_types",
]
