"""Golden Path 3 -- "Move that file into my Bartholomew folder."

**This path stops, and the stop is the deliverable.**

`W03_E_CONTRACT.md` says path 3 is supportable "within allowlisted filesystem
roots; **defer** any capability not shipped by W03-C". W03-C shipped no capability
that moves, renames, creates or deletes a file, and its absence is not an
oversight: `CapabilityKind`'s own docstring lists "file creation, deletion,
movement, renaming or editing" among the things that are absent **structurally
rather than by policy**. Adding one would be a new product capability, which
W03-E's contract names as an escalation and not a quiet addition.

So this file does two things and refuses a third:

1. It asserts the boundary by name, against the capability vocabulary itself, so
   the stop is a test result rather than a paragraph in a handoff.
2. It runs the path as far as the shipped capabilities genuinely allow -- the
   target is resolved from evidence and `open_path` reveals the file in its
   folder, inside an allowlisted root -- end to end through the real envelope.
3. It does not add a capability, widen a filesystem root, or dress a reveal up
   as a move.
"""

from __future__ import annotations

import pytest

from tests.golden_path.conftest import (
    BARTHOLOMEW_FOLDER,
    DOCUMENTS,
    arm_channel,
    read_action,
    request_action,
)

pytestmark = [pytest.mark.integration]

#: The named stop, in the words the handoff and the deferral register use.
NAMED_STOP = (
    "NAMED STOP -- Golden Path 3 cannot complete: no capability that moves a file "
    "is shipped by W03-C, and `CapabilityKind` excludes file movement structurally "
    "rather than by policy. Adding one is an escalation under W03_E_CONTRACT.md's "
    "escalation boundary, not a W03-E change. What is shipped -- revealing the "
    "file in its folder within an allowlisted root -- runs end to end below."
)


def test_no_shipped_capability_can_move_a_file():
    """The stop, asserted against the vocabulary rather than described.

    If a later wave adds a move capability this test fails, which is the right
    failure: it means the handoff's named stop is stale and Golden Path 3 can be
    completed rather than bounded.
    """
    from bartholomew.actuation.capabilities import ALL_CAPABILITIES

    names = {kind.value for kind in ALL_CAPABILITIES}
    forbidden_verbs = ("move", "rename", "copy", "delete", "write_file", "create_file")
    offending = sorted(n for n in names if any(verb in n for verb in forbidden_verbs))
    assert not offending, f"{NAMED_STOP}\nBut the vocabulary now contains: {offending}"

    # Non-vacuity: the same scan finds a name of that shape when there is one.
    assert any(verb in "windows.move_file" for verb in forbidden_verbs)


def test_asking_for_a_move_is_refused_and_not_approximated(service):
    """A request for the missing capability is refused, not mapped onto a near one.

    The failure mode this guards against is the tempting one: `open_path` is
    right there, it is allowlisted, and it would make the scenario look like it
    worked. The envelope refuses the unknown capability outright, and nothing in
    W03-E converts that refusal into a different action.
    """
    status, payload = request_action(
        service.port,
        capability="windows.move_file",
        parameters={"source": f"{DOCUMENTS}\\notes.txt", "destination": BARTHOLOMEW_FOLDER},
    )

    assert status >= 400, payload
    assert payload.get("governance_allowed") is not True

    # And nothing was created in its place.
    from tests.integration.test_always_on_service import _get

    _, listing = _get(service.port, "/api/actions?limit=50")
    assert not [row for row in listing["actions"] if row.get("capability") == "windows.move_file"]


def test_the_path_runs_as_far_as_the_shipped_capabilities_allow(service, console, device):
    """What Bartholomew *can* do here, end to end, honestly labelled.

    `open_path` reveals a location inside an allowlisted filesystem root. It is
    not a move and this test never says it is: the account a person gets says
    the folder was opened, and the named stop above says the file was not moved.
    """
    status, payload = request_action(
        service.port,
        capability="windows.open_path",
        parameters={"path": BARTHOLOMEW_FOLDER},
    )
    assert status == 201, payload
    action_id = payload["action"]["action_id"]

    shown = console.run("actions", "show", action_id).ok()
    # The canonical path, normalised by the validator, is what the approver reads.
    assert BARTHOLOMEW_FOLDER.lower() in shown.stdout.lower()
    # And the capability's own description of its power says, to the person
    # about to approve it, that this is not a move. The scenario's honesty here
    # is the envelope's, not the console's paraphrase.
    assert "Never creates, deletes, moves, renames" in shown.stdout

    console.run("actions", "approve", action_id).ok()
    arm_channel(service.port)
    status, leased = device.lease()
    assert [a for a in (leased.get("actions") or []) if a.get("action_id") == action_id], leased

    # `open_path` has no defined read-back check -- nothing in the accessibility
    # tree distinguishes "explorer opened this folder" from "an explorer window
    # is open" -- so the honest device report is `unknown`, and the console says
    # unknown. This is the contract's "unknown is never presented as success",
    # arriving on the path where it is easiest to fudge.
    device.report(
        action_id,
        status="unknown",
        detail="explorer was asked to open the folder; nothing confirms what it shows",
        error_category="effect_unverifiable",
    )

    explained = console.run("actions", "explain", action_id).ok()
    assert "NOT success" in explained.stdout
    assert read_action(service.port, action_id)["action"]["state"] == "unknown"


def test_a_path_outside_the_allowlisted_roots_is_refused(service):
    """The root allowlist is a real bound, so the test above is not vacuous."""
    status, payload = request_action(
        service.port,
        capability="windows.open_path",
        parameters={"path": "C:\\Windows\\System32"},
    )
    assert status >= 400 or payload.get("governance_allowed") is False, payload
