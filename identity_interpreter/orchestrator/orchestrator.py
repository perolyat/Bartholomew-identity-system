"""
Orchestrator
------------
Central controller coordinating input, memory, and model routing.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context_builder import ContextBuilder
from .model_router import ModelRouter
from .pipeline import Pipeline
from .response_formatter import ResponseFormatter
from .state_manager import StateManager


class Orchestrator:
    """Central controller coordinating input, memory, and model routing."""

    def __init__(self, log_dir: str = "logs/orchestrator", identity_config: Any = None):
        """
        Initialize the orchestrator.

        Args:
            log_dir: Directory for orchestration trace logs
            identity_config: Optional identity configuration for memory
        """
        self.pipeline = Pipeline()
        self.context = ContextBuilder(identity_config)
        self.state = StateManager()
        self.router = ModelRouter(identity_config=identity_config)
        self.formatter = ResponseFormatter()

        # Setup logging
        self.log_dir = Path(log_dir)
        self._setup_logging()

        # Configure pipeline steps
        self.pipeline.add_step(self._log_step("inject_memory_context"))
        self.pipeline.add_step(self.inject_memory_context)
        self.pipeline.add_step(self._log_step("route_model"))
        self.pipeline.add_step(self.route_model)
        self.pipeline.add_step(self._log_step("format_response"))
        self.pipeline.add_step(self.format_response)

    def _setup_logging(self):
        """Setup logging directory and file."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "orchestrator.log"

    def _log_event(self, step: str, event: str, duration_ms: float = None, **kwargs):
        """
        Log an orchestration event in JSONL format.

        Args:
            step: Pipeline step name
            event: Event type (start, end, error)
            duration_ms: Optional duration in milliseconds
            **kwargs: Additional event data
        """
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": self.state.session_id,
            "step": step,
            "event": event,
        }

        if duration_ms is not None:
            log_entry["duration_ms"] = round(duration_ms, 2)

        log_entry.update(kwargs)

        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception:
            # Fail silently to not disrupt orchestration
            pass

    def _log_step(self, step_name: str):
        """
        Create a wrapper that logs timing for a pipeline step.

        Args:
            step_name: Name of the step to log

        Returns:
            Wrapper function that adds timing logs
        """

        def wrapper(data: dict[str, Any]) -> dict[str, Any]:
            self._log_event(step_name, "start")
            start_time = time.time()

            try:
                result = data
                duration_ms = (time.time() - start_time) * 1000
                self._log_event(
                    step_name,
                    "end",
                    duration_ms=duration_ms,
                    input_len=len(str(data.get("user_input", ""))),
                    output_len=len(str(data.get("response", ""))),
                )
                return result
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                self._log_event(step_name, "error", duration_ms=duration_ms, error=str(e))
                raise

        return wrapper

    def handle_input(self, user_input: str) -> str:
        """
        Process user input through the orchestration pipeline.

        Args:
            user_input: Raw user input string

        Returns:
            Formatted response string
        """
        # Parking brake gate for skills scope
        brake_blocked = False
        try:
            from bartholomew.kernel.daemon import _default_db_path
            from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

            storage = BrakeStorage(_default_db_path())
            brake = ParkingBrake(storage)
            brake_blocked = brake.is_blocked("skills")
        except (ImportError, Exception):
            # Parking brake module not available or schema not initialized
            # Continue normally
            pass

        if brake_blocked:
            raise RuntimeError("ParkingBrake: skills blocked")

        self._log_event("orchestrator", "start", user_input_len=len(user_input))
        start_time = time.time()

        try:
            data = {"user_input": user_input, "session_id": self.state.session_id}
            result = self.pipeline.execute(data)
            duration_ms = (time.time() - start_time) * 1000

            self._log_event(
                "orchestrator",
                "end",
                duration_ms=duration_ms,
                response_len=len(result.get("response", "")),
            )

            return result.get("response", "No response generated.")
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self._log_event("orchestrator", "error", duration_ms=duration_ms, error=str(e))
            raise

    # --- Pipeline Steps ---

    def inject_memory_context(self, data: dict[str, Any]) -> dict[str, Any]:
        """Inject memory context into the data."""
        data["prompt"] = self.context.inject_context(data["user_input"], data["session_id"])
        return data

    def route_model(self, data: dict[str, Any]) -> dict[str, Any]:
        """Route to model and get response."""
        route = self.router.select_route(data)
        data["llm_output"] = self.router.route(data)
        data["route"] = route

        self._log_event("route_model", "routed", backend=route["backend"], model=route["model"])
        return data

    def format_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """Format the final response."""
        tone = self.state.get("tone")
        emotion = self.state.get("emotion")

        data["response"] = self.formatter.format(data["llm_output"], tone=tone, emotion=emotion)
        return data
