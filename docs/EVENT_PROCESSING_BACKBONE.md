# The canonical event backbone (Package A)

Durable, governed, idempotent processing of captured inbound events, driven by
the scheduler that already exists.

## What this closes

Before it, Bartholomew had two halves of a path and nothing between them:

* **capture** (`bartholomew/kernel/inbound_store.py`, `POST /api/inbound/events`)
  recorded *that something arrived*, and deliberately stopped there;
* **interpretation** (`bartholomew/kernel/inbound_interpretation.py`) could say
  what one captured event meant — but had no caller in the running system, no
  record of which events had been looked at, and no way to look at one again
  after a restart.

An authenticated event could therefore be captured perfectly and then sat on
forever. The backbone is the middle: a durable state machine over captured
events, and one scheduler drive that runs it.

## What it does not do

It is not a second authority for anything.

| Question | Answered by | Not by this |
|---|---|---|
| What does an event mean? | `inbound_interpretation` | ✗ |
| May Bartholomew mutate governed state? | Parking Brake + Identity policy, via `runtime_contract` | ✗ |
| What does an objective's history say? | `objective_store` | ✗ |
| When did something run, and did it work? | the scheduler's `ticks`, Reflections, `governance_audit` | ✗ |

It introduces **no external broker** (no Kafka, no Redis), **no second
scheduler**, **no second process**, **no second Executive**, **no second
inbound authority**, and **no second audit log**. The queue is a table in the
runtime's own database; the worker is one drive on the autonomy loop.

### Relationship to `EventBus` and `GlobalWorkspace`

These are not competitors and never carry the same message.

* `EventBus` / `GlobalWorkspace` are **in-process, ephemeral, intra-runtime
  signalling**: one coroutine telling another that something is happening
  *now*. Nothing survives the process.
* `event_processing` is a **durable ledger of external events** that must
  outlive a restart, be claimed exactly once, and be answerable for
  afterwards.

Nothing in the backbone publishes to the workspace, and nothing in the
workspace is persisted here. An event reaches meaning through the
interpretation seam — the semantic authority for inbound material — not
through a second broadcast channel with its own subscribers.

## The state machine

```
captured  --claim-->  claimed  --settle-->  processed
                              --settle-->  irrelevant
                              --settle-->  refused
                              --fail  -->  captured      (attempt spent)
                              --fail  -->  quarantined   (attempts exhausted)
                              --release-> captured       (attempt refunded)
                              --lease -->  captured      (recovered by a later pass)
```

| State | Meaning |
|---|---|
| `captured` | Swept from `inbound_events`, waiting for a pass. |
| `claimed` | A pass holds a bounded lease on it right now. |
| `processed` | A handler ran and its effect, if any, is durable. Terminal. |
| `irrelevant` | Nothing here bears on anything Bartholomew is carrying. An explicit verdict, terminal. |
| `refused` | Deliberately not acted on — an unregistered type, a payload that is not what its type promised, a policy denial, or an interpretation that would have had to guess. Terminal, and never an error. |
| `quarantined` | Repeatedly failed. Terminal, held for inspection, out of the way of every later event. |

`refused` and `irrelevant` are different on purpose. `irrelevant` is an answer
the system *reached*; `refused` is one it *declined to give*. Recording an
uncertain interpretation as irrelevant would manufacture a verdict.

### Properties, and what enforces each

| Property | Enforced by |
|---|---|
| An event is enqueued at most once | `UNIQUE (source_id, event_id)` — the same pair capture already made unique |
| Claimed by at most one pass | one `BEGIN IMMEDIATE` transaction; every settle is conditional on the claim token |
| Not lost when a process dies | claims are leases with an expiry; a later pass recovers them |
| Effects never duplicated | evidence attachment checks the objective's own history (`already_attached`) — independent of the queue |
| Retries bounded | the attempt is spent at claim time, so a crash loop is bounded like an error loop |
| A poison event does not starve later ones | it leaves the ready queue the moment its last attempt fails |
| Tenant isolation | every row carries capture's `runtime_id`; claiming filters on it (`IS`, NULL-safe), and the processor re-checks |
| The brake preserves the backlog | the pass checks the brake **before** sweeping or claiming, so a halted pass leaves the table unchanged |

## Registered event types

Processing is typed; capture is not. `event_type` reaches `inbound_events` as
an opaque string and is still never branched on there. Meaning is assigned one
step later, from a static table in
`bartholomew/kernel/event_processing/adapters.py`:

| Event type | Payload | Handler |
|---|---|---|
| `observation.note` | JSON object, bounded depth and size | the interpretation adapter |
| `observation.status` | same | the same adapter |

Registration is a first-party code change — a constant, a parser, a
registration, and a test. **There is no discovery path, no entry point, no
directory scan and no configuration key** by which a handler can appear, and
nothing in a payload or a model's output can select one.

An event of any other type settles `refused` with reason `unknown_event_type`.
It is not dropped and not retried forever: it stays in the record, is counted
on the health surface, and can be requeued once the type is registered.

## Configuration

`config/kernel.yaml`:

```yaml
event_processing:
  enabled: true          # the ONLY thing that can turn processing on
  batch_limit: 5         # events claimed per tick
  sweep_limit: 200       # captured rows swept into the queue per tick
  lease_seconds: 120     # how long a claim is held
  max_attempts: 3        # failures before quarantine
  backlog_max: 1000      # unprocessed events before capture pushes back
  deadline_seconds: 3.0  # wall-clock budget for one batch
```

Environment overrides — `BARTH_EVENT_PROCESSING_BATCH`, `_SWEEP`, `_LEASE_S`,
`_MAX_ATTEMPTS`, `_BACKLOG_MAX`, `_DEADLINE_S` — are operational tuning and
take precedence over the file.

Cadence uses the scheduler's existing override mechanism: the `drives:` block
in `config/kernel.yaml`, or `DRIVE_INBOUND_EVENT_PROCESSING=every:2`. The
default is `every:15`.

### Enable / disable

Default **ON**, unlike the `proactive:` flags in the same file, and the
difference is deliberate. Those decide whether Bartholomew may contact you
unprompted — a consent question, with exactly one deliberate authority. This
decides whether Bartholomew *looks at* events a verified source already
delivered. It sends nothing, contacts nobody, reaches no external provider,
and creates no objective; its only durable effect is one `fact` row on an
objective you already opened, written through the same governed seam, behind
the same Parking Brake and the same Identity policy as every other objective
write. Capturing events and never reading them is the surprising behaviour.

Two switches, deliberately asymmetric:

* `event_processing.enabled: false` in `config/kernel.yaml` — the only thing
  that can turn it **on**;
* `BARTH_EVENT_PROCESSING_ENABLED=0` — can only turn it **off**.

The variable is a kill switch, not a second authority: an operator handling an
incident should not have to edit a file to stop processing, and being able to
*stop* something is not the risk that "one deliberate act to start it" guards
against. Setting it to `1` on a config-disabled deployment changes nothing.

When off, the drive is not registered at all: zero ticks, zero queue impact.
Captured events accumulate in `inbound_events` exactly as they did before this
feature existed, and the first pass after re-enabling sweeps the whole backlog.

Identity policy is a third, independent switch. Removing `inbound_event_process`
from `tool_use.allowlist` in `Identity.yaml` stops the pass touching anything
while preserving the backlog; removing `inbound_event_processing` stops the
drive running at all.

### Batch limits and the drive timeout

One tick runs inside the scheduler's `BARTH_DRIVE_TIMEOUT` budget (5s by
default). `batch_limit` is 5 for that reason; raising it meaningfully means
raising that timeout too. `deadline_seconds` bounds the batch from the inside:
anything still claimed when it passes is returned to the queue **with its
attempt refunded**, so a slow pass costs latency and never a retry.

### Backpressure

When the non-terminal backlog reaches `backlog_max`, `POST /api/inbound/events`
refuses with **503** and captures nothing. The refusal is retryable and is
distinct from a brake refusal (`EventBacklogFullError` vs
`ParkingBrakeEngagedError`), so a sender's logs say which happened. A door
that keeps accepting into a queue nothing is draining fills the disk silently;
refusing is the honest alternative.

The backlog read is deliberately **not** fail-closed: an unreadable count logs
and lets capture proceed. A brake is a safety gate and must refuse when it
cannot be read; this is a capacity gate, and refusing everything because a
count failed would turn a reporting problem into an outage.

## Health and evidence

**Live:** `GET /api/health` → `components.event_processing`

```
backlog                          non-terminal events waiting
pending / in_flight              the split between them
oldest_unprocessed_age_seconds   head-of-line latency
last_successful_processing_at    the positive signal
retry_attempts / events_retried  work being redone
quarantined / quarantined_sample count, and each one's reason and last error
states                           the full terminal tally
enabled, backlog_limit, batch_limit, max_attempts, lease_seconds
registered_event_types           why an event was refused as unknown
```

`status` is `ok` for a working backbone (including a disabled one, and one
with a backlog), `failed` only when the backlog is at its limit — the point at
which capture starts refusing — and `unknown` when the state cannot be read.

**Frozen:** `bartholomew/runtime/evidence_report.py` →
`sources.event_processing`, plus four summary counts
(`event_processing_count`, `_backlog`, `_quarantined`, `_retry_attempts`).
The report schema version is now **2**; a version-1 artifact is still valid
and simply predates the backbone — its silence about processing must not be
read as "nothing was processed".

A database with no processing table reports `available: false`, never zero.
"Nothing was processed" and "we cannot tell what was processed" are opposite
findings and neither surface blurs them.

**Command line:**

```bash
python -m bartholomew.kernel.event_processing status --db data/barth.db
python -m bartholomew.kernel.event_processing quarantined --db data/barth.db
```

`--db` may be omitted when `BARTH_DB_PATH` is set.

## Deployment

### Schema

Created through the existing startup path. `KernelDaemon.start()` calls
`event_processing.store.ensure_schema()` off the event loop, immediately after
the objective store and before the scheduler task exists — the same
fail-closed discipline the scheduler's own schema uses (issue #24). A daemon
that cannot create the table does not come up.

`ensure_schema()` is `CREATE TABLE IF NOT EXISTS` throughout and is safe to
call repeatedly; the processing pass calls it again defensively.

### Database upgrade

Nothing to run. Start the new build against an existing database and the two
tables (`event_processing`, `event_processing_cursor`) are created at startup.

Events captured **before** the upgrade are picked up automatically: the sweep
reads `inbound_events` from a watermark that starts at zero, so the first pass
after an upgrade enqueues the entire captured history, bounded by `sweep_limit`
per tick. On a large existing history this drains over several ticks rather
than in one; that is intentional.

Expect most of that inherited backlog to settle `refused` /
`unknown_event_type`, because events captured before this feature existed
carry whatever `event_type` their sender chose. That is the correct, visible
outcome — not an error — and those events can be requeued if a matching type
is registered later.

### Rollback

Roll back by deploying the previous build, or by setting
`BARTH_EVENT_PROCESSING_ENABLED=0` and restarting.

* Capture is unaffected either way: `inbound_events` is written by the same
  code as before and the backbone only ever reads it.
* The `event_processing` tables are additive. An older build ignores them
  entirely; they are never read by anything outside this package.
* Evidence already attached to objectives stays attached. It is ordinary
  `objective_events` data, written through the ordinary governed seam, and is
  indistinguishable from evidence attached by any other caller — nothing needs
  to be undone.
* Rolling forward again resumes from the same watermark. Events captured while
  the backbone was off are swept on the first pass after it comes back.

Nothing needs to be drained before a rollback. A claimed event whose process
goes away is recovered by lease expiry — on the next start, or by the previous
build simply not having a queue at all.

### Recovering quarantined events

An event quarantines after `max_attempts` failures. It is held, never
discarded, and never blocks anything behind it.

```bash
# What is quarantined, and why
python -m bartholomew.kernel.event_processing quarantined --db data/barth.db

# Put them all back with a fresh attempt budget
python -m bartholomew.kernel.event_processing requeue --db data/barth.db

# Or just one
python -m bartholomew.kernel.event_processing requeue --db data/barth.db \
    --source-id acme --event-id 42

# Only quarantined, leaving refused events alone
python -m bartholomew.kernel.event_processing requeue --db data/barth.db \
    --state quarantined
```

`requeue` defaults to both `quarantined` and `refused`, because the operator
situation is the same shape — "the reason it stopped has been fixed". It never
touches `processed` or `irrelevant` by default: those are decisions that were
*reached*, and re-running them would put the system back in front of a question
it already answered.

Requeue is safe against a running daemon. It is an ordinary bounded
transaction and cannot move an event a live pass is holding — a `claimed` row
is untouched.

If the backbone and the capture table ever disagree — a database repaired by
hand, say — rewind the sweep:

```bash
python -m bartholomew.kernel.event_processing resync --db data/barth.db --from-row 0
```

Re-sweeping is safe at any time: `UNIQUE (source_id, event_id)` means an event
already known to the backbone is skipped whatever state it is in, so a resync
can never resurrect a settled event or duplicate one.

## Interfaces for Session F

Everything below is public, exported from
`bartholomew.kernel.event_processing`, and stable for the wave.

**To register a new event type** — the only supported extension point:

```python
from bartholomew.kernel.event_processing.registry import (
    HandlerResult, RegisteredEventType, register,
)
from bartholomew.kernel.event_processing.store import (
    STATE_IRRELEVANT, STATE_PROCESSED, STATE_REFUSED,
)

async def handle(ctx, event, payload) -> HandlerResult:
    ...  # ctx is the duck-typed daemon context; event is a CanonicalEvent

register(RegisteredEventType(
    event_type="my.type",
    parse=MyPayload.parse,        # raises PayloadValidationError on a mismatch
    handler=handle,
    description="what this is",
))
```

A handler returns a `HandlerResult` naming `STATE_PROCESSED`,
`STATE_IRRELEVANT` or `STATE_REFUSED`. It may raise
`TransientProcessingError` to spend an attempt, or `BrakeDeferredError` to put
the event back untouched. It **cannot** elect quarantine — that is what
repeated failure produces.

**To run a pass** (the drive's own call):
`await processor.process_batch(ctx) -> ProcessingPassResult`

**To read state:**
`store.get(db, source_id, event_id)`, `store.list_by_state(...)`,
`store.pending_count(db)`, `health.processing_health(db)`,
`health.health_component(db, cfg)`

**To recover:** `store.requeue(...)`, `store.resync_from(...)`

**Envelope:** `CanonicalEvent.from_inbound_row(row, payload)` builds one from
any `inbound_events` row or mapping. It is duck-type compatible with the
`stored` argument `interpret_captured_event()` takes, so an adapter can hand
the interpretation seam the canonical envelope directly.

## Known limitations

**Events captured before a runtime binding existed are not claimed by a bound
process.** Tenant scoping matches `event_processing.runtime_id` against the
process's own binding, NULL-safe. If a deployment captures events while
unbound and is later bound to a user id, those earlier rows carry `NULL` and a
bound process will not claim them. Refusing is the safe direction — nothing is
mis-attributed — and it is visible rather than silent: the backlog stops
draining, the oldest-unprocessed age grows, and
`pending_runtime_bindings` on the health surface reads greater than one. An
operator who is certain those events belong to the bound user can attribute
them explicitly:

```sql
UPDATE event_processing SET runtime_id = 'the-user-id'
WHERE runtime_id IS NULL AND state = 'captured';
```

**The sweep uses a watermark over `inbound_events.id`.** It is sound because
SQLite permits one write transaction at a time and the id is assigned inside
it, so ids commit in ascending order and a row cannot appear below a watermark
already passed. `resync` exists for the case where that reasoning is ever
wrong or a database is repaired by hand.

**A pass that fails on any event marks its scheduler tick a failure.** One
transient fault among five successful events still records `success=0` in
`ticks`. Over-signalling was preferred to under-signalling; the precise
per-event state is on the health surface either way.

**Processing latency is bounded below by the drive cadence** (`every:15` by
default). An event is not processed the instant it is captured, and capture's
202 still means "captured, not processed" — that acknowledgement did not
change.
