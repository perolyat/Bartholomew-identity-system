# Bartholomew — Vision and Personal-First Deployment

> **Status:** PROPOSED (2026-08-15). **Non-canonical** — this is a reference/proposal document
> under `docs/`, not one of the 14 canonical SSOT docs. It records product intent, the personal-first
> deployment target, and the sequence to reach it. Where it would change canonical content, §10
> names exactly what and leaves those documents untouched pending approval.
>
> **Why it exists:** the project has detailed architecture, governance and roadmap documentation,
> and no document stating *what Bartholomew is for or who it is for*. That gap made several
> decisions look like over-engineering when read from the code alone, and left every sequencing
> argument without a target to be checked against. This document is the missing target.

---

## 1. What Bartholomew is for

**A personal assistant and estate manager for someone who could not otherwise employ one.**

The capability of having someone who knows your life, holds its details, notices what needs doing,
and handles the running of it has historically been available only to people wealthy enough to hire
a person. The goal is to make that available to an ordinary individual.

This is the thesis the architecture has been serving implicitly since the beginning, and it explains
choices that are otherwise hard to justify:

- **Why memory, provenance and continuity came before features.** An assistant that forgets is not
  an assistant. Continuity is the product, not a feature of it.
- **Why competencies rather than hardcoded functions.** A human assistant is not purpose-built; they
  learn your domains. `ROADMAP.md` already names Residential Estate Management as the *architecture
  acceptance test* — a competency Bartholomew is trained in, not a feature that was coded.
- **Why governance and consent are first-class.** Something with access to your life, devices and
  microphones must be stoppable and must ask before acting. This is the precondition for trust, not
  overhead on top of it.
- **Why `CONSTITUTION.md` insists Bartholomew is not the LLM.** The assistant is the persistent
  thing that knows you. The model is a supplier that can be replaced without loss.

**Bartholomew has no single purpose.** It exists to be present and useful across the whole of a
life, not to perform one task well.

## 2. Interaction model

| Surface | Role | Status |
|---|---|---|
| **Voice on phone** | The primary interface. How Bartholomew is normally used. | Governed seam exists (`run_voice_through_runtime_contract()`, `voice` parking-brake scope); capture, transcription, streaming and persona-bearing output are Stage 6 and not built. |
| **Chat** | Secondary. Longer or more precise exchanges. | Working today, web only. |
| **Settings / inspection** | Reviewing what was remembered, approving consent requests, engaging the Parking Brake, seeing what Bartholomew has been doing. | Working today (minimal UI). |

Two consequences follow, and both save effort:

1. **The web UI is not the product.** It is the settings, inspection and governance surface. It
   should be clear, honest and complete — not polished beyond that. Effort belongs in the voice path.
2. **Cognitive load is a design constraint, not an aspiration.** The measure of success is that
   using Bartholomew costs the user less attention than doing the thing themselves.

### 2.1 The consent-versus-effortlessness tension

Fictional assistants feel effortless partly because they never ask permission and are never wrong.
Bartholomew deliberately does ask, and can be stopped. These goals genuinely pull against each other.

**The resolution is not fewer gates. It is that gates should be rare, well-targeted, and feel
earned.** Consent should attach to categories rather than instances; Bartholomew should learn what
has already been blessed; the interruption budget should be small and spent well.

The current behaviour is the crude version — `"my partner's name is Jo"` is gated because the
extractor matched the word *name*. The mature version knows what this particular user actually
cares about protecting. That is what S5.4's learning loop is for.

**The interruption budget can only be calibrated from real use.** No amount of design determines how
often Bartholomew should interrupt someone. Only being interrupted does.

## 3. Deployment target: personal-first

**Build one working Bartholomew for one person — its author — running on their own PC and reachable
from their own phone.** No multi-user infrastructure, no hosted service, no tenancy.

This is not a change of direction. `CONSTITUTION.md` already states that the current single-user
deployment — SQLite, local execution, no authentication — is *correct for this stage*, and
`MASTER_PLAN.md` already places platform infrastructure outside current scope.

### 3.1 This is not a throwaway prototype

A personal build is explicitly **not** a disposable prototype to be replaced later by a separate
commercial project. `CONSTITUTION.md` is direct about this: the current single-user Bartholomew is
"the first personal Bartholomew identity running on an early deployment of the future platform —
**not a different, throwaway thing**."

The reasoning is practical rather than sentimental. Governance, consent, memory, the competency
architecture and the relevance gate are all indifferent to how many users exist. None of it would be
rebuilt, so a fresh project would mostly copy this one while losing its history and its decision
record.

**The discipline that keeps both futures open** is the one already recorded: deployment choices must
never become inseparable from Bartholomew's architecture. Concretely — no user identity threaded
through domain code, no hardcoded absolute paths, configuration stays configuration. That costs
approximately nothing now and is the whole difference between *adding* multi-tenancy later and
*rewriting* later.

## 4. Access and security

**Threat model, stated plainly.** Two different threats, needing two different answers:

| Threat | Defence |
|---|---|
| Someone who is not on the user's private network reaching Bartholomew at all | **Network boundary** |
| Someone with physical access to an unlocked device the user owns | **Login** |

A network boundary alone does not help when the attacker is holding the user's phone. A login alone
is a weak boundary if the service is reachable from the open internet — which is the assumption
`ASSUMPTIONS.md` explicitly rejects when it refuses "simple token auth is sufficient."

**Decision:**

1. **Bind to the local network**, with a private VPN mesh (WireGuard/Tailscale-style) for access away
   from home. Nothing internet-facing. From Bartholomew's perspective the phone is local.
2. **Email + password login** on the web UI and phone client. Email address is used *as the
   username* — a familiar string, and forward-compatible with a commercial version.
3. **No email delivery.** No verification messages, no reset links, no SMTP service. Password
   recovery is physical access to the machine, which is the correct model for hardware the user owns.
4. Password hashed with a modern algorithm; a session that persists so the password is not a
   constant tax.

Explicitly **not** built: account management, multi-tenancy, roles, invitations, or federated
identity. Those belong to a commercial deployment and would be built against requirements that do
not yet exist.

## 5. Local governance authority under a cloud brain

The long-term architecture in `CONSTITUTION.md` is lightweight clients over shared backend services
— a "brain in the cloud, thin client in the hand" shape. That matches the intended interaction model.

**One binding property constrains it:** local Governance authority — *the parking brake above all* —
must never become cloud-dependent.

This creates a real design problem that does not yet have an answer:

> If the brain is remote and the client is a phone, **where does the Parking Brake live?**

It cannot be a call to a cloud API, because losing connectivity would mean losing the off-switch —
and an always-present assistant with microphone and camera access is precisely the case where that
is unacceptable.

**Direction (not yet a design):** governance state is authoritative *on the device*; the brain
refuses to act without it; the device can hard-stop everything with no network at all. This needs
designing before the distributed topology is built, not discovered during it.

## 6. Model strategy and economics

**Unit economics are part of the product, not an implementation detail.** "A personal assistant for
someone who is not wealthy" means the monthly running cost per user is a product constraint. An
always-present assistant driven entirely by a frontier model is the thing that already exists for
people who can afford staff.

`Identity.yaml` already encodes the answer:

```yaml
budgets:
  monthly_cloud_spend_usd: 25
  daily_token_cap: 1000000
  low_balance_behavior: "force-local"
```

A million tokens a day is roughly thirty million a month. At any frontier-model rate that costs far
more than $25. The two figures reconcile only one way: **the large majority of traffic must be
local, with cloud reserved for a small, high-value slice.**

This also corrects a conclusion reached earlier in the same session that produced this document.
Local-first is not a limitation to escape on the way to a cloud model — **it is the mechanism that
makes the thesis affordable.**

**Design:** hybrid routing driven by `Identity.yaml`'s existing `by_task_type` selection policy and
budget caps. Ambient, routine, and anything touching stored personal facts stays local. Genuinely
hard reasoning may go to cloud, within budget, falling back to local when spent. Cost per turn grows
with context, not just with input length — the prompt already carries persona, recent conversation,
recalled facts and competencies on every turn, and ambient context will enlarge it further.

**Privacy boundary.** Sending context to a cloud provider means personal facts leave the device for
the first time. This is a genuine boundary crossing, is anticipated by `Identity.yaml`'s
`cloud_optional`, and requires its own recorded decision before implementation.

## 7. Sequence

Each step requires its own separate approval. This records order, not authorisation.

| # | Work | Why here |
|---|---|---|
| 1 | **Hybrid model routing** — Identity-policy-driven local/cloud selection, budget-aware, honest failure | Nothing downstream can be judged through a model too weak to converse. Closes MASTER_PLAN item 11.15 properly. |
| 2 | **S5.4 — experience → learning/consolidation loop**, closing the reflection-ownership gap | Where Bartholomew starts developing rather than only remembering. The prerequisite for §2.1's interruption budget. |
| 3 | **Personal deployment** — network boundary, login, phone access | Makes the primary surface reachable. Far smaller than the multi-user auth problem it replaces. |
| 4 | **Voice** — capture, transcription, streaming, persona-bearing output | The primary interaction model. Seam already exists and is already governed. |
| 5 | **S5.5–S5.7 — initiative scaffolding, dry-run, controlled live initiative** | Proactive presence. Dry-run first: logs what it *would* have done without doing it. |
| 6 | **Reach** — devices, automations, acting on the world | The most consequential surface, deliberately last, behind everything above. |

**Checkpoint discipline.** The author reviews a working system at each step, not once at the end.
Not a launch and not a demo — enough use to judge whether the previous step was right. Four
checkpoints instead of one long bet; a wrong turn costs one step rather than four.

## 8. What this defers, and on what condition

This document defers the real-world use of Usable POC slice 1 that `MASTER_PLAN.md`'s "Next 3 Moves"
item 4 currently names as the next move.

**This is not an override of `docs/TILT.md`. It is an argument about TILT's own precondition.**

TILT's binding principle applies "once a vertical slice is sufficiently functional to generate
meaningful real-user feedback." The claim here is that slice 1 has **not** met that precondition —
not because it is unpolished, but because conversational quality through a 4-bit quantised 7B local
model is poor enough that natural, sustained use will not occur, and feedback gathered from forced
use would describe the model rather than the architecture. That is an experiment-validity argument,
which is one of TILT's own six stated exceptions.

The distinction matters, because "not sufficiently functional yet" is a claim that can be discharged,
whereas "not mature enough" cannot.

**Exit condition — real-world use resumes when both hold:**

1. Step 1 (hybrid model routing) has landed, and ordinary conversation is fluent enough to use
   without effort; and
2. Either step 3 (phone access) or fluent web chat is available, so use can fit into an ordinary day
   rather than requiring a session at a desk.

**This condition is deliberately concrete.** An open-ended deferral would reproduce exactly the
pattern the 2026-08-12 assessment identified and TILT was written to correct. If the exit condition
is met and testing is deferred again, that is a new decision requiring its own record.

## 9. Open questions

1. **Where does the Parking Brake live** in a phone-plus-cloud-brain topology? (§5 — direction only.)
2. **What may cross the local/cloud boundary?** Proposed default: anything ambient, routine, or
   touching stored personal facts stays local. Needs a decision entry before step 1.
3. **PWA or native phone app?** PWA is cheap and the existing UI already reflows to phone width;
   always-on background voice is where mobile browsers are weakest, and platform behaviour needs
   verifying rather than assuming. PWA first is the cheapest way to learn what the voice interaction
   should be before paying for a native build.
4. **Third-party consent.** Ambient microphones and cameras capture family, guests and neighbours who
   consented to nothing. `ROADMAP.md` already flags jurisdiction-aware capture compliance for Stage 6;
   Australian recording law is strict and varies by state. The consent architecture currently protects
   *the user's* data from *their* Bartholomew; the always-on version must also point outward. Not a
   blocker today — but it must shape the sight/voice design rather than be discovered after it.

## 10. Canonical reconciliation required if approved

No canonical document has been modified. If this is approved:

- **`MASTER_PLAN.md`** — "Next 3 Moves" rewritten around §7; P0 item 1's stale
  `fastapi>=0.104,<0.121` ceiling corrected (`requirements.txt` is now `>=0.134,<0.141`, raised for
  CVE-2026-54283).
- **`docs/TILT.md`** — record §8's precondition argument and its exit condition. TILT's principle is
  unchanged; what changes is the assessment of whether slice 1 currently meets its precondition.
- **`DECISIONS.md`** — entries for: personal-first deployment; network boundary plus local login
  without email delivery; the local/cloud data boundary; deferral of slice-1 real-world use with its
  exit condition.
- **`ROADMAP.md`** — Stage 6 scope narrowed for a single-user private-network deployment; sequence
  aligned to §7.
- **`RISKS.md`** — the cross-device auth threat-model risk re-scoped for the personal deployment;
  **and a correction**: the hydration/water-logging entry describes `/api/water/log` and
  `/api/water/today` as "live, working, legacy code". Neither endpoint exists in the API bridge and
  both 404 on every load. Only the UI panel is real (now labelled accordingly).
- **`ASSUMPTIONS.md`** — personal context leaving the device for a cloud model is a new assumption
  and a new boundary.
- **`COGNITIVE_RUNTIME.md`** — item 11.15 partially closed by the model-routing work already merged.

A separate, previously-raised amendment also remains outstanding: `docs/TILT.md`'s treatment of
polish, which should require a named justification, an honest assessment of near- and long-term
value, and a comparison against the alternative use of that time — rather than treating polish as a
category to be refused.

## 11. Non-goals

Multi-user infrastructure · hosted cloud service · account management, roles or federated identity ·
commercial packaging · investor-facing polish · frontend redesign or branding · animation or avatar
work · model-provider marketplace · rebuilding this project as a separate commercial codebase ·
implementing the legacy water-logging endpoints · Usable POC slice 2 scope (which remains
deliberately unscoped pending real feedback).
