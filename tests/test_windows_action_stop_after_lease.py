"""Stop after lease: the brake reaches an action that has already been handed out.

W03-C acceptance criterion 2, and the gap its contract names. On the wave-two
baseline the Parking Brake was read three times -- at request, at approval, and
immediately before the lease -- and then stopped mattering. Once `try_lease`
succeeded, nothing could abort the action: an engaged brake refused the *next*
dispatch, and a cancel written onto a leased row changed a database column no
device ever read. `store.mark_cancelled`'s own docstring said so: "Cancelling a
leased action does not reach out and stop a device -- nothing here can."

Something can now, and this file is what it is worth. The mechanism is two
halves that only work together:

* the **server** answers `/api/device-actions/abort-check`, and moves a halted
  lease to `aborted_by_brake` itself so an operator who engaged a brake and
  then lost the network still sees the action recorded as stopped;
* the **device** reads that signal between its lease and its handler, refuses
  to act on a stale clearance, and re-reads it inside a long step.

What this is not
----------------
A cooperative stop. A companion that has crashed, wedged, or been stopped from
polling is not stopped by any of this, and the tests below do not pretend
otherwise -- `test_the_stop_is_cooperative_and_says_so` asserts the honest
limitation directly. The independent out-of-process emergency stop is a named,
deferred package (`docs/waves/W03/W03_DEFERRALS.md` #8).

**Non-vacuity.** Every "it stopped" assertion has a paired "and with the brake
clear it runs" assertion in the same shape, so a test that stopped stopping
things would fail rather than silently pass.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from bartholomew.actuation import arming, devices, seam, store
from bartholomew.actuation.allowlists import (
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from bartholomew.actuation.capabilities import ALL_CAPABILITIES
from bartholomew.actuation.request import to_iso, utc_now
from bartholomew.actuation.result import (
    DEVICE_REPORTABLE_STATUSES,
    TERMINAL_STATUSES,
    ActionResultStatus,
    ErrorCategory,
    HandlerOutcome,
)
from bartholomew.actuation.store import ActionState
from bartholomew.windows_actuation import dispatch as dispatch_module
from bartholomew.windows_actuation.channel import ChannelResult, ChannelStatus
from bartholomew.windows_actuation.config import ActionCompanionConfig
from bartholomew.windows_actuation.dispatch import AbortSignal, DispatchRefusedError, LeasedAction
from bartholomew.windows_actuation.dispatch import check as device_check
from bartholomew.windows_actuation.handlers import HandlerContext
from bartholomew.windows_actuation.runner import ActionCompanionRunner
from bartholomew.windows_actuation.state import ActionCompanionState

TENANT = "tenant-a"
DEVICE = "desk-pc"
OTHER_DEVICE = "laptop"


# ---------------------------------------------------------------------------
# real fixtures: a real database, a real Governance store, the real seam
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self, db_path):
        from bartholomew.kernel.memory_store import MemoryStore

        self.mem = MemoryStore(db_path)
        self.db_path = db_path
        self.identity_context = None
        self.governance_store = None
        self.blocking_executor = None


@pytest.fixture
def db_path(tmp_path):
    from bartholomew.kernel.memory_store import MemoryStore
    from bartholomew.orchestrator.safety import governance_store as gs

    path = str(tmp_path / "abort.db")
    asyncio.run(MemoryStore(path).init())
    gs.ensure_schema(path)
    store.ensure_schema(path)
    return path


@pytest.fixture
def ctx(db_path):
    return _Ctx(db_path)


@pytest.fixture
def registry():
    device = devices.EnrolledDevice(
        device_id=DEVICE,
        tenant_id=TENANT,
        platform="windows",
        enrolled=True,
        capabilities=tuple(devices.DeclaredCapability(kind=k, version=1) for k in ALL_CAPABILITIES),
        applications=ApplicationAllowlist.from_pairs({"notepad": "C:\\notepad.exe"}),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable(["C:\\Users\\t\\Documents"]),
        trusted_autonomy=frozenset(),
    )

    class _Registry:
        LABEL = "abort-test-registry"

        def lookup(self, *, tenant_id, device_id):
            return device if (tenant_id, device_id) == (TENANT, DEVICE) else None

    reg = _Registry()
    devices.install_registry(reg)
    yield reg
    devices.install_registry(None)


@pytest.fixture(autouse=True)
def armed_channel():
    arming.arm(tenant_id=TENANT, device_id=DEVICE, armed_by="test-fixture", reason="abort suite")
    yield
    arming.reset_for_tests()


def _engage(db_path, scope="global"):
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    GovernanceStore(db_path).engage(scope, reason="test", actor="test")


async def _lease_one(ctx, registry, *, capability="windows.focus_window", parameters=None):
    """Request, approve and lease one action. Returns its id."""
    requested = await seam.run_action_request_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        requested_by="taylor",
        capability=capability,
        capability_version=1,
        parameters=parameters if parameters is not None else {"app_id": "notepad"},
        registry=registry,
    )
    action_id = requested.action.action_id
    await seam.grant_action_approval(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        approver="taylor",
        registry=registry,
    )
    leased = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        registry=registry,
    )
    assert leased.governance_allowed, leased.reason
    return action_id


async def _abort(ctx, action_ids, *, device=DEVICE):
    return await seam.evaluate_action_abort_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=device,
        action_ids=action_ids,
    )


# ---------------------------------------------------------------------------
# the server half
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clear_brake_leaves_a_leased_action_runnable(ctx, registry, db_path):
    """The non-vacuity anchor: without a halt, the signal is affirmative.

    Every "and then it stopped" test below is only worth something because
    this one shows the same call saying "carry on" when nothing is engaged.
    """
    action_id = await _lease_one(ctx, registry)

    signal = await _abort(ctx, [action_id])

    assert signal.halted is False
    assert signal.running == (action_id,)
    assert signal.aborted == ()
    assert signal.must_stop(action_id) is False
    assert store.get_action(db_path, tenant_id=TENANT, action_id=action_id).state is (
        ActionState.LEASED
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["global", "actuation", "skills", "voice"])
async def test_an_engaged_brake_aborts_an_already_leased_action(ctx, registry, db_path, scope):
    """The criterion. The lease already happened; the brake still reaches it."""
    action_id = await _lease_one(ctx, registry)
    _engage(db_path, scope)

    signal = await _abort(ctx, [action_id])

    assert signal.halted is True
    assert signal.aborted == (action_id,)
    assert signal.running == ()
    assert signal.must_stop(action_id) is True

    action = store.get_action(db_path, tenant_id=TENANT, action_id=action_id)
    assert action.state is ActionState.ABORTED_BY_BRAKE
    assert action.terminal is True


@pytest.mark.asyncio
async def test_the_server_records_the_abort_without_the_device_reporting(ctx, registry, db_path):
    """An operator pulls the brake and then the network dies. Still recorded.

    Waiting for the device to report the abort would leave the row at `leased`
    until the expiry sweep called it `unknown` -- an action that a halt
    definitively stopped, recorded as one nobody knows about.
    """
    action_id = await _lease_one(ctx, registry)
    _engage(db_path)

    await _abort(ctx, [action_id])

    action = store.get_action(db_path, tenant_id=TENANT, action_id=action_id)
    assert action.state is ActionState.ABORTED_BY_BRAKE
    assert action.state_reason and "brake" in action.state_reason.lower()
    assert action.parameters is None, "a terminal action's parameters are purged"


@pytest.mark.asyncio
async def test_a_cancel_on_a_leased_row_finally_reaches_the_device(ctx, registry, db_path):
    """The second half of the gap: a leased-row cancel used to reach nobody.

    No brake is engaged here at all. The action was withdrawn while the device
    held it, and before W03-C the only effect was that the device's eventual
    result would be declined -- after it had already acted.
    """
    action_id = await _lease_one(ctx, registry)
    await seam.cancel_action_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        action_id=action_id,
        cancelled_by="taylor",
        reason="changed my mind",
    )

    signal = await _abort(ctx, [action_id])

    assert signal.halted is False, "this is not a halt; it is one withdrawn action"
    assert signal.aborted == (action_id,)
    assert signal.must_stop(action_id) is True


@pytest.mark.asyncio
async def test_an_action_this_device_does_not_own_is_never_answered_as_running(ctx, registry):
    """Scoped in the query, so a device learns nothing about another machine."""
    action_id = await _lease_one(ctx, registry)

    signal = await _abort(ctx, [action_id], device=OTHER_DEVICE)

    assert signal.running == ()
    assert signal.aborted == (action_id,)
    assert signal.must_stop(action_id) is True


@pytest.mark.asyncio
async def test_an_unknown_action_id_is_aborted_not_permitted(ctx, registry):
    """Absence is not permission."""
    signal = await _abort(ctx, ["no-such-action"])
    assert signal.must_stop("no-such-action") is True
    assert signal.running == ()


@pytest.mark.asyncio
async def test_an_unreadable_brake_halts(ctx, registry, db_path, monkeypatch):
    """An unreadable safety gate is not evidence of the absence of one."""
    action_id = await _lease_one(ctx, registry)

    async def _explode(*_a, **_k):
        raise RuntimeError("the governance table is gone")

    monkeypatch.setattr(seam, "engaged_state_fail_closed_off_loop", _explode)

    signal = await _abort(ctx, [action_id])

    assert signal.halted is True
    assert signal.must_stop(action_id) is True


@pytest.mark.asyncio
async def test_an_unreadable_action_state_aborts_everything(ctx, registry, monkeypatch):
    """ "We could not establish that this may run" is not "this may run"."""
    action_id = await _lease_one(ctx, registry)

    def _explode(*_a, **_k):
        raise store.ActionPersistenceError("the action table is unreadable")

    monkeypatch.setattr(store, "states_for", _explode)

    signal = await _abort(ctx, [action_id])

    assert signal.running == ()
    assert signal.aborted == (action_id,)
    assert signal.halted is False, "an unreadable row is not a halt of the channel"


@pytest.mark.asyncio
async def test_the_abort_poll_is_bounded(ctx, registry):
    """A device cannot turn the poll into an unbounded query."""
    signal = await _abort(ctx, [f"a-{i}" for i in range(store.MAX_ABORT_POLL_IDS + 25)])
    assert len(signal.aborted) == store.MAX_ABORT_POLL_IDS


@pytest.mark.asyncio
async def test_an_aborted_action_cannot_later_acquire_a_success(ctx, registry, db_path):
    """Terminal means terminal, and a halt is not overwritten by a late report."""
    action_id = await _lease_one(ctx, registry)
    _engage(db_path)
    await _abort(ctx, [action_id])

    late = await seam.record_action_result_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        status="succeeded",
        error_category=None,
        detail="I did it anyway",
        evidence={},
        observed_at="",
    )

    assert late.governance_allowed is False
    assert late.category is ErrorCategory.REPLAY_REFUSED
    action = store.get_action(db_path, tenant_id=TENANT, action_id=action_id)
    assert action.state is ActionState.ABORTED_BY_BRAKE


@pytest.mark.asyncio
async def test_an_aborted_action_can_never_be_leased_again(ctx, registry, db_path):
    action_id = await _lease_one(ctx, registry)
    _engage(db_path)
    await _abort(ctx, [action_id])

    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    GovernanceStore(db_path).disengage(reason="test", actor="test")

    again = await seam.run_action_dispatch_through_runtime_contract(
        ctx,
        tenant_id=TENANT,
        device_id=DEVICE,
        action_id=action_id,
        registry=registry,
    )
    assert again.governance_allowed is False


# ---------------------------------------------------------------------------
# the bounded interval
# ---------------------------------------------------------------------------


def test_the_abort_clearance_is_a_bounded_interval():
    """ "Within a bounded interval" is a number, and it is a small one."""
    assert 0 < seam.ABORT_CLEARANCE_SECONDS <= 30


def test_the_lease_response_carries_an_abort_deadline():
    """The bound is the server's, stamped on the lease, not chosen by a device."""
    from datetime import datetime, timezone

    deadline = seam.abort_deadline()
    parsed = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    ahead = (parsed - datetime.now(timezone.utc)).total_seconds()
    assert 0 < ahead <= seam.ABORT_CLEARANCE_SECONDS + 1


def test_the_lease_route_actually_stamps_it():
    """A constant nothing sends is not a bound. Asserted over the route source."""
    import ast
    from pathlib import Path

    route = (
        Path(__file__).resolve().parents[1]
        / "bartholomew_api_bridge_v0_1"
        / "services"
        / "api"
        / "routes"
        / "device_actions.py"
    )
    tree = ast.parse(route.read_text(encoding="utf-8"))
    keys = {
        key.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    assert "abort_deadline" in keys
    assert "abort_check_seconds" in keys


# ---------------------------------------------------------------------------
# the device half
# ---------------------------------------------------------------------------


@pytest.fixture
def companion_config(tmp_path):
    return ActionCompanionConfig(
        base_url="http://127.0.0.1:8000",
        device_id=DEVICE,
        state_path=tmp_path / "action-state.json",
        capabilities=frozenset(ALL_CAPABILITIES),
        applications=ApplicationAllowlist.from_pairs({"notepad": "C:\\notepad.exe"}),
        url_domains=UrlDomainAllowlist.from_iterable(["example.com"]),
        filesystem_roots=FilesystemRootAllowlist.from_iterable([str(tmp_path)]),
    )


def _leased_action(**overrides):
    payload = {
        "action_id": "act-1",
        "tenant_id": TENANT,
        "device_id": DEVICE,
        "capability": "windows.focus_window",
        "capability_version": 1,
        "parameters": {"app_id": "notepad"},
        "expires_at": to_iso(utc_now() + timedelta(minutes=5)),
        "repeatability": "non_repeatable",
        "abort_deadline": to_iso(utc_now() + timedelta(seconds=5)),
    }
    payload.update(overrides)
    return LeasedAction.from_wire(payload)


class _FakeChannel:
    """A device channel with a switch on it, and a record of what was asked.

    Deliberately the real client's shape -- three verbs, the same signatures --
    so the runner under test is the real one and only the transport is fake.
    """

    def __init__(self, *, actions=(), halted=False, running=None, readable=True, clearance=None):
        self.device_id = DEVICE
        self._actions = list(actions)
        self.halted = halted
        self.running = running
        self.readable = readable
        #: Fixed clearance stamp, for the tests that need a stale one.
        self.clearance = clearance
        self.abort_calls: list[list[str]] = []
        self.reported: list[tuple[str, ActionResultStatus, str]] = []

    def lease(self, *, limit):
        actions, self._actions = self._actions[:limit], self._actions[limit:]
        return ChannelResult(ChannelStatus.OK, 200, {"actions": []}), actions, []

    def abort_check(self, *, action_ids):
        self.abort_calls.append(list(action_ids))
        if not self.readable:
            return (
                ChannelResult(ChannelStatus.RETRYABLE, None, None, "connection refused"),
                AbortSignal.unreadable("connection refused"),
            )
        running = self.running if self.running is not None else action_ids
        allowed = frozenset() if self.halted else frozenset(running)
        return ChannelResult(ChannelStatus.OK, 200, {}, ""), AbortSignal(
            readable=True,
            halted=self.halted,
            aborted=frozenset(a for a in action_ids if a not in allowed),
            running=allowed,
            reason="a parking brake is engaged" if self.halted else "",
            # Re-stamped on every read, exactly as the server does it.
            clearance_deadline=self.clearance
            or to_iso(
                utc_now() + timedelta(seconds=seam.ABORT_CLEARANCE_SECONDS),
            ),
        )

    def report(self, *, action_id, outcome, observed_at):
        self.reported.append((action_id, outcome.status, outcome.detail))
        return ChannelResult(ChannelStatus.OK, 200, {}, "")


def _watch_handlers(monkeypatch):
    """Replace every handler with a tripwire. Nothing here may touch a machine."""
    from bartholomew.windows_actuation import dispatch as dispatch_module

    ran: list[str] = []

    def _tripwire(_params, _ctx):
        ran.append("handler")
        return HandlerOutcome.succeeded("this should not have happened")

    monkeypatch.setattr(
        dispatch_module,
        "HANDLERS",
        dict.fromkeys(ALL_CAPABILITIES, _tripwire),
    )
    return ran


def test_an_engaged_brake_stops_the_handler_being_entered_at_all(companion_config, monkeypatch):
    """End-to-end against a fake channel: leased, halted, nothing ran.

    The strongest form of the criterion. Not "the handler ran and reported an
    abort" -- the handler was never called.
    """
    ran = _watch_handlers(monkeypatch)
    channel = _FakeChannel(actions=[_leased_action()], halted=True)
    runner = ActionCompanionRunner(companion_config, client=channel, sleep=lambda _s: None)

    outcomes = runner.poll_once()

    assert ran == [], "a halted action reached a handler"
    assert [o.status for o in outcomes] == [ActionResultStatus.ABORTED_BY_BRAKE]
    assert channel.reported == [
        ("act-1", ActionResultStatus.ABORTED_BY_BRAKE, channel.reported[0][2]),
    ]
    assert runner.summary.aborted == 1


def test_with_the_brake_clear_the_same_action_runs(companion_config, monkeypatch):
    """The non-vacuity pair. One flag differs; the outcome inverts."""
    ran = _watch_handlers(monkeypatch)
    channel = _FakeChannel(actions=[_leased_action()], halted=False)
    runner = ActionCompanionRunner(companion_config, client=channel, sleep=lambda _s: None)

    outcomes = runner.poll_once()

    assert ran == ["handler"]
    assert [o.status for o in outcomes] == [ActionResultStatus.SUCCEEDED]
    assert runner.summary.aborted == 0


def test_an_unreadable_abort_signal_stops_the_action(companion_config, monkeypatch):
    """A control that works only when nothing is wrong is not a control."""
    ran = _watch_handlers(monkeypatch)
    channel = _FakeChannel(actions=[_leased_action()], readable=False)
    runner = ActionCompanionRunner(companion_config, client=channel, sleep=lambda _s: None)

    outcomes = runner.poll_once()

    assert ran == []
    assert [o.status for o in outcomes] == [ActionResultStatus.ABORTED_BY_BRAKE]


def test_one_aborted_action_does_not_withhold_the_others(companion_config, monkeypatch):
    """A withdrawn action is about that action; a halt is about the channel."""
    ran = _watch_handlers(monkeypatch)
    channel = _FakeChannel(
        actions=[_leased_action(action_id="act-1"), _leased_action(action_id="act-2")],
        running=["act-2"],
    )
    runner = ActionCompanionRunner(companion_config, client=channel, sleep=lambda _s: None)

    outcomes = runner.poll_once()

    assert len(ran) == 1, "exactly one of the two reached a handler"
    assert [o.status for o in outcomes] == [
        ActionResultStatus.ABORTED_BY_BRAKE,
        ActionResultStatus.SUCCEEDED,
    ]


def test_the_abort_read_happens_before_the_handler_not_after(companion_config, monkeypatch):
    """Ordering is the whole mechanism, so it is asserted as ordering."""
    from bartholomew.windows_actuation import dispatch as dispatch_module

    order: list[str] = []
    channel = _FakeChannel(actions=[_leased_action()])
    original = channel.abort_check

    def _watched(*, action_ids):
        order.append("abort-check")
        return original(action_ids=action_ids)

    channel.abort_check = _watched

    def _handler(_params, _ctx):
        order.append("handler")
        return HandlerOutcome.succeeded("ran")

    monkeypatch.setattr(
        dispatch_module,
        "HANDLERS",
        dict.fromkeys(ALL_CAPABILITIES, _handler),
    )
    ActionCompanionRunner(companion_config, client=channel, sleep=lambda _s: None).poll_once()

    assert order == ["abort-check", "handler"]


def test_the_batch_is_checked_once_not_once_per_action(companion_config, monkeypatch):
    """The gap between the read and the first handler is kept as small as it can be."""
    _watch_handlers(monkeypatch)
    channel = _FakeChannel(
        actions=[_leased_action(action_id=f"act-{i}") for i in range(4)],
    )
    ActionCompanionRunner(companion_config, client=channel, sleep=lambda _s: None).poll_once()

    assert len(channel.abort_calls) == 1
    assert sorted(channel.abort_calls[0]) == ["act-0", "act-1", "act-2", "act-3"]


# ---------------------------------------------------------------------------
# a clearance that went stale is refreshed, not reported as a halt
# ---------------------------------------------------------------------------


def test_a_batch_that_outlives_its_clearance_is_not_a_halt(companion_config, monkeypatch):
    """Regression: the deadline bounds unhonoured halts, not batch duration.

    The lease stamps one clearance across the whole batch. A first action that
    takes longer than `ABORT_CLEARANCE_SECONDS` leaves every later action past
    that stamp -- and `dispatch.check()` refuses a passed clearance with
    `PARKING_BRAKE`. Without the refresh, actions two and three of an ordinary
    batch were recorded as stopped by a brake that was never engaged: a halt
    that did not happen, written into a durable audit row, which is exactly the
    fiction the result vocabulary exists to refuse.

    Here the lease's stamp is already in the past when the batch starts, which
    is the same condition a slow first action produces. All three still run,
    because the runner re-reads a stale clearance instead of inheriting it.
    """
    ran = _watch_handlers(monkeypatch)
    stale = to_iso(utc_now() - timedelta(seconds=30))
    channel = _FakeChannel(
        actions=[_leased_action(action_id=f"act-{i}", abort_deadline=stale) for i in range(3)],
    )
    runner = ActionCompanionRunner(companion_config, client=channel, sleep=lambda _s: None)

    outcomes = runner.poll_once()

    assert len(ran) == 3, "an ordinary batch was refused as a halt"
    assert all(o.status is ActionResultStatus.SUCCEEDED for o in outcomes)
    assert runner.summary.aborted == 0
    # One read for the batch is still the right number: the *signal's* clearance
    # was fresh, and it is the signal's stamp the actions carried -- not the
    # lease's, which was 30 seconds past.
    assert len(channel.abort_calls) == 1


def test_a_refresh_that_says_stop_still_stops(companion_config, monkeypatch):
    """The non-vacuity pair: refreshing is not a way to talk past a halt.

    The clearance is stale *and* a brake is engaged. The refresh happens and
    comes back halted, so nothing runs. A refresh can only ever confirm.
    """
    ran = _watch_handlers(monkeypatch)
    stale = to_iso(utc_now() - timedelta(seconds=30))
    channel = _FakeChannel(
        actions=[_leased_action(abort_deadline=stale)],
        halted=True,
    )
    runner = ActionCompanionRunner(companion_config, client=channel, sleep=lambda _s: None)

    outcomes = runner.poll_once()

    assert ran == []
    assert [o.status for o in outcomes] == [ActionResultStatus.ABORTED_BY_BRAKE]


def test_a_refresh_that_cannot_be_read_stops(companion_config, monkeypatch):
    """A device cannot extend its own clearance by failing to ask."""
    ran = _watch_handlers(monkeypatch)
    stale = to_iso(utc_now() - timedelta(seconds=30))
    channel = _FakeChannel(actions=[_leased_action(abort_deadline=stale)], readable=False)
    runner = ActionCompanionRunner(companion_config, client=channel, sleep=lambda _s: None)

    outcomes = runner.poll_once()

    assert ran == []
    assert [o.status for o in outcomes] == [ActionResultStatus.ABORTED_BY_BRAKE]


def test_the_clearance_a_device_honours_is_always_the_servers(companion_config, monkeypatch):
    """The device never computes how long it may act without asking again.

    A signal carrying an already-past clearance is honoured as past, even
    though the device could trivially have stamped itself a fresh one. That is
    the whole reason the field is server-measured.
    """
    ran = _watch_handlers(monkeypatch)
    past = to_iso(utc_now() - timedelta(seconds=30))
    channel = _FakeChannel(actions=[_leased_action()], clearance=past)
    runner = ActionCompanionRunner(companion_config, client=channel, sleep=lambda _s: None)

    outcomes = runner.poll_once()

    assert ran == [], "the device honoured a clearance it had been told was past"
    assert [o.status for o in outcomes] == [ActionResultStatus.ABORTED_BY_BRAKE]
    assert len(channel.abort_calls) == 2, "it re-read once, and honoured the answer"


@pytest.mark.asyncio
async def test_every_abort_answer_carries_a_freshly_stamped_clearance(ctx, registry, db_path):
    """Server side: each read restarts the clock, on the server's own clock."""
    action_id = await _lease_one(ctx, registry)

    first = await _abort(ctx, [action_id])
    assert first.clearance_deadline
    assert dispatch_module.abort_deadline_passed(first.clearance_deadline) is False

    _engage(db_path)
    halted = await _abort(ctx, [action_id])
    assert halted.halted is True
    assert halted.clearance_deadline, "even a halted answer stamps one"


# ---------------------------------------------------------------------------
# the stale-clearance refusal
# ---------------------------------------------------------------------------


@pytest.fixture
def device_ctx(companion_config):
    return HandlerContext(config=companion_config)


@pytest.mark.parametrize(
    "deadline",
    ["", None, "not a timestamp", "2020-01-01T00:00:00Z", "2020-01-01T00:00:00"],
    ids=["empty", "absent", "unparseable", "past", "naive"],
)
def test_a_stale_or_missing_abort_clearance_refuses(device_ctx, deadline):
    """Every unreadable form of the deadline is treated as an expired one.

    A server that sent no bound must not be read as having sent an unbounded
    one -- the same treatment `_parse_expiry` gives an unreadable expiry, and
    for the same reason.
    """
    overrides = {} if deadline is None else {"abort_deadline": deadline}
    action = _leased_action(**overrides)
    if deadline is None:
        action = LeasedAction(
            action_id="act-1",
            tenant_id=TENANT,
            device_id=DEVICE,
            capability="windows.focus_window",
            capability_version=1,
            parameters={"app_id": "notepad"},
            expires_at=to_iso(utc_now() + timedelta(minutes=5)),
        )
    with pytest.raises(DispatchRefusedError) as refusal:
        device_check(action, device_ctx, ActionCompanionState())
    assert refusal.value.category is ErrorCategory.PARKING_BRAKE


def test_a_live_abort_clearance_passes(device_ctx):
    """The non-vacuity pair for the refusal above."""
    kind = device_check(_leased_action(), device_ctx, ActionCompanionState())
    assert kind.value == "windows.focus_window"


# ---------------------------------------------------------------------------
# per step, for a multi-step handler
# ---------------------------------------------------------------------------


def test_a_long_running_handler_can_be_stopped_between_its_steps(companion_config, monkeypatch):
    """`launch_app` waits seconds for a window. A halt in that gap stops it.

    The process has already started, so the outcome records
    `steps_completed=1` rather than claiming an untouched machine. That is the
    distinction the status exists to carry.
    """
    from bartholomew.windows_actuation import handlers as handlers_module
    from bartholomew.windows_actuation import win32

    executable = companion_config.state_path.parent / "notepad.exe"
    executable.write_text("x", encoding="utf-8")
    config = ActionCompanionConfig(
        base_url=companion_config.base_url,
        device_id=DEVICE,
        state_path=companion_config.state_path,
        capabilities=companion_config.capabilities,
        applications=ApplicationAllowlist.from_pairs({"notepad": str(executable)}),
        url_domains=companion_config.url_domains,
        filesystem_roots=companion_config.filesystem_roots,
    )

    class _Started:
        process_id = 4242
        running = True

    monkeypatch.setattr(win32, "start_process", lambda _p: _Started())
    monkeypatch.setattr(win32, "process_image_name", lambda _pid: str(executable))
    monkeypatch.setattr(win32, "visible_windows", lambda: [])

    ctx = HandlerContext(config=config, abort_gate=lambda: "a parking brake is engaged")
    outcome = handlers_module.launch_app({"app_id": "notepad"}, ctx)

    assert outcome.status is ActionResultStatus.ABORTED_BY_BRAKE
    assert outcome.evidence["steps_completed"] == 1
    assert outcome.evidence["process_id"] == 4242


def test_the_abort_gate_can_only_ever_stop(companion_config):
    """A mid-flight hook that could say anything else would be an instruction."""
    import inspect

    signature = inspect.signature(HandlerContext.check_abort)
    assert list(signature.parameters) == ["self"]

    ctx = HandlerContext(config=companion_config, abort_gate=lambda: None)
    assert ctx.check_abort() is None

    stopping = HandlerContext(config=companion_config, abort_gate=lambda: "halted")
    assert stopping.check_abort() == "halted"

    def _raises():
        raise RuntimeError("transient")

    assert HandlerContext(config=companion_config, abort_gate=_raises).check_abort() is None
    assert HandlerContext(config=companion_config).check_abort() is None


# ---------------------------------------------------------------------------
# the vocabulary, and the honest limitation
# ---------------------------------------------------------------------------


def test_aborted_by_brake_is_terminal_and_is_not_a_success():
    from bartholomew.actuation.result import ActionResult

    assert ActionResultStatus.ABORTED_BY_BRAKE in TERMINAL_STATUSES
    assert ActionResultStatus.ABORTED_BY_BRAKE in DEVICE_REPORTABLE_STATUSES
    result = ActionResult(
        action_id="a",
        tenant_id=TENANT,
        device_id=DEVICE,
        status=ActionResultStatus.ABORTED_BY_BRAKE,
        error_category=ErrorCategory.PARKING_BRAKE,
    )
    assert result.terminal is True
    assert result.succeeded is False


def test_an_abort_is_distinguishable_from_a_withdrawal():
    """The reason this is a status and not a category on `cancelled`.

    A person withdrawing an action and a safety control stopping one are
    different events with different follow-ups, and an operator auditing the
    control needs to count the second without the first.
    """
    assert ActionResultStatus.ABORTED_BY_BRAKE is not ActionResultStatus.CANCELLED
    assert ActionState.ABORTED_BY_BRAKE is not ActionState.CANCELLED
    assert ActionState.ABORTED_BY_BRAKE.value == "aborted_by_brake"


def test_the_stop_is_cooperative_and_says_so():
    """The limitation, asserted rather than left to be discovered.

    Everything W03-C built here depends on the companion asking. A wedged or
    killed process is not stopped by it, the deferral register says so, and
    this test exists so that a later reading of the code cannot mistake this
    for the independent emergency stop that is still owed.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "bartholomew" / "actuation" / "seam.py"
    ).read_text(encoding="utf-8")
    assert "out-of-process" in source
    assert "W03_DEFERRALS.md" in source

    register = (
        Path(__file__).resolve().parents[1] / "docs" / "waves" / "W03" / "W03_DEFERRALS.md"
    ).read_text(encoding="utf-8")
    assert "Out-of-process independent emergency stop" in register
