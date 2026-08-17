# CHECKLISTS

> Operational and engineering checklists. If it’s not checked, it’s not real.
>
> **Last updated:** 2026-08-15 (added a "Platform and personal-identity architecture" checklist —
> the operational form of `CONSTITUTION.md`'s new binding conflict-surfacing rule, covering
> ownership representability, execution beneficiary, identity-not-pinned-to-infrastructure, local
> stop authority, personal-vs-platform learning, and an explicit guard against premature platform
> engineering. No existing checklist covered the "whose Bartholomew is this?" question. See
> `DECISIONS.md`'s "One shared Bartholomew platform; many strongly isolated personal Bartholomew
> identities" entry.)
>
> **Previously (2026-08-14):** added one line to the PR checklist making explicit that the
> existing secrets/confidential-data discipline applies equally to AI coding-agent sessions —
> restates existing `.gitignore`/`detect-private-key` control scope; no new mechanism. See
> `DECISIONS.md`'s new "AI-assisted development is governed by..." entry.)
>
> **Previously (2026-07-31):** Phase B governance restructuring: added a "Staged workstream
> approval" checklist — overview approval does not authorise a stage; stage N approval does not
> authorise stage N+1; a stage must not silently expand into the next. Existing general checklists
> did not unambiguously cover this relationship.)
>
> **Previously (2026-07-28):** documentation reconciliation pass 2: added a "Product & safety
> invariants" checklist covering the six invariants added to `CONSTITUTION.md` this pass, plus
> consistency with the deployment-architecture decision.
>
> **Previously (2026-07-27):** the PR checklist's "`pytest -q` passes" item was misleading — that
> command deselects 3 tests — and the checklist predated the `ci.yml` gates entirely.

## Non-negotiables checklist (Before “ready to Act”)

Mark each as **PASS** or **BLOCKED**.

- **Realism:** We can run it end-to-end on a clean machine.
- **Governance preserved:** parking brake + consent gates enforced; fail-closed behavior.
- **Privacy respected:** redaction before storage; encryption where required; no sensitive logs.
- **Verification included:** tests + repro commands documented.
- **Change control:** major changes include impact + migration + rollback.
- **Interfaces updated:** `INTERFACES.md` updated if contracts changed.
- **Assumptions logged:** unresolved assumptions tracked in `ASSUMPTIONS.md`.
- **Risks assessed:** updated `RISKS.md`.
- **CI plan:** `CI.md` gates updated or explicitly unchanged.

## PR checklist (DoD gate)

- [ ] Acceptance criteria stated in PR description
- [ ] Tests added/updated
- [ ] `pytest -q` passes — **note this deselects `integration`/`slow` tests**; also run
      `pytest -m "integration or slow"`, which is what `ci.yml`'s `critical` job does
- [ ] `ruff check .` and `black --check .` pass **at the pinned versions** in
      `requirements-dev.txt` / `.pre-commit-config.yaml` (an unpinned newer ruff reports rules
      the pinned hook does not — this made one tree simultaneously "clean" locally and "68
      errors" in CI)
- [ ] All `ci.yml` jobs green: `quality`, `tests` (3.10 + 3.11, coverage gate ≥70%),
      `critical` (3.10 + 3.11), `windows` (3.11)
- [ ] No new undeclared runtime dependency (`tests/smoke/test_packaging_contract.py` enforces this)
- [ ] Docs updated (canonical docs if behavior/interface changed)
- [ ] Rollback note included for risky changes
- [ ] No new bypass paths introduced (consent gate / parking brake)
- [ ] No secrets, credentials, API keys, or confidential/customer data exposed to or committed by
      any coding-agent session (human-directed or AI) — existing `.gitignore`/`detect-private-key`
      controls apply equally regardless of who or what staged the change
- [ ] User approval obtained for all doc/code changes before commit
- [ ] Changes presented with clear diff/summary for review

## Product & safety invariants checklist (added 2026-07-28)

Mark each as **PASS** or **BLOCKED** for any change that touches a new capability, subsystem, or
user-facing behaviour. See `CONSTITUTION.md`'s "Safety, Accessibility, and Product Invariants"
section for the full rationale behind each item.

- **Consumer-value gate:** the change materially reduces cognitive burden, reduces life
  administration, prevents an important matter being forgotten, improves the user's outcomes, or
  preserves/increases trust — architectural sophistication alone does not pass this check.
- **Independent emergency control:** if the change adds or touches a capability that could
  interfere with the user's normal controls (UI, input devices, screen), the emergency-shutdown
  path independent of that capability is unaffected/still reachable.
- **Capture teardown safety:** if the change touches voice/sight/any capture capability, stopping
  or tearing down capture does not require passing a consent/policy gate (teardown is not a
  governed "start").
- **Notification-fatigue consideration:** any new notification/reminder respects existing
  mute/quiet-hours controls and does not repeat without new information.
- **Accessibility consideration:** any change touching obligations/follow-ups uses (or does not
  bypass) the `awaiting_response` state rather than silently treating a sent message as resolved.
- **Data portability:** any new kind of user data (memory, preference, goal, approval, audit
  record) is exportable via the data-portability mechanism, or an explicit exception is recorded.
- **Deployment-architecture consistency (updated 2026-08-17):** the change is consistent with
  `DECISIONS.md`'s "Deployment architecture — server-centric Bartholomew with local/edge capability
  agents", which **supersedes** "hybrid local-first". Concretely: **governance, the Parking Brake
  and emergency shutdown stay locally enforceable** and must not become dependent on central
  services being reachable; any remote/cross-device exposure has a reviewed threat model; and
  cognition does not acquire a dependency on a particular UI or device.
  **Changed:** this item previously also required *sensitive memory* to stay local-authoritative.
  It no longer does — personal memory is intended to become server-side. The locally-enforceable
  *stop authority* requirement is the part that was retained, and is what this check now turns on.

## Platform and personal-identity architecture checklist (added 2026-08-15)

Applies to any change that adds or alters persisted personal state, background/scheduled
execution, capability execution context, governance/audit records, an API surface, a client/server
boundary, or the way an underlying model is used. It makes `CONSTITUTION.md`'s "One Platform, Many
Personal Bartholomews" → "Conflict-surfacing rule" operational at change time. The existing
Product & safety invariants checklist above does not cover this: its deployment-architecture item
asks *where authority sits*, not *whose Bartholomew this is*.

The first item is the binding one. The rest are the questions that reveal whether it applies.

- **PASS/BLOCKED — Conflict surfaced, not silently resolved:** if this change would materially
  jeopardise any of the nine properties named in `CONSTITUTION.md`'s conflict-surfacing rule
  (one platform / many identities; strong isolation; persistence of individual identity;
  portability of identity and state; Bartholomew-is-not-the-LLM separation; hybrid/local
  Governance authority; eventual lightweight client; ability to replace infrastructure or models
  without replacing the person's Bartholomew; the two Parking Brake authority tiers and their
  precedence), **the conflict has been explicitly put to the user before implementation.**
  Implementation convenience is not a resolution.
- **PASS/BLOCKED — Ownership representable later:** any new persisted personal state could later
  acquire an owner without redesign (no new *global* uniqueness constraint over personal data, no
  new record type whose ownership would be unrecoverable).
- **PASS/BLOCKED — Beneficiary recoverable:** any new background, scheduled, or capability
  execution can eventually answer "on whose behalf is this running?" — no new work whose
  beneficiary is structurally unknowable.
- **PASS/BLOCKED — Identity not pinned to infrastructure:** the change does not make personal
  identity or continuity depend on a specific device, filesystem path, database engine, server,
  AI provider, or model generation. Deployment choices may be convenient; they may not become
  identity.
- **PASS/BLOCKED — Local stop still local:** the change does not move the parking brake,
  emergency shutdown, or local device permission enforcement behind a remote service, and does not
  create a state where a cloud/service outage leaves the user unable to stop Bartholomew acting on
  their devices.
- **PASS/BLOCKED — Parking Brake tiers and precedence respected:** if the change touches the
  Parking Brake, a Governance gate, or anything that could halt execution, it does not collapse the
  Personal/User and Platform/Admin tiers into one undifferentiated switch, does not let one user's
  brake affect another user's execution or authority, does not make a platform-wide halt
  user-overridable or require disabling users individually, and does not move enforcement into a
  client such that a crash, disconnection or bypass could invalidate the halt state. New brake
  scopes are *subsystem* scopes and are **not** a way to express an authority tier — see
  `COGNITIVE_RUNTIME.md`'s "Authority tiers" subsection, the canonical authority.
- **PASS/BLOCKED — Personal learning stays personal:** the change does not route personal memory,
  personal context, or user-identifying material into shared, platform-level, or cross-instance
  knowledge (see `CONSTITUTION.md`'s "Personal learning vs. potentially generalisable and
  system-level learning" — name removal is not depersonalisation).
- **PASS/BLOCKED — Not premature platform work:** conversely, the change does **not** build
  multi-user, tenancy, authentication, or distributed infrastructure that the current PoC does not
  need. This checklist exists to prevent dead ends, not to authorise platform engineering — see
  `docs/TILT.md`.

## Staged workstream approval (e.g. Phase B B0–B9) (added 2026-07-31)

Applies to any workstream, like Phase B, that is deliberately split into a concise overview plus
multiple separately gated stages (see `ROADMAP.md`'s Phase B section and `docs/PHASE_B_OVERVIEW.md`
for the current example). None of this is duplicated by the Non-negotiables, PR, Release, or
Commit-authorization checklists above, which govern a single change or gate, not the relationship
between an overview and its stages, or between one stage and the next.

- **PASS/BLOCKED:** Approving the workstream's overview document does not, by itself, authorise
  implementation of any stage.
- **PASS/BLOCKED:** Approving stage N's plan or implementation does not authorise stage N+1 — each
  stage requires its own explicit plan approval before implementation begins.
- **PASS/BLOCKED:** A stage's implementation does not silently expand into a later stage's scope;
  any such expansion is instead raised as a proposed change to the overview/`ROADMAP.md` stage
  structure, not implemented unannounced.
- **PASS/BLOCKED:** Implementation still requires diff review, and committing still requires
  explicit user approval, per the Commit authorization checklist below — staging does not relax
  either requirement.

## Release checklist (Stage gate)

- [ ] Gate exit criteria in `ROADMAP.md` met
- [ ] `REVIEWS.md` stage review completed
- [ ] Audit log sanity check performed
- [ ] Known issues documented (with explicit scope)

## Commit authorization checklist

Every `git commit` requires:
- PASS/BLOCKED: User has explicitly reviewed proposed changes
- PASS/BLOCKED: User has authorized the commit (verbal/written confirmation)
- PASS/BLOCKED: No autonomous commits without human approval
- PASS/BLOCKED: Changes align with stated task objectives
- PASS/BLOCKED: Commit performer — commits are executed only after user approval, by the user or an explicitly supervised session

## Prompt hygiene (agent execution)
- PASS/BLOCKED: Prompts do **not** paste huge transcripts.
- PASS/BLOCKED: Large sources are referenced as files; work is chunked with intermediate artifacts.
- PASS/BLOCKED: Each chunk has acceptance + verification and can be re-run deterministically.
