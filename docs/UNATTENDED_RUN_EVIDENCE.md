# Unattended run evidence

> **Status:** Working reference (2026-08-31). **Non-canonical** — a document under `docs/`, not one
> of the canonical SSOT docs. It describes a mechanism and how to use it. It does **not** authorise
> an unattended test, and it does not close a readiness band; `ROADMAP.md`, `DECISIONS.md` and the
> decision register remain the authorities on both.

## What problem this solves

`ROADMAP.md`'s Band A lists, still open, **reliable evidence/logging — "Test #1's own
shutdown-capture gap (OP-W005) must not recur; a test-process property, not yet demonstrated."**

Test #1's record could not say, afterwards, what the running system had actually done, because the
record did not survive the way the run ended. An unattended test whose evidence is destroyed by the
end of the test proves nothing — and worse, it is easy to mistake for a test that went fine.

The runtime already writes plenty. What was missing was narrow:

- **A run identity that outlives a process.** `brake_runtime.runtime_id` names one process
  incarnation and is overwritten by the next one. After a restart there was no way to say "this
  activity was before the restart and that was after", or even that a restart happened.
- **A durable incarnation ledger.** `brake_runtime` holds one row. A process that was killed rather
  than stopped left no trace of having existed as soon as the next one started.

This adds exactly those two things and a report that reads them alongside the records the runtime
already keeps.

## What it is not

The evidence mechanism **observes** the runtime. It is not a second authority, and the code is
arranged so it cannot quietly become one:

- No scheduler. No lifecycle manager. No health authority. Health is `/api/health` and
  `bartholomew.runtime.health`; lifecycle is `KernelDaemon`; restart policy is systemd/Docker.
- It never writes to, corrects, or reconciles another authority's records. The report **reads**
  `ticks`, `governance_audit`, `skill_action_audit`, `inbound_events`, `startup_incidents` and
  `brake_runtime`.
- The `runtime_id` it records is the kernel's own, read through `KernelDaemon.runtime_id`. No second
  identity is minted.
- **It is inert unless asked for.** Nothing is recorded unless `BARTH_UNATTENDED_RUN_ID` is set in
  the service process's environment. A normal deployment gains no new writer and no new table —
  pinned by `test_evidence_recording_is_off_unless_the_run_id_is_set`.

**This does not widen what Bartholomew may do unattended.** It makes unattended *testing*
trustworthy. Every existing gate, band and safety control applies exactly as before.

## The pieces

| Module | Role |
|---|---|
| `bartholomew/runtime/evidence.py` | What the **service process** writes: the run ledger (incarnations, observations) and the two best-effort hooks the API app calls at startup and shutdown. |
| `bartholomew/runtime/evidence_report.py` | Reads the ledger and the runtime's own records; builds and seals the deterministic end-of-run document. |
| `bartholomew/runtime/unattended.py` | The **harness** side: run identity, health sampling, process ownership and cleanup, freeze. Deliberately not in `tests/`, so a human can drive a real run with the same code. |

### The ledger

`unattended_run_incarnations` — one row per process that took part in the run:

| Column | Meaning |
|---|---|
| `run_id` | The run. Constant across restarts. |
| `runtime_id` | The kernel's own incarnation id for that process (`brake_runtime.runtime_id`). |
| `started_ts` / `ended_ts` | When this process joined and left the run. |
| `end_kind` | `clean`, `failed`, or `lost`. |
| `inferred` | `1` when the *end* was inferred by a later process rather than recorded by this one. |

`unattended_run_observations` — append-only, one row per thing observed: health samples (recorded
verbatim from `/api/health`, including failed reads), process spawns and stops, and the run's own
start and finish.

### The truthfulness rule

An incarnation that never recorded its own end is closed by the **next** one as `lost`, with
`inferred = 1` and an `end_detail` saying that the recorded end time is the moment it was noticed,
not the moment the process died.

- `lost` is never upgraded to `clean`, including by the process itself coming back later.
- A source table that does not exist is reported `available: false` **with no `count` field**, so
  "we could not tell" cannot be read as "zero".
- `summary.complete` is tri-state: `true` only when every incarnation recorded its own end and every
  source was readable; `false` when something is known to be missing; `null` when there are no
  incarnations at all, because that could equally mean "never started" or "the ledger was lost".

### Attribution, and its limits

- `ticks`, `governance_audit`, `skill_action_audit` and `inbound_events` are attributed **by
  timestamp**, bucketed into `[incarnation start, next incarnation start)`, with the last window
  open-ended. Resolution is whole seconds, which is what those tables store.
- A row before the first incarnation started is reported as **unattributed**, not pushed into the
  nearest window.
- `inbound_events.runtime_id` is reported as **`tenant_runtime_id`**. That column holds the
  platform's per-user runtime binding (see `inbound_auth.resolved_runtime_id`) — *whose* Bartholomew
  an event belongs in — which is a different thing from the process-incarnation id, and is `None` on
  a single-runtime local deployment. Treating the two as the same would attribute events to the
  wrong incarnation while looking precise about it.

## Running an unattended period

```bash
export BARTH_DB_PATH="$HOME/bartholomew-test/barth.db"
export BARTH_UNATTENDED_RUN_ID="soak-2026-09-01"     # your run's name
python -m bartholomew serve
```

Restart it as often as the scenario calls for — the run id stays the same, and each process becomes
a new incarnation. When the run is over:

```bash
python -m bartholomew unattended-report soak-2026-09-01 \
    --db "$BARTH_DB_PATH" --out evidence/soak-2026-09-01.json
```

The command is read-only. It prints the digest and the verdict, and exits without altering anything.

For a scripted run, drive it through `bartholomew.runtime.unattended.UnattendedRun`, which owns the
processes it starts and terminates them on the way out — including when the run itself fails. An
orphaned `serve` holds the kernel's process lock, so it does not merely waste a process: it stops
the *next* run from starting, and a run that cannot start produces no evidence.

## The frozen artifact

`freeze()` returns an envelope:

```json
{
  "generated_at": "2026-09-01T09:00:00Z",
  "digest_algorithm": "sha256",
  "digest": "…",
  "record": { "…": "…" }
}
```

`generated_at` sits **outside** the digested region on purpose. Freezing the same database twice
gives the same `digest`, so a changed digest means the evidence changed rather than that the clock
moved. `record.report_schema_version` guards against an old artifact being read against new
expectations.

The digest seals the record against accidental drift and lets two copies be compared. It is **not**
a tamper-proofing claim: it is computed from the same database anyone with write access could have
changed first, and nothing here countersigns it.

## What proves it

- `tests/test_unattended_evidence.py` — the rules, individually, fast.
- `tests/integration/test_unattended_run_evidence.py` — the acceptance scenario against real
  `bartholomew serve` subprocesses: start through the supported service path, identify the run,
  demonstrate runtime and scheduler health, observe a governed inbound capture, restart, keep
  pre- and post-restart evidence apart, stop cleanly, freeze. Plus the individual properties it
  depends on — a killed process recorded as `lost`, a fatal runtime failure recorded as `failed`,
  recording off by default, and no orphan left after a failed run.

Both run in CI: the unit file under the default marker expression, the integration file under the
`critical` job's `-m "integration or slow"`.

## Known limitations

- **Whole-second attribution.** Two incarnations starting inside the same second cannot be told
  apart by timestamp. Real restarts are far slower than that, but the limit is real and the report
  names it in its `method` field rather than hiding it.
- **The end time of a `lost` incarnation is when it was noticed**, not when the process died.
  Nothing in the process can record its own sudden death, so this is a floor, not a defect to fix.
- **A ledger write that fails is silent to the runtime by design** — evidence recording must never
  take down the thing it is observing. It shows up as a missing incarnation, which the report reads
  as a gap.
- **Scope.** This says what happened. It does not say whether what happened was *good*: no
  thresholds, no pass/fail on burden, latency or usefulness. Those belong to the scenario and its
  gates.
