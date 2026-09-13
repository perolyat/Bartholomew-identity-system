# EXEC-01 — Goal-to-Plan Deliberation

**Status:** implemented, not merged. Awaiting the User Approval Gate.
**Baseline:** `origin/main` at `31279b9` (the merge of PR #107 / FND-04).
**Scope:** Executive cognition only. No governance, actuation, verification,
recovery, memory, ECI, identity or consent system was redesigned.

---

## 1. What the gap actually was

Bartholomew could carry out a plan. He could not make one.

The Executive's whole understanding of an instruction was one line —
`seam.py`'s `intent: TaskIntent = parse_task(instruction or "")` — and
`parse_task` is a set of five regular-expression recognisers that map phrases the
person already used onto capabilities they already named. `intent.py` said so
about itself, verbatim:

> **Scaffolding status.** As with `task_intents.py`, the pattern set is POC
> scaffolding, not the definition of what Bartholomew understands.

The consequence was reproducible and stark. The same goal, expressed two ways:

| What the person says | What Bartholomew did |
|---|---|
| `Open notepad and then type "Shopping list"` | a two-step governed plan |
| `Start a shopping list for me.` | *"Is 'a shopping list for me' an application, a file path, or a URL?"* |

The second sentence names an **outcome**. The recogniser matched `start` as a
launch verb, found that `shopping list for me` was not a single allowlisted
application key, and asked the person to name a tool. Nothing was wrong with that
behaviour on its own terms — it is the correct refusal for a recogniser that must
never guess — but it meant the person had to do the planning.

Two further facts made this the *dominant* gap rather than one of several:

* **Multi-step planning followed only stated steps.** `intent.py:161` is explicit
  that a plan is "a sequence the user actually asked for, never a decomposition
  the executive invented", and `plan.py` maps intent steps to plan steps 1:1.
  There was no code path that could produce a step nobody mentioned.
* **Capability reasoning ran the wrong way round.** `selection.select_capability`
  takes a capability *identifier* and answers "may this device be asked for it?".
  Nothing answered "what would this outcome require?", and nothing enumerated
  what the device could do at all.

## 2. Which Executive path is authoritative

This needs saying plainly because the repository contains more than one thing
called a planner.

* **`bartholomew/executive/` is the Executive** for a device task. Its two entry
  points are `run_executive_task_through_runtime_contract()` and
  `advance_executive_task_through_runtime_contract()`, and the only non-test
  callers are `POST /api/operator/tasks` and
  `POST /api/operator/tasks/{task_id}/advance`.
* **`bartholomew/kernel/planner.py` is not a planner.** `Planner.decide()`
  returns `None` unconditionally and has done since proactive nudges moved to the
  scheduler; two tests pin that inertness. Its live method is
  `handle_skill_request()`, a single-skill dispatcher for the chat surface. It
  plays **no role** in EXEC-01 and nothing was attached to it.
* **The chat surface does not reach the Executive at all.**
  `kernel/runtime_contract.py` contains no reference to the executive package.
  This is a real limitation, recorded in §8; EXEC-01 did not change it.

## 3. What was repaired

Two new modules inside the existing Executive, one adapter outside it, and four
small edits to existing files. Nothing was removed; `intent.py` was not modified.

### `executive/capability_catalogue.py` — capability awareness

Builds a bounded, machine-readable description of what this device can *actually*
do: the capability identifier, the one-sentence `CapabilityDescriptor.summary`
already written for approvers, its risk class, its approval requirement, its
parameter shapes in prose, and whether its outcome can be read back.

It adds no third notion of what a capability is. Availability is
`select_capability`'s own verdict, so a capability the catalogue offers is
exactly a capability `plan.py` will accept. Allowlist **keys** are included
(an executive that cannot see `notepad` cannot propose starting it); the
executable **paths** behind them are not.

### `executive/deliberation.py` — the cognition step

Turns a described outcome into a bounded, validated `TaskIntent` — the *same*
contract `parse_task` returns, which is the whole integration strategy. Because
the output type is unchanged, every downstream guarantee applies to a deliberated
plan exactly as it applied to a recognised one, with no change to any of them.

The order of operations is the safety argument:

1. `parse_task` reads the instruction literally. **If that produced an actionable
   reading, it is the answer** — unchanged, no model consulted.
2. A request naming no referent ("open it") keeps the recogniser's question.
   Deliberation resolves *how* to reach a goal, never *what* was being pointed at.
3. A request outside the capability vocabulary (`delete`, `install`, `send`,
   `buy`) keeps the recogniser's refusal. Cognition never reopens one.
4. Only then is the model asked.

Which yields the property worth stating on its own:

> **Deliberation can turn a refusal into a proposal. It can never turn one
> proposal into a different proposal.**

Every instruction that worked before EXEC-01 produces a byte-identical plan
afterwards, decided by the same regexes, with no model and no network involved.

### `integration/deliberation_adapter.py` — the port implementation

`tests/test_w03b_no_bypass.py` forbids the executive package from importing
`socket`, `http`, `urllib`, `requests`, `httpx` or `aiohttp`. A model client is
exactly such a thing. So the executive declares a one-method `Protocol`
(`DeliberationPort.deliberate(prompt) -> str`) and never implements it; the
implementation is an adapter over the **existing** `ModelRouter`, which already
chooses between the local Ollama adapter and the opt-in, budget-capped cloud one.

The executive therefore cannot reach a model on its own. It can only use one a
caller handed it, and installation is explicit rather than automatic on startup:

```python
from bartholomew.integration.deliberation_adapter import install_deliberation_port

# `orch` is the identity_interpreter Orchestrator the API already builds;
# `orch.router` is the ModelRouter it already owns. `kernel` is the ctx the
# operator route already passes to the executive seam.
install_deliberation_port(kernel, orch.router)
```

**Nothing in the shipped code calls this.** That is deliberate, and it matches
how this repository already gates consequential capability — `voice.spoken_output`
defaults to false, the ECI sits behind `BARTH_ECI_ENDPOINT_AUTH`. Giving
Bartholomew the ability to reason his way to a course of action on somebody's
computer is an operator's decision, not a side effect of a provider being
reachable. Until someone makes it, `POST /api/operator/tasks` passes a `kernel`
with no `deliberation_port`, `getattr` returns `None`, and the executive reads
instructions literally exactly as it did before EXEC-01.

### Edits to existing files

| File | Change |
|---|---|
| `executive/seam.py` | resolves the device *before* understanding the instruction (deliberation reasons from real capabilities); calls `_understand` instead of `parse_task`; accepts an optional `deliberation` port |
| `executive/plan.py` | `Plan.deliberation` carries cognition provenance; read by the explanation and the audit, by nothing that decides anything |
| `executive/explanation.py` | the person is told, in plain words, which steps were worked out rather than asked for |
| `executive/__init__.py` | exports |

## 4. What role a model plays, and what it is not allowed to be

A model is a source of semantic reasoning about the person's goal. It is not a
source of authority, of permission, or of truth about what exists.

Everything it returns is treated as an untrusted proposal about *intent* and is
put through deterministic validation before it can become a step:

| Check | Enforced by |
|---|---|
| the capability exists | `CapabilityKind(...)`, a closed enum |
| this device declared it | the catalogue, i.e. `select_capability` |
| the executive may *infer* it | `INFERABLE_CAPABILITIES` (see below) |
| the parameters are valid | the **real** `actuation.parameters.validate()`, against this device's **real** allowlists |
| the plan fits | `MAX_PLAN_STEPS` |

A hallucinated capability is not "close enough" — it is refused, and the person
is asked. An `app_id` the operator never allowlisted is refused by the allowlist
itself, not by a list of known-bad names. The steps that survive carry the
validator's **canonical** parameters, so the envelope fingerprints and approves
exactly the shape that was checked.

Stated confidence may make the executive *more* cautious and never less: `low`
becomes a question, and there is no value of it that permits anything.

### Inference is held to a stricter standard than instruction

`INFERABLE_CAPABILITIES` is the one genuinely new judgement in EXEC-01. It draws
a line between a step the **person named** and a step the **executive inferred**:
a named step carries the person's own authority for its scope, an inferred one
carries only Bartholomew's reading of an outcome.

Two capabilities are therefore never inferred. `clipboard_read` returns the
person's content *to Bartholomew*, and no statement of an outcome implies consent
to be read. `accessibility_action` reaches into a live UI tree, where the cost of
a mistaken element is not visible in the request. Both remain fully available to
an instruction that names them.

## 5. How context reaches cognition without becoming authority

Recalled memory reaches the prompt through `evidence.render_evidence_for_prompt`
— the delimited, explicitly non-instructional frame W03-D's memory-poisoning
contract already required. Nothing else from the system reaches the prompt: no
executable paths, no device identifiers, no credentials, no store contents.

Two things are true here and they need separating, because `evidence.py`'s
original sentence — "a poisoned row and a benign row produce the same plan" —
was written when nothing sent recalled text to a model, and EXEC-01 is the first
thing that does.

**Evidence reaches no part of the deterministic path.** It is not consulted when
the catalogue is built, when a capability is checked against the device, when
parameters are validated, when the plan bound is applied, or when the inference
rule is decided. It cannot make an invalid plan valid, introduce a capability
that does not exist or was not declared, widen a bound, or authorise anything.

**Evidence is in the prompt, so it can influence which valid plan a model
proposes.** A note that the person keeps lists in WordPad may well produce a
WordPad plan rather than a Notepad one. That is context doing its job. The
guarantee is not that evidence changes nothing — it is that evidence changes
nothing the validation layer would not have allowed from any source, and that it
confers no authority at all.

`tests/test_exec01_adversarial.py` proves both halves separately, the second
against a port that actually reads the prompt and obeys what the poisoned memory
told it to do. The frame those rows are rendered inside is also now escape-proof:
a row containing the literal close marker used to end the frame early, which is
fixed at the frame's owner and pinned by regression tests.

## 6. Why cognition still cannot authorise anything

Unchanged from W03-B, and now proved for the deliberated path too:

* the executive package never calls `grant_action_approval` (AST-enforced);
* a proposal stops at `pending_approval` and waits for a human;
* the Parking Brake is read **before** the instruction is understood, so an
  engaged brake means no model is consulted at all;
* a step advances only when the previous one **verified**, where a device's own
  report of success with nothing read back is `unknown`;
* `unknown` is never rendered as success and never blindly repeated;
* a failure recovers by a defined decision, and a re-proposal carries a new
  fingerprint that the earlier approval cannot authorise.

## 7. Evidence

`tests/test_exec01_goal_to_plan.py` holds the before/after pair on the *same*
sentences, so the two halves cannot drift into testing different things.
`tests/test_exec01_adversarial.py` covers hallucinated capabilities, undeclared
capabilities, non-allowlisted applications/URLs/paths, submit-key smuggling,
scope expansion, plan-length bounds, malformed and low-confidence output, vague
referents, and both prompt-injection surfaces.
`tests/test_exec01_vertical_slice.py` walks one outcome-level goal through the
real envelope, real Parking Brake, real approval, real verification and real
recovery, and asserts the fourteen acceptance conditions in order.

## 8. What this does not do

* **It does not reach the chat surface.** `/api/chat` has no path to the
  Executive; "start a shopping list" typed there still falls through to a
  conversational reply. EXEC-01 closed the goal-to-plan gap *within* the
  Executive; connecting the chat surface to it is separate, unstarted work.
* **It does not expand Bartholomew's limbs.** The capability vocabulary is the
  same nine kinds. Cognition got better at using them; there are no new ones.
* **Deliberation provenance is not persisted on the task row.** It reaches the
  `ActionReflection` audit trail, which is the durable record of a governed
  decision; adding a column would need migration machinery this repair did not
  otherwise require. A plan reloaded for `advance` carries `None` there, and
  `advance` reads nothing from it.
* **No model is configured by default.** A deployment that does not call
  `install_deliberation_port` keeps exactly the pre-EXEC-01 executive.
* **It does not claim general competence.** It claims that outcome-level goals
  inside the supported Windows capability domain now become bounded, validated,
  governed proposals.
