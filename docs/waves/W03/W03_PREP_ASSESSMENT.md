# W03-PREP — Factual post-Wave-2 assessment of `main`

Authored by **W03-PREP**. This is the evidence base for the Wave 3 decomposition.
It records what the repository actually contains, distinguishing
**implemented / partial / scaffolded / absent**, and never infers readiness from
the existence of a class, interface or design document.

> **Baseline update (post-merge).** The wave-two baseline is now **merged to
> main**: PR #89 as `68222a3` and PR #90 as `99ee734`. **Builders branch from
> `main` (>= `99ee734`); there is no unmerged-head or pre-merge blocker.** The
> `W2G` label used throughout this section named the wave-two candidate head
> *before* it merged; read every "W2G" / "unmerged PR head" reference below as
> "the wave-two baseline, now on main". The findings themselves (what is
> implemented / partial / scaffolded / absent) are unchanged by the merge — only
> where that code lives changed. The manifest (`W03_MANIFEST.yaml`) and §6 are
> authoritative for baseline and blocker status.

## 0. What "main" actually is, and what Wave 3 builds on

The wave-two product packages — Windows actuation, multimodal presence, the
event backbone, the learning/memory control centre, the device registry,
companion authentication, the consent channel, the arming window — are **now on
`main`** via:

- PR #89 `claude/bartholomew-wave-two-integration-qjrxkr` (integrated packages
  A–E), merged as `68222a3`.
- PR #90 `claude/windows-companion-completion-pkg` (companion authentication, an
  operator-reachable device consent channel, an arming window, and one DB path
  resolver on top of #89), merged as `99ee734`.

This assessment was written against the wave-two candidate head **before** that
merge and refers to it as **W2G**; that head is now the merged `main`. The
subsystem findings are what Wave 3 builds on. The pre-merge framing that
followed here — "the honest baseline is the unmerged PR #90 head" and "the
wave-two baseline must be merged before builders start" — is **historical**:
that blocker is discharged (§6), and builders branch from `main`.

Evidence method: three subsystems (Windows perception, Parking Brake, memory)
were assessed by independent code-reading agents with per-file/line citations;
the remaining subsystems were confirmed by direct reads during W03-PREP
(actuation seam callers, the planner, the intent parsers, the capability tables,
the governance decisions). Adversarial verification of the agents' claims was cut
short by a session rate limit; the load-bearing claims below were re-confirmed by
direct code-path reads rather than left on a single agent's word.

## 1. Implemented / partial / scaffolded / absent matrix

Status is judged against the **W2G** head (what Wave 3 builds on). "impl" = works
end-to-end with tests; "partial" = real code + tests but a material piece is
missing, disconnected, or proven only to a hand-off boundary; "scaffold" =
interfaces/stubs, no working path; "absent" = nothing.

| Capability | Status | The decisive fact |
|---|---|---|
| Windows observation — companion (app name, idle) | partial | Real read-only Win32 probe; emits `device.companion.*` inbound events. Probe methods are `pragma: no cover`, no recorded real-desktop run; W2G backbone actually **refuses** these as `unknown_event_type`. |
| Windows observation — multimodal (screen/accessibility/mic) | partial | Governed, consented, bounded session **lifecycle** exists; but after a session is `ACTIVE` **no production code captures, serializes or emits an observation**. The Observe leg is not wired. |
| Observation vs inference separation | absent | No memory or event record carries observed-event / inferred-state / confidence / competing-explanations / cost / reversibility. Interpretation is keyword text-matching. |
| Windows actuation (governed envelope) | partial | Strong envelope: 11 ordered checks, six-fact approval binding, brake read twice, digest-only evidence, honest `unknown`. `type_text` proven live. **But** reachable only via HTTP; no executive path; `focus_window` fails from a background process. |
| Action verification (Verify leg) | partial→absent | Handlers verify some effects by read-back; `type_text` and `open_url` report `effect_unverifiable` because nothing reads a field back. No general Verify. |
| Stop-after-lease / abort in-flight action | absent | Once `try_lease` succeeds, nothing (server or companion) can abort; cancel of a leased row never reaches the device. |
| Event architecture (durable backbone) | impl | `event_processing` is a durable state machine over `inbound_events` with one drive, idempotency, brake deferral, quarantine. One bus, one authority. |
| Interpreted evidence → executive decision | absent | The only production consumers of interpretation/retrieval are the chat prompt and schedule reminders. Nothing turns interpreted PC state into an action decision. |
| Executive / planner / task orchestration | absent | `Planner.decide()` returns `None`; `handle_skill_request` is a single-step router; intent parsing is regex. No multi-step planning, no decomposition, no path from cognition to actuation. |
| Capability interfaces | partial | Four distinct "capability" notions (skill actions, route capabilities, device capability kinds, Identity allowlist kinds). `CAPABILITY_VERSIONS` vocabulary + risk/approval classes exist for `windows.*`; no programmatic capability-selection API for an executive. |
| Identity / authentication / authorization (S8) | impl | Per-user isolated runtimes/DBs, default-deny route→capability table, opaque sessions, device credential verification, a device can never be a person. Live-tested for auth on the action channel. |
| Parking Brake enforcement | partial | Persisted, revision-guarded, fail-closed, re-read at each seam incl. before lease; W2G adds an `actuation` scope. **But** no abort after lease, `stop_all_for_brake` has no caller, no out-of-process stop, and on `main` the CLI brake wrote to a scratch DB (W2G fixes this; not live-retested). |
| Memory infrastructure | partial | One governed write authority, consent/redaction/encryption, hybrid retrieval. **But** no provenance/validity/supersession/revoked fields; retrieval governance is consent-only; recalled text is concatenated verbatim into the prompt. |
| Learning / correction | partial | Governed experience→candidate→review→accept→retrieve loop with candidate-bound approval; manual acceptance enforced structurally. Accepted competency is retrieved into the chat prompt as **advisory** (gates nothing). One lesson kind (`procedural`). |
| Supersession | partial | First-class only for competency/candidate/policy (key@rN archive). Absent for personal facts, preferences, routines, temporary exceptions. Revocation is hard delete with no tombstone. |
| Multimodal / device-platform | partial | Screen/mic/accessibility session machinery; real backends `pragma: no cover`; no STT ships by default. |
| Cross-user / trusted-group | impl (bounded) | Opt-in, sanitized, content-bound adoption through the same candidate-bound gate; no synchronization; three competency source kinds only. |
| API / UI surfaces a user can use | partial | Chat front-end + routes; **no operator console** — first live use needed hand-written JSON, three terminals, env vars, and raw HTTP to request/approve actions. |
| Retrieval-side memory governance | absent | No read path checks authority, provenance, staleness or revocation. |
| Automatic lesson acceptance | absent (by design) | `execution_mode` is a `Final` `shadow` constant; `learning_accept` has no standing permission; a permissive policy consolidates nothing. Correct and to be preserved. |

## 2. Architectural blockers & findings (that bear on a reliable Windows closed loop)

1. **No executive path to actuation.** The action envelope is imported only by the
   HTTP route; nothing in the kernel can propose an action. Wave 3's central build
   (W03-B) is the in-process, governed, no-bypass path from intention to an action
   proposal. *(Confirmed: `grep` shows `bartholomew.actuation.seam` imported only
   by `routes/actions.py`; `runtime_contract.py` does not import actuation.)*
2. **The Observe and Verify legs are not wired.** A capture session reaches ACTIVE
   and stops; nothing reads a window back after an action. Without these there is
   no loop, only issuance. (W03-A + W03-C.)
3. **Observation collapses into inference.** No record separates "inactive 20 min"
   from "overwhelmed, intervene". The research brief requires this separation;
   it must be built into the observation event (W03-A) and memory (W03-D).
4. **Recalled memory is authority-shaped.** No retrieval-side governance;
   verbatim prompt concatenation; no tombstone; no supersession for the record
   kinds a Windows loop will most often correct. (W03-D.)
5. **The brake stops the next action, not the current one.** No abort-after-lease,
   no out-of-process stop. For consequential local device agency the Post-Test #1
   register requires D11/S9 (independent emergency stop) regardless of network
   locality — undischarged. (W03-C builds abort; the out-of-process stop is
   deferred and named.)
6. **The wave-two work is unmerged.** All of the above lives on PR #89/#90 heads.

## 3. Research implications that materially shaped Wave 3

The supplied research brief changed the plan in five concrete ways:

- **A — Governed action envelope.** The envelope already exists (W2G package B).
  Wave 3 does not reinvent it; it makes it the **one** path and forbids the
  cognitive layer any alternate route to Windows (W03-B's no-bypass test; W03-C
  owns the envelope as a shared contract).
- **B — Retrieved memory is evidence, not authority.** Governance today is
  admission/write-side only. Wave 3 adds retrieval-side governance and an
  instruction/data boundary (W03-D), because the closed loop will recall memory
  into decisions that can move a mouse.
- **C — Supersession first-class.** A correction that only appends a conflicting
  memory is unsafe once memory influences action. W03-D makes supersession and
  revocation-with-tombstone first-class for the record kinds the loop corrects.
- **D — Observation distinct from inference.** Built into W03-A's observation
  event and W03-D's memory record, and verified in W03-F.
- **E — Automatic lesson acceptance stays disabled.** The policy infrastructure
  may be built and tested, but manual acceptance remains authoritative all wave.
  This is also existing project authority (`DECISIONS.md`, learning-acceptance).

## 4. What this means for the provisional A–E decomposition

The provisional A–E survives assessment and is adopted, with boundaries sharpened:

- **A owns the read primitive the Verify leg needs** (read-back), so C does not
  build a second screen-reader.
- **The action envelope is C's shared contract**; B consumes it in-process; B may
  not construct any other OS path. This is the single most important seam of the
  wave.
- **The observation event is A's shared contract** (observed vs inferred); B and D
  consume it.
- **Memory retrieval governance is D's shared contract**; B and E consume it.
- No sixth product package is added. Out-of-process emergency stop, autonomous
  capture, automatic acceptance, and unrestricted PC control are **deferred**
  (see `W03_DEFERRALS.md`), not folded in.

## 5. Current test & CI health (baseline)

- Default suite: `main` 2626 tests, coverage **79.67%** (gate 70%); W2G 4144
  tests. Both green in CI. See `W03_CI_BASELINE.md` for timings and the tiering.
- Two console-script packaging tests fail in the W03-PREP **local venv only**
  (pass in CI) — a local editable-install artifact, not a repo regression.

## 6. Blockers before builders start

1. **Merge the wave-two baseline to main. DISCHARGED.** PR #89 merged as `68222a3`
   and PR #90 as `99ee734`; `main` now carries the whole wave-two baseline.
   Builders branch from `main` (>= `99ee734`) directly. This report's earlier
   "one hard blocker" is resolved.
2. **Confirm the #90 consent-channel and brake-DB repairs on a real desktop.**
   Still open. `docs/H_LIVE_RETEST_HANDOFF.md` step 1 and the post-consent brake
   re-read were never live-retested. Not a blocker to *start* A–E, but a blocker
   to *claim* observation start and brake coverage; W03-F carries the live retest.

With the baseline merged, W03-A, W03-C and W03-D can start immediately in
parallel, W03-B alongside them against the published shared contracts.
