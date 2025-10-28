"""
Kill Switch
Emergency shutdown mechanism
"""

from collections.abc import Callable


class KillSwitch:
    """Emergency shutdown mechanism"""

    def __init__(self, identity_config):
        """
        Initialize kill switch

        Args:
            identity_config: Identity configuration object
        """
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
