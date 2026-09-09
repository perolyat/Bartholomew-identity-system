# W03 — Merge Candidate readiness

> **Owner:** the Wave 3 merge-candidate readiness session (continuation of `W03-F`).
> **Scope:** the outstanding Merge Candidate acceptance evidence, and nothing else.
> No feature was added, no Wave 3 scope was changed, no Wave 4 planning was done,
> and no acceptance criterion was weakened.

## 1. The candidate

There is exactly one Wave 3 integration candidate, and this document names it so
that no later session has to re-derive it:

| | |
|---|---|
| Integration session | `W03-F — Integration & Real-World Test Candidate` |
| Original PR | [#99](https://github.com/perolyat/Bartholomew-identity-system/pull/99), branch `claude/w03-f-final-integration-ugqc2n` |
| W03-F frozen head | `7af5fe7c9685cf957619f118fccae59f5751a893` |
| Base | `main @ e96e6a6` |
| Contents | `main`, then the five frozen builder heads in the contract's order — W03-D `111c72d3`, W03-A `56bf995c`, W03-C `e904cd18`, W03-B `fead4b39`, W03-E `e19776e2` — plus W03-F's 15 seam repairs and its integration suite |
| Continuation branch | `claude/wave3-merge-candidate-readiness-bop01v`, whose first commit **is** `7af5fe7`. Every commit W03-F made is preserved unchanged; this branch only adds to it. |

The other open pull requests (#93, #94, #95, #96, #97) are the five **builder** heads
that this candidate already contains. They are not separate merge candidates and must
not be merged individually. #98 and #100 are documentation-only and unrelated to Wave 3
scope.

## 2. What was outstanding

`W03-F` closed with **one** unmet acceptance criterion — contract criterion 1,
*"Merge Candidate tier green on the integrated head"* — blocked by a single red job:

    Windows full default suite + actuation (py3.11)

Ten tests, failing identically on `main @ e96e6a6` (the candidate's own baseline,
which carries no W03 product code) and on the candidate. They are deterministic
platform failures, not flakes: four re-runs reproduced them exactly. `W03-F`
diagnosed them in `BARTHOLOMEW_W03_F_HANDOFF.md` §4a and, correctly under its own
stand-down rule, reported rather than fixed them — they lie in five wave-1/wave-2
modules no W03 session owns.

Reporting them was right for `W03-F`. Leaving them is not a resting state for the
wave: the tier can never go green while they stand, so the criterion could never be
discharged. This session's task was to resolve that evidence, on the candidate,
without weakening it.

## 3. What was done

Four root causes, repaired at the root in each case. Two are **product defects** a
coarse clock exposes; two are **test defects** that made a real assertion unrunnable
on Windows.

### 3.1 Product — "most recent first" was not, when the clock could not separate two writes

Windows resolves the system clock to roughly **15.6 ms**, against microseconds on
Linux. Records written in a tight loop therefore genuinely share a timestamp there,
and every "most recent first" reader in these modules ordered on the timestamp alone:

* `narrator.py` — five `ORDER BY timestamp DESC` sites (`get_recent_episodes` with
  and without `since`, `get_episodes_by_type`, `get_episodes_by_tag`, and the
  non-FTS search fallback). Ties were left to SQLite. **Now** `ORDER BY timestamp
  DESC, rowid DESC` — SQLite's implicit rowid is insertion order, so the tiebreaker
  is the true write order.
* `persona_pack.py` — `get_switch_history`, same fix.
* `global_workspace.py` — `get_history` and `get_all_history` sorted with
  `list.sort`, which is **stable**: it preserves *ascending* publication order among
  ties, so the oldest of a tied group was returned as the newest. That is precisely
  what the Windows failure `{'i': 5} == {'i': 9}` showed. **Now** a
  `_most_recent_first()` helper orders by `(timestamp, publication position)`
  descending; the list is still in publication order at that point, so its index is
  the missing monotonic sequence number. No event field, and no public signature,
  changed.

This is a latent defect on every platform, not a Windows quirk — a fast enough
publisher hits it on Linux too. It is repaired in the product, not papered over in
the tests.

**Four new regression tests reproduce the platform condition on any OS** by writing
records that share a timestamp explicitly (`tests/test_global_workspace.py::TestHistoryOrderingUnderACoarseClock`,
`tests/test_narrator.py::TestPersistence::test_recent_episodes_tied_on_timestamp_are_newest_written_first`).
All four fail against the pre-fix product code and pass after it — verified by
reverting only the three product files and re-running.

### 3.2 Test — a governance assertion that did not run on Windows at all

`tests/test_voice_sight_runtime_contract_seam.py` read production sources with
`Path.read_text()` and no encoding, so Windows decoded UTF-8 sources as cp1252 and
the test died with `UnicodeDecodeError`. The assertion it never reached is the
structural **no-bypass** proof that `_perform_capture` / `_perform_stream` are never
invoked directly. Now `read_text(encoding="utf-8")` at both sites: the governance
assertion runs on Windows for the first time. This one mattered beyond the red tick.

### 3.3 Test — three files unlinked while still open

`tests/test_narrator.py` removed temporary files that a live handle still held
(`WinError 32`): a `NamedTemporaryFile` unlinked *inside* its own `with` block, and
a verifying connection opened with `with sqlite3.connect(...)`, which commits but
does **not** close. POSIX permits removing an open file; Windows does not. The three
tests now use the `tmp_path` fixture and close what they open. Every assertion is
unchanged.

### 3.4 Test — a `#!/bin/sh` stub asked to execute on Windows

`tests/test_spoken_output.py::test_a_wedged_engine_is_abandoned_not_waited_on`
wrote a shell script Windows cannot execute (`WinError 193`). The suite's `recorder`
fixture already skips on Windows; this test did not, and its subject — that a wedged
speech engine is **abandoned rather than waited on** — is worth running on both
platforms. It now writes a batch file that waits, on Windows only. Not skipped.

### 3.5 Two `since`-filter tests that measured the clock rather than the filter

`test_get_recent_episodes_with_since_filter` and
`test_search_episodes_with_since_filter` took `datetime.now()` between two writes as
the cutoff. That only separates them where the clock is finer-grained than the gap,
so on Windows both rows satisfied `timestamp >= cutoff` and the tests failed `2 == 1`.
The cutoff is now derived from the first record's own timestamp and waited past, so
the **filter** decides the result. The assertions — exactly one row, and which one —
are untouched. The product's `>=` semantics are unchanged: they were never wrong.

## 4. What was deliberately not done

* **No test was skipped, quarantined, disabled, deleted, or loosened.** Two
  assertions now run on Windows that previously could not.
* **No governance behaviour was touched**, and no acceptance criterion was
  reworded, narrowed or waived to obtain a green tick.
* **The ordering fix was not swept repo-wide.** Other `ORDER BY timestamp DESC`
  sites exist (`working_memory`, `skill_registry`, `skill_permissions`,
  `experience_kernel`, `memory_manager`). They are the same latent class and are
  recorded here as a follow-up, not fixed: nothing in the acceptance evidence
  depends on them, and widening further would put unrelated modules into the
  wave's merge.
* **Wave 3 scope is unchanged**; no feature was added and no Wave 4 planning done.
* **The real-world Windows acceptance test remains outstanding**, exactly as
  `W03-F` recorded. It is hardware work that follows the merge, and nothing here
  claims otherwise. The §3.11 prerequisite still stands: the live run must set
  `BARTH_RUNTIME_USER_ID`.

## 5. Evidence

Local, on the changed tree:

| Check | Result |
|---|---|
| `ruff check .` (pinned 0.14.3) | clean |
| `black --check .` | clean, 528 files |
| Affected suites (narrator, persona pack, global workspace, spoken output, voice/sight seam) | all pass |
| New ordering regression tests against **pre-fix** product code | all fail, as they must |

CI on this head is the authoritative record; see the pull request for the tier
results, and §6 for the standard this candidate is held to.

## 5a. Measured result, and what remains

> **This section was rewritten after the `_pid_alive` repair (§5b) let the
> Windows suite run to completion for the first time.** Its earlier version
> reported the Windows tier as "0 failed, 2242 passed" and called the ten
> failures resolved with nothing else outstanding. That was true of the tests
> that ran and wrong about what it implied, because roughly half the suite had
> never executed. The corrected figures are below.

### The ten failures are resolved

They were, and are, genuinely fixed — all ten are in the first half of the
suite and all ten pass. That claim survives.

### But no Windows run had ever executed more than about half the suite

The Ctrl+C described in §5b truncated every Windows run at roughly the halfway
mark. The arithmetic is unambiguous:

| Head | Reported | Tests run | Share of the suite |
|---|---|---|---|
| `main @ e96e6a6` (no W03 code) | 10 failed, 2035 passed, 6 skipped | 2051 | ~44% |
| this branch, before the repair | 0 failed, 2239 passed, 6 skipped | 2245 | ~49% |
| **after the repair, run 1** (38 m 20 s) | 16 failed, 4524 passed, 79 skipped | **4619** | **100%** |
| **after the repair, run 2** (13 m 14 s) | 16 failed, 4541 passed, 79 skipped | **4636** | **100%** |

The progress bar stalling at "~48%" was stating the total directly; it was read
as a symptom of the interrupt and never reconciled against the Linux suite's
~4,627 tests, which would have shown the shortfall immediately.

### What the previously-unreached half contains

Sixteen failures, every one in wave-1/2 code and none in a W03 package:

Two complete runs now exist, and comparing them separates the deterministic
failures from the contention-class ones. **Thirteen are identical in both:**

| Cause | Count | Where |
|---|---|---|
| `WinError 32` — a temp DB unlinked while SQLite holds the handle | 10 | `tests/integration/`: `test_recency_flip_integration.py` (3), `test_fts_unavailable_vector_quality.py` (3), `test_hybrid_paraphrase_benchmark.py` (2), `test_lexical_over_vector_on_rare_tokens.py` (2) |
| `UnicodeDecodeError` (cp1252) — `read_text()` with no encoding | 2 | `test_skill_runtime_contract_seam.py`, `test_consent_bypass_redteam.py` |
| Windows path escaping in an assertion | 1 | `test_process_lock.py` |

The remaining **three are all in the documented scheduler / SQLite writer-lock
contention class**, and they do not fully repeat: `test_event_backbone_drive.py`
contributed two in both runs, while the third rotated —
`test_scheduler_queue_containment.py` (an xdist worker crash) in run 1,
`test_event_backbone_processing.py` in run 2. That is the signature the CI
baseline already records for this class, not a new deterministic break.

The first two rows are **the same two defect classes already repaired in this
branch's first commit**, in files the truncated run never reached. The
`UnicodeDecodeError` pair matters for the same reason its sibling did: both are
structural **governance** assertions — the skill-seam no-bypass proof and the
consent-gate red-team check — and neither runs on Windows at all.

### The job budget is marginal — the observations, not a verdict

This section has twice stated a conclusion that the next run contradicted. The
observations, in order, are these. `windows-full` carries `timeout-minutes: 40`:

| Attempt | Head | Outcome |
|---|---|---|
| 1 | `dcf842e` | Suite completed in **38 min 20 s**; job **cancelled** at the cap |
| 2 | `d80aba8` (re-run) | Suite completed in **13 min 14 s**; job **failed** on the 16, a clean result |
| 3 | `ebe9d12` | **Cancelled** at the cap with no summary and no junit written at all |
| 4 | `81e3150` | **Cancelled** at the cap; 97% reached at ~18 min, then no further progress |
| 5 | `cdf08c9` | **Cancelled** at the cap; 97% reached at 15 min 30 s, then no further progress for ~24 min |
| 6 | `cdf08c9` (re-run) | Suite completed in **15 min 11 s**; job **failed** on 6, a clean result (§5c) |

Four of six attempts hit the cap, and the three most recent cancellations share a
shape worth recording exactly: the progress bar reaches 97% at 15–18 minutes and
then **nothing further is printed** until the cap. That is not a slow suite; it is
a tail that does not finish. Each of those runs also shows one `[gw*] node down:
Not properly terminated` at ~62–68%, in the region where
`test_scheduler_queue_containment.py` runs — pytest-timeout is configured with
`timeout_method = "thread"`, which ends a wedged worker with `os._exit`, and
xdist then redistributes its tests. Whether the stalled tail is that
redistribution or a separate wedge is not determined; the junit file is never
written on a cancelled run, so the cancelled attempts carry no per-test timing.
This is **timing evidence for the contention class and the job budget**, reported
here separately as the authorisation requires, and nothing in CI configuration
has been changed in response to it.

So the earlier reading here — that attempt 1
was a slow outlier and the budget is comfortable — is **not supported**; nor is
the reading before it, that the budget is definitively too small. What the
evidence supports is narrower: **the Windows suite's wall-clock is highly
variable, roughly 13 to 40+ minutes, and the 40-minute budget is marginal
against it.** A run can produce a clean result or none at all depending on
runner speed.

Whether that budget should be raised is a judgement about this repository's CI,
not a Wave 3 question. `timeout-minutes` has not been touched.

### Status

None of the sixteen was a consequence of the `_pid_alive` repair — that repair
revealed them — so at the time they were recorded here rather than fixed, under
the authorisation's own stop condition. The thirteen deterministic ones were
then repaired under a further, separate authorisation; §5c is that record and
carries the current status.

## 5b. The `_pid_alive` repair (separately authorised)

A **pre-existing base-branch defect**, discovered while validating this merge
candidate and repaired under explicit, separate user authorisation. It is not
Wave 3 work.

`bartholomew/multimodal/store.py::_pid_alive` probed liveness with
`os.kill(pid, 0)` — the POSIX "signal nothing, just check it exists" idiom. On
Windows that is not a question: `signal.CTRL_C_EVENT == 0`, and CPython routes
signal 0 to `GenerateConsoleCtrlEvent`, so the call **raises Ctrl+C on that
process group's console**. `reconcile_after_restart` calls it on any snapshot
this process owns — our own pid — so the probe interrupted the running process.

Evidence: `os.kill(pid, 0)` is present at that line on `main @ e96e6a6`, added
by wave-1/2 commit `0d1b19a`, and is the only `os.kill` site in the product. A
one-round `--full-trace` CI probe (since reverted byte-for-byte) showed the
interrupt landing inside pytest-timeout's `timer.start()` on an Event already
`set` — nothing blocked, an asynchronous interrupt arriving in whatever frame
the main thread held.

The Windows path now asks instead of signalling: `OpenProcess` for `SYNCHRONIZE`
plus `WaitForSingleObject(handle, 0)`. POSIX behaviour is unchanged. Three
regression tests run on every platform, including the direct one — a SIGINT
handler is installed, the probe is called, and the handler must not fire.

Beyond CI this mattered on its own: on a real Windows machine the same call can
interrupt whatever shares the console.

## 5c. The thirteen deterministic Windows failures (separately authorised)

The second bounded repair, authorised after §5a enumerated the sixteen. Its
scope was the **thirteen failures identical across both complete Windows runs**
and nothing else: the contention class, `timeout-minutes`, CI topology and any
broader cleanup were explicitly excluded. All thirteen are pre-existing wave-1/2
failures that no Windows run had ever reached before §5b.

### Enumeration and root causes

| Class | Root cause | Tests | Repair |
|---|---|---|---|
| **A. `WinError 32`** (10) | `bartholomew/kernel/vector_store.py` opened every connection as `with sqlite3.connect(...) as conn:`, which commits on exit but **never closes**. On CPython 3.11 a `sqlite3.Connection` sits in a reference cycle with its own statement cache, so the handle — and the `.db`/`-wal`/`-shm` files under it — stayed open until the cyclic collector happened to run. POSIX can unlink an open file; Windows cannot, so every `TemporaryDirectory` teardown after a `VectorStore` call raced the collector and lost. Confirmed by an fd probe: seven handles held after one `upsert`, zero after `gc.collect()`; after the fix, zero at every step. `FTSClient`, `HybridRetriever` and `MemoryStore` already closed explicitly and were not the leak. | `tests/integration/`: `test_recency_flip_integration.py` ×3, `test_fts_unavailable_vector_quality.py` ×3, `test_hybrid_paraphrase_benchmark.py` ×2, `test_lexical_over_vector_on_rare_tokens.py` ×2 | All seven sites go through `db_ctx.wal_db()`, the repository's own pattern — same pragmas, `close` in `finally`, already used at seventeen W03 store sites and enforced for the API layer by `test_no_raw_sqlite_connect_in_api.py` citing this exact leak. Every write site already committed explicitly, so the old context's implicit commit was not load-bearing. The ten tests are untouched. |
| **B. `UnicodeDecodeError`** (2) | Two governance walkers read the product with `read_text()` and no encoding — cp1252 on Windows — and died at byte `0x9D` (the last byte of a U+201D quotation mark at offset 5849 of `bartholomew/executive/intent.py`) before reaching their assertion. | `test_skill_runtime_contract_seam.py`, `test_consent_bypass_redteam.py` | `encoding="utf-8"` at the two walker sites. Search roots, AST logic and assertions are byte-identical. **These two no-bypass proofs now execute on Windows for the first time.** |
| **C. Path escaping** (1) | Both `ProcessLockHeldError` messages in `bartholomew/kernel/process_lock.py` rendered the path with `!r`, which doubles every backslash, so on Windows the path in the message was not the path. | `test_process_lock.py` | `'{path}'` at the three sites — quoted plain, byte-identical on POSIX for ordinary paths. No other test asserts on the message text. |

Classes A and B are the same two defect classes as §3.2 and §3.3, in files the
truncated runs never reached; C is new. The reconciliation the authorisation asked
for: "twelve in the two classes already repaired" was correct as to class, but the
earlier repairs were **in tests** (a walker's encoding, a test's own unlink), while
class A's root here is **in the product** — a shared leak that ten tests merely
exposed. Fixing the product once, rather than the ten tests, was the shared
root-cause fix the authorisation preferred.

### Regression coverage, and that it is load-bearing

Every new test was run against the pre-fix product in a separate worktree and
failed there.

* `tests/test_vector_store_handle_lifetime.py` (new): with the collector
  **disabled**, construction, `upsert`, `count`, `count_by_kind` and
  `delete_for_memory` each leave no handle open (read from `/proc/self/fd` where
  the kernel offers it, by rename-probe elsewhere); structurally, the module opens
  no raw `sqlite3.connect`; and the store's directory is deletable the moment a
  call returns — the symptom itself, run on every platform, and able to fail only
  on Windows.
* `tests/test_process_lock.py`: a backslash in the filename (legal on POSIX) makes
  the verbatim-not-escaped property provable everywhere; both messages checked.
* Class B's proof is the two governance tests themselves, now executing on
  Windows. A Linux-side structural guard over test walkers would be suite-wide
  cleanup and is left for separate authorisation.

### Measured result

Head `cdf08c9`. PR Fast and Integration tiers: **green**. Windows full suite,
run 34333396707 attempt 2, complete in 15 min 11 s:

| | Before (§5a, two runs) | After |
|---|---|---|
| Deterministic, identical across runs | **13** | **0** |
| Contention class (documented, §2.6 of the CI baseline) | 3 | 5 |
| Other | 0 | 1 |
| Total | 16 failed / 4,524–4,541 passed | **6 failed / 4,555 passed / 79 skipped** |

**None of the thirteen recurred.** Every one of the ten `WinError 32` tests, both
governance walkers and the lock-message test passed.

### What remains, kept separate

The six that failed, none of them in the authorised classes and **none touched by
this repair**:

1. **Writer-lock / WAL contention class — five.** `test_event_backbone_drive.py`
   ×2 (`claimed` never became `processed`; no tick recorded),
   `test_notifications_api.py` ×2 (`database is locked` inside `_save_settings`,
   surfacing as HTTP 400), `test_sqlite_wal_concurrent_processes.py` (spawned
   worker: `database is locked`). All are named in the CI baseline's §2.6
   intermittent list; all pass in isolation; they rotate between runs. Explicitly
   out of this authorisation's scope, and not fixed.
2. **One new, previously unseen: `test_always_on_runtime_unit.py::`
   `test_the_scheduler_loop_beats_even_when_no_drive_is_due`** —
   `assert 701.25 > 701.25`. The test stamps `time.monotonic()`, sleeps 10 ms, and
   stamps again; Windows resolves `monotonic()` to ~15.6 ms, so two beats inside
   one tick are equal. It passed in both earlier complete runs and is the same
   coarse-clock class as §3.1/§3.5, here in the **test** (it measures the clock,
   not the heartbeat). A **test defect, intermittent on Windows only**, unrelated
   to this repair, and — under the stop condition for new unrelated failures —
   reported rather than fixed. Recorded as a follow-up.

The job budget evidence is in §5a: the two attempts on this head before the clean
one were cancelled at the cap with the stalled-tail shape described there.

### Status

The Merge Candidate tier on `cdf08c9` is **not green**: six failures, all outside
the authorised deterministic classes. The candidate is therefore **not yet ready
for merge approval on the contract's own criterion 1** as literally written. What
the two authorised repairs have established is narrower and, this document holds,
decisive for the next decision: **every deterministic Windows failure this
repository has is gone, and what remains is the pre-existing, documented
intermittent contention class plus one coarse-clock test.** Whether to authorise
work on that class, or to judge the criterion on that evidence, is the user's
call and is not made here.

## 6. The merge gate

It is the user's alone. §7 records how it was exercised.

* The Wave 3 merge requires **explicit user approval**. Auto-merge is not enabled
  and must not be.
* The candidate is **frozen** at the head this document is committed on. Nothing
  further is pushed to it without saying so here.
* The formal real-world Windows acceptance test follows the merge, per the
  project's own sequencing. Automated green is not a claim that Bartholomew is
  usable on the user's real Windows machine.

## 7. Wave 3 exit decision (User Approval Gate, 2026-09-09)

Recorded once, on the user's explicit instruction, as the authoritative Wave 3
exit record.

### The decision

The user approved the Wave 3 exit and authorised the merge of PR #101 on the
evidence in this document, with an **explicit User Approval Gate exception to the
literal wording of contract acceptance criterion 1** ("Merge Candidate tier green
on the integrated head"). The exception is bounded and reasoned: the tier's
remaining red is limited to identified intermittent CI/test-reliability behaviour
that predates Wave 3 and is not attributable to it, and continuing to expand the
integration session into general repository reliability repair was judged the
wrong use of the wave. **That remaining work is repository reliability debt, not
Wave 3 implementation scope**, and is deferred to a separate follow-up (see
"Deferred" below). No test was skipped, quarantined, loosened or reworded to
reach this decision; the criterion is recorded as unmet-as-written and
exception-approved, not as satisfied.

### What Wave 3 passed

* The single integration candidate (§1) is integrated: `main`, the five frozen
  builder heads in contract order, W03-F's seam repairs and its integration
  suite, unchanged, plus this branch's repairs.
* PR Fast tier: **green** on the code head. Integration tier: **green**.
* Every **deterministic** Windows failure the repository had is repaired, at the
  root, with load-bearing regression coverage:
  * the ten W03-F could not meet criterion 1 against (§3);
  * the `_pid_alive` Ctrl+C that had truncated every Windows run in the
    repository's history at ~48% (§5b);
  * the thirteen the completed suite then revealed — ten `WinError 32` from
    `VectorStore`'s never-closed connections, two cp1252 governance walkers, one
    escaped lock path (§5c). Deterministic count **13 → 0**, measured on a
    complete 15-minute Windows run.
* Three governance assertions that could not execute on Windows before now do.
* No Wave 3 scope change, no feature work, no Wave 4 implementation: the diff
  against `main` contains no Wave 4 file or reference.

### What remains, and the evidence it is not Wave 3's

On the last complete Windows run (§5c): six failures.

| Class | Tests | Evidence it predates Wave 3 |
|---|---|---|
| Writer-lock / WAL contention (5) | `test_event_backbone_drive` ×2, `test_notifications_api` ×2, `test_sqlite_wal_concurrent_processes` | Every one is named in `RISKS.md`'s tech-debt watchlist entries of 2026-08-18 and 2026-08-22 and in the CI baseline's §2.6 intermittent list, with failures recorded on `main` as early as `d0c202f` (2026-08-15). They rotate between runs and pass in isolation. None is in a W03 package. |
| Coarse `time.monotonic()` on Windows (1) | `test_always_on_runtime_unit.py::test_the_scheduler_loop_beats_even_when_no_drive_is_due` | Wave-1/2 test; measures a 10 ms sleep against a ~15.6 ms clock. Passed on two earlier complete runs; the product's heartbeat is untouched by Wave 3. |
| Job budget / stalled tail | the `windows-full` job itself | Cancellations at the 40-minute cap with a stalled tail after 97% (§5a) occur on heads with and without the W03 repairs, including documentation-only commits. |

The two authorised repairs on this branch touched none of these tests and none
of the code they exercise for contention.

### Deferred

All of the above is deferred, as one follow-up reliability task separate from
Wave 3, into `RISKS.md`'s tech-debt watchlist (entry dated 2026-09-09), which
carries the diagnostic evidence gathered in this session, including a local
reproduction of the lock-loss mechanism. It is not repaired here.

### Confirmations at merge

| | |
|---|---|
| PR #101 head (code) | `cdf08c97e40ae459f094ad68ffe6b9e8e0fd3713` |
| PR #101 head (this record) | the commit this document is committed on |
| Mergeable against current `main` | yes — `main @ 72ed19a` (PR #100, documentation-only) merges with no conflict |
| Unresolved deterministic failures attributable to Wave 3 | none |
| Wave 4 implementation in the PR | none |

### After the merge

The formal real-world Windows acceptance test follows the merge, per the
project's own sequencing (§6). Automated green is not a claim that Bartholomew
is usable on the user's real Windows machine. Wave 4 implementation and further
CI repair each require separate authorisation.
