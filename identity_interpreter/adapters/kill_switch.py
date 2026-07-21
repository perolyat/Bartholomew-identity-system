"""
Kill Switch
Emergency shutdown mechanism

DEPRECATED (2026-07-21, MASTER_PLAN.md "P2.5 -- Runtime Convergence" / item 11.1):
this is not the authoritative safety kill-switch. That's
`bartholomew.orchestrator.safety.parking_brake.ParkingBrake`, a persistent,
fail-closed brake actually wired into four live gate points (skill
execution, the scheduler, and both perception-adapter stubs). This class has
no live callers anywhere in the codebase today (it only prints status to
stdout and is not otherwise wired to anything) -- do not add new callers.
It will be removed once confirmed nothing else references it (see
DECISIONS.md's "One authority per architectural concept" entry).
"""

import warnings
from collections.abc import Callable


_DEPRECATION_MSG = (
    "identity_interpreter.adapters.kill_switch.KillSwitch is deprecated and "
    "unwired; the authoritative safety kill-switch is "
    "bartholomew.orchestrator.safety.parking_brake.ParkingBrake. See "
    "MASTER_PLAN.md's 'P2.5 -- Runtime Convergence' item 11.1."
)


class KillSwitch:
    """Emergency shutdown mechanism"""

    def __init__(self, identity_config):
        """
        Initialize kill switch

        Args:
            identity_config: Identity configuration object
        """
        warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        self.identity = identity_config
        self.enabled = identity_config.safety_and_alignment.controls.kill_switch.enabled
        self.safe_state = identity_config.safety_and_alignment.controls.kill_switch.safe_state
        self.shutdown_callback: Callable | None = None

    def register_callback(self, callback: Callable) -> None:
        """
        Register shutdown callback

        Args:
            callback: Function to call on shutdown
        """
        self.shutdown_callback = callback

    def trigger(self, reason: str = "Manual activation") -> None:
        """
        Trigger emergency shutdown

        Args:
            reason: Reason for shutdown
        """
        if not self.enabled:
            print("WARNING: Kill switch is disabled")
            return

        print("\n!!! KILL SWITCH ACTIVATED !!!")
        print(f"Reason: {reason}")
        print(f"Entering safe state: {self.safe_state}")

        # Execute callback if registered
        if self.shutdown_callback:
            self.shutdown_callback(reason)

        # Enter safe state based on configuration
        if "stop_tool_use" in self.safe_state:
            print("- Tool use stopped")

        if "local_read_only" in self.safe_state:
            print("- Switched to read-only mode")

        if "network" in self.safe_state:
            print("- Network access disabled")

        print("\nSystem in safe mode. Manual intervention required.")

    def test(self) -> bool:
        """
        Test kill switch functionality

        Returns:
            True if test passes
        """
        if not self.enabled:
            print("Kill switch test FAILED: Switch is disabled")
            return False

        print("Kill switch test: OK")
        print(f"- Enabled: {self.enabled}")
        print(f"- Safe state: {self.safe_state}")
        print(f"- Callback registered: {self.shutdown_callback is not None}")

        return True
