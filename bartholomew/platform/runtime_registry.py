"""
Identity -> runtime resolution: the one function cross-user isolation rests on.

Under the approved Alpha model each personal Bartholomew is an **isolated
runtime with its own database file, its own data directory and its own
keyring namespace**, addressed by a server-generated `user_id`. Isolation is
therefore enforced by the process and filesystem boundary, not by every
future query remembering a `WHERE user_id = ?` predicate. That was the
explicit instruction, and it is also the only version of this that a
41-table schema with FTS mirrors and an embedding index can be trusted to
hold: a missed predicate is a silent leak, a wrong path is a loud failure.

This module is deliberately small and dependency-light. It is the piece to
read first when auditing whether user A can reach user B's data, so it must
be readable in one sitting.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .principal import AuthenticationError, Principal

DATA_ROOT_ENV = "BARTH_DATA_ROOT"
KEYRING_SERVICE_ENV = "BARTHO_MEMORY_KEYRING_SERVICE"

# `user_id` values are server-generated UUID4 (see accounts.create_account).
# This pattern is defence in depth, not the primary control: it exists so
# that a `user_id` reaching this module from anywhere unexpected -- a
# hand-edited row, a future import path, a bug that lets input through --
# cannot contain a path separator or `..` and escape its own directory.
# Path traversal here would defeat every other isolation property at once.
_USER_ID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z",
)


class RuntimeResolutionError(Exception):
    """
    A principal could not be mapped to an isolated runtime.

    Never falls back to a default or shared runtime. "I could not work out
    whose Bartholomew this is" must end the request, because the only
    alternatives are serving someone else's data or inventing a new identity.
    """


@dataclass(frozen=True)
class RuntimeHandle:
    """
    Everything that addresses one personal Bartholomew's isolated state.

    Frozen, and carries no live connection: it is an address, not a session.
    Anything that needs per-user state derives it from these fields, so
    adding a new persistence surface means adding a field here -- which makes
    "did we isolate the new store?" a question with a visible answer.
    """

    user_id: str
    data_dir: Path
    db_path: str
    keyring_service: str

    @property
    def vector_dir(self) -> Path:
        return self.data_dir / "vectors"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"


def _validate_user_id(user_id: str) -> str:
    if not user_id or not _USER_ID_RE.match(user_id):
        raise RuntimeResolutionError(
            "user_id is not a well-formed identifier; refusing to derive a data path from it",
        )
    return user_id


def data_root() -> Path:
    root = os.getenv(DATA_ROOT_ENV)
    base = Path(root) if root else Path(__file__).resolve().parents[2] / "data"
    return base


def runtime_handle_for_user_id(user_id: str) -> RuntimeHandle:
    """
    Derive the isolated runtime address for a `user_id`.

    Pure and side-effect free apart from directory creation. Deliberately
    takes a bare id rather than a `Principal` so it can be used by operator
    tooling (account provisioning, deletion) without fabricating a principal
    -- fabricating principals is exactly the habit this package exists to
    prevent.
    """
    user_id = _validate_user_id(user_id)
    user_dir = (data_root() / "users" / user_id).resolve()

    # Belt and braces against the pattern check above: whatever path
    # manipulation produced `user_dir`, it must still sit inside the users
    # root after resolution.
    users_root = (data_root() / "users").resolve()
    if users_root != user_dir.parent:
        raise RuntimeResolutionError("resolved user data directory escaped the data root")

    user_dir.mkdir(parents=True, exist_ok=True)
    return RuntimeHandle(
        user_id=user_id,
        data_dir=user_dir,
        db_path=str(user_dir / "barth.db"),
        # Namespaced per user so one participant's memory encryption key is
        # not readable under the key name another participant's runtime uses.
        keyring_service=f"bartholomew_memory:{user_id}",
    )


def runtime_handle_for(principal: Principal) -> RuntimeHandle:
    """
    Resolve a **verified** principal to its runtime.

    The type signature is the point: it takes a `Principal`, which can only
    be constructed from a verified session, so there is no way to reach a
    runtime from a caller-supplied string. A platform administrator has no
    personal runtime and is refused here rather than being silently given
    one -- administration is not a personal Bartholomew.
    """
    if principal.is_platform_admin:
        raise RuntimeResolutionError(
            "a platform administrator has no personal Bartholomew runtime",
        )
    return runtime_handle_for_user_id(principal.user_id)


def assert_runtime_matches(handle: RuntimeHandle, active_db_path: str | None) -> None:
    """
    Fail closed when the running kernel is not the resolved principal's.

    The Alpha deployment serves one personal runtime per process behind the
    shared control plane. If the kernel this process is running is not the
    one the authenticated principal resolves to, the correct response is to
    refuse -- **not** to serve whatever runtime happens to be loaded. That
    substitution is precisely the cross-user data disclosure S8 exists to
    prevent, and it is the failure mode a "one global `_kernel`" codebase
    falls into naturally.
    """
    if active_db_path is None:
        raise AuthenticationError("no runtime is active for this identity")
    if Path(active_db_path).resolve() != Path(handle.db_path).resolve():
        raise AuthenticationError(
            "authenticated identity does not match the runtime served by this process",
        )


def apply_runtime_environment(handle: RuntimeHandle) -> None:
    """
    Point process-level, path-driven configuration at one user's runtime.

    Used when a process is launched to serve a specific personal Bartholomew.
    It sets the existing environment variables the kernel and memory manager
    already read, rather than introducing a parallel configuration channel --
    so per-user isolation reuses the seams the repository already has instead
    of adding new ones for other streams to discover.
    """
    os.environ["BARTH_DB_PATH"] = handle.db_path
    os.environ["BARTHO_DB_PATH"] = handle.db_path
    os.environ[KEYRING_SERVICE_ENV] = handle.keyring_service
