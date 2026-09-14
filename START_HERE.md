# START HERE

> **The project bootstrap.** One document, read first, by any human or AI session
> joining Bartholomew. It answers *what this is, what is true today, what governs
> the work, and what to read next* — and nothing else. It is deliberately short.
>
> **Created:** 2026-09-14 (Project Control & Documentation Reset).
> **Current as of:** `main` = `a64f5af` (the merge commit for PR #108 / EXEC-01).
>
> **What this document is the authority for:** the **source hierarchy** (§3), the
> **current-state snapshot** (§4, §5), and the **bootstrap procedure** (§9). It is
> *not* an authority on architecture, decisions, risks or roadmap — it points at the
> documents that are. Where this document and a canonical document disagree about
> anything other than §3/§4/§5/§9, **the canonical document wins and this one is stale.**
> Fix it rather than working around it.

---

## 1. What Bartholomew is

Bartholomew is an **AI Personal Executive System**: one persistent personal
intelligence that takes responsibility for a person's goals, decides what should
happen, gets it done through governed capabilities, and checks that it actually
worked.

The central architectural model is one loop:

```
Observe → Interpret → Recommend/Plan → Govern → Act → Verify → Continue/Recover
```

Bartholomew is the **Executive intelligence and governance authority** in that loop.
Everything else — devices, tools, models, sensors, actuators, presentation layers —
is a **capability or endpoint**, reached through the **External Capability
Interface** (§2). External systems supply observations, inputs, outputs and
execution. They never decide overall user intent or system behaviour.

**Bartholomew is not the LLM.** A model is replaceable infrastructure the Executive
consults. Identity, memory, governance and authority survive a change of model,
provider, device or database.

### What success means

Bartholomew succeeds when it:

- substantially reduces the user's cognitive and administrative burden;
- makes the user feel **less** overwhelmed;
- increases peace of mind;
- does not feel like a chore;
- **helps the user, rather than requiring the user to continually help Bartholomew
  operate.**

That last line is the standing product test. Real-World Test #1 (2026-08-19/20)
failed it — the tester's own judgement was that the burden sat below break-even —
and closing that gap, not adding features, is what current work is for.
Aspirations in `CONSTITUTION.md` and `MASTER_PLAN.md` are **intent, never claims of
current functionality**; §4 below is the claim of current functionality.

## 2. Architectural invariants

These are non-negotiable. `CONSTITUTION.md` is their canonical authority;
`DECISIONS.md` carries the dated decisions; `COGNITIVE_RUNTIME.md` carries the
runtime semantics. This list is an index, not a substitute.

| Invariant | Meaning |
|---|---|
| **Bartholomew is the sole central Executive authority** | There is one Executive. No second planner, agent framework or integration may sit above it or hold its own executive authority. |
| **External systems are capabilities/endpoints** | Devices, tools, specialist agents and presentation layers (including AIRI) provide sensing, actuation, specialist skill or presence. They do not interpret overall user intent. |
| **External Capability Interface (ECI)** | The single governed boundary between Bartholomew and any external system: endpoint identity, advertised capability, availability, governed request/directive flow, result correlation. New integrations go through it rather than inventing a private path. |
| **Governance above autonomy** | Capability never outranks the rules. Growth in cognition never grants growth in authority. |
| **Parking Brake precedence** | The brake is read *before* an instruction is understood, sits below the presentation layer, and cannot be overridden by cognition, a capability or a UI. An engaged brake means no model is consulted at all. It has **two approved authority tiers** — a Personal/User brake that never halts other users, and a higher-scope Platform/Admin brake a user cannot override — orthogonal to the six subsystem scopes. `COGNITIVE_RUNTIME.md`'s "Authority tiers" is canonical for the semantics. |
| **Cognition may propose, never authorise** | The Executive package never grants its own approval (AST-enforced). A proposal stops at `pending_approval` and waits for a human. |
| **Infer the means, not additional authority** | *(Approved 2026-09-14.)* Bartholomew may infer the ordinary means necessary to achieve an authorised goal, but inference must never silently create additional authority. An inferred step carries less authority than one the person named; capabilities involving materially greater authority, privacy exposure, external commitment or destructive consequence are **deny-by-default for inference** unless explicitly reviewed and approved; and every inferred action still passes capability validation, governance, approval, the Parking Brake, the envelope, verification and recovery. |
| **Evidence is not authority** | Recalled memory and endpoint reports are framed, non-instructional data. They can influence *which valid option* is chosen; they can never make an invalid action valid, widen a bound, or authorise anything. |
| **Prospective reasoning is hypothetical** | Simulated or predicted state must never be presented or recorded as observation. |
| **Verification is independent** | A device's own report of success, with nothing read back, is `unknown`. `unknown` is never rendered as success. |
| **Fail closed** | No irreversible action without an explicit gate; a cognition failure degrades to a question, never to an action. |
| **Reuse proven commodity capability** | Custom-build where Bartholomew differentiates — Executive cognition, governance, identity, memory policy, recovery/undo, cross-capability coordination, learning, coherent experience. Do not rebuild commodity infrastructure to own it. |
| **GitHub is the canonical software repository** | See §3. |

## 3. Source hierarchy — who owns what

**The rule, in one line: GitHub owns durable authority; Airtable owns live operational control
within it; a conversation owns nothing.**

**The boundary that matters (approved 2026-09-14):** Airtable may reorder ordinary operational work
inside approved direction. It must **never silently change durable project direction**. If the live
queue would materially change an approved sequencing decision, architectural prerequisite, gate or
direction, **update the durable repository authority first**, then the queue.

| Question | Authority |
|---|---|
| What is the code? | **GitHub** (`main`), always. |
| What is the architecture, and what is frozen? | **GitHub** — `CONSTITUTION.md`, `COGNITIVE_RUNTIME.md`, `DECISIONS.md`, `INTERFACES.md`. |
| What was decided, when, and why? | **GitHub** — `DECISIONS.md`. |
| What are the risks? | **GitHub** — `RISKS.md` (durable), mirrored as a working queue in Airtable. |
| What is the state of work *right now* — status, priority, next action, evidence pointers? | **Airtable** — `Bartholomew Master Project`. (It has no blockers or dependencies field today; a blocker is carried in the next-action text. Representing dependencies structurally would be a separate controlled schema change.) |
| What was proven, and by what evidence? | **GitHub** — the work-package document and `docs/evidence/`; Airtable holds the pointer and the tier. |
| What should I work on next **right now**? | **Airtable** — it owns live operational control: the queue, current priority, status, active owner/session, blockers and dependencies. It may reorder ordinary operational work. |
| What sequence or direction has been **formally approved**? | **GitHub** — architecture, approved decisions, constraints, contracts, major roadmap commitments, sequencing decisions, milestone order, architectural prerequisites, project gates and durable evidence. |

**These are project-management systems, not parts of Bartholomew.** GitHub and Airtable hold the
*project's* control information — what humans and AI sessions need in order to build the thing.
Bartholomew's own runtime does not read them, does not depend on them, and gains no authority from
them. Bartholomew's internal control architecture — Executive, Governance, the Parking Brake, the
action envelope, verification — is §2 above and is an entirely separate subject. Do not let the word
"control" carry across: a change to this section changes how the project is run, never how
Bartholomew behaves.

### The three binding rules

1. **Airtable points; it does not duplicate.** An Airtable record carries status,
   priority, a short plain-English rationale, and a *reference* to the authoritative
   repo document, PR or commit. It never becomes a second copy of an architecture
   document, and the repository never becomes a status board.
2. **Conflict resolution: for *live status* Airtable wins; for *everything else* the
   repository wins.** Status is what Airtable is for and it is updated more often.
   Architecture, decisions, constraints, contracts and evidence are what the
   repository is for. A disagreement is a defect in one of the two systems — resolve
   it in the owning system, in that pass, rather than leaving both standing.
3. **A material decision or discovery that exists only in a chat does not exist.**
   Promote it to `DECISIONS.md` (durable rule), a work-package document (technical
   finding), or an Airtable record (status/priority) before the session ends.

### Canonical repository documents

**Fifteen.** `MASTER_PLAN.md`'s "Canonical docs" section is the registry and carries
the governance rules for the set.

`START_HERE.md` (this file, the bootstrap and current-state snapshot) ·
`CONSTITUTION.md` (enduring principles) · `MASTER_PLAN.md` (programme plan, backlog,
approval ledger) · `COGNITIVE_RUNTIME.md` (runtime semantics and ownership) ·
`ROADMAP.md` (stages, gates, readiness bands) · `docs/TILT.md` (near-term sequencing
priority) · `DECISIONS.md` · `RISKS.md` · `ASSUMPTIONS.md` · `INTERFACES.md` ·
`CHECKLISTS.md` · `REVIEWS.md` · `CI.md` · `TEST_MATRIX.md` · `PERF_BUDGETS.md`.

Every other `*.md` in the repository is a **reference**, not an authority on status.
`docs/archive/` (superseded, kept for record) and `docs/incubator/` (unapproved
ideas) are permanently non-authoritative by design.

## 4. What is actually true today

`main` = `a64f5af`. **Read the status column literally.** The distinction between
these five states is the most easily lost and most expensive thing in this project.

| State | Means |
|---|---|
| **Operational** | Reachable by the user through the normal production path today. |
| **Integrated** | Wired into the production runtime, reachable through some real path (often operator-only). |
| **Present, not enabled** | The code exists and is tested, but no production wiring calls it. |
| **Proven** | Evidence tier — see §6. Automated tests are *not* proof of live usefulness. |
| **Conceptual** | Decided, designed or discussed. Not built. |

| Capability | State | Notes |
|---|---|---|
| Conversation through `/api/chat`, with memory capture and recall | **Operational** | Usable POC slice 1 (`2d443a9`, 2026-08-14): ordinary conversation produces durable, retrievable memory through the governed write path, and chat retrieval sees it. **Operational is not the same as useful** — Real-World Test #1 exercised exactly this and found the burden below break-even (§1). |
| Conversational-primary UI: ordinary-user vs Workshop separation, obligation legibility, first-use orientation | **Operational** | UX Acceleration Sprint, PR #65, merged 2026-08-26 at Taylor's explicit instruction after independent adversarial review. Also carried the Test #1 UI defect repairs. Safety controls stay in the ordinary view. |
| Memory Agency: list, search, correct, forget, export | **Operational** | PR #65, through the single governed `MemoryStore` authority. Correction is a conditional write, so a stale correction cannot destroy a newer legitimate one, and a user's deletion wins by construction. |
| Conversational task control (an ordinary sentence performs a real `TasksSkill` operation) | **Operational** | Capability Acceleration Sprint, PR #66. Goes through the same governed Runtime Contract chokepoint — **note this when scoping EXEC-02: a sentence already causes a governed action; what it does not do is reach the Executive.** |
| Notification delivery (provider-agnostic outbound webhook) | **Present, enabled only by an operator (default OFF)** | Delivered with slice 1 as the `notify` skill's real outbound channel, but inert until `BARTHOLOMEW_NOTIFY_WEBHOOK_URL` is set (`bartholomew/skills/notify.py`). Unset, a notification is recorded, not delivered. |
| Proactive schedule/birthday reminders | **Present, enabled only by an operator (default OFF)** | Usable POC slice 2, 2026-08-25, `docs/POC_SLICE_2_PROACTIVE_REMINDERS.md`. `schedule_reminders` in `config/kernel.yaml` is the **single** switch — deliberately no environment-variable override, so there is exactly one authority over whether Bartholomew may contact you unprompted. Surfaces one reminder per (fact, due date) plus one governed delivery. **Unattended operation is not authorised by it.** |
| Objective continuity (Golden Path slice 2) | **Present, enabled only by an operator (default OFF)** | `objective_continuity` in `config/kernel.yaml`. Same class of switch as the reminders above. |
| External capability provider — forecast lookup | **Present, enabled only by an operator (default OFF)** | `bartholomew/skills/forecast.py`; unavailable unless `BARTHOLOMEW_FORECAST_API_URL` is configured, and deliberately carries no default URL. The first real use of an outside provider as a capability, through the governed path. |
| Local spoken output | **Present, enabled only by an operator (default OFF)** | `spoken_output` in `config/kernel.yaml`; a prototype. When off, nothing speaks and the speech adapter is never reached. The `voice` Parking Brake scope silences it. A missing engine is reported as "no engine", never as speech that happened. |
| Identity projection into every model path | **Integrated** | FND-01, PR #104. Provider-independent and structurally required. |
| Memory substrate: governed write, redaction, consent gating, retrieval | **Integrated** | Beneath the rows above. FND-02 (#105) and FND-03 (#106) repaired redaction separation and consent-inbox privacy/lifecycle. |
| Governance: action envelope, approval, Parking Brake, audit | **Integrated** | The most mature part of the system. Heavily tested. |
| Recovery / undo, independent verification, `FAILED`/`UNKNOWN` distinction | **Integrated** | `bartholomew/executive/recovery.py` and `bartholomew/executive/verification.py`; semantics in `COGNITIVE_RUNTIME.md` and `docs/EXEC_01_GOAL_TO_PLAN_DELIBERATION.md` §6. *(Airtable carries a frozen decision naming a "Recovery & Undo Contract v1.0"; no document of that name exists in this repository — the code and the runtime document above are the traceable authority.)* |
| Windows observe → reason → act → verify golden path | **Integrated (operator-only)** | Wave 3, PR #101. Reached through `POST /api/operator/tasks`, not through ordinary conversation. Real-world acceptance still outstanding. |
| Local-model generation and truthful readiness | **Integrated** | BGPR-01, PR #102. Blocking generation moved off the event loop; readiness reports model reachability distinctly from model selection. |
| External Capability Interface core boundary | **Integrated** | FND-04, PR #107. Endpoint identity, capability advertisement, availability, governed flow, result correlation — proven by a reference vertical slice. **No real external product is attached.** |
| **Executive goal-to-plan deliberation** | **Present, not enabled** | EXEC-01, PR #108. No production caller. See §5. |
| Conversational chat reaching **goal-to-plan deliberation** | **Not built** | Read this precisely. Chat **does** traverse the Runtime Contract's **Executive stage** — it builds a `CandidateAction` that Governance genuinely consumes (`bartholomew/kernel/runtime_contract.py`, Stage 3; `COGNITIVE_RUNTIME.md`'s Executive row). What that stage does **not** reach is `bartholomew/executive/`'s goal-to-plan deliberation: the package holds no import from it, so an outcome-level goal ("start a shopping list") produces a conversational reply rather than a deliberated plan. EXEC-02 deepens an Executive stage that already exists; it does not attach a new brain. |
| Unattended / ambient operation | **Not authorised** | Not a build gap but a governance one: it sits inside Band A's restricted envelope and needs its own recorded decision. Slice 2 supports the Band 0 **attended** checkpoint only. |
| AIRI as a presence endpoint | **Conceptual** | Architectural role agreed; no production integration. |
| Household robotics / embodied endpoints | **Conceptual** | Tracked as a strategic backlog item in Airtable `Work Packages` (status: not started); no repository record carries an approval, so none is claimed here. Robots would be capability endpoints through the ECI, never a second executive. Nothing built. |
| HTTP authentication and the exposure boundary | **Present, deliberately disabled for this deployment** | Not conceptual: `bartholomew/platform/exposure.py` resolves an auth mode, and the rule is fail-closed — **a non-loopback bind forces authentication and TLS on, and neither can then be turned off**; the process refuses to start if `BARTH_AUTH_MODE=disabled` is combined with a non-loopback bind. `disabled` is the single-user localhost development mode this repository runs in. Loopback-only is the default, with a deliberate override path. |
| Multi-user, tenancy, cloud infrastructure, device agents | **Conceptual** | Target architecture recorded in `DECISIONS.md`. None of it exists; this deployment serves exactly one person. |

**A sixth state, used above:** *present, enabled only by an operator (default OFF)*. It is distinct
from "present, not enabled" — these have a real switch a person is meant to throw, and they work
when thrown. Treating a default-OFF capability as absent understates the system; treating it as
operational overstates it.

## 5. EXEC-01 — the most recent package, stated precisely

**Merged.** PR #108, reviewed head `2813c7b`, merge commit `a64f5af`.
Full record: `docs/EXEC_01_GOAL_TO_PLAN_DELIBERATION.md`. Decision:
`DECISIONS.md`, "The Executive deliberates a course of action from an outcome".

**What it accomplished.** The Executive can now take an outcome-level goal ("start a
shopping list for me"), infer the intermediate steps the person did not state, reason
over what the device can actually do, and produce a bounded, validated proposal — as
the **existing** `TaskIntent` contract, through the **existing** seam, with no second
planner and no governance bypass. Before it, the Executive could follow a plan but
made the person write it. A model may supply the semantic reasoning through a
one-method port over the existing `ModelRouter`; inference is held to a stricter
standard than instruction (`clipboard_read` and `accessibility_action` are never
inferred), and every proposed parameter goes through the real device allowlists.

**What it did not accomplish — all four verified in code at `a64f5af`:**

- **Its deliberation is not reachable from chat.** Chat already traverses the Runtime Contract's Executive *stage*; what it does not reach is `bartholomew/executive/`'s goal-to-plan deliberation, because `bartholomew/kernel/runtime_contract.py` holds no import from that package. The gap is depth in an existing stage, not a missing brain.
- **It is not enabled anywhere.** `install_deliberation_port` has **no production
  caller**; a deployment that does not call it keeps exactly the pre-EXEC-01
  Executive. This is deliberate — enabling model-led reasoning on someone's computer
  is an operator's decision, not a side effect of a provider being reachable.
- **No live-model plan quality is proven.** All evidence is against deterministic
  fake ports (§6). Nothing yet says a real model produces *good* plans.
- **The capability domain is unchanged.** Same nine capability kinds. Cognition got
  better at using them; there are no new ones. Deliberation provenance is not
  persisted on the task row (it reaches the `ActionReflection` audit trail).

### What comes next — the approved sequence

**Taylor approved this sequence at the User Approval Gate on 2026-09-14.** It is the durable
sequencing decision; `DECISIONS.md` carries it in full.

1. **Merge PR #109** (this reset) once its required CI is green on the final head.
2. **A narrow Windows writer-lock / WAL reliability repair**, and classify the FND-04 vertical-slice
   failure while doing it. **Scope is deliberately narrow** — this reliability class only, not
   general database cleanup, unrelated refactoring, broad tech-debt reduction or EXEC-02 work.
3. **Restore a trustworthy Windows baseline**, so that Band 0 evidence is not contaminated by a
   known reliability defect. That is the whole purpose of step 2.
4. **One attended Band 0 real-world checkpoint** on the resulting system. This is an
   **evidence/validation step, not a new implementation architecture.**
5. **EXEC-02**, unless the Band 0 checkpoint reveals a material blocker serious enough to justify
   changing course.

`CI.md` and `TEST_MATRIX.md` maintenance is approved as **separate, non-blocking** work. It is not a
prerequisite for Band 0 or for EXEC-02, and becomes blocking only if a concrete documentation
inconsistency is shown to compromise test interpretation or project control.

**EXEC-02 remains the next major Executive implementation package after the checkpoint**, and it is
still **not started**. Read what it is precisely: Bartholomew **already has an Executive stage** in
the runtime, and EXEC-02 **deepens and connects the existing Executive architecture** so that
outcome-level goals reach the deliberation that already exists inside it. It does not attach, bolt
on or wire in a separate Executive brain, and it must develop outward from the existing runtime,
identity, memory, governance, capability and execution systems rather than introduce a parallel
planner or agent architecture.

## 6. Evidence tiers

Never collapse these. A claim must name its tier.

| Tier | What it proves | EXEC-01 |
|---|---|---|
| **Unit / integration tests** | The code does what the author intended, in isolation. | Yes — 4 modules, 117 test functions (verified), parametrised further. The "218 EXEC-01 / 493 targeted" figures are the builder session's own, reported not re-verified. |
| **CI** | It holds on clean infrastructure, not just one laptop. | Yes, on the two tiers a pull request runs — see §7. |
| **Adversarial review** | Someone competent tried to break it. | Yes — 23 findings raised, 18 substantive fixed, zero live defects at the reviewed head. |
| **Mocked-model evidence** | The *plumbing* around a model is correct. | Yes — and this is the ceiling of EXEC-01's evidence. |
| **Real-model evidence** | A live model produces useful, safe output. | **No.** |
| **Real Windows / device evidence** | It works on actual hardware. | **No** for the deliberated path. |
| **Manual acceptance evidence** | A person found it genuinely useful and not a chore. | **No.** Test #1 (pre-EXEC-01) found the opposite. |

## 7. CI, and the state of `main`

**Four workflows, different triggers. They are not interchangeable** — treating them as one is how
the EXEC-01 record ended up contradicting itself, and it is worth knowing before you read any CI
claim in this repository:

| Tier | Workflow | Runs when |
|---|---|---|
| **PR Fast** | `ci.yml` | every push to a pull request — what a draft gets; also manual dispatch |
| **Integration** | `integration.yml` | a pull request is **ready for review (not a draft)**, the `ci:integration` label, a merge queue, or by hand |
| **Merge Candidate** | `merge-candidate.yml` | a push to `main`, a merge queue, the `ci:merge-candidate` label, or a wave integration branch — **not** an ordinary ready-for-review pull request |
| **Nightly** | `nightly.yml` | on schedule; also manual dispatch |

**EXEC-01 (PR #108) at reviewed head `2813c7b`:** PR Fast **passed**; Integration **ran, because the
pull request was marked ready for review, and passed**; Merge Candidate **did not run** (it does not
run on pull requests without the label). Earlier wording that the Integration jobs were "skipped
under normal PR behaviour" described the draft phase only and is superseded —
`docs/EXEC_01_GOAL_TO_PLAN_DELIBERATION.md` §9 holds the full record with the check-run evidence.

**`main` is currently red on the Merge Candidate tier**, and you should know this before you read a
green-looking status anywhere else. At `a64f5af` that tier failed one job of seven — *Windows full
default suite* — at **3 failed, 4,987 passed, 79 skipped**. Two failures
(`tests/test_event_backbone_drive.py` ×2) are named members of the writer-lock / WAL-contention class
recorded in `RISKS.md` and analysed in `docs/waves/W03/W03_MERGE_CANDIDATE_READINESS.md` §7:
pre-existing, rotating between runs, passing in isolation, seen on `main` since 2026-08-15, and
**deferred out of Wave 3 as a separate reliability task with Taylor's explicit approval**. The third
(`tests/test_fnd04_eci_vertical_slice.py`) is in FND-04 code and is not yet listed among that class.
**None is an EXEC-01 test.** This is the state of `main`, not a defect introduced by the last
package, and repairing it is separate, unauthorised work.

**The repair exists and is awaiting Taylor's gate (2026-09-14, PR #110, not merged).** Its record
is `docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md`; the rule it establishes is in `DECISIONS.md` ("A SQLite
statement that can wait for a lock never runs on the event-loop thread"). Read the reclassification
before reading any older statement about these tests: the FND-04 slice failure is **the same root
cause** (proved from the `a64f5af` log, not assumed), the two `test_event_backbone_drive.py`
failures were **never** lock failures (a test-side ordering race, corrected), and
`test_sqlite_wal_concurrent_processes.py` was a **second, independent** SQLite defect (a fresh-file
WAL conversion race), also repaired. The push of `57f86f8` (PR #109, unmodified `main`) was
**cancelled at the 40-minute cap** with the Windows suite stalled after 99 % — the "stalled tail"
symptom, which is a hang rather than a lock error, **recurred on the repaired branch** (run
34857413080, same shape), and so is independent of this repair and remains **separately tracked**
(record §8). Consequence: the Windows baseline is **not yet trustworthy** and Band 0 is **NOT
READY** on today's evidence (record §9); the Windows CI jobs now run `-vv` so the next stall names
its tests.

## 8. Major open risks

`RISKS.md` is the durable authority; this is the short list a new session needs.

1. **Executive cognition is unreachable from normal use.** The headline risk. The
   most valuable cognition in the system is invisible to the user.
2. **Deliberation is not enabled in shipped wiring.** No production caller.
3. **No real-model plan-quality evidence.** The one thing tests cannot substitute for.
4. **Real-world usefulness is unproven and previously failed.** Automated evidence
   materially exceeds live-usefulness evidence.
5. **Bounded capability domain.** Nine Windows capability kinds; no real external
   product is attached through the ECI.
6. **Capability inference — policy settled, review obligation standing.** Taylor approved
   **"infer the means, not additional authority"** on 2026-09-14 (`DECISIONS.md`, and §2 above), so
   this is no longer an open question. What remains is an obligation, not a risk of drift: every
   capability added or removed needs a deliberate review of authority, privacy and consequence,
   because a future capability does **not** become inferable merely by being implemented.
7. **Parking Brake read/write authority split** remains open (constraint C6,
   gated at Band B / safety gate S5). Not closed by Test #1.
8. **`main`'s Merge Candidate tier is red** — the Windows writer-lock / WAL-contention class
   (§7). **No longer deferred: as of 2026-09-14 this is a pre-Band-0 repair requirement**, because
   Band 0 evidence must not be contaminated by a known reliability defect. **The repair is built,
   root-caused and regression-tested in PR #110 (not merged; User Approval Gate)** —
   `docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md`. `tests/test_fnd04_eci_vertical_slice.py` is now
   classified **same root cause, by log evidence**. What that PR does **not** close: the Windows
   "stalled tail" hang that cancelled the `57f86f8` run at the job cap, which is a different
   symptom and is tracked separately in `RISKS.md`.
9. **Documentation currency is itself a risk.** This reset repaired a control plane
   that had drifted roughly a month behind `main`. See §10.

## 9. How to bootstrap a new session

1. **Read this file.** It is the whole orientation.
2. **Read the live queue in Airtable** (`Bartholomew Master Project` →
   `Work Packages`, then `Project Areas`) for current status, priority and next
   action. That is where "what now" lives, not here.
3. **Read the two or three documents this file points you at** for the area you are
   touching — the work-package document for the last package in that area, plus
   `DECISIONS.md` and `COGNITIVE_RUNTIME.md` for anything architectural.
4. **Do not read the historical corpus.** Wave documents, phase documents,
   implementation notes, session handoffs and `docs/archive/` are *record*, not
   briefing. Reach for one only when you need the detail of that specific package.
5. **Before you finish**, update Airtable status and promote anything durable you
   learned into the repository (§3, rule 3).

## 10. Where deeper documentation lives

| You need | Go to |
|---|---|
| Enduring principles, invariants, what may never be traded away | `CONSTITUTION.md` |
| How the runtime actually fits together; component ownership | `COGNITIVE_RUNTIME.md` |
| A dated decision, its alternatives and consequences | `DECISIONS.md` |
| Stage gates, readiness bands, exit criteria | `ROADMAP.md` |
| Why *this* is the next thing rather than that | `docs/TILT.md` |
| Programme plan, backlog, approval ledger | `MASTER_PLAN.md` |
| Risks, tech debt, open constraints | `RISKS.md` |
| API and contract surfaces | `INTERFACES.md` |
| Test strategy / CI tiers | `CI.md` (its header tier table is current; its body predates the four-tier structure) and `TEST_MATRIX.md` (**counts are of 2026-07-27 — it states a 915-test suite; the default suite is now roughly 4,987 tests**) |
| The last Executive package in full | `docs/EXEC_01_GOAL_TO_PLAN_DELIBERATION.md` |
| The External Capability Interface in full | `docs/FND_04_EXTERNAL_CAPABILITY_INTERFACE.md` |
| The Windows writer-lock / WAL repair: root cause, evidence, reclassifications | `docs/WINDOWS_WAL_WRITER_LOCK_REPAIR.md` |
| Real-World Test #1 evidence and the approved register | `docs/evidence/test-1/` |
| Wave 3 contracts and handoffs | `docs/waves/W03/` |
| Superseded history | `docs/archive/` |
