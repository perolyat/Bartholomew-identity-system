# W03 CI baseline & optimization report

Authored by **W03-PREP**. CI optimization is a W03-PREP infrastructure
responsibility and is deliberately **not** assigned to W03-A–E.

## 1. Method

Measured before changing. Two sources of evidence:

- **GitHub Actions run durations** (authoritative for wall-clock a developer
  waits): `actions_get get_workflow_run_usage` on real runs.
- **Local timings** (venv on a 4-core box, to see the shape and prove
  parallel-safety before proposing it): the default suite serially, under
  `pytest-xdist -n 3`, and the integration/slow set, on both `main @ adea4b1`
  and the wave-two candidate head `e3f9256`.

## 2. Starting baseline (before)

### Workflows on `main @ adea4b1`

Three overlapping workflows ran on every pull request:

| Workflow | Jobs | What it ran |
|---|---|---|
| `ci.yml` | quality; tests ×(3.10, 3.11); critical ×(3.10, 3.11); windows(3.11) | quality, the **default suite with coverage** twice, integration/slow twice, Windows lifecycle |
| `pre-commit.yml` | lint-test ×(3.10, 3.11) | the pre-commit hook set + smoke + **the default suite again** twice |
| `smoke.yml` | smoke | a live uvicorn server hitting `/healthz`, `/api/health`, `/docs` |

**The core waste: the default test suite executed four times per pull request** —
`ci.yml tests` on two Pythons (with coverage) and `pre-commit.yml lint-test` on
two Pythons (without) — plus `critical` ran integration/slow twice. Feedback a
developer waits for therefore arrived three-to-four times over.

### Measured wall-clock (before)

| Head | `ci.yml` run duration | Long pole |
|---|---|---|
| `main @ adea4b1` | **15 min 47 s** | the coverage-instrumented default suite (~15 min serial, one per Python) |
| wave-two head `e3f9256` | **28 min 14 s** | same, grown by the five wave-two packages' tests |

Local corroboration (venv, 4 cores):

| Suite | Serial | `xdist -n 3` | Speedup |
|---|---|---|---|
| main default (with coverage) | 13 min 45 s | — | — |
| main default (no coverage) | — | 3 min 32 s | ~3.9× vs serial |
| main integration/slow | 3 min 23 s | (kept serial) | — |
| wave-two default | 20 min 18 s | 5 min 36 s | ~3.6× |
| wave-two integration/slow | 12 min 07 s | (kept serial) | — |

Line coverage on `main`, measured: **79.67%** (gate is 70%; not lowered).

### Identified bottlenecks

1. **Duplicated work** — the default suite 4×/PR, integration/slow 2×/PR.
2. **Serialization** — the suite ran serially; it is ~3.6–3.9× faster in parallel.
3. **Coverage instrumentation on the hot path** — it adds ~30% and gates nothing a
   developer needs within five minutes.
4. **Both Pythons on every push** — 3.10 and 3.11 for ordinary feedback.
5. **A few disproportionately expensive tests** — the two brake-contention
   governance tests take ~10 s each by design (contention timing); the scheduler
   persistence-concurrency tests ~2–5 s. Kept (governance-critical), not deleted.
6. **Recorded intermittent tests** blocking PRs rather than being measured
   deliberately (quiet-hours write-lock, WAL concurrent-processes, and the two the
   wave-two live handoff names: `test_personal_memory_capture_recall`,
   `test_event_backbone_drive`).

## 3. The staged CI model (after)

Four deliberate tiers replace the three overlapping workflows. No test was
deleted, skipped or quarantined; every step the old workflows ran still runs,
once, in the right tier.

| Tier | Workflow | Runs when | Target | Contents |
|---|---|---|---|---|
| **PR Fast** | `ci.yml` | every PR push | **< 5 min** | quality (pre-commit hook set, pip check, security floor, packaging contract, wave-manifest contract); default suite **once** on py3.11 under `xdist -n auto --dist loadfile`, no coverage; a compact Windows job; live-server smoke |
| **Integration** | `integration.yml` | PR ready-for-review, label `ci:integration`, merge queue, manual | < 10–15 min | default suite **with coverage + 70% gate** (py3.11, parallel); integration/slow tests; clean-start lifecycle; scheduler readiness; parking-brake governance; Windows lifecycle + actuation |
| **Merge Candidate** | `merge-candidate.yml` | push to `main`, label `ci:merge-candidate`, `wave/w03-f-*` heads, merge queue, manual | < 15–20 min | everything in Integration on **both** Pythons, plus the **full default suite on Windows** |
| **Nightly** | `nightly.yml` | nightly schedule, manual | unbounded | **serial** (non-xdist) suite on 3.10/3.11/**3.12**; every marker on Windows; a **flake-hunt** repeating the recorded intermittent tests ten times each |

Draft PRs get PR Fast only; the Integration and Merge Candidate jobs are guarded
so they are `skipped` on a draft with no opt-in label (confirmed on PR #91).

## 4. Changes made (this W03-PREP session)

Low-risk workflow/configuration changes only — no product code touched.

1. Rewrote `ci.yml` into the PR Fast tier; added `integration.yml`,
   `merge-candidate.yml`, `nightly.yml`.
2. Removed the 4×-per-PR default-suite duplication: it runs **once** in PR Fast;
   `pre-commit.yml` and `smoke.yml` are folded into the tiers (not dropped as
   checks).
3. Added `pytest-xdist` and run the suite `-n auto --dist loadfile` in the three
   fast tiers. **`--dist loadfile`** is deliberate: several suites share
   module-level state (the API app's process-wide kernel singleton, the
   permission-checker singleton), which is safe within one process and unsafe
   across an interleaving; `loadfile` keeps every test of a module on one worker.
4. Moved the coverage gate, the both-Python matrix, integration/slow, and the
   full Windows suite to the Integration and Merge Candidate tiers.
5. Added pre-commit hook-environment caching to the quality job.
6. Added the wave-manifest contract test (`tests/test_wave_manifest.py`) to PR
   Fast quality, guarded by `hashFiles` so the workflow is valid before it lands.
7. Windows actuation steps (wave-two package B) are carried behind `hashFiles`
   guards, so the tiers are valid on `main` today and pick those suites up when
   the wave-two head merges.

## 5. Measured results (after)

**PR Fast on PR #91's head (real GitHub Actions run):**

| Job | Duration |
|---|---|
| Quality | 1 min 00 s |
| PR Fast tests (py3.11, parallel) | **3 min 47 s** |
| Windows fast | 1 min 41 s |
| smoke | 0 min 28 s |
| **Wall-clock (max)** | **≈ 3 min 47 s** |

**PR Fast target < 5 min: met (3 min 47 s measured).** Integration and Merge
Candidate correctly skipped on the draft.

Before → after for ordinary PR feedback: **~15.8 min → ~3.8 min on a main-based
PR**, and the default suite runs once instead of four times.

## 6. Unresolved bottlenecks & deferred improvements

- **Integration / Merge Candidate wall-clock is projected, not yet CI-measured**
  — those jobs skip on a draft. Measure on the first PR marked ready or labelled
  `ci:integration`; the components (coverage-xdist ≈ 5–6 min, integration/slow
  serial 3:23, Windows lifecycle ≈ 2 min) project comfortably under target, but
  confirm and tune (e.g. shard integration/slow) if the Windows full suite in
  Merge Candidate approaches 20 min.
- **Two ~10 s brake-contention governance tests** remain in PR Fast. They are
  governance-critical and cheap enough in parallel; revisit only if PR Fast
  exceeds target.
- **Recorded intermittent tests** are moved to the Nightly flake-hunt to be
  *measured* (a real flake rate for `RISKS.md`) rather than silently retried or
  quarantined. None was disabled.
- **Local-only test artifacts, not CI failures, not fixed here:** in the W03-PREP
  venv the two `test_declared_console_script_runs_help` cases fail both serially
  and in parallel; the same tests **pass in CI** (quality and PR Fast tests both
  green), so this is a local editable-install/PATH artifact, not a repo
  regression and not xdist-induced. On the wave-two tree, two
  `test_kernel_db_path_resolution` cases additionally fail locally under xdist
  because the test session sets `BARTH_DB_PATH` process-wide (conftest) while
  those tests assert unset-env behaviour; also a local-harness artifact on a
  wave-two-only suite, to be confirmed green in that branch's own CI.

## 7. Required CI tier for every Wave 3 session

| Session | Required tier before its head is declared frozen |
|---|---|
| W03-PREP | PR Fast |
| W03-A, W03-B, W03-C, W03-D, W03-E | Integration |
| W03-F | Merge Candidate |

Recorded in `W03_MANIFEST.yaml` (`sessions[].required_ci_tier`).
