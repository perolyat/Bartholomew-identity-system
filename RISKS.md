# RISKS

> Risk radar: security, privacy, reliability, maintainability, performance, tech debt.
>
> **Last updated:** 2026-08-17 (four tech-debt watchlist items added, none of them a live defect:
> the server-centric deployment decision's connectivity dependency and its unbuilt degraded-mode
> requirement; the cloud budget ledger's check-then-act window (unreachable today — no live path
> passes a `task_type`); the consent ask-path being unreachable from the API/web surface
> (fail-closed, so safe, but `/ui` cannot exercise ask-and-deny); and `test_memory_privacy.py`
> writing to the live `./data` directory when run by hand. Separately, the entry describing
> `/api/water/log` and `/api/water/today` as "live, working, legacy code" is **corrected** — neither
> endpoint exists, and no commit removing them was found. See `DECISIONS.md`'s server-centric
> deployment entry and `docs/FIRST_REAL_WORLD_TEST.md`.)
>
> **Previously (2026-08-15, second pass):** one tech-debt watchlist item added: the Parking Brake's
> split read/write authority — `sight`/`voice` still read the legacy `system_flags`-backed
> `ParkingBrake` while the write authority has been `GovernanceStore` since Phase B stage B6. **Not
> a new finding and not a live safety hole** — B4 and B6 §1 finding 5 both found it and deferred it,
> and the capability behind those seams is inert. Recorded canonically because the new Parking Brake
> authority tiers give it an ordering constraint: consolidate those seams onto `GovernanceStore`
> *before* tiers are introduced. No fix authorised. See `DECISIONS.md`'s "Parking Brake authority
> tiers" entry.)
>
> **Previously (2026-08-15, first pass):** three new tech-debt watchlist items added, all found by a
> repository-grounded review for the platform/personal-identity architecture decision — the
> globally-unique `memories(kind, key)` index with no ownership dimension; personal runtime state
> held in module-level singletons; and the absence of an on-behalf-of identity on scheduler drives
> and capability execution. **None is a defect, and no fix is authorised by this pass** — all three
> are correct for a single-identity PoC and are recorded as migration seams so they are not
> rediscovered from scratch later. See `DECISIONS.md`'s "One shared Bartholomew platform; many
> strongly isolated personal Bartholomew identities" entry and `COGNITIVE_RUNTIME.md`'s
> "Personal-identity ownership" subsection.)
>
> **Previously (2026-08-14):** three new tech-debt watchlist items added — LICENSE declaration
> inconsistency (MIT vs CC-BY-NC-4.0, no LICENSE file); absence of dependency-licence/SBOM/SCA
> tooling; absence of a record of which AI coding tools/services this repository's development
> relies on. Found during a repository-grounded AI-development-provenance and IP-governance review
> prompted by Anthropic's introduction of machine-readable provenance/watermarking for Claude
> output — see `DECISIONS.md`'s new "AI-assisted development is governed by..." entry.
> Documentation-only; none of the three items is resolved by this pass.)
>
> **Corrected 2026-08-14** (same day, following an automated review comment): the AI-provider-tools
> tech-debt entry is corrected to distinguish Cline/Claude Code (repository-evidenced actual use)
> from GitHub Copilot (configured via `.github/copilot-instructions.md`, no proven usage record).
> Wording-only; the risk's category and priority are unchanged.
>
> **Previously (2026-08-11, second pass, same day):** one new tech-debt watchlist item — the
> disagreement between `privacy_guard.SENSITIVE_KEYWORDS` and `memory_rules.yaml`'s
> `ask_before_store` vocabulary, found while fixing the `is_sensitive()` false positives and
> deliberately left unresolved by that fix. Both mechanisms fail closed, so the disagreement
> over-triggers consent rather than under-triggering it; no reconciliation is authorised.)
>
> **Previously (2026-08-11):** status reconciliation against merged repository state: the FTS5
> external-content deletion/index-staleness item is updated, **not closed**. PR #40's single-writer
> `memory_fts` architecture (`bbd920d`) resolves it for the live write path and satisfies both
> regression criteria the entry asked for — re-verified green. A narrower residual was found and
> reproduced while verifying that claim: databases migrated from the old trigger architecture have
> `last_index_text IS NULL`, so the tracked delete is a no-op for exactly those rows and
> pre-migration index content survives `MemoryStore.init()`'s heal. `scripts/backfill_fts.py`
> clears it (verified), but only as a manual operator step. No production fix for the residual is
> authorised.)
>
> **Previously (2026-08-10):** one new tech-debt watchlist item added: an FTS5
> external-content deletion/index-staleness issue found while writing S5.1 regression tests,
> confirmed pre-existing and unrelated to S5.1 — no production fix authorised yet.
>
> **Previously (2026-07-28):**
> **Last updated:** 2026-07-28 (documentation reconciliation pass 2: the legacy-implementation-
> notes cleanup deferred on 2026-07-27 is done — see the tech-debt watchlist entry below for the
> full disposition; the FastAPI-lifespan-migration ticket merged in from its own standalone file
> (still open); three new items added — hydration/water-logging code cleanup recorded as a future
> unapproved decision, the cross-device auth threat model gap, and jurisdiction-aware
> capture/recording compliance.)
>
> **Previously (2026-07-27):** Phase A recorded as merged (`8b96319`); R3/R4's "first Windows
> evidence will come from the first CI run" replaced with the actual result; Phase A's deferred
> findings F9–F11 added to the tech-debt watchlist, where they had previously existed only in
> PR #26's description and so were at risk of being lost. Earlier: 2026-07-25 Phase A
> stabilisation added two tech-debt entries — the undeclared-dependency defect class, one instance
> of which was a live crash on the sensitive-memory write path, and the persistence-ownership
> characterisation preserved for Phase B. Earlier same-week entries: R2 updated for the
> voice/sight governance seam — MASTER_PLAN.md item 11.21; R1's red-team suite — item 11.20; the
> scheduler/WAL concurrency items — item 11.18. R3/R5/R6 last independently re-verified
> 2026-07-21; R4 and the pre-existing tech debt items left as recorded then.

## Risk register (top)

### R1 — Consent bypass / privacy leakage
- **Category:** Privacy, Safety
- **What could go wrong:** A caller retrieves or surfaces a memory that should be excluded (never_store / ask_before_store / context_only).
- **Current controls:** ConsentGate applied by default at FTS/vector layers; memory rules engine; redaction; encryption.
- **Mitigation:** Add bypass-path red-team tests; audit any `apply_consent_gate=False` call sites; enforce “admin-only” paths.
- **Status:** Mitigated (2026-07-24) — the "no red-team test suite exists yet" gap is now closed:
  `tests/test_consent_bypass_redteam.py` (10 tests, see MASTER_PLAN.md item 11.20) drives content
  through the real `memories`/FTS/vector tables (bypassing `upsert_memory()`'s write-time guard,
  since `MemoryRulesEngine.should_store()` already hard-blocks `requires_consent` content there —
  the scenario worth red-teaming is content that reaches storage some other way: a rules.yaml
  reclassification after the fact, a migration, direct DB access) and proves it's never surfaced
  through every production retrieval surface: `get_retriever()`'s three modes, and
  `HybridRetriever`/`FTSOnlyRetriever` constructed directly with no `rules_engine` — the exact
  construction each class's own docstring usage example shows. That last case surfaced a real
  structural finding, not a bug: `HybridRetriever`/`FTSOnlyRetriever` skip their own optional
  rules-engine re-filtering entirely when constructed without one, but the retrieval-layer
  `ConsentGate` baked unconditionally into `FTSClient.search()`/`VectorStore.search()` (default
  `apply_consent_gate=True`) still holds regardless — confirmed by deliberately breaking
  `ConsentGate.filter_memory_ids()` and watching exactly those "no rules_engine" tests fail (and
  no others), proving the tests watch the right thing rather than passing vacuously. Also
  formalized the 2026-07-21 one-time grep audit into two permanent regression guards: an
  AST-based scan that no production call site ever passes `apply_consent_gate=False`, and a
  signature check that no public `.retrieve()` facade (`HybridRetriever`, `FTSOnlyRetriever`,
  `VectorRetrieverAdapter`) exposes a parameter capable of disabling the gate at all. One residual
  nuance, not a leak under current code: `get_retriever(mode="vector")` doesn't pass a
  `memory_store` through by default, so `Retriever`'s own second-layer rule check degrades to a
  content-less stub for that mode specifically — harmless today because the first-layer
  `ConsentGate` inside `VectorStore.search()` already gates unconditionally, but it means "vector"
  mode has one fewer redundant layer than "hybrid"/"fts" if that first layer were ever
  independently broken. Left as a documented, non-blocking observation rather than fixed in this
  pass — no test currently requires it and fixing it wasn't part of this session's scope. The
  earlier fail-closed bug (`Retriever`/`FTSOnlyRetriever`/`HybridRetriever` excluding every
  `requires_consent` memory unconditionally regardless of actual consent) stays fixed and covered
  by `tests/test_retrieval_consent_enforcement.py`, re-verified green as part of this pass.

### R2 — Over-automation / unsafe side effects
- **Category:** Safety
- **What could go wrong:** Skills/scheduler execute actions without explicit consent, or continue running when they shouldn’t.
- **Current controls:** Parking brake (scoped, persistent, fail-closed).
- **Mitigation:** Ensure every “Act” path checks brake; keep Stage 1 strictly read/ack/dismiss; add integration tests.
- **Status:** Controlled, and the "add integration tests" mitigation is well satisfied: `pytest
  -q tests/integration/test_parking_brake_integration.py tests/test_parking_brake_scoped_blocks.py
  tests/unit/safety/test_parking_brake.py` (2026-07-21) covers all five live scopes
  (`skills`, `scheduler`, `sight`, `voice`, `global`) both engaged and disengaged. As of item
  11.21 (2026-07-24) the `sight`/`voice` paths are no longer brake-*only*: their governed seams
  (`runtime_contract.run_sight_/run_voice_through_runtime_contract()`, which the `start_capture()`/
  `start_stream()` adapters now delegate to exclusively) additionally consult the Identity Policy
  Decision and an *always-required, fail-closed* device consent gate before any (currently inert)
  capability call — so a future real capture/stream is gated by consent + policy + brake, not
  brake alone. Covered by `tests/test_voice_sight_runtime_contract_seam.py` (45 tests, including
  deliberate per-gate neutralisation non-vacuity controls). See `COGNITIVE_RUNTIME.md`'s
  "Governance checkpoints" and "Device surfaces" sections for the current call-site list.
  Expansion risk (new "Act" paths forgetting the governed seam) remains the open-ended part of
  this risk — not something a point-in-time audit closes permanently; the item 11.21 AST
  structural test (placeholder capability never callable outside the seam) is one guard against
  that specific regression for voice/sight.

### R3 — SQLite / FTS feature variability causes false confidence
- **Category:** Reliability
- **What could go wrong:** Retrieval works on one platform/build and silently breaks on another (FTS5/matchinfo/bm25 behavior differences).
- **Current controls:** FTS fallback implementations exist; tests exercise some fallbacks.
- **Mitigation:** Linux CI baseline; explicit environment detection; fallback-path tests; consider bundling SQLite build if needed.
- **Status:** Largely mitigated on Linux (2026-07-20/21) — `docs/archive/STATUS_2025-12-29.md` is stale
  and superseded on this topic (MASTER_PLAN.md flags it as such): two FTS5 bugs this doc
  attributed to Windows-only quirks were actually real logic bugs, reproduced identically on
  Linux and fixed (see MASTER_PLAN.md's "FTS5 external-content `upsert()` bug..." section); the
  full suite (`pytest -q`) is fully green on Linux as of 2026-07-20, and `tests/
  test_retrieval_fts5_fallback.py`/`tests/test_fts_schema_hygiene.py` pass locally as of
  2026-07-21. Cross-platform variability itself is an inherent, ongoing category (not a single
  bug to close permanently). Windows behaviour is untestable in this sandbox, but as of Phase A
  (merged 2026-07-27, `8b96319`) `ci.yml`'s `windows` job runs the packaging contract,
  clean-start lifecycle, scheduler readiness and smoke suites on `windows-latest`, so Windows
  results are now produced automatically by CI rather than assumed. **First real result:** the
  Windows job passed on PR #26's head `e923fb9` — the first Windows CI run in this repository's
  history. One green run is a baseline, not proof of cross-platform robustness; the value is
  that a Windows regression now fails a pull request instead of going unobserved.

### R4 — Windows file locking causing flaky tests and masking real failures
- **Category:** Reliability, Maintainability
- **What could go wrong:** Temp DB cleanup fails; tests go red for non-product reasons; teams ignore failures.
- **Current controls:** Some retry cleanup patterns in fixtures.
- **Mitigation:** Close connections deterministically; tighten async fixtures; quarantine truly platform-only failures with markers.
- **Status:** Active, but no longer invisible. As of Phase A (2026-07-25) `ci.yml` has a
  `windows` job, and `tests/test_clean_start_lifecycle.py::
  test_shutdown_releases_database_handles_for_tempdir_cleanup` asserts the exact property that
  fails first under Windows locking (a temp directory containing the database must be deletable
  after `stop()`). That test runs on both Linux and Windows, so a handle leak fails CI instead of
  being written off as platform noise. **Updated 2026-07-27:** the first real Windows evidence
  now exists — the `windows` job passed on PR #26's head `e923fb9`, so this property currently
  holds on `windows-latest` rather than merely being asserted. Still unverifiable *in this
  sandbox* (Linux-only), so local runs remain no evidence for Windows either way. The underlying
  risk — Windows-specific flakiness being written off rather than diagnosed — is now detectable,
  not eliminated.

### R5 — Encryption envelope round-trip bugs
- **Category:** Security, Reliability
- **What could go wrong:** Data is encrypted but cannot be decrypted; summaries/values become unrecoverable; user trust destroyed.
- **Current controls:** EncryptionEngine + tests.
- **Mitigation:** Fix the failing integration tests; add property-based tests for envelope formats; version envelopes.
- **Status:** Mitigated — `pytest -q tests/test_phase2b_encryption.py` passes locally (2026-07-21,
  clean venv); MASTER_PLAN.md's P0 status (2026-07-20) records the full suite, including this
  file, at zero failures. No property-based envelope tests or explicit envelope versioning yet
  added — those two mitigation items remain open.

### R6 — Metrics duplication / cardinality blowups
- **Category:** Performance, Reliability
- **What could go wrong:** Re-registering Prometheus metrics causes runtime crashes; high-cardinality labels blow up memory.
- **Current controls:** Metrics registry guard tests exist.
- **Mitigation:** Make registry init idempotent; enforce label allowlists; add perf budget.
- **Status:** Mitigated — `pytest -q tests/test_metrics_labeled.py tests/test_metrics_labels.py
  tests/test_metrics_production_mode.py tests/test_metrics_registry_guard.py` passes locally
  (2026-07-21); MASTER_PLAN.md's P0 status lists metrics-registry idempotency among the items
  fixed 2026-07-20. Label allowlist enforcement and a dedicated perf budget check aren't
  independently confirmed this pass — see `PERF_BUDGETS.md`.

## Tech debt watchlist

- **(2026-08-17) Server-centric cognition creates a connectivity dependency that has no defined
  degraded mode — TARGET-architecture risk, not a current defect.** `DECISIONS.md`'s "Deployment
  architecture — server-centric Bartholomew with local/edge capability agents" makes core cognition
  server-side by default. That trades a per-device installation burden for a dependency on reaching
  the platform, and the failure it introduces is not "chat is slow" but "the assistant that holds
  your life is unreachable". **Nothing is broken today** — the current prototype is entirely local
  and has no such dependency — which is precisely why this is recorded now, before anything is
  built against it.
  **The mitigation is already constitutionally required and remains unbuilt:** `CONSTITUTION.md`
  requires defined loss-of-connectivity behaviour, safe degradation, and — the hard one — that
  "central infrastructure must never become the only authority capable of stopping or constraining
  the system", carried forward as clause (b) of the superseding decision. So a device agent cannot
  be a pure pass-through: **a user must still be able to stop their own Bartholomew acting on their
  devices while the platform is unreachable**, which means local stop authority cannot itself be a
  remote call. Designing that, along with what Bartholomew is allowed to *do* while degraded (act on
  stale state? queue? refuse?), is a prerequisite for the first device agent, not a follow-up to it.
  No design exists, and none is authorised.
- **(2026-08-17) Cloud budget ledger has a check-then-act window under concurrency — deferred,
  and deliberately not fixed now.** `ModelRouter._route_cloud()` reads the spend snapshot, makes
  the provider request, then records the cost. Two concurrent cloud generations can each observe
  headroom under the cap and jointly exceed it. **Current POC risk is nil and the reason is
  structural, not luck:** no live code path passes a `task_type`, so `select_route()` always
  defaults to `general`, which `Identity.yaml` routes to the local model with no cloud candidate —
  cloud is unreachable from the running system regardless of whether a key is present. The ledger
  also already fails closed on an unreadable read, `low_balance_behavior` is `force-local`, and the
  cap is $25/month for a single user. **This becomes a real requirement before either autonomous
  cloud use or multi-user operation**, at which point the correction is a transactional
  reserve-then-settle against the ledger rather than a distributed billing system. Recorded here
  rather than fixed, because hardening an unreachable path now buys nothing and adds a concurrency
  mechanism nobody can exercise.
- **(2026-08-17) The consent ask-path is unreachable from the API/web surface.**
  `set_consent_handler()` is called only by `chat.py`, the standalone terminal entrypoint. On the
  API path no handler is registered, so `request_permission_to_store()` returns `False`
  unconditionally: sensitive content is **not stored**, and the user is **not asked**. The
  behaviour is fail-closed and therefore safe, but it means the "ask before storing" experience
  does not exist in `/ui` — only its refusal half does. Consequence for testing is recorded in
  `docs/FIRST_REAL_WORLD_TEST.md` §5. Registering a handler on the API path is small, but it is a
  new user-facing behaviour rather than a repair and needs its own approval.
- **(2026-08-17) `test_memory_privacy.py` writes to the live `./data` directory.** It is a manual
  script (its entry point is `run_test()`, which pytest does not collect, so the suite never runs
  it), but executing it by hand constructs a `MemoryManager` against the real deployment and
  attempts to store a synthetic "bank password" memory. Left alone deliberately — it is a manual
  privacy check whose whole point is to run against a real configuration — but worth knowing before
  running it on a machine holding real memories. Its sibling `test_memory_functionality.py`, which
  *is* collected, was redirected to a temp directory on 2026-08-17 after it was found deleting the
  repository's tracked `data/memory.db` contents on every test run.
- **(2026-08-15, second pass) Parking Brake read/write authority is split — known, deferred, and
  newly consequential under the authority-tier model.** Since Phase B stage **B6** the brake's
  write authority is `GovernanceStore` (`parking_brake_state`): `bartholomew/cli.py`'s
  `brake on`/`brake off`, the `skills` gate and the `scheduler` gate all use it. The `sight` and
  `voice` seams in `bartholomew/kernel/runtime_contract.py` still read the **legacy**
  `ParkingBrake`/`BrakeStorage` pair (`system_flags`). **This is not a new finding and not a live
  safety hole:** B4 found those paths unreachable (no live caller) and deferred consolidation, and
  `docs/B6_EXTERNAL_GOVERNANCE_CLI_SAFETY.md` §1 finding 5 re-confirmed and again deferred it; the
  capability behind both seams is inert (Stage 6), so nothing real is ungated today, and R1's
  brake-coverage tests exercise the legacy path directly rather than through the CLI. **What
  changed on 2026-08-15** is the consequence, not the facts: under the Personal/Platform authority
  tiers now recorded in `COGNITIVE_RUNTIME.md`, tier awareness must be added **once**, in
  `GovernanceStore`. If the legacy-reading seams are still present when tiers are introduced, they
  would silently not honour them — a per-user or platform-wide halt that `sight`/`voice` ignore.
  **Risk category:** safety / architectural migration. **Action:** none now — no code change is
  authorised or required, and the existing deferral stands. **The ordering constraint is the
  deliverable:** consolidate `sight`/`voice` onto `GovernanceStore` *before* introducing authority
  tiers or making those capabilities real, whichever comes first.
- **(2026-08-15) `memories` is uniquely indexed on `(kind, key)` globally, with no ownership
  dimension.** `bartholomew/kernel/memory_store.py` declares
  `CREATE UNIQUE INDEX uq_memories_kind_key ON memories(kind, key)`. Correct and desirable for a
  single-identity deployment — it is what makes `upsert_memory()` an upsert. In the multi-identity
  architecture `CONSTITUTION.md`'s "One Platform, Many Personal Bartholomews" section establishes,
  uniqueness must be **per personal identity**, not global: two users may each hold a
  `user_profile`/`home_address`. **Not a defect and no fix is authorised** — the correction is an
  ordinary additive migration (add the ownership column, rebuild the index over
  `(owner, kind, key)`), no more expensive later than now, and doing it now would add an unused
  column to satisfy no current requirement. **The real risk is different and worth watching:** new
  code that *relies on* global `(kind, key)` uniqueness as a semantic guarantee — deduplication
  across the whole store, "there is exactly one home address," caching keyed on `(kind, key)` alone
  — would convert a cheap migration into an expensive one. **Risk category:** architectural
  migration. **Action:** none now; flag in review if new code depends on the global-ness of this
  constraint.
- **(2026-08-15) Personal runtime state lives in module-level singletons.**
  `bartholomew/kernel/narrator.py`, `encryption_engine.py`, `memory_rules.py`,
  `retrieval_config.py` and `metrics_registry.py` each hold process-global singletons that stand in
  for per-identity state. Appropriate for one process serving one person, and the natural
  multi-identity form (a runtime context constructed per identity, or per-identity instances behind
  the platform) does not require these modules to be rewritten — only constructed differently. The
  watch item is that this stays true: a singleton that begins *caching personal content* rather
  than configuration would be harder to separate later. **Risk category:** architectural migration.
  **Action:** none now.
- **(2026-08-15) No ownership/provenance dimension on background work or capability execution.**
  The scheduler (`bartholomew/kernel/scheduler/*`) runs drives with no notion of whose behalf they
  act on, and skills execute without an on-behalf-of identity. Two audit surfaces already carry a
  field that could later carry it — `governance_audit.actor` and `skill_permissions.granted_by` —
  but both currently record which *subsystem or surface* acted, not which *person*. Correct for a
  single-identity PoC; named here so the seam is not rediscovered from scratch. Note the naming
  collision worth avoiding: `bartholomew/kernel/request_admission.py` describes itself as
  "identity-bound," meaning per-request admission tokens, **not** user identity. **Risk category:**
  architectural migration. **Action:** none now; new background work should not deepen the
  assumption (see `CHECKLISTS.md`'s platform/personal-identity checklist).
- **(2026-07-25, Phase A) Undeclared-dependency class of defect — one instance fixed, the
  detection gap closed.** `MemoryStore.upsert_memory()` called `asyncio.run()` from inside an
  `async def`, so it raised `RuntimeError` on *every* sensitive-content write and always fell
  through to an unguarded `import nest_asyncio` — a package declared in no manifest. Any content
  `is_sensitive()` flagged (e.g. containing "routine", "location", "name") that the rules engine
  did not block earlier therefore raised `ModuleNotFoundError`, **even when the user approved
  storing it**. Reproduced directly, then fixed by awaiting the coroutine (consent remains
  fail-closed). Two further latent instances were also uncovered and fixed: `pytest-cov` was
  undeclared while `tests.yml` depended on it (that workflow could never have passed), and the
  `bartholomew` console script was broken at import time by a `typer.Option(param_decls=...)`
  call incompatible with the installed typer. `tests/smoke/test_packaging_contract.py` now fails
  CI on any undeclared third-party runtime import, any first-party module that will not import,
  and any declared console script that will not run `--help`. **The underlying risk is not
  closed** — it is now detected rather than latent.
- **(2026-07-25, Phase A) Persistence ownership remains mixed — characterised, not fixed
  (Phase B).** **Status 2026-07-27: unchanged and still open.** Phase A is merged (`8b96319`);
  **Phase B is proposed but not approved for implementation**, so nothing below has been
  restructured. Phase A deliberately added no database owner and no checkpoint path. Evidence
  preserved for the Phase B audit: `bartholomew/kernel/memory_store.py` (aiosqlite),
  `bartholomew/kernel/scheduler/persistence.py` (sync `sqlite3` behind `SchedulerStore`'s
  dedicated thread), `bartholomew/kernel/persona_pack.py` and `narrator.py` (sync `sqlite3`
  called from async methods), plus two near-duplicate context modules —
  `bartholomew/kernel/db_ctx.py` and `bartholomew_api_bridge_v0_1/services/api/db_ctx.py` —
  all read/write the same SQLite file. `tests/test_clean_start_lifecycle.py` now characterises
  the observable consequences (fresh-DB schema creation, bounded start/stop, handle release
  sufficient for temp-directory deletion, clean restart, no leaked scheduler threads or pending
  tasks) so a regression fails CI. No failure was concealed with a retry or an inflated timeout;
  the bounds are hang detectors, not performance policy.
  - **Reproduced under full-suite load (2026-07-25):**
    `tests/test_sqlite_wal_concurrent_processes.py::test_wal_cleanup_concurrent_processes` failed
    once in a full run (`1 failed, 909 passed, 2 skipped, 3 deselected`) with
    `Worker process failed with code 1`, then passed 3/3 when run in isolation immediately
    afterwards. The same test failed once earlier in the week during item 11.21's verification and
    likewise passed on retry. It is **order/load-dependent, not deterministic**, it exercises
    multi-process WAL cleanup, and it is unrelated to the Phase A changes (which touch neither the
    WAL/checkpoint paths nor multi-process access). Left failing-under-load and recorded here
    rather than retried, quarantined, or given a longer timeout: it is direct evidence for the
    Phase B persistence-ownership audit, and the existing "unresolved root cause: why a `TRUNCATE`
    checkpoint outlasted its own busy-timeout" item below is the likely same root cause.
- **(2026-07-25, Phase A) Three deferred findings, recorded here so they are not silently
  treated as fixed.** Each was found during Phase A verification, judged non-blocking, and
  deliberately left out of that change set. None is fixed as of 2026-07-27. They had previously
  been written down only in PR #26's description, which is not a canonical document.
  - **F9 — two competing packaging manifests.** `setup.py` declares `name="identity_interpreter"`
    with a console script `barth`; `pyproject.toml` declares `name="bartholomew"` with console
    scripts `bartholomew` and `bartholomew-backfill-fts`. `pyproject.toml` is what actually
    installs: `bartholomew` and `bartholomew-backfill-fts` are on `PATH`, `barth` is not
    (re-verified 2026-07-27 with `which`). Root `README.md` documented `barth lint` / `barth
    explain` as the quick-start commands — corrected 2026-07-27, but the duplicate manifest
    itself remains. Risk: the two manifests can drift further, and it is not obvious which one
    a contributor should edit.
  - **F10 — legacy API shim fails a bare import.** `bartholomew_api_bridge_v0_1/app.py` does
    `from services.api.app import app`, which only resolves when the working directory is
    `bartholomew_api_bridge_v0_1/`. The supported entry points (the repo-root `app.py` and
    `bartholomew_api_bridge_v0_1.services.api.app`) both work. It is **explicitly allow-listed**
    in `tests/smoke/test_packaging_contract.py`'s `KNOWN_NON_IMPORTABLE` rather than silently
    skipped, so the exception is reviewable. Risk: a duplicate entry point that looks canonical.
  - **F11 — test-only dependencies in the runtime manifest.** `requirements.txt` lists
    `pytest-asyncio` and `pytest-timeout`, which are test tooling, not runtime dependencies.
    Harmless today (the `quality` CI job installs from `pyproject.toml` only, so the runtime
    contract is still checked honestly) but it misrepresents what the application needs to run.
- ~~**Scheduler-schema startup readiness race** (GitHub issue #24).~~ **Resolved by S5.0**
  (2026-07-25): `KernelDaemon.start()` now ensures the scheduler schema synchronously (fail-closed)
  before returning, so `ticks`/`scheduled_tasks` exist before the API serves requests — closing the
  "no such table: ticks" 500 window at the source. PR #23 fixed the symptom at the endpoint; S5.0
  fixes the cause. See DECISIONS.md's "Scheduler schema is created synchronously during
  KernelDaemon.start()..." entry. Distinct from the two open scheduler tech-debt items below (WAL
  checkpoint instrumentation; mixed sqlite ownership), which S5.0 does **not** address.
- **Legacy “implementation notes” docs are useful but currently compete with SSOT.**
  **Mitigated 2026-07-28:** the deferred cleanup pass named here on 2026-07-27 has been done —
  every file in the list below was individually inspected and either updated in place, given a
  stale/historical banner, moved to `docs/archive/`, merged into a canonical doc, or deleted as
  actively misleading. `UI_INTEGRATION_GUIDE.md` and both copies of `DEV_SETUP_NOTES.md` (root and
  `docs/design_conversations/`) were deleted — all three described either a governed-seam bypass
  (direct `Orchestrator.handle_input()`, direct SQLite access) or an entirely different, unrelated
  tech stack. `VALIDATION_REPORT.md`, `STAGE_0_COMPLETION.md`, `docs/archive/STATUS_2025-12-29.md`, and
  `docs/audits/S0.3_checklist.md` were moved to `docs/archive/`. `.github/copilot-instructions.md`
  (the highest-risk file, since coding agents read it automatically) had its stale `barth` CLI
  references, hardcoded dev path, and self-description as "the constitutional framework"
  corrected. See the changed-file list presented alongside this reconciliation for the complete
  disposition of all ~20 files originally named here.
- **(2026-07-28) `docs/audits/S0_fastapi_lifespan_migration.md` — still-open backlog ticket,
  merged here from its own standalone file.** Migrating `bartholomew_api_bridge_v0_1/services/api/
  app.py` from FastAPI's deprecated `@app.on_event("startup"/"shutdown")` decorators to the
  `lifespan` context-manager pattern. **Verified still open 2026-07-28:** `app.py` lines 98 and
  139 still use `@app.on_event`. Low urgency (the decorators still work; this is a deprecation,
  not a current failure) but tracked here now rather than in a standalone, easily-missed ticket
  file — the original ticket has been archived to `docs/archive/` with a pointer back to this
  entry.
- **(2026-07-28) Hydration/water-logging code cleanup — future, unapproved, not prioritised
  ahead of current architectural work.** The 2026-07-28 documentation reconciliation removed
  hydration/water-logging from onboarding examples, headline demonstrations, and current product
  positioning (see `README.md`, `QUICKSTART.md`, `bartholomew_api_bridge_v0_1/README_API_BRIDGE.md`,
  `ROADMAP.md`'s Stage 0 section). The underlying code was **not** touched.
  **Corrected 2026-08-17:** this entry previously described `/api/water/log` and
  `/api/water/today` as "live, working, legacy code". **Neither endpoint exists** — a repository
  search for `api/water` finds no route registration anywhere in the codebase, and no commit
  removing them, so the claim appears never to have been accurate. What *does* remain is the
  `water_logs` table (2 rows of historical data) and the minimal UI panel
  (`bartholomew_api_bridge_v0_1/ui/minimal/index.html`), which is labelled accordingly. Whether
  to actually remove that code is a separate, future, unapproved decision — recorded here so it
  is not lost, and explicitly **not** placed ahead of Phase B/Stage 1/Stage 5 in priority merely
  because it exists.
- **(2026-07-28) Cross-device auth threat model does not yet exist.** The deployment architecture
  (`DECISIONS.md`; originally the hybrid local-first entry, whose auth gate the 2026-08-17
  server-centric entry carries forward unchanged) explicitly rejects "simple token auth is sufficient" as
  an assumption (see the corrected entry in `ASSUMPTIONS.md`) and requires a reviewed threat model
  before any remote/cross-device exposure of the local runtime. That threat model does not exist
  yet. This is a genuine open risk, not merely a documentation gap: any Stage 6 work that exposes
  the local runtime remotely before this threat model exists and is reviewed would violate the
  deployment-architecture decision.
- **(2026-07-28) Jurisdiction-aware recording/capture compliance is unresolved.** Per
  `CONSTITUTION.md`'s capture-and-recording-safety invariant (recording legality, consent/notice
  requirements, retention, deletion/revocation, public-vs-private context, and travel between
  jurisdictions), no jurisdictional analysis exists yet for any future microphone/camera capture
  capability. This is design-scope risk for Stage 6 (see `ROADMAP.md`), tracked here so it is not
  silently assumed away when real capture work begins.
- ~~Retrieval mode factory mismatches (explicit mode returns wrong retriever).~~ Appears
  resolved: `tests/test_retrieval_factory.py` has explicit coverage for `fts`/`vector`/`hybrid`
  mode selection, invalid-mode handling, and env/config override precedence, all passing
  locally as of 2026-07-21. Left struck through rather than deleted since this wasn't
  independently re-derived from a bug report, only inferred from current test coverage.
- ~~Chunking engine exists but not wired; risk of architecture drift.~~ Stale as of
  2026-07-21: `bartholomew/kernel/chunking_engine.py`'s `ChunkingEngine` is wired into
  `memory_store.py`'s live `upsert_memory()` path (`chunking_engine.enabled` defaults to
  `True`), not a standalone/dormant module — `pytest -q -k chunk` (16 tests) passes locally.
  Left struck through rather than deleted for the same reason as the retrieval-mode item above.
- **(2026-07-24) Unresolved root cause: why a `TRUNCATE` checkpoint outlasted its own
  busy-timeout in CI.** `db_ctx.py`'s `wal_checkpoint()` gained temporary DEBUG-level
  instrumentation (start time, duration, thread, mode, label, the checkpoint's own result row,
  `in_transaction`) to help answer this — it's inert unless that logger's level is explicitly
  raised, so it carries no runtime cost today, but it has no removal date or owning
  investigation ticket. See MASTER_PLAN.md item 11.18 / DECISIONS.md's "Scheduler persistence
  moved off the event loop..." entry for the incident. Either resolve the question and remove
  the instrumentation, or turn it into permanent, deliberately-scoped observability — leaving it
  as unowned "temporary" code is the debt.
- **(2026-07-24) Mixed synchronous `sqlite3` and `aiosqlite` ownership of the same database
  file.** `memory_store.py` (aiosqlite), `scheduler/persistence.py` (sync, now behind
  `SchedulerStore`'s dedicated thread), and `persona_pack.py`/`narrator.py` (sync, still called
  directly from async methods, not audited or fixed by item 11.18) all read/write the same
  underlying db file with no single owner. Item 11.18 fixed the one call path proven to hang
  (the scheduler's own tick loop); it deliberately did not consolidate database ownership more
  broadly, and did not touch `bartholomew_api_bridge_v0_1/services/api/db_ctx.py` — a
  near-duplicate of `bartholomew/kernel/db_ctx.py` with the same per-call-checkpoint pattern
  still live in `liveness.py`/`db.py`'s hot paths. Same latent hazard class as item 11.18 fixed;
  not yet known to have caused a failure outside the one incident that prompted this fix.
- **(2026-08-10) FTS5 external-content `'delete'` command uses stale (empty-string) old values —
  possible stale/searchable content after an update.** Found while writing S5.1's
  caller-supplied-summary regression tests (`docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md`); not
  caused by S5.1. `memory_fts` (`bartholomew/kernel/fts_client.py`) is an FTS5
  **external-content** table (`content='memories', content_rowid='id'`).
  `MemoryStore.upsert_memory()`'s own FTS management code issues
  `INSERT INTO memory_fts(memory_fts, rowid, value, summary) VALUES ('delete', ?, '', '')`
  before re-inserting the new `index_text` — but FTS5's `'delete'` special command on an
  external-content table needs the *actual previously-indexed* column values to correctly locate
  and remove the corresponding index entries; passing empty strings instead means this delete is
  a no-op. **Effect:** whenever an update's new `index_text` differs from what was previously
  indexed (e.g. redaction, `summary_only` substitution, or any other change to what gets
  indexed), the old content's terms can remain matchable via FTS `MATCH` alongside the new
  content — content intended to be replaced or reduced may remain searchable. **Reproduced**
  against a plain, non-competency memory write with no caller-supplied summary (an ordinary
  auto-summarised `summary_only` update), confirming this predates and is unrelated to S5.1 — not
  a regression that stage introduced. **Risk category:** correctness/privacy/governance — the
  same shape of risk R1 above tracks (content that should be excluded/replaced remaining
  reachable through a production retrieval surface), via a different mechanism (a stale FTS
  index, not a consent-gate bypass).

  **RESOLVED for the live write path (2026-08-10, `bbd920d`, merged via PR #40) — one narrower
  residual remains open, see below.** The single-writer `memory_fts` architecture replaced the
  trigger-plus-manual-override design wholesale. `memory_fts_map.last_index_text` now records the
  verbatim governed text last indexed for each memory, and every writer
  (`MemoryStore.upsert_memory()`/`delete_memory()`, `FTSClient.upsert()`/`delete()`,
  `scripts/backfill_fts.py`) goes through one shared primitive pair
  (`reindex_memory_fts[_async]()`/`remove_memory_fts[_async]()`) that issues FTS5's `'delete'`
  with that tracked text rather than the empty strings this entry was raised about
  (`fts_client.py`'s `FTS_DELETE_TRACKED_SQL`). Both regression criteria this entry asked for are
  met by `tests/test_fts_single_writer_architecture.py`: (a) `test_stale_tokens_not_matchable` and
  `test_repeated_updates_with_summary_no_corruption` prove replaced tokens stop being `MATCH`-able
  after an `index_text`-changing update; (b) `test_raw_value_not_matchable_when_summary_preferred`
  proves FTS holds only the currently governed representation. Verified green 2026-08-11.

  **Residual, still open — pre-existing databases carrying trigger-era index content.**
  `FTS_DELETE_TRACKED_SQL` matches zero rows when `last_index_text IS NULL`, which is exactly the
  state of every row in a database migrated from the old trigger architecture (`init_schema()`
  adds the column via `ALTER TABLE`, leaving it NULL). `MemoryStore.init()`'s
  `_heal_unindexed_memories()` does select those rows (`fm.last_index_text IS NULL`) and reindex
  them, but its delete is a no-op for precisely them — so it **inserts the newly governed text
  alongside, rather than in place of, whatever the old triggers had indexed**. **Reproduced
  2026-08-11** against a migrated-state database: a stale token planted as trigger-era index
  content was still `MATCH`-able after a full `MemoryStore.init()` heal, returning the memory with
  its current value. This is the same privacy/correctness shape as the original finding, narrowed
  from "every update" to "content indexed before the migration," and it is **not** covered by the
  tests above (`test_bypass_write_degrades_safely_and_self_heals` covers a memory with *no* index
  entry, which is a different starting state).

  **Verified remedy, not yet wired into any automatic path:** `scripts/backfill_fts.py` calls
  `FTSClient.reset_index()` (drop+recreate `memory_fts`, clear `memory_fts_map`) before
  repopulating every row through `compute_governed_index_text()`, so no pre-migration entry can
  survive it — confirmed 2026-08-11 to clear the reproduced stale token. Running it is currently a
  manual operator step. **No production fix for the residual is authorised yet.** A dedicated fix
  should decide whether migration should force a full governed rebuild (or otherwise clear
  untracked entries) rather than relying on an operator remembering to run the backfill, and
  should add the migration-state regression test the current suite lacks.
- **(2026-08-11) Two overlapping sensitivity vocabularies disagree —
  `privacy_guard.SENSITIVE_KEYWORDS` vs. `memory_rules.yaml`'s `ask_before_store`.** Recorded as a
  separate architectural/governance issue while fixing the `is_sensitive()` false positives (see
  `tests/test_privacy_guard_structural_scanning.py`); **deliberately not resolved as part of that
  fix.** Two independent mechanisms decide whether a memory write needs consent, and they do not
  agree on what is sensitive:
  - `bartholomew/kernel/memory/privacy_guard.py`'s `SENSITIVE_KEYWORDS` — a hardcoded Python list
    (`name`, `address`, `location`, `phone`, `email`, `bank`, `password`, `routine`, `health`,
    `private`, `account`), matched against the write's value, queuing to `pending_sensitive_writes`
    with `reason='privacy_guard'`.
  - `bartholomew/config/memory_rules.yaml`'s `ask_before_store` category — governed, reviewable
    config with regex patterns (`password|bank|account number|two-factor|auth code`,
    `bank|medical|address|phone|email`, `personal data|personal information`, plus tag/speaker
    rules), queuing with `reason='rule_consent'`.

  They overlap heavily but are not consistent. The clearest divergence: **`name` is treated as
  consent-requiring by `privacy_guard` and not by `memory_rules.yaml`**, so whether a bare personal
  name gates a write depends on which mechanism happens to see it. `routine`, `location` and
  `private` are likewise privacy_guard-only. The practical consequences are that the stricter,
  cruder list is the hardcoded one rather than the governed one; that a policy change made in the
  reviewable config can be silently overridden by the Python list; and that the two produce
  different `reason` values for what a user experiences as the same decision.

  **Risk category:** governance legibility and policy consistency — not a known live leak (both
  mechanisms fail *closed*, so the disagreement over-triggers consent rather than under-triggering
  it). **No reconciliation is authorised.** A dedicated pass should decide which vocabulary is
  authoritative, whether `privacy_guard`'s list should move into `memory_rules.yaml` (making it
  reviewable and reloadable like every other rule), and what the migration means for content
  already queued under either `reason`.
- **(2026-08-14) LICENSE declaration is inconsistent and no LICENSE file exists.** `README.md`'s
  "License" section states `CC-BY-NC-4.0`; `pyproject.toml`'s `[project].license` field states
  `{text = "MIT"}`; no `LICENSE`/`LICENSE.md`/`LICENSE.txt` file exists anywhere in the repository
  (confirmed by direct search). These are materially different licences (one Creative Commons
  licence containing a non-commercial restriction, one permissive software licence), and this
  ambiguity is exactly the kind of thing that stalls investor/acquirer/customer diligence. **Risk
  category:** legal/IP, commercial-readiness. **Not resolved by this pass** — which licence is
  correct is a business decision for the project owner, not something a documentation review
  should decide unilaterally. **Before external beta at the latest**, the project owner should
  choose one licence, add a real `LICENSE` file, and make `pyproject.toml`/`README.md` agree.
- **(2026-08-14) No dependency-licence scanning, SBOM generation, or software-composition-analysis
  (SCA) tooling exists.** `pyproject.toml`/`requirements*.txt`/`requirements.lock` pin
  dependencies, and `tests/smoke/test_packaging_contract.py` catches undeclared runtime imports
  (Phase A), but nothing checks licence compatibility of declared dependencies, scans for known
  vulnerabilities (no `pip-audit`/`safety`/Dependabot/equivalent — confirmed absent by direct
  search), or can produce an SBOM. **Risk category:** legal/IP, security, commercial-readiness.
  **Not required for the current single-developer Usable POC** (see `docs/TILT.md`); basic
  dependency-vulnerability scanning is **recommended before external beta** (other people's
  data/devices become involved); licence-compatibility scanning and SBOM generation are
  **required before commercial release**. See `DECISIONS.md`'s AI-governance entry.
- **(2026-08-14) No lightweight record of which AI coding tools/services this repository's
  development relies on, or their output-ownership/terms posture.** Git history shows two tools
  with actual authorship evidence over this project's life (Cline — see `DECISIONS.md`'s
  "Prompt-size discipline for agent execution (Cline)" entry, 2026-01-19; Claude Code — the
  majority of commits since the 2026-07-22 Architect handover). `.github/copilot-instructions.md`
  additionally configures this repository to support GitHub Copilot, but no Copilot-attributed
  commit, trailer, or other usage record exists today — that file demonstrates configuration, not
  proven use, and should not be read as a third data point of actual usage. No document records
  even the two tools with proven use, or notes that each tool's terms (output ownership, indemnity,
  training/data-use settings) are provider-controlled and should be verified against current terms
  before being relied on commercially, rather than assumed stable. **Risk category:**
  legal/IP. **Recommended before external beta** — cheap to produce (a short table, not a legal
  opinion) and closes a real commercial-diligence question.

## Red-team focus areas

1. Consent gate bypass paths (`apply_consent_gate=False`)
2. Parking-brake coverage of any new subsystem
3. Log redaction (ensure sensitive strings never hit logs)
4. Retrieval leakage via snippets/metadata


## Risk: LLM provider rate limits / prompt bloat (operational)
- **Why it matters:** Large “one-shot” prompts can exceed token-per-minute limits (as seen in Cline) and fail nondeterministically.
- **Mitigation:** Chunk/map-reduce processing; reference files instead of pasting; keep prompts under a hard cap; prefer local parsing for huge artifacts.
- **Test/Proof:** A scripted chunking run that produces stable intermediate artifacts + a final merge.
- **Status:** Active
