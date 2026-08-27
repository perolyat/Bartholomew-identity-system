from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CANONICAL_ROOT_DOCS = {
    "MASTER_PLAN.md",
    "CONSTITUTION.md",
    "COGNITIVE_RUNTIME.md",
    "ROADMAP.md",
    "DECISIONS.md",
    "RISKS.md",
    "ASSUMPTIONS.md",
    "INTERFACES.md",
    "CHECKLISTS.md",
    "REVIEWS.md",
    "CI.md",
    "TEST_MATRIX.md",
    "PERF_BUDGETS.md",
}

EXACT_BLOCKED = {
    "docs/TILT.md",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    ".pre-commit-config.yaml",
    "Identity.yaml",
}

BLOCKED_PREFIXES = (
    ".github/",
    "automation/",
    "config/",
    "migrations/",
    "docs/evidence/",
)

SENSITIVE_PARTS = {
    "auth",
    "authentication",
    "authorization",
    "security",
    "governance",
    "parking_brake",
    "parking-brake",
    "consent",
    "privacy",
    "schema",
    "migration",
}

# Durable-memory policy/storage semantics are consequential. Tests for existing
# memory behavior are allowed, but production memory implementation files are not.
MEMORY_PRODUCTION_PREFIXES = (
    "bartholomew/kernel/memory",
    "bartholomew/memory",
    "identity_interpreter/memory",
)


def changed_paths() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRTUXB", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def reason_for_block(path: str) -> str | None:
    if path in CANONICAL_ROOT_DOCS:
        return "canonical SSOT document"
    if path in EXACT_BLOCKED:
        return "policy/dependency/configuration authority"
    if path.startswith(BLOCKED_PREFIXES):
        return "controller, workflow, configuration, or preserved-evidence area"
    if path.startswith(MEMORY_PRODUCTION_PREFIXES) and not path.startswith("tests/"):
        return "durable-memory implementation/policy area"

    parts = {part.lower() for part in Path(path).parts}
    matched = sorted(parts & SENSITIVE_PARTS)
    if matched and not path.startswith("tests/"):
        return f"consequential area: {', '.join(matched)}"

    lowered = path.lower()
    if lowered.endswith(("requirements.lock", "poetry.lock", "uv.lock", "package-lock.json", "pnpm-lock.yaml")):
        return "dependency lockfile"
    return None


def main() -> int:
    paths = changed_paths()
    if not paths:
        print("AUTONOMY_GUARD: no changed files")
        return 0

    blocked: list[tuple[str, str]] = []
    for path in paths:
        reason = reason_for_block(path)
        if reason:
            blocked.append((path, reason))

    print("AUTONOMY_GUARD changed paths:")
    for path in paths:
        print(f"  - {path}")

    if blocked:
        print("\nAUTONOMY_GUARD: BLOCKED", file=sys.stderr)
        for path, reason in blocked:
            print(f"  - {path}: {reason}", file=sys.stderr)
        return 42

    print("\nAUTONOMY_GUARD: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
