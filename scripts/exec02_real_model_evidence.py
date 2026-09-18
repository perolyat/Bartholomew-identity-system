#!/usr/bin/env python3
"""
EXEC-02 real-model evidence: one benign outcome-level goal, through the real
conversational path, on the real production composition.

This is not a test and does not assert. It runs the *shipped* wiring — the real
`Orchestrator`/`ModelRouter`, the real `configure_from_environment` activation
that `app.py` calls on startup, the real `run_chat_through_runtime_contract()`
chat turn, the real Executive seam, the real capability catalogue, the real
device allowlists, the real action envelope and the real Parking Brake — and
prints, step by step, exactly where the request got to and where it stopped.

Nothing here weakens governance to make the demonstration easier. In
particular, it does not approve, authorise or execute anything: a successful
run ends at `pending_approval`, which is the correct stopping point and the
whole point of printing it.

Run it
------
    # 1. Enrol the machine you want the goal planned against, then:
    export BARTH_EXECUTIVE_DELIBERATION=1
    export BARTH_CONVERSATIONAL_EXECUTIVE=1
    export BARTH_CONVERSATIONAL_EXECUTIVE_DEVICE_ID=<your enrolled device id>
    python scripts/exec02_real_model_evidence.py

What each outcome means
-----------------------
* ``proposed`` — the full chain worked: a model turned the goal into steps, the
  steps survived real validation against the real device, and the first one is
  waiting for a human approval. **This is the real-model proof.**
* ``clarification_requested`` with ``deliberated: false`` — the chain is wired
  but no model could answer (no Ollama, no cloud key, the ``stub`` backend).
  The Executive fell back to the literal reading and asked a question. That is
  the correct degradation, and it is evidence of the *plumbing*, not of plan
  quality.
* ``parking_brake_denied`` — the brake is engaged. Release it and re-run.
* ``refused`` — the device is not enrolled, or the goal asked for something
  outside the capability vocabulary.

The environment this was first run in had no Ollama and no cloud key, so it
produced the second outcome. It is committed so the first outcome can be
produced on a machine that has a model, without rebuilding the harness.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

DEFAULT_GOAL = "Start a shopping list for me."


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal", default=DEFAULT_GOAL, help="the benign outcome-level request")
    parser.add_argument("--db", default=None, help="database path (defaults to the real one)")
    args = parser.parse_args()

    from bartholomew.integration import conversational_executive as conv_exec
    from bartholomew.kernel import goal_intents
    from bartholomew.kernel.daemon import KernelDaemon
    from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract
    from identity_interpreter.orchestrator.orchestrator import Orchestrator

    report: dict[str, object] = {"goal": args.goal}

    # --- Step 1: is this even recognised as a goal? Pure, no I/O. -----------
    intent = goal_intents.parse_intent(args.goal)
    report["recognised_as_goal"] = intent is not None
    if intent is not None:
        report["referent"] = intent.referent
        report["trigger"] = intent.trigger

    # --- Step 2: the real kernel, as app.py builds it. ----------------------
    kernel = KernelDaemon(
        cfg_path="config/kernel.yaml",
        db_path=args.db or os.getenv("BARTH_DB_PATH") or "data/barth.db",
        persona_path="config/persona.yaml",
        policy_path="config/policy.yaml",
        drives_path="config/drives.yaml",
        identity_path="Identity.yaml",
    )
    await kernel.start()

    try:
        # --- Step 3: the real router, and the real explicit activation. ----
        orch = Orchestrator(model_identity_config=getattr(kernel, "identity", None))
        router = getattr(orch, "router", None)
        report["model_backend"] = (
            router.config.get("default_backend") if router is not None else None
        )
        report["activation"] = conv_exec.configure_from_environment(kernel, router)

        async def respond(prompt: str) -> str:
            return await asyncio.to_thread(orch.handle_input, prompt, skip_governance_check=True)

        # --- Step 4: one ordinary chat turn. Nothing operator-only. --------
        result = await run_chat_through_runtime_contract(kernel, args.goal, respond)

        report["governance_allowed"] = result.governance_allowed
        report["executive_action"] = result.executive_action
        report["reply"] = result.response
        report["reached_the_executive"] = result.executive_action is not None
        action_ids = (result.executive_action or {}).get("proposed_action_ids") or []
        report["stopped_at"] = (
            "pending_approval (nothing ran)"
            if action_ids
            else "nothing was proposed; see executive_action.outcome"
        )
    finally:
        await kernel.stop()

    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
