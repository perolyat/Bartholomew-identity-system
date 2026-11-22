# SOLOMON / Bartholomew V2 — Architecture Decision Candidate

**Planning date:** 2026-11-23 (Australia/Brisbane)  
**Status:** Proposed for GitHub canonical promotion; **not merged, not implemented, not tested**  
**Authority:** 2026-11-23 owner delegation to finish planning reviews without individual prompts, **not** permission to build, spend, connect accounts, expand private data access, or deploy.  
**Code baseline inspected:** `main` at `0142673628ab74fe5991005d146dfbaa97729c90` (2026-10-09 PR #128). Changes to this SHA or the available device environment require refreshing evidence.  
**Trace:** Airtable V2 Programme R04 `recz9h9ganpeSw6pn`, R05 `recgrhkXGU5enDJQa`, R07 `recV3ohQUkL3IQey7`, V1 acceptance `recnP2pCIJOrzTHUk`, 96/96 coverage `R03A-03B-02.01..10`, deep MEM03 `recRxUWB1Tm6d4SC5`.

## Decision in one paragraph

Develop one persistent, governed, **server-centric** personal AI Executive that is independent of the models, devices and interfaces it uses. Build the first practical single-owner Windows/Android experience **front-end-led, contract-first and vertically integrated**: model the real owner journeys and all success/deny/unknown/stop/rollback states first; implement UI, backend, independent governance, memory, ECI endpoints and independent verification together for real end-to-end results. Reuse validated existing code where its contracts hold, refactor unsafe integration seams, and replace bounded components only where independently justified. An attended Windows-hosted controller is a viable low-cost development configuration, not a promise of always-on cross-device service. Production server location and external account permissions are separate choices. No full robotics, unproven cognitive theory, continuous sensing, autonomous finance trading or commercial multiuser build is a Version 1 prerequisite.

## Existing durable repository authority preserved

The 2026-08-17 **server-centric Bartholomew with local/edge capability agents** entry in `DECISIONS.md` explicitly supersedes the older hybrid local-first entry. It does **not** require a paid public cloud or remove offline local protection. Preserve: one central Executive; personal state independent of any LLM/provider/device; device agents act as capabilities; local user-reachable stop even when a server is unreachable; stricter platform and personal brake tiers; no Internet exposure without independently reviewed authentication, authorization and transport security. Existing `CONSTITUTION.md`, `COGNITIVE_RUNTIME.md` and `INTERFACES.md` remain authoritative until an approved canonical change. This candidate is not a conflicting second authority.

`START_HERE.md` currently documents useful integrated governance and memory substrate, operator-only Windows action, limited conversational Executive behind default-off switches, and absent real-model plan-quality proof. Its current-state snapshot was updated around September 2026 and may be stale. The October 9 merge message reports Windows Gate 8 postmerge qualification still **0/3** and broader incident **OPEN**, despite narrow CI success. Do not confuse old installed binaries, source tests, CI green or mocks with real owner capability.

## Logical trust and component boundaries

```
Windows owner UI  ----+
Android UI/bridge ----+--> Authenticated UI API / device pairing
                            |  (user intent, not grants)
                            v
                    ONE logical Executive
                    plan, task graph, conflicts
                            |
                    Independent Governance
                    GOV-AUTH-1 + Brake, owner scope,
                    effect fingerprint, expiry,
                    data class and cost reservation
                            |
                    governed ECI dispatcher
                      /       |       \
              Windows OS   Android OS   approved services
               endpoint     endpoint     /model adapters
                      \       |       /
                     independent effect
                    readback / reconciliation
                            |
               durable task, evidence, memory,
               obligations, policy and UI status
```

- **Presentation:** Can display/submit intent; cannot mint or override a grant.
- **Reasoning/model layer:** Interchangeable local, self-hosted or provider model(s) produce proposals and natural output. They cannot authorize external effects or decide user rights.
- **Executive:** One logical accountable goal/task owner (not necessarily one CPU thread); can delegate bounded work but must remain the owner of dependency and obligation state.
- **Independent authority:** Check authenticated owner, exact grant/action/scope, current revocation generation, brake tier, data classification, cost budget, actual endpoint capability and owner quiet semantics at **execution time**. No alternative tool/provider path around the gate.
- **External Capability Interface:** Typed adapter and device identity/freshness; observes, requests, receives only governed directives, returns correlated attempt/receipt and independent result where feasible.
- **Data:** One authoritative versioned logical store for tasks, policy, effects, data provenance and pending obligations; model transcripts and old sync buffers are evidence, never grants.
- **Verification/recovery:** An API ACK, scheduled reminder or model sentence is never independent proof of delivery, medication ingestion, purchase, cancellation or successful task completion. Unknown remains visible and blocks blind retries.

### Typed protocol contract (implementation-neutral)

```
OwnerRequest(principal_id, client_device, request_id, utterance, at, source)
IntentProposal(request_id, hypothesis, interpretation_kind, uncertainty)
GoalPlan(goal_id, parent_task, steps, dependencies, predicted_effects, costs)
ScopeGrant(grant_id, principal, action_fingerprint, target, scope, expiry,
           version, data_classes, budget, revoked_at)
EndpointStatus(device_id, capabilities, freshness, observed_status, permitted_actions)
EffectAttempt(intended_effect_id, attempt_id, grant_version, target, phase, at)
EffectEvidence(attempt_id, provider_ack, direct_readback, independent_status, at)
Obligation(task_id, due, outstanding_effect, state, next_safe_step)
MemoryRecord(source_id, class, confidence, freshness, retention, derivatives)
DeletionRecord(target, generation, target_copy, state, independent_proof, exception)
StopCommand(scope, intent_kind, authority_tier, version, affected_endpoints)
```

Required state vocabulary: `PROPOSED`, `WAITING_APPROVAL`, `AUTHORIZED`, `RUNNING`, `PENDING`, `VERIFIED`, `PARTIAL`, `FAILED`, `UNKNOWN`, `CANCELLED`, `STOPPED`, with explicit cross-entity semantics. An operation can be physically performed while its readback remains UNKNOWN. Only declare success at a justified evidence tier; external undo is a new governed operation.

## Client and device strategy

**Preferred conditional UI direction:** share a responsive TypeScript/React application for Windows browser/installable desktop experience and Android mobile surface. Use an appropriately minimal **native Android edge bridge** (candidate Capacitor + Kotlin OS adapter) for real notification, mic, secure pairing, stop and offline integrations; never rely on a browser timer to meet exact alarm or reliable background work. A Flutter single Windows/Android app is a legitimate alternative if a small real-platform spike demonstrates fewer security/maintenance problems; a Tauri Windows/mobile wrapper is optional and must justify its additional implementation cost.

Android's documentation identifies WorkManager as a recommended persistent background scheduler, separate from AlarmManager's time-specific alarm semantics. Notifications on modern Android require app-level runtime permission; many exact alarms require special access. **Explicit owner permission for phone alarms is a separate governance condition, even when Android OS grants a technical permission.** The mobile UI must represent stale device/backend data and queued/unconfirmed operations honestly. Offline actuation cannot continue on obsolete grant just because a command was cached.

Official implementation references (read 2026-11-23):
- https://developer.android.com/develop/background-work/background-tasks/persistent
- https://developer.android.com/about/versions/14/changes/schedule-exact-alarms
- https://developer.android.com/develop/ui/compose/notifications/notification-permission
- https://developer.android.com/topic/architecture/data-layer/offline-first
- https://docs.flutter.dev/platform-integration/desktop
- https://capacitorjs.com/docs/android

## Persistence and deletion design constraints

Existing SQLite/WAL is a candidate for single-owner authoritative task/memory/policy/effect state **only after** a dedicated write-owner, bounded transaction, pending-effect ledger, restart/backup process and Windows headroom tests. Do not silently change to a distributed database just to appear extensible. The historical incident includes measured close/commit and scheduler timing issues; no long-running model calls or synchronous blocking control action while holding a SQLite writer. See https://sqlite.org/wal.html.

The approved MEM03 deep assurance specification requires inspect/correct/restrict/delete controls and per-copy removal/no-use truth across originals, summaries, embeddings, logs, provider copies, backups, offline devices and restored snapshots. Distinguish local verified removal, access restriction, cryptographic unreadability, partial failure and third-party unverifiable copies. Do not preserve forbidden plaintext as 'audit evidence'. The new deep fault cases are designed, not run.

## Choice comparison and reuse rules

| Approach | Benefit | Serious risk | Disposition |
|---|---|---|---|
| Patch entire existing backend | Faster immediate reuse | Repeats old interface/user-value gaps and latent coupling | Not default |
| Clean rebuild | Control of all internal contracts | Rewrites tested safety/state and reproduces bugs | Only if actual audit proves architectural incompatibility |
| **Selective staged reuse/refactor/replacement** | Preserves proven work with bounded regression and rollback | Demands independent component qualification | **Preferred** |
| Strict UI/mock-first | Early visual review | False working product, late safety integration | Reject as complete method |
| Backend-first foundation-only | Strong local subsystem tests | Later discovery of poor whole-user experience | Reject as milestone strategy |
| **Frontend-led, contract-first vertical integration** | User experience + safety + actual verified effect continually connected | Requires disciplined shared contracts | **Preferred delivery method** |

Possible retained modules, not yet requalified for V2: FastAPI service, existing identity/memory, governance/brake, ECI and Windows tool agent, evidence/CI skeleton. Fault-dependent refactors: mixed SQLite access lifecycle, model policy vs runtime router, chat intent and actual general reasoning, incomplete cross-device state, consent/memory derivatives, weak test oracles and final installed experience. All modules can be retained **only if current test evidence supports it**.

## Architecture acceptance spikes before feature construction

1. UI prototype on **actual** supported Windows PC and Android phone, testing foldable layout, push-to-talk, task tree, owner privacy/consent surfaces, no fake connected state.
2. Signed device pairing, offline/local stop despite central service loss, explicit stale/UNKNOWN status.
3. Native Android notification and exact alarm behavior with denied, revoked, blocked and offline state (synthetic data, no real alarms without specific owner consent).
4. Real-model semantic plan vs mocked schema and unfamiliar user intent; check incorrect target, changing goal, refusals and truthful unsupported skill.
5. One genuinely permitted low-risk digital action, complete from owner request through independently verified outcome and durable GUI closure; include denied grant, effect ACK lost, task cancel and duplicate-prevention.
6. SQLite Windows WAL commit/close/crash/concurrent health/control instrumentation and recovery; deep memory deletion/replay/restore in disposable fixtures.
7. Security and cost evaluation of any remote transport/provider/hosting option before enabling it.

**None of these has been executed by the planning review.** Any material failed spike requires comparison to the documented alternate, not rationalized forced adoption.

## Explicit non-authority and next step

This is a **draft design candidate on a non-main branch**, not active runtime policy or code. User has delegated the planning review, but original requirement scope, owner privacy/data grants, banking/health accounts, provider charges, emergency contacts, phone alarms, GitHub main authority and actual implementation/release still require their respective effect-specific controls. Next: implementation work-package plan, pre-build evidence refresh and a single clear implementation go/no-go. Do not quietly start builder waves.
