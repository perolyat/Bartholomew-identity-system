"""
External request admission gate.

Phase B, stage B7. Prevents shutdown from racing externally admitted
governed work: an in-flight HTTP request that's partway through
`run_chat_through_runtime_contract()` (or any other Governance-gated call
into the daemon) when `KernelDaemon.stop()` starts tearing down `mem`,
`skill_registry`, and the scheduler must not be silently abandoned
mid-call. `docs/PHASE_B_OVERVIEW.md`'s B7 scope: "actual external-ingress
inventory; identity-bound admission; exact release ownership; request
cancellation; child and detached work; admission freeze and drain."

Two-phase shutdown protocol, mirroring the confirmed-not-assumed-drain
pattern `DedicatedDbExecutor.close()` (B2) and `SchedulerStore.close()`
already established:
  1. freeze() -- stop accepting new admissions. Every admit() call after
     this point raises AdmissionFrozenError immediately (no new work
     starts racing the teardown that's about to begin).
  2. drain(timeout) -- bounded wait for every already-admitted piece of
     work to release. Returns True if the wait actually confirmed zero
     work in flight, False if the bound was exceeded -- a False return is
     never swallowed by callers; it's the same "abandoned, not
     force-killed" signal B2/B5 already use, since this gate has no
     mechanism to forcibly cancel a request handler's own task (that's the
     ASGI server's concern, not this module's).

"Identity-bound admission": each admission is tracked by its own unique
AdmissionToken (an opaque per-request id assigned by admit(), not an
authenticated end-user identity -- this codebase has no such identity
system for HTTP callers per docs/B0_BASELINE_REPORT.md's ingress
inventory), and release() is only ever a method on that specific token --
there is no "release by id" call that could accidentally release someone
else's admission. Double-release is idempotent, matching this module's own
DedicatedDbExecutor.close() convention.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

logger = logging.getLogger(__name__)


class AdmissionFrozenError(RuntimeError):
    """Raised by admit() once freeze() has been called -- the caller should
    treat this the same as "server shutting down, try again elsewhere"
    (e.g. an HTTP 503), not retry against this same gate."""


class AdmissionToken:
    """
    Returned by AdmissionGate.admit(). release() is the only way to give
    back the admission slot it represents, and is idempotent -- safe to
    call more than once (e.g. once from the happy path and once from a
    `finally` block that runs regardless).
    """

    def __init__(self, gate: AdmissionGate, identity: str):
        self._gate = gate
        self.identity = identity
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._gate._on_release()

    def __repr__(self) -> str:
        return f"AdmissionToken({self.identity!r}, released={self._released})"


class AdmissionGate:
    """One gate per admitting process (e.g. one per FastAPI app instance).
    Not itself SQLite- or HTTP-specific -- admit()'s identity is caller-
    supplied, so this is reusable for any external-ingress surface, not
    only the HTTP routes this stage actually wires it into."""

    def __init__(self, name: str = "default"):
        self.name = name
        self._count = 0
        self._frozen = False
        self._lock = asyncio.Lock()
        self._drained_event = asyncio.Event()
        self._drained_event.set()  # vacuously true: nothing admitted yet

    async def admit(self, identity: str | None = None) -> AdmissionToken:
        """
        Register one piece of externally admitted work. Raises
        AdmissionFrozenError if freeze() has already been called -- callers
        MUST NOT proceed with the work they were about to do if this
        raises.
        """
        async with self._lock:
            if self._frozen:
                raise AdmissionFrozenError(
                    f"AdmissionGate({self.name!r}) is frozen -- no new admissions accepted",
                )
            self._count += 1
            self._drained_event.clear()
        return AdmissionToken(self, identity or uuid.uuid4().hex)

    async def _on_release(self) -> None:
        async with self._lock:
            self._count -= 1
            if self._count <= 0:
                self._count = 0  # defensive floor; release() is idempotent
                # per-token, so this should never actually go negative
                self._drained_event.set()

    async def freeze(self) -> None:
        """Idempotent. Stops accepting new admissions; does not itself wait
        for existing ones -- call drain() afterward for that."""
        async with self._lock:
            self._frozen = True

    async def drain(self, timeout: float = 30.0) -> bool:
        """
        Bounded wait for in_flight_count to reach zero. Returns True if
        confirmed drained within `timeout`, False otherwise (logged here;
        callers must not assume "abandoned" means "gone" -- see this
        module's docstring). Safe to call whether or not freeze() was
        called first, though calling it first is what actually prevents
        the count from growing again while draining.
        """
        try:
            await asyncio.wait_for(self._drained_event.wait(), timeout=timeout)
            return True
        except TimeoutError:
            logger.warning(
                "AdmissionGate(%r).drain(): %d admission(s) still in flight after %.1fs; "
                "proceeding without confirmed drain",
                self.name,
                self._count,
                timeout,
            )
            return False

    @property
    def in_flight_count(self) -> int:
        return self._count

    @property
    def frozen(self) -> bool:
        return self._frozen
