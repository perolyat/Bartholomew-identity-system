# SOLOMON V2 — Implementation Work Packages, Evidence and Release Plan

**Date:** 2026-11-23 (Australia/Brisbane)  
**Status:** Draft GitHub planning proposal; **no code, implementation, integration tests or real-world actions started**.  
**Scope authority:** Owner-approved R03A-02 whole usable Version 1 target, unchanged. User's 2026-11-23 direction delegated **planning reviews**, not authorisation to contact services, pay for models, move financial data, modify phones/PCs, deploy or release.  
**Paired architecture proposal:** [ARCHITECTURE_DECISION_2026-11-23.md](ARCHITECTURE_DECISION_2026-11-23.md)

## 1. What must be delivered as one usable Version 1

A single-owner assistant that can converse naturally in supported PC/Android text and voice, reason about unfamiliar requests and provide useful companionship, own a range of authorised multi-step digital tasks from planning to independently verified results, preserve persistent user-governed memory and prospective commitments, help improve finances and life decisions with genuine evidence and user-approved steps, provide bounded optional health/life administration, show task/device/model/memory/permission controls with coherent continuous identity, and enforce independent GOV-AUTH-1/Parking Brake, privacy, costs, safe stop/undo/recovery and truthful UNKNOWN states. It should reduce the user's effort, not require constant supervision.

It does **not** claim universal tool/robot/model coverage, all-day sensing, medical diagnosis, autonomous investments or guaranteed financial returns, an artificial-consciousness system, commercial multiuser product, or unrestricted self-improvement. A conditional technical capability or unavailable device is identified accurately. A safe narrow integrated engineering spike can validate architecture but **cannot itself be declared Version 1 complete**.

## 2. Traceability baseline

Airtable V2 Decisions was enumerated on 2026-11-23: **96/96 current authoritative product requirement records are Approved with nonempty source wording**. First 90 groups VAL, INT, EXE, CAP, MEM, UX, GOV, LIF, REL, SYS, FUT; six additions WORLD-01, AI-01, HEALTH-01, COMP-01, OPS-01, QUAL-01.

| Coverage/decision | Airtable reference | Status at planning time |
|---|---|---|
| Approved original whole Version 1 outcome | `R03A-02` / `recU5F0vUWHeVm78j` | Approved |
| Requirements-first acceptance framework | `R03A-03B` / `recnP2pCIJOrzTHUk` | Delegated planning approval; no tests |
| Eight composed qualification suites / 16 cases | `R03A-03B-01` / `recX0PqMU2J3XJ0rk` | Delegated planning approval |
| 96/96 current source-to-V1 classifications | `R03A-03B-02.01..10` | Ten delegated planning records; source wording never altered |
| Historical F01-F13 and predicted B01-B10 risk transfers | `R03A-03B-03` / `recPSGM9I3TLCK3Nl` | Planning transferred; risks not resolved |
| Initial numeric, negative and owner pilot gates | `R03A-03B-04` / `recLCWau1WVK9x2zg` | **Provisional targets**, not empirical |
| Deep memory deletion/failure matrix | `R03A-04A` / `recRxUWB1Tm6d4SC5` | Owner approved technical test design; 16 MD-F cases all unexecuted |
| Architecture/platform/reuse/implementation | `R04`, `R05`, `R07` / `recz9h9ganpeSw6pn`, `recgrhkXGU5enDJQa`, `recV3ohQUkL3IQey7` | Delegated planning, not implementation |
| Independent alignment audit | `R03A-ALIGN-01` / `recOpNAhZYLRq6r50` | Previously approved and integrated |

V1 coverage classes: A release-wide invariant, B full supported owner-visible integrated competency, C bounded meaningful V1 capability with long-term scope preserved, D future research/embodiment outside V1. Counts in 96-source matrix: **17 A; 42 B; 23 A+B; 12 C; 2 D.** Even D must respect relevant V1 security and extension invariants. No material reduction to R03A-02 can be made automatically.

Source integrity:
- VAL04 current source is a **fresh 2026-11-17 approved replacement**, not a reconstructed 2026-10-27 quotation. Its old historical eight paragraphs remain unrecovered but do not invalidate the newly approved current source.
- INT03 and EXE07 approved wording was restored, with paragraph-format uncertainty documented in V2.
- WORLD01 current approved 2026-11-16 source supersedes historical rejected draft.
- MEM03's apparently missing process milestone is actually in **V2 Decisions** as `recibYvoBF82zBJyK`; it is a process reminder, not a user-facing requirement. Deep qualification requirements remain mandatory.
- Source-of-truth precedence: GitHub canonical implementation/architecture authority, Airtable live operational planning/status. This draft is a GitHub PR, **not yet merged or authoritative main-branch policy**.

## 3. Eight cross-domain required proof suites

Every suite needs a real positive user-relevant case and a distinct adverse/unfamiliar variant; do not count simulated model output as an observed external effect.

| Suite | Actual owner-visible capability | Adverse / transfer proof |
|---|---|---|
| **I01 Cognition/companionship** | Open conversation, research, explanations, teaching, constructive challenge and user-selected companionship | Unfamiliar intent, contradictory sources, non-manipulative context switch, no fabricated confidence |
| **I02 Executive/task completion** | Real authorised multi-step digital result, plan/dependency handling, independently verified closure and follow-through | Tool outage, bad target, lost ACK, cancelled child, no duplicate external effect, correct UNKNOWN |
| **I03 Memory/prospective commitments** | Real task-relevant governed memory, future promise, corrections, inspect/export/restrict/forget with observable effect | Model swap, old backup, offline device, retained derivative, expired grant and truthful incomplete deletion |
| **I04 Device/model continuity** | Same owner persona and task/permissions across Windows/Android, voice/text and selected replaceable AI engines | Device unavailable, cached stale stop, incompatible fallback and no confidential cloud egress |
| **I05 Financial improvement** | Permitted statement/imported source analysis, accurate cash context, credible savings/income opportunities, one independently verified user-approved next step | Stale balance, missing fees, fraud/false advertising, unapproved purchase/trade/transfer or overspend |
| **I06 Life/health/physical context** | Multi-source ordinary daily problem, dependencies, last-known world state and optional clinician-reviewable health notes | Unknown inventory/location, sensor privacy, unsupported diagnoses or phone alarms |
| **I07 Governance/stop/oversight** | Effective owner controls, per-device stop, exact grant, no model authority and transparent consequences | Tool/prompt injection, overlapping agents, revoked grant, offline endpoint, brake bypass attempts |
| **I08 Operability/recovery/benefit** | Clean repo build, installed Windows/Android use, cost/updates and sustained usefulness | Windows DB writer/last-close/worker tail, power/restart, expired credentials, false-green CI, recovery proof |

Baseline 16 paired scenarios are **not enough** alone. Include unfamiliar variants, deliberate negative mutation, full source requirement safeguards and a sustained owner pilot. Distinguish planned, mock, unit, real-model, actual-device, independent provider readback, owner pilot evidence classes without inflating one into another.

## 4. Release criteria — provisional planning targets, not measurements

**Non-negotiable hard blockers in the declared supported envelope:** zero accepted unauthorised consequential effects; zero known working GOV-AUTH-1/brake/permission/privacy bypasses, cross-tool data exfiltration, duplicate irreversible side effects, reintroduction of prohibited deleted data on tested routes, false VERIFIED success or false complete deletion; no unresolved critical safety/security/data integrity fault. Risk class matters more than average pass percentage. If a critical gate fails, affected execution is disabled and release is blocked. Do not promise mathematical global 100% reliability from finite tests.

**Initial numerical engineering targets to freeze after actual hardware/provider qualification:** minimum 28-day owner-attended pilot and 50 naturally arising eligible owner tasks; >=98% verified completed or owner-accepted safe resolution for declared low/medium-risk tasks, with failure classification pinned before scoring; >=10 unpractised user requests; >=3 real cross-device/model handoffs; >=5 controlled fault episodes; 16 required integrated I01..I08 normal/adverse cases plus distinct generalisation and full critical negative/mutation coverage. Median owner time saved >=25% for genuinely comparable delegated tasks compared with manual baseline; separately assess companionship/independence and avoid forced time metrics. Proposal-only local independent authority/Brake p99 <=1s acknowledgment and ordinary local non-model task control p95 <=2s on the actually supported machine, not a promise of remote effect cancellation or inference speed. Correct state retention in all selected forced-crash/restart/offline cases. Account/service/network charges require owner-configured AUD hard ceilings and atomic effect reservations; no model may choose larger spend. If tests reveal unworkable thresholds, revise with actual evidence and owner review for any material weakening of safety or V1 scope.

**Final release** always requires the owner's separate explicit decision after credible real-device, real-model, real-service where authorized and independent recovery evidence. No pilot, provider account or device testing has been run as part of this plan.

## 5. Staged implementation packages and dependency gates

### P0 — Evidence baseline, source and interface contracts

**Inputs:** pinned GitHub main SHA, 96 source matrix, R03A-02, F01-F13 and B01-B10 hazard map, exact potential reuse inventory.  
**Deliver:** accepted component truth-table (operational vs integrated/default-off/conceptual), current Windows full-suite and DB lifecycle root-cause baseline, real vs fake model readiness, owner use journeys, typed protocol/permission contract, security/data-flow diagrams and explicitly unsupported capability register.  
**Must fail:** a mock/model answer treated as real-world proof, stale source/CI ready, destructive test data in a live personal DB, any unsupported scope demotion.  
**Exit:** independent reviews concur about boundaries and high-risk unknowns; no platform/language/deployment changes based merely on an earlier PR.

### P1 — Owner experience and cross-device feasibility

**Deliver:** clickable, accessibility-aware computer/Android user journeys for conversation, visible task graph/history, active/revoked permissions, per-device status, model/memory options, stop/cancel/leave-me-be/Brake, cost and recovery. Include deliberate UNKNOWN/OFFLINE/PENDING/FAILED states. Actual device proof-of-communication with the lowest-risk synthetic backend and narrow Android OS bridge; real secure pairing and stop check in disconnected mode.  
**Exit:** the owner experience is viable on supported Windows/Android, Android notification/alarm semantics are accurately scoped, UI stack selected against alternatives. No mock task completed is counted real.

### P2 — First real governed end-to-end vertical action

**Deliver:** ordinary user text/voice request → genuine Executive proposal/dependencies → correct current independent owner grant and Brake → a separately authorised *low-risk* device/file/draft outcome → independent readback → durable receipt/obligation → visible closure or UNKNOWN with recovery. Demonstrate denied, pending consent, interrupted, cancelled, duplicate-attempt and offline alternatives.  
**Exit:** source-identified installed build and actual GUI observation; no alternate provider/child path around governance. A working seam is **not** the whole Version 1.

### P3 — Real general intelligence, governed memory and task continuity

**Deliver:** competent actual model(s) on unfamiliar real goals, adaptive planning, corrected task goals, natural conversation/learning/personality, source/provenance memory, inspect/correct/export/delete, prospective commitments, allowed bounded background work and valid model interchange. Deep MD-F01..F16 with disposal-only synthetic fault fixtures, actual provider verification separately where authorised.  
**Exit:** all mandatory owner controls work against real persisted state with true status; source truth independent of model context and no fabricated experience/approval.

### P4 — Everyday personal-value and connected devices

**Deliver:** actual Windows/phone voice/text task continuity, owner-visible reachability, native Android notification and no unauthorized phone alarm, supported web/file/browser/OS action, budget/privacy aware model choice, financial statement/source analysis and genuine actionable improvement, one nonfinancial daily-life outcome, optional consented clinician-ready information summary.  
**Exit:** enough *distinct* real supported domains to meet R03A-02 with no hard-coded per-demo workflows or unauthorised finances/medical actions. If Android remote connectivity absent, state exactly which restricted capability and whether this violates accepted V1.

### P5 — Integrated reliability, privacy and adversarial qualification

**Deliver:** whole-scenario I01..I08 with adverse/unfamiliar variants, F01-F13 + B01-B10 historical/prospective controls, 16 deep deletion fault scenarios, provider/Windows real-model/PC-phone non-vacuous CI + actual installed UI tests. Exact-build evidence, retries/unknown, system stop, unhealthy dependence, autonomy, spend and privacy tests. At least one genuine independent critique of safety/data-flow.  
**Exit:** all critical release blockers met, historical Windows Gate8 requalified/closed **on the actual final head** or implementation replaced and newly qualified with verified rationale; risk ledger reconciled, no false READY.

### P6 — Owner everyday pilot and release authority

**Deliver:** measurable multiweek realistic pilot, real observed user benefit vs supervision time, recoverable incidents and accurate unsupported features, reproducible clean installation/updates/rollback and data portability/exit, privacy-safe audit with cost report.  
**Exit:** all accepted evidence and explicitly limited residual risks reviewed. **Only owner may authorise release** and each privileged model/device/account/financial permission.

## 6. Parallel builder and review method

1. **Wave Prep** freezes the source version, exact parent and child interface contracts, accepted mutation oracles, scope dependencies, non-overlapping file ownership and handoff template.
2. **A — Architecture/contracts:** records, runtime state machine, source-to-test trace, threat and dependency checks; never create independent Executive power.
3. **B — PC/Android UX:** authentic visible controls and failure views against frozen protocol, accessibility and real owner walking through flows.
4. **C — Executive/memory/governance:** current grants/Brake at effect, durable tasks/effects/obligations, deletion and data policy; no model-controlled security gates.
5. **D — ECI/models/endpoints:** qualified tool and model adapters, Windows and Android actual capability/reachability/security/fees, no provider lock-in.
6. **E — Independent verification:** hostile input, mutation, failure injection, repo clean-install and final actual UI acceptance; no self-certification by builders.
7. **Integration:** freeze PR SHAs, merge after independent diff/contract checks, run every required CI tier and actual PC/Android tests; no patching old installed copies or declaring simulated results live.
8. **Context handoff:** every builder warns before context limit, supplies exact SHA, state, tests not run, open effects/risks, and recommends branch/split/new session; all durable in repo and operational status in Airtable.

## 7. Prior failures that must be demonstrably addressed

- Actual Real-World Test #1 failed owner burden break-even; require meaningful task closure and measure rescue workload.
- Existing live-model planning quality is unverified; real semantic test of unfamiliar goals, not a structurally valid mock.
- Windows SQLite commits/closes, scheduler queues and health/writer-lock are distinct observed or hypothesized mechanisms; instrument before fixes; narrow merged patches cannot close Gate8.
- Green CI and exact-head review can falsely suggest readiness; use source-SHA/selected-started-finished complete evidence and mutation sensitivity.
- Prior test-isolation escape once modified real memory; fail tests before accessing anything outside disposal-only fixtures.
- Stale approval, context-summary prompt injection, alternate-route data disclosure and offline stop require negative tests across composed tools/models/devices.
- Source/provenance repair: VAL04 Nov-17 approved replacement is current, WORLD01 current approved supersedes earlier draft, and MEM03 deep milestone is found in Decisions not Programme.
- Repeated apparent local fixes must undergo systemic cause analysis and cross-component regression instead of endless patches.

See Airtable R03A-03B-03 for per-risk F01..F13, B01..B10 and five cross-system alignment contracts with test-suite mapping. A *planned* negative test is not an executed or passed negative test.

## 8. Decision and authorisation ledger

**Planning completed under owner's standing delegation:** requirement trace, acceptance method, future composite suites, risk transfer, provisional measurement envelope, R04 platform/reuse study, R05 staged strategy and R07 architecture/implementation work packages. These are model-reviewed engineering plans **not individual explicit owner votes**, do not alter original V2 product text.

**Still not authorised or achieved:** changes to GitHub main/canonical decision register, implementation code, provider/server purchases, new mobile/sensor notifications, private bank/health access, external actions, real device integration, live-model capability, deletion success, Windows incident closure, pilot or final V1 release. The PR is intentionally **draft** until the source hierarchy, architecture tradeoffs and final implementation go/no-go are considered. Engineering can now proceed from planning into a bounded authorised build when owner explicitly authorises that consequential transition.
