"""The canonical event backbone: durable, governed processing of captured events.

Capture (`bartholomew.kernel.inbound_store`, the `/api/inbound/events` route)
answers *did something arrive, and from where*. Interpretation
(`bartholomew.kernel.inbound_interpretation`) answers *what does one captured
event mean*. Between them there was nothing: no record of which events had
been looked at, no way to look at one again after a restart, and -- crucially
-- no caller. Interpretation was reachable only by someone explicitly asking
for it, so in the running system it never ran at all.

This package is that middle: the durable state and the scheduler-driven pass
that turns "an event was captured" into "an event was processed, and here is
what happened to it".

What it owns
------------
`envelope`   one versioned shape for a captured event, built from the rows
             capture already writes
`registry`   the static table of event types this build can process, each
             with a typed payload and one handler
`store`      the durable state machine -- claiming, leases, attempts,
             quarantine, tenant scoping
`adapters`   the single handler, which calls the existing interpretation seam
`processor`  one governed pass: brake, policy, sweep, claim, settle
`health`     what the backbone is doing, for `/api/health` and for evidence
`config`     settings, and the one kill switch

What it deliberately does not own
---------------------------------
It is **not another authority**. Meaning belongs to `inbound_interpretation`;
permission belongs to the Parking Brake and Identity policy, read through
`runtime_contract`; the objective record belongs to `objective_store`; the
audit trail belongs to Reflections and `governance_audit`; scheduling belongs
to the existing scheduler. This package adds durable *bookkeeping* about
processing and one drive that runs it. Every judgement it appears to make is
a translation of an answer some existing authority gave.

It is **not a message bus**, and does not compete with `EventBus` or
`GlobalWorkspace`. Those are in-process, ephemeral, intra-runtime signalling:
a coroutine tells another coroutine that something happened *now*, and nothing
survives the process. This is a durable ledger of *external* events that must
outlive a restart, be claimed exactly once, and be answerable for afterwards.
The two never carry the same message: nothing here publishes to the workspace
and nothing there is persisted here. An event reaches meaning through the
interpretation seam, which is the semantic authority for inbound material --
not through a second broadcast channel with its own subscribers.

It introduces **no external broker, no second scheduler, no second process**.
The queue is a table in the runtime's own database; the worker is one drive on
the scheduler that already exists.
"""

from __future__ import annotations

# Importing `adapters` is what populates the registry. It is imported here,
# once, at package import time -- registration is a first-party code path and
# there is deliberately no discovery mechanism by which anything else could
# add a handler.
from . import adapters, config, envelope, health, processor, registry, store
from .config import EventProcessingSettings, resolve_settings
from .envelope import ENVELOPE_VERSION, CanonicalEvent
from .health import processing_health
from .processor import ProcessingPassResult, process_batch
from .registry import HandlerResult, RegisteredEventType, registered_types
from .store import (
    STATE_CAPTURED,
    STATE_CLAIMED,
    STATE_IRRELEVANT,
    STATE_PROCESSED,
    STATE_QUARANTINED,
    STATE_REFUSED,
    ProcessingRecord,
    ensure_schema,
)

__all__ = [
    "ENVELOPE_VERSION",
    "STATE_CAPTURED",
    "STATE_CLAIMED",
    "STATE_IRRELEVANT",
    "STATE_PROCESSED",
    "STATE_QUARANTINED",
    "STATE_REFUSED",
    "CanonicalEvent",
    "EventProcessingSettings",
    "HandlerResult",
    "ProcessingPassResult",
    "ProcessingRecord",
    "RegisteredEventType",
    "adapters",
    "config",
    "ensure_schema",
    "envelope",
    "health",
    "process_batch",
    "processing_health",
    "processor",
    "registered_types",
    "registry",
    "resolve_settings",
    "store",
]
