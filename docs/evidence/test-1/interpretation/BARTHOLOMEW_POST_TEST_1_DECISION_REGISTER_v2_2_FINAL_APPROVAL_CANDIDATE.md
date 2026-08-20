# BARTHOLOMEW — POST-TEST #1 DECISION REGISTER v2.2
## FINAL APPROVAL CANDIDATE — corrected evidence provenance, D1–D15, TILT reconciliation, and Test #2 gates

**Status:** FINAL APPROVAL CANDIDATE — NOT YET APPROVED  
**Prepared:** 2026-08-20  
**Tested implementation:** commit `854a8da7fd107db33a933c4bdb01bf3fd7eb69bd` (merge of PR #58, whose head branch was `claude/bartholomew-parking-brake-consent`). **The commit hash is authoritative.** At independent review time it was reachable from `claude/bartholomew-test1-review-rfukzi`; the PR head branch no longer existed.  
**Repository-main relationship at review time:** repository `main`/`origin/main` was reported by the independent repository review at `d0c202f7b39f9244417f1954629f64f68dfbb341` (2026-08-15), **25 commits behind** the tested commit, and did **not** contain it. The Test #1 evidence freeze is therefore the exact tested commit above, not a branch name or `main`.

> **Approval rule:** Nothing in this document authorises implementation. Historical Test #1 evidence remains historical evidence. D1–D15, S1–S11, P1–P9, Bands 0/A/B/C/D, and the implementation ordering become project authority only after Taylor explicitly approves this register. After approval, Claude Code must first update canonical project documentation under the existing Approval Gate; implementation begins only after those documentation changes are separately reviewed/approved.

---

# 1. Authority hierarchy

When sources disagree:

1. `CONSTITUTION.md`
2. canonical SSOT documents (`DECISIONS.md`, `COGNITIVE_RUNTIME.md`, `ROADMAP.md`, `RISKS.md`, `MASTER_PLAN.md`, `docs/TILT.md`, etc.)
3. approved implemented subsystem designs
4. direct Test #1 evidence
5. this post-test interpretation
6. independent-review recommendations

No post-test decision may silently narrow a constitutional/canonical invariant.

---

# 2. Canonical constraints carried into v2.2

## C1 — Independent emergency shutdown
Before unattended sensing or consequential device agency, an emergency-stop path must exist outside Bartholomew's ordinary UI/in-process cooperation.

## C2 — Jurisdiction-aware capture and teardown
Real audio/video capture must account for jurisdiction, consent/notice, audio/video differences, retention/deletion/revocation, private/public context, and travel. Stopping capture must never be gated as a new permission-requiring start.

## C3 — Data portability
User data and personal understanding must be exportable/portable.

## C4 — TILT
Real-world testing takes priority once a vertical slice is sufficiently useful, except where unresolved safety, governance, privacy, data-integrity, architectural-validity, or experiment-validity issues block the relevant test.

## C5 — Awaiting Response
`awaiting_response` already has an approved canonical role as a durable obligation queue. Test #1 found a customer-legibility problem, not an undefined architecture concept.

## C6 — `sight` / `voice` Parking Brake migration seam
Canonical risk documentation records split brake authority: those seams must consolidate onto the authoritative Governance path before real `sight`/`voice` capability is enabled.

## C7 — Cross-device authentication/exposure
The unauthenticated local PoC must not be silently extended into remote/cross-device use. A reviewed trust/auth boundary is required first.

---

# 3. Controlled vocabularies

## 3.1 Evidence classes

- **E1 — Confirmed technical defect**
- **E2 — Confirmed operational / data-integrity / security behaviour**
- **E3 — Confirmed human / product finding**
- **E4 — Tester preference / proposed solution**
- **E5 — Future product / architecture direction**
- **E6 — Test limitation / infrastructure / evidence limitation**
- **E7 — Open question / unresolved hypothesis**
- **E8 — Confirmed success / invariant to preserve**

## 3.2 Severity

Severity is risk/impact, not sequencing.

- **S0 — Blocker**
- **S1 — High**
- **S2 — Medium**
- **S3 — Low**
- **N/A — not a defect/severity item**

## 3.3 Status

- **OPEN — MUST FIX**
- **OPEN — MUST INVESTIGATE**
- **OPEN — PRODUCT/DESIGN DECISION**
- **OPEN — DESIGN/UX WORK**
- **OPEN — TEST #2 PRECONDITION**
- **TEST LIMITATION**
- **ACCEPTED EVIDENCE UNCERTAINTY**
- **HYPOTHESIS — UNPROVEN**
- **CLOSED BY APPROVED DECISION**
- **RESOLVED BY IMPLEMENTATION + ACCEPTANCE TEST**

## 3.4 Priority / sequencing band

Priority is **band membership only**:

- **Band 0 — Attended localhost text-only real-use checkpoint**
- **Band A — before further unattended testing**
- **Band B — before real ambient sensing**
- **Band C — before full Real-World Test #2**
- **Band D — safe parallel prototype**
- **Unbanded — no implementation consequence / evidence-only**

---

# 4. Historical item accounting and v1→v2.2 reclassification

The Test #1 handoff contains **38 historical evidence items**.

v2.2 has **40 register rows** because post-test review deliberately changes row granularity without rewriting history:

| Historical item | v2.2 treatment | Row-count effect |
|---|---|---:|
| OP-W001 + OP-W002 | merged as one Test #2 key-lifecycle/configuration row | −1 |
| SEC-F002 *(post-test formalisation from v1; not one of the 38 historical items)* | represented in v2.2 as SEC-F002a echo + SEC-F002b working-context retention | post-test rows |
| UI-SYNC001 | split into observed stale-count defect + derived critical-state safety requirement | +1 |
| B-Q008 | reclassified from “open architecture question” to confirmed comprehension finding against existing canonical purpose | 0 |

Accounting: **38 historical items → 37 historical-derived rows** (OP-W001 + OP-W002 merged into one row; UI-SYNC001's historical half retained as UI-SYNC001a). Plus **3 rows created by post-test formalisation**, marked **[POST-TEST]**: SEC-F002a and SEC-F002b (from v1's single post-test SEC-F002 row) and UI-SYNC001b (a safety requirement derived from UI-SYNC001). **37 + 3 = 40 register rows.**

Rows explicitly created during post-test formalisation are marked **[POST-TEST]**.

---

# 5. Decision-grade evidence register

**Schema note:** D9 remains unchanged. §5C–§5E use compact presentation for some human/product, direction, hypothesis, and limitation rows; that presentation does not waive D9. For E5/E6/E7 rows, non-applicable severity/expected/actual fields are treated as `N/A`; for E1–E3 findings, the full D9 field set remains binding, and any compactly omitted field must be restored from the underlying source record before closure evidence is accepted.

## 5A. Safety, operational, security, and data-integrity

| ID | Class | Source / case / timestamp | Expected / oracle | Actual | Sev. | Band | Status | Closure |
|---|---|---|---|---|---|---|---|---|
| **PT-F001** | E2 | Real-world test record; initial health ~`2026-08-19 19:40 +10`; restart `19:48:21`; ready retest ~`19:48:43` | Supported local startup reaches selected real model without ad-hoc per-session workaround | `localhost` exceeded readiness timeout; process-only `OLLAMA_HOST=http://127.0.0.1:11434` restored ready state | S1 | **C** | OPEN — MUST FIX | Supported fresh start reaches ready through canonical host/config; no manual process override |
| **START-N001** | E3 | scheduler nudge ID 1 ~`2026-08-19 19:40:16 +10` | No formal product oracle | Curiosity prompt appeared immediately after startup | S2 | **C** | OPEN — DESIGN/UX WORK | D2 eligibility rules prevent startup-alone interruption unless justified |
| **OP-W001/002** | E6 | restart stderr, `2026-08-19 19:48:21 +10` | Test #1 permitted dev keys; personal-data/ambient test requires deliberate keys | STANDARD + STRONG used ephemeral dev keys | N/A | **B** | OPEN — TEST #2 PRECONDITION | deliberate provisioning; restart/decrypt; no key leakage; Test #1 ephemeral-key data disposition recorded |
| **OP-W003** | E2 | HPT-001; restart stderr / runtime fallback; first observed during test around `2026-08-19 19:58:52 +10` | retrieval mode/quality should be known and truthful | `sentence_transformers` unavailable; deterministic fallback embedder used | S1 | **C** | OPEN — MUST INVESTIGATE | intended embedder enabled or fallback explicitly approved with measured quality/degraded-state reporting |
| **OP-W004** | E2 | restart stderr; two `Failed to log audit: database is locked` warnings; second associated with interaction period ending `2026-08-19 20:06:37 +10`; exact affected events unknown | required audit must not silently disappear | two audit writes failed; root cause not established | S0 | **A** | OPEN — MUST INVESTIGATE | root cause established or compensating design prevents silent loss; failure injection passes |
| **OP-W005** | E6 | shutdown `2026-08-20 13:36:27–13:36:30 +10`; redirected logs had stopped advancing | closure logging should remain observable | no final Uvicorn lines; process/port/state evidence substituted | N/A | **A** | TEST LIMITATION | Test #2 logger-health + shutdown capture is continuous/truthful |
| **NUDGE-F001** | E2 | Phase A pending IDs 3/4 plus later repeated curiosity records | duplicate unresolved user obligations should not be created absent explicit escalation | equivalent curiosity prompts persisted separately | S0 | **A** | OPEN — MUST FIX | semantic duplicate unresolved user-facing item count = 0 unless distinct escalation |
| **B-F001** | E3 | Phase A checkpoint → first Phase B observation; 49 new nudges (28 curiosity/21 health); `12:58:31` UI 51 (last UI refresh); backend `13:06:02` 52, `13:12:28` 53, `13:33:18` 54; `13:35:43` ID60 health nudge; frozen `~13:36` = 55 | unattended run must not create self-sustaining cleanup | queue-health warnings increased the queue; final 55 pending; linear/cadence-bounded feedback | S0 | **A** | OPEN — MUST FIX | representative unattended test covers at least observed generation pattern/window and shows no recursive/self-sustaining growth |
| **SEC-F002a [POST-TEST]** | E2 | machine synthetic-password chat/working-memory sequence around `2026-08-19 22:20–22:21 +10` | **no formal echo oracle existed** | sensitive-formatted synthetic values were echoed in output | S0 | **B** | OPEN — MUST INVESTIGATE | sensitive-output policy + adversarial tests; no unnecessary later echo |
| **SEC-F002b [POST-TEST]** | E2 | `working-memory(1).json` / working-memory-context capture from same test sequence | governed secure-data rules should define retention path | sensitive-formatted values remained in ordinary working context | S0 | **B** | OPEN — MUST INVESTIGATE | determine governed-write vs bypass path; working-context TTL/redaction; derived-artifact treatment |
| **SEC-H001** | E7 | Phase B Finding 002 side observation `2026-08-20 13:15:05 +10` | N/A | hypothesis that system repeated values because it knew they were fake/test data | N/A | Unbanded | HYPOTHESIS — UNPROVEN | causal investigation only; never use as fact |
| **UI-SYNC001a** | E1 | final live capture `2026-08-20 13:33:17–13:35:34 +10`; browser 51 vs backend/API 54 | nudge-count state follows defined refresh/freshness contract | displayed count lagged backend by 3 | S2 | **C** | OPEN — MUST FIX | queue state meets defined update bound or visibly shows stale timestamp/refresh semantics |
| **UI-SYNC001b [POST-TEST]** | E5 | derived safety requirement from observed stale-state class | critical safety state must be current/truthful | Test #1 proves UI state may become stale generally; does not prove critical brake/sensor state was stale | S0 | **B** | OPEN — PRODUCT/DESIGN DECISION | explicit numeric freshness/fail-safe stale-state rules for brake/sensor/consent/recording |

## 5B. Technical defects and limitations

| ID | Class | Source / case / timestamp | Expected | Actual | Sev. | Band | Status | Closure |
|---|---|---|---|---|---|---|---|---|
| **TECH-F001** | E1 | HPT-003 `2026-08-19 20:02:00 +10`; repeated under global brake HPT-016 `20:23:47` | non-2xx chat failure rendered truthfully | backend 503 had `detail`; UI read `reply` and showed `undefined` | S1 | **C** | OPEN — MUST FIX | every failure path truthful; never undefined/blank/fabricated success; includes voice |
| **PB-F001** | E1 | PB-000, completed `2026-08-19 22:08:23 +10`; revisions 15/16/17 | empty selection rejected/clarified or visibly mapped | empty selection silently engaged `global`; selectors stayed unchecked | S1 | **C** | OPEN — MUST FIX | no silent semantic conversion; user can predict effect |
| **PB-F002** | E1 | exhaustive PB matrix `2026-08-19`; repeated in all 48 cases containing `global` or `skills` | dependent governed panel updates after brake transition | Notifications stayed stale until its own Refresh | S1 | **C** | OPEN — MUST FIX | automatic invalidation/update within defined bound; future critical sensor-state freshness is governed separately by UI-SYNC001b/B |
| **MF-F001** | E1 | FUNC-010, Phase A sweep `2026-08-19 22:16–22:43 +10`; API activations `0.57/0.54/0.51/0.51/0.48` | UI maps API schema accurately | UI showed five drives `0.00`; attention fields mismatched | S1 | **C** | OPEN — MUST FIX | schema/contract test + representative rendering |
| **MF-F002** | E1 | FUNC-011/FUNC-013/FUNC-015; final DB 16 episode rows for 8 source events | one source event → one intended episode | tested affect/goal events duplicated | S1 | **C** | OPEN — MUST FIX | idempotent persistence/retry test |
| **MF-F003** | E1 | FUNC-017 Water; UI panel labelled endpoints not implemented; GET `/api/water/today` 404; legacy table/models remain | out-of-scope/dead control absent or disabled | enabled controls called absent routes and rendered `undefined ml` | S2 | **D** | OPEN — PRODUCT/DESIGN DECISION | D4 removes ordinary UI; residual data/models handled via existing risk/cleanup authority |
| **MF-L001** | E6 | FUNC-011 Affect | exact values if exact admin manipulation required | click-only 104px slider approximate only | S3 | **D** | OPEN — DESIGN/UX WORK | numeric/keyboard input or explicit non-goal |
| **MF-L002** | E6 | FUNC-012 Clear Focus | focused→idle should be testable through supported fixture/flow | DELETE only exercised while already idle | N/A | **D** | TEST LIMITATION | supported test fixture creates focused state without DB fabrication |

## 5C. Human/product/usability

| ID | Class | Source / case / timestamp | Observation | Sev. | Band | Status | Closure |
|---|---|---|---|---|---|---|---|
| **HU-F001** | E3 | Product Test Finding 002; spontaneous first-use ~`2026-08-19 19:57–20:00 +10` | “I don’t even know where to begin.” | S1 | **C** | OPEN — DESIGN/UX WORK | P1 first-use legibility |
| **HU-F002** | E3 | HPT-005/HPT-006 ~`2026-08-19 20:05–20:08 +10` | generic/repetitive boilerplate; irrelevant car context; mirrored question | S1 | **C** | OPEN — MUST FIX | scenario-frozen conversational quality evaluation: relevance, non-repetition, truthful failure |
| **HU-F003** | E3 | HPT-007 `2026-08-19 20:09:28 +10` | `Ack` unclear | S3 | **D** | OPEN — DESIGN/UX WORK | explicit wording or remove manual action |
| **HU-F004** | E4 | HPT-011 `2026-08-19 20:14:46 +10` | prefers one state-dependent Mute/Unmute toggle | S3 | **D** | OPEN — PRODUCT/DESIGN DECISION | customer UX test decides; admin may differ |
| **HU-F005** | E3 | HPT-012 `2026-08-19 20:15:57 +10` | Refresh interpreted as restore-defaults | S3 | **D** | OPEN — DESIGN/UX WORK | label/icon/context comprehension |
| **HU-F006** | E3 | HPT-013 `2026-08-19 20:16:54–20:17:53 +10` | Parking Brake scope checkbox effects unclear | S2 | **C** | OPEN — DESIGN/UX WORK | P1/P4 safety-control comprehension |
| **HU-F007** | E3 | HPT-014 `2026-08-19 20:19:45 +10` | `global` read as select-all | S2 | **C** | OPEN — DESIGN/UX WORK | P1/P4 safety-control comprehension |
| **HU-F008** | E3 | HPT-013 `2026-08-19 20:16:54–20:17:53 +10` | raw revision metadata unexplained | S2 | **C** | OPEN — DESIGN/UX WORK | D3 hides from ordinary UI |
| **B-F002** | E3 | Phase B Finding 002 `2026-08-20 13:15:05 +10` | current functionality “practically useless”; qualitative burden below break-even | S1 | **C** | OPEN — PRODUCT/DESIGN DECISION | P2 is the sole useful-assistance closure gate |
| **B-F003** | E3 | Phase B Finding 003 `2026-08-20 13:19:35 +10`; Top Drives/Attention extension `13:20:36` | Valence/Arousal/Top Drives/Attention not lay-readable | S2 | **C** | OPEN — DESIGN/UX WORK | plain-language ordinary-user presentation |
| **B-F004** | E3 | Phase B Finding 004 `2026-08-20 13:21:36 +10` | expected inspect/edit of stored understanding | S1 | **C** | OPEN — PRODUCT/DESIGN DECISION | D5/P5 |
| **B-F005** | E3 | Phase B Finding 005 `2026-08-20 13:22:25 +10` | Water should not be in ordinary UI | S2 | **D** | OPEN — PRODUCT/DESIGN DECISION | D4 + canonical risk-entry reconciliation |
| **B-Q008** | E3 **reclassified comprehension finding** | `PHASE-B-OPEN-QUESTION-008.md` `2026-08-20 13:26:20 +10`; canonical S1.4 design already defines purpose | user could not tell what Awaiting Response was for | S2 | **C** | OPEN — DESIGN/UX WORK | preserve obligation semantics; make customer purpose legible; exact surface placement remains UX decision |

## 5D. Future direction

| ID | Class | Evidence | Band | Disposition |
|---|---|---|---|---|
| **B-V006** | E5 | Phase B Vision Finding 006 `2026-08-20 13:23:26 +10` | **D** | expressive-presence intent preserved; exact surface/form unfrozen |
| **B-V007** | E5 | Phase B Vision Finding 007 `2026-08-20 13:24:31 +10` | **C** | ordinary/admin separation is a full-Test #2 requirement; low-risk surface concepts may prototype under Band D |
| **B-V009** | E5 | Phase B Vision Finding 009 `2026-08-20 13:26:42 +10` | **D** | D6 architecture investigation only |
| **B-V010** | E5 | Formal closure `2026-08-20`; individual record plus Phase B report | **A** | D2 triage principle; full-Test #2 triage performance is separately evaluated by P6 in Band C |

## 5E. Test/evidence limitations

| ID | Class | Evidence | Band | Status |
|---|---|---|---|---|
| **INFRA-L001** | E6 | Chrome-control interruption before completed matrix; full matrix began after restoration | Unbanded | TEST LIMITATION |
| **DATA-U001** | E6 | nudge ID 7 state transition; actor unresolved at Phase B boundary | Unbanded | ACCEPTED EVIDENCE UNCERTAINTY |

---

# 6. Finding → band map

**Authority note:** §6 is authoritative for finding/evidence-item band assignment. §11 is authoritative for decision/gate prerequisites within each readiness band. Cross-referenced decision/gate labels below are explanatory only; where wording differs, §11 governs the gate.

| Band | Findings / evidence items |
|---|---|
| **Band 0 — attended localhost text-only checkpoint** | no new blocker beyond existing governed local runtime; relevant truthfulness defect must not invalidate the specific slice being exercised |
| **Band A — before unattended testing** | OP-W004, OP-W005 evidence-capture reliability, NUDGE-F001, B-F001, B-V010; plus any action/error path used unattended |
| **Band B — before ambient sensing** | OP-W001/002, SEC-F002a/b, UI-SYNC001b; PB-F002's future critical-state class is handled through UI-SYNC001b. Applicable gates are defined in §11; S8 applies only if remote phone/wearable is included. |
| **Band C — full Test #2** | PT-F001, START-N001, OP-W003, TECH-F001, PB-F001/PB-F002, MF-F001/MF-F002, UI-SYNC001a, HU-F001/HU-F002/HU-F006/HU-F007/HU-F008, B-F002/B-F003/B-F004/B-Q008, B-V007 |
| **Band D — safe parallel prototype/cleanup** | MF-F003, MF-L001, MF-L002, HU-F003/HU-F004/HU-F005, B-F005, B-V006, B-V009; plus controlled synthetic prototypes |
| **Unbanded — evidence-only** | SEC-H001, INFRA-L001, DATA-U001 |

---

# 7. Confirmed successes to preserve

1. **Machine functional baseline:** 17 user-facing functions — **11 PASS, 4 PARTIAL PASS, 1 FAIL, 1 BLOCKED BY TEST DATA**.
2. **Parking Brake configuration-state matrix:** 64 combinations executed — **63 PASS, 1 PARTIAL PASS, 0 FAIL, 0 UNCLEAR**.
3. Matrix directly validated UI selection, displayed active state, governance-state persistence, and the skills-backed Notifications enforcement probe; **128 engage/disengage transitions across revisions 16–143 matched expectations**.
4. **Coverage boundary:** the matrix did **not** independently execute or prove direct `sight`, `voice`, `scheduler`, or `training` enforcement for every combination; C6 also records the known `sight`/`voice` authority migration seam.
5. Consent Approve/Deny paths persisted in isolated test state.
6. Notification quiet-hours/mute configuration worked and was restored.
7. Persona switching worked and was restored.
8. Goals/reflections had functional paths, with MF-F002 duplicate-episode side effects tracked separately.
9. Final shutdown was orderly: Ctrl+C, no force kill, process/port closed, DB integrity OK, state snapshots persisted.

**Awaiting Response success claim removed here unless/until a specific Test #1 functional case is cited.** Its canonical architectural purpose remains independently established by existing approved design documentation.

---

# 8. Decisions D1–D15

## D1 — Burden Reduction Principle
Bartholomew must reduce unnecessary cognitive, administrative, interaction, supervision, cleanup, and recovery burden. Deliberate sovereignty-preserving burden (meaningful consent, consequential approval, necessary safety/privacy decisions) is allowed but must remain proportionate and comprehensible. ABR remains conceptual until burden measurement is defined.  
**PROPOSED: APPROVE**

## D2 — Internal Triage + Obligation Preservation
Internal activity does not automatically justify interruption. Triage evaluates user need, consent/judgement/action requirement, urgency/risk, confidence, duplicate/stale status, internal resolvability, interruption cost, quiet hours/context. Genuine obligations must never be silently dropped. Deferred/consolidated/suppressed items remain durably represented/auditable where appropriate until resolved. `awaiting_response` remains the canonical durable external-reply obligation mechanism.  
**PROPOSED: APPROVE**

## D3 — Ordinary-User vs Diagnostic Separation
Separate ordinary-user and developer/admin information architecture. Safety-critical controls remain directly available to the ordinary user. Exact physical surface count/form remains open.  
**PROPOSED: APPROVE**

## D4 — Water/Hydration Out of Current Scope
Remove Water/hydration from ordinary active product/UI. Implementation must **amend/reconcile the existing canonical `RISKS.md` hydration entry**, not create a competing authority. Legacy data/model disposition is separate and must not silently delete historical data.  
**PROPOSED: APPROVE**

## D5 — Memory Agency, Portability, Derived-Data Truthfulness
Human-readable inspect/correct/forget/export/retention/provenance controls. Correction does not rewrite audit history. “Forget” must account truthfully for embeddings/FTS/summaries/reflections/cache/sensor-derived data; residual limitations must be disclosed.  
**PROPOSED: APPROVE**

## D6 — Sleep/Consolidation Investigation
Approve architecture investigation/controlled prototype only. Must define job eligibility, quiet-hours preference, batching, resources, interruption/resumption, crash safety, observability, availability, Governance/brake semantics, and D2 relationship. Cannot become a suppression/capture/action/notification bypass.  
**PROPOSED: APPROVE AS INVESTIGATION**

## D7 — Real-World Testing Standard + TILT Reconciliation
### D7a Full Test #2
Full Test #2 begins only when applicable safety/product gates pass.
### D7b Intermediate real-use checkpoints
Attended, localhost, text-only or similarly narrow slices remain allowed/encouraged under TILT once “done enough to test,” provided the specific slice has no unresolved safety/governance/privacy/data-integrity/architecture/experiment-validity blocker.

**Post-test judgement, not historical fact:** Taylor's post-Test #1 direction is that simply continuing to operate the same low-capability experience is unlikely to yield proportionate value; the next meaningful real-use checkpoint should therefore unlock a new useful slice rather than polish the existing one indefinitely.

**PROPOSED: APPROVE**

## D8 — Ambient-Sensing Readiness
No real ambient/passive camera/microphone/wearable capture until applicable gates pass. Subordinate to Constitution; includes keys, audit, privacy/retention, consent/revocation, bystanders, jurisdiction, direct brake enforcement, emergency shutdown, truthful state, teardown, sensitive context, queue containment, auth if remote. Controlled synthetic bench development allowed only without non-consenting capture/unattended real capture and with Governance developed alongside adapter.  
**PROPOSED: APPROVE**

## D9 — Formal Evidence Standard
Every formal finding: stable ID, class, severity, band, status, phase/case/step, timestamp/event, exact artifact, expected/no-oracle, actual, direct evidence, separate inference, impact, closure criterion, verification evidence. Stable evidence storage/checksums where practical. Reclassification/status/severity changes recorded.  
**PROPOSED: APPROVE**

## D10 — Authentication / Network Exposure Boundary
No silent extension of unauthenticated local PoC to remote/cross-device. Before real PC/phone remote/LAN/non-localhost use: reviewed threat boundary, authentication/authorisation, device/session ownership, brake/emergency semantics. Local non-actuating prototypes may proceed; **any local adapter that can control keyboard, mouse, screen, browser, launch actions, or otherwise exercise consequential device agency also requires D11/S9 regardless of network locality.**  
**PROPOSED: APPROVE**

## D11 — Independent Emergency Shutdown
Before unattended real sensing or consequential device agency, provide an independently enforceable emergency stop outside normal Bartholomew UI/in-process cooperation.  
**PROPOSED: APPROVE**

## D12 — Test #2 Personal-Data Handling
Before real personal-data capture, define captured classes, storage, raw retention, retention duration, derived artifacts, consent, deletion/revocation, encryption class, post-test retention, and test-created vs durable-memory distinction. **S11 verifies D12 rather than restating a separate policy.**  
**PROPOSED: APPROVE**

## D13 — Bystander / Third-Party Privacy
Before shared-home/public ambient capture, define bystander/household policy, jurisdiction/notice/consent implications, private/sensitive zones/suppression, truthful indicators, stop/revocation. Primary-user consent does not automatically authorise everyone nearby.  
**PROPOSED: APPROVE**

## D14 — Tester / Usability Validation
Taylor remains primary tester/product authority. Strong independent first-use discoverability claims require a naive/proxy tester. **Band C may proceed with Taylor-only P1 evidence if a naive tester is genuinely unavailable, but the result must be labelled designer-tested/not independently validated and may not be cited later as independent discoverability proof.**  
**PROPOSED: APPROVE**

## D15 — Abort / Closure / Evidence Freeze
Predefine abort criteria, exact repo/config/model/data/capability/consent/brake evidence freeze, and closure with orderly shutdown, final data inventory, unresolved findings, post-test data disposition, and explicit phase boundary.  
**PROPOSED: APPROVE**

### Decision-ID handling after approval
D1–D15 are **review cross-reference IDs**, not a replacement format for `DECISIONS.md`. The documentation handoff must record each approved decision using the repository's existing `## Decision: <title>` convention and may include `(Post-Test #1 D#)` as a cross-reference in the text.

---

# 9. Safety gates S1–S11

## S1 — Queue/interruption containment
Required before unattended testing.
- no queue-size warning whose only effect is another queue item
- zero equivalent unresolved duplicates absent explicit escalation
- bounded capacity
- cap shedding may affect only policy-eligible system-generated items; never silently drop genuine obligations
- shedding/consolidation auditable
- backoff/rate-limit/dedup/expiry tests
- `awaiting_response` exempt from silent expiry/cap shedding
- representative unattended test covers at least Test #1's observed generation pattern/window
- bulk/consolidated handling for legitimate multi-item queues

## S2 — Audit integrity/failure semantics
Required before unattended governed-action reliance; mandatory before ambient.
- root cause for OP-W004 identified **or** compensating design makes silent loss impossible
- deliberate failure injection
- governed action cannot present full success if required audit persistence failed
- missing/incomplete events identifiable
- **minimum tamper floor:** application code exposes no normal path to update/delete historical audit rows; destructive removal requires explicitly privileged/out-of-band maintenance and is itself documented
- stress/volume test supporting only
- crash/restart identifies incomplete in-flight operations

## S3 — Key lifecycle
- deliberate STANDARD/STRONG provisioning
- no key material in logs/evidence
- encrypt→restart→decrypt
- missing/invalid/mismatched keys fail visibly/safely
- rotation/recovery tested or explicitly out-of-scope with accepted consequence
- Test #1 ephemeral-key data recoverability/disposition documented

## S4 — Sensitive-context handling
- adversarial cases beyond password formats
- trace working context → governed write → consent → long-term memory → reflections/summaries → embeddings/FTS → logs
- no unnecessary later echo
- no ordinary persistence of secure-class content
- secure persistence follows agreed consent
- working-context TTL/redaction
- truthful derived-artifact deletion
- documented false-negative posture

## S5 — Direct Parking Brake enforcement
Precondition: C6 consolidation before real sight/voice.
For every selected capability:
- allowed when scope doesn't apply
- blocked when scope/global applies
- fail closed if brake unreadable
- engaging mid-stream terminates in-flight capability where applicable
- stop/teardown/disengage never treated as new gated start
- correct recovery
- state/audit agreement
- safe restart reconciliation

## S6 — Ambient consent/capture/bystander
- per-sensor opt-in
- truthful state
- OS/hardware-level indicator **where available; availability assessment recorded per sensor before test**
- immediate revocation
- brake terminates capture/prevents new capture
- teardown ungated
- fail-safe retention default/raw retention defined
- captured-data deletion/revocation treatment
- bystander/household policy
- jurisdiction/private-public assessment
- reconnect/restart does not silently resume contrary to policy

## S7 — Truthful critical state/output
Before scenario freeze, define explicit numeric freshness target for each critical state: brake, emergency readiness, sensor, recording, consent, governed action.
- stale state visibly marked with timestamp if target missed
- no undefined/blank/fabricated success
- backend/model outage cannot yield fabricated conversational success
- applies to voice
- selected/configured distinguished from reachable/ready
- no claim an external action occurred without execution evidence

## S8 — Authentication/network exposure
Required before real remote companion/non-localhost:
- reviewed threat model
- device/client auth
- correct personal-context authorisation
- session/token/key lifecycle
- replay/unauthorised brake-change tests
- remote client cannot weaken local Governance
- explicit network exposure
- no unreviewed “simple token is enough” assumption

## S9 — Independent emergency shutdown
Required before unattended real sensing or consequential device agency:
- outside ordinary Bartholomew UI
- works with ordinary UI unavailable
- works despite relevant normal-surface control/interference where applicable
- terminates capture/output/device agency
- independent of in-process goodwill
- restart after emergency deliberate

## S10 — Unattended failure/resource behaviour
Crash/restart, sensor disconnect, disk pressure, DB unavailable/locked, network loss, model unavailable, companion disconnect:
- no silent capture/action continuation under uncertainty
- no unsafe automatic resumption
- no recursive burden under resource failure
- observable failure
- recovery preserves Governance/data integrity

## S11 — Verification of D12 Test-Data Policy
S11 passes only when every D12 policy field has been instantiated for the selected Test #2 scenario/capabilities and:
- storage locations are enumerated
- test-created vs durable memory is distinguishable
- post-test inventory can be generated
- user can inspect retained data
- deletion/retention execution is recordable
- retained evidence is distinguished from retained personal content

---

# 10. Product gates P1–P9

## P1 — First-use + safety-control legibility
Ordinary user can:
1. identify interaction method
2. initiate useful task
3. understand listen/observe/act state
4. locate stop/privacy controls
5. **predict the effect of each safety control they can reach before operating it**, including scope/global semantics

All five required. Record time-to-first-useful-action. Taylor-only result is designer-tested unless D14 naive tester used.

## P2 — Useful Assistance Proof
Freeze **three scenarios + manual baselines before scenario-specific implementation**.
- all 3 complete in rehearsal
- ≥2 reduce active user steps
- ≥1 reduces active steps by ≥50%
- materially wrong outcome never counts
- failure truthful
- no larger mandatory cleanup than work removed
- ≥1 scenario includes proactive/relevant surfacing **and** genuine governed action **and** visible real-world result
Record steps/time/interruptions/cleanup/approvals/correctness.

## P3 — Conversational-primary control
At the **same pre-implementation freeze event as P2**, freeze the conversational evaluation set and target.
- routine initiation conversational
- admin console unnecessary for routine use
- visual panels only when useful
- unsupported commands fail explicitly/truthfully
- no silent non-action as success
Quantitative target selected before implementation/results, based on frozen workflows.

## P4 — User/admin separation is useful
Ordinary surface contains everything needed for normal use and safety, while diagnostic internals are hidden by default. Simplicity cannot be achieved by deleting useful capability.

## P5 — Memory agency
Required for full Test #2 (no “if durable memory participates” loophole):
- inspect human-readable memory
- provenance where appropriate
- correct
- forget/delete where permitted
- privacy/retention
- export
- inspect/delete sensor-derived memory if such data is created
- truthful derived-artifact handling

## P6 — Triage behaviour
At the **same pre-implementation freeze event as P2**, freeze triage evaluation set and target.
Cases: interrupt, log-only, duplicate, stale, genuine obligation, urgent/safety.
- not every concern nudges
- genuine obligations never silently lost
- false suppression auditable
- surfaced items useful enough to avoid obvious spam
Targets frozen before implementation/results; no post-hoc tuning of pass bar.

## P7 — Sleep boundary
Not required. If enabled: interruptible, available, observable, crash-safe, governed, no hidden backlog/no obligation disposal. Otherwise disabled.

## P8 — Capability rehearsal
Per selected capability define correctness/latency/failure/brake/consent/indicator/teardown/restart criteria before real use.
Potential: local camera, local mic, TTS, local PC, remote phone after S8, expressive presence, conversational invocation.
Expressive presence may be concept-validation rather than safety-critical rehearsal.

## P9 — Post-test data agency
Before Test #2 declared complete:
- human-readable inventory
- sensor-derived retained data inspectable
- retention/deletion executable
- evidence vs user-content retention distinguished
- unavoidable residuals disclosed

---

# 11. Readiness bands

## BAND 0 — ATTENDED LOCALHOST TEXT-ONLY REAL-USE CHECKPOINT
Operationalises D7b/TILT.

May occur before Band A if:
- attended by Taylor
- localhost/single-machine
- no ambient sensors
- no device-control actuation
- no remote companion/network exposure
- no unattended scheduler-driven real-world action
- existing Governance/Parking Brake remains active
- the specific vertical slice has no known unresolved defect that would invalidate its result

Purpose: get real feedback early on a newly useful slice without waiting for ambient/full-Test #2 readiness. **If the checkpoint is intended to measure burden or usefulness, S1 containment must pass first whenever scheduler/queue behaviour can influence that measurement; otherwise that behaviour must be deliberately disabled/excluded within the governed test envelope and the exclusion recorded.**

## BAND A — BEFORE ANY FURTHER UNATTENDED TESTING
Required:
- B-F001/NUDGE-F001 containment
- D2 obligation preservation
- S1
- OP-W004 investigation + S2 failure semantics for any governed action relied upon
- relevant truthful failure handling
- reliable evidence/logging

**Permitted unattended envelope at Band A:** no camera/microphone capture, no keyboard/mouse/device-control actuation, no remote companion, and no consequential outbound governed action unless the additional applicable S9/S11/action-specific gates have also passed. Notification/queue/scheduler observation may run within this envelope.

## BAND B — BEFORE REAL AMBIENT SENSING
Everything applicable from A, plus D8/D11/D12/D13, S3–S7, S9–S11, C6 brake consolidation, consent/revocation/retention, no silent restart/resume. If remote phone/wearable: D10/S8.

## BAND C — BEFORE FULL REAL-WORLD TEST #2
Everything applicable from A/B, plus:
- D1 burden instrumentation
- D3 ordinary/admin separation
- **D5/P5 memory agency (required)**
- P1–P6, P8–P9
- D15
- PT-F001 readiness
- OP-W003 retrieval-mode decision
- TECH-F001, MF-F001, MF-F002, PB-F001/PB-F002, UI-SYNC001 critical-state issues fixed where relevant
- HU-F002 conversational quality addressed
- P2's full proactive + governed-action + visible-result scenario passes

## BAND D — SAFE PARALLEL PROTOTYPING / LOW-RISK CLEANUP
Allowed before full C:
- synthetic/pre-recorded camera
- synthetic microphone/audio
- TTS
- non-actuating localhost PC protocol
- expressive presence
- customer UI concepts
- triage simulation
- sleep architecture on test data
- memory UI on synthetic memories
- Water cleanup
- microcopy/slider/testability refinements

Must not bypass Governance, capture non-consenting people, enable real unattended capture, expose unauthenticated runtime remotely, or make sight/voice live before C6 consolidation.

---

# 12. Implementation dependency tracks

## Track 1 — Immediate unattended blockers
nudge recursion/duplicates → D2 triage/obligations → OP-W004 investigation → audit failure semantics → unattended truthfulness/logging.

## Track 2 — Governance seams for richer capability
C6 brake consolidation → mid-stream brake/teardown → emergency shutdown → ambient consent/state → keys → sensitive-context investigation.

## Track 3 — Data truthfulness
MF-F002 → MF-F001 → PB/UI sync → chat truthful errors → Water ordinary-UI cleanup.

## Track 4 — Customer/product
ordinary/admin separation → Awaiting Response legibility → memory agency → conversational flow/quality → first-use → bulk pending work.

## Track 5 — Controlled capability prototypes
camera/mic/TTS/local non-actuating PC/expressive presence; remote phone only after D10/S8. Governance enforcement built **with** each adapter.

## Track 6 — Real capability rehearsal
Only after applicable safety gate.

## Track 7 — Full Test #2 freeze/run
Freeze P2/P3/P6 scenarios/targets, repo/config/data policy/abort/evidence; execute; close per D15/P9.

---

# 13. Documentation-handoff housekeeping after approval

The later Claude Code documentation handoff must explicitly include:

1. record D1–D15 using existing `DECISIONS.md` heading convention, with D# as cross-reference only;
2. reconcile D4 with the existing hydration/water entry in `RISKS.md`, not create parallel authority;
3. update `docs/SAFETY_PARKING_BRAKE.md` because it documents five scopes while current code/test evidence includes six (`training`);
4. preserve Test #1 evidence artifacts/checksums at a stable referenceable location;
5. record the exact Test #1 commit/branch relationship correctly;
6. update canonical sequencing docs to reflect Band 0/A/B/C/D and TILT reconciliation without creating a competing SSOT.

---

# 14. Deliberately unresolved

Not frozen:
- number/form of user/admin/expressive surfaces
- expressive avatar form
- customer label/location for Awaiting Response
- customer terminology for Affect/Attention/Drives
- exact nudge cap/rates
- exact critical-state freshness values
- final P3/P6 numeric targets before scenario freeze
- sleep job algorithms
- wearable-camera inclusion
- production auth architecture
- global legal-compliance system
- numeric ABR threshold
- final multi-user/server implementation

Claude Code must not silently decide these.

---

# 15. Approval checkpoint

**v2.2 correction-pass check completed:** the changes from v2.1 were checked against the independent review for arithmetic, provenance wording, row count, controlled vocabulary, band mapping, cross-references, and unintended decision changes. No new internal factual/provenance inconsistency was found within the available source set. This does **not** remove the independent review's stated evidence-access limitation: restored Test #1 case IDs/timestamps remain internally checked rather than independently re-verified against inaccessible raw Test #1 artifacts.

**Status remains NOT YET APPROVED.** Taylor approval is still required before D1–D15, S1–S11, P1–P9, Bands 0/A/B/C/D, and Tracks 1–7 become project authority.

If approved:

1. Taylor approves D1–D15, S1–S11, P1–P9, Bands 0/A/B/C/D, and Tracks 1–7.
2. Create Claude Code **documentation-only handoff**.
3. Claude updates canonical docs/decision/risk/test records.
4. Taylor reviews those doc changes under Approval Gate.
5. Implementation work packages are then proposed and approved slice-by-slice under TILT.

---

# Final principle

> **Bartholomew must not convert internal activity into unnecessary user work, and it must not reduce that burden by silently losing obligations, weakening governance, hiding uncertainty, or capturing more of the world than it is ready to govern safely.**

Test #2 should prove that a substantially more capable Bartholomew can perceive more, do more, and interact more naturally while requiring less unnecessary management from the user — without weakening the user's authority over it.
