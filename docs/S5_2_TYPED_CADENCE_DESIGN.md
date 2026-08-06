# S5.2 Design — Typed Cadence

> **Authority note:** this document is subordinate to `ROADMAP.md` (Stage 5's canonical exit
> criteria and locked safety-scaffolding sequence) and `docs/S5_1_INITIATIVE_ENGINE_ARCHITECTURE_
> DESIGN.md` (S5.1, approved 2026-08-06), whose chassis this builds directly on top of. It does
> not modify either document's already-approved content except where explicitly noted in section 7
> (a narrow, flagged extension of S5.1's Governance-exemption boundary, found while designing this
> stage's first real consumer of that seam, approved by the project owner 2026-08-06) and a small
> precision fix to S5.1 §7's Runtime Contract pipeline table (splitting its Capability-stage
> description per-transition; no behaviour change, since the state machine already separated
> `propose` from `deliver` unambiguously).
>
> **Status:** proposed 2026-08-06, revised 2026-08-06 per reviewer feedback (section 9, "Cadence
> produces eligibility, not delivery" — the expiry-policy exemption in section 7 is approved;
> section 9's eligibility/delivery separation is added and awaits final confirmation). Not yet
> approved as a whole, not yet implemented.
>
> **Scope of this pass:** the typed cadence model (interval, window, daily, weekly), its parsing/
> validation/next-run computation, wiring it into the existing scheduler loop with no schema
> change, and `initiative_sweep` — S5.1's own explicitly deferred item ("§14 item 3: recommend
> with Typed Cadence"), now designed and, on approval, implemented. **No check-in, weekly-review,
> next-best-action, wellness, or maintenance-suggestion drive is designed here** — those are S5.7,
> each its own separately-approved pass built on this chassis, per S5.1's own scope discipline.

## 1. What this closes

`ROADMAP.md`'s locked Stage 5 sequence names "typed cadence (interval / daily / weekly wall-clock)"
as the first safety-scaffolding step. The scheduler that exists today
(`bartholomew/kernel/scheduler/cadence.py`) supports exactly two cadence shapes, both *relative*:
`every:<seconds>` (fire every N seconds) and `window:<seconds>:<times>` (fire up to K times in a
rolling window). Both are correct for what they were built for — `self_check`, `curiosity_probe`,
`reflection_micro`, `fts_optimize`, `awaiting_response_check` — none of which care what time of
day it is. A check-in or a weekly review inherently means "the same wall-clock time each day/week",
which a relative interval cannot express: `every:86400` drifts by however long each tick actually
took plus scheduler jitter, and after enough restarts it stops meaning "morning" at all. This
document adds the two wall-clock cadence types the locked sequence names, without touching either
existing type's behaviour.

It also closes S5.1's own explicitly deferred item (§14 item 3 of that document): *"Should
`initiative_sweep`'s cadence and `REGISTRY` entry be added now or with S5.2 (Typed Cadence)? …
Recommend: with Typed Cadence."* This document designs that drive — the first piece of production
code to actually call `initiative_store.py` / `run_initiative_through_runtime_contract()`.

## 2. Proposed ownership

No new ownership row. This is infrastructure under the Kernel Executive / Planning row
`COGNITIVE_RUNTIME.md` already assigns `daemon.py` / `planner.py` / `scheduler/*` to — the same row
S5.1's `Initiative` concept lives under. Changes land in the existing `bartholomew/kernel/
scheduler/` package: `models.py` (new dataclasses), `cadence.py` (parsing/computation), `loop.py`
(one new argument threaded through two existing call sites), `drives.py` (one new drive +
`REGISTRY` entry).

## 3. The typed Cadence model

Added to `bartholomew/kernel/scheduler/models.py`, alongside the existing `Tick`/`Nudge`/
`Reflection` dataclasses:

```python
@dataclass(frozen=True)
class IntervalCadence:
    """Existing 'every:<seconds>' shape, given a real type."""
    seconds: int

@dataclass(frozen=True)
class WindowCadence:
    """Existing 'window:<seconds>:<times>' shape, given a real type."""
    window_seconds: int
    times: int

@dataclass(frozen=True)
class DailyCadence:
    """Fire once per wall-clock day at hour:minute, in the daemon's configured timezone."""
    hour: int    # 0-23
    minute: int  # 0-59

@dataclass(frozen=True)
class WeeklyCadence:
    """Fire once per wall-clock week at day_of_week/hour:minute, in the daemon's timezone."""
    day_of_week: int  # 0=Monday .. 6=Sunday (Python date.weekday() convention)
    hour: int
    minute: int

Cadence = IntervalCadence | WindowCadence | DailyCadence | WeeklyCadence
```

**String encoding** (`bartholomew/kernel/scheduler/cadence.py`'s `parse()`, extended, existing two
branches unchanged byte-for-byte):

| Form | Type | Example |
|---|---|---|
| `every:<seconds>` | `IntervalCadence` | `every:900` (existing, unchanged) |
| `window:<seconds>:<times>` | `WindowCadence` | `window:3600:2` (existing, unchanged) |
| `daily:<HH>:<MM>` | `DailyCadence` | `daily:08:00` (new) |
| `weekly:<D>:<HH>:<MM>` | `WeeklyCadence` | `weekly:0:08:00` (new — Monday 08:00) |

This keeps `REGISTRY[task_id]["cadence"]`, `kernel.yaml`'s `drives.<task_id>` override, and the
`DRIVE_<TASK_ID>` env var override (`resolve_cadences()`, `scheduler/loop.py`) all working exactly
as today — every existing override mechanism is a plain string, and stays one. `parse()` is the
single, already-existing parsing entrypoint; this document adds two branches to it, not a second
parser.

**Fail-closed validation**, matching the existing `every`/`window` branches' own style (raise
`ValueError` on malformed input, never silently default): `daily`/`weekly` reject an out-of-range
hour (not 0–23), minute (not 0–59), or day-of-week (not 0–6) at parse time — a typo in
`kernel.yaml` or an env override must fail loudly at startup, not silently compute a wrong time.

## 4. Wall-clock computation and timezone handling

**Reuses existing infrastructure — nothing new is introduced.** `KernelDaemon.__init__` already
does `self.tz = tz.gettz(self.cfg["timezone"])` (`daemon.py:94`), sourced from `config/kernel.yaml`'s
mandatory `timezone` key (currently `"Australia/Brisbane"`) via `python-dateutil` (already a
declared dependency, `pyproject.toml`). `scheduler/loop.py::run_scheduler()`'s own docstring
already documents `ctx` as needing `tz` (`"Must have: mem.db_path, cfg (optional), tz"`) — this
attribute is already expected by the scheduler loop's contract, just not yet threaded into cadence
computation. This document plumbs it the rest of the way, rather than inventing a second timezone
mechanism (a new env var, a new config key, or `zoneinfo` alongside the existing `dateutil.tz`
usage) — there is exactly one timezone source in this codebase, and it stays that way.

**`compute_next_run()` signature** (`cadence.py`, extended):

```python
def compute_next_run(
    last_run_ts: int | None,
    scheduled_ts: int | None,
    cadence_str: str,
    now_ts: int,
    window_state: str | None = None,
    tz: tzinfo | None = None,   # new; required only by daily/weekly branches
) -> tuple[int, str | None]:
```

`tz=None` is fine for every existing caller of the `every`/`window` branches (fully
backward-compatible default). If a `daily`/`weekly` cadence string is parsed and `tz` is `None`,
`compute_next_run()` raises `ValueError` rather than silently falling back to UTC — a silent
timezone substitution would compute a wrong, misleading wall-clock time with no visible error,
exactly the kind of failure this project's fail-closed philosophy (Phase B's B5 startup-integrity
work, `awaiting_response`'s non-empty-subject validator) exists to prevent.

**Algorithm for `daily`/`weekly`** (prose; exact DST-safe datetime arithmetic is an
implementation-time detail, not fixed here):
1. Reference point: `scheduled_ts` if this cadence has run before (the time *this* tick was
   targeting, not necessarily when it actually executed — avoids drift the same way the `every`
   branch anchors to `last_run_ts` rather than `now_ts`), else `now_ts` for a brand-new
   registration.
2. Convert the reference point to a local `datetime` in `tz`.
3. **Daily:** the next occurrence is the reference date's `hour:minute` plus exactly one calendar
   day.
4. **Weekly:** the next occurrence is the reference date's `hour:minute` plus exactly seven
   calendar days (not "next matching `day_of_week`" computed from `now`, since the reference point
   already encodes the correct day once cadence 1 has run once — the first-ever run is the one
   place `day_of_week` matters, to land on the right day initially).
5. Convert back to a UTC epoch integer for storage in the existing `next_run_ts INTEGER` column.

**No jitter, no `BARTH_SPEED_FACTOR` scaling for `daily`/`weekly`.** Both exist today for the
`every`/`window` branches for two reasons that don't apply here: jitter (~5%) spreads
self-maintenance load to avoid synchronized ticks (irrelevant to a single daily check-in);
`BARTH_SPEED_FACTOR` compresses intervals for fast test runs (e.g. `tests/test_stage0_alive.py`
sets it to `0.01`), which has no coherent meaning against a wall-clock target — "8am, but 100x
faster" isn't a real time. **This means daily/weekly cadences cannot be sped up for testing the way
existing drives are** — flagged as an explicit consequence (§11 item 3) with a recommendation for
how future S5.7 tests should instead exercise them (mocking/injecting `now_ts`, not env-scaling).

**Missed ticks are not caught up.** If the daemon is down at the scheduled time and restarts
later, `compute_next_run()` always returns the *next future* occurrence relative to `scheduled_ts`
— never a backlog of missed days. This mirrors `every`'s existing behaviour (which also never
replays missed intervals, just resumes from `last_run_ts + delta`) and avoids a stale-feeling
"morning check-in" arriving at 3pm because the machine just woke up — itself a small instance of
`CONSTITUTION.md` §5's "adaptive to time sensitivity" principle, even though this document doesn't
design any actual check-in behaviour.

**DST is an accepted limitation, not solved here.** A daily/weekly tick can shift by up to an hour
twice a year across a DST transition. No special-casing is added for it — check-in-style timing
does not need second-level (or even minute-level) precision, and Stage 6's cross-device work will
revisit timezone handling more broadly regardless. Recorded honestly, not glossed over.

## 5. Backward compatibility

- **No schema change.** `scheduled_tasks.cadence` stays `TEXT`, `next_run_ts` stays `INTEGER`
  (UTC epoch seconds) — `store.py`/`persistence.py`'s `upsert_scheduled_tasks()`, `next_due_task()`,
  `update_next_run()` are all untouched; they operate on plain strings/integers regardless of which
  `Cadence` type produced them.
- **`loop.py`'s two `compute_next_run()` call sites** (the idempotency-recheck branch and the
  post-drive-execution branch, `loop.py:162` and `loop.py:218`) both gain one new argument,
  `tz=ctx.tz`. No other change to `run_scheduler()`'s control flow.
- **Regression guard**: the existing five `REGISTRY` cadence strings (`self_check`,
  `curiosity_probe`, `reflection_micro`, `fts_optimize`, `awaiting_response_check`) must parse to
  behaviourally-identical `IntervalCadence`/`WindowCadence` values and produce identical
  `compute_next_run()` output before and after this change — a pinned-values test (§13) is the
  acceptance bar for "no behaviour change to any existing drive."

## 6. `initiative_sweep`: the first drive built on S5.1 and S5.2

Mirrors `drive_awaiting_response_check`'s exact shape (`scheduler/drives.py:163`) — the closest
and only precedent for "scan a governed store for due transitions, drive each through its Runtime
Contract seam individually, one entry's failure doesn't affect another's":

```python
async def drive_initiative_sweep(ctx: Any) -> Nudge | None:
    store = getattr(ctx, "initiative_store", None)
    if store is None:
        return None

    from bartholomew.kernel.runtime_contract import run_initiative_through_runtime_contract

    now_ts = int(time.time())
    executor = getattr(ctx, "blocking_executor", None)
    expiring = await run_off_loop(store.list_expiring, now_ts, executor=executor)

    for initiative in expiring:
        try:
            await run_initiative_through_runtime_contract(
                ctx, "expire",
                initiative_id=initiative.id,
                actor="scheduler:initiative_sweep",
            )
        except Exception as e:
            print(f"[Scheduler] Error expiring initiative {initiative.id}: {e}")

    return None
```

**`REGISTRY` entry**: `"initiative_sweep": {"fn": drive_initiative_sweep, "cadence": "every:900"}`
— matches `awaiting_response_check`'s existing interval exactly, reusing a precedent rather than
inventing a new one.

**Self-maintenance-exempt at the drive level**: `initiative_sweep` is added to
`_SELF_MAINTENANCE_DRIVES` in `runtime_contract.py`, per S5.1 §7's own instruction — it never
proposes new outbound contact, only closes out rows already past their `expires_at`.

**Honest scope limit**: this is the first production code exercising `initiative_store.py`, but
only its `expire` transition. `propose`/`defer`/`deliver`/`resolve`/`cancel`/`supersede` remain
untested against a real drive until S5.7 designs one. Recorded here rather than implied — sweeping
before any drive proposes anything means there is, by construction, nothing to sweep in production
until S5.7 lands; this drive is correct but inert until then, and its own tests (§13) must
therefore seed synthetic rows directly rather than relying on a real proposing drive to exist yet.

## 7. Found while designing this: a gap in S5.1's Governance-exemption boundary (approved)

S5.1 §7 states, deliberately: *"`kind` is deliberately not added to `_SELF_MAINTENANCE_DRIVES`...
an Initiative represents genuine potential outbound contact... it must be evaluated by Identity
Policy for real, every time, **no exemption**."* Designing `drive_initiative_sweep` (§6) surfaces a
real consequence of that blanket rule: the `expire` transition itself would also be evaluated for
real against `allow_proactive.<category>` — but `expire` is pure bookkeeping (an initiative already
`approved`/`deferred`/`delivered` simply aging past its TTL), never a new act of outbound contact.
If `allow_proactive.<category>` is denied *after* an initiative of that category was already
approved (an operator or the user changes the policy mid-flight), that initiative's later `expire`
transition would itself be denied — leaving it permanently stuck in a non-terminal status, never
reaching `expired`, since the only mechanism that could close it out is now blocked by the same
policy it's trying to *stop* being subject to.

**Proposed fix, scoped narrowly:** a new, transition-level (not drive-level) exemption,
`_SELF_MAINTENANCE_INITIATIVE_TRANSITIONS = frozenset({"expire"})`, checked inside
`run_initiative_through_runtime_contract()` itself — *only* the `expire` transition skips the
Identity Policy gate (ParkingBrake's `"initiative"` scope still applies; the audit/reflection
trail is unaffected). `propose`, `defer`, `deliver`, `resolve`, `cancel`, and `supersede` remain
exactly as S5.1 specified: evaluated for real, every time, no exemption. This is symmetric with
why `initiative_sweep`'s own drive-level exemption exists (§6) — a mechanism that can, by
construction, never constitute new outbound contact does not need Identity Policy's protection,
and denying it can only cause harm (a permanently stuck row) with no corresponding benefit.

This was flagged prominently rather than folded in silently, because it technically narrows a rule
S5.1 stated as absolute. No live behaviour regressed — S5.1 was not yet implemented, so nothing in
production changed — but per the standing instruction that a design change (however small) gets
surfaced rather than assumed, it was held for explicit confirmation rather than assumed correct on
S5.2's own internal consistency alone.

**Approved by the project owner** (2026-08-06): "An already-approved initiative must always be
able to reach its terminal expired state, even if policy changes after approval... this
strengthens the lifecycle semantics without weakening Governance." Treated as settled for S5.2's
implementation; `_SELF_MAINTENANCE_INITIATIVE_TRANSITIONS = frozenset({"expire"})` is part of this
design as approved, not merely proposed.

## 8. Cadence-aware Initiative default-window helper (optional)

A small helper, `default_initiative_window(cadence: Cadence, now: datetime) -> tuple[str, str]`
(returning `due_at`, `expires_at`), giving a sensible per-cadence-type suggestion a future S5.7
drive *may* call: `IntervalCadence` → `[now, now + interval]`; `DailyCadence` → `[now, end of local
day]`; `WeeklyCadence` → `[now, end of local week]`. This does **not** weaken S5.1 §14 item 6
("`expires_at` must be supplied explicitly at `propose` time, no silent default") — the helper
computes a value; the caller must still pass it explicitly. It exists only to reduce boilerplate
once a real drive needs it, not to become an implicit default inside the seam itself.

## 9. Cadence produces eligibility, not delivery

This principle already exists implicitly in S5.1's state machine (§5 of that document): `propose`
and `deliver` are named as categorically separate transitions (listed separately in S5.1 §6's seam
signature), and `approved` is explicitly described there as "not yet delivered; awaiting its
delivery-timing check." This document makes it explicit and binding, because Typed Cadence is
precisely the layer responsible for the "becoming due" trigger S5.1 left unspecified, and the
boundary matters most exactly where a cadence tick and a delivery decision could otherwise be
conflated. (S5.1 §7's own Runtime Contract pipeline table has also been tightened to describe the
Capability stage per-transition rather than in a way that could be read as `propose` conditionally
reaching `deliver` within the same call — a precision fix, not a behaviour change, since the state
machine and transition list were already unambiguous on this point.)

**The rule:** a cadence reaching its scheduled time makes a drive *tick* — nothing more. A tick
gives a drive the opportunity to evaluate whether to propose an Initiative (`propose`, which runs
Executive → Governance and lands the initiative in `approved` or `denied`) or, for a
maintenance-shaped drive like `initiative_sweep` (§6), to sweep a store for a different transition
entirely (`expire`). **No cadence tick may call `deliver` directly, and no drive may treat "my
cadence is due" as sufficient reason to deliver anything.** The full path is always:

```
Cadence due -> drive tick -> propose -> Executive -> Governance -> approved -> [separate
delivery-timing check, not this drive's cadence] -> deliver
```

**Why this needs its own gate, separate from the proposing drive's cadence:** an `approved`
initiative's actual delivery must remain contingent on conditions that have nothing to do with why
it was proposed, and that can change after proposal — quiet hours (S5.4), mute (S5.3), and (per
this principle, reserved for later, not designed here) user activity, device state, and
competing-priority arbitration among several simultaneously-`approved` initiatives. None of these
are Typed Cadence's concern, and none of them are decided by *when* the proposing drive happened
to tick. Coupling delivery to the proposing drive's own cadence would mean every future
contextual-gating feature has to be reimplemented inside every drive that proposes anything —
exactly the "feature-specific scheduler" duplication S5.1 §1 named as the problem this whole
chassis exists to avoid.

**Concretely, within this document's own scope:** `initiative_sweep` (§6) is a clean illustration
— its cadence governs only when it *checks* for expired rows; it never delivers anything. §8's
default-window helper computes a `due_at`/`expires_at` pair for a `propose` call, not a delivery
decision. Neither piece of code in this document ever calls `deliver`, and this section makes
explicit that nothing added by a later stage should either, purely as a consequence of a cadence
firing.

**What this reserves for S5.3/S5.4, not decided here:** the actual delivery-timing check — the
mechanism that walks `approved`/`deferred` initiatives whose `due_at` has passed and decides,
against current mute/quiet-hours/(future contextual) state, whether to call `deliver` now — is
*not* designed in this document. It is very likely its own small, independently-cadenced sweep
(structurally similar to `initiative_sweep`, but calling `deliver`/`defer` instead of `expire`),
but that is S5.3/S5.4's decision to make, not asserted here (see §11 item 5). This document's
contribution is the boundary itself: whatever that future mechanism turns out to be, it is
guaranteed to be a distinct component from any proposing drive's cadence, because no proposing
drive is ever given a `deliver` call to make.

## 10. Non-negotiable invariants

- `parse()` is fail-closed for every cadence type: a malformed string raises `ValueError`, never
  silently substitutes a default (§3).
- `compute_next_run()` never returns a `next_run_ts` in the past relative to its inputs — no
  immediate double-fire, no catch-up storm (§4).
- No behavioural change to any of the five existing `REGISTRY` drives' actual firing schedule,
  proven by a pinned regression test, not asserted by inspection alone (§5, §13).
- `daily`/`weekly` cadences require an explicit `tz`; there is no silent UTC fallback (§4).
- `initiative_sweep` never proposes, delivers, or resolves — only `expire`, per §6/§7's narrowly
  scoped exemption. Widening that exemption to any other transition is out of scope for this
  document and would need its own review.
- **A cadence tick may only lead to `propose` or a non-outbound-contact transition like `expire` —
  never `deliver` directly** (§9). Delivery is always decided by a separate, independently
  gated mechanism, not by the proposing drive's own cadence firing.

## 11. Open design questions for approval time

1. ~~**§7's proposed `expire`-transition exemption**~~ — **resolved, approved by the project owner
   (2026-08-06)**: `_SELF_MAINTENANCE_INITIATIVE_TRANSITIONS = frozenset({"expire"})` as scoped.
2. **`initiative_sweep`'s cadence** — fixed at `every:900`, or configurable like every other drive
   via `kernel.yaml`/env override? *Recommend: configurable* — `resolve_cadences()`'s existing
   precedence already applies uniformly to every `REGISTRY` entry; special-casing this one would
   be inconsistent for no benefit.
3. **How should S5.7's future daily/weekly-cadence drives be tested**, given §4 rules out
   `BARTH_SPEED_FACTOR` scaling for them? *Recommend: inject/mock `now_ts` directly in
   `compute_next_run()` calls rather than relying on wall-clock time to actually pass* — flagged
   now so S5.7's design doesn't rediscover this constraint from a failing/slow test suite.
4. **Day-of-week convention** — `WeeklyCadence.day_of_week` uses Python's `date.weekday()`
   (Monday=0), not cron's (Sunday=0) or ISO 8601's (Monday=1)? *Recommend: keep Python's
   convention* — it's what `datetime`/`dateutil` already use internally, avoiding a translation
   layer at every call site.
5. **Should the future delivery-timing check (S5.3/S5.4, §9) be its own dedicated `REGISTRY`
   drive with its own cadence, or folded into `initiative_sweep`?** *Recommend: its own drive* —
   delivery-timing logic will need to reason about mute/quiet-hours/future contextual gating,
   categorically different from `initiative_sweep`'s simple TTL bookkeeping (§6); keeping them
   separate is a direct consequence of §9's own eligibility/delivery separation principle, not
   just a style preference. Non-binding on S5.3/S5.4's own design, recorded here only so that
   design doesn't have to rediscover the reasoning.

## 12. Explicitly deferred / out of scope

- Any concrete check-in / weekly-review / next-best-action / maintenance / wellness drive (S5.7+).
- Default-off consent (S5.3), quiet-hours defer (S5.4), dry-run mode (S5.5), rationale-logging
  presentation (S5.6) — Typed Cadence only supplies the "when does a drive tick" primitive those
  stages will build on and gate.
- Per-device / per-user timezone configuration — this document reuses the daemon's single
  configured `timezone` (`kernel.yaml`); multi-timezone/cross-device concerns are Stage 6.
- S5.1's reserved (not implemented) dependency/hierarchy mechanism (§13 of that document) —
  unrelated to cadence, not touched here.
- Wiring `_run_daily_reflection()`/`_run_weekly_reflection()` (`daemon.py`) onto an automatic
  cadence — these remain manual/dev-command-triggered only (`handle_command`), unchanged by this
  document; doing so would also run into S5.1 §3's reflection-ownership gap, a separate blocker.

## 13. Verify plan

- `tests/test_cadence_types.py` (new) — `parse()` round-trips for all four types, including
  malformed-input rejection (bad hour/minute/day-of-week ranges) for the two new ones;
  `compute_next_run()` correctness for `daily`/`weekly` including at least one DST-transition-date
  case; `compute_next_run()` raising when `tz=None` is passed with a `daily`/`weekly` cadence.
- A pinned-values regression test (extends `tests/test_scheduler_startup_readiness.py` or a new
  file) asserting today's five `REGISTRY` cadence strings parse to identical typed values and
  produce identical `compute_next_run()` output pre/post this change.
- `tests/test_initiative_sweep_drive.py` (new) — seeds a mix of expired and non-expired
  synthetic `initiatives` rows directly (no real proposing drive exists yet, per §6's honest scope
  note), asserts only expired rows transition, asserts per-entry failure isolation (one bad row
  doesn't block the rest), and asserts the `expire` transition succeeds even when
  `allow_proactive.<category>` is denied (the §7 regression case).
