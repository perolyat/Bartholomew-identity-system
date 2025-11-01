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
curl -X POST http://127.0.0.1:5173/kernel/command/water_log_250
```

The kernel runs an autonomy loop with scheduled drives that monitor system health and generate proactive nudges.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Install package
pip install -e .

# Validate Identity.yaml
barth lint Identity.yaml

# Explain policy decisions
barth explain Identity.yaml --task-type code --confidence 0.4
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

```bash
# Run all tests
pytest

# Run specific test categories
pytest -m database        # Database-related tests
pytest -m integration     # Integration tests
pytest -m windows_quirk   # Tests handling Windows file issues

# Run tests with verbose output
pytest -v
```

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
- ✅ Policy engines (model routing, tool control, safety, persona)
- ✅ Explainable decisions with YAML path references
- ✅ CLI tools (lint, explain, simulate)
- ✅ Adapter stubs (LLM, tools, consent, metrics, storage)
- ✅ Test suite with pytest

## Documentation

See [docs/README.md](docs/README.md) for full documentation.

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

1. Wire real backends (Llama.cpp, Ollama, cloud APIs)
2. Implement real tool sandboxing
3. Add scenario-based testing
4. Build metrics dashboard

## License

CC-BY-NC-4.0
