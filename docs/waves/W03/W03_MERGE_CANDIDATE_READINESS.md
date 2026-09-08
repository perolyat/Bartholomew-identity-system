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

## 6. The merge gate

Unchanged, and it is the user's alone:

* The Wave 3 merge requires **explicit user approval**. Auto-merge is not enabled
  and must not be.
* The candidate is **frozen** at the head this document is committed on. Nothing
  further is pushed to it without saying so here.
* The formal real-world Windows acceptance test follows the merge, per the
  project's own sequencing. Automated green is not a claim that Bartholomew is
  usable on the user's real Windows machine.
