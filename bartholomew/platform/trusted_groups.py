"""
Trusted groups: the one surface where anything crosses a tenant boundary.

Everywhere else in Bartholomew, isolation is the answer. Each personal
runtime has its own database file, its own data directory and its own keyring
namespace, and there is no code path from one to another. This module is the
deliberate, narrow, opt-in exception -- a household or a small set of
deliberately trusted collaborators who have each said, explicitly, that they
want to be able to hand one another *specific, sanitized, typed* things.

What a group is not
-------------------
It is not a directory, not a social graph, not discovery, and not a place
things travel automatically. Nothing here searches across groups, suggests a
group, ranks members, or moves a package without a person having named it. A
group is a list of accounts that have each accepted an invitation, plus an
audit trail of how that list changed.

Enumeration is the boundary
---------------------------
Every read below takes an `actor_user_id` and refuses a non-member -- and it
refuses with the *same* error whether the group does not exist or the actor
is simply not in it. That symmetry is the point: an outsider must not be able
to use the difference between "no such group" and "not your group" to confirm
that a group exists, who is in it, or that a particular account has
somewhere to share to.

Roles
-----
Three, and they are about *administering the group*, never about what may be
published or adopted:

* ``owner``  -- exactly one; created the group. May do everything below, plus
                archive the group and transfer nothing (there is no transfer
                path: ownership is not a thing to hand around at Alpha).
* ``admin``  -- may invite, and may remove ordinary members.
* ``member`` -- may publish to the group and read its inbox.

Publishing and adopting are governed separately, by
`bartholomew.platform.share_exchange` and the recipient's own local
governance respectively. No role here grants either, and an owner cannot
adopt on a member's behalf.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import Enum

from .store import platform_connection, record_platform_audit

#: How long an unaccepted invitation stays usable. An invitation that never
#: expires is a standing key to a household's shared learning, sitting in
#: whatever inbox it was delivered to.
DEFAULT_INVITATION_TTL_S = 7 * 24 * 60 * 60

_MAX_NAME_LENGTH = 128


class GroupRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


#: Roles an invitation may offer. `owner` is absent deliberately: ownership
#: is established by creating the group and is not something an invitation
#: can confer.
INVITABLE_ROLES = frozenset({GroupRole.ADMIN, GroupRole.MEMBER})

#: Roles that may invite and remove.
_ADMINISTRATIVE = frozenset({GroupRole.OWNER, GroupRole.ADMIN})


class GroupError(Exception):
    """A group operation could not be performed as asked.

    Operator/user-facing, and always about a request that named things the
    actor is *allowed to know about*. Anything that would reveal the
    existence or shape of a group the actor is not in raises
    `GroupAccessError` instead.
    """


class GroupAccessError(Exception):
    """The actor may not see or act on this group.

    Raised identically for "no such group", "you are not a member", "your
    membership was removed" and "the group is archived", so the exception
    itself is not an enumeration oracle. Callers must map every instance to
    one indistinguishable refusal.
    """


@dataclass(frozen=True)
class Membership:
    """One account's live standing in one group."""

    group_id: str
    user_id: str
    role: GroupRole

    @property
    def may_administer(self) -> bool:
        return self.role in _ADMINISTRATIVE


def _now(now: int | None) -> int:
    return int(now if now is not None else time.time())


def _clean_name(name: str | None) -> str:
    text = (name or "").strip()
    if not text:
        raise GroupError("a group needs a name")
    if len(text) > _MAX_NAME_LENGTH:
        raise GroupError(f"group name exceeds {_MAX_NAME_LENGTH} characters")
    return text


def _require_live_account(conn: sqlite3.Connection, user_id: str, *, label: str) -> None:
    row = conn.execute(
        "SELECT kind, disabled_at FROM platform_accounts WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        raise GroupError(f"{label} names no provisioned account")
    if row["disabled_at"] is not None:
        raise GroupError(f"{label} names a disabled account")
    if row["kind"] != "user":
        raise GroupError(
            f"{label} is a {row['kind']}; a platform administrator has no personal "
            "Bartholomew and nothing to share from",
        )


def _membership(conn: sqlite3.Connection, group_id: str, user_id: str) -> Membership:
    """The actor's live membership, or `GroupAccessError`.

    One query, one refusal. It deliberately checks the group's existence and
    the actor's membership together so that neither can be probed on its own:
    a missing group and an outsider produce the identical exception with the
    identical message.
    """
    row = conn.execute(
        "SELECT m.role, g.archived_at FROM platform_group_members m "
        "JOIN platform_trusted_groups g ON g.group_id = m.group_id "
        "WHERE m.group_id = ? AND m.user_id = ? AND m.removed_at IS NULL",
        (group_id, user_id),
    ).fetchone()
    if row is None:
        raise GroupAccessError("no such trusted group, or you are not a member of it")
    try:
        role = GroupRole(row["role"])
    except ValueError as exc:
        # A corrupt role must not default to anything: an unreadable
        # membership is not a membership.
        raise GroupAccessError("no such trusted group, or you are not a member of it") from exc
    return Membership(group_id=group_id, user_id=user_id, role=role)


def require_membership(
    group_id: str,
    actor_user_id: str,
    *,
    db_path: str | None = None,
    administrative: bool = False,
    allow_archived: bool = False,
) -> Membership:
    """The public membership check. Raises `GroupAccessError` on any failure.

    `administrative=True` additionally requires owner or admin. An ordinary
    member asking for an administrative action gets the same refusal an
    outsider gets, because "you are a member but not an admin" is still more
    than an unauthorised caller needs to be told.

    `allow_archived` exists for the read paths that must keep working after a
    group is archived -- a recipient inspecting the provenance of something
    they already adopted, above all. Archiving stops new activity; it does not
    retroactively blind people to what they took part in.
    """
    with platform_connection(db_path) as conn:
        membership = _membership(conn, group_id, actor_user_id)
        if not allow_archived:
            archived = conn.execute(
                "SELECT archived_at FROM platform_trusted_groups WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            if archived is not None and archived["archived_at"] is not None:
                raise GroupAccessError("no such trusted group, or you are not a member of it")
        if administrative and not membership.may_administer:
            raise GroupAccessError("no such trusted group, or you are not a member of it")
        return membership


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def create_group(
    owner_user_id: str,
    name: str,
    *,
    db_path: str | None = None,
    now: int | None = None,
) -> str:
    """Create a trusted group owned by `owner_user_id`. Returns the `group_id`.

    The creating account becomes the sole owner and its first member in the
    same transaction: a group with an owner who is not a member would be a
    row nobody can act through.
    """
    name = _clean_name(name)
    ts = _now(now)
    group_id = str(uuid.uuid4())
    with platform_connection(db_path) as conn:
        _require_live_account(conn, owner_user_id, label="owner_user_id")
        conn.execute(
            "INSERT INTO platform_trusted_groups(group_id, name, owner_user_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (group_id, name, owner_user_id, ts),
        )
        conn.execute(
            "INSERT INTO platform_group_members(group_id, user_id, role, joined_at) "
            "VALUES (?, ?, ?, ?)",
            (group_id, owner_user_id, GroupRole.OWNER.value, ts),
        )
        record_platform_audit(
            conn,
            "group.created",
            user_id=owner_user_id,
            detail=f"group={group_id} name={name}",
            ts=ts,
        )
    return group_id


def archive_group(
    group_id: str,
    *,
    actor_user_id: str,
    db_path: str | None = None,
    now: int | None = None,
) -> None:
    """Archive a group. Owner only.

    Stops new invitations, publications and adoptions. Deliberately not a
    delete: the audit trail of who shared what with whom, and the provenance
    attached to anything already adopted, must survive the group being wound
    up. Deleting it would silently orphan a recipient's local record.
    """
    ts = _now(now)
    with platform_connection(db_path) as conn:
        membership = _membership(conn, group_id, actor_user_id)
        if membership.role is not GroupRole.OWNER:
            raise GroupAccessError("no such trusted group, or you are not a member of it")
        conn.execute(
            "UPDATE platform_trusted_groups SET archived_at = ? "
            "WHERE group_id = ? AND archived_at IS NULL",
            (ts, group_id),
        )
        record_platform_audit(
            conn,
            "group.archived",
            user_id=actor_user_id,
            detail=f"group={group_id}",
            ts=ts,
        )


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def invite(
    group_id: str,
    invited_user_id: str,
    *,
    actor_user_id: str,
    role: GroupRole = GroupRole.MEMBER,
    ttl_s: int = DEFAULT_INVITATION_TTL_S,
    db_path: str | None = None,
    now: int | None = None,
) -> str:
    """Invite an existing account to a group. Returns the `invitation_id`.

    An invitation confers nothing on its own -- see `accept_invitation`. It
    expires, and it names one account: there is no link, no code and no
    "anyone with this may join", because those are the shapes that turn a
    household group into an open door.
    """
    if role not in INVITABLE_ROLES:
        raise GroupError(
            f"role {role.value!r} cannot be conferred by invitation; "
            f"invitable roles are {sorted(r.value for r in INVITABLE_ROLES)}",
        )
    ts = _now(now)
    invitation_id = str(uuid.uuid4())
    with platform_connection(db_path) as conn:
        membership = _membership(conn, group_id, actor_user_id)
        if not membership.may_administer:
            raise GroupAccessError("no such trusted group, or you are not a member of it")
        archived = conn.execute(
            "SELECT archived_at FROM platform_trusted_groups WHERE group_id = ?",
            (group_id,),
        ).fetchone()
        if archived is not None and archived["archived_at"] is not None:
            raise GroupError("this group is archived and is not accepting new members")
        _require_live_account(conn, invited_user_id, label="invited_user_id")

        existing = conn.execute(
            "SELECT 1 FROM platform_group_members "
            "WHERE group_id = ? AND user_id = ? AND removed_at IS NULL",
            (group_id, invited_user_id),
        ).fetchone()
        if existing:
            raise GroupError("that account is already a member of this group")

        conn.execute(
            "INSERT INTO platform_group_invitations"
            "(invitation_id, group_id, invited_user_id, invited_by_user_id, role,"
            " created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                invitation_id,
                group_id,
                invited_user_id,
                actor_user_id,
                role.value,
                ts,
                ts + int(ttl_s),
            ),
        )
        record_platform_audit(
            conn,
            "group.invited",
            user_id=actor_user_id,
            detail=(
                f"group={group_id} invitation={invitation_id} "
                f"invited={invited_user_id} role={role.value}"
            ),
            ts=ts,
        )
    return invitation_id


def list_invitations(user_id: str, *, db_path: str | None = None) -> list[dict]:
    """Invitations addressed to `user_id`. Scoped by predicate, never widened.

    Returns invitations only for the asking account: there is no path here to
    "who else was invited", which would be group enumeration by another name.
    """
    with platform_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT i.*, g.name AS group_name FROM platform_group_invitations i "
            "JOIN platform_trusted_groups g ON g.group_id = i.group_id "
            "WHERE i.invited_user_id = ? ORDER BY i.created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def accept_invitation(
    invitation_id: str,
    *,
    actor_user_id: str,
    db_path: str | None = None,
    now: int | None = None,
) -> Membership:
    """Accept an invitation. Only the invited account may do this.

    Acceptance is the moment membership begins -- an invitation that was sent
    but never accepted has never given anyone access to anything. Expired,
    already-answered, revoked and someone-else's invitations all refuse.
    """
    ts = _now(now)
    with platform_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM platform_group_invitations WHERE invitation_id = ?",
            (invitation_id,),
        ).fetchone()
        if row is None or row["invited_user_id"] != actor_user_id:
            # Same refusal for "no such invitation" and "not yours": an
            # invitation id must not be a probe for whether a group exists.
            raise GroupAccessError("no such invitation")
        if row["accepted_at"] is not None:
            raise GroupError("this invitation has already been accepted")
        if row["declined_at"] is not None or row["revoked_at"] is not None:
            raise GroupError("this invitation is no longer open")
        if ts >= row["expires_at"]:
            raise GroupError("this invitation has expired")
        _require_live_account(conn, actor_user_id, label="the accepting account")

        archived = conn.execute(
            "SELECT archived_at FROM platform_trusted_groups WHERE group_id = ?",
            (row["group_id"],),
        ).fetchone()
        if archived is None or archived["archived_at"] is not None:
            raise GroupError("this group is archived and is not accepting new members")

        role = GroupRole(row["role"])
        conn.execute(
            "INSERT INTO platform_group_members(group_id, user_id, role, joined_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(group_id, user_id) DO UPDATE SET "
            "role = excluded.role, joined_at = excluded.joined_at, removed_at = NULL",
            (row["group_id"], actor_user_id, role.value, ts),
        )
        conn.execute(
            "UPDATE platform_group_invitations SET accepted_at = ? WHERE invitation_id = ?",
            (ts, invitation_id),
        )
        record_platform_audit(
            conn,
            "group.membership_accepted",
            user_id=actor_user_id,
            detail=f"group={row['group_id']} invitation={invitation_id} role={role.value}",
            ts=ts,
        )
        return Membership(group_id=row["group_id"], user_id=actor_user_id, role=role)


def decline_invitation(
    invitation_id: str,
    *,
    actor_user_id: str,
    db_path: str | None = None,
    now: int | None = None,
) -> None:
    """Decline an invitation. Only the invited account may do this."""
    ts = _now(now)
    with platform_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM platform_group_invitations WHERE invitation_id = ?",
            (invitation_id,),
        ).fetchone()
        if row is None or row["invited_user_id"] != actor_user_id:
            raise GroupAccessError("no such invitation")
        if row["accepted_at"] is not None:
            raise GroupError("this invitation has already been accepted")
        conn.execute(
            "UPDATE platform_group_invitations SET declined_at = ? "
            "WHERE invitation_id = ? AND declined_at IS NULL",
            (ts, invitation_id),
        )
        record_platform_audit(
            conn,
            "group.invitation_declined",
            user_id=actor_user_id,
            detail=f"group={row['group_id']} invitation={invitation_id}",
            ts=ts,
        )


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


def list_members(
    group_id: str,
    *,
    actor_user_id: str,
    db_path: str | None = None,
) -> list[dict]:
    """The group's live members. Members only.

    A non-member gets `GroupAccessError`, identical to the one they would get
    for a group id they invented -- so this is not a way to discover that a
    group, or a person's membership of it, exists.
    """
    with platform_connection(db_path) as conn:
        _membership(conn, group_id, actor_user_id)
        rows = conn.execute(
            "SELECT m.user_id, m.role, m.joined_at, a.username "
            "FROM platform_group_members m "
            "LEFT JOIN platform_accounts a ON a.user_id = m.user_id "
            "WHERE m.group_id = ? AND m.removed_at IS NULL ORDER BY m.joined_at",
            (group_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_groups(user_id: str, *, db_path: str | None = None) -> list[dict]:
    """Groups `user_id` is a live member of. Never anybody else's."""
    with platform_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT g.group_id, g.name, g.owner_user_id, g.created_at, g.archived_at, "
            "m.role, m.joined_at FROM platform_group_members m "
            "JOIN platform_trusted_groups g ON g.group_id = m.group_id "
            "WHERE m.user_id = ? AND m.removed_at IS NULL ORDER BY g.created_at",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def set_role(
    group_id: str,
    target_user_id: str,
    role: GroupRole,
    *,
    actor_user_id: str,
    db_path: str | None = None,
    now: int | None = None,
) -> None:
    """Change a member's role. Owner only, and never the owner's own role.

    Restricting this to the owner rather than to any admin keeps the group's
    administrative set from being something an admin can widen on their own.
    """
    if role not in INVITABLE_ROLES:
        raise GroupError(
            f"role {role.value!r} cannot be assigned; assignable roles are "
            f"{sorted(r.value for r in INVITABLE_ROLES)}",
        )
    ts = _now(now)
    with platform_connection(db_path) as conn:
        actor = _membership(conn, group_id, actor_user_id)
        if actor.role is not GroupRole.OWNER:
            raise GroupAccessError("no such trusted group, or you are not a member of it")
        target = _membership(conn, group_id, target_user_id)
        if target.role is GroupRole.OWNER:
            raise GroupError("the owner's role cannot be changed")
        conn.execute(
            "UPDATE platform_group_members SET role = ? "
            "WHERE group_id = ? AND user_id = ? AND removed_at IS NULL",
            (role.value, group_id, target_user_id),
        )
        record_platform_audit(
            conn,
            "group.role_changed",
            user_id=actor_user_id,
            detail=f"group={group_id} target={target_user_id} role={role.value}",
            ts=ts,
        )


def remove_member(
    group_id: str,
    target_user_id: str,
    *,
    actor_user_id: str,
    db_path: str | None = None,
    now: int | None = None,
) -> None:
    """Remove a member. Owner or admin; an admin may not remove another admin.

    Effective on the next call: every read in this module and in
    `share_exchange` re-derives membership rather than caching it, so a
    removed member loses access immediately rather than at the end of some
    session. What they already adopted stays theirs -- see
    `share_exchange.adopt` and the note there about local records surviving.
    """
    ts = _now(now)
    with platform_connection(db_path) as conn:
        actor = _membership(conn, group_id, actor_user_id)
        if not actor.may_administer:
            raise GroupAccessError("no such trusted group, or you are not a member of it")
        target = _membership(conn, group_id, target_user_id)
        if target.role is GroupRole.OWNER:
            raise GroupError("the group owner cannot be removed")
        if target.role is GroupRole.ADMIN and actor.role is not GroupRole.OWNER:
            raise GroupError("only the owner may remove an administrator")
        conn.execute(
            "UPDATE platform_group_members SET removed_at = ? "
            "WHERE group_id = ? AND user_id = ? AND removed_at IS NULL",
            (ts, group_id, target_user_id),
        )
        record_platform_audit(
            conn,
            "group.member_removed",
            user_id=actor_user_id,
            detail=f"group={group_id} target={target_user_id}",
            ts=ts,
        )


def leave_group(
    group_id: str,
    *,
    actor_user_id: str,
    db_path: str | None = None,
    now: int | None = None,
) -> None:
    """Leave a group. Anyone but the owner, who must archive it instead."""
    ts = _now(now)
    with platform_connection(db_path) as conn:
        membership = _membership(conn, group_id, actor_user_id)
        if membership.role is GroupRole.OWNER:
            raise GroupError(
                "the owner cannot leave a group; archive it instead so the record of "
                "what was shared survives",
            )
        conn.execute(
            "UPDATE platform_group_members SET removed_at = ? "
            "WHERE group_id = ? AND user_id = ? AND removed_at IS NULL",
            (ts, group_id, actor_user_id),
        )
        record_platform_audit(
            conn,
            "group.member_left",
            user_id=actor_user_id,
            detail=f"group={group_id}",
            ts=ts,
        )


def group_audit(
    group_id: str,
    *,
    actor_user_id: str,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    """Membership and sharing events for one group. Members only.

    Reads the existing `platform_audit` table -- the same one accounts and
    sessions write to -- rather than adding a second audit authority. Rows
    name accounts, groups, revisions and content hashes; they never carry
    shared content, so an audit read is not a way around sanitization.
    """
    require_membership(group_id, actor_user_id, db_path=db_path, allow_archived=True)
    with platform_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT ts, event, user_id, detail FROM platform_audit "
            "WHERE (event LIKE 'group.%' OR event LIKE 'share.%') "
            "AND detail LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%group={group_id}%", max(1, min(int(limit), 1000))),
        ).fetchall()
    return [dict(row) for row in rows]


__all__ = [
    "DEFAULT_INVITATION_TTL_S",
    "INVITABLE_ROLES",
    "GroupAccessError",
    "GroupError",
    "GroupRole",
    "Membership",
    "accept_invitation",
    "archive_group",
    "create_group",
    "decline_invitation",
    "group_audit",
    "invite",
    "leave_group",
    "list_groups",
    "list_invitations",
    "list_members",
    "remove_member",
    "require_membership",
    "set_role",
]
