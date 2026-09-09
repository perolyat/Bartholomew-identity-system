"""W03-E acceptance criterion 1: a non-developer can drive the loop.

The first live test's finding 8 is the specification here: first use required
hand-written JSON, three terminals, environment variables and raw HTTP calls to
request and approve a single action. So every assertion below is made through
`bartholomew operator ...` run as a subprocess against a real running server --
never by importing the console's functions, never by composing a JSON body, and
never by calling an internal API. If a step here needed a hand-written body, the
criterion would not be met and this file would not be able to hide it.

The one thing the console does *not* do is propose the action. Proposing is the
executive's job (W03-B) and it is not in this tree, so the setup below enters at
the envelope -- the same door W03-B proposes through -- and every assertion from
there on is about the surface, which is what this file is for. Golden Path 1
carries the interpret leg where it exists.
"""

from __future__ import annotations

import pytest

from tests.golden_path.conftest import (
    DEVICE,
    arm_channel,
    disengage_brake,
    engage_brake,
    request_action,
)

pytestmark = [pytest.mark.integration]


def _pending_action(port) -> str:
    status, payload = request_action(
        port,
        capability="windows.type_text",
        parameters={"text": "the quarterly figures are wrong"},
    )
    assert status == 201, payload
    return payload["action"]["action_id"]


def test_the_console_shows_everything_waiting_in_one_screen(service, console):
    """`operator status`: the screen the live test needed and did not have."""
    _pending_action(service.port)

    result = console.run("status").ok()

    assert "Parking Brake" in result.stdout
    assert "Action channel" in result.stdout
    assert "Actions waiting for your decision: 1" in result.stdout
    assert "Devices asking to observe" in result.stdout
    # It tells a person what to do next, not merely what is true.
    assert "operator actions show" in result.stdout


def test_a_person_can_read_an_action_s_real_parameters_before_deciding(service, console):
    """The approval surface discloses; the list view redacts.

    `windows.type_text` is the case that matters: approving text you have not
    read is an approval in name only, and the list view deliberately shows only
    a digest and a length.
    """
    action_id = _pending_action(service.port)

    listing = console.run("actions", "list", "--pending-only").ok()
    assert action_id in listing.stdout
    assert "the quarterly figures are wrong" not in listing.stdout

    shown = console.run("actions", "show", action_id).ok()
    assert "the quarterly figures are wrong" in shown.stdout
    # And the two questions an approver has to answer sit next to each other:
    # what am I allowing (the capability's own power) and what will it do
    # (this request's parameters).
    assert "windows.type_text" in shown.stdout
    assert "risk:        sensitive" in shown.stdout
    assert "approval:    always" in shown.stdout


def test_a_person_can_approve_and_the_approval_is_the_server_s(service, console):
    action_id = _pending_action(service.port)

    approved = console.run("actions", "approve", action_id, "--note", "yes, that text").ok()
    assert "Approved" in approved.output

    shown = console.run("actions", "show", action_id).ok()
    assert "state:       approved" in shown.stdout


def test_a_person_can_deny_and_it_can_never_run(service, console):
    action_id = _pending_action(service.port)

    denied = console.run("actions", "deny", action_id, "--reason", "not that").ok()
    assert "Withdrawn" in denied.output

    shown = console.run("actions", "show", action_id).ok()
    assert "state:       cancelled" in shown.stdout
    # Approving it afterwards is refused by the server, not by the console.
    late = console.run("actions", "approve", action_id)
    assert late.returncode != 0
    assert "Refused" in late.stderr


def test_a_person_can_disarm_and_arm_the_channel(service, console):
    """Disarm is proven for real. Arm is proven as far as a keyring-less runner allows.

    Arming is the enrolled device's act: the route requires the companion
    credential, and this runner's keyring holds none. So the arm half proves
    that the console refuses with the exact instruction a person needs, rather
    than opaquely -- and the suite's armed channel comes from the double-gated
    test resolver's startup pre-arm (`app.py`), which `channel status` reads
    back. A real arm from the console is a W03-F live-desktop item and the
    handoff says so.
    """
    status = console.run("channel", "status").ok()
    assert '"armed": true' in status.stdout

    closed = console.run("channel", "disarm").ok()
    assert "Channel closed" in closed.output
    assert '"armed": false' in console.run("channel", "status").ok().stdout

    armed = console.run("channel", "arm", "--device-id", DEVICE, "--reason", "one task")
    if armed.returncode == 0:
        assert "No action is approved by this" in armed.output
        assert '"armed": true' in console.run("channel", "status").ok().stdout
    else:
        assert "companion credential store" in armed.output
        # And the refusal changed nothing: still closed, and the console says so.
        assert '"armed": false' in console.run("channel", "status").ok().stdout


def test_a_person_can_inspect_and_operate_the_brake_against_the_running_server(
    service,
    console,
):
    """The live test's other operator defect: a brake that halted a scratch file.

    These commands talk to the running server, which reads the brake through the
    one `GovernanceStore` every other gate reads -- so what the operator engages
    and what the server enforces are the same state by construction, not by
    coincidence.
    """
    assert '"engaged": false' in console.run("brake", "status").ok().stdout

    engaged = console.run("brake", "on", "--scope", "actuation").ok()
    assert "ENGAGED" in engaged.output

    # The running server agrees, and it says so on the surface a worried person
    # actually looks at.
    status_screen = console.run("status").ok()
    assert "Parking Brake: ENGAGED" in status_screen.stdout
    assert "actuation" in status_screen.stdout
    # Inspection is exactly what a halt must not hide.
    assert "Actions waiting for your decision" in status_screen.stdout

    released = console.run("brake", "off").ok()
    assert "DISENGAGED" in released.output
    assert '"engaged": false' in console.run("brake", "status").ok().stdout


def test_the_brake_the_console_engages_is_the_one_the_envelope_reads(service, console):
    """Non-vacuity for the test above: the halt has to actually stop something."""
    console.run("brake", "on", "--scope", "actuation").ok()
    try:
        status, payload = request_action(
            service.port,
            capability="windows.launch_app",
            parameters={"app_id": "spotify"},
        )
        assert status >= 400 or payload.get("governance_allowed") is False, payload
    finally:
        console.run("brake", "off").ok()

    # And with the brake released the identical request is admitted, so the
    # refusal above was the brake and not something else about the request.
    status, payload = request_action(
        service.port,
        capability="windows.launch_app",
        parameters={"app_id": "spotify"},
    )
    assert status == 201, payload


def test_the_console_reports_an_unreachable_server_as_unreachable(service, console):
    """ "He said no" and "I could not ask him" are different facts."""
    from tests.golden_path.conftest import Console

    dead = Console(1)  # nothing listens on port 1
    result = dead.run("status")

    assert result.returncode != 0
    assert "Could not reach Bartholomew" in result.stderr
    assert "Refused" not in result.stderr


def test_a_person_can_see_which_devices_are_waiting_to_observe(service, console):
    """The consent leg is reachable from the same console, and answers honestly."""
    result = console.run("consent", "pending", "--db", str(service.db_path), base_url=False)

    # No device has asked, which is the truthful answer and not an error.
    assert "No device is waiting for your consent" in result.output


def test_nothing_in_the_console_can_arm_approve_or_accept_on_its_own(service):
    """A structural guard on the console's own restraint.

    The three powers that must stay with a person: minting an action approval,
    accepting a lesson, and engaging trusted autonomy. The console posts to the
    routes that hold them and constructs none of them, so none of these names
    may appear in its source.
    """
    import ast
    import pathlib

    source = pathlib.Path("bartholomew/cli_operator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden = {
        "grant_action_approval",
        "grant_learning_acceptance_approval",
        "run_action_dispatch_through_runtime_contract",
        "run_candidate_lesson_through_runtime_contract",
        "trusted_autonomy",
    }
    names = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Attribute, ast.Name))
    }
    assert not (names & forbidden), f"the console reached for an authority: {names & forbidden}"

    # Non-vacuity: the same scan finds the name when it is there.
    planted = ast.parse("grant_action_approval(x)")
    planted_names = {n.id for n in ast.walk(planted) if isinstance(n, ast.Name)}
    assert planted_names & forbidden


def test_the_console_never_calls_the_actuation_or_kernel_seams_directly(service):
    """One architecture: the console is a client, not a second control plane.

    It may read the `GovernanceStore` for the documented `--offline` brake
    fallback -- which is a read of the same one authority, through the one
    `db_paths` resolver -- and it may reach the existing consent CLI. It may not
    import the actuation seam, the executive, or the memory store.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("bartholomew/cli_operator.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden_prefixes = (
        "bartholomew.actuation",
        "bartholomew.executive",
        "bartholomew.windows_actuation",
        "bartholomew.kernel.memory_store",
        "bartholomew.kernel.runtime_contract",
    )
    offending = sorted(m for m in imported if m.startswith(forbidden_prefixes))
    assert not offending, f"the console imported a seam it must reach over HTTP: {offending}"


def test_the_brake_offline_fallback_names_the_file_it_touched(service, tmp_path):
    """The live defect was an operator who could not tell which file changed."""
    from tests.golden_path.conftest import Console

    db = str(service.db_path)
    offline = Console(service.port)
    result = offline.run("brake", "status", "--offline", "--db", db).ok()

    assert db in result.stdout
    assert "Read from the database, not from a running server" in result.stdout


def test_the_engaged_brake_is_visible_from_both_the_server_and_the_file(service, console):
    """The two views cannot disagree, because there is one file and one store."""
    engage_brake(service.port, "actuation")
    try:
        from_server = console.run("brake", "status").ok().stdout
        from_file = (
            console.run(
                "brake",
                "status",
                "--offline",
                "--db",
                str(service.db_path),
            )
            .ok()
            .stdout
        )
        assert '"engaged": true' in from_server
        assert "ENGAGED" in from_file
    finally:
        disengage_brake(service.port)


def test_arming_does_not_approve_anything(service, console):
    """The console's own claim, proven rather than asserted in a docstring."""
    arm_channel(service.port)
    action_id = _pending_action(service.port)

    shown = console.run("actions", "show", action_id).ok()
    assert "state:       pending_approval" in shown.stdout
    assert "Nothing has run" in shown.stdout or "waiting for you" in shown.stdout
