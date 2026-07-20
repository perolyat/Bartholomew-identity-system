# MASTER_PLAN

> **Single Source of Truth (SSOT)** for what Bartholomew is, what matters, where we are, and what we do next.
>
> **Last updated:** 2026-01-19 (Australia/Sydney)

## Vision / North Star

Build a practical, privacy-preserving, consent-first cognitive architecture (“Bartholomew’s Brain”) that:

- Enforces identity, safety, and governance constraints from configuration (`Identity.yaml`, policy/memory rules).
- Maintains durable memory with redaction, encryption, consent gates, retention, and auditability.
- Implements an **Experience Kernel** (self-model + narrator) to maintain continuity and growth over time.
- Plans and nudges safely (fail-closed) and can later graduate into controlled “Act” capabilities.

## Non-negotiables

1. **Fail-closed governance**
   - No irreversible actions without an explicit gate.
   - Parking-brake semantics for subsystems (skills/sight/voice/scheduler/global).

2. **Privacy-first data handling**
   - Redaction before storage where required.
   - Encryption at rest for sensitive kinds/fields.
   - Consent gating for “ask before store” classes.
   - Retention/TTL rules must be enforceable and testable.

3. **Verification-first engineering**
   - If it can't be verified (tests/logs/replay), it's not shipped.
   - Changes that alter interfaces/governance must update docs + tests.

4. **No doc sprawl**
   - Canonical docs are the only SSOT: see links below.

## Doc Governance

All canonical documentation changes follow strict governance:

1. **User approval required**: No doc or code changes are committed without explicit user authorization.
2. **Change presentation**: Proposed changes must be shown via diff or summary before commit.
3. **Traceability**: Each commit must map to an approved task or explicit user request.
4. **Rollback readiness**: User can revert any change via `git checkout -- <files>` or `git revert <commit>`.

See [DECISIONS.md](DECISIONS.md) for the "User Approval Gate" decision and [CHECKLISTS.md](CHECKLISTS.md) for commit authorization checklist.

## Canonical docs

- **MASTER_PLAN.md** (this doc)
- [ROADMAP.md](ROADMAP.md)
- [DECISIONS.md](DECISIONS.md)
- [RISKS.md](RISKS.md)
- [ASSUMPTIONS.md](ASSUMPTIONS.md)
- [INTERFACES.md](INTERFACES.md)
- [CHECKLISTS.md](CHECKLISTS.md)
- [REVIEWS.md](REVIEWS.md)
- [CI.md](CI.md)
- [TEST_MATRIX.md](TEST_MATRIX.md)
- [PERF_BUDGETS.md](PERF_BUDGETS.md)

## Current architecture

### Text diagram (high-level)

```
Identity.yaml + config/*.yaml
        |
        v
identity_interpreter/   (validation, normalization, policy engines)
        |
        v
bartholomew/kernel/     (daemon, planner, memory store, governance engines)
  |   |      |   |  \
  |   |      |   |   \
  |   |      |   |    +-- retrieval (FTS / vector / hybrid)
  |   |      |   +------- encryption / redaction / summarization
  |   |      +----------- consent gate + memory rules
  |   +------------------ event bus + metrics
  +---------------------- SQLite DB (data/barth.db)

bartholomew_api_bridge_v0_1/ (FastAPI surface over kernel + DB)

exports/ (audits, sessions)
logs/    (runtime logs)
```

### Key invariants

- **Identity.yaml is the governing config** for routing, safety, and persona/behavior constraints.
- **Single SQLite DB** is the shared persistence backbone.
- **Consent + privacy gates** pre-filter retrieval results before they reach callers.
- **Parking brake** provides an emergency/operational kill-switch by scope.

## Stage gates / milestones

**Completed**
- **Stage 0: Kernel alive + stable + dreaming** (see `STAGE_0_COMPLETION.md`).

**In progress (engineering reality)**
- **Governance hardening (Phase 2A–2D)**: redaction, encryption, summarization, embeddings, vector store, FTS + hybrid retrieval.
  - Current snapshot includes known failing tests and platform variability (see `docs/STATUS_2025-12-29.md`).

**Next gate (the next thing that should be “done” end-to-end)**
- **Gate: “Green core on CI Linux”**
  - A minimal, reproducible CI run that is green on Linux for the core governance + memory + retrieval path.
  - Windows-only flakiness is tolerated only if clearly quarantined.

See [ROADMAP.md](ROADMAP.md) for concrete exit criteria.

## Backlog (prioritized, smallest safe slices)

> **Rule:** every item must have acceptance criteria + verification steps before it is started.

### P0 — Packaging & Architecture (Pre-requisites)

> **Source:** Cline audit 2026-01-22 verifying ChatGPT repo analysis

0. ✅ **Missing package `__init__.py`** — Fixed 2026-07-20.
   - `bartholomew/` directory has no `__init__.py` file.
   - **Acceptance:** `bartholomew/__init__.py` exists; `pip install -e .` succeeds.
   - **Verify:** `python -c "import bartholomew"` works.
   - **DoD:** File created, editable install tested.
   - **Risk if skipped:** Package is not installable; imports fail.
   - **Note:** also added `bartholomew/kernel/memory/__init__.py`, which had the same
     implicit-namespace-package gap.

1. ✅ **Dependency consolidation to pyproject.toml** — Fixed 2026-07-20.
   - `pyproject.toml` missing runtime deps that exist in `requirements.txt`: `numpy`, `cryptography`.
   - `typer`, `rich` used in CLI but not declared.
   - **Acceptance:** `pyproject.toml` is single source of truth for all deps.
   - **Verify:** `pip install .` installs all deps; no manual `requirements.txt` needed.
   - **DoD:** All runtime deps in `[project.dependencies]`; `requirements.txt` mirrors or deprecated.
   - **Risk if skipped:** Dependency drift; CI/CD failures.
   - **Note:** verification (fresh venv install + test collection) also turned up two more
     undeclared runtime imports not in the original audit: `jsonschema` (used by
     `identity_interpreter/loader.py`) and `requests` (used by
     `identity_interpreter/adapters/llm_stub.py`), plus `pydantic`'s `EmailStr` needing the
     `email` extra (`identity_interpreter/models.py`). All four are now declared in both
     `pyproject.toml` and `requirements.txt`.
   - **Follow-up fixed 2026-07-20:** pinned `fastapi>=0.104,<0.121` in `pyproject.toml` and
     `requirements.txt` (that ceiling keeps `starlette` on the `0.4x` line; `fastapi>=0.121`
     pulls `starlette>=1.0`, which breaks `starlette.testclient`'s implicit `httpx`
     dependency — reproduced and confirmed on a clean venv). Also re-encoded
     `requirements.lock` from UTF-16 to UTF-8/LF so it's actually readable/usable, and added
     `httpx`/`freezegun` to `requirements-dev.txt` (both were imported by tests but
     undeclared, so a clean dev install couldn't even collect the test suite).
   - **New follow-up found while fixing this, root-caused and fixed 2026-07-20:** with the
     dependency set actually installing, `pytest -q -m smoke` (the full suite together, as
     CI's `lint-test` job runs it) hung rather than failed. `pytest-timeout` (`timeout = 120`,
     `timeout_method = "thread"` in `pyproject.toml`) was added first as a safety net so this
     fails fast with a clear traceback instead of hanging the CI job indefinitely.
     Root cause (confirmed via `faulthandler` thread dump): `bartholomew_api_bridge_v0_1/
     services/api/db.py` resolves `BARTH_DB_PATH` into a module-level `DB_PATH` constant the
     moment that module is first imported; later `os.environ["BARTH_DB_PATH"] = ...`
     assignments by other test modules (e.g. `tests/test_stage0_alive.py`'s own attempted
     override) do nothing, because Python caches the already-imported module. Since
     `tests/test_liveness_self.py` imports it first (alphabetically) without setting the env
     var at all, every test in the session that starts the API app's `KernelDaemon` — each
     with its own background scheduler thread — ended up sharing the real, git-tracked
     `data/barth.db`. Their scheduler threads then deadlocked on file locks against that
     shared (and, from repeated interrupted test runs, sometimes already-corrupted) file
     during `TestClient.__exit__`'s shutdown handshake. Fixed by setting
     `os.environ.setdefault("BARTH_DB_PATH", ...)` to a fresh temp path at the top of the
     root `conftest.py`, which pytest always imports before collecting any test module —
     guaranteeing the override is in place before `db.py` ever gets imported. Verified:
     the previously-hanging pair now runs in ~3.5s, the full smoke suite in ~4s, and
     `data/barth.db` is no longer touched by running the test suite at all.
   - **Follow-up noticed along the way, root-caused and fixed 2026-07-20 (see below):**
     `tests/test_consent_gates.py::test_fts_search_without_consent_gate` and all three tests
     in `tests/test_metrics_production_mode.py` were failing for two entirely unrelated
     reasons — see "FTS5 external-content `upsert()` bug" and "`sys.path` self-pollution"
     below.

## FTS5 external-content `upsert()` bug and `sys.path` self-pollution — fixed 2026-07-20

Investigated two test failures the docs (`docs/STATUS_2025-12-29.md`) attributed to
Windows-only FTS5/environment quirks. Both reproduced identically on Linux and were real
logic bugs, not platform noise — the doc's diagnosis was stale/wrong for the current code.

### Bug 1: `FTSClient.upsert()` corrupts SQLite's FTS5 view for never-indexed rows

**Symptom:** `tests/test_consent_gates.py::test_fts_search_without_consent_gate` failed with
`sqlite3.DatabaseError: database disk image is malformed` — a genuinely confusing SQLite
error that does *not* mean the file is actually corrupt on disk.

**Root cause:** `FTSClient.upsert()` unconditionally issued FTS5's special `'delete'` command
(`INSERT INTO memory_fts(memory_fts, rowid, ...) SELECT 'delete', ...`) for every rowid,
regardless of whether that rowid had ever actually been indexed. In external-content FTS5
mode, creating the virtual table does *not* backfill rows that already exist in the content
table (`memories`) — so a `memories` row inserted before `FTSClient.init_schema()` ran (or via
any path that bypasses the sync triggers) has no corresponding entries in the FTS5 shadow
tables. Issuing `'delete'` for such a rowid is a genuine SQLite/FTS5 misuse this build reports
as "database disk image is malformed" — confirmed with a minimal, project-independent repro
outside pytest entirely (bare `sqlite3`, no project code).

**Fix:** `upsert()` now checks `memory_fts_map` (the class's own bookkeeping table, already
populated exactly on real inserts by both `upsert()` and the sync triggers) before issuing
`'delete'`, and skips it for rows that were never indexed.

**Verify:** `pytest -q tests/test_consent_gates.py` — 9 passed, 1 skipped (was 1 failed).

### Bug 2: two more instances of permanent `sys.path` self-pollution (broke `test_metrics_production_mode.py`, likely much more)

**Symptom:** All three `tests/test_metrics_production_mode.py` tests failed with
`ImportError: attempted relative import with no known parent package` inside
`bartholomew_api_bridge_v0_1/services/api/app.py`'s `from . import db_ctx` — but *only* when
run after certain other tests in the same session; passed in isolation.

**Root cause:** `bartholomew_api_bridge_v0_1/services/api/routes/metrics.py` (imported as
soon as the API app is, i.e. by nearly every API-app test) did, at module import time:
```python
sys.path.insert(0, ".../bartholomew/kernel")
from metrics_registry import get_metrics_registry
```
`sys.path.insert(0, ...)` here is never undone, so it poisons `sys.path` for the rest of the
process. Once `bartholomew/kernel/` is on `sys.path[0]`, a later bare `from app import app`
anywhere in the same process can resolve to
`bartholomew_api_bridge_v0_1/services/api/app.py` loaded as a *disconnected top-level* module
(shadowing the real `app.py` at repo root, since that directory also happens to contain a
file named `app.py`) — and a module loaded that way has no package context, so its own
relative import (`from . import db_ctx`) fails immediately. Confirmed with a minimal repro
(bare `sys.path.insert` + `from app import app`, no test framework involved) that reproduces
the exact error.

Worse: `bartholomew/kernel/` also contains `types.py`. The same pollution mechanism means any
later bare `import types` anywhere in the process — including deep inside the stdlib
(`dataclasses`, `enum`, `typing`, etc. all do this) — could silently resolve to this project's
`bartholomew.kernel.types` module instead of Python's real `types` module. This is a strong
candidate for at least some of the ~20 other FTS/hybrid/retrieval test failures already on
record as "pre-existing, not investigated" (several show `ValueError`/`sqlite3.OperationalError`
with no obvious connection to FTS at all) — not confirmed as the cause of all of them, but
worth checking before assuming they're something else.

Found two instances of this same anti-pattern (a bare `sys.path.insert(0, <dir>)` with no
corresponding removal, used to reach a sibling package without a proper dotted import) and
fixed both — replaced with the proper package-qualified import, since both targets
(`bartholomew.kernel.metrics_registry`, `bartholomew.kernel.db_ctx`) were already reachable
that way:
- `bartholomew_api_bridge_v0_1/services/api/routes/metrics.py` — inserted `bartholomew/kernel/`
  to reach `metrics_registry` (this is the one that broke the tests above).
- `bartholomew/kernel/scheduler/health.py` — inserted `bartholomew_api_bridge_v0_1/services/api/`
  to reach `db_ctx.wal_db`, duplicating `bartholomew.kernel.db_ctx.wal_db`, which already exists.

A **third** instance was already fixed as part of the earlier `input()` follow-up work today,
for an unrelated reason (it also duplicated a kernel-local helper instead of using it) —
`bartholomew/kernel/memory_store.py`'s `MemoryStore.close()` inserted
`bartholomew_api_bridge_v0_1/services/api/` to reach a second copy of
`wal_checkpoint_truncate`, when `bartholomew.kernel.db_ctx.wal_checkpoint_truncate` (the
kernel's own copy, explicitly written "to avoid coupling to the API layer") already did the
same thing plus the Windows-handle-release step the duplicate lacked calling correctly.

A **fourth** instance remains, deliberately not fixed: `scripts/hybrid_search.py` (a
standalone CLI demo script, not imported anywhere in the test suite) inserts the repo root
onto `sys.path[0]`. Lower risk (the repo root doesn't contain files that shadow stdlib/common
module names the way `bartholomew/kernel/` and `bartholomew_api_bridge_v0_1/services/api/`
do), and out of the test suite's reachable import graph, so left alone.

**Verify:**
```bash
pytest -q -k "consent or privacy or memory_rules or phase2 or metrics or stage1"
# 129 passed, 2 skipped (was 126 passed, 3 failed, 2 skipped)
pytest -q  # full suite: 42 failures -> 38 (all remaining are the separate,
           # already-larger, not-yet-investigated FTS/hybrid/encryption body of work)
```

2. ✅ **Fix malformed memory_rules.yaml rule** — Fixed 2026-07-20.
   - The `safety.audit` rule in `always_keep` section lacks `match:`/`metadata:` structure:
     ```yaml
     # WRONG (was):
     - kind: safety.audit
       summarize: false
     # CORRECT (now):
     - match:
         kind: safety.audit
       metadata:
         summarize: false
     ```
   - **Acceptance:** All rules use consistent `match:`/`metadata:` schema.
   - **Verify:** Unit test confirms all rules are parsed by engine.
   - **DoD:** Rule fixed; test added.
   - **Risk if skipped:** Silent rule failures; safety.audit memories not governed.
   - **Note:** the rule's `recall_policy` value was also `always_keep` (a copy of the
     section name) instead of `always` (the value every other rule in the file uses) —
     fixed to `always` at the same time. No automated "all rules parse" test was added;
     verification was a one-off script confirming every rule has `match`/`metadata`.

3. ✅ **Refactor `input()` out of kernel** — Fixed 2026-07-20.
   - `bartholomew/kernel/memory/privacy_guard.py` calls `input()` blocking on stdin.
   - **Acceptance:** Kernel emits consent event via event bus; never calls `input()`.
   - **Verify:** `grep -r "input(" bartholomew/kernel/` returns no matches (excluding tests).
   - **DoD:** Consent flow uses event bus; UI/CLI handles user prompt.
   - **Risk if skipped:** Headless/API deployments hang indefinitely.
   - **Note on implementation:** rather than wiring the existing `EventBus` (which has no
     shared instance reachable from this module), `privacy_guard` now exposes
     `set_consent_handler(handler)` — a pluggable sync/async callback. With no handler
     registered it fails closed (denies) instead of blocking, which is what fixes the
     headless-hang risk. `chat.py` (the interactive terminal entrypoint) registers a
     stdin-prompting handler at startup so its UX is unchanged; the FastAPI kernel daemon
     registers nothing and now fails closed instead of hanging.
   - **Follow-up fixed 2026-07-20:** `identity_interpreter/adapters/consent_terminal.py`
     (`ConsentAdapter.request_consent`) had the same blocking-`input()` shape and was
     reachable from `identity_interpreter/adapters/memory_manager.py` when called from
     *within* a running event loop (e.g. from an API request) — actually the more dangerous
     case, since it would freeze the whole event loop rather than just one coroutine. It was
     outside `bartholomew/kernel/`, so out of scope of this item's grep-based acceptance
     criterion, but got the same treatment now: `privacy_guard` gained
     `get_consent_handler()`, and `ConsentAdapter.request_consent()` now calls that
     registered handler synchronously instead of `input()` — same registered handler as the
     async path (`chat.py`'s stdin prompt still works via it), same fail-closed default with
     no handler registered, and fails closed rather than blocking if a misconfigured async
     handler is ever registered (can't safely `await` from this synchronous, already-in-a-loop
     call site). Session-scoped consent caching (`session_consents`) is unchanged. Verified:
     imports cleanly, `pytest -q -k consent` and `pytest -m smoke` show no new failures beyond
     the two pre-existing ones already on record above.

---

### P0 — Make the build trustworthy
5. **Canonical SSOT docs (done in this repo snapshot)**
   - **Acceptance:** canonical docs exist; cross-linked; "Next 3 Moves" current.
   - **Verify:** open markdown; links resolve.

6. **CI minimal gates (Linux)**
   - Make `pytest -q`, `ruff check .`, `black --check .` run in CI.
   - Quarantine platform-specific failures (Windows locking, SQLite build flags) into explicit markers.
   - **Acceptance:** CI green on Linux; quarantines documented.
   - **Verify:** GitHub Actions run; locally `ruff check . && black --check . && pytest -q`.

7. **Fix non-environmental failing tests** called out in `docs/STATUS_2025-12-29.md`
   - Summarization truncation fallback.
   - Encryption round-trip for envelopes.
   - Embedding persist lifecycle (`persist_embeddings_for`, `embed_store` defaults).
   - Retrieval factory returning wrong retriever for explicit modes.
   - Metrics registry idempotency.
   - **Acceptance:** P0 failures are green on Linux CI; regressions covered by tests.
   - **Verify:** `pytest -q` on Linux CI; replay the failing cases.

### P1 — Unified Persona Core (Experience Kernel) + personality packs
8. **Experience Kernel MVP** (self-model + narrator)
   - Minimal "self" state, narrator summarization, and reflection hooks wired into the kernel loop.
   - **Acceptance:** kernel can produce a stable "about me" snapshot and a day/week reflection without leaking sensitive memory.
   - **Verify:** `pytest -q tests/test_experience_kernel.py` (to be added) + run a scenario replay.

9. **Persona / Mentor Mode packs (config-driven)**
   - System prompt packs (e.g., Calm Mentor / Coach / Gamer Ally) selectable via config/UI without code edits.
   - **Acceptance:** switching persona changes tone/constraints; logged in audit trail.
   - **Verify:** `pytest -q tests/test_persona_switching.py` + manual API smoke.

### P2 — Modularity: skill registry + a few safe starter skills
10. **Skill manifest + registry** (local "marketplace" later)
    - Standard manifest schema (id, purpose, permissions, data touched, risk class, tests).
    - **Acceptance:** skills discoverable, loadable, and permission-scoped.
    - **Verify:** `pytest -q tests/test_skill_registry.py`.

11. **Starter skills (safe + reversible)**
    - `tasks.basic` (add/list in SQLite)
    - `notify.*` (log fallback)
    - `calendar.draft_block` (draft-only; behind consent)
    - **Acceptance:** end-to-end: prompt → decide → tool call (with consent) → persisted + audited.
    - **Verify:** `pytest -q tests/test_end_to_end_tasks_and_audit.py`.

### P3 — Initiative engine (proactive nudges) and workflows
12. **Scheduler-driven check-ins + workflows**
   - Morning/evening check-in; weekly review; “next best action” suggestion engine.
   - **Acceptance:** runs on schedule, respects quiet hours and parking brake; produces suggestions only (no Act).
   - **Verify:** `pytest -q tests/test_scheduler_checkins.py` + dry-run mode.

### P4 — Distributed being (cross-device) + voice adapters
13. **Cross-device thin client (PWA) + auth**
    - Token auth; shared session state; chat + timeline.
    - **Acceptance:** same state visible from two clients; no unauthenticated access.
    - **Verify:** integration tests + `curl` smoke.

14. **Voice adapters (optional / graceful unavailable)**
    - STT/TTS endpoints return "unavailable" when binaries missing.
    - **Acceptance:** voice endpoints fail gracefully; do not crash kernel.
    - **Verify:** `pytest -q tests/test_voice_adapters.py`.

### P5 — Embodiments (future)
15. **Mode system + signals** (Work/Life/Game/Car)
16. **Smart home integration** (read-only first)
17. **Gaming overlays** (separate surface; strict privacy + safety review)

---

## Full test suite investigation — 38 failures → 9 → 4 → 2, fixed 2026-07-20

Follow-up to the FTS5/`sys.path` investigation above: went through every remaining failure
in `pytest -q` (the full suite, not just `-m smoke`) one at a time. 29 were real, root-caused,
fixed bugs (several were the *same* underlying bug recurring in different call sites). 9 are
left deliberately unfixed because they require a design/product decision, not a mechanical
fix — documented individually below so nobody re-discovers them from scratch.

### Fixed (29 tests across 10 distinct bugs)

1. **No OS keystore in headless/CI environments** (8 tests: `test_bartholomew.py`,
   `test_cold_boot.py` x5, `test_memory_functionality.py` x2). `MemoryManager._init_encryption()`
   correctly fails closed when encryption is required (`Identity.yaml`'s `encryption.at_rest:
   true`) but no OS keystore backend is reachable (no D-Bus/Secret Service, no macOS Keychain,
   no Windows Credential Manager) — that's the right production behavior, not a bug. The gap
   was test infrastructure: nothing gave the test session a keystore stand-in. Added an
   in-memory `keyring.backend.KeyringBackend` in root `conftest.py`, installed for the whole
   session via `keyring.set_keyring(...)`. Side benefit: tests no longer write real encryption
   keys into a developer's actual OS keychain when run locally on a machine that has one. Only
   affects `identity_interpreter/adapters/memory_manager.py` (the `chat.py` CLI path) — the
   FastAPI/Docker production path uses a completely separate, env-var-based key system
   (`bartholomew.kernel.encryption_engine`) untouched by this.

2. **`test_liveness_api.py` never triggers the app's startup lifespan** (3 tests). Used
   `client = TestClient(app)` at module level instead of `with TestClient(app) as c:` — a bare
   `TestClient` never runs FastAPI's startup/shutdown lifespan, so the kernel daemon (and the
   schema it creates: `reflections`, `nudges`, etc tables) never started, and every query 404'd
   with `no such table`. This bug predates today; it was masked before the `BARTH_DB_PATH`
   test-isolation fix (previous section) because every test silently shared the real
   `data/barth.db`, which already had those tables from real usage. Fixed by switching to the
   same `with TestClient(app) as c:` fixture pattern already used correctly in
   `tests/test_stage0_alive.py`.

3. **`safety.audit` entries are correctly encrypted now, but a test reads them as plaintext**
   (1 test: `tests/integration/test_parking_brake_integration.py::test_audit_trail_records_changes`).
   Direct consequence of fixing the malformed `memory_rules.yaml` rule in the P0 pass (PR #1) --
   that rule (`encrypt: standard` for `kind: safety.audit`) was silently never matching before
   because it was malformed, so audit entries were accidentally stored as plaintext JSON. Now
   that the rule correctly matches, entries are properly encrypted at rest (the *intended*
   behavior), and the test's raw `sqlite3.connect(...).execute("SELECT ... FROM memories")` +
   `json.loads(row[1])` broke because `row[1]` is now an encryption envelope, not plaintext.
   Fixed the test to decrypt via `bartholomew.kernel.encryption_engine._encryption_engine
   .try_decrypt_if_envelope(...)` before parsing.

4. **`HybridRetriever._apply_boosts()` return-tuple mismatch** (7 tests across
   `tests/test_hybrid_rrf.py` and `tests/test_hybrid_boosts_flip.py`). The method returns a
   3-tuple `(boosted_fts, boosted_vec, boost_map)` -- its own internal caller already unpacks
   3 values -- but these two test files still unpacked only 2, raising `ValueError: too many
   values to unpack`. `boost_map` (a debug breakdown) was evidently added to the method after
   these tests were written. Updated all 7 call sites to unpack 3 (`_boost_map` discarded,
   unused).

5. **FTS5 query-syntax crashes on free-text queries with punctuation** (contributed to several
   of the retrieval/hybrid test failures). `HybridRetriever._pull_fts_candidates()` forwarded
   natural-language queries straight into `FTSClient.search()`, which passes the string to
   SQLite's FTS5 `MATCH` operator -- FTS5 parses that as its *own* query grammar (operators,
   phrases, column filters), not literal text, so a bare `.` or `?` from ordinary sentence
   punctuation raised `fts5: syntax error`. Added `_sanitize_fts_query()` (strips
   `.,!?;` -- characters that are never meaningful FTS5 syntax) at exactly the boundary where
   free text enters the FTS5 subsystem, so `FTSClient.search()` itself is untouched and still
   honors its documented contract of accepting raw FTS5 query syntax (quoted phrases,
   `AND`/`OR`/`NOT`, `field:value`) for callers who intend that.

6. **`FTSClient.upsert()` / `rebuild_index()` / schema triggers issue FTS5's `'delete'`
   special command for rowids that were never actually indexed** (recurring instance of the
   bug already fixed in `upsert()` during the earlier PR #1 pass -- turned out to have three
   more instances):
   - `rebuild_index()` unconditionally ran `DELETE FROM memory_fts` before rebuilding, which is
     exactly its own documented "initial index population" use case (content-table rows that
     predate the index) -- i.e. it crashed on its own primary purpose. Fixed: only issue the
     `DELETE` when `memory_fts_map` shows there's actually something indexed to clear.
   - The `memory_fts_update` and `memory_fts_delete` triggers (fired automatically by SQLite on
     any `UPDATE`/`DELETE` against `memories`) had the same unconditional `'delete'`-command
     issue, just baked into SQL instead of Python. Fixed by adding
     `WHEN EXISTS (SELECT 1 FROM memory_fts_map WHERE memory_id = old.id)` guards, and splitting
     `memory_fts_update` into that guarded version plus a `memory_fts_update_backfill` trigger
     (`WHEN NOT EXISTS ...`) that just inserts instead of delete-then-insert for never-indexed
     rows.
   - `MemoryStore.delete_memory()` *also* issued this exact special command manually and
     unconditionally, immediately before doing `DELETE FROM memories` (whose own trigger would
     run the same cleanup correctly, per the method's own code comment: "triggers will also
     fire for cleanup"). Removed the redundant manual step now that the trigger is fixed.
   - **Update 2026-07-20 (PR #2 review):** a bot reviewer correctly flagged that the caveat
     above was a real gap, not just a note -- `CREATE TRIGGER IF NOT EXISTS` alone never
     upgrades an already-existing database's trigger bodies. Fixed: `init_schema()` and
     `init_chunk_schema()` now explicitly `DROP TRIGGER IF EXISTS` every FTS trigger before
     running the schema script, so `IF NOT EXISTS` always recreates them fresh with the
     current definitions on every call, on any database. Also applied the same trigger guards
     (`WHEN EXISTS`/`WHEN NOT EXISTS` against `chunk_fts_map`) to `chunk_fts_update`/
     `chunk_fts_delete`, and the same `upsert()`-style pre-check to `upsert_chunk()` -- these
     are `chunk_fts`'s exact mirror of the `memory_fts` bugs, found while responding to the
     review, not previously caught by any test.
   - **Second bot finding, also fixed:** `rebuild_index()`'s `memory_fts_map`-based check
     (used to decide whether `DELETE FROM memory_fts` was safe to issue) assumes the map
     always accurately reflects the real FTS5 index state. If it doesn't -- e.g. a database
     from before the map table existed, or the map and index having drifted out of sync some
     other way -- an empty map would wrongly skip the `DELETE`, leaving stale entries mixed in
     with the fresh rebuild instead of properly clearing them. Fixed by dropping and
     recreating the `memory_fts` table itself during rebuild instead of trying to introspect
     its state first (a bare, non-`MATCH` query against an external-content FTS5 table can't
     reliably tell you what's actually indexed -- established earlier in this investigation).
     Same fix applied to `rebuild_chunk_index()`.
   - Verified: full `pytest -q` unchanged before/after these follow-up fixes (still 9 known
     failures, all pre-existing and already documented -- no regressions, no new passes).

7. **`MemoryStore.delete_memory()` never enabled `PRAGMA foreign_keys`** (1 test:
   `tests/test_stage2f_chunking.py::test_delete_memory_cascades_to_chunks`). `foreign_keys` is a
   per-connection SQLite setting, not persistent in the database file; this method opened its
   own fresh `aiosqlite.connect()` without setting it, so `memory_chunks`' `ON DELETE CASCADE`
   silently never fired on that connection -- chunks were orphaned instead of cascade-deleted.
   Added `await db.execute("PRAGMA foreign_keys = ON")`. Only fixed this one call site; the
   codebase has roughly ten other `aiosqlite.connect()` blocks in this file that weren't
   audited for the same gap (none are currently failing a test, so out of scope here, but worth
   a dedicated pass).

8. **`VectorStore` didn't create its DB's parent directory** (1 test:
   `tests/test_retrieval_factory.py::test_db_path_resolution_explicit`). Given an explicit
   `db_path` whose parent directory doesn't exist yet, `sqlite3.connect()` raised
   `unable to open database file`. `bartholomew_api_bridge_v0_1/services/api/db.py` already
   handles this (`os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)`); `VectorStore` didn't.
   Added the same. Also fixed the test itself, which used a hardcoded relative path
   (`"custom/path.db"`) with no `tmp_path` -- it was writing a real `custom/` directory into the
   repo's working tree on every run (same class of hygiene issue as `data/barth.db` getting
   mutated by test runs, fixed earlier). Switched to `tmp_path`.

9. **FTS5 `tokenize=` directive: single-quoted outer value can't contain a `tokenchars`
   argument** (1 test: `tests/test_fts_schema_hygiene.py::test_tokenizer_config_with_args`).
   Confirmed independent of this project's code with a bare `sqlite3`/FTS5 repro:
   `tokenize='unicode61 tokenchars .-@_'` is a parse error; FTS5 requires the `tokenchars`
   *value* itself to be single-quoted (`tokenchars '.-@_'`), which then can't nest inside an
   outer single-quoted SQL string. Fixed by switching the schema template's outer quoting to
   double quotes (`tokenize="{tokenizer}"` -- confirmed this doesn't affect the plain `porter`
   tokenizer case already in production use) and fixing the test's config value to include the
   required inner quoting. No real config in this repo currently uses `fts_tokenizer_args` with
   `tokenchars`, so this was a test-only gap, not something affecting production.

10. **`RetrievalConfigManager` doesn't support the legacy `fts.tokenizer` config location**
    (1 test: `tests/test_retrieval_hot_reload.py::test_config_manager_tokenizer_backward_compat`).
    There are two independent tokenizer-config-loading implementations in the codebase --
    `fts_client.py`'s `_load_tokenizer_config()` (supports both `retrieval.fts_tokenizer` and
    legacy `fts.tokenizer`) and `retrieval_config.py`'s `RetrievalConfigManager._load_config()`
    (only ever read `retrieval.fts_tokenizer`) -- and they'd drifted apart. Added the same
    legacy fallback to `RetrievalConfigManager`.

### Round 2 fixes — the 3 design-decision items, resolved 2026-07-20

The 3 items below were left unfixed pending a product/design call (see original writeups,
preserved in git history). The user made explicit decisions on all 3; implemented accordingly.

11. **`tests/test_bm25_udf_fallback.py`** (3 tests) — user decision: "implement a real
    fallback" (not remove the fallback path, not just skip the tests). Replaced the broken
    `matchinfo()`-based mechanism (`_rank_pcx()`, the old `sql_fallback` query) with a Python
    term-frequency ranking (`_extract_query_terms()` + `_term_frequency_rank()` in
    `fts_client.py`): the fallback SQL now just does `WHERE memory_fts MATCH ? ORDER BY m.id
    DESC LIMIT ?` against a bounded candidate pool (`max(fetch_limit * 5, 100)`), and rank is
    computed and sorted in Python, matching `bm25()`'s own "lower is better" convention. All 3
    existing tests pass unchanged against the new implementation. Also fixed the identical bug
    in `search_chunks()` (`-rank_pcx(matchinfo(chunk_fts, 'pcx'))`, calling the now-deleted
    `_rank_pcx` — would have raised `NameError` if ever actually hit) with the same
    term-frequency approach, found while doing this fix; no test previously exercised that path.

12. **`tests/test_fts_schema_hygiene.py::test_migrate_schema_fixes_rowid_mismatch`** — a
    mechanical fix, not a design decision (the "left unfixed" writeup already fully specified
    the correct approach). Rewrote `migrate_schema()` to compare `memory_fts_map` against
    `memories` in both directions (orphaned map entries with no matching `memories` row;
    `memories` rows with no `memory_fts_map` entry) instead of the broken `memory_fts` LEFT
    JOIN, and call `rebuild_index()` (already fixed earlier to drop-and-recreate rather than
    `DELETE`) when either mismatch is found. Rewrote the test's setup to create a mismatch this
    approach can actually detect (a direct `INSERT INTO memory_fts_map` for a nonexistent
    `memory_id`, via a connection that skips `set_wal_pragmas()`'s `PRAGMA foreign_keys = ON`
    so the invalid reference can be inserted) — the old setup (direct `INSERT INTO memory_fts`)
    tested a scenario that was structurally undetectable via portable SQL, which is exactly why
    the original check could never work.

13. **`tests/test_retrieval_fts5_fallback.py::test_get_retriever_degrades_fts_mode_when_unavailable`**
    — user asked for a recommendation; recommended and implemented "honor explicit mode" (i.e.
    keep `get_retriever()`'s current code as-is, per its own docstring and the sibling
    `test_explicit_mode_overrides_env_and_config` in `test_retrieval_factory.py`). Rewrote the
    outdated test to exercise the actual non-explicit path (`mode` resolved via
    `BARTHO_RETRIEVAL_MODE` env var rather than passed as an argument — `get_retriever()`'s
    `mode_explicit` tracking only covers the function argument, so an env/config-resolved
    `"fts"` is still eligible to degrade) and added
    `test_get_retriever_honors_explicit_fts_mode_when_unavailable` to codify the explicit-mode
    behavior itself, which wasn't previously covered by any test.

Verified: `pytest -q` on the four affected test files (34 tests) all pass; full `pytest -q`
now shows exactly the 4 remaining known issues below (was 9), no regressions elsewhere.

### Round 3 fix — recency-boost fusion redesign, resolved 2026-07-20

The two ranking-quality gaps below (`test_lexical_over_vector_on_rare_tokens.py`,
`test_fts_unavailable_vector_quality.py`) were previously documented as "genuine
fusion-math quality gaps... not root-caused to a specific line." Root-caused this round:
both were symptoms of the same underlying bug in `HybridRetriever`.

14. **Recency boost multiplied the *entire* fused score per-memory instead of being weighted
    into fusion** (2 tests: `test_lexical_over_vector_on_rare_tokens.py::test_lexical_beats_vector_on_exact_rare_tokens`,
    `test_fts_unavailable_vector_quality.py::test_vector_quality_maintained_when_fts_unavailable`).
    `_apply_boosts()` computed `total_boost = recency_boost * kind_boost * rule_boost` and
    multiplied it onto *both* the normalized FTS and vector scores before fusion — so
    `recency_boost` wasn't a tie-breaker, it was a multiplier on the whole relevance signal.
    Instrumented a failing query directly (`BARTHO_RETRIEVAL_DEBUG=1`, `last_debug`): an exact
    keyword match (`bm25_norm=1.0`, `vec_norm=0.0`) lost top-1 to a completely unrelated memory
    (`bm25_norm=0.0`, random `vec_norm=0.68`) purely because the unrelated memory was ~20 days
    "more recent" in the synthetic test corpus — at the default 7-day half-life, that's a ~7x
    recency multiplier, which swamps the 0.6/0.4 FTS/vector weight split (a perfect FTS match
    should only ever lose by this weighting if the other candidate's *vector* score is high
    enough to overcome a 0.6-vs-≤0.4 gap on its own). Since real memories almost always have
    *some* age difference, this meant recency could override actual relevance for essentially
    any two-candidate comparison, not just deliberately-engineered edge cases — a real
    production bug, not a test artifact.

    User decision: fold recency into fusion as its own weighted term (rather than bounding/
    compressing the existing multiplier). Implemented in `bartholomew/kernel/hybrid_retriever.py`:
    - Added `HybridRetrievalConfig.weight_recency` (default `0.0` — opt-in, no behavior change
      unless configured; `__post_init__` normalizes `weight_fts + weight_vec + weight_recency`
      together).
    - `_apply_boosts()` no longer multiplies `recency_boost` into `boosted_fts`/`boosted_vec` —
      only `kind_boost * rule_boost` now (both left untouched; out of scope of this bug, and
      `kind_boosts` defaults to empty so it's inert unless configured). `boost_map` still
      reports the raw recency value for debug/introspection (`Result.recency`).
    - Added `_normalize_recency_scores()`: min-maxes raw recency-decay values across the
      candidate set, same pattern as the existing `_normalize_fts_scores()`/
      `_normalize_vec_scores()` — turns "most recent of these candidates" into a `[0, 1]`
      relative signal instead of an absolute value whose magnitude depends on wall-clock age.
    - `_fuse_weighted()` gained an optional `recency_scores`/`weight_recency` term:
      `fused = w_fts*s_fts + w_vec*s_vec + w_recency*s_recency`. Backward compatible (both
      default to no contribution) for the several unit tests that call it with just FTS/vector
      scores.
    - `_fuse_rrf()` gained its own recency-rank term (`weight_recency / (k + recency_rank)`,
      same `1/(k+rank)` shape as the existing FTS/vector RRF terms, so its contribution is
      bounded the same way rather than an unbounded multiplier) — computed only when
      `weight_recency > 0`, so the zero-weight default is byte-for-byte identical to the old
      formula.
    - `retrieval_config.py` gained loading for a new `retrieval.hybrid_weights.recency` key
      (mirrors the existing `fts`/`vector` keys); `config/kernel.yaml` sets it to `0.15`
      (normalizes to ~13% of the fused score alongside ~52%/~35% for FTS/vector).
    - **Also fixed while investigating**: `tests/integration/test_fts_unavailable_vector_quality.py`'s
      own `calculate_hit_rate()` helper had a loop bug — its inner `break` only exited the
      `memory_map` lookup for a single result, not the `results` loop, so a query with several
      same-group matches in its top-10 could count multiple hits for itself. Observed hit rates
      of 110-120%, which is nonsensical for a rate. Fixed to count at most one hit per query.
    - **Updated 3 existing unit tests that hard-coded the old multiplicative-recency contract**
      as their expected behavior (`test_hybrid_boosts_flip.py::test_recency_boost_flips_top1_weighted`,
      `test_hybrid_boosts_flip.py::test_combined_boosts_flip_top1`,
      `test_hybrid_rrf.py::TestKindAndRuleBoosts::test_combined_boosts`) to exercise the new,
      bounded path instead — they still demonstrate recency (and kind boosts) can flip
      near-tied rankings, just no longer via an unbounded per-memory multiplier.
    - **Also fixed**: `test_retrieval_hot_reload.py::test_config_manager_loads_defaults` was
      silently loading the *real* `config/kernel.yaml` instead of testing dataclass defaults —
      `RetrievalConfigManager._find_path()` falls back to relative `DEFAULT_CONFIG_PATHS` when
      the passed-in path doesn't exist, and pytest runs from the repo root where that real file
      exists. Previously masked because the real file's `fts`/`vector` weights happened to match
      the test's hardcoded expectations; broken by adding `recency` to `config/kernel.yaml`
      above. Fixed by monkeypatching `DEFAULT_CONFIG_PATHS` to `[]` for the duration of the test
      so it actually exercises "no config file found."

    Verified: both originally-failing tests now pass; full `pytest -q` re-run clean except the
    2 already-deferred recency-flip tests below (no new regressions). The recency-flip tests'
    measured flip rate changed as a side effect (0% → 40%/10%) since recency can no longer push
    "old" completely out of the top-5 the way the unbounded multiplier did — still under
    threshold, for a different reason than before, but the deferral decision (below) stands
    unchanged.

### Left unfixed -- explicitly deferred by user decision, not a mechanical fix (2 tests)

- **`tests/integration/test_recency_flip_integration.py`** (2 tests). The test's pass/fail
  logic only counts a "win" when *both* the old and recent variant appear in the top-5 with
  recent ranked above old. After the recency-fusion fix above, the measured flip rate is
  40% (weighted) / 10% (RRF) against a ≥70-75% threshold — recency still meaningfully
  influences ranking (see `test_hybrid_boosts_flip.py`), just not strongly enough on this
  specific synthetic corpus/threshold combination to clear the bar. Whether to widen `top_k`,
  change what counts as a "win," retune `weight_recency`, or something else is a call about the
  test's intended measurement/tuning target, not a bug -- **user decision 2026-07-20: leave as
  a known issue for now.**

### Verify
```bash
pytest -q                          # 2 failures now (was 4, was 9, was 38 originally)
pytest -q -m smoke                 # unaffected, still green
```

---

## Echo Integration Roadmap (Brainstorm-Derived Features)

> **Source:** Extracted from 81 design conversations (logs/brainstorm/)
> **Status:** Future exploration—45 features identified across 4 stage gates
> **Note:** These represent a companion AI agent concept with gaming, smart home, and cross-device capabilities

### Echo Foundation (Gate 0) — 5 features
- LangGraph Agent Kernel (perceive → retrieve → decide → act → learn)
- Episodic and Semantic Memory (SQLite + Chroma + RAG)
- Permissions System (YAML-based ask/auto/never policies)
- Tauri + Python Architecture (desktop-first, offline)
- Code Signing and Runtime Attestation

### Echo Core (Gate 1) — 16 features
- Gaming Mode with Session Detection, Build Guidance, Inventory Coaching
- Permissions-Aware Memory, Modular Skill Manifests
- Context-Aware Modes (In-Game, Life, Work, Focus, Car)
- Scheduled Check-ins (APScheduler with mode-aware quieting)
- Device Identity Binding (EDID with TPM/Secure Enclave)
- Mutual TLS Device Pairing, Multi-Factor Authentication Gates
- Tamper-Evident Action Logging (ed25519 signatures)
- Device Bridge Services (Rust/Go for USB/Bluetooth/mDNS)
- Game Session Awareness, Contextual Help Adaptation
- Echo Organic Immune System (EOIS) - three-layer defense

### Echo Advanced (Gate 2) — 21 features
- Smart Home Integration (Matter/Home Assistant with scenes)
- Android Auto Car Mode (PTT, <6s replies, safety constraints)
- Cross-Device Sync (desktop/mobile/car with real-time updates)
- Personality Packs (switchable personas: Coach, Gamer Ally, Calm Mentor)
- Audit Trail (human-readable action logs with rationale)
- Shadow + Smoke UI Theme (futuristic glass/neon aesthetic)
- Voice I/O (Vosk + Piper/Coqui for local STT/TTS)
- USB PC Rescue Mode, Smart TV Voice Remote
- Device Troubleshooting Knowledge Base, Trusted Device List
- IoT Protocol Adapters (DLNA, WebOS, Tizen, Chromecast, HDMI-CEC)
- Offline Voice Processing, Cross-Domain Maturation
- Behavioral Baseline Detection, Canary Tokens
- Encrypted Quarantine Store, Network Isolation Controls
- Restore Points and Rollback, Forensics Incident Export
- Binary Watermarking

### Echo Ecosystem (Gate 3) — 3 features
- Local Skill Marketplace (install/remove without restart)
- Skill Marketplace Vetting (static analysis + signatures)
- Opt-in Differential Privacy Telemetry

**Acceptance for Echo exploration:**
- Features mapped to dependencies, constraints, and evidence
- Each feature has rationale and suggested stage gate
- Full feature JSON available at: `logs/brainstorm/merged/features_master.json`

**Verification:**
```bash
# View extracted features
cat logs/brainstorm/merged/features_master.json | python -m json.tool | head -50
# Check feature breakdown by gate
python -c "import json; from pathlib import Path; features = json.loads(Path('logs/brainstorm/merged/features_master.json').read_text()); gates = {}; [gates.setdefault(f['suggested_stage_gate'], []).append(f['feature']) for f in features]; [print(f'{gate}: {len(feats)} features') for gate, feats in sorted(gates.items())]"
```

---

## Risks summary

See [RISKS.md](RISKS.md) (privacy, consent bypass, platform-specific SQLite/FTS behavior, test flakiness, metrics duplication).

## Decisions summary

See [DECISIONS.md](DECISIONS.md) (SSOT docs, fail-closed governance, single DB, consent gates at lowest layer, etc.).

## Assumptions summary

See [ASSUMPTIONS.md](ASSUMPTIONS.md) (CI on Linux is the health baseline; Windows locking is noise; SQLite build features vary).

## Test expectations summary

See [TEST_MATRIX.md](TEST_MATRIX.md).

## Perf budgets summary

See [PERF_BUDGETS.md](PERF_BUDGETS.md).

## Next 3 Moves (always current)

> **Updated:** 2026-07-20 — items 0–3 (packaging/dependency/config/input() P0s), the
> dependency-pin/CI-install/test-hang follow-ups, the `consent_terminal.py` input() gap, and
> the full-suite failure sweep (38 → 9 → 4 → 2) are all done. `pytest -q` itself is now the fast,
> fresh "CI minimal gates" verify command; the 2 remaining known failures are explicitly
> deferred by user decision (see "Full test suite investigation" above), not pending a call.

1. ✅ ~~Fix P0 packaging issues (items 0–1)~~ — done 2026-07-20.
2. ✅ ~~Fix malformed memory_rules.yaml + refactor `input()` out of kernel (items 2–3)~~ — done 2026-07-20.
3. ✅ ~~Pin the dependency set, fix the CI install step, fix the test DB-path hang~~ — done 2026-07-20.
4. ✅ ~~Fix `identity_interpreter/adapters/consent_terminal.py`'s blocking `input()`~~ — done 2026-07-20.
5. ✅ ~~Fix non-environmental failing tests from `docs/STATUS_2025-12-29.md` (item 7)~~ — done
   2026-07-20; see "Full test suite investigation" above. `docs/STATUS_2025-12-29.md` itself is
   now stale/superseded on this topic and shouldn't be treated as current status.

1. **CI minimal gates (Linux)** (remainder of items 5–6)
   - `pytest -q -m smoke`, `ruff check .`, `black --check .` already run in CI
     (`.github/workflows/pre-commit.yml`); not yet done: running the *full* `pytest -q` (not
     just `-m smoke`) in CI, now that it's down to 2 known, documented, and explicitly deferred
     failures instead of 38 undiagnosed ones.
   - The 2 remaining failures are deferred by explicit user decision (2026-07-20, see above),
     not awaiting a call -- CI could quarantine them with `xfail`/a skip reason referencing this
     doc so `pytest -q` goes green with a marker distinguishing "known, decided, deferred" from
     "new regression," if/when that's wanted.
   - **Verify:** GitHub Actions green on Linux.

## Pending Approvals

> **Status:** Tracks proposed changes through approval and commit lifecycle.
>
> **Process:** Agent proposes → User reviews → User approves → Commit is executed → Record in ledger
>
> **Rule:** Never mark anything as committed without a commit hash.

### Pending (awaiting user approval)
- *None* (last updated: 2026-01-19)

### Approval Ledger
Record of approved changes with commit tracking (max 5 entries):
- *No entries yet*

**Ledger format:**
- YYYY-MM-DD — <short description> — Approved by <user> — Commit: <hash> (or **not yet committed**)

## Quality gates

- Governance invariants preserved (parking brake, consent gates, redaction/encryption rules).
- Unit + integration tests updated and passing (or explicitly quarantined with justification).
- Interfaces updated if contracts change.
- Risks/assumptions/decisions updated.

## Definition of Done (DoD)

A change is “done” only when:

- Implementation complete.
- Tests added/updated and passing (or explicit reason + quarantine).
- Lint/format/type checks pass (if enabled).
- Canonical docs updated if behavior/interfaces changed.
- Acceptance criteria verified.
- Governance not regressed (consent, parking brake, privacy rules).
- Rollback note included if risky.
- CI Gatekeeper satisfied (see [CI.md](CI.md)).
