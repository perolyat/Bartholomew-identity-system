# Bartholomew Identity Interpreter

**This workspace is Bartholomew's Brain** - a cognitive architecture implementing identity, memory, planning, safety, and decision-making systems.

Python implementation of the Identity Interpreter for Bartholomew AI system.

## Phase 0 Quickstart - "It's Alive" Baseline

```bash
# Setup
python -m venv .venv && .venv\Scripts\activate  # Windows
# or: source .venv/bin/activate                 # Linux/Mac

pip install -e .
pip install -r requirements.txt

# First run creates DB and starts the kernel
uvicorn app:app --reload --port 5173

# Test the kernel is alive (nudges will print in console every ~15s)
curl http://127.0.0.1:5173/api/health
```

*(Corrected 2026-07-28: this smoke-test example previously used `/kernel/command/water_log_250`
— a legacy Stage 0 example endpoint, not a current product priority; hydration/water-logging is
not part of current active product direction, see `CONSTITUTION.md`'s consumer-value gate and
`RISKS.md`'s tech-debt watchlist. `/api/health` is a neutral liveness check that doesn't imply any
particular feature is the point of the demo.)*

The kernel runs an autonomy loop with scheduled drives that monitor system health and generate proactive nudges.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Install package
pip install -e .

# Validate Identity.yaml
python -m identity_interpreter.cli lint Identity.yaml

# Explain policy decisions
python -m identity_interpreter.cli explain Identity.yaml --task-type code --confidence 0.4
```

> **Corrected 2026-07-27.** These commands previously read `barth lint` / `barth explain`. The
> `barth` console script is declared in `setup.py` but **not installed** — `pyproject.toml` is
> the manifest that actually installs, and it declares `bartholomew` and
> `bartholomew-backfill-fts` instead. Verified with `which`. The competing-manifest problem
> itself is tracked as finding **F9** in [RISKS.md](RISKS.md) and is not fixed.

## Getting Started (Development)

### Environment Setup

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Linux/macOS
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -U pip setuptools wheel
pip install -e .
pip install -r requirements.txt -r requirements-dev.txt
```

### Developer One-Liners

```bash
# Run tests
pytest -q

# Lint code
ruff check .

# Format code
black .

# Type check (optional)
mypy .

# Install pre-commit hooks
pre-commit install

# Run all hooks
pre-commit run --all-files
```

## Database Configuration

Both the Kernel and API use a single SQLite database.

**Resolution order:**
1. `BARTH_DB_PATH` environment variable (used as-is)
2. Default: `data/barth.db` under the project root (directory containing `pyproject.toml`)

**Examples:**

```bash
# Windows (cmd)
set BARTH_DB_PATH=D:\data\barth-prod.db

# Windows (PowerShell)
$env:BARTH_DB_PATH="D:\data\barth-prod.db"

# Linux/macOS (bash)
export BARTH_DB_PATH=/var/lib/bartholomew/barth.db
```

On first run, the data directory is created automatically if it doesn't exist.

**Git hygiene:**

The repository ignores local SQLite databases and WAL/SHM files by default:
- `data/*.db`
- `data/*-wal`
- `data/*-shm`
- `data/**/*.db`

## Testing

Run tests with pytest:

> ⚠️ **A plain `pytest` does not run every test.** `pyproject.toml` sets
> `addopts = "-q -m 'not integration and not slow'"`, so 3 of the 915 collected tests are
> deselected. Use `pytest -m ""` to run genuinely everything. See [CI.md](CI.md).

```bash
# Default suite (deselects integration/slow — 912 of 915 tests)
pytest

# Genuinely everything, no marker deselection
pytest -m ""

# Only what the default command skips
pytest -m "integration or slow"

# Run specific test categories
pytest -m database        # Database-related tests
pytest -m integration     # Integration tests
pytest -m windows_quirk   # Tests handling Windows file issues

# Run tests with verbose output
pytest -v
```

### Smoke Tests

Quick sanity checks for fast feedback:

```bash
# Run smoke tests (Windows)
scripts\smoke.ps1

# Run smoke tests (Linux/macOS)
./scripts/smoke.sh

# Or directly with pytest
pytest -q -m smoke
```

Smoke tests verify core functionality and can run in seconds.

### Windows Testing Notes

**File Locking Issues**: Windows tests may occasionally fail with `PermissionError` (WinError 32) during teardown due to lingering database file handles. This is a Windows-specific quirk where SQLite WAL files can remain locked briefly after connection closure.

**Test Fixtures**: The test suite includes robust fixtures in `conftest.py` that handle Windows file locking:

- `temp_db_path`: Creates temporary database files with retry cleanup logic
- `db_conn`: Provides database connections with proper teardown
- `ensure_cleanup`: Auto-runs garbage collection after each test

**CI Expectations**:

- Local Windows development: Occasional non-functional test failures due to file locking
- CI environments: Should be more reliable due to isolated container environments
- Test failures related to file deletion are infrastructure issues, not logic bugs

**Troubleshooting**: If tests consistently fail with file permission errors:

1. Close any database browser tools (DB Browser for SQLite, etc.)
2. Restart the terminal/IDE to clear file handles
3. Run tests individually with `pytest -k test_name` to isolate issues

## Features

- ✅ JSON Schema validation of Identity.yaml
- ✅ Pydantic v2 type-safe models
- ✅ Policy engines (model selection, safety, confidence). *Tool-use policy and persona moved out
  of `identity_interpreter` in items 11.12/11.14 — they are now owned by
  `bartholomew/kernel/policy_engine.py` and `bartholomew/kernel/persona_pack.py` respectively,
  one authority per concept.*
- ✅ Explainable decisions with YAML path references
- ✅ CLI tools (lint, explain, simulate)
- ✅ Adapter stubs (LLM, tools, consent, metrics, storage)
- ✅ Test suite with pytest

## Documentation

**Start with [MASTER_PLAN.md](MASTER_PLAN.md)** — it is the Single Source of Truth for what
exists, where the project is, and what is authorised next. It lists the 14 canonical documents.
Everything else in this repository, including this README and most of `docs/`, is a reference and
is **not** an authority on project status — the one deliberate exception is `docs/TILT.md`, listed
below, which is itself canonical.

Most useful entry points:

- [MASTER_PLAN.md](MASTER_PLAN.md) — SSOT: stage status, backlog, approval ledger
- [docs/TILT.md](docs/TILT.md) — current execution priority: Usable POC / time-to-real-use
- [COGNITIVE_RUNTIME.md](COGNITIVE_RUNTIME.md) — how Bartholomew actually thinks (the runtime loop)
- [ROADMAP.md](ROADMAP.md) — stage gates and engineering workstreams with exit criteria
- [CI.md](CI.md) — what CI runs and how to reproduce it locally
- [RISKS.md](RISKS.md) — risk register and tech-debt watchlist, including known open findings

See [docs/README.md](docs/README.md) for the `identity_interpreter` module reference.

### Key Documentation

- [Reflection Generation](docs/REFLECTION_GENERATION.md) - LLM-based daily/weekly reflections with safety guardrails
- [Metrics Security](METRICS_SECURITY_IMPLEMENTATION.md) - Production-ready metrics implementation
- [Quick Start](QUICKSTART.md) - Getting started guide

## Project Structure

```text
├── identity_interpreter/     # Core package
│   ├── policies/            # Policy engines
│   ├── adapters/            # External integrations (stubs)
│   └── schema/              # JSON Schema
├── tests/                   # Test suite
├── docs/                    # Documentation
├── scenarios/               # Test scenarios
└── exports/                 # Runtime outputs
```

## Next Steps

**Removed 2026-07-28** (documentation reconciliation pass 2) — this independent list competed
with the canonical roadmap. For current priorities, see `MASTER_PLAN.md`'s "Next 3 Moves" and
`ROADMAP.md`'s "Near-term milestone plan," both of which require separate explicit approval
before any listed step begins.

## License

CC-BY-NC-4.0
