# The External Capability Interface

> **Status:** Working reference (2026-09-13). **Non-canonical** — a document
> under `docs/`, not one of the 14 canonical SSOT docs. It describes a
> mechanism, how to operate it, and what it does not close. It does **not**
> authorise remote exposure, an AIRI integration, a companion, browser, phone
> or home-automation integration, or any capability broker;
> `CONSTITUTION.md`, `MASTER_PLAN.md`, `DECISIONS.md`, `ASSUMPTIONS.md`,
> `INTERFACES.md` and `RISKS.md` remain the authorities. Where this note and a
> canonical document disagree, the canonical document wins.

## 0. The architectural rule, in one paragraph

Bartholomew is the central executive intelligence and governance authority.
External systems are **capability endpoints**, not competing executives. An
endpoint may observe, receive what a person said, supply context, advertise
what it can do, carry out a governed instruction and report what happened. It
may **not** independently determine overall user intent, executive priorities
or system-wide behaviour. The **External Capability Interface** (ECI) is the
governed boundary between the two. That is the human-facing architectural
term; a transport implementation may use HTTP, WebSocket, IPC or a queue, and
the boundary is still the External Capability Interface.

## 1. Why this exists now

FND-01 to FND-03 hardened identity projection, memory redaction and the
consent inbox. Useful, and none of it gave real-world loops a place to attach.

The repository already had five external seams, every one of them real:
inbound capture (`/api/inbound/events`), the event backbone, the governed
Windows action channel, the platform device registry, and the observation
companion. What it did not have was a **named boundary**. Each seam was built
for one product, and the honest projection of that trajectory is one assistant
architecture per integration — an AIRI brain, a Windows companion brain, a
phone agent — discovered far too late to unify.

`INTERFACES.md` had recorded the agreed shape for this boundary **twice**
(2026-08-17 and 2026-08-27), both marked *"Nothing of this exists"*. FND-04
builds the core of that agreed shape.

## 2. Capability is not authority

This is the single idea the whole boundary is built to protect.

An endpoint advertising `computer.click`, `browser.navigate`, `speaker.speak`
or `camera.observe` is saying:

> *"I am capable of performing this operation."*

It is **not** saying:

> *"I am authorised to decide when this operation should occur."*

The first is the endpoint's to state. The second belongs to Bartholomew's
executive and Governance. Three separate facts gate every directive, and all
three must hold (`bartholomew/eci/capabilities.py`):

1. **This deployment understands the capability** — the platform's frozen
   vocabulary. Unknown kind, or known kind at an unknown version, is
   *unsupported*: refused, never approximated.
2. **The endpoint declared it and the operator admitted it** — the manifest is
   the endpoint's claim; the approval ceiling is what a person actually agreed
   to. Declaring is not being granted.
3. **The endpoint says it can serve it right now** — availability, which is a
   different fact from declaration. Silence is `unreported`, not "probably
   fine", and is not directable.

## 3. What Bartholomew decides, and what the boundary merely carries

The boundary holds an **empty responder seam**. With nothing installed, an
exchange is captured and the endpoint is told so; no directive is ever
produced. That is the correct state for a deployment that has not wired an
executive in.

The real responder lives in `bartholomew/integration/eci_responder.py` and
delegates to an existing governed seam:

```
run_spoken_output_through_runtime_contract(text, enabled=…, db_path=…,
                                           identity_context=…, speak_fn=…)
```

That seam's own docstring, written a wave before this boundary existed, states
the architecture FND-04 realises:

> `speak_fn` is injected, like `capture_fn`/`stream_fn`, so this seam owns
> Governance while `spoken_output.py` owns the capability — and so the
> capability is reachable only through this path in production.

**Bartholomew governs; the capability is somebody else's to perform.** FND-04
makes the ECI be that somebody. The directive is minted *inside* the callback
the seam invokes only after all three of its gates pass — enablement
(`config/kernel.yaml`'s `voice.spoken_output`, default false),
`ParkingBrake("voice")` read fail-closed, and the Identity policy decision. So
a directive **cannot exist unless Bartholomew's own governance allowed it**.
That is requirement (J) holding structurally, not by a check someone
remembered to write at the boundary.

## 4. The admission order

See `INTERFACES.md` for the table. The part worth repeating here is what is
*not* in this package: there is no Parking Brake, no consent gate, no audit
store and no identity system in `bartholomew/eci/`. Steps 5–7 of the admission
— brake (both tiers, fail-closed), backpressure, Identity policy, durable
idempotent capture and the provenance record — are **one existing call**,
`run_inbound_through_runtime_contract()`. Reading the brake again here would
be a second place that believed it knew the halt rule.

`tests/test_fnd04_eci_boundary.py::TestStructuralProhibitions` asserts those
absences over this package's source rather than leaving them to this document.

## 5. Failure behaviour

Every one of these is a distinct, reportable outcome. None is silently turned
into success, and none is collapsed into another.

| Condition | Outcome | Recorded? |
|---|---|---|
| Endpoint not verified / boundary closed | `refused_identity` (401) | nothing |
| Malformed envelope, unknown kind or status | `refused_malformed` (422) | nothing |
| Any Parking Brake scope engaged | `refused_brake` (503) | nothing — retryable |
| Event backlog full, persistence unavailable | `unavailable` (503) | nothing — retryable |
| Identity policy denied | `refused_governance` (403) | the refusal |
| Capability unsupported / undeclared / unauthorised / unreported / unavailable | `refused_capability` (409) | the exchange |
| Result names no directive this runtime issued | `uncorrelated` (409) | the exchange; **not applied** |
| Result names another endpoint's directive | `uncorrelated` (409) | the exchange; **not applied** |
| Result arrives after the directive expired | `expired` (409) | recorded as `timed_out` |
| Second result for one directive | `already_settled` (409) | first stands |
| Redelivered exchange | `duplicate` (200) | no second exchange, **no second directive** |

**Endpoint disappears.** There is no heartbeat. An availability report goes
stale after 300s and reads as `unreported`; a directive nobody answers is
settled `timed_out` by `expire_overdue_directives()`. Neither creates a retry:
`bartholomew/actuation/recovery.py` is this repository's Recovery contract, a
retry is a *new* directive decided by a Bartholomew seam, and nothing here
contradicts that.

**`unknown` is a real answer.** An endpoint that cannot tell whether the effect
landed says so, and it is never folded into `failed`.

**`started` means issued, not heard.** For a delegated capability, the seam's
"started" outcome means the directive was issued to a capable endpoint.
Whether the words were actually said is the endpoint's correlated result, in
the directive ledger. Reading one as the other is the same mistake as reading
capture's 202 as "processed".

## 6. Operating it

The boundary is **closed by default** and opens in two independent steps,
because they are two decisions:

- **The responder** is installed by `install_seams()` at startup. It makes the
  boundary able to carry what Bartholomew decides; it makes Bartholomew decide
  nothing new. With `voice.spoken_output` off — the default — it produces no
  directive at all.
- **The endpoint channel** stays behind `BARTH_ECI_ENDPOINT_AUTH`, with its own
  fail-closed resolver, a different module global from `inbound_auth`'s and
  `device_action_auth`'s. **Opening any one of the three channels does not open
  the others.**

An endpoint needs a device credential from the existing enrolment flow
(`bartholomew/platform/devices.py` — pending → approve → complete) with the
capability in its manifest. It presents the credential in
`x-bartholomew-device-credential`, the same header and the same credential the
companion already uses, because it answers the same question: *which machine
is this*.

`/api/health`'s seam report carries `eci_responder` and `eci_endpoint_channel`,
so "is this boundary live, and is anything able to answer on it" has one
answer rather than two guesses.

## 7. What this does NOT prove

Recorded plainly, because the value of a first boundary is destroyed by
overclaiming what it has been through.

- **The reference responder is an acknowledgement, not reasoning.** It speaks
  one fixed phrase and never reads the endpoint's payload. That is deliberate
  — an endpoint that could put text into Bartholomew's mouth would have
  acquired Bartholomew's voice — but it means the slice proves *the governed
  loop*, not that Bartholomew answered what was actually asked. A deployment
  wanting the latter substitutes a responder that calls the chat/executive
  path; the boundary it plugs into does not change.
- **One transport.** HTTP only. The contract is written so a WebSocket, IPC or
  queue adapter changes no core type, but no such adapter exists or has been
  tried, so "transport-independent" is an argued property, not a demonstrated
  one.
- **One endpoint, and it is a reference client.** Not AIRI, not a companion,
  not a browser, not a phone. No real external product has been attached.
- **A bearer credential, not per-request signing.** Unchanged from Package E,
  and `docs/E_DEVICE_TRUST_AND_TRUSTED_GROUPS.md` remains the statement of what
  that does and does not close.
- **No live multi-endpoint, concurrency or soak testing.** Settlement is
  exactly-once by a conditional UPDATE and is unit-tested as such; it has not
  been run under real contention.
- **Nothing about capability *selection*.** There is no broker, no routing, no
  scoring. Which capability to use, and whether to use one at all, is the
  executive's — and in this slice a single Bartholomew seam decides it.

## 8. Where the integrations go from here

The point of one boundary is that the next integration is a client of it
rather than a new architecture.

- **AIRI** should use this boundary primarily as Bartholomew's **face, voice
  and presence** — advertising `multimodal.spoken_output` and its own
  presence/observation capabilities, submitting what it hears and sees as
  observations and requests, and carrying out governed directives. It is not
  where intent is decided, and it should never acquire a planner.
- **The Windows companion** already has the deeper channel it needs for
  actuation (`bartholomew/actuation/`, unchanged and not superseded). Its
  observation and presence halves belong here.
- **Phone, browser, home automation, cameras, speakers, specialist tools** are
  expected to take the same shape: declare, report availability, observe,
  request, carry out, report. Each one that instead grows its own intent model
  is a conflict under `CONSTITUTION.md` invariant 10, not an implementation
  detail.

See `tests/test_fnd04_eci_vertical_slice.py` for the complete worked loop,
`tests/test_fnd04_eci_boundary.py` for the refusals and the structural
prohibitions, and `INTERFACES.md`'s "External Capability Interface" section for
the contract.
