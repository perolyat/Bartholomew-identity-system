# CHECKLISTS

> Operational and engineering checklists. If it’s not checked, it’s not real.
>
> **Last updated:** 2026-07-27 (planning-document reconciliation: the PR checklist's
> "`pytest -q` passes" item was misleading — that command deselects 3 tests — and the checklist
> predated the `ci.yml` gates entirely)

## Non-negotiables checklist (Before “ready to Act”)

Mark each as **PASS** or **BLOCKED**.

- **Realism:** We can run it end-to-end on a clean machine.
- **Governance preserved:** parking brake + consent gates enforced; fail-closed behavior.
- **Privacy respected:** redaction before storage; encryption where required; no sensitive logs.
- **Verification included:** tests + repro commands documented.
- **Change control:** major changes include impact + migration + rollback.
- **Interfaces updated:** `INTERFACES.md` updated if contracts changed.
- **Assumptions logged:** unresolved assumptions tracked in `ASSUMPTIONS.md`.
- **Risks assessed:** updated `RISKS.md`.
- **CI plan:** `CI.md` gates updated or explicitly unchanged.

## PR checklist (DoD gate)

- [ ] Acceptance criteria stated in PR description
- [ ] Tests added/updated
- [ ] `pytest -q` passes — **note this deselects `integration`/`slow` tests**; also run
      `pytest -m "integration or slow"`, which is what `ci.yml`'s `critical` job does
- [ ] `ruff check .` and `black --check .` pass **at the pinned versions** in
      `requirements-dev.txt` / `.pre-commit-config.yaml` (an unpinned newer ruff reports rules
      the pinned hook does not — this made one tree simultaneously "clean" locally and "68
      errors" in CI)
- [ ] All `ci.yml` jobs green: `quality`, `tests` (3.10 + 3.11, coverage gate ≥70%),
      `critical` (3.10 + 3.11), `windows` (3.11)
- [ ] No new undeclared runtime dependency (`tests/smoke/test_packaging_contract.py` enforces this)
- [ ] Docs updated (canonical docs if behavior/interface changed)
- [ ] Rollback note included for risky changes
- [ ] No new bypass paths introduced (consent gate / parking brake)
- [ ] User approval obtained for all doc/code changes before commit
- [ ] Changes presented with clear diff/summary for review

## Release checklist (Stage gate)

- [ ] Gate exit criteria in `ROADMAP.md` met
- [ ] `REVIEWS.md` stage review completed
- [ ] Audit log sanity check performed
- [ ] Known issues documented (with explicit scope)

## Commit authorization checklist

Every `git commit` requires:
- PASS/BLOCKED: User has explicitly reviewed proposed changes
- PASS/BLOCKED: User has authorized the commit (verbal/written confirmation)
- PASS/BLOCKED: No autonomous commits without human approval
- PASS/BLOCKED: Changes align with stated task objectives
- PASS/BLOCKED: Commit performer — commits are executed only after user approval, by the user or an explicitly supervised session

## Prompt hygiene (agent execution)
- PASS/BLOCKED: Prompts do **not** paste huge transcripts.
- PASS/BLOCKED: Large sources are referenced as files; work is chunked with intermediate artifacts.
- PASS/BLOCKED: Each chunk has acceptance + verification and can be re-run deterministically.
