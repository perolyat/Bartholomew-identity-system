# CI

> How to run quality checks locally and what CI enforces.
>
> **Last updated:** 2026-07-28 (documentation reconciliation pass 2: the "CI Philosophy → Linux
> is the Baseline" section rewritten — it self-contradicted this file's own "Quarantine Strategy"
> section by still saying Windows failures are "documented as environmental noise unless proven
> to be logic bugs," when the rest of this file, `DECISIONS.md`, and `ASSUMPTIONS.md` had already
> moved to "observed and diagnosed by default.")
>
> **Previously (2026-07-27):** Phase A recorded as merged with its real merge commit and first
> cross-platform results; test counts refreshed against actual collection; two references to a
> `docs/STATUS_2026-01-19.md` that has never existed replaced. Earlier, 2026-07-25 — Phase A:
> `ci.yml` added, broken manual-only `tests.yml` removed, and the "`pytest -q` runs the full test
> suite" claim corrected — it does not.)

**Phase A is merged.** Merge commit `8b96319c4059d9dfada2579ca5f6da22b34e1f31` (PR #26,
2026-07-27). All **9** checks were green on the merged head `e923fb9`: `quality`; `tests` on
Ubuntu 3.10 and 3.11; `critical` on Ubuntu 3.10 and 3.11; `windows` on 3.11; `lint-test` on 3.10
and 3.11; and `smoke`. The Windows job was the first ever run in this repository.

## GitHub Actions Workflows

### 1. ci.yml ✅ AUTO-RUN (primary)

**Status:** Runs automatically on **every pull request**, **every push to main**, and
**manual dispatch** (`workflow_dispatch`).

**Jobs:**

| Job | Runner | Python | What it does |
|---|---|---|---|
| `quality` | Ubuntu | 3.11 | Installs from **declared** deps only (`pip install -e .`), `pip check`, `ruff check .`, `black --check .`, packaging contract |
| `tests` | Ubuntu | 3.10, 3.11 | `pip check`, smoke tests, default suite **with coverage across all first-party packages**, gate line >= 70%, uploads `coverage.xml` |
| `critical` | Ubuntu | 3.10, 3.11 | The `integration`/`slow` tests **excluded by the default marker expression**, clean-start lifecycle, scheduler startup readiness, parking-brake governance |
| `windows` | Windows | 3.11 | `pip check`, packaging contract, clean-start lifecycle (DB handle release / temp cleanup), scheduler readiness, smoke |

**What it catches:**
- A runtime dependency missing from `pyproject.toml` (the `quality` job installs *only* declared deps)
- A console script or first-party module broken at import time
- Integration/lifecycle regressions that the default `pytest -q` silently skips
- Windows-only failures (file locking, path handling, DB handle release)
- Coverage regressions across `bartholomew`, `identity_interpreter`, `bartholomew_api_bridge_v0_1`

### 2. pre-commit.yml ✅ AUTO-RUN

**Status:** push/PR to main, plus manual dispatch.

**Configuration:**
- **Runs on:** Ubuntu | **Python matrix:** 3.10, 3.11
- `pre-commit run --all-files --show-diff-on-failure` (black, ruff, end-of-file-fixer,
  trailing-whitespace, detect-private-key, check-yaml, check-added-large-files)
- `pytest -q -m smoke`
- `pytest -q` — **the default suite, which is not every test** (see the marker note below)

### 3. smoke.yml ✅ AUTO-RUN

**Status:** push/PR to main/master, plus manual dispatch.

- Starts a real uvicorn server and exercises `/healthz`, `/api/health`, `/docs`.
- This is the only workflow that boots an actual HTTP server, which is why it is kept
  alongside `ci.yml`.

### Removed: tests.yml

`tests.yml` was manual-only (`workflow_dispatch`) **and could never have passed**: it ran
`pytest --cov=...` while `pytest-cov` was declared in no manifest, so the command failed with
`unrecognized arguments: --cov`. Its coverage role is now served by `ci.yml`'s `tests` job,
which runs automatically, measures all first-party packages, and installs `pytest-cov` from
`requirements-dev.txt`.

---

## Lint/format tool versions are pinned

`.pre-commit-config.yaml` pins `ruff` and `black`, and `requirements-dev.txt` now pins the
**same** versions. Without that pin, `ci.yml`'s bare `ruff check .` installed whichever ruff was
newest and reported rules the pinned pre-commit hook does not — the same tree was simultaneously
"clean" under `pre-commit` and "68 errors" under CI. When bumping either file, bump both.

---

## ⚠️ `pytest -q` does NOT run every test

`pyproject.toml` sets:

```toml
[tool.pytest.ini_options]
addopts = "-q -m 'not integration and not slow'"
```

So a plain `pytest -q` **deselects every `integration`- and `slow`-marked test**. Verified by
collection on 2026-07-27: **915 tests collected in total, 3 deselected, so `pytest -q` runs
912.** (The earlier figure of 895 was correct when written and has simply grown; the 3
deselected tests are unchanged.)

- `test_cold_boot.py::test_cold_boot_reload`
- `test_integration.py::test_model_integration`
- `tests/test_experience_kernel.py::TestExperienceKernelIntegration::test_kernel_with_identity_drives_integration`

They pass, but nothing ran them automatically before Phase A. `ci.yml`'s `critical` job now
runs them explicitly via `-m "integration or slow"`.

To run genuinely everything locally:

```bash
pytest -m ""            # all markers, no deselection
pytest -m "integration or slow"   # only what the default command skips
```

---

## Local Development Commands

### Pre-commit (fast feedback)

```bash
# Run all pre-commit hooks
pre-commit run --all-files

# Install hooks to run on every commit
pre-commit install
```

### Individual tools

```bash
# Format code
black .

# Check formatting without modifying
black --check .

# Lint
ruff check .

# Type check (optional)
mypy .
```

### Tests

```bash
# Default: unit tests + fast tests (excludes integration and slow)
pytest -q

# Smoke tests only (fastest sanity check)
pytest -q -m smoke

# Integration tests
pytest -q -m integration

# With coverage report (all first-party runtime packages, as CI measures)
pytest --cov=bartholomew --cov=identity_interpreter \
       --cov=bartholomew_api_bridge_v0_1 --cov-report=term-missing

# With coverage enforcement (matches ci.yml's gate)
pytest --cov=bartholomew --cov=identity_interpreter \
       --cov=bartholomew_api_bridge_v0_1 --cov-branch --cov-fail-under=70

# Specific test file
pytest -q tests/test_stage0_alive.py

# Specific test function
pytest -q -k test_kernel_boots_and_shuts_down
```

### Full CI simulation

```bash
# Run what pre-commit.yml runs
pre-commit run --all-files && pytest -q -m smoke && pytest -q

# Run what ci.yml's `critical` job runs (the tests `pytest -q` skips)
pytest -m "integration or slow"
pytest tests/test_clean_start_lifecycle.py tests/test_scheduler_startup_readiness.py

# Run what ci.yml's `tests` job runs (coverage + gate)
pytest --cov=bartholomew --cov=identity_interpreter \
       --cov=bartholomew_api_bridge_v0_1 --cov-branch --cov-fail-under=70
```

---

## CI Philosophy

### Linux is the Baseline — corrected 2026-07-28 (was self-contradicting this file's own
"Quarantine Strategy" section below)

Per [DECISIONS.md](DECISIONS.md), **Linux CI is the source of truth** in the narrower sense that
the full default test suite and the coverage gate run there. This section previously stated
"Windows-specific failures are documented as environmental noise unless proven to be logic bugs"
— that directly contradicted this same file's "Quarantine Strategy" section (below), which
correctly describes the current, corrected posture: Phase A added a real `windows` CI job, and
Windows failures are now **observed and diagnosed by default**, not dismissed as noise. Two
concrete reasons this old wording was actively wrong, not just imprecise: the 2026-07-20 FTS5
investigation found two failures previously attributed to "Windows-only quirks" were real logic
bugs reproducible on Linux, and no formal quarantine list has ever existed (`ASSUMPTIONS.md` A1).

**Corrected rationale for treating Linux as the baseline (narrower claim than before):**
- The full default `pytest -q` suite and the 70% coverage gate run on Linux; the `windows` job
  runs a smaller, targeted set (packaging contract, clean-start lifecycle, scheduler readiness,
  smoke) — so Linux is "the baseline" for breadth of coverage, not for whether Windows failures
  matter.
- SQLite build features can still vary across platforms (FTS5/matchinfo availability) — this is a
  real cross-platform risk category (see `ASSUMPTIONS.md` A2), not a reason to dismiss any given
  Windows failure without diagnosis.
- A Windows-only failure must be diagnosed like any other CI failure; "it's just Windows" is a
  claim requiring proof, not a default assumption (`DECISIONS.md`'s "CI health baseline is Linux"
  entry, amended 2026-07-27).

### Coverage Gates

**Enforced by ci.yml's `tests` job:**
- Line coverage: >=70% (measured baseline 2026-07-25: **73.52%**)
- Scope: `bartholomew`, `identity_interpreter`, `bartholomew_api_bridge_v0_1`

The threshold is the project's pre-existing declared value (`.coveragerc`
`[report] fail_under`). Widening the scope from one package to three was measured first and
still clears it, so the gate was enforced rather than lowered.

**Local verification:**
```bash
pytest --cov=bartholomew --cov=identity_interpreter \
       --cov=bartholomew_api_bridge_v0_1 --cov-branch --cov-fail-under=70
```

**Regression policy:**
- Any change that drops coverage below gates must either:
  - Add tests to restore coverage, OR
  - Justify in PR why coverage drop is acceptable

### Quarantine Strategy

Platform-specific test failures should be:
1. Marked with `@pytest.mark.windows_quirk` or similar (both `windows_quirk` and `database` are
   registered in the root `conftest.py`, not in `pyproject.toml`)
2. Documented in [ASSUMPTIONS.md](ASSUMPTIONS.md) with justification
3. Not allowed to hide real logic bugs

**Prefer running the platform over quarantining it (Phase A posture, 2026-07-27).** Quarantine is
the fallback, not the first move. Phase A deliberately did the opposite of the historical plan: it
added a Windows CI job and a test asserting the exact database-handle-release property that fails
first under Windows locking, rather than marking such failures as noise. Note also that no formal
quarantine list has ever been created (see [ASSUMPTIONS.md](ASSUMPTIONS.md) A1) — so "we quarantine
with justification" describes an intention, not an existing artifact.

**Example:**
```python
@pytest.mark.windows_quirk
def test_sqlite_wal_cleanup():
    # This may fail on Windows due to file locking during cleanup
    ...
```

---

## Common Failure Patterns

For current test health see [REVIEWS.md](REVIEWS.md)'s "Last Review Snapshot". *(Corrected
2026-07-27: this line pointed at `docs/STATUS_2026-01-19.md`, a file that has never existed in
this repository. The only STATUS snapshot on disk is `docs/archive/STATUS_2025-12-29.md`, which carries
an explicit stale-doc banner and must not be read as current.)*

### Environmental (Platform Noise)

**Windows file locking:**
- `PermissionError: [WinError 32] ... being used by another process`
- Cause: SQLite connections not fully closed before tempdir cleanup
- Windows keeps stricter locks than POSIX
- **Mitigation:** Mark as `windows_quirk` if reproducible only on Windows

**SQLite build variance:**
- `sqlite3.OperationalError: unable to use function matchinfo in the requested context`
- `sqlite3.DatabaseError: database disk image is malformed`
- Cause: Python/SQLite build lacks FTS5 or matchinfo support
- **Mitigation:** FTS fallback implementations exist; ensure they're exercised

**pytest plugin issues:**
- `fixture 'mocker' not found` → install `pytest-mock`
- Async fixture `'coroutine' object has no attribute` → `pytest-asyncio` config mismatch

### Non-Environmental (Real Bugs - Priority to Fix)

**Current P0 failures:** none. The six failures this section used to list (from the
2025-12-29 snapshot) were all fixed in the 38 -> 0 sweep on 2026-07-20 — see MASTER_PLAN.md's
"Full test suite investigation". Verified 2026-07-25: default suite and
`-m "integration or slow"` both pass locally on Python 3.10 and 3.11.

**One known intermittent failure, deliberately not hidden (added 2026-07-27):**
`tests/test_sqlite_wal_concurrent_processes.py::test_wal_cleanup_concurrent_processes` failed
once under full-suite load during Phase A verification and passed 3/3 in isolation immediately
afterwards. It was **not** retried, quarantined, re-marked, or given a longer timeout, and it is
not counted as environmental noise — it is preserved as evidence for the Phase B persistence
audit (see [RISKS.md](RISKS.md)). If it fails in CI, diagnose it; do not paper over it.

**Process:**
- Fix one at a time (smallest surface first)
- Add/adjust tests for each fix
- Verify with `pytest -q -k <test_name>`
- Update [INTERFACES.md](INTERFACES.md) if contracts changed

---

## Interpreting CI Results

### Green ✅
All checks passed. Safe to merge (pending code review).

### Red due to formatting/linting ❌
**pre-commit.yml failed:**
1. Run `black .` locally
2. Run `ruff check .` and fix issues
3. Commit fixes
4. Push

### Red due to test failures ❌
**Check which workflow failed:**

1. **smoke.yml:** Server startup or health endpoint broken
   - Check recent changes to `app.py` or `bartholomew_api_bridge_v0_1/`
   - Test locally: `uvicorn app:app --port 5173`, then `curl http://localhost:5173/healthz`

2. **ci.yml:** quality/tests/critical/windows job failure
   - Check test logs for failure details
   - Reproduce locally: `pytest -q -k <failing_test>`
   - Fix root cause, not just the test

3. **Check if failure is environmental:**
   - Does it reproduce on Linux?
   - Is it in the "environmental failures" list in STATUS snapshot?
   - If yes, consider quarantine with `@pytest.mark.windows_quirk`

### Yellow (warnings) ⚠️
Coverage close to threshold or non-critical issues. Review but may not block merge.

---

## CI Gatekeeper Definition

Before any merge to main:

- [ ] `ci.yml` passes (quality + tests/coverage + critical + windows)
- [ ] `pre-commit.yml` passes (hooks + smoke + default suite)
- [ ] `smoke.yml` passes (live server health)
- [ ] Coverage gate met (line >=70% across all first-party packages)
- [ ] Governance invariants preserved (see [CHECKLISTS.md](CHECKLISTS.md))

---

## CI stabilisation status

**Done (Phase A, merged 2026-07-27, `8b96319`):** the goal this section previously tracked —
"enable auto-run so all PRs are validated" — is met by `ci.yml`, which runs automatically on every
pull request and push to main. Manual dispatch is retained on all three workflows. Confirmed by a
real run, not by inspection: 9/9 checks green on PR #26's head `e923fb9`.

**Deliberately still open (Phase B — proposed, NOT approved for implementation):** persistence
ownership is mixed
(synchronous `sqlite3`, `aiosqlite`, the scheduler's dedicated DB thread, and two near-duplicate
`db_ctx` modules all touch the same file). Phase A adds regression tests that *characterise*
clean-start and shutdown behaviour but does not restructure it. See RISKS.md's tech-debt
watchlist.

## Links

- [MASTER_PLAN.md](MASTER_PLAN.md) - Overall project plan
- [ROADMAP.md](ROADMAP.md) - Stage gates and milestones
- [TEST_MATRIX.md](TEST_MATRIX.md) - Test coverage by subsystem
- [CHECKLISTS.md](CHECKLISTS.md) - Pre-merge checklist
- [PERF_BUDGETS.md](PERF_BUDGETS.md) - Performance expectations
- [REVIEWS.md](REVIEWS.md) - Latest project/test health snapshot (replaces the
  `docs/STATUS_2026-01-19.md` link that stood here; that file has never existed)
