"""The External Capability Interface (ECI): Bartholomew's governed external boundary.

Bartholomew is the executive. Everything outside it -- an avatar or voice
presence, a Windows or phone companion, a browser, a home-automation bridge, a
camera, a speaker, a specialist tool, a future device or service -- is a
**capability endpoint**. An endpoint may observe, receive what a person said,
supply context, advertise what it can do, carry out a governed instruction and
report what happened. It may not decide what the person wants, what
Bartholomew should prioritise, or what the system should do next.

The ECI is the boundary between the two. It is the architectural term; the
concept is not renamed by any transport that carries it. A later WebSocket,
local-IPC or queue adapter is an *implementation* of this boundary, not a
replacement for it, and `contract.py` is written so that such an adapter
changes no core type.

    endpoint --> observation / request / context --> ECI --> Bartholomew
    (identity + memory + executive reasoning + governance)
    --> governed directive --> ECI --> endpoint executes
    --> result --> ECI --> Bartholomew correlates and continues

Capability is not authority
---------------------------
An endpoint advertising `multimodal.spoken_output`, `windows.open_url` or
anything else has said **"I am able to perform this operation."** It has not
said, and cannot say, **"I may decide when this operation happens."** Those
are different facts with different owners: the endpoint owns the first, and
Bartholomew's executive and governance own the second. `capabilities.py`
enforces the separation, and no module in this package converts one into the
other.

What this package is
--------------------
`contract.py`      the transport-independent value objects: who an endpoint
                   is, what it may say, what it may be told, and what honestly
                   became of a submission. No decisions, no I/O.
`capabilities.py`  the three separate facts -- understood, declared-and-
                   approved, currently available -- that must all hold before
                   a directive may name a capability.
`store.py`         the correlation ledger and the availability record. The two
                   pieces of durable state nothing else in the repository held.
`boundary.py`      the one admission order, composing authorities that already
                   exist, plus the responder seam through which Bartholomew --
                   and only Bartholomew -- issues a directive.
`reference_endpoint.py`
                   a deliberately minimal endpoint used to prove the boundary
                   end to end. Not a product, not AIRI, not a companion.

What this package is NOT
------------------------
**Not a second executive.** Nothing here reasons about what should happen. The
responder seam is empty by default and is filled by a Bartholomew-owned seam
in `bartholomew/integration/`; the boundary carries what that seam decided and
has no opinion of its own.

**Not a capability broker.** There is no provider registry, no selection, no
routing, no scoring and no marketplace. `CONSTITUTION.md`'s "Bartholomew
employs an ecosystem; it does not become it" explicitly authorises none of
those, and none is here.

**Not a second identity, brake, consent gate or audit store.** Authentication
is the platform's (`platform/http_identity.py`, `platform/devices.py`); the
Parking Brake is `orchestrator/safety/governance_store.py`, read once through
the existing governed capture path and composed across both authority tiers
there; provenance is the `ActionReflection` that path already writes. Adding a
second of any of them is the failure mode this package was written to avoid.

**Not a replacement for the governed Windows action channel.**
`bartholomew/actuation/` keeps its own envelope, its eleven-point admission,
its human-bound approvals and its lease, and this package neither wraps nor
supersedes it. The relationship is deliberate and worth stating plainly: that
channel is a *specialisation* of this boundary's pattern for the one family of
capabilities whose risk demands per-action human approval, and it remains the
only path by which a Windows action reaches a machine. The ECI is the general
boundary for capabilities whose governance is owned by the Bartholomew seam
that issues them. A future change may express the action channel as an ECI
transport; nothing here requires it, and nothing here has changed it.

Inbound content is data, never authority
----------------------------------------
Whatever an endpoint puts in a payload -- a web page's text, a tool's
response, a device's reading, a person's words -- is material Bartholomew may
reason *about*. It never becomes an instruction Bartholomew follows. There is
no path in this package from payload content to a governance decision, an
identity, a capability grant or a directive's parameters, and
`tests/test_fnd04_eci_boundary.py` asserts the absence over this package's
source rather than leaving it to this docstring.
"""

from .capabilities import (
    AvailabilityReport,
    CapabilityDecision,
    CapabilityStanding,
    resolve_standing,
)
from .contract import (
    ECI_CONTRACT_VERSION,
    CapabilityRef,
    ContractError,
    DirectiveStatus,
    EndpointIdentity,
    ExchangeKind,
    ExchangeOutcome,
    ExchangeReceipt,
    GovernedDirective,
    InboundExchange,
)

__all__ = [
    "ECI_CONTRACT_VERSION",
    "AvailabilityReport",
    "CapabilityDecision",
    "CapabilityRef",
    "CapabilityStanding",
    "ContractError",
    "DirectiveStatus",
    "EndpointIdentity",
    "ExchangeKind",
    "ExchangeOutcome",
    "ExchangeReceipt",
    "GovernedDirective",
    "InboundExchange",
    "resolve_standing",
]
