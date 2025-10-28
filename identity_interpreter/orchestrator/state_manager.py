"""
State Manager
-------------
Maintains transient runtime state for the current session.
"""
import uuid
from typing import Dict, Any


class StateManager:
    """Maintains transient runtime state for the current session."""

    def __init__(self):
        self.session_id = str(uuid.uuid4())
        self.state: Dict[str, Any] = {}

    def set(self, key: str, value: Any):
        """Set a state value."""
        self.state[key] = value

    def get(self, key: str, default=None):
        """Get a state value with optional default."""
        return self.state.get(key, default)

    def clear(self):
        """Clear all state values."""
        self.state.clear()

    def export(self) -> Dict[str, Any]:
        """Export current state and session ID."""
        return {"session_id": self.session_id, "state": self.state}
