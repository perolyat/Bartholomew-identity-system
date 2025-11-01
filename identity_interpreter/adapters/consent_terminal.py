"""
Consent Adapter for terminal-based user prompts
"""

from typing import Any


class ConsentAdapter:
    """Terminal-based consent prompting"""

    def __init__(self, identity_config: Any):
        """
        Initialize consent adapter

        Args:
            identity_config: Identity configuration object
        """
        self.identity = identity_config
        self.session_consents = {}

    def request_consent(
        self,
        action: str,
        details: str | None = None,
        scope: str = "per_use",
    ) -> bool:
        """
        Request user consent for an action

        Args:
            action: Action requiring consent
            details: Optional details about the action
            scope: Consent scope (per_use, per_session)

        Returns:
            True if consent granted
        """
        # Check if already consented this session
        if scope == "per_session" and action in self.session_consents:
            return self.session_consents[action]

        # Terminal prompt
        print("\n=== CONSENT REQUIRED ===")
        print(f"Action: {action}")
        if details:
            print(f"Details: {details}")
        print(f"Scope: {scope}")

        # Real user input
        response = input("Allow? (y/n): ").strip().lower()
        granted = response in ("y", "yes")

        if scope == "per_session":
            self.session_consents[action] = granted

        return granted

    def revoke_session_consent(self, action: str) -> None:
        """
        Revoke a session-scoped consent

        Args:
            action: Action to revoke consent for
        """
        if action in self.session_consents:
            del self.session_consents[action]

    def clear_session_consents(self) -> None:
        """Clear all session consents"""
        self.session_consents.clear()
