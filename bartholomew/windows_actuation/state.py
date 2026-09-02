"""The action companion's own durable state: a ledger of what it has already run.

One small JSON file, atomically written. The companion is a client, not a
second store of Bartholomew's truth -- the authoritative record of what was
asked and what happened lives in `windows_action_requests` on the Bartholomew
side. What this file has to do is answer one question the server cannot answer
for it: *have I already executed this action on this machine?*

That question has to be answered locally because the two sides can disagree
about what happened. The server refuses a second lease of a non-repeatable
action, which handles a duplicate delivery. It does not handle the case where
this process executed an action, crashed before reporting the result, and was
restarted -- the server still sees the action as leased, and a companion
without a durable ledger would happily run it again on the way back up. With
the ledger, the restarted companion recognises the action id and reports what
it already did instead of doing it twice.

A corrupt or unreadable file is **not** treated as an empty ledger. An empty
ledger means "I have run nothing", which would re-enable exactly the double
execution the file exists to prevent. Instead the companion refuses every
non-repeatable action until an operator clears the file deliberately -- a
visible failure rather than a silent repeat.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: How many executed action ids to keep. Bounded so the file cannot grow
#: without limit on a long-running install; oldest are dropped first. Far more
#: than any plausible in-flight window, so dropping an entry can only happen
#: long after the action's own expiry has already refused it server-side.
MAX_LEDGER_ENTRIES = 2000


class LedgerUnreadableError(RuntimeError):
    """The executed-action ledger could not be read. Refuse everything.

    Its own type so the fail-closed branch is greppable, and so it can never
    be mistaken for "the ledger is empty".
    """


@dataclass
class ExecutedEntry:
    """One action this machine has already run, and what it reported."""

    action_id: str
    status: str
    observed_at: str
    reported: bool = False


@dataclass
class ActionCompanionState:
    """What survives an action-companion restart."""

    executed: dict[str, ExecutedEntry] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    def order(self) -> list[str]:
        return list(self.executed)


class ActionStateFile:
    """Load/save `ActionCompanionState`, atomically. Fails closed on corruption."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    def load(self) -> ActionCompanionState:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            # A genuinely absent file is a genuinely fresh install. That is the
            # one case where an empty ledger is the truth.
            return ActionCompanionState()
        except (OSError, ValueError) as e:
            raise LedgerUnreadableError(
                f"the executed-action ledger at {self.path} could not be read "
                f"({type(e).__name__}). Every non-repeatable action is refused until "
                "it can be, because an unreadable ledger is not an empty one and "
                "treating it as empty would risk running an action twice. Delete the "
                "file deliberately to start a new ledger.",
            ) from e
        if not isinstance(raw, dict):
            raise LedgerUnreadableError(
                f"the executed-action ledger at {self.path} is not an object",
            )
        executed: dict[str, ExecutedEntry] = {}
        for action_id, entry in (raw.get("executed") or {}).items():
            if not isinstance(entry, dict):
                raise LedgerUnreadableError(
                    f"the ledger entry for {action_id!r} is malformed",
                )
            executed[str(action_id)] = ExecutedEntry(
                action_id=str(action_id),
                status=str(entry.get("status", "unknown")),
                observed_at=str(entry.get("observed_at", "")),
                reported=bool(entry.get("reported", False)),
            )
        return ActionCompanionState(
            executed=executed,
            updated_at=float(raw.get("updated_at", 0.0) or 0.0),
        )

    def save(self, state: ActionCompanionState) -> None:
        """Write via a temp file and rename, so a crash never leaves a half file."""
        state.updated_at = time.time()
        if len(state.executed) > MAX_LEDGER_ENTRIES:
            for action_id in state.order()[: len(state.executed) - MAX_LEDGER_ENTRIES]:
                state.executed.pop(action_id, None)
        payload: dict[str, Any] = {
            "executed": {
                action_id: {
                    "status": entry.status,
                    "observed_at": entry.observed_at,
                    "reported": entry.reported,
                }
                for action_id, entry in state.executed.items()
            },
            "updated_at": state.updated_at,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent),
            prefix=".action-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
