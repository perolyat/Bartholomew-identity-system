# CONSTITUTION

> The Architecture Constitution: Bartholomew's non-negotiable principles, architectural
> invariants, and governance rules.
>
> **This document is deliberately different from every other canonical doc.** `MASTER_PLAN.md`,
> `ROADMAP.md`, `DECISIONS.md`, etc. describe current state and change often. This document
> describes enduring intent and should change rarely. Every architectural proposal — new
> subsystem, new pillar, new capability, any deviation from what's written here — should be
> evaluated against this document first. If a proposal conflicts with it, the conflict must be
> resolved explicitly (update this document with rationale, or reject the proposal) — never
> silently overridden by implementation convenience.
>
> **Established:** 2026-07-22, on handover from the project's originating architect
> ("Bartholomew," in the design-conversation transcripts under `docs/design_conversations/`) to
> the repository-native Architect role. See `DECISIONS.md` for the dated governance record of
> that transition. **Current Architect: Claude** (repository-native; see `DECISIONS.md`).
>
> **Amended 2026-07-28** (documentation reconciliation pass 2, per this document's own governance
> rule — recorded explicitly with rationale, not silently overridden): split the "Personality"
> section into enduring character values (kept here) and default persona/product inspiration
> (moved to Experience-layer ownership); added a new "Safety, Accessibility, and Product
> Invariants" section (independent emergency shutdown, capture/recording safety, data portability,
> cognitive accessibility, adaptive notifications, the consumer-value gate) that had no canonical
> home before this pass.
>
> **Amended 2026-08-08** (New Direction reconciliation, per an architecture-review handoff
> reconciled against current repository code — see `DECISIONS.md`'s "One developing digital
> individual — competency and training architecture" entry for the full rationale): added a new
> "One Developing Digital Individual: Competencies and Training" section formalising that
> Bartholomew acquires professional/practical competence (e.g. residential estate management,
> vehicle management, travel planning, finance) the way a human employee does — through training,
> instruction, correction, supervised work, and experience — and that this learning becomes
> available to the one Executive wherever contextually relevant, never as a separate domain brain.
> Distinguishes skill/capability (an executable tool) from competency (learned judgement) sharply,
> for the first time in a canonical doc. Extended "Domain Independence" with the same generalisation
> test applied explicitly to competencies. **Same-day follow-up:** added "Personal learning vs.
> potentially generalisable and system-level learning" — personal learning stays within an
> individual Bartholomew's governed memory and is never auto-promoted to shared/global/product-level
> knowledge; potentially generalisable and system/product learning are distinct candidate
> categories, never automatically promoted either, subject to a future (not-yet-built) governed
> generalisation process. See `DECISIONS.md`'s "Personal, potentially generalisable, and
> system-level learning are architecturally distinct" entry. This amendment does not authorise
> implementation of any competency runtime, training pipeline, cross-instance/product-level learning
> infrastructure, or Estate Management feature — see `ROADMAP.md`'s restructured Stage 5 for the
> (unapproved-until-separately-approved) staged plan.
>
> **Amended 2026-08-15, follow-up pass** (Parking Brake authority tiers): the "One Platform, Many
> Personal Bartholomews" section's hybrid/local-Governance subsection gains the requirement for two
> distinct Parking Brake authority tiers — an independently enforceable **Personal/User** brake and
> a separate higher-scope **Platform/Admin** brake — with their precedence stated (a platform halt
> overrides personal autonomy/trust/approvals and cannot be overridden by a user; one user's brake
> never halts another's), and the principle that brake scope is Governance authority enforced below
> the presentation layer, not a UI feature. The **conflict-surfacing rule gains a ninth property**
> covering these tiers. The scope/precedence semantics themselves live in `COGNITIVE_RUNTIME.md`'s
> "Authority tiers" subsection, which is their canonical authority — this document states the
> enduring requirement and does not restate the mechanics. Documentation-only; the current
> single-user brake conceptually *is* the Personal/User tier and is sufficient, and no
> Platform/Admin tier should be built now. See `DECISIONS.md`'s "Parking Brake authority tiers"
> entry.
>
> **Amended 2026-08-15** (platform/personal-identity architecture, per this document's own
> governance rule — recorded explicitly with rationale): added a new "One Platform, Many Personal
> Bartholomews" section establishing that Bartholomew is ultimately **one shared platform serving
> many strongly isolated personal Bartholomew identities**, that a new user does not receive a
> duplicated copy of the stack or model, that **Bartholomew is not the LLM** (models are a
> replaceable cognitive resource, not Bartholomew's identity), that personal identity/state must be
> portable across devices, backends, databases, providers and model generations, that a lightweight
> client is the long-term direction while local Governance authority (the parking brake above all)
> must never become cloud-dependent, and that strong isolation between personal identities is
> non-negotiable. The section closes with a **binding conflict-surfacing rule**: eight named
> properties that a future proposal may not silently trade away without first informing the user.
> This amendment is **documentation-only and authorises no implementation** — the current
> single-user PoC, its SQLite persistence, local execution and absence of auth all remain correct
> for this stage, and `docs/TILT.md`'s real-world-use priority is unchanged. See `DECISIONS.md`'s
> "One shared Bartholomew platform; many strongly isolated personal Bartholomew identities" entry
> for the full rationale, `COGNITIVE_RUNTIME.md`'s "Personal-identity ownership" subsection for
> what the code assumes today, and `ASSUMPTIONS.md` A9.
>
> **Amended 2026-08-12** (Usable POC / time-to-real-use prioritisation, per this document's own
> governance rule — resolved explicitly with rationale, not silently overridden or left as an
> unreconciled conflict): a repository-grounded assessment found that this document's "Development
> Philosophy" section, followed faithfully, had produced genuinely well-built, well-tested
> machinery (persistence, governance, competency retrieval) that had not yet been put in front of
> real use — most concretely, ordinary conversation wrote nothing durable and retrievable. The
> "Development Philosophy" section below is revised so that its architecture-first default no
> longer implies holding back a vertical slice that is already sufficiently safe and functional to
> generate meaningful real-world feedback. This is a **narrowing of when the default applies, not
> a repeal of it**: architecture-first discipline still governs how a slice is built, and every
> other engineering standard in this document (fail-closed governance, privacy-first handling,
> verification-first engineering, one authority per concept, testability, explainability) is
> unchanged. See `DECISIONS.md`'s "Usable POC / time-to-real-use prioritisation" entry and
> `docs/TILT.md` (the current operational application of the revised principle — which vertical
> slice is next, what is deferred, and why).

---

## Project Vision

Bartholomew is not an AI assistant. It is intended to become a **lifelong digital companion**.

The closest fictional comparisons are JARVIS, FRIDAY, Cortana (Halo), and Samantha (*Her*) —
but none of those are complete.

The long-term goal is software that feels less like an application and more like another
intelligent being living inside the user's devices. The user should eventually think:

> "My phone became intelligent."

— not —

> "I installed an AI app."

This distinction drives nearly every architectural decision.

## Primary Goal

The goal is not answering questions. **The goal is improving the user's life.** Conversation is
only one capability.

Bartholomew exists to:
- reduce cognitive load
- remember everything important
- automate repetitive work
- improve health
- improve finances
- improve relationships
- improve organisation
- proactively help
- become increasingly useful over decades

## Core Principle: Responsibilities, Not Technologies

The architecture is organised around **responsibilities**, never technologies. Technology
changes. Responsibilities remain.

- Wrong: "Email Module"
- Correct: "Observation Layer" — because observations may originate from email, SMS, bank,
  camera, microphone, clipboard, browser, or calendar.

## The Five Pillars

Everything belongs inside one of five major systems. No new subsystem should exist outside
these pillars without extremely strong justification.

### 1. Governance

Governance always sits above every other subsystem. Nothing bypasses Governance — not Memory,
not Executive, not Plugins, not AI.

Governance owns:
- permissions
- privacy
- user approval
- autonomous limits
- auditing
- explainability
- parking brake
- emergency shutdown
- trust

### 2. Executive

The Executive **decides**. It does not observe. It does not remember. It decides.

Responsibilities:
- planning
- prioritisation
- scheduling
- orchestration
- delegation
- recommendation generation
- action selection

The Executive is also the **sole place** where relevant memory, retrieved competencies, current
goals/context, and available capabilities are combined into a decision. Competencies (see "One
Developing Digital Individual" below) inform what the Executive decides is good judgement; they
never decide anything themselves. There is exactly one Executive — never one per domain,
competency, or capability.

### 3. Memory

Memory exists to create institutional knowledge about one individual. The long-term competitive
moat is not the language model — **it is decades of accumulated understanding of the user.**
Memory therefore becomes more valuable every year.

Memory should remember: preferences, routines, relationships, habits, life events, goals,
projects, patterns, successful interventions, mistakes.

Memory should not become a dumping ground. Only useful knowledge belongs in long-term memory.

Memory is also the one substrate that holds what Bartholomew has been **trained** on and what it
has **learned through experience** — domain knowledge, procedures, heuristics, corrections, and
competency evidence, alongside the preferences and facts above. A competency (estate management,
finance, travel, vehicle management, or any future one) is a description of what has been learned,
built from this same shared substrate — never a private memory belonging to that competency alone.
See "One Developing Digital Individual" below.

### 4. Capability

Capabilities (also called skills) are interchangeable, relatively simple **executable tools** —
send an email, read a calendar, search the web, read a document, create a notification, interact
with a device, eventually prepare or execute a payment. Capabilities should be independently
replaceable. No capability should be tightly coupled to another.

A capability is not the same thing as a **competency** — see "One Developing Digital Individual"
below for the distinction. Capabilities stay deliberately simple execution mechanisms; they do not
carry domain judgement, and high-level judgement about *when* and *how well* to use a capability
must not be embedded inside the capability's own implementation.

### 5. Experience

Experience is the human side: voice, conversation, notifications, avatar, emotional expression,
visual interface, animations, personality. This pillar is intentionally separate from
intelligence.

## Observation Philosophy

One of the biggest architectural shifts: the system is **reality-first, not source-first**.

Email is not important. Bills are important. Subscriptions are important. Appointments are
important. Purchases are important. The observation layer extracts reality from arbitrary
sources.

**Pipeline:** Observe → Interpret → Recommend → Act

- Observations are immutable facts.
- Interpretations are meaning.
- Recommendations are advice.
- Actions change reality.

These are distinct architectural layers.

## Domain Independence

Nothing should be designed specifically for bills. Bills were only the first proving ground. The
architecture should work identically for subscriptions, appointments, shopping, travel, health,
banking, documents, relationships, etc.

**If adding the third domain requires schema redesign, the architecture has failed.**

The same test applies one level up, to learned competence (added 2026-08-08 — see "One Developing
Digital Individual" below): nothing should be architected specifically for residential estate
management. Estate management is the first serious **competency** proving ground, the same role
bills played for the observation pipeline — not a special case the architecture exists to serve.
**If adding a second competency (e.g. vehicle management) — and especially a structurally
different third one (e.g. travel or finance) — requires redesigning the core competency model, or
introducing another Executive, Memory authority, or Governance path, the abstraction has failed.**

## One Developing Digital Individual: Competencies and Training

*(Added 2026-08-08, per the architecture-review handoff reconciled in `DECISIONS.md`. This section
formalises a principle the rest of this document already implied — one Executive, one Memory
substrate, cross-cutting Governance — by making it explicit for how Bartholomew becomes good at
things over time.)*

**Bartholomew is one developing digital individual.** It is not, and must not become, a collection
of independent domain-specific applications, "Manager" services, or per-domain brains. Estate
Management, Finance, Travel, Vehicle Management, and every future professional or practical area of
responsibility are **competencies that this one Bartholomew has learned**, not separate cognitive
systems that happen to share a name. This follows directly from the Five Pillars above: there is
one Executive that decides, one Memory substrate that remembers, one set of capabilities Bartholomew
can use, and one Governance that constrains all of it — competence in a new area extends what that
one individual knows and can judge well, it does not fork any of the four.

### Skill/capability vs. competency

These are not the same concept, and must not be conflated:

- A **skill/capability** (Pillar 4, above) is a relatively dumb, executable ability or tool: send
  an email, read a calendar, search the web, read a document, create a notification, interact with
  a device, eventually prepare or execute a payment. It performs an authorised action. It carries no
  domain judgement of its own.
- A **competency** is learned professional or practical ability — for example residential estate
  management, financial management, travel planning, vehicle management. A competency can combine
  domain knowledge, procedures, heuristics, judgement, the relevant capabilities needed to act,
  user-specific knowledge, prior experience, corrections, observed outcomes, a proficiency/confidence
  level, known knowledge gaps, and supervision requirements.

The relationship between the two: a competency describes what good judgement looks like in some
area of responsibility; the Executive decides what should happen, informed by that judgement;
Governance decides whether it may happen; a capability is how it is physically done. **A competency
must never become another autonomous agent** — it does not decide, does not execute, and does not
own memory, planning, or governance of its own. Concretely, this repository must never introduce an
`EstateExecutive`, `EstatePlanner`, `EstateMemory`, `EstateGovernance`, `EstateLLM`, or any
comparable per-domain cognition/runtime — for Estate or any other competency — unless the repository
contains an unavoidable technical reason, in which case the conflict must be surfaced explicitly to
the Architect/project owner rather than implemented.

### Training vs. configuration

**Training is how Bartholomew develops competence — analogous to training a human employee**, not
a settings screen. Training may include: formal reference material (manuals, procedures, regulatory
material); direct user instruction (rules and preferences the user states outright); demonstration
(the user shows Bartholomew how a task should be handled); correction (the user identifies a
misunderstanding and explains why); supervised work (Bartholomew proposes, the user approves or
corrects); independent experience (an action's outcome is observed and reflected on); and continual
consolidation (evidence strengthens, weakens, or qualifies what was previously learned). **Ordinary
operational training does not mean foundation-model fine-tuning.** In the overwhelming common case,
training updates structured knowledge, procedures, examples, preferences, corrections, competency
evidence, and experience records inside the shared Memory substrate — not model weights. Training
must never bypass provenance, consent, privacy, Governance, or audit; a "trained" fact or procedure
is held to the same governance standard as any other memory.

### Shared memory and transferable learning

Estate-related information (or any other competency's information) must never be conceptually
trapped inside a domain-specific memory. Property information, assets, warranties, manuals,
contractors, maintenance history, quotes, outcomes, and user preferences are represented through
Bartholomew's one shared Memory substrate (Pillar 3), with appropriate provenance, privacy
classification, relationships, and retrieval semantics — the same substrate every other kind of
knowledge lives in. What Bartholomew learns in service of one competency **may transfer** to improve
judgement elsewhere when contextually appropriate — for example, experience comparing contractor
quotes for a home repair may improve Bartholomew's general ability to evaluate quotes in an
unrelated context. Transfer must never be indiscriminate: any such generalisation must remain
subject to relevance, provenance, confidence, privacy, Governance, and domain-appropriate
boundaries, the same as any other governed use of memory. A competency is a lens the Executive can
apply to shared knowledge, not a walled garden that owns a private copy of it.

### Personal learning vs. potentially generalisable and system-level learning

*(Added 2026-08-08, same pass. This is a different, larger-scope boundary than "Shared memory and
transferable learning" above — that section concerns learning moving *within* one individual
Bartholomew, across its own competencies. This section concerns whether and how learning could
ever move *between* separate individual Bartholomew instances, or into the product itself — a
categorically stronger privacy and governance bar. The two must not be conflated.)*

An individual Bartholomew — including an early or test instance, developed and used before the
product's eventual customer release — accumulates substantial personal knowledge, experience,
corrections, procedures, heuristics, preferences, outcomes, and context about its user. Three
categories of what is learned must remain architecturally distinguishable, from the moment
candidate learning is produced onward:

- **Personal learning** — belongs to a particular user/individual Bartholomew instance: personal
  preferences, routines, relationships, household/property information, personal history,
  user-specific behavioural patterns, trusted contractors, personal thresholds, private documents,
  and other contextual information about that user's life. This must remain within that
  individual's governed memory unless an explicit, appropriate mechanism permits otherwise, and
  **must never automatically become global or shared Bartholomew knowledge.**
- **Potentially generalisable learning** — a lesson discovered through one individual's experience
  that *may* be useful beyond that individual: a generally useful reasoning heuristic, an improved
  procedure, a recurring failure mode, a better way of interpreting a class of documents, a safer
  workflow, a generally useful competency lesson, a correction to an incorrect assumption, or
  evidence that a reasoning strategy works or fails under particular conditions. Such a lesson is
  only ever a **candidate**; it must never be automatically promoted into shared/global Bartholomew
  knowledge.
- **System/product learning** — an observation primarily about Bartholomew itself rather than
  about the user's world: a workflow producing excessive false positives, a reasoning strategy that
  repeatedly fails, a safety check that needs to occur earlier, a competency missing an important
  procedure, an interface causing repeated misunderstanding, a default behaviour that is
  consistently inappropriate. Distinguishable from both personal memory and ordinary competency
  knowledge.

**Removing a person's name alone does not make information non-personal.** Any future
generalisation of a candidate lesson — from one individual Bartholomew toward the product or toward
other instances — must consider whether the lesson is genuinely independent of the individual,
whether it can be genuinely de-identified, its provenance, consent and user expectations,
sensitivity, re-identification risk, confidence, validation across cases where appropriate,
Governance, and auditability. **Where safe generalisation cannot be established, the learning
remains individual.** This mirrors, and does not weaken, this document's Sovereign Principle and
data-portability invariant (see "Sovereign Principle" and "Safety, Accessibility, and Product
Invariants" below): trust in Bartholomew includes trusting that one's own experience does not
silently become someone else's, or the product's, knowledge.

Early and test Bartholomews are, in this sense, **experienced predecessors** of later Bartholomew
instances — not sources of shared memory for them. Their private memories do not become the
memories of future users. Properly validated, appropriately de-personalised lessons discovered
through their experience may eventually contribute to improving future Bartholomew versions
(training material, competency definitions, procedures, defaults, or product releases), but only
through an explicit, governed generalisation process — never automatically, and not by a mechanism
that exists today. See `COGNITIVE_RUNTIME.md`'s "Personal, generalisable, and system-level learning
classification" section for how this constrains the candidate-learning data shape, and
`DECISIONS.md`'s corresponding entry for the full rationale. **This principle does not authorise
building any cross-user, cross-instance, or product-level learning infrastructure now** — it
constrains S5.1 onward so that distinction remains representable later, rather than requiring the
learning model to be redesigned when a governed generalisation mechanism is eventually proposed.

### Governance is cross-cutting, not a peer

Governance (Pillar 1) constrains the entire system — Memory, Training, Competencies, Capabilities,
and the Executive alike — not just one runtime checkpoint among several. At runtime, Governance may
appear as the admission gate between an Executive proposal and execution (this is how the Runtime
Contract in `COGNITIVE_RUNTIME.md` implements it), but architecturally it sits above every
subsystem, the same way it already sits above Memory, Executive, Capabilities, and every other
pillar per the Five Pillars section above. Training and competency acquisition are not exceptions:
a competency's knowledge, procedures, and evidence are governed data, subject to the same consent,
privacy, and audit requirements as everything else Memory holds.

### Specialised interfaces are views, not separate applications

A domain-specific UI (an Estate view showing properties, rooms, assets, appliances, warranties,
documents, maintenance, contractors, jobs, quotes, costs, and upcoming obligations; a future
Vehicle or Travel view; etc.) is a **lens or control surface over the one Bartholomew** — reading and
writing the same shared Memory, routed through the same one Executive and the same Governance — not
a separate application with its own intelligence. Such a UI must never become the authoritative
source of the domain state it displays; it renders and edits shared Bartholomew state, the same as
every other interface.

### Acceptance test

Residential Estate Management is the first serious proving ground for this competency architecture,
not the architecture itself (see "Domain Independence" above for the generalisation test this
implies). The architecture should be judged partly by whether Estate Management can be implemented
without creating a second brain, a separate memory authority, a separate Executive, a separate
Governance path, or duplicated reasoning infrastructure — and whether a second and, especially, a
structurally different third competency can be added without redesigning the core competency model.
See `ROADMAP.md`'s Stage 5 for the staged, separately-approved plan this principle governs, and
`COGNITIVE_RUNTIME.md` for how the Runtime Contract is conceptually extended to retrieve and apply
competencies without creating a second decision authority.

## One Platform, Many Personal Bartholomews (added 2026-08-15)

*(Added 2026-08-15. This section states enduring architectural intent, **not** current
implementation. Today's repository runs exactly one personal Bartholomew identity on a single-user
deployment, and that is correct for the current stage — see "What this does not authorise" at the
end of this section, `ASSUMPTIONS.md` A9, and `DECISIONS.md`'s "One shared Bartholomew platform;
many strongly isolated personal Bartholomew identities" entry for the full rationale. Nothing here
authorises building multi-user infrastructure now.)*

**Bartholomew is ultimately one shared platform serving many strongly isolated personal
Bartholomew identities.** A new Bartholomew user must never require the creation or distribution
of an entirely separate copy of the complete Bartholomew software stack, AI model, cognition
system, or infrastructure. Individuality comes from persistent, isolated, personal state — not
from duplicating the machinery.

The conceptual model is the way a person experiences a service like ChatGPT: shared underlying
software, models and infrastructure, yet an experience that is unmistakably *theirs* and
persistent across sessions. For Bartholomew the personal half is far larger — memory,
preferences, permissions, relationships, goals, history, learned understanding, autonomy/trust
configuration, and the evolving model of the user's household and world — but the structural
insight is the same.

### The three layers that must never be conflated

1. **The Bartholomew platform** — shared software, architecture, the common Executive mechanisms,
   the common Governance mechanisms, capability infrastructure, model access, updates, and other
   shared services. This is what the repository builds.
2. **Underlying intelligence and resources** — LLMs, multimodal models, specialist AI services,
   reasoning engines, and other computational resources Bartholomew *uses*. These are replaceable
   suppliers of cognition, not Bartholomew.
3. **A user's personal Bartholomew** — the persistent, isolated identity and state representing
   one individual's memories, preferences, goals, relationships, permissions, history, learned
   understanding, trust/autonomy configuration, and evolving model of their life. This is what the
   user means by "my Bartholomew."

From the infrastructure side, users may share substantial parts of layers 1 and 2. From the user's
side it must nevertheless feel like: **"This is my Bartholomew."** That personal continuity is a
core product characteristic, not a presentation detail.

### Bartholomew is not the LLM

**Bartholomew must never be architecturally defined as an LLM, or as any particular AI model.**
The model layer is a *cognitive resource available to* Bartholomew. Bartholomew itself is the
persistent Personal Executive System surrounding and orchestrating those resources: its Executive,
Memory, Capability, Governance and Experience pillars, its persistent personal state, provenance,
learned user understanding, autonomy/trust model, and ongoing continuity.

The practical consequence: Bartholomew must eventually be able to upgrade, replace, or mix
underlying models **without destroying the user's personal Bartholomew identity.** A model
generation change is a supplier change, not a bereavement. This is the same "responsibilities, not
technologies" principle stated at the top of this document, applied to cognition itself — and it
is why `COGNITIVE_RUNTIME.md`'s ownership table names *Identity System*, *Kernel Executive* and
*Memory Substrate* as owners, never a model.

### Bartholomew employs an ecosystem; it does not become it (added 2026-08-27)

The subsection above states this for the model layer. It holds equally for **everything external
that Bartholomew can use.** Frontier models, specialist AI agents, coding and research systems,
conventional APIs, SaaS applications, operating-system services, local applications, web services,
device and edge capability agents, sensors, smart-home systems and future capability providers are
**resources Bartholomew can employ — not entities that become Bartholomew.** This is the
"Responsibilities, Not Technologies" principle at the top of this document applied one step further
out: the architecture reasons about a **needed capability**, never about a named vendor.

Three consequences are binding.

**The provider supplies capability; Bartholomew owns the objective.** An external system may
execute a bounded task. It never becomes the owner of the user's ongoing objective, and it must
never acquire authority over Bartholomew's Governance, identity, personal Memory policy or autonomy
boundaries. The Executive decides and delegates, Governance decides whether it may happen, and the
provider is how it is physically done — the capability/competency distinction stated above, applied
to suppliers. **An external capability provider is a capability, however intelligent it is
internally.** Consequently no provider may name an architectural concept: there must never be a
`ClaudeExecutive`, `GeminiMemory`, `OpenAIPlanner`, `SiriManager` or any comparable provider-named
cognitive authority, for exactly the reason there must never be an `EstateExecutive`. Provider
integrations belong beneath the existing Capability pillar as interchangeable adapters.

**External output is evidence with provenance — not truth, not learning, and not memory.** What an
external system returns is an observation or a result carrying its source, governed by the same
consent, privacy, provenance and audit rules as any other input. Whether it becomes longer-lived
knowledge is decided by the learning rules already stated above — observable outcome, verification,
repeated evidence, user correction, confidence, source provenance — and by the personal /
potentially generalisable / system-level classification, which is unchanged. Learning *which*
external capability works well for *what* is ordinary competency development through this same one
Memory substrate, not a second learning system, and never a licence for uncontrolled
self-modification or autonomous modification of model weights.

**Ecosystem progress should become supply, not obsolescence.** Bartholomew should avoid
unnecessarily duplicating a capability it can obtain safely, reliably and governably from outside;
a stronger reasoning model, coding agent, vision or speech system, or application integration
should *increase* what Bartholomew can do. The durable asset is not the intelligence — it is
decades of governed personal memory, learned competence, provenance, objectives and executive
continuity about one individual, which no supplier provides.

**This section authorises no implementation.** It does not authorise a capability broker, provider
selection or routing logic, external-agent integration, provider-performance learning, or a
provider marketplace. `docs/TILT.md` remains the near-term sequencing authority, and the correct
first step is one narrowly scoped external provider performing one bounded task through the
existing governed seam. See `DECISIONS.md`'s "Bartholomew is the persistent executive above an
ecosystem of external intelligence and capability providers" entry for the decision, alternatives
and consequences, including the one genuine gap it records rather than closes: Bartholomew has no
structured representation of a long-lived objective today.

### Identity is portable across infrastructure

A user's Bartholomew must remain their Bartholomew even if they replace their phone or computer;
the UI/client changes; the backend is migrated; servers are replaced; databases are replaced or
upgraded; AI providers change; underlying model generations change; capabilities are added or
removed; or the deployment topology evolves.

**Bartholomew identity is therefore logically independent of the particular compute
infrastructure, model provider, device, or application instance currently serving it.** Personal
continuity and state must be portable, recoverable and capable of migration, subject to
Governance, security, privacy and user control. This is the same commitment as the data-portability
invariant below ("Safety, Accessibility, and Product Invariants" §3), stated one level up: not only
must the user be able to *export* their data, the architecture must not trap their identity inside
one implementation technology in the first place.

### Client versus Bartholomew

The eventual customer application downloaded to a phone, computer or other device **should not
need to contain the entirety of Bartholomew's intelligence or platform.** The long-term
architecture should permit a relatively lightweight Bartholomew client communicating securely with
Bartholomew services.

A client may legitimately own: interaction/UI; authentication; notifications; local device
interfaces and selected sensors; selected local/private state; encryption and key functions where
appropriate; local permissions; device-specific capability adapters; offline and degraded
functionality; and locally enforceable safety/governance controls. Heavy or shared functions may
eventually run remotely: expensive model inference, complex reasoning, shared capabilities,
specialist AI orchestration, large-scale retrieval, background cognition, platform updates.

**This is not a licence to make Bartholomew cloud-only.** It defines what *may* move, not what
must.

### Hybrid architecture and local Governance authority

Bartholomew should be capable of evolving toward a hybrid Personal Executive architecture: some
responsibilities remote or shared, some necessarily local or locally enforceable. This extends —
and does not replace — `DECISIONS.md`'s deployment-architecture entry, which remains the authority
on *where* authority sits.

> **Pointer (2026-08-17):** that authority is now `DECISIONS.md`'s **"Deployment architecture —
> server-centric Bartholomew with local/edge capability agents"**, which supersedes the
> "hybrid local-first" entry this section previously named. Core cognition and personal memory are
> intended to be server-centric by default, with native device applications acting as governed
> capability bridges rather than independent brains. **This section is unchanged in substance and
> is not weakened by that decision** — the paragraphs below, and the "not a licence to make
> Bartholomew cloud-only" constraint above, are named by the superseding entry as binding
> constraints *on* it.

Because Bartholomew may eventually control significant parts of a person's digital and physical
environment, **central infrastructure must never become the only authority capable of stopping or
constraining the system.** The architecture must preserve the ability for critical local
Governance controls to remain authoritative: the Parking Brake / kill switch, local device
permissions, credential and security boundaries, safe degradation, defined loss-of-connectivity
behaviour, and user control over local execution.

**A cloud or service outage must never create a condition in which the user cannot locally stop
Bartholomew from acting on their devices.** This is the multi-user, platform-era restatement of
the independent-emergency-shutdown invariant below (§1), and it constrains any future
lightweight-client design: the client is allowed to be thin in cognition, but never thin in the
ability to stop.

**Two Parking Brake authority tiers are therefore required** (added 2026-08-15): a
**Personal/User** brake, independently enforceable, halting execution for that one personal
Bartholomew without affecting any other user's; and a separate, higher-scope **Platform/Admin**
brake allowing authorised platform governance to halt execution platform-wide in a serious safety,
security, governance, systemic-defect or critical-operational emergency, without disabling users
one at a time. An active Platform/Admin halt overrides subordinate personal autonomy permissions,
trust levels, approvals and execution authority — **a user must not be able to override a
platform-wide safety halt through personal settings** — while one user's Personal brake must never
stop another user's Bartholomew. The Platform tier adds an authority above the user; it removes
nothing from the local authority described in the paragraph above. Parking Brake scope is
**Governance authority, not a UI feature**: the halt must be enforced below the presentation layer
at the execution boundary, and a client crashing, disconnecting or being bypassed must not by
itself invalidate the halt state. `COGNITIVE_RUNTIME.md`'s "The kill-switch: `ParkingBrake`" →
"Authority tiers" section is the **canonical authority** for these scope and precedence semantics
and is not restated here; see `DECISIONS.md` for the decision record. This requires no
implementation now — the current single-user brake conceptually *is* the Personal/User tier and is
sufficient at this stage.

### Strong isolation between personal identities is non-negotiable

One user's Bartholomew must never accidentally receive, expose, infer from, modify, act upon, or
otherwise access another user's private state. The only exception is a future feature that
*intentionally* permits interaction and that Governance authorises — designed deliberately, never
arrived at by accident.

**User ownership/tenancy must therefore become a first-class architectural concept wherever
persistence or execution requires it.** Anything that persists personal state, executes on a
user's behalf, schedules background work, or records governance/audit provenance must eventually
be able to answer: *whose Bartholomew is this?*

The failure modes to design against — each of which the current single-user PoC exhibits
legitimately, and which must not harden into permanent architecture — are: one process implicitly
equalling one user; one database implicitly equalling one user; global singleton personal state;
storage records without ownership where ownership will eventually be required; local filesystem
paths acting as permanent identity boundaries; APIs assuming a trusted single-user environment;
Executive state that cannot be separated by user; memory that cannot be associated with an
explicit personal identity; scheduler or background work without ownership; capabilities that
cannot determine on whose behalf they are executing; and Governance or audit records lacking
sufficient identity provenance. `COGNITIVE_RUNTIME.md`'s "Personal-identity ownership" subsection
records which of these exist today, and how each is classified.

### Personal learning does not become platform knowledge

The distinction between personal, potentially generalisable, and system/product learning is
already established above in "Personal learning vs. potentially generalisable and system-level
learning," and is **not restated here** — that section remains the single authority. This section
adds only its platform-era consequence: *shared platform improvement must never be conflated with
sharing personal Bartholomew memories.* Only genuinely non-personal or properly depersonalised
lessons may ever become candidates for shared platform learning, through an explicit, governed
process that does not exist today. Removing a name is not depersonalisation.

### The governing principle for the current stage

> Build the current single-user Bartholomew as the first deployment of an architecture that can
> later support many strongly isolated personal Bartholomew identities through lightweight clients
> and shared backend services, without introducing unnecessary distributed-system complexity
> before the usable PoC proves the product.

The current single-user Bartholomew is conceptually **the first personal Bartholomew identity
running on an early deployment of the future platform** — not a different, throwaway thing. SQLite,
local execution, the current web application, process-global runtime state and the absence of
authentication all remain appropriate for the PoC. The requirement is only that these temporary
deployment choices never become inseparable from Bartholomew's identity or conceptual
architecture.

### What this does not authorise

This section authorises **no implementation work**. It does not authorise multi-tenant
infrastructure, cloud or microservice deployment, authentication systems, a client/server split, a
tenancy migration, schema rewrites to add ownership columns, or any broad refactor of the current
PoC to make it multi-user. `docs/TILT.md`'s priority — real-world use of a working vertical slice
— is unchanged and takes precedence. Future platform work of that kind requires its own separate
proposal and approval, the same as any other subsystem.

### Conflict-surfacing rule (binding)

This architectural decision is considered crucial to the success of the project, and the ordinary
"resolve conflicts explicitly" rule in this document's header is **strengthened** for it.

If a proposed feature, implementation shortcut, architecture change, refactor, dependency choice,
or user request would materially jeopardise any of the following, the architect or builder **must
explicitly inform the user of the conflict before implementing it** — not discover it afterwards,
and never trade it away silently for implementation convenience:

1. one shared platform / many personal identities;
2. strong isolation between personal Bartholomew identities;
3. persistence of an individual Bartholomew identity;
4. portability of identity and personal state;
5. separation between Bartholomew and its underlying LLM/model;
6. hybrid architecture and local Governance authority (including the ability to stop Bartholomew
   locally during a cloud outage);
7. the eventual lightweight-client architecture;
8. the ability to replace infrastructure or models without replacing the person's Bartholomew;
9. **the two Parking Brake authority tiers and their precedence** (added 2026-08-15) — an
   independently enforceable Personal/User brake that never halts other users; a separate
   platform-wide Platform/Admin brake that a user cannot override and that does not require
   disabling users individually; and the enforcement of both below the presentation layer at the
   execution boundary rather than in a client. Collapsing the tiers into one undifferentiated
   switch, letting one user's brake affect another, or making a client the only thing holding the
   halt state are conflicts under this rule, not implementation details.
10. **Bartholomew's executive authority over external providers** (added 2026-08-27) — the
    separation between Bartholomew and any external intelligence or capability supplier; the rule
    that a provider executes bounded tasks and never owns the user's objective; and the rule that
    external output is evidence with provenance rather than truth, learning or memory. Letting an
    external system acquire authority over Governance, identity, personal Memory policy or autonomy
    boundaries, introducing a provider-named cognitive authority, or promoting an external
    assertion into durable knowledge outside the existing learning and Governance rules are
    conflicts under this rule, not implementation details.

Surfacing the conflict is the requirement; the user may then decide. Proceeding without surfacing
it is a governance violation, not a judgement call. See `CHECKLISTS.md`'s "Platform and
personal-identity architecture checklist" for the operational form of this rule, and "Expectations
of the Architect" below.

## Current Philosophy on Persistence

The project intentionally chose not to persist observations yet. Instead:

Observation → Interpretation → Knowledge

Knowledge becomes persistent. This decision was made to avoid premature complexity. **The
Architect may revisit this later.**

## Automation Philosophy

Autonomy is earned. Not enabled.

The roadmap begins with recommendation-first. Later, actions that are repeatable, low-risk,
well-understood, and user-approved become autonomous.

**Baby Mode ladder:**

Observe only → Recommend → Assist → Limited automation → Governed automation → Trusted autonomy

## Sovereign Principle

The user is always the final authority. Never optimise for maximum autonomy. **Optimise for
maximum trust.**

## Safety, Accessibility, and Product Invariants (added 2026-07-28)

Six enduring principles, identified as missing from the canonical documentation set during the
2026-07-28 planning reconciliation. Recording their existence here does not authorise
implementation of any dependent feature — it ensures the requirement is written down as governed,
canonical guidance before any future stage (Stage 1, 5, 6, or otherwise) is separately approved to
build against it.

### 1. Independent emergency shutdown

The user must be able to stop Bartholomew through a path outside Bartholomew's own ordinary
application control. That path must remain available even if Bartholomew controls or interferes
with the keyboard, mouse, screen, browser, or normal software UI. **Goodwill, expected
cooperation, and ordinary in-process controls are not security mechanisms.** An emergency
shutdown path that depends on Bartholomew's own code choosing to honour it is not an emergency
shutdown path. This is an enduring safety invariant, not an implementation detail — see
`ROADMAP.md`/`COGNITIVE_RUNTIME.md` for tracking of the (not yet built) mechanism itself.

### 2. Capture and recording safety

Audio, video, and other capture must be jurisdiction-aware. Before any real capture capability
ships, the design must account for: whether recording is legal in the operating jurisdiction;
whether consent or notice is required; whether audio and video rules differ; retention
limitations; deletion and revocation; public-versus-private context; and a changing jurisdiction
while the user is travelling. **Stopping, disabling, or tearing down capture must never require
permission to continue capturing** — teardown is not a governed "start" and must not be gated as
one (see `COGNITIVE_RUNTIME.md`'s "Device surfaces" section and `ROADMAP.md` Stage 6 for the
existing single-start governance seam this invariant constrains).

### 3. Data portability

Users must be able to export their memories, preferences, personal model, governance settings,
provenance, approvals and audit history, and active goals/unresolved matters. **Trust may be a
product advantage; lock-in must not be.** This is an enduring commitment independent of which
deployment architecture (`DECISIONS.md`) or hosting choice the user has made.

### 4. Cognitive accessibility

Bartholomew must actively reduce cognitive and executive-function burden — this is not merely a
UX nicety but a core expression of the Primary Goal above. In particular: important unresolved
matters must not disappear merely because the user sent a message, took an intermediate step, or
otherwise moved on to something else. See `COGNITIVE_RUNTIME.md`'s `awaiting_response` obligation
state for the runtime-lifecycle mechanism this invariant requires.

### 5. Adaptive notifications without notification fatigue

Notifications must adapt to topic, urgency, time sensitivity, risk, user preference, current
context, and previous responses. Repetition must remain useful — a reminder that has stopped
changing the user's behaviour or informing them of something new is fatigue, not diligence.
Message blindness (the user learning to ignore Bartholomew's notifications because too many were
unhelpful) is a trust failure, not merely an annoyance.

### 6. Consumer-value gate

Features should be prioritised by whether they materially: reduce cognitive burden; reduce life
administration; prevent important matters from being forgotten; improve the user's outcomes; and
preserve or increase trust. **Architectural sophistication alone is not sufficient product
value.** A feature that demonstrates an impressive cognitive architecture but does not clear this
gate should not be prioritised ahead of one that does.

## Architectural Principles

The system should:
- prefer composition over inheritance
- prefer interfaces over implementations
- prefer events over tight coupling
- prefer responsibilities over mechanisms
- prefer replaceability over optimisation
- prefer explicit governance over implicit behaviour
- fail safely
- be explainable
- be observable
- be testable
- be evolvable

## UX Principles

The application should feel alive. Setup should feel like "my phone just became intelligent."
The user should almost never need forms. Voice should be the primary interface; typing is
secondary. Configuration should disappear wherever possible.

## Personality

**Split 2026-07-28 between enduring character values (this document) and switchable presentation
(the Experience/persona-pack layer).** This document previously stated the "1950s gentleman"
aesthetic as if it were itself a constitutional invariant. It sat oddly next to the persona/traits
ownership boundary already established in `DECISIONS.md` (item 11.12, 2026-07-22): persona packs
own *how* Bartholomew presents (tone, style — switchable, user-configurable), while `Identity.yaml`
and this document own *who* Bartholomew stably is. The split below resolves that.

**Enduring character values (constitutional — should change rarely, if ever):**
- Competence.
- Calm authority.
- Warmth without manipulation.
- Honesty.
- Respect for user sovereignty.
- Reliability.
- Non-theatrical behaviour where appropriate.

These are the character traits every persona presentation must embody, regardless of which
persona pack is active. A persona pack that violated one of these (e.g., manipulative warmth, or
theatrical unreliability) would be a governance violation, not a legitimate stylistic choice.

**Default persona/product inspiration (Experience-layer, user-configurable, not an invariant):**
the original product direction — **a 1950s gentleman, filtered through 2025 intelligence**, with
inspirations including JARVIS, Lance Reddick, and FRIDAY — remains the *default* persona pack's
aesthetic. It is owned by `bartholomew/kernel/persona_pack.py`'s `PersonaPackManager` (the same
authority `DECISIONS.md` item 11.12 already established for tone/style) as one pack among
potentially several, not as a constitutional requirement every future persona must satisfy. Users
may configure or switch presentation; they may not configure away the enduring character values
above, since those are governance-level, not presentation-level.

## Long-Term Goal

The ultimate objective is not an assistant. It is creating something approaching a digital
companion. The user should eventually trust Bartholomew similarly to how someone trusts an
exceptionally competent executive assistant who has known them for decades.

## Development Philosophy

Architecture first, by default. Before a capability is built, its design should be understood, not
discovered by accident in production. Large refactors early are encouraged; large refactors late
are failures — a late refactor is a sign the design was wrong earlier, not evidence that skipping
design was fine. Correctness, safety, and one-authority-per-concept are never traded away for
speed.

**That default stops applying, specifically, once a vertical slice is already sufficiently safe
and functional to generate meaningful real-world feedback.** At that point:

> Real-world testing of that slice takes priority over additional polish or hardening of it —
> unless a defect threatens safety, governance, privacy, data integrity, architectural validity, or
> the validity of the experiment itself.

Architecture-first discipline governs *how* a slice is designed and built. It does not justify
holding a working, safe slice back from real use so it can be refined further against hypothetical
future requirements — real usage is itself part of how correct architecture is validated, not a
reward for finishing it. `docs/TILT.md` is the current, actively-maintained operational
application of this principle: which vertical slice is next, what real-world testing has priority
over, and what is deliberately deferred. It is kept separate from this document because that
content changes with every slice; this principle does not.

## Documentation Philosophy

Documentation is considered part of the architecture. Architecture must remain discoverable
without relying on conversation history. The repository should become the authoritative source
of truth. **No architectural knowledge should permanently live only inside an AI conversation.**

## Expectations of the Architect

The Architect should:
- Challenge assumptions rather than simply implement them.
- Read the entire repository before proposing major changes.
- Treat existing documentation as hypotheses to validate against the code.
- Keep documentation synchronized with implementation.
- Prefer simplification over expansion.
- Actively identify architectural debt.
- Preserve the five-pillar architecture unless there is compelling evidence to change it.
- **Surface, rather than silently resolve, any conflict with the nine properties listed in "One
  Platform, Many Personal Bartholomews" → "Conflict-surfacing rule" above.** Implementation
  convenience is never sufficient reason to trade one of them away unannounced.
- Design for a system expected to evolve over decades, not months.
- Avoid premature abstraction, but also avoid domain-specific shortcuts that would block future
  expansion.
- Ensure every significant decision includes explicit rationale, trade-offs, and expected
  long-term consequences (see `DECISIONS.md`'s format).

## Handover Note

This document captures the enduring architectural context and principles developed prior to
2026-07-22. The detailed implementation state, decisions, roadmap, and current code are tracked
in the other canonical docs (`MASTER_PLAN.md`, `ROADMAP.md`, `DECISIONS.md`, `RISKS.md`,
`ASSUMPTIONS.md`, `INTERFACES.md`, `CHECKLISTS.md`, `REVIEWS.md`, `CI.md`, `TEST_MATRIX.md`,
`PERF_BUDGETS.md`, `COGNITIVE_RUNTIME.md`) and are the responsibility of the repository-native
Architect, which is the appropriate place for that knowledge to live.
