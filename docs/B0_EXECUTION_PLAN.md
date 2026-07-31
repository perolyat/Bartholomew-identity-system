# B0 Execution Plan — Verified Persistence Baseline

> **Status:** DRAFT — awaiting explicit user approval before execution begins, per
> `docs/PHASE_B_OVERVIEW.md` §9 (Approval Model) and `CHECKLISTS.md`'s "Staged workstream approval"
> checklist. Approval of `docs/PHASE_B_OVERVIEW.md` authorised only the existence and shape of the
> B0–B9 structure; it did not authorise this plan or any B0 work. This plan authorises B0's
> diagnostic work only — it does not authorise B1 or any later stage.
>
> **Authority:** subordinate to `docs/PHASE_B_OVERVIEW.md` (canonical stage definition) and
> `ROADMAP.md` (canonical status). `docs/PHASE_B_RISK_MAP.md`'s B0 row is the starting inventory
> this plan revalidates, not a source of authority by itself.

## 1. Objective

Produce a concise, repository-grounded current-state report covering the facts later Phase B
stages (B1–B9) depend on, per `docs/PHASE_B_OVERVIEW.md`'s B0 scope. **No production code, schema,
or test changes are made in B0.** B0 characterises; it does not fix.

## 2. Scope — investigation areas and method

Each area below is investigated by direct repository reading/grep and, where noted, by running the
*existing* test/CI suite read-only (observing behavior, not adding tests). Every finding in the
resulting report must cite exact `file:line` evidence — no claim carried over from
`docs/PHASE_B_RISK_MAP.md` or the archived research doc is trusted without re-verification here.

| # | Area | Method |
|---|---|---|
| a | Actual DB path(s) | Grep `config/` and `bartholomew/` for the default DB path (e.g. `data/barth.db`), env-var overrides, and every place a path is constructed, to confirm there is (or isn't) exactly one resolved path in practice. |
| b | SQLite connection owners | Enumerate every module that opens a `sqlite3`/`aiosqlite` connection: `MemoryStore` (`aiosqlite`), the scheduler's `SchedulerStore` (sync `sqlite3` on its dedicated worker thread), `persona_pack.py`, `narrator.py`, `bartholomew/kernel/db_ctx.py`, `bartholomew_api_bridge_v0_1/services/api/db_ctx.py`, and any others a fresh repo-wide grep for `sqlite3.connect(`/`aiosqlite.connect(` turns up. Record each site's file:line and its connection policy (pragmas, timeout, sync mode) as-is today. |
| c | Synchronous DB work reachable from the event loop | Grep for synchronous `sqlite3` calls inside `async def` call chains (known candidates: `persona_pack.py`, `narrator.py` per `DECISIONS.md`'s 2026-07-24 entry) and confirm which call sites, if any, remain unresolved since that fix. |
| d | Current WAL/checkpoint behaviour | Read both `db_ctx.py` copies. Confirm `bartholomew/kernel/db_ctx.py`'s `wal_db()` default (`checkpoint=None`, per the 2026-07-24 `DECISIONS.md` entry) still holds, and identify every remaining unconditional-checkpoint-per-call site — `DECISIONS.md` names the API bridge's `liveness.py`/`db.py` as untouched by that fix; confirm or correct that against current code. |
| e | Real Parking Brake construction sites | Grep for `ParkingBrake(` instantiations repo-wide. Classify each as live-daemon (in scope for B4) or standalone-CLI-process (`bartholomew/cli.py`, in scope for B6), per the split `docs/PHASE_B_RISK_MAP.md` already records (7 sites / 3 CLI at archive time) — re-count against the current repository rather than assuming that count still holds. |
| f | Real API/CLI ingress | Enumerate FastAPI routes in the API bridge and CLI entry points in `bartholomew/cli.py` as the actual external-ingress inventory later needed by B7. `docs/PHASE_B_RISK_MAP.md` records 5 real routes at archive time — re-verify. |
| g | Actual daemon/API process topology | Trace how the daemon and API layer relate at runtime (same process vs. separate) — `KernelDaemon` in `bartholomew/`, FastAPI app/lifespan in `bartholomew_api_bridge_v0_1/`. |
| h | Startup/shutdown order | Trace `KernelDaemon.start()`/`stop()`'s actual current sequence (schema init, `MemoryStore`, scheduler schema/task creation, experience kernel, narrator, skills), building on the ordering already fixed by the S5.0 `DECISIONS.md` entry (2026-07-25) — confirm it still matches. |
| i | Supported Python/Windows behaviour | Read `CI.md`'s matrix and `pyproject.toml`'s Python version constraints; note any Windows-specific skips/xfails in the test suite (e.g. `test_fixtures_windows.py`, `test_sqlite_wal_cleanup.py`). |

## 3. Deliverable

One new document, `docs/B0_BASELINE_REPORT.md`, containing:

1. A dated finding for each area (a)–(i) above, each claim cited to `file:line`.
2. Two inventory tables: SQLite connection owners (area b), and Parking Brake construction sites
   (area e) — mirroring `docs/PHASE_B_RISK_MAP.md`'s row format so later stages can diff against it.
3. An explicit "confirmed / revised / new since archive" note against every `docs/PHASE_B_RISK_MAP.md`
   B0 row, so drift from the archived research is visible rather than silently inherited.
4. An "open questions for B1" section for anything B0 surfaces that doesn't cleanly resolve without
   a design decision (B0 records the question; it does not answer it).

## 4. Non-goals

- No code, schema, or production config changes.
- No new tests. Existing tests/CI may be *run* to observe current behaviour (e.g. confirming the
  Windows CI leg's actual skip behaviour) but B0 does not add or modify test files.
- No fixes to anything found, however small — even an apparently trivial one-line fix is out of
  scope and gets recorded as a finding for the owning later stage instead.

## 5. Verification

Every finding must be independently re-creatable from the cited `file:line` — a reviewer should be
able to re-run the same grep/read and get the same answer. Before the report is presented for
approval, I will re-run the connection-owner and Parking-Brake-construction-site greps a second time
from a clean shell to guard against a stale in-context assumption.

## 6. Exit / handoff

B0 exits when `docs/B0_BASELINE_REPORT.md` is written, self-consistent, and approved. That approval
closes B0 only — it does not authorise B1's plan or any implementation. `ROADMAP.md`'s Phase B
section is updated to record B0 as complete, with a link to the report.

## 7. Approval gates on this plan

- This plan document itself requires explicit user approval before I begin executing §2's
  investigation steps.
- The resulting `docs/B0_BASELINE_REPORT.md` (and the `ROADMAP.md` status update) requires its own
  separate, explicit approval before commit, per `DECISIONS.md`'s "User Approval Gate" and
  `CHECKLISTS.md`'s "Commit authorization checklist" — plan approval does not pre-approve the
  commit.
