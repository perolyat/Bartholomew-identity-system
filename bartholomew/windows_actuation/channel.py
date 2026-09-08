"""The action channel: a separate, separately authenticated boundary. Three verbs.

**This is not the observation client and cannot be reached from it.** The
observation companion (`bartholomew/companion/client.py`) POSTs to
`/api/inbound/events`, has exactly one method, and returns a three-scalar
delivery status -- a server that answered an observation with
`{"action": ...}` is answering into a client with nowhere to put it, which
`tests/test_companion_no_actuation.py` proves against a hostile server. This
client talks to `/api/device-actions/*`, authenticates with its own
credentials against its own resolver, and is imported by neither the
observation package nor anything the observation package imports.
`tests/test_windows_action_channel_separation.py` asserts that in both
directions over the module import graph.

Three verbs, and the asymmetry between them is deliberate:

* `lease()` **does** parse the response into typed actions, because that is
  what an action channel is for. Every field is then re-validated by
  `dispatch.check()` before anything runs, so a hostile response gets five
  device-side refusals rather than an execution.
* `report()` sends an outcome and reads nothing back but a status code.
* `abort_check()` asks whether the actions this companion is holding may still
  run, and can only ever make it act **less**. It is the narrowest verb of the
  three: its response has no field that names an action to run, a program, a
  path or a parameter, and the only thing a hostile answer to it can achieve is
  stopping a companion that would otherwise have acted. A third verb widens the
  surface, so it was worth checking what it widens it *to*, and the answer is
  "a set of ids the companion already knows, and a boolean".

Everything a response can influence is bounded: a fixed number of actions, a
fixed maximum body size, and a typed constructor that refuses a malformed
entry. There is no field in the lease response that names a program, a path
outside the allowlists, a command, or a handler -- the wire format has no such
key, and `LeasedAction.from_wire` would refuse it if it did.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from bartholomew.actuation.result import HandlerOutcome

from .dispatch import AbortSignal, DispatchRefusedError, LeasedAction

logger = logging.getLogger(__name__)

LEASE_PATH = "/api/device-actions/lease"
RESULT_PATH_TEMPLATE = "/api/device-actions/{action_id}/result"
ABORT_PATH = "/api/device-actions/abort-check"

DEFAULT_TIMEOUT_SECONDS = 20.0

#: Largest lease response this client will read. A channel that can be made to
#: return an unbounded body is a channel that can exhaust the companion.
MAX_RESPONSE_BYTES = 256 * 1024

#: Most actions one lease call will accept, whatever the server sends.
MAX_ACTIONS_PER_LEASE = 20


class ChannelStatus(str, Enum):
    """What happened to one channel call. Transport only -- never meaning."""

    #: The call succeeded and its body was read.
    OK = "ok"
    #: 401/403: this companion is not authenticated for the action channel.
    #: Retrying will not help; it is a configuration problem, not a transient.
    REFUSED = "refused"
    #: 400/404/409/422: the server rejected this specific call.
    REJECTED = "rejected"
    #: 503 or a transport failure. Retry later.
    RETRYABLE = "retryable"


TERMINAL_STATUSES = frozenset({ChannelStatus.REFUSED, ChannelStatus.REJECTED})


@dataclass(frozen=True)
class ChannelResult:
    """The outcome of one channel call."""

    status: ChannelStatus
    http_status: int | None
    #: Parsed body, only for a successful lease. None for everything else, so
    #: no code path reads a body out of a failed call.
    body: dict[str, Any] | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is ChannelStatus.OK


def _classify(http_status: int) -> ChannelStatus:
    if 200 <= http_status < 300:
        return ChannelStatus.OK
    if http_status in (401, 403):
        return ChannelStatus.REFUSED
    if http_status in (400, 404, 409, 410, 413, 422):
        return ChannelStatus.REJECTED
    return ChannelStatus.RETRYABLE


class ActionChannelClient:
    """Leases actions, asks whether they may still run, and reports results.

    Deliberately no `execute`, no `run`, no generic `post`: the three methods
    below are the whole interface, and
    `tests/test_windows_action_channel_separation.py` asserts that the public
    surface is exactly those three and that none of them is a wider one.
    """

    def __init__(
        self,
        base_url: str,
        *,
        device_id: str,
        credential_headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.base_url = base_url.rstrip("/")
        self.device_id = device_id
        self._headers = dict(credential_headers or {})
        self._timeout = timeout

    # -- transport --------------------------------------------------------

    def _post(self, path: str, payload: Mapping[str, Any]) -> ChannelResult:
        data = json.dumps(dict(payload)).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - scheme validated below
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json", **self._headers},
            method="POST",
        )
        if request.type not in ("http", "https"):
            raise ValueError(f"Refusing to use the action channel over {request.type!r}")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    oversize = f"the response exceeded {MAX_RESPONSE_BYTES} bytes and was not read"
                    return ChannelResult(
                        ChannelStatus.REJECTED,
                        response.status,
                        None,
                        oversize,
                    )
                status = _classify(response.status)
                return ChannelResult(status, response.status, _json(raw), "")
        except urllib.error.HTTPError as e:
            body = e.read(MAX_RESPONSE_BYTES)
            return ChannelResult(_classify(e.code), e.code, None, _short(body))
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return ChannelResult(
                ChannelStatus.RETRYABLE,
                None,
                None,
                f"{type(e).__name__}: {e}",
            )

    # -- the three verbs --------------------------------------------------

    def lease(self, *, limit: int = 5) -> tuple[ChannelResult, list[LeasedAction], list[str]]:
        """Ask for actions this device may run now.

        Returns `(result, actions, malformed)`. `malformed` names the action
        ids -- or `"<unnamed>"` -- of entries the typed constructor refused, so
        a companion talking to something that is not Bartholomew produces a
        legible log line rather than a silent nothing.

        Leasing is a **request**, not an instruction: the server has already
        decided each of these passed its eleven governance checks, and this
        client re-checks five of them before running anything.
        """
        result = self._post(LEASE_PATH, {"device_id": self.device_id, "limit": int(limit)})
        if not result.ok or not isinstance(result.body, dict):
            return result, [], []

        raw_actions = result.body.get("actions")
        if not isinstance(raw_actions, list):
            return (
                ChannelResult(
                    ChannelStatus.REJECTED,
                    result.http_status,
                    None,
                    "the lease response had no 'actions' list",
                ),
                [],
                [],
            )

        actions: list[LeasedAction] = []
        malformed: list[str] = []
        for entry in raw_actions[:MAX_ACTIONS_PER_LEASE]:
            try:
                actions.append(LeasedAction.from_wire(entry))
            except DispatchRefusedError as refusal:
                identifier = (
                    str(entry.get("action_id"))
                    if isinstance(entry, dict) and entry.get("action_id")
                    else "<unnamed>"
                )
                malformed.append(identifier)
                logger.warning("Refusing a malformed leased action %s: %s", identifier, refusal)
        return result, actions, malformed

    def abort_check(self, *, action_ids: list[str]) -> tuple[ChannelResult, AbortSignal]:
        """Ask whether these leased actions may still run. Fail-closed on everything.

        The device half of stop-after-lease. Returns `(result, signal)`, and
        every path that is not a well-formed 200 produces
        `AbortSignal.unreadable(...)` -- a transport failure, a 401, a 503, a
        body that is not an object, a body with no lists. `may_run()` is False
        for all of them, so a companion that cannot reach the abort signal does
        not act, which is the only reading of "the brake stops it" that is
        worth having.

        Note what this verb cannot do in the other direction: there is no field
        in the response this client reads that could *start* anything. It
        narrows what is runnable and never widens it.
        """
        result = self._post(
            ABORT_PATH,
            {"device_id": self.device_id, "action_ids": [str(a) for a in action_ids]},
        )
        if not result.ok:
            return result, AbortSignal.unreadable(
                f"the abort check did not succeed ({result.http_status}): {result.detail}",
            )
        return result, AbortSignal.from_wire(result.body)

    def report(
        self,
        *,
        action_id: str,
        outcome: HandlerOutcome,
        observed_at: str,
    ) -> ChannelResult:
        """Report what this device observed. Reads nothing back but a status.

        The status vocabulary is the handler's, unmodified: a `succeeded` here
        means a handler observed its effect, and an `unknown` is sent as
        `unknown` rather than rounded to either neighbour.
        """
        return self._post(
            RESULT_PATH_TEMPLATE.format(action_id=action_id),
            {
                "device_id": self.device_id,
                "status": outcome.status.value,
                "error_category": (
                    outcome.error_category.value if outcome.error_category else None
                ),
                "detail": outcome.detail,
                "evidence": dict(outcome.evidence),
                "observed_at": observed_at,
            },
        )


def _json(raw: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _short(body: bytes | None, limit: int = 300) -> str:
    """A truncated, decoded response body, for logs only."""
    if not body:
        return ""
    return body.decode("utf-8", errors="replace")[:limit]
