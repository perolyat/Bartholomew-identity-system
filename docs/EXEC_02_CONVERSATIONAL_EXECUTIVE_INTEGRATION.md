# EXEC-02 — Conversational Executive Deliberation Integration

**Status:** **merged.** Approved by Taylor at the User Approval Gate and merged to
`main` in PR #115 on 2026-09-18.
**Provenance:** reviewed head `c3f1c5c`; merge commit / `main` after merge `25cfd90`.
**Baseline it was built on:** `origin/main` at `c5cb3a0` (the merge of PR #114).

> **Written at merge, not retrofitted.** EXEC-01's header read *"implemented, not
> merged"* and was stale from the moment PR #108 merged — a defect the Project
> Control & Documentation Reset had to correct, and which `RISKS.md` records as a
> project-control risk in its own right. This header was updated in the same hour
> as the merge, for that reason.
**Scope:** the conversational surface's route into the Executive. No governance,
actuation, verification, recovery, memory, ECI, identity or consent system was
redesigned, and the Executive's own cognition was not changed at all.

---

## 1. What the gap actually was

`docs/EXEC_01_GOAL_TO_PLAN_DELIBERATION.md` §8 states it in one line, and it was
still true at `c5cb3a0`:

> **It does not reach the chat surface.** `/api/chat` has no path to the
> Executive; "start a shopping list" typed there still falls through to a
> conversational reply.

Read precisely, because the shape of the gap decides the shape of the repair.
Chat **already traverses the Runtime Contract's Executive stage** — it builds a
`CandidateAction` that Governance genuinely consumes. What that stage did not
reach was `bartholomew/executive/`'s goal-to-plan deliberation. The gap was
**depth in a stage that exists**, not a missing brain, and EXEC-02 is written
that way throughout.

Measured at `c5cb3a0`, on the code rather than from the document:

| Sentence | `task_intents` | `objective_intents` | `executive/intent.parse_task` | What chat did |
|---|---|---|---|---|
| `add a task to ring the roofer` | claims it | — | — | governed task creation |
| `Open notepad` | — | — | `windows.launch_app` | **conversational reply** |
| `Start a shopping list for me.` | — | — | ambiguity | **conversational reply** |
| `Sort these files out for me.` | — | — | ambiguity | **conversational reply** |

Rows two to four are the class this package is about: requests that genuinely
name an outcome on the person's machine, for which the Executive already had
either a literal reading or a deliberation path, reaching neither.

## 2. The decision seam, and why it is regexes rather than a model

`bartholomew/kernel/goal_intents.py` is a pure recogniser — no I/O, no clock, no
persistence, no model call — and a direct sibling of `task_intents.py`,
`forecast_intents.py` and `objective_intents.py`. It answers exactly one
question: *is this utterance a request for an outcome on the person's machine?*

A goal is claimed only when **both** halves are present:

* a **request construction** — an **action verb** from the closed `_ACTION_VERBS`
  set, optionally preceded by a way of addressing the ask to him ("can you",
  "please", "I need you to", "help me"); **and**
* a **device-domain referent** — one of a closed noun set (file, folder,
  document, note, window, desktop, clipboard, browser, tab, app, …).

**The action verb is required, and the address alone is never sufficient.**
There is one action vocabulary, used by every construction, so "can you X" and
a bare "X" are held to the same standard and cannot drift apart. *A first cut
matched the auxiliary alone*, which made "can you recommend a browser", "would
you say this app is good", "can you tell me where my files are" and "will you
remember my notes" into goals — questions answered with a machine plan, the
exact false positive this module exists to prevent. Raised by automated review
on PR #115, confirmed, and pinned by `TestAnAuxiliaryIsNotAnInstruction`,
including a test of the *property* that the two constructions accept and reject
the same verbs.

Both, never either:

| Utterance | Request | Referent | Verdict |
|---|---|---|---|
| `Sort these files out for me.` | yes | `files` | **goal** |
| `Sort out what you think about Tolstoy` | yes | none | conversation |
| `Can you recommend a browser?` | address, no action verb | `browser` | conversation |
| `The files are a mess.` | none | `files` | conversation |
| `Do you remember the document I mentioned?` | — | — | conversation (excluded outright) |

The alternative — asking a model "is this a task?" — is the "everything goes to
the planner" behaviour the brief rules out, and it is the behaviour that turns
conversation into an interrogation. A recogniser written as data can be read,
argued with and pinned by test. `tests/test_exec02_goal_intents.py` pins it.

**Errors are asymmetric and the seam is tuned to the cheap one.** A false
negative costs one goal the person can restate more plainly. A false positive
costs a conversational turn answered with a plan nobody asked for. Every
ambiguous construction therefore resolves to *not a goal*.

**Consequential verbs are claimed on purpose.** "Delete these files for me" is
recognised here and *refused there*, by `executive/intent.py`'s out-of-vocabulary
rule, in the Executive's own words — and `deliberation.py` is forbidden from
reopening a refusal. The alternative, letting it fall through to the model, is
the one outcome that must never happen: a generated sentence about a deletion
that did not occur, which the person cannot tell from one that did.

## 3. Where it sits in the dispatch table, and why last

`_CHAT_DISPATCH` is the ordered table the chat turn already had. EXEC-02 adds
one entry:

```
task  ->  forecast  ->  objective  ->  executive_goal  ->  the model
```

**Last, and that is the whole of its safety posture.** Every deterministic
recogniser keeps first refusal on every utterance, so a known explicit command
still takes the path it has always taken and no simple instruction is sent
through a planner (invariant 3). This entry sees only what already fell through
to the model.

`_handle_executive_goal` has **three gates, in this order, and the first two are
free**:

1. the explicit activation must be installed on the runtime context;
2. `goal_intents.parse_intent` must claim the utterance;
3. only then is the Executive called at all.

A runtime that has not enabled this never even runs the recogniser, so an
unconfigured deployment pays nothing for EXEC-02 existing and behaves
identically to `c5cb3a0`. That is asserted, not assumed
(`TestDisabledByDefault::test_the_recogniser_is_not_even_run_when_disabled`).

## 4. What the handler does, and the list of things it cannot do

It resolves the activation, recognises the goal, and calls

```python
seam.run_executive_task_through_runtime_contract(
    daemon, tenant_id=…, device_id=…, requested_by=…, instruction=intent.instruction,
)
```

— the **same** entry point `POST /api/operator/tasks` calls, with the same
arguments, the same contracts and the same governance. Then it renders what came
back.

The handler selects no capability, builds no plan, parses no instruction,
validates no parameter, mints no approval and executes nothing. Everything that
decides anything is downstream of it. This is enforced by test at the level of
its own source text
(`TestItUsesTheExistingContracts::test_the_kernel_grows_no_second_planner`), and
by `tests/test_w03b_no_bypass.py`'s existing AST rule at the package level.

`kernel/planner.py` was not touched and remains inert.

**The person's own words reach the Executive unrewritten.** A plan must answer
the sentence that was said.

**The turn's `CandidateAction` stays `chat_response`**, for exactly the reason
`_handle_task_intent` records for itself: the Executive action is a *nested*
governed act, evaluated for real at the action envelope against the capability
it actually names, which is the grain the device allowlists and approval
requirements use. Re-deciding it at the chat gate would replace a truthful
account of a refusal with a denial of the whole conversation turn.

## 5. Activation, and the two switches

`bartholomew/integration/conversational_executive.py`. An adapter, like every
module in that package: it owns no cognition, no policy, no store and no
vocabulary. It holds the three pieces of **platform authority** the Executive
requires and a chat turn cannot derive from a sentence — `tenant_id`,
`device_id`, `requested_by` — and hangs them on the runtime context.

| Switch | Default | What it widens |
|---|---|---|
| `BARTH_EXECUTIVE_DELIBERATION` | **off** | installs EXEC-01's `DeliberationPort`, for every Executive caller including the operator console |
| `BARTH_CONVERSATIONAL_EXECUTIVE` | **off** | routes qualifying conversational goals into the Executive at all |
| `BARTH_CONVERSATIONAL_EXECUTIVE_DEVICE_ID` | none | **required** for the above; activation is refused without it |
| `BARTH_CONVERSATIONAL_EXECUTIVE_TENANT_ID` | *resolved, not defaulted* | an **override**; normally leave unset — see below |
| `BARTH_CONVERSATIONAL_EXECUTIVE_REQUESTED_BY` | `chat` | who is recorded as asking |

**Two switches, deliberately, because they widen different things and neither
implies the other.** Deliberation without conversational routing is EXEC-01's
existing posture. Conversational routing without deliberation is a genuine and
safe configuration: chat reaches the Executive, the Executive reads instructions
literally, and an outcome it cannot recognise becomes a question rather than a
plan.

**A model being reachable enables neither** (invariant 8). `install_deliberation_
port` has always refused to be automatic and this does not make it so: a router
being present is a *precondition* here, never a trigger. Pinned by
`TestProductionActivationIsExplicit::test_a_reachable_model_does_not_switch_
anything_on`.

**There is no default device id, and there will not be one.** A device id guessed
from a registry would mean a sentence typed into chat could plan against a
machine nobody named.

**The tenant is resolved, never invented.** `resolve_tenant_id()` reaches the
same answer `routes/operator.py` reaches, by the same reads in the same order:
an explicit override, then this process's runtime binding, then the `local`
sentinel for an unbound process. *A first cut of this module defaulted it to the
string `"default"`, which was a real defect and is worth recording rather than
quietly fixing:* devices enrolled through the platform are tenant-qualified, so
planning under `"default"` would have reported a normally-enrolled machine as
**not enrolled** — the feature would have looked configured and refused every
conversational goal, with a message blaming the enrolment. Raised by automated
review on PR #115, confirmed against `device_action_auth.resolved_tenant_id`,
and pinned by
`TestProductionActivationIsExplicit::test_the_tenant_is_the_platform_s_answer_not_a_synthetic_default`.
The sentinel is duplicated rather than imported (so `bartholomew/` keeps no
import edge to the API bridge) and a second test fails if the two ever diverge.

This closes **R-EXEC01-2**: `configure_from_environment` is called from
`bartholomew_api_bridge_v0_1/services/api/app.py`'s startup, after the
orchestrator rebuild that gives the router its loaded Identity. It is
best-effort and prints the posture the process actually came up in — an
activation that silently did not happen is the worst of the available outcomes.

## 6. Cognition is still not authority

Nothing here grants anything. A step proposed from a conversational goal goes
through the same action envelope, the same Parking Brake, the same capability
validation, the same device allowlists, the same approval requirement and the
same independent verification as one proposed from the operator console.
Measured, on the real rows:

* the proposal stops at `pending_approval`, and nothing else is ever written;
* only the **first** step of a multi-step plan is proposed — a step advances
  only when the previous one verified, so a conversational goal does not get to
  queue a machine's afternoon;
* an engaged Parking Brake means **no model is consulted at all** — the
  Executive reads the brake, in its most restrictive form, before it understands
  anything, and chat's own Governance stage fails closed on `skills` before that;
* a device that is not enrolled is refused, not worked around;
* a request outside the capability vocabulary is refused in the Executive's own
  words, and is never handed to the model to narrate.

## 7. The reply is truthful, and cannot say the work is done

The turn's reply is built from what the Executive reported, and the
conversational model is **not** asked to narrate it — a generated sentence about
an action could contradict the record, and the person would have no way to tell
which was true.

Six states, six renderings: proposed / awaiting approval, clarification
requested, refused, Parking Brake, unavailable, failed. The renderers live in
`goal_intents.py` and **none of them can claim the work is finished**, because at
this point in the Runtime Contract it demonstrably is not: the Executive stops at
`pending_approval` and the envelope refuses dispatch without an approval whatever
the sentence says. Completion is `POST /api/operator/tasks/{id}/advance`'s to
report, after real execution and independent verification.
`TestRenderingIsTruthful::test_no_renderer_can_claim_the_work_is_finished` holds
every renderer against a list of completion claims.

An Executive outcome this surface does not recognise maps to `failed`, never to
success. The chat record and the Reflection both carry `executed: false` and
`verified: false` explicitly, so an audit reader sees the claim being made
rather than inferring it from an absence.

Failure degrades to a question, never to a fabrication. A model that raises, a
model that returns prose instead of JSON, no port at all, an Executive that
raises, a runtime with no executive package: every one of them proposes nothing,
writes no action row, and says so.

## 8. Evidence

### Deterministic and integration

| Suite | Tests | What it proves |
|---|---|---|
| `tests/test_exec02_goal_intents.py` | 78 | the decision seam: conversation stays conversation, goals are recognised, an address without an action verb is not a request, both halves are required, and no renderer can claim completion |
| `tests/test_exec02_conversational_executive.py` | 46 | the vertical slice, on the real chat turn, real dispatch table, real Executive, real catalogue, real validators, real allowlists, real envelope, real brake, real approval requirement; plus tenant resolution and the evidence script's own path |

The only stand-in in the slice is the model, which is a fixed-payload
`DeliberationPort` — the seam EXEC-01 already declared, and the same fixture
shape `tests/test_exec01_vertical_slice.py` uses.

The brief's ten required proofs map onto that file's classes one to one; the
mapping is in its module docstring.

**Regression.** `test_chat_dispatch_table.py`, `test_runtime_contract_chat_seam.py`,
`test_objective_chat_seam.py`, `test_forecast_chat_seam.py`,
`test_api_chat_runtime_contract.py`, the four `test_exec01_*.py` suites and
`test_w03b_no_bypass.py`: 290 tests, green.

### Real-model evidence — what was achieved, and the exact blocker

`scripts/exec02_real_model_evidence.py` drives one benign outcome-level request
through the **shipped composition**: the real `Orchestrator`/`ModelRouter`, the
real `configure_from_environment` activation `app.py` calls on startup, the real
chat turn, the real Executive. It is committed so the proof can be completed on
a machine that has a model, without rebuilding the harness.

**Run in this environment (Linux container, 2026-09-18):**

* the request was recognised as a goal (`referent: "shopping list"`);
* both switches activated: `deliberation_installed: true`,
  `conversational_executive_enabled: true`;
* the real router resolved `default_backend: "local"` from `Identity.yaml`;
* an ordinary chat turn **reached the real Executive** (`reached_the_executive:
  true`), which opened a real task (`exec-…`) and stopped truthfully;
* with a device enrolled so the capability catalogue was non-empty, the chain
  ran all the way to a **real outbound model call** — the real
  `ModelRouterDeliberationPort`, over the real `ModelRouter`, raising:

  ```
  bartholomew.integration.deliberation_adapter.DeliberationUnavailableError:
    the model backend could not deliberate: [ERROR] Could not connect to Ollama
    at http://127.0.0.1:11434 (no connection within 5s, or refused)
  ```

  and the Executive degraded to the literal reading and asked a question, with
  `deliberated: false`, `deliberation_reason: "the deliberation step was
  unavailable"`, and **zero action rows written**.

**The exact blocker.** No model is provisioned in this container: Ollama is not
running on `127.0.0.1:11434`, `ANTHROPIC_API_KEY` is unset, and the optional
`anthropic` SDK is not installed — so `is_configured()` is false and no cloud
backend is built. Nothing in EXEC-02 can be adjusted to change that; a model has
to exist.

**What that evidence does and does not establish.** It establishes that Executive
cognition is genuinely *reachable* from the real user-facing conversational
system — the request crossed every layer from `/api/chat`'s seam to an outbound
HTTP call to a model provider, with no operator-only route and no mock anywhere
in the chain. It establishes that the failure path is truthful end to end. It
establishes **nothing about plan quality**, because no model answered.
`R-EXEC01-3` therefore stands, unchanged, and nothing here may be read as
closing it.

**A correction to this section, made 2026-09-18 after automated review of PR
#115.** The run described above was made by a script that built a `KernelDaemon`
and nothing else. `app.py` also calls `install_seams()` on startup, which
installs Session E's registry as the one device truth; without it the actuation
registry stays at its fail-closed default and **every** device is refused. So
the first run's "no capabilities are available on this device to reason with"
was partly this script's omission and not only a fact about the environment, and
the completion steps first published here would not have worked. The script now
installs the same seams, in the same order, before the chat turn
(`TestTheEvidenceScriptExercisesTheRealPath` pins that it does). The blocker
above is unchanged and independently real — the second run reached a live
outbound call to Ollama and failed on the connection, which is downstream of
every registry question.

**To complete it** (Taylor, on the Windows PC, alongside the deferred Band 0
checkpoint):

1. start Ollama with the Identity-selected model pulled;
2. enrol the device **under the tenant this process resolves** — the run prints
   it as `activation.tenant_id`, and it is `local` on an unbound single-user
   deployment;
3. set the environment variables from §5;
4. run `python scripts/exec02_real_model_evidence.py`.

An `outcome` of `proposed` with a non-empty `proposed_action_ids` and
`stopped_at: "pending_approval (nothing ran)"` is the real-model proof.

## 9. What this does not do

* **It does not make Bartholomew autonomous.** Nothing runs. The single most
  important property of this package is the one that stayed the same.
* **It does not widen the capability domain.** The same nine Windows capability
  kinds. Chat can now reach them; there are no new ones.
* **It does not enable itself.** Default-off, two switches, no default device id.
* **It does not claim general goal competence.** It claims that an outcome-level
  goal inside the supported Windows capability domain, typed into ordinary
  conversation, becomes a bounded, validated, governed *proposal* — and that
  everything else stays conversation.
* **It does not surface `executive_action` on the HTTP response.** `ChatOut`
  carries `reply` alone, as it does for the task, forecast and objective
  recognisers; the truth is in the reply and in the Reflection. Changing that
  contract was not needed and was not done.
* **It does not expose approval from chat.** Approving a proposed action remains
  the operator console's, deliberately: the surface that *understands* a goal is
  not the surface that authorises it.

## 10. Residual risks carried forward

1. **No real-model plan-quality evidence exists** (R-EXEC01-3, unchanged). §8
   says exactly how far the real-path evidence goes and where it stops.
2. **The recogniser's vocabulary is a closed list, and lists drift.** A goal
   phrased outside the request/referent sets is a false negative. This is the
   deliberate direction of error (§2), but it means the sets need reviewing as
   the capability domain grows — the same standing obligation `INFERABLE_
   CAPABILITIES` carries.
3. **Approving from chat is not possible.** A person who states a goal
   conversationally must go to the operator console to authorise the result.
   That is a real seam in the experience, deliberately left: it is a governance
   surface question, not a cognition one.
4. **One device per deployment.** The activation names a single `device_id`.
   Multi-device conversational goals would need the person to say which machine,
   which is a recogniser and consent question this package did not open.
