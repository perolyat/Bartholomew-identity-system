# S5.3 Design — Default-Off Consent and Functional Mute

> **Authority note:** this document is subordinate to `ROADMAP.md` (Stage 5's locked
> safety-scaffolding sequence), `docs/S5_1_INITIATIVE_ENGINE_ARCHITECTURE_DESIGN.md` and
> `docs/S5_2_TYPED_CADENCE_DESIGN.md` (S5.1/S5.2, both approved and implemented 2026-08-06), whose
> chassis this builds directly on top of, and `CONSTITUTION.md`'s Automation Philosophy, Sovereign
> Principle, and "Adaptive notifications without notification fatigue" invariant.
>
> **Status:** proposed 2026-08-06, not yet approved, not yet implemented. Plan only, per explicit
> instruction — no code changes until approved.
>
> **Scope of this pass:** default-off per-category consent (grant/revoke), functional per-category
> mute, and the new delivery-eligibility-check drive that actually enforces both by deciding
> whether an `approved`/`deferred`/`snoozed` initiative proceeds to `deliver`. **Quiet-hours
> (S5.4), dry-run (S5.5), rationale-logging presentation (S5.6), and any concrete proposing drive
> (S5.7) are out of scope** — the delivery-check drive built here is designed with a clear
> extension point for S5.4 to add quiet-hours to the same mechanism, not to duplicate it.

## 1. What this closes

`ROADMAP.md`'s locked sequence names this stage next, and is specific about why: *"default-OFF
consent and working mute are prerequisites for live delivery, not later enhancements."* Two
things already exist from S5.1 that this stage must build on, not redo:

- **Schema already reserved**: `initiative_store.py`'s `initiative_consent` table
  (`category TEXT PRIMARY KEY, allowed INTEGER NOT NULL DEFAULT 0, muted INTEGER NOT NULL DEFAULT
  0, updated_at TEXT NOT NULL, actor TEXT`) and `InitiativeStore.is_category_consented()` already
  exist and are already the live Governance gate 3 inside `run_initiative_through_runtime_
  contract()`'s `propose` transition. **No schema change is needed for consent.**
- **`muted` is reserved but not yet read anywhere.** The column exists; nothing checks it. This
  stage is what wires it in.
- **S5.2 §11 item 5 named this stage's other job directly**: *"Should the future delivery-timing
  check (S5.3/S5.4) be its own dedicated `REGISTRY` drive with its own cadence, or folded into
  `initiative_sweep`? Recommend: its own drive."* Nothing has built that drive yet — `approved`
  initiatives currently have no path to `delivered` at all in production. This stage builds it.

## 2. A critical existing-architecture fact this plan must reconcile: `NotifySkill` already has a working, global mute/quiet-hours mechanism

Before designing anything new, the repository already has a **complete, functional, per-user
(not per-category) mute + quiet-hours system**: `bartholomew/skills/notify.py`'s `NotifySkill`,
built in Stage 1 S1.3, with a persisted `notification_settings` singleton row
(`quiet_hours_start`/`end`, `muted`, `muted_until`), `mute`/`unmute`/`set_quiet_hours` actions,
`_is_muted()`/`_is_quiet_hours()` checks, and `routes/notifications.py` exposing all of it over
HTTP (`GET /api/notifications/settings`, `PUT .../quiet-hours`, `POST .../mute`, `POST
.../unmute`). Crucially, `_action_send()` (the `deliver` transition's own delivery path — every
Initiative delivery already calls `NotifySkill.execute("send", ...)`) **already** checks
`self._is_muted() or self._is_quiet_hours()` and queues instead of sending when either is active.

**This means a blunt, global "silence everything" mute already exists and already applies to
Initiative delivery today**, entirely independent of anything this stage builds. What's missing
is the **per-category** layer `initiative_consent` reserves — a user should be able to consent to
`maintenance`-category initiatives while never consenting to `wellness`, or mute `check_in`
specifically without going globally silent. These are two independent, complementary gates, not
competing implementations of the same feature:

| | `NotifySkill`'s mute (S1.3, existing) | `initiative_consent`'s mute (this stage) |
|---|---|---|
| Scope | Every notification, from every surface | Initiatives of one category only |
| Granularity | Global on/off (+ optional expiry) | Per-category on/off |
| Where it's checked | Inside `NotifySkill._action_send()`, at actual send time | Before `deliver` is ever called, at the Initiative layer |
| Governs | The transport (does anything get sent right now) | The Initiative's own lifecycle (does this initiative even reach delivery) |

Both stay in force. An `approved` initiative in a *consented, unmuted* category can still end up
silently queued inside `NotifySkill` if the user has *globally* muted everything — that is
correct, expected, existing behaviour, not a gap this stage introduces.

**One latent inconsistency, found while reviewing, flagged not fixed here:** `deliver()` (S5.1)
marks an initiative's status `delivered` unconditionally once `NotifySkill.execute("send", ...)`
returns successfully — but a globally-muted/quiet-hours call to `send` *itself* returns success
while only *queuing* the underlying notification (`NotifySkill._action_queue()`'s own success
path). An initiative can therefore read `status: delivered` while the actual notification is
still sitting in `NotifySkill`'s own pending queue. This predates this stage (it's a direct
consequence of S5.1's `deliver()` design) and is not fixed here — flagged in §8 as an open
question, since the right fix (checking `NotifySkill`'s returned data for whether it queued vs.
sent, and only marking `delivered` on an actual send) touches the `deliver` transition itself,
arguably S5.4's concern once quiet-hours is in the same picture, not decided unilaterally now.

## 3. Consent and mute: two related but distinct writes

Per `initiative_consent`'s existing schema, each category independently has:
- **`allowed`** (default `0` — **default-off consent**, gate 3 of `propose`'s Governance check).
  Only a category with `allowed=1` can ever have a new initiative approved.
- **`muted`** (default `0` — **functional mute**, this stage's new delivery-time gate). A category
  can be `allowed=1, muted=1` — consent granted in principle, but temporarily silenced — the same
  shape `NotifySkill`'s own mute already models at the global level, just per-category here.

These are independent bits, not one three-state field, matching `initiative_consent`'s existing
two-column shape exactly (no schema change).

**New `InitiativeStore` methods** (mirrors `GovernanceStore.engage()`/`disengage()`'s shape —
direct, audited writes, not a seam transition; see §5 for why):

```python
def set_category_consent(
    self, category: str, *, allowed: bool | None = None, muted: bool | None = None,
    actor: str | None = None,
) -> None:
    """Upsert initiative_consent for `category`. Only the fields explicitly
    passed change; the other stays at its current (or default-off) value.
    A category with no prior row is created with allowed=0/muted=0 as its
    baseline, then the requested field(s) applied on top -- consistent
    with is_category_consented()'s existing "no row = not consented"
    default."""

def get_category_consent(self, category: str) -> dict:
    """Returns {"category", "allowed", "muted", "updated_at", "actor"} --
    the row if one exists, or the default-off baseline if not."""

def list_category_consent(self) -> list[dict]:
    """Every registered VALID_CATEGORIES row, defaulted for any category
    with no row yet -- the whole picture in one call, for a future
    settings UI (not built here, Stage-1-adjacent follow-on, same
    deferral S5.1 §14 item 2 already recorded for the Initiative queue
    UI)."""
```

`is_category_consented()` (S5.1, existing) is unchanged. A new `is_category_muted(category) ->
bool` is added alongside it, same "no row = default" shape (default `False` — a category with no
mute preference set is not muted, distinct from consent's default-off).

## 4. The delivery-eligibility-check drive

New drive, `initiative_delivery_check` (name chosen to leave room for S5.4 to extend it with
quiet-hours without implying it's mute-only), mirroring `initiative_sweep`'s exact shape (S5.2 §6):

```python
async def drive_initiative_delivery_check(ctx: Any) -> Nudge | None:
    store = getattr(ctx, "initiative_store", None)
    if store is None:
        return None

    from bartholomew.kernel.runtime_contract import run_initiative_through_runtime_contract

    now_ts = int(time.time())
    executor = getattr(ctx, "blocking_executor", None)
    due = await run_off_loop(store.list_due_for_delivery, now_ts, executor=executor)

    for initiative in due:
        try:
            if not store.is_category_consented(initiative.category):
                # Consent was revoked after approval -- stronger signal
                # than a temporary mute (see Sec 8 open question 1);
                # cancels rather than defers.
                await run_initiative_through_runtime_contract(
                    ctx, "cancel", initiative_id=initiative.id,
                    actor="scheduler:initiative_delivery_check",
                )
            elif store.is_category_muted(initiative.category):
                await run_initiative_through_runtime_contract(
                    ctx, "defer", initiative_id=initiative.id, reason="muted",
                    actor="scheduler:initiative_delivery_check",
                )
            else:
                await run_initiative_through_runtime_contract(
                    ctx, "deliver", initiative_id=initiative.id,
                    actor="scheduler:initiative_delivery_check",
                )
        except Exception as e:
            print(f"[Scheduler] Error checking delivery for initiative {initiative.id}: {e}")

    return None
```

**`REGISTRY` entry**: `"initiative_delivery_check": {"fn": drive_initiative_delivery_check,
"cadence": "every:900"}` — same interval as `initiative_sweep`/`awaiting_response_check`, no new
precedent invented.

**New store method, `list_due_for_delivery(now_ts)`**: every initiative in `approved`,
`deferred`, or `snoozed` status whose `due_at` has passed (or is `NULL`, treated as immediately
due — mirrors `awaiting_response_store.list_due_for_transition()`'s own null-handling posture of
"nothing to wait on means due now"). This is the query that makes `deferred`/`snoozed`
initiatives re-enter delivery once conditions change, closing the loop S5.1 §5 described
("deferred → approved" / "snoozed → approved" re-entry) without a dedicated re-entry transition —
exactly as S5.1 anticipated: *"`deliver()` simply accepts any of `approved`/`deferred`/`snoozed`
as its pre-state, so a future delivery-timing check... can call `deliver` directly once
conditions are met."*

**Governance-exemption question this drive itself raises (parallel to S5.2 §7's `expire` case,
not yet decided — see §8 open question 3):** should `drive_initiative_delivery_check`'s own tick
join `_SELF_MAINTENANCE_DRIVES`? It never *proposes* new outbound contact (only decides whether an
*already-approved* initiative proceeds), the same reasoning that put `initiative_sweep` there —
but unlike `expire`, its `deliver` branch is exactly the moment outbound contact *happens*. Leaning
toward: the drive's own tick is self-maintenance-exempt (deciding *whether to check* is not
outbound contact), but the `deliver` transition it dispatches is **not** added to
`_SELF_MAINTENANCE_INITIATIVE_TRANSITIONS` — it stays fully gated, consistent with every other
non-`expire` transition. This mirrors the existing split exactly: drive-tick exemption is a
different question from transition-level exemption, and only `expire` has ever earned the latter.

## 5. Why consent/mute writes are direct store calls, not a new Runtime Contract transition

`GovernanceStore.engage()`/`disengage()` (Parking Brake) are the closest and only precedent for
"a user-facing control-plane write about the *system's* governed behaviour," and they are **not**
routed through any Runtime Contract seam — they're direct, audited (`governance_audit`, atomic
state+audit write), Governance-owned mutations. Category consent/mute is the same shape: it is not
an Initiative's own lifecycle transition (it doesn't belong to any single `initiative_id`, has no
Observation/Interpretation/CandidateAction of its own, and predates any initiative in that
category ever existing). Routing it through `run_initiative_through_runtime_contract()` would
force an artificial `initiative_id`-shaped API onto a category-scoped control. `set_category_
consent()` is therefore a direct `InitiativeStore` method, called directly by the new API route
(§6), exactly mirroring how `routes/governance.py` calls `GovernanceStore.engage()`/`disengage()`
directly rather than through a seam.

## 6. API surface

**Not `/api/consent`** — already taken by S1.2's `pending_sensitive_writes` memory-consent inbox
(`routes/consent.py`), a completely different concept (human review of a specific queued memory
write, not a standing per-category preference). New route file, `routes/initiative_settings.py`,
mounted at `/api/initiatives/consent`, mirroring `routes/notifications.py`'s thin-wrapper shape
(direct store calls here, since §5 established this isn't a skill action):

```
GET  /api/initiatives/consent                  -> list_category_consent()
GET  /api/initiatives/consent/{category}        -> get_category_consent(category)
PUT  /api/initiatives/consent/{category}        -> set_category_consent(category, allowed=..., muted=...)
```

Request body for `PUT`: `{"allowed": bool | null, "muted": bool | null}` — either field omitted
(`null`) leaves that field unchanged (matches `set_category_consent()`'s own partial-update
shape). `category` path param validated against `VALID_CATEGORIES` (400 on an unknown category,
matching every other route's existing validation posture). No bulk "grant all" endpoint —
deliberate: default-off is meant to be a deliberate, per-category act, not something a single
click defeats (§7 invariant).

## 7. Non-negotiable invariants

- **Consent stays default-off.** A category with no `initiative_consent` row is never treated as
  consented; `is_category_consented()`'s existing behaviour is unchanged, not loosened.
- **The delivery-check drive may only call `deliver`, `defer`, or `cancel` — never `propose`.**
  Extends S5.2 §9/§10's eligibility/delivery-separation principle to this new trigger: `due_at`
  passing (plus consent/mute state) decides *whether an already-`approved` initiative proceeds*,
  never whether a new one gets proposed.
- **No bulk consent grant.** Every `PUT` targets exactly one category.
- **`muted` and revoked `allowed` are handled differently** (defer vs. cancel — §8 open question
  1), and that distinction is a deliberate design choice here, not an oversight to reconcile later.

## 8. Open design questions for approval time

1. **Muted → `defer`, revoked consent → `cancel` — confirm this asymmetry?** *Recommend: yes* — a
   mute is inherently framed as temporary/re-checkable (same as `NotifySkill`'s own `muted_until`
   framing); revoking a category's consent is a stronger, more deliberate signal that the
   initiative shouldn't happen at all, matching `cancel`'s own S5.1 semantics ("the condition that
   motivated it no longer holds"). Alternative: treat both as `defer` and let them quietly expire
   — rejected as recommendation, since a revoked-consent initiative sitting in `deferred` still
   *looks* pending/actionable in any future queue UI, which is misleading.
2. **The `deliver()`-marks-`delivered`-even-when-`NotifySkill`-only-queued inconsistency (§2)** —
   fix now, or defer to S5.4 (which will need to touch `deliver`'s gating logic anyway for
   quiet-hours)? *Recommend: defer to S5.4* — fixing it here means partially redesigning
   `deliver()`'s Capability stage for a S5.4-shaped problem (distinguishing "genuinely delivered"
   from "queued by the transport layer") before S5.4's own quiet-hours design exists to inform
   what "genuinely delivered" should even mean once quiet-hours is folded in too.
3. **Is `initiative_delivery_check`'s own drive tick self-maintenance-exempt?** (§4) *Recommend:
   yes, drive-tick only* — mirrors `initiative_sweep`'s exemption reasoning exactly; the `deliver`
   transition it dispatches stays fully gated regardless.
4. **`list_due_for_delivery()`'s `NULL due_at` handling** — treat as "due now" (proposed above) or
   require `due_at` to be set for delivery consideration? *Recommend: due now* — `expires_at` is
   already mandatory at `propose` time (S5.1 §14 item 6); `due_at` was left optional in that same
   schema, and a `NULL` reading as "immediately eligible" is the only interpretation consistent
   with `propose` not requiring it.
5. **Should `get_category_consent()`/`list_category_consent()` be exempt from any Governance
   check** (they're pure reads of Governance-adjacent state)? *Recommend: yes, no gate* — mirrors
   `routes/governance.py`'s existing read endpoints (e.g. Parking Brake status), which are
   unauthenticated reads today, consistent with this API bridge's stated auth posture throughout.

## 9. Explicitly deferred / out of scope

- Quiet-hours defer (S5.4) — this stage's drive is built with an explicit extension point (an
  additional `is_within_quiet_hours()`-shaped check alongside `is_category_muted()`) but does not
  implement it.
- Dry-run mode (S5.5), structured rationale-logging presentation (S5.6).
- Any concrete proposing drive (S5.7) — there is still nothing in production that calls `propose`,
  so this stage's own tests must seed synthetic `approved`/`deferred` rows directly, same honest
  scope note as S5.2's `initiative_sweep` tests.
- A consent-management UI (Stage-1-adjacent follow-on, per S5.1 §14 item 2's same deferral for the
  Initiative queue view).
- The `deliver()`/`NotifySkill`-queuing inconsistency (§2, §8 item 2) — flagged, not fixed here.
- Reconciling or unifying `NotifySkill`'s global mute with `initiative_consent`'s per-category
  mute — both stay independent, as designed (§2); no unification is proposed.

## 10. Verify plan

- `tests/test_initiative_store.py` additions: `set_category_consent()`/`get_category_consent()`/
  `list_category_consent()` (partial updates, default-off baseline, unknown-category handling),
  `is_category_muted()`, `list_due_for_delivery()` (NULL `due_at` treated as due, terminal
  statuses excluded, wrong-status rows excluded).
- `tests/test_initiative_delivery_check_drive.py` (new, mirrors `test_initiative_sweep_drive.py`):
  seeds synthetic `approved`/`deferred`/`snoozed` rows; asserts consented+unmuted → `delivered`;
  muted → `deferred` with `deferred_reason="muted"`; revoked consent → `cancelled`; per-entry
  failure isolation; not-yet-due rows untouched.
- `tests/test_initiative_settings_api.py` (new, mirrors `test_notifications_api.py`): `GET`/`PUT`
  round-trip, partial updates leave the other field unchanged, unknown category → 400, default-off
  baseline for a never-set category.
- Pinned-values regression addition to `tests/test_scheduler_cadence_regression.py`: the new
  `initiative_delivery_check` REGISTRY entry, same shape as S5.2's own addition for
  `initiative_sweep`.
