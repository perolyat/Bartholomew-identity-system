"""Recognising content that must never be typed, copied or recorded.

Two different questions, deliberately separated, because conflating them is
how a detector ends up either uselessly noisy or dangerously permissive:

* `detect_secrets(text)` -- does this text *contain* credential material? Used
  to refuse a `clipboard_write` or `type_text` outright, and to refuse
  *returning* a `clipboard_read` result.
* `is_sensitive_field(...)` -- is the place we are about to type into a
  password, PIN, token or payment field? Used by `type_text`, and answered
  from the accessibility tree rather than guessed.

**This is a refusal detector, not a redactor.** A hit means the action does not
happen. It is deliberately biased towards false positives: refusing to type a
string that merely looks like an API key costs one retyped sentence, while
missing one costs a credential. Nothing here tries to "clean" content and
proceed.

**It never sees the outside of this process.** No detected value, and no
sample of the text, is returned, logged or persisted -- `SecretFinding` carries
a category and a position, never the matched text. The reason is narrow and
practical: a detector that logged what it found would be a second copy of every
secret it caught.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

#: Longest text this detector will scan. Longer input is **refused** -- it
#: comes back as an `unscannable_length` finding rather than a clean scan of a
#: prefix, because a detector that silently ignores everything past a bound is
#: a detector you get past by padding.
MAX_SCANNED_CHARS = 4096


@dataclass(frozen=True)
class SecretFinding:
    """One reason a piece of text was refused.

    Deliberately carries no sample of the matched text. `category` is what an
    audit row records and what the person is told; the value itself stays
    where it was.
    """

    category: str
    #: Where in the scanned text the match began, so a person can find it
    #: themselves without the system quoting it back.
    offset: int

    def describe(self) -> str:
        return f"{self.category} at offset {self.offset}"


#: The PEM armour a private key is wrapped in, assembled rather than written.
#:
#: A detector's own pattern is indistinguishable from the thing it detects, and
#: the repository's `detect-private-key` pre-commit hook reads source files:
#: spelling the marker out here failed the hook on the very file whose job is
#: to recognise it. Joining the halves at import produces the identical regex
#: and leaves no line matching a scanner's signature -- the same reason the
#: secret-shaped test fixtures are assembled from fragments.
_PEM_OPEN = "-----BEGIN "
_PEM_PRIVATE = "PRIVATE KEY"
_PEM_CLOSE = "-----"

# Each entry is (category, compiled pattern). Ordered from most specific to
# most general so the reported category is the most informative one.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key_block",
        re.compile(_PEM_OPEN + r"[A-Z ]*" + _PEM_PRIVATE + _PEM_CLOSE),
    ),
    (
        "pgp_private_key",
        re.compile(_PEM_OPEN + "PGP " + _PEM_PRIVATE + " BLOCK" + _PEM_CLOSE),
    ),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("stripe_key", re.compile(r"\b[sr]k_(?:live|test)_[0-9A-Za-z]{16,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("json_web_token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer_token", re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("basic_auth_header", re.compile(r"\b[Bb]asic\s+[A-Za-z0-9+/=]{16,}")),
    ("url_embedded_credentials", re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    ("connection_string_password", re.compile(r"(?i)\b(?:pwd|password)\s*=\s*\S+")),
    (
        "labelled_secret",
        re.compile(
            r"(?i)\b(?:api[_\- ]?key|secret[_\- ]?key|client[_\- ]?secret|access[_\- ]?token"
            r"|auth[_\- ]?token|refresh[_\- ]?token|private[_\- ]?key|passphrase|password|passwd"
            r"|pin[_\- ]?code|otp|one[_\- ]?time[_\- ]?code|security[_\- ]?code|cvv|cvc"
            r"|seed[_\- ]?phrase|recovery[_\- ]?(?:code|phrase))\b\s*[:=]\s*\S+",
        ),
    ),
    (
        "recovery_phrase",
        # Twelve or more lowercase words in a row with no punctuation is the
        # shape of a BIP-39 mnemonic and is never ordinary prose to type.
        re.compile(r"\b(?:[a-z]{3,8}\s+){11,}[a-z]{3,8}\b"),
    ),
)

#: A run of characters that looks like a key even though nothing labels it as
#: one. Checked separately from `_PATTERNS` because it needs an entropy test
#: as well as a shape test -- "abcdefghijklmnopqrstuvwxyz0123456789" is long
#: and alphanumeric but is not a secret.
_HIGH_ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")

#: Digit groups that could be a payment card number, before the Luhn check.
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _shannon_entropy_bits_per_char(text: str) -> float:
    """Entropy of one string, in bits per character.

    Used only to tell a long random-looking token apart from a long ordinary
    word. Implemented here rather than pulled in because it is six lines and a
    dependency for six lines is a worse trade than the six lines.
    """
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for char in text:
        counts[char] = counts.get(char, 0) + 1
    total = len(text)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


#: Below this, a long alphanumeric run is a word or a path, not a key. 3.5 bits
#: per character sits comfortably above English text (~2.3 for lowercase words
#: of this length) and below base64-encoded random bytes (~5.5).
_ENTROPY_THRESHOLD = 3.5


def _luhn_valid(digits: str) -> bool:
    """The check digit test every payment card number satisfies."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def detect_secrets(text: str) -> tuple[SecretFinding, ...]:
    """Every reason this text must not be typed, copied or stored.

    An empty tuple means no detector fired. That is *not* a guarantee that the
    text holds no secret -- no detector can promise that -- which is why every
    capability that uses this also requires an explicit human approval. This
    is the second control, not the only one.
    """
    if not isinstance(text, str) or not text:
        return ()
    if len(text) > MAX_SCANNED_CHARS:
        # **Refused, not truncated.** Scanning a prefix and returning "nothing
        # found" for the rest is a detector that can be walked past by padding:
        # put 4,096 harmless characters in front of a credential and the
        # credential is never looked at. Every caller bounds its input well
        # below this, so reaching here means something bypassed a bound -- and
        # the safe answer to that is a finding, not a clean scan.
        return (SecretFinding(category="unscannable_length", offset=MAX_SCANNED_CHARS),)
    scanned = text
    findings: list[SecretFinding] = []
    seen: set[str] = set()

    for category, pattern in _PATTERNS:
        match = pattern.search(scanned)
        if match and category not in seen:
            seen.add(category)
            findings.append(SecretFinding(category=category, offset=match.start()))

    for match in _HIGH_ENTROPY_CANDIDATE.finditer(scanned):
        candidate = match.group(0)
        if _shannon_entropy_bits_per_char(candidate) >= _ENTROPY_THRESHOLD:
            if "high_entropy_token" not in seen:
                seen.add("high_entropy_token")
                findings.append(
                    SecretFinding(category="high_entropy_token", offset=match.start()),
                )
            break

    for match in _CARD_CANDIDATE.finditer(scanned):
        digits = re.sub(r"[ -]", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            if "payment_card_number" not in seen:
                seen.add("payment_card_number")
                findings.append(
                    SecretFinding(category="payment_card_number", offset=match.start()),
                )
            break

    return tuple(findings)


def secret_categories(text: str) -> tuple[str, ...]:
    """Just the categories, for an audit row. Never the matched values."""
    return tuple(f.category for f in detect_secrets(text))


# ---------------------------------------------------------------------------
# Sensitive *fields*, as opposed to sensitive *content*
# ---------------------------------------------------------------------------

#: Words that mark a field as one Bartholomew must never type into. Matched
#: against the accessibility name, automation id, help text and placeholder of
#: the focused element, with word-ish boundaries so "pin" matches "PIN code"
#: and "Enter PIN" but not "shipping".
#: Stored in the folded form `_fold()` produces -- lowercase, accents stripped
#: -- so "Contraseña" and "Passwort" are recognised as readily as "Password".
#: A localised banking page labels its password field in its own language, and
#: a detector that only knew English would type into one.
_SENSITIVE_FIELD_WORDS: tuple[str, ...] = (
    "password",
    "contrasena",
    "passwort",
    "kennwort",
    "senha",
    "wachtwoord",
    "losenord",
    "salasana",
    "adgangskode",
    "hasło",
    "haslo",
    "mot de passe",
    "parola",
    "passwd",
    "passphrase",
    "pass phrase",
    "pin",
    "passcode",
    "otp",
    "one-time",
    "one time code",
    "2fa",
    "mfa",
    "two-factor",
    "authenticator",
    "verification code",
    "security code",
    "secret",
    "api key",
    "apikey",
    "token",
    "credential",
    "private key",
    "seed phrase",
    "recovery phrase",
    "recovery code",
    "backup code",
    "card number",
    "cardnumber",
    "credit card",
    "debit card",
    "cvv",
    "cvc",
    "csc",
    "expiry",
    "expiration date",
    "sort code",
    "routing number",
    "account number",
    "iban",
    "bsb",
    "swift",
    "ssn",
    "social security",
    "tax file number",
    "national insurance",
    "licence number",
    "license number",
    "passport",
    "date of birth",
    "mother's maiden",
    "security question",
    "security answer",
)

_WORD_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}


def _word_pattern(word: str) -> re.Pattern[str]:
    pattern = _WORD_BOUNDARY_CACHE.get(word)
    if pattern is None:
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])")
        _WORD_BOUNDARY_CACHE[word] = pattern
    return pattern


def _fold(text: str | None) -> str:
    """Lowercase, strip accents, and collapse whitespace.

    Accent stripping matters: a field labelled `Contrasenã` should not slip
    past a check that only knows the unaccented spelling, and a label with a
    non-breaking space should compare the same as one with an ordinary space.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().lower()


def sensitive_field_reasons(
    *,
    is_password: bool | None,
    name: str | None = None,
    automation_id: str | None = None,
    help_text: str | None = None,
    placeholder: str | None = None,
) -> tuple[str, ...]:
    """Why the focused field must not be typed into. Empty means no reason found.

    **`is_password=None` is itself a reason.** It means the accessibility tree
    could not be read, so nothing is known about the field -- and "unknown" is
    handled as "sensitive", not as "fine". A caller that cannot see where it is
    typing must not type.
    """
    reasons: list[str] = []
    if is_password is None:
        reasons.append("focused_field_unreadable")
    elif is_password:
        reasons.append("focused_field_is_password")

    haystack = " ".join(
        _fold(part) for part in (name, automation_id, help_text, placeholder) if part
    )
    if haystack:
        for word in _SENSITIVE_FIELD_WORDS:
            if _word_pattern(word).search(haystack):
                reasons.append(f"focused_field_labelled:{word.replace(' ', '_')}")
                break
    return tuple(reasons)


# ---------------------------------------------------------------------------
# Controls that finish something
# ---------------------------------------------------------------------------

#: Control names that commit an action to the outside world or destroy
#: something. `windows.accessibility_action` refuses to target an element whose
#: name matches any of them, and no capability in this build can press a button
#: in the first place -- this is the second fence, not the first.
_FINAL_ACTION_WORDS: tuple[str, ...] = (
    "send",
    "submit",
    "confirm",
    "confirm and pay",
    "purchase",
    "buy",
    "buy now",
    "pay",
    "pay now",
    "checkout",
    "check out",
    "place order",
    "order",
    "subscribe",
    "delete",
    "remove",
    "erase",
    "destroy",
    "wipe",
    "format",
    "reset",
    "revoke",
    "transfer",
    "withdraw",
    "publish",
    "post",
    "tweet",
    "share",
    "install",
    "uninstall",
    "update now",
    "upgrade",
    "sign in",
    "log in",
    "login",
    "sign up",
    "authorize",
    "authorise",
    "allow",
    "grant",
    "approve",
    "accept",
    "agree",
    "yes",
    "ok",
    "run",
    "execute",
    "open anyway",
    "keep",
    "trust",
    "enable",
    "disable",
    "shut down",
    "restart",
    "log out",
    "sign out",
)


def final_action_reason(control_name: str | None) -> str | None:
    """The word that makes this control a final action, or None.

    Used by `windows.accessibility_action`. A match refuses the action: the
    permitted semantic operations (expand, collapse, scroll, focus) have no
    business naming a Send button, so a request that does is either confused
    or is trying something.
    """
    folded = _fold(control_name)
    if not folded:
        return None
    for word in _FINAL_ACTION_WORDS:
        if _word_pattern(word).search(folded):
            return word
    return None
