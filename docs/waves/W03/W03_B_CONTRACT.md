# W03-B — Executive Task Orchestration

> Authoritative builder contract. Read `docs/waves/W03/README.md` and
> `docs/waves/W03/W03_PREP_ASSESSMENT.md` first.

## Identity

| | |
|---|---|
| Session | **W03-B — Executive Task Orchestration** |
| Immutable id | `W03-B` |
| Branch | `wave/w03-b-executive-task-orchestration` |
| PR | `[W03-B] Executive Task Orchestration` |
| Handoff | `BARTHOLOMEW_W03_B_HANDOFF.md` |
| Required CI tier | Integration |
| May start | Immediately after W03-PREP, against the published `action-envelope` and `observation-event` contracts |

## Mission

Turn **user intention + current state + memory/evidence + available capabilities**
into a **governed, executable task**, and drive it through the loop:
Interpret -> Decide -> (propose to Govern) -> observe result -> continue or
recover -> explain.

This is the largest genuinely-new build of the wave. The baseline has **no
executive that can act on the PC**: `Planner.decide()` returns `None`, intent
parsing is regex-based single-shot skill routing (`task_intents`,
`objective_intents`, `forecast_intents`), and — decisively — **nothing in the
kernel imports the actuation seam**; `bartholomew/actuation/seam.py` is called
only by the HTTP route `routes/actions.py`. So today a Windows action can be
created only by an external HTTP POST, never by cognition. W03-B builds the
in-process path from intention to a governed action proposal, and it must be the
**only** new such path.

## Ownership

**Owns (may modify freely):**
- `bartholomew/executive/` — a **new** package: task interpretation, planning /
  step sequencing, capability selection, uncertainty handling, action-envelope
  proposal generation, multi-step progression, failure/recovery decisions, and
  outcome explanation. It defines its own
  `run_executive_task_through_runtime_contract` seam inside this package.

**May consume (do not modify):**
- `action-envelope` (owned by W03-C): W03-B constructs an `ActionRequest` and
  calls `run_action_request_through_runtime_contract` / `grant_action_approval`
  in process. It never bypasses this seam.
- `observation-event` (owned by W03-A): reads perceived state and verification
  read-backs.
- `memory-retrieval-governance` (owned by W03-D): retrieves evidence and lessons,
  treated as evidence not authority.
- `bartholomew/kernel/runtime_contract.py` existing seams (chat, skills, drives,
  awaiting_response), `competency_reasoning`, `objective_store`, the model
  router in `identity_interpreter/orchestrator`. Call them; do not restructure
  `runtime_contract.py` (a shared hotspot — additive new-module code only).

**Must not modify:** actuation/windows_actuation (W03-C); multimodal (W03-A);
memory schema/retrieval internals (W03-D); `app.py` router registration (W03-F).

## Dependencies

- **Required pre-existing interfaces:** the action envelope (W2G package B), the
  awaiting_response obligation state, the model router, the runtime-contract
  seams — all on the baseline head.
- **Other W03 sessions:** consumes W03-A (observation), W03-C (action envelope),
  W03-D (memory retrieval). It can start immediately against the **published
  contract signatures** (they exist on the baseline); it does not need those
  heads frozen to start, only stable signatures.
- **Start:** immediately after W03-PREP.
- **Freeze:** after W03-A/C/D contract surfaces are stable; coordinate with W03-F.

## Explicit non-goals

- **No direct PC control path.** W03-B must never call `windows_actuation`, spawn
  a process, synthesise input, or construct any actuation channel other than the
  action envelope. A test asserts `bartholomew/executive/` imports nothing from
  `bartholomew/windows_actuation` and reaches the OS only via the envelope.
- **No new governance authority.** It proposes; the Parking Brake, Identity
  policy, capability check, approval and arming remain W03-C's seam. An approval
  is requested through the host boundary, never minted by the executive.
- **No autonomous high-risk action.** The executive proposes; execution still
  requires the human authorization the envelope demands. No standing "act"
  permission is added to `Identity.yaml` by this session.
- **No broad proactive autonomy.** Deciding to act unprompted is deferred (see
  `W03_DEFERRALS.md`). W03-B acts on an explicit user task.

## Acceptance criteria (observable, testable)

1. Given a natural-language task and current perceived state, the executive
   produces a plan whose every Windows step is expressed as an **action-envelope
   proposal** (`ActionRequest`) — proven by a test that an executive-generated
   Windows action is represented through the canonical envelope and **cannot
   execute without authorization through the defined host boundary and a clear
   Parking Brake**.
2. There is **no bypass**: an import/AST test proves `bartholomew/executive/`
   reaches actuation only through `bartholomew/actuation/seam.py`.
3. Uncertainty is preserved: when interpretation is materially ambiguous, the
   executive raises an `awaiting_response` clarification rather than guessing —
   proven by a test on an ambiguous instruction ("open it") producing a
   clarification, not an action.
4. Multi-step progression: a task of >1 step advances only after the prior step's
   result is observed and verified (consumes W03-C's Act->Verify result); a failed
   or `unknown` step triggers a defined recovery decision, not a blind retry.
5. Outcome explanation: every task attempt produces a human-readable explanation
   of what was proposed, what was authorized, what executed, and what was
   verified, sourced from `ActionReflection` — no fabricated success.
6. Recalled memory/evidence informs the plan but never authorizes it: a test
   proves a lesson or memory row cannot cause an action to skip approval, widen
   scope, or select a capability the device has not been granted.

## Testing

- **Package-local:** interpretation -> plan; capability selection; clarification
  on ambiguity; recovery on failure/`unknown`; explanation generation.
- **Integration:** end-to-end intention -> envelope proposal -> (refused without
  approval) -> approved -> dispatched to lease boundary -> result observed ->
  next step, against real governance/action stores.
- **Adversarial/governance:** the no-bypass import test; a poisoned memory
  instruction cannot change the proposed capability or skip authorization
  (coordinate with W03-D); replay of the same intention under an engaged brake is
  refused by policy state, not by model wording.
- **Required CI tier:** Integration.

## Escalation boundary

Stop and report rather than expanding scope if:
- delivering a useful plan seems to require an **LLM that can emit tool calls
  reaching the OS outside the envelope**, or any standing action permission in
  `Identity.yaml`;
- multi-step recovery seems to need an autonomous retry loop that acts without a
  fresh human authorization for a `sensitive`/`always`-approval capability;
- the executive needs the action-envelope or observation-event signature changed
  (that is W03-C's / W03-A's contract; request the change through W03-PREP).
