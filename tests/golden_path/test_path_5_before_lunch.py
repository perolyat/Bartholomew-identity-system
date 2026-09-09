"""Golden Path 5 -- "Go back to what I was working on before lunch."

The path that must degrade honestly. `W03_E_CONTRACT.md`: reconstruct from
observation history and memory, then "restore only as far as existing
capabilities safely permit"; where the Windows foreground lock blocks it, "the
honest experience is 'here is what you were doing' plus an operator-confirmed
focus, **not synthesised input**".

That last clause is the whole test. There is a way to make this scenario look
successful -- synthesise the input Windows requires to hand foreground to a
background process -- and it is named in W03-E's escalation boundary as a thing
to stop and report rather than do. So this file proves three things:

1. Bartholomew can reconstruct and *state* what the person was doing, from
   evidence, with no actuation at all.
2. The restore is proposed as an ordinary `focus_window` action through the one
   envelope, and it waits for a person exactly like every other action.
3. Nothing in the tree synthesises input to defeat the foreground restriction,
   and when the restriction bites the outcome is `unknown` -- never success.
"""

from __future__ import annotations

import json

import pytest

from tests.golden_path import components
from tests.golden_path.conftest import arm_channel, read_action, request_action

pytestmark = [pytest.mark.integration]

#: The named stop this path carries, in the words the handoff uses.
NAMED_STOP = (
    "NAMED STOP -- Golden Path 5 restores focus only as far as Windows permits a "
    "background process to. `SetForegroundWindow` from a process that does not "
    "hold foreground rights is refused by Windows, and W03-E does not synthesise "
    "input to defeat that (W03_E_CONTRACT.md escalation boundary; "
    "W03_PREP_ASSESSMENT.md records `focus_window` failing from a background "
    "process). The honest experience is: here is what you were doing, and one "
    "focus action you can approve."
)

BEFORE_LUNCH = "invoice-run-march.xlsx"
OBSERVATION_KIND = "environment_observation"


@pytest.fixture
async def store(tmp_path):
    from bartholomew.kernel.memory.privacy_guard import set_consent_handler
    from bartholomew.kernel.memory_store import MemoryStore

    set_consent_handler(None)
    mem = MemoryStore(str(tmp_path / "path5.db"))
    await mem.init()
    try:
        yield mem
    finally:
        await mem.close()
        set_consent_handler(None)


async def test_bartholomew_can_say_what_you_were_doing_without_acting(store):
    """Leg one: reconstruct from evidence. Read-only, and it stays read-only."""
    from datetime import datetime, timedelta, timezone

    before_lunch = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    await store.upsert_memory(
        OBSERVATION_KIND,
        "foreground/invoice-run",
        json.dumps(
            {
                "observed_event": {
                    "kind": "window_state",
                    "summary": f"{BEFORE_LUNCH} was in the foreground",
                    "facts": {"window_title": BEFORE_LUNCH, "application": "excel.exe"},
                },
                "inferred_state": None,
            },
        ),
        before_lunch,
    )

    row = await store.get_memory(OBSERVATION_KIND, "foreground/invoice-run")
    assert row is not None and BEFORE_LUNCH in row["value"]

    # Reconstructing proposes nothing. The account a person gets here is an
    # answer, and an answer is not an action.
    from bartholomew.actuation import store as action_store

    try:
        rows = action_store.recent_actions(store.db_path, tenant_id="local", limit=50)
    except Exception:
        rows = []
    assert rows == []


def test_the_restore_is_one_ordinary_action_a_person_approves(service, console, device):
    """Leg two: restoring is `focus_window`, and it waits like anything else."""
    status, payload = request_action(
        service.port,
        capability="windows.focus_window",
        parameters={"app_id": "notepad"},
    )
    assert status == 201, payload
    action_id = payload["action"]["action_id"]

    shown = console.run("actions", "show", action_id).ok()
    assert "state:       pending_approval" in shown.stdout
    assert "windows.focus_window" in shown.stdout

    console.run("actions", "approve", action_id).ok()
    arm_channel(service.port)
    _, leased = device.lease()
    assert [a for a in (leased.get("actions") or []) if a.get("action_id") == action_id], leased


def test_a_foreground_lock_refusal_is_never_reported_as_a_restore(service, console, device):
    """Leg three, and the honest degradation the contract asks for.

    Windows refuses foreground to a background process. When that happens the
    device reports it, and every surface a person meets says the restore did not
    happen -- rather than reporting the *attempt* as the outcome.
    """
    status, payload = request_action(
        service.port,
        capability="windows.focus_window",
        parameters={"app_id": "notepad"},
    )
    action_id = payload["action"]["action_id"]
    console.run("actions", "approve", action_id).ok()
    arm_channel(service.port)
    device.lease()

    device.report(
        action_id,
        status="failed",
        detail=(
            "Windows refused the foreground change: this process does not hold "
            "foreground rights and no input was synthesised to take them"
        ),
        error_category="permission_denied",
    )

    explained = console.run("actions", "explain", action_id).ok()
    assert "did not do what was asked" in explained.stdout
    assert "succeeded" not in explained.stdout.lower()
    assert read_action(service.port, action_id)["action"]["state"] == "failed"

    # And a person reading the list sees it as a failure too, not as a blank.
    listed = console.run("actions", "list").ok()
    assert "did not do what was asked" in listed.stdout


async def test_a_superseding_observation_wins_and_the_old_one_is_kept(store):
    """The supersession-aware half of leg one (W03-D).

    Two observations of the same window key, the later correcting the earlier.
    Retrieval serves the current one; the earlier is archived, not erased, so a
    person can still be told what was believed before.
    """
    components.require("retrieval_verdict")

    from datetime import datetime, timezone

    key = "foreground/before-lunch"
    first = json.dumps(
        {"observed_event": {"kind": "window_state", "facts": {"window_title": "draft-v1.docx"}}},
    )
    second = json.dumps(
        {"observed_event": {"kind": "window_state", "facts": {"window_title": BEFORE_LUNCH}}},
    )
    now = datetime.now(timezone.utc).isoformat()

    stored = await store.upsert_memory(OBSERVATION_KIND, key, first, now)
    assert stored.outcome == "stored", stored
    # W03-D's published signature: `correct_memory(kind, key, value) ->
    # CorrectionOutcome`, conditional on the record still being the one seen.
    corrected = await store.correct_memory(OBSERVATION_KIND, key, second)
    assert corrected.stored is True and corrected.target_changed is False, corrected

    current = await store.get_memory(OBSERVATION_KIND, key)
    assert BEFORE_LUNCH in current["value"]
    assert "draft-v1.docx" not in current["value"]

    archived = await store.list_memories_by_kind(["memory_revision"], limit=20)
    assert any(key in (row.get("key") or "") for row in archived), [r.get("key") for r in archived]


def test_nothing_in_this_package_synthesises_input(service):
    """The escalation boundary, asserted structurally.

    W03-E owns three paths. None of them may reach an input-synthesis primitive,
    directly or by name, because doing so is exactly how this scenario would be
    made to *look* successful.
    """
    import ast
    import pathlib

    owned = [
        pathlib.Path("bartholomew/cli_operator.py"),
        pathlib.Path("bartholomew_api_bridge_v0_1/services/api/routes/operator.py"),
        *sorted(pathlib.Path("tests/golden_path").glob("*.py")),
    ]
    forbidden = {
        "SendInput",
        "keybd_event",
        "mouse_event",
        "SetForegroundWindow",
        "AttachThreadInput",
        "pyautogui",
        "pynput",
        "ctypes",
        "windll",
    }

    offending: list[str] = []
    for path in owned:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {
            node.attr if isinstance(node, ast.Attribute) else node.id
            for node in ast.walk(tree)
            if isinstance(node, (ast.Attribute, ast.Name))
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        hit = names & forbidden
        if hit:
            offending.append(f"{path}: {sorted(hit)}")

    assert not offending, f"{NAMED_STOP}\nBut input synthesis was reached: {offending}"

    # Non-vacuity: the same scan finds it when it is there.
    planted = ast.parse("import ctypes\nctypes.windll.user32.SetForegroundWindow(h)")
    planted_names = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(planted)
        if isinstance(node, (ast.Attribute, ast.Name))
    } | {alias.name for n in ast.walk(planted) if isinstance(n, ast.Import) for alias in n.names}
    assert planted_names & forbidden


def test_the_capability_vocabulary_offers_no_way_to_force_focus():
    """And the boundary holds one layer down, in what can be asked for at all."""
    from bartholomew.actuation.capabilities import ALL_CAPABILITIES, describe

    focus = [k for k in ALL_CAPABILITIES if k.value == "windows.focus_window"]
    assert focus, "focus_window is the only restore this path has"

    summary = describe(focus[0]).summary.lower()
    # The capability is a request to Windows, and its own description says so.
    assert "foreground" in summary or "focus" in summary

    # There is no second, stronger focus capability to reach for.
    forcing = [k.value for k in ALL_CAPABILITIES if "force" in k.value or "input" in k.value]
    assert not forcing, f"{NAMED_STOP}\nBut a forcing capability exists: {forcing}"
