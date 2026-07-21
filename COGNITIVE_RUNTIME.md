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
> **Last updated:** 2026-07-21

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
| Reflection | A durable record of what happened, written *before* returning. | a `WorkingMemoryManager.add()` item for chat; a `skill_action_audit` row for skills (see "Two different Reflection mechanisms" below — these are not yet the same thing) |
| Memory | The Reflection becoming durable, queryable state. | `WorkingMemoryManager.persist_snapshot()` (on `KernelDaemon.stop()`); `skill_action_audit` table (written immediately, every call) |

**No exceptions is the goal, not yet the reality.** Today two surfaces run through an explicit,
code-level version of this shape — chat (`run_chat_through_runtime_contract()`) and skill
execution (`SkillRegistry.execute_action()`, which is the single choke-point every skill call
flows through, whatever triggered it). Voice, sight, the scheduler's own drives, and future
sensors are named in the Runtime Contract's design but do not yet construct an `Observation`/
`CandidateAction` — see "What's not converged yet" below.

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

Four duplicate pairs existed structurally as of the 2026-07-21 audit; the losing side of each
now carries an explicit deprecation docstring pointing at the winner (verified directly in the
source, not just recorded here):

| Concept | Authoritative | Deprecated (still has legacy callers) |
|---|---|---|
| Model routing | `identity_interpreter/orchestrator/model_router.py` (`ModelRouter`, drives live requests) | `identity_interpreter/policies/model_router.py` (CLI `explain` + standalone `chat.py` script only) |
| Persona | `bartholomew/kernel/persona_pack.py` (`PersonaPackManager`, wired into `NarratorEngine`/`ExperienceKernel`) | `identity_interpreter/policies/persona.py` (same two legacy callers) |
| Permission gates | `bartholomew/kernel/skill_permissions.py` (`PermissionChecker`, actually gates `SkillRegistry.execute_action()`) | `identity_interpreter/policies/tool_policy.py` (CLI `explain` only) |
| Kill-switch | `bartholomew/orchestrator/safety/parking_brake.py` (`ParkingBrake`, persistent, wired into 5 live gate points) | `identity_interpreter/adapters/kill_switch.py` (prints to stdout, zero live callers) |

Rule going forward: **do not add new callers to a deprecated module.** Delete only once its
last caller is migrated.

## Governance checkpoints

### The kill-switch: `ParkingBrake`

One global brake (`system_flags` table), with scopes. `engage("skills")` blocks only that
scope; `engage()` with no args defaults to `"global"`, which blocks everything. Fail-closed:
if the brake check itself errors, treat it as blocked (see `SkillRegistry._is_blocked_by_brake()`
and `runtime_contract.py`'s governance stage — both catch-and-deny rather than catch-and-allow).

Five live call sites today, each checking a different scope:

| Scope | Call site |
|---|---|
| `skills` | `SkillRegistry.execute_action()` |
| `skills` | `runtime_contract.py`'s chat Governance stage |
| `skills` | `identity_interpreter/orchestrator/orchestrator.py`'s `handle_input()` (the chat backend itself — a second, independent check of the same scope; belt-and-suspenders, not yet unified into one check) |
| `scheduler` | `bartholomew/kernel/scheduler/loop.py` |
| `sight` | `identity_interpreter/adapters/sight/pipeline.py` (stub adapter) |
| `voice` | `identity_interpreter/adapters/voice_io/stream_bridge.py` (stub adapter) |

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

**Not wired today:** the scheduler's own drives (`_run_drive()` in `scheduler/loop.py`) don't
consult it at all; an earlier attempt to wire scheduler drives into this check caused a real
production regression (drives were denied by default and the scheduler's un-backed-off retry
loop busy-looped the event loop badly enough that `/healthz` stopped answering — see
`DECISIONS.md` and `MASTER_PLAN.md` item 11.2's writeup). So today a tool-use rule change in
`Identity.yaml` provably changes skill-execution outcomes and can affect chat (for any future
non-conversational candidate action), but does not yet reach the scheduler-drive path — the
remaining concrete gap under Principle One.

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

**Two different Reflection mechanisms today, not one.** Chat's Reflection stage writes a
Working Memory item. Skill execution's Reflection stage writes a `skill_action_audit` row
(`SkillRegistry._audit_execution()`, itself PII-redacted via `redact_pii()` on string params) —
every `execute_action()` attempt, success or denial alike. Both are durable, both are audited,
but they are structurally different records, not the same `Reflection` type flowing through
one Memory sink. Unifying them is unstarted work, not yet claimed as done anywhere.

**Two reflection *pipelines* also remain unreconciled** (noted, not new here): `daemon.py`'s
daily/weekly `ReflectionGenerator` (via `identity_interpreter.adapters.reflection_generator`)
and `narrator.py`'s own `generate_daily_reflection_narrative()` /
`generate_weekly_reflection_narrative()` both exist and neither has been retired in favor of
the other (tracked in `ROADMAP.md` Stage 3's "Still open" note).

## Exit Gate status

`MASTER_PLAN.md`'s Runtime Convergence Exit Gate asks seven questions; P3 (the initiative
engine) is recommended to wait until all seven are "yes" (a recommendation, not yet a binding
gate — needs explicit user sign-off to block on). Answered honestly against the code as it
exists today:

| # | Question | Status | Evidence |
|---|---|---|---|
| 1 | Can every input source create an Observation? | **Partial** | Chat does (`runtime_contract.Observation`). Skill execution has an equivalent choke-point (`execute_action()`) but no explicit `Observation` object. Voice/sight adapters and scheduler drives have their own `ParkingBrake` checks but never construct one. |
| 2 | Does every proposed action pass through the Executive? | **Partial** | Chat's `CandidateAction` is explicit. Skill execution is a single choke-point in practice but not modeled as a `CandidateAction`. Scheduler drives bypass this entirely. |
| 3 | Does every execution pass through the same Governance path? | **Partial** | All five live call sites share the same `ParkingBrake` class, but scopes differ by surface. The Identity Context → Policy Decision check (item 11.2) is now wired for both skill execution and chat's Governance stage (2026-07-21) — chat's only exemption is plain-conversation `CandidateAction` kinds, the same category exemption scheduler drives need but don't yet have. The scheduler's own drives still bypass Policy Decision entirely. |
| 4 | Does every completed action produce a Reflection? | **Yes, structurally** | Chat → Working Memory item; skills → `skill_action_audit` row (see "Two different Reflection mechanisms" above — the *fact* of a reflection is universal for these two surfaces; its *shape* is not unified). |
| 5 | Does every Reflection update Memory? | **Yes** | Working Memory snapshots persist on daemon stop; `skill_action_audit` writes immediately; daily/weekly reflections persist via `MemoryStore.insert_reflection()`. |
| 6 | Does every conversation see the Experience Kernel? | **Yes, for chat** | Item 11.4 — `/api/chat` routes through `run_chat_through_runtime_contract()`, whose Interpretation stage reads `daemon.experience.get_active_goals()`, `daemon.persona_manager.get_active_pack_id()`, and (item 11.7) `daemon.working_memory.get_context_string()` for prior-turn content. Falls back to the unwrapped path only when the kernel isn't running (startup/shutdown window). No other conversational surface exists today to check. |
| 7 | Does every interface expose the same personality? | **No** | The deprecated `identity_interpreter/policies/persona.py` still has live legacy callers (CLI `explain`, standalone `chat.py` script) independent of `PersonaPackManager`; voice/sight adapters don't consult persona at all yet. |

**Reading this table:** the loop shape is real, tested, and load-bearing for chat and skills —
this is not aspirational. What's not yet true is *uniformity across every surface*, which is
exactly what Principle One demands. The gaps above (scheduler drives skipping both Executive
and Policy Decision; voice/sight being parking-brake-only stubs; two Reflection shapes; two
reflection-narrative pipelines; one lingering persona duplication) are the concrete backlog
for closing this gate, not a restatement of the plan.

## Verify

```bash
pytest -q tests/test_runtime_contract_chat_seam.py
pytest -q tests/test_api_chat_runtime_contract.py
pytest -q tests/test_runtime_convergence_policy.py
pytest -q tests/test_experience_kernel.py tests/test_persona_pack.py
```

## See also

- `MASTER_PLAN.md` — "P2.5 — Runtime Convergence" (the full narrative and backlog this doc is
  extracted from)
- `ROADMAP.md` — Stage 4.5 (stage-gate framing, exit criteria)
- `DECISIONS.md` — "One authority per architectural concept" and related entries
- `INTERFACES.md` — subsystem-level interface contracts
