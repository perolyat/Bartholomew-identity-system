"""Privacy class, retention class, and refusing to carry a secret.

Every derived observation this package produces is classified before it
leaves the adapter that made it, because an unclassified observation is one
that a later consumer has to guess about -- and the wave's rule is that
nothing infers meaning it was not given (invariant 15).

**The vocabularies are the frozen ones** (§3.1): privacy is
`context_only | ordinary | sensitive | restricted`; retention is
`ephemeral | operational | memory_candidate | audit`. This module does not
invent a third axis.

**Defaults are the cautious end.** A microphone transcript and a screen
observation both default to `sensitive` / `ephemeral`: heard speech and
whatever happens to be on a screen are not ordinary context, and neither is
a memory candidate until a human says so. Nothing here can promote an
observation to `memory_candidate`; that is Session D's decision through its
own governed path.

**Secret refusal is refusal, not masking-and-continue.** `scan_for_secrets()`
recognises the shapes this wave must never carry -- password and PIN fields,
API keys and tokens, private key blocks, payment card numbers, recovery
codes. When a detector fires on a *field* (an accessibility control that is a
password box), the correct answer is to omit the field entirely and record
that it was omitted. When one fires inside free text, the text is redacted to
a placeholder. Both are recorded in the observation's `redactions` list, so a
reader can see that something was removed rather than silently receiving a
shorter string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class PrivacyClass(str, Enum):
    CONTEXT_ONLY = "context_only"
    ORDINARY = "ordinary"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


class RetentionClass(str, Enum):
    EPHEMERAL = "ephemeral"
    OPERATIONAL = "operational"
    MEMORY_CANDIDATE = "memory_candidate"
    AUDIT = "audit"


#: What a derived observation is classified as unless a stricter rule fires.
#: Deliberately at the cautious end -- see the module docstring.
DEFAULT_PRIVACY = PrivacyClass.SENSITIVE
DEFAULT_RETENTION = RetentionClass.EPHEMERAL

#: Hard bounds on what one observation may carry. A transcript or a screen
#: description is a short summary, not a recording: anything longer is
#: truncated, and the truncation is recorded.
MAX_OBSERVATION_CHARS = 2000
MAX_TEXT_ELEMENTS = 40
MAX_ELEMENT_CHARS = 200

#: The placeholder that replaces detected secret material in free text.
REDACTION_PLACEHOLDER = "[redacted]"

#: Accessibility control types and field names that must never have their
#: value read at all. Matching is on the *field*, so the value is never even
#: put into an observation to be redacted later.
SECRET_FIELD_MARKERS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "passphrase",
        "pin",
        "secret",
        "token",
        "apikey",
        "api_key",
        "credential",
        "credentials",
        "otp",
        "mfa",
        "2fa",
        "totp",
        "recoverycode",
        "recovery_code",
        "securitycode",
        "security_code",
        "cvv",
        "cvc",
        "cardnumber",
        "card_number",
        "creditcard",
        "credit_card",
        "ssn",
        "privatekey",
        "private_key",
        "seedphrase",
        "seed_phrase",
        "mnemonic",
    },
)

#: Accessibility roles whose contents are secret by construction.
SECRET_ROLES: frozenset[str] = frozenset({"passwordbox", "password", "securetextfield"})

_SECRET_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # PEM private key blocks.
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?(-----END[A-Z ]*PRIVATE KEY-----)?", re.S),
    # Common API-key prefixes followed by a long opaque body.
    re.compile(r"\b(?:sk|pk|rk|api|key|tok|ghp|gho|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b"),
    # AWS access key ids.
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # JWTs.
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    # "password: hunter2" / "api key = ...".
    re.compile(
        r"\b(?:pass(?:word|phrase)?|pwd|pin|secret|token|api[ _-]?key|otp|cvv)\b\s*[:=]\s*\S+",
        re.I,
    ),
    # Payment card numbers (13-19 digits, optionally grouped).
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),
)


@dataclass
class RedactionRecord:
    """One thing that was removed, and why. Never silently dropped."""

    where: str
    rule: str

    def as_dict(self) -> dict[str, str]:
        return {"where": self.where, "rule": self.rule}


@dataclass
class Classification:
    """The privacy/retention labels and the redaction trail for one observation."""

    privacy_class: PrivacyClass = DEFAULT_PRIVACY
    retention_class: RetentionClass = DEFAULT_RETENTION
    redactions: list[RedactionRecord] = field(default_factory=list)
    truncated: bool = False

    def note(self, where: str, rule: str) -> None:
        self.redactions.append(RedactionRecord(where=where, rule=rule))

    def escalate(self, privacy: PrivacyClass) -> None:
        """Move to a stricter privacy class; never to a looser one."""
        order = [
            PrivacyClass.CONTEXT_ONLY,
            PrivacyClass.ORDINARY,
            PrivacyClass.SENSITIVE,
            PrivacyClass.RESTRICTED,
        ]
        if order.index(privacy) > order.index(self.privacy_class):
            self.privacy_class = privacy

    def as_dict(self) -> dict[str, object]:
        return {
            "privacy_class": self.privacy_class.value,
            "retention_class": self.retention_class.value,
            "redactions": [r.as_dict() for r in self.redactions],
            "truncated": self.truncated,
        }


def is_secret_field(name: str | None, role: str | None = None) -> bool:
    """Whether this accessibility field's value must never be read.

    Normalises away spaces, hyphens and underscores first, so "Card Number",
    "card-number" and "cardNumber" all match one marker.
    """
    if role and role.strip().lower().replace(" ", "") in SECRET_ROLES:
        return True
    if not name:
        return False
    normalised = re.sub(r"[\s_-]+", "", name.strip().lower())
    return any(marker.replace("_", "") in normalised for marker in SECRET_FIELD_MARKERS)


def scan_for_secrets(text: str) -> tuple[str, list[str]]:
    """Redact detected secret material. Returns (text, rules that fired)."""
    if not text:
        return text, []
    fired: list[str] = []
    result = text
    for index, pattern in enumerate(_SECRET_TEXT_PATTERNS):
        replaced, count = pattern.subn(REDACTION_PLACEHOLDER, result)
        if count:
            fired.append(f"secret_pattern_{index}")
            result = replaced
    return result, fired


def bound_text(text: str, limit: int = MAX_OBSERVATION_CHARS) -> tuple[str, bool]:
    """Truncate to `limit`, reporting whether it happened."""
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + "...", True


def sanitise(
    text: str,
    where: str,
    classification: Classification,
    limit: int = MAX_OBSERVATION_CHARS,
) -> str:
    """Redact, bound and record -- the one path text takes into an observation.

    Text that had a secret redacted out of it is escalated to `restricted`:
    the fact that a secret was on screen or spoken is itself not ordinary
    context, even once the value is gone.
    """
    cleaned, fired = scan_for_secrets(text)
    for rule in fired:
        classification.note(where, rule)
    if fired:
        classification.escalate(PrivacyClass.RESTRICTED)
    cleaned, truncated = bound_text(cleaned, limit)
    if truncated:
        classification.truncated = True
        classification.note(where, "length_bound")
    return cleaned
