# S5.4 Design — Quiet-Hours Defer

> **Authority note:** subordinate to `ROADMAP.md` (Stage 5's locked safety-scaffolding sequence)
> and `docs/S5_3_DEFAULT_OFF_CONSENT_AND_MUTE_DESIGN.md` (S5.3, approved and implemented
> 2026-08-07), whose `initiative_delivery_check` drive this builds directly on top of.
>
> **Status:** approved 2026-08-07 (design proposal + architectural addendum both approved by the
> project owner). Implementation in progress under this document.
>
> **Scope of this pass:** a pluggable suppression-policy registry (quiet hours + NotifySkill's own
> manual mute today; Driving Mode/Focus Mode/etc. are a registry entry away, not built here), a
> per-initiative `delivery_policy` (standard/immediate/silent/critical_override), richer
> `initiative_audit` detail, and tick-scoped coalescing with deterministic digest summarization.
> Dry-run (S5.5), rationale-logging presentation (S5.6), and any concrete proposing drive (S5.7)
> remain out of scope.

## 1. Suppression-policy registry (extensible beyond quiet hours)

`bartholomew/kernel/notification_suppression.py` (new): an ordered list of independently pluggable
`(name, async check_fn(ctx, cache) -> bool)` pairs.

```python
SUPPRESSION_POLICIES: list[tuple[str, SuppressionCheck]] = [
    ("quiet_hours", _check_quiet_hours),
    ("notify_muted", _check_notify_muted),
    # future: ("driving_mode", _check_driving_mode), ("focus_mode", _check_focus_mode)
]
```

`active_suppression_reason(ctx, cache)` walks the list, returns the first active policy's name or
`None`. Adding Driving Mode/Focus Mode later means appending one entry + its own check function —
no changes to the drive's loop, the seam, or the schema. `cache` is a plain dict the drive creates
once per tick; NotifySkill's settings are fetched at most once per tick (not once per due
initiative) and reused across both existing checks.

`notify_muted` folds in NotifySkill's own manual global mute (S1.3, distinct from S5.3's
per-category mute) alongside quiet hours, because both produce the identical symptom Section 6
below closes: `NotifySkill.send()` silently queuing instead of delivering.

## 2. Per-initiative delivery policy (not permanently tied to quiet hours)

New `initiatives.delivery_policy` column (additive migration via `PRAGMA table_info` + `ALTER
TABLE`, mirroring `memory_store.py`'s existing pattern — no data loss, existing rows default to
`"standard"`). `VALID_DELIVERY_POLICIES = {"standard", "immediate", "silent",
"critical_override"}`, settable at `propose()` time, default `"standard"`.

| policy | suppression-policy gate | consent/category-mute gate | NotifySkill call |
|---|---|---|---|
| `standard` (default) | fully subject — defers, reason = active policy name | fully subject | normal |
| `immediate` | bypassed | fully subject | forced `priority="urgent"` |
| `silent` | delivers anyway during an active suppression window | fully subject | `sound=False` |
| `critical_override` | bypassed | fully subject | forced `priority="urgent"` |

**Invariant, unchanged from S5.3:** no `delivery_policy` value bypasses consent or category mute.
Those gate *whether* Bartholomew may contact the user about a category at all; `delivery_policy`
only governs *when/how* once that's already established. Widening that boundary is a separate
governance decision, not something this stage does implicitly.

## 3. Priority-override support without redesign

`immediate` and `critical_override` both map to NotifySkill's own `NotificationPriority.URGENT`
when calling `notify.send` — not just skipping the drive-level check. NotifySkill has its own
independent quiet-hours/mute gate (S1.3); without forcing `urgent`, an "immediate" initiative would
still get silently queued one layer down, reproducing the exact inconsistency this stage exists to
close. The two values stay distinct (rather than merging into one) so a later distinction — e.g.
`critical_override` alone eventually warranting escalation/repeat-until-acknowledged behavior —
doesn't require touching this plumbing again.

## 4. Complete audit trail

No schema change (`initiative_audit.detail` is already free-text). It previously stored only the
bare new-status string; `defer`/`deliver` now write a small JSON object instead (every other
transition — `resolve`/`expire`/`cancel`/`supersede` — keeps writing the bare string, unchanged):

- `defer`: `{"status": "deferred", "reason": "<policy name>"}`
- `deliver`: `{"status": "delivered", "coalesced": bool, "batch_id": str|None, "batch_size": int,
  "eligible_at": <iso ts>}`

`eligible_at` covers "when it became eligible again" — this drive delivers the instant it detects
eligibility, so this is the same instant as the transition's own `ts`, recorded explicitly rather
than left implicit. `list_audit()` parses `detail` as JSON, falling back to `{"status": <raw
string>}` for rows written before this change (and for every non-defer/deliver transition) — no
backfill, existing history stays readable as-is.

## 5. Coalescing → intelligent summarization

Within one drive tick, every `standard`-policy initiative that reaches "deliver" (suppression
clear) is collected rather than notified immediately. `immediate`/`critical_override`/`silent` are
never batched — they go out individually and instantly by construction. If the batch has exactly
one member, behavior is identical to pre-S5.4 (single per-item notify). If more than one:

- Each is advanced through `deliver` with `suppress_notification=True` (new seam param — the
  per-item auto-notify is skipped, state + audit still commit normally, mark-then-notify order).
- One `summarize_batch(initiatives) -> (title, message)` call produces a single combined
  notification — grouped by category/kind (e.g. "3 updates: 2 reminders, 1 maintenance" + a
  categorized bullet body), not a flat concatenation of every rationale in arrival order.
- `summarize_batch()` is an isolated, swappable function. This stage's implementation is
  deterministic grouping (no model call) — a smarter/LLM-based summarizer can replace its body
  later without touching the drive's control flow. Not wired now: it would route through the same
  `ReflectionGenerator`-ownership gap `ROADMAP.md` already flags as blocking live `review`-category
  work, and pulling that in is out of scope here.

## 6. Integration with the existing pipeline / the queued-notification inconsistency

Before this stage, `deliver()` wrote `status="delivered"` before `_deliver_initiative_notification`
called `NotifySkill.send()`, which could itself silently queue (its own quiet-hours/mute check)
rather than truly notify — so `status="delivered"` could lie. Section 1's suppression-policy gate
closes this: `deliver()` (for `standard`-policy initiatives) is never invoked while a suppression
policy is active, because the drive defers first. Folding in `notify_muted` (not just `quiet_hours`)
closes the remaining known instance of the same inconsistency. `NotifySkill` itself is unchanged.

## 7. How deferred initiatives are stored

No new table. Reuses S5.1's `initiatives.status = "deferred"` + `deferred_reason` (free-text,
already used for `"muted"` in S5.3); this stage adds `"quiet_hours"` and `"notify_muted"` as further
conventional values. Re-entry is already free: `deliver()`'s pre-states already include `deferred`.

## 8. Expiry behaviour

No new code. S5.2's `initiative_sweep` drive already scans every non-terminal status (including
`deferred`) for `expires_at` passed, independent of `initiative_delivery_check`. A
suppression-deferred initiative whose `expires_at` passes mid-window expires on its own schedule
exactly like a mute-deferred one already does.

## 9. Edge cases

- **Consent revoked while deferred for a suppression reason:** no special-case needed — the
  consent gate runs unconditionally every tick regardless of current `deferred_reason`.
- **Initiative cancelled independently:** `cancel` is already valid from any non-terminal state;
  once cancelled it drops out of `list_due_for_delivery()`'s status filter.
- **Restart during a suppression window:** all relevant state (initiative status, NotifySkill
  settings) is persisted in SQLite; the drive carries no in-memory state across ticks.
- **Duplicate notifications:** mitigated by mark-then-notify ordering in the coalesced path — all N
  initiatives commit to `delivered` first, the one coalesced notify call happens after. Worst case
  on a crash/failure between those two steps is a `delivered`-but-unseen initiative (an
  already-accepted best-effort risk class per `_deliver_initiative_notification`'s existing
  docstring), never a double-send.
- **Scheduler restart:** same reasoning as above; each tick's batch is fully self-contained.

## 10. Seam change

`run_initiative_through_runtime_contract(ctx, "deliver", ..., suppress_notification: bool = False,
notify_overrides: dict | None = None, coalesced: bool = False, batch_id: str | None = None,
batch_size: int = 1)`. The seam stays policy-agnostic — it optionally skips the auto-notify and
forwards override params (`sound`, `priority`) and audit metadata verbatim. All delivery-policy
*meaning* lives in the drive, not the seam or the store, so adding a fifth policy value later
touches only the drive's dispatch table. `propose` gains `delivery_policy: str = "standard"`,
forwarded to `store.propose()`.

## 11. Test strategy

- `tests/test_notification_suppression.py` (new): registry order, per-tick cache reuse (one skill
  call for N checks), fail-open with no `skill_registry`.
- `tests/test_initiative_store.py`: `delivery_policy` validation at `propose()`, schema migration
  idempotency, `list_audit()` JSON parsing + legacy-string fallback.
- `tests/test_runtime_contract_initiative.py`: `suppress_notification`/`notify_overrides` wiring,
  `delivery_policy` forwarded from `propose`.
- `tests/test_initiative_delivery_check_drive.py`: all four delivery policies; quiet-hours defer;
  notify-muted defer; multiple simultaneously-eligible standard initiatives → exactly one coalesced
  `notify.send` call + each individually reaches `delivered`; single eligible initiative →
  unchanged per-item notify; consent revoked while deferred-for-suppression → still cancels.

## 12. API / data model changes

- **Data model:** one additive column (`initiatives.delivery_policy`); no other schema change.
- **API:** none required — quiet hours are already configurable via S1.3's
  `/api/notifications/quiet-hours` and `/api/notifications/settings`. No `propose` HTTP route
  exists yet (S5.7), so `delivery_policy` is Python-API-only for now.
- **Internal seam signature:** `suppress_notification`, `notify_overrides`, `coalesced`,
  `batch_id`, `batch_size` (all optional, `deliver`-only) and `delivery_policy` (optional,
  `propose`-only) added to `run_initiative_through_runtime_contract()`.
