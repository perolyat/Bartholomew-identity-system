# CI

> How to run quality checks locally and what CI enforces.
>
> **Last updated:** 2026-01-19

## GitHub Actions Workflows

### 1. pre-commit.yml ✅ AUTO-RUN

**Status:** Enabled on push/PR to main

**Configuration:**
- **Runs on:** Ubuntu
- **Python matrix:** 3.10, 3.11
- **Actions:**
  - `pre-commit run --all-files --show-diff-on-failure` (format, lint, security)
  - `pytest -q -m smoke` (fast sanity checks)

**What it catches:**
- Code formatting issues (black)
- Linting issues (ruff)
- Security issues (detect-private-key, etc.)
- Fast smoke test failures

### 2. smoke.yml ✅ AUTO-RUN

**Status:** Enabled on push/PR to main/master

**Configuration:**
- **Runs on:** Ubuntu
- **Python:** 3.11
- **Actions:**
  - Start uvicorn server on port 5173
  - Test `/healthz` endpoint (verify status == "ok", version == "0.1.0")
  - Test `/api/health` endpoint
  - Test `/docs` endpoint (Swagger/OpenAPI)

**What it catches:**
- Server startup failures
- API endpoint regressions
- Health check contract violations

### 3. tests.yml ⚠️ MANUAL-ONLY

**Status:** Manual dispatch only (`workflow_dispatch`)

**Why manual:** "Enable on push/PR once Stage 1 stabilizes" (per workflow comment)

**Configuration:**
- **Runs on:** Ubuntu
- **Python:** 3.11
- **Actions:**
  - Full test suite: `pytest --cov=bartholomew --cov-branch`
  - Coverage gates:
    - Line coverage: >=70% (enforced)
    - Branch coverage: >=60% (enforced)
  - Artifacts: coverage.xml, htmlcov/

**What it catches:**
- Unit test failures
- Integration test failures
- Coverage regressions

**To enable auto-run:**
1. Fix P0 non-environmental failures (see Phase 3 in action plan)
2. Verify Linux CI is green
3. Change workflow trigger from `workflow_dispatch` to:
   ```yaml
   on:
     push:
       branches: [main]
     pull_request:
       branches: [main]
   ```
4. Update ROADMAP "Next 3 Moves"
5. Record decision in DECISIONS.md

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

# With coverage report
pytest --cov=bartholomew --cov-report=term-missing

# With coverage enforcement (CI gates)
pytest --cov=bartholomew --cov-branch --cov-fail-under=70

# Specific test file
pytest -q tests/test_stage0_alive.py

# Specific test function
pytest -q -k test_kernel_boots_and_shuts_down
```

### Full CI simulation

```bash
# Run what CI runs (pre-commit + smoke)
pre-commit run --all-files && pytest -q -m smoke

# Run full test suite like tests.yml
pytest --cov=bartholomew --cov-branch --cov-fail-under=70
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

**Enforced by tests.yml:**
- Line coverage: >=70%
- Branch coverage: >=60%

**Local verification:**
```bash
pytest --cov=bartholomew --cov-branch --cov-fail-under=70
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

**Current P0 failures** (as of 2025-12-29, needs refresh):
1. Summarization truncation fallback
2. Encryption envelope round-trip
3. Embeddings persist lifecycle (`persist_embeddings_for` returns 0)
4. `embed_store` defaulting logic
5. Retrieval factory mode mismatch (`mode="fts"` returns VectorRetriever)
6. Metrics registry idempotency (Prometheus `Duplicated timeseries`)

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

2. **tests.yml (if enabled):** Unit/integration test failure
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

- [ ] `pre-commit.yml` passes (format + lint + smoke)
- [ ] `smoke.yml` passes (server health)
- [ ] `tests.yml` passes (once enabled; currently manual-only)
- [ ] No new P0 failures introduced
- [ ] Coverage gates met (line >=70%, branch >=60%)
- [ ] Governance invariants preserved (see [CHECKLISTS.md](CHECKLISTS.md))

---

## Stage 1 Stabilization Plan

**Current state:** tests.yml is manual-only

**Goal:** Enable auto-run tests.yml so all PRs are validated

**Blockers:**
- 6 P0 non-environmental test failures (see `docs/STATUS_2025-12-29.md`)

**Path to green:**
1. Refresh STATUS snapshot (Phase 2)
2. Fix P0 failures one-by-one (Phase 3)
3. Verify Linux CI green
4. Enable auto-run (Phase 4)
5. Update ROADMAP "Next 3 Moves #2" to complete

**Target:** End of Stage 2 (governance hardening complete)

---

## Links

- [MASTER_PLAN.md](MASTER_PLAN.md) - Overall project plan
- [ROADMAP.md](ROADMAP.md) - Stage gates and milestones
- [TEST_MATRIX.md](TEST_MATRIX.md) - Test coverage by subsystem
- [CHECKLISTS.md](CHECKLISTS.md) - Pre-merge checklist
- [PERF_BUDGETS.md](PERF_BUDGETS.md) - Performance expectations
- `docs/STATUS_2026-01-19.md` - Latest test health snapshot
