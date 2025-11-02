#!/usr/bin/env bash
# Run smoke tests only
set -euo pipefail
pytest -q -m smoke
