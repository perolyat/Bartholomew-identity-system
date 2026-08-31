"""Submitting an observation to the existing inbound boundary. Submit-only.

**This client is a one-way valve, and that is the load-bearing property.** It
POSTs an envelope and it classifies the HTTP status. It never parses the
response body for anything the companion then does, never dispatches on a
response field, and has no code path by which any value returned by Bartholomew
reaches the host machine. `submit()` returns a small immutable result describing
*delivery*, and the runner acts only on that result's status -- so a server (or
anything impersonating one) that answered with `{"command": "..."}` would be
answering into a client that has nowhere to put it.
`tests/test_companion_no_actuation.py` proves this with a server that tries.

**Credentials are carried, not invented.** The companion does not define an
authentication scheme. It sends whatever headers the deployment's operator
configures, so that whichever resolver the deployment installed can verify the
source. With no resolver installed -- the repository default -- the inbound
route refuses with 401 and the companion captures nothing. That is the correct
behaviour and the companion does not work around it. See
`docs/D_PC_COMPANION_OBSERVATION.md` for what this does and does not amount to.

Status handling mirrors the route's own contract exactly, including the part
that matters most: a 200 is a *duplicate*, not a fresh capture, and is reported
as such rather than counted twice.
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

logger = logging.getLogger(__name__)

INBOUND_PATH = "/api/inbound/events"

#: How long to wait on one delivery attempt.
DEFAULT_TIMEOUT_SECONDS = 15.0


class DeliveryStatus(str, Enum):
    """What happened to one submission. Delivery only -- never meaning."""

    #: 202: durably captured as a new inbound event.
    CAPTURED = "captured"
    #: 200: this event was already captured. No second event exists.
    DUPLICATE = "duplicate"
    #: 401/403: not authorised or not verified. Retrying will not help.
    REFUSED = "refused"
    #: 422/413: the envelope was rejected. A retry of the same body will not help.
    INVALID = "invalid"
    #: 503 or a transport failure. Nothing was captured; retry later.
    RETRYABLE = "retryable"


#: Statuses on which a retry is pointless. Retrying a refusal just turns an
#: authentication problem into an authentication problem repeated forever.
TERMINAL_STATUSES = frozenset({DeliveryStatus.REFUSED, DeliveryStatus.INVALID})


@dataclass(frozen=True)
class DeliveryResult:
    """The outcome of one submission, as the runner is allowed to see it.

    Deliberately not the response body. `detail` is a string kept for logging
    and operator inspection only; nothing branches on it.
    """

    status: DeliveryStatus
    http_status: int | None
    detail: str = ""

    @property
    def delivered(self) -> bool:
        """True when the event is durably on the Bartholomew side.

        Both `CAPTURED` and `DUPLICATE` mean "it is recorded", which is what the
        runner needs to know to stop retrying. They stay distinguishable so the
        companion never reports a duplicate as a new capture.
        """
        return self.status in (DeliveryStatus.CAPTURED, DeliveryStatus.DUPLICATE)


def _classify(http_status: int) -> DeliveryStatus:
    if http_status == 202:
        return DeliveryStatus.CAPTURED
    if http_status == 200:
        return DeliveryStatus.DUPLICATE
    if http_status in (401, 403):
        return DeliveryStatus.REFUSED
    if http_status in (413, 422):
        return DeliveryStatus.INVALID
    return DeliveryStatus.RETRYABLE


class InboundSubmitClient:
    """Posts envelopes to `/api/inbound/events`. Has no other verb."""

    def __init__(
        self,
        base_url: str,
        *,
        credential_headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.base_url = base_url.rstrip("/")
        self._headers = dict(credential_headers or {})
        self._timeout = timeout

    def submit(self, envelope: Mapping[str, Any]) -> DeliveryResult:
        """Deliver one envelope. Returns a delivery outcome; never raises for HTTP."""
        data = json.dumps(dict(envelope)).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - scheme validated below
            f"{self.base_url}{INBOUND_PATH}",
            data=data,
            headers={"Content-Type": "application/json", **self._headers},
            method="POST",
        )
        if request.type not in ("http", "https"):
            raise ValueError(f"Refusing to submit over {request.type!r}")

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                # The body is read to close the connection cleanly and to give an
                # operator something in the log. It is deliberately not returned
                # to the runner and nothing here branches on it.
                detail = _short(response.read())
                return DeliveryResult(_classify(response.status), response.status, detail)
        except urllib.error.HTTPError as e:
            return DeliveryResult(_classify(e.code), e.code, _short(e.read()))
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            # The service is not reachable. Nothing was captured, and this is
            # exactly the case a retry exists for.
            return DeliveryResult(DeliveryStatus.RETRYABLE, None, f"{type(e).__name__}: {e}")


def _short(body: bytes | None, limit: int = 300) -> str:
    """A truncated, decoded response body, for logs only."""
    if not body:
        return ""
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - decode with errors= does not raise
        return ""
    return text[:limit]
