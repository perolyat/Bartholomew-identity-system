# Session handoff — 2026-08-15 (session 2)

> Working note for whoever picks this up next (human or agent). Non-canonical.
> Delete or overwrite freely; it describes a moment, not a decision.
>
> Supersedes the previous handoff note of the same name. The previous session's content is
> preserved below under "Carried forward" where it is still true.

## Where things stand

`main` is at the merge of PR #52. Branch `claude/project-status-review-pfpysy` carries the vision
document + hybrid model routing, open as **PR #53**. This session's work sits on
`claude/project-status-review-j3es1c`, branched **from pfpysy** (not from main), open as a PR
targeting pfpysy — so it stacks on step 1 rather than duplicating it.

| PR | State | What |
|---|---|---|
| #51 | merged | Serve the minimal UI from the API app at `/ui` |
| #52 | merged | Real model path: anti-fabrication fix, live wiring, UI hierarchy |
| #53 | open | Vision document + hybrid model routing (step 1) |
| this branch | open | S5.4 part 1 — reflection-ownership gap closed |

## Read these first, in this order

1. **`docs/VISION_AND_PERSONAL_DEPLOYMENT.md`** — what Bartholomew is for, who for, the deployment
   target, and the six-step sequence everything is measured against.
2. **`docs/TILT.md`** — execution-sequencing priority, canonical and binding. Vision §8 argues about
   TILT's *precondition* rather than overriding it, and carries an exit condition. **Read §3 of this
   note before accepting that argument** — its exit condition has a defect.
3. **`docs/S5_4_REFLECTION_OWNERSHIP.md`** — what this session built and what it deliberately left.
4. **`MASTER_PLAN.md`** "Next 3 Moves" — still says real-world use of slice 1 is next. Nothing
   canonical has been changed.

## What this session did

Closed the **reflection-ownership gap** — the half of `ROADMAP.md` S5.4 that S5.4's own row names as
a prerequisite ("S5.4 depends on reflection composition having a single authority").

`daemon.py` used to call `ReflectionGenerator` and `NarratorEngine` independently and
string-concatenate their outputs, which `COGNITIVE_RUNTIME.md` named exactly: *"concatenation, not
architectural unification."* Now the narrator's episodic material is collected first and passed into
`ReflectionGenerator` as `episodic_evidence` — one authority composes one document. The full design
note, including the fallback design and the behavioural consequence, is
`docs/S5_4_REFLECTION_OWNERSHIP.md`.

**Not done: the rest of S5.4** — the experience → candidate learning → provenance/confidence →
Governance → consolidation loop. That is the larger half and is the obvious next piece of work.

## Two decisions still waiting on the user

The user was asked both and answered: *"i don't really understand these decisions but i trust you to
build the system yourself."* That is a delegation to keep building, **not a ratification**. Both are
still open, and both were routed around rather than resolved — the work above needs neither.

1. **Is the vision document's thesis right?** Still unratified. Nothing built so far depends on it:
   the reflection-ownership target was approved 2026-07-28, long before the vision document existed.
   The moment work *does* depend on it (steps 3–6: deployment, voice, initiative, reach), this needs
   a real answer.
2. **The local/cloud data boundary** (vision §9 open question 2). Still unratified. Cloud remains off
   without an API key, and `Identity.yaml` routes `general` to the local model regardless, so nothing
   personal has left the device. **Recommended default when it is decided:** anything ambient,
   routine, or touching stored personal facts stays local; genuinely hard reasoning may go cloud
   within the budget cap. This needs a `DECISIONS.md` entry before cloud is enabled in earnest.

## Three defects that are easy to reintroduce

Carried forward from the previous session, all still true:

- **A failed model backend must never answer with mock text.** `ModelRouter.route()` used to end in
  an unconditional `"Mock response for: ..."` return, so any provider failure produced a fabricated
  reply indistinguishable from a real one. Only the explicit `stub` backend may return non-model
  text; everything else raises `ModelBackendError`. Pinned by `tests/test_model_backend_honesty.py`.
  **Do not add a fallback that returns text on failure.**
- **The budget ledger fails closed.** `identity_interpreter/orchestrator/budget_ledger.py` computes
  `budget_exhausted` from recorded spend; an unreadable ledger reports *exhausted* rather than
  allowing uncapped spend. An unpriced model is costed at the most expensive known rate, never free.
  Keep that direction.
- **Never forward sampling parameters to a cloud Claude model.** `Identity.yaml` carries
  `temperature: 0.2` / `top_p: 0.9` and `select_model()` returns them for every model, but current
  Claude models reject them with a 400. `cloud_llm.py` deliberately drops them; a test asserts their
  *absence*.

## The fourth trap is worse than recorded — and now has a reproduction

The previous handoff said: *"`Orchestrator(identity_config=...)` is the wrong wiring … use
`model_identity_config=`."* That is right, and the reflection path violates it — with a concrete
consequence, reproduced directly in a headless container this session:

```
ReflectionGenerator.__init__
  -> Orchestrator(identity_config=...)
  -> ContextBuilder -> MemoryManager -> OS keystore
  -> RuntimeError: Encryption is required but keystore initialization failed
```

**`ReflectionGenerator` cannot be constructed on a headless host at all.** It is not a simple
one-line fix: it genuinely needs `orchestrator.context` for `build_prompt_context()`, so it cannot
just switch to `model_identity_config=`. It needs the memory/context dependency made optional or
lazily built. Left untouched this session on purpose — it is a separate concern from ownership.

## The reflection LLM path is dead today — two independent causes

Worth knowing before building the rest of S5.4 on top of it:

1. The keystore crash above (headless hosts).
2. **`backend="stub"` always fails its own safety check.** Observed in the suite: `Safety violation
   detected … attempting redraft` → `Redraft still violated safety policies` → template fallback.
   `daemon.py` hard-codes `backend="stub"` in both reflection paths, so even where construction
   succeeds, **no real model composes a reflection.** PR #53 wired real routing for chat; the
   reflection loop was never moved onto it.

Every reflection produced today is therefore template-composed. The ownership contract is correct
and tested either way, but the model-composition half of it is not exercised in a real deployment
until one of these is fixed. **Fixing #2 is small and high-value** — it is probably the single best
next action, and it is what would let the rest of S5.4 be judged on real output.

## A defect in vision §8's exit condition (raised, not resolved)

Vision §8 defers real-world use of slice 1, arguing slice 1 has not met TILT's precondition because
the local 7B model is too weak for feedback to describe the architecture rather than the model. Its
exit condition, clause 1:

> Step 1 (hybrid model routing) has landed, **and** ordinary conversation is fluent enough to use
> without effort

Step 1 has landed. But `Identity.yaml:51` routes `general: ["Mistral-7B-Instruct-GGUF-Q4_K_M"]` —
no cloud entry — and PR #53's own verification table confirms *cloud configured → general: local*.
**Step 1 did not change ordinary conversation quality at all**; it moved `safety_review` and `code`.

So clause 1 cannot be discharged by step 1 landing. It resolves only one of three ways, and each is
a real decision: route `general` to cloud (contradicts vision §6's economics), get fluency from a
larger *local* model (real work, not in the §7 sequence), or concede the clause was mis-specified.
§8 itself says that deferring again once the condition is met "is a new decision requiring its own
record" — this is adjacent to that and deserves the same treatment. **This is the highest-leverage
open question in the project**, because TILT is canonical and it is the only thing holding real use
back.

## Known-inaccurate canonical text (flagged, not corrected)

- **`RISKS.md`** describes `/api/water/log` and `/api/water/today` as "live, working, legacy code".
  Neither endpoint exists; only the UI panel is real, and it is labelled accordingly.
- **`MASTER_PLAN.md`** P0 item 1 records a `fastapi>=0.104,<0.121` ceiling; `requirements.txt` is
  `>=0.134,<0.141` (raised for CVE-2026-54283).
- **`COGNITIVE_RUNTIME.md`**'s "Reflection ownership" *"Current implementation"* paragraph now
  describes superseded behaviour (see `docs/S5_4_REFLECTION_OWNERSHIP.md` §6).

## Still outstanding from earlier sessions

The `docs/TILT.md` amendment on polish — that polish is acceptable when it carries a named
justification, an honest read of near- and long-term value, and a comparison against the alternative
use of the time. Agreed in principle, never written.

## Running it locally

```
ollama pull mistral:7b-instruct      # local model; cloud stays off without an API key
uvicorn app:app --port 5173
```

Then `http://localhost:5173` (redirects to `/ui/`). Check `/api/health` — `"model_real": true` means
a real model is wired; `false` means the stub, and nothing observed afterwards means anything.

**Test environment note:** this container needed `pip install -r requirements.txt
-r requirements-dev.txt`, plus `pip install --ignore-installed cryptography` to get past a Debian
system-package conflict. Three `tests/smoke/test_packaging_contract.py` failures are expected unless
the project is pip-installed as a distribution — they fail identically on an untouched checkout.

## Suggested next session prompt

```
Repo: Bartholomew-identity-system.
Branch: claude/project-status-review-j3es1c (stacked on pfpysy / PR #53).

Read docs/SESSION_HANDOFF.md, then docs/S5_4_REFLECTION_OWNERSHIP.md, then
docs/VISION_AND_PERSONAL_DEPLOYMENT.md and docs/TILT.md.

Governance: the 14 canonical docs are the authority on project state. Where any
other file disagrees, they win. If code contradicts a canonical statement,
surface the contradiction rather than silently picking a side.

Work, in this order:

1. Move the reflection loop onto the real model path. daemon.py hard-codes
   backend="stub" in both reflection paths, and stub output fails its own
   safety check every time, so no real model has ever composed a reflection.
   This is small and unblocks judging everything else on real output.

2. Fix ReflectionGenerator's construction on headless hosts. It calls
   Orchestrator(identity_config=...), which builds ContextBuilder ->
   MemoryManager -> OS keystore and raises. It needs orchestrator.context for
   build_prompt_context(), so make that dependency optional or lazy rather
   than switching to model_identity_config=.

3. Then the rest of S5.4: the experience -> candidate learning ->
   provenance/confidence -> Governance/review -> consolidation loop described
   in COGNITIVE_RUNTIME.md. The reflection-ownership prerequisite is done.

Do not reintroduce: mock text on model-backend failure; a budget ledger that
fails open; temperature/top_p forwarded to a cloud Claude model; the
concatenation of NarratorEngine output onto ReflectionGenerator output.

Two decisions remain unratified and are noted in the handoff (the vision
thesis, and the local/cloud data boundary). Nothing in steps 1-3 depends on
either. Also read the handoff's section on the defect in vision §8's exit
condition before doing any work that assumes real-world testing stays
deferred.
```

## Carried forward

Everything in the previous handoff not restated above is either superseded by this note or still
accurate as written there; that note's content is preserved in git history at commit `622c179`.
