# Session handoff — 2026-08-22

> Working note for whoever picks this up next (human or agent). Non-canonical.
> Delete or overwrite freely; it describes a moment, not a decision.
>
> Supersedes the 2026-08-15 handoff of the same name (preserved in git history at `54431ba` and
> earlier). Everything in that note about the reflection LLM path being dead, the headless-keystore
> construction crash, and its three "known-inaccurate canonical text" items has since been fixed —
> do not act on that note.

## Where things stand

`main` is at `6c3fb8a` (merge of PR #61, 2026-08-22). Every PR through #61 is merged; nothing is
open. The full default suite is green (1607 passed, 2 expected skips, with the package installed
editable as CI does), and all 9 CI jobs were green on the merged head.

Recent sequence, all approved by Taylor and merged:

| When | What | Merge |
|---|---|---|
| 2026-08-20 | Post-Test #1 Decision Register v2.2 propagation (PR #59) | `54431ba` |
| 2026-08-21 | **WP-A1** — queue containment (B-F001 / NUDGE-F001 / S1 portion / D2) (PR #60) | `2e3a340` |
| 2026-08-22 | **WP-A2** — audit-write integrity, S2 degraded-result semantics, OP-W004 root cause (PR #61) | `6c3fb8a` |

## Read these first, in this order

1. `MASTER_PLAN.md` "Next 3 Moves" and "Approval Ledger" — current position and approvals.
2. `ROADMAP.md` "Post-Test #1 readiness bands" — the Band A status note (2026-08-22) says what is
   discharged and what remains.
3. `DECISIONS.md`, the three 2026-08-22 entries — S2 degraded-result semantics; the per-surface
   Reflection classification; the WP-A1 curiosity-equivalence decision.
4. `RISKS.md` tech-debt watchlist, the four 2026-08-22 entries — the live, known-unfixed items.

## What is in flight (approved, not yet implemented)

- **WP-A2b — provenance-bearing Reflection surfaces.** Approved in principle, **design-first: do
  not implement without the design-review checkpoint.** Scope: make `record_action_reflection()`
  failures observable and propagate them through the chat / training / sight+voice result
  contracts only; leave the additive surfaces (skill, awaiting_response, scheduler) unchanged.
  Authority: `DECISIONS.md` "One Reflection sink, two semantic roles".
- **Documentation reconciliation pass** — the pass that produced this note; PR pending approval.

## Traps that are easy to reintroduce

- **Never swallow a required audit write's failure.** `_log_audit` / `_audit_execution` return
  their failure and `_finish()` folds it into the result. A bare `except Exception: logger.warning`
  around an audit write reintroduces OP-W004. The structural test
  (`tests/test_audit_write_integrity.py::TestConnectionAuthority`) also fails any bare
  `sqlite3.connect()` in the covered modules — use `kernel/db_ctx.connect()` + `set_wal_pragmas()`.
- **The degraded path is only for actions that genuinely executed.** A failed pre-action gate
  (brake, consent, policy) stays fail-closed; an unreadable brake refuses the action. Pinned by
  test; do not "fix" that into a degraded success.
- **Do not claim the quiet-hours flake is fixed.** `test_set_quiet_hours_updates_settings` fails
  intermittently (~3-4/1200, only in the first ~8 requests after process start) because the
  skill's own state write loses the writer lock during the scheduler's startup burst. WP-A2
  measured it unchanged before/after — by design. The fix is a future bounded package on the
  timeout/startup-burst interaction; see the two 2026-08-22 `RISKS.md` entries.
- **The effective SQLite lock timeout is 5s**, not `db_ctx.connect()`'s documented 30s —
  `set_wal_pragmas()`'s `PRAGMA busy_timeout = 5000` overrides it. Taylor decided 2026-08-22 to
  retain 5s for now. Do not silently change either value.
- **`get_permission_checker()` ignores `db_path` after first construction** (process-global
  singleton). Reset it in tests that construct registries on their own paths; see the `RISKS.md`
  entry before touching it in production code — fixing it is its own bounded work item.
- Carried forward, all still true: no mock text on model-backend failure; the budget ledger fails
  closed; never forward `temperature`/`top_p` to a cloud Claude model; never concatenate
  NarratorEngine output onto ReflectionGenerator output.

## Decisions that remain explicitly unresolved

Not settled by any documentation, and not to be settled by inference (Taylor, 2026-08-22):

1. The vision-document thesis (`docs/VISION_AND_PERSONAL_DEPLOYMENT.md`).
2. The local/cloud data boundary (vision §9, open question 2).
3. `config/policy.yaml`'s `affected_components` 4-vs-6 scope list — runtime significance
   undetermined; flagged in `RISKS.md`, must not be "resolved" by editing documentation.

## Running it locally

```
pip install -e . -r requirements-dev.txt   # editable install; the packaging-contract smoke tests
                                           # fail without it (environmental, not a defect)
ollama pull mistral:7b-instruct            # local model; cloud stays off without an API key
uvicorn app:app --port 5173                # then http://localhost:5173 -> /ui/
```

`/api/health`: `"model_real": true` means a real model is wired; `false` means the stub.
