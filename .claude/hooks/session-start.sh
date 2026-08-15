#!/bin/bash
# SessionStart hook — prepares a Claude Code on the web container so that the
# test suite, linters, and the FastAPI app can run without a manual setup step.
#
# Runs synchronously: the session starts only after this completes, which
# guarantees no race between the agent invoking pytest/ruff and the install.
# Only runs in remote (web) sessions; local machines keep their own venv.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"

# A venv is required, not optional: the container's system Python is
# Debian-managed, so pip cannot upgrade or uninstall its packages
# (`RECORD file not found`). This mirrors the README's documented setup.
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel

# -e . installs the package and the console scripts declared in pyproject.toml
# (`bartholomew`, `bartholomew-backfill-fts`). Note setup.py's `barth` script is
# NOT installed — see RISKS.md finding F9.
python -m pip install -e .
python -m pip install -r requirements.txt -r requirements-dev.txt

# The kernel and API resolve the DB from BARTH_DB_PATH, else data/barth.db under
# the project root. Create the directory up front so first run does not race.
mkdir -p data

# Persist for the session so pytest/ruff/black resolve to the PINNED versions in
# the venv rather than whatever the system Python has. An unpinned ruff reports
# rules the pre-commit hook does not — see CI.md.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"${CLAUDE_PROJECT_DIR:-$(pwd)}/.venv\""
    echo "export PATH=\"${CLAUDE_PROJECT_DIR:-$(pwd)}/.venv/bin:\$PATH\""
    echo 'export PYTHONPATH="."'
  } >> "$CLAUDE_ENV_FILE"
fi

echo "Bartholomew session-start hook complete."
