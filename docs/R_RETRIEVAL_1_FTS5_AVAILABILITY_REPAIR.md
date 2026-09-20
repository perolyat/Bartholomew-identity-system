# R-RETRIEVAL-1 — FTS5 Availability / Retrieval Correctness Repair

**Status:** implemented, tested, **CI green**, **not merged**. Awaiting Taylor's User Approval
Gate. PR #118 (draft); head `4d2e5de`.
**Baseline it was built on:** `origin/main` at `840c3c54c695c9ce13e0aff008e110157a39c473`
(the merge of PR #117).
**Branch:** `claude/r-retrieval-1-fts5-fix-qru53y`.
**Scope:** the FTS5 availability contract — where the answer is stored, how failures are
classified, when it is re-probed, and what reporting says about it. No retrieval
architecture, no memory changes, no fusion/ranking changes, no embedding changes.

> **This document is the package's durable evidence.** `RISKS.md` remains canonical for
> the risk's substance; Airtable owns live status. Where this document and `RISKS.md`
> disagree about what the risk *is*, `RISKS.md` wins.

---

## 1. What was wrong

`RISKS.md`'s R-RETRIEVAL-1 entry (as corrected on 2026-09-19) records three defects in
`bartholomew/kernel/retrieval.py`'s FTS5 availability mechanism. All three were re-derived
from the code at `840c3c5` before anything was changed, and all three held.

**1. The answer was process-global and unkeyed.**

```python
_fts5_available_cache: bool | None = None   # retrieval.py:34 at 840c3c5
```

`_check_fts5_once()` returned that value if it was not `None`. The first probe in a
process therefore answered for **every** database any later caller retrieved from, for the
life of the process, and nothing ever re-probed it.

**2. Every failure collapsed into "FTS5 is absent", at two sites.**

```python
# fts_client.py:84-87              # retrieval.py:59-62
except Exception:                  except Exception:
    return False                       available = False
```

"This SQLite build has no FTS5", "the database was locked at probe time", "the path could
not be opened" and "a test patched the probe" all produced the same `False`. The lower
site destroyed the distinction before the upper site could act on it, which is why fixing
only `_check_fts5_once()` could not have recovered it.

**3. Nothing reported it.** `describe_retrieval()` — the accessor that exists so health,
CLI and result contracts cannot drift from reality — reported embedding degradation in
detail and carried **no FTS field at all**.

### What was *not* wrong, and is not claimed here

The blast radius is the narrower one `RISKS.md` records after its 2026-09-19 correction,
and this package does not re-inflate it:

| Resolved mode | Effect of a latched `False` |
|---|---|
| `fts` resolved from `BARTHO_RETRIEVAL_MODE` or `kernel.yaml` | lexical retrieval genuinely lost; with no embedder, `get_retriever()` then raises `EmbedderUnavailableError` and returns nothing |
| `fts` passed explicitly as an argument | honoured; no degradation |
| `hybrid` — the repository default and the chat path | `logger.info` only; the FTS arm still runs |

The default hybrid/chat path was **not** proven to lose recall, and this package does not
claim it did. What the tests now do is make that property explicit and defended
(§4, `TestDefaultHybridPath`) rather than incidental.

---

## 2. Root cause

One cause, expressed three ways: **the code stored a conclusion where it should have
stored an observation.**

A boolean can record "FTS is usable". It cannot record *what was observed*, *which
database was observed*, or *whether the observation completed*. Because the only thing
kept was the conclusion:

- it could not be scoped (a conclusion about what? — nothing in the value says);
- it could not be re-derived (a conclusion carries no confidence, so there is nothing to
  distinguish a fact worth caching from a failure worth retrying);
- it could not be reported (a `False` tells an operator nothing about *why*).

The repair is therefore not "add a dict key" or "add a try/except branch". It is to make
the probe return a **structured observation** — status plus the detail behind it — and to
let scope, caching, degradation and reporting all be derived from that one honest value.

---

## 3. What changed

### 3.1 `bartholomew/kernel/fts_client.py` — the probe tells the truth

- `FTS5Status` — `AVAILABLE`, `ABSENT`, `PROBE_ERROR`.
- `FTS5ProbeResult(status, detail)` — frozen, with `available`, `conclusive` and
  `as_dict()`. `conclusive` is the load-bearing field: a `PROBE_ERROR` answers nothing and
  must never be cached or reported as degradation.
- `probe_fts5(conn)` — attempts the temp virtual table. **Only** a SQLite error matching
  "no such module … fts5" is `ABSENT`. Every other failure is `PROBE_ERROR`. The matcher
  (`_is_fts5_absent_error`) is deliberately narrow: an unrecognised failure staying
  inconclusive costs a repeated probe, whereas a wrongly-recognised absence costs silent
  recall loss.
- A successful `CREATE` followed by a failed cleanup `DROP` is now `AVAILABLE`. The
  previous implementation reported that as unavailable — a false negative on a capability
  that had just been exercised.
- `fts5_available(conn) -> bool` is kept as a thin wrapper over `probe_fts5().available`.
  **Every existing caller's fail-safe behaviour is unchanged**: still `False` for any
  non-available outcome.
- `FTSClient._probe_fts5()` still fails closed on any non-available outcome (it is an
  ingestion path), but its `RuntimeError` now says *which* happened instead of asserting a
  missing SQLite build for every failure.

### 3.2 `bartholomew/kernel/retrieval.py` — scope and recovery

- `_fts5_available_cache` (global bool) is **removed**, replaced by
  `_fts5_probe_cache: dict[str, FTS5ProbeResult]`, keyed by the resolved database
  (`_fts5_cache_key()`: `os.path.abspath` for real paths, left alone for `:memory:` and
  `file:` URIs).
- `check_fts5(db_path, *, force=False)` is the real API. It caches **only conclusive**
  outcomes. A `PROBE_ERROR` — including a `sqlite3.connect()` that failed, which is now
  its own `PROBE_ERROR` rather than an absence claim — is returned but never stored, so
  the next caller re-probes. That is the recovery contract: a transient failure costs one
  extra probe, not a process lifetime of degraded retrieval.
- `reset_fts5_cache(db_path=None)` is the supported way to clear it, for one database or
  all. Tests previously reassigned the private global, which was itself part of the
  coupling this risk is about.
- `_check_fts5_once(db_path)` is retained as the fail-safe boolean view.
- **The degradation gate now requires proof.** `fts`-from-config degrades to `vector` only
  when the probe is `ABSENT`. An inconclusive probe keeps the configured mode and logs
  that it will be retried — because degrading on an unproven absence is the expensive
  mistake: in this repository's default environment the degraded `vector` mode raises
  `EmbedderUnavailableError`, turning a transient probe error into a total retrieval
  outage.

### 3.3 Reporting — `describe_retrieval()`, `/api/health`, CLI

- `describe_retrieval(mode=None, db_path=None)` gains a `db_path` argument, resolved
  exactly as `get_retriever()` resolves it, so a no-argument caller describes the database
  retrieval would actually use.
- It returns a new `fts` block alongside the existing `embedding` block:
  `{status, available, conclusive, detail, db_path}`.
- A **conclusively absent** FTS5 now moves `mode_effective` exactly as an unavailable
  embedder does: `hybrid` → `vector`; config-resolved `fts` → `vector`; explicit `fts` →
  `none` (honoured, but it cannot match anything); and with no embedder *and* no FTS5,
  `none`.
- An **unknown** FTS5 deliberately changes nothing else. It is reported as
  `status: probe_error` and does not set `degraded` or move the mode. Claiming degradation
  that has not been established is the same class of untruth as hiding degradation that
  has.
- `/api/health` gains `retrieval_fts_status` and `retrieval_fts_available`, read from the
  same accessor, so the two cannot drift. Its failure branch reports `"unknown"` / `None`,
  never assumed-good. All existing keys are unchanged.
- `bartholomew embeddings stats` prints the FTS5 line, and now describes retrieval for the
  database the rest of that command is reporting on.

### 3.4 Existing tests migrated, semantics preserved

Nine call sites in `tests/test_retrieval_fts5_fallback.py` and
`tests/integration/test_fts_unavailable_vector_quality.py` reached into
`retrieval._fts5_available_cache` and patched `retrieval.fts5_available`. They now use
`reset_fts5_cache()` and patch `probe_fts5` with a conclusive `ABSENT` — which is what
those tests always meant. **No assertion was relaxed and no expectation was changed.**

---

## 4. What did NOT change

- Default `hybrid` retrieval, and the chat path's `get_retriever(db_path=…,
  memory_store=…)` via `runtime_contract.py`.
- Explicit `mode="fts"` is still honoured and never degraded.
- Explicit `mode="vector"` still raises `EmbedderUnavailableError` rather than serving
  lexical results under a vector label.
- Genuine-absence behaviour: config-resolved `fts` still degrades to `vector`; `hybrid`
  still logs and runs vector-only.
- `fts5_available()`'s fail-safe boolean contract, and `FTSClient`'s fail-closed ingestion
  probe.
- Fusion, ranking, tokenizer, schema, chunking, embeddings, `MemoryStore`, governance,
  authority, autonomy, privacy/redaction/consent. Nothing in this package touches them.

---

## 5. Negative and forbidden states now tested

`tests/test_retrieval_fts5_availability_contract.py` — **33 tests**, organised by the
statement each one forbids.

| # | Statement proved | Test |
|---|---|---|
| 1 | A transient SQLite failure is **not** reported as absence | `test_transient_sqlite_failure_is_not_reported_as_absence` |
| 2 | An arbitrary exception is **not** an absence claim | `test_arbitrary_exception_is_a_probe_error_not_an_absence_claim` |
| 3 | A failed cleanup does **not** unprove a capability that worked | `test_a_successful_create_is_not_unproved_by_a_failed_cleanup` |
| 4 | The boolean view stays fail-safe for every non-available outcome | `test_boolean_view_stays_fail_safe_for_every_non_available_outcome` |
| 5 | Database A's result does **not** determine database B | `test_a_result_from_database_a_does_not_determine_database_b` |
| 6 | Two spellings of one database do not double-probe | `test_two_spellings_of_one_database_share_a_single_answer` |
| 7 | Resetting one database does **not** clear the others | `test_resetting_one_database_leaves_the_others_cached` |
| 8 | A transient failure does **not** poison a later valid probe | `test_a_transient_probe_failure_does_not_poison_a_later_valid_probe` |
| 9 | An unfinished probe is **not** recorded as a capability answer | `test_an_inconclusive_probe_is_never_recorded_as_a_capability_answer` |
| 10 | An unopenable database is unknown, **not** absent | `test_an_unopenable_database_is_unknown_not_absent` |
| 11 | Conclusive answers are cached (recovery is not a per-call cost) | `test_conclusive_answers_are_cached_and_not_reprobed`, `test_absence_is_also_cached_rather_than_reprobed_forever` |
| 12 | Config `fts` does **not** degrade on an unproven absence | `test_config_fts_does_not_degrade_on_an_unproven_absence` |
| 13 | An explicit `fts` request is **never** rewritten by fallback logic | `test_an_explicit_fts_request_is_never_rewritten_by_fallback_logic` |
| 14 | Reprobe/recovery does **not** invent a mode nothing configured | `test_recovery_restores_the_configured_mode_and_invents_no_other` |
| 15 | An explicit `vector` request is unaffected by FTS recovery | `test_an_explicit_vector_request_is_unaffected_by_fts_recovery` |
| 16 | Default hybrid keeps its lexical arm when unrelated cached state changed | `test_hybrid_keeps_its_lexical_arm_when_another_database_probed_absent` |
| 17 | **Lexical recall itself survives** an unrelated absent probe (end to end) | `test_lexical_recall_survives_an_unrelated_absent_probe` |
| 18 | Reporting does **not** claim effective lexical retrieval when absent | `test_reporting_does_not_claim_effective_lexical_retrieval_when_absent` |
| 19 | Reporting does **not** claim degradation when FTS is available | `test_reporting_does_not_claim_degradation_when_fts_is_available` |
| 20 | Reporting does **not** invent degradation from an unknown probe | `test_reporting_does_not_invent_degradation_from_an_unknown_probe` |
| 21 | Reporting **never** claims availability for an unprobeable database | `test_reporting_never_claims_availability_for_an_unprobeable_database` |
| 22 | Honouring an explicit `fts` does not make it work — reported as `none` | `test_explicit_fts_on_an_absent_build_is_reported_as_no_effective_arm` |
| 23 | `/api/health` carries the same answer, from the same accessor | `test_health_surface_carries_the_fts_answer` |
| 24 | A vector-only outage does **not** blame FTS5 — in either of its two uncovered cells | `test_a_vector_only_outage_does_not_blame_fts5[available]`, `[probe_error]` |

Test 17 is the one that matters most and the one type-stability assertions cannot give
you: it seeds a real memory through `MemoryStore`, poisons the cache with another
database's `ABSENT`, retrieves through the ordinary hybrid path, and asserts the row comes
back. Type stability is not recall.

An autouse fixture clears the cache before and after every test in the module, so no test
can inherit another's answer — contamination between cases is designed out of the suite
that tests contamination.

---

## 6. Evidence

### 6.1 The pre-fix control — the same experiment, both trees

`docs/evidence/r-retrieval-1/fts5_availability_control.py` asks the risk entry's three
questions. It imports nothing the repair added and discovers whichever probe seam the tree
it runs in has, so the two runs are one experiment rather than two.

**`origin/main` at `840c3c5`, in a clean worktree (pre-fix):**

```
tree probe seam : fts5_available (main)
1. SCOPE     B reports available = False        -> FAIL   (A's answer decided B)
2. RECOVERY  first=False  second=False          -> FAIL   (the failed probe latched)
3. REPORTING ['degraded','embedding','mode_configured','mode_effective','reason','semantic']
             carries an 'fts' field             -> FAIL
RESULT: FAILS: SCOPE, RECOVERY, REPORTING          (exit 1)
```

**This branch (post-fix):**

```
tree probe seam : probe_fts5 (repaired)
1. SCOPE     B reports available = True         -> PASS
2. RECOVERY  first=False  second=True           -> PASS
3. REPORTING [... 'fts' ...] carries an 'fts' field -> PASS
RESULT: all three hold                             (exit 0)
```

All three regression cases fail against the implementation this package replaces and pass
against the repair. The full transcripts are in §6.4.

### 6.2 Focused tests

```
tests/test_retrieval_fts5_availability_contract.py              33 passed
```

### 6.3 Retrieval-adjacent regression set

```
test_retrieval_fts5_fallback.py, test_retrieval_factory.py,
test_retrieval_embedder_truthfulness.py, test_fts_search.py,
test_fts_schema_hygiene.py, test_fts_single_writer_architecture.py,
test_bm25_udf_fallback.py, test_hybrid_rrf.py,
test_retrieval_hot_reload.py, test_self_state_api.py           105 passed
```

### 6.4 Broader tiers

See §9.

---

## 7. The historical poisoned-memory CI failure — NOT reproduced, NOT claimed

`tests/test_w03d_memory_poisoning.py::TestPoisonedExternalContent::test_email_shaped_poison_is_framed_and_powerless`
failed once, in Merge Candidate run 35396770160 on the superseded head `167f94c`, with
"the seeded note was not recalled".

**This package did not reproduce it and does not claim to fix it.** No attempt was made to
construct a reproduction beyond running the test, because `RISKS.md` already establishes
two independent reasons the latch cannot be the explanation — the latch is constant for a
whole process yet the same file's other recall-dependent assertions passed in that same
run, and on the mode that test resolves a latched `False` does not suppress recall at all.
Re-deriving the defect from the code did not turn up any third mechanism that would.

**The cause of that single failure remains unknown**, and `RISKS.md`'s record of it is
unchanged by this package. If it recurs it is a separate investigation with a separate
risk entry.

---

## 8. A separate defect found, recorded not repaired: R-RETRIEVAL-2

Confirmed by experiment while testing reporting (`TestSeparatelyRecordedDefect`):

With **no embedder and no FTS5**, `get_retriever()` returns an `FTSOnlyRetriever` over a
database with no FTS5 behind it. Reporting is truthful — `describe_retrieval()` says
`mode_effective: none`, `degraded: true` — but the **factory's** contract is not: it hands
back an object that cannot serve its purpose, where the analogous explicit-`vector` case
raises `EmbedderUnavailableError` rather than returning a retriever that cannot retrieve.

This is a *factory contract* defect, not the availability-state defect R-RETRIEVAL-1
covers, and repairing it is **not** required to make availability truthful — which is why
it is recorded in `RISKS.md` as **R-RETRIEVAL-2** and left alone here. A characterisation
test pins today's behaviour so the separate fix has a starting point; if that test starts
failing because `get_retriever()` began raising, that is R-RETRIEVAL-2 being closed, not a
regression in this package.

---

## 8a. One defect found by adversarial review of this package, and fixed before the PR

Found by a five-dimension adversarial review of the diff, run before the PR was opened.
**It was a defect this package itself introduced, in the reporting code it exists to make
truthful** — which is why it is recorded here rather than quietly amended.

**What was wrong.** `describe_retrieval()` appended

> "No retrieval arm is operational: there is no embedder, and FTS5 is absent."

gated only on `effective_mode == "none"`. A configured **`vector`** mode reaches `"none"`
on the embedder alone and never passes through the FTS branch at all, so the sentence was
emitted for `vector` + unavailable embedder **whatever FTS5 was doing** — including in the
same payload whose own `fts` block said `"status": "available", "available": true`, served
next to it in one `/api/health` response.

The `probe_error` cell is the worse half: it asserted a **conclusive absence from an
inconclusive observation**, which is the precise untruth §3.3 and `describe_retrieval()`'s
own docstring say must not happen. Reproduced with no patching at all:

```
configured=vector, real probe (FTS5 present):
  fts      : available available= True
  effective: none
  claims 'FTS5 is absent' in reason: True      <-- contradicts its own fts block
```

**Why nothing caught it.** The identical condition in its *hybrid* form is asserted
against — `test_reporting_does_not_claim_degradation_when_fts_is_available` and
`test_reporting_does_not_invent_degradation_from_an_unknown_probe` both assert
`"FTS5 is absent" not in reason`. The `vector` route to `"none"` had no equivalent. Row 24
of §5 is that gap closed, in both cells.

**The fix** is one conjunct — `if effective_mode == "none" and fts_absent:` — inside the
block this package added. Verified as a real regression test, not a decoration: with the
conjunct removed both new cases fail with *"reporting blamed FTS5 for an outage FTS5 did
not cause"*; with it restored both pass.

This does **not** change the R-RETRIEVAL-2 entry's statement that reporting is truthful for
*that* state (hybrid + no embedder + FTS5 conclusively absent) — that claim was checked
against this finding and is accurate for the state it describes.

---

## 9. CI and tier results

### 9.1 GitHub CI — green, every job verified individually

Run 35501036504 (`CI`), head `4d2e5de`, **conclusion `success`**, 4/4 jobs:

| Job | Result |
|---|---|
| Quality (format, lint, packaging contract) | success — `pre-commit` (black, ruff, hygiene), `pip check`, Starlette security floor, packaging contract, wave manifest |
| PR Fast tests (Ubuntu, py3.11, parallel) | **success — the full default suite, zero failures** |
| Windows fast (packaging, lifecycle, actuation suites) | success — including real-Win32 governed actuation |
| smoke | success — including `/api/health`, which now serves the new FTS fields |

**The `Integration` and `Merge Candidate` tiers report `skipped`**: they do not run while the
PR is a draft. Both were run locally instead (§9.2), and both will run on GitHub when the PR
is marked ready for review.

**CI's default suite passing with zero failures settles §9.3.** The three failures seen in
the local sandbox did not occur on the runner, whose shorter temp paths do not reach the
wrap width. The code is not what differs between the two; the rendered path length is.

### 9.2 Local tiers

Run locally on this branch at `4033e3e`, Python 3.11.15, with the package installed
(`pip install -e .`) so the packaging-contract tests can see their console scripts.

| Tier | Command | Result |
|---|---|---|
| Focused (this package) | `pytest tests/test_retrieval_fts5_availability_contract.py` | **33 passed** |
| Retrieval-adjacent | 10 retrieval/FTS/hybrid/health test files | **105 passed** |
| Default (PR Fast equivalent) | `pytest -p no:cacheprovider -n auto --dist loadfile` | **5526 passed, 2 skipped, 3 failed** |
| Integration / slow | `pytest -m "integration or slow" -p no:cacheprovider` | **314 passed, 25 skipped, 0 failed** |
| Lint | `ruff check .` / `black --check .` | clean (515 files) |

The integration/slow tier's **314 passed / 25 skipped / 0 failed** is the same count
`RISKS.md` records for the full local runs on `main` and on the EXEC-02 branch. It includes
`tests/test_w03d_memory_poisoning.py` — the file whose isolated recall failure is discussed
in §7 — which passed.

### 9.3 The local default-tier failures are pre-existing, and the evidence says so

```
FAILED tests/test_kernel_db_path_resolution.py::test_brake_on_without_db_engages_the_database_the_server_reads
FAILED tests/test_kernel_db_path_resolution.py::test_brake_status_without_db_reports_the_servers_database
FAILED tests/test_kernel_db_path_resolution.py::test_brake_without_db_and_without_env_uses_the_project_default
```

All three are `rich` wrapping a long temp path across a line in the CLI's output, so an
`assert <path> in result.output` substring check misses:

```
AssertionError: assert '.../test_brake_on_without_db_engag0/live/barth.db' in
  '... Database: \n/tmp/pytest-of-root/pytest-5/popen-gw0/test_brake_on_without_db_engag0/live/bart\nh.db ...'
```

They are **not** regressions and are unrelated to retrieval. Classified by control, not by
inspection: the same tests were run under the same `-n auto --dist loadfile` command in a
clean `git worktree` of `origin/main` at `840c3c5`, and **failed identically there** —
3 failed, 11 passed. The `xdist` worker directory (`popen-gw0/…`) is what pushes the path
past the wrap width, which is why they pass when the same file is run serially.

**How many of them fail varies between runs, on both trees**, because the temp path's
length depends on the pytest session number and worker id: an earlier run of this tier saw
two of the three. That variability is itself the tell that the assertion, not the code
under test, is what is fragile. It is recorded as **R-TEST-1** in `RISKS.md` — a separate,
pre-existing test-robustness defect, not absorbed into this package.

Three further failures seen on the first pass —
`tests/smoke/test_packaging_contract.py::test_declared_console_script_runs_help[bartholomew]`,
`[bartholomew-backfill-fts]` and `test_no_undeclared_third_party_runtime_imports` — were this
sandbox not having run `pip install -e .`
(`FileNotFoundError: 'bartholomew'`, `PackageNotFoundError: No package metadata was found for
bartholomew`). They also failed identically on the `origin/main` control, and all 9 tests in
that file pass once the package is installed. CI installs it.

A third, independent corroboration: PR #117's own merge commit message, written by an
earlier session, records "the two known pre-existing `test_kernel_db_path_resolution.py`
failures, which reproduce on clean main".

**No failure in any tier is attributable to this change, and GitHub CI is green on the
current head.**

---

## 10. Does R-RETRIEVAL-1 close?

**Yes, completely — on the evidence, and subject to the User Approval Gate.** Every
condition `RISKS.md` names under "What would close it" is met:

| Closing condition (from `RISKS.md`) | Status |
|---|---|
| key the cache by database, or drop the caching entirely | **done** — keyed by resolved database; the unkeyed global is removed |
| distinguish genuine absence from a failed probe at `fts_client.fts5_available()` **first** | **done** — `probe_fts5()` is the new primary; `fts5_available()` is a view over it |
| …and at `_check_fts5_once()` **second** | **done** — `check_fts5()` acts on `status`, and `sqlite3.connect()` failure is its own `PROBE_ERROR` |
| surface FTS availability in `describe_retrieval()` | **done** — the `fts` block, plus `/api/health` and the CLI |
| regression coverage for multiple databases, transient probe failure and reporting | **done** — §5, 33 tests |
| do not claim the unknown red CI failure is solved | **honoured** — §7 |

What R-RETRIEVAL-1 does **not** close, and never covered: R-RETRIEVAL-2 (§8).

---

## 11. Remaining risks

1. **R-RETRIEVAL-2** (§8) — new, recorded, not repaired here.
2. **The absence matcher is narrow by design.** A SQLite build reporting a missing FTS5 in
   wording `_is_fts5_absent_error()` does not recognise would be classified
   `PROBE_ERROR` — re-probed every call, never cached, never degrading a mode. The cost is
   a repeated probe and a `logger.warning`, not silent recall loss. This is the deliberate
   direction of the error.
3. **The isolated poisoned-memory CI recall failure remains unexplained** (§7).
4. **`describe_retrieval()` now opens a database connection.** Called with no argument it
   probes the resolved default path, once per process per database. On a health endpoint
   this is a cached, sub-millisecond `sqlite3.connect()` after the first call; a database
   that cannot be opened is reported `probe_error` rather than raising.
5. **Windows.** The new suite carries `SKIP_WINDOWS_FTS` for the classes that build FTS
   databases, matching this repository's existing convention. Behaviour on a Windows
   SQLite build without FTS5 is classified by the same matcher but is not exercised in CI.

---

## 12. For the User Approval Gate

- Nothing is merged. The branch is `claude/r-retrieval-1-fts5-fix-qru53y`; the PR is a
  draft and will not be merged by the session that opened it.
- No authority, autonomy, governance or privacy boundary is touched. Nothing became
  approvable, automatic or permitted that was not before.
- No parallel memory or retrieval authority is introduced: the repair is inside the
  existing governed `MemoryStore`/retrieval path, and adds no bypass around it.
- The one judgement call worth a reviewer's attention is in §3.2: **an inconclusive probe
  no longer degrades a configured `fts` mode.** That is a behaviour change on a path that
  previously dropped the lexical arm on any exception. It is deliberate, argued above, and
  pinned by `test_config_fts_does_not_degrade_on_an_unproven_absence`.
