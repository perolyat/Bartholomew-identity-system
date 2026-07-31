> **REFERENCE / RESEARCH — NON-AUTHORITATIVE FOR IMPLEMENTATION**
>
> This document preserves Phase B research: proposed mechanisms, concurrency and lifecycle risk
> analysis, a repository investigation record, failure sequences, candidate invariants, and
> candidate tests. It is **not** an approved implementation specification. Any mechanism described
> here must be independently revalidated against the actual repository state during the Phase B
> stage that owns it (see `docs/PHASE_B_RISK_MAP.md` for the stage mapping). Approval of this
> archived document is **not** approval of B0 or any other implementation stage. The authoritative
> Phase B stage structure, gates, and status are defined in `ROADMAP.md` and summarised in
> `docs/PHASE_B_OVERVIEW.md`; this document is subordinate to both.
>
> Archived 2026-07-31 from the most recently amended working copy produced during the Phase B
> design-review process. Content below this banner is reproduced verbatim from that working copy
> and has not been rewritten.

---

# Phase B — Persistence Ownership Specification (Final, Standalone, Corrected)

**Status:** DESIGN ONLY. No branch created for this work, no repository file edited, nothing staged,
nothing committed. This document is a complete specification, not an implementation. Slice 0 has not
begun and requires its own separate, explicit go-ahead after this document is approved.

**Repository status, re-confirmed at the moment this document was written:**

```
$ git branch --show-current
claude/phase-b-persistence-design-pdbcqt
$ git rev-parse HEAD
d96e7887ddd6c8e6d231052b5e6d7599e0739ee8
$ git status --short
(clean — no output)
$ git status
On branch claude/phase-b-persistence-design-pdbcqt
nothing to commit, working tree clean
```

No file is modified, staged, or untracked. `claude/phase-b-persistence-design-pdbcqt` is the session's
pre-existing checkout branch — it was not created, and nothing has been committed to it, during this or
any prior design-review turn. HEAD remains at `d96e7887ddd6c8e6d231052b5e6d7599e0739ee8`, unchanged
throughout every round of this review.

**This specification is standalone relative to Designs v1–v12 and all earlier Phase B design documents.
It requires only the repository at commit `d96e7887ddd6c8e6d231052b5e6d7599e0739ee8`, this document, and
separately approved execution specifications explicitly identified herein.**

This document fully supersedes every prior version of this specification. It is self-contained and does
not depend on any other document. It incorporates corrections from an independent closure review that
inspected both this specification and the actual repository at the commit above. That review's nine
validated blocking findings are resolved in the **Blocker-Resolution Matrix** immediately below, each
traced through every affected definition, caller, route, CLI path, schema element, and test. Nothing in
this document should be read as a diff against an earlier version — every mechanism described here is
the final, current design, verified again in this pass.

**Legend:** **[V]** verified against commit `d96e7887ddd6c8e6d231052b5e6d7599e0739ee8` by direct
inspection during this pass. **[I]** inference from that verification. **[P]** proposed design decision.

---

## Blocker-Resolution Matrix

Nine findings were submitted by an independent closure review. Each was independently re-verified against
this specification's own text and, where the finding made a claim about the repository, against the
actual repository at the commit above (not the reviewer's citation alone). All nine are **ACCEPTED** —
every one identified either a genuine internal contradiction in the prior version of this document, or a
genuine mismatch between this document's assumptions and the actual repository.

| # | Verdict | Repository / specification evidence (re-verified) | Correction applied | Affected definitions / callers | Proving test |
|---|---|---|---|---|---|
| 1 | **ACCEPTED** | The prior shutdown sequence computed `internal_tasks_terminal` but its clean-marker gate tested only `admission_terminal`, `governance_drained`, and `governance_lane_closed_cleanly`. The clean-marker SQL's own `WHERE` clause checks only `runtime_id` and `write_fence_open=0` — it has no representation of producer/admission/supervisor state. | `internal_tasks_terminal` added as a mandatory precondition to the clean-marker gate (§9). Invariant I8 rewritten to separate the one condition the SQL `WHERE` clause itself enforces (fence closed, correct runtime) from the conditions verified in Python before that transaction is even attempted (§2). | `KernelDaemon.stop()` (§9); I8 (§2) | Test 1 (§13) |
| 2 | **ACCEPTED** | The prior `start()` failure handler released the daemon lock and re-raised with no unwind of MemoryStore, either executor, any producer task, or `BrakeWatcher`. | A complete, stage-aware, reverse-order unwind (`_unwind_failed_start`) added, verifying each activated resource's terminal state before the lock is released; unwind failure poisons the instance and retains the lock (§8). | `KernelDaemon.start()`, new `KernelDaemon._unwind_failed_start()` (§8) | Tests 2, 2a–2d (§13) |
| 3 | **ACCEPTED** | Direct repository inspection at `d96e7887ddd6c8e6d231052b5e6d7599e0739ee8` (this pass, not the reviewer's citations alone) confirms: the real chat route is `POST /api/chat` (`bartholomew_api_bridge_v0_1/services/api/app.py:249`); `POST /kernel/command/{cmd}` exists (`app.py:183`); `POST /api/nudges/{nudge_id}/ack` and `/dismiss` exist and write through `MemoryStore.set_nudge_status()` directly (`app.py:318,331`); `POST /api/reflection/run` exists (`app.py:370`); no `handle_sight()`/`handle_voice()` method or HTTP route exists anywhere — only `run_sight_through_runtime_contract()`/`run_voice_through_runtime_contract()` seam functions (`bartholomew/kernel/runtime_contract.py:578,684`), reachable only from tests and non-HTTP adapters, not from any API route. `Orchestrator.handle_input()` (`identity_interpreter/orchestrator/orchestrator.py:116`) is `def`, not `async def`, and constructs its own independent `ParkingBrake(BrakeStorage(_default_db_path()))` inline (lines 130–133) — there is no shared-instance injection point anywhere in the current repository. | Ingress inventory replaced with the five real routes; `handle_input()`'s synchronous signature preserved unchanged; admission is checked at each real route, validated at the async seam/handler each route calls into; the shared-`ParkingBrake` consolidation target is named precisely across all seven real construction sites (§7.3, §8, §12). | `chat()`, `kernel_command()`, `ack_nudge()`, `dismiss_nudge()`, `trigger_reflection()` (all in `app.py`); `Orchestrator.handle_input()`; `run_chat_through_runtime_contract`, `run_drive_through_runtime_contract`, `run_sight_through_runtime_contract`, `run_voice_through_runtime_contract` (`runtime_contract.py`); `skill_registry.py`'s construction site; `Orchestrator.set_parking_brake()` (new) | Test 3, 3a (§13) |
| 4 | **ACCEPTED** | `_bump_with_version()` unconditionally assigns `persisted_version = new_persisted_version` with no comparison against the current value, while `_reconcile_persisted_version()` (called immediately before it, on the same successful result) can already have advanced `persisted_version` past that same value via an independent, concurrent observation (e.g. `BrakeWatcher`) that does not touch `revision`. A delayed loosening whose `revision` check still passes can therefore regress `persisted_version` and reapply stale scopes. | `_bump_with_version()` rewritten to reject `observed_version < current.persisted_version` inside the same `_state_lock` critical section that applies the result — never assigning a lower version, never applying stale state (§5.5). | `ParkingBrake._bump_with_version`, `_apply_if_still_current`, `_reconcile_pending_loosening` (§5.5) | Test 4 (§13) |
| 5 | **ACCEPTED** | `engage()`'s own contract (§5.3) already refused mutation once `CLOSED`, but the shutdown-sequence proof (§9 of the prior version) stated a post-shutdown governance call "can... continue applying its in-memory tightening locally" — directly contradicting `engage()`'s own code, and the prior version's Test 34 required exactly the behavior Test 17 required to be absent. | The shutdown proof rewritten to state only the `engage()`-contract-consistent outcome (refusal, no mutation, in either lifecycle state once `CLOSED`); Test 34 rewritten to assert the same outcome as Test 17, and the two are merged into one authoritative test rather than left as two contradictory ones (§9, §13). | `KernelDaemon.stop()`'s proof paragraph (§9); Test 17/34 (§13, merged) | Test 17 (§13) |
| 6 | **ACCEPTED** | `DedicatedDbExecutor.close()` called `self._pool.shutdown(wait=False, cancel_futures=False)` and returned `drained=True` once the last accepted operation's future resolved — never verifying the worker thread itself exited, despite this specification's own prose elsewhere claiming the executor is "drained, worker thread joined" before the clean marker. | `close()` rewritten to perform a bounded join (`asyncio.wait_for(asyncio.to_thread(self._pool.shutdown, wait=True), timeout=timeout)`) and return `False` if the join cannot be confirmed within the timeout (§4.3). | `DedicatedDbExecutor.close` (§4.3); `KernelDaemon.stop()`'s reliance on its return value (§9) | Test 6 (§13) |
| 7 | **ACCEPTED** | The prior I1 claimed `schema.py` as "the sole authority for every DDL statement Phase B touches, including MemoryStore/scheduler," while §3 defined only the governance tables and the implementation-slice section stated Slices 9–16 perform no cross-module schema consolidation — an unresolved contradiction between claimed and actual scope. Direct repository inspection confirms `MemoryStore`'s own DDL lives in `bartholomew/kernel/memory_store.py` (`CREATE TABLE` statements at lines 107, 118, 130, 144, 155, 162) and the scheduler's own DDL lives under `scheduler/persistence.py`, under their existing modules' ownership, unchanged. | I1 narrowed unambiguously to the three governance tables this specification actually defines (§2, §3); `MemoryStore`'s and the scheduler's existing schema ownership is explicitly stated as unchanged and out of this specification's authority, consistent with the "no cross-module consolidation" slice boundary already in place. | I1 (§2); §3's schema section; §15's slice boundaries | Test 7 (§13) |
| 8 | **ACCEPTED** | `rollback_clear_maintenance()` deleted the marker file unconditionally, with no lock acquisition or process check of any kind — its own docstring already conceded this rests on operator judgement alone. A legacy, pre-Phase-B process, by construction, never participates in `DaemonProcessLock` and cannot be detected by it. | `rollback_clear_maintenance()` now performs a best-effort `BEGIN EXCLUSIVE` quiescence check before clearing — detecting (not proving the absence of) an actively-transacting writer at the instant of the check — and its own output text states explicitly, honestly, that this is not a complete guarantee against an idle legacy process, which remains an irreducible operator responsibility (§10.3, §14). | `rollback_clear_maintenance` (§10.3) | Test 8 (§13) |
| 9 | **ACCEPTED** | `GovernedRequestAdmission.release()` took no identity argument — any caller could decrement `_in_flight` regardless of which (if any) admission it was releasing, and the prior underflow clamp protected only against going negative, not against releasing the wrong active admission. | Replaced the bare counter with a set of active admission identities; `try_admit()` now returns a fresh identity (or `None` if refused); `release(admission_id)` removes only that exact identity if present, and is a no-op — never affecting any other admission — for a foreign, duplicate, or unrecognized identity (§7.2). | `GovernedRequestAdmission`, `AdmissionToken`, `_AdmissionScope`, `spawn_detached_governed_task` (§7.2, §7.4) | Test 9 (§13) |

**Additional test corrections from the independent test-feasibility audit, applied throughout §13:** Test
28 now drives the five real ingress routes rather than hypothetical `/chat`/`/sight`/`/voice`. Test 34 is
merged into Test 17 (§9's contradiction resolution). Test 3 now asserts worker-thread termination, not
only gate non-force-release. Test 21 is narrowed to what the clean-marker SQL alone actually proves, with
producer/admission terminality now covered by Tests 1 and 33 respectively. Test 41 gains an explicit
maintenance-clearance sub-case. Test 51 replaces the point-in-time lock/transaction assertion with a
many-iteration concurrent stress harness bounded by a timeout (a real deadlock manifests as the test
hanging past it), plus post-iteration invariant checks — not a snapshot claimed to be a structural proof.
Test 52 adds a genuinely partial schema (existing tables missing the newly-required columns), not only a
fresh database and a fully-complete one. Test 54 now covers all seven real `ParkingBrake` construction
sites, not `handle_input()` alone.

---

## 1. Scope

Phase B unifies persistence and governance-state ownership across the Bartholomew kernel and API
bridge: one `db_ctx` connection/pragma module; one executor implementation run as two independently
configured lanes (general, governance); `MemoryStore` concurrency hardening including a corrected,
genuinely transactional `reembed_memory()`; a `PersistenceTaskSupervisor` with a whole-runtime failure
ledger; a complete `ParkingBrake` redesign (race-safe state machine, monotonic `engage()`, a durable
database-visible write fence, a runtime-scoped clean-shutdown protocol, fail-closed `BrakeWatcher`
degradation, an offline CLI protocol); `VectorStore`/`FTSClient` daemon-integrated adapters; the
liveness/metrics read-lane correction; CLI path/command/audit/schema corrections; a request-scoped API
admission-identity protocol; and a cross-platform, poison-aware daemon process lock.

**Explicitly out of scope, deferred to a separately approved Phase C:** relocating `MemoryStore`'s or the
scheduler's own schema ownership into `bartholomew/kernel/db/schema.py` (§2's I1, narrowed this pass, and
§7's schema-authority resolution); a split-process/multi-host deployment topology; a real authenticated
network control-plane for external governance operations (the offline CLI protocol in §6 is a local,
single-host, process-lock-based mechanism, not network-facing); a schema-version table; automatic
orphan-detection for embeddings; wiring `run_sight_through_runtime_contract`/`run_voice_through_runtime_
contract` to an actual external (HTTP or adapter) ingress, since no such ingress currently exists to wire
them to.

---

## 2. Final invariants and ownership rules

These are the properties every mechanism in this document exists to uphold.

**I1 — One schema authority for the tables this specification defines.** `bartholomew/kernel/db/schema.
py` is the sole authority for `parking_brake_state`, `brake_runtime`, and `governance_audit` — the three
governance tables this specification adds or touches (§3). It is **not** claimed as the authority for
`MemoryStore`'s own tables (`memories`, `memory_chunks`, `nudges`, `reflections`, `memory_consent`,
`system_flags` — defined in `bartholomew/kernel/memory_store.py`) or the scheduler's own tables (defined
in `scheduler/persistence.py`), both of which remain under their existing modules' ownership, unchanged,
for the duration of Phase B. Relocating them into `schema.py` is explicitly deferred to a separately
approved Phase C (§1) — this specification claims authority only over the tables it actually defines in
§3, never a broader "all DDL in the system" claim.

**I2 — Exactly two persistent-connection owners for daemon-driven writes.** `MemoryStore` holds one
persistent `aiosqlite` connection for its own domain (unaffected by I1's narrowing — its schema ownership
is unchanged, only its *connection-management* pattern is within Phase B's scope). `DedicatedDbExecutor`,
instantiated twice (general lane, governance lane), owns all other daemon-driven writes through
short-lived, per-operation connections opened and closed on a dedicated worker thread. Every other module
that needs to read or write this database (`VectorStore`, `FTSClient`, the CLI, `rollback_prepare`) does
so through either a daemon-integrated adapter routed through one of the two lanes, or — for the CLI and
rollback tooling, which run as separate, short-lived processes — a direct, short-lived connection gated
by the mechanisms in §6 and §10, never by assumption.

**I3 — Governance state has exactly one authoritative persisted representation.** `parking_brake_state`
(current engaged/scopes/version/last-loosening-marker) and `brake_runtime` (current runtime identity,
clean-shutdown marker, durable write fence) are read and written only through `_engage_op`/`_loosen_op`/
`_establish_clean_shutdown_direct`/`establish_runtime`/`migrate_legacy_parking_brake_value` (§5, §9). No
other code path writes these tables directly — including the CLI, which is routed through the same
`_engage_op`/`_loosen_op` functions the daemon uses (§6).

**I4 — Tightening is monotonic and always safe to apply; loosening always requires confirmation.**
`engage()` (in-process) and `apply_external_refresh()` (from any external observation) can only ever
widen the locally enforced scope set or leave it unchanged — never narrow it based on an unconfirmed
signal. `disengage()`/`narrow_scopes()` never change local enforcement until their own persistence has
durably confirmed, via an exact optimistic-concurrency check against the database, and — as of this
pass's correction — never apply a confirmation whose observed version is older than what local state
already knows (§5.5).

**I5 — A stale write can never restore what a later, confirmed loosening removed.** Every engage
persistence attempt is fenced against an exact `version`; on failure, the response's `last_loosen_
version` proves — unmaskably, database-resident, not reconstructed from a Python-level counter or an
audit-history lookup — whether a loosening has committed since the attempt's own fence point. If so, the
attempt stops without retrying.

**I6 — No engage command's intent can be silently dropped.** Every `engage()` call owns an independent,
dedicated persistence task. No two calls ever share a task, so one task's decision to stop (per I5) can
never strand a different call's still-pending intent.

**I7 — No external, out-of-process writer can mutate governance state while the daemon is mid-shutdown,
and no accidental caller can disable that protection.** A durable, database-resident write fence
(`brake_runtime.write_fence_open`), checked transactionally inside every governance write regardless of
caller, closes this gap. It can be bypassed only by presenting an `OfflineWritePermit` — a capability
that can only be constructed while a process-lifetime lock proves no daemon is running — never by a
boolean argument an ordinary caller could pass.

**I8 — `brake_runtime.clean=1` is committed only as the last governance database transaction of a
shutdown, and only when every precondition holds — stated precisely, distinguishing what the commit's own
SQL enforces from what is verified in Python before that transaction is even attempted, since these are
different kinds of guarantee and must not be conflated.**
  - **Enforced by the commit's own `WHERE` clause, transactionally, at the instant of the commit:** the
    runtime identity matches (`runtime_id=?`) and the durable write fence is closed (`write_fence_open=
    0`) (§5.7, §9).
  - **Verified in Python, as mandatory preconditions checked before the commit is even attempted, and
    now including every one of the following, not a subset:** external request admission has drained
    (`admission_terminal`), every internal producer task has been cancelled and awaited to actual
    completion (`internal_tasks_terminal` — corrected this pass to be a mandatory precondition, not
    merely computed and left unchecked), governance writes have drained (`governance_drained`), and the
    governance executor's worker thread has been confirmed terminated, not merely instructed to stop
    (`governance_lane_closed_cleanly`, corrected this pass per I8's own requirement that this claim be
    genuinely verified, §4.3). If **any** of these four Python-verified preconditions is false, the
    commit is never attempted at all (§9) — the SQL's own enforcement is a second, independent barrier
    for the two conditions it can check, not the sole guarantee for the other two.

**I9 — External request admission is admitted exactly once, at the true outermost ingress, for the real
routes this repository defines, and revoked before the admission count is released.** A `ContextVar`-
carried, capability-style token — not a docstring convention — is the only thing downstream governed code
accepts as proof of admission. Detached child work that outlives its parent request must register its own,
independent admission or is refused. Release is bound to the exact admission identity that owns it — a
foreign, duplicate, or unrecognized identity affects no other admission's tracked state (§7).

**I10 — The daemon's process lock is released only after every activated resource — whether reached
during a complete shutdown or abandoned mid-startup — reaches a verified terminal state.** An incomplete
shutdown or a failed startup that cannot verify every resource it activated has stopped — by exception or
by a resource's own reported non-termination — poisons the instance and withholds the lock; only OS-level
process termination frees it in that case (§8, §9).

**I11 — Every governance write, offline or online, in-process or CLI, is auditable.** Every committed
transition (engage, disengage, narrow) inserts exactly one row into `governance_audit`, in the same
transaction as the state change, with a UUID key (never colliding, never requiring a second table's
schema to exist).

**I12 — Clearing rollback's maintenance fence is bounded by the best available check, honestly scoped, not
claimed as a complete guarantee.** A legacy, pre-Phase-B process participates in no part of this
specification's locking protocol and cannot be proven stopped by it. Maintenance clearance performs the
strongest check actually available — a point-in-time exclusive-lock quiescence probe — and states plainly
that this detects an actively-transacting writer, not the absence of one (§10.3, §14).

---

## 3. Complete schema and migrations

All DDL for the three tables this specification owns (I1) lives in `bartholomew/kernel/db/schema.py`.
`ensure_governance_schema(conn)` is idempotent, safe to call on every startup and from the CLI's own
preflight — it creates every governance table with its final column set and additionally, defensively,
adds any column a table might be missing if it already exists from an older, partial bootstrap of this
same schema (exercised explicitly by Test 52, §13, against a genuinely partial table shape, not only a
fresh or fully-complete database).

```python
# bartholomew/kernel/db/schema.py

def ensure_governance_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parking_brake_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            engaged INTEGER NOT NULL,
            scopes_json TEXT NOT NULL,
            version INTEGER NOT NULL,
            last_loosen_version INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brake_runtime (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            runtime_id TEXT NOT NULL,
            clean INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            write_fence_open INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS governance_audit (
            event_id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            engaged INTEGER NOT NULL,
            scopes_json TEXT NOT NULL,
            version INTEGER NOT NULL,
            source TEXT NOT NULL,
            ts TEXT NOT NULL
        )
    """)
    _ensure_column(conn, "parking_brake_state", "last_loosen_version",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "brake_runtime", "write_fence_open", "INTEGER NOT NULL DEFAULT 1")
    _ensure_brake_runtime_singleton_row(conn)


def _ensure_column(conn, table: str, column: str, ddl_type_and_default: str) -> None:
    """Idempotent ADD COLUMN: SQLite has no ADD COLUMN IF NOT EXISTS.
    Checked via PRAGMA table_info, never a blind try/except, so a
    genuine, unrelated OperationalError is never silently swallowed.
    This is the code path that makes a genuinely PARTIAL prior schema
    (a table that already exists but predates one of these columns)
    safe: CREATE TABLE IF NOT EXISTS is a no-op against it, and this
    function is what actually brings it up to date, preserving every
    existing row and defaulting the new column correctly for each."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type_and_default}")


def _ensure_brake_runtime_singleton_row(conn) -> None:
    """
    Schema-level guarantee: brake_runtime ALWAYS has exactly one row
    (id=1) after this function runs, with write_fence_open=1 by
    default. This makes "brake_runtime has no row" structurally
    unreachable after schema initialization — every reader of
    write_fence_open (§6) treats a missing row as a pure defensive
    backstop for an already-impossible condition, never a normal path.
    runtime_id is a placeholder empty string here; establish_runtime()
    (§5.9) always overwrites it at the first real daemon startup.
    """
    conn.execute(
        "INSERT INTO brake_runtime (id, runtime_id, clean, started_at, write_fence_open) "
        "VALUES (1, '', 0, ?, 1) ON CONFLICT(id) DO NOTHING",
        (_now_iso(),),
    )
```

**One value migration, run once per daemon startup, idempotent by construction:**

```python
async def migrate_legacy_parking_brake_value(self) -> None:
    """
    If parking_brake_state has NO row yet, AND a legacy system_flags
    ['parking_brake'] row exists (written by pre-Phase-B code), seed
    parking_brake_state's initial row from that value. If parking_
    brake_state already has a row, or neither row exists, this is a
    no-op. Idempotency is structural: the first check makes every call
    after the first a guaranteed no-op — safe to call unconditionally on
    every startup, no separate "have I migrated" flag needed. Runs as
    one atomic transaction via the governance lane.

    Interaction with establish_runtime()'s own conservative default,
    stated explicitly: on the very first Phase-B-aware startup against
    a pre-existing database, there is also no brake_runtime row with a
    matching runtime_id yet — establish_runtime() (§5.9) will therefore
    see prev_clean=False and force engaged=global regardless of what
    this migration just seeded. This is correct, not an oversight:
    pre-Phase-B code never maintained brake_runtime under this
    protocol's terms, so there is no proof its last shutdown was clean.
    The migrated value takes effect starting from the SECOND Phase-B
    startup onward, once one clean shutdown has legitimately occurred.

    system_flags itself is a MemoryStore-owned table (I1) — this
    function only ever reads it, never creates or migrates it; it must
    run after MemoryStore.init() has established it.
    """
    def _op(conn: "sqlite3.Connection") -> str:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute("SELECT 1 FROM parking_brake_state WHERE id=1").fetchone()
            if existing is not None:
                conn.rollback()
                return "already_migrated_or_initialized"
            legacy_row = conn.execute(
                "SELECT value FROM system_flags WHERE key = 'parking_brake'",
            ).fetchone()
            if legacy_row is None:
                conn.rollback()
                return "no_legacy_value_fresh_install"
            legacy = json.loads(legacy_row[0])
            legacy_engaged = bool(legacy.get("engaged", False))
            legacy_scopes = frozenset(legacy.get("scopes") or (["global"] if legacy_engaged else []))
            conn.execute(
                "INSERT INTO parking_brake_state "
                "(id, engaged, scopes_json, version, last_loosen_version, updated_at) "
                "VALUES (1, ?, ?, 1, 0, ?)",
                (1 if legacy_engaged else 0, json.dumps(sorted(legacy_scopes)), _now_iso()),
            )
            _insert_audit_row(conn, uuid.uuid4().hex, "migrate_legacy_value", legacy_engaged,
                               legacy_scopes, 1, "migration")
            conn.commit()
            return "migrated"
        except BaseException:
            conn.rollback()
            raise

    tracked = await self._governance_executor.run_tracked(_op, timeout=5.0)
    if tracked.status is not OpStatus.SUCCEEDED:
        logger.error(
            "migrate_legacy_parking_brake_value(): could not confirm the migration "
            "transaction (%s) — proceeding WITHOUT it. establish_runtime() will see no "
            "parking_brake_state row and apply its own fail-closed default (globally "
            "engaged), which is safe regardless of the historical value. A migration "
            "failure is treated as equivalent to a fresh install, not an unknown/dangerous "
            "state requiring a startup abort, because its fallback is already the safe one.",
        )
        return
    logger.info("migrate_legacy_parking_brake_value(): %s", tracked.result)
```

**Migration/compatibility summary:** every DDL statement is `CREATE TABLE IF NOT EXISTS` or a
`PRAGMA`-checked idempotent `ALTER TABLE ADD COLUMN`; the one value-migration above is idempotent and
checked-before-applied; nothing here is destructive; rollback (§10) is a guarded tool, not an unenforced
instruction, with its own honestly-scoped limitation stated in I12/§14 rather than hidden.

---

## 4. Executor and supervisor contracts

### 4.1 Outcome types

```python
class OpStatus(Enum):
    CLOSED_NOT_SUBMITTED = "closed_not_submitted"
    QUEUE_TIMEOUT_NOT_SUBMITTED = "queue_timeout_not_submitted"
    SUBMITTED_PENDING = "submitted_pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DedicatedDbExecutorTimeout(RuntimeError):
    """Raised by DedicatedDbExecutor.run() for any TrackedOperation whose
    terminal status is not SUCCEEDED/FAILED. Carries the TrackedOperation
    itself (as .tracked) for callers that need to distinguish, e.g.,
    QUEUE_TIMEOUT_NOT_SUBMITTED (transient contention) from
    CLOSED_NOT_SUBMITTED (the executor is closed)."""
    def __init__(self, tracked: "TrackedOperation"):
        self.tracked = tracked
        super().__init__(f"operation did not complete: {tracked.status}")
```

### 4.2 `TrackedOperation` — one object per operation, resolved exactly once

```python
@dataclass
class TrackedOperation:
    status: OpStatus
    result: Any = None
    exception: BaseException | None = None
    _cf_future: "concurrent.futures.Future | None" = field(default=None, repr=False)
    _loop: "asyncio.AbstractEventLoop | None" = field(default=None, repr=False)
    _callbacks: list = field(default_factory=list, repr=False)
    _resolved_once: bool = field(default=False, repr=False)

    def on_eventual_completion(self, callback) -> None:
        """
        Registers callback(self) to fire once, on the event loop that
        was running when this object was created, via call_soon/
        call_soon_threadsafe — never invoked synchronously inline, never
        from a worker thread. If this object is ALREADY resolved
        (status is terminal) — true for every synchronously-terminal
        construction (CLOSED_NOT_SUBMITTED, QUEUE_TIMEOUT_NOT_SUBMITTED,
        both constructed with _resolved_once=True and a captured _loop
        so this path is always safe to call, even on an object that was
        never actually submitted to anything) as well as for the
        genuinely-pending-then-later-resolved path — the callback is
        scheduled for the next loop iteration via call_soon, not
        invoked here.
        """
        if self._resolved_once:
            self._loop.call_soon(callback, self)
        else:
            self._callbacks.append(callback)

    def _resolve_on_loop(self, fut) -> None:
        """
        Runs ON THE EVENT LOOP — either because run_tracked() itself
        calls it directly (already on the event loop, resolved-within-
        budget case) or because a worker-thread trampoline hopped here
        via call_soon_threadsafe (genuinely-pending case). Guarded by
        _resolved_once so whichever path arrives first wins and the
        other becomes a no-op — there is exactly one TrackedOperation
        object per run_tracked() call, resolved exactly once, regardless
        of which path triggers it.
        """
        if self._resolved_once:
            return
        self._resolved_once = True
        if fut.cancelled():
            self.status = OpStatus.FAILED
            self.exception = CancelledError("operation was cancelled")
        else:
            exc = fut.exception()
            if exc is not None:
                self.status = OpStatus.FAILED
                self.exception = exc
            else:
                self.status = OpStatus.SUCCEEDED
                self.result = fut.result()
        for cb in self._callbacks:
            self._loop.call_soon(cb, self)
        self._callbacks.clear()
```

### 4.3 `DedicatedDbExecutor`

One class. Two instances: `general_executor` (owned by `KernelDaemon`), `governance_executor` (owned by
`KernelDaemon`, passed into `ParkingBrake`'s constructor).

```python
class DedicatedDbExecutor:
    def __init__(self, db_path: str, *, thread_name: str, busy_timeout_ms: int = 5000,
                 max_queue_depth: int | None = None):
        self.db_path = db_path
        self._busy_timeout_ms = busy_timeout_ms
        self._max_queue_depth = max_queue_depth
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=thread_name)
        self._gate = asyncio.Lock()
        self._current_future: "Future | None" = None
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._close_result: bool | None = None
        self._waiting_count = 0   # callers currently inside await gate.acquire() — does NOT
                                    # include the operation that already holds the gate and is
                                    # executing.

    async def run_tracked(self, operation, *args, timeout: float | None = None,
                           reserved: bool = False) -> "TrackedOperation":
        """
        reserved: bypasses the queue-depth cap entirely — used ONLY by
        BrakeWatcher's own observation read (§11), so a burst of OTHER
        governance operations filling the cap can never outright reject
        the watcher's attempt to look. Reserved calls are still subject
        to the SAME single gate/thread as everything else — this
        exempts them only from the instant-rejection queue-depth check.
        """
        loop = asyncio.get_running_loop()
        if self._closed:
            return TrackedOperation(status=OpStatus.CLOSED_NOT_SUBMITTED, _loop=loop, _resolved_once=True)
        if not reserved and self._max_queue_depth is not None and self._waiting_count >= self._max_queue_depth:
            return TrackedOperation(status=OpStatus.QUEUE_TIMEOUT_NOT_SUBMITTED, _loop=loop, _resolved_once=True)

        start = time.monotonic()
        self._waiting_count += 1
        try:
            if timeout is not None:
                await asyncio.wait_for(self._gate.acquire(), timeout=timeout)
            else:
                await self._gate.acquire()
        except asyncio.TimeoutError:
            return TrackedOperation(status=OpStatus.QUEUE_TIMEOUT_NOT_SUBMITTED, _loop=loop, _resolved_once=True)
        finally:
            self._waiting_count -= 1

        if self._closed:
            self._gate.release()
            return TrackedOperation(status=OpStatus.CLOSED_NOT_SUBMITTED, _loop=loop, _resolved_once=True)

        try:
            elapsed = time.monotonic() - start
            remaining = None if timeout is None else max(0.0, timeout - elapsed)
            cf_future = self._pool.submit(self._execute, operation, args)
        except BaseException:
            self._gate.release()
            raise

        self._current_future = cf_future
        cf_future.add_done_callback(functools.partial(_gate_release_trampoline, self, loop))
        tracked = TrackedOperation(status=OpStatus.SUBMITTED_PENDING, _cf_future=cf_future, _loop=loop)
        cf_future.add_done_callback(functools.partial(_resolve_trampoline, loop, tracked))

        asyncio_fut = asyncio.wrap_future(cf_future)
        done, pending = await asyncio.wait({asyncio_fut}, timeout=remaining)
        # asyncio.wait NEVER cancels on timeout, unlike asyncio.wait_for
        # — an accepted-but-not-yet-started operation is never lost
        # merely because this call gave up waiting.
        if asyncio_fut in pending:
            return tracked   # genuinely SUBMITTED_PENDING; resolved later via the trampoline above
        tracked._resolve_on_loop(cf_future)   # resolved within budget: reuse the SAME object
        return tracked

    async def run(self, operation, *args, timeout: float | None = None) -> Any:
        tracked = await self.run_tracked(operation, *args, timeout=timeout)
        if tracked.status is OpStatus.SUCCEEDED:
            return tracked.result
        if tracked.status is OpStatus.FAILED:
            raise tracked.exception
        raise DedicatedDbExecutorTimeout(tracked)

    def _execute(self, operation, args):
        """Runs on the worker thread. Opens, pragma-configures, closes."""
        conn = db_ctx.connect(self.db_path)
        try:
            db_ctx.set_wal_pragmas(conn)
            conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            return operation(conn, *args)
        except BaseException:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            db_ctx.close_quietly(conn)

    async def close(self, timeout: float = 5.0) -> bool:
        """
        A successful (`True`) result means three things, all verified,
        not assumed: every accepted operation completed; the executor
        stopped accepting new work; and the worker thread has ACTUALLY
        terminated — `ThreadPoolExecutor.shutdown(wait=False)` alone
        proves only the second of these. This function performs a
        bounded join to verify the third, and returns `False` — never
        silently claiming success — if that join cannot be confirmed
        within `timeout`.
        """
        self._closed = True
        async with self._close_lock:
            if self._close_result is not None:
                return self._close_result
            pending = self._current_future
            drained = True
            if pending is not None and not pending.done():
                done, still_pending = await asyncio.wait({asyncio.wrap_future(pending)}, timeout=timeout)
                if still_pending:
                    drained = False
                    logger.warning(
                        "DedicatedDbExecutor(%s).close(): pending work did not finish within "
                        "%.1fs — NOT cancelled, gate is NOT force-released.",
                        self._pool._thread_name_prefix, timeout,
                    )
            if not drained:
                self._pool.shutdown(wait=False, cancel_futures=False)
                self._close_result = False
                return False

            # All accepted work is done. Now verify the worker thread
            # itself has actually exited — shutdown(wait=True) blocks
            # until it has, run off the event loop via to_thread and
            # bounded by the same timeout, so a hung worker thread
            # cannot make this function hang the caller indefinitely.
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._pool.shutdown, wait=True), timeout=timeout,
                )
                worker_terminated = True
            except asyncio.TimeoutError:
                worker_terminated = False
                logger.error(
                    "DedicatedDbExecutor(%s).close(): worker thread did not terminate "
                    "within %.1fs of shutdown(wait=True) — returning False. The clean "
                    "marker and process-lock release must NOT proceed on this result.",
                    self._pool._thread_name_prefix, timeout,
                )
            self._close_result = worker_terminated
            return worker_terminated

    def _release_gate(self, fut) -> None:
        """Runs on the event loop (via the trampoline below). Clears
        self._current_future if it matches, releases the gate if held."""
        if self._current_future is fut:
            self._current_future = None
        if self._gate.locked():
            self._gate.release()


def _gate_release_trampoline(executor, loop, fut) -> None:
    """Runs on the WORKER thread. Touches nothing except the hop."""
    loop.call_soon_threadsafe(executor._release_gate, fut)


def _resolve_trampoline(loop, tracked, fut) -> None:
    """Runs on the WORKER thread. Touches nothing except the hop."""
    loop.call_soon_threadsafe(tracked._resolve_on_loop, fut)
```

**Instantiation:** both instances are constructed by `KernelDaemon.__init__` (§8) — `general_executor`
with `thread_name="sync-db", busy_timeout_ms=5000`, `governance_executor` with `thread_name="brake-db",
busy_timeout_ms=2000, max_queue_depth=3` — and `governance_executor` is passed into `ParkingBrake`'s own
constructor as its `executor` argument, so `ParkingBrake` uses it without owning its construction.

### 4.4 `PersistenceTaskSupervisor` — whole-runtime failure ledger

```python
class SupervisorLifecycle(Enum):
    OPEN = "open"
    QUIESCING = "quiescing"
    SEALED = "sealed"
    DRAINED = "drained"


class TaskRefused(RuntimeError):
    """Raised for any refused launch — BEST_EFFORT from SEALED onward,
    CRITICAL from DRAINED onward."""


class TaskPriority(Enum):
    CRITICAL = "critical"
    BEST_EFFORT = "best_effort"


@dataclass(frozen=True)
class DrainResult:
    succeeded: tuple
    failed: tuple
    still_pending: tuple
    ever_failed: bool   # True if ANY critical task has EVER failed during this supervisor
                          # instance's ENTIRE lifetime — not just ones this specific drain
                          # call's own snapshot happened to observe.

    @property
    def clean(self) -> bool:
        return not self.failed and not self.still_pending and not self.ever_failed


class PersistenceTaskSupervisor:
    def __init__(self):
        self._tasks: dict[uuid.UUID, tuple] = {}      # id -> (Task, TaskPriority, name)
        self._outcomes: dict[uuid.UUID, OpStatus] = {}
        self._lock = threading.Lock()
        self._lifecycle = SupervisorLifecycle.OPEN
        self._any_critical_failure_ever = False        # THE latch; never reset

    def begin_quiescing(self) -> None:
        with self._lock:
            if self._lifecycle is SupervisorLifecycle.OPEN:
                self._lifecycle = SupervisorLifecycle.QUIESCING

    def seal(self) -> None:
        with self._lock:
            self._lifecycle = SupervisorLifecycle.SEALED

    def launch(self, name: str, coro_factory, *, priority: "TaskPriority") -> "asyncio.Task":
        """coro_factory: zero-arg callable, invoked ONLY on admission —
        a refused launch never creates a coroutine. Multiple concurrent
        CRITICAL launches (e.g. one independent task per engage()
        command, §5) are fully supported — this is a dict keyed by a
        fresh UUID per launch, never assuming at most one is ever
        outstanding."""
        handle_id = uuid.uuid4()
        with self._lock:
            if priority is TaskPriority.BEST_EFFORT and self._lifecycle in (
                SupervisorLifecycle.SEALED, SupervisorLifecycle.DRAINED,
            ):
                raise TaskRefused(name)
            if priority is TaskPriority.CRITICAL and self._lifecycle is SupervisorLifecycle.DRAINED:
                raise TaskRefused(name)
            coro = coro_factory()
            task = asyncio.create_task(coro, name=name)
            self._tasks[handle_id] = (task, priority, name)

        def _on_done(t, handle_id=handle_id, priority=priority, name=name):
            failed = (not t.cancelled()) and (t.exception() is not None)
            with self._lock:
                self._tasks.pop(handle_id, None)
                outcome = OpStatus.FAILED if (failed or t.cancelled()) else OpStatus.SUCCEEDED
                self._outcomes[handle_id] = outcome
                if priority is TaskPriority.CRITICAL and outcome is OpStatus.FAILED:
                    self._any_critical_failure_ever = True
            if failed:
                level = logging.ERROR if priority is TaskPriority.CRITICAL else logging.WARNING
                logger.log(level, "task %r (%s) failed: %s", name, handle_id, t.exception())

        task.add_done_callback(_on_done)
        return task

    async def drain_critical(self, timeout: float = 5.0) -> "DrainResult":
        deadline = time.monotonic() + timeout
        seen: set = set()
        while True:
            with self._lock:
                current_critical = {h for h, (_, p, _) in self._tasks.items() if p is TaskPriority.CRITICAL}
                seen |= current_critical
                pending_tasks = [self._tasks[h][0] for h in current_critical]
                if not pending_tasks:
                    self._lifecycle = SupervisorLifecycle.DRAINED
                    break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._lock:
                    self._lifecycle = SupervisorLifecycle.DRAINED
                break
            await asyncio.wait(pending_tasks, timeout=remaining)
        with self._lock:
            succeeded, failed, still_pending = [], [], []
            for hid in seen:
                if hid in self._tasks:
                    still_pending.append(hid)
                else:
                    (succeeded if self._outcomes.get(hid) is OpStatus.SUCCEEDED else failed).append(hid)
            ever_failed = self._any_critical_failure_ever
        return DrainResult(tuple(succeeded), tuple(failed), tuple(still_pending), ever_failed)
```

---

## 5. `ParkingBrake` — complete transition semantics

### 5.1 State

```python
@dataclass(frozen=True)
class BrakeState:
    engaged: bool
    scopes: frozenset
    revision: int              # bumped whenever (engaged, scopes) changes; NEVER bumped by
                                  # a persisted_version-only reconciliation
    persisted_version: int     # the freshest parking_brake_state.version this process has
                                  # observed from ANY source (its own writes, or an
                                  # external observation) — monotonic, never regresses


class GovernanceWriteLifecycle(Enum):
    OPEN = "open"       # normal engage/disengage/narrow writes accepted
    FROZEN = "frozen"    # normal writes refused; only the privileged clean-marker path may proceed
    CLOSED = "closed"    # the governance lane itself has been closed; nothing further, ever


class WriteOutcome(Enum):
    APPLIED = "applied"
    SUPERSEDED = "superseded"   # CAS fenced off by a transition that committed since
                                  # expected_version was captured
    FENCED = "fenced"           # refused by the durable, database-visible write fence (§6),
                                  # checked inside the same transaction that would otherwise commit


class EngageResult:
    def __init__(self, live_state: "BrakeState", persistence: str):
        self.live_state = live_state
        self.persistence = persistence   # "pending" | "confirmed_sync" | "refused_shutting_down"


class LoosenOutcome(Enum):
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    PENDING = "pending"
    REFUSED_SHUTTING_DOWN = "refused_shutting_down"
    REJECTED_BUSY = "rejected_busy"


class ParkingBrake:
    def __init__(self, storage, executor: "DedicatedDbExecutor | None" = None,
                 task_supervisor: "PersistenceTaskSupervisor | None" = None):
        self._storage = storage
        self._executor = executor
        self._supervisor = task_supervisor
        self._state_lock = threading.Lock()
        self._state = BrakeState(engaged=True, scopes=frozenset({"global"}), revision=0,
                                  persisted_version=0)
        self._write_lifecycle_lock = threading.Lock()
        self._write_lifecycle = GovernanceWriteLifecycle.OPEN
        self._outstanding_writes: set[str] = set()   # op_ids admitted and not yet fully finished
        self._write_refused_during_freeze = False
        self._pending_disengage_op_id: str | None = None
        self._governance_failure_lock = threading.Lock()
        self._any_governance_persistence_failure_ever = False   # never reset; covers EVERY
                                                                    # transition kind, engage
                                                                    # or loosen
        self._runtime_id: str | None = None
```

There is no reservation slot, no "one outstanding engage task" bookkeeping, and no intent-generation
counter anywhere in this design: **every `engage()` call launches its own, fully independent persistence
task** (§5.3). Sharing one task across multiple calls was tried and rejected — see §5.3's rationale.

### 5.2 Write admission and the failure latch

```python
def _admit_write(self, op_id: str) -> bool:
    with self._write_lifecycle_lock:
        if self._write_lifecycle is not GovernanceWriteLifecycle.OPEN:
            self._write_refused_during_freeze = True
            return False
        self._outstanding_writes.add(op_id)
        return True

def _release_write(self, op_id: str) -> None:
    with self._write_lifecycle_lock:
        self._outstanding_writes.discard(op_id)

def seal_write_lifecycle_closed(self) -> None:
    """Called by KernelDaemon.stop() (§9) after freeze_and_drain_writes()
    and before governance_executor.close() — names the terminal
    write-admission state explicitly."""
    with self._write_lifecycle_lock:
        self._write_lifecycle = GovernanceWriteLifecycle.CLOSED

def _latch_persistence_failure(self, context: str, exc: BaseException | None) -> None:
    with self._governance_failure_lock:
        self._any_governance_persistence_failure_ever = True
    logger.error(
        "ParkingBrake: PERMANENT governance persistence-failure latch set (%s): %r — no "
        "future clean shutdown can be reported for the remaining life of this process, "
        "regardless of whether later writes succeed.", context, exc,
    )

def _governance_persistence_failed_ever(self) -> bool:
    with self._governance_failure_lock:
        return self._any_governance_persistence_failure_ever

def _reconcile_persisted_version(self, version: int) -> None:
    """The only way persisted_version is ever updated outside
    apply_external_refresh's own fold. Does NOT bump revision — that
    field tracks (engaged, scopes) intent only. Monotonic: never
    regresses, since operations can complete out of local-callback
    order even though DB commits are strictly ordered."""
    with self._state_lock:
        if version > self._state.persisted_version:
            self._state = dataclasses.replace(self._state, persisted_version=version)
```

### 5.3 `engage()` — one independent persistence task per command

**Why one task per command, not a shared coalescing task:** a design that shares one background
persistence task across multiple `engage()` calls must decide, when that task's write is superseded by a
confirmed loosening, whether to retry. It must stop — retrying would risk re-adding a scope a
`narrow_scopes()` or `disengage()` just removed. But if a *different*, later `engage()` call was relying
on that same shared task to eventually persist *its own* contribution, stopping strands that
contribution silently: `self._state` still reflects it in memory, but nothing will ever write it to the
database until an unrelated future `engage()` call happens to start a new task. The two requirements —
never restore what a loosening removed, and never drop what a later engage still wants — cannot both be
satisfied by one shared task giving up on behalf of every caller counting on it. They are satisfied by
never sharing: every `engage()` call gets its own task, so one task's decision to stop can never strand
a different call's intent.

**Post-`CLOSED` behavior — the single authoritative rule, stated once here and nowhere contradicted
elsewhere in this document:** once the governance write lifecycle is `CLOSED`, `engage()` refuses before
mutating either live or persisted state at all. This is not merely `engage()`'s own contract in
isolation — every other section of this document that describes what happens to a governance call after
shutdown (§9's proof, in particular) states this exact same outcome and no other. While merely `FROZEN`
(clean shutdown not yet attempted), a late `engage()` call **may** still tighten locally — doing so is
guaranteed to invalidate the clean determination via `_write_refused_during_freeze`, which `finalize_
clean_shutdown()` has not yet consulted at that point.

```python
def engage(self, *scopes: str) -> "EngageResult":
    """
    Monotonic tightening only: flips in-memory state immediately,
    synchronously, before persistence is attempted — boolean OR + scope
    union against whatever the CURRENT local state is. Once the write
    lifecycle is CLOSED, this method refuses BEFORE mutating self._state
    at all: a post-CLOSED local tightening with no persisted
    counterpart would leave an already-committed clean marker
    dishonest, silently lost at the next restart. engage() has no
    await anywhere in its body, so this check-then-mutate sequence
    cannot be interleaved by anything else on the single event loop
    thread.
    """
    incoming = frozenset(scopes) if scopes else frozenset({"global"})

    with self._write_lifecycle_lock:
        if self._write_lifecycle is GovernanceWriteLifecycle.CLOSED:
            logger.error(
                "ParkingBrake.engage(): refused — governance write lifecycle is CLOSED. "
                "NOT mutating in-memory state.",
            )
            return EngageResult(live_state=self._state, persistence="refused_shutting_down")

    with self._state_lock:
        current = self._state
        new_scopes = (current.scopes | incoming) if current.engaged else incoming
        live_state = self._bump(True, new_scopes)

    if self._executor is None:
        audit_id = uuid.uuid4().hex
        _write_flag_direct(self._storage, engaged=True, scopes=live_state.scopes, audit_event_id=audit_id)
        return EngageResult(live_state=live_state, persistence="confirmed_sync")

    op_id = uuid.uuid4().hex
    if not self._admit_write(op_id):
        logger.error("ParkingBrake.engage(): persistence refused — governance writes are frozen.")
        return EngageResult(live_state=live_state, persistence="refused_shutting_down")

    try:
        self._supervisor.launch(
            "parking_brake_engage",
            lambda: self._persist_engage_task(op_id),
            priority=TaskPriority.CRITICAL,
        )
    except TaskRefused:
        self._release_write(op_id)
        logger.error("ParkingBrake.engage(): supervisor refused the launch (sealed/drained)")
        return EngageResult(live_state=live_state, persistence="refused_shutting_down")

    return EngageResult(live_state=live_state, persistence="pending")


def _bump(self, engaged: bool, scopes: frozenset) -> "BrakeState":
    """The ONLY place self._state is reassigned for an (engaged, scopes)
    change. Always called with self._state_lock held. Always constructs
    a brand-new, immutable BrakeState and reassigns wholesale — never
    mutates the existing object in place — so any unlocked reader (a
    bare attribute read of self._state from another thread) is safe:
    CPython's GIL makes that read atomic, and it either observes the
    old object or the new one, never a torn one."""
    new_state = BrakeState(engaged=engaged, scopes=scopes, revision=self._state.revision + 1,
                            persisted_version=self._state.persisted_version)
    self._state = new_state
    return new_state


async def _persist_engage_task(self, op_id: str) -> None:
    """
    One independent task per engage() call. Loops only to ensure its
    OWN view of self._state eventually gets persisted, or is
    legitimately superseded by a loosening — in which case it stops
    unconditionally, never sharing fate with a sibling task launched by
    a DIFFERENT engage() call.
    """
    try:
        while True:
            with self._state_lock:
                if not self._state.engaged:
                    return
                scopes_to_persist = self._state.scopes
                revision_at_read = self._state.revision
                fence_version = self._state.persisted_version
            audit_id = uuid.uuid4().hex
            try:
                outcome, cur_version, cur_scopes, cur_engaged, cur_last_loosen = await self._executor.run(
                    _engage_op, scopes_to_persist, fence_version, audit_id, "daemon", timeout=5.0,
                )
            except DedicatedDbExecutorTimeout as exc:
                if exc.tracked.status is OpStatus.QUEUE_TIMEOUT_NOT_SUBMITTED:
                    # Multiple independent engage tasks can legitimately
                    # contend for the governance lane's bounded queue
                    # depth — expected, transient contention, not a
                    # failure. Back off briefly and retry from a fresh
                    # self._state read.
                    await asyncio.sleep(0.05)
                    continue
                self._latch_persistence_failure("engage", exc)
                raise
            except BaseException as exc:
                self._latch_persistence_failure("engage", exc)
                raise

            if outcome is WriteOutcome.FENCED:
                logger.warning("ParkingBrake: engage op=%s fenced — governance writes are closing.", op_id)
                return

            # Always fold the authoritative response through the SAME
            # tightening-only merge BrakeWatcher uses (§5.6) — safe
            # regardless of outcome: widens or no-ops local bookkeeping,
            # never used to decide whether to RESTORE removed scopes.
            self.apply_external_refresh(cur_engaged, cur_scopes, cur_version)

            if outcome is WriteOutcome.SUPERSEDED:
                if cur_last_loosen > fence_version:
                    # A LOOSENING committed at or after our fence point —
                    # unmaskable, since last_loosen_version only ever
                    # advances on a loosening commit, regardless of what
                    # (if anything) committed afterward (§5.4). Stop
                    # unconditionally: if the operator still wants this
                    # engaged, a NEW engage() call starts its own,
                    # independent task with a fully fresh read.
                    return
                # No loosening since our fence point — the only thing
                # that could have advanced the version is ANOTHER
                # engage (in-process or external). Nothing was removed;
                # always safe to retry unconditionally.
                continue

            with self._state_lock:
                if self._state.revision == revision_at_read:
                    return
    finally:
        self._release_write(op_id)
```

### 5.4 `_engage_op` / `_loosen_op` — the shared, database-fenced write primitive

```python
def _read_write_fence_open(conn) -> bool:
    row = conn.execute("SELECT write_fence_open FROM brake_runtime WHERE id=1").fetchone()
    if row is None:
        # Should be UNREACHABLE post-schema-init (§3's singleton-row
        # guarantee). If somehow still missing, FAIL CLOSED.
        return False
    return bool(row[0])


def _engage_op(conn, incoming: frozenset, expected_version: int, audit_event_id: str,
                audit_source: str, *, offline_permit: "OfflineWritePermit | None" = None) -> tuple:
    """
    Returns (WriteOutcome, current_version, current_scopes,
    current_engaged, last_loosen_version).

    offline_permit: None for every daemon-side call and the CLI's
    default, online-aware path — the durable write_fence_open flag (§6)
    is checked as usual. A valid, live OfflineWritePermit (§6) — never a
    boolean — bypasses that check for the CLI's explicit --offline path,
    which has independently proven no daemon process exists before ever
    reaching this function.

    last_loosen_version is read directly off parking_brake_state in the
    same SELECT that reads engaged/scopes/version — never written by
    this function; only _loosen_op ever advances it. Checking `cur_
    last_loosen > expected_version` (done by the caller, §5.3) answers
    "has any loosening committed since my fence point" unmaskably,
    regardless of what committed afterward — unlike inspecting only the
    single most recent audit-table row, which a later, unrelated engage
    can make look like nothing was ever loosened.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        fence_bypassed = offline_permit is not None and offline_permit.is_valid()
        if not fence_bypassed and not _read_write_fence_open(conn):
            conn.rollback()
            return (WriteOutcome.FENCED, 0, frozenset(), False, 0)

        row = conn.execute(
            "SELECT engaged, scopes_json, version, last_loosen_version "
            "FROM parking_brake_state WHERE id=1",
        ).fetchone()
        if row is None:
            if expected_version != 0:
                conn.rollback()
                return (WriteOutcome.SUPERSEDED, 0, frozenset(), False, 0)
            new_scopes = incoming
            conn.execute(
                "INSERT INTO parking_brake_state "
                "(id, engaged, scopes_json, version, last_loosen_version, updated_at) "
                "VALUES (1, 1, ?, 1, 0, ?)",
                (json.dumps(sorted(new_scopes)), _now_iso()),
            )
            _insert_audit_row(conn, audit_event_id, "engage", True, new_scopes, 1, audit_source)
            conn.commit()
            return (WriteOutcome.APPLIED, 1, new_scopes, True, 0)

        cur_engaged, cur_scopes, cur_version, cur_last_loosen = (
            bool(row[0]), frozenset(json.loads(row[1])), row[2], row[3],
        )
        if cur_version != expected_version:
            conn.rollback()
            return (WriteOutcome.SUPERSEDED, cur_version, cur_scopes, cur_engaged, cur_last_loosen)

        new_scopes = (cur_scopes | incoming) if cur_engaged else incoming
        new_version = cur_version + 1
        cur = conn.execute(
            "UPDATE parking_brake_state SET engaged=1, scopes_json=?, version=?, updated_at=? "
            "WHERE id=1 AND version=?",
            (json.dumps(sorted(new_scopes)), new_version, _now_iso(), expected_version),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return (WriteOutcome.SUPERSEDED, cur_version, cur_scopes, cur_engaged, cur_last_loosen)
        _insert_audit_row(conn, audit_event_id, "engage", True, new_scopes, new_version, audit_source)
        conn.commit()
        # last_loosen_version is UNCHANGED by an engage commit — this
        # UPDATE never touches it — so a subsequent read still correctly
        # reflects the most recent LOOSENING, not this engage.
        return (WriteOutcome.APPLIED, new_version, new_scopes, True, cur_last_loosen)
    except BaseException:
        conn.rollback()
        raise


def _loosen_op(conn, expected_version: int, new_engaged: bool, new_scopes: frozenset,
                audit_event_id: str, audit_source: str, *,
                offline_permit: "OfflineWritePermit | None" = None) -> tuple:
    """
    Returns (WriteOutcome, current_version, current_scopes,
    current_engaged) — a four-tuple; loosening never retries on
    SUPERSEDED (its own caller, _attempt_loosening, simply reports it
    upward), so it has no need for last_loosen_version in its own
    return. A successful APPLIED write ALSO sets last_loosen_version =
    new_version, in the same UPDATE/INSERT statement, atomically with
    the version bump the whole engage-side mechanism depends on.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        fence_bypassed = offline_permit is not None and offline_permit.is_valid()
        if not fence_bypassed and not _read_write_fence_open(conn):
            conn.rollback()
            return (WriteOutcome.FENCED, 0, frozenset(), False)

        row = conn.execute("SELECT engaged, scopes_json, version FROM parking_brake_state WHERE id=1").fetchone()
        if row is None:
            if expected_version != 0:
                conn.rollback()
                return (WriteOutcome.SUPERSEDED, 0, frozenset(), False)
            conn.execute(
                "INSERT INTO parking_brake_state "
                "(id, engaged, scopes_json, version, last_loosen_version, updated_at) "
                "VALUES (1, ?, ?, 1, 1, ?)",
                (1 if new_engaged else 0, json.dumps(sorted(new_scopes)), _now_iso()),
            )
            _insert_audit_row(conn, audit_event_id, "narrow_or_disengage", new_engaged, new_scopes, 1, audit_source)
            conn.commit()
            return (WriteOutcome.APPLIED, 1, new_scopes, new_engaged)

        cur_engaged, cur_scopes, cur_version = bool(row[0]), frozenset(json.loads(row[1])), row[2]
        if cur_version != expected_version:
            conn.rollback()
            return (WriteOutcome.SUPERSEDED, cur_version, cur_scopes, cur_engaged)
        new_version = cur_version + 1
        cur = conn.execute(
            "UPDATE parking_brake_state SET engaged=?, scopes_json=?, version=?, "
            "last_loosen_version=?, updated_at=? WHERE id=1 AND version=?",
            (1 if new_engaged else 0, json.dumps(sorted(new_scopes)), new_version,
             new_version, _now_iso(), expected_version),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return (WriteOutcome.SUPERSEDED, cur_version, cur_scopes, cur_engaged)
        _insert_audit_row(conn, audit_event_id, "narrow_or_disengage", new_engaged, new_scopes, new_version, audit_source)
        conn.commit()
        return (WriteOutcome.APPLIED, new_version, new_scopes, new_engaged)
    except BaseException:
        conn.rollback()
        raise


def _insert_audit_row(conn, event_id: str, action: str, engaged: bool, scopes: frozenset,
                       version: int, source: str) -> None:
    conn.execute(
        "INSERT INTO governance_audit (event_id, action, engaged, scopes_json, version, source, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, action, 1 if engaged else 0, json.dumps(sorted(scopes)), version, source, _now_iso()),
    )
```

### 5.5 `disengage()` / `narrow_scopes()` — confirmation-gated loosening, version-guarded reconciliation

**The correction applied this pass (finding 4):** `_reconcile_persisted_version()` can advance `self.
_state.persisted_version` from an independent source (a concurrent `BrakeWatcher` observation, or another
loosening/engage entirely) without touching `revision`, since version-only reconciliation is defined to
leave `revision` untouched (§5.1). A delayed loosening's own revision check can therefore still pass —
nothing about `(engaged, scopes)` intent changed — even though `persisted_version` has already moved past
the value that delayed loosening is about to apply. `_bump_with_version()` must never blindly assign a
version lower than what is already known: it now re-checks `observed_version` against the current
`persisted_version`, inside the *same* `_state_lock` acquisition used to apply the result, and refuses to
apply — leaving state exactly as it was — if the observation is stale.

```python
async def _attempt_loosening(self, new_engaged: bool, new_scopes: frozenset) -> "LoosenOutcome":
    op_id = uuid.uuid4().hex
    if not self._admit_write(op_id):
        logger.error(
            "ParkingBrake: loosening attempt (engaged=%s, scopes=%s) refused — governance "
            "writes are frozen. No in-memory change is made for a loosening before "
            "persistence confirms it, so nothing to undo — but this refusal invalidates a "
            "clean-shutdown determination.", new_engaged, sorted(new_scopes),
        )
        return LoosenOutcome.REFUSED_SHUTTING_DOWN

    with self._state_lock:
        base_revision = self._state.revision
        expected_version = self._state.persisted_version
        self._pending_disengage_op_id = op_id

    audit_id = uuid.uuid4().hex
    tracked = await self._executor.run_tracked(
        _loosen_op, expected_version, new_engaged, new_scopes, audit_id, "daemon", timeout=5.0,
    )

    def _clear_pending_if_mine() -> None:
        with self._state_lock:
            if self._pending_disengage_op_id == op_id:
                self._pending_disengage_op_id = None

    def _apply_if_still_current(observed_version: int) -> bool:
        """
        Applies a loosening result iff (a) it is still the pending
        operation this process is tracking, (b) no newer local intent
        has arrived since this attempt's own base_revision, AND (c) —
        the correction this pass adds — observed_version is not older
        than persisted_version already known, all checked and applied
        inside ONE _state_lock acquisition. (c) is the fix for finding
        4: without it, a delayed loosening whose revision check alone
        passes could regress persisted_version and reapply a stale
        scope set that a MORE RECENT observation (via a different path
        entirely — apply_external_refresh, or a different loosening
        call) had already superseded.
        """
        with self._state_lock:
            if self._pending_disengage_op_id != op_id:
                return False
            if self._state.revision != base_revision:
                self._pending_disengage_op_id = None
                return False
            if observed_version < self._state.persisted_version:
                # Stale relative to what is ALREADY known, even though
                # revision itself didn't change (a version-only
                # reconciliation never bumps it, §5.1/§5.6) — refuse to
                # apply, and refuse to regress persisted_version.
                self._pending_disengage_op_id = None
                return False
            self._state = BrakeState(engaged=new_engaged, scopes=new_scopes,
                                      revision=self._state.revision + 1,
                                      persisted_version=observed_version)
            return True

    if tracked.status is OpStatus.SUCCEEDED:
        write_outcome, observed_version, _observed_scopes, _observed_engaged = tracked.result
        self._reconcile_persisted_version(observed_version)
        if write_outcome is WriteOutcome.FENCED:
            _clear_pending_if_mine(); self._release_write(op_id)
            return LoosenOutcome.REFUSED_SHUTTING_DOWN
        if write_outcome is WriteOutcome.SUPERSEDED:
            _clear_pending_if_mine(); self._release_write(op_id)
            return LoosenOutcome.SUPERSEDED
        applied = _apply_if_still_current(observed_version)
        self._release_write(op_id)
        return LoosenOutcome.CONFIRMED if applied else LoosenOutcome.SUPERSEDED

    if tracked.status is OpStatus.FAILED:
        _clear_pending_if_mine(); self._release_write(op_id)
        self._latch_persistence_failure(f"disengage/narrow op={op_id}", tracked.exception)
        logger.error("ParkingBrake: loosening op=%s FAILED (%s)", op_id, tracked.exception)
        return LoosenOutcome.FAILED

    if tracked.status in (OpStatus.CLOSED_NOT_SUBMITTED, OpStatus.QUEUE_TIMEOUT_NOT_SUBMITTED):
        _clear_pending_if_mine(); self._release_write(op_id)
        if tracked.status is OpStatus.CLOSED_NOT_SUBMITTED:
            with self._write_lifecycle_lock:
                lifecycle_now = self._write_lifecycle
            if lifecycle_now is GovernanceWriteLifecycle.OPEN:
                self._latch_persistence_failure(
                    f"op={op_id}: CLOSED_NOT_SUBMITTED while write lifecycle is still OPEN", None,
                )
                return LoosenOutcome.FAILED
            return LoosenOutcome.REFUSED_SHUTTING_DOWN
        return LoosenOutcome.REJECTED_BUSY

    tracked.on_eventual_completion(
        lambda t: self._reconcile_pending_loosening(op_id, base_revision, expected_version,
                                                     new_engaged, new_scopes, t),
    )
    logger.warning("ParkingBrake: loosening op=%s PENDING; not yet applied", op_id)
    return LoosenOutcome.PENDING


def _reconcile_pending_loosening(self, op_id, base_revision, expected_version,
                                  new_engaged, new_scopes, tracked) -> None:
    """The delayed-completion path — uses the SAME (b)+(c) guarded apply
    as the immediate path above, never a separately-implemented,
    possibly-inconsistent check."""
    try:
        if tracked.status is OpStatus.FAILED:
            self._latch_persistence_failure(f"delayed disengage/narrow op={op_id}", tracked.exception)
            with self._state_lock:
                if self._pending_disengage_op_id == op_id:
                    self._pending_disengage_op_id = None
            logger.error("ParkingBrake: delayed loosening op=%s FAILED (%s)", op_id, tracked.exception)
            return
        if tracked.status is not OpStatus.SUCCEEDED:
            with self._state_lock:
                if self._pending_disengage_op_id == op_id:
                    self._pending_disengage_op_id = None
            return
        write_outcome, observed_version, _scopes, _engaged = tracked.result
        self._reconcile_persisted_version(observed_version)
        if write_outcome in (WriteOutcome.SUPERSEDED, WriteOutcome.FENCED):
            with self._state_lock:
                if self._pending_disengage_op_id == op_id:
                    self._pending_disengage_op_id = None
            return
        applied = False
        with self._state_lock:
            if self._pending_disengage_op_id != op_id or self._state.revision != base_revision:
                self._pending_disengage_op_id = None
            elif observed_version < self._state.persisted_version:
                # Finding 4's fix, applied identically here: reject a
                # stale observed_version even though revision still
                # matches — never regress persisted_version, never
                # reapply stale scopes.
                self._pending_disengage_op_id = None
            else:
                self._state = BrakeState(engaged=new_engaged, scopes=new_scopes,
                                          revision=self._state.revision + 1,
                                          persisted_version=observed_version)
                applied = True
        if applied:
            logger.warning("ParkingBrake: delayed loosening op=%s now confirmed and applied", op_id)
    finally:
        self._release_write(op_id)


async def disengage(self) -> "LoosenOutcome":
    return await self._attempt_loosening(False, frozenset())

async def narrow_scopes(self, *scopes: str) -> "LoosenOutcome":
    return await self._attempt_loosening(True, frozenset(scopes))
```

Note that the prior, separately-named `_bump_with_version()` helper is removed: both the immediate and
delayed reconciliation paths above now construct the corrected `BrakeState` inline, under the same lock
acquisition as the `observed_version < persisted_version` check, rather than delegating to a helper that
could be called without that check ever being performed — the fix is structural (there is no longer an
unguarded path to construct a version-regressing state), not merely an added `if`.

### 5.6 `apply_external_refresh()` — version-aware, tightening-only external merge

```python
def apply_external_refresh(self, engaged: bool, scopes: frozenset, version: int) -> None:
    """
    Called by BrakeWatcher (§11) on every poll cycle where the observed
    version differs from the last one seen, and by the engage
    persistence task (§5.3) on every response it receives. Synchronous
    — no I/O, no await.

    If `version` is strictly older than current.persisted_version, this
    observation is stale/out-of-order — e.g. a delayed engage-
    persistence result arriving after a newer, already-reconciled
    disengage. It must not modify EITHER live state OR persisted_
    version bookkeeping: an older observation carries no information
    more current than what is already known, and applying its
    tightening would silently resurrect a state a newer transition
    already superseded.

    Otherwise, two independent effects, applied together under ONE lock
    acquisition:

      1. TIGHTENING-ONLY MERGE: new_engaged = current.engaged OR
         engaged; new_scopes = union if new_engaged else empty. Can
         only ever make local state MORE restrictive or leave it
         unchanged — safe regardless of arrival order or how stale
         `version` might otherwise be, which is why this path needs
         none of §5.4's CAS/fencing machinery.
      2. MONOTONIC persisted_version reconciliation: advances to
         max(current, observed), independent of whether the tightening
         merge itself changes anything.

    Deliberately does NOT bump `revision` on a version-only
    reconciliation — revision tracks (engaged, scopes) intent only.
    This is precisely the property §5.5's correction accounts for: a
    version-only advance through THIS function can leave revision
    unchanged while persisted_version moves ahead of what a THEN-
    pending loosening last observed.
    """
    with self._state_lock:
        current = self._state
        if version < current.persisted_version:
            return   # stale observation — unconditional no-op, checked first.

        new_engaged = current.engaged or engaged
        new_scopes = (current.scopes | scopes) if new_engaged else frozenset()
        new_persisted_version = max(current.persisted_version, version)

        state_changed = (new_engaged != current.engaged) or (new_scopes != current.scopes)
        version_changed = new_persisted_version != current.persisted_version
        if not state_changed and not version_changed:
            return

        if state_changed:
            self._state = BrakeState(engaged=new_engaged, scopes=new_scopes,
                                      revision=current.revision + 1,
                                      persisted_version=new_persisted_version)
        else:
            self._state = dataclasses.replace(current, persisted_version=new_persisted_version)


def force_local_engage_degraded(self) -> None:
    """Called by BrakeWatcher (§11) after fail_closed_after consecutive
    failed observation cycles. Forces local state to globally engaged —
    does NOT persist anything (storage is presumed unreachable) and
    does NOT auto-clear; an operator must confirm the issue and
    explicitly disengage/narrow once resolved."""
    with self._state_lock:
        if not self._state.engaged or self._state.scopes != frozenset({"global"}):
            self._state = BrakeState(engaged=True, scopes=frozenset({"global"}),
                                      revision=self._state.revision + 1,
                                      persisted_version=self._state.persisted_version)
```

### 5.7 Freeze, drain, and the final clean-marker transaction

```python
async def freeze_and_drain_writes(self, timeout: float = 5.0) -> bool:
    """
    Called by KernelDaemon.stop() (§9) before the governance executor is
    closed. Freezes admission of every NEW normal governance write,
    closes the durable write fence (§6) — a real database write, done
    via governance_executor while it is still open — then drains every
    already-admitted write (the supervisor's own failure-latched
    drain_critical() for engage tasks, and self._outstanding_writes for
    loosening calls) up to `timeout`. Returns True iff everything
    drained cleanly AND no write was ever refused during this window AND
    no governance persistence write has EVER failed during this
    runtime's life (engage or loosen).
    """
    with self._write_lifecycle_lock:
        self._write_lifecycle = GovernanceWriteLifecycle.FROZEN

    fence_tracked = await self._executor.run_tracked(
        lambda conn: _set_write_fence_op(conn, open_=False), timeout=2.0,
    )
    if fence_tracked.status is not OpStatus.SUCCEEDED:
        logger.error(
            "ParkingBrake.freeze_and_drain_writes(): could not close the durable write fence "
            "(%s) — an external CLI write could still succeed during this shutdown window. "
            "Treating this as a drain failure.", fence_tracked.status,
        )
        return False

    drain_result = await self._supervisor.drain_critical(timeout=timeout)
    deadline = time.monotonic() + timeout
    while True:
        with self._write_lifecycle_lock:
            if not self._outstanding_writes:
                break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.error(
                "ParkingBrake.freeze_and_drain_writes(): %d directly-tracked write(s) did not "
                "finish within %.1fs.", len(self._outstanding_writes), timeout,
            )
            return False
        await asyncio.sleep(min(0.05, remaining))

    with self._write_lifecycle_lock:
        refused_any = self._write_refused_during_freeze
    persistence_failed_ever = self._governance_persistence_failed_ever()
    if refused_any:
        logger.error("ParkingBrake.freeze_and_drain_writes(): at least one write was refused during freeze.")
    if persistence_failed_ever:
        logger.error(
            "ParkingBrake.freeze_and_drain_writes(): a governance persistence write failed at "
            "some point during this runtime's life — clean shutdown is impossible regardless "
            "of this specific drain's own outcome.",
        )
    return drain_result.clean and not refused_any and not persistence_failed_ever


async def finalize_clean_shutdown(self) -> bool:
    """
    Called by KernelDaemon.stop() (§9) ONLY after every Python-verified
    precondition in I8 holds, including internal_tasks_terminal and a
    genuinely worker-thread-verified governance_lane_closed_cleanly
    (§4.3) — corrected this pass; see §9 for the full gate. Performs the
    clean-marker write as a raw, standalone database transaction — NOT
    through governance_executor, which no longer accepts submissions by
    the time this runs. This is the single, final, dedicated database
    transaction of the shutdown sequence; nothing governance-related
    runs after it, by construction — there is no lane left to run
    anything through.
    """
    with self._write_lifecycle_lock:
        if self._write_refused_during_freeze:
            logger.error(
                "ParkingBrake.finalize_clean_shutdown(): a governance write was refused "
                "during the freeze window — refusing to write clean=1.",
            )
            return False
    return await asyncio.to_thread(
        _establish_clean_shutdown_direct, self._storage.db_path, self._runtime_id,
    )


def _set_write_fence_op(conn, open_: bool) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "UPDATE brake_runtime SET write_fence_open=? WHERE id=1", (1 if open_ else 0,),
        )
        if cur.rowcount != 1:
            conn.rollback()
            raise WriteFenceUpdateFailed(
                f"UPDATE brake_runtime SET write_fence_open affected {cur.rowcount} row(s), "
                "expected exactly 1 — brake_runtime's singleton row is missing or duplicated.",
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


class WriteFenceUpdateFailed(RuntimeError):
    """Raised when the fence UPDATE does not affect exactly one row —
    structurally impossible after schema init (§3); never silently
    treated as success if it somehow occurs."""


def _establish_clean_shutdown_direct(db_path: str, runtime_id: str) -> bool:
    """
    Standalone connection — deliberately independent of
    governance_executor, which is guaranteed closed by the time this is
    ever called. The WHERE clause requires write_fence_open=0
    TRANSACTIONALLY, alongside the matching runtime_id — this is the
    ONE part of I8 the SQL itself enforces; every other precondition
    (admission, internal tasks, governance drain, worker termination)
    is verified in Python before this function is ever invoked (§9).
    """
    conn = db_ctx.connect(db_path)
    try:
        db_ctx.set_wal_pragmas(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "UPDATE brake_runtime SET clean=1 "
                "WHERE id=1 AND runtime_id=? AND write_fence_open=0",
                (runtime_id,),
            )
            conn.commit()
            return cur.rowcount == 1
        except BaseException:
            conn.rollback()
            raise
    finally:
        db_ctx.close_quietly(conn)
```

### 5.8 Schema application, persisted-value loading

```python
async def ensure_schema_applied(self) -> None:
    """
    Called by KernelDaemon.start() step 2. Mandatory — raises, aborting
    startup, on failure — unlike migrate_legacy_parking_brake_value()
    (§3), which degrades gracefully. Schema CREATION must succeed for
    anything else in this specification to be meaningful; the value
    migration's own failure is separately recoverable via establish_
    runtime()'s conservative default.
    """
    def _op(conn):
        conn.execute("BEGIN IMMEDIATE")
        try:
            ensure_governance_schema(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    tracked = await self._executor.run_tracked(_op, timeout=5.0)
    if tracked.status is not OpStatus.SUCCEEDED:
        raise RuntimeError(f"ensure_schema_applied(): could not apply governance schema ({tracked.status})")


async def _load_persisted_value_normally(self) -> None:
    """
    Called by establish_runtime() (below) only when the prior runtime's
    shutdown was recorded clean (prev_clean=True) — trusts parking_
    brake_state as authoritative (I3) and loads it into self._state,
    including its version as the initial persisted_version.
    """
    def _op(conn):
        return _read_state_op(conn)   # §11 — returns (engaged, scopes, version)
    tracked = await self._executor.run_tracked(_op, timeout=5.0)
    if tracked.status is not OpStatus.SUCCEEDED:
        logger.error(
            "_load_persisted_value_normally(): could not read parking_brake_state (%s) — "
            "falling back to the conservative globally-engaged default.", tracked.status,
        )
        with self._state_lock:
            self._bump(True, frozenset({"global"}))
        return
    engaged, scopes, version = tracked.result
    with self._state_lock:
        self._state = BrakeState(engaged=engaged, scopes=scopes, revision=self._state.revision + 1,
                                  persisted_version=version)


def _write_flag_direct(storage, *, engaged: bool, scopes: frozenset, audit_event_id: str) -> None:
    """
    Used ONLY when ParkingBrake is constructed with executor=None — a
    standalone/test harness with no async executor available, not used
    by the daemon or the CLI (both always provide a real executor, or
    the CLI routes through _engage_op/_loosen_op directly). Writes
    synchronously via storage's own direct connection, delegating to
    _engage_op with a freshly read expected_version for its own atomic
    read-then-write.
    """
    conn = storage.connect()
    try:
        db_ctx.set_wal_pragmas(conn)
        row = conn.execute("SELECT version FROM parking_brake_state WHERE id=1").fetchone()
        expected_version = row[0] if row else 0
        _engage_op(conn, scopes, expected_version, audit_event_id, "sync", offline_permit=None)
    finally:
        db_ctx.close_quietly(conn)
```

### 5.9 `establish_runtime()`

```python
async def establish_runtime(self) -> bool:
    self._runtime_id = uuid.uuid4().hex
    started_at = _now_iso()

    def _op(conn):
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT runtime_id, clean FROM brake_runtime WHERE id=1").fetchone()
            prev_runtime_id, prev_clean = (row[0], bool(row[1])) if row else (None, False)
            conn.execute(
                "INSERT INTO brake_runtime (id, runtime_id, clean, started_at, write_fence_open) "
                "VALUES (1, ?, 0, ?, 1) ON CONFLICT(id) DO UPDATE SET "
                "runtime_id=excluded.runtime_id, clean=0, started_at=excluded.started_at, "
                "write_fence_open=1",
                (self._runtime_id, started_at),
            )
            conn.commit()
            return (prev_runtime_id, prev_clean)
        except BaseException:
            conn.rollback()
            raise

    tracked = await self._executor.run_tracked(_op, timeout=5.0)
    if tracked.status is not OpStatus.SUCCEEDED:
        logger.critical(
            "could not confirm the runtime transition (%s) — refusing autonomy producers "
            "this run; remaining globally engaged.", tracked.status,
        )
        self._degraded_no_autonomy = True
        return False
    prev_runtime_id, prev_clean = tracked.result
    if not prev_clean:
        with self._state_lock:
            self._bump(True, frozenset({"global"}))
    else:
        await self._load_persisted_value_normally()
    self._degraded_no_autonomy = False
    return True
```

Every new daemon run starts with the durable write fence durably open (`write_fence_open=1`), regardless
of how the previous run ended — this `INSERT ... ON CONFLICT DO UPDATE` is the only code path that ever
reopens it.

---

## 6. Durable write fence and offline CLI protocol

**Why a durable, database-resident fence, not an in-process gate alone:** `GovernedRequestAdmission`
(§7) gates the API/HTTP layer; `_write_lifecycle`/`_admit_write` (§5.2) gate the daemon's own in-process
`engage()`/`disengage()`/`narrow_scopes()` calls. Neither has any effect on the CLI, which opens its own
direct connection and calls `_engage_op`/`_loosen_op` (§5.4) straight through. A CLI invocation running
concurrently with the daemon's shutdown sequence — a human at a terminal, a cron job, an automation
script — could otherwise commit a write after the daemon has frozen, drained, and even finalized
`clean=1`, entirely undetected by any in-process bookkeeping. `brake_runtime.write_fence_open`, checked
transactionally inside the exact functions the daemon and the CLI already share, closes this without
introducing a new process, a new authentication surface, or any network exposure.

**Why the fence alone is not enough for offline CLI use, and why an unforgeable capability, not a
boolean:** `freeze_and_drain_writes()` closes the fence; nothing reopens it except the *next* daemon's
`establish_runtime()`. After a clean daemon stop, the fence stays closed indefinitely — meaning the CLI
remains unable to write at all until a brand-new daemon process starts, even though no daemon is running
and no protection is actually needed. The CLI's own use as an offline administration tool requires a
separate, explicit, *safe* bypass — proved safe by an independent, stronger mechanism (a process-lifetime
lock, §8), not by silently reopening the shared fence (which would affect every other reader's view of
it) and not by a plain boolean argument any ordinary caller could pass to disable protection by mistake.

### 6.1 `OfflineWritePermit` — an unforgeable capability, not a boolean

```python
_PERMIT_CONSTRUCTION_KEY = object()   # module-private; never exported — no external caller
                                        # can obtain this object to construct a permit directly.


class OfflineWritePermit:
    """
    Proof that the caller currently holds DaemonProcessLock's exclusive,
    process-lifetime lock against this database. Can ONLY be
    constructed by _HeldLock (§8) — there is no public constructor an
    ordinary caller could invoke. Becomes permanently invalid the
    instant the lock scope it was issued from exits, whether via normal
    `with`-block exit or an exception. _engage_op()/_loosen_op() (§5.4)
    re-validate is_valid() at the moment of use, not merely at
    permit-creation time, so a permit captured and reused after its
    lock scope has already exited is rejected, not silently trusted.
    """
    def __init__(self, held_lock: "_HeldLock", *, _key):
        if _key is not _PERMIT_CONSTRUCTION_KEY:
            raise RuntimeError(
                "OfflineWritePermit cannot be constructed directly — it is issued only by "
                "_HeldLock.issue_offline_write_permit(), which itself only exists after "
                "successfully proving no daemon process holds the lock.",
            )
        self._held_lock = held_lock

    def is_valid(self) -> bool:
        return not self._held_lock.is_released()
```

`_HeldLock` (§8) gains permit issuance:

```python
class _HeldLock:
    def __init__(self, fh):
        self._fh = fh
        self._released = False

    def issue_offline_write_permit(self) -> "OfflineWritePermit":
        """The ONLY way to obtain an OfflineWritePermit — requires an
        actual, currently-held _HeldLock instance. Each call issues a
        fresh permit tied to THIS lock instance; the permit becomes
        invalid the instant this lock is released."""
        if self._released:
            raise RuntimeError("cannot issue a permit from an already-released lock")
        return OfflineWritePermit(self, _key=_PERMIT_CONSTRUCTION_KEY)

    def is_released(self) -> bool:
        return self._released

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        _platform_unlock(self._fh)
        self._fh.close()
        self._released = True   # every permit issued from this lock now reports is_valid()=False
```

### 6.2 CLI functions — lock acquired before any database operation

**The ordering invariant:** for the `--offline` path, `DaemonProcessLock` acquisition happens **first**
— before schema initialization, before opening any connection, before any read or write. No operation
may touch the database before the CLI has proven no daemon is running.

```python
_CLI_MAX_CAS_RETRIES = 5


class CliOperationFailed(RuntimeError):
    pass


def _cli_acquire_offline_lock_or_raise(db_path: str) -> "_HeldLock":
    """
    The CLI's own precondition for --offline: attempt the exact
    process-lifetime lock a live KernelDaemon holds for its entire run,
    non-blocking. Success proves no daemon process — alive or idle —
    currently exists against this database. This is called BEFORE
    _cli_ensure_governance_schema(), before db_ctx.connect(), before
    anything else in the offline path touches the file at all.
    """
    lock = DaemonProcessLock(db_path)
    held = lock.try_acquire_nonblocking()
    if held is None:
        raise CliOperationFailed(
            "brake engage/off/narrow --offline: refused — a daemon process is currently "
            "running against this database (idle or active). Stop it first, then retry. "
            "No schema check, connection, read, or write was attempted.",
        )
    return held


def _cli_read_current(conn) -> tuple[bool, frozenset, int]:
    row = conn.execute(
        "SELECT engaged, scopes_json, version FROM parking_brake_state WHERE id=1",
    ).fetchone()
    if row is None:
        return (False, frozenset(), 0)
    return (bool(row[0]), frozenset(json.loads(row[1])), row[2])


def _cli_engage(db: str, scope: list[str] | None, *, offline: bool = False) -> None:
    incoming = frozenset(scope) if scope else frozenset({"global"})
    held_lock = _cli_acquire_offline_lock_or_raise(db) if offline else None
    permit = held_lock.issue_offline_write_permit() if held_lock is not None else None
    try:
        _cli_ensure_governance_schema(db)   # only reached AFTER the lock is held, for --offline
        conn = db_ctx.connect(db)
        try:
            db_ctx.set_wal_pragmas(conn)
            for _attempt in range(_CLI_MAX_CAS_RETRIES):
                _, _, expected_version = _cli_read_current(conn)
                audit_id = uuid.uuid4().hex
                outcome, new_version, new_scopes, _new_engaged, _cur_last_loosen = _engage_op(
                    conn, incoming, expected_version, audit_id, "cli", offline_permit=permit,
                )
                if outcome is WriteOutcome.APPLIED:
                    console.print(
                        f"\n[yellow]Persisted state updated: engaged=True, "
                        f"scopes={sorted(new_scopes)}, version={new_version}[/yellow]"
                    )
                    if offline:
                        console.print(
                            "Written directly (--offline); no daemon is running to propagate "
                            "this anywhere — it will take effect at that daemon's next startup.\n"
                        )
                    else:
                        console.print(
                            "This is a TIGHTENING. If a daemon is currently running, "
                            "BrakeWatcher's tightening-only merge MAY propagate this to its "
                            "live state automatically (typically ~3s under normal conditions; "
                            "no fixed worst-case bound under sustained storage contention).\n"
                        )
                    return
                if outcome is WriteOutcome.FENCED:
                    raise CliOperationFailed(
                        "brake engage: refused — governance writes are durably fenced. This "
                        "means EITHER a daemon is currently mid-shutdown, OR the last daemon "
                        "stopped cleanly and left writes closed until ITS OWN next startup "
                        "(write_fence_open is reopened only by a daemon's own establish_"
                        "runtime(), never automatically). Pass --offline (which independently "
                        "verifies no daemon is running before writing) to write directly, or "
                        "start a new daemon.",
                    )
                # SUPERSEDED: retry against freshly re-read state — the CLI's entire
                # concurrency story, since it has no in-memory state to reconcile against.
            raise CliOperationFailed(
                f"brake engage: could not apply after {_CLI_MAX_CAS_RETRIES} attempts — "
                "another writer is persistently racing this one.",
            )
        finally:
            db_ctx.close_quietly(conn)
    finally:
        if held_lock is not None:
            held_lock.__exit__(None, None, None)   # also marks `permit` invalid via is_released()


def _cli_loosen(db: str, new_engaged: bool, scope: list[str] | None, *, offline: bool = False) -> None:
    """Backs `brake off`/`brake disengage` (new_engaged=False, scope=None)
    and `brake narrow` (new_engaged=True, scope=[...])."""
    new_scopes = frozenset(scope) if scope else frozenset()
    held_lock = _cli_acquire_offline_lock_or_raise(db) if offline else None
    permit = held_lock.issue_offline_write_permit() if held_lock is not None else None
    try:
        _cli_ensure_governance_schema(db)
        conn = db_ctx.connect(db)
        try:
            db_ctx.set_wal_pragmas(conn)
            for _attempt in range(_CLI_MAX_CAS_RETRIES):
                _, _, expected_version = _cli_read_current(conn)
                audit_id = uuid.uuid4().hex
                outcome, observed_version, observed_scopes, _observed_engaged = _loosen_op(
                    conn, expected_version, new_engaged, new_scopes, audit_id, "cli",
                    offline_permit=permit,
                )
                if outcome is WriteOutcome.APPLIED:
                    console.print(
                        f"\n[yellow]Persisted state updated: engaged={new_engaged}, "
                        f"scopes={sorted(observed_scopes)}, version={observed_version}[/yellow]"
                    )
                    if offline:
                        console.print(
                            "Written directly (--offline); no daemon is running.\n"
                        )
                    else:
                        console.print(
                            "This is a LOOSENING. It updates ONLY the persisted, next-start "
                            "governance state. BrakeWatcher's merge is tightening-only BY "
                            "DESIGN and can NEVER loosen a running daemon's live state from an "
                            "external observation. If a daemon is currently running, this "
                            "change will NOT take live effect until: (a) the daemon is "
                            "restarted, reading this value at startup, or (b) a future "
                            "authenticated live-control operation calls disengage()/narrow_"
                            "scopes() in-process. Until then, the running daemon's live "
                            "enforcement remains exactly as restrictive as it already was.\n"
                        )
                    return
                if outcome is WriteOutcome.FENCED:
                    raise CliOperationFailed(
                        f"brake {'narrow' if new_engaged else 'off'}: refused — governance "
                        "writes are durably fenced. Pass --offline (after confirming no "
                        "daemon is running — this command verifies that for you) to write "
                        "directly, or start a new daemon.",
                    )
            raise CliOperationFailed(
                f"brake {'narrow' if new_engaged else 'off'}: could not apply after "
                f"{_CLI_MAX_CAS_RETRIES} attempts.",
            )
        finally:
            db_ctx.close_quietly(conn)
    finally:
        if held_lock is not None:
            held_lock.__exit__(None, None, None)


def _cli_status(db: str) -> None:
    _cli_ensure_governance_schema(db)
    conn = db_ctx.connect(db)
    try:
        db_ctx.set_wal_pragmas(conn)
        engaged, scopes, version = _cli_read_current(conn)
        console.print(f"persisted: engaged={engaged}, scopes={sorted(scopes)}, version={version}")
        console.print(
            "note: this is the PERSISTED value; a running daemon's LIVE state may be more "
            "restrictive if a loosening was issued while it was running (see 'brake off/"
            "narrow's own output for why).",
        )
    finally:
        db_ctx.close_quietly(conn)


def _cli_ensure_governance_schema(db_path: str) -> None:
    conn = db_ctx.connect(db_path)
    try:
        db_ctx.set_wal_pragmas(conn)
        ensure_governance_schema(conn)   # §3 — idempotent; creates every governance table
                                           # (and the singleton brake_runtime row) if missing
        conn.commit()
    finally:
        db_ctx.close_quietly(conn)
```

**Command surface:** `brake on`/`engage` → `_cli_engage`; `brake off`/`disengage` → `_cli_loosen(db,
False, None)`; `brake narrow`/`narrow_scopes` → `_cli_loosen(db, True, scope)`; `brake status` →
`_cli_status`. Every write command accepts `--offline`, never inferred or defaulted to `True`
automatically — an operator must consciously opt in, having been told by this exact tool that no daemon
is running.

---

## 7. API admission-token lifecycle

**Why a real token, not internal-task cancellation alone:** cancelling the daemon's own internal
background tasks (`_tick_task`, `_consumer_task`, `_dream_task`, `_scheduler_task`) proves nothing about
the API/HTTP layer's own willingness to accept new external work — a brand-new inbound HTTP request can
be admitted and start executing entirely independently of the daemon's internal asyncio tasks. External
request admission must be closed and drained as its own, explicit step.

**Why a `ContextVar`-carried capability, not a docstring convention:** a rule enforced only by a comment
("call `try_admit()` once, at the outermost ingress") can be silently violated by a future entry point, a
refactor, or a direct unit-test invocation — with no error, just quietly-wrong counting. A token object,
validated by every downstream governed call, turns "was this ever admitted" into a runtime check instead
of a review-time hope.

**Why the token must be revocable, not merely present:** `contextvars.ContextVar` values propagate by
being copied into any `asyncio.Task` created from within the same logical context — including a detached
child task the parent does not await. If the parent's request completes and releases its admission while
a detached child (holding the *same* inherited token object) is still running, the admission count no
longer reflects that child's continued activity: a shutdown drain could report success while governed
work is still in flight. Revoking the token — a piece of shared, mutable state on the token object itself,
visible to every holder of that same reference, including inherited copies — before releasing the
admission count closes this.

**Why release must be bound to a specific admission identity, not an unqualified decrement:** a public
`release()` that takes no argument cannot distinguish "the caller releasing its own admission" from "any
other code path decrementing an admission it does not own." Only the admission's own, unique identity —
created at `try_admit()` time and never exposed for reconstruction by another caller — may remove that
admission's entry; a foreign, duplicate, or unrecognized identity is a no-op that leaves every other
admission's tracked state untouched.

### 7.1 Core types

```python
import contextvars

_current_admission_token: "contextvars.ContextVar[AdmissionToken | None]" = contextvars.ContextVar(
    "_current_admission_token", default=None,
)


class AdmissionToken:
    """
    Proof that the calling context is running inside an admitted scope.
    Created ONLY by GovernedRequestAdmission.admit(). Carries its own
    active/revoked state as a plain, shared mutable attribute — since a
    ContextVar's *binding* is copied into a child task, but the token
    OBJECT it points to is the same Python object, revoking it here is
    visible to every holder, including any inherited copy in a detached
    child task. Also carries the admission_id it was issued for, so
    release is always bound to that exact identity, never reconstructed
    or guessed by unrelated code.
    """
    __slots__ = ("_id", "_admission_id", "_active")
    def __init__(self, admission_id: str):
        self._id = uuid.uuid4().hex
        self._admission_id = admission_id
        self._active = True

    def is_active(self) -> bool:
        return self._active

    def _revoke(self) -> None:
        self._active = False


class AdmissionRefused(RuntimeError):
    """Raised by admit() when the admission gate is closed (shutting down)."""


class AdmissionRequired(RuntimeError):
    """Raised by require_admission_token() when called from OUTSIDE any
    admitted scope, or with an already-revoked token in scope — a
    governed entry point reached without going through its outermost
    ingress's admit() call, or reached by a detached child task after
    its parent's scope has already exited."""


def require_admission_token() -> None:
    """
    Called by EVERY governed downstream entry point as its own first
    action — VALIDATES, never admits. Rejects both an absent token
    (direct/bypassing invocation) and a present-but-revoked token (a
    detached child task that inherited a token whose parent scope has
    already exited).
    """
    token = _current_admission_token.get()
    if token is None or not token.is_active():
        raise AdmissionRequired(
            "this governed entry point was invoked without an active admission token in "
            "scope — either no admit() context is present, or the token has already been "
            "revoked (the outer request that admitted it has already exited/released; a "
            "detached child task inheriting a stale token is refused, not silently trusted).",
        )
```

### 7.2 `GovernedRequestAdmission` — identity-bound active admissions

```python
class GovernedRequestAdmission:
    """
    Owned by KernelDaemon. Gates admission of new external requests at
    the API boundary, before they ever reach ParkingBrake/Orchestrator.
    Tracks a SET of active admission identities rather than a bare
    counter: only the exact identity returned by try_admit() may remove
    its own entry via release(); a foreign, duplicate, or unrecognized
    identity affects no other admission's tracked state.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._open = True
        self._active_ids: set[str] = set()

    def try_admit(self) -> "str | None":
        with self._lock:
            if not self._open:
                return None
            admission_id = uuid.uuid4().hex
            self._active_ids.add(admission_id)
            return admission_id

    def release(self, admission_id: str) -> None:
        with self._lock:
            if admission_id not in self._active_ids:
                logger.error(
                    "GovernedRequestAdmission.release(%s) called for an admission ID that "
                    "is not currently active — a double release, a foreign/unbound identity, "
                    "or a release with no matching admit(). Ignored: it must not affect any "
                    "OTHER admission's tracked state.", admission_id,
                )
                return
            self._active_ids.discard(admission_id)

    def close(self) -> None:
        with self._lock:
            self._open = False

    async def drain(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if not self._active_ids:
                    return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)

    def admit(self):
        """The ONLY correct way to admit a request. Used exactly once,
        at the OUTERMOST ingress point of a logical request — never
        re-entered by downstream code."""
        admission_id = self.try_admit()
        if admission_id is None:
            raise AdmissionRefused()
        return _AdmissionScope(self, admission_id)


class _AdmissionScope:
    def __init__(self, admission: "GovernedRequestAdmission", admission_id: str):
        self._admission = admission
        self._admission_id = admission_id
        self._token = AdmissionToken(admission_id)
        self._ctx_reset = None
        self._released = False

    def __enter__(self) -> "AdmissionToken":
        self._ctx_reset = _current_admission_token.set(self._token)
        return self._token

    def __exit__(self, *exc) -> None:
        # Order matters: revoke BEFORE releasing the admission count.
        # Any child task still concurrently running sees the revoked
        # state as promptly as possible; release() (which affects the
        # drain-visible _active_ids set) happens only after.
        self._token._revoke()
        _current_admission_token.reset(self._ctx_reset)
        self._release_once()

    def _release_once(self) -> None:
        if self._released:
            return   # idempotent — a second release (e.g. __exit__ running twice, or a
                       # future bug calling release() explicitly too) is a harmless no-op,
                       # never affecting this admission's already-removed entry, and never
                       # capable of touching a DIFFERENT admission's identity.
        self._released = True
        self._admission.release(self._admission_id)   # bound to the EXACT id this scope owns
```

### 7.3 Every real external ingress, grounded in the actual repository

The repository at commit `d96e7887ddd6c8e6d231052b5e6d7599e0739ee8` defines these HTTP routes capable of
starting kernel, governance, or persistence work (`bartholomew_api_bridge_v0_1/services/api/app.py`):
`POST /api/chat` (`chat()`, line 249), `POST /kernel/command/{cmd}` (`kernel_command()`, line 183),
`POST /api/nudges/{nudge_id}/ack` (`ack_nudge()`, line 318), `POST /api/nudges/{nudge_id}/dismiss`
(`dismiss_nudge()`, line 331), and `POST /api/reflection/run` (`trigger_reflection()`, line 370). No
`handle_sight()`/`handle_voice()` method or HTTP route exists in this repository at all — the only sight/
voice-related code is `run_sight_through_runtime_contract()`/`run_voice_through_runtime_contract()` in
`bartholomew/kernel/runtime_contract.py`, reachable only from tests and non-HTTP adapter modules
(`identity_interpreter/adapters/sight/pipeline.py`, `.../voice_io/stream_bridge.py`), never from any API
route. These two seams are therefore governed the same way internal producers are (§9) — by their own
adapter's lifecycle, not by `GovernedRequestAdmission` — unless and until a future change exposes them
via an actual HTTP ingress, at which point the identical `admit()` pattern below applies to that new
route.

```python
# app.py — FIVE separate, independent HTTP routes, each its own outermost ingress. None
# nested inside another; each calls admit() itself, immediately wrapping its existing body.

async def chat(body: "ChatIn"):
    try:
        with daemon.governed_request_admission.admit():
            ...   # existing body, unchanged — including its call into
                    # run_chat_through_runtime_contract(), which itself calls
                    # require_admission_token() as its own first action (below)
    except AdmissionRefused:
        raise HTTPException(status_code=503, detail="shutting down")

async def kernel_command(cmd: str):
    try:
        with daemon.governed_request_admission.admit():
            return await _kernel.handle_command(cmd)   # require_admission_token() added as
                                                           # handle_command()'s own first action
    except AdmissionRefused:
        raise HTTPException(status_code=503, detail="shutting down")

async def ack_nudge(nudge_id: int):
    try:
        with daemon.governed_request_admission.admit():
            return await _kernel.mem.set_nudge_status(nudge_id, "acked", ...)   # require_
                                                                                    # admission_token()
                                                                                    # added as set_
                                                                                    # nudge_status()'s
                                                                                    # own first action
    except AdmissionRefused:
        raise HTTPException(status_code=503, detail="shutting down")

async def dismiss_nudge(nudge_id: int):
    try:
        with daemon.governed_request_admission.admit():
            return await _kernel.mem.set_nudge_status(nudge_id, "dismissed", ...)
    except AdmissionRefused:
        raise HTTPException(status_code=503, detail="shutting down")

async def trigger_reflection(kind: str = "daily"):
    try:
        with daemon.governed_request_admission.admit():
            return await _kernel.handle_command(f"reflection_run_{kind}")
    except AdmissionRefused:
        raise HTTPException(status_code=503, detail="shutting down")
```

`run_chat_through_runtime_contract()` (the actual per-chat-request governance seam `chat()` calls into —
not `Orchestrator.handle_input()` directly, which it calls only *after* this seam's own check),
`_kernel.handle_command()`, and `MemoryStore.set_nudge_status()` each gain `require_admission_token()` as
their own first action, validating only — never admitting. `Orchestrator.handle_input()` **keeps its
exact existing synchronous signature** (`def handle_input(self, user_input: str) -> str:`) — it is not
converted to `async def`, and no caller of it is required to change how it calls it. Since `require_
admission_token()` is a plain, synchronous `ContextVar` read, it can additionally be called from within
`handle_input()`'s own (still-synchronous) body without altering its signature or async-ness at all — this
specification adds that call there too, as a second, defense-in-depth check, not as a replacement for the
seam-level check above.

```python
# bartholomew/kernel/runtime_contract.py::run_chat_through_runtime_contract — gains this
# as its own first action, before constructing/using ParkingBrake:
require_admission_token()

# identity_interpreter/orchestrator/orchestrator.py::Orchestrator.handle_input — UNCHANGED
# signature, gains this as its own first line, still entirely synchronous:
def handle_input(self, user_input: str) -> str:
    require_admission_token()
    ...   # existing body otherwise unchanged in this pass beyond the ParkingBrake
            # consolidation named in §8/§12
```

**Internal producers, and the sight/voice seams, are explicitly out of scope for this gate:**
`self._scheduler_task` and the other internal producer tasks originate inside the daemon's own event
loop, not from an external request — they have no "outermost external ingress" to admit at. They are
governed exclusively by `KernelDaemon.stop()`'s internal task cancellation-and-await step (§9, which runs
*before* `freeze_and_drain_writes()`), and must never call `admit()`/`require_admission_token()`. The
sight/voice runtime-contract seams (above) are treated identically, for the reason already stated: no
HTTP ingress currently exists for them.

### 7.4 Detached governed child work

A parent request must never call `asyncio.create_task()` directly for governed work it does not intend
to await before its own admission scope exits: the child inherits a `ContextVar` binding to the SAME
token object the parent is about to revoke, and any subsequent `require_admission_token()` call inside
that child is correctly refused the moment the parent's scope exits. Legitimate detached governed work
must register its **own**, independent admission instead:

```python
def spawn_detached_governed_task(coro_factory) -> "asyncio.Task":
    """
    The ONLY sanctioned way to launch governed work that outlives its
    parent request's own admission scope. Admits its OWN, independent
    admission identity — a separate try_admit() call, its own
    AdmissionToken, its own entry in _active_ids — held open for
    exactly the child task's own lifetime, released via the task's own
    completion, not when this function returns.
    """
    admission = daemon.governed_request_admission
    admission_id = admission.try_admit()
    if admission_id is None:
        raise AdmissionRefused()
    child_token = AdmissionToken(admission_id)

    async def _run():
        ctx_reset = _current_admission_token.set(child_token)
        try:
            await coro_factory()
        finally:
            child_token._revoke()
            _current_admission_token.reset(ctx_reset)
            admission.release(admission_id)

    return asyncio.create_task(_run())
```

`drain()` (§7.2) correctly waits for any task spawned this way, since its admission is tracked in `_
active_ids` for its own, independent lifetime — indistinguishable, from the drain's perspective, from any
other in-flight external request.

---

## 8. `KernelDaemon.start()` — complete sequence, with a verified failed-start unwind

**The real `ParkingBrake` construction-site inventory, grounded in the repository:** direct inspection at
the commit above found **seven** independent production construction sites, none sharing an instance
today: `Orchestrator.handle_input()` (`identity_interpreter/orchestrator/orchestrator.py:133`);
`run_chat_through_runtime_contract` (`runtime_contract.py:238`); `run_drive_through_runtime_contract`
(`runtime_contract.py:392`); `run_sight_through_runtime_contract` (`runtime_contract.py:631`); `run_
voice_through_runtime_contract` (`runtime_contract.py:718`); `skill_registry.py:667`; and three sites in
`bartholomew/cli.py` (261, 277, 291). Phase B's consolidation target is that the first five — every
seam reachable from the live daemon — share **one** instance, injected at startup; the CLI's three sites
are replaced entirely by §6's `_engage_op`/`_loosen_op`-based functions, which never construct a
`ParkingBrake` object at all. `Orchestrator.set_parking_brake()` does not exist in the repository today;
it is added by this specification as the injection point, and each of the four `runtime_contract.py` seam
functions and `skill_registry.py`'s site gain an equivalent injected reference (`set_parking_brake()` on
whatever object owns each, or a constructor parameter, depending on that call site's own existing
structure) rather than constructing `ParkingBrake(BrakeStorage(...))` inline as they do today.

```python
class DaemonPoisonedError(RuntimeError):
    """Raised by stop()/start() on an instance that failed to reach a
    verified terminal shutdown or failed-start-cleanup state on a prior
    call (§9). The daemon lock was deliberately NOT released; only
    OS-level process termination frees it. Never retry stop()/start()
    on this instance."""


class KernelDaemon:
    def __init__(self, db_path: str, ...):
        self.db_path = db_path
        self._poisoned = False
        self.general_executor = DedicatedDbExecutor(db_path, thread_name="sync-db", busy_timeout_ms=5000)
        self.governance_executor = DedicatedDbExecutor(
            db_path, thread_name="brake-db", busy_timeout_ms=2000, max_queue_depth=3,
        )
        self.task_supervisor = PersistenceTaskSupervisor()
        self.parking_brake = ParkingBrake(storage=..., executor=self.governance_executor,
                                            task_supervisor=self.task_supervisor)
        self.governed_request_admission = GovernedRequestAdmission()
        self.brake_watcher = BrakeWatcher(self.parking_brake, self.governance_executor)
        self.daemon_lock: "DaemonProcessLock | None" = None
        # ... mem, orchestrator, scheduler, and every other pre-existing daemon field, unchanged ...

    async def start(self) -> None:
        """
        Numbered sequence. Step 0 (the process lock) is the absolute
        first action. Every activated resource is tracked by a stage
        flag; on ANY failure, _unwind_failed_start() stops and verifies
        the termination of every resource actually activated so far, in
        reverse order, BEFORE the lock is released — releasing it
        without that verification would let a second daemon process
        acquire the same lock while this process's own producers,
        watcher, or executors are still running (finding 2's exact
        failure sequence).
        """
        if self._poisoned:
            raise DaemonPoisonedError(
                "this KernelDaemon instance is POISONED — terminate the process; a poisoned "
                "instance must never be restarted in-process.",
            )

        # 0. Process lock, before ANYTHING else.
        self.daemon_lock = DaemonProcessLock(self.db_path)
        self.daemon_lock.acquire_or_raise()   # raises DaemonLockHeld if another Phase-B
                                                 # daemon process already holds this database's
                                                 # lock; nothing else has happened yet, nothing
                                                 # to unwind for THIS specific failure.

        mem_initialized = False
        producers_started = False
        watcher_started = False
        runtime_ok = False

        try:
            if maintenance_mode_is_active(self.db_path):
                raise RuntimeError(
                    "maintenance mode is active (a rollback_prepare() hand-off is in "
                    "progress) — refusing to start until an operator clears it.",
                )

            # 1. MemoryStore's own persistent connection and schema.
            await self.mem.init()
            mem_initialized = True

            # 2. Governance schema — mandatory, aborts startup on failure.
            await self.parking_brake.ensure_schema_applied()

            # 3. One-time, idempotent forward migration — logs and continues on failure.
            await self.parking_brake.migrate_legacy_parking_brake_value()

            # 4. Runtime transition: reopens the durable write fence, decides whether the
            #    persisted governance value can be trusted.
            runtime_ok = await self.parking_brake.establish_runtime()

            # 5. Scheduler schema readiness (general lane, fail-closed, unchanged
            #    pre-Phase-B convention; scheduler's own DDL, unaffected by I1's narrowing).
            await self._ensure_scheduler_schema()

            # 6. Experience kernel restore, async, awaited, via the general lane.
            await self._init_experience_kernel()

            # 7. Skill loading (general lane).
            await self._load_skills()

            # 8. IF runtime_ok: create the internal producer tasks.
            if runtime_ok:
                self._tick_task = asyncio.create_task(self._tick_loop())
                self._scheduler_task = asyncio.create_task(self._scheduler_loop())
                self._dream_task = asyncio.create_task(self._dream_loop())
                self._consumer_task = asyncio.create_task(self._consumer_loop())
                producers_started = True

            # 9. BrakeWatcher starts ALWAYS, regardless of step 8's outcome.
            self.brake_watcher.start()
            watcher_started = True

            # 10. Wire the SAME ParkingBrake instance into every one of the five real
            #     construction sites named above — closing the actual construction-site
            #     gap, not a hypothetical one.
            Orchestrator.set_parking_brake(self.parking_brake)
            runtime_contract.set_parking_brake(self.parking_brake)   # used by all four
                                                                        # run_*_through_runtime_
                                                                        # contract seam functions
            skill_registry.set_parking_brake(self.parking_brake)

        except BaseException:
            cleanup_ok = await self._unwind_failed_start(
                mem_initialized=mem_initialized, producers_started=producers_started,
                watcher_started=watcher_started,
            )
            if cleanup_ok:
                self.daemon_lock.release()
            else:
                logger.critical(
                    "KernelDaemon.start(): failed-start cleanup could not verify every "
                    "activated resource reached a terminal state — the daemon lock will "
                    "NOT be released; this instance is now POISONED. Terminate this "
                    "process; only the OS's automatic release on exit will free the lock.",
                )
                self._poisoned = True
            raise
        # On SUCCESSFUL start, the lock is deliberately NOT released here — held for the
        # daemon's entire remaining lifetime, released only by stop() (§9) or by process
        # exit (automatic, both platforms, via the underlying OS primitive, §10).

    async def _unwind_failed_start(self, *, mem_initialized: bool, producers_started: bool,
                                     watcher_started: bool) -> bool:
        """
        Stops, in reverse activation order, every resource this specific
        start() attempt actually activated before failing, and verifies
        each one reaches a terminal state. Returns True only if every
        activated resource is confirmed stopped/closed. Both executors
        are constructed in __init__ (not start()) and may have been
        used by any of steps 2–7 regardless of which step actually
        failed — both are always closed here, verifying worker-thread
        termination via §4.3's corrected close(), never left running
        just because the step that failed happened to be an earlier one.
        """
        all_ok = True
        if watcher_started:
            try:
                await self.brake_watcher.stop()
            except BaseException:
                logger.exception("_unwind_failed_start: brake_watcher.stop() raised")
                all_ok = False
        if producers_started:
            for t in (self._tick_task, self._consumer_task, self._dream_task, self._scheduler_task):
                if t and not t.done():
                    t.cancel()
            for t in (self._tick_task, self._consumer_task, self._dream_task, self._scheduler_task):
                if t:
                    try:
                        await asyncio.wait_for(t, timeout=5.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        all_ok = False
        governance_ok = await self.governance_executor.close(timeout=2.0)
        general_ok = await self.general_executor.close(timeout=5.0)
        all_ok = all_ok and governance_ok and general_ok
        if mem_initialized:
            try:
                await self.mem.close(checkpoint=False)
            except BaseException:
                logger.exception("_unwind_failed_start: mem.close() raised")
                all_ok = False
        return all_ok
```

**First-start migration behavior, stated precisely:** on the very first Phase-B-aware startup against a
pre-existing database, there is no `brake_runtime` row with a matching `runtime_id` yet even if step 3's
migration seeded `parking_brake_state` — `establish_runtime()` (step 4) will see `prev_clean=False` and
force `engaged=global` regardless. This is correct, not an oversight: pre-Phase-B code never maintained
`brake_runtime` under this protocol's terms, so there is no proof its last shutdown was clean. The
migrated value takes effect starting from the **second** Phase-B startup onward, once one clean shutdown
has legitimately occurred under this protocol's own terms.

---

## 9. `KernelDaemon.stop()` — complete sequence, corrected clean-marker precondition

```python
async def stop(self) -> None:
    """
    Releases self.daemon_lock ONLY if EVERY sub-step below reaches a
    VERIFIED terminal state — not merely "no exception was raised."
    shutdown_clean (brake_runtime.clean=1) is an ORTHOGONAL concept: a
    daemon can terminate every resource correctly (earning a lock
    release) while still failing to achieve a clean GOVERNANCE shutdown
    specifically. Any failure to reach the verified terminal state —
    exception or a sub-step's own reported non-termination — marks this
    instance POISONED and withholds release; only OS-level process
    termination frees the lock in that case.
    """
    if self._poisoned:
        raise DaemonPoisonedError(
            "this KernelDaemon instance is POISONED from a prior incomplete shutdown or "
            "failed-start cleanup — terminate the process; do not call stop() again.",
        )

    admission_terminal = False
    internal_tasks_terminal = False
    governance_terminal = False
    general_terminal = False
    shutdown_clean = False

    try:
        # 1. Close and drain EXTERNAL request admission FIRST — before anything else.
        #    Cancelling internal tasks proves nothing about the API layer's own
        #    willingness to accept new work; this step is what actually stops it.
        self.governed_request_admission.close()
        admission_terminal = await self.governed_request_admission.drain(timeout=5.0)

        # 2. Cancel and await every INTERNAL producer task.
        self.task_supervisor.begin_quiescing()
        for t in (self._tick_task, self._consumer_task, self._dream_task, self._scheduler_task):
            if t and not t.done():
                t.cancel()
        await self.brake_watcher.stop()
        internal_tasks_terminal = True
        for t in (self._tick_task, self._consumer_task, self._dream_task, self._scheduler_task):
            if t:
                try:
                    await asyncio.wait_for(t, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    internal_tasks_terminal = False
        self.task_supervisor.seal()

        # 3. Freeze normal governance-write admission, close the durable database fence,
        #    and drain everything already admitted (§5.7).
        governance_drained = await self.parking_brake.freeze_and_drain_writes(timeout=5.0)

        # 4. Seal write-admission CLOSED, then close (and, per §4.3's correction, verify
        #    the worker thread of) the governance executor.
        self.parking_brake.seal_write_lifecycle_closed()
        governance_lane_closed_cleanly = await self.governance_executor.close(timeout=2.0)
        governance_terminal = governance_lane_closed_cleanly

        # 5. The clean marker — the SINGLE, FINAL, dedicated database transaction — only
        #    if EVERY Python-verified precondition in I8 holds. internal_tasks_terminal is
        #    now a MANDATORY member of this gate, not merely computed and left unchecked.
        if (admission_terminal and internal_tasks_terminal
                and governance_drained and governance_lane_closed_cleanly):
            shutdown_clean = await self.parking_brake.finalize_clean_shutdown()
        else:
            logger.error(
                "shutdown preconditions not all met (admission_terminal=%s, "
                "internal_tasks_terminal=%s, governance_drained=%s, "
                "governance_lane_closed_cleanly=%s) — brake_runtime.clean will NOT be set.",
                admission_terminal, internal_tasks_terminal, governance_drained,
                governance_lane_closed_cleanly,
            )

        # 6. general_executor and MemoryStore's own close — unrelated to governance
        #    tables; no further governance database mutation occurs from this point on,
        #    by construction (the governance executor is already closed and finalize_
        #    clean_shutdown() was its last write).
        general_terminal = await self.general_executor.close(timeout=5.0)
        await self.mem.close(checkpoint=general_terminal and shutdown_clean)
    except BaseException:
        logger.critical(
            "KernelDaemon.stop(): an unexpected exception interrupted the shutdown "
            "sequence — the daemon lock will NOT be released; this instance is now "
            "POISONED. Terminate this process; only the OS's automatic release on exit "
            "will free the lock.", exc_info=True,
        )
        self._poisoned = True
        raise

    if admission_terminal and internal_tasks_terminal and governance_terminal and general_terminal:
        self.daemon_lock.release()
        logger.info("KernelDaemon.stop(): verified terminal state reached; daemon lock released.")
    else:
        logger.critical(
            "KernelDaemon.stop(): shutdown did NOT reach a verified terminal state "
            "(admission_terminal=%s, internal_tasks_terminal=%s, governance_terminal=%s, "
            "general_terminal=%s) — the daemon lock will NOT be released; this instance "
            "is now POISONED. Terminate this process; only the OS's automatic release on "
            "exit will free the lock.",
            admission_terminal, internal_tasks_terminal, governance_terminal, general_terminal,
        )
        self._poisoned = True
```

**Proof of I8, corrected to state exactly what is enforced and by what mechanism, and to state one single
outcome for a governance call arriving after this sequence — never two contradictory ones:**
`_establish_clean_shutdown_direct()`'s own `WHERE` clause transactionally enforces `write_fence_open=0`
and the matching `runtime_id` — this is the one part of I8 the SQL itself proves, independent of Python
control flow. Every other precondition — `admission_terminal`, `internal_tasks_terminal`, `governance_
drained`, and `governance_lane_closed_cleanly` (itself now genuinely worker-thread-verified, §4.3) — is
checked in Python, all four required, *before* step 5 is even attempted; a `False` on any one of them
means `finalize_clean_shutdown()` is never called at all, not merely that its own check might fail.
Once this sequence has run to completion, **any** governance call arriving afterward is refused before
any mutation, live or persisted — this is `engage()`'s own contract (§5.3) and it is the only outcome
this document describes anywhere; there is no second, contradictory path in which a post-shutdown call
"still tightens locally." (Local tightening while merely `FROZEN`, before this sequence reaches step 4,
remains permitted and remains the mechanism that invalidates a clean determination — a temporally earlier,
distinct case from "after this sequence has completed," which is what this paragraph describes.)

---

## 10. Process lock, poisoned-instance behavior, and rollback

### 10.1 `DaemonProcessLock` — cross-platform, process-lifetime

**Why a process-lifetime lock, not `BEGIN EXCLUSIVE`:** `BEGIN EXCLUSIVE` proves only "no transaction is
open at this exact instant." A daemon can be alive, fully idle, holding no open transaction, and `BEGIN
EXCLUSIVE` from a second connection would succeed against it — a false quiescence signal. A lock held for
the entire process lifetime, released automatically by the OS on any exit (clean or crash), has no such
gap.

```python
import sys

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class DaemonLockHeld(RuntimeError):
    """Raised by acquire_or_raise() when another process already holds
    the daemon lock."""


class _LockUnavailable(Exception):
    pass


def _daemon_lock_path(db_path: str) -> str:
    return db_path + ".daemon.lock"


def _ensure_lock_byte_exists(fh) -> None:
    """
    Windows-only. msvcrt.locking() locks/unlocks a byte region that must
    lie within the file's current extent. A freshly created, empty lock
    file has no byte 0 to lock. Writes a single placeholder byte if the
    file is currently empty; leaves existing content (e.g. from a prior
    run, or a deliberately non-empty test fixture) untouched otherwise.
    POSIX flock has no analogous requirement — it locks the whole open
    file description regardless of size — so this is skipped there.
    """
    fh.seek(0, os.SEEK_END)
    if fh.tell() == 0:
        fh.write(b"\0")
        fh.flush()


def _platform_lock_nonblocking(fh) -> None:
    if sys.platform == "win32":
        fh.seek(0)   # ALWAYS seek to the SAME fixed byte before locking — msvcrt.locking()
                       # operates on the range starting at the CURRENT position, which is not
                       # otherwise guaranteed stable across "a+b" opens of a non-empty file.
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as e:
            raise _LockUnavailable() from e
    else:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise _LockUnavailable() from e


def _platform_unlock(fh) -> None:
    if sys.platform == "win32":
        fh.seek(0)   # the SAME fixed byte as the lock call
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class DaemonProcessLock:
    """
    Cross-platform, process-lifetime advisory lock. Both platforms
    guarantee the same observable property — exclusive, held for as
    long as the owning process is alive, automatically released by the
    OS on any process exit including a crash — via different
    mechanisms: POSIX flock ties the lock to the open file description;
    Windows ties it to the file handle, released as part of standard
    handle cleanup on process exit.
    """
    def __init__(self, db_path: str):
        self._path = _daemon_lock_path(db_path)
        self._fh = None   # open file object; None means not currently held

    def acquire_or_raise(self) -> None:
        held = self._try_acquire()
        if held is None:
            raise DaemonLockHeld(
                f"another process already holds the daemon lock at {self._path} — a Phase-B "
                "daemon appears to already be running against this database.",
            )
        self._fh = held._fh   # held via THIS DaemonProcessLock instance's own release(),
                                 # not via the _HeldLock context-manager protocol — used by
                                 # KernelDaemon, which owns the lock for its whole lifetime
                                 # rather than a single `with` block.

    def release(self) -> None:
        """Explicit, IDEMPOTENT release. Called from KernelDaemon.stop()'s
        verified-terminal path (§9) and from the failed-start unwind
        path in start() (§8) — covering every failed-startup path.
        Idempotent: calling it when nothing is held is a no-op, always
        safe from an exception handler regardless of how far startup
        progressed before failing."""
        if self._fh is None:
            return
        _platform_unlock(self._fh)
        self._fh.close()
        self._fh = None

    def try_acquire_nonblocking(self) -> "_HeldLock | None":
        """Used by the CLI's offline path (§6) and rollback_prepare()
        (§10.3): a genuinely independent acquisition attempt against the
        SAME lock file. Returns a context manager holding the lock for
        the `with` block's duration if acquired, or None if a daemon
        process currently holds it."""
        return self._try_acquire()

    def _try_acquire(self) -> "_HeldLock | None":
        fh = open(self._path, "a+b")
        if sys.platform == "win32":
            _ensure_lock_byte_exists(fh)
        try:
            _platform_lock_nonblocking(fh)
        except _LockUnavailable:
            fh.close()
            return None
        return _HeldLock(fh)
```

`_HeldLock` (including `issue_offline_write_permit()`) is defined in full in §6.1.

### 10.2 Poisoned-instance behavior

`DaemonPoisonedError` and the `self._poisoned` flag are defined and enforced entirely within `Kernel
Daemon.start()`/`stop()` (§8, §9). Once `self._poisoned` is `True`: every subsequent `start()`/`stop()`
call on that instance raises `DaemonPoisonedError` immediately, without attempting anything further; the
daemon lock remains held; only terminating the OS process releases it (the same automatic, platform-level
mechanism described in §10.1). No code path anywhere in this specification clears `self._poisoned` —
recovery is "kill the process, start a new one," not "retry in place." This applies identically whether
the poisoning arose from an incomplete `stop()` or from a failed-start cleanup that could not verify every
activated resource had terminated (§8).

### 10.3 Rollback

```python
class RollbackAborted(RuntimeError):
    pass


def _maintenance_marker_path(db_path: str) -> str:
    return db_path + ".maintenance"


def maintenance_mode_is_active(db_path: str) -> bool:
    return os.path.exists(_maintenance_marker_path(db_path))


def rollback_prepare(db_path: str) -> None:
    """
    1. PROCESS-LIFETIME QUIESCENCE CHECK: attempt the exact lock a live
       KernelDaemon holds for its entire run, non-blocking. Success
       proves no Phase-B daemon process — alive or idle — currently
       exists against this database. Failure aborts loudly, reading and
       writing nothing.
    2. Still holding that lock: read parking_brake_state, write it into
       system_flags['parking_brake'] (BEGIN IMMEDIATE).
    3. Re-verify via a fresh, separate connection.
    4. Write the maintenance-mode marker file — BEFORE releasing the
       daemon lock, so there is no window where the lock is free but
       the marker isn't yet in place. KernelDaemon.start() (§8) refuses
       to start while it exists — the controlled hand-off: a process
       supervisor auto-restarting the Phase-B daemon during the
       operator's deployment window is refused, not raced against.
    5. Release the daemon lock.
    6. Print VERIFIED, plus an explicit reminder that maintenance mode
       remains ACTIVE until an operator explicitly clears it (§10.3's
       rollback_clear_maintenance(), below — itself only a best-effort
       check, not a complete guarantee; see I12/§14).
    """
    lock = DaemonProcessLock(db_path)
    held = lock.try_acquire_nonblocking()
    if held is None:
        raise RollbackAborted(
            "Could not acquire the daemon process lock — a Phase-B daemon process currently "
            "appears to be running against this database (this check detects an IDLE "
            "daemon too, not only one mid-write). Stop it first, then re-run this tool. "
            "Nothing was read or written.",
        )
    with held:
        conn = db_ctx.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT engaged, scopes_json FROM parking_brake_state WHERE id=1",
                ).fetchone()
                engaged, scopes_json = (bool(row[0]), row[1]) if row else (True, json.dumps(["global"]))
                legacy_value = json.dumps({"engaged": engaged, "scopes": json.loads(scopes_json)})
                conn.execute(
                    "INSERT INTO system_flags(key, value, updated_at) VALUES ('parking_brake', ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (legacy_value, _now_iso()),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        finally:
            db_ctx.close_quietly(conn)

        verify_conn = db_ctx.connect(db_path)
        try:
            row = verify_conn.execute("SELECT value FROM system_flags WHERE key='parking_brake'").fetchone()
            if row is None or row[0] != legacy_value:
                raise RollbackAborted(
                    "Post-write verification FAILED — system_flags['parking_brake'] does not "
                    "match what was just written. Do NOT deploy old code. Maintenance mode "
                    "was NOT activated.",
                )
        finally:
            db_ctx.close_quietly(verify_conn)

        with open(_maintenance_marker_path(db_path), "w") as f:
            f.write(json.dumps({"activated_at": _now_iso(), "reason": "rollback_prepare"}))

    print(
        "VERIFIED — the daemon process lock proved no Phase-B daemon was running (idle or "
        "active), the legacy value was copied and independently re-verified, and "
        "maintenance mode is now ACTIVE (a Phase-B daemon will refuse to start while it "
        "remains active). Safe to deploy pre-Phase-B code now. Once old code is confirmed "
        "stable AND independently confirmed terminated, run rollback_clear_maintenance().",
    )


def rollback_clear_maintenance(db_path: str) -> None:
    """
    Corrected this pass (finding 8): clearing maintenance mode is no
    longer a bare file deletion based solely on operator judgement. A
    legacy, pre-Phase-B process participates in no part of this
    specification's locking protocol — DaemonProcessLock cannot detect
    it, because it never acquires that lock. There is therefore no way
    to PROVE such a process has stopped from inside this protocol. What
    CAN be checked, honestly and without overclaiming, is whether ANY
    writer is ACTIVELY TRANSACTING against this database at the instant
    of this call — a BEGIN EXCLUSIVE probe. This detects a legacy
    process that happens to be mid-write; it does NOT detect one that
    is running but currently idle. That residual gap is named here,
    not hidden, and remains an irreducible operator responsibility
    (I12, §14): independently confirm — via process-manager or
    host-level verification, outside this tool's own reach — that the
    legacy process has actually been terminated before relying on this
    call's success as sufficient justification to restart a Phase-B
    daemon.
    """
    marker = _maintenance_marker_path(db_path)
    if not os.path.exists(marker):
        print("Maintenance mode was not active; nothing to clear.")
        return
    conn = db_ctx.connect(db_path, timeout=3.0)
    try:
        conn.execute("BEGIN EXCLUSIVE")
    except sqlite3.OperationalError as e:
        db_ctx.close_quietly(conn)
        raise RollbackAborted(
            "Could not acquire an exclusive lock within 3s — another writer (possibly the "
            "legacy process this rollback was for) appears to be ACTIVELY TRANSACTING "
            "against this database right now. Refusing to clear maintenance mode. Confirm "
            "the legacy process has fully stopped, then retry. (This check cannot detect an "
            "IDLE legacy process — see this function's own documentation and I12/§14.)",
        ) from e
    conn.rollback()
    db_ctx.close_quietly(conn)
    os.remove(marker)
    print(
        "Maintenance mode cleared — a Phase-B daemon may now start again. NOTE: this only "
        "confirmed no writer was actively transacting at this instant; it is NOT proof the "
        "legacy process has fully stopped, since that process never participates in "
        "DaemonProcessLock. Independently confirm it has been terminated (process-manager "
        "or host-level verification) before relying on this.",
    )
```

**Rollback plan:** every implementation slice (§15) is independently revertible without unwinding later
slices, except the slice depending on the executor's own schema, which depends on the governance schema
slice. `rollback_prepare()` is a required precondition of rolling back the `ParkingBrake` slice in a live
deployment — its own internal quiesce-check and post-write verification are what make "guarded" an
enforced property of the tool, not a documentation claim. `rollback_clear_maintenance()`'s own limitation
is stated plainly rather than hidden (I12, §14) — it is a proportionate floor, not a complete guarantee,
and the operator's own independent confirmation remains part of the authorized procedure, not an
unstated assumption.

---

## 11. `BrakeWatcher`

```python
class BrakeWatcher:
    def __init__(self, brake: "ParkingBrake", governance_executor: "DedicatedDbExecutor",
                 interval: float = 1.0, poll_attempt_timeout: float = 2.0,
                 fail_closed_after: int = 5):
        """
        interval: sleep between poll cycles once the previous one has
            resolved — NOT a cap on any individual read's own duration.
        poll_attempt_timeout: the POLL-ATTEMPT BOUND — how long ONE
            single read is allowed to take before this cycle counts as
            a failure. Deliberately decoupled from `interval`.
        """
        self._brake, self._executor = brake, governance_executor
        self._interval, self._poll_attempt_timeout = interval, poll_attempt_timeout
        self._fail_closed_after = fail_closed_after
        self._consecutive_failures = 0
        self._last_seen_version: int | None = None
        self._task: "asyncio.Task | None" = None

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

            cycle_ok = False
            try:
                tracked = await self._executor.run_tracked(
                    _read_state_op, timeout=self._poll_attempt_timeout, reserved=True,
                )
                if tracked.status is OpStatus.SUCCEEDED:
                    engaged, scopes, version = tracked.result
                    if version != self._last_seen_version:
                        self._brake.apply_external_refresh(engaged, scopes, version)   # may raise
                        self._last_seen_version = version   # ONLY reached if the call above
                                                               # succeeded — a raise here
                                                               # leaves _last_seen_version at
                                                               # its previous value, so the
                                                               # NEXT cycle retries the SAME
                                                               # version rather than silently
                                                               # treating it as handled.
                    cycle_ok = True
                else:
                    logger.warning("BrakeWatcher: poll-attempt did not succeed (%s)", tracked.status)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("BrakeWatcher: unexpected error in poll cycle")
                # cycle_ok is guaranteed False here in every case reaching this branch,
                # including a raise from result-unpacking or apply_external_refresh itself.

            if cycle_ok:
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
                logger.warning(
                    "BrakeWatcher: consecutive failed observation cycles=%d/%d",
                    self._consecutive_failures, self._fail_closed_after,
                )
                if self._consecutive_failures >= self._fail_closed_after:
                    logger.error(
                        "BrakeWatcher: %d consecutive failed cycles — forcing local "
                        "engaged=global until storage is reachable again. Does NOT "
                        "auto-clear; an operator must confirm the issue and explicitly "
                        "disengage/narrow.", self._consecutive_failures,
                    )
                    self._brake.force_local_engage_degraded()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


def _read_state_op(conn) -> tuple[bool, frozenset, int]:
    row = conn.execute(
        "SELECT engaged, scopes_json, version FROM parking_brake_state WHERE id=1",
    ).fetchone()
    if row is None:
        return (True, frozenset({"global"}), 0)   # fresh install fallback — matches
                                                     # establish_runtime()'s own conservative
                                                     # default (§5.9)
    return (bool(row[0]), frozenset(json.loads(row[1])), row[2])
```

**Three bounds, stated honestly, distinctly, separately:**

- **Poll-attempt bound:** `poll_attempt_timeout` (2.0s default). One single read either resolves or
  gives up within this time, regardless of queue depth, because `reserved=True` exempts it from
  `max_queue_depth` (§4.3) — it always at least attempts, though it can still be *delayed* (not rejected)
  by whatever the single gate/thread is executing at that moment.
- **Propagation bound (honestly scoped, not a single fixed worst case):** under non-adversarial
  conditions, propagation typically completes within one cycle — approximately `interval +
  poll_attempt_timeout` ≈ 3.0s for the first attempt after an external write lands. There is no
  universal, provable upper bound on propagation time under sustained adversarial contention — what is
  provable is the fail-closed bound below, which is what actually protects Governance under that
  condition.
- **Fail-closed bound:** `fail_closed_after * (interval + poll_attempt_timeout)` ≈ `5 * 3.0 = 15s` worst
  case before the system proactively engages globally when it cannot verify governance state through
  `fail_closed_after` consecutive non-`SUCCEEDED`/exception-raising poll attempts.

**Starvation protection:** `asyncio.Lock` grants waiters in FIFO order; combined with `reserved=True`
guaranteeing the watcher's read is never rejected before even joining the queue, and combined with the
engage side never sharing more than one CAS attempt per command (§5.3), the watcher cannot be starved
indefinitely by a bounded burst of other governance operations.

---

## 12. Final function signatures and caller inventory

| Function / method | Signature | Called by |
|---|---|---|
| `ensure_governance_schema` | `(conn) -> None` | `ParkingBrake.ensure_schema_applied()`; `_cli_ensure_governance_schema` |
| `_ensure_column` | `(conn, table, column, ddl) -> None` | `ensure_governance_schema` |
| `_ensure_brake_runtime_singleton_row` | `(conn) -> None` | `ensure_governance_schema` |
| `migrate_legacy_parking_brake_value` | `(self) -> None` (async, `ParkingBrake` method) | `KernelDaemon.start()` step 3 |
| `_engage_op` | `(conn, incoming, expected_version, audit_event_id, audit_source, *, offline_permit=None) -> (WriteOutcome, version, scopes, engaged, last_loosen_version)` | `_persist_engage_task`; `_cli_engage`; `_write_flag_direct` |
| `_loosen_op` | `(conn, expected_version, new_engaged, new_scopes, audit_event_id, audit_source, *, offline_permit=None) -> (WriteOutcome, version, scopes, engaged)` | `_attempt_loosening`; `_cli_loosen` |
| `_read_write_fence_open` | `(conn) -> bool` | `_engage_op`; `_loosen_op` |
| `_set_write_fence_op` | `(conn, open_) -> None` | `freeze_and_drain_writes` (via `governance_executor.run_tracked`) |
| `_insert_audit_row` | `(conn, event_id, action, engaged, scopes, version, source) -> None` | `_engage_op`; `_loosen_op`; `migrate_legacy_parking_brake_value` |
| `_establish_clean_shutdown_direct` | `(db_path, runtime_id) -> bool` | `ParkingBrake.finalize_clean_shutdown()` (via `asyncio.to_thread`) |
| `ParkingBrake.engage` | `(self, *scopes) -> EngageResult` | every real construction site listed below, in-process |
| `ParkingBrake._persist_engage_task` | `(self, op_id) -> None` (async) | `ParkingBrake.engage` (via `PersistenceTaskSupervisor.launch`), one independent task per call |
| `ParkingBrake._attempt_loosening` | `(self, new_engaged, new_scopes) -> LoosenOutcome` (async) | `disengage`; `narrow_scopes` |
| `ParkingBrake.disengage` / `narrow_scopes` | `(self) -> LoosenOutcome` / `(self, *scopes) -> LoosenOutcome` (async) | future authenticated live-control operations |
| `ParkingBrake.apply_external_refresh` | `(self, engaged, scopes, version) -> None` | `BrakeWatcher._run`; `ParkingBrake._persist_engage_task` |
| `ParkingBrake.force_local_engage_degraded` | `(self) -> None` | `BrakeWatcher._run` |
| `ParkingBrake.freeze_and_drain_writes` | `(self, timeout=5.0) -> bool` (async) | `KernelDaemon.stop()` |
| `ParkingBrake.seal_write_lifecycle_closed` | `(self) -> None` | `KernelDaemon.stop()` |
| `ParkingBrake.finalize_clean_shutdown` | `(self) -> bool` (async) | `KernelDaemon.stop()` |
| `ParkingBrake.establish_runtime` | `(self) -> bool` (async) | `KernelDaemon.start()` step 4 |
| `DedicatedDbExecutor.run_tracked` / `.run` / `.close` | see §4.3 | `ParkingBrake`, `BrakeWatcher`, `KernelDaemon` |
| `PersistenceTaskSupervisor.launch` / `.drain_critical` | see §4.4 | `ParkingBrake.engage`; `freeze_and_drain_writes` |
| `GovernedRequestAdmission.admit` / `try_admit` / `release(admission_id)` / `close` / `drain` | see §7.2 | `chat`/`kernel_command`/`ack_nudge`/`dismiss_nudge`/`trigger_reflection` routes; `KernelDaemon.stop()` |
| `require_admission_token` | `() -> None` | `run_chat_through_runtime_contract`; `_kernel.handle_command`; `MemoryStore.set_nudge_status`; `Orchestrator.handle_input` (defense in depth) |
| `spawn_detached_governed_task` | `(coro_factory) -> asyncio.Task` | any code that legitimately needs governed work to outlive its request |
| `DaemonProcessLock.acquire_or_raise` / `.release` / `.try_acquire_nonblocking` | see §10.1 | `KernelDaemon.start()`/`stop()`; `_cli_acquire_offline_lock_or_raise`; `rollback_prepare` |
| `_HeldLock.issue_offline_write_permit` | `(self) -> OfflineWritePermit` | `_cli_engage`; `_cli_loosen` |
| `OfflineWritePermit.is_valid` | `(self) -> bool` | `_engage_op`; `_loosen_op` |
| `_cli_engage` / `_cli_loosen` / `_cli_status` | `(db, ..., *, offline=False) -> None` | `bartholomew/cli.py`'s `brake` command group |
| `_cli_ensure_governance_schema` | `(db_path) -> None` | `_cli_engage`; `_cli_loosen`; `_cli_status` |
| `rollback_prepare` / `rollback_clear_maintenance` | `(db_path) -> None` | operator-invoked CLI tool, standalone |
| `KernelDaemon.start` / `.stop` / `._unwind_failed_start` | see §8, §9 | process entrypoint / signal handler |

**`ParkingBrake` construction/injection sites, exhaustive, grounded in the repository (resolves finding 3
and Test 54's prior gap):**

| Site | File:line (current, pre-Phase-B) | Phase B treatment |
|---|---|---|
| `Orchestrator.handle_input()` | `identity_interpreter/orchestrator/orchestrator.py:133` | Replaced with a reference set via new `Orchestrator.set_parking_brake()`, called once at startup (§8 step 10). Signature of `handle_input` itself unchanged. |
| `run_chat_through_runtime_contract` | `bartholomew/kernel/runtime_contract.py:238` | Replaced with an injected reference via new `runtime_contract.set_parking_brake()` (§8 step 10). Gains `require_admission_token()` as its own first action (§7.3). |
| `run_drive_through_runtime_contract` | `runtime_contract.py:392` | Same injection mechanism; this is an internal-producer (scheduler) seam, not admission-gated (§7.3). |
| `run_sight_through_runtime_contract` | `runtime_contract.py:631` | Same injection mechanism; no HTTP ingress exists for this seam (§7.3) — not admission-gated until one does. |
| `run_voice_through_runtime_contract` | `runtime_contract.py:718` | Same treatment as sight. |
| `skill_registry.py` | `skill_registry.py:667` | Replaced with an injected reference via new `skill_registry.set_parking_brake()` (§8 step 10). |
| CLI (three sites) | `bartholomew/cli.py:261,277,291` | Removed entirely — replaced by `_cli_engage`/`_cli_loosen`/`_cli_status` (§6.2), which never construct a `ParkingBrake` object. |

**CLI command surface, final:** `brake on`/`engage [--offline]`, `brake off`/`disengage [--offline]`,
`brake narrow SCOPE... [--offline]`, `brake status`. Default database path resolution is one canonical
`resolve_db_path()` shared by the CLI, the daemon, and the API bridge — no divergent defaults (the CLI
currently defaults to `data/bartholomew.db` while the API resolves `data/barth.db`; Slice 3, §15, is
sequenced specifically to close this before any lock, CLI, or migration work depends on a resolved path).

**API route surface, final, grounded in the actual repository:** `POST /api/chat` → `chat()`; `POST
/kernel/command/{cmd}` → `kernel_command()`; `POST /api/nudges/{nudge_id}/ack` → `ack_nudge()`; `POST
/api/nudges/{nudge_id}/dismiss` → `dismiss_nudge()`; `POST /api/reflection/run` → `trigger_reflection()`
— all five in `bartholomew_api_bridge_v0_1/services/api/app.py`, each wrapping its existing body with
`admit()` (§7.3). `GET /healthz`, `GET /api/health`, `GET /api/conversation/recent`,
`bartholomew_api_bridge_v0_1/services/api/routes/liveness.py`'s and `self_state.py`'s read-only routes,
and `GET /metrics` remain outside the admission gate — they perform no governance/persistence writes and
are unaffected by this specification.

---

## 13. Complete executable test matrix

**Group A — Executor and supervisor (§4).**

1. `TrackedOperation` resolves through exactly one object on both the resolved-within-budget and the
   genuinely-pending-then-later-resolved paths; `on_eventual_completion()` registered on an
   already-terminal object fires via `call_soon`, never synchronously inline; a cancelled `concurrent.
   futures.Future` is reported `FAILED` with a distinguishable exception, never `SUCCEEDED`.
2. `DedicatedDbExecutor.run_tracked()`'s three synchronous, never-submitted return paths each construct a
   `TrackedOperation` with `_resolved_once=True` and a captured loop, so `on_eventual_completion()` called
   on any of them fires immediately rather than hanging.
3. **`close()` proves worker-thread termination, not only gate non-force-release (corrects the prior
   insufficient version of this test — finding 6):** submit an operation that blocks until released by a
   test-controlled barrier; call `close(timeout=T)` concurrently; release the barrier; **before** `close()`
   returns, assert no thread whose name starts with the executor's configured `thread_name_prefix` remains
   in `threading.enumerate()`; assert `close()` returns `True` only after that is confirmed. Separately:
   hold the barrier past `T`; assert `close()` returns `False`, and assert `KernelDaemon.stop()` (§9)
   never attempts `finalize_clean_shutdown()` when `governance_executor.close()` returns `False`, and never
   releases the daemon lock when `general_executor.close()` returns `False`.
4. `PersistenceTaskSupervisor.drain_critical()` reports `clean=False` via the whole-runtime failure
   ledger (`ever_failed`) for a critical task that failed and was removed from tracking *before* the
   drain call began, with no other critical task outstanding for that specific call's own snapshot.
5. `launch()` refuses `CRITICAL` admission once `DRAINED`, and `BEST_EFFORT` admission once `SEALED`;
   multiple concurrent `CRITICAL` launches (one independent engage task per command, §5.3) are all
   tracked and drained correctly by one `drain_critical()` call.

**Group B — `ParkingBrake` core transitions (§5).**

6. Every governance write — `engage()`, `disengage()`, `narrow_scopes()` — is refused once `freeze_and_
   drain_writes()` has frozen the lane; each refusal is recorded (`_write_refused_during_freeze`); for
   `engage()` specifically, its live in-memory tightening still takes effect locally despite the refusal
   (this is the `FROZEN`-specific behavior; contrast with Test 17's `CLOSED`-specific behavior below).
7. **Stale engage versus confirmed scope narrowing:** engage with scopes `{A,B}`; stall its persistence
   task before submission; confirm `narrow_scopes({"A"})` to completion; release the stalled task; assert
   its CAS is `SUPERSEDED`, `cur_last_loosen_version > fence_version` is detected, and it stops without
   retrying — persisted scopes remain exactly `{A}`, never `{A,B}`.
8. **A newer local engage arriving after a loosening while an older engage task remains pending:** launch
   Task1 via `engage("X","A")`; stall it before submission; confirm `narrow_scopes(["A"])`; before
   releasing Task1's stall, call `engage("B")` — assert this launches its **own, independent** Task2;
   release Task1 — assert it is superseded and stops without retrying; assert Task2 independently
   completes and persists `scopes={A, B}` — proving `B` is never lost despite Task1's stale attempt.
9. **Engage occurring before a later superseding loosening but after the attempt snapshot (an
   intervening loosening masked by a subsequent, unrelated engage):** stall a task fenced at `V`; confirm
   a loosening committing `V→V+1` (`last_loosen_version=V+1`); additionally confirm a second, external
   engage committing `V+1→V+2` with `last_loosen_version` unchanged at `V+1`; release the stalled task;
   assert `cur_last_loosen_version(V+1) > fence_version(V)` is correctly detected despite the latest
   transition looking like a tightening, and the task stops without retrying.
10. **Missing transition provenance:** against a fresh row (`last_loosen_version` defaults to `0`), force
    a `SUPERSEDED` result caused only by a second, concurrent engage; assert `0 > fence_version` is
    `False` for any legitimate fence version, so the task correctly retries unconditionally.
11. `_loosen_op()` first-install cases (missing row, correct vs. stale `expected_version`) and
    existing-row cases, with `cursor.rowcount` explicitly asserted for both `parking_brake_state` and its
    `last_loosen_version` companion write.
12. Historical direct-loosening failure: inject a failure into `_loosen_op`'s transaction well before any
    shutdown sequence begins; later, with no other outstanding governance activity, assert `freeze_and_
    drain_writes()` returns `False` via `_governance_persistence_failed_ever()`.
13. Queue-rejected loosening never becomes permanently outstanding: saturate `max_queue_depth`; assert a
    `disengage()` returning `QUEUE_TIMEOUT_NOT_SUBMITTED` is released from `_outstanding_writes`
    immediately, returning `LoosenOutcome.REJECTED_BUSY`, never `PENDING`.
14. **Delayed loosening completion cannot regress `persisted_version` or apply stale state (corrects and
    replaces the prior version of this test — finding 4):** confirm a `disengage()` or `narrow_scopes()`
    to version `10` normally. Separately, stage a SECOND loosening whose write commits version `11` but
    whose `SUBMITTED_PENDING` completion callback is held on a test barrier; while it is held, drive an
    independent version-`12` observation through `apply_external_refresh()` that changes `persisted_
    version` to `12` **without** changing `revision` (e.g. scopes already fully contained in the
    observation); release the barrier for the version-`11` callback. Assert: `self._state.persisted_
    version` remains `12`, not regressed to `11`; `self._state`'s `(engaged, scopes)` reflect the
    version-`12` observation, not the stale version-`11` result; `_pending_disengage_op_id` is cleared.
15. `apply_external_refresh()` advances `persisted_version` monotonically (never regresses on an
    out-of-order older observation) and bumps `revision` if and only if `(engaged, scopes)` actually
    changed on that specific call.
16. **Immediate tightening from a CAS-supersession tuple:** assert `cur_scopes`/`cur_engaged`/`cur_
    version` from every `SUPERSEDED` (and `APPLIED`) result flow through `apply_external_refresh` before
    any retry/return decision — never discarded.
17. **Post-`CLOSED` engage: refusal, no mutation, no contradiction (merges and replaces the prior,
    mutually-contradictory Tests 17 and 34 — finding 5):** drive a full clean shutdown (`_write_lifecycle`
    ends `CLOSED`); record `self._state` and every persisted row; call `engage("extra_scope")`; assert
    `persistence == "refused_shutting_down"`, `self._state` is byte-for-byte identical to the recorded
    snapshot (not merely "still engaged" — exact `scopes`/`revision` unchanged), and a fresh connection
    reads identical persisted rows. This is now the **only** required test describing behavior after
    `CLOSED`; no other test in this matrix asserts or requires local tightening to occur in that state.
18. **Unexpected executor closure:** with `_write_lifecycle` still `OPEN`, call `governance_executor.
    close()` directly (out of band); call `disengage()`; assert `CLOSED_NOT_SUBMITTED` maps to `Loosen
    Outcome.FAILED` and `_governance_persistence_failed_ever()` becomes `True`. As a control, repeat with
    `freeze_and_drain_writes()` having genuinely run first (`FROZEN`); assert the same result maps to
    `REFUSED_SHUTTING_DOWN` and does not latch a new failure.

**Group C — Durable write fence and offline CLI (§6).**

19. **A missing fence row during shutdown:** delete `brake_runtime`'s row; assert `_read_write_fence_
    open()` returns `False` (fail-closed). Separately, run `ensure_governance_schema()` against a fresh
    database; assert the singleton row exists with `write_fence_open=1` with no `establish_runtime()`
    call having happened.
20. **Zero-row fence `UPDATE` being rejected:** delete `brake_runtime`'s row; call `_set_write_fence_op`;
    assert `WriteFenceUpdateFailed` is raised and the transaction is rolled back.
21. **Clean-marker SQL: proves exactly what it enforces, no more (narrowed — finding 1's audit note):**
    call `_establish_clean_shutdown_direct()` directly with the fence deliberately left open; assert
    `rowcount == 0` and the function returns `False`, with `clean` remaining unset. This test proves only
    the `WHERE`-clause-enforced portion of I8 (runtime identity, fence closure); it does **not** claim to
    prove producer or admission terminality — those are proven by Tests 1 and 33 respectively, which
    assert the *Python-level* preconditions gate whether this function is even called.
22. **CLI mutation during the freeze/final-marker window:** drive a daemon through `freeze_and_drain_
    writes()`; concurrently invoke `_cli_engage`/`_cli_loosen` from a separate connection; assert every
    attempt returns `FENCED` and raises `CliOperationFailed`, and no row changes.
23. **CLI mutation after a successful daemon stop:** drive a daemon through a full clean `stop()`
    (`write_fence_open` ends `0`); call `_cli_engage(..., offline=False)`; assert `FENCED`/
    `CliOperationFailed`. Call `_cli_engage(..., offline=True)`; assert `DaemonProcessLock` acquisition
    succeeds, the write applies, and `write_fence_open` in the database is **still `0`** afterward.
24. **Offline CLI schema initialization attempted while a daemon owns the process lock:** with a daemon
    holding the lock, call `_cli_engage(..., offline=True)`; assert `DaemonProcessLock` acquisition fails
    **before** `_cli_ensure_governance_schema`/`db_ctx.connect` are ever called (verified via
    instrumentation, not merely the eventual error).
25. **Fence bypass being impossible without a valid, live `OfflineWritePermit`:** call `_engage_op(...,
    offline_permit=None)` with the fence closed; assert `FENCED`. Attempt to construct an
    `OfflineWritePermit` directly (bypassing `_HeldLock.issue_offline_write_permit()`); assert `RuntimeError`.
26. **Permit invalidation after its lock scope exits:** acquire the lock, issue a permit, exit the lock
    scope; assert `permit.is_valid()` is `False`; assert `_engage_op(..., offline_permit=permit)` now
    behaves exactly as if `offline_permit=None` (respects the fence).

**Group D — API admission tokens (§7).**

27. **Direct `Orchestrator.handle_input()` invocation without an admission token:** call it directly, no
    enclosing `admit()` context; assert `AdmissionRequired` is raised before any governance logic runs.
28. **Every real external ingress admitted exactly once (corrects the prior hypothetical version —
    finding 3):** instrument `try_admit()`'s call count; drive one request through each of the five real
    routes — `POST /api/chat`, `POST /kernel/command/{cmd}`, `POST /api/nudges/{id}/ack`, `POST /api/
    nudges/{id}/dismiss`, `POST /api/reflection/run` — assert exactly one call per request in every case;
    assert `run_chat_through_runtime_contract`/`_kernel.handle_command`/`MemoryStore.set_nudge_status`
    never call `try_admit()` themselves (only `require_admission_token()`).
29. **A child asyncio task inheriting an admission token but attempting governed work after the outer
    request exits:** admit a request; spawn a raw `asyncio.create_task()` child (the prohibited pattern,
    not `spawn_detached_governed_task`); let the outer scope exit (revoking the token, releasing
    admission); have the child call `require_admission_token()`; assert `AdmissionRequired` is raised.
30. **Shutdown drain not reporting terminal while explicitly registered governed child work remains
    active:** use `spawn_detached_governed_task` for background work; begin `drain()` while the child is
    still running; assert it returns `False`/blocks until the child completes, and `True` once its own
    admission releases.
31. **Admission release bound to its own identity (corrects the prior unbound-decrement version — finding
    9):** admit Request A (capturing `id_A`); call `admission.release("nonexistent-id")` and `admission.
    release(id_B)` (a foreign, real identity from a separate admission `B`); assert `id_A` remains in
    `_active_ids` after both calls, and `id_B`'s own admission is also unaffected; call `admission.
    release(id_A)` twice; assert the second call is a no-op and does not affect `id_B`.
32. **Admission-drain timeout preventing clean shutdown:** hold one request admitted with `release()`
    deliberately never called through the remainder of shutdown; assert `drain()` times out (`False`);
    assert `finalize_clean_shutdown()` is never even attempted; assert `brake_runtime.clean` remains `0`.
33. **API request arriving during shutdown admission closure:** call `close()`; immediately attempt
    `try_admit()`; assert it returns `None`. Separately: admit successfully, then `close()` and
    `drain(timeout=1.0)`; assert it blocks until `release(admission_id)` is called, and returns `False`
    if the timeout elapses first.

**Group E — Shutdown, process lock, poisoned instance (§8, §9, §10).**

34. **Clean marker requires internal producer termination (new this pass — finding 1):** hold an internal
    producer task (e.g. `_scheduler_task`) on a synchronization event so it suppresses/outlives
    cancellation past its `await asyncio.wait_for(..., timeout=5.0)` window; run `stop()`; assert
    `internal_tasks_terminal` is `False`; assert `finalize_clean_shutdown()` is never called; assert
    `brake_runtime.clean` remains `0`; assert the instance is poisoned and the daemon lock remains held.
35. **Failed-start unwind, parameterized across every stage (new this pass — finding 2):** inject a
    failure immediately after each of: `mem.init()`, `ensure_schema_applied()`, `migrate_legacy_...()`,
    `establish_runtime()`, scheduler-schema readiness, experience-kernel restore, skill loading, producer
    creation, and `brake_watcher.start()`. For each injection point, assert `_unwind_failed_start()` stops
    and confirms termination of exactly the resources activated before that point (no more, no fewer);
    assert both executors are always closed and worker-termination-verified via the corrected `close()`
    (Test 3); assert `mem.close()` is called iff `mem.init()` had succeeded; assert the daemon lock is
    released only if every one of these confirms terminal, and is retained (with the instance poisoned)
    if any does not.
36. **Late engage after final clean validation:** run the full shutdown sequence to completion
    (`shutdown_clean=True`); call `engage()` on the same instance; assert refusal for persistence, local
    tightening still applies, and no database row changes. (This exercises the same contract as Test 17,
    reached via the full sequence rather than by directly manipulating `_write_lifecycle`.)
37. **Shutdown exception lock release, parameterized across every stage:** force each of admission drain,
    internal-task await, `freeze_and_drain_writes()`, and `governance_executor.close()` to raise inside
    `stop()`; for each, assert `daemon_lock.release()` was not called (a fresh acquisition attempt still
    fails), `self._poisoned` is `True`, and a subsequent `stop()`/`start()` call raises `DaemonPoisoned
    Error` immediately.
38. **Windows daemon locking:** on Windows (or a CI leg targeting it), start a daemon, assert a second
    `acquire_or_raise()` against the same path raises `DaemonLockHeld`; stop the first daemon; assert a
    fresh acquisition now succeeds.
39. **Windows locking against a non-empty lock file:** pre-populate the lock file with several bytes of
    arbitrary content before the first acquisition; assert acquisition still succeeds; assert a second,
    concurrent attempt correctly fails; assert `release()` followed by a fresh acquisition succeeds — all
    against the same non-empty file throughout.
40. **First-start migration**, all three cases: (a) fresh database, no legacy row → `"no_legacy_value_
    fresh_install"`; `establish_runtime()` applies its fail-closed default. (b) a legacy `system_flags`
    row, no `parking_brake_state` yet → migration seeds it, returns `"migrated"`, **and** this same first
    Phase-B startup still starts globally engaged regardless — a **second**, subsequent startup then
    correctly trusts the migrated value. (c) `parking_brake_state` already has a row →
    `"already_migrated_or_initialized"`, a true no-op.
41. **Rollback quiescence, including unsafe maintenance clearance (extended this pass — finding 8):**
    `rollback_prepare()` against a daemon that is alive but idle (no open transaction); assert it fails to
    acquire the lock, reads nothing, writes nothing. Stop the daemon; assert `rollback_prepare()` now
    succeeds, verifies correctly, and a subsequent daemon start with the maintenance marker present is
    refused. **New sub-case:** with the marker present, start a legacy-style writer process that holds an
    open transaction against the database (and does not, and cannot, hold `DaemonProcessLock`); call
    `rollback_clear_maintenance()`; assert it raises `RollbackAborted` and the marker is not removed while
    that writer holds its transaction; release the legacy writer's transaction; assert `rollback_clear_
    maintenance()` now succeeds and a Phase-B daemon can start again.

**Group F — `BrakeWatcher` (§11).**

42. Poll-attempt bound: a single read genuinely resolves or gives up within `poll_attempt_timeout`,
    independent of `interval`.
43. Fail-closed bound, asserted as an **upper bound only**, using a controllable clock/explicit poll-cycle
    hook rather than wall-clock scheduling: under sustained storage failure, `force_local_engage_
    degraded()` fires no later than `fail_closed_after * (interval + poll_attempt_timeout)` cycles; fast
    failures may legitimately trigger earlier, so no lower-bound assertion is made.
44. **Watcher starvation under a burst:** saturate the governance lane's queue-depth cap with concurrent
    `disengage()`/`narrow_scopes()` calls; assert a simultaneously-issued non-reserved caller is rejected
    while the watcher's own `reserved=True` poll is not.
45. **Repeated watcher exceptions against one unchanged database version:** seed the observed version at
    `V`; monkey-patch `apply_external_refresh` to raise on every call; run several poll cycles without the
    underlying version ever changing; assert `apply_external_refresh` is called on every cycle, `_last_
    seen_version` never silently advances to `V` while the patch is in effect, and `_consecutive_
    failures` increments on every cycle.

**Group G — Schema authority, CLI, and unrelated-but-touched subsystems.**

46. **CLI contract:** `_cli_engage`/`_cli_loosen` called against the real (not mocked) `_engage_op`/
    `_loosen_op` signature and return shape; a forced `SUPERSEDED` is retried and eventually succeeds;
    `_CLI_MAX_CAS_RETRIES` consecutive supersessions raise `CliOperationFailed`.
47. **CLI live-versus-persisted messaging:** assert the engage path's message states live propagation is
    possible via the tightening-only watcher; the loosening path's message states it is not, and names
    restart/a future authenticated live-control operation as the only paths — asserted by content, not
    merely presence.
48. CLI schema preflight: `brake on`/`off`/`narrow`/`status` against a genuinely fresh database (no
    prior daemon run) succeed without error, having created exactly the tables they need.
49. Default `bartholomew brake on` modifies the exact file a default-constructed daemon/API process
    would open (one canonical `resolve_db_path()`); `SUPERSEDED` is reported accurately; the CLI never
    prints a bare state word without the persisted-vs-live distinction.
50. Atomic, append-only audit: a forced mid-transaction failure inside `_engage_op`/`_loosen_op` rolls
    back the state change and the `governance_audit` insert together; two transitions within the same
    wall-clock second produce two distinct, non-colliding rows (UUID-keyed).
51. **`MemoryStore` concurrency and the corrected `reembed_memory()` — a genuine stress harness, not a
    point-in-time snapshot (corrects the prior "structural no-deadlock proof" claim — finding 4/audit):**
    run many iterations (e.g. hundreds) of concurrent, randomly-interleaved-delay `upsert_memory()`/
    `reembed_memory()` calls under a bounded overall test timeout — a genuine deadlock manifests as the
    test hanging past that timeout, which is what actually falsifies the claim, not an after-the-fact
    check of whether a lock happens to be free at one instant. After every iteration, assert the
    invariant directly: no lost updates, no partial commits (verified by reading back full row state, not
    by inspecting lock/transaction objects), and injected mid-transaction failures never leave a partial
    write. Generation-before-destruction embedding replacement is exercised with failure injection at
    both the generation and database-replace stages, across the same many-iteration harness.
52. **Schema partial-initialization compatibility, including a genuinely partial shape (extended this pass
    — finding 7/audit):** `ensure_governance_schema()`'s own idempotency on (a) a fresh database with
    none of the three tables, (b) a database with all three tables and all current columns already
    present, and (c) — the case previously missing — a database where `parking_brake_state`/`brake_
    runtime` already exist but predate `last_loosen_version`/`write_fence_open` (constructed by creating
    the tables with only their pre-this-specification column set, then running `ensure_governance_
    schema()`); assert the missing columns are added with the correct default, existing rows are
    preserved unchanged otherwise, and no data is lost.
53. `test_all_four_component_scopes`, rewritten so `engage("skills")` then `engage("sight")` leaves both
    blocked, with a follow-up `narrow_scopes("sight")` (awaited) leaving only `"sight"` blocked.
54. **`ParkingBrake` shared-instance consistency across every real construction site (extended this pass
    — finding 3/audit):** assert `Orchestrator.handle_input()`, `run_chat_through_runtime_contract`,
    `run_drive_through_runtime_contract`, `run_sight_through_runtime_contract`, `run_voice_through_
    runtime_contract`, and `skill_registry.py`'s governance check **all** reference the exact same
    `ParkingBrake` instance injected at `KernelDaemon.start()` step 10 — not merely `handle_input()` in
    isolation, which the prior version of this test checked alone while five other real construction
    sites in the repository remained independent.
55. **Schema-authority boundary is what I1 actually claims, not more (new this pass — finding 7):** a
    repository-wide check that `bartholomew/kernel/db/schema.py` defines only `parking_brake_state`,
    `brake_runtime`, and `governance_audit`; that `MemoryStore`'s and the scheduler's own DDL remain
    defined in their existing modules, unmodified by this specification; and that no implementation slice
    (§15) attempts to move them.

---

## 14. Explicitly accepted residual risks and deferred work

1. **Livelock under sustained, adversarial, simultaneous multi-writer contention against `version`.**
   Optimistic concurrency (both engage's CAS and loosening's CAS) can, in principle, retry indefinitely
   if two or more writers keep racing the same row forever. No exponential backoff or priority mechanism
   is specified beyond the short, fixed backoff on `QUEUE_TIMEOUT_NOT_SUBMITTED` (§5.3) — accepted as
   disproportionate to engineer further against a scenario that requires sustained, adversarial,
   concurrent writers to a single-daemon, single-host deployment.
2. **`BrakeWatcher`'s propagation bound is not universally provable** under sustained adversarial storage
   contention (§11) — only the fail-closed bound is. Stated honestly rather than asserted away.
3. **Tuned-but-not-independently-proven defaults**: `busy_timeout_ms`, `max_queue_depth` (governance lane
   `3`), `interval`/`poll_attempt_timeout`/`fail_closed_after` for `BrakeWatcher`, and the CLI's `_CLI_
   MAX_CAS_RETRIES` (`5`) are reasonable starting points, not values derived from load testing under this
   specification. Performance verification (§15) measures them; tuning is expected before Slice 17 exits.
4. **No real authenticated network control-plane for external governance operations.** The offline CLI
   protocol (§6) is local, single-host, process-lock-based — it does not address a split-process or
   multi-host deployment, which remains explicitly deferred to Phase C (§1).
5. **No automatic orphan-detection for embeddings** in `MemoryStore` — deferred, unrelated to Phase B's
   governance/persistence-ownership scope.
6. **No schema-version table.** Every migration in this specification is self-describing and idempotent
   (checked-before-applied), which has so far made an explicit version table unnecessary; revisit if a
   future migration cannot be made idempotent this way.
7. **The constitutional independent-emergency-shutdown mechanism remains out of scope.** `ParkingBrake`'s
   in-process software kill switch does not, and is not intended to, satisfy an independent,
   out-of-process emergency-shutdown invariant — that requires a mechanism outside the application's own
   control entirely, which this specification does not attempt to provide.
8. **Replacement-semantics inventory — complete, repository-grounded, re-verified against the exact base
   commit in this document's header.** The current, pre-Phase-B `ParkingBrake.engage()`
   (`bartholomew/orchestrator/safety/parking_brake.py:152–160`) replaces the blocked-scope set on every
   call — `engage(*scopes)` builds `scopes_set` from only that call's own arguments (`set(scopes)` or
   `{"global"}`, line 159) and `_write()` persists exactly that set (line 174–176), never reading or
   unioning with `self._cache.scopes`. `disengage()` (line 162–164) always writes `set()` regardless of
   prior state and is unaffected by the Phase B redesign, which replaces `engage()` with the monotonic-
   union semantics defined in §5.3 (a later `engage()` call only ever widens the blocked-scope set; a
   `narrow_scopes()`/`disengage()` call is the sole mechanism that removes a scope). A repository-wide
   search (`grep` for `ParkingBrake`, `BrakeStorage`, `parking_brake`, `PARKING_BRAKE`, `scoped_blocks`,
   `is_blocked(`, `engage(`, followed by reading every hit's surrounding context, not the grep line alone)
   found 31 repository files referencing these identifiers. Of those, **exactly four** genuinely test,
   assert, or document behavior that depends on replace-vs-union semantics and will need to change once
   `engage()` becomes monotonic — not twelve, and the number is not assumed from any earlier claim; it is
   the result of this pass's own re-verification, reported in full below with no external document
   required to interpret it.

   | # | File | Class / function / method | Current behavior, verified at `d96e7887ddd6c8e6d231052b5e6d7599e0739ee8` | Phase B replacement-semantics requirement | Affected caller(s) | Verifying test |
   |---|---|---|---|---|---|---|
   | 1 | `tests/test_parking_brake_scoped_blocks.py` | `test_all_four_component_scopes` (lines 95–120) | Sequentially calls `brake.engage("skills")` (103), `brake.engage("sight")` (108), `brake.engage("voice")` (113), `brake.engage("scheduler")` (118) on **one** brake instance, asserting after each call that the *previous* scope's `is_blocked()` has become `False` (lines 104, 110, 115, 120) — a direct assertion of replace semantics. | Must be rewritten to assert that each successive `engage()` call **adds** to the blocked set: after all four calls, `is_blocked()` is `True` for `"skills"`, `"sight"`, `"voice"`, and `"scheduler"` simultaneously; a subsequent, awaited `narrow_scopes("sight")` then leaves only `"sight"` blocked. | None (leaf test file; invoked only by the pytest suite / CI) | Test 53 (§13) is this exact rewrite, already specified in full. |
   | 2 | `tests/test_parking_brake_persistence_roundtrip.py` | `test_state_reloads_after_change` (lines 106–127) | `brake.engage("voice")` (117) → asserts `brake.state().scopes == {"voice"}` (119); `brake.engage("global")` (122) → asserts `brake.state().scopes == {"global"}` (123), i.e. the raw persisted scope set is asserted to have been *overwritten*, not unioned. | Must assert `brake.state().scopes == {"voice", "global"}` after the second call (the union), and additionally reload state from a fresh instance backed by the same storage to confirm the union — not just `"global"` alone — was actually persisted and survives a reload. | None (leaf test file) | Test 8 (§13) already proves the underlying union-persistence mechanism (`_engage_op`'s `cur_scopes \| incoming` merge, §5.4) for two different scopes from two separate calls; this file's own rewritten assertion is the concrete instance of that same proof, specific to the `"voice"`+`"global"` case and its reload path. |
   | 3 | `docs/SAFETY_PARKING_BRAKE.md` | "Selective Blocking" section (lines 152–160) | Narrates `bartholomew brake on --scope scheduler` followed later by `bartholomew brake on --scope skills`, stating in prose that this "allow[s] scheduler but block[s] skills" (line 159's comment) — documents replace semantics as the intended, correct behavior. | Must be rewritten to state that a second `brake on --scope X` **adds** `X` to the blocked set (both `scheduler` and `skills` remain blocked); un-blocking `scheduler` specifically requires `bartholomew brake narrow --scope skills` (§6.2's CLI surface), which explicitly narrows the persisted scope set rather than replacing it via a further `engage`. | None — operator-facing documentation, no code caller. | No executable test applies to prose directly; the rewritten text's factual accuracy is verified by consistency with Test 47 (§13, CLI live-vs-persisted messaging content) and Test 53 (§13, the engage-then-narrow sequence this section must now describe) during Slice 17's documentation rewrite (§15). |
   | 4 | `bartholomew/cli.py` | `brake_on()` (lines 242–266, specifically the console message at 264–266) | Prints `f"Scopes: {', '.join(sorted(scopes))}"` using the **locally-passed** `scopes` variable (the arguments to *this* invocation, line 257) rather than the brake's actual resulting state — correct today only because `engage()` currently replaces, so the just-passed scopes are, by construction, the entire resulting set. | This function is superseded entirely by `_cli_engage` (§6.2), which prints `new_scopes` taken from `_engage_op`'s own return value (§5.4) — the actual, freshly-committed union, never the locally-passed argument — so this specific defect does not carry forward into the Phase B CLI at all. `brake_on()` itself is removed as part of Slice 6's CLI replacement (§15). | No test in the repository exercises `brake_on()` directly (confirmed: no hits for `brake_app`/`CliRunner` under `tests/`), so no existing test asserts the current, soon-to-be-replaced text. | Test 46 (§13, CLI contract) asserts `_cli_engage`'s printed message is built from `_engage_op`'s returned `new_scopes`, not the locally-passed argument — structurally precluding this defect's recurrence. Test 49 (§13) additionally asserts the CLI never prints a bare state word without the persisted-vs-live distinction. |

   **The remaining 27 files** referencing these identifiers were individually read in full context and
   found to construct, engage (exactly once, from a fresh instance), disengage, or call `is_blocked()`
   without ever calling `engage()` a second time with a different scope set on the same instance — replace
   and union semantics are observationally identical for a single call, so none of the following require
   any change for this specific redesign: `tests/unit/safety/test_parking_brake.py`,
   `tests/conftest.py`, `tests/integration/test_parking_brake_integration.py` (all ten of its tests),
   `tests/test_api_chat_runtime_contract.py`, `tests/test_skill_runtime_contract_seam.py` (both relevant
   tests), `tests/test_scenario_replay.py`, `tests/test_runtime_contract_chat_seam.py` (both tests),
   `tests/test_scheduler_drive_convergence.py` (both tests), `tests/test_voice_sight_runtime_contract_seam.
   py` (all three relevant tests), `tests/test_end_to_end_tasks_and_audit.py`,
   `tests/helpers/fake_orchestrator.py` (calls only `is_blocked()`, never `engage()`),
   `bartholomew/kernel/memory_store.py` (seeds the initial `system_flags['parking_brake']` row only),
   `bartholomew/kernel/runtime_contract.py` (calls only `is_blocked()` at its four gate points),
   `bartholomew/kernel/skill_registry.py` (calls only `is_blocked()`), `identity_interpreter/orchestrator/
   orchestrator.py` (calls only `is_blocked()`), `config/policy.yaml` (static schema, no call-order
   semantics), `.github/workflows/ci.yml` (names test files to run, asserts nothing itself), and the
   prose-only documentation files `CHANGELOG.md`, `docs/README.md`, `COGNITIVE_RUNTIME.md`, `DECISIONS.md`,
   `INTERFACES.md`, `MASTER_PLAN.md`, `RISKS.md`, `TEST_MATRIX.md`, `docs/archive/ENGINEERING_LOG_2026.md`,
   `docs/brain.md`, `docs/incubator/ECHO_IDEAS.md`, `ASSUMPTIONS.md`, `CHECKLISTS.md`, `CI.md`,
   `CONSTITUTION.md`, `ORCHESTRATION_INTEGRATION.md`, `QUICKSTART.md`, `REVIEWS.md`, `ROADMAP.md` — each
   describes the parking-brake feature at a grain too coarse to be falsified by a replace-vs-union change
   (single-call scope descriptions, architecture notes, or test-file listings, never a narration of what a
   *second* `engage()` call does to a *prior* one).
9. **The single-process topology is assumed throughout.** `DedicatedDbExecutor`'s single-gate/single-
   thread serialization, `DaemonProcessLock`'s process-lifetime semantics, and the durable write fence
   (§6) all reason about "one daemon process, one CLI process at a time" — a genuinely multi-daemon or
   multi-host deployment would need new design work not covered here (see item 4).
10. **Schema authority is deliberately narrow (I1, corrected this pass).** `MemoryStore`'s and the
    scheduler's own DDL remain under their existing modules' ownership for the duration of Phase B; this
    specification does not claim, and does not attempt, to consolidate them. Any future decision to
    relocate that DDL into `bartholomew/kernel/db/schema.py` requires its own complete migration,
    caller-update, and implementation-slice plan under a separately approved Phase C — this specification
    explicitly does not authorize that relocation implicitly or partially.
11. **Rollback's maintenance-clearance check is a proportionate floor, not a complete guarantee (I12,
    corrected this pass).** `rollback_clear_maintenance()`'s `BEGIN EXCLUSIVE` probe detects an
    actively-transacting legacy writer at the instant of the check; it cannot detect one that is running
    but currently idle, because a legacy, pre-Phase-B process participates in no part of this
    specification's locking protocol and cannot be proven stopped by it. Independent, operator-controlled
    confirmation (process-manager or host-level verification) that the legacy process has actually
    terminated remains a required, irreducible part of the authorized rollback procedure — not an
    implementation detail this specification can substitute a purely technical check for.
12. **`run_sight_through_runtime_contract`/`run_voice_through_runtime_contract` are not wired to any
    external ingress in the current repository** and are therefore not gated by `GovernedRequestAdmission`
    in this pass (§7.3). If a future change exposes either via an HTTP route or an external adapter
    callback, that new ingress must gain the identical `admit()`/`require_admission_token()` treatment
    described in §7 — this specification does not extend admission coverage to a mechanism that does not
    yet exist to be covered.

---

## 15. Ordered implementation slices and approval boundaries

**Slice 0 — hypothesis falsification. Execution boundary.** This document defines the Phase B
architecture and the permitted diagnostic boundary only. It does not, and is not intended to, contain
enough detail to execute Slice 0. Slice 0 cannot commence from this document alone.

Slice 0 requires a separately produced, standalone, and explicitly approved execution specification. That
execution specification — not this one — must define: the exact intermittent-WAL-corruption hypothesis
being tested, stated precisely enough to be falsifiable; the instrumentation to be added, scoped file by
file; the deterministic procedure for reproducing and observing the condition; the named possible outcomes
and, for each, the consequence that follows for Phase B's subsequent slices; every file touched by the
instrumentation; the cleanup/removal requirements once the diagnostic concludes; and its own explicit
approval boundary, separate from and in addition to this document's approval.

Neither Slice 0 nor any part of Phase B implementation is authorised by this document or by this
amendment. This section states the existence and required contents of that future execution specification;
it does not supply them.

**Slice 1 — `db_ctx` unification.** One `connect()`/`set_wal_pragmas()`/`close_quietly()` module,
adopted by every direct-connection caller. Approval boundary: every existing direct-`sqlite3.connect()`
call site identified and migrated; no behavior change beyond pragma consistency.

**Slice 2 — `DedicatedDbExecutor` (§4.1–4.3).** Approval boundary: Tests 1–3 pass, including the
corrected worker-termination proof (Test 3); both lane instances constructed with their final parameters
(§4.3); no caller wired in yet beyond unit tests.

**Slice 3 — `resolve_db_path()`.** One canonical resolver shared by the CLI, daemon, and API bridge.
Approval boundary: every default-path reference converges; Test 49 passes. Sequenced before any lock,
CLI, or migration work depends on a resolved path (§12).

**Slice 4 — `PersistenceTaskSupervisor` (§4.4).** Approval boundary: Tests 4–5 pass.

**Slice 5 — the full `ParkingBrake` redesign (§5, §6, §7), including the real construction-site
consolidation (§8, §12).** The largest, most tightly-coupled slice — state machine, per-command engage
tasks, the durable write fence, `OfflineWritePermit`, the admission-token protocol, the two-barrier
shutdown sequence, and migrating all five real live-daemon `ParkingBrake` construction sites to one
injected instance are interdependent enough that splitting them further would leave intermediate states
this specification does not describe. **Approval boundary: Tests 6–33 and 54 pass, including every fence,
permit, and admission-token test and the full construction-site consistency check; §16's consistency
audit re-run against the actual implementation, not merely this document.**

**Slice 6 — CLI corrections (§6.2, §12's command surface).** Approval boundary: Tests 22–26, 46–49 pass.

**Slice 7 — `KernelDaemon.start()`/`stop()` and the process lock (§8, §9, §10.1, §10.2).** Approval
boundary: Tests 34–39 pass; the poisoned-instance state machine verified under both exception and
non-exception non-termination paths for both startup and shutdown, including the parameterized
failed-start unwind (Test 35) and the parameterized shutdown-exception lock retention (Test 37).

**Slice 8 — rollback tooling (§10.3).** Sequenced after Slice 5 (depends on `parking_brake_state`'s
final schema shape) and Slice 7 (depends on `DaemonProcessLock`). Approval boundary: Test 41 passes,
including its unsafe-maintenance-clearance sub-case.

**Slices 9–16 — `MemoryStore`, `VectorStore`/`FTSClient` daemon adapters, `consent_gate.py` (verified
unreachable, no adapter needed), `experience_kernel.py`, `working_memory.py`, `persona_pack.py`,
`narrator.py`, `skill_registry.py`.** Approval boundary per slice: the specific caller's own required
tests (Test 51's corrected stress harness for `MemoryStore`) pass; no cross-module schema consolidation
attempted, consistent with I1's narrowed scope (§2) — Test 55 verifies this boundary is actually
respected, not merely stated.

**Slice 17 — liveness/metrics read-lane fix; replacement-semantics artifacts; guard-rail tests and
documentation.** Approval boundary: Tests 45, 53 pass; each of the four replacement-semantics files
enumerated in §14 item 8's inventory table individually verified against the implemented (no longer
replace-only) `engage()`/`narrow_scopes()` behavior; `docs/SAFETY_PARKING_BRAKE.md` rewritten to match the
two-step engage/narrow sequence; performance verification (below) reported.

**Performance verification, run across Slices 5–17:** `MemoryStore` serialization cost before/after;
governance-lane poll overhead idle/loaded; thread-count accounting (three total: `MemoryStore`'s internal
thread, `general_executor`, `governance_executor`); liveness-endpoint latency/availability under
general-lane saturation; `reembed_memory()`'s transactional cost; how often the engage task's own
loosening-supersession check actually fires under realistic concurrent engage/disengage activity, to
confirm the mechanism is exercised under real load, not only the targeted test.

**No slice begins without the prior slice's approval boundary having been explicitly signed off. Slice 0
itself requires a separate go-ahead beyond this specification's own approval.**

---

## 16. Consistency audit

This section cross-checks every definition against every caller, CLI path, API path, test, migration, and
rollback operation named in this document, after this pass's corrections.

**Function signatures vs. call sites.** `_engage_op` is defined once (§5.4) with a five-element return,
always; both call sites (`_persist_engage_task` §5.3, `_cli_engage` §6.2) unpack five values, and
`_write_flag_direct` (§5.8) also calls it with the correct positional/keyword arguments. `_loosen_op` is
defined once (§5.4) with a four-element return, always; both call sites (`_attempt_loosening` §5.5,
`_cli_loosen` §6.2) unpack four values. Neither function is redefined anywhere else in this document.

**Synchronous/asynchronous call contracts (re-verified this pass — finding 3).** `Orchestrator.handle_
input()` is `def`, not `async def`, in both the repository and this specification (§7.3) — no caller is
required to `await` it, and none does; `require_admission_token()` is a plain synchronous `ContextVar`
read, callable from `handle_input()`'s synchronous body without changing its signature. `run_chat_
through_runtime_contract`, `run_drive_through_runtime_contract`, `run_sight_through_runtime_contract`,
`run_voice_through_runtime_contract`, `_kernel.handle_command`, and `MemoryStore.set_nudge_status` are all
`async def` in the repository, so adding `await`-compatible `require_admission_token()` calls (themselves
synchronous, called from within already-`async` bodies) introduces no new synchronous/asynchronous
mismatch anywhere in this document.

**`WriteOutcome` values vs. every consumer.** `APPLIED`, `SUPERSEDED`, `FENCED` are the only three values
(§5.1). Every consumer handles all three where relevant: `_persist_engage_task` branches on `FENCED` then
`SUPERSEDED` then falls through to the revision check for `APPLIED`; `_attempt_loosening` branches on
`FENCED` then `SUPERSEDED` then `APPLIED`; `_cli_engage`/`_cli_loosen` branch on `APPLIED` then `FENCED`,
retrying on the implicit remaining case (`SUPERSEDED`). No consumer treats `FENCED` and `SUPERSEDED`
interchangeably.

**Version-regression guard vs. every reconciliation path (finding 4, re-verified).** `_apply_if_still_
current()` (immediate path, §5.5) and `_reconcile_pending_loosening()` (delayed path, §5.5) both check
`observed_version < self._state.persisted_version` inside the same `_state_lock` acquisition that applies
the result, before constructing the new `BrakeState` — the check and the application are no longer
separable into a helper (`_bump_with_version`) that could be called without it. `apply_external_refresh()`
(§5.6) performs the identical check independently, for its own, separate calling context.

**Post-`CLOSED` contract vs. every description of it (finding 5, re-verified).** `engage()`'s own code
(§5.3), its section-level rationale note directly above it, and §9's shutdown proof all state the
identical outcome: refusal, no live or persisted mutation, once `CLOSED`. Test 17 is the sole required
test describing this state; Test 36 exercises the same contract via the full shutdown sequence rather
than direct state manipulation, and does not restate or contradict Test 17's assertions.

**Executor `close()` vs. every caller of its return value (finding 6, re-verified).** `close()` returns
`True` only after a bounded `shutdown(wait=True)` join is confirmed (§4.3). `KernelDaemon.stop()` (§9)
uses `governance_lane_closed_cleanly` (from `governance_executor.close()`) as one of four mandatory
preconditions for `finalize_clean_shutdown()`, and `general_terminal` (from `general_executor.close()`)
as one of four mandatory preconditions for the daemon-lock release decision — both now correspond to a
genuinely verified condition, not an assumed one.

**API route inventory vs. the actual repository (finding 3, re-verified this pass).** §7.3 and §12 both
list the identical five routes (`chat`, `kernel_command`, `ack_nudge`, `dismiss_nudge`, `trigger_
reflection`), sourced from direct inspection of `app.py` at the commit in this document's header, not
from any prior document's claims. Both sections agree that no `handle_sight()`/`handle_voice()` route
exists. §8's construction-site table and §12's inventory table list the identical seven real `ParkingBrake`
construction sites.

**Admission identity vs. every release call site (finding 9, re-verified).** `try_admit()` returns an
identity string or `None`, never a bare boolean; `release(admission_id)` is the only mutator of `_active_
ids`, called by `_AdmissionScope._release_once()` (bound to the scope's own `_admission_id`) and by
`spawn_detached_governed_task`'s own cleanup (bound to its own, independently obtained `admission_id`). No
call site anywhere in this document calls `release()` without an identity argument.

**Shutdown sequencing vs. I8's restated distinction.** `KernelDaemon.stop()` (§9) computes all four
Python-verified preconditions — `admission_terminal`, `internal_tasks_terminal`, `governance_drained`,
`governance_lane_closed_cleanly` — and gates `finalize_clean_shutdown()` on all four together; the
lock-release decision at the end separately gates on all four *terminal* flags (`admission_terminal`,
`internal_tasks_terminal`, `governance_terminal`, `general_terminal`) together. Neither gate omits a
computed value the way the prior version omitted `internal_tasks_terminal` from the first.

**CLI command surface vs. the function inventory.** Every CLI command listed in §12 maps to exactly one
of `_cli_engage`/`_cli_loosen`/`_cli_status` (§6.2) — no command bypasses `_cli_ensure_governance_schema`
or the offline-lock-first ordering (verified explicitly by Test 24).

**Rollback vs. its own stated limitation.** `rollback_prepare()`'s process-lock quiescence check remains
fully enforceable (§10.3); `rollback_clear_maintenance()`'s `BEGIN EXCLUSIVE` probe is described
identically, with the identical honest limitation, in its own docstring (§10.3), I12 (§2), and the
residual-risk register (§14 item 11) — no section overstates what it proves relative to the others.

**Test matrix vs. every mechanism, including this pass's corrections.** Every one of the nine accepted
findings has at least one test in §13 whose description explicitly names the correction it proves (Tests
3, 14, 17, 24–26, 31, 34–35, 41, 52, 54–55). No test in §13 requires an outcome contradicted by another
test — the prior Test 17/34 contradiction (finding 5) no longer exists, since Test 34 has been rewritten
to exercise the same contract as Test 17 rather than its opposite. Test 28 drives only the five routes
this document's own repository inspection found; no hypothetical route appears in any test.

**Migration and rollback vs. the schema they operate on.** `migrate_legacy_parking_brake_value()` (§3)
and `establish_runtime()` (§5.9) both target the exact columns `ensure_governance_schema()` (§3) creates,
including `last_loosen_version` and `write_fence_open`, both defaulted correctly (`0` and `1`
respectively) in every `INSERT` path across §3, §5.4, and §5.9. `rollback_prepare()`/`rollback_clear_
maintenance()` (§10.3) read and write only `parking_brake_state` and `system_flags` — never `brake_
runtime`'s `write_fence_open` or `clean` columns.

**No orphaned or duplicate definitions.** Every type named in §12's inventory is defined exactly once in
this document, in the section its own inventory row cites. `_bump_with_version` — present in an earlier
version of this specification — no longer exists anywhere in this document; every call site that once
used it now constructs the corrected, version-guarded `BrakeState` inline (§5.5).

No further inconsistency was found in this pass, across either this document's own internal claims or the
repository facts it depends on. Any found during actual implementation (Slice 0 onward) should be
resolved by amending this specification before the affected slice's approval boundary is signed off, not
by silently diverging from it in code.

---

## Repository status, final re-confirmation

```
$ git branch --show-current
claude/phase-b-persistence-design-pdbcqt
$ git rev-parse HEAD
d96e7887ddd6c8e6d231052b5e6d7599e0739ee8
$ git status --short
(clean — no output)
$ git status
On branch claude/phase-b-persistence-design-pdbcqt
nothing to commit, working tree clean
```

Active local branch: `claude/phase-b-persistence-design-pdbcqt` (the session's pre-existing checkout,
not created by this or any prior turn of this review). Exact HEAD commit:
`d96e7887ddd6c8e6d231052b5e6d7599e0739ee8`, unchanged throughout every round of this review. Working
tree: clean. Staged files: none. Modified files: none. Untracked files: none.

This document is the complete, standalone, corrected Phase B Persistence Ownership Specification,
incorporating all nine accepted findings from the independent closure review and the test corrections
that accompanied them. It defines the Phase B architecture and the permitted diagnostic boundary; it does
not define, and does not contain sufficient detail for, Slice 0's execution. It awaits its own re-review
and explicit approval; Slice 0 additionally requires a separately produced, standalone execution
specification (§15) with its own explicit approval before Slice 0 may begin. No repository file has been
modified, staged, or committed in the production of this document.

---
