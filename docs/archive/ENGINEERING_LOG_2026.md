# Engineering Log 2026 (archived from MASTER_PLAN.md)

> **ARCHIVED — historical engineering narrative only. Not a canonical document. Not current
> guidance.** For current project status, use `MASTER_PLAN.md` (the executive SSOT), `ROADMAP.md`
> (stage gates), `COGNITIVE_RUNTIME.md` (current architecture), `DECISIONS.md` (durable decisions),
> and `RISKS.md` (current risk register). Where anything below conflicts with those canonical
> documents, the canonical documents win.
>
> **Extracted 2026-07-28** (documentation reconciliation pass 2) from `MASTER_PLAN.md`, which had
> grown to ~2,200 lines — a long implementation chronicle rather than a usable executive plan, per
> the reconciliation brief's own diagnosis. Every item below is preserved **verbatim** from
> `MASTER_PLAN.md` as it stood before this extraction, except where an explicit "**Correction
> added 2026-07-28**" editorial note is inserted to flag a claim later superseded by the
> reflection-ownership decision recorded in `COGNITIVE_RUNTIME.md`. Item numbers (11.1–11.22) are
> preserved unchanged so that existing cross-references from `DECISIONS.md`, `RISKS.md`,
> `INTERFACES.md`, `TEST_MATRIX.md`, and `ROADMAP.md` citing "item 11.x" remain resolvable — they
> now resolve here rather than in `MASTER_PLAN.md` itself. `MASTER_PLAN.md` retains a compact index
> of these same item numbers with a one-line summary and a pointer to this file.

---

## FTS5 external-content `upsert()` bug and `sys.path` self-pollution — fixed 2026-07-20

Investigated two test failures the docs (`docs/STATUS_2025-12-29.md`) attributed to
Windows-only FTS5/environment quirks. Both reproduced identically on Linux and were real
logic bugs, not platform noise — the doc's diagnosis was stale/wrong for the current code.

### Bug 1: `FTSClient.upsert()` corrupts SQLite's FTS5 view for never-indexed rows

**Symptom:** `tests/test_consent_gates.py::test_fts_search_without_consent_gate` failed with
`sqlite3.DatabaseError: database disk image is malformed` — a genuinely confusing SQLite
error that does *not* mean the file is actually corrupt on disk.

**Root cause:** `FTSClient.upsert()` unconditionally issued FTS5's special `'delete'` command
(`INSERT INTO memory_fts(memory_fts, rowid, ...) SELECT 'delete', ...`) for every rowid,
regardless of whether that rowid had ever actually been indexed. In external-content FTS5
mode, creating the virtual table does *not* backfill rows that already exist in the content
table (`memories`) — so a `memories` row inserted before `FTSClient.init_schema()` ran (or via
any path that bypasses the sync triggers) has no corresponding entries in the FTS5 shadow
tables. Issuing `'delete'` for such a rowid is a genuine SQLite/FTS5 misuse this build reports
as "database disk image is malformed" — confirmed with a minimal, project-independent repro
outside pytest entirely (bare `sqlite3`, no project code).

**Fix:** `upsert()` now checks `memory_fts_map` (the class's own bookkeeping table, already
populated exactly on real inserts by both `upsert()` and the sync triggers) before issuing
`'delete'`, and skips it for rows that were never indexed.

**Verify:** `pytest -q tests/test_consent_gates.py` — 9 passed, 1 skipped (was 1 failed).

### Bug 2: two more instances of permanent `sys.path` self-pollution (broke `test_metrics_production_mode.py`, likely much more)

**Symptom:** All three `tests/test_metrics_production_mode.py` tests failed with
`ImportError: attempted relative import with no known parent package` inside
`bartholomew_api_bridge_v0_1/services/api/app.py`'s `from . import db_ctx` — but *only* when
run after certain other tests in the same session; passed in isolation.

**Root cause:** `bartholomew_api_bridge_v0_1/services/api/routes/metrics.py` (imported as
soon as the API app is, i.e. by nearly every API-app test) did, at module import time:
```python
sys.path.insert(0, ".../bartholomew/kernel")
from metrics_registry import get_metrics_registry
```
`sys.path.insert(0, ...)` here is never undone, so it poisons `sys.path` for the rest of the
process. Once `bartholomew/kernel/` is on `sys.path[0]`, a later bare `from app import app`
anywhere in the same process can resolve to
`bartholomew_api_bridge_v0_1/services/api/app.py` loaded as a *disconnected top-level* module
(shadowing the real `app.py` at repo root, since that directory also happens to contain a
file named `app.py`) — and a module loaded that way has no package context, so its own
relative import (`from . import db_ctx`) fails immediately. Confirmed with a minimal repro
(bare `sys.path.insert` + `from app import app`, no test framework involved) that reproduces
the exact error.

Worse: `bartholomew/kernel/` also contains `types.py`. The same pollution mechanism means any
later bare `import types` anywhere in the process — including deep inside the stdlib
(`dataclasses`, `enum`, `typing`, etc. all do this) — could silently resolve to this project's
`bartholomew.kernel.types` module instead of Python's real `types` module. This is a strong
candidate for at least some of the ~20 other FTS/hybrid/retrieval test failures already on
record as "pre-existing, not investigated" (several show `ValueError`/`sqlite3.OperationalError`
with no obvious connection to FTS at all) — not confirmed as the cause of all of them, but
worth checking before assuming they're something else.

Found two instances of this same anti-pattern (a bare `sys.path.insert(0, <dir>)` with no
corresponding removal, used to reach a sibling package without a proper dotted import) and
fixed both — replaced with the proper package-qualified import, since both targets
(`bartholomew.kernel.metrics_registry`, `bartholomew.kernel.db_ctx`) were already reachable
that way:
- `bartholomew_api_bridge_v0_1/services/api/routes/metrics.py` — inserted `bartholomew/kernel/`
  to reach `metrics_registry` (this is the one that broke the tests above).
- `bartholomew/kernel/scheduler/health.py` — inserted `bartholomew_api_bridge_v0_1/services/api/`
  to reach `db_ctx.wal_db`, duplicating `bartholomew.kernel.db_ctx.wal_db`, which already exists.

A **third** instance was already fixed as part of the earlier `input()` follow-up work today,
for an unrelated reason (it also duplicated a kernel-local helper instead of using it) —
`bartholomew/kernel/memory_store.py`'s `MemoryStore.close()` inserted
`bartholomew_api_bridge_v0_1/services/api/` to reach a second copy of
`wal_checkpoint_truncate`, when `bartholomew.kernel.db_ctx.wal_checkpoint_truncate` (the
kernel's own copy, explicitly written "to avoid coupling to the API layer") already did the
same thing plus the Windows-handle-release step the duplicate lacked calling correctly.

A **fourth** instance remains, deliberately not fixed: `scripts/hybrid_search.py` (a
standalone CLI demo script, not imported anywhere in the test suite) inserts the repo root
onto `sys.path[0]`. Lower risk (the repo root doesn't contain files that shadow stdlib/common
module names the way `bartholomew/kernel/` and `bartholomew_api_bridge_v0_1/services/api/`
do), and out of the test suite's reachable import graph, so left alone.

**Verify:**
```bash
pytest -q -k "consent or privacy or memory_rules or phase2 or metrics or stage1"
# 129 passed, 2 skipped (was 126 passed, 3 failed, 2 skipped)
pytest -q  # full suite: 42 failures -> 38 (all remaining are the separate,
           # already-larger, not-yet-investigated FTS/hybrid/encryption body of work)
```

---

## Retrieval consent-enforcement bug: `requires_consent` memories excluded unconditionally — fixed 2026-07-21

**Symptom:** none reported — found by reading `bartholomew/kernel/retrieval.py` and
`hybrid_retriever.py` while investigating Runtime Convergence gaps; two `# TODO: Check
memory_consent table` comments (`retrieval.py:318`, `hybrid_retriever.py:719`) were the tell.

**Root cause:** three retriever classes each ran their own rules-engine pass over
`requires_consent` memories and excluded them unconditionally, regardless of the real
`memory_consent` table — `Retriever._should_include()` and `FTSOnlyRetriever._evaluate_rules()`
in `retrieval.py`, and `HybridRetriever._evaluate_rules()` in `hybrid_retriever.py` (the
vector-only, FTS-only, and hybrid retrieval modes respectively — i.e. all three). This
duplicated, weaker logic sat *on top of* `FTSClient.search()`/`VectorStore.search()`'s own
`apply_consent_gate=True` default, which correctly reads `memory_consent` via
`ConsentGate.get_consented_memory_ids()` one layer down. Net effect: a memory a user had
genuinely consented to (a real `memory_consent` row, e.g. from `MemoryStore.upsert_memory()`'s
embedding flow) already passed the lower-level `ConsentGate` check, then got silently
re-excluded by the retriever's own redundant pass anyway. Fail-closed (no privacy leak) but the
consent-granting feature was non-functional for retrieval in all three modes — this project's
own privacy-first non-negotiable ("Consent gating for 'ask before store' classes... must be
enforceable and testable") wasn't actually true for the retrieval side of consent.

**Fix:** all three methods now accept a `consented_ids: set[int]` (loaded once per
call via `ConsentGate(db_path).get_consented_memory_ids()`, matching the existing pattern
`ConsentGate.filter_memory_ids()` already uses) and only exclude a `requires_consent` memory
when its ID isn't in that set — reusing the one authoritative consent-check
(`ConsentGate`/`memory_consent` table) instead of a fourth reimplementation. Defaults to the
old fail-closed behavior when no `consented_ids` is passed, so nothing regresses for any
caller that doesn't opt in.

**Acceptance:** a `requires_consent` memory with a real `memory_consent` row is retrievable
in all three modes; one without a row stays excluded; `never_store` (`allow_store=false`)
memories stay excluded regardless of consent.

**Verify:** `pytest -q tests/test_retrieval_consent_enforcement.py tests/test_consent_gates.py
tests/test_fts_search.py tests/test_hybrid_boosts_flip.py tests/test_hybrid_fusion_math.py
tests/test_hybrid_recency.py tests/test_hybrid_rrf.py tests/test_hybrid_tiebreakers.py
tests/test_retrieval_factory.py tests/test_retrieval_fts5_fallback.py
tests/test_retrieval_hot_reload.py tests/test_kernel_privacy_guard.py
tests/test_phase2d_embeddings.py tests/test_working_memory.py test_memory_functionality.py` —
all passed locally (clean venv; see item 11.6's note on the sandbox's system-Python
`cryptography` conflict, unrelated to this change).

---

## RISKS.md R1 (consent bypass / privacy leakage): red-team test suite — added 2026-07-24

**The gap:** R1's own status note (2026-07-21) had already audited every `apply_consent_gate`
call site by grep (no production bypass found) and fixed the fail-*closed* bug above, but named
one mitigation still open: "No red-team test suite for bypass paths exists yet."

**Why not through `MemoryStore.upsert_memory()`:** `MemoryRulesEngine.should_store()` already
hard-blocks any `requires_consent` memory at write time (not "store but hide from retrieval" —
see that method's own docstring: "Block memories that require consent until explicit consent is
captured and a separate promotion path is used"). So the scenario R1 actually names — a memory
that *should* be excluded already existing and being retrieved — can only be reproduced the way
it would happen for real: content that reaches the `memories`/FTS/vector tables some other way (a
`memory_rules.yaml` reclassification after the fact, a migration, direct DB access, a future
write-path bug), not via the normal write path. `tests/test_consent_bypass_redteam.py` lands
content that way, then drives it through every production retrieval surface a real caller would
use.

**What the suite proves (10 tests):**
- `get_retriever()`'s three modes (hybrid/vector/fts) never surface an unconsented
  `ask_before_store` memory, and correctly still surface a consented one (regression guard for
  the fail-closed bug fixed above) and a plain one, from a single query that makes all three
  raw candidates.
- `HybridRetriever(db_path=...)` and `FTSOnlyRetriever(db_path=...)` constructed directly with no
  `rules_engine` — the exact construction each class's own docstring usage example shows — still
  never leak. This surfaced a genuine structural finding (not a bug): both classes skip their own
  optional rules-engine re-filtering entirely when `rules_engine` is `None` (`if self.rules_engine:`
  in each `retrieve()`), but the `ConsentGate` baked unconditionally into
  `FTSClient.search()`/`VectorStore.search()` (`apply_consent_gate=True` by default, one layer
  down) still holds regardless. Confirmed genuine, not vacuous, by deliberately breaking
  `ConsentGate.filter_memory_ids()` to include everything and watching exactly these two "no
  rules_engine" tests fail — and no others, including the `get_retriever()` factory tests, which
  stayed green because `hybrid`/`fts` mode's own second-layer rules-engine check independently
  caught it. That's defense-in-depth working as designed, not a test-quality problem.
- A `never_store` memory smuggled directly into the DB (bypassing the write-time guard
  entirely) is still never surfaced by `HybridRetriever` or a direct `FTSClient.search()` call.
- Revoking consent (deleting the `memory_consent` row) mid-session immediately re-excludes the
  memory on the same retriever instance's next call — no caching/staleness bypass.
- Two now-permanent regression guards, replacing what used to be one-time manual audits: an
  AST-based repo scan (`bartholomew/`, `bartholomew_api_bridge_v0_1/`, `identity_interpreter/`)
  proving no production call site ever passes `apply_consent_gate=False`, and a signature check
  proving no public `.retrieve()` facade (`HybridRetriever`, `FTSOnlyRetriever`,
  `VectorRetrieverAdapter`) exposes a parameter capable of disabling the gate at all.

**One residual nuance surfaced, left as a documented observation, not fixed this pass:**
`get_retriever(mode="vector")` doesn't pass a `memory_store` through by default, so
`Retriever._load_memory()` returns a content-less stub (`{"id": ..., "kind": "unknown"}`) for that
mode, degrading its own second-layer rule check to a no-op for content-based rules specifically.
Harmless under today's code — the first-layer `ConsentGate` inside `VectorStore.search()` gates
unconditionally regardless — but "vector" mode has one fewer redundant layer than "hybrid"/"fts"
if that first layer were ever independently broken. No test requires fixing it and doing so was
outside this session's scope; flagged in RISKS.md's R1 entry for a future pass.

**Acceptance:** no production retrieval surface — factory-constructed or built directly per a
class's own documented usage — ever surfaces a `never_store` or unconsented `ask_before_store`
memory; the fail-closed regression (consented memories wrongly excluded) stays caught; the
bypass knob (`apply_consent_gate=False`) is now a permanent, tested non-event in production code.

**Verify:** `pytest -q tests/test_consent_bypass_redteam.py tests/test_consent_gates.py
tests/test_retrieval_consent_enforcement.py` — 28 passed, 1 pre-existing conditional skip
unrelated to this change (`test_consent_gates.py`'s own `pytest.skip("Memory blocked by privacy
guard...")` guard); full `pytest -q` — clean (zero failures/errors; one flaky, unrelated
`test_wal_cleanup_concurrent_processes` failure seen once under full-suite concurrency load and
not on retry, tracked separately as item 11.18's own open tech debt, not this change).

---

## P2.5 — Runtime Convergence (architectural prerequisite) ✅ Complete 2026-07-24 (item 11.22)

> **Source:** Architect review, 2026-07-21, responding to the P2 skill-registry wiring
> write-up above and the grounded architectural audit that preceded it (subsystem map,
> observation/action pipeline trace, and import/dependency graph — all confirmed by direct
> code reading, not inference).

**The finding:** the audit confirmed the project effectively has "two brains" today
(`bartholomew/kernel` and `identity_interpreter/`) — no longer competing conceptually, but
still duplicated structurally (four concrete pairs: model routing, persona, permission gates,
kill-switch — see item 11.1 below). It also confirmed `Identity.yaml` governs only the chat
path; the autonomous kernel/scheduler/skill-execution path never consults it, so two execution
paths can produce different behavior — an architectural inconsistency ("Bartholomew
should have one personality, not one personality per interface"), not primarily a safety bug.
Separately, the Experience Kernel/Narrator/Working Memory ("Living Device" continuity — affect,
attention, goals, persistent narration) are fully built but never reachable from chat — called
"the single biggest opportunity in the entire repository."

**Governing principles for this milestone:**
- **Principle Zero** (governs flow): *"Every external stimulus and every internally generated
  initiative must traverse the same cognitive loop before execution."*
- **Principle One — Uniform Cognition** (governs decision-making): *"Every decision, regardless
  of origin, is made by the same cognitive architecture."* Zero ensures everything enters the
  same loop; One ensures everything is decided by the same mind.
- **Architectural Invariant:** *"Every architectural responsibility has exactly one
  authoritative owner. All other implementations are adapters, compatibility layers, or
  deprecated migrations."*

**Runtime Contract:** every interaction — chat, voice, vision, email, calendar, scheduler,
webhook, background daemon, future sensors — enters through:

```
Observation -> Interpretation -> Executive -> Governance -> Capability -> Execution -> Reflection -> Memory
```

No exceptions. Not even chat.

11.1. **Authority ownership for duplicated concepts**
    - Owner table separates authoritative ownership from implementation (implementations stay
      replaceable; ownership stays stable):

      | Concept | Authoritative Owner | Implementations |
      |---|---|---|
      | Identity | Identity System (`identity_interpreter`) | YAML today, database tomorrow |
      | Planning | Kernel Executive | `daemon.py`/`planner.py`/`scheduler/*` |
      | Memory | Memory Substrate | SQLite now, Postgres later |
      | Experience | Experience Kernel | Narrator, Working Memory, Affect |
      | Governance | Governance (ParkingBrake + `skill_permissions.py`) | — |
      | Capabilities | Skill Registry | Local skills, remote services, future MCP |
      | Conversation | Chat Surface | — |

    - Four duplicate pairs found by direct grep, each needing exactly one authoritative
      side: model routing (`identity_interpreter/orchestrator/model_router.py`, actually used,
      vs. `identity_interpreter/policies/model_router.py`, CLI-only); persona
      (`bartholomew/kernel/persona_pack.py`, wired into Narrator/ExperienceKernel, vs.
      `identity_interpreter/policies/persona.py`, chat-only); permission gates
      (`bartholomew/kernel/skill_permissions.py` vs. `identity_interpreter/policies/tool_policy.py`);
      kill-switch (`bartholomew/orchestrator/safety/parking_brake.py`, persistent/wired into
      four live gate points, vs. `identity_interpreter/adapters/kill_switch.py`, print-only,
      unwired).
    - **Do not delete first.** Mark the non-authoritative side deprecated (docstring +
      comment), route new callers through the winner, delete only once nothing depends on
      the loser.
    - **Acceptance:** each pair has a documented, single authoritative owner (recorded in
      `DECISIONS.md`); the deprecated side carries an explicit deprecation notice; no caller
      regressions.
    - **Verify:** full `pytest -q` stays green; no new callers added to a deprecated module
      after this point.

11.2. **Identity Context -> Executive -> Policy Decision** — ✅ implemented 2026-07-21
    (skill-execution only; scope corrected from the original scheduler-drive plan — see below)
    - Identity does not answer "what should I do?" — it answers "who am I?" It publishes a
      declarative **Identity Context** (values, red lines, behavioral constraints,
      preferences, communication style, risk profile, decision heuristics, goals). The
      **Executive** consumes that context and is what constructs the actual **Policy
      Decision** — not Identity, and not the Kernel parsing YAML directly.
    - Closes the "`Identity.yaml` governs only chat" gap for skill execution: added
      `identity_interpreter/identity_context.py` (`IdentityContext` +
      `build_identity_context()`) and `bartholomew/kernel/policy_engine.py`
      (`PolicyDecision` + `evaluate_tool_policy()`). `SkillRegistry.execute_action()` now
      consults it via an optional `identity_context` constructor param (default `None` — no
      behavior change unless wired). `daemon.py` gained an optional `identity_path` param that
      loads `Identity.yaml` once and shares the built context; the live API bridge
      (`bartholomew_api_bridge_v0_1/services/api/app.py`) wires it in by default.
    - **Corrected during implementation (real regression found and reverted):** the original
      plan also wired `scheduler/loop.py`'s `_run_drive()` to the same
      `evaluate_tool_policy()` check, using each drive's `task_id` (e.g. `"self_check"`)
      against `Identity.yaml`'s `tool_use.allowlist`. This was a category error — internal
      scheduler drives are kernel self-maintenance functions, not "tools" in the
      `tool_use.allowlist` sense, and the real `Identity.yaml`'s allowlist (`web_fetch`,
      `browser_action`) never includes drive task_ids. Wiring it this way denied every
      scheduler drive by default in production the moment `identity_path` was passed, and the
      scheduler's retry loop doesn't back off on denial (a denied, 0-duration drive is
      immediately re-due) — this busy-looped and starved the asyncio event loop badly enough
      that the live FastAPI app never answered its first `/healthz` request. Caught by the
      `smoke` CI check on PR #10, reproduced locally (`uvicorn app:app` hangs with
      `curl: (7) Failed to connect`), root-caused, and fixed by removing the scheduler-side
      check entirely — the Policy Decision mechanism applies to skill/capability execution,
      not the scheduler's internal drives.
    - **Acceptance:** a single skill-execution path demonstrably respects an `Identity.yaml`
      tool-use rule change (allowlisting/de-allowlisting a skill_id flips
      `SkillRegistry.execute_action()`'s outcome for that skill).
    - **Verify:** `pytest -q tests/test_runtime_convergence_policy.py`.

11.3. **Runtime Contract as a code seam** — ✅ implemented 2026-07-21 (chat, not yet wired as
    `/api/chat`'s live default)
    - The Observation -> Interpretation -> Executive -> Governance -> Capability -> Execution
      -> Reflection -> Memory shape becomes real code, starting with chat + skill-execution.
      Voice/sight/other sensors remain future work (see `ROADMAP.md` Stage 6) but must be able
      to plug into the same seam later without a redesign.
    - Added `bartholomew/kernel/runtime_contract.py`:
      `run_chat_through_runtime_contract(daemon, user_input, respond_fn)` — a directly
      callable function tracing every stage (`Observation`, `Interpretation`,
      `CandidateAction`, a `ParkingBrake("skills")` Governance check, then
      Capability/Execution via the injected `respond_fn`, a Working Memory `Reflection`
      entry, and Memory durability via `WorkingMemoryManager`'s existing
      snapshot/persistence).
    - **Deliberately not wired as `/api/chat`'s default behavior in this change** — item
      11.2's scheduler-drive regression (see DECISIONS.md) showed that flipping a live
      production default needs its own dedicated live-smoke-verification pass, not just
      `pytest`; wiring this into the actual chat endpoint is a tracked follow-up (see item
      11.4), done separately and smoke-tested on its own.
    - **Acceptance:** a chat input produces a Working Memory entry and a distinct
      candidate-action representation before any execution, with each stage separately
      logged/testable.
    - **Verify:** `pytest -q tests/test_runtime_contract_chat_seam.py`.

11.4. **Wire chat into the Experience Kernel ("Living Device")** — ✅ implemented 2026-07-21
    - `/api/chat` (`bartholomew_api_bridge_v0_1/services/api/app.py`) now routes through
      `run_chat_through_runtime_contract()` (item 11.3) when the kernel is running, so
      Working Memory and the Experience Kernel's active goals/persona actually reach chat —
      falls back to the prior, unwrapped `orch.handle_input()` call only when the kernel
      isn't available (e.g. narrow startup/shutdown windows), preserving that edge case
      exactly.
    - `runtime_contract.py`'s Interpretation stage was extended to read
      `daemon.experience.get_active_goals()` and `daemon.persona_manager.get_active_pack_id()`
      and fold them into the prompt actually sent to the chat backend — this is what makes
      "can reference persisted persona/goal state" genuinely true rather than just
      structurally possible. Governance denial now returns a proper `503`, not an unhandled
      `500` (no existing test pinned the old status code — confirmed by grep before changing
      it).
    - **Real bug found and fixed during live-smoke verification (not caught by `pytest`
      alone):** the enriched prompt's own `"User: "` prefix collided with the existing chat
      orchestrator's `inject_memory_context()` step, which applies its *own* `"\n\nUser: "`
      wrapping around whatever prompt it receives — producing a visibly doubled
      `"User: ... User: ..."` prefix in the actual HTTP response. Fixed by dropping the
      redundant label from `runtime_contract.py`'s own contribution and letting the
      downstream backend apply it once. Caught only by curling the live `/api/chat` endpoint
      directly, not by any unit test — same discipline (verify against a running app, not
      just `pytest`) established after item 11.2's regression.
    - **Acceptance:** a chat turn observably updates working memory and can reference
      persisted persona/goal state from a previous turn.
    - **Verify:** `pytest -q tests/test_runtime_contract_chat_seam.py
      tests/test_api_chat_runtime_contract.py`.

11.5. **Author `COGNITIVE_RUNTIME.md`** — ✅ implemented 2026-07-21
    - The canonical document defining the cognitive loop, runtime invariants, the ownership
      table, and the execution/observation/reflection/memory lifecycles, plus governance
      checkpoints — the answer to "how does Bartholomew think?" Added to the Canonical docs
      list above.
    - Written by reading the actual implementation (`runtime_contract.py`,
      `experience_kernel.py`, `narrator.py`, `working_memory.py`, `persona_pack.py`,
      `policy_engine.py`, `identity_context.py`, `parking_brake.py`, `skill_registry.py`), not
      from the plan narrative alone — this surfaced gaps not previously written down in one
      place: chat's Governance stage checks only `ParkingBrake`, not the Identity Context →
      Policy Decision path item 11.2 wired for skill execution; chat and skill execution each
      produce a durably-persisted but structurally different Reflection record (a Working
      Memory item vs. a `skill_action_audit` row) rather than one unified shape; and the
      Exit Gate's seven questions are mostly "partial," not "yes" — documented honestly in the
      new doc's "Exit Gate status" table rather than glossed over.
    - **Acceptance:** the doc exists, is added to the Canonical docs list, and every claim in
      it is traceable to a specific file/function rather than restating the plan.
    - A later, separate document, `ARCHITECTURAL_INVARIANTS.md` (Principle Zero, Principle
      One, one authority per concept, fail-closed, memory is consent-gated, every decision is
      explainable, etc. — the rules meant to survive any future rewrite) remains a *future*
      addition, not part of this item.

11.6. **Wire chat's Governance stage into the Policy Decision check** — ✅ implemented 2026-07-21
    - Closes the gap `COGNITIVE_RUNTIME.md` (item 11.5) named: chat's Governance stage
      (`runtime_contract.py`) previously checked only `ParkingBrake`, not the Identity Context
      → Policy Decision path item 11.2 already wired for `SkillRegistry.execute_action()`.
    - **A real regression risk found and avoided before it shipped, same discipline as item
      11.2's scheduler-drive revert:** naively evaluating chat's `"chat_response"`
      `CandidateAction` against `evaluate_tool_policy()` like any other tool name would have
      denied every chat turn in production the instant it landed. Confirmed by direct reading
      of `Identity.yaml`'s real `tool_use` section (`default_allowed: false`, `allowlist:
      [web_fetch, browser_action]` — no conversational entry) and
      `bartholomew_api_bridge_v0_1/services/api/app.py`, which already constructs
      `KernelDaemon(identity_path="Identity.yaml")` by default — so `daemon.identity_context`
      is never `None` in the live API. `evaluate_tool_policy()`'s own docstring says its
      `tool_name` param means "a skill_id or scheduler drive task_id" — conversation is
      neither, the same category error item 11.2 made for scheduler drives.
    - **Fix:** added `_CONVERSATIONAL_KINDS` (currently `{"chat_response"}`, the only kind the
      chat seam produces today) to `runtime_contract.py`. The Governance stage now consults
      `policy_engine.evaluate_tool_policy(daemon.identity_context, candidate_action.kind)`
      whenever an `IdentityContext` is wired in and the kind isn't in that exempt set — real
      today for any future tool/skill-shaped candidate action a chat turn might propose,
      correctly inert for plain conversation, mirroring the scheduler-drive exemption's
      reasoning rather than repeating its mistake.
    - **Acceptance:** a chat turn with a restrictive `IdentityContext`
      (`tool_use_default_allowed=False`, empty `allowlist`) still succeeds
      (`governance_allowed=True`) — proven by
      `TestChatGovernanceConsultsPolicyDecision.test_chat_not_denied_by_a_restrictive_tool_use_policy`.
      Behavior for daemons that don't wire an `IdentityContext` at all is unchanged
      (`test_skipped_entirely_when_no_identity_context_wired`).
    - **Verify:** `pytest -q tests/test_runtime_contract_chat_seam.py
      tests/test_api_chat_runtime_contract.py tests/test_runtime_convergence_policy.py` — 21
      passed locally (venv with `requirements.txt` + `requirements-dev.txt` + `pip install -e
      .`; the sandbox's system Python has an unrelated pre-existing `cryptography` package
      conflict blocking `pip install -e .` directly, unrelated to this change).

11.7. **Wire recent conversation history into chat's Interpretation stage; found and flagged
    a 5th duplicated-memory-injection concept** — ✅ implemented 2026-07-21
    - Closes a gap item 11.4 didn't: goals/persona reached the prompt, but no prior turn's
      own *content* did. `runtime_contract.py`'s `_build_interpretation()` now also folds in
      `daemon.working_memory.get_context_string()` (called before this turn's own Reflection
      write, so it only ever contains prior turns) — a chat turn can now genuinely answer "what
      did I just tell you?" using only what a prior turn wrote into Working Memory.
    - **Found while investigating:** `identity_interpreter.orchestrator.context_builder.
      ContextBuilder` was clearly *meant* to be the thing injecting prior conversational memory
      into chat's prompt (backed by `identity_interpreter/adapters/memory_manager.py`'s
      `MemoryManager`, which itself has two stale `# TODO Phase 2b/2c` comments for encryption/
      summarization). Traced it and confirmed it's dead code in production: `ContextBuilder`
      only builds a `MemoryManager` when given a non-`None identity_config`, and
      `bartholomew_api_bridge_v0_1/services/api/app.py` constructs `Orchestrator()` with none
      — so `build_prompt_context()` always returns `""` today. This is a fifth duplicated
      concept (conversational-memory injection) beyond the four item 11.1 already found, just
      never exercised at runtime so it never surfaced as a behavioral conflict. Documented
      in-place (module docstrings in both files) rather than adding a formal ownership-table
      row for a concept this narrow; the authoritative implementation is now `runtime_contract.
      py` + `WorkingMemoryManager`, per COGNITIVE_RUNTIME.md's ownership table's existing
      "Experience" entry. Did not implement the stale TODOs — see the docstring note explaining
      why (the authoritative Memory Substrate, `bartholomew.kernel.memory_store.MemoryStore`,
      already does both, live).
    - **Acceptance:** a second chat turn's prompt contains a prior turn's own raw content
      (not just goals/persona) — proven by
      `test_second_turn_prompt_references_first_turns_own_content`.
    - **Verify:** `pytest -q tests/test_runtime_contract_chat_seam.py
      tests/test_api_chat_runtime_contract.py tests/test_runtime_convergence_policy.py
      tests/test_working_memory.py` — 25 passed locally.

11.8. **Reconcile the two reflection-narrative pipelines** — ⚠️ **additively appended, not
    architecturally unified** (implemented 2026-07-21; framing corrected 2026-07-28)
    - Closed ROADMAP.md Stage 3's then-"Still open" note. `daemon.py`'s `_run_daily_reflection()`/
      `_run_weekly_reflection()` generated content exclusively via `identity_interpreter.
      adapters.reflection_generator.ReflectionGenerator` (or a generic template fallback) — its
      own "Notable Events" section literally reads `(Future: chat highlights, emotional
      events, user activities)`, a placeholder. Meanwhile `narrator.py`'s `NarratorEngine`
      already builds a real narrative from actual persisted episodes (affect/attention/drive/
      goal/observation) via `generate_daily_reflection_narrative()`/
      `generate_weekly_reflection_narrative()` — fully built, independently tested (`tests/
      test_narrator.py`), but confirmed by grep to have **zero callers anywhere in the repo
      except tests** before this change. The persisted/exported daily and weekly reflections
      never reflected anything that actually happened.
    - **Fix:** both methods in `daemon.py` now call the corresponding narrator method after
      `ReflectionGenerator` produces its content, and append the result (`content = f"{content}
      \n\n---\n\n{episodic_narrative}"`) rather than replacing or parsing either pipeline's
      output — the safer integration, since both remain independently correct and neither
      needed to change shape. Never lets a narrator error break reflection generation (wrapped
      in its own `try`/`except`, matching this file's existing fail-safe pattern for the
      `ReflectionGenerator` call itself).
    - **Correction added 2026-07-28:** at the time this item landed, it was described elsewhere
      as "reconciled." That word is retained above only in scare-quotes context; it should be
      read precisely as **concatenation of two independent pipelines' outputs**, not as
      establishing a single architectural authority. Per the reflection-ownership decision now
      recorded in `COGNITIVE_RUNTIME.md`, the **approved target architecture** is:
      `ReflectionGenerator` is the authoritative owner of reflection composition/output, and
      `NarratorEngine`'s episodic narrative is supplementary evidence appended to it — not an
      independent, co-equal pipeline. The code as of this item's landing does not yet enforce
      that ownership model (both pipelines still run unconditionally and independently); closing
      that gap is a separately-authorised future code change, not done here or since.
    - **Acceptance (as originally written):** a daily/weekly reflection persisted after a real
      episode (e.g. a goal added via `ExperienceKernel.add_goal()`) contains that episode's own
      content, and `meta["episodic_narrative_included"]` is `True`; the weekly reflection's
      existing "Identity Core Alignment" safety-audit section is unchanged (append-only, not
      clobbered).
    - **Verify:** `pytest -q tests/test_reflection_narrative_integration.py` — 4 passed
      locally; `pytest -q tests/test_stage3_integration.py tests/test_stage0_alive.py
      tests/test_narrator.py tests/test_stage1_api_endpoints.py` — 140 passed (no
      regressions).

11.9. **Scenario replay test harness + a real restart-persistence bug it found** —
    ✅ implemented 2026-07-21
    - Closes the "dedicated scenario replay test harness (distinct from `tests/
      test_stage3_integration.py::TestFullLifecycle`)" item this backlog previously marked
      deliberately out of scope. `TestFullLifecycle` hand-wires individual kernel modules
      together directly; `tests/test_scenario_replay.py` instead drives a real `KernelDaemon`
      through the actual `run_chat_through_runtime_contract()` seam across one continuous
      multi-turn session (chat -> goal added -> a later turn referencing both the goal and a
      prior turn's own content -> persona switch observed next turn -> parking brake blocking
      and recovering -> daily reflection capturing the real episode -> a simulated restart).
    - **A real, previously-live bug found while writing it:** `daemon.py`'s
      `_init_experience_kernel()` called `self.experience.load_last_snapshot()`, printed
      `"[Kernel] Restored experience state from last snapshot"`, and then did nothing else with
      the result — `load_last_snapshot()` only loads and returns a `SelfSnapshot`; applying it
      is a separate method, `ExperienceKernel.restore_from_snapshot()`, that nothing was
      calling. Every daemon restart silently reset drives/affect/attention/active_goals to
      fresh-instance defaults while logging that it had restored them — the log message was
      never true. (`WorkingMemoryManager.load_last_snapshot()`'s equivalent path was already
      correct — it calls `self.restore(snapshot)` internally — so this bug was isolated to
      `ExperienceKernel`.) Same bug class as the 2026-07-20 "silently-swallowed `AttributeError`
      disabled the kernel's entire tick loop" fix in this same file (see "Experience Kernel
      MVP: bug fix + privacy gap" below) — another log message asserting something the code
      didn't actually do.
    - **Fix:** one line — `_init_experience_kernel()` now calls
      `self.experience.restore_from_snapshot(snapshot)` before printing the (now-true) message.
    - **Also confirmed, not a bug:** `PersonaPackManager` has no persisted "active pack" state
      at all (`persona_switch_log` is an audit trail, not restorable state) — every boot
      intentionally activates the default pack via `_init_experience_kernel()`'s own "activate
      default if none active" logic. The scenario test asserts this explicitly rather than
      assuming persona should survive restart.
    - **Acceptance:** a goal added before a simulated restart (`persist_snapshot()` + a second
      `KernelDaemon` instance against the same db) is present in
      `daemon2.experience.get_active_goals()` and is referenced in a post-restart chat turn's
      prompt.
    - **Verify:** `pytest -q tests/test_scenario_replay.py` — 2 passed locally;
      `pytest -q tests/test_stage3_integration.py tests/test_experience_kernel.py
      tests/test_stage0_alive.py tests/test_reflection_narrative_integration.py
      tests/test_runtime_contract_chat_seam.py tests/test_api_chat_runtime_contract.py
      test_kernel_alive.py` — 96 passed (no regressions).

11.10. **Fix five previously-live 500s in the `self_state` API router; add its first
    HTTP-level test file** — ✅ implemented 2026-07-21
    - `bartholomew_api_bridge_v0_1/services/api/routes/self_state.py` (`/api/self/*`,
      `/api/persona/*`, `/api/episodes/*`) had **zero HTTP-level tests anywhere in the repo**
      before this change — everything else touching this area
      (`tests/test_runtime_contract_chat_seam.py`, `tests/test_scenario_replay.py`) calls
      `daemon.experience.add_goal()` etc. directly, never through the actual routes. Writing
      `tests/test_self_state_api.py` (the first such file) found five call sites that had
      drifted from the real `ExperienceKernel`/`NarratorEngine` method signatures they call —
      every one of these was a genuine, currently-live bug, not a hypothetical:
      - `PUT /api/self/attention` — passed `attention_type=`/`context_tags=`; the real method
        is `set_attention(target, focus_type, intensity, tags)`. **Always raised `TypeError`.**
      - `GET /api/self/drives` and `GET /api/self/drives/top` — passed `limit=`; the real
        method is `get_top_drives(n=3)`. **Always raised `TypeError`.**
      - `POST /api/self/drives/{id}/activate` — passed `amount=`; the real method is
        `activate_drive(drive_id, boost=0.0)`. **Always raised `TypeError`.**
      - `POST /api/self/drives/{id}/satisfy` — passed `amount=`; the real method,
        `satisfy_drive(drive_id)`, takes no second parameter at all (a fixed, unparameterized
        reduction). **Always raised `TypeError`.**
      - `GET /api/episodes/by-type/{episode_type}` — passed the raw URL path string straight
        to `NarratorEngine.get_episodes_by_type()`, which calls `.value` on it internally,
        assuming an `EpisodeType` enum. **Always raised `AttributeError`.**
      - (Also fixed as part of the same investigation, see item above this one's sibling in
        spirit: `POST /api/self/goals` always returned `"added": null` —
        `ExperienceKernel.add_goal()` had no return statement. Now returns `bool`, mirroring
        `complete_goal()`'s existing contract. No caller anywhere used the old `None` return
        value, confirmed by grep, so this was safe to change.)
    - **Separately confirmed, not fixed (out of scope, a deeper pre-existing gap):**
      `daemon.py` constructs `ExperienceKernel(db_path=..., workspace=...)` with no
      `identity_path` — so `ExperienceKernel` always falls back to its own hardcoded
      `DEFAULT_DRIVES` list; neither `config/drives.yaml` nor `Identity.yaml`'s
      `identity.self_model.drives` ever reaches it. `daemon.py` separately loads
      `config/drives.yaml` into `self.drives` for the `Planner`, entirely disconnected from
      `ExperienceKernel`'s own drive list — a second "drives" concept with two independent
      configs and no shared source of truth. Noted here rather than fixed; a real design
      question (should `ExperienceKernel` read `Identity.yaml`'s drives?) that deserves its own
      scoped decision, not a same-session fix bundled into a route-signature bugfix pass.
    - **Test-isolation finding along the way:** `app_module`'s module-level `_kernel` is a
      process-wide singleton shared by every test file that imports
      `bartholomew_api_bridge_v0_1.services.api.app` — `tests/test_self_state_api.py`'s new
      goal/persona-mutating tests initially leaked state into
      `tests/test_api_chat_runtime_contract.py`'s assertions when both ran in the same pytest
      session (confirmed: goals from one file's tests showed up in the other file's chat
      response). Fixed with explicit cleanup (an autouse fixture removing every goal the class
      creates; the persona-switch test restores whatever pack was active before it ran).
    - **Acceptance:** every route in `self_state.py`'s drives/attention/episodes-by-type
      surface returns a real response instead of a 500; `POST /self/goals`'s `added` field is
      accurate.
    - **Verify:** `pytest -q tests/test_self_state_api.py` — 14 passed locally;
      `pytest -q tests/test_self_state_api.py tests/test_api_chat_runtime_contract.py
      tests/test_stage1_api_endpoints.py tests/test_stage0_alive.py
      tests/test_experience_kernel.py tests/test_narrator.py tests/test_scenario_replay.py
      tests/test_runtime_contract_chat_seam.py` — 197 passed (no regressions, no
      cross-file leakage).

11.11. **Wire `NarratorEngine.search_episodes()` into a real route** — ✅ implemented 2026-07-21
    - Generalizing item 11.10's ASSUMPTIONS.md A1b finding ("audit for public methods with
      zero test coverage, not just zero *failures*"), swept every public method in
      `narrator.py` for external callers by grep. Everything else checked out (private
      handler methods correctly called only internally; `set_persona_manager()` is genuinely
      dead code now that `daemon.py` passes `persona_manager` at construction instead — its
      own docstring names the ordering problem that made it necessary, which no longer
      applies; left alone, not worth a same-session cleanup). `search_episodes()` stood out:
      a fully-built, independently-tested FTS5 full-text search over episodic entries (with
      its own graceful LIKE fallback already built in) that no API route exposed at all.
    - Added `GET /api/episodes/search` (`self_state.py`) — `q`, `limit`, optional
      `episode_type`/`tone` filters (validated against the real enums, 400 on an unrecognized
      value, same pattern as item 11.10's `get_episodes_by_type` fix). **Must be registered
      before `GET /episodes/{episode_id}`** in the file — both are single-segment paths under
      `/episodes/`, and FastAPI/Starlette resolves in registration order, so the existing
      path-param route would otherwise greedily match `"search"` as an `episode_id`. Placed
      it there and added a regression test asserting the ordering holds.
    - **Acceptance:** a goal added via `ExperienceKernel.add_goal()` is findable by its own
      text through `GET /api/episodes/search?q=...`.
    - **Verify:** `pytest -q tests/test_self_state_api.py` — 18 passed locally (4 new);
      `pytest -q tests/test_self_state_api.py tests/test_api_chat_runtime_contract.py
      tests/test_narrator.py tests/test_stage1_api_endpoints.py` — 128 passed (no
      regressions).

11.12. **Retire the deprecated persona module — migrate its two legacy callers to
    `PersonaPackManager`, then delete it** — ✅ implemented 2026-07-22
    - Completes the first of item 11.1's four duplicate pairs through the full
      deprecate → migrate → delete cycle. `identity_interpreter/policies/persona.py`
      (`get_persona_config()` and the never-exported `get_style_guidelines()`/
      `should_adjust_tone()`) had exactly two live callers, confirmed by grep: `chat.py`'s
      standalone script (`get_response()`'s system prompt) and `identity_interpreter/cli.py`'s
      `explain` command. Both now read persona *tone* from the authoritative
      `bartholomew.kernel.persona_pack.PersonaPackManager`'s active pack — the same object the
      live daemon/Narrator/ExperienceKernel use — so every text interface now shows the
      kernel's active-pack tone rather than `Identity.yaml`'s `persona.tone` field.
    - **The one real design question this surfaced, resolved deliberately, not glossed:**
      `PersonaPack` (the authoritative unit) has **no `traits` field** — packs model tone,
      style, drive-boosts, and narrative overrides (the *switchable presentation*), not the
      being's stable character. The deprecated function conflated both by reading everything
      off `Identity.yaml`'s `persona` block. So the migration split the two concerns along the
      ownership table's existing boundary: **tone/style → `PersonaPackManager`** (switchable,
      matches the live kernel), **`traits` → `Identity.yaml` via `identity.persona.traits`
      directly** (stable identity descriptor, not persona-pack state). This is the correct
      separation — persona packs own *how* Bartholomew presents; Identity owns *who* it is —
      not a residual duplication. Recorded in `DECISIONS.md`'s "One authority per architectural
      concept" entry. **Reaffirmed and extended 2026-07-28** by the personality/Constitution
      split recorded in `CONSTITUTION.md`/`DECISIONS.md`: the "1950s gentleman" aesthetic is a
      default persona-pack inspiration, not a constitutional invariant.
    - Both callers fall back to the built-in `create_default_pack()` when no pack is active
      (e.g. launched outside the repo root, where `config/persona_packs/*.yaml` isn't found),
      so tone is never empty. `identity_interpreter/cli.py` importing
      `bartholomew.kernel.persona_pack` is the intended dependency direction (an interface
      depending on the authoritative core) and introduces no cycle — `persona_pack.py` imports
      nothing from `identity_interpreter` (confirmed by reading its imports).
    - Removed `get_persona_config` from `identity_interpreter/policies/__init__.py`'s exports,
      deleted `tests/test_policies.py::test_persona_config` (tested only the deleted function;
      the authoritative system keeps its own coverage in `tests/test_persona_pack.py`), and
      `git rm`'d `identity_interpreter/policies/persona.py`.
    - **Exit-gate impact:** moves question #7 ("does every interface expose the same
      personality?") from "No" to "closer, still partial" — every *text* interface now shares
      the active pack's tone; voice/sight adapters still don't consult persona at all (Stage 6),
      which is what keeps it short of a full "yes."
    - **Acceptance:** `identity_interpreter/policies/persona.py` no longer exists; `chat.py` and
      `cli.py`'s `explain` both source tone from `PersonaPackManager.get_tone()`; no caller
      anywhere imports the removed module.
    - **Verify:** `pytest -q tests/test_policies.py tests/test_persona_pack.py` — 59 passed
      locally; `python -m identity_interpreter.cli explain Identity.yaml` prints the active
      pack's tone (`warm, helpful, kind, curious`) with traits still from Identity; `ruff check`
      and `black --check` (pinned 25.9.0) clean on all four touched files.

11.13. **Delete the deprecated kill-switch adapter — the third of item 11.1's four pairs to
    fully retire** — ✅ implemented 2026-07-22
    - (Sibling item 11.12 — the persona pair — landed separately, first; this is a parallel
      slice, so the two items merge into the 11.11 → 11.12 → 11.13 sequence from independent
      branches.) Unlike the persona and (future) model-routing pairs, the kill-switch pair
      needed **no caller migration at all** — `identity_interpreter/adapters/kill_switch.py`'s
      `KillSwitch` class was print-only and, as item 11.1 already recorded, **unwired with zero
      live callers**. Re-confirmed exhaustively before deleting: the only references to the
      symbol anywhere were the module itself and its re-export in
      `identity_interpreter/adapters/__init__.py` (import + `__all__`). The unrelated
      `KillSwitch` **in `identity_interpreter/models.py` is a different class** — a Pydantic
      schema model for the `Identity.yaml` `safety_and_alignment.controls.kill_switch` field,
      not the adapter — and stays untouched; every other `kill_switch` mention in the tree
      (`chat.py`, `loader.py`) reads that Identity field, not the adapter.
    - **The authoritative owner is unchanged and already live:**
      `bartholomew/orchestrator/safety/parking_brake.py`'s `ParkingBrake` — a persistent,
      fail-closed brake wired into five live gate points. Nothing about the safety surface
      changes here; this only removes dead, misleadingly-named code that suggested a second
      kill-switch mechanism existed.
    - **Change:** `git rm identity_interpreter/adapters/kill_switch.py`; removed its import and
      `__all__` entry from `identity_interpreter/adapters/__init__.py`. No other file changed.
    - **Acceptance:** `identity_interpreter/adapters/kill_switch.py` no longer exists;
      `from identity_interpreter.adapters import KillSwitch` no longer resolves; no code or test
      imports the removed class (grep-confirmed); `ParkingBrake` remains the sole kill-switch
      authority.
    - **Verify:** `python -c "import identity_interpreter.adapters"` succeeds; `pytest -q
      tests/test_policies.py` and the adapters-touching suites stay green; `ruff check` +
      `black --check` (pinned 25.9.0) clean.

11.14. **Retire the deprecated tool-policy module — migrate its one caller, then delete it —
    the fourth-identified pair; three of four now fully retired** — ✅ implemented 2026-07-22
    - `identity_interpreter/policies/tool_policy.py`'s `check_tool_allowed()` had exactly one
      live caller (grep-confirmed): the CLI `explain --tool` command
      (`identity_interpreter/cli.py`). Its other function, `get_sandbox_paths()`, had **zero**
      callers anywhere. Migrated the CLI to build a declarative `IdentityContext`
      (`build_identity_context()`) and call `bartholomew.kernel.policy_engine.evaluate_tool_policy()`,
      then deleted the module.
    - **The nuance this pair surfaced (and the ownership-label correction it forced):** item
      11.1 / the ownership table named `bartholomew.kernel.skill_permissions.PermissionChecker`
      as the "permission gates" authority. But `PermissionChecker` gates skill **manifests**
      (permission *categories* like `memory.read`/`network.fetch` at declared levels
      never/ask/auto) — it needs a loaded skill and cannot answer the question
      `check_tool_allowed()` actually answered, namely "is this tool in `Identity.yaml`'s
      `tool_use.allowlist`?". The true functional successor of `check_tool_allowed()` is
      `evaluate_tool_policy()` (`policy_engine.py`), whose own docstring already said it
      "mirrors the logic `check_tool_allowed()` implements" — the same Executive-side path
      skill execution (`SkillRegistry.execute_action()`) and the scheduler already consult. So
      the CLI migrated there, not to `PermissionChecker`. Updated `policy_engine.py`'s docstring
      (it referenced `check_tool_allowed()` as still-existing) to note it is now the sole
      implementation, and corrected the ownership table row in `COGNITIVE_RUNTIME.md` and
      `DECISIONS.md` accordingly.
    - **Minor, deliberate output change:** the old CLI printed a `sandbox` field the authoritative
      `IdentityContext` doesn't model; the migrated command drops it and instead surfaces the
      policy engine's `reason` (e.g. why a non-allowlisted tool is denied) — strictly more
      informative for the allow/deny decision the command exists to explain.
    - Removed `check_tool_allowed` from `identity_interpreter/policies/__init__.py`'s exports;
      rewrote `tests/test_policies.py::test_tool_policy` to assert against `evaluate_tool_policy`
      via `build_identity_context` (the exact path the CLI now uses; broader deny/allow/default/
      consent coverage already lives in `tests/test_runtime_convergence_policy.py`); `git rm`'d
      the module.
    - **Acceptance:** `identity_interpreter/policies/tool_policy.py` no longer exists;
      `python -m identity_interpreter.cli explain Identity.yaml --tool web_fetch` reports
      `allowed: true` / `in_allowlist: true`, and `--tool <unknown>` reports `allowed: false`
      with a reason; nothing imports the removed module.
    - **Verify:** `pytest -q tests/test_policies.py tests/test_runtime_convergence_policy.py` —
      passes locally; CLI `explain --tool` live-checked for both an allowlisted and a
      non-allowlisted tool; `ruff check` + `black --check` (pinned 25.9.0) clean.

11.15. **Reclassify the "model routing" pair — it was a mislabel, not a duplicate; un-deprecate
    `select_model` and record the real gap** — ✅ implemented 2026-07-22
    - (Item 11.14 — the permission-gates pair — lands separately via its own branch; this is the
      fourth and last of item 11.1's audit-named "pairs" to be resolved, closing out item 11.1.)
    - **The finding:** unlike the other three, the "model routing" pair is **not two
      implementations of one concept** — they do different jobs, confirmed by direct reading:
      - `identity_interpreter/policies/model_router.py`'s `select_model(identity, task_type)`
        does **Identity-policy-driven model *selection*** — it reads `Identity.yaml`'s
        `meta.deployment_profile.model_policies.selection.by_task_type` to choose a model +
        parameters for a task type. It is the *only* code in the repo that reads `by_task_type`.
      - `identity_interpreter/orchestrator/model_router.py`'s `ModelRouter` does **backend
        *routing* + generation** — `select_route(data)` maps a `backend` hint to a hardcoded
        backend config and `route()` calls the LLM. It never reads `by_task_type`. Confirmed the
        live path (`Orchestrator.route_model()`) passes only `user_input`/`prompt`/`session_id`,
        so it just gets the default backend.
      A mechanical "migrate callers to `ModelRouter`, delete `select_model`" would have been a
      real regression (chat.py/CLI would stop honoring `Identity.yaml`'s task-type policy and
      fall back to the default backend) — the same class of trap as item 11.2's scheduler-drive
      revert. So this pair is resolved by **correcting the record, not deleting**.
    - **Change:** removed the deprecation notice + `DeprecationWarning` from
      `policies/model_router.py` and rewrote its docstring to establish it as the authoritative
      owner of *task-type model selection*, explicitly distinct from `ModelRouter` (routing).
      No caller changed; no behavior changed. Updated the ownership tables
      (`COGNITIVE_RUNTIME.md`, `DECISIONS.md`) to show selection and routing as two concepts,
      not a pair.
    - **Real gap surfaced (tracked future work, not part of this item):** the live runtime
      (`Orchestrator.route_model()` → `ModelRouter`) ignores `Identity.yaml`'s `by_task_type`
      selection policy entirely — only `select_model`'s standalone callers (CLI `explain`,
      `chat.py`) honor it. Teaching the live router to consult the selection policy (or otherwise
      unifying the two so the daemon's model choice is Identity-driven) is a genuine feature that
      touches the live generation path and needs its own smoke-verified change (item 11.2
      discipline) — deliberately **not** done here.
    - **Acceptance:** `select_model` no longer emits a `DeprecationWarning`; `chat.py` and CLI
      `explain` behave exactly as before; docs describe selection and routing as distinct owners;
      item 11.1's four "pairs" are all resolved (three retired: items 11.12–11.14; one
      reclassified: this item).
    - **Verify:** `pytest -q tests/test_policies.py test_integration.py` stays green (no
      deprecation warning now emitted); `python -m identity_interpreter.cli explain Identity.yaml`
      shows the Identity-selected model unchanged; `ruff check` + `black --check` (pinned 25.9.0)
      clean.

11.16. **Unify the two Reflection shapes — one canonical `ActionReflection` through one Memory
    sink; closes Exit Gate #4** — ✅ implemented 2026-07-23
    - **The gap:** the Runtime Contract's Reflection stage had two structurally different records.
      Chat wrote a Working Memory item; skill execution wrote a `skill_action_audit` row. Both
      durable, both audited, but "not the same `Reflection` type flowing through one Memory
      sink" — `COGNITIVE_RUNTIME.md` named this as Exit Gate question #4's shortfall ("the *fact*
      of a reflection is universal; its *shape* is not").
    - **Change:** added `bartholomew/kernel/reflection.py` — a canonical `ActionReflection`
      (`surface`, `action`, `outcome`, `summary`, `details`, `ts`) and an async
      `record_action_reflection()` that persists it to the **already-existing** shared sink,
      `MemoryStore.reflections` (new `kind="action_reflection"`, alongside the daily/weekly and
      drive reflections already stored there). Both surfaces now emit it: `runtime_contract.py`'s
      chat Reflection stage (for both the responded *and* the governance-denied outcome — the
      denied case produced no reflection at all before) and `SkillRegistry._finish()` (made
      `async`; every `execute_action()` exit already funnels through it, so success/failure/
      denial/brake-block all get one). PII-safe by construction: `to_memory_row()` runs
      `redact_pii()` over the summary and every string in `details`, matching what
      `skill_action_audit` already did.
    - **Deliberately additive — no regression, no removal.** The surface-specific stores stay,
      because they serve different jobs from the durable Reflection: Working Memory remains chat's
      short-term context buffer (still feeds `get_context_string()` for prior-turn content);
      `skill_action_audit` remains the immediate detailed compliance audit. What is now true that
      wasn't: one canonical Reflection *shape* flows into one Memory *sink* for every surface.
      Both writes are best-effort (a reflection-write failure never breaks the action), mirroring
      `_audit_execution`'s existing swallow-and-log posture. Retiring or deriving the
      surface-specific stores from the unified record (so there's one write, not additive ones)
      is a possible future simplification, explicitly out of scope here.
    - **Note (added 2026-07-28):** this item unified the *shape* of the reflection record used by
      chat and skill execution. It is a separate, already-resolved concern from the daily/weekly
      *content* pipelines addressed in item 11.8, which remain not fully unified — see item 11.8's
      2026-07-28 correction and `COGNITIVE_RUNTIME.md`'s reflection-ownership section.
    - **Acceptance:** a chat turn and a skill execution both produce a `reflections` row under
      kind `action_reflection`, distinguishable only by `meta.surface`; the durable record is
      PII-redacted; existing Working-Memory and `skill_action_audit` behavior is unchanged.
    - **Verify:** `pytest -q tests/test_reflection_unification.py` — 6 passed (unit shape +
      redaction, no-op-without-store, skill-writes-reflection, chat-writes-reflection,
      both-share-one-kind-and-sink); `pytest -q tests/test_skill_registry.py
      tests/test_runtime_contract_chat_seam.py tests/test_scenario_replay.py
      tests/test_api_chat_runtime_contract.py` — 64 passed (no regressions); `ruff check` +
      `black --check` (pinned 25.9.0) clean.

11.17. **Scheduler-drive convergence — Observation, Executive, Governance for the scheduler
    surface; closes Exit Gate questions #1-3 for that surface** — ✅ implemented 2026-07-23
    - **The gap:** `COGNITIVE_RUNTIME.md`'s Exit Gate table named all three "Partial" answers to
      the same root cause: `scheduler/loop.py`'s `_run_drive()` had its own
      `ParkingBrake("scheduler")` check but never constructed an `Observation`, was never modeled
      as a `CandidateAction`, and never consulted the Identity Context → Policy Decision path
      (item 11.2) at all — the scheduler was the one live surface still fully outside the Runtime
      Contract seam chat (11.3/11.4/11.6) and skill execution (11.2) already traverse.
    - **The constraint this had to respect:** item 11.2's *first* attempt at this exact wiring
      evaluated every drive's `task_id` against `evaluate_tool_policy()` unconditionally. Because
      `Identity.yaml`'s real `tool_use.allowlist` (`[web_fetch, browser_action]`,
      `default_allowed: false`) has never listed a drive `task_id`, that denied every registered
      drive by default in production, and — because the scheduler's retry loop has no backoff on
      denial — busy-looped the asyncio event loop badly enough that `/healthz` never answered
      (see DECISIONS.md's "tool_use.allowlist gates skill/capability execution, not scheduler
      drives" entry). That attempt was reverted; the corrected scope explicitly left scheduler
      drives ungated pending "a different, drive-appropriate policy source."
    - **Change:** added `bartholomew.kernel.runtime_contract.run_drive_through_runtime_contract()`
      — the same Observation → Interpretation → CandidateAction shape chat uses (`source=
      "scheduler"`, `kind=task_id`), the pre-existing `ParkingBrake("scheduler")` check unchanged
      (still raises on block, not a routine denial), and a Policy Decision check gated by a new
      `_SELF_MAINTENANCE_DRIVES` exemption set (`self_check`, `curiosity_probe`,
      `reflection_micro`, `fts_optimize` — today's full `drives.py` `REGISTRY`) — the
      "drive-appropriate policy source" the revert asked for: known kernel self-maintenance
      drives stay exempt exactly as before (zero behavior change, zero regression risk), while
      any *future* scheduler-originated action outside that set (e.g. a drive that acts on the
      user's behalf) is genuinely evaluated, mirroring `_CONVERSATIONAL_KINDS`' reasoning for chat
      (item 11.6). Also extends Exit Gate #4's unified `ActionReflection` (item 11.16) to the
      scheduler surface (`surface="scheduler"`) — additive, best-effort, not itself part of what
      #1-3 required. `scheduler/loop.py`'s `_run_drive()` now delegates to it, preserving its
      exact pre-existing contract (raises `RuntimeError` on parking-brake block; returns
      `(nudge_or_none, success_flag)` otherwise) so its call site and the tests that import it
      directly needed no changes.
    - **Live-smoke-verified, not just `pytest`** (the discipline item 11.2's regression made
      mandatory for exactly this class of change): booted a real `KernelDaemon` with
      `identity_path="Identity.yaml"` — the real restrictive production policy that caused the
      original incident — and ran the actual `run_scheduler()` loop for several seconds
      alongside a concurrent healthz-style pinger coroutine. All four drives ticked
      successfully (`ok=1`) and the pinger completed all 50 scheduled pings on time, proving the
      event loop stayed responsive under the exact conditions that starved it before.
    - **Acceptance:** a scheduler drive's task_id outside `_SELF_MAINTENANCE_DRIVES`, under a
      restrictive `IdentityContext`, is denied (no execution, a `governance_denied` reflection);
      every currently-registered drive is provably unaffected under that same restrictive
      context (the exact regression scenario); the parking-brake block still raises, not denies.
    - **Verify:** `pytest -q tests/test_scheduler_drive_convergence.py` — 17 passed;
      `pytest -q tests/integration/test_parking_brake_integration.py
      tests/test_runtime_convergence_policy.py tests/test_runtime_contract_chat_seam.py` — no
      regressions; `ruff check` + `black --check` (pinned 25.9.0) clean.

11.18. **Scheduler persistence off the event loop — fixing a CI-caught deadlock hazard found
    while merging item 11.17** — ✅ implemented 2026-07-24
    - **Not itself a Runtime Convergence Exit Gate item** (doesn't change any of the seven
      Observation/Governance/Reflection answers below) — a concurrency/infrastructure fix
      surfaced by, and required to safely land, item 11.17's own PR.
    - **The gap:** getting item 11.17's PR green surfaced three independent problems in
      sequence, each caught by CI rather than found by inspection. (1) `test_kernel_alive.py`
      wrote directly to the real, git-tracked `data/barth.db`/`data/memory.db` on every run —
      fixed by isolating it to `tmp_path`. (2) With that fixed, CI then caught
      `tests/test_self_state_api.py::TestPersonaEndpoints::test_switch_persona_is_reflected_by_current`
      failing with `sqlite3.OperationalError: database is locked` — the shared kernel's
      background scheduler ticking a drive right as the test's own persona-switch write ran on
      the same file — fixed with a bounded retry (test-only). (3) With both fixed, CI then hung
      for the full 120s pytest-timeout inside `test_kernel_alive.py`, on Python 3.10 — a genuine
      deadlock shape, not a flake: `scheduler/loop.py`'s `run_scheduler()` tick loop, and
      `scheduler/health.py`'s `get_system_metrics()` (called from `drives.py`'s
      `drive_self_check`/`drive_reflection_micro`, both invoked *inside* that same loop), made
      synchronous, blocking `sqlite3` calls — including an unconditional, exclusive `PRAGMA
      wal_checkpoint(TRUNCATE)` on every single operation via `db_ctx.py`'s `wal_db()` — directly
      on the asyncio event loop. This pattern predates item 11.17 (present since the scheduler's
      original implementation); a fresh DB makes every registered drive immediately due
      (`upsert_scheduled_tasks()` sets `next_run_ts = now`), which is what made the always-latent
      hazard reliably manifest inside a test's tight, same-instant execution window.
    - **Change:** `bartholomew/kernel/scheduler/store.py` (new) — `SchedulerStore`, offloading
      all scheduler persistence and `get_system_metrics()` onto one dedicated worker thread per
      `KernelDaemon`, with an `asyncio.Lock`-gated single-in-flight-operation submission model
      (not scattered `asyncio.to_thread()` calls) so scheduler DB calls never block the event
      loop and stay strictly sequential. Cancelling the coroutine awaiting a call does not lose
      tracking of an already-running worker future (done-callback-driven, not tied to the
      awaiter's own control flow). `close()` is idempotent, bound-drains outstanding work, and
      returns whether it fully drained. `db_ctx.py`'s `wal_db()` no longer checkpoints on every
      call by default (was: unconditional `TRUNCATE`; now: none, relying on SQLite's own
      automatic WAL checkpoint — correctness-neutral, WAL mode already guarantees readers see
      committed writes regardless of checkpoint timing). Explicit `TRUNCATE` is retained for
      controlled shutdown only (`MemoryStore.close()`, the API bridge's `atexit` hook —
      unchanged). `KernelDaemon.stop()` skips that shutdown checkpoint if `SchedulerStore.close()`
      didn't drain within its bound, logging the deferral rather than risking contention with a
      thread that may still be running. Incidentally fixed a dead-on-arrival import bug found
      along the way: `_run_daily_reflection()`'s `from .scheduler.persistence import
      get_system_metrics` doesn't exist there (the function lives in `scheduler/health.py`) and
      had always raised `ImportError`, silently swallowed — `pending_nudges` had always been `0`
      in daily reflections; now routed through `scheduler_store` correctly.
    - **Merged directly to `main`** (fast-forward, explicit user approval, "Claude/Cline are
      authorised trusted builders") rather than as a separate PR, since it was a fix blocking
      item 11.17's own PR from landing safely, not independent work.
    - **Verify:** `pytest -q tests/test_scheduler_persistence_concurrency.py` (new — 8 tests:
      event-loop responsiveness under lock contention, fresh-DB scheduler startup bounded to
      well under the old 120s timeout, `PASSIVE` vs `TRUNCATE` checkpoint contention behavior
      including a subprocess-isolated hard-timeout guard so a genuinely stuck checkpoint can't
      hang the suite itself, serialization/peak-concurrency, and two `SchedulerStore.close()`
      lifecycle cases) — 8 passed; `pytest -q tests/test_scheduler_drive_convergence.py
      tests/test_runtime_convergence_policy.py` — 25 passed, no regressions; `pytest -q
      test_kernel_alive.py tests/test_self_state_api.py tests/test_stage3_integration.py
      tests/test_reflection_narrative_integration.py tests/test_runtime_contract_chat_seam.py
      tests/test_reflection_unification.py` — 57 passed; full `pytest -q` — clean (zero
      failures/errors); `ruff check` + `black --check` (pinned 25.9.0) clean. See DECISIONS.md's
      "Scheduler persistence moved off the event loop..." entry for the full incident writeup,
      alternatives considered, and what's explicitly deferred (not decided) rather than fixed
      here: why the original hung `TRUNCATE` call outlasted its own 30s busy-timeout (temporary
      DEBUG-level instrumentation added to help answer this later — see RISKS.md), and whether
      the daemon's split `aiosqlite`/sync-`sqlite3` database ownership should eventually
      consolidate.

11.19. **Skill-execution convergence — Observation, Executive/CandidateAction for the skill
    surface; closes Exit Gate questions #1-2 for that surface, strengthens #3** — ✅ implemented
    2026-07-24
    - **The gap:** unlike chat (11.3/11.6) and scheduler drives (11.17), `SkillRegistry.
      execute_action()` was already a real, single choke-point (parking brake, Identity Policy
      Decision, unified Reflection into the shared sink) — so, unlike those two surfaces,
      nothing was actually missing from Governance/Reflection behavior. The gap
      `COGNITIVE_RUNTIME.md`'s Exit Gate table named was narrower and purely representational:
      none of that behavior was expressed as an `Observation`/`CandidateAction`, so the table
      couldn't honestly answer "yes" to questions #1-2 for this surface even though the
      underlying mechanics already matched chat/scheduler's shape.
    - **The requirement this had to satisfy, not just the shape:** merely constructing an
      `Observation`/`CandidateAction` object and discarding it would not have closed anything —
      the objects had to be genuinely consumed by the Governance decision before any skill side
      effect, not decorative. So `execute_action()` now builds `Observation(source="skill",
      raw_content=f"{skill_id}.{action}")` and `CandidateAction(kind=skill_id, ...)` at entry (
      `kind=skill_id`, not `"<skill_id>.<action>"`, deliberately matching the exact grain
      `evaluate_tool_policy()`/`Identity.yaml`'s `tool_use.allowlist` already operate on — the
      same grain `tests/test_runtime_convergence_policy.py`'s `ALLOW_CONTEXT` already allowlists
      by skill_id, not skill_id+action), and the Identity Policy check now evaluates
      `candidate_action.kind` itself rather than a separately-derived local. `_finish()`/
      `_record_reflection()` source the Reflection's `surface` from `observation.source` rather
      than the previous hardcoded literal.
    - **New named production seam, mirroring the established pattern exactly:**
      `runtime_contract.run_skill_through_runtime_contract()` — unlike
      `run_chat_through_runtime_contract()`/`run_drive_through_runtime_contract()`, which had to
      build Governance from scratch for their surfaces, this one is a thin delegator to
      `execute_action()` (which already owns the real logic), the same shape
      `scheduler/loop.py`'s `_run_drive()` already is relative to
      `run_drive_through_runtime_contract()`. `Planner.handle_skill_request()` — the sole
      production caller of `execute_action()` — now calls this instead of `execute_action()`
      directly. `execute_action()` remains directly callable as the execution primitive
      underneath (still exercised directly by ~5 pre-existing test files); this is not a second,
      parallel Governance path, and behavioral-equivalence between the two is directly tested.
    - **Proven, not assumed — new tests
      (`tests/test_skill_runtime_contract_seam.py`, 15 tests):**
      - The policy authority receives the exact CandidateAction constructed inside
        `execute_action()`: a spy on both the `CandidateAction` constructor and
        `evaluate_tool_policy()`'s incoming `tool_name` asserts they're the same value.
      - Denied actions never invoke the underlying skill (a `SpySkill` whose `execute()` logs an
        unmistakable side effect never logs it under a denying `IdentityContext`), including an
        "unrelated tool allowlisted" context that rules out a hardcoded/drifted stand-in kind.
      - Execution occurs only after the Governance decision: an ordering assertion proves the
        policy check completes strictly before `SkillBase.execute()` runs.
      - A structural (AST-based, not textual — avoids false positives from prose that merely
        *mentions* `execute_action()`) repo scan of `bartholomew/`, `bartholomew_api_bridge_v0_1/`,
        `identity_interpreter/` proves no production call site outside `runtime_contract.py`
        invokes `.execute_action(` directly, plus a behavioral patch-and-assert test that
        `Planner.handle_skill_request()` actually calls the named seam function.
      - Exactly one `ActionReflection` per attempt — success, parking-brake denial, policy
        denial, execution exception — verified by direct row counts against the shared
        `reflections` sink and the `skill_action_audit` table (no duplicate writes).
    - **Acceptance:** `SkillRegistry.execute_action()` builds and genuinely consumes an
      `Observation`/`CandidateAction` for every request; `Planner.handle_skill_request()` is the
      one production route into skill execution and it goes through
      `run_skill_through_runtime_contract()`; existing skill behavior (permissions, consent,
      parking brake, audit trail) is unchanged.
    - **Verify:** `pytest -q tests/test_skill_runtime_contract_seam.py` — 15 passed; `pytest
      tests/test_skill_runtime_contract_seam.py tests/test_skill_registry.py
      tests/test_reflection_unification.py tests/test_runtime_convergence_policy.py
      tests/test_end_to_end_tasks_and_audit.py tests/test_scheduler_drive_convergence.py
      tests/test_runtime_contract_chat_seam.py tests/test_api_chat_runtime_contract.py` — 112
      passed, no regressions; full `pytest -q` — clean (zero failures/errors).

11.20. **RISKS.md R1 red-team test suite (consent bypass / privacy leakage)** — ✅ added
    2026-07-24. Not a Runtime Convergence item per se, but the risk mitigation gated on the same
    milestone. Full write-up in the standalone "RISKS.md R1 (consent bypass / privacy leakage):
    red-team test suite" section above and in RISKS.md's R1 entry. Summary: `tests/
    test_consent_bypass_redteam.py` (10 tests) proves no production retrieval surface ever
    surfaces a `never_store`/unconsented `ask_before_store` memory, plus two permanent structural
    guards (no `apply_consent_gate=False` in production; no `.retrieve()` facade exposes a
    gate-bypass parameter). Non-vacuity confirmed by deliberately breaking `ConsentGate` and
    watching exactly the right tests fail.

11.21. **Voice/sight convergence — Observation/CandidateAction + governed seam for the two
    remaining device surfaces; closes Exit Gate questions #1-3 for them (the last
    current-production governance gap)** — ✅ implemented 2026-07-24
    - **The gap:** voice (`identity_interpreter/adapters/voice_io/stream_bridge.py:start_stream()`)
      and sight (`identity_interpreter/adapters/sight/pipeline.py:start_capture()`) were
      parking-brake-only stubs with no production caller (only
      `tests/integration/test_parking_brake_integration.py`), no `Observation`/`CandidateAction`,
      no Identity Policy consultation, and no consent gate or Reflection at all — the last
      surfaces `COGNITIVE_RUNTIME.md`'s Exit Gate table named "Partial" for questions #1-3.
    - **Strictly architectural scope:** this adds *only* the governed seam and its invariants. No
      real microphone/camera/streaming/transcription/computer-vision/device-lifecycle work, and no
      Stage 6 capture architecture, is introduced — the capability bodies
      (`_perform_stream`/`_perform_capture`) stay inert placeholders.
    - **The seam:** `runtime_contract.run_voice_through_runtime_contract()` /
      `run_sight_through_runtime_contract()` build `Observation(source="voice"/"sight")` and
      `CandidateAction(kind="voice_stream_start"/"sight_capture_start")` — distinct, stable kinds
      for a *single start attempt* (the `_start` suffix is deliberate: approval authorizes one
      start, never continuing access), not generic skill kinds. Governance runs three gates,
      strictly before any capability call: (1) ParkingBrake (scope, preserving the pre-existing
      `except ImportError: pass` behaviour); (2) additive Identity Policy Decision (skipped when no
      `IdentityContext`, matching chat/scheduler/skill; under real `Identity.yaml` these kinds are
      denied by default); (3) an *always-required, fail-closed* device consent gate reusing the one
      interactive consent channel (`privacy_guard.get_consent_handler()`) that skill "ask"
      permissions use — absent handler, declined, or unresolved (falsy) all deny. Exactly one
      `ActionReflection` into the shared sink for every outcome (started / policy denial / consent
      denial / brake denial / execution error).
    - **Sole production entry, no bypass:** `start_stream()`/`start_capture()` remain as public
      compatibility wrappers (unchanged signatures/return shapes) but delegate *exclusively* to the
      seam; the inert capability is passed in as `stream_fn`/`capture_fn` and is reachable only
      through the seam. An AST structural test forbids any direct call to
      `_perform_stream`/`_perform_capture` anywhere in production and asserts the wrappers delegate
      to the named seam.
    - **Proven, not assumed — new tests (`tests/test_voice_sight_runtime_contract_seam.py`, 45
      tests, both surfaces parametrized):** the CandidateAction is genuinely consumed (constructor
      + `evaluate_tool_policy` spies agree on the exact kind); denied starts (policy / consent-absent
      / consent-declined / consent-unresolved / brake) never invoke the capability (call-count 0);
      approved starts execute exactly once; governance completes strictly before execution
      (ordering assertion `["policy","consent","capability"]`); exactly one Reflection per attempt
      for every outcome; compat wrappers delegate only to the seam. **Required non-vacuity
      controls:** three mutation tests neutralise each gate (force-allow policy, force-allow consent,
      force `is_blocked` False) *in the test only* and assert the placeholder then executes —
      plus a manual pre-commit check that deliberately broke each of the three gates in the
      production seam and confirmed exactly that gate's denial tests (and only those) failed.
    - **Two existing tests updated (not weakened):** `test_sight_allowed_when_disengaged` /
      `test_voice_allowed_when_disengaged` now register an approving consent handler, because
      disengaging the brake alone is no longer sufficient to start — device consent is
      additionally required. The three brake-*engaged* tests pass unmodified (the brake is checked
      first and still short-circuits).
    - **A recorded Stage 6 safety requirement (not implemented now):** safely stopping/tearing down
      a future active capture session must never depend on obtaining permission to *continue*
      capturing — teardown is not a governed "start". See COGNITIVE_RUNTIME.md's "Device surfaces"
      section.
    - **Acceptance:** every executable voice/sight start creates the right Observation/
      CandidateAction; consent + Identity Policy + parking brake all decide before any device or
      downstream action; denied requests produce no side effect; approved requests execute exactly
      once; reflection/audit occur exactly once; production callers cannot bypass the seam; the
      inert adapters stay inert behind it.
    - **Verify:** `pytest -q tests/test_voice_sight_runtime_contract_seam.py` — 45 passed;
      `pytest -q tests/test_voice_sight_runtime_contract_seam.py
      tests/integration/test_parking_brake_integration.py tests/test_parking_brake_scoped_blocks.py
      tests/unit/safety/test_parking_brake.py` — all passed; full `pytest -q` — 879 passed, 2
      skipped, 0 failures.

11.22. **Exit Gate question #7 (personality uniformity): reclassify the voice/sight-persona
    residual to Stage 6; declare Stage 4.5 complete** — ✅ documentation-only, 2026-07-24 (no
    production code changed).
    - **Why this was needed:** after item 11.21, Q7 was the only exit-gate question not marked
      "yes", which is inconsistent with declaring Stage 4.5's convergence complete. Precise
      resolution required determining, from the authoritative stage definition, whether Q7's
      remaining residual is a Stage 4.5 deliverable or a Stage 6 dependency.
    - **The authoritative determination:** Q7 *is* a genuine Stage 4.5 criterion — the P2.5
      finding above states the milestone's own goal as *"Bartholomew should have one personality,
      not one personality per interface"* (an architectural inconsistency), so personality
      uniformity is central to this stage, not peripheral, and was **not** hand-waved wholesale to
      Stage 6. What Q7 architecturally demands — every personality-bearing interface sourcing
      persona from the single authority (`PersonaPackManager`) rather than a duplicated
      implementation — is **done** for every interface that exists as a personality-bearing surface
      today (chat, CLI `explain`, standalone `chat.py`; the duplicated `identity_interpreter/
      policies/persona.py` was removed in item 11.12). The `traits` split is a deliberate
      stable-identity-vs-persona-state design decision, already documented as "not a duplication",
      i.e. not an open convergence gap.
    - **What is reclassified (the residual only, not the question):** the one thing still
      outstanding under Q7 — voice/sight *consulting* persona — is a Stage 6 **dependency**, not a
      Stage 4.5 shortcut: a surface that produces no personality-bearing output cannot "expose the
      same personality", so converging persona onto voice/sight is architecturally impossible until
      Stage 6 gives them persona-producing output. That work is the already-approved Stage 6
      boundary for real voice/sight functionality (see item 11.21's Stage 6 note). It is moved to
      ROADMAP.md Stage 6's carried-forward requirements, with this reason recorded.
    - **Result:** with the residual reclassified, all seven exit-gate questions are satisfied
      within Stage 4.5's scope; no official Stage 4.5 exit criterion is left partial while the
      stage is marked complete. Stage 4.5 (Runtime Convergence) is **complete**. Real voice/sight
      functionality — including persona-producing output — remains Stage 6.
    - **Scope:** documentation only (`COGNITIVE_RUNTIME.md`, `MASTER_PLAN.md`, `ROADMAP.md`). No
      production code, tests, or behaviour changed.

**Runtime Convergence Exit Gate** — before P3 resumes, all seven must be "yes":
1. Can every input source create an Observation?
2. Does every proposed action pass through the Executive?
3. Does every execution pass through the same Governance path?
4. Does every completed action produce a Reflection?
5. Does every Reflection update Memory?
6. Does every conversation see the Experience Kernel?
7. Does every interface expose the same personality?

**Status as of item 11.22 (2026-07-24): all seven satisfied within Stage 4.5's scope — Stage 4.5
Runtime Convergence is complete.** Questions **1–6 are "yes"** for every surface that exists
today (item 11.21 closed the last current-production governance gap, voice/sight). Question **7
is "yes" within Stage 4.5's scope**: every personality-bearing interface (chat, CLI `explain`,
`chat.py`) sources persona from the one authority (`PersonaPackManager`); the `traits` read from
`Identity.yaml` are a deliberate stable-identity/persona-state split, not a convergence gap. Q7's
only residual — voice/sight consulting persona — was **formally reclassified to Stage 6 by item
11.22** (see that item for the reason); it is architecturally impossible to satisfy inside Stage
4.5 because voice/sight produce no persona-bearing output until Stage 6 builds it. See
`COGNITIVE_RUNTIME.md`'s Exit Gate table for the per-question evidence. No official Stage 4.5
exit criterion is left partial.

**Note:** this section records the architect's recommendation and gives it a measurable exit
gate; it does not itself pause P3 — that requires separate, explicit user sign-off.

---

## Full test suite investigation — 38 failures → 9 → 4 → 2 → 0, fixed 2026-07-20

Follow-up to the FTS5/`sys.path` investigation above: went through every remaining failure
in `pytest -q` (the full suite, not just `-m smoke`) one at a time. 29 were real, root-caused,
fixed bugs (several were the *same* underlying bug recurring in different call sites). 9 are
left deliberately unfixed because they require a design/product decision, not a mechanical
fix — documented individually below so nobody re-discovers them from scratch.

### Fixed (29 tests across 10 distinct bugs)

1. **No OS keystore in headless/CI environments** (8 tests: `test_bartholomew.py`,
   `test_cold_boot.py` x5, `test_memory_functionality.py` x2). `MemoryManager._init_encryption()`
   correctly fails closed when encryption is required (`Identity.yaml`'s `encryption.at_rest:
   true`) but no OS keystore backend is reachable (no D-Bus/Secret Service, no macOS Keychain,
   no Windows Credential Manager) — that's the right production behavior, not a bug. The gap
   was test infrastructure: nothing gave the test session a keystore stand-in. Added an
   in-memory `keyring.backend.KeyringBackend` in root `conftest.py`, installed for the whole
   session via `keyring.set_keyring(...)`. Side benefit: tests no longer write real encryption
   keys into a developer's actual OS keychain when run locally on a machine that has one. Only
   affects `identity_interpreter/adapters/memory_manager.py` (the `chat.py` CLI path) — the
   FastAPI/Docker production path uses a completely separate, env-var-based key system
   (`bartholomew.kernel.encryption_engine`) untouched by this.

2. **`test_liveness_api.py` never triggers the app's startup lifespan** (3 tests). Used
   `client = TestClient(app)` at module level instead of `with TestClient(app) as c:` — a bare
   `TestClient` never runs FastAPI's startup/shutdown lifespan, so the kernel daemon (and the
   schema it creates: `reflections`, `nudges`, etc tables) never started, and every query 404'd
   with `no such table`. This bug predates today; it was masked before the `BARTH_DB_PATH`
   test-isolation fix (previous section) because every test silently shared the real
   `data/barth.db`, which already had those tables from real usage. Fixed by switching to the
   same `with TestClient(app) as c:` fixture pattern already used correctly in
   `tests/test_stage0_alive.py`.

3. **`safety.audit` entries are correctly encrypted now, but a test reads them as plaintext**
   (1 test: `tests/integration/test_parking_brake_integration.py::test_audit_trail_records_changes`).
   Direct consequence of fixing the malformed `memory_rules.yaml` rule in the P0 pass (PR #1) --
   that rule (`encrypt: standard` for `kind: safety.audit`) was silently never matching before
   because it was malformed, so audit entries were accidentally stored as plaintext JSON. Now
   that the rule correctly matches, entries are properly encrypted at rest (the *intended*
   behavior), and the test's raw `sqlite3.connect(...).execute("SELECT ... FROM memories")` +
   `json.loads(row[1])` broke because `row[1]` is now an encryption envelope, not plaintext.
   Fixed the test to decrypt via `bartholomew.kernel.encryption_engine._encryption_engine
   .try_decrypt_if_envelope(...)` before parsing.

4. **`HybridRetriever._apply_boosts()` return-tuple mismatch** (7 tests across
   `tests/test_hybrid_rrf.py` and `tests/test_hybrid_boosts_flip.py`). The method returns a
   3-tuple `(boosted_fts, boosted_vec, boost_map)` -- its own internal caller already unpacks
   3 values -- but these two test files still unpacked only 2, raising `ValueError: too many
   values to unpack`. `boost_map` (a debug breakdown) was evidently added to the method after
   these tests were written. Updated all 7 call sites to unpack 3 (`_boost_map` discarded,
   unused).

5. **FTS5 query-syntax crashes on free-text queries with punctuation** (contributed to several
   of the retrieval/hybrid test failures). `HybridRetriever._pull_fts_candidates()` forwarded
   natural-language queries straight into `FTSClient.search()`, which passes the string to
   SQLite's FTS5 `MATCH` operator -- FTS5 parses that as its *own* query grammar (operators,
   phrases, column filters), not literal text, so a bare `.` or `?` from ordinary sentence
   punctuation raised `fts5: syntax error`. Added `_sanitize_fts_query()` (strips
   `.,!?;` -- characters that are never meaningful FTS5 syntax) at exactly the boundary where
   free text enters the FTS5 subsystem, so `FTSClient.search()` itself is untouched and still
   honors its documented contract of accepting raw FTS5 query syntax (quoted phrases,
   `AND`/`OR`/`NOT`, `field:value`) for callers who intend that.

6. **`FTSClient.upsert()` / `rebuild_index()` / schema triggers issue FTS5's `'delete'`
   special command for rowids that were never actually indexed** (recurring instance of the
   bug already fixed in `upsert()` during the earlier PR #1 pass -- turned out to have three
   more instances):
   - `rebuild_index()` unconditionally ran `DELETE FROM memory_fts` before rebuilding, which is
     exactly its own documented "initial index population" use case (content-table rows that
     predate the index) -- i.e. it crashed on its own primary purpose. Fixed: only issue the
     `DELETE` when `memory_fts_map` shows there's actually something indexed to clear.
   - The `memory_fts_update` and `memory_fts_delete` triggers (fired automatically by SQLite on
     any `UPDATE`/`DELETE` against `memories`) had the same unconditional `'delete'`-command
     issue, just baked into SQL instead of Python. Fixed by adding
     `WHEN EXISTS (SELECT 1 FROM memory_fts_map WHERE memory_id = old.id)` guards, and splitting
     `memory_fts_update` into that guarded version plus a `memory_fts_update_backfill` trigger
     (`WHEN NOT EXISTS ...`) that just inserts instead of delete-then-insert for never-indexed
     rows.
   - `MemoryStore.delete_memory()` *also* issued this exact special command manually and
     unconditionally, immediately before doing `DELETE FROM memories` (whose own trigger would
     run the same cleanup correctly, per the method's own code comment: "triggers will also
     fire for cleanup"). Removed the redundant manual step now that the trigger is fixed.
   - **Update 2026-07-20 (PR #2 review):** a bot reviewer correctly flagged that the caveat
     above was a real gap, not just a note -- `CREATE TRIGGER IF NOT EXISTS` alone never
     upgrades an already-existing database's trigger bodies. Fixed: `init_schema()` and
     `init_chunk_schema()` now explicitly `DROP TRIGGER IF EXISTS` every FTS trigger before
     running the schema script, so `IF NOT EXISTS` always recreates them fresh with the
     current definitions on every call, on any database. Also applied the same trigger guards
     (`WHEN EXISTS`/`WHEN NOT EXISTS` against `chunk_fts_map`) to `chunk_fts_update`/
     `chunk_fts_delete`, and the same `upsert()`-style pre-check to `upsert_chunk()` -- these
     are `chunk_fts`'s exact mirror of the `memory_fts` bugs, found while responding to the
     review, not previously caught by any test.
   - **Second bot finding, also fixed:** `rebuild_index()`'s `memory_fts_map`-based check
     (used to decide whether `DELETE FROM memory_fts` was safe to issue) assumes the map
     always accurately reflects the real FTS5 index state. If it doesn't -- e.g. a database
     from before the map table existed, or the map and index having drifted out of sync some
     other way -- an empty map would wrongly skip the `DELETE`, leaving stale entries mixed in
     with the fresh rebuild instead of properly clearing them. Fixed by dropping and
     recreating the `memory_fts` table itself during rebuild instead of trying to introspect
     its state first (a bare, non-`MATCH` query against an external-content FTS5 table can't
     reliably tell you what's actually indexed -- established earlier in this investigation).
     Same fix applied to `rebuild_chunk_index()`.
   - Verified: full `pytest -q` unchanged before/after these follow-up fixes (still 9 known
     failures, all pre-existing and already documented -- no regressions, no new passes).

7. **`MemoryStore.delete_memory()` never enabled `PRAGMA foreign_keys`** (1 test:
   `tests/test_stage2f_chunking.py::test_delete_memory_cascades_to_chunks`). `foreign_keys` is a
   per-connection SQLite setting, not persistent in the database file; this method opened its
   own fresh `aiosqlite.connect()` without setting it, so `memory_chunks`' `ON DELETE CASCADE`
   silently never fired on that connection -- chunks were orphaned instead of cascade-deleted.
   Added `await db.execute("PRAGMA foreign_keys = ON")`. Only fixed this one call site; the
   codebase has roughly ten other `aiosqlite.connect()` blocks in this file that weren't
   audited for the same gap (none are currently failing a test, so out of scope here, but worth
   a dedicated pass).

8. **`VectorStore` didn't create its DB's parent directory** (1 test:
   `tests/test_retrieval_factory.py::test_db_path_resolution_explicit`). Given an explicit
   `db_path` whose parent directory doesn't exist yet, `sqlite3.connect()` raised
   `unable to open database file`. `bartholomew_api_bridge_v0_1/services/api/db.py` already
   handles this (`os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)`); `VectorStore` didn't.
   Added the same. Also fixed the test itself, which used a hardcoded relative path
   (`"custom/path.db"`) with no `tmp_path` -- it was writing a real `custom/` directory into the
   repo's working tree on every run (same class of hygiene issue as `data/barth.db` getting
   mutated by test runs, fixed earlier). Switched to `tmp_path`.

9. **FTS5 `tokenize=` directive: single-quoted outer value can't contain a `tokenchars`
   argument** (1 test: `tests/test_fts_schema_hygiene.py::test_tokenizer_config_with_args`).
   Confirmed independent of this project's code with a bare `sqlite3`/FTS5 repro:
   `tokenize='unicode61 tokenchars .-@_'` is a parse error; FTS5 requires the `tokenchars`
   *value* itself to be single-quoted (`tokenchars '.-@_'`), which then can't nest inside an
   outer single-quoted SQL string. Fixed by switching the schema template's outer quoting to
   double quotes (`tokenize="{tokenizer}"` -- confirmed this doesn't affect the plain `porter`
   tokenizer case already in production use) and fixing the test's config value to include the
   required inner quoting. No real config in this repo currently uses `fts_tokenizer_args` with
   `tokenchars`, so this was a test-only gap, not something affecting production.

10. **`RetrievalConfigManager` doesn't support the legacy `fts.tokenizer` config location**
    (1 test: `tests/test_retrieval_hot_reload.py::test_config_manager_tokenizer_backward_compat`).
    There are two independent tokenizer-config-loading implementations in the codebase --
    `fts_client.py`'s `_load_tokenizer_config()` (supports both `retrieval.fts_tokenizer` and
    legacy `fts.tokenizer`) and `retrieval_config.py`'s `RetrievalConfigManager._load_config()`
    (only ever read `retrieval.fts_tokenizer`) -- and they'd drifted apart. Added the same
    legacy fallback to `RetrievalConfigManager`.

### Round 2 fixes — the 3 design-decision items, resolved 2026-07-20

The 3 items below were left unfixed pending a product/design call (see original writeups,
preserved in git history). The user made explicit decisions on all 3; implemented accordingly.

11. **`tests/test_bm25_udf_fallback.py`** (3 tests) — user decision: "implement a real
    fallback" (not remove the fallback path, not just skip the tests). Replaced the broken
    `matchinfo()`-based mechanism (`_rank_pcx()`, the old `sql_fallback` query) with a Python
    term-frequency ranking (`_extract_query_terms()` + `_term_frequency_rank()` in
    `fts_client.py`): the fallback SQL now just does `WHERE memory_fts MATCH ? ORDER BY m.id
    DESC LIMIT ?` against a bounded candidate pool (`max(fetch_limit * 5, 100)`), and rank is
    computed and sorted in Python, matching `bm25()`'s own "lower is better" convention. All 3
    existing tests pass unchanged against the new implementation. Also fixed the identical bug
    in `search_chunks()` (`-rank_pcx(matchinfo(chunk_fts, 'pcx'))`, calling the now-deleted
    `_rank_pcx` — would have raised `NameError` if ever actually hit) with the same
    term-frequency approach, found while doing this fix; no test previously exercised that path.

12. **`tests/test_fts_schema_hygiene.py::test_migrate_schema_fixes_rowid_mismatch`** — a
    mechanical fix, not a design decision (the "left unfixed" writeup already fully specified
    the correct approach). Rewrote `migrate_schema()` to compare `memory_fts_map` against
    `memories` in both directions (orphaned map entries with no matching `memories` row;
    `memories` rows with no `memory_fts_map` entry) instead of the broken `memory_fts` LEFT
    JOIN, and call `rebuild_index()` (already fixed earlier to drop-and-recreate rather than
    `DELETE`) when either mismatch is found. Rewrote the test's setup to create a mismatch this
    approach can actually detect (a direct `INSERT INTO memory_fts_map` for a nonexistent
    `memory_id`, via a connection that skips `set_wal_pragmas()`'s `PRAGMA foreign_keys = ON`
    so the invalid reference can be inserted) — the old setup (direct `INSERT INTO memory_fts`)
    tested a scenario that was structurally undetectable via portable SQL, which is exactly why
    the original check could never work.

13. **`tests/test_retrieval_fts5_fallback.py::test_get_retriever_degrades_fts_mode_when_unavailable`**
    — user asked for a recommendation; recommended and implemented "honor explicit mode" (i.e.
    keep `get_retriever()`'s current code as-is, per its own docstring and the sibling
    `test_explicit_mode_overrides_env_and_config` in `test_retrieval_factory.py`). Rewrote the
    outdated test to exercise the actual non-explicit path (`mode` resolved via
    `BARTHO_RETRIEVAL_MODE` env var rather than passed as an argument — `get_retriever()`'s
    `mode_explicit` tracking only covers the function argument, so an env/config-resolved
    `"fts"` is still eligible to degrade) and added
    `test_get_retriever_honors_explicit_fts_mode_when_unavailable` to codify the explicit-mode
    behavior itself, which wasn't previously covered by any test.

Verified: `pytest -q` on the four affected test files (34 tests) all pass; full `pytest -q`
now shows exactly the 4 remaining known issues below (was 9), no regressions elsewhere.

### Round 3 fix — recency-boost fusion redesign, resolved 2026-07-20

The two ranking-quality gaps below (`test_lexical_over_vector_on_rare_tokens.py`,
`test_fts_unavailable_vector_quality.py`) were previously documented as "genuine
fusion-math quality gaps... not root-caused to a specific line." Root-caused this round:
both were symptoms of the same underlying bug in `HybridRetriever`.

14. **Recency boost multiplied the *entire* fused score per-memory instead of being weighted
    into fusion** (2 tests: `test_lexical_over_vector_on_rare_tokens.py::test_lexical_beats_vector_on_exact_rare_tokens`,
    `test_fts_unavailable_vector_quality.py::test_vector_quality_maintained_when_fts_unavailable`).
    `_apply_boosts()` computed `total_boost = recency_boost * kind_boost * rule_boost` and
    multiplied it onto *both* the normalized FTS and vector scores before fusion — so
    `recency_boost` wasn't a tie-breaker, it was a multiplier on the whole relevance signal.
    Instrumented a failing query directly (`BARTHO_RETRIEVAL_DEBUG=1`, `last_debug`): an exact
    keyword match (`bm25_norm=1.0`, `vec_norm=0.0`) lost top-1 to a completely unrelated memory
    (`bm25_norm=0.0`, random `vec_norm=0.68`) purely because the unrelated memory was ~20 days
    "more recent" in the synthetic test corpus — at the default 7-day half-life, that's a ~7x
    recency multiplier, which swamps the 0.6/0.4 FTS/vector weight split (a perfect FTS match
    should only ever lose by this weighting if the other candidate's *vector* score is high
    enough to overcome a 0.6-vs-≤0.4 gap on its own). Since real memories almost always have
    *some* age difference, this meant recency could override actual relevance for essentially
    any two-candidate comparison, not just deliberately-engineered edge cases — a real
    production bug, not a test artifact.

    User decision: fold recency into fusion as its own weighted term (rather than bounding/
    compressing the existing multiplier). Implemented in `bartholomew/kernel/hybrid_retriever.py`:
    - Added `HybridRetrievalConfig.weight_recency` (default `0.0` — opt-in, no behavior change
      unless configured; `__post_init__` normalizes `weight_fts + weight_vec + weight_recency`
      together).
    - `_apply_boosts()` no longer multiplies `recency_boost` into `boosted_fts`/`boosted_vec` —
      only `kind_boost * rule_boost` now (both left untouched; out of scope of this bug, and
      `kind_boosts` defaults to empty so it's inert unless configured). `boost_map` still
      reports the raw recency value for debug/introspection (`Result.recency`).
    - Added `_normalize_recency_scores()`: min-maxes raw recency-decay values across the
      candidate set, same pattern as the existing `_normalize_fts_scores()`/
      `_normalize_vec_scores()` — turns "most recent of these candidates" into a `[0, 1]`
      relative signal instead of an absolute value whose magnitude depends on wall-clock age.
    - `_fuse_weighted()` gained an optional `recency_scores`/`weight_recency` term:
      `fused = w_fts*s_fts + w_vec*s_vec + w_recency*s_recency`. Backward compatible (both
      default to no contribution) for the several unit tests that call it with just FTS/vector
      scores.
    - `_fuse_rrf()` gained its own recency-rank term (`weight_recency / (k + recency_rank)`,
      same `1/(k+rank)` shape as the existing FTS/vector RRF terms, so its contribution is
      bounded the same way rather than an unbounded multiplier) — computed only when
      `weight_recency > 0`, so the zero-weight default is byte-for-byte identical to the old
      formula.
    - `retrieval_config.py` gained loading for a new `retrieval.hybrid_weights.recency` key
      (mirrors the existing `fts`/`vector` keys); `config/kernel.yaml` sets it to `0.15`
      (normalizes to ~13% of the fused score alongside ~52%/~35% for FTS/vector).
    - **Also fixed while investigating**: `tests/integration/test_fts_unavailable_vector_quality.py`'s
      own `calculate_hit_rate()` helper had a loop bug — its inner `break` only exited the
      `memory_map` lookup for a single result, not the `results` loop, so a query with several
      same-group matches in its top-10 could count multiple hits for itself. Observed hit rates
      of 110-120%, which is nonsensical for a rate. Fixed to count at most one hit per query.
    - **Updated 3 existing unit tests that hard-coded the old multiplicative-recency contract**
      as their expected behavior (`test_hybrid_boosts_flip.py::test_recency_boost_flips_top1_weighted`,
      `test_hybrid_boosts_flip.py::test_combined_boosts_flip_top1`,
      `test_hybrid_rrf.py::TestKindAndRuleBoosts::test_combined_boosts`) to exercise the new,
      bounded path instead — they still demonstrate recency (and kind boosts) can flip
      near-tied rankings, just no longer via an unbounded per-memory multiplier.
    - **Also fixed**: `test_retrieval_hot_reload.py::test_config_manager_loads_defaults` was
      silently loading the *real* `config/kernel.yaml` instead of testing dataclass defaults —
      `RetrievalConfigManager._find_path()` falls back to relative `DEFAULT_CONFIG_PATHS` when
      the passed-in path doesn't exist, and pytest runs from the repo root where that real file
      exists. Previously masked because the real file's `fts`/`vector` weights happened to match
      the test's hardcoded expectations; broken by adding `recency` to `config/kernel.yaml`
      above. Fixed by monkeypatching `DEFAULT_CONFIG_PATHS` to `[]` for the duration of the test
      so it actually exercises "no config file found."

    Verified: both originally-failing tests now pass; full `pytest -q` re-run clean except the
    2 already-deferred recency-flip tests below (no new regressions). The recency-flip tests'
    measured flip rate changed as a side effect (0% → 40%/10%) since recency can no longer push
    "old" completely out of the top-5 the way the unbounded multiplier did — still under
    threshold, for a different reason than before, but the deferral decision (below) stands
    unchanged.

### Round 4 fix — recency-flip integration tests, resolved 2026-07-20

User decision: don't leave these deferred, dig in and fix properly. Root-caused (not a call
about test intent after all — a real corpus/config bug, same investigative depth as the round 3
fix above):

15. **`tests/integration/test_recency_flip_integration.py`** (2 tests, `test_recency_boost_flips_rankings_weighted`
    and `..._rrf`). Two independent bugs stacked on top of each other:
    - **The corpus made all 25 groups look nearly identical to FTS/vector relevance.**
      `create_recency_corpus()` built every group's text as shared boilerplate
      ("...in group N. Dark mode enabled with accent color blue.") differing only by a trailing
      group number. Since a query's own group barely stood out from the other 24 near-duplicate
      groups, even a correctly-bounded recency signal ended up promoting *other* groups' "recent"
      documents above a query's own group's "old" document, rather than resolving the intra-group
      old-vs-recent tie the test is actually about. Confirmed by labeling and printing actual
      top-5 results directly: a group-0 query's top-5 often didn't contain group 0's own "old"
      variant *at all* — it was crowded out by e.g. `g10-recent`, `g5-recent`, `g19-recent`
      instead (both "old" and "recent" need to co-occur in the top-5 for the test's win-counting
      to count anything). Fixed by giving each group a genuinely distinct topic (25 unrelated,
      everyday nouns in `_RECENCY_TOPICS`) instead of a shared sentence with a number appended —
      now the only real ambiguity left for a query is its own group's old vs. recent variant, as
      intended. (One topic candidate, "workout routine plan", tripped
      `bartholomew.kernel.memory.privacy_guard.SENSITIVE_KEYWORDS`'s `"routine"` entry, which
      fails closed on a consent gate with no handler registered in tests — screened the final
      topic list against that keyword list.)
    - **The tests' own hand-constructed `HybridRetrievalConfig` never set `weight_recency`**,
      so — after the round 3 fix above made recency's fusion weight opt-in (default `0.0`) —
      these tests were exercising a config with *zero* recency influence at all, regardless of
      `half_life_hours`. Fixed by setting `weight_recency=0.15` in both tests' configs, mirroring
      `config/kernel.yaml`'s production default.
    - With both fixed together: weighted mode measures 100% flip rate (was 0%, threshold ≥75%),
      RRF measures 75% (was 0%, threshold ≥70%) — both comfortably clear their existing
      thresholds, so the thresholds themselves didn't need changing. `test_recency_disabled_no_flip`
      (the third, already-passing test in the same file, used as a control) still measures 60%
      with recency genuinely disabled (`half_life_hours=0.0`), safely under its `<80%` bound.

    Also found and fixed while investigating the ranking-quality gap above (round 3's own
    follow-up, not part of the recency-flip corpus fix): `retrieve()`'s query-aware weighting
    path passed `recency_scores` into `_fuse_weighted()` without an explicit per-call
    `weight_recency`, so `_query_aware_weights()`'s fts/vec pair (already normalized to sum to
    1.0 on its own) plus the config's `weight_recency` on top could sum to >1.0 whenever a query
    was detected as lexical/semantic — silently underweighting recency relative to what
    `config/kernel.yaml` specifies, and leaving `weight_override` callers unable to fully opt out
    of the config's recency contribution for a single call. Flagged by automated PR review (Codex)
    on PR #4; fixed by rescaling the query-aware fts/vec pair by `(1 - weight_recency)` and by
    having `weight_override` calls explicitly zero out recency for that call (an override caller
    predates `weight_recency`'s existence and has no way to express a recency component in its
    2-tuple, so it now gets exactly what it asks for instead of an uninvited extra ~13%). Also
    fixed a related, previously-dormant bug in the same code while touching it: `call_weight_fts`/
    `call_weight_vec` were only ever assigned inside the weighted-fusion branch, so an RRF-mode
    call with `BARTHO_RETRIEVAL_DEBUG=1` would hit `NameError` building the debug info — no
    existing test exercises that combination, so it was never caught. Moved the initialization
    above the `if fusion_mode == "rrf"` branch.

    Verified: full `pytest -q` is now **fully green — 0 known failures** (was 2, was 4, was 9,
    was 38 originally). `ruff check` clean.

### Verify
```bash
pytest -q                          # 0 failures (was 2, was 4, was 9, was 38 originally)
pytest -q -m smoke                 # unaffected, still green
```

---

## Experience Kernel MVP: bug fix + privacy gap — fixed 2026-07-20

Started work on P1 ("Experience Kernel MVP") expecting a greenfield build. Research first (per
this doc's own governance rule -- see Doc Governance -- and standard practice: investigate before
implementing) surfaced that the feature already exists in code, just undocumented as such (see the
backlog correction above). Two real, concrete gaps were found and fixed instead of building from
scratch.

### Bug 1: silently-swallowed `AttributeError` disabled the kernel's entire tick loop

**Symptom:** none visible -- this was found by code inspection, not a failing test. No existing
test runs the daemon's real tick loop to completion with a long-enough window to hit it.

**Root cause:** `daemon.py`'s `_system_tick()` called `self.experience.decay_affect(rate=0.02)` --
but `ExperienceKernel` has no `decay_affect` method, only `decay_affect_to_baseline(delta_seconds:
float = 60.0)`. This raised `AttributeError` on *every single tick* (default every 15s) since
Stage 3 landed. A broad `except Exception as e: print(...)` around the whole tick body swallowed
it silently -- and because the exception aborted the `try` block partway through, **every line
after it in the same block never ran either**: `self.persona_manager.auto_activate_if_needed(...)`
and `await self.planner.decide(self.state)`. In production, this meant affect never decayed toward
baseline, persona auto-activation never fired, and the planner was never consulted on a tick --
the tick loop's entire real work was dead code.

**Fix:** `self.experience.decay_affect_to_baseline(delta_seconds=self.interval)` (the daemon's own
tick interval, matching the parameter's semantics).

**Verify:** `pytest -q tests/test_stage3_integration.py::TestDaemonIntegration` (2 tests) --
passed before and after (they were passing "by accident," never having exercised the code past the
crash); full `pytest -q` unaffected.

### Bug 2: `INTERFACES.md`'s privacy contract for this subsystem was never implemented

**Symptom:** none visible via tests (no test asserted on this either way) -- found via direct
reasoning about the acceptance criterion ("without leaking sensitive memory") plus confirming
`NarratorConfig.redact_personal_data` (loaded from `Identity.yaml`'s
`narrator_episodic_layer.logs.redact_personal_data: true`) had zero call sites checking it anywhere
in `narrator.py`.

**Root cause:** `episodic_entries` (Narrator) and `experience_snapshots` (Experience Kernel) are
maintained via their own plain `sqlite3` schema/queries, entirely bypassing the pipeline that
protects everything in the `memories` table (`ConsentGate`, `memory_rules.py`, `redaction_engine.py`).
Episode narratives aren't built from raw `memories` rows (confirmed: they're built from
GlobalWorkspace event payloads and template strings) -- but several fields *are* arbitrary,
caller-supplied free text with no redaction of their own: `ExperienceKernel.set_attention()`'s
`target`, `add_goal()`/`complete_goal()`'s `goal`, `update_affect()`'s `emotion`, `set_context()`'s
string `value`s, and `NarratorEngine`'s own `generate_observation_episode()`/
`generate_reflection_episode()` `content` params. Any of these can end up verbatim in a persisted,
exportable daily/weekly reflection narrative or in `GET /api/self`'s "safe-to-share" snapshot.

**Fix:**
- Added `redact_pii(text)` to `redaction_engine.py` -- deliberately **not** reusing
  `bartholomew.kernel.memory.privacy_guard.SENSITIVE_KEYWORDS` (name/address/location/phone/email/
  bank/password/routine/health/private/account). That list is a *consent-prompt* trigger (a human
  confirms before storing, so false positives are cheap); this needed *silent, automatic*
  redaction with no human in the loop, and those keywords are also just ordinary vocabulary a
  wellness-focused assistant's own self-model legitimately uses constantly -- confirmed directly:
  reusing that list broke two existing tests by mangling "Answer health question" into
  "Answer **** question" and "user question about health" into "...about ****". Narrowed to
  matching only concrete, unambiguous PII *shapes* instead (email addresses, phone numbers,
  SSN-pattern digit groups) -- fewer false positives for content this subsystem is expected to
  legitimately handle.
- `ExperienceKernel`: `set_attention()`, `update_affect()`, `add_goal()`/`complete_goal()` (redacted
  consistently on both sides so a caller's raw text still matches for removal),
  `set_context()` (string values only) now redact unconditionally -- no existing config toggle for
  this at the Experience Kernel level, and `self_snapshot()`/`GET /api/self` are documented as
  "safe-to-share" with no legitimate reason to expose raw PII there.
- `NarratorEngine`: added a `_redact()` helper gated by the existing `NarratorConfig.redact_personal_data`
  flag (now actually doing something), applied to the free-text fields in
  `generate_affect_episode`/`generate_attention_episode`/`generate_goal_added_episode`/
  `generate_goal_completed_episode`/`generate_observation_episode`/`generate_reflection_episode`.
  Redundant with the Experience Kernel-level redaction for event-sourced fields (defense in depth,
  harmless since redaction is idempotent) but is the only protection for the two narrator-only
  entry points (`generate_observation_episode`/`generate_reflection_episode`) that don't route
  through Experience Kernel at all.

**Verify:**
```bash
pytest -q tests/test_narrator.py::TestPIIRedaction tests/test_narrator.py::TestReflectionNarratives::test_daily_reflection_redacts_pii
pytest -q tests/test_experience_kernel.py::TestExperienceKernelPIIRedaction
pytest -q                          # full suite still fully green (0 failures)
```

### Not done in this pass (explicitly out of scope, noted above in the backlog item)

- No dedicated "scenario replay" test harness (the acceptance criterion's "run a scenario replay"
  step) -- `test_stage3_integration.py::TestFullLifecycle` is the closest existing precedent. **This
  gap was later closed** — see item 11.9 above, `tests/test_scenario_replay.py`.
- The two reflection pipelines (`daemon.py`'s `ReflectionGenerator` vs. `narrator.py`'s
  `generate_*_reflection_narrative`) were not reconciled — both still exist independently.
  **Status as of 2026-07-28 (corrected; see item 11.8's 2026-07-28 note above and
  `COGNITIVE_RUNTIME.md`'s reflection-ownership section): item 11.8 (2026-07-21) made
  `daemon.py` append `narrator.py`'s output to `ReflectionGenerator`'s content, but this is
  concatenation, not unification — both pipelines still run independently and neither is the
  codebase's enforced single authority. The approved target architecture (`ReflectionGenerator`
  authoritative, `NarratorEngine` supplementary) has not yet been implemented in code.**
- No date-range query against the `memories` table itself was added for narrator/reflection use
  (only `episodic_entries` supports this today); if a future reflection feature needs to summarize
  actual stored memories rather than kernel-internal episodes, that query would need to be written
  and would need to route through `ConsentGate` filtering, which nothing does automatically outside
  `retrieval.py`'s code paths.
