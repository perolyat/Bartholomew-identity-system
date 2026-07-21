# COGNITIVE_RUNTIME

> **The canonical answer to "how does Bartholomew think?"**
>
> Authored 2026-07-21 as MASTER_PLAN.md's "P2.5 — Runtime Convergence" item 11.5, after items
> 11.1–11.4 actually implemented the pieces this document describes. Where this doc says
> "implemented," that means: real code, real tests, verified against a live running app — not
> aspiration. Where it says "planned," nothing described exists in code yet.

## Governing principles

**Principle Zero** (governs flow): *Every external stimulus and every internally generated
initiative must traverse the same cognitive loop before execution.*

**Principle One — Uniform Cognition** (governs decision-making): *Every decision, regardless of
origin, is made by the same cognitive architecture.*

**Architectural Invariant**: *Every architectural responsibility has exactly one authoritative
owner. All other implementations are adapters, compatibility layers, or deprecated migrations.*

Together: everything enters the same loop (Zero), and everything is decided by the same mind
(One), and each part of that mind has exactly one owner (the Invariant). See `DECISIONS.md`
for the full rationale behind each.

## Ownership table

| Concept | Authoritative Owner | Implementations |
|---|---|---|
| Identity | Identity System (`identity_interpreter`) | YAML today (`Identity.yaml`), database tomorrow |
| Planning | Kernel Executive | `bartholomew/kernel/daemon.py`, `planner.py`, `scheduler/*` |
| Memory | Memory Substrate | `bartholomew/kernel/memory_store.py` (SQLite now, Postgres later) |
| Experience | Experience Kernel | `experience_kernel.py`, `narrator.py`, `working_memory.py`, `persona_pack.py` |
| Governance | Governance | `bartholomew/orchestrator/safety/parking_brake.py` + `bartholomew/kernel/skill_permissions.py` |
| Capabilities | Skill Registry | `bartholomew/kernel/skill_registry.py` — local skills today, remote services/MCP later |
| Conversation | Chat Surface | `bartholomew_api_bridge_v0_1/services/api/app.py`'s `/api/chat` |

Four duplicate pairs were found and marked deprecated (not deleted) rather than left ambiguous
— see `DECISIONS.md`'s "One authority per architectural concept" entry for the full list
(model routing, persona, permission gates, kill-switch).

## The Runtime Contract

Every interaction is meant to enter through the same shape:

```
Observation -> Interpretation -> Executive -> Governance -> Capability -> Execution -> Reflection -> Memory
```

No exceptions. Not even chat.

### Implementation status by stage (2026-07-21)

| Stage | Implemented for | Not yet implemented for |
|---|---|---|
| Observation | Chat (`bartholomew/kernel/runtime_contract.py`'s `Observation` dataclass) | Voice, sight (adapter stubs exist — `identity_interpreter/adapters/voice_io/`, `.../sight/` — but only gate-check `ParkingBrake`; no real capture/interpretation) |
| Interpretation | Chat — enriched with active goals/persona from the Experience Kernel | Voice, sight |
| Executive (candidate action) | Chat (`CandidateAction`, always `kind="chat_response"` today — no branching to skill invocation yet) | A real "should this become a skill call instead of a chat reply" decision |
| Governance | Chat (`ParkingBrake("skills")`) and skill execution (`ParkingBrake("skills")` + `PermissionChecker` +, when wired, `policy_engine.evaluate_tool_policy()`) | Scheduler drives — deliberately excluded, see below |
| Capability / Execution | Chat (via the existing `identity_interpreter.orchestrator.orchestrator.Orchestrator`, injected as `respond_fn`) and skills (`SkillRegistry.execute_action()`) | A unified capability-selection layer that picks between "reply" and "invoke skill X" for the same input |
| Reflection | Chat (`WorkingMemoryManager.add()`, a real, live caller for the first time) and Experience Kernel mutators (`NarratorEngine`'s episodic entries, always-on) | — |
| Memory | Working Memory's own snapshot/persistence (`WorkingMemoryManager.persist_snapshot()`, called by `KernelDaemon.stop()`) | A dedicated durable store for full chat transcripts (today only the working-memory item exists, not a separate long-term chat log) |

### Why scheduler drives are excluded from Governance's Policy Decision

`scheduler/loop.py`'s `_run_drive()` only checks `ParkingBrake("scheduler")` — it does **not**
consult `bartholomew.kernel.policy_engine.evaluate_tool_policy()`. This was tried during item
11.2 and reverted: internal scheduler drives (`self_check`, `curiosity_probe`,
`reflection_micro`, `fts_optimize`) are kernel self-maintenance functions, not "tools" in
`Identity.yaml`'s `tool_use.allowlist` sense. Gating them on it denied every drive by default in
production and, combined with the scheduler's retry loop not backing off on denial, starved the
asyncio event loop badly enough that the live app never answered its first `/healthz` request.
See `DECISIONS.md`'s "`tool_use.allowlist` gates skill/capability execution, not scheduler
drives" entry for the full incident write-up. If scheduler drives ever need Identity-derived
gating, it needs a different, drive-appropriate policy source — not this one — and must be
verified against a live running app, not just `pytest`.

## Execution lifecycle (skill/capability execution)

`SkillRegistry.execute_action(skill_id, action, params)` (`bartholomew/kernel/skill_registry.py`)
is the single choke-point every skill execution flows through, in this order:

1. Validate the skill is loaded and ready, and the action exists on its manifest.
2. **Governance — ParkingBrake**: check the `"skills"` scope. Fails closed (denies) if the
   check itself errors.
3. **Governance — Identity Policy Decision** (optional, opt-in via the `identity_context`
   constructor param; `None` by default): `policy_engine.evaluate_tool_policy(identity_context,
   skill_id)`. Skipped entirely if no `IdentityContext` was wired in.
4. **Governance — manifest permissions**: `_resolve_permissions()` resolves `"ask"`-level
   permissions via the same consent-handler mechanism used for memory-write consent
   (session-scoped grants only); `"never"`/ungranted `"auto"` permissions deny outright.
5. **Execution**: `loaded.instance.execute(action, params)`.
6. **Audit**: every attempt (success, failure, permission denial, or brake block) is recorded
   to the `skill_action_audit` table via `_finish()`/`_audit_execution()`, with PII-redacted
   params, regardless of which stage above produced the outcome.

See `INTERFACES.md`'s "Skill manifest" section and `tests/test_end_to_end_tasks_and_audit.py` /
`tests/test_runtime_convergence_policy.py`.

## Observation → Reflection lifecycle (chat)

`run_chat_through_runtime_contract(daemon, user_input, respond_fn)`
(`bartholomew/kernel/runtime_contract.py`), wired into `/api/chat`
(`bartholomew_api_bridge_v0_1/services/api/app.py`) when the kernel is running:

1. **Observation**: wrap the raw message with provenance (`source="chat"`, timestamp).
2. **Interpretation**: read `daemon.experience.get_active_goals()` and
   `daemon.persona_manager.get_active_pack_id()`; fold them into the prompt. Never raises — a
   failure to read Experience Kernel state falls back to the raw input unchanged.
3. **Executive**: construct a `CandidateAction(kind="chat_response", ...)` — today always this
   one kind; there is no branching logic yet deciding "this should be a skill call instead."
4. **Governance**: `ParkingBrake("skills")` check. Denial returns HTTP 503 from `/api/chat`,
   not an unhandled 500.
5. **Capability/Execution**: call the injected `respond_fn` (in production, the existing
   `identity_interpreter.orchestrator.orchestrator.Orchestrator.handle_input()`).
6. **Reflection**: `daemon.working_memory.add(...)` — a real, observable Working Memory entry,
   for the first time ever reachable from a conversation.
7. **Memory**: durability piggybacks on `WorkingMemoryManager`'s existing snapshot mechanism;
   no separate write in this stage.

See `tests/test_runtime_contract_chat_seam.py` and `tests/test_api_chat_runtime_contract.py`
(the latter hits the actual live `/api/chat` route via `TestClient`, not just the underlying
function — the discipline this whole milestone converged on after item 11.2's regression
wasn't caught by unit tests alone).

## Reflection lifecycle (Experience Kernel → Narrator)

Independent of chat, any `ExperienceKernel` mutator (`update_affect()`, `set_attention()`,
`activate_drive()`, `satisfy_drive()`, `add_goal()`, `complete_goal()` —
`bartholomew/kernel/experience_kernel.py`) emits a `GlobalWorkspace` event. `NarratorEngine`
(`bartholomew/kernel/narrator.py`) subscribes to these and turns them into an `EpisodicEntry`
via `generate_*_episode()`, with PII redacted and tone chosen from current affect
(`determine_tone()`), then `persist_episode()` writes it to the `episodic_entries` SQLite table
(FTS5-indexed). Persona packs can override narrative templates per tone
(`PersonaPackManager.get_narrative_templates()`, wired into `NarratorEngine._get_templates()`).

## Memory lifecycle

Two independent durability mechanisms exist today (not yet unified — a candidate future
convergence item):

- **Working Memory** (`WorkingMemoryManager`): token-bounded, in-process active context with
  configurable overflow policy (FIFO/LRU/PRIORITY/SUMMARIZE). Snapshotted via
  `persist_snapshot()`/loaded via `load_last_snapshot()`, called from `KernelDaemon.stop()`/
  `_init_experience_kernel()`.
- **Memory Store** (`bartholomew/kernel/memory_store.py`): the durable, consent-gated,
  redacted, encrypted long-term store (SQLite), with FTS/vector/hybrid retrieval. This is what
  `chat.py`'s standalone script and skills persist their own data through — `/api/chat`'s live
  path does not yet write conversation turns here (only into Working Memory).

## Governance checkpoints (all of them, in one place)

- **`ParkingBrake`** (`bartholomew/orchestrator/safety/parking_brake.py`): the persistent,
  fail-closed kill-switch, scoped (`global`/`skills`/`sight`/`voice`/`scheduler`). Checked by:
  skill execution, the chat Runtime Contract, `scheduler/loop.py`'s `_run_drive()` (`scheduler`
  scope only), and the (unwired) voice/sight adapter stubs.
- **`PermissionChecker`** (`bartholomew/kernel/skill_permissions.py`): per-skill manifest
  permission levels (`auto`/`ask`/`never`), consent resolution, session/persistent grants.
- **`policy_engine.evaluate_tool_policy()`** (`bartholomew/kernel/policy_engine.py`): the
  Executive's Identity-Context-derived Policy Decision, opt-in, currently wired only into skill
  execution (see "Why scheduler drives are excluded," above).
- **Consent handlers** (`bartholomew/kernel/memory/privacy_guard.py`): the one pluggable,
  interactive "ask the user" mechanism in the codebase, used for both memory-write consent and
  skill `"ask"`-level permission resolution.

## What this document does not yet cover (tracked, not forgotten)

- A unified capability-selection layer so the Executive can choose "reply via chat" vs. "invoke
  skill X" for the same input, rather than chat always producing a `chat_response` candidate
  action.
- Voice and sight as real observation sources (today: gate-check stubs only, exercised only by
  tests).
- A single durable store for full chat transcripts, unifying Working Memory's session-scoped
  view with Memory Store's long-term, consent-gated one.
- `ARCHITECTURAL_INVARIANTS.md` — a smaller, later document (explicitly *not* part of this
  milestone) containing only the rules meant to survive any future rewrite (Principle Zero,
  Principle One, one authority per concept, fail-closed, memory is consent-gated, every
  decision is explainable, etc.).

## Related canonical docs

- [MASTER_PLAN.md](MASTER_PLAN.md) — "P2.5 — Runtime Convergence" for the full backlog and
  acceptance criteria items 11.1–11.5 were implemented against.
- [DECISIONS.md](DECISIONS.md) — the architectural decisions and incident write-ups this
  document summarizes.
- [INTERFACES.md](INTERFACES.md) — detailed contracts for the Skill manifest and the Identity
  Context / Policy Decision shape.
- [ROADMAP.md](ROADMAP.md) — Stage 4.5, mirroring this milestone at the stage-gate level.
