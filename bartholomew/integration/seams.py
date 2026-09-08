"""The Wave 3 cross-package adapters, and the one place to look for them.

`install.py` composes the wave-two seams. This module holds the W03 ones: the
adapters that exist **only** because two frozen packages describe the same
thing with different shapes, and neither may import the other.

There is exactly one such adapter today, and it is here rather than in either
package for a reason each package states itself:

* `bartholomew/actuation/` must not import `bartholomew/multimodal/`
  (W03-C handoff §7.2: "W03-C deliberately built the socket and not the
  plug"). The action envelope cannot depend on perception.
* `bartholomew/multimodal/` does not know the envelope exists, and must not:
  observation and actuation are separate channels on purpose, and a read-back
  that imported the action package would be one import away from an
  observation channel that could act.

So the plug is integration's, and it lives here.

Nothing in this module decides anything. Every governance question --- consent,
the Parking Brake, scope, identity --- is re-asked by W03-A's `read_back()` at
the moment of the read, and this module neither duplicates those checks nor
routes around them. What it does is translate one package's answer into the
other package's vocabulary without ever inventing a fact that was not observed.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MultimodalReadBackProvider:
    """W03-A's `read_back()` in the shape W03-C's `ReadBackProvider` asks for.

    W03-C's protocol is::

        read_back(*, tenant_id, device_id, target: str, expected: str | None) -> ReadBack

    W03-A's primitive is::

        read_back(*, tenant_id, device_id, target: CaptureScope | None, requested_by,
                  store, db_path, ...) -> ReadBackResult

    Three differences, and the honest resolution of each:

    **`target`.** They are not the same thing, despite the name. W03-C's caller
    passes `stored.capability` (`seam.py`, the `_maybe_verify` path) --- a
    *capability name* such as `windows.type_text`, not a window. W03-A's
    `target` is a `CaptureScope`: a display, a window handle or a rectangle.
    Coercing a capability name into a `CaptureScope` would invent a scope
    nobody consented to, so this adapter passes `target=None`, which is W03-A's
    documented "whatever the live session is consented to observe". The read is
    therefore bounded by the consent that already exists and **cannot widen
    it**: a session covering a different window still answers
    `outside_consented_scope`, and no session at all still answers
    `no_consented_session`.

    **`requested_by`.** W03-C's protocol does not carry the action's principal,
    so the adapter has none to forward and passes `None`. That is a narrowing,
    not a bypass: W03-A's principal check exists to stop a *content* principal
    (a model response, an inbound event, a companion observation) from causing
    a screen read, and this path is not a principal at all --- it is the
    envelope's own post-result verification step, reachable only after an
    action has already passed identity, capability, scope, risk and
    authorization. The read stays gated by the consented session, the Parking
    Brake and the scope, which W03-A re-checks on every call. Recorded in the
    W03-F handoff as a declared limitation rather than papered over: this
    adapter cannot exercise W03-A's principal refusal, and passing a fabricated
    principal to make it appear otherwise would be worse than passing none.

    **The return type.** `ReadBackResult` (rich, carries text) becomes
    `ReadBack` (digest and length only). The reduction happens in W03-C's own
    `ReadBack.from_text()` so observed screen text is never held by this module
    and never reaches an evidence row.

    The one rule this adapter enforces itself: **an unavailable or empty read
    is never translated into a comparison.** A read that produced nothing is
    "nothing is known", not evidence for or against the action --- so it comes
    back as an unavailable `ReadBack`, which `verify_effect()` turns into
    `UNVERIFIABLE`. That keeps the wave's truthfulness rule intact from both
    sides: nothing here can manufacture a confirmation, and nothing here can
    manufacture a contradiction out of a failed read either.
    """

    #: Shown on the seam report, so an operator can tell a live read-back path
    #: from an absent one without reading code.
    LABEL = "multimodal read-back (W03-A) -> action verification (W03-C)"

    def __init__(self, *, db_path: str | None = None) -> None:
        #: The same database the rest of the composition resolved. Passed
        #: through so the observation event the read-back emits is recorded
        #: where the server will look for it, rather than in whatever file a
        #: default happened to name --- the exact failure class W03-C's live
        #: test found with the CLI's `--db`.
        self._db_path = db_path

    def read_back(
        self,
        *,
        tenant_id: str,
        device_id: str,
        target: str,
        expected: str | None = None,
    ) -> Any:
        from bartholomew.actuation.verification import ReadBack
        from bartholomew.multimodal.readback import read_back as multimodal_read_back

        try:
            observed = multimodal_read_back(
                tenant_id=tenant_id,
                device_id=device_id,
                # Not `target`: see the class docstring. A capability name is
                # not a consented scope, and this must never invent one.
                target=None,
                requested_by=None,
                db_path=self._db_path,
            )
        except Exception as exc:  # noqa: BLE001 - a failed read observed nothing
            # `verify_effect` already treats an exception as unavailability;
            # returning the value rather than propagating keeps the reason.
            logger.warning(
                "The multimodal read-back raised while verifying %s: %s",
                target,
                type(exc).__name__,
            )
            return ReadBack.unavailable(
                f"the read-back raised {type(exc).__name__}, so nothing was observed"
            )

        if not getattr(observed, "available", False):
            code = getattr(observed, "code", None) or "unavailable"
            reason = getattr(observed, "reason", None) or "no reason given"
            return ReadBack.unavailable(f"{code}: {reason}")

        text = getattr(observed, "text", None)
        if not text:
            # Available with nothing read. Not a contradiction --- a comparison
            # against an empty string would report "the expected text is not
            # there", which is a claim about the screen that nobody observed.
            return ReadBack.unavailable(
                "the read-back reported success but returned no text, so nothing "
                "could be compared"
            )

        if getattr(observed, "provenance_degraded", False):
            # The read really happened; only its durable record did not. W03-A
            # already reports this honestly on its own result, and discarding a
            # real observation would be its own kind of untruth --- so it is
            # passed through and logged, and the W03-F handoff records that a
            # verdict reached this way has a weaker audit trail than usual.
            logger.warning(
                "Read-back for %s succeeded but its provenance is degraded (%s)",
                target,
                getattr(observed, "provenance_error", None),
            )

        return ReadBack.from_text(text, expected=expected)


def install_w03_seams(*, db_path: str | None = None) -> dict[str, str]:
    """Install the Wave 3 cross-package adapters. Returns what happened.

    Called from `install_seams()`, so there is still one composition entry
    point and one report. Every failure is reported rather than raised: a seam
    that could not be installed leaves the owning package's own fail-closed
    default in place, which for the read-back means `UNVERIFIABLE` --- safe,
    because it can never produce a confirmation.
    """
    report: dict[str, str] = {}

    # -- A -> C: the action envelope's server-side verify can observe ------
    #
    # Until this line runs, `verify_effect()` has no provider and every
    # server-side verdict is `UNVERIFIABLE` regardless of what the device
    # reported. That is safe but blind, and it is the state every builder
    # branch shipped in, because neither package may plug the other in.
    try:
        from bartholomew.actuation.verification import install_read_back_provider

        provider = MultimodalReadBackProvider(db_path=db_path)
        install_read_back_provider(provider)
        report["read_back_provider"] = provider.LABEL
    except Exception as e:  # noqa: BLE001 - a failed seam is reported, not fatal
        report["read_back_provider"] = f"not installed: {type(e).__name__}: {e}"
        logger.exception("Could not install the multimodal read-back provider")

    # -- B: the executive's tables exist before the first task -------------
    #
    # `executive_store` creates them idempotently from the seam, so this is
    # belt and braces rather than a fix (W03-B handoff §5.7). It is here so a
    # deployment's schema is complete after start-up rather than after whoever
    # happens to call the seam first, which is what the other stores already do.
    if not db_path:
        # `ensure_schema` takes a real path. Without one there is nothing to
        # create here, and the seam still creates the tables on first use.
        report["executive_schema"] = "not ensured: no database path was resolved"
    else:
        try:
            from bartholomew.executive.store import (
                ensure_schema as ensure_executive_schema,
            )

            ensure_executive_schema(db_path)
            report["executive_schema"] = "ensured (executive_tasks, executive_task_steps)"
        except Exception as e:  # noqa: BLE001
            report["executive_schema"] = f"not ensured: {type(e).__name__}: {e}"
            logger.exception("Could not ensure the executive schema")

    return report


__all__ = ["MultimodalReadBackProvider", "install_w03_seams"]
