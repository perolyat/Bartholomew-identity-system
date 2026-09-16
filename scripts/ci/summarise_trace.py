"""Turn an execution trace into the few lines that decide what happened.

Run as ``python -m scripts.ci.summarise_trace [trace-dir]``. Intended to run
as an ``if: always()`` CI step, so the answer is in the job log itself and
not only in an artifact somebody has to download.

It answers, in order, the questions the controller console could never
answer on its own:

1. Did every worker reach session finish, or did one stop existing?
2. For the slowest reports, how much of the elapsed time was the test and
   how much was the report getting from the worker to the controller?
3. What was outstanding when the run stalled, and on which worker?
4. Which tests cost the most SQLite connections, and how long did opening
   them take?
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_TOP = 15
#: Above this, a recorded duration is not a measurement but a patched clock.
_IMPLAUSIBLE_S = 86400.0


def _load(path: Path) -> list[dict[str, Any]]:
    events = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # A process killed mid-write leaves a partial last line. That is
            # information too, but it is not an event.
            continue
    return events


def _fmt(seconds: float) -> str:
    return f"{seconds:8.1f}s"


def summarise(trace_dir: Path) -> int:
    if not trace_dir.is_dir():
        print(f"execution trace: nothing at {trace_dir}")
        return 0

    controller_path = trace_dir / "controller.jsonl"
    controller = _load(controller_path) if controller_path.exists() else []
    workers = {path.stem: _load(path) for path in sorted(trace_dir.glob("gw*.jsonl"))}

    print("=" * 78)
    print(f"EXECUTION TRACE  {trace_dir}")
    print("=" * 78)

    # 1. Worker lifecycle.
    print("\n-- worker lifecycle -------------------------------------------")
    created = [e for e in controller if e["event"] == "node_created"]
    downs = [e for e in controller if e["event"] == "node_down"]
    print(f"workers created: {len(created)}  ({', '.join(e['gateway'] for e in created)})")
    for event in downs:
        state = "CRASHED" if event.get("crashed") else "finished"
        print(f"  {event['gateway']:>5}  {state}  {event.get('error') or ''}")
    for name, events in workers.items():
        finished = [e for e in events if e["event"] == "session_finish"]
        exited = [e for e in events if e["event"] == "process_exit"]
        phases = [e for e in events if e["event"] == "phase"]
        last = phases[-1]["nodeid"] if phases else "<none>"
        print(
            f"  {name:>5}  reports={len(phases):5d}  "
            f"session_finish={'yes' if finished else 'NO'}  "
            f"process_exit={'yes' if exited else 'NO'}  last={last}",
        )
        if finished:
            info = finished[-1]
            print(
                f"         sqlite: {info.get('sqlite_connections')} connections, "
                f"{info.get('sqlite_connect_seconds')}s opening them",
            )

    # 2. Test time versus transport time.
    print("\n-- slowest reports: test time vs transport ---------------------")
    worker_calls: dict[tuple[str, str], dict[str, Any]] = {}
    for name, events in workers.items():
        for event in events:
            if event["event"] == "phase":
                worker_calls[(event["nodeid"], event["when"])] = {**event, "worker": name}
    rows = []
    starts: dict[str, float] = {}
    for event in controller:
        if event["event"] == "logstart_received":
            starts[event["nodeid"]] = event["t"]
        elif event["event"] == "report_received":
            key = (event["nodeid"], event["when"])
            emitted = worker_calls.get(key)
            if emitted is None:
                continue
            transport = event["t"] - emitted["t"]
            rows.append(
                (
                    emitted.get("wall_s", 0.0),
                    transport,
                    event["nodeid"],
                    event["when"],
                    emitted["worker"],
                ),
            )
    # A trace written before the clocks were bound at import (or by a
    # process whose clock a test patched) can carry impossible numbers.
    # They are shown separately rather than allowed to sort to the top.
    implausible = [r for r in rows if abs(r[1]) > _IMPLAUSIBLE_S or r[0] > _IMPLAUSIBLE_S]
    rows = [r for r in rows if r not in implausible]
    if implausible:
        print(f"({len(implausible)} report(s) ignored: the test patched the clock)")
    if rows:
        print(f"{'test':>9} {'transport':>10}  worker  when      nodeid")
        by_total = sorted(rows, key=lambda r: r[0] + max(r[1], 0.0), reverse=True)
        for wall, transport, nodeid, when, worker in by_total[:_TOP]:
            print(f"{_fmt(wall)} {_fmt(transport)}  {worker:>5}  {when:<8}  {nodeid}")
        worst = max(rows, key=lambda r: r[1])
        print(
            f"\nlargest worker-to-controller delay: {worst[1]:.1f}s "
            f"on {worst[2]} ({worst[3]}, {worst[4]})",
        )
        print(
            "  A large transport delay with a small test time is the harness; "
            "the reverse is the test.",
        )
    else:
        print("no report could be matched to a worker record")

    # 3. Stalls and aborts.
    print("\n-- stalls -----------------------------------------------------")
    any_stall = False
    for event in controller:
        if event["event"] in ("stall", "abort"):
            any_stall = True
            print(
                f"controller {event['event'].upper()} after {event.get('idle_s')}s; "
                f"last={event.get('where')}  queued_units={event.get('workqueue_units')}",
            )
            for gateway, pending in (event.get("outstanding") or {}).items():
                print(f"    {gateway}: {len(pending)} outstanding {pending[:3]}")
    for name, events in workers.items():
        for event in events:
            if event["event"] == "stall":
                any_stall = True
                print(
                    f"{name} STALL after {event.get('idle_s')}s in {event.get('where')} "
                    f"(test {event.get('nodeid')})",
                )
                print(f"    threads: {event.get('threads')}")
    if not any_stall:
        print("none")
    stacks = sorted(trace_dir.glob("*.stacks.txt"))
    if stacks:
        print(f"\nstack dumps written: {', '.join(p.name for p in stacks)}")

    # 4. Thread growth across each worker's run.
    print("\n-- live threads, first to last test on each worker ---------------")
    for name, events in workers.items():
        counts = [e.get("threads") for e in events if e.get("threads") is not None]
        if not counts:
            continue
        print(
            f"  {name:>5}  first={counts[0]:4d}  median={sorted(counts)[len(counts) // 2]:4d}  "
            f"last={counts[-1]:4d}  max={max(counts):4d}",
        )

    # 5. SQLite cost.
    print("\n-- most SQLite connections per test ----------------------------")
    per_test: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for event in worker_calls.values():
        entry = per_test[event["nodeid"]]
        entry[0] += event.get("sqlite_connections", 0) or 0
        entry[1] += event.get("sqlite_connect_s", 0.0) or 0.0
    ranked = sorted(per_test.items(), key=lambda kv: kv[1][0], reverse=True)[:_TOP]
    if ranked and ranked[0][1][0]:
        print(f"{'opens':>8} {'connect':>10}  nodeid")
        for nodeid, (count, seconds) in ranked:
            print(f"{int(count):8d} {_fmt(seconds)}  {nodeid}")
    else:
        print("none recorded")

    print("=" * 78)
    return 0


def main(argv: list[str]) -> int:
    trace_dir = Path(argv[1] if len(argv) > 1 else "exec-trace")
    return summarise(trace_dir)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
