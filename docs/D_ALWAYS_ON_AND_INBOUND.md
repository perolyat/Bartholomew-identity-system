# Always-On Runtime and Governed Inbound Capture — Planning Note

**Status: APPROVED (proposal), IMPLEMENTED (this branch).**

> Approved 2026-08-27 by Taylor, under the User Approval Gate, with corrections recorded in §8.
> This is a right-sized planning note per `docs/TILT.md`'s vertical-slice discipline — **not** a
> design document and **not** a second authority on anything. Where this note and a canonical
> document disagree, the canonical document wins.
>
> Authority above this note: `CONSTITUTION.md` (safety invariants, emergency shutdown),
> `DECISIONS.md` (deployment architecture; Parking Brake authority tiers; the 2026-08-18
> "inspect, but do not mutate" Governance decision), `COGNITIVE_RUNTIME.md` (the Runtime
> Contract and the kill switch), and `INTERFACES.md` §6 (the API/client boundary).

## 0. The slice in one paragraph

Bartholomew cannot be a Personal Executive System if it only exists while Taylor has a terminal
or a browser open, and it cannot be one if it learns about the world exclusively by being told.
This slice does two narrow things. It makes Bartholomew **a supervised service** rather than a
program someone has to remember to run — one entry point, one process per runtime, honest health,
clean restart. And it opens **one governed door** through which an authenticated external event
can arrive, be recorded with its provenance, and go no further. It is deliberately not a
deployment platform, not a webhook product, and not email or calendar. It is the operational
foundation those things later stand on.

## 1. What was already true

Worth stating, because the runtime was in better shape than it looked and most of this slice is
*not* new machinery:

* The kernel already outlives the browser. It runs server-side in the API process; a client
  connecting or disconnecting was never a lifecycle event.
* Graceful shutdown already worked (Phase B stage B5): admission close, drain, task cancellation,
  WAL checkpoint, clean-shutdown marker.
* Duplicate daemons against one database were already prevented (`ProcessLock`, stage B6).
* Durable state already survived restart, and the scheduler already caught up overdue drives.
* Idempotency already existed as a concept — `ticks.idempotency_key TEXT UNIQUE`.

What was missing was never the runtime. It was **a way to start it that isn't a person's
terminal**, and **a health surface that tells the truth about the scheduler**.

## 2. What actually prevented always-on operation

1. No non-interactive entry point. The sanctioned start scripts ran `uvicorn --reload` in the
   foreground; the process died with the terminal and nothing started at boot.
2. `--reload` in those scripts is actively unsafe against a held process lock — a crash loop.
3. **Health lied by omission.** `/healthz` answered `ok` from a process whose scheduler task had
   died, and `/api/liveness/self`'s `last_tick` was only ever written by `set_last_tick()`, which
   the scheduler loop never called — so it reported process-start time forever. A scheduler could
   die and nothing anywhere would notice.
4. The scheduler task was created fire-and-forget with no done-callback, so its death was silent.

## 3. Process topology

```
systemd unit / docker compose / (optional) Windows service wrapper
  └── python -m bartholomew serve
        └── uvicorn (workers=1, reload=False)
              └── FastAPI
                    └── KernelDaemon  ── ProcessLock ── scheduler ── background tasks
```

Kernel and API stay co-located. That is not a convenience: SQLite persistence is single-writer
and the daemon takes an exclusive lock on the database file, so splitting them or adding workers
produces one working process and N−1 that fail on the lock. `serve` refuses `--workers > 1` and
`--reload` outright, with the reason, rather than letting a unit file discover it at runtime.

Bartholomew does not supervise itself. Restart and start-at-boot are systemd's, Docker's or the
Windows service manager's job — see `deploy/README.md`.

## 4. Scheduler lifecycle and health

Unchanged in cadence, pacing, `next_run_ts` semantics and drive execution. Two additions:

* **A heartbeat** (`bartholomew/runtime/health.py`), beaten once per *loop iteration* — not per
  executed drive, because an idle loop with nothing due is alive and a drive-only heartbeat would
  report a healthy idle scheduler as dead. In-memory by design: it answers "is *this* process's
  scheduler running right now", which no previous process's durable state can answer.
* **Supervision**: a done-callback on the scheduler task records cancellation as a clean stop and
  anything else as a failure.

`GET /api/health` now reports `service`, `runtime`, `scheduler` and `inbound` separately, and its
top-level `status` is `degraded` — not `ok` — when any of them has failed. Unknown stays a third
state; "we could not tell" is never reported as "it works". `/healthz` is untouched and remains
the trivial probe.

## 5. The inbound seam

```
external sender
  → POST /api/inbound/events
  → principal resolution            (fail closed; §6)
  → envelope validation             (422 on malformed)
  → run_inbound_through_runtime_contract()
        Observation → Interpretation → Executive → Governance → Capture → Reflection
  → inbound_events row + one ActionReflection
```

**The envelope is domain-blind.** `source_id`, `event_id`, `event_type`, `occurred_at`, `payload`.
`event_type` is an opaque string: it is stored, echoed, and never branched on. There is no
`if email`, no `if calendar`, and one `CandidateAction` kind (`inbound_capture`) rather than one
per provider — so allowlisting capture can never widen into allowlisting a domain. Future provider
adapters translate *into* this envelope, upstream of the ingress.

**Capture is not comprehension.** Nothing on this path writes Memory, creates an objective,
invokes a skill, or schedules work. The row records *that something arrived*; it never records
that Bartholomew believes it. Tests assert `memories` and `nudges` stay empty.

**What the status codes mean** — three different states, never conflated:

| Code | Meaning |
|---|---|
| 202 | Authenticated, validated and **durably persisted as captured**. Explicitly *not* processed. |
| 200 | Duplicate of an already-captured event; the existing row is reported, no second event exists. |
| 401 | Not verified. Nothing read as authoritative, nothing written. |
| 403 | Body claimed a `source_id` other than the verified one, or Governance policy refused. |
| 413 / 422 | Too large / malformed. Nothing written. |
| 503 | Parking Brake engaged, or runtime/persistence unavailable. Nothing written; retryable. |

There is no code meaning "processed". The 202 body says so in words as well.

## 6. Authentication seam

Authentication, `Principal`, sessions, identity-to-runtime resolution and per-user isolation are
**owned by the authenticated control plane and are not implemented here**. This slice provides
`inbound_auth.InboundPrincipalResolver` — one async method — and a structural
`VerifiedInboundSource` (three attributes: `source_id`, `runtime_id`, `verified_by`) that the
control plane's own Principal can satisfy directly or through a thin adapter it owns. It is not a
second principal type and nothing in production code constructs one.

**Default: deny.** With no resolver installed, every inbound request is refused — including from
loopback. Fail-closed three ways: no resolver, a resolver returning `None`, and a resolver that
raises.

**Local-peer status is reachability, never authority.** The existing loopback boundary decides
where this process can be reached from; it has never decided who may capture events, and nothing
in the inbound path consults it. Setting `BARTH_API_ALLOW_NON_LOOPBACK=1` authorises nothing here,
and there is a test asserting exactly that.

The test-only resolver, which exists so the end-to-end HTTP path is provable against a real server
before the control plane lands, requires **two** environment variables that exist in no deployed
configuration, and announces itself in three places at once: a startup warning,
`test_resolver_active` on `/api/health`, and `verified_by="test-resolver"` on every durable row it
admits. It cannot enable itself silently, and not from a single stray variable.

## 7. Governance, provenance, idempotency

**Parking Brake — "inspect, but do not mutate."** A braked inbound request writes **nothing at
all**: no `inbound_events` row, no Reflection. Recording a "received and refused" row would itself
be a governed-state mutation performed while the user has halted mutation — the exact side door a
brake exists to close. The caller gets a truthful, retryable 503, nothing is acknowledged as
captured, and the sender's own retry succeeds once the brake is released. Reading
`/api/inbound/events` still works under the brake, because inspection is what a halt must not hide.

No new brake scope was added. The gate is the existing engaged-state fail-closed read — the same
one that guards memory mutation, and for the same reason: capture belongs to none of the existing
subsystem scopes. A scoped *downstream-processing* category, if ever wanted, needs its own
evidence and approval.

**Identity policy.** `inbound_capture` is allowlisted in `Identity.yaml`, following the recorded
precedent of S1.3's `notify` and S1.4's `awaiting_response_*` entries. What that authorises is
narrow and stated at the allowlist entry itself: recording that an authenticated external event
arrived — not believing it, acting on it, or executing anything on its behalf.

**Provenance.** Every row answers: where it came from (`source_id`), what verified it
(`verified_by` — never a caller-supplied value), when we received it (`received_at`) versus when
the sender says it happened (`occurred_at`), exactly what content was accepted
(`payload_json` + `payload_sha256`), and what happened to it (`outcome`, `governance_reason`).
One `ActionReflection` per capture goes to the existing shared sink — not a parallel provenance
system — carrying the digest rather than the payload, so the audit trail proves what was accepted
without copying third-party content into a second store.

**Idempotency.** `UNIQUE(source_id, event_id)`, mirroring `ticks.idempotency_key` rather than
inventing a second mechanism. The constraint, not the pre-check, is the guarantee: concurrent
deliveries both pass a pre-check and only one wins the INSERT, and the duplicate branch reports
the row it actually read back. The digest is over canonical (key-sorted) JSON, so a re-serialised
retry is still recognised as the same event. Idempotency is scoped per source — two sources do not
share an id space.

## 8. Corrections applied from the approval

1. No interim production HMAC/shared-secret resolver was built. Production fails closed until the
   control plane's resolver is installed.
2. No public-internet exposure. The proof is loopback and the existing non-loopback boundary is
   preserved unchanged.
3. No competing runtime registry and no competing principal type. The single-runtime lifecycle is
   kept as-is with an injectable interface; `app.py` was touched last and only for router
   inclusion, the test-resolver hook, and the component-health helper.
4. Brake semantics corrected to zero governed-state mutation, with a test proving it.
5. 202/200/503 semantics as in §5. No fabricated success anywhere.
6. `serve` + systemd + retained Docker Compose; NSSM documented as an optional operator-managed
   adapter, never bundled or required.

## 9. Deliberately not done

* The `app.py` `on_event` → `lifespan` migration. It is a real improvement and it is deprecated
  API, but it is not required by anything in this slice and `app.py` is the likeliest collision
  point with the authenticated control plane's work. Left for whoever restructures that file next.
* Email, calendar, cameras, sensors, companion apps, provider adapters of any kind.
* A queue or event-processing framework. One table and one unique constraint were enough.
* Objective Continuity and any downstream consumption of captured events.
* Any change to retrieval.

## 10. Evidence

| Claim | Where |
|---|---|
| Service starts with no terminal/browser; scheduler runs; heartbeat advances | `tests/integration/test_always_on_service.py` (real subprocess, real HTTP) |
| Client disconnects have no lifecycle effect | same |
| Graceful shutdown: clean marker, closed write fence, no startup incident, lock released | same |
| Restart preserves durable state written through the real governed path | same |
| Second instance against one database refused; `--workers`/`--reload` refused | same |
| Valid event captured with provenance; duplicates; malformed; oversized | `tests/integration/test_inbound_http.py` (real server) |
| Unauthenticated *from loopback* refused; non-loopback opt-in authorises nothing | same |
| Test resolver needs both gates and announces itself | same |
| Brake: zero mutation, retryable, inspection still works, capture resumes | same + `tests/test_inbound_capture_seam.py` |
| Persistence failure reported, never fabricated; governance unreadable fails closed | `tests/test_inbound_capture_seam.py` |
| Heartbeat/stall/supervision logic; health degrades on a dead scheduler | `tests/test_always_on_runtime_unit.py` |
