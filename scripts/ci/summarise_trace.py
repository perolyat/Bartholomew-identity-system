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
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_TOP = 15
#: Above this, a recorded duration is not a measurement but a patched clock.
_IMPLAUSIBLE_S = 86400.0
#: How much of each stack dump to echo into the job log.
_CONTROLLER_STACK_LINES = 260
_WORKER_STACK_LINES = 120


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


def _print_stacks(trace_dir: Path) -> None:
    """Put the stack dumps in the log, not only in an artifact.

    A CI artifact is not always reachable from where the diagnosis is
    made, and a stall that can only be explained by downloading a zip is
    a stall that stays unexplained. The tail of each dump is the most
    recent one, which is the one that matters.
    """
    for pattern, tail, what in (
        ("controller.stacks.txt", _CONTROLLER_STACK_LINES, "controller"),
        ("gw*.stacks.txt", _WORKER_STACK_LINES, "worker"),
        ("gw*.gil.txt", _WORKER_STACK_LINES, "worker (GIL-free dump)"),
    ):
        for path in sorted(trace_dir.glob(pattern)):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                print(f"\n-- {path.name}: unreadable ({exc}) --")
                continue
            if not lines:
                continue
            print(
                f"\n-- {path.name} ({what}), last {min(tail, len(lines))} of {len(lines)} lines --",
            )
            for line in lines[-tail:]:
                print(f"  {line}")


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
    # Keyed by worker as well as by test. When a worker is lost its work is
    # requeued, so the same (nodeid, when) is reported twice by two
    # different processes. Keying on the test alone let one worker's
    # emit time be subtracted from another worker's receive time, which
    # invented worker-to-controller delays of 173 s and 585 s in runs
    # 35185611629 and 35189705193 -- the exact shape of a harness defect,
    # and entirely an artefact of this summary.
    worker_calls: dict[tuple[str, str, str], dict[str, Any]] = {}
    for name, events in workers.items():
        for event in events:
            if event["event"] == "phase":
                worker_calls[(name, event["nodeid"], event["when"])] = {**event, "worker": name}
    rows = []
    starts: dict[str, float] = {}
    for event in controller:
        if event["event"] == "logstart_received":
            starts[event["nodeid"]] = event["t"]
        elif event["event"] == "report_received":
            worker = event.get("worker")
            if worker is None:
                # Pre-dates the worker field, or a report with no node.
                # Guessing which worker sent it is what caused the defect
                # above, so it is left out rather than guessed.
                continue
            key = (worker, event["nodeid"], event["when"])
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
                f"last={event.get('where')}  queued_units={event.get('workqueue_units')}  "
                f"event_queue_depth={event.get('event_queue_depth')}  "
                f"shutting_down={event.get('shutting_down')}",
            )
            depth = event.get("event_queue_depth")
            if isinstance(depth, int):
                print(
                    "    => controller loop is "
                    + (
                        "BLOCKED (events arrived and are not being drained)"
                        if depth > 0
                        else "IDLE (no event pending: the wakeup was lost)"
                    ),
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
    _print_stacks(trace_dir)

    # 4. Thread growth across each worker's run.
    print("\n-- live threads, first to last test on each worker ---------------")
    for name, events in workers.items():
        counts = [e["live_threads"] for e in events if isinstance(e.get("live_threads"), int)]
        if not counts:
            continue
        print(
            f"  {name:>5}  first={counts[0]:4d}  median={sorted(counts)[len(counts) // 2]:4d}  "
            f"last={counts[-1]:4d}  max={max(counts):4d}",
        )

    # 5. SQLite cost, split across the write path.
    #
    # Opening was measured and acquitted (about 0.4 ms each). The question
    # this answers is where the rest of a storage-heavy test's time goes:
    # the commit, which fsyncs, the close, which releases the file handles,
    # or neither -- in which case it is inside execute and the statements
    # themselves are the cost.
    print("\n-- SQLite cost per test, whole write path ----------------------")
    per_test: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0])
    for event in worker_calls.values():
        # Summed per test across workers on purpose: a test that ran twice
        # because its first worker was lost cost the run both attempts.
        entry = per_test[event["nodeid"]]
        entry[0] += event.get("sqlite_connections", 0) or 0
        entry[1] += event.get("sqlite_connect_s", 0.0) or 0.0
        entry[2] += event.get("sqlite_commits", 0) or 0
        entry[3] += event.get("sqlite_commit_s", 0.0) or 0.0
        entry[4] += event.get("sqlite_close_s", 0.0) or 0.0
    wall_of: dict[str, float] = defaultdict(float)
    for event in worker_calls.values():
        wall = event.get("wall_s", 0.0) or 0.0
        if wall <= _IMPLAUSIBLE_S:
            wall_of[event["nodeid"]] += wall
    ranked = sorted(per_test.items(), key=lambda kv: kv[1][0], reverse=True)[:_TOP]
    if ranked and ranked[0][1][0]:
        print(
            f"{'opens':>8} {'connect':>10} {'commits':>8} {'commit':>10} "
            f"{'close':>10} {'wall':>10}  nodeid",
        )
        for nodeid, (opens, connect_s, commits, commit_s, close_s) in ranked:
            wall = wall_of.get(nodeid, 0.0)
            print(
                f"{int(opens):8d} {_fmt(connect_s)} {int(commits):8d} {_fmt(commit_s)} "
                f"{_fmt(close_s)} {_fmt(wall)}  {nodeid}",
            )
        print(
            "\n  Accounted time is connect + commit + close. What the wall "
            "column has\n  beyond that is inside execute, not in the "
            "per-operation connection\n  lifecycle -- which is the difference "
            "between a pooling fix and a\n  statement or schema fix.",
        )
    else:
        print("none recorded")

    # 6. What actually failed -- printed LAST, on purpose.
    #
    # A CI log is read from the end, and log APIs return the tail. This
    # summary is long enough to push pytest's own "short test summary info"
    # out of that window, which is exactly what happened on run 35193584212:
    # the job was red and the name of the failing test was unreachable
    # without downloading an artifact. A diagnostic that displaces the very
    # line a reader needs is a defect in the diagnostic.
    print("\n-- what failed ------------------------------------------------")
    failures: dict[str, list[str]] = defaultdict(list)
    for event in worker_calls.values():
        if event.get("outcome") not in (None, "passed", "skipped"):
            failures[event["nodeid"]].append(f"{event['worker']}/{event['when']}")
    crashed = [e for e in controller if e["event"] == "node_down" and e.get("crashed")]
    if not failures and not crashed:
        print("nothing: no worker crashed and no phase reported a bad outcome")

    # Also emitted as GitHub workflow annotations. Printing is not enough:
    # on a Windows runner the post-job cleanup alone fills the log tail that
    # a log API will return, so anything a step prints can be unreachable no
    # matter where in the job it runs. An annotation is attached to the
    # check run instead of the log, so it survives that and appears at the
    # top of the job in the UI.
    def annotate(message: str) -> None:
        if os.environ.get("GITHUB_ACTIONS") != "true":
            return
        # Newlines would end the workflow command; GitHub's own escape.
        print(f"::error title=Windows execution::{message}".replace("\n", "%0A"))

    for gateway in crashed:
        # Name the test it died on, not "a test that failed": a worker that
        # stops existing has its in-flight test reported as failed with no
        # message, and that row says nothing about the test itself.
        last = "<unknown>"
        for event in workers.get(gateway["gateway"], []):
            if event["event"] in ("phase", "logstart"):
                last = event["nodeid"]
        print(f"  WORKER LOST  {gateway['gateway']}  died on {last}")
        print(f"               {gateway.get('error') or ''}")
        annotate(f"worker {gateway['gateway']} was lost while running {last}")
    for nodeid, where in sorted(failures.items()):
        print(f"  {', '.join(where):<24}  {nodeid}")
        annotate(f"{nodeid} reported {', '.join(where)}")

    print("=" * 78)
    return 0


def main(argv: list[str]) -> int:
    trace_dir = Path(argv[1] if len(argv) > 1 else "exec-trace")
    return summarise(trace_dir)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
