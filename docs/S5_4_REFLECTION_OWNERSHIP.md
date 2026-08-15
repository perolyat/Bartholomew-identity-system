# S5.4 (part 1) — Closing the reflection-ownership gap

> **Status:** IMPLEMENTED, pending review. **Non-canonical** — a right-sized planning/implementation
> note per `docs/TILT.md`'s vertical-slice discipline, not one of the 14 canonical SSOT docs.
> It changes no canonical document. §6 lists what would need reconciling if this is accepted.

## 1. What this closes

`ROADMAP.md`'s S5.4 row requires that the experience → learning/consolidation loop be built on
reflection composition having **a single authority**, and names the pre-existing gap it must close
first:

> the pre-existing reflection-ownership implementation gap (`ReflectionGenerator` authoritative,
> `NarratorEngine` supplementary — see `COGNITIVE_RUNTIME.md`'s "Reflection ownership" section)

`COGNITIVE_RUNTIME.md`'s "Reflection ownership" section (corrected 2026-07-28) describes the defect
precisely and records the approved target:

> **Current implementation:** `daemon.py`'s `_run_daily_reflection()`/`_run_weekly_reflection()` call
> **both** `ReflectionGenerator` … **and** `narrator.py`'s `generate_daily_reflection_narrative()` …
> and string-concatenate the two outputs. **This is concatenation, not architectural unification.**
>
> **Approved target architecture (recorded 2026-07-28):** `ReflectionGenerator` is the authoritative
> owner of reflection composition and final reflection output. `NarratorEngine`'s episodic narrative
> is supplementary evidence supplied *to* that authoritative process — not an independent, co-equal,
> or competing reflection pipeline.

This note implements exactly that target. **It does not implement the rest of S5.4** (the
experience → candidate-learning → provenance/confidence → consolidation loop), which is the larger
half and remains to be built on top of this.

## 2. Why this piece was safe to build now

The two decisions outstanding at the start of the session — whether the vision document's thesis is
right, and what may cross the local/cloud boundary — do **not** gate this work:

- The ownership target was recorded and approved on 2026-07-28, months before the vision document
  existed. It is canonical, and does not derive from the vision document's thesis.
- Reflection composition is entirely local. Nothing here sends anything to a cloud provider, and
  nothing here depends on where that boundary is drawn.

So this is buildable without pre-empting either decision.

## 3. The change

**Before:** the daemon generated a reflection, then separately generated a narrator document, then
joined them with `content = f"{content}\n\n---\n\n{episodic_narrative}"`. Two independently composed
documents, two top-level titles, no single authority.

**After:** the daemon collects the narrator's episodic material **first** and passes it into
`ReflectionGenerator` as `episodic_evidence`. One authority composes one document.

| File | Change |
|---|---|
| `identity_interpreter/orchestrator/prompt_composer.py` | Both compose functions take `episodic_evidence`. New `_build_episodic_evidence_block()` renders it into the prompt framed explicitly as *source material to interpret, not text to copy*. The daily prompt's "Notable Events" section now instructs grounding in that evidence. |
| `identity_interpreter/adapters/reflection_generator.py` | `generate_daily_reflection()` / `generate_weekly_audit()` take `episodic_evidence` and pass it to composition. New `_compose_fallback_sections()` folds evidence into the fallback templates. `meta["episodic_evidence_present"]` records whether evidence was supplied, on every path. Class docstring states the ownership rule. |
| `bartholomew/kernel/daemon.py` | Evidence collected before generation and passed in; the concatenation removed from both daily and weekly paths. New module-level `_compose_episodic_section()` for the kernel's last-resort template. |

### 3.1 The fallback question, and why it is not concatenation again

If the LLM is unavailable there is no model to interpret the evidence — but discarding it would lose
the only real record of what happened, since the fallback templates are entirely generic (their
"Notable Events" section literally reads *"(Future: chat highlights, emotional events, user
activities)"*).

The evidence is therefore **folded into the fallback document as a subsection**: its top-level title
is dropped and its remaining headings are demoted one level, emitted under `## Recorded Episodes`.
The result is one reflection containing a sourced section — not two reflections joined by a rule.
That distinction is the entire point of the ownership rule, so it is asserted directly: several
tests check the output contains exactly one `# ` title.

## 4. A consequence worth stating plainly

Under concatenation, episode text appeared in the reflection **verbatim** — guaranteed. Under single
authority, when a real model composes, it *interprets* the evidence, so specific episode strings may
not appear literally. That is correct behaviour and the point of unification, but it is a real
behavioural change:

- The old tests asserted literal substring presence on the model path. Re-pinning that would re-pin
  concatenation by another route.
- The new tests assert the evidence was **supplied** (`episodic_evidence_present`), that it reaches
  the prompt (unit tests on `prompt_composer`), and assert literal presence only on the
  template-fallback path, which genuinely does include it verbatim.

## 5. Two findings from building this

Both are pre-existing, neither is introduced here, and together they mean **the LLM path for
reflections is effectively dead today** — every reflection is template-composed, by one of two
independent routes:

1. **`ReflectionGenerator` cannot be constructed on a headless host.** Its `__init__` calls
   `Orchestrator(identity_config=...)`, which builds `ContextBuilder → MemoryManager →` OS keystore
   and raises `RuntimeError: Encryption is required but keystore initialization failed`. Reproduced
   directly in this container. `docs/SESSION_HANDOFF.md` already flags `identity_config=` as the
   wrong wiring and points at `model_identity_config=` — but `ReflectionGenerator` genuinely needs
   `orchestrator.context` for `build_prompt_context()`, so it cannot simply switch. This needs a
   real fix (likely: make the memory/context dependency optional or lazily built), and is left
   untouched here deliberately — it is a separate concern from ownership, and guessing at it would
   have widened this change well past its scope. The daemon degrades to its last-resort template,
   which is why that path was made to carry the evidence.

2. **With `backend="stub"`, generation always fails its own safety check.** Observed in the test
   suite: `Safety violation detected … attempting redraft` → `Redraft still violated safety
   policies` → internal template fallback. The daemon hard-codes `backend="stub"` in both reflection
   paths, so even where construction succeeds, no real model composes a reflection. PR #53 wired
   real model routing for chat; the reflection loop was never moved onto it.

**Neither is fixed here.** Both are recorded in `docs/SESSION_HANDOFF.md` as next-session work. The
ownership contract is correct and tested regardless of which path composes — but note that until
finding 1 or 2 is fixed, the model-composition half of this change is not exercised in a real
deployment.

## 6. Canonical reconciliation required if accepted

No canonical document has been modified.

- **`COGNITIVE_RUNTIME.md`** — the "Reflection ownership" section's *"Current implementation"*
  paragraph now describes superseded behaviour. Its "Approved target architecture" paragraph is
  unchanged and is what the code now does.
- **`ROADMAP.md`** — S5.4's row can record the ownership half as closed; the sub-stage as a whole
  is **not** complete (the learning/consolidation loop is the larger remaining half).
- **`MASTER_PLAN.md`** — if it tracks the reflection-pipeline gap, that entry changes state.

## 7. Tests

- `tests/test_reflection_ownership.py` (new, 13 tests) — evidence reaches the prompt; absent evidence
  leaves no empty scaffold; evidence framed as source material; fallback folding drops the evidence
  title, demotes headings, introduces no second `# ` title, and yields nothing for empty or
  title-only evidence; both fallback templates carry evidence and remain single-titled.
- `tests/test_reflection_narrative_integration.py` (updated) — retargeted from the concatenation
  contract to the ownership contract; adds single-title assertions for daily and weekly, and a test
  that a narrator failure degrades to a reflection without evidence rather than to no reflection.
- `tests/test_scenario_replay.py` — meta key updated.

Full suite: **1516 passed, 3 failed, 2 skipped**. The 3 failures are in
`tests/smoke/test_packaging_contract.py`, are environment-caused (the project is not pip-installed as
a distribution in this container, so declared console scripts are absent), and were verified to fail
identically on the untouched base branch.
