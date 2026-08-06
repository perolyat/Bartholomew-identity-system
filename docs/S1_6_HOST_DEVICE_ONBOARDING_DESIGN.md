# S1.6 Design — Host-Device Onboarding Guidance

> **Authority note:** this document is the design this sub-stage was implemented against. It is
> subordinate to `ROADMAP.md` (Stage 1's canonical exit criteria) and `docs/STAGE_1_OVERVIEW.md`
> (S1.6's implemented-scope record), and to `DECISIONS.md`'s "Deployment architecture: hybrid
> local-first" entry, which is the approved direction this onboarding content stays consistent
> with.
>
> **Status:** proposed 2026-08-05, revised the same day per reviewer feedback (user-experience
> framing, future upgrade paths, and a "How should I choose?" section — see Sec 4), design
> approved 2026-08-05, **implementation approved and completed 2026-08-05.** See
> `docs/STAGE_1_OVERVIEW.md`'s S1.6 section for what was actually built, including the one
> content-fidelity deviation worth noting (Sec 3/4's prose was adapted for end-user-facing copy —
> internal doc citations like `Identity.yaml`/`CONSTITUTION.md`/section cross-references were
> replaced with plain language while preserving every substantive claim, per Open Question 2's own
> "wording polish is implementation-time, not design-blocking" scoping).

## 1. What this closes

`ROADMAP.md`'s Stage 1 "Also in scope" note: *"host-device onboarding guidance — during setup, show
the user the realistic advantages and limitations of running the trusted local Bartholomew runtime
on a phone, a personal computer, a home server/hub, a hosted cloud service, or a hybrid
local-plus-cloud deployment."* Its exit criterion is specific and testable: *"Host-device onboarding
presents the trade-offs above **without recommending a specific device as though it were the only
supported option**."*

Reviewer feedback on the first draft (2026-08-05) added two requirements this revision incorporates:
the content must describe the *user experience* of each target, not just its technical
architecture, including how a user can migrate later (Sec 3); and it must include practical,
priority-conditional decision support — a "How should I choose?" section (Sec 4) — while the
neutrality requirement above stays intact (Sec 7 explains why those two things do not conflict).

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
  export button exists. This directly bounds Sec 3's "future upgrade path" copy below: migration
  guidance may describe the *intended* path once portability ships, but must not claim a
  one-click migration exists today.
- **No new persistence schema.** See Sec 6 — a client-only "seen this" flag is sufficient; there is
  nothing here worth a governed, audited SQLite table the way S1.4's obligation queue was.

## 3. Deployment target content

Five targets, matching `ROADMAP.md`'s list exactly, in that order (alphabetized would reorder
"cloud" before "computer" and read as arbitrary; the ROADMAP.md order already reads
local-device-first-then-hybrid, which is itself consistent with the hybrid-local-first architecture
without *naming* one as recommended — see Sec 7 for how neutrality is enforced independent of list
order). Each target now carries four parts, matching the reviewer's requested structure: what it
will actually feel like to use, real advantages, real limitations, and the future upgrade path away
from it. None is flagged "recommended" here — that judgment, scoped to be conditional on the user's
own priorities rather than universal, lives only in Sec 4.

**1. Phone**
- *What it will feel like:* Bartholomew lives in your pocket. Notifications, nudges, and
  check-ins arrive the way any other app's would; voice becomes the natural way to talk to it once
  Stage 6 voice adapters exist, closest to `CONSTITUTION.md`'s UX Principle that setup should feel
  like "my phone just became intelligent." Day to day, this is the most *ambient* of the five —
  Bartholomew is simply wherever you already are.
- *Advantages:* Always with the user; natural fit for a voice-primary interface; built-in
  microphone/notification surfaces need no extra hardware.
- *Limitations:* `Identity.yaml`'s local models (`Mistral-7B-Instruct-GGUF-Q4_K_M` primary,
  `Phi-4 3B`/`TinyLlama 1.1B` fallbacks) are real compute and battery/thermal load on a phone; OS
  background-execution limits (iOS/Android both restrict long-running background processes) work
  against an always-on daemon and Stage 5's proactive scheduler drives; on-device storage is the
  most constrained of the five options for a growing memory database.
- *Future upgrade path:* A phone is a reasonable place to start — small footprint, nothing else to
  set up. If storage, battery drain, or background-reliability limits become noticeable, the
  intended path (once Stage 6's cross-device work and `CONSTITUTION.md`'s data-portability
  commitment are both actually built — neither exists yet, see Sec 2) is to move the trusted
  runtime to a personal computer or home server/hub while keeping the phone as a client, without
  losing memory or governance history in the move.

**2. Personal computer**
- *What it will feel like:* Bartholomew runs quietly in the background on the machine you already
  work at, at its most capable — the closest any of the five targets comes to `local_primary`
  running at full quality. It feels less "always in your pocket" and more "always at your desk,"
  ready the moment you sit down.
- *Advantages:* Best local compute-to-battery ratio of the five for running `local_primary` well;
  an always-on daemon is straightforward; most storage headroom of any single-device option; best
  fit for the filesystem/OS integration `Identity.yaml`'s `tool_use.sandbox.filesystem` already
  assumes.
- *Limitations:* Not always with the user — away from the machine, there is no live daemon to
  reach yet (that gap is exactly what Stage 6 cross-device access closes, once its own auth/threat
  model work is separately approved); the user must keep it powered on for Stage 5's scheduled
  check-ins/proactive drives to fire on schedule.
- *Future upgrade path:* Works well as a permanent home for the trusted runtime on its own. If the
  "not always with me" limitation matters, the intended path is the same migration as phone's,
  in reverse framing: once Stage 6 cross-device access ships, this machine can stay the
  authoritative runtime while a phone (or other devices) become reachable clients of it — no need
  to start over.

**3. Home server/hub**
- *What it will feel like:* Bartholomew becomes infrastructure — always on, always reachable in
  principle from every device in the home, the way a home router or NAS already is. Less
  "an app you open" and more "a presence in the house."
- *Advantages:* Always-on without depending on a personal device's battery or the user remembering
  to leave a laptop open; centralizes one authoritative runtime that (once Stage 6 ships) could
  eventually serve multiple client devices from a single trusted source; closest fit to `Identity.
  yaml`'s multi-device ambition ("coordinating skills across devices"); of the five, the one whose
  *day-to-day feel* changes least over time, since it's not competing for the same battery/attention
  as a phone or laptop.
- *Limitations:* Requires the user to own, set up, and maintain a machine that stays powered on —
  a real cost and technical-complexity barrier, not assumed as free; the trusted runtime is now
  physically separate from whatever device the user is actually holding, so — same as personal
  computer, more acutely — reaching it while away depends entirely on Stage 6 work that does not
  exist yet.
- *Future upgrade path:* This is closest to the long-run destination for someone who wants an
  always-on, privacy-preserving runtime and is willing to invest in it — there usually isn't a
  further "upgrade" from here beyond Stage 6 making it reachable from more devices, which is a
  capability arriving *to* this target, not a reason to leave it.

**4. Hosted cloud service**
- *What it will feel like:* From the user's side, likely the lowest-friction of the five — no
  device to dedicate, no daemon to keep running, sign in from anywhere. That convenience is real
  and worth stating plainly (see Sec 4), even though the project's own architecture deliberately
  does not offer this as a first-party option today (see limitations below).
- *Advantages:* No local hardware constraints on inference quality; in principle the easiest
  target for cross-device reachability, since a cloud endpoint doesn't depend on any one physical
  device being on; minimal setup burden for the user.
- *Limitations:* Directly in tension with `DECISIONS.md`'s hybrid local-first decision, which
  explicitly rejected a pure hosted architecture because it would make sensitive memory and
  governance enforcement — including the parking brake and emergency shutdown — dependent on a
  remote service the user does not fully control (`CONSTITUTION.md`'s sovereignty principle and
  independent-emergency-shutdown invariant). **This project does not offer a first-party hosted
  deployment of the trusted runtime itself today** — only optional cloud *model inference* per
  `Identity.yaml`'s `cloud_optional` list. This option must be presented as the project's honest
  architectural position (why full hosting is deliberately not where sensitive state lives), not as
  a fifth interchangeable choice available today.
- *Future upgrade path:* Because no first-party hosted runtime exists, there is no "start here,
  migrate later" path to describe for this target the way there is for the other four — the honest
  framing is that a user who most values pure convenience is, today, better served by hybrid
  local-plus-cloud (Sec 5), which keeps the sovereignty guarantees while still using optional cloud
  services where the user wants them.

**5. Hybrid local-plus-cloud**
- *What it will feel like:* Day to day, indistinguishable from whichever local target (phone,
  computer, or hub) is hosting the runtime — the difference is invisible in normal use and shows up
  only when Bartholomew reaches for a cloud model call the user has explicitly opted into (e.g., a
  harder reasoning task routed to `cloud_optional`), or, once built, when relay/sync makes another
  device usably reachable.
- *Advantages:* This is the architecture `DECISIONS.md` actually approved: a local device or hub
  stays authoritative for sensitive memory, governance, the parking brake, and emergency shutdown
  (independent of network/cloud availability), while optional cloud services — better model
  inference, relay, future synchronisation — are used only where the user explicitly opts in.
  Keeps sovereignty guarantees while allowing better model quality than local-only compute permits.
- *Limitations:* More moving parts to explain than a single-target choice; today "hybrid" concretely
  means "local runtime + optional cloud model calls" (`Identity.yaml`'s `cloud_optional`) — the
  richer relay/multi-device-sync mechanics implied by "hybrid" are Stage 6 scope, not built yet.
- *Future upgrade path:* Not a starting point most users pick first (it presumes an existing local
  target to pair with cloud services), but the natural place any of the other three local targets
  (phone/computer/hub) can grow into once the user wants better model quality or, later, genuine
  multi-device reach — with no loss of the sovereignty guarantees that made the local target worth
  choosing in the first place.

**Currently-available-vs-planned framing (non-negotiable, see Sec 7):** every target's copy —
including the new "future upgrade path" bullets above — must distinguish what exists in this
codebase today (a runnable local daemon + API bridge + optional cloud model calls) from what is
architectural direction not yet implemented (cross-device reachability, data export, a hosted
runtime offering). This is not a phrasing nicety — presenting planned capability as available would
violate `CONSTITUTION.md`'s "no deception about AI identity or capabilities" red line applied to
this project's own onboarding copy. Every "future upgrade path" bullet above already reflects this:
none claims Stage 6 migration or data export exists today.

## 4. "How should I choose?" — priority-conditional guidance

Reviewer-requested addition: practical guidance a user can act on immediately, not just five
parallel descriptions to weigh unaided. Proposed content, directly reflecting the reviewer's
suggested mapping:

| If your priority is... | Consider... | Why |
|---|---|---|
| Convenience and mobility | **Phone** | Always with you; nothing extra to set up or maintain. |
| Everyday desktop use | **Personal computer** | Best local model quality and storage of the single-device options, while you're at it. |
| Privacy and an always-on presence | **Home server/hub** | Stays on independent of any one personal device; closest fit to a fully local, always-reachable setup. |
| Minimal setup effort | **Hosted cloud service** | Lowest friction to get started — with the honest caveat in Sec 3 that this project does not offer a first-party hosted runtime today, only optional cloud model calls from a local target. |
| The long-term ideal, once available | **Hybrid local-plus-cloud** | The architecture `DECISIONS.md` actually approved: local sovereignty plus optional cloud quality — most valuable once Stage 6's relay/sync work lands, but already meaningful today via `cloud_optional` model calls from any local target. |

**This choice is not permanent.** Every target's "future upgrade path" in Sec 3 exists because
migrating later is expected, not a failure to plan ahead — a user who starts on a phone for
convenience and later wants an always-on home-server feel, or wants to add optional cloud model
calls, is not locked into their first answer. This must be stated explicitly and prominently
wherever this table appears (heading copy, not a footnote), both in the UI and in the API response
(Sec 5's `migration_note` field).

## 5. Proposed ownership and shape

No existing `COGNITIVE_RUNTIME.md` ownership-table concept covers "static informational content
about deployment choices" — this is presentation content, not a runtime concept, so it does not need
a new row there. Proposed:

- **Content module:** `bartholomew_api_bridge_v0_1/services/api/onboarding_content.py` — a plain
  Python list of dataclasses/dicts per target: `target_id`, `name`, `feel: str` (Sec 3's "what it
  will feel like"), `advantages: list[str]`, `limitations: list[str]`, `upgrade_path: str`,
  `available_today: bool`. A separate, parallel structure holds Sec 4's priority-conditional
  guidance: a list of `{priority: str, target_id: str, rationale: str}` rows plus one
  module-level `migration_note: str` constant carrying the "this choice is not permanent" copy. No
  database, no I/O. Kept as a Python module rather than a YAML/JSON config file so it is covered by
  the same `ruff`/`black`/review process as the rest of the codebase, matching precedent
  (`bartholomew/config/*.yaml` is for genuinely user-tunable runtime config; this is fixed
  editorial content, closer to a docstring than a setting).
- **API:** `bartholomew_api_bridge_v0_1/services/api/routes/onboarding.py`, one route:
  `GET /api/onboarding/deployment-guide` → `{"targets": [...], "choosing_guide": [...],
  "migration_note": "..."}`. Read-only, no governance implication (no Parking Brake / Identity
  Policy check needed — this is equivalent to a static asset, not a capability), consistent with
  this bridge's existing no-auth posture (`INTERFACES.md`: "local/dev surface, no auth"). Exposed
  via the API — not hardcoded only in `ui/minimal/index.html` — so any future non-browser client
  (or a redesigned UI) reads the same single source of truth rather than a second copy drifting
  out of sync.
- **UI:** two presentations of the same data, both reading the one endpoint:
  1. A dismissible first-run modal — shown once per browser via a **client-side-only**
     `localStorage` flag (`bartholomew_onboarding_seen`), never a server-persisted "has this user
     completed onboarding" field. Leads with Sec 4's "How should I choose?" table (the
     immediately actionable part) with the full per-target detail (Sec 3) available to expand.
     See Sec 6 for why this is deliberately not new persistence.
  2. An always-reachable "🏠 Deployment Guide" reference card in `ui/minimal/index.html`, same
     card/`empty-state` conventions as every other Stage 1 card, so a user can revisit the guidance
     — including re-reading the choosing-guide table if their priorities change — later without
     re-triggering the modal.

## 6. Why no new persistence

Every other Stage 1 sub-stage added a governed SQLite table because it tracked *state that must be
auditable and consistent regardless of client* (parking brake, consent decisions, notification
settings, the awaiting_response queue). "Has this particular browser already seen the onboarding
modal" has none of those properties: it is not safety-relevant, not something Governance needs to
reason about, and losing it (e.g., a cleared browser) has zero consequence beyond seeing the modal
again — the reference card is always available regardless. Building a `governance_store`-style
table for it would be exactly the kind of premature schema `CONSTITUTION.md`'s consumer-value gate
warns against. `localStorage` is the correct, minimal mechanism.

## 7. Neutrality enforcement (the exit criterion's actual requirement)

`ROADMAP.md`'s exit criterion is specifically about *not recommending one option as though it were
the only supported one* — not about hiding the hybrid local-first architecture's own preferences
(which are real, approved, and documented in `DECISIONS.md`), and, per this revision, not in tension
with giving practical decision support. The design resolves this as:

- Every target gets the same structure (feel + advantages + limitations + upgrade path), the same
  length budget, and no target-specific styling in Sec 3 (no "recommended" badge, no
  highlighted/first-position visual treatment beyond `ROADMAP.md`'s own listed order).
- Sec 4's "How should I choose?" table is **priority-conditional, not a single verdict**: it never
  says "choose X"; it says "if your priority is Y, X serves that priority, because Z." Five
  different priorities map to five different targets — by construction, no one target is presented
  as universally correct, and a user whose priority isn't the first one they read is not implicitly
  told they picked wrong.
- Where `DECISIONS.md`'s architectural preference is genuinely relevant (hosted-cloud's tension with
  sovereignty; hybrid's status as the actually-approved direction), it is stated as **fact with its
  reasoning**, not as a marketing steer — the same honest-tradeoffs posture the rest of this
  document already takes. A user who, having read the reasoning, still wants hosted-cloud-style
  convenience is not blocked from that read; they are just not told it's equivalent today when it
  is not (Sec 3).
- The explicit, prominent "this choice is not permanent" framing (Sec 4) further defuses any
  perceived pressure toward a single answer: picking a starting point is deliberately framed as
  low-stakes, not as committing to one option forever.
- No call-to-action language ("get started with X now") anywhere in the content module, including
  in the choosing-guide rationale text.

## 8. Open design questions for approval time

1. **Exact first-run trigger condition.** Proposed: `localStorage` flag set on modal dismiss/close,
   checked on every UI page load. Alternative considered: show it based on "kernel has zero
   nudges/reflections yet" (a true first-boot signal) — rejected as the default proposal because it
   couples onboarding-UI state to backend data that exists for an unrelated reason, and would
   re-show the modal on a second browser even after the user dismissed it once elsewhere; still
   worth a final call at approval time if reviewer disagrees.
2. **Content wording final pass.** Sec 3/4's copy reflects the reviewer's requested structure and
   mapping directly, grounded in this repository's real capabilities/limits, but is still a design
   draft, not final consumer-facing copywriting — wording polish is an implementation-time, not a
   design-blocking, decision.
3. **Whether `available_today` should be enforced by a test** that greps the codebase for
   contradicting claims (e.g., failing CI if `available_today: true` is set on a target whose
   copy references a feature not yet merged) — proposed: not for this sub-stage; a manual review of
   Sec 3 in the approved design plus ordinary code review is deemed sufficient given this is static
   content, not executable capability. Revisit if onboarding content grows or changes frequently
   enough that drift becomes a real risk.

## 9. Non-negotiable invariants (mirrors S1.4's and Phase B's overview docs)

- No implicit authority expansion: approving this design document does not approve its
  implementation.
- No target is presented as the only supported option, nor visually/structurally privileged over
  the others in Sec 3; Sec 4's priority-conditional table maps distinct priorities to distinct
  targets rather than issuing one verdict (Sec 7).
- No planned-but-unbuilt capability (cross-device sync, hosted runtime, data export) is described
  as available today, in either the per-target content (Sec 3) or the future-upgrade-path/migration
  copy (Sec 3, Sec 4's `migration_note`) — Sec 3's "currently-available-vs-planned" framing.
- The "this choice is not permanent" migration framing (Sec 4) must appear prominently, not as a
  footnote, wherever the choosing-guide table is shown.
- No new governed action surface, Parking Brake scope, or Identity.yaml `tool_use.allowlist` entry —
  this sub-stage is purely informational.

## 10. Verify plan (once implementation is separately approved)

```bash
pytest -q tests/test_onboarding_api.py   # GET /api/onboarding/deployment-guide shape: all 5
                                          # targets present with feel/advantages/limitations/
                                          # upgrade_path, choosing_guide covers 5 distinct
                                          # priorities mapped to 5 distinct targets, migration_note
                                          # present and non-empty, no auth required
```
UI: manual/Playwright check that the first-run modal appears once per fresh `localStorage`, does
not reappear after dismissal, leads with the choosing-guide table, and the reference card renders
the same content identically outside the modal.
