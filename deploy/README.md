# Running Bartholomew as a service

Bartholomew's kernel and scheduler run inside the API process. That process is
what must stay alive — not a browser tab, and not a developer's terminal.

Everything below launches the same thing:

```
python -m bartholomew serve
```

No arguments are required. It binds through the existing access boundary
(loopback unless a non-loopback bind has been deliberately enabled), starts the
kernel, starts the scheduler, and serves the API and UI. Closing the browser
has no effect on it.

## What supervises it

Bartholomew does not supervise itself. Restart-on-failure and start-at-boot
belong to the operating system, and two supervisors disagreeing is worse than
one. Pick one of:

| Target | Mechanism |
|---|---|
| Linux server (the Alpha target) | `deploy/bartholomew.service` (systemd) |
| Any host with Docker | `docker compose up -d` (`restart: unless-stopped`) |
| Windows development | run `python -m bartholomew serve` in a terminal |
| Windows as a service (optional) | an operator-managed wrapper such as NSSM — see below |

### systemd

```bash
sudo install -m 644 deploy/bartholomew.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bartholomew
systemctl status bartholomew
journalctl -u bartholomew -f
```

Edit `User`, `WorkingDirectory`, `ExecStart` and `BARTH_DB_PATH` in the unit
before enabling it.

### Docker

```bash
docker compose up -d
docker compose logs -f
docker compose down
```

The compose file publishes to `127.0.0.1:5173` only. A bare `-p 5173:5173`
publishes on every host interface and must not be used.

### Windows

`python -m bartholomew serve` works as-is and is the supported development
path. If a Windows *service* is wanted, a third-party wrapper such as
[NSSM](https://nssm.cc/) can register the same command. That is an optional,
operator-managed adapter: nothing in this repository bundles it, depends on it,
or requires it, and Docker Desktop is an equally supported alternative.

## Invariants the service enforces

`serve` refuses two configurations outright, exiting **4** with the reason:

* `--workers` greater than 1
* `--reload`

Both would run more than one kernel against one database. Bartholomew's SQLite
persistence is single-writer and `KernelDaemon` takes an exclusive lock on the
database file at startup, so additional processes cannot start anyway — the
refusal just makes that legible instead of surfacing as a lock error later.

**Scale by running separate runtimes against separate databases, never by
adding workers to one.**

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | Clean exit | — |
| 3 | Another Bartholomew already owns this database | Stop it, or use a different `BARTH_DB_PATH` |
| 4 | Refused configuration (`--workers`/`--reload`/bad bind) | Fix the unit file; retrying will not help |
| 5 | An unrecoverable component failed (e.g. the autonomy loop died) | Nothing — the supervisor restarts it. Check logs if it recurs |
| 143 / -15 | Terminated by SIGTERM | Normal. uvicorn re-raises the signal after a graceful shutdown |

`systemd` is configured with `RestartPreventExitStatus=3 4`, so neither
un-winnable case turns into a restart loop.

### "Could not acquire process lock"

Something else owns the database. On Linux the lock is released automatically
when the holder exits, so this almost always means a second instance really is
running (`systemctl status bartholomew`, `ps aux | grep bartholomew`). On
Windows the file handle can outlive an ungracefully-killed process; confirm no
`python` process is holding it before doing anything to the lock file. The lock
is never broken automatically — doing so would defeat the only thing preventing
two schedulers writing one database.

## Shutdown

`SIGTERM` (what every supervisor sends) triggers a graceful stop: request
admission closes, in-flight work drains, background tasks are cancelled, the
SQLite WAL is checkpointed, and the process lock is released. That takes up to
`serve.SHUTDOWN_BUDGET_SECONDS` (30s), so any supervisor's stop timeout must
exceed it — the unit file uses 45s and compose uses `stop_grace_period: 45s`. A
`SIGKILL` partway through is exactly the unclean shutdown the next startup then
has to detect and recover from.

## Is it actually alive?

`GET /api/health` answers per component, and reports `"status": "degraded"`
when any of them has failed:

```json
{
  "status": "ok",
  "components": {
    "service":   {"status": "ok"},
    "runtime":   {"status": "ok", "state": "running"},
    "scheduler": {"status": "ok", "state": "running",
                  "last_beat": "...Z", "seconds_since_beat": 3.1,
                  "last_drive": "self_check", "stalled": false},
    "inbound":   {"status": "ok", "open": false,
                  "test_resolver_active": false,
                  "detail": "Inbound capture is closed: no principal resolver installed."}
  }
}
```

`/healthz` stays a trivial liveness probe (that is its job for load balancers);
`/api/health` is the one that can tell you the scheduler died.

A `scheduler` that reports `failed` or `stalled: true` means the autonomy loop
is no longer running in this process even though the API still answers.

**You should rarely see that state persist**, because an unexpected scheduler
exit no longer only *reports* itself: the process shuts down gracefully and
exits 5, and the supervisor restarts it. The health field remains the truthful
in-process signal (and covers the stalled case, where the loop is alive but not
progressing); the exit status is what actually produces recovery. A degraded
field nothing acts on is a report, not a recovery — `systemd` and Docker only
listen to exit statuses.

If the unit keeps restarting, systemd gives up after 5 restarts in 5 minutes
(`StartLimitBurst`) and marks it failed, so a genuinely unrecoverable fault
surfaces to a human instead of looping invisibly. `journalctl -u bartholomew`
carries the `FATAL:` line naming the component and reason.

## Exposure

The API is loopback-only by default and has no authentication of its own;
inbound capture fails closed until the authenticated control plane installs a
principal resolver. Do not add `BARTH_API_ALLOW_NON_LOOPBACK=1` to make the
service reachable from elsewhere — public exposure is a separate, explicit
decision that follows authentication and TLS, not a deployment convenience.

## Devices and trusted groups

Enrolling a companion device, rotating or revoking its credential, and running
trusted-group sharing are local operator procedures against the control-plane
database -- there is no remote endpoint for any of them, for the same reason
`bartholomew accounts` has none. The full runbook, including the lost-device
and compromised-credential procedures and how to roll the feature back, is in
[`docs/E_TRUST_OPERATOR_RUNBOOK.md`](../docs/E_TRUST_OPERATOR_RUNBOOK.md); the
mechanism and its threat model are in
[`docs/E_DEVICE_TRUST_AND_TRUSTED_GROUPS.md`](../docs/E_DEVICE_TRUST_AND_TRUSTED_GROUPS.md).

Inbound capture stays fail-closed unless the unit sets
`BARTH_DEVICE_INBOUND_AUTH=1`, which installs the device-credential resolver.
Leave it unset until devices are actually enrolled; it refuses to start
alongside `BARTH_INBOUND_ALLOW_TEST_RESOLVER`.

## Unattended test runs

For an unattended test period — as opposed to ordinary running — set
`BARTH_UNATTENDED_RUN_ID` in the unit's environment and keep it constant across
restarts:

```ini
Environment=BARTH_UNATTENDED_RUN_ID=soak-2026-09-01
```

Each process then records its own incarnation of that run, and a process that
ends without recording a shutdown is written off as `lost` by the next one
rather than blending into the record. Afterwards:

```bash
python -m bartholomew unattended-report soak-2026-09-01 \
    --db /var/lib/bartholomew/barth.db --out evidence/soak.json
```

Unset in every ordinary deployment, and unset is the default: without it the
runtime records nothing extra and creates no additional tables. See
`docs/UNATTENDED_RUN_EVIDENCE.md`. **This is test instrumentation only — it does
not change, and does not widen, what Bartholomew may do while unattended.**
