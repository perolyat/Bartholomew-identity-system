# CHECKLISTS

> Operational and engineering checklists. If it’s not checked, it’s not real.
>
> **Last updated:** 2026-01-19

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
- [ ] `pytest -q` passes (or quarantined with justification)
- [ ] `ruff check .` passes
- [ ] `black --check .` passes
- [ ] Docs updated (canonical docs if behavior/interface changed)
- [ ] Rollback note included for risky changes
- [ ] No new bypass paths introduced (consent gate / parking brake)

## Release checklist (Stage gate)

- [ ] Gate exit criteria in `ROADMAP.md` met
- [ ] `REVIEWS.md` stage review completed
- [ ] Audit log sanity check performed
- [ ] Known issues documented (with explicit scope)


## Prompt hygiene (agent execution)
- PASS/BLOCKED: Prompts do **not** paste huge transcripts.
- PASS/BLOCKED: Large sources are referenced as files; work is chunked with intermediate artifacts.
- PASS/BLOCKED: Each chunk has acceptance + verification and can be re-run deterministically.
