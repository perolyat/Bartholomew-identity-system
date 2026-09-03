"""
Package E: trusted groups, membership and the enumeration boundary.

Trusted-group sharing is the one surface in Bartholomew where anything
crosses a tenant boundary, so these tests are written adversarially: the
question each one asks is not "does the happy path work" but "what can a
person who is not in this group find out, or do".

Everything runs against a real control-plane database with real accounts. A
mocked membership check would prove only that the test author remembered the
rule.
"""

from __future__ import annotations

import tempfile

import pytest

from bartholomew.platform import accounts  # noqa: E402
from bartholomew.platform import trusted_groups as tg  # noqa: E402
from bartholomew.platform.principal import PrincipalKind  # noqa: E402
from bartholomew.platform.store import init_platform_schema  # noqa: E402

PASSWORD = "alpha-participant-password"


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
    """Contain this module's control-plane path; see the S8 suite for why."""
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="e-groups-")
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
    for name, kind in (
        ("alice", PrincipalKind.USER),
        ("bob", PrincipalKind.USER),
        ("carol", PrincipalKind.USER),
        ("dan", PrincipalKind.USER),
        ("ops", PrincipalKind.PLATFORM_ADMIN),
    ):
        try:
            made[name] = accounts.create_account(name, PASSWORD, kind=kind)
        except accounts.AccountError:
            made[name] = next(
                a["user_id"] for a in accounts.list_accounts() if a["username"] == name
            )
    return made


def _household(users, name="Household"):
    """Alice owns it; Bob has accepted an invitation. Carol is outside."""
    group_id = tg.create_group(users["alice"], name)
    invitation = tg.invite(group_id, users["bob"], actor_user_id=users["alice"])
    tg.accept_invitation(invitation, actor_user_id=users["bob"])
    return group_id


# ---------------------------------------------------------------------------
# 13: creation and roles, tenant-isolated
# ---------------------------------------------------------------------------


def test_group_creation_makes_the_creator_owner_and_first_member(users):
    """13. A group with an owner who is not a member is a row nobody can act through."""
    group_id = tg.create_group(users["alice"], "Just Alice")
    members = tg.list_members(group_id, actor_user_id=users["alice"])
    assert [(m["user_id"], m["role"]) for m in members] == [
        (users["alice"], tg.GroupRole.OWNER.value),
    ]


def test_role_assignment_is_owner_only_and_never_touches_the_owner(users):
    """13. An admin cannot widen the administrative set on their own."""
    group_id = _household(users, "Roles")
    tg.set_role(group_id, users["bob"], tg.GroupRole.ADMIN, actor_user_id=users["alice"])
    assert {m["user_id"]: m["role"] for m in tg.list_members(group_id, actor_user_id=users["bob"])}[
        users["bob"]
    ] == tg.GroupRole.ADMIN.value

    invitation = tg.invite(group_id, users["carol"], actor_user_id=users["bob"])
    tg.accept_invitation(invitation, actor_user_id=users["carol"])

    # An admin may not promote anybody.
    with pytest.raises(tg.GroupAccessError):
        tg.set_role(group_id, users["carol"], tg.GroupRole.ADMIN, actor_user_id=users["bob"])
    # And the owner's own role is not assignable at all.
    with pytest.raises(tg.GroupError):
        tg.set_role(group_id, users["alice"], tg.GroupRole.MEMBER, actor_user_id=users["alice"])


def test_ownership_cannot_be_conferred_by_invitation(users):
    """13. Ownership is established by creating a group, not handed around."""
    group_id = tg.create_group(users["alice"], "No Transfers")
    with pytest.raises(tg.GroupError):
        tg.invite(group_id, users["bob"], actor_user_id=users["alice"], role=tg.GroupRole.OWNER)


def test_a_group_belongs_to_its_members_and_to_nobody_else(users):
    """13. `list_groups` is scoped by predicate and cannot be widened by omission."""
    group_id = _household(users, "Scoped")
    assert group_id in {g["group_id"] for g in tg.list_groups(users["alice"])}
    assert group_id in {g["group_id"] for g in tg.list_groups(users["bob"])}
    assert group_id not in {g["group_id"] for g in tg.list_groups(users["carol"])}


def test_a_platform_administrator_cannot_own_or_join_a_group(users):
    """13. An administrator has no personal Bartholomew and nothing to share from."""
    with pytest.raises(tg.GroupError):
        tg.create_group(users["ops"], "Ops Group")
    group_id = tg.create_group(users["alice"], "No Admins")
    with pytest.raises(tg.GroupError):
        tg.invite(group_id, users["ops"], actor_user_id=users["alice"])


# ---------------------------------------------------------------------------
# 14: invitations are explicit and expire
# ---------------------------------------------------------------------------


def test_an_invitation_confers_nothing_until_it_is_accepted(users):
    """14. Being invited is not being a member."""
    group_id = tg.create_group(users["alice"], "Pending Invite")
    tg.invite(group_id, users["bob"], actor_user_id=users["alice"])

    with pytest.raises(tg.GroupAccessError):
        tg.list_members(group_id, actor_user_id=users["bob"])
    assert group_id not in {g["group_id"] for g in tg.list_groups(users["bob"])}


def test_only_the_invited_account_can_accept_an_invitation(users):
    """14. An invitation names one account. It is not a link anyone may use."""
    group_id = tg.create_group(users["alice"], "Named Invite")
    invitation = tg.invite(group_id, users["bob"], actor_user_id=users["alice"])

    with pytest.raises(tg.GroupAccessError):
        tg.accept_invitation(invitation, actor_user_id=users["carol"])
    assert group_id not in {g["group_id"] for g in tg.list_groups(users["carol"])}

    tg.accept_invitation(invitation, actor_user_id=users["bob"])
    assert group_id in {g["group_id"] for g in tg.list_groups(users["bob"])}


def test_an_invitation_expires(users):
    """14. An invitation that never expired would be a standing key."""
    group_id = tg.create_group(users["alice"], "Expiring Invite")
    invitation = tg.invite(
        group_id,
        users["carol"],
        actor_user_id=users["alice"],
        ttl_s=3600,
        now=1_000_000,
    )
    with pytest.raises(tg.GroupError, match="expired"):
        tg.accept_invitation(invitation, actor_user_id=users["carol"], now=1_000_000 + 3601)
    # Still refused afterwards, not merely at that instant.
    with pytest.raises(tg.GroupError):
        tg.accept_invitation(invitation, actor_user_id=users["carol"], now=1_000_000 + 999_999)


def test_an_invitation_cannot_be_accepted_twice_or_after_declining(users):
    """14. Acceptance and refusal are both terminal."""
    group_id = tg.create_group(users["alice"], "Once Only")
    invitation = tg.invite(group_id, users["bob"], actor_user_id=users["alice"])
    tg.accept_invitation(invitation, actor_user_id=users["bob"])
    with pytest.raises(tg.GroupError):
        tg.accept_invitation(invitation, actor_user_id=users["bob"])

    declined = tg.invite(group_id, users["carol"], actor_user_id=users["alice"])
    tg.decline_invitation(declined, actor_user_id=users["carol"])
    with pytest.raises(tg.GroupError):
        tg.accept_invitation(declined, actor_user_id=users["carol"])


def test_only_owners_and_admins_may_invite(users):
    """14. Membership is not a licence to widen the group."""
    group_id = _household(users, "Invite Rights")
    invitation = tg.invite(group_id, users["carol"], actor_user_id=users["alice"])
    tg.accept_invitation(invitation, actor_user_id=users["carol"])
    with pytest.raises(tg.GroupAccessError):
        tg.invite(group_id, users["dan"], actor_user_id=users["carol"])


# ---------------------------------------------------------------------------
# 15: non-members cannot enumerate or access group data
# ---------------------------------------------------------------------------


def test_a_non_member_cannot_enumerate_a_group_its_members_or_its_invitations(users):
    """15. And the refusal is the same one an invented group id gets.

    The symmetry is the property under test: if "no such group" and "not your
    group" produced different errors, a stranger could confirm that a group
    exists, and that a particular account has somewhere to share to, without
    ever being in it.
    """
    group_id = _household(users, "Private Household")
    invented = "00000000-0000-4000-8000-000000000000"

    real_refusals = []
    invented_refusals = []
    for target, bucket in ((group_id, real_refusals), (invented, invented_refusals)):
        for call in (
            lambda t=target: tg.list_members(t, actor_user_id=users["carol"]),
            lambda t=target: tg.require_membership(t, users["carol"]),
            lambda t=target: tg.group_audit(t, actor_user_id=users["carol"]),
            lambda t=target: tg.invite(t, users["dan"], actor_user_id=users["carol"]),
            lambda t=target: tg.archive_group(t, actor_user_id=users["carol"]),
            lambda t=target: tg.leave_group(t, actor_user_id=users["carol"]),
        ):
            with pytest.raises(tg.GroupAccessError) as caught:
                call()
            bucket.append(str(caught.value))

    assert real_refusals == invented_refusals, (
        "the refusal for a real group must be indistinguishable from the refusal for "
        "one that does not exist, or the error message is an enumeration oracle"
    )


def test_a_non_member_cannot_see_invitations_addressed_to_others(users):
    """15. `list_invitations` answers only for the asking account."""
    group_id = tg.create_group(users["alice"], "Invitation Privacy")
    tg.invite(group_id, users["bob"], actor_user_id=users["alice"])
    assert tg.list_invitations(users["carol"]) == [] or all(
        row["invited_user_id"] == users["carol"] for row in tg.list_invitations(users["carol"])
    )
    assert any(row["group_id"] == group_id for row in tg.list_invitations(users["bob"]))


# ---------------------------------------------------------------------------
# 16: removal and departure are immediate
# ---------------------------------------------------------------------------


def test_a_removed_member_immediately_loses_access(users):
    """16. Effective on the next call, because membership is re-derived, not cached."""
    group_id = _household(users, "Removal")
    assert tg.list_members(group_id, actor_user_id=users["bob"])

    tg.remove_member(group_id, users["bob"], actor_user_id=users["alice"])

    with pytest.raises(tg.GroupAccessError):
        tg.list_members(group_id, actor_user_id=users["bob"])
    assert group_id not in {g["group_id"] for g in tg.list_groups(users["bob"])}


def test_a_member_can_leave_but_the_owner_must_archive(users):
    """16. Departure is a member's own decision; the owner's is a different one."""
    group_id = _household(users, "Departure")
    tg.leave_group(group_id, actor_user_id=users["bob"])
    with pytest.raises(tg.GroupAccessError):
        tg.list_members(group_id, actor_user_id=users["bob"])

    with pytest.raises(tg.GroupError, match="archive"):
        tg.leave_group(group_id, actor_user_id=users["alice"])


def test_an_admin_cannot_remove_the_owner_or_another_admin(users):
    """16. Removal rights stop short of the people who granted them."""
    group_id = _household(users, "Removal Limits")
    tg.set_role(group_id, users["bob"], tg.GroupRole.ADMIN, actor_user_id=users["alice"])
    invitation = tg.invite(
        group_id,
        users["carol"],
        actor_user_id=users["alice"],
        role=tg.GroupRole.ADMIN,
    )
    tg.accept_invitation(invitation, actor_user_id=users["carol"])

    with pytest.raises(tg.GroupError):
        tg.remove_member(group_id, users["alice"], actor_user_id=users["bob"])
    with pytest.raises(tg.GroupError):
        tg.remove_member(group_id, users["carol"], actor_user_id=users["bob"])
    # The owner may.
    tg.remove_member(group_id, users["carol"], actor_user_id=users["alice"])


def test_archiving_stops_new_activity_without_erasing_the_record(users):
    """16. Archiving is not deleting: what was shared, and with whom, survives.

    Deleting would silently orphan whatever a recipient had already adopted --
    the record would still be in their runtime with a provenance pointing at
    nothing.
    """
    group_id = _household(users, "Archive")
    tg.archive_group(group_id, actor_user_id=users["alice"])

    with pytest.raises(tg.GroupError):
        tg.invite(group_id, users["carol"], actor_user_id=users["alice"])
    with pytest.raises(tg.GroupAccessError):
        tg.require_membership(group_id, users["bob"])

    # But a member can still read their own provenance.
    membership = tg.require_membership(group_id, users["bob"], allow_archived=True)
    assert membership.role is tg.GroupRole.MEMBER
    assert tg.group_audit(group_id, actor_user_id=users["bob"])


# ---------------------------------------------------------------------------
# 29 (membership half): auditability without content
# ---------------------------------------------------------------------------


def test_membership_changes_are_audited_by_account_and_group(users):
    """29. Who joined, who left, who was removed -- named, and content-free.

    The audit rows carry account and group identifiers only. Nothing here
    quotes what was shared, so reading the audit trail is never a way around
    sanitization.
    """
    group_id = _household(users, "Audit Trail")
    tg.set_role(group_id, users["bob"], tg.GroupRole.ADMIN, actor_user_id=users["alice"])
    tg.remove_member(group_id, users["bob"], actor_user_id=users["alice"])

    events = [row["event"] for row in tg.group_audit(group_id, actor_user_id=users["alice"])]
    assert events[:4] == [
        "group.member_removed",
        "group.role_changed",
        "group.membership_accepted",
        "group.invited",
    ]
    rendered = str(tg.group_audit(group_id, actor_user_id=users["alice"]))
    assert group_id in rendered
    assert users["bob"] in rendered


# ---------------------------------------------------------------------------
# Adversarial-review regressions (2026-09-02)
# ---------------------------------------------------------------------------


def test_an_outsider_cannot_change_roles_or_remove_members(users):
    """13/16 (regression). The isolation test did not cover these two.

    It exercised `list_members`, `require_membership`, `group_audit`,
    `invite`, `archive_group` and `leave_group` for an outsider, but never the
    two functions that change who is in a group and what they may do -- so a
    refactor that dropped their membership check would have left the suite
    green.
    """
    group_id = _household(users, "Outsider Writes")
    invented = "00000000-0000-4000-8000-000000000001"

    real, unreal = [], []
    for target, bucket in ((group_id, real), (invented, unreal)):
        for call in (
            lambda t=target: tg.set_role(
                t,
                users["bob"],
                tg.GroupRole.ADMIN,
                actor_user_id=users["carol"],
            ),
            lambda t=target: tg.remove_member(t, users["bob"], actor_user_id=users["carol"]),
        ):
            with pytest.raises(tg.GroupAccessError) as caught:
                call()
            bucket.append(str(caught.value))

    assert real == unreal
    # And Bob is still an ordinary member of a group he was never removed from.
    assert {
        m["user_id"]: m["role"] for m in tg.list_members(group_id, actor_user_id=users["alice"])
    }[users["bob"]] == tg.GroupRole.MEMBER.value
