# privacy_guard.py
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable


SENSITIVE_KEYWORDS = [
    "name",
    "address",
    "location",
    "phone",
    "email",
    "bank",
    "password",
    "routine",
    "health",
    "private",
    "account",
]

# Resolves a consent prompt for storing sensitive content. Registered by a
# UI/CLI at startup via set_consent_handler(); the kernel itself never blocks
# on stdin. With no handler registered, requests fail closed (denied) instead
# of hanging headless/API deployments.
ConsentHandler = Callable[[str], "bool | Awaitable[bool]"]
_consent_handler: ConsentHandler | None = None


def set_consent_handler(handler: ConsentHandler | None) -> None:
    """Register the callback used to resolve sensitive-content consent prompts."""
    global _consent_handler
    _consent_handler = handler


def get_consent_handler() -> ConsentHandler | None:
    """Return the currently registered consent handler, if any."""
    return _consent_handler


def is_sensitive(text: str) -> bool:
    return any(keyword in text.lower() for keyword in SENSITIVE_KEYWORDS)


async def request_permission_to_store(text: str) -> bool:
    if _consent_handler is None:
        return False

    result = _consent_handler(text)
    if inspect.isawaitable(result):
        result = await result
    return bool(result)
