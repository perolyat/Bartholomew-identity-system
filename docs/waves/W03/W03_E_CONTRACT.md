# W03-E — Windows Golden Path Experience

> Authoritative builder contract. Read `docs/waves/W03/README.md` and
> `docs/waves/W03/W03_PREP_ASSESSMENT.md` first.

## Identity

| | |
|---|---|
| Session | **W03-E — Windows Golden Path Experience** |
| Immutable id | `W03-E` |
| Branch | `wave/w03-e-windows-golden-path` |
| PR | `[W03-E] Windows Golden Path Experience` |
| Handoff | `BARTHOLOMEW_W03_E_HANDOFF.md` |
| Required CI tier | Integration |
| May start | After W03-A/B/C/D contract surfaces are stable; scenario harness and operator surface can begin immediately |

## Mission

Make A–D usable as **one coherent Bartholomew experience**. This is not generic
UI polish. Its job is to establish realistic end-to-end Windows scenarios that use
the actual architecture, and to add the **minimum user surface** through which a
non-developer can drive and authorize the loop. The first live test (docs/G §9,
finding 8) recorded that there is *no operator console*: first-time use required
hand-written JSON, three terminals, environment variables, and raw HTTP calls to
request and approve actions. W03-E closes that specific gap and no more.

## Golden Paths (refined against what the repo can realistically support)

Refined from the provisional five against the assessment. Each names the legs it
exercises:

1. **"Open Spotify."** — Interpret -> capability select (`launch_app`) -> envelope
   proposal -> approve -> lease -> execute -> verify (real pid, allowlisted image)
   -> explain. Fully supportable (real-Win32 `launch_app` is proven).
2. **"Find the document I was working on earlier."** — Observe (recent-app /
   accessibility observation) + memory retrieval -> answer with provenance; a
   read-only path that needs no actuation. Supportable as a perception+memory
   scenario.
3. **"Move that file into my Bartholomew folder."** — Interpret -> resolve target
   from observation -> `open_path`/file-navigation envelope proposal -> approve ->
   execute -> verify. Supportable within allowlisted filesystem roots; **defer**
   any capability not shipped by W03-C.
4. **Correction influences a later task.** — User corrects Bartholomew mid-task;
   the correction becomes a candidate lesson, is explicitly approved, supersedes
   the prior behaviour, and changes a *later* relevant task. Exercises the whole
   D loop end to end. The wave's most important longitudinal scenario.
5. **"Go back to what I was working on before lunch."** — Observe/idle history +
   memory + supersession-aware retrieval -> reconstruct and offer to restore
   focus/apps. Supportable as far as `focus_window` allows; where the Windows
   foreground lock blocks it (assessment), the honest experience is "here is what
   you were doing" plus an operator-confirmed focus, not synthesised input.

`type_text` into a focused field is the proven live actuation; scenarios that
require Bartholomew to *establish* foreground focus itself must degrade honestly
(operator focuses; Bartholomew says so) rather than force it.

## Ownership

**Owns (may modify freely):**
- `bartholomew/cli_operator.py` — a **new** operator surface: list pending
  actions, show an action's canonical parameters, approve/deny, arm/disarm, brake
  on/off/status (delegating to `db_paths`), consent list/answer — one coherent
  console for a non-developer.
- `bartholomew_api_bridge_v0_1/services/api/routes/operator.py` — the authenticated
  routes that surface backs, if a route is needed beyond existing ones.
- `tests/golden_path/` — the end-to-end scenario suite.

**May consume (do not modify):** every A–D surface through its published contract;
existing routes (`actions`, `device_consent`, `multimodal`, `learning`).

**Must not modify:** the builders' owned modules; `app.py` router registration
(W03-F).

## Dependencies

- **Other W03 sessions:** consumes W03-A, W03-B, W03-C, W03-D. The operator
  surface and scenario harness can begin against published contracts; the passing
  end-to-end scenarios need those heads working.
- **Start:** after A/B/C/D contracts are stable; **freeze last among builders.**

## Explicit non-goals

- **No new product capability.** W03-E wires and surfaces; it does not add a
  Windows capability, a memory field, or an executive behaviour that A–D did not
  build. If a Golden Path needs one, that is an escalation, not a quiet addition.
- **No customer-facing UI theming / productization** (deferred).
- **No web UI build-out** beyond the minimum operator surface; the console may be
  CLI-first.

## Acceptance criteria (observable, testable)

1. A non-developer can, through the operator surface alone (no hand-written JSON,
   no raw HTTP), see a pending action's real parameters, approve or deny it, arm
   the channel, and engage the brake against the running server — proven by a test
   driving the surface, not the internal APIs.
2. Golden Paths 1, 2 and 4 pass end to end against real stores to the lease
   boundary (path 1), fully read-only (path 2), and across two tasks (path 4:
   correction approved then applied later). Paths 3 and 5 pass to the extent
   W03-C's shipped capabilities and the foreground-lock reality allow, with any
   shortfall asserted as a named stop, not omitted.
3. Every Golden Path exercises the real architecture: the action envelope, the
   observation event, the retrieval verdict, the Parking Brake, and manual lesson
   acceptance — no scenario mocks a governance authority.
4. Each scenario ends with a truthful outcome explanation; a `unknown`/
   `effect_unverifiable` result is never presented as success.

## Testing

- **Package-local:** operator surface commands; scenario fixtures.
- **Integration:** the Golden Path suite against real control-plane + kernel
  stores.
- **Adversarial/governance:** path 4 proves an *unapproved* correction does not
  influence the later task; the brake engaged during a Golden Path halts it.
- **Required CI tier:** Integration; the live-desktop run of paths 1 and 4 is a
  W03-F real-world-test acceptance target.

## Escalation boundary

Stop and report rather than expanding scope if a Golden Path cannot be delivered
without a new capability, a new memory field, a new executive behaviour, an
autonomous-capture or standing-action permission, or synthesising input to force
window focus. Report the shortfall; do not widen A–D from inside W03-E.
