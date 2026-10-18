# V2 Systems Engineering — Cross-Disciplinary Principle Translation

> **Status:** Methodological direction explicitly approved by Taylor on 2026-10-18; this document is a documentation-only proposal pending the normal GitHub review/merge gate. It is **not** an approved V2 architecture, a mandate to implement the examples below, or authorisation to modify Bartholomew.  
> **Programme owner:** R03A — Top-down requirements decomposition and dependency blueprint.  
> **Related work:** R01 (evidence/lessons), R02 (product outcomes/scenarios), R03 (architecture/failure model), R04 (reuse/platform strategy), R06 (engineering/verification method), R07 (foundation approval gate).  
> **Project authority:** Existing CONSTITUTION.md, DECISIONS.md, START_HERE.md source hierarchy and User Approval Gate remain in force. GitHub main is the durable technical authority; Airtable is the planning and traceability index.

## 1. Intent

Use valuable, evidence-supported principles from robotics **and other engineering disciplines** to make Bartholomew more capable, reliable, adaptable, safe and useful. Abstract the underlying engineering mechanism away from its original domain **before** expressing it as a Bartholomew product need, general reusable capability, design constraint or test.

Do **not** copy a robotic implementation, create a robotics-specific subsystem, or treat an evocative analogy as proof that the same architecture will work in software. The end goal is the user's life improving with less supervision and burden, not engineering complexity for its own sake.

Relevant source domains can include robotics, aviation/spaceflight, control theory, distributed systems, industrial safety, biology/neuroscience, human factors, reliability engineering and security. This is an open set, not a requirement to research them all.

## 2. Binding method / stage gates

For each promising external principle:

1. **Capture a source.** Record title/type, author/organisation if known, publication or observation date, source URL/file reference where available, claim, and why it might matter. Separate video narration/marketing, demonstration, peer-reviewed or measured evidence, and personal interpretation. Do not fabricate a citation, a result or a source.
2. **Extract the mechanism.** State in domain-neutral language *which problem the source solves, how feedback/state/resources/constraints are handled, and under what conditions the principle works*. Note assumptions, limitations, competing explanations and confidence.
3. **Translate to a Bartholomew outcome.** Link to a real user scenario or product objective, including the practical problem and consequences of failure. Explain the transfer: source-domain mechanism -> abstract invariant -> relevant software behaviour. If no concrete value is established, retain as research reference, not a requirement.
4. **Check existing design/requirements first.** Search R02 scenarios, R03A requirements, R03 architecture, approved governance, V1 lessons and reusable capability definitions. Classify as (a) already covered, (b) stronger test/evidence for an existing requirement, (c) meaningful extension, (d) genuinely new capability, or (e) not applicable. Prefer enriching an existing reusable contract to inventing a narrow new subsystem.
5. **Form a candidate, not a decision.** Describe observable behaviour, owner/responsibility boundary, dependencies, external interfaces, freshness/uncertainty constraints, normal and compound failure modes, authority/privacy/security limits, portability/operating cost, and acceptance/negative tests. Keep product requirement separate from any proposed architecture or vendor implementation.
6. **Compare solutions.** Examine reuse/integration, adaptation of existing components, simple native designs and custom build. Record complexity, maintainability, failure isolation, reversibility, cost and net burden on the user. A sophisticated principle is not evidence that a sophisticated implementation is necessary.
7. **Validate before promotion.** Assess provenance, strength of evidence, applicability, benefit, unintended effects, duplicate coverage and testability. Review with the user whenever it changes requirements, constraints or major direction. Architectural decisions and implementation remain independently gated.
8. **Trace and close.** For accepted requirements, assign stable IDs and link the origin -> outcome -> requirement -> dependency/interface -> design choice -> work package -> verification method -> independent acceptance evidence. Record actual results and unresolved uncertainties; a persuasive demonstration does not count as tested Bartholomew capability.

Use this flow during appropriate research/design reviews and scenario decomposition; it must **not** become a mandatory lengthy research ritual for every routine engineering task.

## 3. Minimum record schema (for R03A implementation of the shared tracking graph)

Until the linked R03A schema is approved, Airtable programme entries may carry these details as structured text; do not silently create a competing permanent schema:

- **Source ID and provenance** — external reference, observation date, demonstrator versus validated real-world use, exact claim.
- **Extracted principle** — domain-neutral mechanism; evidence class/confidence; assumptions and known limitations.
- **Bartholomew link** — user outcome/scenario, current problem, reusable target capability and relevant responsibility.
- **Overlap and classification** — existing requirement IDs/controls; covered, extension, new, contradicted, parked or rejected.
- **Candidate specification** — observable behaviour, normal/failure scenarios, independent success evidence, risks, guardrails, explicit unknowns and solution options.
- **Graph links** — parent/outcome, cross-cutting constraints, depends-on, interface and downstream consumers; trace through implementation/test/evidence when approved.
- **Decision history** — proposer, reviewer/user decision, date, status and rationale; distinguish method approval from approval of each candidate.
- **Outcome** — demonstrable improvement in task completion, reliability, user supervision/burden, maintenance or other agreed product measures.

Suggested status progression: Research reference -> Extracted principle -> Candidate for requirements review -> Approved requirement / Existing requirement strengthened / Rejected / Parked -> Tested with evidence. This is **not** yet an approved Airtable field/configuration design.

## 4. Worked examples — research hypotheses, NOT adopted architecture

| Originating concept | Domain-neutral principle | Potential Bartholomew translation | Critical qualification |
|---|---|---|---|
| Walking-robot balance correction / feedback control | Observe effect, estimate deviation, correct within safe limits | Closed-loop task execution: plan success evidence before acting; observe the actual result and recover when uncertain | A tool's own success message is not independent verification; do not retry externally consequential actions blindly |
| Robot proprioception | Maintain a measurable representation of internal state, uncertainty and capability | Evidence-based self/operational state: know actual device/service availability, active tasks, permissions and degraded resources | Do not claim consciousness, perfect state knowledge or unobserved facts |
| Bounded stabilisation/reflex control | Fast local response to predictable disturbances within verified envelopes | Bounded fail-safe reactions such as safe suspension/containment of affected work on lost connection | No local reflex may add authority, evade Parking Brake or independently decide user goals |
| Sensor fusion / localisation | Reconcile multiple noisy and stale signals | General world/item/account state, provenance, time/freshness and contradiction resolution across authorised endpoints | Conflicting sources must remain explicit unknowns where unresolvable; respect capture and retention limits |
| Robust walking over uneven ground | Design and test against disturbances, not pristine conditions | Cross-device disconnections, concurrent conflicting tasks, missing permissions, uncertain transactions, partial outages and recovery testing | Tests must prove actual safety, recovery and useful outcomes, not just error-free happy paths |
| Field failure of an impressive demonstration | Operational suitability differs from technical feasibility | Assess net real-world burden removed, noise/interruption, supervision, running cost and maintenance | A demonstration or vendor claim cannot substitute for user-relevant field evidence |

The 2026-10-18 initiating reference was a user-supplied clip about Boston Dynamics BigDog/LS3 and the transition from impressive robotics demonstrations to real-world applicability. Its existence is evidence for the research *prompt*, not proof that particular robotic engineering mechanisms are suitable for this software.

## 5. Review rubric: pass only when all relevant checks are satisfied

- **Evidence:** Is the claimed mechanism actually supported by its source? Are alternative interpretations and missing proof visible?
- **Fit:** Which concrete Bartholomew outcome or failure mode improves? What gets measurably better for Taylor?
- **Generality:** Does the requirement apply across relevant devices, accounts or domains rather than just the motivating example? Are different cases allowed to share one capability?
- **Safety and governance:** Does it preserve independent authority proof, stop controls, consent and privacy, no hidden self-change, and observation-versus-inference separation?
- **Reliability:** Are latency, consistency, partial failure, conflict handling, failure containment, recovery, observability and independent verification covered where relevant?
- **Economics:** Does benefit exceed engineering/operational/user-supervision burden? Is a simpler or existing solution sufficient?
- **Traceability:** Can the requirement and its tests be traced back to a user outcome and credible evidence without orphan dependencies?
- **Authority:** Is this an approved *method*, a proposed requirement, an approved requirement, or a tested implementation? Never merge those states.

An idea failing the rubric should be revised, parked or rejected rather than quietly becoming a requirement. The objective is useful engineering transfer, **not** a permanent accumulation of attractive concepts.

## 6. Fit within the V2 planning process

- **R01:** Keep evidence quality, source reliability and V1 learned constraints visible.
- **R02:** Start with user life scenarios and desired outcomes; outside ideas support outcomes, not dictate them.
- **R03/R03A:** Recursively derive testable requirements and dependency graph; apply cross-cutting resilience/authority invariants at each layer; maintain interface ownership.
- **R04:** Evaluate mature external implementations and general-purpose capabilities before any bespoke subsystem.
- **R06:** Create review/verification checks that expose failure cases and net user burden, without procedural excess.
- **R07:** Ensure novel requirements and architecture choices are consciously approved before implementation.
- **Delivery:** Verify real-world outcomes, not just clean builds, green CI, nominal action execution or source-domain analogies.

**Specific boundaries:** This method does not mandate any robot, embodiment, sensor, extra worker pool, separate 'reflex engine', control-theory algorithm, new AI autonomy tier, additional storage layer or self-modification mechanism. Any such design remains an individually justified and user-approved proposal. Bartholomew V2 is currently scoped to Taylor personally; other people remain relevant for bystander/privacy/authority constraints, not a presumed multi-tenant product.

## 7. Change and approval record

- **2026-10-18 — User approval:** Taylor asked to record the agreed cross-domain transfer approach properly in the V2 systems-engineering methodology. Approved direction: identify valuable engineering lessons (initially robotics, expanded to other disciplines); abstract the reusable principle; map it to general capabilities and requirements; avoid simplistic domain-specific copies and needless subsystem proliferation.
- **2026-10-18 — Planning elaboration:** Steps, record schema, review rubric and worked examples are proposed process details drafted to operationalise the direction. They do not individually approve technical implementations.
- **GitHub promotion:** This documentation-only proposal sits on its own review branch/PR. No code, tests, runtime, live systems, V1 incident statuses or implementation gates are changed. **Do not merge without Taylor's explicit approval.**
