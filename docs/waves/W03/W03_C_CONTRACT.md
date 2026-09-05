# W03-C — Governed Windows Action & Reliability

> Authoritative builder contract. Read `docs/waves/W03/README.md` and
> `docs/waves/W03/W03_PREP_ASSESSMENT.md` first.

## Identity

| | |
|---|---|
| Session | **W03-C — Governed Windows Action & Reliability** |
| Immutable id | `W03-C` |
| Branch | `wave/w03-c-governed-windows-action` |
| PR | `[W03-C] Governed Windows Action & Reliability` |
| Handoff | `BARTHOLOMEW_W03_C_HANDOFF.md` |
| Required CI tier | Integration |
| May start | Immediately after W03-PREP |

## Mission

Make Windows actuation genuinely useful **and** reliable through the one canonical
governed action boundary, and close the loop `Act -> Observe result -> Verify ->
Continue/Recover`. Action issuance alone is not success.

The baseline already ships a strong envelope (wave-two package B): eleven ordered
governance checks, an approval bound to six facts, a brake read twice, digest-only
evidence, `unknown` as an honest status, and a real-Win32 test suite. `type_text`
was proven to produce real keystrokes on a live Windows 10 desktop. W03-C owns
that envelope and hardens the two places it stops short of a reliable loop:
**verification** (nothing reads a field back, so `type_text` is
`effect_unverifiable`) and **stop-after-lease** (once `try_lease` succeeds nothing
— server or companion — can abort the action, and a leased-row cancel never
reaches the device).

## Ownership

**Owns (may modify freely):**
- `bartholomew/actuation/` — the envelope: schema, parameters, capabilities,
  seam, approvals, dispatch admission, lease, results, arming, sensitive-content.
- `bartholomew/windows_actuation/` — the companion process, handlers, Win32,
  UIA, allowlists, prohibitions, runner, channel.
- `bartholomew/integration/device_registry.py`,
  `bartholomew/integration/device_action_resolver.py` — the E-backed device and
  capability resolution for actions.
- `bartholomew_api_bridge_v0_1/services/api/routes/actions.py`,
  `routes/device_actions.py` — the request/approve surface and the device lease
  channel.
- `deploy/windows/` — the alpha installer scripts.

**Owns and publishes (shared contract `action-envelope`):** the in-process
callables `run_action_request_through_runtime_contract`, `grant_action_approval`,
`evaluate_dispatch_admission`, `run_action_dispatch_through_runtime_contract`,
`record_action_result_through_runtime_contract`,
`cancel_action_through_runtime_contract`. W03-B and W03-E consume them; only W03-C
changes their signature.

**May consume (do not modify):**
- `observation-event` / the read-back primitive (owned by W03-A) — for the Verify
  step. W03-C calls A's read to confirm a Windows action's effect; it does not
  build its own screen-reading path.
- The Parking Brake / `GovernanceStore` and Identity policy — call, do not fork.

**Must not modify:** multimodal (W03-A); executive (W03-B); memory schema (W03-D);
`app.py` router registration (W03-F).

## Dependencies

- **Required pre-existing interfaces:** the wave-two action envelope, Session E's
  device registry, companion authentication, the arming window, the DB path
  resolver — all on the baseline head.
- **Other W03 sessions:** consumes W03-A's read-back for Verify (coordinate the
  signature early). W03-B consumes W03-C's envelope.
- **Start:** immediately after W03-PREP.

## Explicit non-goals

- **No unrestricted execution.** No arbitrary shell, no arbitrary code execution,
  no destructive or high-risk system access. New capabilities stay within the
  declared vocabulary, each with a risk class and approval requirement; nothing
  becomes autonomy-eligible without an explicit per-device grant.
- **No new path to the OS.** Actuation remains reachable only through the
  authenticated device lease channel calling the seam.
- **No weakening of governance to gain reliability.** The Verify and abort work
  must not remove or relax any of the eleven checks, the approval binding, or the
  digest-only evidence rule.
- **No autonomous arming.** Arming stays an explicit, bounded, human act.

## Acceptance criteria (observable, testable)

1. **Act -> Verify:** after a successful lease and execution, the handler (or the
   server, via A's read-back) observes the resulting Windows state and records a
   verdict distinct from issuance: `type_text` into a known field is confirmed by
   reading the field back where a provider exists, and honestly reports `unknown`
   where it does not — proven on the real-Win32 suite and asserted structurally.
2. **Stop-after-lease:** an engaged Parking Brake (global or `actuation`) aborts
   an in-flight or not-yet-started leased action within a bounded interval — the
   device polls a brake/abort signal between lease and handler (and per step for
   multi-step), the lease response carries an abort deadline, and an aborted
   action records an `aborted_by_brake` status. Proven end-to-end against a fake
   channel.
3. **Envelope integrity:** an executive-generated action (from W03-B) traverses
   the full envelope and **cannot execute without** identity, declared capability,
   validated parameters, risk classification, a clear brake (read at request,
   approval and immediately before lease), an action-bound approval and arming.
4. **No bypass and no second registry:** structural tests keep observation and
   actuation resolvers separate, keep one device truth per deployment, and keep
   `windows_action_dispatch` unreachable without a bound approval regardless of
   the Identity allowlist.
5. **Recovery:** a failed or `unknown` result is distinguishable and drives a
   defined outcome (retry only where idempotent and re-authorized, else surface);
   an abandoned lease sweeps to the honest terminal state.
6. **Operator brake reaches the running server:** all action/brake operator
   commands default `--db` through `bartholomew.kernel.db_paths` and print the
   resolved file; the ten remaining stale-default `--db` commands are migrated.

## Testing

- **Package-local:** capability/parameter validation, prohibitions, the eleven
  checks, approval binding, arming, results/evidence, the new abort path, the new
  verify path.
- **Integration:** request -> refuse-without-approval -> approve -> lease ->
  execute -> verify -> result, and brake-engaged-mid-lease -> aborted, against
  real stores and a fake device channel.
- **Adversarial/governance:** replay the same proposed action under differing
  brake / identity / capability / scope / risk / consent — **policy state, not
  model wording, decides**; a compromised device cannot widen its allowlists or
  put screen content into a durable row.
- **Required CI tier:** Integration (Ubuntu governance/prohibition suites) plus
  the real-Win32 suite in the Windows job; the live `type_text`+verify retest on
  a real desktop is a Wave 3 acceptance target recorded for W03-F.

## Escalation boundary

Stop and report rather than expanding scope if:
- a genuinely useful action seems to need unrestricted shell / arbitrary code /
  destructive access, or a new autonomy-eligible high-risk capability;
- an **out-of-process** emergency stop (S9) is required to make abort trustworthy
  — that is its own deferred package, not W03-C's to invent;
- `focus_window`'s foreground-lock limitation blocks a Golden Path and the only
  fix is synthesising input to force focus (forbidden — report instead).
