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
> **Last updated:** 2026-08-08 (New Direction reconciliation: added a new "Competency, Training,
> and Learning" section conceptually extending the Runtime Contract to cover competency/knowledge
> retrieval, confidence/proficiency, Executive application of competencies, and the
> Experience → Reflection → candidate learning → governed consolidation loop — per the
> architecture-review handoff reconciled in `DECISIONS.md`. This is a conceptual extension only,
> recorded the same way the `awaiting_response` state below was: as a canonical requirement, not
> yet implemented, and not authorising any code change. The Executive remains the sole decision
> authority; nothing here adds a second one.)
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

## Competency, Training, and Learning (conceptual extension — added 2026-08-08, not yet implemented)

*Per the architecture-review handoff reconciled in `DECISIONS.md`'s "One developing digital
individual: competency and training architecture" entry. This section conceptually extends the
Runtime Contract stages above to describe how Bartholomew is meant to acquire and apply learned
competence. Nothing in this section is implemented today — `Planner.decide()` (Executive) still
returns `None` unconditionally, and no competency, training, or candidate-learning data model
exists in the code as of this writing. This is a canonical requirement for future, separately
authorised work (see `ROADMAP.md`'s Stage 5), the same status the `awaiting_response` state above
has. Recording it here does not authorise building it.*

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

## Governance checkpoints

### The kill-switch: `ParkingBrake`

One global brake (`system_flags` table), with scopes. `engage("skills")` blocks only that
scope; `engage()` with no args defaults to `"global"`, which blocks everything. Fail-closed:
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

**Current implementation:** `daemon.py`'s `_run_daily_reflection()`/`_run_weekly_reflection()`
call **both** `identity_interpreter.adapters.reflection_generator.ReflectionGenerator` (LLM-based,
safety-checked, produces `content`) **and** `narrator.py`'s
`generate_daily_reflection_narrative()`/`generate_weekly_reflection_narrative()` (template-based,
built from real persisted episodes), and string-concatenate the two outputs
(`content = f"{content}\n\n---\n\n{episodic_narrative}"`, added in item 11.8, 2026-07-21). **This
is concatenation, not architectural unification.** Both pipelines run unconditionally and
independently; the code does not enforce a single authority over reflection composition today.
This document previously stated the two pipelines "remain unreconciled" while `ROADMAP.md`
separately stated they were "✅ reconciled... additively" and referenced a "Still open" note in
`ROADMAP.md` Stage 3 that no longer exists there (it had been overwritten by the "reconciled"
text in the same 2026-07-27 pass that removed it) — those two canonical documents gave literally
opposite answers to the same question. That contradiction is resolved by this section: neither
past phrasing was quite right; "additive concatenation, not unification" is the precise, single
description now used consistently in `MASTER_PLAN.md`, `ROADMAP.md`, and here.

**Approved target architecture (recorded 2026-07-28):** `ReflectionGenerator` is the authoritative
owner of reflection composition and final reflection output. `NarratorEngine`'s episodic
narrative is supplementary evidence supplied *to* that authoritative process — not an
independent, co-equal, or competing reflection pipeline.

**The gap between current implementation and approved target:** closing this gap requires a real
code change — routing `NarratorEngine`'s episodic narrative into `ReflectionGenerator` as an input
(e.g. as additional context/evidence it composes with, rather than an appended, separately-produced
block) — plus tests verifying `ReflectionGenerator` is the sole point of final composition. **That
code change has not been made.** It is out of scope for this documentation-only pass and requires
its own separate authorisation.

**Binding consequence for Stage 5:** live proactive *reflection* behaviour (`ROADMAP.md` Stage 5)
remains blocked until this gap is closed by a separately authorised code change and verified by
tests — concatenation of two independently-running pipelines is not an acceptable foundation for
new proactive behaviour built on top of reflection output.

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
  developing digital individual: competency and training architecture"
- `INTERFACES.md` — subsystem-level interface contracts
