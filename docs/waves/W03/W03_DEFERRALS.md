# W03 — deferral register

Authored by **W03-PREP**. These are important, in some cases strategically
central, capabilities that are **deliberately not in Wave 3**. Wave 3 prioritises
making the existing architecture genuinely useful on one Windows PC. Nothing here
is to be implemented merely because it is important; each needs its own proposal
and, where noted, a director decision or a Post-Test #1 gate.

| # | Deferred | Why deferred / gate | Nearest Wave 3 touchpoint |
|---|---|---|---|
| 1 | Broad home cameras / microphones / speaker presence | Ambient sensing is a Band B gate (register D8, S3–S7, S9–S11, bystander D13). Out of a single-PC closed loop. | W03-A ships screen/accessibility only, consented, bounded. |
| 2 | Full Android / iPhone companions | FUTURE PLATFORM WORK (ROADMAP "what we will not do yet"); needs device-agent protocol + Stage 6 auth/threat model. | none |
| 3 | Full estate / household automation | Beyond one governed PC loop. | none |
| 4 | **Automatic lesson acceptance enabled** | Manual acceptance is authoritative this wave (register + learning-acceptance decision). Policy infra may be built/tested in shadow only. Enabling is a **director decision**. | W03-D builds & tests the policy infra in shadow. |
| 5 | Broad proactive autonomy (acting unprompted) | Wave 3 acts on an explicit user task. Autonomous initiative needs its own governance and gates. | W03-B acts only on an explicit task; uncertainty → clarification. |
| 6 | Autonomous capture-start (observing without a human answer) | The anti-autonomy consent gate is the enforcement point; a permissive auto-approving handler is exactly what must not ship. Director decision. | W03-A keeps every session explicitly consented. |
| 7 | Unrestricted / high-risk PC control (arbitrary shell, code exec, destructive access) | Blast-radius and D10/S9 concerns; the envelope's allowlists exist to bound this. | W03-C stays within the declared capability vocabulary. |
| 8 | Out-of-process independent emergency stop (D11 / S9) | Required for consequential local device agency, but is its own package (OS-level companion stop + a flag the companion checks before each handler). Named, not built. | W03-C builds in-process abort-after-lease; the out-of-process stop is deferred and named as a follow-on package. |
| 9 | macOS / Linux actuation & perception equivalence | Wave 3 is a Windows loop; the probes/handlers are Windows-specific by design. | none |
| 10 | Rich avatar / embodied presentation | Not needed for a usable task loop. | none |
| 11 | Customer-facing UI customization / productization | W03-E ships a minimal operator surface only. | W03-E |
| 12 | Broad external-service ecosystem (capability broker, provider registry/marketplace) | FUTURE PLATFORM WORK (external-providers decision). One provider through the existing seam comes first. | none |
| 13 | Full prospective life simulation | Out of scope. | none |
| 14 | Speech-to-text engine by default | Package C ships no STT; the microphone modality is honest-empty without a supplied backend. | W03-A leaves the mic backend pluggable; not a Wave 3 deliverable. |
| 15 | Widening `LESSON_KINDS` beyond `procedural` | Separately authorised decision. | W03-D keeps the single kind. |
| 16 | Enabling a real deployment's action channel (`BARTH_DEVICE_ACTION_AUTH`) | A deployment/director decision, not a wave deliverable. | W03-F verifies inert-by-default. |

Anything a builder discovers to be important but not required for the Windows
closed loop should be **added to this register via W03-PREP/W03-F**, not silently
pulled into the wave.
