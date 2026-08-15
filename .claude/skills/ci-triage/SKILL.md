---
name: ci-triage
description: Diagnose CI failures and plan test coverage for Bartholomew using CI.md and TEST_MATRIX.md. Use when a CI job fails, before claiming tests pass or a PR is mergeable, when deciding which tests a change needs, when a failure looks flaky or Windows-specific, or when reproducing CI locally.
---

# CI triage and test coverage

Two canonical docs: `CI.md` (what CI runs, how to reproduce, the Gatekeeper) and `TEST_MATRIX.md`
(what is covered and what a new subsystem must add).

## Before claiming anything passes

**A bare `pytest` is not the full suite.** `pyproject.toml` sets
`addopts = "-q -m 'not integration and not slow'"`, deselecting 3 of 915 tests. Saying "all tests
pass" after a bare `pytest` is false.

```bash
pytest                          # default — deselects integration + slow
pytest -m ""                    # genuinely everything
pytest -m "integration or slow" # what ci.yml's `critical` job runs
```

**Lint must use the pinned versions** in `requirements-dev.txt` (`ruff==0.14.3`,
`black==26.3.1`). An unpinned newer ruff reports rules the pinned hook does not — this once made a
tree simultaneously clean locally and 68-errors in CI. The SessionStart hook puts the venv on
`PATH` so this resolves correctly; verify with `ruff --version` if a lint result looks surprising.

## The four ci.yml jobs

`quality` (format/lint/packaging contract) → `tests` (3.10 + 3.11, coverage ≥70%) → `critical`
(the integration/slow tests the default expression excludes, 3.10 + 3.11) → `windows` (3.11
lifecycle/file-handle behaviour). Cheapest first, so failures surface fast.

Merge requires all four **plus** `pre-commit.yml` and `smoke.yml` — see `## CI Gatekeeper
Definition` in `CI.md`.

## Triage order

1. **Which job?** The job name tells you the class of failure before reading any log.
2. **Environmental or real?** `CI.md`'s `## Common Failure Patterns` splits these explicitly.
   Windows `PermissionError` (WinError 32) in teardown is a known SQLite WAL file-handle quirk with
   retry fixtures in `conftest.py` — it is *not* a logic bug, and `RISKS.md` R4 warns that treating
   it as noise can also mask real failures. Read the actual assertion before deciding.
3. **Reproduce locally** using `CI.md`'s `## Full CI simulation`, not an approximation of it.
4. **Coverage gate** is line ≥70% across all first-party packages — see `## Coverage Gates`.

## Test coverage for a change

Grep `TEST_MATRIX.md` for the subsystem before writing tests — it is organised by subsystem
(`### Consent gates + governance`, `### Redaction`, `### Encryption`, `### FTS + hybrid
retrieval`, `### Parking brake`, `### Consent-bypass red team (RISKS.md R1)`, and others).

`## When adding a new subsystem` states the required baseline. Anything touching consent, the
parking brake, or retrieval filtering needs a bypass test, not just a happy-path test.

## Rules

- Report failures with the actual output. Never characterise a red CI as passing or "just flaky"
  without evidence from `## Common Failure Patterns`.
- A quarantined test needs an explicit justification — see `## Quarantine Strategy`.
- If a failure reproduces on the base branch, say so plainly; that is a pre-existing failure, not
  yours, and it still needs stating rather than silence.
