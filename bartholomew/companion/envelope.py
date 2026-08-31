"""Turning a `DeviceObservation` into the existing inbound envelope.

There is no second ingestion authority here. The companion produces exactly the
body `POST /api/inbound/events` already accepts -- `source_id`, `event_id`,
`event_type`, `payload`, `occurred_at` -- and everything after that (Parking
Brake, Identity policy, capture, the `UNIQUE(source_id, event_id)` idempotency
constraint, the durable provenance row) is the inbound seam's, unchanged.

**Two kinds of provenance, and they are not the same kind.**

* `source_id` is *verified* provenance. The route overwrites nothing and trusts
  nothing here: it compares the submitted `source_id` against the one the
  deployment's installed resolver verified, and refuses with 403 on a mismatch.
  The companion must be configured with the source id it is actually issued.
* `payload["device_id"]` is *claimed* provenance. It is a label the companion
  asserts about itself. It is durably recorded, and it is useful for telling two
  machines apart under one verified source -- but it is not authenticated, and
  nothing in Bartholomew may treat it as though it were. See
  `docs/D_PC_COMPANION_OBSERVATION.md` for the authentication limitations this
  prototype does not close.

**Idempotency.** `event_id` is derived deterministically from the observation's
content, so re-submitting the same observation after a timeout produces the same
id and lands on the existing row instead of creating a second logical event. It
is content-derived rather than random precisely so that a retry issued by a
*restarted* companion -- which has no memory of a random id -- is still a retry.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .observation import DeviceObservation

#: Length of the hex digest kept in an event id. 128 bits of a SHA-256 is far
#: past any collision concern for one device's observation stream, and a short
#: id keeps the durable rows readable when a person inspects them.
EVENT_ID_DIGEST_CHARS = 32


def derive_event_id(observation: DeviceObservation) -> str:
    """A stable idempotency key for one observation.

    Same observation -> same id, on this run or on any later one. Two different
    observations differ in at least `sequence`, so they never collide.

    The `companion:` prefix makes the origin of an id legible in the
    `inbound_events` table without parsing anything.
    """
    material = json.dumps(
        {
            "device_id": observation.device_id,
            "kind": observation.kind.value,
            "sequence": observation.sequence,
            "observed_at": observation.observed_at,
            "values": observation.payload(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:EVENT_ID_DIGEST_CHARS]
    return f"companion:{observation.kind.value}:{digest}"


def to_inbound_envelope(observation: DeviceObservation, *, source_id: str) -> dict[str, Any]:
    """The exact JSON body to POST to `/api/inbound/events`.

    Five keys, all of which the existing envelope already defines. The companion
    adds no field to the inbound contract, which is why it needs no change to the
    route, the store, the schema or the route-capability policy.
    """
    return {
        "source_id": source_id,
        "event_id": derive_event_id(observation),
        "event_type": observation.event_type,
        "payload": observation.payload(),
        "occurred_at": observation.observed_at,
    }
