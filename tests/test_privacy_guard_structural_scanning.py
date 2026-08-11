"""
Regression tests for the two `privacy_guard.is_sensitive()` defects found
while integrating S5.2's training seam (both pre-existing, neither specific
to competency records).

Part 1 -- lexical: matching was an unanchored substring scan, so ordinary
English tripped the consent gate. Now word-boundary anchored, with explicit
protection for the two ways naive anchoring would have *weakened* the gate
(plurals, and compound terms substring matching caught incidentally).

Part 2 -- structural: the gate scans the stored representation, which for
JSON includes schema key names, so records were flagged on the shape of
their schema rather than their content. Schema keys of *registered* kinds
are now excluded; all values, and all unregistered keys, are still scanned.

The tests below are deliberately paired: for every false positive removed
there is a test proving the corresponding true positive survives. A change
that made this file's "not sensitive" tests pass by weakening the gate would
fail its "still sensitive" tests.
"""

from __future__ import annotations

import json

import pytest

from bartholomew.kernel.competency import (
    COMPETENCY_KINDS,
    COMPETENCY_SCHEMA_KEYS,
    CompetencyEnvelope,
    CompetencyEvidence,
    CompetencyHeuristic,
    CompetencyKnowledge,
    CompetencyProcedure,
    CompetencyRecord,
)
from bartholomew.kernel.memory.privacy_guard import (
    SENSITIVE_COMPOUND_TERMS,
    SENSITIVE_KEYWORDS,
    is_sensitive,
    register_structural_schema,
    registered_schema_keys,
    registered_structural_kinds,
)


def _envelope() -> CompetencyEnvelope:
    return CompetencyEnvelope(competency_id="estate_management")


# ---------------------------------------------------------------------------
# Part 1a: lexical false positives are gone.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "previously_matched"),
    [
        ("The memory allocation failed", "location"),
        ("Relocation of the server", "location"),
        ("The company went bankrupt", "bank"),
        ("Accounting for the variance", "account"),
        ("He was held accountable", "account"),
        ("Healthcare is unrelated here", "health"),
        ("Rename the column", "name"),
        ("The filename is report.pdf", "name"),
    ],
)
def test_ordinary_english_no_longer_trips_the_gate(text, previously_matched):
    assert previously_matched in text.lower()  # the old substring scan matched here
    assert is_sensitive(text) is False, f"{text!r} still spuriously flagged"


# ---------------------------------------------------------------------------
# Part 1b: the two weakenings naive boundary matching would have introduced.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "her addresses are listed below",  # plural: naive \baddress\b would miss
        "patient names",  # plural: naive \bname\b would miss
        "the bank accounts",
        "their phone numbers",
    ],
)
def test_plurals_still_trip_the_gate(text):
    """Naive word-boundary matching would have silently stopped catching
    these. If this fails, the boundary correction has weakened the gate."""
    assert is_sensitive(text) is True


@pytest.mark.parametrize("text", ["my username is bob", "surname: Smith", "her nickname"])
def test_compound_terms_still_trip_the_gate(text):
    """Substring matching caught these incidentally via `name`. They are
    enumerated explicitly so boundary matching does not lose them."""
    assert is_sensitive(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "my password is hunter2",
        "the bank account number is 12345678",
        "her email address",
        "my daily routine starts at 6am",
    ],
)
def test_plain_true_positives_are_unchanged(text):
    assert is_sensitive(text) is True


def test_compound_terms_each_contain_a_base_keyword():
    """SENSITIVE_COMPOUND_TERMS exists to preserve what substring matching
    already caught -- not to broaden the vocabulary. Each entry must be a
    compound of an existing keyword, or it is a new policy decision."""
    for term in SENSITIVE_COMPOUND_TERMS:
        assert any(keyword in term for keyword in SENSITIVE_KEYWORDS), (
            f"{term!r} is not a compound of any existing keyword -- adding it widens "
            "the sensitivity vocabulary, which is a governance decision, not this fix"
        )


# ---------------------------------------------------------------------------
# Part 2a: registered schema keys are structure, not content.
# ---------------------------------------------------------------------------
class TestRegisteredStructuralKinds:
    def test_all_five_competency_kinds_are_registered(self):
        assert set(COMPETENCY_KINDS) <= registered_structural_kinds()
        for kind in COMPETENCY_KINDS:
            assert registered_schema_keys(kind) == COMPETENCY_SCHEMA_KEYS

    @pytest.mark.parametrize(
        ("kind", "record_factory"),
        [
            ("competency", lambda: CompetencyRecord(envelope=_envelope(), name="Estate Mgmt")),
            (
                "competency_procedure",
                lambda: CompetencyProcedure(
                    envelope=_envelope(),
                    slug="q",
                    name="Quote comparison",
                    steps=["Get at least three quotes"],
                ),
            ),
            (
                "competency_knowledge",
                lambda: CompetencyKnowledge(
                    envelope=_envelope(),
                    slug="b",
                    topic="Boilers",
                    content="Bleed annually",
                ),
            ),
            (
                "competency_heuristic",
                lambda: CompetencyHeuristic(
                    envelope=_envelope(),
                    slug="w",
                    rule="Check warranty first",
                ),
            ),
            (
                "competency_evidence",
                lambda: CompetencyEvidence(
                    envelope=_envelope(),
                    slug="r",
                    situation="Boiler failed",
                ),
            ),
        ],
    )
    def test_benign_competency_records_are_not_sensitive(self, kind, record_factory):
        """`competency` and `competency_procedure` were previously flagged on
        their `"name"` schema key alone."""
        payload = json.dumps(record_factory().to_dict())
        assert is_sensitive(payload, kind=kind) is False


# ---------------------------------------------------------------------------
# Part 2b: values are still scanned in full -- the no-bypass invariant.
# ---------------------------------------------------------------------------
class TestValuesAlwaysScanned:
    def test_sensitive_content_inside_a_registered_record_still_requires_consent(self):
        record = CompetencyProcedure(
            envelope=_envelope(),
            slug="utility",
            name="Utility account access",
            steps=["The electricity account number is 12345678 and the password is hunter2"],
        )
        payload = json.dumps(record.to_dict())
        assert is_sensitive(payload, kind="competency_procedure") is True

    def test_sensitive_content_in_any_value_position_is_caught(self):
        """Every field is content, not just the obvious one."""
        for field_kwargs in (
            {"situation": "her email address is bob@example.com"},
            {"action_taken": "looked up the bank account"},
            {"outcome": "recorded their phone"},
            {"lesson": "never store a password"},
        ):
            fields = {"situation": "boiler failed", **field_kwargs}
            record = CompetencyEvidence(envelope=_envelope(), slug="e", **fields)
            payload = json.dumps(record.to_dict())
            assert is_sensitive(payload, kind="competency_evidence") is True, field_kwargs

    def test_unregistered_keys_nested_in_a_registered_record_are_scanned(self):
        """Only the registered schema keys are exempt. User-chosen keys --
        here the area names inside `proficiency.by_area` -- are content."""
        record = CompetencyRecord(envelope=_envelope(), name="Estate Mgmt")
        record.proficiency = {"overall": 0.3, "by_area": {"bank account handling": 0.2}}
        payload = json.dumps(record.to_dict())
        assert is_sensitive(payload, kind="competency") is True


# ---------------------------------------------------------------------------
# Part 2c: unregistered structured data keeps conservative key-and-value
# scanning. This pins the failure mode of the rejected "scan values, not
# keys" fix, which would have been a real consent bypass.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        '{"password": "hunter2"}',
        '{"email": "bob@example.com"}',
        '{"name": "Alice Smith"}',
        '{"account": "12345678"}',
    ],
)
def test_unregistered_json_is_still_flagged_by_its_keys(payload):
    """The key is the only sensitivity signal here -- the value alone
    contains no keyword. A values-only scan would miss all of these."""
    assert is_sensitive(payload) is True
    assert is_sensitive(payload, kind="some_unregistered_kind") is True


def test_registered_kind_with_non_json_value_falls_back_to_raw_scanning():
    """A registered kind whose stored value is not the structured shape (a
    summary substitution, say) must not silently skip scanning."""
    assert is_sensitive("her email address is bob@example.com", kind="competency") is True


# ---------------------------------------------------------------------------
# Part 2d: the schema-key inventory guard.
# ---------------------------------------------------------------------------
def test_registered_schema_keys_contain_no_unreviewed_sensitivity_indicator():
    """
    Excluding schema keys is only safe while no registered schema has a key
    that is itself the sole signal that its value is sensitive.

    Today exactly one registered key collides with the vocabulary: `name`,
    which in this schema labels a competency or procedure title, never a
    person. If a future schema change adds (say) a `password` or `email`
    field, this fails -- at which point that kind must be de-registered or
    the key treated as content, NOT added to this allowlist to make the test
    pass again.
    """
    reviewed_and_approved = {"name"}

    colliding = {key for key in COMPETENCY_SCHEMA_KEYS if is_sensitive(key)}

    assert colliding == reviewed_and_approved, (
        f"registered competency schema keys collide with the sensitivity vocabulary in an "
        f"unreviewed way: {sorted(colliding - reviewed_and_approved)}. Excluding such a key "
        "would hide the only signal that its value is sensitive."
    )


def test_schema_keys_are_derived_not_hand_listed():
    """The registration is computed from the dataclasses, so it cannot drift
    from the real serialised shape."""
    record = CompetencyProcedure(
        envelope=_envelope(),
        slug="q",
        name="Quote comparison",
        steps=["step"],
    )
    assert set(record.to_dict()) <= COMPETENCY_SCHEMA_KEYS


def test_registering_a_kind_does_not_affect_other_kinds():
    register_structural_schema("_probe_kind", {"harmless_key"})
    try:
        assert is_sensitive('{"harmless_key": "nothing"}', kind="_probe_kind") is False
        # A different kind is unaffected and still conservative.
        assert is_sensitive('{"harmless_key": "nothing"}', kind="competency") is False
        assert is_sensitive('{"password": "x"}', kind="_probe_kind") is True
    finally:
        from bartholomew.kernel.memory.privacy_guard import _SCHEMA_KEYS_BY_KIND

        _SCHEMA_KEYS_BY_KIND.pop("_probe_kind", None)
