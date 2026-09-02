"""
Package E: what may be published to a trusted group, and what happens after.

Two halves, both against the real control-plane database:

* the **sanitizer** (`bartholomew.kernel.trusted_share`) -- eligibility,
  prohibited fields, the content allowlist and the fail-closed refusal; and
* the **exchange** (`bartholomew.platform.share_exchange`) -- explicit
  publication to one named group, the inbox, revisions, concurrency,
  revocation, and what a removed member can still reach.

The recurring question is not "does publishing work" but "what escapes". The
sanitizer is enforced below any UI, so every test here calls the library
directly: if a rule only held in a form, these tests would pass while the
rule did nothing.
"""

from __future__ import annotations

import tempfile

import pytest

from bartholomew.kernel import trusted_share as ts  # noqa: E402
from bartholomew.platform import accounts  # noqa: E402
from bartholomew.platform import share_exchange as sx  # noqa: E402
from bartholomew.platform import trusted_groups as tg  # noqa: E402
from bartholomew.platform.store import init_platform_schema  # noqa: E402

PASSWORD = "alpha-participant-password"


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="e-share-")
    for var, value in {
        "BARTH_PLATFORM_DB_PATH": "<tmp>/platform.db",
        "BARTH_DATA_ROOT": "<tmp>/data",
    }.items():
        mp.setenv(var, value.replace("<tmp>", tmp))
    yield
    mp.undo()


@pytest.fixture(scope="module", autouse=True)
def _schema():
    init_platform_schema()


@pytest.fixture(scope="module")
def users():
    init_platform_schema()
    made = {}
    for name in ("alice", "bob", "carol"):
        try:
            made[name] = accounts.create_account(name, PASSWORD)
        except accounts.AccountError:
            made[name] = next(
                a["user_id"] for a in accounts.list_accounts() if a["username"] == name
            )
    return made


@pytest.fixture
def household(users):
    """Alice owns it, Bob has accepted. Carol is outside."""
    group_id = tg.create_group(users["alice"], "Household")
    invitation = tg.invite(group_id, users["bob"], actor_user_id=users["alice"])
    tg.accept_invitation(invitation, actor_user_id=users["bob"])
    return group_id


def heuristic_record(rule="Book the boiler service in early autumn, before the rush."):
    """A realistic S5.1 heuristic, envelope and all.

    The envelope matters: `provenance.detail` here is exactly the free text
    that would re-identify the publisher, and `confidence`/`classification`
    are their judgement about their own Bartholomew. Every one of them must
    come off before publication.
    """
    return ts.SourceRecord(
        kind="competency_heuristic",
        key="home_maintenance.boiler",
        value={
            "competency_id": "home_maintenance",
            "slug": "boiler",
            "rule": rule,
            "conditions": "Annual gas boiler servicing.",
            "counterexamples": [],
            "classification": "personal",
            "confidence": 0.8,
            "supervision": {"requires_review": False},
            "provenance": {
                "source_type": "user_instruction",
                "detail": "Taylor told me this on 3 May while we were in the kitchen",
                "recorded_by": "user",
                "recorded_at": "2026-05-03T00:00:00Z",
            },
        },
    )


# ---------------------------------------------------------------------------
# 17: only explicitly selected eligible records may enter sanitization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        "fact",
        "event",
        "preference",
        "conversation",
        "episode",
        "inbound_event",
        "reflection",
        "objective",
        "personal_fact",
        "competency_evidence",
        "candidate_lesson",
        "adopted_share_candidate",
        "learning_acceptance_approval",
        "memory_export",
        "something_nobody_thought_of",
    ],
)
def test_ineligible_source_kinds_never_reach_the_sanitizer(kind):
    """17. Default deny: an unrecognised kind is refused with the general reason.

    Raw memory, conversation history, inbound events, reflections,
    objectives, personal facts, approvals and candidate rows are refused
    structurally -- not filtered out later, never admitted. There is
    deliberately no generic "share this memory" package type.
    """
    record = ts.SourceRecord(kind=kind, key="k", value={"rule": "anything"})
    with pytest.raises(ts.ShareEligibilityError):
        ts.require_eligible_source(record)
    with pytest.raises(ts.ShareEligibilityError):
        ts.propose(
            record,
            requested_kind=ts.KIND_COMPETENCY,
            share_id="s",
            group_id="g",
            publisher_user_id="u",
        )


def test_there_is_no_generic_raw_memory_package_type():
    """17. The four package types are the whole vocabulary."""
    assert ts.SHAREABLE_KINDS == {
        "competency",
        "correction",
        "household_routine",
        "guidance",
    }
    for invented in ("memory", "raw", "everything", "conversation"):
        with pytest.raises(ts.ShareEligibilityError):
            ts.classify_share_kind(heuristic_record(), invented)


def test_the_requested_package_type_is_checked_against_the_record(users, household):
    """17. The user names the type; the system checks it rather than trusting it.

    The type decides which content allowlist applies, so a mislabelled record
    would be projected through the wrong one.
    """
    record = heuristic_record()
    with pytest.raises(ts.ShareEligibilityError, match="correction"):
        ts.classify_share_kind(record, ts.KIND_CORRECTION)
    with pytest.raises(ts.ShareEligibilityError, match="competency_procedure"):
        ts.classify_share_kind(record, ts.KIND_HOUSEHOLD_ROUTINE)
    assert ts.classify_share_kind(record, ts.KIND_COMPETENCY) == ts.KIND_COMPETENCY


def test_a_non_member_cannot_even_sanitize_against_a_group(users, household):
    """17. The sanitizer is not an oracle about a group you are not in."""
    with pytest.raises(tg.GroupAccessError):
        sx.propose(
            heuristic_record(),
            requested_kind=ts.KIND_COMPETENCY,
            group_id=household,
            publisher_user_id=users["carol"],
        )


# ---------------------------------------------------------------------------
# 18: prohibited private fields fail publication, below the UI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("password", "hunter2"),
        ("api_key", "sk-live-abc"),
        ("credentials", {"user": "x"}),
        ("approval", {"approver": "taylor"}),
        ("transcript", "we talked about it"),
        ("conversation", ["hello"]),
        ("screenshot", "iVBORw0KGgo="),
        ("audio", "blob"),
        ("objective_id", 17),
        ("diagnosis", "something private"),
        ("medication", "a prescription"),
        ("location", "-33.8688,151.2093"),
        ("coordinates", [1.0, 2.0]),
        ("address", "1 Example Street"),
        ("relationships", ["neighbour"]),
        ("account_number", "12345678"),
        ("salary", 100000),
        ("memory_export", {"rows": []}),
        ("payload", {"raw": "source event"}),
    ],
)
def test_a_prohibited_field_refuses_the_whole_publication(field, value):
    """18. Refused outright, not quietly stripped.

    Stripping would mean the publisher believed they had shared something and
    had not -- or, worse, that a near-miss field name silently became the
    thing that leaked. If a record contains a credential, the answer is "not
    shareable", not "shareable minus the credential".
    """
    record = heuristic_record()
    record.value[field] = value
    with pytest.raises(ts.SanitizationRefusedError) as caught:
        ts.sanitize(record, ts.KIND_COMPETENCY)
    assert caught.value.categories == ("prohibited_field",)
    assert field in str(caught.value)


def test_a_prohibited_field_nested_anywhere_still_refuses():
    """18. At any depth, including inside a list -- an unscanned branch is where it would sit."""
    record = heuristic_record()
    record.value["counterexamples"] = [{"note": "fine"}, {"detail": {"password": "hunter2"}}]
    with pytest.raises(ts.SanitizationRefusedError):
        ts.sanitize(record, ts.KIND_COMPETENCY)


def test_field_name_matching_ignores_separators_and_case():
    """18. `apiKey`, `api_key` and `API KEY` are one rule, not three."""
    for spelling in ("apiKey", "api_key", "API-KEY", "Api Key"):
        record = heuristic_record()
        record.value[spelling] = "sk-live"
        with pytest.raises(ts.SanitizationRefusedError):
            ts.sanitize(record, ts.KIND_COMPETENCY)


def test_prohibited_content_refuses_even_in_an_allowed_field():
    """18. A rule of thumb is not made safe by the key it sits under.

    The field-name rule and the content rule are complementary: a credential
    pasted into `rule` survives the allowlist and must still refuse.
    """
    cases = {
        "credential": "Use api_key: sk-live-9f3a when calling the plumber's portal",
        "financial": "Pay it from iban: GB29NWBK60161331926819",
        "precise_location": "The valve is at -33.868800, 151.209300",
        "media": "See data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==",
        "contact_identifier": "Email the plumber at someone@example.com",
    }
    for category, rule in cases.items():
        with pytest.raises(ts.SanitizationRefusedError) as caught:
            ts.sanitize(heuristic_record(rule), ts.KIND_COMPETENCY)
        assert category in caught.value.categories


def test_a_package_assembled_by_hand_still_cannot_carry_a_prohibited_field():
    """18. Defence in depth: `validate()` re-checks on the way out.

    A future code path that assembled a package without going through
    `propose()` must not be able to publish one either.
    """
    package = ts.TrustedSharePackage(
        share_id="s",
        group_id="g",
        publisher_user_id="u",
        source_candidate_fingerprint="f",
        kind=ts.KIND_COMPETENCY,
        content={"rule": "fine", "password": "hunter2"},
    )
    errors = package.validate()
    assert any("never-shareable fields" in error for error in errors)


def test_publication_refuses_an_invalid_package(users, household):
    """18. The exchange will not store what the sanitizer would have refused."""
    package = ts.TrustedSharePackage(
        share_id="s",
        group_id=household,
        publisher_user_id=users["alice"],
        source_candidate_fingerprint="f",
        kind=ts.KIND_COMPETENCY,
        content={"rule": "fine", "secret": "hunter2"},
    )
    with pytest.raises(sx.PublicationError):
        sx.publish(package, publisher_user_id=users["alice"], confirm_group_id=household)


# ---------------------------------------------------------------------------
# 19: sanitization records its policy revision and removed fields
# ---------------------------------------------------------------------------


def test_sanitization_records_its_policy_revision_and_what_it_removed():
    """19. And what it removed is the publisher's envelope, by name only."""
    content, sanitization = ts.sanitize(heuristic_record(), ts.KIND_COMPETENCY)

    assert sanitization.policy_revision == ts.POLICY_REVISION == 1
    assert set(sanitization.removed_fields) == {
        "competency_id",
        "slug",
        "classification",
        "confidence",
        "supervision",
        "provenance",
    }
    assert set(content) == {"rule", "conditions", "counterexamples"}


def test_the_publishers_provenance_and_judgement_never_travel():
    """19. Nothing in the package re-identifies the publisher or inherits their confidence.

    `provenance.detail` is the free text that says who said what, and where;
    `confidence` and `classification` are the publisher's judgement about
    their own Bartholomew. The recipient forms their own -- which is why
    adoption produces a low-confidence local candidate rather than a copy.
    """
    package = ts.propose(
        heuristic_record(),
        requested_kind=ts.KIND_COMPETENCY,
        share_id="s",
        group_id="g",
        publisher_user_id="u",
    )
    rendered = ts.canonical_json(package.content)
    assert "Taylor" not in rendered
    assert "kitchen" not in rendered
    assert "3 May" not in rendered
    assert "confidence" not in package.content
    assert "classification" not in package.content
    assert "provenance" not in package.content


def test_the_origin_fingerprint_binds_the_package_without_carrying_the_origin():
    """19. Same record, same fingerprint; edited record, different one."""
    first = heuristic_record()
    second = heuristic_record()
    assert first.origin_fingerprint() == second.origin_fingerprint()

    edited = heuristic_record("Book it in August instead.")
    assert edited.origin_fingerprint() != first.origin_fingerprint()
    assert first.key not in first.origin_fingerprint()


def test_an_oversized_package_is_refused():
    """19. A share is a rule, a routine or a paragraph -- not a bulk transfer."""
    record = heuristic_record("x" * (ts.MAX_CONTENT_BYTES + 1))
    with pytest.raises(ts.SanitizationRefusedError) as caught:
        ts.sanitize(record, ts.KIND_COMPETENCY)
    assert "oversized" in caught.value.categories


def test_a_record_with_nothing_publishable_is_refused():
    """19. An empty projection is not a package, and is refused rather than published."""
    record = ts.SourceRecord(
        kind="competency_heuristic",
        key="k",
        value={"competency_id": "c", "slug": "s", "confidence": 0.5},
    )
    with pytest.raises(ts.SanitizationRefusedError) as caught:
        ts.sanitize(record, ts.KIND_COMPETENCY)
    assert "empty" in caught.value.categories


# ---------------------------------------------------------------------------
# 20: publishing requires explicit confirmation, for one specified group
# ---------------------------------------------------------------------------


def test_proposing_writes_nothing(users, household):
    """20. Inspecting what would be shared and sharing it are two decisions."""
    package = sx.propose(
        heuristic_record(),
        requested_kind=ts.KIND_COMPETENCY,
        group_id=household,
        publisher_user_id=users["alice"],
    )
    assert sx.inbox(users["bob"], group_id=household) == []
    with pytest.raises(sx.ShareNotFoundError):
        sx.inspect(package.share_id, actor_user_id=users["bob"])


def test_publishing_requires_the_destination_group_to_be_confirmed(users, household):
    """20. One package, one group, one explicit confirmation.

    `confirm_group_id` is a second statement of where this is going, made
    after the publisher has seen the sanitized content -- so publishing
    cannot be a button that inherits a group from three screens ago.
    """
    other = tg.create_group(users["alice"], "Book Club")
    package = sx.propose(
        heuristic_record(),
        requested_kind=ts.KIND_COMPETENCY,
        group_id=household,
        publisher_user_id=users["alice"],
    )
    with pytest.raises(sx.PublicationError, match="confirm"):
        sx.publish(package, publisher_user_id=users["alice"], confirm_group_id=other)
    assert sx.inbox(users["bob"], group_id=household) == []

    sx.publish(package, publisher_user_id=users["alice"], confirm_group_id=household)
    assert [entry.package.share_id for entry in sx.inbox(users["bob"], group_id=household)] == [
        package.share_id,
    ]


def test_a_package_is_not_a_bearer_instrument(users, household):
    """20. Holding a package does not let another account publish it."""
    invitation = tg.invite(household, users["carol"], actor_user_id=users["alice"])
    tg.accept_invitation(invitation, actor_user_id=users["carol"])
    package = sx.propose(
        heuristic_record(),
        requested_kind=ts.KIND_COMPETENCY,
        group_id=household,
        publisher_user_id=users["alice"],
    )
    with pytest.raises(sx.PublicationError, match="publisher"):
        sx.publish(package, publisher_user_id=users["carol"], confirm_group_id=household)


def test_publishing_twice_over_one_share_id_is_refused(users, household):
    """20. An update is offered, never substituted -- so it goes through `publish_revision`."""
    package = sx.propose(
        heuristic_record(),
        requested_kind=ts.KIND_COMPETENCY,
        group_id=household,
        publisher_user_id=users["alice"],
    )
    sx.publish(package, publisher_user_id=users["alice"], confirm_group_id=household)
    with pytest.raises(sx.PublicationError, match="publish_revision"):
        sx.publish(package, publisher_user_id=users["alice"], confirm_group_id=household)


# ---------------------------------------------------------------------------
# 21: recipients can inspect, decline or adopt
# ---------------------------------------------------------------------------


def _published(users, household, rule=None):
    package = sx.propose(
        heuristic_record(rule) if rule else heuristic_record(),
        requested_kind=ts.KIND_COMPETENCY,
        group_id=household,
        publisher_user_id=users["alice"],
    )
    return sx.publish(package, publisher_user_id=users["alice"], confirm_group_id=household)


def test_delivery_is_not_adoption(users, household):
    """21. An inbox entry is something to decide about, not something taken."""
    package = _published(users, household)
    entry = sx.inbox(users["bob"], group_id=household)[0]
    assert entry.state == sx.RECEIPT_DELIVERED
    assert entry.adopted_revision is None
    assert entry.package.share_id == package.share_id


def test_a_recipient_can_inspect_decline_or_adopt(users, household):
    """21. Three distinct outcomes, each recorded."""
    declined = _published(users, household, "Bleed the radiators in October.")
    adopted = _published(users, household, "Service the boiler in September.")

    assert sx.inspect(declined.share_id, actor_user_id=users["bob"]).content

    sx.decline(declined.share_id, actor_user_id=users["bob"])
    inbox = sx.inbox(users["bob"], group_id=household)
    states = {e.package.share_id: e.state for e in inbox}
    assert states[declined.share_id] == sx.RECEIPT_DECLINED

    taken = sx.adopt(adopted.share_id, actor_user_id=users["bob"])
    assert taken.revision == 1
    states = {
        e.package.share_id: (e.state, e.adopted_revision)
        for e in sx.inbox(users["bob"], group_id=household)
    }
    assert states[adopted.share_id] == (sx.RECEIPT_ADOPTED, 1)


def test_a_publisher_does_not_receive_their_own_share(users, household):
    """21. An inbox is what other people sent you."""
    package = _published(users, household)
    assert package.share_id not in {
        e.package.share_id for e in sx.inbox(users["alice"], group_id=household)
    }


def test_a_non_member_can_neither_inspect_nor_adopt(users, household):
    """15/21. And the refusal does not confirm that the share id is real."""
    package = _published(users, household)
    with pytest.raises(sx.ShareNotFoundError):
        sx.inspect(package.share_id, actor_user_id=users["carol"])
    with pytest.raises(sx.ShareNotFoundError):
        sx.adopt(package.share_id, actor_user_id=users["carol"])
    assert package.share_id not in {e.package.share_id for e in sx.inbox(users["carol"])}


def test_a_removed_member_immediately_loses_access_to_shared_packages(users, household):
    """16. Removal reaches the shared material too, on the next call.

    Membership is re-derived on every read rather than cached, which is what
    makes it immediate: there is no invalidation step to forget.
    """
    package = _published(users, household)
    assert package.share_id in {
        entry.package.share_id for entry in sx.inbox(users["bob"], group_id=household)
    }

    tg.remove_member(household, users["bob"], actor_user_id=users["alice"])

    # Asserted across Bob's whole inbox, not just this group's: he is still a
    # member of the other households these tests create, and removal must
    # take exactly the access it removed and no more.
    assert package.share_id not in {entry.package.share_id for entry in sx.inbox(users["bob"])}
    with pytest.raises(tg.GroupAccessError):
        sx.inbox(users["bob"], group_id=household)
    with pytest.raises(sx.ShareNotFoundError):
        sx.inspect(package.share_id, actor_user_id=users["bob"])
    with pytest.raises(sx.ShareNotFoundError):
        sx.adopt(package.share_id, actor_user_id=users["bob"])


# ---------------------------------------------------------------------------
# 24-25: revisions, forks and concurrency
# ---------------------------------------------------------------------------


def test_a_publisher_update_does_not_overwrite_what_a_recipient_adopted(users, household):
    """24. A new revision is a new row, offered as a proposal.

    The recipient's adopted revision keeps existing exactly as they adopted
    it -- so "the publisher changed their mind" arrives as something to look
    at, never as a silent substitution.
    """
    package = _published(users, household)
    sx.adopt(package.share_id, actor_user_id=users["bob"])
    sx.mark_local_fork(package.share_id, actor_user_id=users["bob"])

    sx.publish_revision(
        package.share_id,
        heuristic_record("Book it in August instead."),
        requested_kind=ts.KIND_COMPETENCY,
        publisher_user_id=users["alice"],
        expected_revision=1,
    )

    entry = next(e for e in sx.inbox(users["bob"]) if e.package.share_id == package.share_id)
    assert entry.adopted_revision == 1
    assert entry.latest_revision == 2
    assert entry.has_pending_update
    assert entry.local_fork
    assert entry.state == sx.RECEIPT_ADOPTED

    # Revision 1 is still readable, byte for byte, as adopted.
    original = sx.inspect(package.share_id, actor_user_id=users["bob"], revision=1)
    assert original.content == package.content
    assert len(sx.revisions(package.share_id, actor_user_id=users["bob"])) == 2


def test_a_concurrent_revision_is_detected_and_writes_nothing(users, household):
    """25. No last-write-wins, and no force flag to reach for."""
    package = _published(users, household)
    sx.publish_revision(
        package.share_id,
        heuristic_record("Second revision."),
        requested_kind=ts.KIND_COMPETENCY,
        publisher_user_id=users["alice"],
        expected_revision=1,
    )

    with pytest.raises(sx.ConcurrentRevisionError):
        sx.publish_revision(
            package.share_id,
            heuristic_record("A third, written against a stale read."),
            requested_kind=ts.KIND_COMPETENCY,
            publisher_user_id=users["alice"],
            expected_revision=1,
        )
    assert len(sx.revisions(package.share_id, actor_user_id=users["alice"])) == 2


def test_only_the_original_publisher_may_revise(users, household):
    """25. Membership is not authority over somebody else's publication."""
    invitation = tg.invite(household, users["carol"], actor_user_id=users["alice"])
    tg.accept_invitation(invitation, actor_user_id=users["carol"])
    package = _published(users, household)
    with pytest.raises(sx.PublicationError, match="original publisher"):
        sx.publish_revision(
            package.share_id,
            heuristic_record("Carol's edit."),
            requested_kind=ts.KIND_COMPETENCY,
            publisher_user_id=users["carol"],
            expected_revision=1,
        )


def test_a_revision_does_not_reset_a_recipients_existing_decision(users, household):
    """24. A declined share stays declined; a new revision is an update, not a re-ask."""
    package = _published(users, household)
    sx.decline(package.share_id, actor_user_id=users["bob"])
    sx.publish_revision(
        package.share_id,
        heuristic_record("Revised."),
        requested_kind=ts.KIND_COMPETENCY,
        publisher_user_id=users["alice"],
        expected_revision=1,
    )
    entry = next(e for e in sx.inbox(users["bob"]) if e.package.share_id == package.share_id)
    assert entry.state == sx.RECEIPT_DECLINED


# ---------------------------------------------------------------------------
# 26-28: revocation
# ---------------------------------------------------------------------------


def test_revocation_blocks_new_adoption_and_further_revisions(users, household):
    """26. Withdrawn means withdrawn, in both directions."""
    package = _published(users, household)
    sx.revoke(package.share_id, actor_user_id=users["alice"])

    with pytest.raises(sx.AdoptionRefusedError):
        sx.adopt(package.share_id, actor_user_id=users["bob"])
    with pytest.raises(sx.PublicationError, match="revoked"):
        sx.publish_revision(
            package.share_id,
            heuristic_record("Too late."),
            requested_kind=ts.KIND_COMPETENCY,
            publisher_user_id=users["alice"],
            expected_revision=1,
        )
    entry = next(e for e in sx.inbox(users["bob"]) if e.package.share_id == package.share_id)
    assert not entry.has_pending_update, "a withdrawn share must not advertise an available update"


def test_revocation_stays_visible_in_recipient_provenance(users, household):
    """27. A recipient must be able to see that what they took has been withdrawn."""
    package = _published(users, household)
    sx.adopt(package.share_id, actor_user_id=users["bob"])
    sx.revoke(package.share_id, actor_user_id=users["alice"])

    provenance = sx.provenance(package.share_id, actor_user_id=users["bob"])
    assert provenance["revoked"] is True
    assert provenance["revoked_at"]
    assert provenance["adopted_revision"] == 1
    assert sx.inspect(package.share_id, actor_user_id=users["bob"]).is_revoked


def test_revocation_does_not_delete_the_recipients_adoption(users, household):
    """28. The publisher gets "un-share", not a remote delete on someone's memory.

    The receipt still says the recipient adopted revision 1, and the package
    they adopted is still readable. Anything they created locally from it is
    in their own runtime, which this module has no write path to at all.
    """
    package = _published(users, household)
    sx.adopt(package.share_id, actor_user_id=users["bob"])
    sx.revoke(package.share_id, actor_user_id=users["alice"])

    entry = next(e for e in sx.inbox(users["bob"]) if e.package.share_id == package.share_id)
    assert entry.state == sx.RECEIPT_ADOPTED
    assert entry.adopted_revision == 1
    assert sx.inspect(package.share_id, actor_user_id=users["bob"], revision=1).content


def test_only_the_publisher_may_revoke(users, household):
    """26. And a non-publisher's refusal does not confirm the share exists."""
    package = _published(users, household)
    with pytest.raises(sx.ShareNotFoundError):
        sx.revoke(package.share_id, actor_user_id=users["bob"])
    assert not sx.inspect(package.share_id, actor_user_id=users["bob"]).is_revoked


# ---------------------------------------------------------------------------
# 29: audit and provenance identify without exposing
# ---------------------------------------------------------------------------


def test_the_audit_trail_names_users_groups_revisions_and_hashes_but_no_content(
    users,
    household,
):
    """29. Enough to answer "who shared what with whom", and nothing more.

    The rule under test is that the audit is not a way around sanitization:
    the shared text appears nowhere in it, only its digest.
    """
    package = _published(users, household, "Bleed the radiators before the first frost.")
    sx.adopt(package.share_id, actor_user_id=users["bob"])
    sx.revoke(package.share_id, actor_user_id=users["alice"])

    rows = tg.group_audit(household, actor_user_id=users["alice"], limit=200)
    events = [row["event"] for row in rows]
    assert "share.published" in events
    assert "share.adopted" in events
    assert "share.revoked" in events

    rendered = str(rows)
    assert package.share_id in rendered
    assert household in rendered
    assert package.content_hash() in rendered
    assert "Bleed the radiators" not in rendered, "audit rows must not carry shared content"
    assert "Taylor" not in rendered


def test_provenance_carries_the_sanitization_record_the_recipient_can_check(users, household):
    """29. A recipient can see which policy produced what they are holding."""
    package = _published(users, household)
    sx.adopt(package.share_id, actor_user_id=users["bob"])
    provenance = sx.provenance(package.share_id, actor_user_id=users["bob"])

    assert provenance["publisher_user_id"] == users["alice"]
    assert provenance["group_id"] == household
    assert provenance["sanitization"]["policy_revision"] == ts.POLICY_REVISION
    assert "provenance" in provenance["sanitization"]["removed_fields"]
    assert provenance["content_hash"] == package.content_hash()
    assert provenance["source_candidate_fingerprint"]
