# privacy_guard.py
"""
Keyword-based sensitivity check gating memory writes.

`is_sensitive()` decides whether content needs human consent before it can
be stored (`MemoryStore.upsert_memory()`'s privacy_guard gate). It is one of
two overlapping sensitivity vocabularies in the codebase -- see the
"privacy_guard vs memory_rules.yaml" entry in `RISKS.md` for the recorded,
deliberately-unresolved disagreement between them.

Matching semantics (corrected 2026-08-11)
------------------------------------------
Two defects were found while integrating S5.2's training seam, both
pre-existing and neither specific to any one caller:

1. **Lexical.** Matching was an unanchored substring scan, so ordinary
   English tripped the gate: "memory allocation" matched `location`, "the
   company went bankrupt" matched `bank`, "accounting" matched `account`,
   "healthcare" matched `health`, "filename"/"rename" matched `name`.
   Matching is now word-boundary anchored.

   Boundary anchoring alone would have *weakened* the gate in two ways, so
   both are explicitly compensated:
     - plurals ("her addresses", "patient names") would stop matching, so
       the pattern accepts an optional `s`/`es` suffix;
     - compound terms substring matching caught incidentally
       ("username", "surname", "nickname") would stop matching, so they are
       enumerated in `SENSITIVE_COMPOUND_TERMS`.

2. **Structural.** The gate scans whatever representation the caller stores.
   For structured (JSON) content that includes the *schema keys*, so a
   record was flagged on the shape of its schema rather than on anything a
   user wrote -- e.g. S5.1's competency records carry a `"name"` key, which
   made every one of them "sensitive" regardless of content. See
   `register_structural_schema()`.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

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

#: Compound terms that the previous unanchored substring match caught
#: incidentally (each contains a `SENSITIVE_KEYWORDS` entry) and that
#: word-boundary matching would otherwise lose. Enumerated so the boundary
#: correction cannot weaken the gate. This is not a general expansion of the
#: sensitivity vocabulary -- it preserves exactly what was already caught.
SENSITIVE_COMPOUND_TERMS = [
    "username",
    "surname",
    "nickname",
]

# One pattern over both lists. `\b` anchoring makes ordering irrelevant:
# "username" cannot match the `name` alternative, because there is no word
# boundary before "name" inside it. The optional `(?:s|es)` suffix keeps
# plurals matching.
_SENSITIVE_PATTERN = re.compile(
    r"\b(?:"
    + "|".join(re.escape(term) for term in SENSITIVE_KEYWORDS + SENSITIVE_COMPOUND_TERMS)
    + r")(?:s|es)?\b",
)

# Resolves a consent prompt for storing sensitive content. Registered by a
# UI/CLI at startup via set_consent_handler(); the kernel itself never blocks
# on stdin. With no handler registered, requests fail closed (denied) instead
# of hanging headless/API deployments.
ConsentHandler = Callable[[str], "bool | Awaitable[bool]"]
_consent_handler: ConsentHandler | None = None


# ---------------------------------------------------------------------------
# Device consent: a second, richer channel -- deliberately not the first one
# ---------------------------------------------------------------------------
#
# A device observation start (screen, microphone) asks a person before any
# adapter is touched. On an interactive front-end the plain string handler
# above serves that ask too. A headless server has nobody at a terminal, so
# it needs a channel a human can reach from elsewhere -- and that channel must
# know *what* it is asking about (which tenant, which machine, which
# modality), which a bare prompt string cannot carry.
#
# It is a separate registry rather than a richer version of the one above for
# one reason that matters: `MemoryStore.upsert_memory()` branches on whether
# `get_consent_handler()` is None. With no handler, a sensitive memory write is
# *queued* for later review; with any handler, it is asked interactively and
# *discarded* if nobody answers. Registering a server-side device channel on
# the shared global would silently turn every unanswered memory prompt from
# "held for you" into "thrown away". Keeping the device channel here means only
# the device seams consult it, and memory behaviour does not move.


@dataclass(frozen=True)
class DeviceConsentRequest:
    """One ask to a human: may this device start observing, right now, once.

    `prompt` is the modality-specific wording the Runtime Contract composes
    (it names the single modality and what it does *not* permit). The other
    fields are context so the person can tell whose machine is asking; they
    are never a substitute for the prompt and a handler must show both.
    """

    prompt: str
    tenant_id: str | None = None
    principal_id: str | None = None
    device_id: str | None = None
    modality: str | None = None
    correlation_id: str | None = None
    session_id: str | None = None


DeviceConsentHandler = Callable[[DeviceConsentRequest], "bool | Awaitable[bool]"]
_device_consent_handler: DeviceConsentHandler | None = None


def set_device_consent_handler(handler: DeviceConsentHandler | None) -> None:
    """Register the channel that asks a person about a device start.

    Consulted by the device seams only (`runtime_contract._resolve_device_consent`),
    ahead of the plain handler. Grants authorise a single start attempt; a
    handler that remembers answers would be a consent bypass, not a convenience.
    """
    global _device_consent_handler
    _device_consent_handler = handler


def get_device_consent_handler() -> DeviceConsentHandler | None:
    return _device_consent_handler


# ---------------------------------------------------------------------------
# Structural schema registry
# ---------------------------------------------------------------------------
#
# Governance rule (approved 2026-08-11): the schema-defined keys of a
# *registered* structured record kind are structural metadata, not user
# content, and are excluded from content sensitivity scanning. **All values
# are always scanned in full**, as are any keys not part of the registered
# schema (e.g. user-chosen keys inside a nested map).
#
# Unknown/unregistered structured data deliberately keeps the conservative
# key-and-value scan. That matters: a bare `{"password": "hunter2"}` is
# sensitive *only* by virtue of its key, so a blanket "scan values, not keys"
# rule would have been a real consent bypass. It was measured and rejected
# for exactly that reason.
#
# Registration is a fail-safe, not a fail-open: if a kind is never
# registered, its content is scanned conservatively (raw, keys included) and
# at worst over-triggers consent. Nothing becomes less protected by a
# missing registration.
_SCHEMA_KEYS_BY_KIND: dict[str, frozenset[str]] = {}


def register_structural_schema(kind: str, keys: Iterable[str]) -> None:
    """
    Declare `kind`'s schema-defined key names as structural metadata.

    Callers should register the *complete* set of keys their serialised
    shape emits, including nested ones. Any key not registered is treated as
    content and scanned.

    A kind whose schema legitimately contains a sensitivity-indicating key
    (say a literal `password` field) must NOT be registered -- excluding
    that key would hide the only signal that the adjacent value is a secret.
    """
    _SCHEMA_KEYS_BY_KIND[kind] = frozenset(keys)


def registered_schema_keys(kind: str) -> frozenset[str] | None:
    """The registered schema keys for `kind`, or None if it is unregistered."""
    return _SCHEMA_KEYS_BY_KIND.get(kind)


def registered_structural_kinds() -> frozenset[str]:
    """Every kind currently registered as structured."""
    return frozenset(_SCHEMA_KEYS_BY_KIND)


def set_consent_handler(handler: ConsentHandler | None) -> None:
    """Register the callback used to resolve sensitive-content consent prompts."""
    global _consent_handler
    _consent_handler = handler


def get_consent_handler() -> ConsentHandler | None:
    """Return the currently registered consent handler, if any."""
    return _consent_handler


def _matches(text: str) -> bool:
    return bool(_SENSITIVE_PATTERN.search(text.lower()))


def _collect_scan_parts(node: object, schema_keys: frozenset[str], parts: list[str]) -> None:
    """
    Build the content projection of a decoded structure: every value, plus
    every key that is not part of the registered schema.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key not in schema_keys:
                parts.append(str(key))
            _collect_scan_parts(value, schema_keys, parts)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _collect_scan_parts(item, schema_keys, parts)
    elif node is not None:
        parts.append(str(node))


def is_sensitive(text: str, *, kind: str | None = None) -> bool:
    """
    Whether `text` needs consent before storage.

    `kind` is the memory kind being written. When it names a registered
    structured schema and `text` decodes as JSON, the scan runs over the
    content projection (values, plus non-schema keys) instead of the raw
    serialisation. In every other case -- unregistered kind, no kind, or
    content that does not decode as JSON -- the raw text is scanned exactly
    as before.
    """
    schema_keys = _SCHEMA_KEYS_BY_KIND.get(kind) if kind is not None else None
    if schema_keys is None:
        return _matches(text)

    try:
        decoded = json.loads(text)
    except (TypeError, ValueError):
        # Registered kind, but this value is not the structured shape (a
        # summary substitution, say). Fall back to the conservative scan
        # rather than assuming anything about it.
        return _matches(text)

    parts: list[str] = []
    _collect_scan_parts(decoded, schema_keys, parts)
    return _matches(" ".join(parts))


async def request_permission_to_store(text: str) -> bool:
    if _consent_handler is None:
        return False

    result = _consent_handler(text)
    if inspect.isawaitable(result):
        result = await result
    return bool(result)
