# COGNITIVE_RUNTIME

> The canonical answer to "how does Bartholomew think?" — the cognitive loop, the runtime
> invariants it's supposed to satisfy, who owns which architectural concept, and the
> observation/reflection/memory lifecycle, grounded in the code as it exists today (not as
> planned). Written per `MASTER_PLAN.md` "P2.5 — Runtime Convergence" item 11.5.
>
> **Status:** describes a partially-converged runtime. Where a surface doesn't yet follow the
> shape described here, that's stated explicitly rather than glossed over — see "Exit Gate
> status" below.
>
> **Last updated:** 2026-08-17 (two changes. (1) The "Reflection ownership" section was rewritten:
> its "current implementation" text described concatenation and stated the unification code change
> "has not been made", both superseded by `8d87258`; and the reflection *model* path was repaired
> (the daemon no longer pins `backend="stub"`, and `ReflectionGenerator` can now be constructed on a
> headless host), so the section records provenance semantics and marks the S5.4 ownership
> prerequisite discharged. (2) Added "Cognition is independent of device and UI" under
> Personal-identity ownership, recording what `DECISIONS.md`'s server-centric deployment entry means
> for ownership inside this runtime. Documentation-only; that subsection authorises no device-agent,
> capability-protocol or multi-tenancy implementation, and none exists.)
>
> **Previously (2026-08-15):** (platform/personal-identity architecture: added one Ownership-table
> row and a new "Personal-identity ownership" subsection recording what the runtime assumes today
> about *whose* Bartholomew it is serving — verified against the code, which has no user/tenant/
> owner concept anywhere — and classifying each single-user assumption as acceptable-for-PoC, a
> documented migration seam, or a trap. No trap was found. Documentation-only; no code change
> authorised, and no existing runtime behaviour is described differently. See `CONSTITUTION.md`'s
> "One Platform, Many Personal Bartholomews" section and `DECISIONS.md`'s corresponding entry.)
>
> **Previously (2026-08-08):** New Direction reconciliation: added a new "Competency, Training,
> and Learning" section conceptually extending the Runtime Contract to cover competency/knowledge
> retrieval, confidence/proficiency, Executive application of competencies, and the
> Experience → Reflection → candidate learning → governed consolidation loop — per the
> architecture-review handoff reconciled in `DECISIONS.md`. This is a conceptual extension only,
> recorded the same way the `awaiting_response` state below was: as a canonical requirement, not
> yet implemented, and not authorising any code change. The Executive remains the sole decision
> authority; nothing here adds a second one. **Same-day follow-up:** added "Personal, generalisable,
> and system-level learning classification" — candidate learning must carry a personal /
> potentially-generalisable / system-level classification; a future, entirely conceptual
> generalisation pipeline is recorded but not built, and no cross-instance transport mechanism is
> authorised.)
>
> **Previously (2026-07-28):** documentation reconciliation pass 2: reflection ownership section
> rewritten to distinguish current-implementation concatenation from the approved target
> architecture, resolving a contradiction with `ROADMAP.md`/`MASTER_PLAN.md`; added the mapping to
> `CONSTITUTION.md`'s Observe/Interpret/Recommend/Act pipeline and the Observe/Interpret/Recommend/
> Govern/Act/Learn naming, with an explicit statement that the Executive is a decision-owning
> pillar, not a passive step; added the `awaiting_response` obligation-state requirement, not yet
> implemented. Previously: 2026-07-24.)

## The governing principles

Two rules, both from the 2026-07-21 architectural audit (`MASTER_PLAN.md` "P2.5 — Runtime
Convergence"), drive everything below:

- **Principle Zero** (governs flow): *every external stimulus and every internally generated
  initiative must traverse the same cognitive loop before execution.*
- **Principle One — Uniform Cognition** (governs decision-making): *every decision, regardless
  of origin, is made by the same cognitive architecture.*

Zero is about entry — nothing skips the loop. One is about the mind doing the deciding — the
loop isn't allowed to fork into "one brain for chat, another for skills." A companion
**Architectural Invariant** underpins both: *every architectural responsibility has exactly
one authoritative owner; everything else is an adapter, compatibility layer, or deprecated
migration.*

## The Runtime Contract (the loop itself)

```
Observation -> Interpretation -> Executive -> Governance -> Capability -> Execution -> Reflection -> Memory
```

| Stage | What it means | Where it lives |
|---|---|---|
| Observation | A raw external stimulus, wrapped with provenance (source + timestamp). | `bartholomew/kernel/runtime_contract.py`'s `Observation` dataclass |
| Interpretation | The observation given structure/context — for chat, enriched with persisted Experience Kernel state (active goals, active persona) so a reply can genuinely reference them. | `runtime_contract.py`'s `Interpretation` dataclass, built by `_build_interpretation()` |
| Executive | Proposes a candidate action; doesn't execute it. | `runtime_contract.py`'s `CandidateAction` dataclass |
| Governance | Fail-closed checks: the `ParkingBrake` kill-switch (always), plus — where wired — the Identity Context → Policy Decision check (`policy_engine.evaluate_tool_policy()`) and manifest-declared "ask"-level consent. | `bartholomew/orchestrator/safety/parking_brake.py`, `bartholomew/kernel/policy_engine.py`, `bartholomew/kernel/skill_registry.py`'s `_resolve_permissions()` |
| Capability | The thing that actually knows how to do the action (a chat backend, a loaded skill). | injected `respond_fn` for chat; `SkillBase` subclasses for skills |
| Execution | Running the capability and capturing its result. | `runtime_contract.py`'s `respond_fn` call; `SkillRegistry.execute_action()`'s `loaded.instance.execute()` |
| Reflection | A durable record of what happened, written *before* returning. | one canonical `ActionReflection` (`bartholomew/kernel/reflection.py`) for **both** surfaces, plus each surface's own store (chat still adds a Working Memory item; skills still write a `skill_action_audit` row) — see "Unified Reflection" below |
| Memory | The Reflection becoming durable, queryable state. | the shared sink: `MemoryStore.reflections` (kind `action_reflection`), written every action for chat and skills alike; plus `WorkingMemoryManager.persist_snapshot()` (chat context, on `KernelDaemon.stop()`) and the `skill_action_audit` table (skill compliance, immediate) |

**Every live surface now runs through an explicit, code-level version of this shape.** Chat
(`run_chat_through_runtime_contract()`), scheduler drives (item 11.17,
`run_drive_through_runtime_contract()`, which `scheduler/loop.py`'s `_run_drive()` delegates
to), skill execution (item 11.19, `SkillRegistry.execute_action()` builds the shape and
`run_skill_through_runtime_contract()` is the named seam `Planner.handle_skill_request()`
calls), and — as of item 11.21 (2026-07-24) — the voice and sight device surfaces:
`run_voice_through_runtime_contract()` / `run_sight_through_runtime_contract()` construct the
same `Observation`/`CandidateAction`, run parking brake + Identity Policy + fail-closed device
consent before any capability call, and emit one `ActionReflection` per attempt. The historical
adapters (`start_stream()` / `start_capture()`) are now thin compatibility wrappers that
delegate exclusively to those seams; their capture/stream *capability* stays an inert Stage 6
placeholder, reachable only through the governed seam. See "Device surfaces (voice/sight)" below
for what is and isn't in scope.

### Mapping to `CONSTITUTION.md`'s simple cognitive model (added 2026-07-28)

`CONSTITUTION.md`'s "Observation Philosophy" section states a simpler four-stage pipeline —
**Observe → Interpret → Recommend → Act** — for describing how reality-first observation becomes
action. That pipeline and this document's eight-stage Runtime Contract are not competing models;
the simple pipeline is a coarser view of the same loop. The authoritative mapping:

| `CONSTITUTION.md`'s stage | Runtime Contract stage(s) |
|---|---|
| Observe | Observation |
| Interpret | Interpretation |
| Recommend | **Executive** produces a recommendation or `CandidateAction` |
| *(implicit — see below)* | **Governance** evaluates the proposed `CandidateAction` |
| Act | Capability + Execution |
| *(implicit — see below)* | Reflection + Memory |

`CONSTITUTION.md`'s four-stage version compresses Governance into "Recommend → Act" (a
recommendation that is acted on is implicitly understood to have been approved) and does not
separately name the Reflection/Memory stages this document tracks explicitly. Nothing in
`CONSTITUTION.md` should be read as omitting Governance, Reflection, or Memory — they are simply
folded into the coarser four-stage description there, and this table is the explicit unfolding.
A fuller six-stage naming that some product discussion uses — **Observe, Interpret, Recommend,
Govern, Act, Learn** — maps onto the same eight Runtime Contract stages as: Observe = Observation;
Interpret = Interpretation; Recommend = Executive produces a `CandidateAction`; Govern = Governance
evaluates the proposal; Act = Capability + Execution; Learn = Reflection + Memory.

**The Executive is a decision-owning pillar, not a passive pipeline step.** Per `CONSTITUTION.md`'s
Five Pillars, the Executive "decides. It does not observe. It does not remember. It decides." In
the Runtime Contract, this means the Executive's `CandidateAction` is a genuine proposal that
Governance can and does deny (see the Exit Gate table below and the non-vacuity test requirements
noted throughout this document) — it is not a rubber-stamp step that always leads to execution.
Any future surface added to the Runtime Contract must preserve this: constructing a
`CandidateAction` that Governance cannot meaningfully deny is not a valid implementation of the
Executive stage.

### The `awaiting_response` obligation state (added 2026-07-28)

When Bartholomew sends a message (or the user sends one) that requires an external reply before
the underlying matter can be considered resolved, that matter enters an **`awaiting_response`**
state rather than being treated as complete the moment the message is sent. This is a runtime
lifecycle concept — an obligation that persists across the gap between Action and the eventual
Reflection that resolves it — and a cognitive-accessibility mechanism (per `CONSTITUTION.md`):
users who may mentally dismiss a matter after sending the original message should not have
Bartholomew do the same.

**Required properties of this state (recorded here as a canonical requirement; not yet
implemented — see below):**
- The matter remains visible to the Executive as an open obligation, not archived as resolved.
- It resumes automatically — without the user having to re-raise it — when a response arrives.
- It escalates or reminds appropriately when overdue, subject to the same governed notification
  controls (adaptive notifications, mute, quiet-hours) as any other Bartholomew-initiated contact.
- Provenance and every state transition (opened, reminded, escalated, resolved) remain auditable,
  the same as any other governed action.
- It is subject to the same Governance path as any other Capability/Execution — creating,
  escalating, or resolving an `awaiting_response` entry is itself an action that traverses
  Observation → Interpretation → Executive → Governance → Capability → Execution → Reflection →
  Memory, not a side channel that bypasses it.

**Implementation status:** this state does not exist in code today. It is recorded here as a
canonical runtime-lifecycle requirement so that Stage 1 (the minimal consumer web governance
shell, which must expose an awaiting-response queue — see `ROADMAP.md`) and any future proactive
Stage 5 behaviour are built against a single, already-agreed shape rather than inventing one ad
hoc. Building it is separate, approved work, not authorised by this documentation entry.

## Competency, Training, and Learning (conceptual extension — added 2026-08-08; data model implemented S5.1, the rest not yet)

*Per the architecture-review handoff reconciled in `DECISIONS.md`'s "One developing digital
individual — competency and training architecture" entry. This section conceptually extends the
Runtime Contract stages above to describe how Bartholomew is meant to acquire and apply learned
competence. **Status corrected 2026-08-11** (this paragraph previously read "Nothing in this
section is implemented today... no competency, training, or candidate-learning data model exists
in the code," which predated S5.1's 2026-08-10 merge): the **competency data model now exists** —
`bartholomew/kernel/competency.py`, five `MemoryStore` `kind` values, pure data with no
persistence or retrieval of its own (`ROADMAP.md`'s S5.1). Everything else in this section remains
unimplemented: `Planner.decide()` (Executive) still returns `None` unconditionally (S5.3), no
training-ingestion path exists (S5.2), and no candidate-learning/consolidation loop exists (S5.4).
Those remain canonical requirements for future, separately authorised work (see `ROADMAP.md`'s
Stage 5), the same status the `awaiting_response` state above has. Recording them here does not
authorise building them.*

### Why this extends the Runtime Contract rather than replacing it

`CONSTITUTION.md`'s "One Developing Digital Individual" section establishes that competencies are
learned descriptions of good judgement, not a second decision authority. Concretely, that means
competency/training machinery must slot into the *existing* eight-stage loop
(`Observation -> Interpretation -> Executive -> Governance -> Capability -> Execution ->
Reflection -> Memory`) as richer inputs to Interpretation and Executive and richer outputs from
Reflection — it must not add a competing loop, a competing Executive, or a competing Memory
authority. The table below extends the existing per-stage description; it does not add new stages.

| Stage | Conceptual extension |
|---|---|
| Interpretation | In addition to today's active-goals/active-persona enrichment, Interpretation is where **relevant competency and knowledge retrieval** happens: given the current Observation, retrieve the competencies, domain knowledge, procedures, heuristics, and prior experience/evidence from Memory that are plausibly relevant — the same retrieval machinery (FTS/vector/hybrid, `ConsentGate`-filtered) that already serves other memory reads, not a second retrieval path. |
| Executive | The Executive's `CandidateAction` construction is where **competencies are applied** — combining retrieved knowledge/procedures/heuristics, each competency's proficiency/confidence for the situation at hand, current goals and context, and available capabilities into a proposed action (or a request for missing information, or an explicit "I'm not confident enough here"). This is the Executive doing more of what it already does (propose, don't execute) with richer inputs — not a new decision-maker. Where a competency's own recorded supervision requirements call for it (e.g. low confidence, high-impact action, explicit "ask first" policy), the `CandidateAction` must reflect that need for review; Governance still makes the actual admission decision. |
| Governance | Unchanged in kind: the same fail-closed admission gate. A competency's own supervision/confidence metadata can *inform* what Governance is asked to approve (e.g. "requires consent" reflected in the proposal), but Governance's authority to approve, deny, or require consent is not delegated to the competency. |
| Capability | Unchanged: still a relatively dumb, executable tool. Competency reasoning happens before this stage, never inside a capability's own implementation. |
| Execution | Unchanged. |
| Reflection | In addition to today's `ActionReflection` (what happened, what surface, what outcome), Reflection is where **candidate learning** is produced: what Bartholomew believed going in, what it did, the observed outcome, whether the judgement was correct, and what — if anything — should change next time. This is additional structured content carried by the same reflection record, not a second reflection mechanism. |
| Memory | In addition to today's durable `MemoryStore.reflections` sink, Memory is where **governed consolidation** happens: candidate learning, carrying its own provenance and confidence, is either consolidated into memory/procedure/competency state directly (low-impact, high-confidence, routine cases) or routed through Governance/user review first (high-impact, low-confidence, or policy-flagged cases) before being consolidated — never silently. |

### Training as Memory input, not a separate pipeline

Per `CONSTITUTION.md`'s "Training vs. configuration," training material (formal reference material,
direct instruction, demonstration, correction, supervised-work outcomes, independent experience)
enters Bartholomew the same way any other knowledge does: as Observations that flow through
Interpretation, get judged by the Executive where relevant, and land in the shared Memory substrate
with provenance and consent — not through a separate training-ingestion runtime. A "trained"
procedure or piece of domain knowledge is Memory content like any other; it is retrieved,
Governance-filtered, and confidence-scored the same way retrieved knowledge always is.

### Memory semantics this implies (kinds, not a schema)

`bartholomew/kernel/memory_store.py`'s `memories` table already stores an open-ended `kind` string
(today's comment lists `fact`, `event`, `preference` as examples, not an enum), governed by
`memory_rules.yaml`'s kind/content/tag-matching rules rather than a fixed schema. The
competency/training direction does not require a new memory architecture or a new schema — it
requires new, well-understood *kinds* of content to exist within the same substrate, each
carrying provenance and (where applicable) confidence: domain knowledge, procedures, heuristics,
corrections, outcomes, competency evidence, and candidate learning, alongside the facts, events,
and preferences already stored today. Defining the exact `kind` values, their governance-rule
treatment (`memory_rules.yaml`), and any additional structured fields they need is deliberately
left to a separately authorised implementation pass — this document records the *semantic*
requirement, not a schema.

### Transfer boundaries

Learning acquired in service of one competency may improve judgement elsewhere (`CONSTITUTION.md`'s
example: contractor-quote evaluation transferring beyond estate management). This must remain
bounded by: **relevance** (does the retrieved evidence actually apply to the current situation),
**provenance** (where did this evidence come from and how much should that be trusted),
**confidence** (how strong is the evidence), **privacy** (does surfacing this evidence in a new
context violate the consent/privacy classification it was recorded under), **Governance** (does
applying it here require the same review a first-time action of this kind would), and **domain
boundaries** (some evidence is legitimately domain-specific and must not generalise — e.g. a
plumbing-contractor heuristic does not transfer to travel booking just because both involve
comparing vendor quotes). None of these is optional; indiscriminate transfer is explicitly a
non-goal.

### Personal, generalisable, and system-level learning classification (added 2026-08-08)

*Extends the "Transfer boundaries" subsection above with a structurally different, larger-scope
boundary. "Transfer boundaries" concerns evidence moving between competencies **within one
individual Bartholomew**. This subsection concerns whether learning could ever move **between
separate individual Bartholomew instances**, or into the product itself — per `CONSTITUTION.md`'s
"Personal learning vs. potentially generalisable and system-level learning" section, which this
conceptually implements. Nothing here is implemented; no cross-instance transport mechanism exists.*

Per that principle, candidate learning (produced at the Reflection stage, per the table above) must
carry a **classification** in addition to its provenance and confidence: **personal** (belongs to
this individual/instance, stays in its governed Memory, never auto-promoted), **potentially
generalisable** (a candidate lesson that might improve future Bartholomew versions, but only ever
a candidate), or **system/product** (an observation about Bartholomew's own behaviour rather than
the user's world). This classification is structured content the candidate-learning record carries
— not a new subsystem, store, or decision authority.

The future, conceptually-reserved-but-**not-built** generalisation pipeline this classification
must remain capable of supporting:

```
Individual experience -> Reflection -> Candidate learning -> Classification
  -> (Personal | Potentially generalisable | System-level)
  -> Privacy and provenance evaluation -> De-identification where genuinely possible
  -> Consent/Governance as required -> Validation -> Generalised lesson
  -> possible incorporation into future Bartholomew training, competency definitions,
     procedures, defaults, or product releases
```

**Status: entirely conceptual.** No classification field, no de-identification mechanism, no
cross-instance transport, no validation process, and no product-level incorporation path exist in
this repository today, and none is authorised by this section. The requirement this section
records is narrower and purely architectural: S5.1's competency/candidate-learning data model must
not make this future distinction structurally impossible to add later (e.g., by omitting
classification/provenance fields entirely) — it does not require building the pipeline now.

### Non-goals (mirrors `CONSTITUTION.md`)

- No `EstateExecutive`, `EstatePlanner`, `EstateMemory`, `EstateGovernance`, `EstateLLM`, or
  equivalent per-competency cognition/runtime, for Estate or any other competency.
- No second Memory authority holding competency data outside `MemoryStore`.
- No second Executive, and no competency that decides or executes on its own.
- No unrestricted self-modification: candidate learning is proposed, provenance/confidence-scored,
  and — where required — governed/reviewed before consolidation; it is never applied silently or
  unconditionally, and it is a distinct mechanism from unrestricted self-modification or a
  standing ability to rewrite policy/user preferences without review.
- Ordinary operational training is not assumed to mean foundation-model fine-tuning.
- No cross-user or cross-instance learning-transport mechanism exists or is authorised here.
  Personal learning never automatically becomes shared, global, or product-level knowledge; a
  "potentially generalisable" classification marks a candidate for a future governed process, not
  a transfer that has happened.

## Ownership table

One authoritative owner per architectural concept. Other implementations of the same concept
are adapters, compatibility layers, or deprecated migrations — never a second decision-maker.
(Mirrors `MASTER_PLAN.md` item 11.1 / `DECISIONS.md`'s "One authority per architectural
concept" entry; this file is the reference copy going forward.)

| Concept | Authoritative owner | Implementations |
|---|---|---|
| Identity | Identity System (`identity_interpreter`) | YAML today (`Identity.yaml`), database tomorrow |
| Planning | Kernel Executive | `daemon.py` / `planner.py` / `scheduler/*` |
| Memory | Memory Substrate | SQLite now, Postgres later |
| Experience | Experience Kernel | `experience_kernel.py`, `narrator.py`, `working_memory.py` |
| Governance | Governance (`ParkingBrake` + `skill_permissions.py`) | — |
| Capabilities | Skill Registry | Local skills today; remote services / MCP later |
| Conversation | Chat Surface | — |
| Competency (data: knowledge, procedures, evidence, proficiency) | Memory Substrate — competencies are a structured *description* held in the same substrate, not a separate store (added 2026-08-08, conceptual — no implementation exists yet) | — |
| Competency (application/reasoning) | Kernel Executive — same owner as Planning, above; applying a competency is the Executive doing its existing job with richer inputs, not a second reasoning authority (added 2026-08-08, conceptual) | — |
| Training (ingestion of trained material) | No new owner — training material is Observation → Interpretation → Memory like any other input (added 2026-08-08, conceptual) | — |
| Personal-identity ownership (whose Bartholomew persisted state and executing work belong to) | **No owner exists today, deliberately.** The runtime serves exactly one personal Bartholomew identity, and the deployment itself — one process, one SQLite database, one filesystem path — is the implicit boundary. When ownership becomes real it belongs to the Identity System (row 1), never to individual stores or capabilities (added 2026-08-15, conceptual — see below) | — |

The 2026-07-21 audit named four "duplicate pairs." Three (persona, permission gates,
kill-switch) are genuine. The fourth, "model routing," was **reclassified in item 11.15 as not
a duplicate**: model *selection* (the Identity task-type policy) and model *routing* (backend
dispatch) are distinct concepts, so neither side is deprecated. Status of each:

| Concept | Authoritative | Deprecated / notes |
|---|---|---|
| Model selection **vs.** routing — *distinct, not a pair* (item 11.15) | selection: `identity_interpreter/policies/model_router.py` (`select_model`, reads `Identity.yaml`'s `by_task_type`; used by CLI `explain` + `chat.py`). routing: `identity_interpreter/orchestrator/model_router.py` (`ModelRouter`, backend dispatch + generation, live via `Orchestrator.route_model()`). | — (neither deprecated). **Tracked gap:** the live routing path doesn't yet consult the selection policy — only `select_model`'s callers honor `by_task_type`. |
| Persona | `bartholomew/kernel/persona_pack.py` (`PersonaPackManager`, wired into `NarratorEngine`/`ExperienceKernel`; both legacy callers now migrated) | ~~`identity_interpreter/policies/persona.py`~~ — **removed 2026-07-22** (item 11.12); CLI `explain` + standalone `chat.py` now read tone from `PersonaPackManager`'s active pack |
| Permission gates | `bartholomew/kernel/skill_permissions.py` (`PermissionChecker`, gates skill *manifests* at `SkillRegistry.execute_action()`) **+** `bartholomew/kernel/policy_engine.py` (`evaluate_tool_policy`, the `tool_use`-allowlist check) | ~~`identity_interpreter/policies/tool_policy.py`~~ — **removed 2026-07-22** (item 11.14); its `tool_use`-allowlist role was superseded by `evaluate_tool_policy`, and the CLI `explain --tool` caller now uses `evaluate_tool_policy(build_identity_context(...))` |
| Kill-switch | `bartholomew/orchestrator/safety/parking_brake.py` (`ParkingBrake`, persistent, wired into 5 live gate points) | ~~`identity_interpreter/adapters/kill_switch.py`~~ — **removed 2026-07-22** (item 11.13); was print-only with zero live callers |

Rule going forward: **do not add new callers to a deprecated module.** Delete only once its
last caller is migrated.

### Personal-identity ownership (added 2026-08-15)

`CONSTITUTION.md`'s "One Platform, Many Personal Bartholomews" section establishes that
Bartholomew is ultimately one shared platform serving many strongly isolated personal identities,
and that the current runtime is **the first personal Bartholomew identity on an early deployment
of that platform** — not a different system. This subsection records what that runtime actually
assumes today, so a future reader does not mistake current deployment convenience for architecture,
and does not have to re-derive the seams. **Nothing here is a defect report, and nothing here
authorises a change.**

**What the runtime assumes today.** There is no user, tenant, owner or account concept anywhere in
`bartholomew/`, `bartholomew_api_bridge_v0_1/` or `identity_interpreter/` — verified by search, not
assumed. One process serves one person; `BARTH_DB_PATH` (default `data/barth.db`) resolves one
SQLite database that *is* the personal state; the API bridge has no authentication and treats every
caller as the owner (`INTERFACES.md` §6 records this accurately as a local/dev surface). Several
kernel components hold module-level singletons (`narrator.py`, `encryption_engine.py`,
`memory_rules.py`, `retrieval_config.py`, `metrics_registry.py`), which is process-global state
standing in for per-identity state. Two audit surfaces already carry a provenance field that could
later carry identity — `governance_audit.actor` and `skill_permissions.granted_by` — though both
currently record *which subsystem or surface* acted, not *which person*. Note also that
`request_admission.py`'s "identity-bound" wording refers to per-request admission tokens, not to
user identity; the two are unrelated.

**Classification.** Each single-user assumption is one of four things. Recording which one it is
now is the point of this subsection:

| Assumption in current code | Classification |
|---|---|
| One process / one runtime serves one person; module-level singletons hold personal runtime state | **Acceptable for the PoC.** Correct for a single-identity deployment, and the natural multi-identity form (one runtime context per identity, or per-identity instances behind the platform) does not require these modules to be rewritten — only constructed differently. |
| One SQLite database at one filesystem path is the personal-state boundary | **Acceptable for the PoC, and a documented seam.** A per-identity database is itself a legitimate strong-isolation strategy, so this choice does not foreclose the platform architecture. What must not happen is code *reasoning about* the path as though it were the identity. |
| API bridge assumes a trusted single-user environment (no auth, no caller identity) | **Documented migration seam.** Already governed: `DECISIONS.md`'s deployment-architecture entry and `ROADMAP.md` Stage 6 both require a reviewed threat model before any remote exposure (a requirement the 2026-08-17 server-centric entry carries forward unchanged from the superseded hybrid local-first entry). The admission middleware in `app.py` is the existing single chokepoint where caller identity would attach — one place, not per-route. |
| `memories` is uniquely indexed on `(kind, key)` **globally**, with no ownership dimension (`memory_store.py`) | **Documented migration seam — the one worth naming explicitly.** In a multi-identity store, uniqueness must be per identity, not global; two users may each have a `user_profile`/`home_address`. Correctable later by an ordinary additive migration (add the ownership column, rebuild the index over `(owner, kind, key)`). Cheap now *and* cheap later, so it is deliberately **not** being changed now — but it must not be relied upon as a global-uniqueness guarantee by future code. |
| Scheduler, drives and background work carry no ownership (`scheduler/*`) | **Documented migration seam.** Background cognition executing on someone's behalf is precisely where "on whose behalf?" must eventually be answerable. No change now; the requirement is that new background work does not acquire *additional* assumptions that one scheduler equals one person. |
| Governance/parking-brake state is a singleton row (`governance_store.py`) | **Acceptable for the PoC, with a constraint.** Per `CONSTITUTION.md`, local Governance authority must remain locally enforceable regardless of topology — so a future platform must not relocate the brake's authority to a central service, whatever it does with the brake's *state*. |

**No serious architectural trap was found.** No current code equates Bartholomew with a particular
model, prevents personal state from being exported or migrated, or makes personal state
structurally unable to acquire an owner. The Ownership table above is already model-agnostic
(Memory: "SQLite now, Postgres later"; Identity: "YAML today, database tomorrow"; Capabilities:
"local skills today, remote services / MCP later"), which is the same replaceability this
architecture requires.

**The constraint this places on new work:** do not add *new* code that deepens any of the
assumptions above — in particular, do not introduce new persisted personal state that could not
later acquire an owner, new background execution whose beneficiary is unrecoverable, or new global
uniqueness constraints over personal data. That is a constraint on how new code is shaped, not a
mandate to change existing code.

### Cognition is independent of device and UI (added 2026-08-17)

`DECISIONS.md`'s "Deployment architecture — server-centric Bartholomew with local/edge capability
agents" is the authority for the deployment direction; this subsection records only what it means
for **ownership inside this runtime**, which is this document's concern.

**No device, client or presentation surface owns any part of cognition.** The Runtime Contract,
Executive, Memory Substrate, Experience Kernel and Governance checkpoints must remain reachable and
correct with *no* particular UI attached. A client — the web application today, a native companion
application later — is a source of Observations and a consumer of output, never a required
component of the loop. This is already how the runtime is built: `run_chat_through_runtime_contract()`
takes an `Observation` and a response callable and knows nothing about HTTP or a browser, and the
voice/sight seams are the same shape. The entry above makes that property load-bearing rather than
incidental, so it is recorded here.

**Device capabilities are discovered, never assumed.** When device/agent capabilities eventually
exist, what a device can do is a runtime fact it declares and Governance constrains — not a static
assumption compiled into cognition, and not uniform across platforms (Windows, macOS, Android and
iOS differ materially). The existing precedent is the correct one: `cloud_llm.readiness()` and
`/api/health`'s `model_status` report what is *actually* available rather than what is configured,
because a capability reported as present but unusable is the failure mode worth designing against.

**The constraint this places on new work:** do not make cognition depend on a specific client,
transport, or device being present; do not infer a capability's availability from configuration
alone. **No device-agent, capability-protocol or multi-tenancy implementation is authorised** —
none exists, and this subsection creates no interface.

## Governance checkpoints

### The kill-switch: `ParkingBrake`

> **This section is the canonical authority for Parking Brake scope, authority tiers, and
> precedence.** Other documents reference it; they do not restate it. See "Authority tiers" below
> — a reader who takes "one brake" to mean "one undifferentiated switch shared by every personal
> Bartholomew" has misread this section, and that reading is explicitly wrong as architectural
> direction.

One brake per deployment today, with scopes. `engage("skills")` blocks only that scope;
`engage()` with no args defaults to `"global"`, which blocks everything. Fail-closed:
if the brake check itself errors, treat it as blocked (see `SkillRegistry._is_blocked_by_brake()`
and `runtime_contract.py`'s governance stage — both catch-and-deny rather than catch-and-allow).

Live call sites today, each checking a different scope:

| Scope | Call site |
|---|---|
| `skills` | `SkillRegistry.execute_action()` |
| `skills` | `runtime_contract.py`'s chat Governance stage |
| `skills` | `identity_interpreter/orchestrator/orchestrator.py`'s `handle_input()` (the chat backend itself — a second, independent check of the same scope; belt-and-suspenders, not yet unified into one check) |
| `scheduler` | `bartholomew/kernel/scheduler/loop.py` (via `runtime_contract.run_drive_through_runtime_contract()`, item 11.17) |
| `sight` | `runtime_contract.run_sight_through_runtime_contract()` (item 11.21); `identity_interpreter/adapters/sight/pipeline.py`'s `start_capture()` delegates here |
| `voice` | `runtime_contract.run_voice_through_runtime_contract()` (item 11.21); `identity_interpreter/adapters/voice_io/stream_bridge.py`'s `start_stream()` delegates here |

As of item 11.21, the `sight`/`voice` scopes are no longer brake-*only*: those two seams run the
brake check, then the same additive Identity Policy Decision the other surfaces use, then an
always-required fail-closed device consent gate, before their (inert Stage 6) capability.

**Where brake state actually lives (corrected 2026-08-15).** This section previously read "one
global brake (`system_flags` table)", which Phase B overtook. Since stage **B6**, the write
authority is `GovernanceStore` (`parking_brake_state` + `governance_audit`, in
`bartholomew/orchestrator/safety/governance_store.py`) — `bartholomew/cli.py`'s `brake on`/
`brake off`, the `skills` gate and the `scheduler` gate all go through it. The legacy
`ParkingBrake`/`BrakeStorage` pair (`system_flags`) still exists and is still what the `sight` and
`voice` seams read. That split is **known and deliberately deferred**, not newly discovered: B4
found those paths unreachable (no live caller) and deferred consolidation;
`docs/B6_EXTERNAL_GOVERNANCE_CLI_SAFETY.md` §1 finding 5 re-confirmed and again deferred it. It is
not a live safety hole today because the capability behind those seams is inert. See `RISKS.md`'s
tech-debt watchlist for why it matters more under the authority tiers below.

#### Authority tiers: Personal/User and Platform/Admin (added 2026-08-15)

*(Architectural direction, **not** current implementation. Required by `CONSTITUTION.md`'s "One
Platform, Many Personal Bartholomews" and recorded in `DECISIONS.md`. Nothing here authorises
implementation — see "Current PoC mapping" below for what exists now, which is sufficient.)*

Once the platform serves many personal Bartholomews, there must be **two distinct Parking Brake
authority tiers**. They are a MUST-HAVE pair; narrower governance scopes are possible future
extensibility only (see "Not now" below).

| Tier | Who may activate | What it halts | Authority |
|---|---|---|---|
| **Personal/User Parking Brake** | the user, for their own Bartholomew | relevant execution for **that personal Bartholomew only** — autonomous actions, scheduled execution, capability execution, external side effects, device/environment control | the user is the ultimate authority over execution performed on their behalf |
| **Platform/Admin Parking Brake** | authorised platform administration/governance | relevant execution **across the entire platform**, in a serious safety, security, governance, systemic-defect, critical-operational or other platform-wide emergency | higher scope; overrides subordinate personal autonomy permissions, trust levels, approvals and execution authority |

**The tiers are orthogonal to the existing `scopes` axis, and must not be conflated with it.**
Today's scopes (`global`, `skills`, `sight`, `voice`, `scheduler`, `training`) answer *what class
of execution is halted*. The tiers answer *whose execution stops, and on whose authority*. Adding
`"platform"` as another string alongside `"skills"` would be a category error and an actual safety
defect: it would make a platform-wide halt clearable by the same ordinary `disengage()` any user
can call. A platform halt is a different authority, not a bigger scope.

**Precedence rules (unambiguous, in order):**

1. An active **Platform/Admin** brake overrides personal settings, autonomy level, trust level,
   prior approvals and execution authority. **A user must not be able to override it** through any
   personal control, setting, or accumulated trust.
2. A **Personal/User** brake halts only that personal Bartholomew. One user engaging their brake
   **must never** stop, degrade, or alter the authority or state of any other user's Bartholomew.
3. The tiers compose **restrictively, never permissively**: execution proceeds only if *neither*
   tier blocks it. Disengaging one tier never implies disengaging the other. This preserves the
   existing "the brake can only become more restrictive without an explicit, confirmed loosening
   action" invariant `GovernanceStore` already implements via revision-guarded `disengage()`.
4. A platform-wide halt **must not require** administrators to disable users individually; and
   individually disabling users is not a substitute for it.

**Local enforceability is not optional, and the Platform tier does not replace it.** Per
`CONSTITUTION.md`'s hybrid/local Governance requirement and `DECISIONS.md`'s deployment-
architecture entry — clause (b) of the 2026-08-17 server-centric entry, which retained this
requirement verbatim from the superseded hybrid local-first entry precisely because moving
cognition server-side makes it matter more, not less: wherever Bartholomew can act on a user's local devices or physical/digital environment,
that user must retain a **locally enforceable** means of stopping their own Bartholomew even when
central services are unavailable, connectivity is lost, or the remote platform is malfunctioning.
**A platform outage must never leave local autonomous execution unstoppable.** The Platform/Admin
tier adds an authority above the user; it removes nothing from the user's local authority over
their own devices.

**Parking Brake scope is Governance authority, not a UI feature.** A client may expose controls,
but the halt must be enforced below the presentation layer, at the execution boundary — which is
where the live gates already sit (`SkillRegistry.execute_action()`, the Runtime Contract's
Governance stage, the scheduler drive path). A client disappearing, crashing, being bypassed, or
losing connectivity **must not by itself invalidate the underlying halt state.** The current
implementation already satisfies this shape: state is persisted and re-read fail-closed at the
gate, not held in a UI session.

**Designs that are ruled out** — each is a defect, not a trade-off: one undifferentiated global
brake boolean used for every user; one user's brake stopping every user; one user's brake
affecting another user's authority or state; no independent platform-wide emergency halt; users
able to override a platform safety halt; central infrastructure as the *only* mechanism capable of
stopping local execution; a platform-wide halt that requires disabling users one at a time.

**Not now.** Narrower governance scopes — globally disabling one defective capability, suspending
one integration, disabling an execution class while preserving read-only cognition, isolating a
compromised subsystem — are recognised as **possible future extensibility only**. Do not design or
implement that system. The two MUST-HAVE tiers are Personal/User and Platform/Admin.

**Current PoC mapping.** This deployment serves exactly one personal Bartholomew identity
(`ASSUMPTIONS.md` A9), so the existing brake **conceptually is the Personal/User Parking Brake**,
and is sufficient at this stage. It is global only because there is exactly one user for it to be
global over — not because a single undifferentiated switch is the intended architecture. **No
Platform/Admin tier exists, and none should be built now**; with one deployment and one user there
is no platform to halt and no administrator distinct from the user. The migration seam to preserve
is simply that brake state and its gates must eventually be able to answer *whose* brake and
*which tier* — which is the same ownership seam recorded under "Personal-identity ownership"
above, applied to Governance. The relevant consequence for the deferred sight/voice consolidation:
tier awareness must be added **once**, in `GovernanceStore`, not twice — so the legacy-reading
seams should be consolidated onto `GovernanceStore` *before* tiers are introduced, or they would
silently not honour them.

### Identity Context → Executive → Policy Decision

Identity never answers "what should I do?" — only "who am I?" `identity_interpreter.identity_context.build_identity_context()`
extracts a declarative `IdentityContext` (core values, red lines, `tool_use` allowlist/
default-allowed/consent-prompts) from a loaded `Identity.yaml`. The Kernel's Executive —
`bartholomew.kernel.policy_engine.evaluate_tool_policy()` — is what turns that context into an
executable `PolicyDecision` (`allowed`, `requires_consent`, `rationale`, `reason`) for one
specific proposed action. Identity computes no decisions; the Kernel never parses
`Identity.yaml` directly.

**Wired today:** `SkillRegistry.execute_action()` consults it when a daemon is constructed
with `identity_path` (optional — no `IdentityContext` means the check is skipped, not denied).
`KernelDaemon.__init__()` builds one context and shares it. As of 2026-07-21, chat's Governance
stage (`runtime_contract.py`) consults it too — with one deliberate carve-out: `CandidateAction`
kinds in `_CONVERSATIONAL_KINDS` (currently just `"chat_response"`, the only kind the chat seam
produces today) are exempt from the `tool_use.allowlist` check, for the same reason scheduler
drives are (see next paragraph) — `evaluate_tool_policy()`'s `tool_name` param means "a skill_id
or scheduler drive task_id," and Identity.yaml's real `tool_use` section is `default_allowed:
false` / `allowlist: [web_fetch, browser_action]`, so evaluating plain conversation against it
unconditionally would deny 100% of chat turns the moment an `IdentityContext` is wired in
(which the live API bridge already does by default). The exemption is proven by
`tests/test_runtime_contract_chat_seam.py::TestChatGovernanceConsultsPolicyDecision`. A future
tool/skill-shaped candidate action proposed *during* a chat turn (kind outside the exempt set)
would still be evaluated for real.

As of item 11.17 (2026-07-23), the scheduler's own drives (`_run_drive()` in
`scheduler/loop.py`, now delegating to `run_drive_through_runtime_contract()`) consult it too,
with the same shape of carve-out: task_ids in `_SELF_MAINTENANCE_DRIVES` (`self_check`,
`curiosity_probe`, `reflection_micro`, `fts_optimize` — today's full `drives.py` `REGISTRY`)
are exempt. This is the corrected version of an earlier attempt (`MASTER_PLAN.md` item 11.2)
that evaluated every drive's task_id unconditionally — that denied every drive by default in
production and busy-looped the event loop badly enough that `/healthz` stopped answering (see
`DECISIONS.md`). The exemption is proven under the exact restrictive `Identity.yaml` policy
that caused that incident by
`tests/test_scheduler_drive_convergence.py::TestRegressionGuardAgainstItem112`, plus a live
`run_scheduler()` smoke check (item 11.17's writeup in `MASTER_PLAN.md`). A future
scheduler-originated action outside the exempt set (e.g. a drive that acts on the user's
behalf) would still be evaluated for real. So today a tool-use rule change in `Identity.yaml`
provably changes skill-execution, chat, and scheduler-drive outcomes alike for anything outside
each surface's known-safe exemption set — closing the remaining concrete gap under Principle
One that this section previously named.

### Consent ("ask"-level permissions)

Manifest-declared skill permissions at `"auto"` level are granted at `load_skill()` time.
`"ask"`-level permissions are resolved per-call by `SkillRegistry._resolve_permissions()`,
which reuses the same registered consent handler as memory consent
(`bartholomew.kernel.memory.privacy_guard.get_consent_handler()`) rather than inventing a
second "ask the user" mechanism.

### Memory-side consent, redaction, retention

Separate from the action-governance path above: `MemoryRulesEngine` (`memory_rules.py`,
config-driven from `memory_rules.yaml`) decides `should_store()` / `requires_consent()` per
memory kind; `ConsentGate` (`consent_gate.py`) pre-filters retrieval results (FTS and vector)
down to consented memory IDs before a caller ever sees them; `redaction_engine.py` /
`redact_pii()` strip PII from text before it's persisted. This governs the durable
`MemoryStore` (SQLite). It's a separate mechanism from the Experience Kernel's own privacy
handling below — both exist, neither replaces the other.

## The memory / reflection lifecycle

There are three distinct "memory" mechanisms in play, each with its own scope and lifetime.
None of them is "the" memory system to the exclusion of the others — the Memory Substrate
ownership entry above refers to the durable SQLite backbone all three ultimately write to.

1. **Working Memory** (`working_memory.py`, `WorkingMemoryManager`) — short-term, token-budgeted
   (`OverflowPolicy`-governed eviction, priority decay, attention boosting). This is what
   chat's Reflection stage writes to (`daemon.working_memory.add(...)`), and what
   `runtime_contract.py`'s Interpretation stage reads back — `get_active_goals()` /
   `get_active_pack_id()` (via the Experience Kernel / persona manager), and, as of
   2026-07-21, `get_context_string()` directly, so a later chat turn's prompt can reference an
   earlier turn's own content, not just goals/persona (item 11.7). This is also the
   authoritative replacement for `identity_interpreter.orchestrator.context_builder.
   ContextBuilder`'s conversational-memory injection, which is dead code in production (see
   that module's docstring) — a fifth duplicated-concept pair beyond item 11.1's original four,
   just never exercised at runtime because nothing wires an `identity_config` into
   `Orchestrator()` today. Persisted via `persist_snapshot()` / restored via
   `load_last_snapshot()` around daemon start/stop.

2. **Experience Kernel self-model** (`experience_kernel.py`, `ExperienceKernel`) — drives,
   affect, attention, active goals, situational context; decays/updates every tick
   (`daemon._system_tick()`). Snapshotted (`SelfSnapshot`) and persisted the same way as
   Working Memory. This is "who Bartholomew currently is," not a log of what happened.

3. **Narrator episodic layer** (`narrator.py`, `NarratorEngine`) — persisted `EpisodicEntry`
   rows (affect changes, drive activations, goals, observations, reflections), generated from
   `GlobalWorkspace` events and persona-aware narrative templates (`_get_templates()` prefers
   the active `PersonaPack.narrative_overrides` over static fallback templates). Text passed
   through `_redact()` before persistence when `Identity.yaml`'s
   `narrator_episodic_layer.logs.redact_personal_data` is set — this is the Experience
   Kernel's *own* privacy gate, separate from `ConsentGate`/`memory_rules.py` above (a gap
   found and fixed in the Stage 3 correction; see `ROADMAP.md`).

**Unified Reflection: one shape, one sink (item 11.16, 2026-07-23).** Both surfaces now emit
the *same* canonical record — `bartholomew.kernel.reflection.ActionReflection` (`surface`,
`action`, `outcome`, `summary`, `details`, `ts`) — through the *same* Memory sink,
`MemoryStore.reflections` under the `action_reflection` kind (`record_action_reflection()`),
for every action and every outcome (a chat turn that responded or was governance-denied; a
skill execution that succeeded, failed, was denied, or brake-blocked). The record is PII-safe
by construction: `to_memory_row()` runs `redact_pii()` over the summary and every string in
`details`, matching what `skill_action_audit` already did for its params. This is what Exit
Gate #4 asked for — the *shape* is now unified, not just the *fact* of a reflection.

The change is deliberately **additive**: the surface-specific stores stay, because they serve
different jobs from the durable Reflection. Chat still adds a Working Memory item (its
short-term context buffer, feeding `get_context_string()`); skill execution still writes a
`skill_action_audit` row (`SkillRegistry._audit_execution()`, the detailed immediate compliance
audit). What changed is that there is now *also* one canonical Reflection type flowing into one
sink, which there wasn't before. Retiring or deriving the surface-specific stores from the
unified record (so there's genuinely one write, not three) is a possible future simplification,
not done here. **This unified-shape item is distinct from, and does not resolve, the
reflection-*ownership* question below** — it unifies the record type both surfaces write, not
which subsystem composes daily/weekly reflection *content*.

### Reflection ownership (corrected 2026-07-28)

**Approved target architecture (recorded 2026-07-28):** `ReflectionGenerator` is the authoritative
owner of reflection composition and final reflection output. `NarratorEngine`'s episodic
narrative is supplementary evidence supplied *to* that authoritative process — not an
independent, co-equal, or competing reflection pipeline.

**Current implementation (updated 2026-08-17): the target architecture is implemented.**
`daemon.py`'s `_run_daily_reflection()`/`_run_weekly_reflection()` collect the narrator's episodic
material **first** and pass it into `ReflectionGenerator` as `episodic_evidence`; one authority
composes one document. The string concatenation this section previously described
(`content = f"{content}\n\n---\n\n{episodic_narrative}"`) is gone. See
`docs/S5_4_REFLECTION_OWNERSHIP.md` for the design, and `tests/test_reflection_ownership.py` /
`tests/test_reflection_narrative_integration.py` for the tests that pin it.

> **Superseded text.** Until 2026-08-17 this subsection stated that both pipelines ran
> independently, that "additive concatenation, not unification" was the precise description, and
> that "**That code change has not been made.**" All three statements described behaviour that
> commit `8d87258` had already replaced. The historical contradiction they were written to resolve
> — this document saying "remain unreconciled" while `ROADMAP.md` said "✅ reconciled…additively" —
> is settled and no longer live; it is recorded here only so the correction is traceable.

**What was still missing, and is now fixed (2026-08-17):** ownership was correct, but no real model
had ever *exercised* it. Two independent defects made every reflection in the project's history
template-composed:

1. `daemon.py` pinned `backend="stub"` in both reflection paths. The stub's mock text tripped
   `ReflectionGenerator`'s own red-line check, the redraft tripped it again, and composition fell
   through to a fallback template every night. Identity's model policy never applied to reflection.
2. `ReflectionGenerator.__init__` could not run on a headless host at all —
   `Orchestrator(identity_config=…)` built a `ContextBuilder`, which eagerly built a
   `MemoryManager`, which required the OS keystore and raised.

Both are repaired. The daemon passes no `backend` override, so reflection routes through the same
Identity-driven selection as every other surface (`task_type: general`, which `Identity.yaml` keeps
local — reflection reads stored personal memory, and the local/cloud egress boundary is
unratified). `ContextBuilder` builds its `MemoryManager` lazily, so a keystore-less host composes
with empty memory context instead of failing to construct. Pinned by
`tests/test_reflection_model_path.py`.

**Provenance:** a stored reflection's `meta.generator` names what actually composed it — `llm`,
`template` (generation failed; `meta.error` carries backend/model/reason), or `stub`. It was
previously hard-coded to `llm` for any success, including stub output, which meant mock text could
be persisted as model-composed.

**Binding consequence for Stage 5:** the reflection-ownership prerequisite is **discharged**. Live
proactive reflection behaviour (`ROADMAP.md` Stage 5) is no longer blocked on it. The remainder of
S5.4 — the experience → candidate learning → provenance/confidence → Governance → consolidation
loop — is the larger half and remains unbuilt.

## Exit Gate status

`MASTER_PLAN.md`'s Runtime Convergence Exit Gate asks seven questions; P3 (the initiative
engine) is recommended to wait until all seven are "yes" (a recommendation, not yet a binding
gate — needs explicit user sign-off to block on). Answered honestly against the code as it
exists today:

| # | Question | Status | Evidence |
|---|---|---|---|
| 1 | Can every input source create an Observation? | **Yes** (for every surface that exists today) | Chat (`source="chat"`), scheduler drives (item 11.17, `source="scheduler"`), skill execution (item 11.19, `source="skill"`), and — as of item 11.21 (2026-07-24) — voice and sight: `run_voice_through_runtime_contract()` / `run_sight_through_runtime_contract()` build `Observation(source="voice"/"sight", raw_content="voice_stream_start"/"sight_capture_start")` at their governed entry point, and the compat adapters (`start_stream()`/`start_capture()`) delegate exclusively to them. The capture/stream *capability* is an inert Stage 6 placeholder reachable only through the seam, so any future real device input flows through an Observation by construction. Proven by `tests/test_voice_sight_runtime_contract_seam.py`. No other input source exists today; future sensors are Stage 6. |
| 2 | Does every proposed action pass through the Executive? | **Yes** | Chat (`kind="chat_response"`), scheduler (`kind=task_id`, item 11.17), skill (`kind=skill_id`, item 11.19), and voice/sight (`kind="voice_stream_start"`/`"sight_capture_start"`, item 11.21) each build an explicit `CandidateAction` that is *genuinely consumed* by Governance, not decorative. For voice/sight, `tests/test_voice_sight_runtime_contract_seam.py::TestGovernanceGenuinelyGatesExecution` captures both the constructed `kind` and the value `evaluate_tool_policy()` receives and asserts they're equal, proves denied starts never invoke the underlying capability (a call-counting spy stays at 0), and shows an unrelated-but-present allowlist entry still denies (ruling out a hardcoded/drifted stand-in). |
| 3 | Does every execution pass through the same Governance path? | **Yes** | All five live surfaces share the same `ParkingBrake` class (scopes differ by surface, by design) plus the additive Identity Context → Policy Decision check (chat item 11.6, scheduler 11.17, skill 11.19, voice/sight 11.21 — each exempting only its own known-safe kinds where applicable; voice/sight have no exemption, so every start is policy-evaluated). Voice/sight additionally *always* require a fail-closed device consent gate (`privacy_guard.get_consent_handler()`, the same channel skill "ask" permissions reuse) — absent/declined/unresolved all deny. `tests/test_voice_sight_runtime_contract_seam.py` proves ordering (policy, then consent, then capability — strictly), exactly-once execution and reflection, that the compat wrappers delegate only to the seam, and — the required non-vacuity controls — that deliberately neutralising *any one* of the three gates (brake, policy, consent) makes exactly that gate's denial tests execute the placeholder, so none of the denial tests pass vacuously. A separate AST structural test proves the placeholder capability is never invoked outside the governed seam. |
| 4 | Does every completed action produce a Reflection? | **Yes** | Item 11.16 (2026-07-23) — chat turns and skill executions now emit the same `ActionReflection` into the same sink (`MemoryStore.reflections`, kind `action_reflection`), for every outcome. Shape *and* sink are unified (see "Unified Reflection" above); each surface's own store (Working Memory for chat context, `skill_action_audit` for skill compliance) is retained additively. |
| 5 | Does every Reflection update Memory? | **Yes** | Working Memory snapshots persist on daemon stop; `skill_action_audit` writes immediately; daily/weekly reflections persist via `MemoryStore.insert_reflection()`. |
| 6 | Does every conversation see the Experience Kernel? | **Yes, for chat** | Item 11.4 — `/api/chat` routes through `run_chat_through_runtime_contract()`, whose Interpretation stage reads `daemon.experience.get_active_goals()`, `daemon.persona_manager.get_active_pack_id()`, and (item 11.7) `daemon.working_memory.get_context_string()` for prior-turn content. Falls back to the unwrapped path only when the kernel isn't running (startup/shutdown window). No other conversational surface exists today to check. |
| 7 | Does every interface expose the same personality? | **Yes** (Stage 4.5 scope; voice/sight persona formally reclassified to Stage 6 — see note below the table) | The Stage 4.5 goal this question encodes — *"one personality, not one personality per interface"* (MASTER_PLAN.md's P2.5 finding) — is met for every interface that produces personality-bearing output today: chat, CLI `explain`, and standalone `chat.py` all source tone from the single persona authority (`PersonaPackManager`'s active pack) after the deprecated `identity_interpreter/policies/persona.py` was removed (item 11.12, 2026-07-22). The `traits` those callers read from `Identity.yaml` are a stable identity descriptor, not persona-pack state — a deliberate split, not a convergence gap. The one remaining piece — voice/sight consulting persona — is **formally reclassified to Stage 6 (2026-07-24)**: a surface that produces no personality-bearing output cannot "expose" a personality, so converging persona onto voice/sight is inseparable from Stage 6's persona-producing voice/sight functionality (the already-approved Stage 6 boundary), not a Stage 4.5 deliverable left undone. |

**Reading this table:** the loop shape is real, tested, and load-bearing for chat, skills, the
scheduler, and (for the single-start attempt) voice/sight — this is not aspirational. As of item
11.21 (2026-07-24) the governance questions **1–3 are all "yes" for every surface that exists
today** — the last current-production governance gap (voice/sight being parking-brake-only stubs)
is closed. Questions **4–6** are "yes" for the surfaces that exist (#6 notes chat is the only
conversational surface). Question **7** is "yes within Stage 4.5's scope": every personality-
bearing interface shares the one persona authority, and its only residual — voice/sight persona —
was **formally reclassified to Stage 6 on 2026-07-24** (see the Q7 row and ROADMAP.md Stage 6),
because a surface with no persona-bearing output cannot expose a personality until Stage 6 builds
that output. With that reclassification recorded, **all seven exit-gate questions are satisfied
within Stage 4.5's scope, and Stage 4.5's Runtime Convergence is complete.** One non-gate item
remains open but was never a Stage 4.5 exit criterion: two reflection-narrative pipelines (Stage
3's "Still open" note). Timeline: two-Reflection-shapes gap closed by 11.16; scheduler-drive gap
by 11.17; skill-execution Observation/CandidateAction gap by 11.19; voice/sight governance gap by
11.21; the Q7 voice/sight-persona residual reclassified to Stage 6 by 11.22; the four
duplicate-concept pairs by 11.12–11.15.

### Device surfaces (voice/sight) — scope boundary (item 11.21)

What item 11.21 established is strictly the **governed architectural seam**, not any device
capability:

- Every executable voice/sight *start* creates the appropriate `Observation`/`CandidateAction`,
  passes ParkingBrake → Identity Policy → fail-closed device consent **before** any capability
  call, executes the (inert) capability at most once, and writes exactly one `ActionReflection`
  to the shared sink — success, policy denial, consent denial, brake denial, or execution error
  alike.
- The `start_stream()`/`start_capture()` adapters remain as public compatibility entry points but
  delegate *exclusively* to the seam; their capability bodies (`_perform_stream`/`_perform_capture`)
  are inert Stage 6 placeholders, reachable only through the seam (an AST test forbids any direct
  call).
- **Governance approval here authorizes exactly one start attempt** — never indefinite or
  continuing microphone/camera access. The action kinds are suffixed `_start` to encode this.

Explicitly **Stage 6, not implemented here**: real microphone/camera/streaming/transcription/
computer-vision/device-driver behaviour; continuous capture sessions; consent renewal and
revocation; and stop/teardown semantics. One safety requirement is recorded for Stage 6 now so it
isn't lost: **safely stopping or tearing down a future active capture session must never depend on
obtaining permission to *continue* capturing** — teardown is not a governed "start" and must not
be gated as one.

## Verify

```bash
pytest -q tests/test_runtime_contract_chat_seam.py
pytest -q tests/test_api_chat_runtime_contract.py
pytest -q tests/test_runtime_convergence_policy.py
pytest -q tests/test_experience_kernel.py tests/test_persona_pack.py
pytest -q tests/test_scheduler_drive_convergence.py
pytest -q tests/integration/test_parking_brake_integration.py
```

## See also

- `MASTER_PLAN.md` — "P2.5 — Runtime Convergence" (the full narrative and backlog this doc is
  extracted from)
- `ROADMAP.md` — Stage 4.5 (stage-gate framing, exit criteria) and Stage 5 (the staged,
  separately-approved competency/training/learning plan this document's "Competency, Training, and
  Learning" section conceptually underwrites)
- `CONSTITUTION.md` — "One Developing Digital Individual: Competencies and Training" (the
  enduring principle this document's conceptual extension implements)
- `DECISIONS.md` — "One authority per architectural concept" and related entries, and "One
  developing digital individual — competency and training architecture"
- `INTERFACES.md` — subsystem-level interface contracts
