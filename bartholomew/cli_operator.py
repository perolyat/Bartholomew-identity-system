"""`bartholomew operator` -- the console a non-developer drives Bartholomew from.

The first live test recorded the gap this module closes (docs/G Sec.9, finding 8):
using Bartholomew for the first time required hand-written JSON, three
terminals, environment variables and raw HTTP calls to request and approve a
single Windows action. Nothing was missing from the architecture; what was
missing was a way for a person to *reach* it.

So this is a surface and not a system. Every command is a thin client over the
loopback control plane the server already exposes, and **none of them decides
anything**. There is no governance here: no brake logic, no approval binding,
no lease, no capability selection, no lesson acceptance. Each command asks the
one authority that owns the question and prints what it said. If a command
appears to have succeeded it is because the server said so, and if the server
said `unknown` this prints `unknown`.

    bartholomew operator status                     one screen: is he halted, armed, waiting?
    bartholomew operator actions list|show|approve|deny
    bartholomew operator channel arm|status|disarm
    bartholomew operator brake status|on|off
    bartholomew operator consent pending|approve|deny
    bartholomew operator lessons pending|show|approve|accept|reject
    bartholomew operator task run|show

Three deliberate refusals, because a console that quietly widened authority
would be worse than no console:

* **Nothing here mints an approval.** `actions approve` posts to the approval
  route, which builds the approval from the stored action and binds it to six
  facts. This module never constructs one.
* **Nothing here accepts a lesson on its own.** `lessons accept` is two
  explicit commands (`approve` then `accept`) because acceptance requires a
  candidate-bound authorization that a person grants, and collapsing them into
  one flag would make manual acceptance a formality.
* **Nothing here converts an unknown into a success.** `explain_outcome` is the
  single function that turns a status into a sentence, and `unknown` /
  `effect_unverifiable` read as unverified in every one of them.

The commands live here rather than in `cli.py` for the reason `cli_trust.py`,
`cli_companion.py` and `cli_consent.py` all give: that file is a shared
integration hotspot, and one registration line is a smaller thing for every
other stream to merge around than several hundred.
"""

from __future__ import annotations

import json
from typing import Any

import typer

from bartholomew.cli_companion import DEFAULT_BASE_URL, is_safe_base_url

operator_app = typer.Typer(help="The operator console: see, approve and halt what Bartholomew does")
actions_app = typer.Typer(help="Pending and recent Windows actions")
channel_app = typer.Typer(help="The Windows action channel's arming window")
brake_app = typer.Typer(help="The Parking Brake, against the running server")
consent_app = typer.Typer(help="A device's request to observe, and your answer")
lessons_app = typer.Typer(help="Candidate lessons awaiting your explicit acceptance")
task_app = typer.Typer(help="Give Bartholomew a task in ordinary words")

operator_app.add_typer(actions_app, name="actions")
operator_app.add_typer(channel_app, name="channel")
operator_app.add_typer(brake_app, name="brake")
operator_app.add_typer(consent_app, name="consent")
operator_app.add_typer(lessons_app, name="lessons")
operator_app.add_typer(task_app, name="task")

_BASE_URL_HELP = "The running Bartholomew. Default: loopback on the standard port."
_DB_HELP = (
    "Kernel database, for the offline fallback only. Default: BARTH_DB_PATH, else "
    "<project root>/data/barth.db -- the same file the running server reads."
)

_REVIEWER_HELP = (
    "Your name, as it will be recorded in the audit of this decision. On a "
    "deployment with sign-in the server records the signed-in person regardless."
)

#: The exit code every command uses for "the answer is no, and that is a real
#: answer" -- a refusal, a 4xx, an unreachable server. Distinct from 2, which
#: means the operator asked for something malformed.
EXIT_REFUSED = 1
EXIT_MISUSE = 2


# ---------------------------------------------------------------------------
# The transport. One place, so there is one answer to "how does this reach him".
# ---------------------------------------------------------------------------


class ServerUnreachableError(RuntimeError):
    """The console could not reach Bartholomew at all.

    Its own type because "he said no" and "I could not ask him" are different
    facts, and a console that printed them the same way would be the exact
    dishonesty this package exists to remove.
    """


def _echo(text: str, *, err: bool = False) -> None:
    typer.echo(text, err=err)


def _emit(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


def call(
    method: str,
    path: str,
    *,
    base_url: str,
    body: dict | None = None,
    device_credential: bool = False,
    device_id: str | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    """One loopback call. Returns `(status_code, parsed_body)`.

    Raises `ServerUnreachableError` rather than inventing a status code, so a caller
    can never mistake "no server" for "the server refused".
    """
    import requests  # noqa: PLC0415

    if not is_safe_base_url(base_url):
        # The device credential and the consent nonce both travel on this
        # transport. Sending either over plaintext HTTP to anything but
        # loopback is refused rather than warned about.
        raise typer.BadParameter(
            f"Refusing to talk to {base_url!r} over plaintext HTTP. "
            "Use loopback, or a URL with https://.",
        )

    headers: dict[str, str] = {}
    if device_credential:
        from bartholomew.platform.companion_credential import load  # noqa: PLC0415
        from bartholomew.platform.device_inbound import DEVICE_CREDENTIAL_HEADER  # noqa: PLC0415

        found = load(device_id=device_id)
        if found is None:
            raise typer.BadParameter(
                "No companion credential is configured on this machine. Store the one "
                "issued at enrolment with:\n"
                "  bartholomew companion credential store --device-id <DEVICE_ID>",
            )
        resolved_device, secret = found
        headers[DEVICE_CREDENTIAL_HEADER] = secret
        if body is not None and not body.get("device_id"):
            body["device_id"] = resolved_device

    try:
        response = requests.request(
            method,
            f"{base_url.rstrip('/')}{path}",
            headers=headers,
            json=body,
            timeout=timeout,
        )
    except Exception as e:  # noqa: BLE001 - a console reports, it does not raise
        raise ServerUnreachableError(f"Could not reach Bartholomew at {base_url}: {e}") from e

    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"detail": response.text}


def _detail(payload: Any) -> str:
    """The server's own words for a refusal, never this module's paraphrase."""
    if isinstance(payload, dict):
        for key in ("detail", "reason", "message", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return json.dumps(payload, default=str)


def _report(status: int, payload: Any, *, success: str | None = None) -> int:
    """Print one server answer and return the process exit code."""
    if status < 400:
        if success:
            _echo(success)
        _emit(payload)
        return 0
    _echo(f"Refused ({status}): {_detail(payload)}", err=True)
    return EXIT_REFUSED


def _run(fn) -> None:
    """Run one command body, turning an unreachable server into an exit code."""
    try:
        code = fn()
    except ServerUnreachableError as e:
        _echo(str(e), err=True)
        raise typer.Exit(code=EXIT_REFUSED) from None
    raise typer.Exit(code=code)


# ---------------------------------------------------------------------------
# Truthful outcome explanation. The one place a status becomes a sentence.
# ---------------------------------------------------------------------------

#: Every action outcome this build can report, and what a person should take
#: from it. Exhaustive over both `actuation.result.ActionResultStatus` and
#: `actuation.store.ActionState` -- the two vocabularies a person meets, which
#: overlap but are not the same set -- and a status this table does not know is
#: reported as *unknown*, never as success. `tests/golden_path/
#: test_outcome_truthfulness.py` asserts the exhaustiveness against the enums
#: themselves, so a ninth member added by a later wave fails a test here rather
#: than silently rendering as "not understood".
#:
#: `unknown` and `effect_unverifiable` are deliberately worded so that no
#: reading of them is "it worked". That is the whole point of the table: the
#: live test found the loop's honest verdicts were the ones most easily lost in
#: presentation, and a console is exactly where they get lost.
_OUTCOME_SENTENCES: dict[str, str] = {
    "succeeded": "It ran and the effect was confirmed by reading the machine back.",
    "failed": "It ran and did not do what was asked. The machine was read back and disagreed.",
    "refused": "It never ran: governance refused it.",
    "unknown": (
        "It was issued and nobody can say whether it worked. "
        "This is NOT success -- nothing read the machine back."
    ),
    "expired": "It was never approved in time, so it can never run.",
    "cancelled": "It was withdrawn before it could run.",
    "aborted_by_brake": "It was stopped by the Parking Brake after it had been leased.",
    "timed_out": (
        "It was issued and the device stopped waiting for an answer. "
        "Whether it took effect is unknown."
    ),
    "accepted": "It has been recorded and admitted. Nothing has run.",
    "started": "The device has taken it and may be running it now.",
    "pending_approval": "It is waiting for you to approve or deny it. Nothing has run.",
    "approved": "You approved it. It has not run yet.",
    "leased": "The device has taken it and may be running it now.",
    "requested": "It has been proposed. Nothing has run.",
}

#: Result-level detail that must never be presented as a confirmed effect, even
#: when the surrounding status is optimistic.
_UNVERIFIED_MARKERS = frozenset({"effect_unverifiable", "unavailable", "not_verified"})


def explain_outcome(
    status: str | None,
    *,
    verified: Any = None,
    verify_method: str | None = None,
) -> str:
    """One sentence a person can act on, that never overstates what is known.

    Three different facts hide behind a device's `succeeded`, and each gets its
    own sentence: `verified is True` means the machine was read back and agreed;
    `verified is False` means somebody looked and it had not happened; and
    `verified is None` means nobody recorded a verification at all. Only the
    first is confirmed success. The other two are reported as what they are.
    """
    key = (status or "").strip().lower()
    sentence = _OUTCOME_SENTENCES.get(key)
    if sentence is None:
        return (
            f"Outcome {status!r} is not one this console understands, so it is "
            "reported as unknown rather than guessed at."
        )
    if key == "succeeded":
        if verified is True and verify_method not in _UNVERIFIED_MARKERS:
            return sentence
        if verified is False or verify_method in _UNVERIFIED_MARKERS:
            # A device that says `succeeded` while nothing read the machine
            # back is reporting issuance, not effect.
            return (
                "It was issued and reported as done, but nothing confirmed the effect "
                "on the machine. This is NOT confirmed success."
            )
        return (
            "The device reported it as done, and no verification was recorded either "
            "way. This is NOT confirmed success -- nothing here read the machine back."
        )
    if verify_method in _UNVERIFIED_MARKERS and key not in {"refused", "cancelled", "expired"}:
        return f"{sentence} (No read-back was available: {verify_method}.)"
    return sentence


def _render_value(value: Any) -> str:
    """One parameter value, as a person about to approve it should see it.

    Plainly quoted, because this is the surface a non-developer reads and a
    Windows path rendered through `repr` arrives with every backslash doubled --
    which is exactly the kind of small illegibility that made the first live
    test unusable.

    The exception is a string carrying leading or trailing whitespace or a
    control character, where what is *not* visible is the whole decision. Those
    fall back to `repr` and say so, because approving text you cannot see the
    edges of is the same failure as approving a digest.
    """
    if not isinstance(value, str):
        return repr(value)
    if value != value.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return f"{value!r}   <- shown exactly; it carries whitespace or control characters"
    return f'"{value}"'


def _outcome_line(action: dict[str, Any], results: list[dict[str, Any]]) -> str:
    """The account of one action. The stored row's state is the authority.

    The results history is read only for the verification evidence a terminal
    row carries. A `started` note followed by a brake abort or a cancellation
    must read as the abort, not as "may be running", so the row's own state --
    the one column the envelope maintains -- decides which sentence is used.
    """
    state = action.get("state") or action.get("status")
    evidence: Any = {}
    if results:
        latest = results[-1]
        evidence = latest.get("evidence") or {}
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except ValueError:
                evidence = {}
        if not isinstance(evidence, dict):
            evidence = {}
    return explain_outcome(
        state,
        verified=evidence.get("verified"),
        verify_method=evidence.get("verify_method"),
    )


# ---------------------------------------------------------------------------
# status -- the one screen
# ---------------------------------------------------------------------------


def _safe(base_url: str, method: str, path: str) -> tuple[int, Any] | None:
    """A read that contributes to the overview, or `None` if it is not available.

    The overview must render on a partly-available system: a build without the
    learning routes, a halted server, a channel that has never been armed. A
    section that could not be read says so instead of being omitted.
    """
    try:
        return call(method, path, base_url=base_url, timeout=10.0)
    except (ServerUnreachableError, typer.BadParameter):
        return None


@operator_app.command("status")
def operator_status(
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """Everything waiting for you, in one place: halt, channel, actions, asks.

    This is the screen the live test needed and did not have. It reads only;
    it changes nothing, and it is readable while the Parking Brake is engaged
    because inspection is exactly what a halt must not hide.
    """

    def body() -> int:
        lines: list[str] = []
        unavailable: list[str] = []

        brake = _safe(base_url, "GET", "/api/governance/brake")
        if brake is None:
            raise ServerUnreachableError(f"Could not reach Bartholomew at {base_url}.")
        if brake[0] < 400 and isinstance(brake[1], dict):
            state = brake[1].get("brake") or brake[1]
            engaged = bool(state.get("engaged"))
            scopes = ", ".join(sorted(state.get("scopes") or [])) or "-"
            lines.append(
                f"Parking Brake: {'ENGAGED' if engaged else 'clear'}"
                + (f"  (scopes: {scopes})" if engaged else ""),
            )
        else:
            unavailable.append(f"parking brake ({_detail(brake[1])})")

        channel = _safe(base_url, "GET", "/api/actions/channel")
        if channel and channel[0] < 400 and isinstance(channel[1], dict):
            window = channel[1].get("channel") or channel[1]
            armed = bool(window.get("armed"))
            lines.append(
                "Action channel: "
                + (
                    f"ARMED for {window.get('device_id', '?')}, "
                    f"{window.get('seconds_remaining', '?')}s left"
                    if armed
                    else "closed (nothing can be carried out)"
                ),
            )
        elif channel is not None:
            unavailable.append(f"action channel ({_detail(channel[1])})")

        actions = _safe(base_url, "GET", "/api/actions?limit=100")
        if actions and actions[0] < 400 and isinstance(actions[1], dict):
            rows = actions[1].get("actions") or []
            pending = [r for r in rows if (r.get("state") or "") == "pending_approval"]
            lines.append(f"Actions waiting for your decision: {len(pending)}")
            for row in pending[:10]:
                lines.append(f"  {row.get('action_id')}  {row.get('capability')}")
            if pending:
                lines.append("  Inspect one with:  bartholomew operator actions show <action_id>")
        elif actions is not None:
            unavailable.append(f"actions ({_detail(actions[1])})")

        asks = _safe(base_url, "GET", "/api/device-consent/pending")
        if asks and asks[0] < 400 and isinstance(asks[1], dict):
            rows = asks[1].get("pending") or asks[1].get("requests") or []
            lines.append(f"Devices asking to observe: {len(rows)}")
            if rows:
                lines.append("  Answer with:  bartholomew operator consent pending")
        elif asks is not None:
            unavailable.append(f"device consent ({_detail(asks[1])})")

        lessons = _safe(base_url, "GET", "/api/learning/candidates?limit=100")
        if lessons and lessons[0] < 400 and isinstance(lessons[1], dict):
            rows = lessons[1].get("candidates") or []
            waiting = [r for r in rows if (r.get("review_state") or "") == "proposed"]
            lines.append(f"Lessons awaiting your explicit acceptance: {len(waiting)}")
            if waiting:
                lines.append("  Review with:  bartholomew operator lessons pending")
        elif lessons is not None:
            unavailable.append(f"candidate lessons ({_detail(lessons[1])})")

        _echo("\n".join(lines))
        if unavailable:
            # Named, not omitted. A section that silently vanished would read
            # as "nothing is waiting", which is a different claim.
            _echo("\nCould not read: " + "; ".join(unavailable), err=True)
        return 0

    _run(body)


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------


@actions_app.command("list")
def actions_list(
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
    limit: int = typer.Option(50, "--limit", min=1, max=100),
    pending_only: bool = typer.Option(
        False,
        "--pending-only",
        help="Only the actions still waiting for your decision.",
    ),
) -> None:
    """Every recent action and what happened to it, newest first.

    Parameters here are the redacted form -- text somebody asked to have typed
    is a digest and a length. Use `actions show` to read what an action would
    actually do before approving it.
    """

    def body() -> int:
        status, payload = call("GET", f"/api/actions?limit={limit}", base_url=base_url)
        if status >= 400:
            return _report(status, payload)
        rows = payload.get("actions") or []
        if pending_only:
            rows = [r for r in rows if (r.get("state") or "") == "pending_approval"]
        if not rows:
            _echo("No actions." if not pending_only else "Nothing is waiting for your decision.")
            return 0
        for row in rows:
            _echo(f"\n{row.get('action_id')}")
            _echo(f"  capability: {row.get('capability')}  (v{row.get('capability_version')})")
            _echo(f"  device:     {row.get('device_id')}")
            _echo(f"  state:      {row.get('state')}   status: {row.get('status')}")
            _echo(f"  {explain_outcome(row.get('status') or row.get('state'))}")
        _echo(
            "\nInspect one before deciding:  bartholomew operator actions show <action_id>",
        )
        return 0

    _run(body)


@actions_app.command("show")
def actions_show(
    action_id: str = typer.Argument(..., help="From `bartholomew operator actions list`"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """What this action would actually do -- the canonical parameters.

    This is the approval surface. The list view redacts; this one discloses,
    because a person cannot approve text they have not read, and an approval
    bound to a digest the approver never saw expanded is an approval in name
    only.
    """

    def body() -> int:
        status, payload = call("GET", f"/api/actions/{action_id}", base_url=base_url)
        if status >= 400:
            return _report(status, payload)
        action = payload.get("action") or {}
        summary = payload.get("approval_summary") or {}
        results = payload.get("results") or []

        _echo(f"\nAction {action.get('action_id')}")
        _echo(f"  device:      {action.get('device_id')}")
        _echo(f"  capability:  {action.get('capability')} (v{action.get('capability_version')})")
        _echo(f"  risk:        {summary.get('risk_class')}")
        _echo(f"  approval:    {summary.get('approval_requirement')}")
        _echo(f"  what it can do: {summary.get('summary')}")
        _echo(f"  state:       {action.get('state')}   status: {action.get('status')}")
        # `canonical_parameters` is what this action would actually do, and the
        # read route discloses it transiently to exactly this surface. Falling
        # back to the redacted `parameters` when it is absent is deliberate and
        # is labelled: a digest presented as if it were the text would be the
        # "approval in name only" this surface exists to prevent.
        canonical = action.get("canonical_parameters")
        if canonical:
            _echo("\n  This request asks for, exactly:")
            for key, value in sorted(canonical.items()):
                _echo(f"    {key} = {_render_value(value)}")
        else:
            _echo("\n  Only the redacted form is available for this action:")
            for key, value in sorted((action.get("parameters") or {}).items()):
                _echo(f"    {key} = {_render_value(value)}")
            _echo(
                "    (The canonical parameters are disclosed only while the action can "
                "still run; this one is past that.)",
            )
        if results:
            _echo("\n  What happened:")
            for entry in results:
                _echo(
                    f"    {entry.get('observed_at')}  {entry.get('status')}  {entry.get('detail')}",
                )
        recovery = payload.get("recovery") or action.get("recovery")
        if isinstance(recovery, dict) and recovery.get("outcome"):
            # W03-C's recovery plan is advice about a *finished* action. It is
            # shown as the server's own words, and it is not shown at all on a
            # row that has not run yet, where "nothing to do" would misread.
            if (action.get("state") or "") not in {"pending_approval", "approved", "leased"}:
                _echo(f"\n  Next step ({recovery.get('outcome')}): {recovery.get('reason')}")
                if recovery.get("requires_new_approval"):
                    _echo("  Any retry is a new action and needs a new approval.")
        _echo(f"\n  {_outcome_line(action, results)}")
        _echo(
            f"\n  Approve:  bartholomew operator actions approve {action.get('action_id')}"
            f"\n  Deny:     bartholomew operator actions deny {action.get('action_id')}",
        )
        return 0

    _run(body)


@actions_app.command("approve")
def actions_approve(
    action_id: str = typer.Argument(..., help="From `bartholomew operator actions list`"),
    note: str = typer.Option(None, "--note", help="Why you approved it. Kept in the audit."),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """Approve exactly this action, as it stands.

    The approval is built by the server from the stored action and bound to
    the action, tenant, device, capability, version, parameter fingerprint,
    expiry and you. This console constructs no part of it, and an approval for
    one action can authorise no other.
    """

    def body() -> int:
        status, payload = call(
            "POST",
            f"/api/actions/{action_id}/approve",
            base_url=base_url,
            body={"note": note},
        )
        return _report(status, payload, success="Approved. It still needs an armed channel to run.")

    _run(body)


@actions_app.command("deny")
def actions_deny(
    action_id: str = typer.Argument(..., help="From `bartholomew operator actions list`"),
    reason: str = typer.Option(None, "--reason", help="Why. Kept in the audit."),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """Withdraw this action so it can never run.

    Deliberately reachable while the Parking Brake is engaged: denying only
    ever removes an action's ability to happen, and a halt that stopped you
    withdrawing something would be a halt that made things less safe.
    """

    def body() -> int:
        status, payload = call(
            "POST",
            f"/api/actions/{action_id}/cancel",
            base_url=base_url,
            body={"reason": reason},
        )
        return _report(status, payload, success="Withdrawn. It can never run.")

    _run(body)


@actions_app.command("explain")
def actions_explain(
    action_id: str = typer.Argument(..., help="From `bartholomew operator actions list`"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """What actually happened, in one sentence, without overstating it."""

    def body() -> int:
        status, payload = call("GET", f"/api/actions/{action_id}", base_url=base_url)
        if status >= 400:
            return _report(status, payload)
        _echo(_outcome_line(payload.get("action") or {}, payload.get("results") or []))
        return 0

    _run(body)


# ---------------------------------------------------------------------------
# channel
# ---------------------------------------------------------------------------


@channel_app.command("status")
def channel_status(
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """Whether the machine may carry anything out at all right now."""
    _run(lambda: _report(*call("GET", "/api/actions/channel", base_url=base_url)))


@channel_app.command("arm")
def channel_arm(
    minutes: int = typer.Option(None, "--minutes", help="Up to 15; the default is 15."),
    reason: str = typer.Option(None, "--reason"),
    device_id: str = typer.Option(None, "--device-id"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """Open the action channel for a bounded window.

    **Arming is not approval.** It authorises no action: every action still
    needs its own explicit, content-bound approval, and an unapproved one is
    refused on an armed channel exactly as it was on a closed one. Arming is
    the coarser question of whether the machine may carry out anything at all.
    """

    def body() -> int:
        payload: dict = {"device_id": device_id or "", "reason": reason}
        if minutes:
            payload["seconds"] = minutes * 60
        status, response = call(
            "POST",
            "/api/actions/channel/arm",
            base_url=base_url,
            body=payload,
            device_credential=True,
            device_id=device_id,
        )
        return _report(status, response, success="Channel armed. No action is approved by this.")

    _run(body)


@channel_app.command("disarm")
def channel_disarm(
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """Close the channel immediately. Needs no credential and is never refused."""
    _run(
        lambda: _report(
            *call("POST", "/api/actions/channel/disarm", base_url=base_url, body={}),
            success="Channel closed.",
        ),
    )


# ---------------------------------------------------------------------------
# brake -- against the running server
# ---------------------------------------------------------------------------
#
# The live test's second operator defect was a brake command that wrote to a
# scratch database while the server read another one, so the halt the operator
# believed they had engaged was not the halt the server enforced. These
# commands therefore talk to the **running server**, which reads the brake
# through the one `GovernanceStore` every other gate reads.
#
# `--offline` is the documented fallback for a server that is not running. It
# resolves the database through `bartholomew.kernel.db_paths` -- the one
# resolver -- and prints the file it touched, so the two can never silently
# disagree again.


def _offline_brake_db(explicit: str | None) -> tuple[str, str]:
    from bartholomew.kernel.db_paths import (  # noqa: PLC0415
        describe_kernel_db_path,
        resolve_kernel_db_path,
    )  # noqa: PLC0415

    return resolve_kernel_db_path(explicit), describe_kernel_db_path(explicit)["source"]


@brake_app.command("status")
def brake_status(
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
    offline: bool = typer.Option(False, "--offline", help="Read the database directly instead."),
    db: str = typer.Option(None, "--db", help=_DB_HELP),
) -> None:
    """Is Bartholomew halted, and over what?"""

    def body() -> int:
        if offline:
            from bartholomew.orchestrator.safety.governance_store import (  # noqa: PLC0415
                GovernanceStore,
            )  # noqa: PLC0415

            path, source = _offline_brake_db(db)
            state = GovernanceStore(path).state()
            _echo(f"Parking Brake: {'ENGAGED' if state.engaged else 'clear'}")
            if state.engaged:
                _echo(f"Scopes: {', '.join(sorted(state.scopes))}")
            _echo(f"Database: {path}  (from {source})")
            _echo("Read from the database, not from a running server.")
            return 0
        return _report(*call("GET", "/api/governance/brake", base_url=base_url))

    _run(body)


@brake_app.command("on")
def brake_on(
    scope: list[str] = typer.Option(
        None,
        "--scope",
        help="global, skills, sight, voice, scheduler, training, actuation. Default: global.",
    ),
    reason: str = typer.Option("operator console", "--reason"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
    offline: bool = typer.Option(False, "--offline", help="Write the database directly instead."),
    db: str = typer.Option(None, "--db", help=_DB_HELP),
) -> None:
    """Halt Bartholomew. Everything downstream reads this before it acts."""
    scopes = list(scope) if scope else ["global"]

    def body() -> int:
        if offline:
            from bartholomew.orchestrator.safety.governance_store import (  # noqa: PLC0415
                GovernanceStore,
                WriteFenceClosedError,
            )
            from bartholomew_api_bridge_v0_1.services.api.routes.governance import (  # noqa: PLC0415
                VALID_SCOPES,
            )

            unknown = sorted(set(scopes) - VALID_SCOPES)
            if unknown:
                # The same refusal the server gives, so the offline path cannot
                # write a scope no consumer reads and call it a halt.
                _echo(f"Unknown scope(s) {unknown}. Valid: {sorted(VALID_SCOPES)}", err=True)
                return EXIT_MISUSE
            path, source = _offline_brake_db(db)
            try:
                GovernanceStore(path).engage(
                    *scopes,
                    reason=f"operator console (offline): {reason}",
                    actor="operator-cli",
                )
            except WriteFenceClosedError as e:
                _echo(f"Could not engage: {e}", err=True)
                return EXIT_REFUSED
            _echo(f"Parking brake ENGAGED -- scopes: {', '.join(sorted(scopes))}")
            _echo(f"Database: {path}  (from {source})")
            return 0
        status, payload = call(
            "POST",
            "/api/governance/brake/engage",
            base_url=base_url,
            body={"scopes": scopes, "reason": reason},
        )
        return _report(status, payload, success="Parking brake ENGAGED.")

    _run(body)


@brake_app.command("off")
def brake_off(
    reason: str = typer.Option("operator console", "--reason"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
    offline: bool = typer.Option(False, "--offline", help="Write the database directly instead."),
    db: str = typer.Option(None, "--db", help=_DB_HELP),
) -> None:
    """Release the halt."""

    def body() -> int:
        if offline:
            from bartholomew.orchestrator.safety.governance_store import (  # noqa: PLC0415
                GovernanceStore,
                StaleGovernanceWriteError,
                WriteFenceClosedError,
            )

            path, source = _offline_brake_db(db)
            try:
                GovernanceStore(path).disengage(
                    reason=f"operator console (offline): {reason}",
                    actor="operator-cli",
                )
            except (WriteFenceClosedError, StaleGovernanceWriteError) as e:
                _echo(f"Could not disengage: {e}", err=True)
                return EXIT_REFUSED
            _echo("Parking brake DISENGAGED.")
            _echo(f"Database: {path}  (from {source})")
            return 0
        status, payload = call(
            "POST",
            "/api/governance/brake/disengage",
            base_url=base_url,
            body={"reason": reason},
        )
        return _report(status, payload, success="Parking brake DISENGAGED.")

    _run(body)


# ---------------------------------------------------------------------------
# consent -- a device's ask to observe
# ---------------------------------------------------------------------------
#
# The answer needs the ask's nonce, which lives only in the kernel database:
# reading it is what proves the answer comes from the operator's own machine
# and account. This mirrors `bartholomew consent` deliberately rather than
# reimplementing it -- the answering logic is that module's, and this group
# exists so an operator can stay in one console.


@consent_app.command("pending")
def consent_pending(db: str = typer.Option(None, "--db", help=_DB_HELP)) -> None:
    """Which devices are waiting for your permission to observe."""
    from bartholomew.cli_consent import consent_pending as _pending  # noqa: PLC0415

    _pending(db=db)


@consent_app.command("approve")
def consent_approve(
    request_id: str = typer.Argument(..., help="From `bartholomew operator consent pending`"),
    db: str = typer.Option(None, "--db", help=_DB_HELP),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
    note: str = typer.Option(None, "--note"),
) -> None:
    """Allow this one start attempt. Nothing is remembered for the next one."""
    from bartholomew.cli_consent import consent_approve as _approve  # noqa: PLC0415

    _approve(request_id=request_id, db=db, base_url=base_url, note=note)


@consent_app.command("deny")
def consent_deny(
    request_id: str = typer.Argument(..., help="From `bartholomew operator consent pending`"),
    db: str = typer.Option(None, "--db", help=_DB_HELP),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
    note: str = typer.Option(None, "--note"),
) -> None:
    """Refuse this start attempt."""
    from bartholomew.cli_consent import consent_deny as _deny  # noqa: PLC0415

    _deny(request_id=request_id, db=db, base_url=base_url, note=note)


# ---------------------------------------------------------------------------
# lessons -- manual acceptance, kept manual
# ---------------------------------------------------------------------------


@lessons_app.command("pending")
def lessons_pending(
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
    limit: int = typer.Option(50, "--limit", min=1, max=100),
) -> None:
    """Corrections Bartholomew has turned into candidate lessons, awaiting you.

    A candidate is not knowledge. Until you accept one it changes nothing
    Bartholomew recalls, and nothing here can accept one on your behalf.
    """

    def body() -> int:
        status, payload = call(
            "GET",
            f"/api/learning/candidates?limit={limit}",
            base_url=base_url,
        )
        if status >= 400:
            return _report(status, payload)
        rows = [
            r for r in (payload.get("candidates") or []) if (r.get("review_state")) == "proposed"
        ]
        if not rows:
            _echo("No candidate lesson is waiting for you.")
            return 0
        for row in rows:
            _echo(f"\n{row.get('competency_id')}/{row.get('slug')}")
            _echo(f"  {row.get('rule') or ''}")
            _echo(f"  confidence: {row.get('confidence')}   state: {row.get('review_state')}")
        _echo(
            "\nRead one:   bartholomew operator lessons show <competency_id> <slug>"
            "\nAccepting is two explicit steps, and neither is a formality:"
            "\n  bartholomew operator lessons approve <competency_id> <slug>"
            "\n  bartholomew operator lessons accept  <competency_id> <slug>",
        )
        return 0

    _run(body)


@lessons_app.command("show")
def lessons_show(
    competency_id: str = typer.Argument(...),
    slug: str = typer.Argument(...),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """The candidate lesson in full: what it claims, and what it stands on."""
    _run(
        lambda: _report(
            *call(
                "GET",
                f"/api/learning/candidates/{competency_id}/{slug}",
                base_url=base_url,
            ),
        ),
    )


@lessons_app.command("approve")
def lessons_approve(
    competency_id: str = typer.Argument(...),
    slug: str = typer.Argument(...),
    note: str = typer.Option(None, "--note"),
    approver: str = typer.Option("operator", "--as", help=_REVIEWER_HELP),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """Grant the candidate-bound authorization that acceptance requires.

    Step one of two. This grants permission to accept *this* candidate as it
    stands; it does not accept it, and it authorises no other candidate.
    """
    _run(
        lambda: _report(
            *call(
                "POST",
                f"/api/learning/candidates/{competency_id}/{slug}/approve",
                base_url=base_url,
                body={"approver": approver, "note": note},
            ),
            success="Authorized. It is still not accepted, and still changes nothing.",
        ),
    )


@lessons_app.command("accept")
def lessons_accept(
    competency_id: str = typer.Argument(...),
    slug: str = typer.Argument(...),
    note: str = typer.Option(None, "--note"),
    reviewer: str = typer.Option("operator", "--as", help=_REVIEWER_HELP),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """Accept the lesson. From now on it can influence a later task.

    Step two of two, and the only step that changes what Bartholomew knows.
    It requires the authorization `lessons approve` granted for this exact
    candidate; without it the server refuses, and this console cannot supply
    one.
    """
    _run(
        lambda: _report(
            *call(
                "POST",
                f"/api/learning/candidates/{competency_id}/{slug}/accept",
                base_url=base_url,
                body={"reviewer": reviewer, "note": note},
            ),
            success="Accepted. A later relevant task can now use it.",
        ),
    )


@lessons_app.command("reject")
def lessons_reject(
    competency_id: str = typer.Argument(...),
    slug: str = typer.Argument(...),
    note: str = typer.Option(None, "--note"),
    reviewer: str = typer.Option("operator", "--as", help=_REVIEWER_HELP),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """Reject the lesson permanently. Rejection is terminal."""
    _run(
        lambda: _report(
            *call(
                "POST",
                f"/api/learning/candidates/{competency_id}/{slug}/reject",
                base_url=base_url,
                body={"reviewer": reviewer, "note": note},
            ),
            success="Rejected. It can never be accepted afterwards.",
        ),
    )


# ---------------------------------------------------------------------------
# task -- ordinary words in, a governed plan out
# ---------------------------------------------------------------------------
#
# The executive (W03-B) is reached over the operator routes, which W03-F
# registers. Until then these commands report the absence honestly rather than
# reaching into the kernel from a CLI process that does not own it.

_NO_EXECUTIVE = (
    "This build does not serve the operator task routes, so Bartholomew cannot be "
    "given a task from here yet. Everything else in this console works: you can "
    "still see, approve, deny and halt actions that reach him another way."
)


@task_app.command("run")
def task_run(
    instruction: str = typer.Argument(..., help='In ordinary words, e.g. "Open Spotify"'),
    device_id: str = typer.Option(..., "--device-id", help="Which enrolled machine"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """Give Bartholomew a task.

    He interprets it, chooses a capability the device actually declares, and
    proposes each Windows step through the one action envelope. Nothing runs:
    every step stops at `pending_approval` for you to inspect and approve.
    If he is not sure what you meant he asks, rather than guessing.

    The instruction is an ordinary command-line argument, so it lands in the
    shell history like any other. Do not put a password or other secret in it;
    the envelope refuses secret-looking text anyway, and the place to type
    something private is your own keyboard.
    """

    def body() -> int:
        status, payload = call(
            "POST",
            "/api/operator/tasks",
            base_url=base_url,
            body={"instruction": instruction, "device_id": device_id},
        )
        if _routes_unserved(status, payload):
            _echo(_NO_EXECUTIVE, err=True)
            return EXIT_REFUSED
        if status >= 400:
            return _report(status, payload)
        _echo(_render_task(payload))
        return 0

    _run(body)


def _routes_unserved(status: int, payload: Any) -> bool:
    """FastAPI's own 404 for an unregistered route, as opposed to a task the
    route looked for and did not find (which carries the seam's reason)."""
    return status == 404 and isinstance(payload, dict) and payload.get("detail") == "Not Found"


@task_app.command("show")
def task_show(
    task_id: str = typer.Argument(..., help="From `bartholomew operator task run`"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """Where the task stands, as recorded. Looking changes nothing.

    This is a read of the stored plan. It does not observe the machine, verify
    a step, or propose the next one -- that is `task advance`, which is a
    request-class act and is named as one.
    """

    def body() -> int:
        status, payload = call("GET", f"/api/operator/tasks/{task_id}", base_url=base_url)
        if _routes_unserved(status, payload):
            _echo(_NO_EXECUTIVE, err=True)
            return EXIT_REFUSED
        if status >= 400:
            return _report(status, payload)
        _echo(_render_task(payload))
        return 0

    _run(body)


@task_app.command("advance")
def task_advance(
    task_id: str = typer.Argument(..., help="From `bartholomew operator task run`"),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help=_BASE_URL_HELP),
) -> None:
    """Have Bartholomew look at what became of the current step and continue.

    He reads the step's action from the envelope's own record, verifies what
    he can against the machine, and either proposes the next step -- which
    stops at `pending_approval` like every other -- asks you a question, or
    stops. Nothing runs from this command; it can only propose.
    """

    def body() -> int:
        status, payload = call(
            "POST",
            f"/api/operator/tasks/{task_id}/advance",
            base_url=base_url,
            body={},
        )
        if _routes_unserved(status, payload):
            _echo(_NO_EXECUTIVE, err=True)
            return EXIT_REFUSED
        if status >= 400:
            return _report(status, payload)
        _echo(_render_task(payload))
        return 0

    _run(body)


def _render_task(payload: Any) -> str:
    """A task as a person reads it. Never more certain than the payload is."""
    if not isinstance(payload, dict):
        return json.dumps(payload, default=str)
    lines: list[str] = []
    task = payload.get("task") or payload
    if task.get("task_id"):
        lines.append(f"Task {task['task_id']}")
    if payload.get("question") or task.get("question"):
        # An ambiguous instruction becomes a question, never the most likely
        # reading. This is the branch that prints it.
        lines.append(
            f"Bartholomew needs to know: {payload.get('question') or task.get('question')}",
        )
        return "\n".join(lines)
    if task.get("declined") or payload.get("declined"):
        lines.append(f"Declined: {payload.get('reason') or task.get('reason')}")
        return "\n".join(lines)
    if task.get("instruction"):
        lines.append(f"Understood as: {task['instruction']}")
    if task.get("status"):
        lines.append(f"Status: {task['status']}")
    for step in payload.get("steps") or task.get("steps") or []:
        lines.append(
            f"  step {step.get('ordinal', '?')}: {step.get('capability')} "
            f"-> {step.get('status')}"
            + (f"   action {step['action_id']}" if step.get("action_id") else ""),
        )
    for stop in payload.get("named_stops") or []:
        lines.append(f"  STOP: {stop}")
    explanation = payload.get("explanation") or task.get("explanation")
    if explanation:
        lines.append(f"\n{explanation}")
    # `awaiting_authorization` is W03-B's own word for a step whose action
    # exists, was admitted, and is waiting for a person at the host boundary
    # (`executive.plan.StepStatus`). It is the only step state a person can act
    # on from here, so it is the only one the hint points at.
    pending = [
        s.get("action_id")
        for s in (payload.get("steps") or [])
        if s.get("action_id") and s.get("status") == "awaiting_authorization"
    ]
    if pending:
        lines.append(
            "\nNothing has run. Inspect and decide:\n"
            + "\n".join(f"  bartholomew operator actions show {a}" for a in pending),
        )
    return "\n".join(lines)


__all__ = ["operator_app", "explain_outcome", "call", "ServerUnreachableError"]
