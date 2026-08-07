# S5.1 Design — The Initiative Engine Architecture

> **Authority note:** this document is subordinate to `ROADMAP.md` (Stage 5's canonical exit
> criteria and locked safety-scaffolding sequence), `COGNITIVE_RUNTIME.md` (the Runtime Contract
> pipeline and ownership table this design extends), and `CONSTITUTION.md` (the Five Pillars,
> Automation Philosophy, and Safety/Accessibility/Product Invariants this design must satisfy).
> It does not modify any of those documents; approval of this document authorises the specific
> additions to each proposed inline (the ownership-table row, §2; the S5.0–S5.7 sub-staging, §2)
> to actually be made.
>
> **Status:** proposed 2026-08-06, revised twice the same day per reviewer feedback (Initiative
> Dependencies and hierarchical parent/child reservation, §13; then the declarative/informational
> non-negotiable invariants, §16), design approved 2026-08-06, **implementation approved and
> completed 2026-08-06** (alongside S5.2, whose own approval covered this document's
> implementation too — see `docs/S5_2_TYPED_CADENCE_DESIGN.md`'s Status line). See `ROADMAP.md`'s
> Stage 5 section for what was actually built: `bartholomew/kernel/initiative_store.py` and
> `run_initiative_through_runtime_contract()` in `runtime_contract.py`, exactly as designed here.
> S5.7 and every later sub-stage still require their own separate, explicit approval before
> implementation, same as every other Phase B/Stage 1 sub-stage.
>
> **Correction, 2026-08-07 (S5.5 approval):** §7's Execution-stage table row and §15's "Dry-run
> mode's plumbing" bullet originally left ambiguous whether a dry-run `deliver` would still
> perform a real `initiative_store.py` write (skipping only the `NotifySkill` call) or skip the
> store write too. `docs/S5_5_DRY_RUN_MODE_DESIGN.md` (approved 2026-08-07) resolves this
> explicitly: **a dry run never writes to `initiatives`/`initiative_audit` at all** — both
> passages below are corrected accordingly. This is a resolution of a question both passages
> already flagged as deferred to S5.5, not a reversal of anything S5.1 firmly decided.
>
> **Scope of this pass:** architecture only — the generic `Initiative` object, its lifecycle, its
> Runtime Contract seam, and the governance/audit/memory contracts every future proactive
> behaviour will be built against. **No scheduling policy, no consent UI, no quiet-hours logic, no
> dry-run plumbing, and no concrete drive (check-in, weekly review, next-best-action, wellness,
> maintenance) is implemented or designed here.** Those are separate, later, separately-approved
> passes built *on top of* this chassis — Typed Cadence (proposed next) is the first of them. This
> mirrors how S1.4 and each Phase B stage were each brought to their own approval individually
> rather than as one undifferentiated block.

## 1. What this closes

`ROADMAP.md`'s Stage 5 section names six safety-scaffolding steps in a locked order — typed
cadence, default-off consent + functional mute, quiet-hours defer, dry-run, structured rationale
logging, then live drives under a new `allow_proactive` governance category — but assumes each
step is implemented as its own feature-specific mechanism. Building Typed Cadence first, without
first defining what a proactive suggestion *is* architecturally, risks repeating S1.4's own
lesson in reverse: S1.4 built one governed store because a scan-and-notify shape already existed
twice (`nudges`, `awaiting_response`) and a third bespoke implementation would have fragmented the
concept. Stage 5 has *six* future behaviours named in `ROADMAP.md` Stage 6 alone (check-ins,
weekly review, next-best-action, wellness, maintenance suggestions, adaptive notifications) that
all share the same shape: **something Bartholomew wants to bring to the user's attention without
being asked.** This document names that shape once — an `Initiative` — so every future drive
proposes one, rather than each drive inventing its own scheduling, governance-gating, audit, and
delivery logic independently.

This also closes a real gap in `COGNITIVE_RUNTIME.md`'s ownership table: `grep -ri initiative`
across the repository (research pass, 2026-08-06) found the word used only as Stage 5's product
label ("the initiative engine") and once in Principle Zero's prose ("every internally generated
initiative must traverse the same cognitive loop") — never as a named architectural concept with
an owner, a dataclass, or a schema. This document is the first to propose one.

## 2. Proposed ownership

Checked against `COGNITIVE_RUNTIME.md`'s ownership table and `CONSTITUTION.md`'s Five Pillars,
following the same elimination S1.4's design used:

- Not **Memory** — an Initiative is a planning artifact (a proposal to act), not memory content.
- Not **Governance** — an Initiative is *subject to* governance, exactly like a `CandidateAction`
  from any other surface; it is not itself a governance mechanism.
- Not **Experience** — Working Memory is a short-term conversational buffer; an Initiative has its
  own multi-day lifecycle (propose → defer → deliver → resolve/expire) that outlives any single
  conversational turn.
- Not **Capability** — Capabilities (skills) *execute* things; an Initiative is a suggestion that a
  capability might later be invoked, not the invocation itself.
- Not a sixth Pillar. `CONSTITUTION.md` requires "extremely strong justification" to add one, and
  none exists here: an Initiative is squarely a planning concept, which `CONSTITUTION.md`'s
  Executive pillar already owns ("decides... does not observe... does not remember").

**Proposed: the Kernel Executive owns Initiatives**, the same row `awaiting_response` was assigned
to (`daemon.py` / `planner.py` / `scheduler/*`), for the same reason — "an obligation is a
planning concept: something the Executive must keep track of and eventually act on." An Initiative
is the general case of which an obligation (`awaiting_response`) is one specific, already-shipped
instance (see §11 for how the two relate and why `awaiting_response` is *not* being rewritten to
use this new machinery).

New module: `bartholomew/kernel/initiative_store.py` — sibling to `awaiting_response_store.py`,
`governance_store.py`, and `scheduler/persistence.py`. Same shape: isolated class, `ensure_schema()`,
tested standalone before any Runtime Contract wiring.

**Addition to `COGNITIVE_RUNTIME.md`'s ownership table** (this design is approved; the table itself
is updated in the same change as this approval):

| Concept | Authoritative owner | Implementations |
|---|---|---|
| Initiative | Kernel Executive | `initiative_store.py`, `run_initiative_through_runtime_contract()` |

**Stage 5 sub-staging** (adopted alongside this approval, mirroring Stage 1's S1.0–S1.6 and Phase
B's B0–B9; `ROADMAP.md` is updated in the same change): Stage 5 was one undifferentiated block
beyond the already-landed S5.0 prerequisite. This document formalises the six locked-sequence
steps as their own gates:

| Stage | Objective | Status |
|---|---|---|
| **S5.0** | Scheduler-schema readiness (closes issue #24) | ✅ done 2026-07-25, PR #25 |
| **S5.1** | Initiative Engine architecture (this document) | ✅ design approved 2026-08-06 (architecture only — implementation not yet approved) |
| **S5.2** | Typed cadence | not started — proposed next |
| **S5.3** | Default-off consent + functional mute | not started |
| **S5.4** | Quiet-hours defer | not started |
| **S5.5** | Dry-run mode | not started |
| **S5.6** | Structured rationale logging | not started |
| **S5.7** | Live check-in / weekly-review / next-best-action drives under `allow_proactive` | not started |

Each still requires its own separate, explicit approval before work begins, per this project's
standing invariant that approving one sub-stage never implicitly approves the next.

## 3. Prerequisite check: is Stage 5 unblocked?

`COGNITIVE_RUNTIME.md`'s Exit Gate table (all seven questions) is confirmed "yes" as of item
11.22 (2026-07-24) — the pipeline exists for every live surface today, and Stage 1's user-facing
governance shell (parking brake, consent inbox, mute/notification controls, awaiting-response
queue) shipped 2026-08-06 (PR #38). Both named prerequisites for *beginning* Stage 5 are satisfied.

One prerequisite remains genuinely open and is **not** closed by this document: the
reflection-ownership gap (`daemon.py`'s daily/weekly reflection generation string-concatenates
`ReflectionGenerator` and `narrator.py` output rather than treating either as authoritative).
`COGNITIVE_RUNTIME.md` states plainly that "live proactive reflection behaviour... remains blocked
until this gap is closed by a separately authorised code change." **This document's architecture
is not blocked by that gap** — nothing here requires reading `ReflectionGenerator`/`narrator.py`
output. But it means one specific *future* Initiative category — a `review` kind that
summarises daily/weekly reflections as a proactive prompt — cannot be implemented until that gap
closes separately. §15 records this as an explicit scope boundary, not a silent omission.

## 4. The generic `Initiative` object

The core requirement this document exists to satisfy: one object, one store, one seam — not a
scheduler per feature. Every future proactive behaviour (check-in, reminder, review,
next-best-action, maintenance suggestion, wellness nudge) is an `Initiative` with a different
`kind`/`category`, not a different code path.

```python
@dataclass(frozen=True)
class Initiative:
    id: int | None
    kind: str            # namespaced, e.g. "checkin.morning", "review.weekly" -- a registered
                          # identifier, not free text (mirrors REGISTRY task_id discipline)
    category: str         # one of a fixed, registered taxonomy -- see ProactiveIntent below
    status: str            # see state machine, section 5
    priority: str          # "low" | "normal" | "high"
    confidence: float      # 0.0-1.0, drive-supplied
    rationale: str         # required, non-empty, structured "why now" (Stage 5 exit criterion)
    payload: dict          # kind-specific data, opaque to the Initiative Engine itself
    origin_drive: str      # the REGISTRY task_id that proposed this
    parent_initiative_id: int | None  # reserved for hierarchical initiatives -- see section 13.
                                       # Always None in S5.1; nothing reads or enforces it yet.
    created_at: str        # ISO8601
    due_at: str | None     # earliest eligible delivery time
    expires_at: str | None # hard TTL -- past this, the initiative is abandoned, not delivered late
    governance_decision: str | None   # "allowed" | "denied"
    governance_reason: str | None
    deferred_reason: str | None       # "quiet_hours" | "muted" -- see section 7
    delivered_at: str | None
    resolved_at: str | None
    resolution: str | None            # "accepted" | "dismissed" | "snoozed" | "expired"
                                       # | "cancelled" | "superseded"
    actor: str | None
```

**Proposed schema** (`initiative_store.py`'s `ensure_schema()`), mirroring
`awaiting_response_store.py`'s precedent (schema + a paired append-only audit table, atomic
state+audit writes):

```sql
CREATE TABLE IF NOT EXISTS initiatives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    priority TEXT NOT NULL DEFAULT 'normal',
    confidence REAL NOT NULL,
    rationale TEXT NOT NULL,
    payload TEXT,                 -- JSON, kind-specific, opaque to this store
    origin_drive TEXT NOT NULL,
    parent_initiative_id INTEGER REFERENCES initiatives(id),  -- reserved, see section 13;
                                                                -- NULL and unread in S5.1
    created_at TEXT NOT NULL,
    due_at TEXT,
    expires_at TEXT,
    governance_decision TEXT,
    governance_reason TEXT,
    deferred_reason TEXT,
    delivered_at TEXT,
    resolved_at TEXT,
    resolution TEXT,
    actor TEXT
);
CREATE INDEX IF NOT EXISTS idx_initiatives_status ON initiatives(status, due_at);
CREATE INDEX IF NOT EXISTS idx_initiatives_category ON initiatives(category, status);
CREATE INDEX IF NOT EXISTS idx_initiatives_parent ON initiatives(parent_initiative_id);

CREATE TABLE IF NOT EXISTS initiative_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    initiative_id INTEGER NOT NULL REFERENCES initiatives(id),
    ts TEXT NOT NULL,
    transition TEXT NOT NULL,
    actor TEXT,
    detail TEXT
);

-- Reserved, see section 13. Many-to-many prerequisite edges (a DAG, not a tree --
-- hierarchy is parent_initiative_id above). Write-only in S5.1: propose() records
-- rows here if a drive supplies depends_on, but no transition reads or enforces
-- this table yet -- proposing an initiative with unresolved dependencies is not
-- blocked in this pass.
CREATE TABLE IF NOT EXISTS initiative_dependencies (
    initiative_id INTEGER NOT NULL REFERENCES initiatives(id),
    depends_on_id INTEGER NOT NULL REFERENCES initiatives(id),
    PRIMARY KEY (initiative_id, depends_on_id)
);
```

**Why a new table, not `nudges`:** `nudges` (in `MemoryStore.SCHEMA`, extended by S5.0's
`created_ts_s`/`acted_ts_s` columns) is a "fire and log" table with no governance-decision,
consent, expiry, or state-machine columns, already written directly by every self-maintenance
drive. Retrofitting it with an Initiative's full lifecycle risks destabilising `self_check` /
`curiosity_probe` / `reflection_micro` / `fts_optimize`, none of which need any of this. A new
table, isolated exactly as `awaiting_response_store.py` was, is the lower-risk choice and the one
directly precedented twice already (`governance_store.py`, `awaiting_response_store.py`). Flagged
as an explicit decision, not an oversight — an alternative considered and rejected.

## 5. Lifecycle / state machine

```
proposed --(governance denies)--------------------------------> denied [terminal]
proposed --(governance allows)---------------------------------> approved
approved --(quiet hours or category muted at delivery-check)---> deferred
deferred --(delivery window reopens, still before expires_at)--> approved
approved --(delivery capability executes)-----------------------> delivered
delivered --(user accepts)---------------------------------------> accepted [terminal]
delivered --(user dismisses)-------------------------------------> dismissed [terminal]
delivered --(user snoozes)----------------------------------------> snoozed
snoozed --(snooze due_at reached)---------------------------------> approved (re-enters, new due_at)
{proposed*, approved, deferred, delivered} --(expires_at reached)-> expired [terminal]
{any non-terminal} --(explicit cancel)-----------------------------> cancelled [terminal]
{any non-terminal} --(superseded by a newer initiative, same kind)-> superseded [terminal]
```

\* `proposed` is normally resolved to `denied`/`approved` synchronously within the same
`propose` seam call (see §6) — it is not expected to persist in `proposed` status, but the
expiry sweep still covers it defensively in case a caller crashes between the two.

Per-state semantics:
- **proposed** — the Executive has constructed a `CandidateAction`; Governance has not yet run, or
  is running inline as part of the same seam call. Never delivered from this state.
- **denied** — Governance (ParkingBrake, Identity Policy, or category consent — §7) refused this
  initiative. Terminal. Still audited and reflected — a denial is not silently dropped, mirroring
  `run_awaiting_response_through_runtime_contract()`'s treatment of governance denial as a
  first-class, recorded outcome, not an exception.
- **approved** — Governance allowed it. Not yet delivered; awaiting its delivery-timing check
  (`due_at`, quiet hours, mute — resolved by later sub-stages S5.3/S5.4, not decided here).
- **deferred** — approved, but the delivery-timing check found a reason not to deliver *right now*
  (quiet hours in effect, or the category is muted). Re-checked, not abandoned — distinguishes a
  *delay* from a *denial*. See §7 for why mute and quiet-hours both route through `deferred` but
  are semantically different.
- **delivered** — the delivery capability (§6) has executed. From here the initiative is genuinely
  in front of the user; it awaits their response or its own `expires_at`.
- **accepted / dismissed** — explicit user resolution. Terminal.
- **snoozed** — user asked for it again later; re-enters `approved` with an updated `due_at`, not a
  new initiative row (preserves the audit trail and origin).
- **expired** — `expires_at` passed without resolution. Terminal, but distinct from `dismissed`:
  expiry means the user never engaged with it, not that they actively rejected it — a distinction
  future confidence/priority tuning (deferred to individual initiative-kind designs) may care
  about.
- **cancelled** — the *system* withdrew it before the user saw it or acted on it (e.g. the
  originating drive determines the condition that motivated it no longer holds). Distinct from
  `superseded`.
- **superseded** — a newer initiative of the same `kind` explicitly replaces this one (e.g. a
  second morning check-in proposal before the first was resolved). Prevents duplicate proposals
  from silently piling up; the mechanism for detecting "same kind, still open" is
  `initiative_store.py`'s `list_open_by_kind()`, mirroring
  `awaiting_response_store.py`'s `list_open_by_origin()`.

## 6. Executive ownership of initiative creation and management

**Non-negotiable invariant, mirroring `awaiting_response_store.py`'s own docstring verbatim in
spirit:** no route, skill, or drive writes to `initiative_store.py` directly. The sole path is a
new Runtime Contract seam:

```python
async def run_initiative_through_runtime_contract(
    ctx,
    transition: str,   # "propose" | "defer" | "deliver" | "resolve" | "expire" | "cancel" | "supersede"
    *,
    initiative_id: int | None = None,
    kind: str | None = None,
    payload: dict | None = None,
    rationale: str | None = None,
    priority: str = "normal",
    confidence: float | None = None,
    due_at: str | None = None,
    expires_at: str | None = None,
    resolution: str | None = None,
    actor: str | None = None,
    parent_initiative_id: int | None = None,   # reserved, recorded only -- see section 13
    depends_on: list[int] | None = None,       # reserved, recorded only -- see section 13
) -> InitiativeRuntimeResult: ...
```

This mirrors `run_awaiting_response_through_runtime_contract()`'s `transition`-parameterised shape
exactly, for the same reason: one function, one Governance pass, one Reflection, per call —
callers never see `initiative_store.py` at all. `parent_initiative_id` and `depends_on` are
accepted and written by `propose` in this pass (a drive that already knows its parent or
prerequisites today can record that fact without waiting for a future pass) but are not read or
enforced by anything else — no transition blocks, defers, or cascades on them yet.

**Individual drives become thin proposal generators, not schedulers.** This is the concrete
mechanism satisfying "a generic Initiative object rather than feature-specific schedulers": a
future `checkin_morning` drive (S5.7, not designed here) does nothing but decide *whether* a
morning check-in is warranted right now and, if so, call:

```python
await run_initiative_through_runtime_contract(
    ctx, "propose",
    kind="checkin.morning", payload={...}, rationale="...",
    priority="normal", confidence=0.8, due_at=..., expires_at=...,
)
```

All lifecycle, governance-gating, audit, and delivery logic lives exactly once, in the seam and
`initiative_store.py` — not duplicated per drive. Each future drive is registered in
`scheduler/drives.py`'s `REGISTRY` exactly like `awaiting_response_check` is today, with its own
cadence; a new self-maintenance-style sweep drive, `initiative_sweep`, handles the `expire`
transition for anything past `expires_at` (see §10) and is the one piece of Initiative-Engine
plumbing this document *does* fully specify, since it's infrastructure, not a proactive behaviour
itself.

## 7. Proactive intent classification before governance evaluation

Governance's decision depends on knowing *what kind of proactive contact this is* — "is
`check_in` allowed" cannot be evaluated without first knowing this is a `check_in`. This document
therefore inserts a classification step between Interpretation and Governance, populating a
structured `category` (not free text) that both Governance and later delivery-adaptation logic
key off of:

```python
@dataclass(frozen=True)
class ProactiveIntent:
    category: str    # registered taxonomy, see below
    urgency: str      # "low" | "normal" | "high"
    sensitivity: bool # touches a topic that should route through consent-adjacent handling
```

**Registered taxonomy** (fixed set, extensible only by a future design revising this document —
not free text, so Identity Policy and consent can be written against stable keys):
`check_in`, `reminder`, `review`, `next_best_action`, `maintenance`, `wellness`.

Mapped onto the 8-stage Runtime Contract pipeline:

| Stage | For an Initiative |
|---|---|
| **Observation** | `source="scheduler"`, `raw_content` = the originating drive's proposal summary |
| **Interpretation** | Enriched with Experience Kernel state (goals, affect, attention) — check-ins and wellness nudges plausibly want this context. **Classification happens here**: `classify_proactive_intent()` produces the `ProactiveIntent` above, attached alongside the `Interpretation` |
| **Executive** | `CandidateAction(kind=f"initiative_{transition}_{category}", ...)` — the full `Initiative` proposal (kind, payload, rationale, confidence, priority, due_at, expires_at) plus its `ProactiveIntent` |
| **Governance** | Three gates, in order — see §8 |
| **Capability** | Per-transition, not uniform: `propose`/`defer`/`resolve`/`expire`/`cancel`/`supersede` write to `initiative_store.py` only. `deliver` alone invokes the existing `NotifySkill` path via `run_skill_through_runtime_contract(ctx.skill_registry, "notify", "send", {...})`, reused exactly as `awaiting_response`'s `remind`/`escalate` transitions already do — no new delivery channel is introduced here. A single `propose` call never itself reaches `deliver`; see S5.2's `docs/S5_2_TYPED_CADENCE_DESIGN.md` for the fuller treatment of why a cadence tick (or any other proposal trigger) produces eligibility, not delivery. |
| **Execution** | Running the above; in live mode, `deliver` performs a real `initiative_store.py` write and a real `NotifySkill` call. **Corrected 2026-08-07 (S5.5, see `docs/S5_5_DRY_RUN_MODE_DESIGN.md`): under dry-run mode, this stage does not run at all** — neither the store write nor the `NotifySkill` call happens; the entire transition is simulated into a separate, non-authoritative `DryRunResult` instead. This replaces the row's original "no-op write ... gated by a mode flag this document defers" framing, which left open whether the store write itself would still occur under dry-run — S5.5 resolves that it does not. |
| **Reflection** | `ActionReflection(surface="initiative", action=candidate_action.kind, outcome=..., summary=rationale, details={initiative_id, category, priority, confidence, governance_decision})` — written at every *transition* (not on every deferred re-check, to avoid reflection spam; a `deferred` initiative re-checked hourly by the sweep does not get a new reflection each hour, only on its actual state change) |
| **Memory** | Reflections table (as above) + `initiative_audit` (full transition history, mirroring `awaiting_response_audit`) + Working Memory — see §10 |

`kind` is deliberately **not** added to `_SELF_MAINTENANCE_DRIVES` in `runtime_contract.py`, for
the same reason `awaiting_response_*` kinds are excluded: an Initiative represents genuine
potential outbound contact about specific content, not internal bookkeeping — it must be evaluated
by Identity Policy for real, every time, no exemption. (The `initiative_sweep` maintenance drive
itself, which only walks expired rows, *is* self-maintenance-shaped and should be added to that
exemption set — it never proposes new outbound contact, only closes out old rows. This is
analogous to how `fts_optimize` is exempt but `awaiting_response_check`'s actual reminders are
not.)

## 8. Governance hooks and approval flow

Three gates, evaluated in order, inside the `propose` transition:

1. **ParkingBrake — new dedicated scope, `"initiative"`.** Distinct from the existing
   `"scheduler"` scope, which already gates whether the *originating drive* runs at all. This
   gives the user two independent levers: brake general scheduler activity (self-checks, FTS
   maintenance keep running quietly) while separately braking all Bartholomew-initiated
   suggestions, or vice versa. Precedent: `"skills"` and `"scheduler"` are already independent
   scopes despite `awaiting_response_check` triggering via the scheduler — the brake scope tracks
   *what kind of action is being gated*, not *what triggered it*.
2. **Identity Policy — `allow_proactive` category, default-deny.** Evaluated for real (per §7,
   never exempted) against `f"allow_proactive.{category}"`. This is `ROADMAP.md`'s named
   requirement ("a new default-deny `allow_proactive` governance category... excluded from
   `tool_use`, no self-maintenance exemption") — implemented here as a category-scoped policy key
   so an operator can allow `maintenance`-category initiatives without blanket-allowing
   `wellness`. Like S1.4's `tool_use.allowlist` additions, the actual `Identity.yaml` entries are
   an implementation-time dependency, not something this document adds — flagged here so it isn't
   rediscovered as a surprise gap the way S1.4's was.
3. **Per-category user consent — new table, `initiative_consent`.** Distinct from Identity Policy:
   gate 2 is the operator/developer-level kill switch (like `tool_use.allowlist`); this gate is the
   **end-user-level, default-off opt-in** `ROADMAP.md` requires ("default-OFF consent... a
   prerequisite for live delivery, not a later enhancement"). One row per registered category:

   ```sql
   CREATE TABLE IF NOT EXISTS initiative_consent (
       category TEXT PRIMARY KEY,
       allowed INTEGER NOT NULL DEFAULT 0,   -- default-off, per ROADMAP.md's Stage 5 sequencing
       muted INTEGER NOT NULL DEFAULT 0,
       updated_at TEXT NOT NULL,
       actor TEXT
   );
   ```

   Mirrors `governance_store.py`'s `parking_brake_state` shape (state + `updated_at` + `actor`)
   but keyed per-category rather than a `CHECK (id=1)` singleton, since it's inherently multi-row.
   Whether this needs `governance_store.py`'s revision-guarded-loosening pattern
   (`StaleGovernanceWriteError`) is an open question for approval (§14) — consent flags are
   lower-stakes than the Parking Brake kill switch, but the same race is theoretically possible.

**`muted` vs. `deferred`-by-quiet-hours — a distinction this document establishes even though
neither mechanism is implemented here (S5.3/S5.4):** an initiative in a **muted** category is
still governed (gates 1 and 2 still apply; it can still be `denied` on those grounds), but at the
delivery-check step a muted category routes straight to `deferred` with
`deferred_reason="muted"` and is *not* proactively re-surfaced — its history stays queryable but
it quietly ages toward `expired`. A **quiet-hours** defer is a *delay*, not a suppression: same
`deferred` status, `deferred_reason="quiet_hours"`, but it is re-checked and delivered once quiet
hours end, provided `expires_at` hasn't passed. Both reuse the same `deferred` state (§5) rather
than inventing two, but `deferred_reason` lets S5.3/S5.4's later implementations tell them apart
without a schema change.

## 9. Audit and rationale requirements

- `rationale` is required and non-empty at `propose` time, enforced at the seam boundary — the
  same shape as S1.4's `_subject_must_be_non_empty` validator, not left to be caught downstream as
  an uncaught error. This directly satisfies `ROADMAP.md`'s Stage 5 exit criterion, "suggestions
  logged with rationale."
- Every **transition** (not every delivery-timing re-check) writes an `initiative_audit` row in
  the same atomic state+audit transaction `governance_store.py` and `awaiting_response_store.py`
  both already use (`BEGIN IMMEDIATE` ... commit, or rollback on any exception) — "the two are
  never observable independently."
- Every transition also produces an `ActionReflection` (§7's table), so an Initiative's full
  history is visible through the same audit/provenance surface Stage 1's S1.5 already built —
  no second, parallel history view is needed.

## 10. Memory interaction

- **Reflections and `initiative_audit`**, as above — this is the Initiative's *own* audit trail
  (did the engine propose/govern/deliver/resolve this suggestion correctly), a different concept
  from `ReflectionGenerator`'s daily/weekly narrative reflections. The reflection-ownership gap
  (§3) does not block this — nothing here writes to or reads from that pipeline. It only becomes
  relevant to a *specific future initiative kind* (`review`) that would summarise narrative
  reflection content as its payload — flagged again in §15, not glossed over.
- **Working Memory**: on `deliver`, this document proposes creating a Working Memory item tagged
  `source="initiative"` (mirroring chat's `source="chat"` tagging), so a delivered proactive
  suggestion becomes visible to the Experience Kernel and subsequent conversational context — the
  same way `run_chat_through_runtime_contract()` already does for chat turns. This satisfies
  Exit Gate question 6 ("does every conversation see the Experience Kernel") for the proactive
  case: Bartholomew speaking proactively is a form of conversation and should not be invisible to
  its own continuity model.
- **Data portability** (`CONSTITUTION.md` §3): `initiatives`, `initiative_audit`, and
  `initiative_consent` are user state ("active goals and unresolved matters," explicitly named in
  that invariant) and must be included whenever the export feature (Stage 6, not yet built) ships.
  Recorded here as a forward dependency, not implemented in this pass.

## 11. Relationship to `awaiting_response` — explicit boundary, not a merge

`awaiting_response` tracks a **reactive** obligation: something already opened by a specific prior
exchange, waiting on a specific reply. An `Initiative` is **proactive**: Bartholomew originating
new contact with no pre-existing obligation triggering it. They look superficially similar
(both scan-and-notify, both governed, both auditable) but the trigger direction is opposite.

**This document does not rewrite `awaiting_response_store.py` to use `Initiative` machinery.**
It is an already-shipped, already-tested S1.4 feature; re-architecting it here would be an
unapproved change to approved, merged functionality — exactly what the user's own standing
instruction on the PR #38 regression fix warned against doing casually. Whether
`awaiting_response`'s `remind`/`escalate` transitions should *emit* an Initiative in some future
integration (e.g. so an escalated obligation also shows up in a unified proactive-suggestions
view) is recorded in §14 as an open question, explicitly not decided here.

## 12. Expiry, cancellation, priority, confidence handling

- **Expiry**: `expires_at` is a hard TTL, drive-supplied at `propose` time (default per category
  deferred to individual future initiative-kind designs, not fixed here). The `initiative_sweep`
  drive (§7) walks `list_expiring(now_ts)` on its own cadence and transitions any non-terminal row
  past `expires_at` to `expired` via the `expire` transition — self-maintenance-shaped, exempted
  from Identity Policy per §7.
- **Cancellation**: the *system* withdraws an initiative before user resolution — e.g. the
  condition that motivated it stopped being true. Explicit `cancel` transition, callable only by
  the originating drive (or an operator/API surface built in a later stage), never silent.
- **Supersession**: a newer initiative of the same `kind` explicitly replaces an older open one —
  prevents duplicate proposals piling up (e.g. two morning check-ins before the first resolves).
  `initiative_store.py` exposes `list_open_by_kind(kind)` for a drive to check before proposing,
  mirroring `awaiting_response_store.py`'s `list_open_by_origin()`.
- **Priority**: a 3-tier enum (`low`/`normal`/`high`), not a numeric scale — deliberately minimal
  for this MVP-architecture pass. Flagged in §14 as revisitable if a concrete need for finer-grained
  ranking emerges once real initiative kinds compete for the user's attention simultaneously.
- **Confidence**: a required `float` in `[0.0, 1.0]`, drive-supplied, validated for range at the
  seam boundary. **This document captures and audits confidence; it does not define any policy
  for how confidence affects delivery, priority, or governance.** That is deliberately deferred to
  individual future initiative-kind designs (e.g. a `next_best_action` drive might auto-downgrade
  low-confidence proposals to `low` priority) — baking a specific confidence policy into the
  generic engine would be exactly the kind of feature-specific logic this document exists to keep
  out of the shared chassis.

## 13. Initiative dependencies and hierarchy (reserved, not implemented in S5.1)

Two relationship shapes the Executive will plausibly need once multiple drives propose
initiatives that interact — a longer-running workflow whose steps are separate initiatives, and
a coordinating initiative that decomposes into sub-initiatives. Both are reserved here at the
schema level, so no future migration is needed to add them and no individual drive is tempted to
invent its own private notion of "wait for X before proposing Y." **Neither is functionally
implemented in this pass** — no state-machine check, no seam-level enforcement, no delivery gate
reads either of them yet. This satisfies the request to add these "if they fit naturally without
complicating the current implementation, otherwise explicitly reserve the extension points" —
recording a relationship is a plain write with no new control flow; *acting* on one is deferred.

**Dependencies** (prerequisite relationships, many-to-many, a DAG, not a tree): the new
`initiative_dependencies` join table (§4). `propose` accepts an optional `depends_on: list[int]`
(§6) and records it; nothing currently reads it. A drive proposing, say, a future `review.weekly`
kind that should only fire after that week's `checkin.morning` instances resolve could record that
dependency today without waiting for a future pass to enforce it — the data survives the gap
between "recorded" and "enforced."

*Future direction (not decided, sketch only):* the natural place to enforce a dependency is the
delivery-timing check already planned for S5.3/S5.4 (§7's `deferred`-vs-`approved` distinction) —
an initiative with an unresolved `depends_on_id` would stay `approved` but never reach `deliver`,
reusing the existing `deferred`-style non-denial semantics rather than adding a new `blocked`
status, likely via a new `deferred_reason="blocked_on_dependency"` value. This is a plausible
shape, not a commitment; a real proposal needs its own design pass once a concrete multi-step
workflow exists to design against, per §14 item 7 below.

**Hierarchy** (parent/child, a tree): `parent_initiative_id` (§4), nullable, self-referencing.
`propose` accepts an optional `parent_initiative_id` (§6) and records it; nothing currently reads
it, computes a rollup status, or cascades resolution/cancellation between parent and children.

*Future direction (not decided, sketch only):* a coordinating "workflow" initiative could
represent a multi-step proactive plan (e.g. a parent spawning separate `checkin.evening` and
`reminder.pack_bag` child initiatives), with the parent's own resolution defined as a rollup of
its children's (e.g. resolved once all children reach a terminal state). Rollup semantics,
cascade-on-cancel behaviour, and whether a parent can be delivered independently of its children
are all real design questions a future pass must answer — reserving the column does not imply an
answer to any of them.

**Why reserve rather than fully build:** neither relationship has a concrete consumer yet — Typed
Cadence (S5.2, proposed next) needs neither one. Building enforcement logic against a hypothetical
workflow risks the exact mistake `CONSTITUTION.md`'s consumer-value gate warns against:
"architectural sophistication alone is not sufficient product value." Reserving the schema costs
nothing now (two nullable columns and one small join table, no new state, no new seam behaviour)
and avoids a breaking migration later; building the coordination logic itself is deferred until a
real multi-step proactive behaviour needs it.

## 14. Open design questions for approval time

Each with a recommendation, per this project's own convention for surfacing undecided points
rather than silently picking one:

1. **Does `initiative_consent` need revision-guarded writes** (like `governance_store.py`'s
   `StaleGovernanceWriteError` protection against loosening on stale data)? *Recommend: no, not
   for this MVP* — consent toggles are user-initiated single-actor changes (unlike the Parking
   Brake, which multiple concurrent callers can race), so the atomic-write-without-revision-guard
   pattern `awaiting_response_store.py` already uses is sufficient. Revisit if a concrete race is
   found.
2. **Where does a `delivered` Initiative surface in the UI** — reuse the existing
   awaiting-response-queue-style pattern (a dedicated queryable list) or fold into the existing
   `nudges` UI surface? *Recommend: a dedicated queue, mirroring S1.4's own UI treatment* — this is
   Stage-1-adjacent follow-on UI work, not decided in this architecture pass, and should get its
   own short design note once a real initiative kind exists to test it against.
3. **Should `initiative_sweep`'s cadence and `REGISTRY` entry be added now or with S5.2 (Typed
   Cadence)?** *Recommend: with Typed Cadence* — this document specifies the sweep's *behaviour*
   (§7, §12) so Typed Cadence's implementation has a clear target, but adding a `REGISTRY` entry
   with no drives yet producing initiatives has nothing to sweep. Implementing it prematurely
   also means it enters CI/production before any drive exercises it.
4. **Numeric priority vs. the 3-tier enum (§12)?** *Recommend: keep the enum for now* — no
   concrete ranking requirement exists yet; expanding a stored enum later is a trivial migration,
   narrowing a numeric scale after drives depend on specific values is not.
5. **Should `awaiting_response` escalations eventually emit Initiatives for a unified view (§11)?**
   *Recommend: not now* — track as a future integration question once both mechanisms have run in
   production for a while and any concrete duplication pain is real, not hypothetical.
6. **Default `expires_at` per category** — a global default, or must every drive supply one
   explicitly? *Recommend: require it explicitly at `propose` time, no silent default* — an
   initiative with no TTL that's forgotten indefinitely is a "message blindness"/trust failure
   waiting to happen (`CONSTITUTION.md` §5); forcing the caller to state one is cheap and keeps
   the failure mode visible in code review rather than in production.
7. **When dependency-blocking is eventually designed, does it reuse `deferred`/`deferred_reason`
   (§13's sketch) or introduce a new `blocked` status?** *Recommend: reuse `deferred_reason`* — it
   keeps the state machine (§5) unchanged for a mechanism not yet needed by any drive, consistent
   with how mute and quiet-hours already share `deferred` for the same reason. Not binding on
   whichever future pass actually designs dependency enforcement — recorded as a starting lean,
   not a decision.

## 15. Explicitly deferred / out of scope for this document

- **Typed Cadence itself** (S5.2, proposed next) — the actual interval/daily/weekly wall-clock
  scheduling model for concrete drives built on this chassis.
- **Default-off consent's UI/API surface** — only `initiative_consent`'s persistence shape (§8) is
  specified; how a user actually views/toggles it is Stage-1-adjacent follow-on work (S5.3).
- **Quiet-hours defer's coalescing/expiry semantics** — this document only establishes that
  `deferred` is a state the machine supports and that `deferred_reason` distinguishes mute from
  quiet-hours (§7); the actual quiet-hours window logic is S5.4.
- **Dry-run mode's plumbing** — §7 (corrected 2026-08-07) now states the resolved behaviour: under
  dry-run, `propose`/`deliver` never write to `initiatives`/`initiative_audit` at all. The mode
  flag, its storage (a new Governance-owned scoped switch, mirroring `parking_brake_state`), and
  the separate `DryRunResult` provenance mechanism are fully specified in
  `docs/S5_5_DRY_RUN_MODE_DESIGN.md` (approved 2026-08-07).
- **Structured rationale logging's presentation** — §9 makes `rationale` mandatory and audited;
  how it's formatted/surfaced for review is S5.6.
- **Every concrete drive** (check-in, weekly review, next-best-action, maintenance suggestion,
  wellness) — S5.7 and beyond, each its own separately-approved design, each simply calling
  `run_initiative_through_runtime_contract(ctx, "propose", ...)` per §6.
- **The reflection-ownership architectural gap** (§3) — not closed here; blocks only the future
  `review` initiative kind specifically, not this architecture.
- **Dependency-blocking and hierarchy-rollup enforcement logic** (§13) — the schema is reserved
  (`initiative_dependencies`, `parent_initiative_id`); no transition reads, blocks, or cascades on
  either yet. A future pass, once a concrete multi-step workflow needs it.
- **`Identity.yaml`'s actual `allow_proactive.*` allowlist entries** (§8) — an implementation-time
  dependency flagged here, added when a concrete category is first implemented, per
  `governance.change_control`, exactly as S1.4's allowlist gap was handled.

## 16. Non-negotiable invariants

Two architectural invariants, established here alongside §6's existing one (no route, skill, or
drive writes to `initiative_store.py` outside the Runtime Contract seam):

1. **An Initiative is declarative, not imperative.** It represents *what* the Executive intends
   (`kind`, `category`, `rationale`, `payload`, per §4) — never *how* it will be carried out.
   Execution strategy stays the Capability layer's responsibility, decided only after Governance
   approval, at `deliver` time (§7) — the same separation every other Runtime Contract surface
   already keeps between its `CandidateAction` (what's proposed) and its Capability (how it's
   done). A drive proposing `checkin.morning` never encodes which notification channel, retry
   policy, or delivery mechanism to use; that decision belongs entirely to whatever Capability
   executes at delivery time, free to change without altering a single `Initiative` row's shape.
   This keeps the generic object stable as delivery mechanisms evolve, and closes a specific
   failure mode: a drive's `payload` smuggling in an implicit "and do it exactly this way"
   instruction would let it dictate execution unilaterally, exactly the kind of unchecked
   Executive-to-Capability shortcut Governance exists to sit between.
2. **Dependencies and hierarchy are informational until promoted by Governance.** Recording a
   `depends_on` edge or a `parent_initiative_id` (§13) is a plain, side-effect-free write — it
   must never itself schedule, defer, block, or execute anything. Any future dependency- or
   hierarchy-enforcement logic (§13's sketch; §14 item 7) must still flow through the same
   Executive-proposes, Governance-evaluates pipeline as every other Initiative transition: a
   recorded dependency may *inform* a future `defer` decision at the delivery-timing check (§7),
   but it can never itself cause a `deliver`, `cancel`, or any other transition to fire without
   passing through Governance again. This forecloses the failure mode dependency graphs
   characteristically invite — "B depends on A" quietly becoming "A's resolution directly
   triggers B" through a side channel — the same class of shortcut `awaiting_response_store.py`'s
   own non-negotiable invariant (§6) was written to prevent for a simpler case.

## 17. Verify plan

None yet — this is architecture only, no code changes. S5.2 (Typed Cadence)'s own design and
implementation pass will include the first concrete tests exercising this chassis:
`initiative_store.py`'s schema and state machine in isolation, then
`run_initiative_through_runtime_contract()`'s Governance/Reflection integration, mirroring exactly
how S1.4's own test plan was structured (`tests/test_awaiting_response_store.py` before
`tests/test_runtime_contract_awaiting_response.py` before the API-layer tests).
