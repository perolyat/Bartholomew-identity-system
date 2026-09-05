# W03-A — Live Windows Perception & State Evidence

> Authoritative builder contract. Read `docs/waves/W03/README.md` (inherited rules)
> and `docs/waves/W03/W03_PREP_ASSESSMENT.md` (baseline) first.

## Identity

| | |
|---|---|
| Session | **W03-A — Live Windows Perception & State Evidence** |
| Immutable id | `W03-A` |
| Branch | `wave/w03-a-live-windows-perception` |
| PR | `[W03-A] Live Windows Perception & State Evidence` |
| Handoff | `BARTHOLOMEW_W03_A_HANDOFF.md` |
| Required CI tier | Integration |
| May start | Immediately after W03-PREP (owns disjoint code) |

## Mission

Create the reliable **governed observation** side of the Windows closed loop: the
**Observe** leg, and the read primitive the **Verify** leg consumes. Turn the
wave-two multimodal package — which today reaches a bounded, consented, ACTIVE
session but then does nothing — into a running observation loop that captures a
narrow, governed slice of PC state, classifies observation separately from
inference, and emits it as an evidence event onto the canonical backbone.

The post-Wave-2 reality this fixes (see assessment §Windows perception): a screen
session becomes `ACTIVE` and then **no production code ever calls
`capture_with_fallback` / `observe_active_window`, serializes an observation, or
submits it to the sink**; the kernel receives `element_count`, not the elements;
interpretation is keyword text matching with no confidence or competing
hypotheses; and nothing can read a window's text back after an action.

## Ownership

**Owns (may modify freely):**
- `bartholomew/multimodal/` — the capture session lifecycle, the active-session
  observation loop (new), screen/accessibility/microphone backends, the
  observation vocabulary, privacy/retention classification, the event sink, and
  the operator device-consent channel.
- `bartholomew/integration/multimodal_events.py` — serialization of observations
  into the canonical ingress.
- `bartholomew_api_bridge_v0_1/services/api/routes/multimodal.py` and
  `routes/device_consent.py` — the authenticated capture-start surface and the
  consent answer/list surface.

**Owns and publishes (shared contract `observation-event`):** the observed-vs-
inferred observation event envelope. W03-B, W03-D, W03-E and W03-F consume it;
only W03-A changes its shape.

**May consume (do not modify):**
- `bartholomew/kernel/event_processing/*` and the inbound backbone (W2G package A) —
  register observation event types and emit into it; do not fork it.
- `bartholomew/kernel/runtime_contract.py` observation/consent/brake seams —
  call `run_multimodal_session_through_runtime_contract` and the consent gate; do
  not add a second governance authority.
- The governed memory write path (`MemoryStore.upsert_memory`, owned by W03-D) for
  persisting an observation as evidence — call it, do not edit it.

**Must not modify:** `bartholomew/actuation/`, `bartholomew/windows_actuation/`
(W03-C); memory schema/retrieval internals (W03-D); `bartholomew/executive/`
(W03-B); `app.py` router registration (W03-F integration-only).

## Dependencies

- **Required pre-existing interfaces:** the wave-two multimodal package, the
  event backbone, the device-consent channel, and Session E's device registry
  (all on the baseline head).
- **Other W03 sessions:** none to start. W03-C consumes W03-A's read-back
  primitive for its Verify step; coordinate the read-back signature early.
- **Start:** immediately after W03-PREP.
- **Freeze:** independent; freeze for W03-F once acceptance criteria pass.

## Explicit non-goals

- **No Windows actuation.** W03-A never launches, focuses, types, clicks or
  writes. It reads only. Any actuation is W03-C through the action envelope.
- **No autonomous capture policy.** Every session is still started by an explicit
  request and an interactive consent answer. W03-A does not add a rule that starts
  observation on its own; that remains deferred (see `W03_DEFERRALS.md`).
- **No interpretation-to-decision.** W03-A records observations and a bounded,
  clearly-marked inference with confidence; it does not decide to act on them.
  Deciding is W03-B.
- **No raw retention.** No raw audio, no raw image, no window titles beyond the
  governed vocabulary, persisted anywhere.

## Acceptance criteria (observable, testable)

1. An ACTIVE screen/accessibility session runs an observation loop that, on each
   tick, produces an `ObservationEvent` carrying **distinct** fields:
   `observed_event`, `inferred_state` (nullable), `confidence` (nullable),
   `competing_explanations` (list), and provenance (source, occurred_at,
   captured_at, digest) — proven by a test asserting the record can represent
   `"the user has been inactive for 20 minutes"` **without** asserting
   `"the user is overwhelmed"`.
2. The observation event is emitted into the canonical backbone and is claimable
   by exactly one consumer; a retry collapses to one logical row (content-derived
   id). No second event bus is introduced (registry assertion).
3. The kernel receives the observation **content** (bounded accessibility text /
   screen description), not merely `element_count`.
4. A governed **read-back** primitive exists: given a window/target, W03-A returns
   the current UI-state text (or an honest "unavailable") through the same
   consent/brake/identity governance as any observation. Exposed as the
   `observation-event` shared contract's read query for W03-C to consume.
5. Every capture path passes the existing governance order (tenant, device
   capability, Parking Brake read, Identity policy, explicit consent, scope/
   duration) before anything is touched; an engaged brake — global or a relevant
   scope — stops an in-flight session within a bounded interval (wire
   `stop_all_for_brake`, which currently has no production caller).
6. Privacy/retention holds: raw audio and raw images are never persisted;
   secret-shaped content is refused or redacted with the redaction recorded;
   truncation is recorded, never silent.
7. Provenance: an observation whose envelope `tenant_id` disagrees with the
   process binding is refused, not re-attributed.

## Testing

- **Package-local:** observation-loop tick shape; observed-vs-inferred separation
  (the non-collapse test); privacy/retention; consent/brake gating; read-back
  primitive availability and its "unavailable" honesty.
- **Integration:** an observation flows capture -> serialize -> backbone ->
  claimable, against real stores; brake engaged mid-session ends it within bound.
- **Adversarial/governance:** a poisoned accessibility label carrying imperative
  text is stored as observation content and does **not** change any downstream
  kind or authority (coordinate the assertion with W03-D); a session started with
  a `model:`/`companion:`/`event:` principal is refused.
- **Required CI tier:** Integration. Real UIA/mss backends stay `pragma: no
  cover` and are exercised only in the Nightly Windows tier / live retest.

## Escalation boundary

Stop and report to W03-PREP/W03-F rather than deciding alone if:
- closing the loop appears to require an **autonomous capture-start policy**
  (starting observation without a human answer) — that is deferred and is a
  director decision;
- the read-back primitive would need to read arbitrary screen pixels or window
  contents beyond the consented scope to be useful for Verify;
- the observation event schema needs a field that other builders' contracts do
  not anticipate (change the shared contract via W03-PREP, not unilaterally).
