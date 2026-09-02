"""
Operator commands for the device registry and for trusted groups.

Local-only by construction, like `bartholomew accounts`: every command below
talks to the control-plane database on disk, not over HTTP. There is
deliberately **no remote device-enrolment or group-administration endpoint**
-- an authenticated remote surface that can enrol a device is an
authenticated remote surface that can enrol an attacker's device, and the
same reasoning that kept account provisioning off the network keeps this off
it too.

Two rules about secrets, both enforced here rather than advised:

* **A secret is printed exactly once, at the moment it is minted**, and never
  by any read command. `devices show`, `devices list` and `devices manifest`
  cannot print credential material because they are not given any -- the
  registry's read surface does not return it.
* **A secret is never taken as a command-line argument.** `devices complete`
  reads it from stdin, so it does not land in shell history, in `ps` output,
  or in a CI log that echoed the command.

Kept in its own module so `bartholomew/cli.py` -- a shared integration
hotspot every stream touches -- gains three registration lines rather than
six hundred.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

devices_app = typer.Typer(help="Device enrolment, credentials and capability manifests")
groups_app = typer.Typer(help="Trusted groups: membership and invitations")
share_app = typer.Typer(help="Explicit, sanitized sharing to a trusted group")

console = Console()


def _init() -> None:
    from bartholomew.platform.store import init_platform_schema

    init_platform_schema()


def _fail(message: str) -> None:
    console.print(f"\n[red]x {message}[/red]\n")
    raise typer.Exit(1)


def _read_json_file(path: str, label: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        _fail(f"could not read {label} from {path!r}: {exc}")
    if not isinstance(data, dict):
        _fail(f"{label} must be a JSON object")
    return data


def _read_secret_from_stdin(prompt: str) -> str:
    """Take a one-time secret from stdin, never from argv.

    Reads the whole of stdin when it is a pipe, so
    `cat secret.txt | bartholomew devices complete ...` works without the
    secret ever appearing in a process listing.
    """
    if sys.stdin.isatty():
        return typer.prompt(prompt, hide_input=True).strip()
    return sys.stdin.read().strip()


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


@devices_app.command("capabilities")
def devices_capabilities() -> None:
    """Show the frozen capability vocabulary this deployment understands.

    Anything absent from this list is *unsupported*, not approximated: a
    device may declare it, and this deployment will authorise nothing on the
    strength of the declaration.
    """
    from bartholomew.platform.device_capabilities import describe_vocabulary

    table = Table(title="Known device capabilities")
    table.add_column("kind")
    table.add_column("versions")
    for entry in describe_vocabulary():
        table.add_row(entry["kind"], ", ".join(str(v) for v in entry["versions"]))
    console.print(table)


@devices_app.command("enrol")
def devices_enrol(
    user_id: str = typer.Argument(..., help="Owning account's user_id (`accounts list`)"),
    name: str = typer.Argument(..., help="A label for this machine, e.g. 'desk-pc'"),
    platform: str = typer.Option("windows", help="Device platform"),
) -> None:
    """Create a pending enrolment. Issues no credential and authorises nothing."""
    from bartholomew.platform import devices

    _init()
    try:
        device_id = devices.create_pending_enrolment(user_id, name, platform=platform)
    except devices.DeviceError as exc:
        _fail(str(exc))
    console.print(f"\n[green]+ Pending enrolment created[/green] {name}")
    console.print(f"  device_id: {device_id}")
    console.print("  status: pending -- approve it to issue a one-time enrolment secret.\n")


@devices_app.command("approve")
def devices_approve(
    device_id: str = typer.Argument(..., help="device_id from `devices list`"),
    approver: str = typer.Option(..., help="Who is approving this enrolment"),
    ttl_hours: int = typer.Option(24, help="How long the one-time secret stays usable"),
) -> None:
    """Approve a pending device and print its one-time enrolment secret.

    The secret is shown once and stored nowhere. Transfer it to the machine
    out of band -- a password manager's secure note, or typed at the console
    -- and never by email or chat. If it is lost, approve again: the previous
    secret is revoked in the same transaction.
    """
    from bartholomew.platform import devices

    _init()
    try:
        issued = devices.approve_enrolment(
            device_id,
            approver=approver,
            ttl_s=max(1, ttl_hours) * 3600,
        )
    except (devices.DeviceError, devices.DeviceAuthenticationError) as exc:
        _fail(str(exc))
    console.print(f"\n[green]+ Enrolment approved[/green] {device_id}")
    console.print(f"  credential_id: {issued.credential_id}")
    console.print(f"  enrolment secret: [bold]{issued.secret}[/bold]")
    console.print("  [yellow]Shown once. Transfer it out of band, then discard it.[/yellow]")
    console.print("  The device is still 'approved', not active: it cannot authenticate")
    console.print("  anything until its first verified contact completes enrolment.\n")


@devices_app.command("complete")
def devices_complete(
    manifest_file: str = typer.Option(
        ...,
        help="Path to the device's declared capability manifest (JSON)",
    ),
) -> None:
    """Complete enrolment with a one-time secret read from stdin.

    Normally the companion does this itself on first contact. This command
    exists so an operator can verify the whole path locally before wiring a
    machine up, and so a first contact can be performed by hand during
    recovery.

    The secret is read from stdin, never taken as an argument:

        cat /run/secrets/enrolment | bartholomew devices complete --manifest-file m.json
    """
    from bartholomew.platform import devices
    from bartholomew.platform.device_capabilities import ManifestError

    _init()
    declaration = _read_json_file(manifest_file, "capability manifest")
    secret = _read_secret_from_stdin("Enrolment secret")
    if not secret:
        _fail("no enrolment secret was provided on stdin")
    try:
        verified, credential = devices.complete_enrolment(secret, declaration)
    except (devices.DeviceAuthenticationError, ManifestError) as exc:
        _fail(str(exc))
    console.print(f"\n[green]+ Device enrolled[/green] {verified.device_id}")
    console.print(f"  tenant: {verified.user_id}")
    console.print(f"  manifest_version: {verified.manifest_version}")
    console.print(f"  supported capabilities: {len(verified.manifest.known)}")
    unsupported = verified.manifest.unknown
    if unsupported:
        console.print(
            f"  [yellow]unsupported (declared, authorising nothing):[/yellow] "
            f"{', '.join(str(c) for c in unsupported)}",
        )
    console.print(f"  device credential: [bold]{credential.secret}[/bold]")
    console.print("  [yellow]Shown once. Configure the companion with it now.[/yellow]\n")


@devices_app.command("list")
def devices_list(
    user_id: str = typer.Argument(..., help="Owning account's user_id"),
) -> None:
    """List one account's devices. Never prints credential material."""
    from bartholomew.platform import devices

    _init()
    table = Table(title=f"Devices for {user_id}")
    for column in ("device_id", "name", "platform", "status", "manifest", "last seen"):
        table.add_column(column)
    for row in devices.list_devices(user_id):
        table.add_row(
            row["device_id"],
            row["display_name"],
            row["platform"],
            row["status"],
            f"v{row['manifest_version']} ({len(row['supported_capabilities'])} supported)",
            str(row["last_seen_at"] or "never"),
        )
    console.print(table)


@devices_app.command("show")
def devices_show(
    device_id: str = typer.Argument(..., help="device_id from `devices list`"),
) -> None:
    """Show one device's registry row, including unsupported declarations."""
    from bartholomew.platform import devices

    _init()
    row = devices.get_device(device_id)
    if row is None:
        _fail(f"no device {device_id!r}")
    console.print(json.dumps(row, indent=2))


@devices_app.command("manifest")
def devices_manifest(
    device_id: str = typer.Argument(..., help="device_id from `devices list`"),
) -> None:
    """Print the registered capability manifest in its canonical shape."""
    from bartholomew.platform import devices

    _init()
    rendered = devices.manifest_json(device_id)
    if rendered is None:
        _fail(f"no device {device_id!r}")
    console.print(rendered)


@devices_app.command("rotate")
def devices_rotate(
    device_id: str = typer.Argument(..., help="device_id from `devices list`"),
    actor: str = typer.Option(..., help="Who is rotating this credential"),
) -> None:
    """Issue a new device credential and invalidate every previous one."""
    from bartholomew.platform import devices

    _init()
    try:
        issued = devices.rotate_device_credential(device_id, actor=actor)
    except devices.DeviceError as exc:
        _fail(str(exc))
    console.print(f"\n[green]+ Credential rotated[/green] {device_id}")
    console.print(f"  credential_id: {issued.credential_id}")
    console.print(f"  device credential: [bold]{issued.secret}[/bold]")
    console.print("  [yellow]Shown once. The previous credential no longer works.[/yellow]\n")


@devices_app.command("disable")
def devices_disable(
    device_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="Who is disabling this device"),
) -> None:
    """Refuse a device temporarily. Credentials survive; nothing authenticates."""
    from bartholomew.platform import devices

    _init()
    try:
        devices.set_device_disabled(device_id, True, actor=actor)
    except devices.DeviceError as exc:
        _fail(str(exc))
    console.print(f"\n[yellow]! Device disabled:[/yellow] {device_id}\n")


@devices_app.command("enable")
def devices_enable(
    device_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="Who is re-enabling this device"),
) -> None:
    """Re-enable a disabled device. A revoked device is never re-enabled."""
    from bartholomew.platform import devices

    _init()
    try:
        devices.set_device_disabled(device_id, False, actor=actor)
    except devices.DeviceError as exc:
        _fail(str(exc))
    console.print(f"\n[green]+ Device enabled:[/green] {device_id}\n")


@devices_app.command("revoke")
def devices_revoke(
    device_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="Who is revoking this device"),
    reason: str = typer.Option(None, help="Why, for the audit trail"),
) -> None:
    """Revoke a lost or compromised device. Terminal, immediate, audited."""
    from bartholomew.platform import devices

    _init()
    try:
        devices.revoke_device(device_id, actor=actor, reason=reason)
    except devices.DeviceError as exc:
        _fail(str(exc))
    console.print(f"\n[red]x Device revoked:[/red] {device_id}")
    console.print("  Every credential it held is revoked. A new enrolment is required.\n")


@devices_app.command("audit")
def devices_audit(
    user_id: str = typer.Option(None, help="Restrict to one account"),
    limit: int = typer.Option(50, help="How many events"),
) -> None:
    """Enrolment, rotation, disable and revocation events, newest first."""
    from bartholomew.platform import devices

    _init()
    table = Table(title="Device audit")
    for column in ("ts", "event", "user_id", "detail"):
        table.add_column(column)
    for row in devices.device_audit(user_id=user_id, limit=limit):
        table.add_row(str(row["ts"]), row["event"], row["user_id"] or "", row["detail"] or "")
    console.print(table)


# ---------------------------------------------------------------------------
# Trusted groups
# ---------------------------------------------------------------------------


def _role(value: str):
    from bartholomew.platform.trusted_groups import GroupRole

    try:
        return GroupRole(value)
    except ValueError:
        _fail(f"unknown role {value!r}; use 'admin' or 'member'")


@groups_app.command("create")
def groups_create(
    owner_user_id: str = typer.Argument(..., help="The owner's user_id"),
    name: str = typer.Argument(..., help="A name for the group, e.g. 'Household'"),
) -> None:
    """Create a trusted group. The creating account becomes its owner and first member."""
    from bartholomew.platform import trusted_groups as tg

    _init()
    try:
        group_id = tg.create_group(owner_user_id, name)
    except tg.GroupError as exc:
        _fail(str(exc))
    console.print(f"\n[green]+ Group created[/green] {name}")
    console.print(f"  group_id: {group_id}\n")


@groups_app.command("list")
def groups_list(user_id: str = typer.Argument(..., help="Whose groups to list")) -> None:
    """Groups this account is a live member of. Never anybody else's."""
    from bartholomew.platform import trusted_groups as tg

    _init()
    table = Table(title=f"Trusted groups for {user_id}")
    for column in ("group_id", "name", "role", "archived"):
        table.add_column(column)
    for row in tg.list_groups(user_id):
        table.add_row(
            row["group_id"],
            row["name"],
            row["role"],
            "yes" if row["archived_at"] else "no",
        )
    console.print(table)


@groups_app.command("invite")
def groups_invite(
    group_id: str = typer.Argument(...),
    invited_user_id: str = typer.Argument(..., help="The invited account's user_id"),
    actor: str = typer.Option(..., help="The inviting account's user_id"),
    role: str = typer.Option("member", help="'member' or 'admin'"),
    ttl_days: int = typer.Option(7, help="How long the invitation stays open"),
) -> None:
    """Invite one existing account. An invitation confers nothing until accepted."""
    from bartholomew.platform import trusted_groups as tg

    _init()
    try:
        invitation_id = tg.invite(
            group_id,
            invited_user_id,
            actor_user_id=actor,
            role=_role(role),
            ttl_s=max(1, ttl_days) * 86400,
        )
    except (tg.GroupError, tg.GroupAccessError) as exc:
        _fail(str(exc))
    console.print(f"\n[green]+ Invitation created[/green] {invitation_id}")
    console.print("  It expires, and only the invited account can accept it.\n")


@groups_app.command("invitations")
def groups_invitations(user_id: str = typer.Argument(...)) -> None:
    """Invitations addressed to this account."""
    from bartholomew.platform import trusted_groups as tg

    _init()
    table = Table(title=f"Invitations for {user_id}")
    for column in ("invitation_id", "group", "role", "expires_at", "state"):
        table.add_column(column)
    for row in tg.list_invitations(user_id):
        state = "accepted" if row["accepted_at"] else ("declined" if row["declined_at"] else "open")
        table.add_row(
            row["invitation_id"],
            row["group_name"],
            row["role"],
            str(row["expires_at"]),
            state,
        )
    console.print(table)


@groups_app.command("accept")
def groups_accept(
    invitation_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="The accepting account's user_id"),
) -> None:
    """Accept an invitation. Only the invited account may do this."""
    from bartholomew.platform import trusted_groups as tg

    _init()
    try:
        membership = tg.accept_invitation(invitation_id, actor_user_id=actor)
    except (tg.GroupError, tg.GroupAccessError) as exc:
        _fail(str(exc))
    console.print(
        f"\n[green]+ Joined[/green] group {membership.group_id} as {membership.role.value}\n",
    )


@groups_app.command("decline")
def groups_decline(
    invitation_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="The declining account's user_id"),
) -> None:
    """Decline an invitation."""
    from bartholomew.platform import trusted_groups as tg

    _init()
    try:
        tg.decline_invitation(invitation_id, actor_user_id=actor)
    except (tg.GroupError, tg.GroupAccessError) as exc:
        _fail(str(exc))
    console.print("\n[yellow]! Invitation declined.[/yellow]\n")


@groups_app.command("members")
def groups_members(
    group_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="A member's user_id"),
) -> None:
    """List a group's members. Members only."""
    from bartholomew.platform import trusted_groups as tg

    _init()
    try:
        rows = tg.list_members(group_id, actor_user_id=actor)
    except tg.GroupAccessError as exc:
        _fail(str(exc))
    table = Table(title=f"Members of {group_id}")
    for column in ("user_id", "username", "role"):
        table.add_column(column)
    for row in rows:
        table.add_row(row["user_id"], row["username"] or "", row["role"])
    console.print(table)


@groups_app.command("set-role")
def groups_set_role(
    group_id: str = typer.Argument(...),
    target_user_id: str = typer.Argument(...),
    role: str = typer.Argument(..., help="'member' or 'admin'"),
    actor: str = typer.Option(..., help="The owner's user_id"),
) -> None:
    """Change a member's role. Owner only."""
    from bartholomew.platform import trusted_groups as tg

    _init()
    try:
        tg.set_role(group_id, target_user_id, _role(role), actor_user_id=actor)
    except (tg.GroupError, tg.GroupAccessError) as exc:
        _fail(str(exc))
    console.print(f"\n[green]+ Role updated:[/green] {target_user_id} is now {role}\n")


@groups_app.command("remove")
def groups_remove(
    group_id: str = typer.Argument(...),
    target_user_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="An owner's or admin's user_id"),
) -> None:
    """Remove a member. Access stops immediately; what they adopted stays theirs."""
    from bartholomew.platform import trusted_groups as tg

    _init()
    try:
        tg.remove_member(group_id, target_user_id, actor_user_id=actor)
    except (tg.GroupError, tg.GroupAccessError) as exc:
        _fail(str(exc))
    console.print(f"\n[yellow]! Removed[/yellow] {target_user_id} from {group_id}")
    console.print("  Their access stops now. Records they already adopted are their own.\n")


@groups_app.command("leave")
def groups_leave(
    group_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="The leaving account's user_id"),
) -> None:
    """Leave a group. The owner archives instead."""
    from bartholomew.platform import trusted_groups as tg

    _init()
    try:
        tg.leave_group(group_id, actor_user_id=actor)
    except (tg.GroupError, tg.GroupAccessError) as exc:
        _fail(str(exc))
    console.print(f"\n[yellow]! Left group[/yellow] {group_id}\n")


@groups_app.command("archive")
def groups_archive(
    group_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="The owner's user_id"),
) -> None:
    """Archive a group. Stops new activity; the record of what was shared survives."""
    from bartholomew.platform import trusted_groups as tg

    _init()
    try:
        tg.archive_group(group_id, actor_user_id=actor)
    except (tg.GroupError, tg.GroupAccessError) as exc:
        _fail(str(exc))
    console.print(f"\n[yellow]! Group archived:[/yellow] {group_id}\n")


@groups_app.command("audit")
def groups_audit(
    group_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="A member's user_id"),
    limit: int = typer.Option(50),
) -> None:
    """Membership and sharing events for one group. Members only, content-free."""
    from bartholomew.platform import trusted_groups as tg

    _init()
    try:
        rows = tg.group_audit(group_id, actor_user_id=actor, limit=limit)
    except tg.GroupAccessError as exc:
        _fail(str(exc))
    table = Table(title=f"Audit for {group_id}")
    for column in ("ts", "event", "user_id", "detail"):
        table.add_column(column)
    for row in rows:
        table.add_row(str(row["ts"]), row["event"], row["user_id"] or "", row["detail"] or "")
    console.print(table)


# ---------------------------------------------------------------------------
# Sharing
# ---------------------------------------------------------------------------


def _source_record(record_file: str):
    from bartholomew.kernel.trusted_share import SourceRecord

    data = _read_json_file(record_file, "source record")
    missing = [field for field in ("kind", "key", "value") if field not in data]
    if missing:
        _fail(f"source record is missing {', '.join(missing)}")
    return SourceRecord(kind=data["kind"], key=data["key"], value=data["value"])


@share_app.command("propose")
def share_propose(
    record_file: str = typer.Option(..., help="Path to the selected source record (JSON)"),
    kind: str = typer.Option(..., help="competency | correction | household_routine | guidance"),
    group: str = typer.Option(..., help="The destination group_id"),
    publisher: str = typer.Option(..., help="The publishing account's user_id"),
    out: str = typer.Option(None, help="Write the proposed package here for inspection"),
) -> None:
    """Sanitize a selected record into a proposed package. Publishes nothing.

    This is the inspection step. Read what comes back -- including
    `sanitization.removed_fields` -- before running `share publish`.
    """
    from bartholomew.kernel.trusted_share import SanitizationRefusedError, ShareEligibilityError
    from bartholomew.platform import share_exchange as sx
    from bartholomew.platform import trusted_groups as tg

    _init()
    record = _source_record(record_file)
    try:
        package = sx.propose(
            record,
            requested_kind=kind,
            group_id=group,
            publisher_user_id=publisher,
        )
    except (ShareEligibilityError, SanitizationRefusedError, tg.GroupAccessError) as exc:
        _fail(str(exc))
    rendered = json.dumps(package.as_dict(), indent=2)
    if out:
        Path(out).write_text(rendered, encoding="utf-8")
        console.print(f"\n[green]+ Proposed package written to[/green] {out}")
    console.print(rendered)
    console.print(
        "\n[yellow]Nothing has been published.[/yellow] Inspect the content and the "
        "removed fields, then run `share publish`.\n",
    )


@share_app.command("publish")
def share_publish(
    package_file: str = typer.Option(..., help="Path to the inspected package (JSON)"),
    publisher: str = typer.Option(..., help="The publishing account's user_id"),
    confirm_group: str = typer.Option(
        ...,
        help="Name the destination group again, explicitly, to confirm",
    ),
) -> None:
    """Publish an inspected package to one named group.

    The group must be named again here. There is no publish-to-all and no
    inherited destination: one package, one group, one confirmation.
    """
    from bartholomew.kernel.trusted_share import TrustedSharePackage
    from bartholomew.platform import share_exchange as sx
    from bartholomew.platform import trusted_groups as tg

    _init()
    data = _read_json_file(package_file, "share package")
    try:
        package = TrustedSharePackage.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        _fail(f"package file is not a share package: {exc}")
    try:
        sx.publish(package, publisher_user_id=publisher, confirm_group_id=confirm_group)
    except (sx.PublicationError, tg.GroupAccessError) as exc:
        _fail(str(exc))
    console.print(f"\n[green]+ Published[/green] share {package.share_id} rev {package.revision}")
    console.print(f"  group: {package.group_id}\n")


@share_app.command("revise")
def share_revise(
    share_id: str = typer.Argument(...),
    record_file: str = typer.Option(..., help="Path to the updated source record (JSON)"),
    kind: str = typer.Option(..., help="The package type, unchanged"),
    publisher: str = typer.Option(..., help="The original publisher's user_id"),
    expected_revision: int = typer.Option(..., help="The revision you were looking at"),
) -> None:
    """Publish a new revision. Refuses if the share moved under you."""
    from bartholomew.kernel.trusted_share import SanitizationRefusedError, ShareEligibilityError
    from bartholomew.platform import share_exchange as sx
    from bartholomew.platform import trusted_groups as tg

    _init()
    record = _source_record(record_file)
    try:
        package = sx.publish_revision(
            share_id,
            record,
            requested_kind=kind,
            publisher_user_id=publisher,
            expected_revision=expected_revision,
        )
    except sx.ConcurrentRevisionError as exc:
        _fail(f"{exc}\nNothing was written. Re-read the share and decide again.")
    except (
        sx.PublicationError,
        sx.ShareNotFoundError,
        ShareEligibilityError,
        SanitizationRefusedError,
        tg.GroupAccessError,
    ) as exc:
        _fail(str(exc))
    console.print(f"\n[green]+ Revision published[/green] rev {package.revision}")
    console.print("  Recipients are offered it as an update. Nobody's copy was replaced.\n")


@share_app.command("inbox")
def share_inbox(
    user_id: str = typer.Argument(..., help="The recipient's user_id"),
) -> None:
    """What trusted groups have shared with this account, and what became of it."""
    from bartholomew.platform import share_exchange as sx

    _init()
    table = Table(title=f"Group inbox for {user_id}")
    for column in ("share_id", "kind", "rev", "state", "adopted", "update", "revoked"):
        table.add_column(column)
    for entry in sx.inbox(user_id):
        table.add_row(
            entry.package.share_id,
            entry.package.kind,
            str(entry.latest_revision),
            entry.state,
            str(entry.adopted_revision or "-"),
            "yes" if entry.has_pending_update else "-",
            "yes" if entry.package.is_revoked else "-",
        )
    console.print(table)


@share_app.command("inspect")
def share_inspect(
    share_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="The reading account's user_id"),
    revision: int = typer.Option(None, help="A specific revision; default is the latest"),
) -> None:
    """Read one package in full, before deciding anything about it."""
    from bartholomew.platform import share_exchange as sx

    _init()
    try:
        package = sx.inspect(share_id, actor_user_id=actor, revision=revision)
    except sx.ShareNotFoundError as exc:
        _fail(str(exc))
    console.print(json.dumps(package.as_dict(), indent=2))


@share_app.command("revisions")
def share_revisions(
    share_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="A member's user_id"),
) -> None:
    """Every revision of one share, oldest first."""
    from bartholomew.platform import share_exchange as sx

    _init()
    try:
        packages = sx.revisions(share_id, actor_user_id=actor)
    except sx.ShareNotFoundError as exc:
        _fail(str(exc))
    table = Table(title=f"Revisions of {share_id}")
    for column in ("revision", "published_at", "content_hash", "revoked"):
        table.add_column(column)
    for package in packages:
        table.add_row(
            str(package.revision),
            package.published_at,
            package.content_hash()[:16],
            "yes" if package.is_revoked else "-",
        )
    console.print(table)


@share_app.command("decline")
def share_decline(
    share_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="The recipient's user_id"),
) -> None:
    """Decline a shared package. Records a decision; adopts nothing."""
    from bartholomew.platform import share_exchange as sx

    _init()
    try:
        sx.decline(share_id, actor_user_id=actor)
    except sx.ShareNotFoundError as exc:
        _fail(str(exc))
    console.print(f"\n[yellow]! Declined[/yellow] {share_id}\n")


@share_app.command("adopt")
def share_adopt(
    share_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="The recipient's user_id"),
    revision: int = typer.Option(None, help="A specific revision; default is the latest"),
    out: str = typer.Option(None, help="Write the adopted package here"),
) -> None:
    """Take a package for local consideration. **This is not acceptance.**

    Adoption records the decision on the exchange and returns the package.
    Turning it into a local candidate happens in the recipient's own runtime,
    and making it retrievable knowledge needs their own candidate-bound
    approval on top of that.
    """
    from bartholomew.platform import share_exchange as sx
    from bartholomew.platform import trusted_groups as tg

    _init()
    try:
        package = sx.adopt(share_id, actor_user_id=actor, revision=revision)
    except (sx.ShareNotFoundError, sx.AdoptionRefusedError, tg.GroupAccessError) as exc:
        _fail(str(exc))
    rendered = json.dumps(package.as_dict(), indent=2)
    if out:
        Path(out).write_text(rendered, encoding="utf-8")
    console.print(rendered)
    console.print(
        f"\n[green]+ Adopted[/green] {share_id} rev {package.revision} for local review.\n"
        "  [yellow]This is a candidate, not knowledge.[/yellow] It is stored under a kind\n"
        "  retrieval cannot see, and becomes retrievable only after an explicit\n"
        "  acceptance approval bound to this exact candidate.\n",
    )


@share_app.command("revoke")
def share_revoke(
    share_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="The original publisher's user_id"),
) -> None:
    """Withdraw a share. Stops adoption and updates; deletes nobody's local record."""
    from bartholomew.platform import share_exchange as sx

    _init()
    try:
        sx.revoke(share_id, actor_user_id=actor)
    except sx.ShareNotFoundError as exc:
        _fail(str(exc))
    console.print(f"\n[red]x Share revoked:[/red] {share_id}")
    console.print("  No member may adopt it now, and no revision may be published.")
    console.print("  Records already adopted stay with their recipients, marked withdrawn.\n")


@share_app.command("provenance")
def share_provenance(
    share_id: str = typer.Argument(...),
    actor: str = typer.Option(..., help="The recipient's user_id"),
) -> None:
    """Where an adopted share came from, and whether it has been withdrawn."""
    from bartholomew.platform import share_exchange as sx

    _init()
    try:
        console.print(json.dumps(sx.provenance(share_id, actor_user_id=actor), indent=2))
    except sx.ShareNotFoundError as exc:
        _fail(str(exc))


__all__ = ["devices_app", "groups_app", "share_app"]
