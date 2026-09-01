"""`python -m bartholomew.companion` -- run the observation companion.

No arguments and no subcommands: there is exactly one thing this process does,
and a command surface would be the beginning of a control surface. Everything is
configured through the environment (`config.py`), and a misconfiguration exits
non-zero before anything is observed.
"""

from __future__ import annotations

import logging
import sys

from .config import ConfigError, load_config
from .probes import default_probe
from .runner import CompanionRunner
from .sources import LiveObservationSource


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if argv:
        print("bartholomew.companion takes no arguments; configure it via the environment.")
        return 2
    try:
        config = load_config()
    except ConfigError as e:
        print(f"Companion configuration error: {e}", file=sys.stderr)
        return 2

    probe = default_probe()
    logging.getLogger(__name__).info(
        "Companion starting: device=%s source=%s probe=%s -> %s",
        config.device_id,
        config.source_id,
        probe.name,
        config.base_url,
    )
    runner = CompanionRunner(
        config,
        LiveObservationSource(device_id=config.device_id, probe=probe),
    )
    summary = runner.run()
    logging.getLogger(__name__).info(
        "Companion stopped: captured=%d duplicates=%d refused=%d undelivered=%d",
        summary.captured,
        summary.duplicates,
        summary.refused,
        summary.undelivered,
    )
    # A companion that could never deliver anything exits non-zero: silence is
    # not success, and an operator watching a service manager should see it.
    return 0 if (summary.captured or summary.duplicates) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
