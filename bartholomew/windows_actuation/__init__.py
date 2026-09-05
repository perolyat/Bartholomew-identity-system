"""Bartholomew's Windows action companion: the governed actuation boundary.

A separate process from the observation companion, with separate credentials,
a separate HTTP boundary and a separate trust channel. It leases typed,
already-governed action requests for one enrolled device, dispatches each
through a closed table of nine handlers, and reports back a typed result --
including `unknown`, when the honest answer is that the effect could not be
observed.

**Read four files and you have read what this can do to a computer:**

* `bartholomew/actuation/capabilities.py` -- the nine capability kinds.
* `bartholomew/actuation/parameters.py`   -- their exact, closed parameters.
* `win32.py`                              -- every OS call, in one file.
* `handlers.py`                           -- the nine handlers.

**What is structurally absent**, not merely unimplemented, and asserted over
this package's source by `tests/test_windows_action_prohibitions.py`: shell
execution, PowerShell, Python or any other interpreter; arbitrary executable
paths (the launcher takes an allowlist *key*, and the process starter takes one
parameter with no argument vector); model-generated commands; reflection-based
dispatch; downloaded code; file deletion, movement or mutation; software
installation; sending a message; submitting a form; publishing; a purchase; an
account or security change; credential entry; control of any machine but the
enrolled one; and any non-Windows platform. There is no generic `command`,
`script`, `shell`, `args` or equivalent field anywhere in the wire format.

**It cannot be reached through the observation path.** `bartholomew/companion/`
submits observations one way and its client has a single `submit` verb that
returns three scalars; nothing it can receive reaches this package, and neither
package imports the other.
"""

from .config import ACTION_COMPANION_VERSION

__all__ = ["ACTION_COMPANION_VERSION"]
