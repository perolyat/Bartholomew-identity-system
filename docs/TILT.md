# TILT — Time-to-Real-Use Priority (Usable POC)

> **Status:** Canonical. Added 2026-08-12 as the **14th** canonical SSOT document (see
> `MASTER_PLAN.md`'s "Canonical docs" section and `DECISIONS.md`'s "Canonical SSOT docs" entry).
> This is a deliberate, narrow exception to the general rule that everything under `docs/` is a
> non-authoritative reference — this specific document governs near-term execution sequencing and
> is binding, the same way `ROADMAP.md` and `DECISIONS.md` are.
>
> **Last updated:** 2026-08-20 — **D7 reconciliation added** (documentation-only). One new section,
> "Reconciliation with the Post-Test #1 readiness bands (D7)", records the reconciliation Taylor
> approved on 2026-08-20 as part of Post-Test #1 Decision Register v2.2. **The principle below is
> unchanged and is not weakened**: what is added is the distinction between a narrow attended
> real-use checkpoint and unattended, ambient, or full-Test-#2 activity, which carry stricter
> prerequisites. Nothing here turns TILT into "finish everything before testing", and nothing here
> permits testing past an unresolved safety blocker.
>
> **Previously (2026-08-14):** slice 1 implemented — see "First vertical slice" below; its
> acceptance-bar wording clarified in the same pass, wording only, no behaviour change.
>
> **Previously (2026-08-12):** reconciled into `CONSTITUTION.md`, same day as creation.
> `CONSTITUTION.md`'s "Development Philosophy" section now states this document's principle
> directly (amended 2026-08-12) — this document is no longer a temporary supersession of a
> contradictory Constitution; it is the current operational application of a principle
> `CONSTITUTION.md` itself now states. See `DECISIONS.md`'s "Usable POC / time-to-real-use
> prioritisation" entry for the full rationale and alternatives considered.

## Why this document exists

A repository-grounded assessment (2026-08-12) found that Bartholomew's persistence, governance,
and competency-retrieval machinery are genuinely well-built and well-tested, but that almost none
of it has yet been put in front of real use. Specifically: ordinary conversation writes nothing
durable and retrievable (chat only touches short-term Working Memory and an audit-shaped
Reflection record); the one retrieval-and-selection mechanism that does exist (S5.1–S5.3's
competency architecture) is reachable only through a formal training-ingestion API, not through
anything a real day-to-day conversation would trigger; and the one notification mechanism
(`notify` skill) has no delivery channel outside the browser tab. `CONSTITUTION.md`'s "Development
Philosophy" section, as it read before 2026-08-12, stated the project deliberately spends more
time designing than coding and that correct architecture outweighs rapid feature delivery — a
philosophy that, followed faithfully, produced exactly this outcome.

This document does not repudiate that philosophy's engineering standards. It corrects sequencing:
architecture is proven by putting real things through it, not by hardening it further before
anything real has gone through it. `CONSTITUTION.md`'s "Development Philosophy" section has been
amended (2026-08-12) to state this directly and durably — this document is the tactical detail
underneath that constitutional principle, not a document straining against it.

## The principle (binding for near-term work)

> **Once a vertical slice is sufficiently functional to generate meaningful real-user feedback,
> real-world testing takes priority over additional polish or hardening — unless a defect
> threatens safety, governance, privacy, data integrity, architectural validity, or the validity
> of the experiment itself.**

Those six exceptions are the only legitimate reasons to hold a shippable slice back. "It could be
cleaner," "it doesn't yet handle a case that hasn't occurred," "a future stage might need it
differently," and "the design isn't fully written up" are not on that list.

## Reconciliation with the Post-Test #1 readiness bands (D7)

> **Added 2026-08-20.** Source: Post-Test #1 Decision Register v2.2, **D7** (clauses D7a and D7b),
> approved by Taylor 2026-08-20. The decision is recorded in `DECISIONS.md` as "Real-world testing
> standard — TILT reconciled, not weakened and not abandoned"; the bands it references are defined
> in `ROADMAP.md`'s "Post-Test #1 readiness bands". This section states what the reconciliation
> means **for this document**, and does not restate either of those.

Real-World Test #1 produced findings of a kind this document's six exceptions were always meant to
catch — audit writes that failed silently, a queue that grew itself, sensitive-formatted values
echoed with no defined oracle. What it also showed is that the six exceptions have to be applied
**per test class**, because "this slice is safe enough to try, attended, on localhost" and "this
system is safe enough to run unattended with real sensors" are different questions that can have
different answers on the same day.

**The reconciliation, in one sentence:**

> **Real-world testing remains preferred once a slice is sufficiently useful to test — unless an
> unresolved safety, governance, privacy, data-integrity, architectural-validity, or
> experiment-validity issue blocks *that specific test*.**

That is the same rule as "The principle" above, with "that specific test" made explicit. It is
**not** either of the two failure modes it sits between:

| Not this | Why not |
|---|---|
| **"Finish everything before testing."** | That is the pre-2026-08-12 sequencing failure this document exists to correct. Band 0 exists precisely so a narrow attended checkpoint does not have to wait for ambient or Test #2 readiness. |
| **"Test regardless of unresolved safety blockers."** | The six exceptions are not advisory. A blocker that would invalidate *this* test, or make it unsafe, still stops it — and the stricter classes of testing carry additional prerequisites that are not optional. |

**Both halves are preserved:**

- **Band 0 narrow real-use checkpoints remain allowed and encouraged.** Attended, localhost,
  text-only or similarly narrow slices, under existing Governance and Parking Brake, with no ambient
  sensors, no device-control actuation, no remote/network exposure, and no unattended
  scheduler-driven real-world action — provided the specific slice has no unresolved blocker that
  would invalidate its result. One clarification worth carrying here because it is easy to lose: **if
  the checkpoint is being used to measure burden or usefulness, unresolved queue recursion and
  duplicate behaviour must not contaminate the measurement** — safety gate S1 containment must pass
  first where scheduler/queue behaviour could influence it, or that behaviour must be deliberately
  excluded within the governed envelope and the exclusion recorded.
- **Unattended, ambient, and full Test #2 activity carry stricter prerequisites** — Bands A, B and C
  respectively. `ROADMAP.md` holds them. This document does not restate them and does not soften
  them.

**Post-test direction, recorded as judgement rather than as fact.** Taylor's post-Test #1 direction
is that simply continuing to operate the same low-capability experience is unlikely to yield
proportionate value: **the next meaningful real-use checkpoint should unlock a new useful slice
rather than polish the existing one indefinitely.** That is a judgement about what to build next,
consistent with "Direction for later slices" below — not a change to the principle, and not a
finding from Test #1.

## The prioritisation test (apply to all near-term work)

> **What real Bartholomew capability does this unlock for the tester?**

If the honest answer is primarily "cleaner architecture," "more complete documentation,"
"additional abstraction," "future-proofing," "better theoretical correctness," or "polish,"
that work is deferred until real usage demonstrates the need for it. This does not mean ignoring
genuine blockers, data-corruption risks, security/privacy boundaries, Governance, the Parking
Brake, or defects that would invalidate a test — those remain legitimate reasons to stop and fix
something, per the six exceptions above.

## What this does NOT change

- **Governance remains fully authoritative.** Parking Brake, consent gates, fail-closed defaults,
  audit trails, and the single Governance path are unchanged and unaffected by this document. No
  slice under this track may bypass them.
- **The five-pillar architecture (Governance, Executive, Memory, Capability, Experience) is
  unchanged.** This document is about what gets built next and in what order, not about rebuilding
  or working around the architecture. The repository-grounded assessment behind this document
  specifically found the architecture itself is not the bottleneck — the process wrapped around it
  is.
- **`CONSTITUTION.md`'s engineering standards** (fail-closed safety, privacy-first handling,
  verification-first engineering, one authority per concept, testability, explainability) remain
  in force. This document does not lower them.
- **`CONSTITUTION.md`'s "Development Philosophy" section already states this document's principle
  directly** (amended 2026-08-12) — architecture-first discipline still governs *how* a slice is
  built, narrowed so it no longer holds a slice back from real use once that slice is already
  sufficiently safe and functional to generate meaningful feedback. This document is not a
  competing or overriding document; it is where that constitutional principle's tactical detail
  lives — which slice is next, what real-world testing has priority over, what is deferred — kept
  separate because that content changes with every slice, while the constitutional principle does
  not.

## What "Usable POC" actually means

`docs/S5_2_TRAINING_KNOWLEDGE_ACQUISITION_DESIGN.md` and the S5.1–S5.3 competency work already
proved the retrieval/governance seam works. **The Usable POC is not Personal Memory Capture and
Recall.** That is its *first vertical slice* — chosen because it is the smallest slice that closes
the biggest real gap (no organic memory loop) and is achievable without new architecture. The POC
itself is the progressive demonstration, across slices, of the full loop:

```
real-world information/input
  -> Observation/Interpretation as appropriate
  -> persistent useful understanding/memory
  -> retrieval and reasoning
  -> useful Recommendation/proactive surfacing
  -> user interaction/approval where required
  -> at least one real governed Action
  -> visible real-world result
```

A slice is not required to cover the whole loop. The POC as a whole is not complete, and should
not be treated as complete, until later slices have demonstrated the right-hand side of that
diagram too: proactive surfacing of something noticed, and at least one genuine governed action
with a visible real-world result — not just memory and retrieval. See "Direction for later slices"
below.

## Vertical-slice discipline (applies to every slice, starting with the first)

This is the mechanism that stops the next slice from becoming another S5.3: a real capability
(~600 lines of logic) that shipped behind a ~600-line approved design doc, a decision-log entry,
a roadmap gate, and four separately-reviewed implementation steps.

For each slice:

1. **One right-sized planning note, not a full design-doc-and-decision-ledger cycle.** A slice
   gets exactly one concise note covering what it writes, what it retrieves, what governance/
   consent gate applies, and what "done enough to test" looks like — approved as a single unit.
   The full design-doc/multi-step-approval process remains available for genuinely large or
   safety-sensitive changes, but is not the default for a slice sized to fit this track.
2. **"Done enough to test" is the bar, not "done."** A slice ships once it clears the
   prioritisation test and the six non-negotiable exceptions above are satisfied. It does not need
   to handle every edge case, support every input shape, or be tuned — S5.3's own constants were
   explicitly left provisional pending real usage, which is the right posture and should be the
   default, not the exception.
3. **Ship it, then let real use — not further internal review — decide what's next.** Once a
   slice is in your hands and generating real feedback, further hardening of that slice competes
   with starting the next slice, and generally loses, per the principle above.
4. **This discipline applies to this document too.** If a future amendment to `docs/TILT.md`
   starts accumulating design-doc-level ceremony, that is itself a violation of what this document
   is for.

## First vertical slice: Personal Memory Capture and Recall

**Implemented 2026-08-14** (commit `2d443a9`), per the approved planning note
`docs/POC_SLICE_1_MEMORY_CAPTURE_RECALL.md`. This section describes the slice as scoped; the note
is the detailed record, including what was delivered and its known limitations. Slice 1's
completion does **not** authorise slice 2 — see "Direction for later slices" below.

**What it adds**, all flowing through the existing Runtime Contract seam, no new architecture:

1. A governed write path so ordinary chat content that looks like a durable personal fact (a
   person, a date, a preference, a commitment) can be proposed as a memory write — reusing the
   existing `pending_sensitive_writes` consent flow and `memory_rules.yaml`'s already-defined
   `user_profile`/`birthday`/`user_schedule` categories, not new ones.
2. A retrieval widening so chat's existing competency-retrieval call (in
   `bartholomew/kernel/runtime_contract.py` — `_retrieve_competency_context` as scoped, renamed
   `_retrieve_memory_context` on implementation) also queries those personal-fact kinds, reusing
   `competency_reasoning.py`'s relevance gate as-is.
3. One real notification delivery channel, so a "noticed something" nudge can reach the tester
   outside the browser tab.

**Acceptance bar** (see `DECISIONS.md` and the assessment this document formalises for the full
list): a fact stated in one conversation can be relevantly recalled in a later separate
conversation without the user restating the fact, and without bypassing consent/governance.

> **Wording clarified 2026-08-14** (independent review of the implementation; wording only, no
> behaviour change). This bar previously read "correctly recalled, unprompted, in a later
> *unrelated* conversation." Read literally that asks for a memory to surface in a conversation it
> has nothing to do with — the opposite of correct behaviour, and precisely what S5.3's relevance
> gate exists to prevent. The intent was always a *separate later* conversation in which the fact
> is relevant, without the user restating it. The implementation behaves that way; only the
> wording changed.

## Second vertical slice: Proactive Schedule Reminders

**Implemented 2026-08-25**, per `docs/POC_SLICE_2_PROACTIVE_REMINDERS.md` (approved as planning
and as implementation on the same day, under Taylor's Capability Acceleration Sprint instruction —
a deliberate one-off exception to the sequencing discipline below, which it does not abolish). That
note is the detailed record, including the four approval points as decided and what was actually
delivered against what was predicted.

**What it adds**, all through the existing Runtime Contract seam and existing Governance, with no
new architecture: an opt-in (**default OFF**) scheduler drive notices stored date-bearing
`user_schedule`/birthday facts falling due inside a look-ahead window and surfaces exactly one
reminder per `(fact, due date)` — a WP-A1-contained nudge in the existing queue, plus one governed
`NotifySkill` delivery to the real outbound webhook. That delivery is the slice's Action and its
visible real-world result, and the delivery outcome is recorded on the nudge so the queue
distinguishes *noticed and delivered* from *noticed and not delivered*.

**This is the right-hand side of the loop this document's diagram describes**: proactive surfacing
of something noticed, plus one genuine governed action with a visible real-world result. Slice 1
covered memory and retrieval; between them the two slices have now demonstrated the loop end to
end — which is the condition this document's "Sunset condition" names for revisiting its own
tactical content.

**Still explicitly not authorised by it:** unattended operation. Running the reminder loop
unattended would put a recurring outbound governed action inside Band A's restricted envelope and
needs its own recorded decision. The Band 0 attended checkpoint is what this slice supports.

Delivered alongside it in the same sprint, and *not* part of this slice: conversational task
control (an ordinary sentence now performs a real `TasksSkill` operation through the same governed
chokepoint) and a default-OFF local spoken-output prototype.

## Direction for later slices (deliberately not specified yet)

Per the principle above, slice 2+ scope is **not** designed now — designing it ahead of real
feedback from slice 1 would repeat the exact mistake this document exists to correct. What is
fixed is direction, not content: subsequent slices progress toward proactive surfacing of
something Bartholomew noticed, and at least one genuine governed action with a visible real-world
result — not indefinitely more memory/retrieval refinement. `ROADMAP.md`'s existing S5.4
(experience -> learning/consolidation loop) and S5.5–S5.7 (initiative safety scaffolding, dry-run,
controlled live initiative) already contain real, considered raw material for this — cadence,
consent-to-be-proactive, quiet hours, dry-run rationale logging, a default-deny `allow_proactive`
governance category. That work is **deferred, not discarded**: once slice 1 is in real use and has
generated genuine feedback, the next slice should draw on this existing material, right-sized the
same way slice 1 is, rather than either rebuilding it from scratch or implementing it wholesale
ahead of need.

## What is deferred (not abandoned)

- `ROADMAP.md` Stage 5 **S5.4** (experience -> learning/consolidation loop, as originally scoped),
  **S5.5–S5.7** (initiative safety scaffolding, dry-run, controlled live initiative) — real,
  approved-in-direction work; resequenced to follow slice 1 and be informed by its feedback rather
  than preceding it. Not abandoned; see "Direction for later slices" above.
- Activating a real embeddings model (`sentence-transformers` is currently commented out in
  `requirements.txt`; retrieval runs on a deterministic fallback embedder today) — worth doing
  once there is real content to retrieve; not blocking a first slice that can run on FTS-only
  retrieval.
- A second competency domain (e.g. Vehicle Management) per `ROADMAP.md`'s "Estate Management as
  architecture acceptance test" sequence — correct plan, too early until domain-general personal
  memory (this track) is proven.
- Stage 6 (cross-device, auth, voice) and further persistence/concurrency hardening beyond the
  completed B0–B9 — no evidence yet that either is a real blocker to a single-user, single-device
  real-life test.
- Further competency-selection tuning beyond what S5.3 shipped — its constants are explicitly
  provisional pending real usage; tune later, from real usage, not now.

## Sunset condition

The principle itself is now reconciled into `CONSTITUTION.md` directly (2026-08-12) and is not on
a timer. What should be revisited, once the Usable POC has demonstrated the full loop end-to-end —
memory, retrieval, proactive surfacing, and at least one genuine governed action, all in real use —
is this document's *tactical* content: whether standing slice-by-slice guidance is still needed as
a separate canonical document, or whether ordinary `ROADMAP.md`/`DECISIONS.md` practice can carry
it from that point on. This document should not become a permanent home for detail that has
stopped changing.
