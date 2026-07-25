# CI

> How to run quality checks locally and what CI enforces.
>
> **Last updated:** 2026-07-25 (Phase A: `ci.yml` added — automatic PR/push/dispatch CI with
> Ubuntu + Windows, packaging-contract checks, explicit integration/lifecycle tests and
> coverage across all first-party packages; broken manual-only `tests.yml` removed; the
> "`pytest -q` runs the full test suite" claim corrected — it does not)

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

## ⚠️ `pytest -q` does NOT run every test

`pyproject.toml` sets:

```toml
[tool.pytest.ini_options]
addopts = "-q -m 'not integration and not slow'"
```

So a plain `pytest -q` **deselects every `integration`- and `slow`-marked test**. As of
2026-07-25 that is 3 tests out of 895:

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

### Linux is the Baseline

Per [DECISIONS.md](DECISIONS.md), **Linux CI is the source of truth**.

Windows-specific failures are documented as environmental noise unless proven to be logic bugs.

**Rationale:**
- Windows file locking can cause spurious test cleanup failures
- SQLite build features vary (FTS5/matchinfo availability)
- Linux CI is more deterministic and reproducible

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
1. Marked with `@pytest.mark.windows_quirk` or similar
2. Documented in [ASSUMPTIONS.md](ASSUMPTIONS.md) with justification
3. Not allowed to hide real logic bugs

**Example:**
```python
@pytest.mark.windows_quirk
def test_sqlite_wal_cleanup():
    # This may fail on Windows due to file locking during cleanup
    ...
```

---

## Common Failure Patterns

See `docs/STATUS_2026-01-19.md` (or latest STATUS snapshot) for current test health.

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

**Done (Phase A, 2026-07-25):** the goal this section previously tracked — "enable auto-run so
all PRs are validated" — is met by `ci.yml`, which runs automatically on every pull request and
push to main. Manual dispatch is retained on all three workflows.

**Deliberately still open (deferred to Phase B):** persistence ownership is mixed
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
- `docs/STATUS_2026-01-19.md` - Latest test health snapshot
