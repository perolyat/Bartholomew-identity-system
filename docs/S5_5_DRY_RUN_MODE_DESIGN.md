# S5.5 Design — Dry-Run Mode

> **Authority note:** subordinate to `ROADMAP.md` (Stage 5's locked safety-scaffolding sequence),
> `COGNITIVE_RUNTIME.md` (the Runtime Contract pipeline this design's interception boundary sits
> inside), `CONSTITUTION.md` (the Five Pillars, Automation Philosophy's "Baby Mode ladder", and
> Sovereign Principle this design exists to serve), and `docs/S5_1_INITIATIVE_ENGINE_ARCHITECTURE_
> DESIGN.md` (S5.1, approved and implemented 2026-08-06), whose `run_initiative_through_runtime_
> contract()` seam this design wires into.
>
> **Status:** approved 2026-08-07 (design direction approved subject to resolving the S5.1
> conflict below, which this document's approval also resolves). Implementation not yet started.
>
> **Supersedes, explicitly:** `docs/S5_1_INITIATIVE_ENGINE_ARCHITECTURE_DESIGN.md` §7's Execution-
> stage table row and §15's "Dry-run mode's plumbing" bullet, both of which left ambiguous whether
> a dry-run `deliver` would still perform a real `initiative_store.py` write (skipping only the
> `NotifySkill` call) or skip the store write too — both explicitly deferred the exact answer to
> this document. **Resolved here: a dry run never writes to `initiatives`/`initiative_audit` at
> all.** See that document's own 2026-08-07 correction note and its corrected §7/§15 passages.
>
> **Scope of this pass:** the dry-run primitive (data model, resolution logic, storage, API shape)
> is designed to generalize to every Runtime Contract surface, per Uniform Cognition. **Actual
> wiring in this pass is scoped to the Initiative Engine seam
> (`run_initiative_through_runtime_contract()`'s `propose`/`deliver` transitions) and the skill
> boundary it calls into (`SkillRegistry.execute_action()`, specifically the `notify` skill).**
> Chat, scheduler-drive, awaiting_response, and voice/sight dry-run wiring are not built here —
> the primitive supports them without redesign, but wiring them in is separate, later,
> separately-approved work, matching S5.2–S5.4's own scope discipline.

## 1. Purpose and behavioural contract

Dry-run mode lets a real action travel through the entire Runtime Contract — Observation,
Interpretation, Executive, and every real Governance gate — and stop at the boundary between
Governance's approval and Capability's actual effect, producing a structured, truthful record of
exactly what would have happened, instead of making it happen.

- Every gate that would run for a live action runs for real, unmodified, in dry-run: ParkingBrake,
  Identity Policy, per-category consent, category mute, the suppression-policy registry (S5.4).
  Dry-run changes nothing about *whether* Governance would allow the action.
- The one thing dry-run changes is whether the final effect happens. A denial is reported
  truthfully — dry-run is not a "what if the brake weren't there" sandbox.
- No real Initiative row, audit row, Working Memory note, skill-level notification, or any other
  ground-truth state is written. The dry run produces its own, separately-typed, separately-stored
  record.
- This is not an `if dry_run: return` flag inside capability code. No capability decides for
  itself whether to honour dry-run — the decision is made once, centrally, before any capability
  is invoked, from declarative metadata the capability author supplied ahead of time. Capability is
  a consumer of that decision, never the gate.

This is the evidence-generation mechanism for `CONSTITUTION.md`'s Automation Philosophy ladder —
*Observe only → Recommend → Assist → Limited automation → Governed automation → Trusted
autonomy* — letting Bartholomew demonstrate "Governance would have said yes, here is exactly what
it would have done" before being granted the next rung, not a developer convenience feature.

## 2. Architectural placement

Per `COGNITIVE_RUNTIME.md`'s stage table, dry-run's boundary sits between Governance and
Capability:

```
Observation -> Interpretation -> Executive -> Governance -> [ DRY-RUN BOUNDARY ] -> Capability -> Execution -> Reflection -> Memory
```

- **Executive**: unaffected — the `CandidateAction` proposed is identical whether or not the call
  is a dry run.
- **Governance**: unaffected — all three gates evaluate the real `CandidateAction` against real
  state. This is what makes dry-run a genuine rehearsal of Governance's actual judgment.
- **Approval system**: unchanged and fully exercised — "ask"-level consent resolution
  (`SkillRegistry._resolve_permissions()`) still runs for real; a dry run that would need consent
  says so, it does not assume approval.
- **Capability/adapter execution**: this is where dry-run acts. Capability is never told to
  pretend — it is simply never invoked for the effect-having part of its job (§5).
- **Audit/provenance**: Reflection and Memory both still fire, into a distinct channel (§8).

Per S5.1 §16's existing invariant ("An Initiative is declarative... execution strategy stays the
Capability layer's responsibility, decided only after Governance approval, at `deliver` time"):
dry-run's boundary is exactly that same seam, extended with one more question after Governance
approves — *do we actually reach Capability, or record what Capability would have done.*

## 3. Maximizing real-pipeline traversal without side effects

Everything before the boundary is unmodified and real: real `Observation`/`Interpretation`/
`CandidateAction` construction; real ParkingBrake read; real Identity Policy evaluation; real
per-category consent/mute lookups; real S5.4 suppression-policy registry evaluation (all reads,
no external effect). For skills specifically, any action a capability author has declared
side-effect-free (§5) actually executes during dry-run — reading `notify.get_notification_
settings` for real during a dry-run `deliver` check increases fidelity at zero risk.

Only the final mutating step — the store write that constitutes "this really happened"
(`InitiativeStore.propose()`/`.deliver()`) or a capability call with effect outside Bartholomew's
own database (`NotifySkill.send()`, and any future `email.send`/`calendar.create_event`/purchase/
browser action) — is replaced with construction of a `DryRunResult`.

## 4. The precise interception boundary

Two concrete boundaries, both already the single production choke-point for their surface:

1. **`run_initiative_through_runtime_contract()`** (`bartholomew/kernel/runtime_contract.py`),
   immediately before the transition-specific store call (the `if transition == "propose": ...
   elif governance_allowed: if transition == "defer": store.defer(...) elif transition ==
   "deliver": store.deliver(...)` block). Governance has already run in full above this point.
2. **`SkillRegistry.execute_action()`** (`bartholomew/kernel/skill_registry.py`), immediately
   before `loaded.instance.execute(action, params or {})`. Every governance check (brake, policy,
   "ask"-consent) has already run in full above this point.

## 5. Centrally, or at capability/adapter boundaries — both, in a non-overlapping split

The go/no-go decision is always centralized, at the two boundaries in §4 — a capability is never
asked "should you actually run." Letting each capability decide would mean N independent judgment
calls instead of one, violating "every architectural responsibility has exactly one authoritative
owner" and reproducing the shallow `if dry_run:` pattern this design exists to avoid.

What varies per capability is a static, declarative classification of its own actions, supplied at
design time, not decided at call time — the same shape existing manifest-declared permissions
(`level: auto/ask/never`) already use. `bartholomew/kernel/skill_manifest.py`'s `SkillAction`
gains one new field, `side_effect: bool = True` (default `True` — unclassified actions are
side-effecting, fail-closed). `execute_action()` reads
`loaded.manifest.get_action(action).side_effect` and only calls `loaded.instance.execute(...)` for
real if `side_effect is False` or the call isn't a dry run. `config/skills/notify.yaml` marks
`list_pending`, `get_quiet_hours`, `is_quiet_hours`, `get_notification_settings` as
`side_effect: false` (pure reads); `send`, `queue`, `cancel`, `set_quiet_hours`, `mute`, `unmute`
stay at the default `true`.

## 6. Preventing side effects from capabilities that don't honour dry-run

They structurally cannot violate it: for any action not explicitly declared `side_effect: false`,
`loaded.instance.execute()` is never called under dry-run. There is no `if dry_run` branch inside
`NotifySkill` (or any future capability) for the registry to trust — the capability's own code is
unreachable for that action under dry-run.

**Named residual risk (see also RISKS.md):** this guarantee covers effects reachable through the
registered entry point. It does not protect against a capability performing a side effect from
somewhere else entirely — a background thread, a module-level import side effect, a call bypassing
`SkillRegistry` altogether. No capability does this today (most, including `NotifySkill`, don't yet
perform real external I/O — `_deliver_notification()` just logs), so the risk is theoretical now
and becomes load-bearing once Stage-6-era capabilities land. Recorded as a future hardening item,
not built here: a single low-level "effector" chokepoint every side-effecting call must funnel
through, one layer below today's `SkillRegistry.execute_action()`.

## 7. What a `DryRunResult` contains

New dataclass, `bartholomew/kernel/dry_run.py`:

| Field | Content |
|---|---|
| `dry_run_id` | Unique id for this simulation |
| `surface` | `"initiative"`, `"skill"`, etc. (mirrors `Observation.source`) |
| `proposed_action` | The `CandidateAction.kind` actually evaluated |
| `target` | `initiative_id` (or `"new"` for a `propose`), or `skill_id`/`action` |
| `parameters` | The real parameters that would have been used |
| `expected_effects` | Best-effort structured description of what execution would have done |
| `governance_decision` | The real, fully-evaluated verdict: allowed/denied, plus which gate and why |
| `approval_requirements` | What was actually checked: brake scope + state, Identity Policy decision + reason, consent/mute state |
| `would_execute` | `True` only if `governance_decision == allowed` and every other real gate passed |
| `actor`, `ts` | Standard provenance fields |

## 8. Audit/provenance representation — never confused with real actions

Dry-run results never enter any table a real query trusts as ground truth. New, dedicated table
(`dry_run_results`, owned by `dry_run.py`), not `initiatives`, not `initiative_audit`, not
`MemoryStore.reflections` (the unified `ActionReflection` sink), not `skill_action_audit`, not
Working Memory. This is structural, not conventional: `list_due_for_delivery()`,
`list_expiring()`, and every other real-state query never touch `dry_run_results` because dry-run
never writes to the tables they read — no future drive can forget a filter clause because there is
no such column to forget.

A new `SkillResultStatus.DRY_RUN` enum value (alongside `SUCCESS`/`ERROR`/`PENDING`/`CANCELLED`/
`PERMISSION_DENIED`) makes this true at the type level for the skill surface too: `.success` only
ever means `SUCCESS`, so no caller can mistake a dry-run result for a completed one.

Exposed read-only via `GET /api/dry-run/results`, never merged into `/api/initiatives/.../audit`
or any real audit view.

**Terminology note:** "Reflection" here means the Runtime Contract's per-action audit record
(`ActionReflection`), not the daily/weekly narrative reflection pipeline (`ReflectionGenerator`/
`NarratorEngine`) `COGNITIVE_RUNTIME.md` documents as having an open ownership gap. Dry-run's
provenance mechanism is unrelated to, and unblocked by, that gap.

## 9. Parking Brake and approval-gate interaction

Full, real, unmodified interaction. If the `"initiative"` ParkingBrake scope is engaged, a dry run
of a `deliver` transition truthfully reports `would_execute=False`, `governance_decision="denied"`
— it does not pretend the brake isn't there. Same for Identity Policy denial and missing consent.
Governance can interrupt a dry run exactly as it interrupts a live action, because it's the same
Governance code path, evaluated for real, every time.

Dry-run and ParkingBrake remain distinct concepts: ParkingBrake's verb is *stop* (a
`parking_brake_denied` outcome, a hard deny); dry-run's verb is *simulate* (a `would_execute`
result, not a denial). Modeling dry-run as a ParkingBrake scope would conflate "Governance said no"
with "Governance said yes, we just didn't act on it" — exactly the confusion §8 prevents. Dry-run
gets its own persistent, Governance-owned scoped switch (§11), architecturally parallel to
ParkingBrake but a distinct concept with distinct outcome semantics.

## 10. Failure behaviour and fail-closed guarantees

- If resolving whether a call is a dry run errors (e.g. the dry-run-engaged switch's storage is
  unreadable): **deny the action outright** — the same fail-closed philosophy
  `_is_blocked_by_brake()` already uses ("if the brake check itself errors, treat it as blocked").
  Not "silently downgrade to simulate" — that could leave a caller who wanted a real action
  believing something happened when nothing did.
- If building the `DryRunResult` itself errors after Governance already ran: the error propagates,
  matching every other seam's existing behaviour for a `store.propose`/`store.deliver` exception.
- A capability whose declared-safe read-only action errors during a dry run: treated exactly as a
  live error would be — logged, does not crash the dry run, `expected_effects` reflects the read
  failure rather than fabricating data.

## 11. Request-scoped, session-scoped, or global

The mechanism is request-scoped; the decision is resolved from two layered sources, OR'd together.

- **Mechanism (non-negotiable):** `dry_run: bool` is an explicit parameter on each seam call
  (`run_initiative_through_runtime_contract(..., dry_run=...)`, `execute_action(...,
  dry_run=...)`), threaded to the boundary in §4. No ambient/implicit state. This is also what
  lets `ROADMAP.md`'s Stage 5 exit criterion — *"scheduler runs check-ins... in dry-run + live"* —
  mean what it says: the same drive code path, toggled by one explicit parameter.
- **Resolution:** a new persistent, globally-scoped switch, architecturally identical to
  ParkingBrake/`GovernanceStore`'s `engage`/`disengage` pattern — `dry_run_state` table alongside
  `parking_brake_state` in `governance_store.py`, same engage/disengage/revision-guarded-loosening
  shape. `effective_dry_run = global_switch_engaged OR caller_requested_dry_run` — either source
  pushes toward simulation, **neither can push away from it**: a caller may never force dry-run
  off when the global switch says on, mirroring ParkingBrake's own fail-closed OR-composition.
- **Session-scoped:** evaluated and rejected — no clear use case beats the two above for this
  system, and it would add a third resolution layer for no identified benefit.

## 12. API/interface implications

- `GET /api/dry-run/status`, `POST /api/dry-run/engage` (`scopes`, `reason`, `actor`),
  `POST /api/dry-run/disengage` (`reason`, `expected_revision`, `actor`) — mirrors
  `routes/governance.py`'s existing brake shape.
- `GET /api/dry-run/results` (list, filterable by surface/target).
- No change to `/api/initiatives/consent` or any existing route. No `propose` HTTP route exists
  yet (S5.7), so per-call `dry_run` override is a Python-level kwarg for now.

## 13. Required tests

- Spy/monkeypatch `NotifySkill.execute()` and assert it is never called for a `side_effect: true`
  action during a dry-run `deliver`, across all four S5.4 delivery policies (immediate/
  critical_override are the highest-risk case, since they bypass suppression policies).
- Assert `initiatives`/`initiative_audit` row counts are provably unchanged (`SELECT COUNT(*)`
  before/after) across a full dry-run `propose` → `deliver` sequence.
- Assert `MemoryStore.reflections` and Working Memory both gain zero new rows from a dry run.
- Non-vacuity control: deliberately break the dry-run interception and confirm the "zero side
  effects" tests actually fail.
- Fidelity test: a dry-run `get_notification_settings`-classified read genuinely executes and
  reflects live state.
- Fail-closed test: dry-run-switch storage read raises → action is denied, not silently simulated
  or silently executed.
- Denial-fidelity tests: a dry run under each real ParkingBrake/Identity Policy/consent denial
  reports that denial truthfully, `would_execute=False`.
- Manifest-classification default test: an unclassified `SkillAction` is treated as `True`.
- API tests mirroring `test_governance_api.py`'s engage/disengage/revision-conflict shape.

## 14. Migration / backwards compatibility

- New `dry_run_results` table: pure addition.
- `SkillAction.side_effect`: additive field with a default (`True`); existing manifests keep
  working unmodified, conservatively classified.
- New `dry_run_state` table in `governance_store.py`: additive schema, no change to
  `parking_brake_state`/`governance_audit`.
- `SkillResultStatus.DRY_RUN`: new enum member, additive; existing `== SUCCESS`/`.success` checks
  unaffected.
- Seam functions gain one new optional kwarg (`dry_run: bool = False`) each — default preserves
  exactly today's behaviour for every existing caller and test.

## 15. Security risks and architectural traps

- The §6 residual risk: only as strong as "every side-effecting call funnels through a registered
  capability entry point" — true today, becomes load-bearing later. Flagged in RISKS.md.
- A "read-only" classification is itself a trust boundary — a capability author mis-declaring a
  mutating action as `side_effect: false` reintroduces the exact risk this design closes. Same
  review scrutiny as `permissions.level`.
- Rejected explicitly: a dry-run `propose` that persists a real row "to make deliver dry-runs
  easier to test." The entire safety property depends on never writing to `initiatives` under
  dry-run, even for convenience.
- Rejected explicitly: a caller-supplied `dry_run=False` overriding an engaged global switch.
  Closed by §11's OR-composition.
- `governance_denied` and `would_execute=False` (Governance allowed, but it's a dry run) must stay
  distinguishable in the result shape (§7) or a future caller could conflate "not allowed" with "a
  rehearsal."

## 16. Exact files/modules

**New:**
- `bartholomew/kernel/dry_run.py` — `DryRunResult`, `dry_run_results` schema/store, resolution
  helper, `record_dry_run_result()`.
- `bartholomew_api_bridge_v0_1/services/api/routes/dry_run.py` — the four §12 endpoints.
- `tests/test_dry_run.py`, `tests/test_dry_run_api.py`, plus additions to
  `tests/test_runtime_contract_initiative.py` and a new `tests/test_skill_registry_dry_run.py`.

**Modified:**
- `bartholomew/kernel/runtime_contract.py` — `dry_run` param on
  `run_initiative_through_runtime_contract()`; branch at the `propose`/`deliver` store-write
  points (§4).
- `bartholomew/kernel/skill_registry.py` — `dry_run` param on `execute_action()`; branch before
  `loaded.instance.execute()` (§4); a parallel `_finish_dry_run()`-shaped path writing to
  `dry_run_results` instead of `skill_action_audit`/the unified Reflection sink.
- `bartholomew/kernel/skill_manifest.py` — `SkillAction.side_effect: bool = True`.
- `bartholomew/kernel/skill_base.py` — `SkillResultStatus.DRY_RUN`.
- `bartholomew/orchestrator/safety/governance_store.py` — new `dry_run_state` table +
  `engage_dry_run()`/`disengage_dry_run()`/`is_dry_run_engaged()`.
- `config/skills/notify.yaml` — mark four read-only actions `side_effect: false` (§5).
- `bartholomew_api_bridge_v0_1/services/api/app.py` — register the new router.
- `ROADMAP.md`/`MASTER_PLAN.md` — S5.5 marked implemented, at commit time, per standing process.

## 17. Acceptance criteria

1. A dry-run `propose`/`deliver` call runs every real Governance gate and reports a truthful,
   structured `DryRunResult`, for both an allowed and a denied scenario.
2. Zero rows are added to `initiatives`, `initiative_audit`, `MemoryStore.reflections`,
   `skill_action_audit`, or Working Memory as a result of any dry-run call — proven by count-based
   tests, not code inspection.
3. `NotifySkill.execute()` is provably never invoked for a `side_effect: true` action under
   dry-run, across all four S5.4 delivery policies, with a non-vacuity control.
4. The global dry-run switch and per-call override compose correctly (§11), including the
   fail-closed error path (§10).
5. `GET /api/dry-run/results` returns a truthful record of a prior dry run, structurally
   distinguishable from real audit data by construction (separate table/endpoint).
6. Full existing suite remains green; default `dry_run=False` is a no-op change to every existing
   call site.
7. This document reflects the resolved S5.1 conflict and is the authoritative reference for future
   dry-run wiring (chat, scheduler drives, awaiting_response, voice/sight).

## 18. Explicitly deferred / out of scope for this document

- Dry-run wiring for chat, scheduler drives generally, awaiting_response, and voice/sight — the
  primitive supports them; wiring is separate, later, separately-approved work (§0).
- The low-level "effector" chokepoint named in §6/§15 as a future hardening item — not needed until
  a capability performs real external I/O.
- S5.6 (structured rationale logging) and S5.7 (live drives) — this document's `DryRunResult`
  shape is designed with an eye toward being a natural input to S5.6, without assuming S5.6's
  (not yet proposed) design.
