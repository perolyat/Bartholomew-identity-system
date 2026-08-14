# Usable POC — Slice 1 Planning Note: Personal Memory Capture and Recall

**Status: implemented, 2026-08-14 — commit `2d443a9`, approved on review.** This note was first
approved as planning/documentation only (2026-08-14); implementation was then approved separately
and explicitly, per `docs/TILT.md`'s vertical-slice discipline ("one right-sized planning note,
not a full design-doc-and-decision-ledger cycle") and `MASTER_PLAN.md`'s Doc Governance rule. See
`ROADMAP.md`'s "Usable POC — progressive vertical slices" section for the completion record and
`DECISIONS.md`'s "Usable POC slice 1 implementation approved" entry.

**Slice 1's completion does not authorise slice 2.** Per `docs/TILT.md`, slice 2 is scoped from
slice 1's *real usage feedback*, not designed ahead of it, and requires its own approval.

Scope is exactly `docs/TILT.md`'s "First vertical slice: Personal Memory Capture and Recall"
section — this note makes that section's three additions concrete enough to implement, decides
the notification channel `docs/TILT.md` left open, and states the acceptance bar and non-goals.
No new architecture, memory kind, consent gate, or governance category is introduced anywhere in
this slice.

## 1. What it writes

A small, deterministic fact-extractor runs on chat `Observation`s (in
`bartholomew/kernel/runtime_contract.py`, alongside the existing `_retrieve_competency_context`
call). It looks for a narrow set of durable personal-fact patterns and, on a match, calls the
existing `MemoryStore.upsert_memory()` unchanged — same rules-engine evaluation
(`memory_rules.yaml`), same `pending_sensitive_writes` consent queue for anything that trips
`ask_before_store`, same `never_store`/`always_keep` handling. No new write path, no bypass.

Recognized fact types map onto the three categories `memory_rules.yaml` already governs (no new
kinds):

| Fact type (examples) | `kind` | `key` |
|---|---|---|
| Birthday ("my birthday is March 3rd") | `user_profile` | `birthday` (matches the existing `key: birthday` always-keep rule, `encrypt: standard`) |
| Identity/preference fact ("my partner's name is Jo", "I prefer window seats") | `user_profile` | slug of the fact subject, e.g. `partner_name`, `seat_preference` |
| Schedule/commitment ("I have a dentist appointment on the 20th") | `user_schedule` | slug of the event, e.g. `dentist_appointment` |

Matching is deterministic (keyword/regex patterns per type — no LLM call, no new dependency),
consistent with S5.3's own "provisional constants, tuned later from real usage" posture. Content
that doesn't match a pattern is left alone; this is not a general-purpose fact extractor and
isn't trying to be.

**This pattern set is explicitly POC scaffolding, not the intended long-term boundary of
Bartholomew's memory-capture capability** (per approval clarification, 2026-08-14). It exists to
get slice 1 to a testable end-to-end loop; broadening what counts as a capturable fact is
real, expected future work, informed by what real use actually surfaces — not scoped or
designed here.

## 2. What it retrieves

`_retrieve_competency_context()` currently filters retrieval to `RetrievalFilters(kinds=list(
COMPETENCY_KINDS))`. This slice widens that filter to also include the two personal-fact kinds
above (`user_profile`, `user_schedule`), so an ordinary chat turn's retrieval call sees them.

`competency_reasoning.select_relevant()` (the lexical relevance gate — shared-term overlap,
confidence floor, ranking, cap) is reused as-is; personal-fact records don't carry the
`CompetencyRecord` schema (`proficiency`, `supervision`, evidence, etc.), so a thin adapter wraps
each retrieved personal-fact row into a `CompetencyCandidate`-shaped object with the fields the
gate actually reads (content for term-matching, no confidence/supervision — treated as always
above the confidence floor, same as `None`-confidence competency records today). Selected facts
render as their own prompt block, separate from the competency block, so the two stay visibly
distinct in the interpretation the Executive sees.

## 3. Notification channel: outbound webhook (decided)

`bartholomew/skills/notify.py`'s `_deliver_notification()` is currently a stub — it only logs,
with a `# TODO: Integrate with system notification APIs` comment. This slice replaces that stub
with a real outbound HTTP POST to a configured webhook URL, using the `requests` dependency
already in `requirements.txt` (call runs through `run_off_loop()`, per the B2/B8
event-loop-isolation discipline the rest of the kernel follows for blocking I/O).

- Configuration: one new setting (env var, e.g. `BARTHOLOMEW_NOTIFY_WEBHOOK_URL`), read at
  `NotifySkill.initialize()`. No new credential storage — a topic-style webhook (e.g. an `ntfy.sh`
  topic URL) needs no secret at all; a generic JSON-POST webhook (e.g. a Slack incoming webhook)
  stores only the URL itself, the same way `deliver_at`/quiet-hours settings are already
  persisted. If unset, delivery falls back to today's log-only behaviour — not a new failure mode.
- **Provider-agnostic by design (per approval clarification, 2026-08-14):** the implementation is
  a plain configurable-URL HTTP POST with no provider-specific code path. `ntfy.sh` is the
  intended first real-world test endpoint, but only as a configuration value the tester supplies —
  it must not become an architectural dependency (no `ntfy`-specific SDK, payload shape, or
  behaviour baked into the delivery code; any endpoint that accepts a POST works equally).
- Trigger: when a fact write from §1 completes (stored immediately) or is queued to
  `pending_sensitive_writes` (needs consent), the extractor calls the existing `notify` skill's
  `send` action with a short message ("Bartholomew stored: your birthday is March 3" /
  "Bartholomew wants to remember something — review in the consent inbox"). This reuses
  `_action_send()`'s existing quiet-hours/mute logic unchanged; only `_deliver_notification()`'s
  body changes.
- This is the slice's only notification behavior. No proactive "noticed something" surfacing is
  built here — that's S5.4–S5.7 territory, explicitly deferred per `docs/TILT.md`.

## 4. Acceptance bar

**A fact stated in one conversation can be relevantly recalled in a later separate conversation
without the user restating the fact** — and without bypassing consent/governance.

> **Wording clarified 2026-08-14** (independent review of the implementation; wording only, no
> behaviour change). This bar previously read "correctly recalled, unprompted, in a later
> *unrelated* conversation." Taken literally that asks for a memory to surface in a conversation
> it has nothing to do with, which would be the opposite of correct behaviour — and is exactly
> what S5.3's relevance gate exists to prevent. The intent was always "a *separate later*
> conversation, without the user restating it", which is what the implementation does and what
> the wording above now says. `docs/TILT.md` carries the same clarification.

Concretely for this note:

- A recognized fact typed in chat is either stored (visible via existing memory inspection) or
  correctly queued to the consent inbox when it matches an `ask_before_store` pattern — never
  silently dropped, never stored bypassing a `never_store`/consent rule.
- In a later, separate conversation where the fact is relevant, it is retrieved and influences the
  response (visible in the rendered personal-fact prompt block) without the user restating it.
- Conversely, a stored fact does **not** surface in a genuinely unrelated conversation — the
  relevance gate holding is part of the bar, not a limitation of it.
- The webhook fires at least once end-to-end (a real HTTP delivery reaches the configured
  endpoint), confirming the channel works outside the browser tab — not just that the code path
  was exercised.

## 5. Non-goals (explicit)

- No new memory kind, consent gate, or governance category — only new callers of existing ones.
- No embeddings model activation (`sentence-transformers` stays commented out; retrieval runs on
  the existing deterministic fallback embedder / FTS path, per `docs/TILT.md`'s "What is
  deferred").
- No proactive/initiative surfacing, cadence, or quiet-hours redesign (S5.4–S5.7 remain deferred).
- No SMTP/email channel, no credential-bearing notification provider — webhook only.
- No tuning of S5.3's relevance-gate constants (`DEFAULT_MIN_SHARED_TERMS`,
  `DEFAULT_CONFIDENCE_FLOOR`, etc.) — provisional, tuned later from real usage per existing
  policy.
- Does not touch open issue #42 (`notify.py`'s 5s `busy_timeout` under observed SQLite
  contention) or #22 (forwarding `IdentityContext` through voice/sight compat wrappers) — both
  stay open, unrelated to this slice's scope.
- Does not extract facts from anything other than the recognized deterministic patterns in §1 —
  not a general NLU/fact-extraction system.

## 6. Resolved at approval (2026-08-14)

Both questions this note originally left open are now resolved by the approval clarification:

1. **Webhook target**: mechanism only, provider-agnostic, no bundled provider — confirmed. `ntfy`
   is the first real-world test endpoint, not a dependency (see §3).
2. **Trigger-phrase/fact-extractor coverage**: the narrow starting pattern set (§1) is confirmed
   as POC scaffolding, explicitly not the long-term boundary of Bartholomew's memory-capture
   capability. Broadening it is real future work, deferred until real slice-1 usage informs it —
   not designed in this note.

Nothing is open — the write path, retrieval widening, and notification mechanism above are final
for this slice as approved.

## 7. As implemented (commit `2d443a9`)

Delivered as planned. Three files changed, three test files added (40 tests):

- `bartholomew/kernel/personal_facts.py` (new) — the deterministic extractor and the retrieval
  adapter, pure data/logic with no I/O, matching `competency_reasoning.py`'s discipline.
- `bartholomew/kernel/runtime_contract.py` — retrieval widened to the personal-fact kinds; capture
  runs inside the governance-allowed branch through `MemoryStore.upsert_memory()` unchanged.
- `bartholomew/skills/notify.py` — `_deliver_notification()`'s log-only stub replaced with a
  provider-agnostic outbound HTTP POST to `BARTHOLOMEW_NOTIFY_WEBHOOK_URL`, run off the event loop.

**One deviation from this note, recorded at review.** §2 said "widens that filter"; the
implementation does widen the single retrieval filter, but then runs **two independent
`select_relevant()` passes** over the retrieved candidates — one for competencies, one for
personal facts — rather than one merged call. Reason: `select_relevant()` commits to a single
domain per selection (S5.3 Decision C), so a merged call would let a recalled fact evict an
applicable competency, or vice versa — a silent S5.3 regression. The relevance gate itself is
reused byte-for-byte in both passes. Pinned by
`tests/test_personal_memory_capture_recall.py::TestCompetencyReasoningUnaffected`.

**Known limitations, accepted as POC scaffolding** (per `docs/TILT.md`: tune from real usage, not
ahead of it):

- The extractor's pattern set is narrow and deliberately provisional (§1).
- The relevance gate does no stemming (inherited from S5.3): "seat" does not match a stored
  "seats". Recall works on content-word overlap.
- `_COMPETENCY_RETRIEVAL_TOP_K` (20) is now shared across both record families; left unchanged so
  S5.3's behaviour stays byte-identical rather than retuned ahead of evidence.
- Facts whose text contains a `privacy_guard` keyword (`name`, `bank`, `account`, …) are
  consent-gated by design, so e.g. "my partner's name is Jo" queues rather than stores.

**Next step is real use, not more slice-1 work.** Per `docs/TILT.md`'s prioritisation principle,
further hardening of this slice now competes with starting slice 2 and generally loses. Slice 2 is
scoped from real feedback and requires its own approval.
