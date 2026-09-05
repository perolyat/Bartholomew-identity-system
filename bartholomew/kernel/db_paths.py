"""Where the kernel database is. One answer, read fresh, shared by every surface.

The running API server, the kernel daemon and the operator CLI must all name
the *same* file when they say "the database", or a control written on one
surface is silently invisible on another. The live Windows test found exactly
that: `bartholomew brake on` printed "ENGAGED" against a scratch file the
server had never opened, while the server -- which honours `BARTH_DB_PATH` --
carried on dispatching. An emergency stop that appears to work and does
nothing is the worst shape a safety control can take, so the resolution now
lives in one place and every surface delegates to it.

Resolution order, first match wins:

1. An explicit path the caller was given (an operator's `--db`). Explicit
   always wins: tests address per-test databases while a session-wide
   `BARTH_DB_PATH` is set, and a per-user runtime process sets the variable
   for itself, so a shell must retain a way to name a specific file.
2. `BARTH_DB_PATH`, as-is. Not normalised: `platform.exposure` compares it
   resolved against the bound user's database and must keep matching.
3. `<project root>/data/barth.db`, the project root being the nearest parent
   of this file holding `pyproject.toml`.
4. `<cwd>/data/barth.db` when no project root can be found (an installed
   wheel with no source tree).

Read fresh on every call, never cached at module scope: a frozen constant is
the defect `bartholomew_api_bridge_v0_1/services/api/db.py` documents from
PR #38, where whichever test module imported first won the path for the whole
session.

This is the *kernel* database. The platform control plane (accounts, devices,
trusted groups) is resolved separately in `bartholomew.platform.store`, and
the two are deliberately not folded together: one variable naming both would
let a misconfiguration of one silently redirect the other.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The environment variable the kernel database is configured through.
KERNEL_DB_PATH_ENV = "BARTH_DB_PATH"

#: The basename every default resolves to. Named once so no surface can drift
#: back to a differently spelled file.
DEFAULT_DB_BASENAME = "barth.db"


def find_project_root() -> Path:
    """The nearest ancestor of this file that holds `pyproject.toml`, else cwd."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def default_kernel_db_path() -> str:
    """Steps 3-4 of the resolution order: the path used when nothing names one."""
    return str(find_project_root() / "data" / DEFAULT_DB_BASENAME)


def resolve_kernel_db_path(explicit: str | None = None, *, create_parent: bool = True) -> str:
    """The kernel database path this process should use. See the module note.

    `create_parent` makes the parent directory exist, which is what a server
    about to open the file wants; a read-only inspection can pass False. The
    guard on an empty dirname matters: a bare `--db barth.db` has no parent
    and `os.makedirs("")` raises.
    """
    if explicit is not None and str(explicit).strip():
        path = str(explicit)
    else:
        env = (os.getenv(KERNEL_DB_PATH_ENV) or "").strip()
        path = env or default_kernel_db_path()
    if create_parent:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    return path


def describe_kernel_db_path(explicit: str | None = None) -> dict[str, str | bool]:
    """How the path was arrived at -- for an operator surface that must say
    which file it is about to touch, and why that one."""
    env = (os.getenv(KERNEL_DB_PATH_ENV) or "").strip()
    if explicit is not None and str(explicit).strip():
        source = "explicit"
    elif env:
        source = KERNEL_DB_PATH_ENV
    else:
        source = "default"
    return {
        "path": resolve_kernel_db_path(explicit, create_parent=False),
        "source": source,
        "env_set": bool(env),
    }


__all__ = [
    "DEFAULT_DB_BASENAME",
    "KERNEL_DB_PATH_ENV",
    "default_kernel_db_path",
    "describe_kernel_db_path",
    "find_project_root",
    "resolve_kernel_db_path",
]
