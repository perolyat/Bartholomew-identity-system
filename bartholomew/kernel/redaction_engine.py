"""
Redaction Engine for Bartholomew
Implements rule-based content redaction with multiple strategies
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class RedactionPolicyError(ValueError):
    """A redaction policy was required but cannot be applied as written.

    Raised when an instruction names no patterns, names a pattern that does
    not compile, or names a strategy this engine does not implement.

    This is deliberately an *error* and not a silently-ignored condition.
    Every function in this module used to answer "I cannot apply this
    pattern" with `return text` -- the original, unredacted text -- which is
    precisely how a broken redaction rule became a silent decision to store
    and index raw sensitive material (FND-02). Callers are expected to fail
    the write closed on this, not to swallow it.
    """


def mask_sensitive(text: str, pattern: str) -> str:
    """
    Mask sensitive content by replacing matches with asterisks

    Args:
        text: Input text to redact
        pattern: Regex pattern to match sensitive content

    Returns:
        Text with matches replaced by ****

    Raises:
        RedactionPolicyError: if `pattern` does not compile.
    """
    try:
        return re.sub(pattern, "****", text, flags=re.IGNORECASE)
    except re.error as e:
        # FND-02: this used to `return text` -- the unredacted original.
        # A redaction pattern that does not compile is a broken privacy
        # control, and answering it with the raw text is how broken
        # controls become silent data disclosure. Callers fail the write
        # closed on this instead.
        raise RedactionPolicyError(f"Invalid regex pattern {pattern!r}: {e}") from e


def remove_sensitive(text: str, pattern: str) -> str:
    """
    Remove sensitive content by deleting matches

    Args:
        text: Input text to redact
        pattern: Regex pattern to match sensitive content

    Returns:
        Text with matches removed

    Raises:
        RedactionPolicyError: if `pattern` does not compile.
    """
    try:
        return re.sub(pattern, "", text, flags=re.IGNORECASE)
    except re.error as e:
        # FND-02: this used to `return text` -- the unredacted original.
        # A redaction pattern that does not compile is a broken privacy
        # control, and answering it with the raw text is how broken
        # controls become silent data disclosure. Callers fail the write
        # closed on this instead.
        raise RedactionPolicyError(f"Invalid regex pattern {pattern!r}: {e}") from e


def replace_sensitive(text: str, pattern: str, replacement: str) -> str:
    """
    Replace sensitive content with a custom string

    Args:
        text: Input text to redact
        pattern: Regex pattern to match sensitive content
        replacement: String to replace matches with

    Returns:
        Text with matches replaced by replacement string

    Raises:
        RedactionPolicyError: if `pattern` does not compile.
    """
    try:
        return re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    except re.error as e:
        # FND-02: this used to `return text` -- the unredacted original.
        # A redaction pattern that does not compile is a broken privacy
        # control, and answering it with the raw text is how broken
        # controls become silent data disclosure. Callers fail the write
        # closed on this instead.
        raise RedactionPolicyError(f"Invalid regex pattern {pattern!r}: {e}") from e


# ---------------------------------------------------------------------------
# Named redaction patterns (FND-02)
#
# These describe the SHAPE of sensitive material -- an email address, an
# account number -- not the vocabulary that signals a memory is sensitive.
# The distinction is the whole point of FND-02.
#
# `memory_rules.yaml`'s redaction rules fire on *classifier* expressions
# like `(?i)(bank|medical|address|phone|email)`. A classifier answers "does
# this memory need protecting?". It does not answer "which characters must
# go?" -- masking the word `email` leaves `a@b.com` sitting in the index.
# So a rule that requires redaction must name, explicitly, which of these
# shapes it protects, via `redact_patterns:` in its metadata.
#
# This is the same judgement `redact_pii()` below already records for the
# Experience Kernel: match concrete, unambiguous shapes, never the ordinary
# vocabulary a wellness assistant legitimately uses ("Answer health
# question" must not become "Answer **** question"). FND-02 extends that
# rule to the governed memory path instead of inventing a second policy.
# ---------------------------------------------------------------------------
_EMAIL_PATTERN = r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}"
_PHONE_PATTERN = r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"
_SSN_PATTERN = r"\b\d{3}-\d{2}-\d{4}\b"

# A run of five or more digits: account numbers, card fragments, sort codes,
# member ids. Deliberately broad *within already-classified content* -- these
# patterns only ever run against a memory a governance rule has already
# judged sensitive (bank/secure/personal-data), so the cost of catching an
# extra number there is far below the cost of missing an account number.
# Four digits and fewer are left alone so ordinary years and times survive.
#
# The lookbehind keeps it off the fractional part of a number or an ISO
# timestamp: a competency record carries `recorded_at` values like
# "2026-09-12T07:28:48.025871+00:00", whose six-digit microseconds would
# otherwise be masked. Corrupting a provenance timestamp is not a
# conservative privacy choice -- it destroys audit data while protecting
# nothing, and provenance/audit is a governance control in its own right.
_LONG_DIGIT_SEQUENCE_PATTERN = r"(?<![\d.])\b\d{5,}\b"

# 13-19 digits, optionally spaced or hyphenated, as payment cards are written.
_CARD_NUMBER_PATTERN = r"\b(?:\d[ -]?){12,18}\d\b"

_IBAN_PATTERN = r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}\b"

# A labelled secret and the value ASSIGNED to it. The assignment token
# ("is", ":", "=") is REQUIRED, not optional, and that is the whole
# difference between a redaction pattern and a keyword blocklist:
#
#   "my password is hunter2"            -> matches, hunter2 is removed
#   "I changed my password yesterday"   -> does NOT match
#   "remember their auth code for later" -> does NOT match
#
# An optional connector made this eat the token after any mention of the
# word, which mangles ordinary sentences and (confirmed against
# tests/test_competency_memory_shapes.py) rewrote a stored competency
# record that contained no secret at all. Redaction that damages benign
# text buys no privacy and costs the user their memory -- the same
# judgement `redact_pii()` below records about keyword matching.
#
# The label is swept up with the value on purpose: "password: hunter2"
# leaves nothing useful behind once `hunter2` is gone, and keeping the
# label would require variable-length lookbehind, which Python's `re` does
# not support.
# No inline `(?i)`: every pattern here is combined into one alternation and
# applied with `re.IGNORECASE` as a flag. A global inline flag anywhere but
# the very start of a combined pattern is a compile error -- which, before
# the helpers were made to fail closed, silently disabled the entire
# instruction and returned the text unredacted.
_SECRET_ASSIGNMENT_PATTERN = (
    r"\b(?:password|passcode|passphrase|pin|auth(?:[\s_-]*code)?|otp|"
    r"one[\s-]*time[\s-]*code|two[\s-]*factor|2fa|api[\s_-]*key|"
    r"access[\s_-]*token|secret)\b\s*(?:is|are|was|were|=|:)\s*\S+"
)

REDACTION_PATTERNS: dict[str, str] = {
    "email": _EMAIL_PATTERN,
    "phone": _PHONE_PATTERN,
    "ssn": _SSN_PATTERN,
    "long_digit_sequence": _LONG_DIGIT_SEQUENCE_PATTERN,
    "card_number": _CARD_NUMBER_PATTERN,
    "iban": _IBAN_PATTERN,
    "secret_assignment": _SECRET_ASSIGNMENT_PATTERN,
}

_KNOWN_STRATEGY_PREFIX = "replace:"
_KNOWN_STRATEGIES = ("mask", "remove")

# A leading inline global flag group, e.g. the `(?i)` that every
# `match.content` expression in memory_rules.yaml begins with.
_LEADING_GLOBAL_FLAGS_RE = re.compile(r"^\(\?([aimsux]+)\)")


def _scope_leading_global_flags(pattern: str) -> str:
    """Rewrite a leading inline *global* flag group as a *scoped* one.

    ``(?s)BEGIN.*END``  ->  ``(?s:BEGIN.*END)``

    Patterns are combined into one alternation before they are applied, and
    Python rejects a global inline flag anywhere but the very start of the
    *whole* expression -- so a perfectly valid-looking `(?i)secretword`
    becomes a compile error the moment it is combined with anything else.
    Before the helpers were made to fail closed, that compile error was
    swallowed and the text came back unredacted: one rule author's harmless
    `(?i)` silently disabled the entire instruction.

    An earlier version of this function *deleted* the flag group instead.
    That was correct only for `i`, which `apply_redaction()` re-applies as
    `re.IGNORECASE` -- and silently wrong for every other flag, which is a
    fail-open of exactly the kind FND-02 exists to remove. `(?s)BEGIN.*END`
    became `BEGIN.*END`, which no longer crosses a newline, so a multiline
    secret went unmatched; the stripped pattern still compiled, so
    validation passed and the sensitive text was stored unredacted with
    nothing logged. Caught in review of the FND-02 PR itself.

    Scoping preserves the flag's meaning and is legal anywhere in a
    combined expression. A flag character outside the supported set is left
    alone and reported by the combined-compile check in
    `RedactionInstruction.__post_init__`, which fails closed.
    """
    match = _LEADING_GLOBAL_FLAGS_RE.match(pattern)
    if not match:
        return pattern
    flags = match.group(1)
    body = pattern[match.end() :]
    return f"(?{flags}:{body})"


@dataclass(frozen=True)
class RedactionInstruction:
    """The authoritative answer to "what must be removed, and how".

    FND-02 exists because this concept had no representation of its own.
    Three different things were sharing one dictionary key called
    ``content``:

      A. the memory's own text (what `MemoryRulesEngine.evaluate()` returns
         under ``content``),
      B. the condition that made a governance rule match (a rule's
         ``match.content`` regex),
      C. the redaction instruction itself.

    `apply_redaction()` read (C) out of a dict that was actually carrying
    (A), so a user's memory became the regular expression used to redact
    itself. Giving (C) its own type -- one that a memory dict cannot
    impersonate -- is what stops that class of bug returning, and it is why
    `apply_redaction()` refuses a `Mapping` outright rather than simply
    reading a differently-named key.

    An instance is validated at construction: if it exists, it can be
    applied. There is no such thing as a RedactionInstruction that silently
    does nothing.

    Attributes:
        patterns: One or more regexes identifying the sensitive spans.
        strategy: ``mask``, ``remove``, or ``replace:<text>``.
        source: Free-text provenance for audit/logging, e.g. the rule
            categories that demanded this redaction. Never used to decide
            behaviour.
    """

    patterns: tuple[str, ...]
    strategy: str = "mask"
    source: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.patterns, str):
            raise RedactionPolicyError(
                "patterns must be a tuple of regexes, not a single string "
                "(a bare string would be iterated character by character).",
            )
        object.__setattr__(
            self,
            "patterns",
            tuple(
                _scope_leading_global_flags(p) if isinstance(p, str) else p for p in self.patterns
            ),
        )
        if not self.patterns:
            raise RedactionPolicyError(
                "A redaction instruction must name at least one pattern. "
                "Policy required redaction but identified nothing to redact.",
            )
        for pattern in self.patterns:
            if not isinstance(pattern, str) or not pattern:
                raise RedactionPolicyError(f"Redaction pattern is not usable: {pattern!r}")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise RedactionPolicyError(
                    f"Redaction pattern does not compile: {pattern!r} ({exc})",
                ) from exc
        # Individually-valid patterns can still be invalid together -- an
        # inline global flag like `(?i)` is legal alone and a compile error
        # once alternated with anything else. `apply_redaction()` runs the
        # combined form, so the combined form is what must be proven.
        try:
            re.compile(self.combined_pattern)
        except re.error as exc:
            raise RedactionPolicyError(
                f"Redaction patterns do not compile when combined: "
                f"{self.combined_pattern!r} ({exc})",
            ) from exc
        if not (
            self.strategy in _KNOWN_STRATEGIES or self.strategy.startswith(_KNOWN_STRATEGY_PREFIX)
        ):
            raise RedactionPolicyError(
                f"Unknown redaction strategy {self.strategy!r}; "
                f"expected one of {_KNOWN_STRATEGIES} or 'replace:<text>'.",
            )

    @property
    def combined_pattern(self) -> str:
        """All patterns as one alternation, applied in a single pass."""
        return "|".join(f"(?:{p})" for p in self.patterns)


def apply_redaction(text: str, instruction: RedactionInstruction) -> str:
    r"""
    Apply an explicit redaction instruction to text.

    Args:
        text: Input text to redact.
        instruction: A validated `RedactionInstruction`. **Not** a rule dict,
            and emphatically not a `MemoryRulesEngine.evaluate()` result --
            passing a mapping raises `TypeError`, because that is exactly
            the confusion FND-02 removed.

    Returns:
        The redacted text.

    Raises:
        TypeError: if `instruction` is a mapping or any non-instruction.

    Examples:
        >>> ins = RedactionInstruction(patterns=(r"\d{3}-\d{2}-\d{4}",),
        ...                            strategy="mask")
        >>> apply_redaction("SSN: 123-45-6789", ins)
        'SSN: ****'

        >>> ins = RedactionInstruction(patterns=(r"hunter2",),
        ...                            strategy="replace:[REDACTED]")
        >>> apply_redaction("password: hunter2", ins)
        'password: [REDACTED]'
    """
    if isinstance(instruction, Mapping):
        raise TypeError(
            "apply_redaction() no longer accepts a rule/policy dict. A memory "
            "evaluated by MemoryRulesEngine carries the user's own text under "
            "'content', and reading that as a regex is the FND-02 defect. "
            "Resolve an explicit RedactionInstruction instead -- see "
            "memory_rules.resolve_redaction_instruction().",
        )
    if not isinstance(instruction, RedactionInstruction):
        raise TypeError(
            f"apply_redaction() requires a RedactionInstruction, got "
            f"{type(instruction).__name__}.",
        )

    pattern = instruction.combined_pattern
    strategy = instruction.strategy

    if strategy == "mask":
        return mask_sensitive(text, pattern)
    if strategy == "remove":
        return remove_sensitive(text, pattern)
    # Validated at construction, so this is the only remaining case.
    replacement = strategy.split(_KNOWN_STRATEGY_PREFIX, 1)[1]
    return replace_sensitive(text, pattern, replacement)


# mask_sensitive()/apply_redaction() above require the caller to supply a
# specific regex pattern (driven by memory_rules.yaml's per-kind rules).
# Experience Kernel / Narrator accept arbitrary free text (attention
# targets, goal descriptions, affect labels, observation/reflection
# content) with no per-field rule of their own, so they need a
# general-purpose pattern instead of one tailored to a specific memory
# kind.
#
# Deliberately NOT reusing memory.privacy_guard.SENSITIVE_KEYWORDS here
# (name/address/location/phone/email/bank/password/routine/health/
# private/account) -- that list is a *consent-prompt* trigger (a human
# confirms before storing), which tolerates false positives fine. This
# function is *silent, automatic* redaction with no human in the loop, and
# those keywords are also just ordinary vocabulary a wellness-focused
# assistant's own self-model legitimately uses constantly (e.g. a "health"
# or "routine"-related goal/attention target) -- using them here mangled
# entirely benign content in testing (confirmed: "Answer health question"
# became "Answer **** question"). So this matches only concrete,
# unambiguous PII *shapes* instead -- narrower, but far fewer false
# positives for content this subsystem is expected to legitimately handle.
PII_PATTERN = re.compile(
    "|".join([_EMAIL_PATTERN, _SSN_PATTERN, _PHONE_PATTERN]),
    re.IGNORECASE,
)


def redact_pii(text: str | None) -> str | None:
    """
    General-purpose PII redaction for free text with no per-field rule.

    Intended for Experience Kernel / Narrator call sites that accept
    arbitrary caller-supplied text (attention targets, goal descriptions,
    affect labels, observation/reflection content) and need a safe default
    rather than requiring every call site to supply its own regex.

    Returns text unchanged if empty/None (so callers can pass through
    optional fields, e.g. set_attention(target=None), without a None check).
    """
    if not text:
        return text
    return PII_PATTERN.sub("****", text)
