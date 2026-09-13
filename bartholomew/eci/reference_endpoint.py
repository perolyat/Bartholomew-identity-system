"""A deliberately minimal external capability endpoint. Not a product.

This exists to prove the boundary end to end, and to be the smallest honest
thing that can: it enrols, says what it can do, says whether it can do it
right now, submits what the person said, carries out whatever governed
directive comes back, and reports the result. That is the entire canonical
interaction pattern with nothing else in the way.

**It is not AIRI, not the Windows companion, and not a computer-control
agent.** FND-04 deliberately does not make any of those a prerequisite for
proving that the boundary works; attaching a major external product to an
unproven boundary is the order of operations this work package exists to
avoid.

What makes it a good proof is what it cannot do
------------------------------------------------
It has no intent model, no planner, no goal, no memory and no opinion. It
cannot decide to act: `carry_out()` runs only a directive it was handed, and
the only thing it does with one is record the text and report back. If the
boundary were ever to let an endpoint determine what happens next, this class
would have to grow something to determine it with -- and it has nothing.

It also cannot lie its way into authority. It has no field for a user id, a
runtime id or a tenant: those come from the credential the platform issued,
resolved server-side. A test that wants to prove an endpoint cannot claim
another's identity has to reach past this class to construct the attempt,
which is itself the point.

Transport-shaped, but only just
-------------------------------
It takes `post` and `get` callables rather than importing an HTTP client, so
the same endpoint drives a `TestClient`, a real `httpx` session, or a future
non-HTTP adapter. The boundary's core is transport-independent; this is a
client of one transport and says so.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .endpoint_auth import ENDPOINT_CREDENTIAL_HEADER


@dataclass
class TurnRecord:
    """Everything one full loop produced, for a test to assert over."""

    submit_status: int
    submit_body: dict[str, Any]
    directive: dict[str, Any] | None = None
    result_status: int | None = None
    result_body: dict[str, Any] | None = None

    @property
    def directed(self) -> bool:
        return self.directive is not None

    @property
    def correlation_id(self) -> str | None:
        if self.directive is None:
            return None
        value = self.directive.get("correlation_id")
        return value if isinstance(value, str) else None


@dataclass
class ReferenceEndpoint:
    """One minimal endpoint speaking the External Capability Interface over HTTP."""

    post: Callable[..., Any]
    get: Callable[..., Any]
    credential: str

    #: Everything this endpoint was actually directed to say, in order. The
    #: assertion surface for "Bartholomew said exactly this and nothing else",
    #: and for proving an endpoint cannot put words in Bartholomew's mouth.
    spoken: list[str] = field(default_factory=list)

    #: Set to make `carry_out()` report a failure instead of a success, so the
    #: honest-failure paths are exercised by the same endpoint that exercises
    #: the happy one.
    fail_with: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {ENDPOINT_CREDENTIAL_HEADER: self.credential}

    def describe_self(self) -> Any:
        """Ask the boundary who it thinks this endpoint is."""
        return self.get("/api/eci/endpoint", headers=self.headers)

    def report_availability(
        self,
        kind: str,
        version: int = 1,
        *,
        available: bool = True,
        detail: str = "",
    ) -> Any:
        """Say whether this endpoint can serve a capability right now.

        Separate from declaring it, and deliberately a separate call: an
        endpoint that has declared `multimodal.spoken_output` and whose speech
        engine is missing is in a different state from one that never
        declared it, and only the endpoint knows which.
        """
        return self.post(
            "/api/eci/availability",
            headers=self.headers,
            json={
                "capability": {"kind": kind, "version": version},
                "available": available,
                "detail": detail,
            },
        )

    def submit(
        self,
        *,
        exchange_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        status: str | None = None,
    ) -> Any:
        body: dict[str, Any] = {
            "exchange_id": exchange_id,
            "kind": kind,
            "payload": payload or {},
        }
        if correlation_id is not None:
            body["correlation_id"] = correlation_id
        if status is not None:
            body["status"] = status
        return self.post("/api/eci/exchanges", headers=self.headers, json=body)

    def carry_out(self, directive: dict[str, Any]) -> tuple[str, str | None]:
        """Perform one directive. Records what it was told to say; decides nothing.

        Returns `(status, reason)` where status is one of the three the
        contract allows. `unknown` is reachable through `fail_with="unknown"`
        because an endpoint that genuinely cannot tell whether the effect
        landed must be able to say so -- folding that into `failed` is exactly
        what `bartholomew/actuation/recovery.py` exists to prevent.
        """
        text = (directive.get("parameters") or {}).get("text")
        if self.fail_with:
            return (self.fail_with, f"the reference endpoint was told to report {self.fail_with!r}")
        if isinstance(text, str):
            self.spoken.append(text)
        return ("succeeded", None)

    def run_turn(self, *, exchange_id: str, said: str) -> TurnRecord:
        """The whole canonical loop, once.

        request -> boundary -> Bartholomew -> directive -> boundary ->
        this endpoint carries it out -> result -> boundary -> correlated.
        """
        response = self.submit(
            exchange_id=exchange_id,
            kind="request",
            payload={"text": said},
        )
        body = _json(response)
        record = TurnRecord(submit_status=_status(response), submit_body=body)

        directive = body.get("directive") if isinstance(body, dict) else None
        if not isinstance(directive, dict):
            return record
        record.directive = directive

        status, reason = self.carry_out(directive)
        result = self.submit(
            exchange_id=f"{exchange_id}-result",
            kind="result",
            payload={"reason": reason} if reason else {},
            correlation_id=directive["correlation_id"],
            status=status,
        )
        record.result_status = _status(result)
        record.result_body = _json(result)
        return record


def _status(response: Any) -> int:
    return int(getattr(response, "status_code", 0))


def _json(response: Any) -> dict[str, Any]:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is reported as empty
        return {}
    return body if isinstance(body, dict) else {}


__all__ = ["ReferenceEndpoint", "TurnRecord"]
