# SOLOMON V2 — Adversarial Architecture Self-Review and Builder Handoff

**Prepared:** 2026-11-23 (Australia/Brisbane)  
**Review type:** A separate **self-challenge of the proposed design**, *not* an independent external audit, empirical test or safety certification. Another reviewer and actual tests are still necessary at the implementation gate.  
**GitHub source baseline:** `main` commit `0142673628ab74fe5991005d146dfbaa97729c90` (2026-10-09).  
**Companion proposals:** [Architecture](ARCHITECTURE_DECISION_2026-11-23.md), [Implementation & acceptance](IMPLEMENTATION_AND_ACCEPTANCE_PLAN_2026-11-23.md).  
**Decision status:** delegated planning only; no implementation, hardware, financial or health effect and no release.

## 1. Red-team question: Could this pass a beautiful demo and still fail the owner?

Yes. Particularly if a polished screen calls a single static model, converts its model output into fake “completed” task events, uses a mock Android connection, suppresses error cases, silently revives cached deleted knowledge, or calls a provider ACK a verified real-world outcome. **No milestone is complete merely because it looks good.** At least one genuinely supported, independent, reproducible goal→grant→act→verify→task/obligation closure path must be available through the actual installed app. Later milestones must generalise across contexts and retain model, phone, memory and privacy boundaries.

## 2. High-risk threat and falsification register

| Risk | Plausible hidden failure | Non-negotiable mitigation / proposed falsifier |
|---|---|---|
| **T01 Split-brain approval** | Phone is offline with a cached valid-looking capability; owner centrally revokes access; phone executes before hearing it | Do not allow open-ended offline consequential execution. Scoped expiring locally enforceable leases, effect limits and local Brake; for revocation-sensitive effects require fresh central proof. Report inability to immediately revoke an unreachable endpoint and quarantine offline queue before replay; never claim server revocation reached it. |
| **T02 Emergency stop illusion** | UI says “stopped” after central request ACK but unreachable Windows/Android worker continues | Distinguish local accepted, target ACK, independently verified per-endpoint quiescence and UNKNOWN, plus irreversible already-sent consequences. Two-tier Brake and stop intent tests across broken connectivity; local user must be able to stop device actions even offline. |
| **T03 Model/proxy as authority** | Cloud model or a tool output forges owner approval, impersonates high-priority status, changes target or claims user intended a payment | Detached cryptographically/session-authenticated grant and exact effect fingerprint at the action gate, current version, owner consent, policy, Brake. Treat all external/model text as evidence only. Mutate routing and prompt order; no successful grant bypass. |
| **T04 Changed target under same approval** | Model modifies task recipient/address while reusing previously granted “send message” or “buy” authority | Grant is bound to canonical intended effect/target/risk/cost, not ambiguous task label; changed target invalidates grant and requires re-consent. |
| **T05 Multi-agent duplicated effects** | Two delegates and recovery worker all retry after ambiguous vendor timeout | Stable intended-effect ID, single effect-owner/outbox reservation, reconciled provider-side effect before any repeat, UNKNOWN cannot imply failed/no charge. |
| **T06 Memory deletion laundering** | Deleted information survives in vector search, old summary, background queue, local telemetry or restored snapshot and comes back as “new inference” | Track source and derivatives; deny current use, per-copy verification, backup generation barriers and synthetic restoration probes. Opaque third-party erasure acknowledged as unverified. MD-F01–F16 future deep suite is mandatory. |
| **T07 False oracle** | Test passes when status card hidden, model self-reports completion or HTTP 200 received | Independent external readback and deliberately broken mutant must fail each oracle; mock/CI/live installed provenance tiers separate. |
| **T08 Windows lifecycle deadlocks** | Scheduler/client/SQLite tests green in limited CI, but full Windows close/commit/worker tail blocks safety or shutdown | Exact-head all-tier Windows, full chosen suite (including skipped markers), bounded resource stress/long tail and independent stop-path latency; prior PR #128 did NOT close full Windows gate 8. |
| **T09 Android platform overclaim** | WebView closes or Android idle policy kills background job; reminder marked delivered or exact alarm without OS and owner permission | Test target Android hardware/OS and OS permission revocation/Doze/notification blocked/offline cases. WorkManager only for its actual guarantees; exact alarms only via compliant OS path and explicit owner grant; scheduled ≠ delivered ≠ acted. |
| **T10 Server availability mismatch** | Core runs on user's Windows PC; phone away from home displays continuously working AI even when PC powered off | Display last-known and offline; qualify approved secure remote access or hosting separately. Never count stale cached messages as live task progress. |
| **T11 Health/finance scope leak** | Generic tool or model fallback sends private health/bank data to commercial service; AI initiates trade/payment while assisting with planning | Data-minimized provider-specific egress proof and independent financial effect permission; local-only fallback if compatible; refuse unsupported action and disclose limits. Owner research consent is never spend/trade/contact authority. |
| **T12 Cost race** | Parallel model calls check budget at the same time and each spends the remaining balance | Atomic cost reservation/charged reconciliation at admission and async dispatch; owner period ceilings, no fallback/model fan-out override. |
| **T13 State/migration corruption** | New runtime migration or test run touches old real data or permits restored backups to revert consent/deletion | Disposable tests only, pre-migration snapshot and restoration drill, correct database path for all components, idempotent schema migration and rollback proof before owner data touched. |
| **T14 Architecture approval drift** | Airtable “Approved” planning record interpreted as permission to rewrite GitHub `main`, launch autonomous cloud agents or spend money | Source hierarchy: repository governs code/canonical architecture, Airtable operational tracker, user grants real consequences. Draft PR is NOT canonical or implementation go/no-go; use exact source/version-bound decision handoff. |
| **T15 Human value lost to optimisation** | Agent meets task-throughput KPI while bothering owner with notifications, lengthy approvals, false companionship or expensive low-value calls | Owner can decline, silence, pause and critique; measure **net** owner burden/time and voluntary utility. No live service continually optimises routine without consent. |
| **T16 Hidden release scope reduction** | One scripted action works but core reasoning, durable multi-device persona, memory agency and financial usefulness deferred as “later” | Trace current 96 source requirements and R03A-02 first-version boundary to full user-visible competency. Material exceptions require owner's explicit decision; P2 vertical thin seam does not qualify complete V1. |

## 3. Architecture decisions that remain CONDITIONAL

**H1: Where the server runs.** Existing canonical architecture is server-centric. A Windows-hosted attended build may be suitable for initial controlled testing but does not guarantee phone access when off. A hosted owner-trusted service needs explicit hosting/security/data/cost authorization. **Do not automatically switch canonical server-centric direction into per-device competing “brains.”**

**H2: Native Android technology.** React web UI + limited Android native bridge is *preferred provisionally*. Actually test Android OS background, microphone, notification, exact alarm, secure storage, stop and Fold layout against Flutter or an alternative. If this cannot deliver a supported permission-safe user experience at sustainable maintenance cost, choose another option under the evidence-based architectural exception process.

**H3: Persistence architecture.** SQLite/WAL single-owner may be enough with bounded transaction ownership; don't preemptively introduce a cloud database. First investigate measured Windows commit/close concurrency and atomic effects. If retainability fails risk gates, document exact contrary evidence and replace the affected storage seam with migration proof.

**H4: Models and integrations.** The current code includes local/cloud model adapter paths; current real capability and data contract must be checked. Real model generalisation, accurate tool use and owner-benefit are not proven by static tests or provider availability. Never select a non-consented provider merely because it exists in a manifest.

**H5: Local stop under offline central control.** A real endpoint can sometimes be disconnected before learning central revocation. Architecture must explicitly distinguish local owner Brake, expiring leases and remote unconfirmed state. Do not promise impossible instantaneous remote stop. When a task requires fresh centrally checked authorization and offline network is unavailable, it must not execute the consequential step.

## 4. Builder handoff: exact entry point, no hidden assumptions

**Do not start with code changes.** First read GitHub `START_HERE.md`, `CONSTITUTION.md`, `DECISIONS.md`, `COGNITIVE_RUNTIME.md`, `INTERFACES.md`, `ROADMAP.md` and these three V2 draft docs, then independently check GitHub `main` HEAD and whether any prior PR has merged since SHA `014267...`.

1. Read owner-approved R03A-02 and current V2 Decisions product sources (96/96; don't reapprove or edit), current R03A-03B whole-system gates and R03A-04A deletion fault standard in Airtable.
2. Read the historical failure and alignment audits. Check the Oct 2026 Windows Gate 8 state, actual source-revision CI, task-effect storage, current Python packaging and no live test-data leaks.
3. Start a bounded, separately authorized **P0 source/contract/threat feasibility package**; freeze exact app/device/platform evidence before choosing final UI and remote host.
4. Write the versioned typed contract and storyboard all owner-visible normal + denied + offline + unknown + cancelled + reversed states for one goal. Build the *real* P2 vertical effect only after independent grant/Brake and privacy tests are ready.
5. Prepare independent adversarial test code/oracles using disposable data before exposing any real owner records. Actual Android/Windows integration and provider expenses need separate approval.
6. When context nears its limit, provide full handoff of exact source SHA, open PR heads, tests **actually run** vs not run, permissions/side effects, blockers, expected next action and whether to branch or split work.
7. Do not merge this PR or mark it implementation-authoritative without the separately controlled GitHub/owner decision. Do not reinterpret standing PLANNING delegation as permission to pay, publish, send, monitor, update a personal phone, rewrite privacy policies or run the system without owner authorization.

## 5. Honest current readiness

| Item | State on 2026-11-23 |
|---|---|
| 96 current product requirement sources | Verified Approved in Airtable |
| Engineering source interpretations and deep MEM03 fault **design** | Approved; tests **not run** |
| Source-to-first-V1 classification / risk transfer / architecture comparison | Completed as delegated planning in Airtable |
| Architecture and implementation-stage documents | In draft GitHub PR branch, not merged or executed |
| Validated Android integration and real model plan quality | **Not proven** |
| Current Windows full reliability / Gate 8 | Historic source reports **open**, must reverify current head |
| Owner-entered finance/health source access, OS alarm, model/cloud spend | **Not granted by these documents** |
| First usable V1, full acceptance tests, owner pilot, release | **Not completed** |
| Ready to seek ONE bounded implementation kickoff after reviewing draft ADR and pre-build safety envelope | **Planning-ready, not implementation-authorized** |

**Outstanding independent reviews / evidence:** a distinct reviewer must evaluate this threat model and its negative tests, legal/OS/API compliance of chosen platforms, true installation/time/cost and real end-to-end task outcomes. This self-review is not a substitute for them.
