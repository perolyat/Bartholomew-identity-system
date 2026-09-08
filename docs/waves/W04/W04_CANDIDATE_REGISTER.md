# W04 — Wave 4 Candidate Register

> **Wave 4 is not selected, and nothing here is scheduled.** This register exists so that
> strategically important ideas have a governed home *before* Wave 4 is scoped, instead of being
> argued from memory when it is. Per `docs/waves/W03/README.md` and `W03_DEFERRALS.md`, Wave 4 must
> emerge from **Wave 3 evidence and real-world acceptance testing**; a candidate appearing here
> confers no priority, no approval and no implementation authority.
>
> **Wave 3 scope is unchanged by this file.** No W03 contract, manifest entry or deferral is
> amended by it. `docs/TILT.md` and the Post-Test #1 readiness bands (`ROADMAP.md`) continue to
> govern what is worked on next.
>
> **Created 2026-09-08** by **PLN-VIO-01 — Violoop Product Learnings Integration**, a
> documentation-only planning session. The external research behind candidates C01–C14 and its
> evidence grading are in `docs/research/RESEARCH_REGISTER.md` (RSCH-01). Vendor claims are graded
> **E3** there and are **never** sufficient to promote a candidate.

## How a candidate leaves this register

Three exits, and only three:

1. **Promoted** — a wave contract adopts it, after its *promotion evidence* below is satisfied by
   **E1** evidence (independently verified by this project: our own real-world use, measurement or
   tests) and any governance gate it names has passed. Promotion is a director decision.
2. **Deferred** — its *deferral/rejection evidence* is met, or a prerequisite is unmet. It stays
   here with the finding recorded.
3. **Rejected** — recorded with the evidence that killed it. Rejected candidates are not deleted.

A candidate whose promotion evidence rests only on external claims (E2–E3) is **not promotable**,
however plausible it is.

## Cross-cutting constraints on every candidate below

These are not restated per candidate; they bind all of them.

- Governance sits above autonomy. Nothing here creates a second decision authority, a second
  memory authority, a second executive or a parallel loop (`COGNITIVE_RUNTIME.md`, ownership table).
- The Parking Brake — personal and platform tiers — remains absolute, and preparation is **not**
  exempt from it.
- Consent, privacy classification, provenance and audit apply to preparation exactly as they apply
  to execution. "It was only preparation" is never a reason to skip a gate.
- Manual lesson acceptance remains authoritative; automatic acceptance is a separate director
  decision (`W03_DEFERRALS.md` #4).
- Nothing here authorises ambient sensing, unattended capture, remote exposure of the runtime, or
  real device actuation beyond what a wave contract already permits.

## Register

| id | Candidate | Layer | Status |
|---|---|---|---|
| W04-C01 | Preparation Plane / Commitment Plane | Executive + Governance | candidate |
| W04-C02 | Actuation Routing Ladder | Capability (Windows companion) | candidate |
| W04-C03 | Ambient contextual assistance / reduced prompting | Interpretation + Experience | candidate |
| W04-C04 | Persistent background delegation runtime | Executive + scheduler/runtime | candidate |
| W04-C05 | Explicit Workflow Memory | Memory | candidate |
| W04-C06 | Generalising a correction beyond the record it superseded | Reflection + Memory + Governance | candidate |
| W04-C07 | Learning-quality and burden metrics | Metrics / product measurement | candidate |
| W04-C08 | Mobile task / approval / status surface | Client (companion) | candidate |
| W04-C09 | Local companion perception + privacy filtering | Observation (edge) | candidate |
| W04-C10 | Extend the shipped Learning Control Centre to new learned-content classes | Experience + Memory agency | candidate |
| W04-C11 | Governed proactivity (Proactivity Eligibility Model) | Executive + Governance | candidate |
| W04-C12 | Sanitized transferable competencies (cross-user) | Memory + Governance | candidate |
| W04-C13 | Long-term model personalization | research only | **research-only; not promotable in W04** |
| W04-C14 | Dedicated local Bartholomew Hub | research only | **research-only; not promotable in W04** |

---

### W04-C01 — Preparation Plane / Commitment Plane

- **Problem.** Bartholomew's autonomy posture is currently near-uniform across very different acts:
  reading a calendar and sending an email are both "an action needing a gate", so caution
  calibrated for the second suppresses the first. The result is a system that asks a lot and
  prepares little — the burden failure Test #1 already recorded (`DECISIONS.md`, Burden Reduction).
- **Expected benefit.** Substantially more work done ahead of the user (drafts, comparisons,
  conflict detection, staged changes) while consequential commitment stays as conservative as it is
  today, or more so. Aggressive preparation + conservative commitment.
- **Dependencies.** Consequence assessment and a recoverability classification that actually
  distinguishes reversible from irreversible; the existing Governance admission gate; audit;
  Identity/authority; a defined place to hold prepared-but-uncommitted work.
- **Governance / safety.** The distinction **must not** become a bypass. Preparation remains
  subject to consent, privacy classification, the Parking Brake and audit; some preparation is
  itself sensitive (reading medical mail to draft a reply). Requires an explicit rule that a
  prepared artifact never self-promotes into a commitment.
- **Layer.** Executive proposal shape + Governance admission, not a new subsystem.
- **Promotion evidence (E1).** Real-world use showing (a) a measurable fall in user-performed steps
  per achieved outcome, and (b) prepared work accepted by the user at a materially better rate than
  it is discarded, with (c) zero instances of preparation crossing into commitment without the
  commitment gate.
- **Deferral / rejection evidence.** Preparation that the user routinely discards (it becomes noise
  and cost, not help); any demonstrated path by which prepared state produced an external effect;
  inability to classify reversibility reliably enough to be safe.

### W04-C02 — Actuation Routing Ladder

- **Problem.** Treating every computer interaction as visual clicking is the least deterministic,
  least verifiable and least recoverable option available, and it is the one most likely to fail
  silently or act on the wrong thing.
- **Expected benefit.** Higher reliability and, more importantly, **better verification and
  recovery**, by preferring routes whose outcome can actually be checked.
- **Direction (normative wording deferred).** Roughly: native/API → structured OS capability →
  accessibility / UI Automation → keyboard/command mechanism → vision-guided GUI. **The ordering is
  a starting hypothesis, not a contract.** The existing Windows actuation architecture
  (`docs/B_GOVERNED_WINDOWS_ACTUATION.md`, the W03 capability vocabulary and its allowlists) is the
  authority on what routes exist today, and platform nuance may reorder the middle of the ladder.
  The governing principle is the stable part: *choose the most deterministic, least destructive,
  easiest-to-verify and most recoverable execution route available for the intended outcome.*
- **Selection inputs (eventual).** Reliability, authority, consequence, recoverability,
  observability, verification quality, latency, privacy, application capability.
- **Dependencies.** The W03 governed action envelope and capability vocabulary; per-route
  verification; abort/lease semantics.
- **Governance / safety.** Route choice must never widen the allowlist: a route unavailable to the
  governed envelope stays unavailable. Vision remains a legitimate fallback, not a default, and not
  a way around a capability that was deliberately not granted.
- **Layer.** Capability / Windows companion.
- **Promotion evidence (E1).** Wave 3 real-world evidence that route choice materially changed
  success, verification quality or recovery on actual tasks — measured here, not claimed elsewhere.
- **Deferral / rejection evidence.** Routes prove indistinguishable in practice on the real task
  mix; or the ladder adds selection complexity without measurable reliability gain.

### W04-C03 — Ambient contextual assistance / reduced prompting

- **Problem.** The current interaction shape is: open Bartholomew → explain context → construct a
  prompt → wait → transfer the result somewhere by hand. That is five user-performed steps before
  any value, and it is why an otherwise capable system reads as "another thing to manage".
- **Expected benefit.** The preferred shape becomes *ambient awareness → contextual understanding →
  prepared assistance → minimal necessary intervention → verified outcome*. Chat remains an
  important modality; it stops being the dominant one for routine executive assistance.
- **Dependencies.** C01 (there is nothing safe to offer without a preparation plane), C11
  (something must decide when offering is warranted), context sources the user has consented to.
- **Governance / safety.** Ambient *awareness* is not ambient *sensing*: this candidate does not
  authorise passive capture and is bounded by the Band B ambient-sensing gates. Interruption cost is
  a first-class input; a system that surfaces more often is a regression under
  `CONSTITUTION.md`'s notification-fatigue invariant.
- **Layer.** Interpretation + Experience.
- **Promotion evidence (E1).** Real use showing prompt burden and manual context-restatement both
  falling, with no rise in unwanted interruptions.
- **Deferral / rejection evidence.** Surfacing accuracy too low to be welcome; users preferring
  explicit invocation once both are available.

### W04-C04 — Persistent background delegation runtime

- **Problem.** "Bartholomew, take care of this" currently means "stay here and watch it happen".
  Work does not survive closing the surface, switching device, or a pause for authority.
- **Expected benefit.** Delegated jobs that survive UI closure, application/device switching, safe
  temporary disconnection, waits for authority, long runtimes, resumable failure and later
  clarification. The user's surface reports *status, progress, blockers, approval requests,
  exceptions, completion and outcome verification* rather than requiring supervision.
- **Dependencies.** Server-centric cognition (`DECISIONS.md`, server-centric deployment entry — the
  natural home for a durable job); the `awaiting_response` obligation state; audit; recovery/undo;
  a defined loss-of-connectivity behaviour with locally enforceable stop authority.
- **Governance / safety.** A long-running job is exactly where an unstoppable process would be most
  damaging: the Parking Brake must terminate in-flight delegated work, and an out-of-process
  emergency stop (`W03_DEFERRALS.md` #8) is a hard prerequisite for any *actuating* delegated job.
  Obligations must never be silently lost.
- **Layer.** Executive + runtime/scheduler.
- **Promotion evidence (E1).** Real evidence of successfully completed unattended delegated work,
  with correct pause-for-authority behaviour and clean recovery from at least one genuine failure —
  and a demonstrated stop that actually stops it.
- **Deferral / rejection evidence.** Emergency stop still absent; recovery unproven; users
  unwilling to delegate at any depth once it is offered.

### W04-C05 — Explicit Workflow Memory

- **Problem.** Bartholomew can learn *about* the user without learning *how the user gets things
  done*, which is where most of the recurring administrative burden actually lives.
- **Expected benefit.** Reusable operational structure: information sources, decision rules,
  preferred tools, sequence, handoffs, conflict resolution, verification, approval points, expected
  result, recovery strategy. **Explicitly not** replayed mouse coordinates or recorded screen
  actions — a coordinate replay is brittle, unexplainable and unverifiable, and would fail the
  inspectability requirements Bartholomew already imposes on learning.
- **Reconciliation, not new taxonomy.** `COGNITIVE_RUNTIME.md`'s "Memory semantics this implies"
  already establishes open-ended `kind`s carrying provenance and confidence, and S5.1 already
  defines competencies. Workflow memory is a **kind within that substrate**, alongside knowledge,
  preference, episodic, competency and policy/boundary content — not a new store, schema authority
  or retrieval path. The reconciled list lives in `COGNITIVE_RUNTIME.md`; this entry does not
  restate it.
- **Dependencies.** C06 (workflows worth keeping mostly arrive as corrections); the existing
  consent/provenance machinery; retrieval.
- **Governance / safety.** A learned workflow encodes *authority* ("wants approval before step C"),
  so a wrong or stale workflow is a governance risk, not just a quality one. Must be inspectable,
  amendable, revocable and versioned; must never itself grant authority.
- **Layer.** Memory.
- **Promotion evidence (E1).** Real repeated tasks where a captured workflow demonstrably reduced
  user corrections or user-performed steps on a later run.
- **Deferral / rejection evidence.** Captured workflows too brittle or too situation-specific to
  reuse; users unwilling to accept them once shown.

### W04-C06 — Generalising a correction beyond the record it superseded

- **Not the base loop — that is Wave 3's.** `docs/waves/W03/W03_D_CONTRACT.md` already owns
  `experience -> correction -> candidate lesson -> governance -> memory/competency`, with
  first-class supersession and a Wave 3 exit criterion that an approved correction changes a later
  turn. **That loop is active Wave 3 scope and this candidate does not restate, defer or
  re-litigate it.** `COGNITIVE_RUNTIME.md`'s correction → lesson lifecycle describes W03-D's loop,
  not a candidate.
- **The genuinely post-W03 problem.** W03-D makes a correction change *the record it superseded*.
  It does not make a correction change *situations it never saw*. A user who corrects the same
  judgement in five neighbouring contexts still pays five corrections, because nothing proposes
  "this looks like the same lesson" across them.
- **Expected benefit.** A validated lesson that generalises across situations — one correction
  improving future behaviour in cases structurally like it, rather than only where it was made.
  Two further extensions W03-D does not cover: **retirement** of an already-promoted lesson
  (including learning derived from it), and **promotion thresholds** for that generalisation step.
- **Dependencies.** W03-D complete and its real-world evidence in hand — this candidate is
  unassessable before then; C05 (a generalised lesson needs the operational structure to attach
  to); the personal / potentially-generalisable / system-level classification; recovery/undo.
- **Governance / safety.** Generalisation is the step where learning stops being auditable if
  done carelessly: a lesson applied to a situation nobody corrected must carry the provenance of
  the correction it came from, and must be rejectable there without unpicking the original.
  Manual acceptance remains authoritative (`W03_DEFERRALS.md` #4); nothing here proposes automatic
  acceptance, and the shadow-only policy stays shadow-only. Retiring a promoted lesson must be as
  available and auditable as promoting it was.
- **Layer.** Reflection + Memory + Governance — as an extension of W03-D's machinery, never a
  second pipeline beside it.
- **Promotion evidence (E1).** Wave 3 evidence that corrections *recur across similar situations*
  (without which there is nothing to generalise), plus a demonstrated clean retirement of a
  promoted lesson.
- **Deferral / rejection evidence.** W03-D's loop proves sufficient in real use — corrections do
  not recur across contexts, in which case this candidate is unnecessary and should be rejected;
  or generalisation cannot be made reversible cleanly.

### W04-C07 — Learning-quality and burden metrics

- **Problem.** `DECISIONS.md`'s Burden Reduction Principle is explicit that **ABR remains
  conceptual until burden measurement is defined**, and Band C requires burden instrumentation.
  Without measures, "reduces burden" is unfalsifiable and every candidate above is unpromotable.
- **Candidate measures.** Repeated-task completion time ↓; user corrections per workflow ↓;
  unnecessary intervention frequency ↓; prepared-work acceptance ↑; successful recovery rate ↑;
  repeated recovery incidents ↓; successful unattended delegated work ↑; depth of tasks willingly
  delegated ↑; **user-performed steps per successful outcome ↓**; prompt burden ↓; manual
  context-restatement ↓; false-positive proactive interventions ↓.
- **The important one.** *User-performed steps per successful outcome* is the measure that
  distinguishes reducing burden from **moving** it. A slice that adds capability while raising this
  number is a regression under the Burden Reduction Principle even with every test green.
- **Dependencies.** None structural; it is instrumentation of paths that already exist.
- **Governance / safety.** Measurement must not become surveillance: burden metrics are personal
  data under the same consent, retention and export rules as anything else, and must be
  inspectable and exportable by the user.
- **Layer.** Metrics / product measurement — extending the existing product-gate and acceptance
  criteria, **not** a separate KPI system.
- **Promotion evidence (E1).** A small set of these that can be computed honestly from real use
  without new capture. Promotion of this candidate is a **prerequisite for honestly promoting
  C01, C03, C04, C05, C06 and C11**, since each of their thresholds is stated in these terms.
- **Deferral / rejection evidence.** A metric that cannot be computed without capture the gates
  forbid is dropped rather than approximated.

### W04-C08 — Mobile task / approval / status surface

- **Problem.** Approvals and exception handling are the moments delegation actually needs a human,
  and they are exactly the moments the user is away from the PC.
- **Expected benefit.** A surface showing task status, progress, blockers, approval requests,
  exceptions, completion and outcome verification — e.g. *Working — 6/9 complete* / *Waiting for
  approval — send revised document* / *Blocked — supplier portal requires authentication* /
  *Completed — no further action required*.
- **Dependencies.** C04 (there is nothing to show without durable delegated jobs); Stage 6
  authentication and threat model; the device-capability protocol — all **FUTURE PLATFORM WORK**
  per `ROADMAP.md`.
- **Governance / safety.** An approval surface is an *authority* surface: it cannot ship before
  authentication and per-device trust exist. Approving a consequential act from a phone must carry
  the same authority checks as approving it anywhere else.
- **Layer.** Client / companion.
- **Promotion evidence (E1).** C04 promoted and real delegated work blocking on approvals; Stage 6
  auth gate passed.
- **Deferral / rejection evidence.** Auth/threat-model gate unmet — an automatic deferral.

### W04-C09 — Local companion perception + privacy filtering

- **Problem.** High-frequency perception (screen and local UI state) is expensive and privacy-hot
  to ship wholesale to a server brain.
- **Expected benefit.** Filtering and structuring at the edge: less raw personal content leaves the
  device, and what does is structured evidence rather than pixels.
- **Dependencies.** The W03 companion perception work; consent state; the device-capability model.
- **Governance / safety.** Edge filtering must not become an ungoverned second observation
  authority, and must not be a route by which capture starts without an explicit human answer
  (`W03_DEFERRALS.md` #6). Bystander and jurisdiction rules apply unchanged.
- **Layer.** Observation (edge).
- **Promotion evidence (E1).** Measured reduction in personal content leaving the device with no
  loss of decision quality on real tasks.
- **Deferral / rejection evidence.** Filtering that discards what the brain needed; any weakening
  of the consent gate to make it work.

### W04-C10 — Inspectable "what Bartholomew has learned" surface

- **This extends a surface that already exists.** The **Learning and Memory Control Centre**
  (Package D, `docs/D_LEARNING_MEMORY_CONTROL_CENTRE.md`; the `#/learning` view in
  `bartholomew_api_bridge_v0_1/ui/minimal/index.html`) already ships candidate inspection,
  provenance, shadow-policy preview, competency correction and revocation, and export — see
  `DECISIONS.md`'s "The learning policy is built in full and shipped structurally unable to
  accept". **This candidate is not that surface and must not be read as proposing it from zero.**
- **The remaining gap.** That surface shows what Bartholomew has learned in the kinds that exist
  today. It has nothing to show for the content this register's other candidates would create:
  learned workflows (C05), generalised lessons and what Bartholomew wants permission to
  generalise (C06), and the low-confidence assumptions behind prepared work (C01).
- **Expected benefit.** The existing Control Centre extended to cover those, so that new classes
  of learned behaviour arrive already inspectable, amendable and revocable rather than acquiring
  governance later.
- **Scope note.** This records the product/architecture requirement. **No UI is designed here**,
  and the existing view is the thing to extend, not to replace.
- **Dependencies.** C05, C06 — this candidate is empty without them; the existing Control Centre,
  memory-agency and provenance machinery.
- **Governance / safety.** Revocation must be real: retiring a competency must actually change
  behaviour and be auditable, not merely hide a row.
- **Layer.** Experience + Memory agency.
- **Promotion evidence (E1).** C05 or C06 promoted and producing content the existing Control
  Centre cannot show, plus a real revocation of that new content demonstrably changing behaviour.
- **Deferral / rejection evidence.** No new learned-content class materialises — in which case the
  shipped Control Centre is already sufficient and this candidate is unnecessary.

### W04-C11 — Governed proactivity (Proactivity Eligibility Model)

- **Problem.** "Be proactive" without a model degenerates into notification spam, which
  `CONSTITUTION.md` already classes as a trust failure.
- **Direction.** Eligibility considers: probability assistance is actually needed; confidence in
  context; expected usefulness; reversibility; authorization; consequence; interruption cost;
  urgency; user preference; operational confidence. Conceptually *need × confidence × usefulness ×
  reversibility × permission − interruption cost × consequence/risk* — **recorded as an intuition
  pump, deliberately not frozen as a formula**; no weights, no thresholds and no scoring function
  are approved by this entry.
- **The behavioural point.** Prefer preparing useful work silently to emitting a notification. Bad:
  *"You have a meeting tomorrow."* Better: location, travel time, calendar context and conflicts
  already checked, and only the issue or decision that genuinely needs the user is surfaced.
- **Dependencies.** C01 (silent preparation is what makes silence useful rather than negligent);
  C07; the existing triage/interruption decision (`DECISIONS.md`: internal triage does not justify
  interruption).
- **Governance / safety.** Proactivity is **subordinate** to identity, consequence, recoverability
  and the Parking Brake. The maturity progression *tell me what to do → I understand what you're
  doing → I can suggest what would help → I've prepared it for you → I can handle this if you
  approve → you previously authorized me to handle this → I handled it and will interrupt you only
  when something genuinely requires your attention* describes **product maturity, not an entitlement
  ladder**: no stage is reached by elapsed time or accumulated success alone, and none of it
  overrides `CONSTITUTION.md`'s "autonomy is earned, not enabled" or the Baby Mode ladder.
- **Layer.** Executive + Governance.
- **Promotion evidence (E1).** Real evidence that proactive surfacing is welcome — false-positive
  interventions low and falling, unnecessary interventions falling, no rise in ignored
  notifications.
- **Deferral / rejection evidence.** Any measured increase in message blindness; users disabling it.

### W04-C12 — Sanitized transferable competencies (cross-user learning)

- **Problem.** Capability that one household member's Bartholomew learned is useful to another's;
  private experience is not.
- **Direction (already Bartholomew's).** *personal experience → sanitized lesson → generalized
  skill candidate → validation → explicit sharing authorization → recipient-local adoption.* The
  principle it serves: **separate private experience from transferable capability.** Private
  memories do not become shared memory.
- **Reconciliation.** This restates, and does not extend, `DECISIONS.md`'s "Trusted-group sharing
  crosses tenants only as a sanitized typed package, and lands as a candidate" and
  `CONSTITUTION.md`'s "Personal learning does not become platform knowledge". The Violoop research
  is corroboration for an existing position; it authorises nothing further, and **no active
  implementation scope is expanded**.
- **Dependencies.** C05, C06; trusted-group/device-trust work; sanitization and de-identification.
- **Governance / safety.** No hive mind. Sharing is explicit, per-package, revocable and auditable;
  the recipient adopts a **candidate**, never a fact. Sanitization failure is a privacy incident.
- **Layer.** Memory + Governance.
- **Promotion evidence (E1).** A genuine trusted-group need in real use, plus sanitization shown
  adversarially sound on real content.
- **Deferral / rejection evidence.** Sanitization that cannot be shown sound — an automatic
  rejection, not a risk to accept.

### W04-C13 — Long-term model personalization (research only)

- **Status.** **Research direction only. Not promotable in Wave 4, and deliberately not placed in
  any near-term roadmap scope.**
- **Why deliberately last.** Weight-level behavioural change is materially harder to explain,
  audit, attribute, test, revoke, roll back and compare against previous behaviour — which is the
  entire set of properties Bartholomew's learning governance depends on. The preferred hierarchy is
  **memory → lessons → workflow models → versioned skills/competencies → policy adaptation**, and
  only far beyond that, model-weight personalization.
- **Evidence threshold for even *investigating* promotion.** Every layer above it exhausted and
  demonstrably insufficient in real use; a credible story for attribution, revocation, rollback and
  behavioural diffing; and an independent evaluation showing the benefit is unreachable by the
  cheaper layers. Absent all of that, this stays research.
- **Rejection evidence.** The cheaper layers prove sufficient — the expected outcome.

### W04-C14 — Dedicated local Bartholomew Hub (research only)

- **Status.** **Research/future consideration only. Hardware is not added to near-term
  implementation scope by this register.**
- **Position.** The Personal Executive System must first be proven through **server + companion
  software**. That another product ships hardware is not evidence that Bartholomew needs it; it is
  evidence about that product's go-to-market.
- **Conceivable future value.** Local compute; microphones; household/device connectivity;
  always-on presence; physical safety controls (a stop that does not depend on software goodwill is
  the most interesting of these, and connects to the independent-emergency-shutdown invariant).
- **Evidence threshold.** Real, repeated capability the software-only architecture demonstrably
  cannot deliver — not convenience, and not a claim from a vendor selling a device.
- **Rejection/deferral evidence.** Software-only proves sufficient; or the household/ambient gates
  (Band B) remain unmet, which they are.

---

## Explicitly *not* candidates

Recorded so a later session does not rediscover them as ideas:

- **Universal approval for every action.** Rejected: it is babysitting, and it is what the staged
  autonomy model already improves on — low-consequence + reversible + authorized + high-confidence
  may act; uncertain or higher-consequence asks; irreversible or critical requires explicit
  authority; Parking Brake means no governed execution at all.
- **Visual screen state as the sole source of truth.** Rejected: screen perception is one
  observation source; structured evidence is preferred wherever it exists.
- **"API-free" or "vision-only" as product virtues.** Rejected: hybrid execution beats ideological
  purity. Use whichever governed mechanism produces the safest, quickest, most deterministic and
  most verifiable result.
- **Copying Violoop's product scope.** Violoop is a useful reference for Bartholomew's **PC
  presence, actuation and delegation layer**. It is not a blueprint for Bartholomew, whose target
  is an executive intelligence operating across the user's life — PC, phone, communications, files,
  calendar, web/services, household, cameras, voice, speakers, relationships, personal memory,
  physical environment and multiple devices.
