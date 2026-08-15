# CLAUDE.md — operating rules for AI coding sessions

This file is loaded automatically at the start of every Claude Code session in this repository.
It is a **navigation and rules summary**, not an authority. Where it disagrees with a canonical
document, **the canonical document wins** and this file should be corrected.

---

## 1. What this project is

Bartholomew is a privacy-preserving, consent-first cognitive architecture ("Bartholomew's Brain"):
identity, durable memory, an Experience Kernel (self-model + narrator), governed planning, and
fail-closed safety. Python, FastAPI, SQLite (WAL), pytest. Single maintainer.

Entry point for status is always `MASTER_PLAN.md`. Never infer project status from `README.md`,
implementation notes, or anything under `docs/` except `docs/TILT.md`.

---

## 2. The approval gate — read this before doing anything

**No code or canonical-doc change is committed without explicit user authorization for that
specific change.** This is `MASTER_PLAN.md`'s "Doc Governance" and `CHECKLISTS.md`'s "Commit
authorization checklist", and it is not a formality.

In practice, for this repo:

- A roadmap or backlog item being *listed* is **sequencing, not authorisation**. `MASTER_PLAN.md`'s
  "Next 3 Moves" says this explicitly: each step requires its own separate, explicit approval
  before work begins.
- Present a diff or a summary **before** committing, not after.
- Do not batch an unapproved change in with an approved one.
- Investigation, reading, running tests, and drafting are always fine without approval. Writing to
  tracked files and committing are not.

If you are unsure whether something is approved: it is not. Ask.

---

## 3. Current execution priority (TILT)

`docs/TILT.md` is canonical and binding on near-term sequencing. Its test, applied to every
proposed piece of work:

> **What real Bartholomew capability does this unlock for the tester?**

If the honest answer is "cleaner architecture", "more complete documentation", "additional
abstraction", "future-proofing", "better theoretical correctness", or "polish" — it is **deferred**.

A shippable vertical slice may only be held back for one of six reasons: a defect threatening
**safety, governance, privacy, data integrity, architectural validity, or the validity of the
experiment itself**. Nothing else qualifies.

**Where the project actually is:** slice 1 (Personal Memory Capture and Recall) is implemented and
merged. The next move is *putting it into real use* — configuring `BARTHOLOMEW_NOTIFY_WEBHOOK_URL`
and running the capture/recall loop against a real endpoint — not more slice-1 engineering.
Proposals to harden slice 1 should expect to lose to that, and you should say so.

---

## 4. Canonical documents (14) — the only SSOT

| Document | What it governs |
|---|---|
| `MASTER_PLAN.md` | **Start here.** Stage status, backlog, approval ledger, Next 3 Moves |
| `CONSTITUTION.md` | Development philosophy, product/identity principles |
| `COGNITIVE_RUNTIME.md` | How the runtime loop actually thinks |
| `ROADMAP.md` | Stage gates, workstreams, exit criteria and exit evidence |
| `docs/TILT.md` | Current execution-sequencing priority (see §3) |
| `DECISIONS.md` | Decision log / ADRs — every architectural choice and its alternatives |
| `RISKS.md` | Risk register + tech-debt watchlist (open findings, e.g. F9) |
| `ASSUMPTIONS.md` | Stated assumptions and their status |
| `INTERFACES.md` | Contracts between subsystems |
| `CHECKLISTS.md` | PR/DoD gate, commit authorization, safety invariants |
| `REVIEWS.md` | Review records |
| `CI.md` | What CI runs, how to reproduce locally, Gatekeeper definition |
| `TEST_MATRIX.md` | Test coverage expectations |
| `PERF_BUDGETS.md` | Performance budgets |

Everything else — this file included — is a **reference**. Two locations are permanently
non-authoritative by design: `docs/incubator/` (unapproved ideas) and `docs/archive/` (superseded
history).

**These files are large** (`DECISIONS.md` ~163KB, `ROADMAP.md` ~94KB, `COGNITIVE_RUNTIME.md` ~62KB,
`CONSTITUTION.md` ~51KB). Do not read them end to end. Grep for the specific section or entry, then
read that region. Reading a whole canonical doc to answer a narrow question is a context-budget bug.

**Routing skills do this for you.** Five skills in `.claude/skills/` map the large docs and load
themselves when the work matches — they hold section anchors and grep strategy, not copies of the
content, so there is nothing to keep in sync:

| Skill | Routes to | Fires when |
|---|---|---|
| `runtime-map` | `COGNITIVE_RUNTIME.md` | runtime loop, Experience Kernel, memory/reflection, subsystem ownership |
| `interfaces` | `INTERFACES.md` | changing a signature, schema, route, or audit record |
| `ci-triage` | `CI.md`, `TEST_MATRIX.md` | CI failures, test planning, before claiming tests pass |
| `product-principles` | `CONSTITUTION.md` | product/identity questions — *should we*, not *how* |
| `risk-check` | `RISKS.md`, `ASSUMPTIONS.md` | finding a defect, or relying on an unvalidated premise |

---

## 5. Commands that are actually correct here

```bash
# Environment
python -m venv .venv && source .venv/bin/activate   # Linux/macOS
pip install -e . && pip install -r requirements.txt -r requirements-dev.txt

# Tests — READ THIS
pytest                    # deselects integration+slow (912 of 915) — NOT everything
pytest -m ""              # genuinely everything
pytest -m "integration or slow"   # what ci.yml's `critical` job runs
pytest -q -m smoke        # fast sanity

# Quality — must be the PINNED versions from requirements-dev.txt / .pre-commit-config.yaml
ruff check .
black --check .
mypy .                    # optional

# Run the app
uvicorn app:app --reload --port 5173
curl http://127.0.0.1:5173/api/health
```

### Known traps

- **`pytest` alone is not the full suite.** `pyproject.toml` sets
  `addopts = "-q -m 'not integration and not slow'"`. Claiming "all tests pass" after a bare
  `pytest` is a false statement. Use `pytest -m ""`.
- **Unpinned `ruff` lies.** A newer ruff than the pinned hook reports rules the hook does not.
  This once made a tree simultaneously clean locally and 68-errors in CI. Always use the pinned
  version.
- **`barth` is not a real command.** `setup.py` declares it but `pyproject.toml` is the manifest
  that installs, and it declares `bartholomew` and `bartholomew-backfill-fts`. The competing-manifest
  problem is open finding **F9** in `RISKS.md`. Use `python -m identity_interpreter.cli` or the
  `bartholomew` entry point.
- **Windows `PermissionError` (WinError 32)** in teardown is a SQLite WAL file-handle quirk, not a
  logic bug. `conftest.py` has retry fixtures. Do not "fix" it by changing product code.
- **DB path** resolves from `BARTH_DB_PATH`, else `data/barth.db` under the project root.

---

## 6. Definition of Done

A change is done only when all of these hold (`MASTER_PLAN.md` DoD + `CHECKLISTS.md` PR gate):

- Implementation complete; acceptance criteria stated and verified.
- Tests added/updated and passing — both the default suite **and** `integration or slow`.
- `ruff check .` and `black --check .` pass at pinned versions.
- All `ci.yml` jobs green: `quality`, `tests` (3.10 + 3.11, coverage ≥70%), `critical` (3.10 + 3.11),
  `windows` (3.11). Plus `pre-commit.yml` and `smoke.yml`.
- No new undeclared runtime dependency (`tests/smoke/test_packaging_contract.py` enforces this).
- Canonical docs updated if behaviour or interfaces changed.
- **Governance not regressed**: no new bypass path around the consent gate or parking brake;
  redaction/encryption/retention rules intact.
- No secrets or credentials staged — applies identically to human and AI-authored changes.
- Rollback note included for risky changes.

---

## 7. Governance invariants — never regress these

1. **Fail-closed.** No irreversible action without an explicit gate. Parking-brake semantics hold
   per subsystem (skills / sight / voice / scheduler / global) and at the authority tiers recorded
   in `docs/SAFETY_PARKING_BRAKE.md`.
2. **Privacy-first.** Redaction before storage where required; encryption at rest for sensitive
   kinds/fields; consent gating for "ask before store" classes; enforceable, testable retention/TTL.
3. **Verification-first.** If it cannot be verified by tests, logs, or replay, it is not shipped.
4. **No doc sprawl.** Do not create a new top-level `*.md` to explain a change. Extend the relevant
   canonical doc, or add an implementation note under `docs/` and link it from the canonical doc.
   This repo has already run two documentation-reconciliation passes to undo sprawl.

---

## 8. Working style for agent sessions

- **Grep before reading.** See §4 on document size.
- **Smallest safe slice.** `MASTER_PLAN.md`'s backlog is explicitly ordered as "smallest safe
  slices". Match that granularity; do not bundle.
- **Chunk with artifacts.** Per `CHECKLISTS.md` prompt hygiene: reference large sources as files
  rather than pasting them, and make each chunk independently re-runnable with its own acceptance
  criteria.
- **Record decisions where they belong.** An architectural choice goes in `DECISIONS.md` with its
  alternatives; a newly discovered defect or debt goes in `RISKS.md`; a new assumption goes in
  `ASSUMPTIONS.md`. A decision that only lives in a commit message is lost.
- **Correct drift when you find it.** This repo's convention is a dated, explicit correction note
  (`*Corrected 2026-07-28: ...*`) rather than a silent edit. Follow it — the history of a
  correction is part of the record.
- **Branch discipline.** Develop on the assigned `claude/*` branch. Never push to `main`.

---

## 9. External tooling

Connectors and plugins configured for this project, and the rules for using them, are documented
in `docs/TOOLING.md`. Read it when a task involves Linear, issue tracking, external docs lookup,
or the ledger tables — it defines which system owns which fact, so the repo and the trackers do
not drift apart.
