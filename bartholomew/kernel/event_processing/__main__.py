"""Operator commands for the event backbone. Read state, requeue, resync.

Deliberately a module rather than a subcommand of `bartholomew`: these are
recovery operations on this package's own state, and keeping them here means
the recovery path cannot drift from the state machine it recovers.

    python -m bartholomew.kernel.event_processing status
    python -m bartholomew.kernel.event_processing quarantined
    python -m bartholomew.kernel.event_processing requeue --source-id acme --event-id 42
    python -m bartholomew.kernel.event_processing resync --from-row 0

Every command takes `--db` or reads `BARTH_DB_PATH`. None of them starts a
runtime, and `requeue`/`resync` are the only two that write. Both are safe
against a running daemon: they are ordinary bounded transactions against the
same database, and neither can move an event out of a state a live pass is
holding (a claimed event is untouched by both).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import store
from .health import processing_health


def _db_path(args: argparse.Namespace) -> str:
    path = args.db or os.getenv("BARTH_DB_PATH") or os.getenv("BARTHO_DB_PATH")
    if not path:
        raise SystemExit(
            "No database given. Pass --db /path/to/barth.db or set BARTH_DB_PATH.",
        )
    return path


def _cmd_status(args: argparse.Namespace) -> int:
    print(json.dumps(processing_health(_db_path(args)), indent=2, sort_keys=True))
    return 0


def _cmd_quarantined(args: argparse.Namespace) -> int:
    records = store.list_by_state(
        _db_path(args),
        store.STATE_QUARANTINED,
        limit=args.limit,
    )
    print(json.dumps([r.as_dict() for r in records], indent=2, sort_keys=True))
    return 0


def _cmd_requeue(args: argparse.Namespace) -> int:
    states = tuple(args.state) if args.state else (store.STATE_QUARANTINED, store.STATE_REFUSED)
    moved = store.requeue(
        _db_path(args),
        source_id=args.source_id,
        event_id=args.event_id,
        from_states=states,
        limit=args.limit,
    )
    print(f"requeued {moved} event(s) from {', '.join(states)}")
    return 0


def _cmd_resync(args: argparse.Namespace) -> int:
    watermark = store.resync_from(_db_path(args), from_inbound_row_id=args.from_row)
    print(
        f"sweep cursor set to inbound_events.id > {watermark}; captured events "
        "above it are re-examined on the next pass (events already known to the "
        "backbone are skipped)",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bartholomew.kernel.event_processing",
        description="Inspect and recover the event-processing backbone.",
    )
    parser.add_argument("--db", default=None, help="database path (or set BARTH_DB_PATH)")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="print the processing health snapshot")
    status.set_defaults(func=_cmd_status)

    quarantined = sub.add_parser("quarantined", help="list quarantined events")
    quarantined.add_argument("--limit", type=int, default=50)
    quarantined.set_defaults(func=_cmd_quarantined)

    requeue = sub.add_parser(
        "requeue",
        help="put quarantined or refused events back in the ready queue",
    )
    requeue.add_argument("--source-id", default=None)
    requeue.add_argument("--event-id", default=None)
    requeue.add_argument(
        "--state",
        action="append",
        choices=sorted(store.ALL_STATES),
        help="state to requeue from; repeatable (default: quarantined and refused)",
    )
    requeue.add_argument("--limit", type=int, default=100)
    requeue.set_defaults(func=_cmd_requeue)

    resync = sub.add_parser(
        "resync",
        help="rewind the sweep cursor so earlier captured events are re-examined",
    )
    resync.add_argument("--from-row", type=int, default=0)
    resync.set_defaults(func=_cmd_resync)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
