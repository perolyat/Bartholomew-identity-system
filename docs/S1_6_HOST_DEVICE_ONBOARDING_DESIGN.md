# S1.6 Design — Host-Device Onboarding Guidance

> **Authority note:** this document is a *design proposal*, not an approval. It is subordinate to
> `ROADMAP.md` (Stage 1's canonical exit criteria) and `docs/STAGE_1_OVERVIEW.md` (S1.6's scope
> stub), and to `DECISIONS.md`'s "Deployment architecture: hybrid local-first" entry, which is the
> approved direction this onboarding content must stay consistent with. Nothing in this document
> authorises implementation — per `docs/STAGE_1_OVERVIEW.md`'s non-negotiable invariants, S1.6
> needs its own separate, explicit approval before any code is written, same as every other Stage 1
> sub-stage.
>
> **Status:** proposed 2026-08-05. Not approved. Not implemented.

## 1. What this closes

`ROADMAP.md`'s Stage 1 "Also in scope" note: *"host-device onboarding guidance — during setup, show
the user the realistic advantages and limitations of running the trusted local Bartholomew runtime
on a phone, a personal computer, a home server/hub, a hosted cloud service, or a hybrid
local-plus-cloud deployment."* Its exit criterion is specific and testable: *"Host-device onboarding
presents the trade-offs above **without recommending a specific device as though it were the only
supported option**."*

Unlike S1.4, this sub-stage introduces no new governed capability, no new action surface, and no
Governance/Parking-Brake concern — it is informational content plus a thin read-only API, not a
mechanism. Its risk is almost entirely about *honesty*: `CONSTITUTION.md`'s red line against
"deception about AI identity or capabilities" applies directly to a page whose entire purpose is
describing what Bartholomew can and cannot do on a given device today.

## 2. Non-goals (explicitly out of scope here)

- **No cross-device auth, sync, or relay implementation.** That is Stage 6's own separately-designed
  work (`ROADMAP.md` Stage 6, `ASSUMPTIONS.md`'s corrected cross-device-auth entry). This design
  must not imply any of it already exists.
- **No hosted-cloud-runtime offering.** `Identity.yaml`'s `deployment_profile.models.cloud_optional`
  is optional *model inference* only (`OpenAI GPT`, `Anthropic`, `Google`) — there is no first-party
  hosted deployment of the trusted runtime itself anywhere in this codebase. The "hosted cloud
  service" deployment target (below) must be presented as the project's honest architectural
  position on that option, not as a feature a user can pick today.
- **No data-export/portability feature.** `CONSTITUTION.md`'s Data Portability invariant and
  `ROADMAP.md` Stage 6's "data portability/export delivery" scope note both confirm this is a real
  commitment but not yet built. Onboarding content may state the commitment; it must not claim the
  export button exists.
- **No new persistence schema.** See Sec 5 — a client-only "seen this" flag is sufficient; there is
  nothing here worth a governed, audited SQLite table the way S1.4's obligation queue was.

## 3. Deployment target content

Five targets, matching `ROADMAP.md`'s list exactly, in that order (alphabetized would reorder
"cloud" before "computer" and read as arbitrary; the ROADMAP.md order already reads
local-device-first-then-hybrid, which is itself consistent with the hybrid-local-first architecture
without *naming* one as recommended — see Sec 6 for how neutrality is enforced independent of list
order). Each target lists real advantages and real limitations; none is flagged "recommended."

**1. Phone**
- *Advantages:* Always with the user — the closest fit to `CONSTITUTION.md`'s UX Principle that
  setup should feel like "my phone just became intelligent," and the natural home for a
  voice-primary interface once Stage 6 voice adapters exist. Built-in microphone/notification
  surfaces.
- *Limitations:* `Identity.yaml`'s local models (`Mistral-7B-Instruct-GGUF-Q4_K_M` primary,
  `Phi-4 3B`/`TinyLlama 1.1B` fallbacks) are real compute and battery/thermal load on a phone; OS
  background-execution limits (iOS/Android both restrict long-running background processes) work
  against an always-on daemon and Stage 5's proactive scheduler drives; on-device storage is the
  most constrained of the five options for a growing memory database.

**2. Personal computer**
- *Advantages:* Best local compute-to-battery ratio of the five for running `local_primary` well;
  an always-on daemon is straightforward; most storage headroom; best fit for the filesystem/OS
  integration `Identity.yaml`'s `tool_use.sandbox.filesystem` already assumes.
- *Limitations:* Not always with the user — away from the machine, there is no live daemon to
  reach yet (that gap is exactly what Stage 6 cross-device access closes, once its own auth/threat
  model work is separately approved); the user must keep it powered on for Stage 5's scheduled
  check-ins/proactive drives to fire on schedule.

**3. Home server/hub**
- *Advantages:* Always-on without depending on a personal device's battery or the user remembering
  to leave a laptop open; centralizes one authoritative runtime that (once Stage 6 ships) could
  eventually serve multiple client devices from a single trusted source, closest fit to `Identity.
  yaml`'s multi-device ambition ("coordinating skills across devices").
- *Limitations:* Requires the user to own, set up, and maintain a machine that stays powered on —
  a real cost and technical-complexity barrier, not assumed as free; the trusted runtime is now
  physically separate from whatever device the user is actually holding, so — same as personal
  computer, more acutely — reaching it while away depends entirely on Stage 6 work that does not
  exist yet.

**4. Hosted cloud service**
- *Advantages:* No local hardware constraints on inference quality; in principle the easiest
  target for cross-device reachability, since a cloud endpoint doesn't depend on any one physical
  device being on.
- *Limitations:* Directly in tension with `DECISIONS.md`'s hybrid local-first decision, which
  explicitly rejected a pure hosted architecture because it would make sensitive memory and
  governance enforcement — including the parking brake and emergency shutdown — dependent on a
  remote service the user does not fully control (`CONSTITUTION.md`'s sovereignty principle and
  independent-emergency-shutdown invariant). **This project does not offer a first-party hosted
  deployment of the trusted runtime itself today** — only optional cloud *model inference* per
  `Identity.yaml`'s `cloud_optional` list. This option must be presented as the project's honest
  architectural position (why full hosting is deliberately not where sensitive state lives), not as
  a fifth interchangeable choice available today.

**5. Hybrid local-plus-cloud**
- *Advantages:* This is the architecture `DECISIONS.md` actually approved: a local device or hub
  stays authoritative for sensitive memory, governance, the parking brake, and emergency shutdown
  (independent of network/cloud availability), while optional cloud services — better model
  inference, relay, future synchronisation — are used only where the user explicitly opts in.
  Keeps sovereignty guarantees while allowing better model quality than local-only compute permits.
- *Limitations:* More moving parts to explain than a single-target choice; today "hybrid" concretely
  means "local runtime + optional cloud model calls" (`Identity.yaml`'s `cloud_optional`) — the
  richer relay/multi-device-sync mechanics implied by "hybrid" are Stage 6 scope, not built yet.

**Currently-available-vs-planned framing (non-negotiable, see Sec 6):** every target's copy must
distinguish what exists in this codebase today (a runnable local daemon + API bridge + optional
cloud model calls) from what is architectural direction not yet implemented (cross-device
reachability, data export, a hosted runtime offering). This is not a phrasing nicety — presenting
planned capability as available would violate `CONSTITUTION.md`'s "no deception about AI identity
or capabilities" red line applied to this project's own onboarding copy.

## 4. Proposed ownership and shape

No existing `COGNITIVE_RUNTIME.md` ownership-table concept covers "static informational content
about deployment choices" — this is presentation content, not a runtime concept, so it does not need
a new row there. Proposed:

- **Content module:** `bartholomew_api_bridge_v0_1/services/api/onboarding_content.py` — a plain
  Python list of dataclasses/dicts (`target_id`, `name`, `advantages: list[str]`,
  `limitations: list[str]`, `available_today: bool`), no database, no I/O. Kept as a Python module
  rather than a YAML/JSON config file so it is covered by the same `ruff`/`black`/review process as
  the rest of the codebase, matching precedent (`bartholomew/config/*.yaml` is for genuinely
  user-tunable runtime config; this is fixed editorial content, closer to a docstring than a
  setting).
- **API:** `bartholomew_api_bridge_v0_1/services/api/routes/onboarding.py`, one route:
  `GET /api/onboarding/deployment-guide` → `{"targets": [...]}`. Read-only, no governance
  implication (no Parking Brake / Identity Policy check needed — this is equivalent to a static
  asset, not a capability), consistent with this bridge's existing no-auth posture
  (`INTERFACES.md`: "local/dev surface, no auth"). Exposed via the API — not hardcoded only in
  `ui/minimal/index.html` — so any future non-browser client (or a redesigned UI) reads the same
  single source of truth rather than a second copy drifting out of sync.
- **UI:** two presentations of the same data, both reading the one endpoint:
  1. A dismissible first-run modal — shown once per browser via a **client-side-only**
     `localStorage` flag (`bartholomew_onboarding_seen`), never a server-persisted "has this user
     completed onboarding" field. See Sec 5 for why this is deliberately not new persistence.
  2. An always-reachable "🏠 Deployment Guide" reference card in `ui/minimal/index.html`, same
     card/`empty-state` conventions as every other Stage 1 card, so a user can revisit the guidance
     later without re-triggering the modal.

## 5. Why no new persistence

Every other Stage 1 sub-stage added a governed SQLite table because it tracked *state that must be
auditable and consistent regardless of client* (parking brake, consent decisions, notification
settings, the awaiting_response queue). "Has this particular browser already seen the onboarding
modal" has none of those properties: it is not safety-relevant, not something Governance needs to
reason about, and losing it (e.g., a cleared browser) has zero consequence beyond seeing the modal
again — the reference card is always available regardless. Building a `governance_store`-style
table for it would be exactly the kind of premature schema `CONSTITUTION.md`'s consumer-value gate
warns against. `localStorage` is the correct, minimal mechanism.

## 6. Neutrality enforcement (the exit criterion's actual requirement)

`ROADMAP.md`'s exit criterion is specifically about *not recommending one option as though it were
the only supported one* — not about hiding the hybrid local-first architecture's own preferences
(which are real, approved, and documented in `DECISIONS.md`). The design resolves this distinction
as:

- Every target gets the same structure (advantages + limitations), the same length budget, and no
  target-specific styling (no "recommended" badge, no highlighted/first-position visual treatment
  beyond `ROADMAP.md`'s own listed order).
- Where `DECISIONS.md`'s architectural preference is genuinely relevant (hosted-cloud's tension with
  sovereignty; hybrid's status as the actually-approved direction), it is stated as **fact with its
  reasoning**, not as a marketing steer — the same honest-tradeoffs posture the rest of this
  document already takes. A user who, having read the reasoning, still wants hosted-cloud-style
  convenience is not blocked from that read; they are just not told it's equivalent today when it
  is not (Sec 3).
- No call-to-action language ("get started with X now") anywhere in the content module.

## 7. Open design questions for approval time

1. **Exact first-run trigger condition.** Proposed: `localStorage` flag set on modal dismiss/close,
   checked on every UI page load. Alternative considered: show it based on "kernel has zero
   nudges/reflections yet" (a true first-boot signal) — rejected as the default proposal because it
   couples onboarding-UI state to backend data that exists for an unrelated reason, and would
   re-show the modal on a second browser even after the user dismissed it once elsewhere; still
   worth a final call at approval time if reviewer disagrees.
2. **Content length/tone final pass.** Sec 3's copy is a first draft grounded directly in this
   repository's real capabilities/limits, not final consumer-facing copywriting — wording polish is
   an implementation-time, not a design-blocking, decision.
3. **Whether `available_today` should be enforced by a test** that greps the codebase for
   contradicting claims (e.g., failing CI if `available_today: true` is set on a target whose
   copy references a feature not yet merged) — proposed: not for this sub-stage; a manual review of
   Sec 3 in the approved design plus ordinary code review is deemed sufficient given this is static
   content, not executable capability. Revisit if onboarding content grows or changes frequently
   enough that drift becomes a real risk.

## 8. Non-negotiable invariants (mirrors S1.4's and Phase B's overview docs)

- No implicit authority expansion: approving this design document does not approve its
  implementation.
- No target is presented as the only supported option, nor visually/structurally privileged over
  the others (Sec 6).
- No planned-but-unbuilt capability (cross-device sync, hosted runtime, data export) is described
  as available today (Sec 3's "currently-available-vs-planned" framing).
- No new governed action surface, Parking Brake scope, or Identity.yaml `tool_use.allowlist` entry —
  this sub-stage is purely informational.

## 9. Verify plan (once implementation is separately approved)

```bash
pytest -q tests/test_onboarding_api.py   # GET /api/onboarding/deployment-guide shape, all 5
                                          # targets present, no auth required
```
UI: manual/Playwright check that the first-run modal appears once per fresh `localStorage`, does
not reappear after dismissal, and the reference card renders the same five targets identically
outside the modal.
