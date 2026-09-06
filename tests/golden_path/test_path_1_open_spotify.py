"""Golden Path 1 -- "Open Spotify."

The contract's fully-supportable path: interpret -> capability select
(`launch_app`) -> envelope proposal -> approve -> lease -> execute -> verify ->
explain. Every leg below runs against the real thing. What is not real is
Windows itself: the device half speaks the real action channel and reports a
result, but no process is launched here, so the *verification* assertions are
about the vocabulary and the verdict, not about a real pid. The live desktop run
is a W03-F real-world-test acceptance target and this file does not claim it.

The interpret leg is W03-B's. Where that package is in the tree the path starts
at an instruction in ordinary words; where it is not, the path starts at the
envelope and says so by name.
"""

from __future__ import annotations

import pytest

from tests.golden_path import components
from tests.golden_path.conftest import (
    DEVICE,
    OPERATOR_ROUTES_UNSERVED,
    arm_channel,
    operator_routes_served,
    read_action,
    request_action,
    require_operator_routes,
)
from tests.integration.test_always_on_service import _get

pytestmark = [pytest.mark.integration]

INSTRUCTION = "Open Spotify"


def _propose_open_spotify(service, console) -> str:
    """The proposal, through whichever legs this server carries.

    Returns the action id. Three states are possible and each is named rather
    than hidden: the executive is absent (W03-B not in the tree); the executive
    is present but `app.py` does not serve the operator routes (W03-F has not
    registered them); or both are there and the path starts from ordinary words.
    A run in which the executive was not reached proved a strictly shorter path,
    and a reader of the log must be able to tell which happened.
    """
    if components.probe("executive") and operator_routes_served(service.port):
        result = console.run("task", "run", INSTRUCTION, "--device-id", DEVICE).ok()
        assert "windows.launch_app" in result.stdout, result.output
        # Both the executive's own account and the console's hint say so.
        assert "nothing has run" in result.stdout.lower(), result.output
        assert "Inspect and decide" in result.stdout, result.output
        action_ids = [token for token in result.stdout.split() if token.startswith("act-")]
        assert action_ids, f"the executive proposed nothing:\n{result.output}"
        return action_ids[0]

    # Entered at the envelope: the same door W03-B proposes through.
    status, payload = request_action(
        service.port,
        capability="windows.launch_app",
        parameters={"app_id": "spotify"},
    )
    assert status == 201, payload
    return payload["action"]["action_id"]


def test_open_spotify_travels_the_whole_envelope_and_ends_with_a_truthful_account(
    service,
    console,
    device,
):
    """The path, end to end, to the boundary the contract names.

    Contract acceptance criterion 2 for path 1 is "end to end against real
    stores **to the lease boundary**". That is exactly where this stops being a
    claim about software and starts being a claim about a desktop, so it is
    where this test stops claiming.
    """
    action_id = _propose_open_spotify(service, console)

    # Proposed, and nothing has run: the envelope's own posture, read back
    # through the surface a person uses.
    shown = console.run("actions", "show", action_id).ok()
    assert "state:       pending_approval" in shown.stdout
    assert 'app_id = "spotify"' in shown.stdout
    assert "risk:        moderate" in shown.stdout

    # A device that leases before the approval gets nothing. The allowlist is
    # not what decides this; the approval is.
    status, leased = device.lease()
    assert status < 400, leased
    assert not [a for a in (leased.get("actions") or []) if a.get("action_id") == action_id]

    console.run("actions", "approve", action_id, "--note", "yes, open it").ok()
    arm_channel(service.port)

    status, leased = device.lease()
    assert status < 400, leased
    mine = [a for a in (leased.get("actions") or []) if a.get("action_id") == action_id]
    assert mine, f"an approved action on an armed channel did not lease: {leased}"
    # The lease is what hands over the canonical parameters, and nothing else does.
    assert mine[0]["parameters"]["app_id"] == "spotify"

    # The device reports what it observed. On a real desktop `launch_app`
    # verifies by looking for the process image it started; here the report is
    # the one a successful launch produces, and the assertions below are about
    # how that report is recorded and explained -- not about a real process.
    status, recorded = device.report(
        action_id,
        status="succeeded",
        detail="spotify.exe started and a window appeared",
        # `os_state_read_back` is a member of W03-C's closed `VERIFY_METHODS`.
        evidence={"verified": True, "verify_method": "os_state_read_back"},
    )
    assert status < 400, recorded

    explained = console.run("actions", "explain", action_id).ok()
    if components.probe("verify_evidence"):
        # W03-C's evidence keys survive the allowlist: the read-back agreed, and
        # the account says so.
        assert "confirmed by reading the machine back" in explained.stdout
        assert "NOT" not in explained.stdout
    else:
        # Without W03-C the envelope drops `verified`/`verify_method` (a closed
        # allowlist, correctly), so nothing recorded a verification -- and the
        # account must say exactly that rather than round the device's word up.
        assert "no verification was recorded" in explained.stdout
        assert "NOT confirmed success" in explained.stdout

    final = read_action(service.port, action_id)["action"]
    assert final["state"] == "succeeded"


def test_a_device_that_cannot_confirm_the_effect_is_never_explained_as_success(
    service,
    console,
    device,
):
    """The same path, with the honest verdict -- criterion 4.

    This is the assertion the wave exists to protect. A device that issued the
    action and could not read the machine back reports `unknown`, and every
    surface a person meets says so. There is no wording anywhere in the console
    that turns this into "done".
    """
    action_id = _propose_open_spotify(service, console)
    console.run("actions", "approve", action_id).ok()
    arm_channel(service.port)
    device.lease()

    device.report(
        action_id,
        status="unknown",
        detail="the launch was issued and nothing could confirm a window appeared",
        error_category="effect_unverifiable",
        evidence={"verified": None, "verify_method": "unavailable"},
    )

    explained = console.run("actions", "explain", action_id).ok()
    assert "NOT success" in explained.stdout
    assert "nobody can say whether it worked" in explained.stdout

    listed = console.run("actions", "list").ok()
    assert "NOT success" in listed.stdout

    final = read_action(service.port, action_id)["action"]
    assert final["state"] == "unknown"
    assert final["state"] != "succeeded"


def test_a_device_that_reports_success_with_nothing_read_back_is_still_not_success(
    service,
    console,
    device,
):
    """Issued is not succeeded, even when the device is optimistic.

    Non-vacuity companion to the test above: the *status* here is `succeeded`,
    so a console that keyed only on the status word would print a confirmation.
    The evidence says nothing was read back, and the account follows the
    evidence.
    """
    # W03-C owns the `verified`/`verify_method` evidence keys. Without them the
    # envelope's evidence allowlist drops both -- correctly, it is a closed
    # allowlist -- so on a tree without W03-C there is nothing for the console to
    # read and this assertion has nothing to stand on. The property itself is
    # proven exhaustively and without a server in `test_outcome_truthfulness.py`.
    components.require("verify_evidence")

    action_id = _propose_open_spotify(service, console)
    console.run("actions", "approve", action_id).ok()
    arm_channel(service.port)
    device.lease()
    device.report(
        action_id,
        status="succeeded",
        detail="the launch command returned without error",
        evidence={"verified": False, "verify_method": "unavailable"},
    )

    explained = console.run("actions", "explain", action_id).ok()
    assert "NOT confirmed success" in explained.stdout


def test_the_parking_brake_engaged_during_the_path_stops_the_governed_loop(
    service,
    console,
    device,
):
    """Contract acceptance criterion 6, on this path.

    The brake is engaged from the operator console -- the surface a worried
    person actually reaches for -- after the action is approved and before it is
    leased. Nothing after that point proceeds, and the halt is visible on the
    same console.
    """
    action_id = _propose_open_spotify(service, console)
    console.run("actions", "approve", action_id).ok()
    arm_channel(service.port)

    console.run("brake", "on", "--scope", "actuation").ok()
    try:
        status, leased = device.lease()
        mine = [a for a in (leased.get("actions") or []) if a.get("action_id") == action_id]
        assert not mine, f"an approved action leased through an engaged brake: {leased}"

        # The channel reports the truth rather than merely the fact: the window
        # is open and nothing can be carried out through it.
        _, channel = _get(service.port, "/api/actions/channel")
        assert channel["channel"]["brake_engaged"] is True
        assert channel["channel"]["armed"] is False

        # And a *new* proposal is refused too, so the halt is on the loop and
        # not merely on this one row.
        status, refused = request_action(
            service.port,
            capability="windows.launch_app",
            parameters={"app_id": "spotify"},
        )
        assert status >= 400 or refused.get("governance_allowed") is False
    finally:
        console.run("brake", "off").ok()

    # Non-vacuity: released, the identical lease succeeds. So the refusal above
    # was the brake, not something else about the action.
    status, leased = device.lease()
    assert [a for a in (leased.get("actions") or []) if a.get("action_id") == action_id], leased


def test_one_action_s_approval_leases_no_other_action(service, console, device):
    """Approving one action does not lease an unapproved sibling.

    The six-fact binding itself is W03-C's and is pinned in
    `test_windows_action_governance.py`; this is the scenario-level consequence,
    seen from the surface that drives it.
    """
    first = _propose_open_spotify(service, console)
    status, payload = request_action(
        service.port,
        capability="windows.launch_app",
        parameters={"app_id": "notepad"},
    )
    assert status == 201, payload
    second = payload["action"]["action_id"]

    console.run("actions", "approve", first).ok()
    arm_channel(service.port)

    status, leased = device.lease(limit=10)
    ids = {a.get("action_id") for a in (leased.get("actions") or [])}
    assert first in ids
    assert second not in ids, "one action's approval leased another action"


def test_the_interpret_leg_runs_from_ordinary_words(service, console):
    """The full path 1 opening: words in, a governed proposal out.

    Needs W03-B in the tree *and* `routes/operator.py` registered (W03-F's
    step). Where either is missing this is a named stop, by name, in the run log.
    """
    components.require("executive")
    require_operator_routes(service.port)

    result = console.run("task", "run", INSTRUCTION, "--device-id", DEVICE).ok()
    # The executive's own account, not the console's hint.
    assert "proposed and waiting for your approval" in result.stdout
    assert "I cannot approve these myself" in result.stdout
    assert "windows.launch_app" in result.stdout

    task_id = next(t for t in result.stdout.split() if t.startswith("exec-"))
    shown = console.run("task", "show", task_id).ok()
    assert "awaiting_authorization" in shown.stdout
    # A read changes nothing: the same action id, still waiting.
    action_id = next(t for t in result.stdout.split() if t.startswith("act-"))
    assert action_id in shown.stdout
    assert "state:       pending_approval" in console.run("actions", "show", action_id).ok().stdout


def test_the_interpret_leg_is_named_when_this_tree_does_not_carry_it(service, console):
    """The named stop itself, asserted rather than left as a skip.

    W03-E does not merge W03-B and does not register routers in `app.py`. So
    there are two ways the interpret leg can be absent, and the console must
    name the one it hit rather than fail obscurely or pretend the executive
    refused. Where both the package and the route are present the leg runs.
    """
    if components.probe("executive") and operator_routes_served(service.port):
        result = console.run("task", "run", INSTRUCTION, "--device-id", DEVICE).ok()
        assert "windows.launch_app" in result.stdout
        assert "nothing has run" in result.stdout.lower()
        return

    result = console.run("task", "run", INSTRUCTION, "--device-id", DEVICE)
    assert result.returncode != 0
    assert "does not serve the operator task routes" in result.stderr
    # And it says what still works, so the absence is a named boundary rather
    # than a dead end.
    assert "Everything else in this console works" in result.stderr
    if components.probe("executive"):
        # The package is here and the door is not: W03-F's step, by name.
        print(f"\n{OPERATOR_ROUTES_UNSERVED}")
