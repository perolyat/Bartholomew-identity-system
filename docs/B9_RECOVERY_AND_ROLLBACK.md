# B9 — Recovery and Maintenance Procedures

> Phase B, stage B9. Per `docs/PHASE_B_OVERVIEW.md`'s exit condition: "rollback/maintenance
> procedures documented with their actual, honest limitations (not overstated guarantees)." This
> document describes what actually exists — no procedure below claims a capability the code doesn't
> have.

## 1. Recovering from a crashed daemon

**What's automatic:** `bartholomew/kernel/process_lock.py`'s lock is an OS-held `flock`/`msvcrt`
lock on an open file descriptor. The OS releases it automatically when the holding process exits for
any reason, including `kill -9` — there is no daemon-side cleanup step required for the *next*
`start()` to succeed. `bartholomew/kernel/lifecycle_marker.py`'s marker will still say `"running"`
from the crashed instance; the next `start()` detects this and **logs a warning naming the previous
instance's id** — it does not block startup and does not attempt automatic repair (per
`docs/B5_IMPLEMENTATION.md`'s "conservative unclean-start recovery" design).

**What's not automatic, and has no dedicated tooling:** there is no "repair" command that inspects
*why* the previous run didn't shut down cleanly, no automatic WAL integrity check beyond what SQLite
itself performs on open, and no attempt to recover or replay any Governance transition that might
have been in flight at the moment of the crash. If a crash happened *during* a
`GovernanceBrakeStore` transition, SQLite's own transaction atomicity (not anything this stage added)
is what guarantees the write either fully landed or not at all — Phase B does not add its own crash-
recovery layer on top of that; it relies on it.

**If the lock genuinely appears stuck** (a `start()` is refused, claiming another process holds the
lock, but you're confident no daemon is actually running): there is no force-unlock command. The
correct manual procedure is: (1) check the PID `ProcessLock.owner_pid()` reports (or read the
`<db_path>.lock` file directly — it holds a bare PID), (2) confirm via `ps`/Task Manager that no
process with that PID is running, (3) only then delete the `.lock` file by hand. This is a manual,
last-resort procedure, not a supported CLI feature — automating "delete the lock file" was
deliberately not built, since a false positive (deleting a live daemon's lock) would defeat the whole
mechanism.

## 2. Rolling back the Governance schema swap (B3/B4/B6)

**The B3 schema itself is non-destructively additive.** `parking_brake_state`, `brake_runtime`, and
`governance_audit` are new tables; nothing about creating them modifies or removes the legacy
`system_flags` row. If a rollback of B3 alone were ever needed, the new tables can simply be ignored
(or dropped) — no data the legacy path depends on was touched.

**Rolling back the runtime wiring (B4's `check_scope_blocked()` swap and B6's CLI swap) is riskier and
must be done together, never partially.** `bartholomew/orchestrator/safety/parking_brake.py`'s legacy
`ParkingBrake`/`BrakeStorage` classes still exist, untouched, and would work if re-wired. But if only
*one* of the daemon's read path (B4) or the CLI's write path (B6) were reverted while the other
stayed on the new schema, the exact hazard `docs/B4_IMPLEMENTATION.md` §1 identified and got explicit
user sign-off to avoid would reopen: daemon reads and CLI writes targeting two different tables, with
an operator's `brake on`/`off` silently failing to affect the live daemon. **There is no automated
safeguard against this partial-rollback mistake** — reverting B4/B6 correctly requires reverting both
together, by a human who understands why, exactly as this document states.

**The legacy `system_flags` "parking_brake" value is inert but not deleted.** `tests/test_b9_
adversarial_validation.py::test_stale_legacy_flag_is_never_consulted_once_b3_schema_exists`
confirms `check_scope_blocked()` never reads it once B3's schema exists, however stale or divergent
its value becomes. It is left in place (not cleaned up) — matching this repository's "deprecate
before deleting duplicates" convention (`DECISIONS.md`) — should a rollback ever need it back.

## 3. Known, accepted gaps carried forward (not silently dropped)

Two findings from earlier stages remain open, by deliberate scope decision, not oversight — recorded
here again so "Phase B complete" doesn't imply "no gaps remain":

- **`bartholomew/kernel/narrator.py`'s `persist_episode()` is still reachable from the event loop
  without being routed through a `DedicatedDbExecutor`** (`docs/B2_IMPLEMENTATION.md`'s open finding).
  It's reached via `GlobalWorkspace.publish()`'s synchronous, cross-cutting subscriber dispatch —
  fixing it means giving that whole dispatch mechanism an async-aware calling convention, a change
  affecting every subscriber, not a narrow one. `check_same_thread=False` was applied preemptively
  (B2) so the connection itself is safe to eventually move onto a dedicated thread; the move itself
  never happened.
- **`MemoryStore`'s `aiosqlite`-based connections apply no explicit WAL/busy_timeout pragmas**
  (`docs/B8_IMPLEMENTATION.md` §4). `db_ctx.set_wal_pragmas()` is written for a synchronous
  `sqlite3.Connection`; applying the equivalent to `aiosqlite` needs its own async-aware helper, not
  built in this phase.

Neither gap is believed to be an active correctness bug (both predate Phase B and are unchanged by
it) — they're recorded as remaining connection-policy/event-loop-isolation debt for whoever picks up
this class of work next.

## 4. Windows

This entire Phase B implementation (B0–B9) was developed and verified in a Linux environment.
`process_lock.py`'s Windows branch (`msvcrt.locking`) was written to mirror the POSIX
(`fcntl.flock`) branch's semantics but **has never been executed** in this development environment —
`docs/B6_IMPLEMENTATION.md` §5 already recorded this. `CI.md`'s dedicated `windows` job (Python 3.11
only, no 3.10 Windows leg) is the only real verification this code has had on that platform. If it
fails there, that is the first genuine signal about the Windows path, not a hypothetical risk being
disclosed for completeness.
