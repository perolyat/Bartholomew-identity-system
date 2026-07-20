"""
Consent Adapter for user prompts, routed through the shared kernel consent handler
"""

import inspect
from typing import Any

from bartholomew.kernel.memory.privacy_guard import get_consent_handler


class ConsentAdapter:
    """Consent prompting via the shared kernel consent handler.

    Callers use this specifically when they're already inside a running event
    loop, so this must never block on stdin itself -- doing so would freeze
    the whole loop, not just one coroutine. It delegates to whatever handler
    is registered with bartholomew.kernel.memory.privacy_guard.set_consent_handler()
    (e.g. chat.py registers a terminal prompt for interactive CLI use) and
    calls it synchronously. With no handler registered, or a handler that
    returns an awaitable (which can't be safely awaited from this synchronous,
    already-in-a-loop context), requests fail closed (denied).
    """

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

        prompt = f"Action: {action}"
        if details:
            prompt += f"\nDetails: {details}"

        handler = get_consent_handler()
        if handler is None:
            granted = False
        else:
            result = handler(prompt)
            granted = False if inspect.isawaitable(result) else bool(result)

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
