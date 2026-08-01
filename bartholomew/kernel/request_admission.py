"""
Identity-bound external request admission (Phase B stage B7).

Tracks externally admitted work (real HTTP requests reaching the daemon's
governed state) so KernelDaemon.stop() can wait for it to finish naturally
instead of tearing resources out from under it -- closing the gap
docs/PHASE_B_OVERVIEW.md Sec 4's shutdown invariant names explicitly:
"externally admitted governed work cannot be silently dropped by
shutdown."

Identity-bound by design (not a bare counter): docs/PHASE_B_RISK_MAP.md's
B7 rows name a real prior-design finding -- a release() with no identity
argument lets any caller release any in-flight admission, regardless of
which one it actually held. try_admit() returns a fresh, unguessable
token; release(token) removes only that exact token if present, and is a
safe no-op for a foreign, duplicate, or already-released one.

Deliberately narrow: this module knows nothing about HTTP, FastAPI, or
the daemon's own lifecycle state -- it is a plain admit/release/drain
primitive, the same "storage/transport-agnostic reusable primitive"
shape blocking_executor.py and process_lock.py already established.
Callers (the app.py HTTP middleware, KernelDaemon.stop()) own deciding
*when* admission should be open or closed.
"""

from __future__ import annotations

import asyncio
import time
import uuid


class RequestAdmission:
    """One instance per KernelDaemon, owning the set of currently
    in-flight externally-admitted requests."""

    def __init__(self) -> None:
        self._active: set[str] = set()
        self._closed = False

    def try_admit(self) -> str | None:
        """Admit one unit of external work, returning a fresh token to
        release later -- or None if admission is closed (shutdown has
        begun), which callers must treat as a refusal, not a silent
        pass-through."""
        if self._closed:
            return None
        token = uuid.uuid4().hex
        self._active.add(token)
        return token

    def release(self, token: str | None) -> None:
        """Release exactly the given token. A no-op for None, a foreign
        token, or a token already released -- never affects any other
        admission, closing the identity-confusion gap this module's
        docstring names."""
        if token is None:
            return
        self._active.discard(token)

    def close(self) -> None:
        """Stop admitting new work. Idempotent. Does not affect already-
        admitted tokens -- see drain() to wait for those."""
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def admitted_count(self) -> int:
        return len(self._active)

    async def drain(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds for every currently-admitted
        token to be released. Returns True if fully drained, False if
        the timeout elapsed with admissions still outstanding -- the
        caller (KernelDaemon.stop()) must treat False as "not confirmed
        clean," matching Phase B stage B5's "verified shutdown, not
        assumed shutdown" invariant, not silently proceed as if nothing
        was still in flight."""
        deadline = time.monotonic() + timeout
        while self._active and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        return not self._active
