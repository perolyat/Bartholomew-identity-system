# W03-F — Integration & Real-World Test Candidate

> Authoritative integration contract. Read `docs/waves/W03/README.md`,
> `docs/waves/W03/W03_PREP_ASSESSMENT.md` and every builder contract first.

## Identity

| | |
|---|---|
| Session | **W03-F — Integration & Real-World Test Candidate** |
| Immutable id | `W03-F` |
| Branch | `wave/w03-f-integration-real-world-test` |
| PR | `[W03-F] Integration & Real-World Test Candidate` |
| Handoff | `BARTHOLOMEW_W03_F_HANDOFF.md` |
| Required CI tier | Merge Candidate |
| May start | Only after W03-A, W03-B, W03-C, W03-D and W03-E are frozen |

## Mission

The final integration session. Obtain the frozen builder heads, integrate them in
a deliberate order, resolve integration-only interface mismatches, reject
architectural bypasses, run Integration/Merge Candidate verification, and prepare
the Real-World Test candidate. It must **not** become a sixth builder package: it
invents no major functionality A–E omitted.

## Integration order

`W03-D -> W03-A -> W03-C -> W03-B -> W03-E` (also in the manifest). Rationale:
the memory/learning substrate (D) and perception (A) are foundational; actuation
(C) next; the executive (B) ties perception + memory + actuation together through
the envelope; the experience (E) is layered last because it consumes all four.
Verify after each addition, as the wave-two integration did.

## Ownership

**Owns (may modify freely):**
- `bartholomew/integration/install.py`, `bartholomew/integration/seams.py` — the
  installer/seam wiring that composes the builders (router registration in
  `app.py`, resolver installation, event-sink installation).
- `tests/integration/test_w03_integration.py` — the wave integration + governance
  verification suite.
- Integration-only edits elsewhere **only** to reconcile an interface mismatch,
  recorded in the handoff; never a feature addition.

**Must not:** add a Windows capability, a memory field, an executive behaviour, a
Golden Path, or any standing permission that A–E did not build; enable automatic
lesson acceptance; enable a real deployment's action channel on its own authority.

## Responsibilities

1. Obtain and use the **frozen** builder heads (record each SHA in the handoff).
2. Integrate in the order above; verify after each addition.
3. Resolve integration-only interface mismatches (prefer preserving both sides
   additively, as wave-two did); regenerate any generated files with the repo's
   tooling, never by hand.
4. **Reject architectural bypasses:** assert one action envelope, one event
   backbone, one memory authority, one governance authority, one device registry;
   assert the executive reaches the OS only through the envelope.
5. Run Integration and Merge Candidate verification (both Pythons, full Windows
   suite) and get it green.
6. Verify the governance invariants below hold on the integrated head.
7. Exercise the Golden Paths; run the live-desktop retest of the actuation + verify
   loop and the correction-influences-later-task path per
   `docs/H_LIVE_RETEST_HANDOFF.md`, recording observed (not expected) results.
8. Fix integration defects (integration-only, minimal).
9. Prepare the Real-World Test candidate and report final repository state.

## Governance invariants to verify (asserted, not inspected)

- Parking Brake authoritative over the whole integrated path, including
  stop-after-lease abort of an in-flight Windows action (W03-C) and stop of an
  active observation session (W03-A).
- Identity / capability / scope authorization: an action cannot execute without
  identity, declared capability, validated parameters, risk class, approval and
  arming through the host boundary.
- Action-envelope enforcement: the executive has no OS path outside the envelope
  (import/AST assertion).
- Retrieval-side memory governance: recalled memory cannot grant authority, widen
  scope, cross a boundary, or resurrect a revoked grant.
- Supersession/correction: currently-valid state wins over obsolete; history
  preserved.
- Observation vs inference: an observation record never silently asserts an
  inferred human state.
- Manual lesson acceptance remains authoritative; `execution_mode == shadow`; no
  standing `learning_accept` / action-dispatch permission.

## Acceptance criteria

1. Merge Candidate tier green on the integrated head (both Pythons, full Windows
   suite, coverage gate met).
2. Every governance invariant above asserted by a test in
   `tests/integration/test_w03_integration.py`.
3. The Golden Paths run end to end; the live-desktop retest is recorded with
   observed results and an explicit "what this did not establish".
4. No architectural bypass present; the wave exposes one of each authority.
5. A Real-World Test candidate document exists, mapping the wave to the applicable
   Post-Test #1 readiness-band gates (D10/D11/S9 for consequential local device
   agency; S5 direct brake enforcement; S7 truthful state) and stating which are
   discharged and which remain.

## Escalation boundary

Stop and report rather than acting alone if:
- integration reveals a builder shipped a **bypass** (a second authority, an OS
  path outside the envelope, retrieval that grants authority) that cannot be
  reconciled without a feature change — that goes back to the builder, not fixed
  by widening F;
- the live retest shows a governance property fails on real hardware;
- closing the loop would require enabling automatic acceptance, a standing action
  permission, or an out-of-process emergency stop that no package built.
