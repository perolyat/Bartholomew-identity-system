# Usable POC — Slice 2 Planning Note: Proactive Schedule Reminders

**Status: DRAFT — awaiting Taylor's approval under the User Approval Gate. Not implemented.**

> One right-sized planning note per `docs/TILT.md`'s vertical-slice discipline: what it notices,
> what it surfaces, which governance gates apply, and what "done enough to test" looks like —
> approved as a single unit. Scoped per Taylor's 2026-08-22 direction: proactive noticing plus one
> governed action with a visible real-world result, deliberately narrow, existing architecture
> wherever possible.

## 0. The slice in one paragraph

Bartholomew already remembers date-bearing facts you tell it in conversation ("car rego is due
June 5" lands on the `user_schedule` memory kind — slice 1). Today nothing ever looks at those
facts unless you ask. This slice adds the noticing: a scheduler drive periodically scans stored
`user_schedule` facts for items falling due soon and, **if you have turned the feature on**,
surfaces exactly one reminder per upcoming item — as a nudge in the queue *and* as a real
notification delivered through the governed NotifySkill path to the outbound webhook, arriving on
a device outside the browser. That delivery **is** this slice's governed action with a visible
real-world result: it runs through `SkillRegistry.execute_action()`'s full Governance pass (parking
brake, skill permissions, Identity policy, WP-A2 truthful audit) and S1.3's quiet-hours/mute rules,
exactly as the two existing notification call sites do.

This is the first time Bartholomew acts on its own memory without being asked. Everything it acts
*through* already exists.

## 1. What it notices (reads)

- **Input:** stored memories of kind `user_schedule` (and `user_profile` key `birthday`) — the
  kinds slice 1 captures onto and `memory_rules.yaml` has always governed. **No new memory kind,
  no schema change, no capture-path change.**
- **How:** a new pure-logic module (`bartholomew/kernel/schedule_noticing.py`, mirroring
  `personal_facts.py`'s discipline: deterministic, no I/O, no LLM) parses the free-text "when" of
  each stored fact at notice time — explicit dates ("June 5", "5/6", "2026-09-01") and a small set
  of relative forms — and selects facts falling due within a **provisional look-ahead window**
  (default 3 days; §7). A fact whose date cannot be parsed is simply not noticed — never guessed,
  never surfaced wrongly, and recall-by-asking is unaffected.
- Reads run inside the drive through the existing `MemoryStore` read path. Consent-gated content
  is structurally out of reach: facts sitting in `pending_sensitive_writes` were never stored, so
  the scan cannot see them — the slice-1 boundary "consent-gated content is never stored, never
  recalled, never quoted in an outbound notification" is inherited, not re-implemented.

## 2. What it surfaces, and the governed action (writes)

Per due fact, exactly two effects, both through existing machinery:

1. **A nudge** in the existing `nudges` queue via WP-A1's contained insert
   (`insert_nudge_contained`), with a new containment-allowlist reason (`schedule_reminder`) whose
   dedup identity is **(fact key, parsed due date)** — so repeated drive firings before you act
   collapse into one unresolved item with an occurrence count, per the approved "bounded by
   identity, not by shedding" decision. Message shape: *"Reminder: <fact text> — due <date>"*.
2. **One real notification** through
   `run_skill_through_runtime_contract(registry, "notify", "send", ...)` — byte-for-byte the shape
   `_notify_fact_captured()` and `_notify_awaiting_response()` already use. That path is the
   governed action: brake (`skills` scope) fail-closed, `nudge.create` permission, Identity
   `tool_use.allowlist` on `"notify"`, quiet-hours/mute **defer** (NotifySkill queues, it does not
   drop), `skill_action_audit` row with WP-A2 degraded-result truthfulness, and outbound webhook
   delivery (`BARTH_NOTIFY_WEBHOOK_URL`) off the event loop. **The visible real-world result is
   the reminder arriving on the tester's device outside the browser.**

**No new notification mechanism, no new write path, no new memory authority, no new table.** The
only schema-adjacent change is one new row *value* (`reason="schedule_reminder"`) in the existing
`nudges` table and one new entry in `containment.py`'s explicit eligibility allowlist.

## 3. Governance, consent, and the register's constraints

- **Default OFF.** Proactive reminding is consent-to-be-proactive (the S5.5 material, right-sized):
  a `config/kernel.yaml` flag (`proactive.schedule_reminders`, default `false`). When off, the
  drive is not registered at all — zero ticks, zero queue impact, zero behaviour change for any
  existing deployment or test. Turning it on is a deliberate operator act.
- **Identity gating, not self-maintenance-exempt.** The drive (`schedule_reminder_check`) follows
  `awaiting_response_check`'s recorded precedent exactly: deliberately **not** in
  `_SELF_MAINTENANCE_DRIVES`, so it requires its own `Identity.yaml` `tool_use.allowlist` entry —
  a reminder is genuine outbound contact about specific user content. One allowlist line, same
  comment discipline as S1.4's.
- **Parking brake:** two independent existing checks, unchanged — the scheduler's own drive-level
  brake check, and `execute_action()`'s `skills`-scope check on the notify call. Engaged brake ⇒
  no scan effects surface and no notification is sent.
- **START-N001 / D2:** the drive can never interrupt merely because the process started — it fires
  only when a stored fact's parsed date is inside the window. Deferral is never silent loss: the
  nudge row is the durable representation; quiet hours defer delivery, never discard it.
- **Register's deliberately-unresolved items:** nothing here freezes nudge caps/rates, P6 targets,
  or surface form. All constants are provisional (§7).

## 4. Not nagging, without losing the obligation

- **While unresolved:** the WP-A1 partial UNIQUE index makes "at most one pending reminder per
  (fact, due date)" a database invariant. Repeat firings bump `occurrence_count`, auditable in
  `nudge_containment_events`.
- **After you act:** acking/dismissing frees the dedup key by design. To avoid re-reminding the
  next tick, the drive skips a (fact, due date) that already has *any* nudge row (any status)
  within the current window — a read-only courtesy check. This is deliberately application-level:
  its failure direction is safe in D2's terms (worst case a reminder you already resolved is
  *not* re-sent; a race's worst case, a duplicate pending row, is prevented by the index). It is
  a courtesy, not the safety invariant — the index remains the invariant.

## 5. Acceptance bar (done enough to test)

**A date-bearing fact stated in conversation days earlier produces, unprompted, exactly one
governed reminder before it falls due — visible in the nudge queue *and* delivered through the
real webhook outside the browser — while quiet hours defer it, mute defers it, an engaged brake
prevents it entirely, and a fresh start with the feature off (or nothing due) produces zero
proactive behaviour of any kind.**

Verified by tests (loopback webhook server, as slice 1's acceptance test did) and then by an
attended **Band 0** checkpoint: localhost, no sensors, no actuation, Governance and brake active,
S1 containment passing (the Band 0 measurement precondition holds). This slice authorises **no
unattended operation**: running the reminder loop unattended would put a recurring outbound
governed action inside Band A's restricted envelope and needs its own recorded decision first.

## 6. Non-goals (explicit)

- **No approve-then-act two-step** (suggestion → user approval → e.g. `tasks.create`). That is the
  full P2 scenario shape and the natural slice 3; doing it here would double the surface area of a
  slice whose point is the first end-to-end proactive loop. This slice's governed action is the
  governed delivery itself. *(Flagged in §8 — Taylor should confirm this scoping.)*
- No LLM/semantic date understanding; no capture-path changes; no new extractor patterns.
- No new UI (the nudge appears in the existing queue UI; the notification in the existing channel).
- No recurring-event logic (birthdays surface via their stored date only as parsed).
- No changes to curiosity/self-check drives, S5.3 retrieval, or anything in WP-A1/A2/A2b.
- No sight/voice involvement — the C6 device-identity split is untouched and is not a blocker.
- The busy-timeout/startup-burst defect is untouched and is not a blocker: the drive's first run
  sits on the normal cadence, not the startup instant; for the Band 0 checkpoint it remains a
  recorded known condition, as already catalogued in `RISKS.md`.

## 7. Provisional constants (POC scaffolding — tuned from real use, never frozen here)

| Constant | Provisional value | Note |
|---|---|---|
| Look-ahead window | 3 days | how far ahead a due date triggers a reminder |
| Drive cadence | `every:3600` (hourly) | coarse on purpose; reminders are day-granular |
| Reminders per tick | cap 3, closest-due first | overflow stays represented as nudges, undelivered ones surface next tick |
| Date patterns | explicit dates + a small relative set | same provisional posture as slice 1's extractor |

## 8. Approval points inside this note

1. **The scope judgement in §6:** governed delivery *is* this slice's governed action; the
   approve-then-act two-step is slice 3. (Alternative: fold the two-step in now — roughly doubles
   scope and adds an approval-inbox surface decision the register leaves open.)
2. **The §4 after-ack semantics:** one reminder per (fact, due date) per window, not re-raised
   after ack. (Alternative: re-remind daily until due — noisier, closer to a task manager.)
3. **The §3 consent placement:** a `config/kernel.yaml` operator flag, default off. (Alternative:
   an `Identity.yaml` field — more visible, but Identity edits are heavier-weight.)

## 9. Expected implementation scope (for the approval, not begun)

| Area | Files | Size |
|---|---|---|
| Noticing logic (pure) | `bartholomew/kernel/schedule_noticing.py` (new) | ~150 |
| Drive + registration | `scheduler/drives.py`, daemon/scheduler startup (conditional on the flag) | ~80 |
| Containment eligibility | `scheduler/containment.py` (one reason + identity fn) | ~25 |
| Config + Identity | `config/kernel.yaml` (flag), `Identity.yaml` (one allowlist entry) | ~10 |
| Tests | `tests/test_schedule_noticing.py`, `tests/test_schedule_reminder_drive.py` (parsing table; default-off; window selection; dedup + after-ack; quiet-hours defer; brake block at both gates; Identity-denied when allowlist entry absent; loopback-webhook end-to-end; WP-A1 soak unaffected) | ~500 |

No canonical document is edited by the implementation; the DECISIONS/ROADMAP records follow as the
usual separate documentation step after delivery.
