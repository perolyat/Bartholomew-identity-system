"""
What may cross between two people, and the sanitizer that decides.

Bartholomew's default is that nothing crosses. One person's memory lives in
one database, behind one process, under one keyring namespace, and the
architecture has no path from it to anybody else's. Trusted-group sharing is
the single, opt-in exception, and this module is the gate on it: a **pure
data and validation** module -- no persistence, no retrieval, no I/O -- in the
same discipline `competency.py`, `training.py` and `candidate_learning.py`
hold to. Persistence lives in `bartholomew.platform.share_exchange`; the
decision about what is *publishable* lives here, below any UI, so hiding a
field in a form is never what protects it.

Three layers, in order, and each one fails closed
--------------------------------------------------
1. **Eligibility.** Only an explicitly named record of an eligible kind may
   enter sanitization at all. Raw memory, conversation history, inbound
   events, reflections, objectives, personal facts, approvals and candidate
   rows are structurally ineligible -- not filtered out later, never admitted.
   There is deliberately no generic "share this memory" package type.

2. **Prohibited fields refuse the whole publication.** A source record
   carrying a field from `PROHIBITED_FIELD_NAMES` anywhere, at any depth, is
   refused outright rather than quietly stripped. Stripping would mean the
   publisher believed they had shared something and had not, or -- worse --
   that a near-miss field name silently became the thing that leaked. If a
   record contains a credential, the correct answer is "not shareable", not
   "shareable minus the credential".

3. **Everything not explicitly allowed is removed and named.** The content
   projection is an *allowlist* per package kind. Envelope fields --
   provenance, classification, confidence, supervision, reviewer, keys,
   timestamps -- are removed and recorded by name in
   `Sanitization.removed_fields`. That is what keeps the publisher out of the
   package: `provenance.detail` is exactly the free text that says "accepted
   by Taylor from objective 17", and it never travels.

   The surviving values are then scanned for prohibited *content* --
   credentials, health, financial, precise location, media blobs. A hit
   refuses publication. It does not redact: a sanitizer that quietly edits a
   person's words is one that can quietly get it wrong.

The recipient inherits nothing but the substance
------------------------------------------------
Confidence, classification and supervision are the publisher's own judgement
about their own Bartholomew. They are removed, not copied. The recipient's
Bartholomew forms its own -- which is why adoption produces a *low-confidence
local candidate* rather than an accepted record, and why nothing here can
make a shared package into knowledge on the other side.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: The sanitization policy revision recorded on every package. Bumping it is
#: a deliberate act: a recipient inspecting an old package must be able to see
#: which rules produced it, and a rule change must not retroactively claim to
#: have applied to packages published before it existed.
POLICY_REVISION: int = 1

KIND_COMPETENCY = "competency"
KIND_CORRECTION = "correction"
KIND_HOUSEHOLD_ROUTINE = "household_routine"
KIND_GUIDANCE = "guidance"

#: The only package types that exist. There is deliberately **no generic
#: raw-memory package**: a share must be a thing someone chose to say, not a
#: slice of what Bartholomew happens to hold.
SHAREABLE_KINDS: frozenset[str] = frozenset(
    {KIND_COMPETENCY, KIND_CORRECTION, KIND_HOUSEHOLD_ROUTINE, KIND_GUIDANCE},
)

#: Memory kinds a share may be built from. The five S5.1 competency kinds,
#: minus `competency_evidence` -- evidence is a record of specific situations
#: and their outcomes, which is private source-event content by definition.
ELIGIBLE_SOURCE_KINDS: frozenset[str] = frozenset(
    {"competency_knowledge", "competency_procedure", "competency_heuristic"},
)

#: Kinds that are named as ineligible rather than merely absent, each with the
#: reason, so a future reader can see the exclusions were decided rather than
#: forgotten. `is_eligible_source` refuses anything outside
#: `ELIGIBLE_SOURCE_KINDS` regardless; this map exists to produce a truthful
#: refusal message.
INELIGIBLE_SOURCE_REASONS: dict[str, str] = {
    "fact": "a raw personal memory is never shareable",
    "event": "a raw personal memory is never shareable",
    "preference": "a raw personal memory is never shareable",
    "conversation": "raw conversation history is never shareable",
    "episode": "raw conversation and episodic history is never shareable",
    "inbound_event": "private source-event content is never shareable",
    "reflection": "Bartholomew's own reflections about one person are not shareable",
    "objective": "private objectives are never shareable",
    "personal_fact": "personal facts are never shareable",
    "competency_evidence": (
        "evidence records specific situations and outcomes -- private source-event content"
    ),
    "candidate_lesson": (
        "a candidate is an unreviewed inference; publishing one would export a "
        "guess as though it were something the publisher stood behind"
    ),
    "adopted_share_candidate": (
        "a candidate adopted from another group is not the publisher's to "
        "re-publish; re-sharing would launder provenance"
    ),
    "learning_acceptance_approval": "an internal approval credential is never shareable",
    "memory_export": "a complete memory-database export is never shareable",
}

#: The content projection, per package kind. An **allowlist**: any key of the
#: source record not named here is removed and recorded, whatever it is.
#:
#: Note what is absent from every one of them -- `competency_id`, `slug`,
#: `classification`, `confidence`, `supervision`, `provenance`, `created_at`,
#: `updated_at`, `reviewer`. Those are the publisher's own bookkeeping and
#: judgement, and none of it is the recipient's to inherit.
CONTENT_FIELDS: dict[str, frozenset[str]] = {
    KIND_COMPETENCY: frozenset(
        {"topic", "content", "name", "steps", "rule", "conditions", "counterexamples"},
    ),
    KIND_CORRECTION: frozenset({"rule", "conditions", "counterexamples", "topic", "content"}),
    KIND_HOUSEHOLD_ROUTINE: frozenset({"name", "steps", "conditions"}),
    KIND_GUIDANCE: frozenset({"topic", "content", "rule", "conditions"}),
}

#: Field names whose presence anywhere in a source record refuses publication
#: outright. Matched case-insensitively against the key, and against the key
#: with separators removed, so `api_key`, `apiKey` and `api key` are one rule.
#:
#: These are the categories the sharing contract names as never implicitly
#: shareable. They are enforced here, below any presentation layer, so a UI
#: that forgot to hide a field cannot become the reason one leaked.
PROHIBITED_FIELD_NAMES: frozenset[str] = frozenset(
    {
        # credentials and secrets, internal approval credentials
        "password",
        "passphrase",
        "secret",
        "token",
        "apikey",
        "accesskey",
        "privatekey",
        "credential",
        "credentials",
        "sessiontoken",
        "approval",
        "approver",
        "candidatefingerprint",
        "authorization",
        # raw memory, conversation, source events, exports
        "rawmemory",
        "memories",
        "memorydump",
        "memoryexport",
        "export",
        "transcript",
        "conversation",
        "messages",
        "episode",
        "episodes",
        "sourceevent",
        "payload",
        # media
        "screenshot",
        "screencapture",
        "image",
        "imagedata",
        "audio",
        "audiodata",
        "video",
        "frame",
        "attachment",
        # private objectives
        "objective",
        "objectiveid",
        "objectivetitle",
        # health
        "health",
        "diagnosis",
        "medication",
        "prescription",
        "medical",
        "symptom",
        "symptoms",
        # precise location
        "location",
        "coordinates",
        "latitude",
        "longitude",
        "gps",
        "address",
        "postcode",
        "locationhistory",
        # inferred relationships
        "relationship",
        "relationships",
        "contacts",
        "socialgraph",
        # financial
        "financial",
        "salary",
        "income",
        "iban",
        "bsb",
        "accountnumber",
        "cardnumber",
        "creditcard",
        "balance",
        "bank",
    },
)

#: Content patterns that refuse publication when they appear in a *surviving*
#: value. Complementary to the field-name rule: a rule of thumb whose text
#: happens to contain an API key is not made safe by the key being in a field
#: called `rule`.
#:
#: Deliberately narrow and high-signal. This is a backstop for material that
#: is unmistakably one of the prohibited categories, not a general classifier
#: -- a sanitizer that guesses would either refuse everything or lull a
#: publisher into thinking it had checked more than it had. What actually
#: keeps the categories out is the eligibility rule and the allowlist above.
PROHIBITED_CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("credential", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("credential", re.compile(r"\b(?:api[_ -]?key|access[_ -]?token|bearer)\b\s*[:=]", re.I)),
    ("credential", re.compile(r"\bpass(?:word|phrase)\b\s*[:=]", re.I)),
    ("financial", re.compile(r"\b(?:iban|bsb|account(?:\s+|_)?number)\b\s*[:=#]?\s*\w", re.I)),
    ("financial", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("precise_location", re.compile(r"[-+]?\d{1,3}\.\d{4,}\s*,\s*[-+]?\d{1,3}\.\d{4,}")),
    ("media", re.compile(r"data:(?:image|audio|video)/[a-z0-9.+-]+;base64,", re.I)),
    ("contact_identifier", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
)

#: Maximum serialised size of a package's content. A share is a rule of
#: thumb, a routine or a paragraph of guidance; anything approaching this is
#: not one of those, and an unbounded field is how a "share" becomes an
#: export.
MAX_CONTENT_BYTES = 16 * 1024

#: Maximum nesting depth scanned and permitted in a source record. A record
#: deeper than this is refused rather than partially scanned -- an unscanned
#: branch is exactly where a prohibited field would sit.
MAX_CONTENT_DEPTH = 8


class ShareEligibilityError(Exception):
    """The named source record may not enter sanitization at all.

    Raised before anything is inspected in detail: this is the "only
    explicitly selected eligible records" rule, and it is a different failure
    from a record that was eligible but turned out to be unpublishable.
    """


class SanitizationRefusedError(Exception):
    """Sanitization could not confidently produce an eligible package.

    A refusal, never a partial result. `categories` names which prohibited
    categories were found, so a publisher is told what is wrong without the
    refusal itself quoting the offending content back at them.
    """

    def __init__(self, message: str, *, categories: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.categories = categories


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def canonical_json(value: Any) -> str:
    """Stable JSON for hashing and storage. Sorted keys, no incidental space."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    """SHA-256 over the canonical form of `value`."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The source record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRecord:
    """One memory row a user has explicitly selected to share from.

    Carries the decoded record, not the row: this module never reads a
    database. `key` is the publisher's own memory key and is **not** part of
    any package -- it is here so the origin fingerprint can bind to the exact
    record, and so a refusal can name what was refused.
    """

    kind: str
    key: str
    value: dict[str, Any]

    def origin_fingerprint(self) -> str:
        """A stable digest of the record this share was cut from.

        Binds a package to its origin without carrying the origin. Two
        publications from the same unchanged record share a fingerprint;
        editing the record produces a different one, so "is this still the
        thing that was published?" is answerable on the publisher's side.
        """
        return fingerprint({"kind": self.kind, "key": self.key, "value": self.value})


def is_eligible_source(kind: str) -> bool:
    """Whether a memory kind may be shared from at all."""
    return kind in ELIGIBLE_SOURCE_KINDS


def require_eligible_source(record: SourceRecord) -> None:
    """Raise `ShareEligibilityError` unless this record may be sanitized.

    Default deny: an unrecognised kind is refused with the general reason,
    not admitted because nobody thought to list it.
    """
    if is_eligible_source(record.kind):
        return
    reason = INELIGIBLE_SOURCE_REASONS.get(
        record.kind,
        "only explicitly shareable competency records may be published to a trusted group",
    )
    raise ShareEligibilityError(
        f"memory kind {record.kind!r} cannot be shared: {reason}",
    )


def classify_share_kind(record: SourceRecord, requested_kind: str) -> str:
    """Confirm the requested package type matches what the record actually is.

    Step 2 of the sharing pipeline. The user names the type; the system
    checks it rather than trusting it, because the type decides which content
    allowlist applies, and a mislabelled record would be projected through the
    wrong one.
    """
    if requested_kind not in SHAREABLE_KINDS:
        raise ShareEligibilityError(
            f"{requested_kind!r} is not a shareable package type; "
            f"permitted types are {sorted(SHAREABLE_KINDS)}",
        )
    require_eligible_source(record)

    source_type = str((record.value.get("provenance") or {}).get("source_type") or "")
    if requested_kind == KIND_CORRECTION and source_type != "correction":
        raise ShareEligibilityError(
            "a correction package must be built from a record whose provenance "
            f"source_type is 'correction'; this record's is {source_type or 'unset'!r}",
        )
    if requested_kind == KIND_HOUSEHOLD_ROUTINE and record.kind != "competency_procedure":
        raise ShareEligibilityError(
            "a household routine must be built from a competency_procedure; "
            f"this record is a {record.kind!r}",
        )
    return requested_kind


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sanitization:
    """What the sanitizer did, recorded on the package itself.

    A recipient can see which policy produced what they are looking at and
    which of the publisher's fields did not travel -- by name only, never by
    value. `removed_fields` is the audit of the allowlist having been applied,
    not a hint about the content it removed.
    """

    policy_revision: int = POLICY_REVISION
    removed_fields: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_revision": self.policy_revision,
            "removed_fields": list(self.removed_fields),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Sanitization:
        return cls(
            policy_revision=int(data.get("policy_revision", POLICY_REVISION)),
            removed_fields=tuple(data.get("removed_fields") or ()),
        )


def _walk_prohibited_fields(node: Any, path: str, depth: int, found: list[str]) -> None:
    if depth > MAX_CONTENT_DEPTH:
        raise SanitizationRefusedError(
            f"record nests deeper than {MAX_CONTENT_DEPTH} levels; refusing rather than "
            "leaving a branch unscanned",
            categories=("unscannable",),
        )
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if _normalise_key(key) in PROHIBITED_FIELD_NAMES:
                found.append(here)
            _walk_prohibited_fields(value, here, depth + 1, found)
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            _walk_prohibited_fields(item, f"{path}[{index}]", depth + 1, found)


def prohibited_fields(record_value: dict[str, Any]) -> tuple[str, ...]:
    """Every prohibited field name present in a record, by path.

    Exposed so a UI can tell a user *why* a record cannot be shared before
    they try -- but the refusal itself is enforced in `sanitize`, below the
    UI, so a caller that never asks still cannot publish one.
    """
    found: list[str] = []
    _walk_prohibited_fields(record_value, "", 0, found)
    return tuple(found)


def _collect_text(node: Any, parts: list[str]) -> None:
    if isinstance(node, dict):
        for value in node.values():
            _collect_text(value, parts)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _collect_text(item, parts)
    elif node is not None and not isinstance(node, bool):
        parts.append(str(node))


def prohibited_content_categories(content: dict[str, Any]) -> tuple[str, ...]:
    """Prohibited categories detected in the *surviving* content values."""
    parts: list[str] = []
    _collect_text(content, parts)
    text = "\n".join(parts)
    hits: list[str] = []
    for category, pattern in PROHIBITED_CONTENT_PATTERNS:
        if pattern.search(text) and category not in hits:
            hits.append(category)
    return tuple(hits)


def sanitize(record: SourceRecord, share_kind: str) -> tuple[dict[str, Any], Sanitization]:
    """Project one eligible record into publishable content. Fails closed.

    Returns `(content, sanitization)`. Raises `SanitizationRefusedError` rather
    than returning a partial or redacted result, because "we removed the
    problem" is a claim this module is not in a position to make truthfully.

    Order matters and is asserted by the tests: prohibited fields are checked
    against the **whole source record** first, so a credential sitting in a
    field the allowlist would have dropped still refuses the publication
    rather than being quietly discarded.
    """
    offending = prohibited_fields(record.value)
    if offending:
        raise SanitizationRefusedError(
            "this record contains fields that are never shareable "
            f"({', '.join(sorted(offending))}); publication refused",
            categories=("prohibited_field",),
        )

    allowed = CONTENT_FIELDS.get(share_kind)
    if allowed is None:
        raise ShareEligibilityError(f"{share_kind!r} is not a shareable package type")

    content: dict[str, Any] = {}
    removed: list[str] = []
    for key in sorted(record.value):
        if key in allowed:
            content[key] = record.value[key]
        else:
            removed.append(key)

    if not content:
        raise SanitizationRefusedError(
            f"nothing in this record survives the {share_kind!r} content policy; "
            "there is no package to publish",
            categories=("empty",),
        )

    categories = prohibited_content_categories(content)
    if categories:
        raise SanitizationRefusedError(
            "the content that would be published matches categories that are never "
            f"shareable ({', '.join(categories)}); publication refused",
            categories=categories,
        )

    encoded = canonical_json(content)
    if len(encoded.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise SanitizationRefusedError(
            f"the sanitized content exceeds {MAX_CONTENT_BYTES} bytes; a trusted-group "
            "share is a rule, a routine or a paragraph of guidance, not a bulk transfer",
            categories=("oversized",),
        )

    return content, Sanitization(POLICY_REVISION, tuple(removed))


# ---------------------------------------------------------------------------
# The package
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustedSharePackage:
    """One sanitized thing, published to exactly one trusted group.

    The frozen logical structure, field for field. `content` is the sanitized
    projection and nothing else; `sanitization` says which policy produced it
    and what did not travel; `source_candidate_fingerprint` binds it to the
    publisher's own record without carrying that record.

    `publisher_user_id` is the **authenticated** publisher, supplied by the
    seam from a verified principal, never read from caller input -- the same
    rule `training.stamp_provenance` applies to `recorded_by`.
    """

    share_id: str
    group_id: str
    publisher_user_id: str
    source_candidate_fingerprint: str
    kind: str
    content: dict[str, Any] = field(default_factory=dict)
    sanitization: Sanitization = field(default_factory=Sanitization)
    revision: int = 1
    published_at: str = field(default_factory=_utcnow_iso)
    revoked_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "share_id": self.share_id,
            "group_id": self.group_id,
            "publisher_user_id": self.publisher_user_id,
            "source_candidate_fingerprint": self.source_candidate_fingerprint,
            "kind": self.kind,
            "content": self.content,
            "sanitization": self.sanitization.as_dict(),
            "revision": self.revision,
            "published_at": self.published_at,
            "revoked_at": self.revoked_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrustedSharePackage:
        return cls(
            share_id=data["share_id"],
            group_id=data["group_id"],
            publisher_user_id=data["publisher_user_id"],
            source_candidate_fingerprint=data["source_candidate_fingerprint"],
            kind=data["kind"],
            content=dict(data.get("content") or {}),
            sanitization=Sanitization.from_dict(data.get("sanitization") or {}),
            revision=int(data.get("revision", 1)),
            published_at=data.get("published_at") or _utcnow_iso(),
            revoked_at=data.get("revoked_at"),
        )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def content_hash(self) -> str:
        """A digest of what a recipient would actually adopt.

        Used to detect a concurrent revision (two publishers, or one publisher
        twice, producing different content for the same share) and to bind a
        recipient's adoption to the exact revision they inspected.
        """
        return fingerprint({"kind": self.kind, "content": self.content})

    def summary(self) -> str:
        """A one-line description for inbox listings and audit output.

        Names the type, the revision and the content digest. Deliberately not
        the content: a listing that quoted the content would make "inspect
        before adopting" a formality.
        """
        return (
            f"{self.kind} share {self.share_id} rev {self.revision} "
            f"({self.content_hash()[:12]})"
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.share_id:
            errors.append("share_id is required")
        if not self.group_id:
            errors.append("group_id is required -- a package belongs to exactly one group")
        if not self.publisher_user_id:
            errors.append("publisher_user_id is required -- publication is never anonymous")
        if not self.source_candidate_fingerprint:
            errors.append("source_candidate_fingerprint is required")
        if self.kind not in SHAREABLE_KINDS:
            errors.append(f"kind must be one of {sorted(SHAREABLE_KINDS)}, got {self.kind!r}")
        if self.revision < 1:
            errors.append("revision starts at 1")
        if not self.content:
            errors.append("a package with no content is not a package")
        if self.sanitization.policy_revision < 1:
            errors.append("sanitization.policy_revision is required")
        # Defence in depth: the package is re-checked against the field and
        # content rules on the way out, so a package assembled by some future
        # path that bypassed `propose()` still cannot carry a prohibited field.
        offending = prohibited_fields(self.content)
        if offending:
            errors.append(
                f"content carries never-shareable fields: {', '.join(sorted(offending))}",
            )
        categories = prohibited_content_categories(self.content)
        if categories:
            errors.append(f"content matches never-shareable categories: {', '.join(categories)}")
        return errors


def propose(
    record: SourceRecord,
    *,
    requested_kind: str,
    share_id: str,
    group_id: str,
    publisher_user_id: str,
    revision: int = 1,
    published_at: str | None = None,
) -> TrustedSharePackage:
    """The sanitizer's entry point: an eligible record -> a proposed package.

    Steps 1-4 of the sharing pipeline in one function -- eligibility,
    classification, sanitization, and the fail-closed refusal -- so there is
    no arrangement of calls that reaches a package without all four having
    run. Publication itself is a separate, explicit act
    (`share_exchange.publish`), because inspecting what would be shared and
    deciding to share it are two decisions.
    """
    share_kind = classify_share_kind(record, requested_kind)
    content, sanitization = sanitize(record, share_kind)
    package = TrustedSharePackage(
        share_id=share_id,
        group_id=group_id,
        publisher_user_id=publisher_user_id,
        source_candidate_fingerprint=record.origin_fingerprint(),
        kind=share_kind,
        content=content,
        sanitization=sanitization,
        revision=int(revision),
        published_at=published_at or _utcnow_iso(),
    )
    errors = package.validate()
    if errors:
        raise SanitizationRefusedError("; ".join(errors), categories=("invalid",))
    return package


__all__ = [
    "CONTENT_FIELDS",
    "ELIGIBLE_SOURCE_KINDS",
    "INELIGIBLE_SOURCE_REASONS",
    "KIND_COMPETENCY",
    "KIND_CORRECTION",
    "KIND_GUIDANCE",
    "KIND_HOUSEHOLD_ROUTINE",
    "MAX_CONTENT_BYTES",
    "POLICY_REVISION",
    "PROHIBITED_CONTENT_PATTERNS",
    "PROHIBITED_FIELD_NAMES",
    "SHAREABLE_KINDS",
    "Sanitization",
    "SanitizationRefusedError",
    "ShareEligibilityError",
    "SourceRecord",
    "TrustedSharePackage",
    "canonical_json",
    "classify_share_kind",
    "fingerprint",
    "is_eligible_source",
    "prohibited_content_categories",
    "prohibited_fields",
    "propose",
    "require_eligible_source",
    "sanitize",
]
