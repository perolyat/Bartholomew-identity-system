"""The Executive's cognition port, reached through Bartholomew's existing model router.

An adapter, like every module in this package: it connects a consumer that
declared a seam (`bartholomew/executive/deliberation.py`'s `DeliberationPort`)
to the producer that already exists (`identity_interpreter`'s `ModelRouter`). It
owns no policy, no store, no authority and no vocabulary.

Why the adapter lives here and not in the executive
----------------------------------------------------
`tests/test_w03b_no_bypass.py` reads `bartholomew/executive/`'s syntax tree and
fails if any module there imports `socket`, `http`, `urllib`, `requests`, `httpx`
or `aiohttp`. A model client is exactly such a thing. So the executive declares a
one-method `Protocol` and never implements it, and the implementation lives out
here where an adapter belongs --- which is the same separation `eci_responder.py`
makes for the voice seam, and for the same reason.

The consequence worth stating: **the executive cannot reach a model on its own.**
It can only use one that a caller handed it. A deployment that wires nothing gets
the deterministic recogniser and today's behaviour.

What this adapter is allowed to do, and what it is not
-------------------------------------------------------
It passes one bounded string to `ModelRouter.route()` and returns the string that
comes back. That is the whole of it. In particular:

* **It does not interpret the answer.** Parsing, validation against the closed
  capability vocabulary, the device's declarations and the real parameter
  validators all happen inside `deliberation.py`, downstream of here. This module
  could not turn a model's text into an action if it tried, because it has no
  code that produces a step.
* **It does not retry.** A provider failure is a failure. The executive degrades
  to a question, which is the honest outcome; a retry loop here would turn a
  provider outage into latency and then into the same question anyway.
* **It does not substitute text of its own.** `ModelRouter` was deliberately
  changed (2026-08-15) to raise `ModelBackendError` rather than return mock text,
  so that no caller could mistake an outage for an answer. This adapter holds the
  same line: it raises, and never invents a plan.

The `stub` backend
-------------------
`ModelRouter` answers a `stub` backend with `"[model] Mock response for prompt:
..."`. That string is not JSON, so `parse_deliberation` refuses it and the person
is asked a question --- which is correct but wasteful and, more importantly,
indistinguishable in a log from a real model answering badly. This adapter
therefore refuses the stub backend outright and says so, so that "no model is
actually configured" is a legible state rather than a puzzling one.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The routing hint recorded on every deliberation request. Not a policy and not
#: a permission --- `ModelRouter` chooses the backend --- but it means a
#: deliberation is identifiable in whatever the router records.
DELIBERATION_TASK_TYPE = "executive_deliberation"

#: The mock-text marker `ModelRouter` returns for its `stub` backend. Matched so
#: the state can be named; never parsed, and never allowed through as an answer.
_STUB_MARKER = "Mock response for prompt:"


class DeliberationUnavailableError(RuntimeError):
    """No model could produce a deliberation, and none was invented.

    Its own type so a caller can tell "the cognition layer is not configured"
    apart from "the cognition layer answered badly". `deliberate_task` catches
    both identically --- it falls back to the deterministic reading either way
    --- but an operator reading a log is owed the difference.
    """


class ModelRouterDeliberationPort:
    """`DeliberationPort` over Bartholomew's existing provider-neutral router.

    Satisfies `bartholomew.executive.deliberation.DeliberationPort` structurally
    rather than by inheritance, so the executive keeps no import edge to this
    package and this module keeps none to a provider.

    `router` is anything with `route(dict) -> str`; in production it is
    `identity_interpreter.orchestrator.model_router.ModelRouter`, which already
    chooses between the local Ollama adapter and the opt-in, budget-capped cloud
    adapter. Choosing a provider is its job and stays its job: EXEC-01 adds no
    second router, no provider registry and no selection logic, which
    `CONSTITUTION.md`'s "Bartholomew employs an ecosystem; it does not become it"
    does not authorise and this work package did not need.
    """

    def __init__(self, router: Any, *, task_type: str = DELIBERATION_TASK_TYPE) -> None:
        self._router = router
        self._task_type = task_type

    def deliberate(self, prompt: str) -> str:
        """One bounded prompt in, the model's raw text out. Blocking by design.

        The executive calls this on a worker thread (`seam._understand` runs it
        through `run_off_loop`), so a slow provider costs latency on one task
        rather than the event loop.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise DeliberationUnavailableError("refusing to ask a model to reason about nothing")

        try:
            answer = self._router.route(
                {
                    "prompt": prompt,
                    "task_type": self._task_type,
                    # The router overwrites the system message with the canonical
                    # identity projection and raises if it cannot build one. That
                    # is its contract and this adapter does not work around it:
                    # everything cognition needs is already inside `prompt`,
                    # which `deliberation.build_prompt` assembled.
                },
            )
        except Exception as error:  # noqa: BLE001 - every provider failure is one failure
            raise DeliberationUnavailableError(
                f"the model backend could not deliberate: {error}",
            ) from (error)

        if not isinstance(answer, str):
            raise DeliberationUnavailableError(
                f"the model backend returned {type(answer).__name__}, not text",
            )
        if _STUB_MARKER in answer:
            raise DeliberationUnavailableError(
                "the stub backend is selected, so no model is actually configured for "
                "deliberation. The executive will read instructions literally.",
            )
        return answer


def install_deliberation_port(ctx: Any, router: Any) -> ModelRouterDeliberationPort | None:
    """Hang a cognition port on a runtime context, or leave it without one.

    Deliberately explicit, and deliberately not automatic on startup. Giving
    Bartholomew the ability to reason his way to a course of action on somebody's
    computer is a decision an operator makes, not a side effect of a model
    happening to be reachable --- so a deployment opts in by calling this, and
    one that does not keeps the deterministic recogniser.

    Returns the installed port, or None when there was no router to install.
    """
    if router is None:
        logger.info("No model router available; the executive will read instructions literally.")
        return None
    port = ModelRouterDeliberationPort(router)
    ctx.deliberation_port = port
    return port


__all__ = [
    "DELIBERATION_TASK_TYPE",
    "DeliberationUnavailableError",
    "ModelRouterDeliberationPort",
    "install_deliberation_port",
]
