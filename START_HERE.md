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
| **Parking Brake precedence** | The brake is read *before* an instruction is understood, sits below the presentation layer, and cannot be overridden by cognition, a capability or a UI. An engaged brake means no model is consulted at all. |
| **Cognition may propose, never authorise** | The Executive package never grants its own approval (AST-enforced). A proposal stops at `pending_approval` and waits for a human. |
| **Evidence is not authority** | Recalled memory and endpoint reports are framed, non-instructional data. They can influence *which valid option* is chosen; they can never make an invalid action valid, widen a bound, or authorise anything. |
| **Prospective reasoning is hypothetical** | Simulated or predicted state must never be presented or recorded as observation. |
| **Verification is independent** | A device's own report of success, with nothing read back, is `unknown`. `unknown` is never rendered as success. |
| **Fail closed** | No irreversible action without an explicit gate; a cognition failure degrades to a question, never to an action. |
| **Reuse proven commodity capability** | Custom-build where Bartholomew differentiates — Executive cognition, governance, identity, memory policy, recovery/undo, cross-capability coordination, learning, coherent experience. Do not rebuild commodity infrastructure to own it. |
| **GitHub is the canonical software repository** | See §3. |

## 3. Source hierarchy — who owns what

**The rule, in one line: GitHub owns durable truth; Airtable owns live status; a
conversation owns nothing.**

| Question | Authority |
|---|---|
| What is the code? | **GitHub** (`main`), always. |
| What is the architecture, and what is frozen? | **GitHub** — `CONSTITUTION.md`, `COGNITIVE_RUNTIME.md`, `DECISIONS.md`, `INTERFACES.md`. |
| What was decided, when, and why? | **GitHub** — `DECISIONS.md`. |
| What are the risks? | **GitHub** — `RISKS.md` (durable), mirrored as a working queue in Airtable. |
| What is the state of work *right now* — status, priority, blockers, next action? | **Airtable** — `Bartholomew Master Project`. |
| What was proven, and by what evidence? | **GitHub** — the work-package document and `docs/evidence/`; Airtable holds the pointer and the tier. |
| What should I work on next? | **Airtable** `Work Packages`, reconciled against `docs/TILT.md` sequencing. |

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
| Identity projection into every model path | **Integrated** | FND-01, PR #104. Provider-independent and structurally required. |
| Memory: governed write, redaction, consent gating, retrieval | **Integrated** | FND-02 (#105) and FND-03 (#106) repaired redaction separation and consent-inbox privacy/lifecycle. |
| Governance: action envelope, approval, Parking Brake, audit | **Integrated** | The most mature part of the system. Heavily tested. |
| Recovery / undo, independent verification, `FAILED`/`UNKNOWN` distinction | **Integrated** | Contract v1.0 authoritative. |
| Windows observe → reason → act → verify golden path | **Integrated** | Wave 3, PR #101. Operator routes (`POST /api/operator/tasks`). Real-world acceptance still outstanding. |
| Local-model generation and truthful readiness | **Integrated** | BGPR-01, PR #102. Blocking generation moved off the event loop. |
| External Capability Interface core boundary | **Integrated** | FND-04, PR #107. Endpoint identity, capability advertisement, availability, governed flow, result correlation — proven by a reference vertical slice. **No real external product is attached.** |
| **Executive goal-to-plan deliberation** | **Present, not enabled** | EXEC-01, PR #108. See §5. |
| Conversational chat reaching the Executive | **Not built** | `kernel/runtime_contract.py` holds no reference to the executive package. A goal typed into `/api/chat` falls through to a conversational reply. |
| AIRI as a presence endpoint | **Conceptual** | Architectural role agreed; no production integration. |
| Household robotics / embodied endpoints | **Conceptual** | Approved strategic backlog item, 2026-09-14. Nothing built. |
| Multi-user, tenancy, cloud infrastructure, device agents, authentication | **Conceptual** | Target architecture recorded in `DECISIONS.md`. None of it exists. This deployment is a single-user PoC. |

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

- **It is not reachable from chat.** `/api/chat` has no path to the Executive.
- **It is not enabled anywhere.** `install_deliberation_port` has **no production
  caller**; a deployment that does not call it keeps exactly the pre-EXEC-01
  Executive. This is deliberate — enabling model-led reasoning on someone's computer
  is an operator's decision, not a side effect of a provider being reachable.
- **No live-model plan quality is proven.** All evidence is against deterministic
  fake ports (§6). Nothing yet says a real model produces *good* plans.
- **The capability domain is unchanged.** Same nine capability kinds. Cognition got
  better at using them; there are no new ones. Deliberation provenance is not
  persisted on the task row (it reaches the `ActionReflection` audit trail).

## 6. Evidence tiers

Never collapse these. A claim must name its tier.

| Tier | What it proves | EXEC-01 |
|---|---|---|
| **Unit / integration tests** | The code does what the author intended, in isolation. | Yes — 4 test modules, 218 EXEC-01 tests; 493 targeted/default tests green at `2813c7b`. |
| **CI** | It holds on clean infrastructure, not just one laptop. | Yes — see §7. |
| **Adversarial review** | Someone competent tried to break it. | Yes — 23 findings raised, 18 substantive fixed, zero live defects at the reviewed head. |
| **Mocked-model evidence** | The *plumbing* around a model is correct. | Yes — and this is the ceiling of EXEC-01's evidence. |
| **Real-model evidence** | A live model produces useful, safe output. | **No.** |
| **Real Windows / device evidence** | It works on actual hardware. | **No** for the deliberated path. |
| **Manual acceptance evidence** | A person found it genuinely useful and not a chore. | **No.** Test #1 (pre-EXEC-01) found the opposite. |

## 7. CI status of EXEC-01 — the single final truth

This was recorded twice, inconsistently, and is settled here:

PR #108 was **marked ready for review before merge, which caused the Integration /
merge-candidate tier to run.** Those checks **completed successfully** at reviewed
head `2813c7b`, alongside the PR Fast/CI tier. Any earlier statement that the
Integration or merge-candidate jobs were *skipped under normal PR behaviour* was
written while the PR was still a draft, describes a state that no longer held at
merge, and is **superseded by this section**. The merge-time truth is: both tiers
ran and both passed.

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
6. **Residual policy judgement on inferred capabilities.** `INFERABLE_CAPABILITIES`
   is a judgement call, not a derived fact, and will need revisiting as the
   capability vocabulary grows.
7. **Parking Brake read/write authority split** remains open (constraint C6,
   gated at Band B / safety gate S5). Not closed by Test #1.
8. **Documentation currency is itself a risk.** This reset repaired a control plane
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
| Test strategy / CI tiers | `TEST_MATRIX.md`, `CI.md` |
| The last Executive package in full | `docs/EXEC_01_GOAL_TO_PLAN_DELIBERATION.md` |
| The External Capability Interface in full | `docs/FND_04_EXTERNAL_CAPABILITY_INTERFACE.md` |
| Real-World Test #1 evidence and the approved register | `docs/evidence/test-1/` |
| Wave 3 contracts and handoffs | `docs/waves/W03/` |
| Superseded history | `docs/archive/` |
