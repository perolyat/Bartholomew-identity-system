# Golden Path — Slice 2 Planning Note: Objective Continuity

**Status: APPROVED and IMPLEMENTED.**

> Approved 2026-08-27 by Taylor, under the User Approval Gate, in response to the slice proposal
> presented at the start of that session. This note is the one right-sized planning note
> `docs/TILT.md`'s vertical-slice discipline calls for — not a design document, and not a second
> authority on anything.
>
> Authority above this note: `DECISIONS.md`'s "Bartholomew is the persistent executive above an
> ecosystem of external intelligence and capability providers" (2026-08-27, PR #69);
> `CONSTITUTION.md`; `COGNITIVE_RUNTIME.md`'s ownership table; and `docs/TILT.md`, which remains
> the near-term sequencing authority. Slice 1 is
> `docs/GOLDEN_PATH_SLICE_1_EXTERNAL_FORECAST.md`.

## 0. The slice in one paragraph

Slice 1 proved Bartholomew can *use* an external capability while remaining the governed Executive
above it. This slice begins proving it can *remain responsible for an outcome* while capabilities
come and go underneath. The move is from `prompt → capability → answer` toward
`objective → evidence → reassessment → follow-up → outcome`. A user says "the roofer needs to come
this week" once; Bartholomew keeps the objective, survives its own restart, records what actually
happens around it, raises it unprompted with what has changed since it last mentioned it, records
the outcome when it is done — and then goes permanently quiet. The strategic test throughout was
**"does this reduce the remembering, prompting and project-management the human has to do?"**,
because Real-World Test #1 found Bartholomew imposing more burden than it removed.

## 1. What it adds

1. **`bartholomew/kernel/objective_store.py`** — objectives and an append-only, classified event
   history, in the existing database through the existing connection authority. Not a new
   persistence subsystem.
2. **`bartholomew/kernel/objective_intents.py`** — the pure recogniser and renderers, holding to
   `task_intents.py`/`forecast_intents.py` discipline: no I/O, no clock read, today passed in.
3. **`bartholomew/kernel/runtime_contract.py`** — one governed seam
   (`run_objective_through_runtime_contract`), one dispatch entry, one Interpretation-stage block.
4. **`bartholomew/kernel/scheduler/drives.py`** — one re-engagement drive, conditionally
   registered, default OFF.
5. **`Identity.yaml`** — seven transition kinds and one drive task_id, **appended**.
6. **`config/kernel.yaml`** — `proactive.objective_continuity`, default `false`.

## 2. Why an objective is a third thing

Deliberately not folded into either existing structure, because their semantics are materially
different:

| | what it is | what its completion means |
|---|---|---|
| **task** (`skills/tasks.py`) | a unit of work with a due date | that piece of work is done |
| **awaiting_response** | an obligation waiting on someone's reply, with an escalation ladder | the reply arrived |
| **objective** | a desired outcome, with a horizon and a history | **stop caring** |

"Ring the roofer" is a task. It carries no record of what doing it was *for*, and completing it
says nothing about whether the roof got fixed. What is reused is these modules' *discipline* — the
same `db_ctx.connect()` + `set_wal_pragmas()` authority, the same `BEGIN IMMEDIATE`
state-change-and-its-event-row-in-one-transaction, the same off-loop rule, the same governed-seam
shape, the same single Reflection sink.

## 3. The event log, and keeping the hypothetical hypothetical

`objective_events` rows are classified by `event_kind`, **CHECK-constrained by the schema** rather
than by convention: `fact` (evidence, carrying provenance), `decision`, `action`, `proposal`,
`state_change`, `surfaced`.

`evidence_events()` excludes `proposal` **in the query itself**, not by a filter each caller has to
remember, and the renderer excludes it again. Bartholomew may reason about what it *could* do next
and record that; there is no path by which such a row becomes evidence that something is true or
that something was done. A considered idea and a completed action must never share a bullet — once
they do, a reader cannot tell them apart.

`record()` also refuses to write a free-standing `state_change`: those belong to the transitions
that cause them, in the same transaction, so the history cannot claim a lifecycle change the
objective never underwent.

## 4. "What changed since last time"

Derived from the event rows on every render, never stored. A kept summary is a fabrication the
moment the events move on — and a fabrication that reads exactly like a real one.

The window is **by event id**, not timestamp. `occurred_at` is second-granular and one turn can
easily record evidence and surface an objective inside the same tick, where a timestamp window
silently drops a real change or repeats one already reported. Both failures read to the user as
Bartholomew being unreliable about their own history. `surface()` writes `last_surfaced_at`,
`last_surfaced_event_id` and the `surfaced` event in one transaction, so the window cannot drift
from the record of when last time was.

## 5. Completion, and three independent stops

The promise is that a completed objective goes quiet **permanently, without the user having to say
so a second time**. It is enforced in three unrelated places, each with its own test:

1. **The store refuses.** `record`, `surface`, `block`, `unblock`, `complete` and `abandon` all
   raise `InvalidTransitionError` against a terminal objective, checked inside the write
   transaction so the check and the write cannot be separated by a race.
2. **The drive cannot see it.** It reads `list_live()`, which cannot return a terminal objective.
3. **The prompt cannot mention it.** The Interpretation block lists live objectives only.

Three, because a completed objective that keeps resurfacing is the single worst outcome this slice
can produce, and one filter someone later forgets is not enough.

`abandoned` is exactly as terminal as `completed`; `no_longer_needed` is recorded as a real and
non-failing ending. All three stop resurfacing identically — the distinction is for the record.

## 6. How re-engagement works

`drive_objective_continuity_check`, registered only when `proactive.objective_continuity` is true
(default false — off means **zero ticks**, not a registered drive that decides to do nothing).

Two independent grounds, and no others: the horizon is inside the look-ahead window, or the
objective has gone quiet longer than the quiet interval (measured from the last surfacing, or from
opening if never surfaced — so a brand-new objective is not raised back at the user in the same
breath they established it). **Evidence arriving is deliberately not a ground.** Something being
learned is not by itself a reason to interrupt someone.

Delivery is `run_skill_through_runtime_contract(registry, "notify", "send", ...)` — byte-for-byte
the shape `_deliver_reminder()` already uses, so `SkillRegistry.execute_action()`'s own Governance
pass and `NotifySkill`'s quiet-hours/mute rules apply unchanged. No second notification mechanism.

**Containment, and where an objective differs from a schedule reminder.** Nudges are keyed through
the existing `containment.dedup_key_for()` with an explicit identity. A reminder's obligation is
"(this fact, this due date)" — inherently one-shot, so `nudge_exists_for_dedup_key()`'s
any-row-ever semantics fit it. An objective is not one-shot; re-engaging after the quiet interval
is the drive's entire purpose. The identity is therefore `objective:<id>:<last surfacing>`: two
ticks inside one round collapse to a single unresolved item (NUDGE-F001 prevented), while the next
round — reachable only by actually surfacing, which advances the id — is a genuinely new
obligation. A fixed per-objective key would have meant Bartholomew raised each objective exactly
once, ever, then went quiet on live work.

The objective is marked surfaced through the governed seam **before** delivery, so a failed
delivery cannot leave it looking un-raised and get raised again next tick.

## 7. How governance applies

Every gate already existed; none was added, weakened or bypassed.

| Gate | Where | Effect |
|---|---|---|
| Parking Brake (`skills`) | `run_objective_through_runtime_contract`, fail-closed | Engaged ⇒ **zero writes**. Objective state is governed state; a braked Bartholomew does not quietly keep bookkeeping. |
| Identity policy | same, on `objective_<transition>` | Evaluated per transition, so permission to record an objective is not permission to close one. Not self-maintenance-exempt: an objective is specific user content. |
| Reflection | same | One `ActionReflection` per transition into the single shared Memory sink, for every outcome including denials. |
| Notification gates | `SkillRegistry.execute_action()` + `NotifySkill` | Unchanged: brake, `nudge.create`, `notify` allowlisting, quiet hours, mute. |

**An objective existing authorises nothing.** This slice governs the *recording* of what the user
wants and what has happened around it. It sends nothing, spends nothing, discloses nothing and
reaches no external provider — the only outbound act anywhere in it is the re-engagement
notification to the user, through the path that already existed, off by default. Remembering "get
the roof repaired" does not become permission to email a roofer; every such action remains a
separately governed action with its own gates. This is pinned by test, not merely asserted here.

## 8. How the forecast contributes evidence without becoming the Executive

When a chat turn's forecast lookup **succeeds** and exactly **one** live objective is matched by
the pure recogniser, the turn records a `fact` event carrying the provider's own provenance block
verbatim, including `evidence: True` and the disclosure record. The forecast skill is unchanged,
still runs its own gates, and has no knowledge that objectives exist: the Executive matched the
utterance and filed the evidence.

Bounded deliberately — a failed or denied lookup is not evidence of anything; ambiguity records
nothing, because a fact filed against the wrong objective is read back later as if it belonged
there; and the attachment happens after the reply is settled and can never change what the user is
told this turn. Continuity is a property of the *next* interaction.

## 9. Recognition, and why it is conservative

No model-based objective extraction. A model asked "is this an objective?" will say yes to a
passing remark, and the cost of a false positive is not a wrong answer — it is a durable record
that then interrupts the user about something they never asked for, which is precisely the burden
Test #1 found. A missed objective costs one restated sentence. Those costs are not symmetric.

Questions and musings ("should I…", "maybe I…", "I was thinking…") are explicitly refused.
Completion and abandonment are checked **before** establishment, so the sentence that ends an
objective can never create a fresh one to nag about. "This week" stays `this_week` rather than
becoming a Friday the user never named.

The list-recogniser was narrowed during implementation: "what's on my plate?" and "what's
outstanding?" read equally as questions about *tasks*, and claiming them stole the turn from task
control and stopped an ordinary question reaching the model at all. `test_api_chat_runtime_contract`
caught it; the narrowing is now pinned by its own test.

## 10. The enabling refactor

`run_chat_through_runtime_contract()`'s dispatch was a nested `if/else` chain — task, then
forecast, then model fall-through — in which order was encoded in indentation. `_CHAT_DISPATCH`
states it once as an ordered tuple of `(name, handler)` pairs; first non-`None` claim wins; model
fall-through unchanged. Landed as its own commit before any feature work, behaviour-preserving,
with the existing task/forecast/chat-seam suites passing untouched.

Deliberately a table and not a framework: no registration API, no discovery, no priority
negotiation, and nothing outside the module can add an entry. Objective recognition is last,
because it is the broadest of the three.

## 11. Done enough to test

- One ordinary sentence establishes a durable objective; the process that heard it goes away
  entirely and the objective is still there.
- A later chat turn knows the objective without the user restating it.
- Evidence, decisions and actions are recorded and classified; a proposal is recorded and can
  never be read or rendered as any of them.
- The drive raises it unprompted, with what has changed since it last mentioned it, once — not
  twice — and the second round reports only what is new.
- The user says "the roofer is sorted"; the outcome is recorded, and it never comes back.
- The brake, an Identity denial and a fresh start with the flag off each produce zero writes and
  zero notifications.

## 12. What is verified, and what is not

**Verified** against a real `MemoryStore`, a real `SkillRegistry` running the real `NotifySkill`, a
real SQLite nudge queue and a real loopback HTTP endpoint — the "not a mock" posture slices 1 and 2
of the Usable POC set, for the same reason: a mocked delivery proves only that the code called
itself. See `tests/test_objective_continuity.py` (end-to-end, including the whole Golden Path in
one scenario), `tests/test_objective_chat_seam.py`, `tests/test_objective_store.py`,
`tests/test_objective_intents.py` and `tests/test_chat_dispatch_table.py`.

**NOT verified: live proactive re-engagement over real elapsed time.** The suite ages objectives by
moving their stored timestamps rather than waiting days, and the drive is exercised directly rather
than through a long-running scheduler. What that cannot prove is the behaviour over a real
multi-day window in a continuously running deployment — which also depends on Session D's
always-on work. Until the procedure below has been run, that is **untested**, and nothing in this
repository should say otherwise.

### Local verification procedure

```bash
pip install -e . -r requirements-dev.txt
```

Then set `proactive.objective_continuity: true` and a short
`proactive.objective_quiet_interval_s` in `config/kernel.yaml`, run
`uvicorn app:app --port 5173`, and in the UI:

1. Say **"the roofer needs to come this week"** → expect Bartholomew to say it will keep track.
2. Restart the process. Say **"what am I working on?"** → expect the objective, from disk.
3. Wait past the quiet interval → expect exactly one notification carrying the objective and its
   history, and no second one on the following ticks.
4. Say **"the roofer is sorted"** → expect it recorded, and then **nothing further, ever**.
5. Run `python -m bartholomew.cli brake on` at step 3 → expect no notification and no state change.

## 13. Cross-stream dependencies

- **Session B (auth/multi-user):** `objectives.subject_ref` exists, is nullable and is written by
  nothing. Single-user semantics are assumed; no identity model is inferred or enforced here.
  Attribution can land later without a migration. **B owns that decision, not this slice.**
- **Session C (retrieval):** none. No embedder, no retriever, no new memory kind, no change to the
  retrieval stack.
- **Session D (always-on):** the drive only fires while a process runs, so real-world proactive
  value depends on D's work. The flag ships **off** and the suite drives ticks directly, so this
  slice's evidence does not depend on D.

## 14. What this slice deliberately does not do

No autonomous planning, no goal decomposition, no next-action selection, no email, calendar,
search, auth, tenancy or deployment. No second notification path. No promotion of objective
content into durable memory beyond the objective's own history. No model-based objective
extraction. No merging with `ExperienceKernel`'s active goals — that is process-lifetime
`list[str]` state with no outcome, history or completion semantics, and conflating the two would
make "complete this" ambiguous between two stores with different rules.

Each of those is either outside what was approved, or complexity real use has not yet earned —
which is `docs/TILT.md`'s whole point.
