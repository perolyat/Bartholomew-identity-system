"""
Publishing to a trusted group, and receiving from one.

The persistence half of trusted-group sharing. `bartholomew.kernel.
trusted_share` decides what may be published and produces the package;
this module decides *who* may publish it, to which group, who then sees it,
and what happens to it afterwards. It writes to the control-plane database,
because a group spans accounts and no single user's kernel can hold it.

Two governed actions, never one
-------------------------------
`propose()` produces a sanitized package and writes nothing. `publish()`
takes that package plus an explicitly named group id and stores it. A caller
that only ever calls `publish()` still has to have obtained a package, and a
caller that only ever calls `propose()` has published nothing -- so "inspect
what would be shared" and "share it" are two decisions in the code, not two
screens in front of one call.

The same split holds on the other side. Delivery puts a package in a group
inbox; it adopts nothing. `adopt()` is a separate act by the recipient, and
even that only produces a **local candidate** in the recipient's own runtime,
which their own governance then decides about. Nothing here can make a
package into the recipient's knowledge, and nothing here writes to a
recipient's kernel database at all.

Revisions are append-only
-------------------------
A publisher update inserts a new `(share_id, revision)` row. It never
rewrites an earlier one, so a recipient's adopted revision keeps existing
exactly as they adopted it, and "the publisher changed their mind" arrives as
a *proposed update* rather than as a silent substitution. A revision that
does not follow the revision the publisher last saw is refused as a
concurrent edit -- there is no last-write-wins branch below.

Revocation stops future adoption; it does not reach into anyone's runtime
and delete what they already took. It stays attached to the provenance, so a
recipient looking at their own adopted record can always find out that the
publisher has since withdrawn it.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from bartholomew.kernel.trusted_share import (
    SourceRecord,
    TrustedSharePackage,
    canonical_json,
)
from bartholomew.kernel.trusted_share import (
    propose as _sanitize_into_package,
)

from .store import platform_connection, record_platform_audit
from .trusted_groups import GroupAccessError, require_membership

#: Receipt states. `delivered` is the resting state of anything in an inbox:
#: a package that has been made visible and about which the recipient has
#: decided nothing.
RECEIPT_DELIVERED = "delivered"
RECEIPT_DECLINED = "declined"
RECEIPT_ADOPTED = "adopted"


class PublicationError(Exception):
    """A publication or revision could not be performed as asked."""


class ConcurrentRevisionError(PublicationError):
    """Someone else published a revision of this share first.

    Its own type so the caller can tell "your update was refused because the
    share moved under you" apart from every other refusal -- and so no branch
    can quietly treat it as success. There is deliberately no force flag: the
    resolution is to look at what the share now says and decide again.
    """


class ShareNotFoundError(Exception):
    """No such share, or none this actor may see.

    Raised identically for both, for the same reason `GroupAccessError` is:
    a share id must not be a probe for whether a group's inbox contains
    something.
    """


class AdoptionRefusedError(Exception):
    """This package may not be adopted right now.

    Revoked, or a revision the recipient has not been offered. Distinct from
    `ShareNotFoundError` because the recipient is entitled to know that a
    package they can see has been withdrawn -- that is the visible-revocation
    property, not a leak.
    """


def _now(now: int | None) -> int:
    return int(now if now is not None else time.time())


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _package_from_row(row: sqlite3.Row) -> TrustedSharePackage:
    return TrustedSharePackage.from_dict(
        {
            "share_id": row["share_id"],
            "group_id": row["group_id"],
            "publisher_user_id": row["publisher_user_id"],
            "source_candidate_fingerprint": row["source_candidate_fingerprint"],
            "kind": row["kind"],
            "content": json.loads(row["content"]),
            "sanitization": json.loads(row["sanitization"]),
            "revision": row["revision"],
            "published_at": row["published_at"],
            "revoked_at": row["revoked_at"],
        },
    )


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


def propose(
    record: SourceRecord,
    *,
    requested_kind: str,
    group_id: str,
    publisher_user_id: str,
    share_id: str | None = None,
    revision: int = 1,
    db_path: str | None = None,
) -> TrustedSharePackage:
    """Sanitize an explicitly selected record into a package. Writes nothing.

    Membership is checked here as well as at publication, so a user cannot
    use the sanitizer as an oracle about a group they are not in.

    The returned package is what `publish()` will store, byte for byte -- so
    what the publisher inspects is what the group receives, rather than a
    preview of it.
    """
    require_membership(group_id, publisher_user_id, db_path=db_path)
    return _sanitize_into_package(
        record,
        requested_kind=requested_kind,
        share_id=share_id or str(uuid.uuid4()),
        group_id=group_id,
        publisher_user_id=publisher_user_id,
        revision=revision,
    )


def publish(
    package: TrustedSharePackage,
    *,
    publisher_user_id: str,
    confirm_group_id: str,
    db_path: str | None = None,
    now: int | None = None,
) -> TrustedSharePackage:
    """Publish an inspected package to one named group.

    `confirm_group_id` must equal the package's own `group_id`. It is a
    second, separate statement of where this is going, made after the
    publisher has seen the sanitized content -- so "publish" cannot be a
    button that inherits a group from whatever was selected three screens
    ago, and there is no multi-group publish anywhere in this module.

    `publisher_user_id` is the authenticated publisher and must match the
    package. A package is not a bearer instrument: holding one does not let
    a different account publish it.
    """
    if confirm_group_id != package.group_id:
        raise PublicationError(
            "publication must confirm the destination group explicitly; "
            f"confirmed {confirm_group_id!r} but the package names {package.group_id!r}",
        )
    if publisher_user_id != package.publisher_user_id:
        raise PublicationError(
            "the authenticated publisher does not match the package's publisher",
        )
    errors = package.validate()
    if errors:
        raise PublicationError("; ".join(errors))

    require_membership(package.group_id, publisher_user_id, db_path=db_path)
    ts = _now(now)

    with platform_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT MAX(revision) AS latest FROM platform_share_packages WHERE share_id = ?",
            (package.share_id,),
        ).fetchone()
        latest = existing["latest"] if existing else None
        if latest is not None:
            raise PublicationError(
                f"share {package.share_id} already exists at revision {latest}; "
                "use publish_revision() so the update is offered rather than substituted",
            )
        _insert_revision(conn, package, ts)
        record_platform_audit(
            conn,
            "share.published",
            user_id=publisher_user_id,
            detail=(
                f"group={package.group_id} share={package.share_id} "
                f"revision={package.revision} kind={package.kind} "
                f"content_hash={package.content_hash()} "
                f"policy_revision={package.sanitization.policy_revision} "
                f"removed_fields={len(package.sanitization.removed_fields)}"
            ),
            ts=ts,
        )
        _deliver_to_members(conn, package, ts)
    return package


def publish_revision(
    share_id: str,
    record: SourceRecord,
    *,
    requested_kind: str,
    publisher_user_id: str,
    expected_revision: int,
    db_path: str | None = None,
    now: int | None = None,
) -> TrustedSharePackage:
    """Publish an updated revision of an existing share.

    `expected_revision` is the revision the publisher was looking at. If the
    share has moved since -- another admin published, or the same person from
    two places -- this raises `ConcurrentRevisionError` and writes nothing.
    There is no override: silently winning that race is precisely the
    last-write-wins behaviour this design refuses.

    The new revision is offered to recipients as an *update*. It does not
    replace anybody's adopted revision, and it does not re-adopt itself for
    anyone who had adopted an earlier one.
    """
    ts = _now(now)
    with platform_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM platform_share_packages WHERE share_id = ? "
            "ORDER BY revision DESC LIMIT 1",
            (share_id,),
        ).fetchone()
        if rows is None:
            raise ShareNotFoundError(f"no share {share_id!r}")
        current = _package_from_row(rows)

    if current.publisher_user_id != publisher_user_id:
        # Not `ShareNotFoundError`: a group member can legitimately see this
        # share, so hiding its existence here would be theatre. What they may
        # not do is revise somebody else's publication.
        require_membership(current.group_id, publisher_user_id, db_path=db_path)
        raise PublicationError("only the original publisher may revise a share")
    require_membership(current.group_id, publisher_user_id, db_path=db_path)

    if current.is_revoked:
        raise PublicationError(
            "this share has been revoked; a revoked share receives no further updates",
        )
    if int(expected_revision) != current.revision:
        raise ConcurrentRevisionError(
            f"share {share_id} is at revision {current.revision}, not "
            f"{expected_revision}; re-read it and decide again",
        )

    package = _sanitize_into_package(
        record,
        requested_kind=requested_kind,
        share_id=share_id,
        group_id=current.group_id,
        publisher_user_id=publisher_user_id,
        revision=current.revision + 1,
    )

    with platform_connection(db_path) as conn:
        # Re-check inside the write transaction. The read above was a
        # separate connection, so a revision could have landed in between;
        # this is the check that actually holds.
        guard = conn.execute(
            "SELECT MAX(revision) AS latest FROM platform_share_packages WHERE share_id = ?",
            (share_id,),
        ).fetchone()
        if guard["latest"] != current.revision:
            raise ConcurrentRevisionError(
                f"share {share_id} moved to revision {guard['latest']} while this "
                "revision was being prepared; nothing was written",
            )
        _insert_revision(conn, package, ts)
        record_platform_audit(
            conn,
            "share.revised",
            user_id=publisher_user_id,
            detail=(
                f"group={package.group_id} share={share_id} "
                f"revision={package.revision} from_revision={current.revision} "
                f"content_hash={package.content_hash()}"
            ),
            ts=ts,
        )
        _deliver_to_members(conn, package, ts, only_new=True)
    return package


def _insert_revision(
    conn: sqlite3.Connection,
    package: TrustedSharePackage,
    ts: int,
) -> None:
    conn.execute(
        "INSERT INTO platform_share_packages"
        "(share_id, revision, group_id, publisher_user_id, kind, content, content_hash,"
        " source_candidate_fingerprint, sanitization, published_at, revoked_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            package.share_id,
            package.revision,
            package.group_id,
            package.publisher_user_id,
            package.kind,
            canonical_json(package.content),
            package.content_hash(),
            package.source_candidate_fingerprint,
            canonical_json(package.sanitization.as_dict()),
            package.published_at,
        ),
    )


def _deliver_to_members(
    conn: sqlite3.Connection,
    package: TrustedSharePackage,
    ts: int,
    *,
    only_new: bool = False,
) -> None:
    """Make a package visible to the group's live members except its publisher.

    Delivery creates a `delivered` receipt and nothing more. It is not
    adoption, it does not touch a recipient's runtime, and `only_new` keeps a
    revision from resetting a recipient who has already declined or adopted:
    their decision stands, and the new revision shows up as a pending update
    rather than as a fresh, undecided item.
    """
    members = conn.execute(
        "SELECT user_id FROM platform_group_members WHERE group_id = ? AND removed_at IS NULL",
        (package.group_id,),
    ).fetchall()
    for member in members:
        recipient = member["user_id"]
        if recipient == package.publisher_user_id:
            continue
        if only_new:
            conn.execute(
                "INSERT OR IGNORE INTO platform_share_receipts"
                "(share_id, recipient_user_id, state, updated_at) VALUES (?, ?, ?, ?)",
                (package.share_id, recipient, RECEIPT_DELIVERED, ts),
            )
            continue
        conn.execute(
            "INSERT INTO platform_share_receipts"
            "(share_id, recipient_user_id, state, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(share_id, recipient_user_id) DO UPDATE SET updated_at = excluded.updated_at",
            (package.share_id, recipient, RECEIPT_DELIVERED, ts),
        )


def revoke(
    share_id: str,
    *,
    actor_user_id: str,
    db_path: str | None = None,
    now: int | None = None,
) -> None:
    """Withdraw a share. Publisher only. Stops adoption and further updates.

    What revocation does: no member may adopt it from now on, no revision may
    be published, and every recipient sees it marked revoked wherever its
    provenance appears.

    What revocation does **not** do: it does not delete or disable anything a
    recipient already adopted. That record is in their runtime, governed by
    their policy, and a publisher who could reach into it would have a remote
    delete on another person's memory -- which is a larger power than
    "un-share this" and is not one this design grants. The recipient is told
    it was withdrawn and decides.
    """
    ts = _now(now)
    ts_iso = _utcnow_iso()
    with platform_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM platform_share_packages WHERE share_id = ? "
            "ORDER BY revision DESC LIMIT 1",
            (share_id,),
        ).fetchone()
        if row is None:
            raise ShareNotFoundError(f"no share {share_id!r}")
        if row["publisher_user_id"] != actor_user_id:
            raise ShareNotFoundError(f"no share {share_id!r}")
        conn.execute(
            "UPDATE platform_share_packages SET revoked_at = ? "
            "WHERE share_id = ? AND revoked_at IS NULL",
            (ts_iso, share_id),
        )
        record_platform_audit(
            conn,
            "share.revoked",
            user_id=actor_user_id,
            detail=f"group={row['group_id']} share={share_id} revoked_at={ts_iso}",
            ts=ts,
        )


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InboxEntry:
    """One item in a group inbox, and what the recipient has done about it."""

    package: TrustedSharePackage
    state: str
    adopted_revision: int | None
    local_fork: bool
    latest_revision: int

    @property
    def has_pending_update(self) -> bool:
        """A newer, live revision exists than the one this recipient adopted.

        False for a revoked share: a withdrawn share offers no updates, and
        presenting one as "available" would be an invitation to adopt
        something the publisher has taken back.
        """
        if self.package.is_revoked or self.adopted_revision is None:
            return False
        return self.latest_revision > self.adopted_revision

    def as_dict(self) -> dict:
        return {
            **self.package.as_dict(),
            "receipt_state": self.state,
            "adopted_revision": self.adopted_revision,
            "local_fork": self.local_fork,
            "latest_revision": self.latest_revision,
            "has_pending_update": self.has_pending_update,
            "revoked": self.package.is_revoked,
        }


def inbox(
    user_id: str,
    *,
    group_id: str | None = None,
    db_path: str | None = None,
) -> list[InboxEntry]:
    """Everything shared to this account, across the groups it is in.

    Membership is re-derived on every call rather than cached, which is what
    makes removal immediate: a removed member's rows disappear from the join
    on the next read, without any explicit invalidation step to forget.

    Listing shows each package's *latest* revision plus the recipient's own
    receipt, so an available update is visible without being applied.
    """
    if group_id is not None:
        require_membership(group_id, user_id, db_path=db_path, allow_archived=True)

    clauses = ["m.user_id = ?", "m.removed_at IS NULL", "p.publisher_user_id != ?"]
    params: list[object] = [user_id, user_id]
    if group_id is not None:
        clauses.append("p.group_id = ?")
        params.append(group_id)

    with platform_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT p.*, r.state, r.adopted_revision, r.local_fork, "  # noqa: S608
            "       (SELECT MAX(revision) FROM platform_share_packages q "
            "        WHERE q.share_id = p.share_id) AS latest_revision "
            "FROM platform_share_packages p "
            "JOIN platform_group_members m ON m.group_id = p.group_id "
            "LEFT JOIN platform_share_receipts r "
            "       ON r.share_id = p.share_id AND r.recipient_user_id = m.user_id "
            f"WHERE {' AND '.join(clauses)} "
            "  AND p.revision = (SELECT MAX(revision) FROM platform_share_packages q2 "
            "                    WHERE q2.share_id = p.share_id) "
            "ORDER BY p.published_at DESC",
            params,
        ).fetchall()

    return [
        InboxEntry(
            package=_package_from_row(row),
            state=row["state"] or RECEIPT_DELIVERED,
            adopted_revision=row["adopted_revision"],
            local_fork=bool(row["local_fork"]),
            latest_revision=int(row["latest_revision"]),
        )
        for row in rows
    ]


def inspect(
    share_id: str,
    *,
    actor_user_id: str,
    revision: int | None = None,
    db_path: str | None = None,
) -> TrustedSharePackage:
    """Read one package in full. Members of its group only.

    The step between "there is something in my inbox" and "I will adopt it".
    Revoked packages are still readable -- that is the whole of "revocation
    remains visibly attached to provenance": a recipient must be able to look
    at what they adopted and see that it has since been withdrawn.
    """
    with platform_connection(db_path) as conn:
        if revision is None:
            row = conn.execute(
                "SELECT * FROM platform_share_packages WHERE share_id = ? "
                "ORDER BY revision DESC LIMIT 1",
                (share_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM platform_share_packages WHERE share_id = ? AND revision = ?",
                (share_id, int(revision)),
            ).fetchone()
    if row is None:
        raise ShareNotFoundError(f"no share {share_id!r}")
    try:
        require_membership(
            row["group_id"],
            actor_user_id,
            db_path=db_path,
            allow_archived=True,
        )
    except GroupAccessError as exc:
        # Collapsed into the same refusal a nonexistent share gets: a
        # non-member must not learn that this share id is real.
        raise ShareNotFoundError(f"no share {share_id!r}") from exc
    return _package_from_row(row)


def revisions(
    share_id: str,
    *,
    actor_user_id: str,
    db_path: str | None = None,
) -> list[TrustedSharePackage]:
    """Every revision of one share, oldest first. Members only."""
    inspect(share_id, actor_user_id=actor_user_id, db_path=db_path)
    with platform_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM platform_share_packages WHERE share_id = ? ORDER BY revision",
            (share_id,),
        ).fetchall()
    return [_package_from_row(row) for row in rows]


def decline(
    share_id: str,
    *,
    actor_user_id: str,
    db_path: str | None = None,
    now: int | None = None,
) -> None:
    """Decline a shared package. Records a decision; adopts nothing."""
    package = inspect(share_id, actor_user_id=actor_user_id, db_path=db_path)
    ts = _now(now)
    with platform_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO platform_share_receipts"
            "(share_id, recipient_user_id, state, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(share_id, recipient_user_id) DO UPDATE SET "
            "state = excluded.state, updated_at = excluded.updated_at",
            (share_id, actor_user_id, RECEIPT_DECLINED, ts),
        )
        record_platform_audit(
            conn,
            "share.declined",
            user_id=actor_user_id,
            detail=f"group={package.group_id} share={share_id}",
            ts=ts,
        )


def adopt(
    share_id: str,
    *,
    actor_user_id: str,
    revision: int | None = None,
    db_path: str | None = None,
    now: int | None = None,
) -> TrustedSharePackage:
    """Take a package for local consideration. Returns what was adopted.

    **This is not acceptance.** It records that the recipient has taken the
    package as something to consider, and returns it so the recipient's own
    runtime can turn it into a local candidate (see
    `bartholomew.kernel.share_adoption`). Nothing here writes to a
    recipient's kernel database, and nothing here can make a package into
    retrievable knowledge -- that requires the recipient's own
    candidate-bound approval, in their own runtime, under their own
    governance.

    A revoked package cannot be adopted. A revision the recipient names must
    exist. Both refuse rather than falling back to "the newest thing we
    have", because adopting something other than what was inspected is
    exactly the substitution this design exists to prevent.
    """
    package = inspect(
        share_id,
        actor_user_id=actor_user_id,
        revision=revision,
        db_path=db_path,
    )
    if package.is_revoked:
        raise AdoptionRefusedError(
            f"share {share_id} was withdrawn by its publisher at {package.revoked_at}; "
            "it cannot be adopted",
        )
    # Membership on the *live* group, not the archived-tolerant read above:
    # inspecting a group you have left is provenance, adopting into it is
    # activity.
    require_membership(package.group_id, actor_user_id, db_path=db_path)

    ts = _now(now)
    with platform_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO platform_share_receipts"
            "(share_id, recipient_user_id, state, adopted_revision, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(share_id, recipient_user_id) DO UPDATE SET "
            "state = excluded.state, adopted_revision = excluded.adopted_revision, "
            "updated_at = excluded.updated_at",
            (share_id, actor_user_id, RECEIPT_ADOPTED, package.revision, ts),
        )
        record_platform_audit(
            conn,
            "share.adopted",
            user_id=actor_user_id,
            detail=(
                f"group={package.group_id} share={share_id} revision={package.revision} "
                f"content_hash={package.content_hash()}"
            ),
            ts=ts,
        )
    return package


def mark_local_fork(
    share_id: str,
    *,
    actor_user_id: str,
    db_path: str | None = None,
    now: int | None = None,
) -> None:
    """Record that the recipient has customised their adopted copy.

    A customised adoption is a **local fork**: a later publisher revision is
    offered to it as a proposal like any other, and never applied over it.
    Marking it is what makes that visible on the exchange side; the local
    record itself is the recipient's, in their own runtime.
    """
    ts = _now(now)
    with platform_connection(db_path) as conn:
        row = conn.execute(
            "SELECT state FROM platform_share_receipts "
            "WHERE share_id = ? AND recipient_user_id = ?",
            (share_id, actor_user_id),
        ).fetchone()
        if row is None or row["state"] != RECEIPT_ADOPTED:
            raise AdoptionRefusedError(
                "only an adopted share can be marked as locally customised",
            )
        conn.execute(
            "UPDATE platform_share_receipts SET local_fork = 1, updated_at = ? "
            "WHERE share_id = ? AND recipient_user_id = ?",
            (ts, share_id, actor_user_id),
        )
        record_platform_audit(
            conn,
            "share.local_fork",
            user_id=actor_user_id,
            detail=f"share={share_id}",
            ts=ts,
        )


def pending_updates(user_id: str, *, db_path: str | None = None) -> list[InboxEntry]:
    """Adopted shares whose publisher has since published a newer revision.

    A queue of *proposals*, not of pending applications. Nothing in this
    module applies one, and a locally forked adoption stays forked however
    many revisions arrive after it.
    """
    return [entry for entry in inbox(user_id, db_path=db_path) if entry.has_pending_update]


def provenance(
    share_id: str,
    *,
    actor_user_id: str,
    db_path: str | None = None,
) -> dict:
    """The provenance a recipient can see for something they adopted.

    Names the group, the publisher account, the revision they adopted, the
    latest revision, the content hash and whether the share has been
    withdrawn. It carries no shared content beyond what the recipient already
    holds and nothing at all about the publisher's own source record -- the
    origin fingerprint is a digest, not a pointer anybody but the publisher
    can follow.
    """
    package = inspect(share_id, actor_user_id=actor_user_id, db_path=db_path)
    with platform_connection(db_path) as conn:
        receipt = conn.execute(
            "SELECT state, adopted_revision, local_fork FROM platform_share_receipts "
            "WHERE share_id = ? AND recipient_user_id = ?",
            (share_id, actor_user_id),
        ).fetchone()
    return {
        "share_id": share_id,
        "group_id": package.group_id,
        "publisher_user_id": package.publisher_user_id,
        "kind": package.kind,
        "latest_revision": package.revision,
        "content_hash": package.content_hash(),
        "source_candidate_fingerprint": package.source_candidate_fingerprint,
        "sanitization": package.sanitization.as_dict(),
        "revoked": package.is_revoked,
        "revoked_at": package.revoked_at,
        "receipt_state": receipt["state"] if receipt else None,
        "adopted_revision": receipt["adopted_revision"] if receipt else None,
        "local_fork": bool(receipt["local_fork"]) if receipt else False,
    }


__all__ = [
    "RECEIPT_ADOPTED",
    "RECEIPT_DECLINED",
    "RECEIPT_DELIVERED",
    "AdoptionRefusedError",
    "ConcurrentRevisionError",
    "InboxEntry",
    "PublicationError",
    "ShareNotFoundError",
    "adopt",
    "decline",
    "inbox",
    "inspect",
    "mark_local_fork",
    "pending_updates",
    "propose",
    "provenance",
    "publish",
    "publish_revision",
    "revisions",
    "revoke",
]
