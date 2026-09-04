"""`python -m bartholomew.windows_actuation` -- run or inspect the action companion.

Two subcommands and no more. `run` is the service; `diagnostics` prints what
this install is configured to do and exits without contacting anything, so an
operator can check a configuration before pointing it at a Bartholomew.

Deliberately no subcommand that performs an action. A command-line way to make
the companion do something would be a control surface that bypasses every gate
this package exists to enforce: the only path to an action is a governed,
approved, leased request from the server.
"""

from __future__ import annotations

import json
import logging
import sys

from . import uia
from .config import ConfigError, load_config
from .runner import ActionCompanionRunner, load_state_or_explain
from .state import LedgerUnreadableError
from .win32 import is_windows

USAGE = (
    "usage: python -m bartholomew.windows_actuation [run|diagnostics]\n"
    "\n"
    "  run           lease, dispatch and report governed actions until interrupted\n"
    "  diagnostics   print this install's configuration and exit (contacts nothing)\n"
)


def _diagnostics() -> int:
    """Everything an operator needs to see, and no credential values."""
    try:
        config = load_config()
    except ConfigError as e:
        print(f"Action companion configuration error: {e}", file=sys.stderr)
        return 2

    state, ledger_error = load_state_or_explain(config)
    report = {
        "platform_is_windows": is_windows(),
        "configuration": config.describe(),
        "accessibility": uia.describe(),
        "ledger": {
            "path": str(config.state_path),
            "readable": state is not None,
            "executed_count": len(state.executed) if state else 0,
            "unreported_count": (
                len([e for e in state.executed.values() if not e.reported]) if state else 0
            ),
            "error": ledger_error or None,
        },
        "enrolment_template": json.loads(config.enrolment_template()),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if not config.capabilities:
        print(
            "\nNote: no capabilities are enabled, so this companion will refuse every "
            "action. Set BARTH_ACTION_CAPABILITIES to change that.",
            file=sys.stderr,
        )
    return 0


def _run() -> int:
    try:
        config = load_config()
    except ConfigError as e:
        print(f"Action companion configuration error: {e}", file=sys.stderr)
        return 2
    if not is_windows():
        print(
            "The Windows action companion runs on Windows only. Nothing was started.",
            file=sys.stderr,
        )
        return 2

    log = logging.getLogger(__name__)
    log.info(
        "Action companion starting: device=%s capabilities=%s -> %s",
        config.device_id,
        [k.value for k in config.capabilities],
        config.base_url,
    )
    try:
        runner = ActionCompanionRunner(config)
    except LedgerUnreadableError as e:
        print(f"Action companion refusing to start: {e}", file=sys.stderr)
        return 3

    summary = runner.run()
    log.info(
        "Action companion stopped: leased=%d succeeded=%d failed=%d unknown=%d "
        "refused_locally=%d unreported=%d",
        summary.leased,
        summary.succeeded,
        summary.failed,
        summary.unknown,
        summary.refused_locally,
        summary.unreported,
    )
    # A companion that could never authenticate exits non-zero: silence is not
    # success, and an operator watching a service manager should see it.
    return 1 if summary.channel_refusals and not summary.leased else 0


def _process_arguments() -> list[str]:
    """This process's own command line.

    Isolated in one function because `tests/test_windows_action_prohibitions.py`
    forbids the token `argv` everywhere else in these packages: a *parameter*
    called `argv` would be an argument vector for something Bartholomew was
    asked to run, which is the escape hatch the whole capability model exists
    to close. This is the entry point reading its own invocation, which is a
    different thing entirely, and keeping it here means the ban elsewhere is
    absolute rather than qualified.
    """
    return list(sys.argv[1:])


def main(command_line: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = list(command_line or [])
    if not args or args == ["run"]:
        return _run()
    if args == ["diagnostics"]:
        return _diagnostics()
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(_process_arguments()))
