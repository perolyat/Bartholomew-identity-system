"""Unit-level evidence for the always-on runtime pieces (Session D).

The lifecycle claims themselves are proven against real processes in
`tests/integration/test_always_on_service.py`. This file covers the parts that
are cheaper and more exactly asserted in isolation: the heartbeat's own logic,
the supervision refusals, and the fail-closed authentication seam.
"""

from __future__ import annotations

import pytest

from bartholomew.runtime import health, serve
from bartholomew_api_bridge_v0_1.services.api import inbound_auth

# ---------------------------------------------------------------------------
# Scheduler heartbeat
# ---------------------------------------------------------------------------


def test_a_fresh_heartbeat_is_not_yet_claiming_to_run():
    hb = health.SchedulerHeartbeat()
    assert hb.state == health.SCHEDULER_STARTING
    assert hb.healthy is False
    assert hb.snapshot()["last_beat"] is None


def test_beating_marks_it_running_and_records_the_drive():
    hb = health.SchedulerHeartbeat()
    hb.beat()
    assert hb.healthy is True
    assert hb.snapshot()["last_drive"] is None

    hb.beat(drive="self_check")
    assert hb.snapshot()["last_drive"] == "self_check"


def _age(hb: health.SchedulerHeartbeat, seconds: float) -> None:
    """Backdate a heartbeat's own recorded times by `seconds`.

    Deliberately not a monkeypatch of `time.monotonic`. Patching the stdlib
    module is a process-wide mutation in a shared test process: correct only
    for as long as nothing else in the run reads the clock, and exactly the
    kind of thing that becomes someone else's ordering bug months later.
    Backdating the record under test is both narrower and a more faithful
    simulation of what actually happens -- the loop stops beating; the clock
    does not lurch.
    """
    if hb.last_beat_monotonic is not None:
        hb.last_beat_monotonic -= seconds
    hb._started_monotonic -= seconds


def test_a_loop_that_stops_beating_is_reported_stalled():
    """A heartbeat that merely exists is not evidence the loop is alive."""
    hb = health.SchedulerHeartbeat()
    hb.beat()
    assert hb.stalled is False

    _age(hb, health.STALL_AFTER_SECONDS + 1)

    assert hb.stalled is True
    assert hb.healthy is False
    snapshot = hb.snapshot()
    assert snapshot["state"] == health.SCHEDULER_FAILED
    assert "no scheduler activity" in snapshot["error"]


def test_a_loop_that_never_beats_does_not_stay_healthy_by_default():
    """Absence of evidence must not read as evidence of health."""
    hb = health.SchedulerHeartbeat()
    hb.state = health.SCHEDULER_RUNNING  # claims to run, never beat
    _age(hb, health.STALL_AFTER_SECONDS + 1)
    assert hb.stalled is True


def test_a_failed_loop_reports_its_error():
    hb = health.SchedulerHeartbeat()
    hb.beat()
    hb.mark_failed(RuntimeError("loop blew up"))
    assert hb.healthy is False
    assert hb.snapshot()["state"] == health.SCHEDULER_FAILED
    assert "loop blew up" in hb.snapshot()["error"]


def test_a_stopped_loop_is_not_a_failure():
    """Cancellation during shutdown is the loop working as intended."""
    hb = health.SchedulerHeartbeat()
    hb.beat()
    hb.mark_stopped()
    assert hb.snapshot()["state"] == health.SCHEDULER_STOPPED
    assert hb.snapshot()["error"] is None


# ---------------------------------------------------------------------------
# Supervision refusals
# ---------------------------------------------------------------------------


def test_multiple_workers_are_refused_with_the_single_writer_reason():
    with pytest.raises(ValueError, match="single-writer"):
        serve.check_supervision_config(workers=2, reload=False)


def test_reload_is_refused():
    with pytest.raises(ValueError, match="[Aa]utoreload"):
        serve.check_supervision_config(workers=1, reload=True)


def test_one_worker_without_reload_is_the_supported_configuration():
    serve.check_supervision_config(workers=1, reload=False)  # does not raise


def test_refused_configuration_returns_a_distinct_exit_code():
    """A supervisor must be able to tell "will never work" from "crashed"."""
    assert serve.serve(workers=3) == serve.EXIT_BAD_CONFIG
    assert serve.EXIT_BAD_CONFIG != serve.EXIT_LOCK_HELD


def test_port_resolution(monkeypatch):
    monkeypatch.delenv("BARTH_API_PORT", raising=False)
    assert serve.resolve_port() == 5173

    monkeypatch.setenv("BARTH_API_PORT", "8080")
    assert serve.resolve_port() == 8080

    monkeypatch.setenv("BARTH_API_PORT", "not-a-port")
    with pytest.raises(ValueError, match="not an integer"):
        serve.resolve_port()

    monkeypatch.setenv("BARTH_API_PORT", "70000")
    with pytest.raises(ValueError, match="out of range"):
        serve.resolve_port()


def test_the_stop_budget_is_shorter_than_the_unit_files_stop_timeout():
    """A SIGKILL partway through `stop()` is the unclean shutdown we recover from."""
    from pathlib import Path

    unit = (Path(__file__).resolve().parents[1] / "deploy" / "bartholomew.service").read_text()
    timeout_line = next(line for line in unit.splitlines() if line.startswith("TimeoutStopSec="))
    seconds = int(timeout_line.split("=")[1].rstrip("s"))
    assert seconds > serve.SHUTDOWN_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# The authentication seam
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_resolver():
    inbound_auth.clear_resolver()
    yield
    inbound_auth.clear_resolver()


def test_no_resolver_is_the_default_and_means_closed():
    assert inbound_auth.get_resolver() is None
    assert inbound_auth.resolver_is_test_only() is False


def test_the_test_resolver_refuses_to_install_without_its_gate(monkeypatch):
    monkeypatch.delenv(inbound_auth.ALLOW_TEST_RESOLVER_ENV, raising=False)
    with pytest.raises(RuntimeError, match="not authentication"):
        inbound_auth.install_test_resolver("token")
    assert inbound_auth.get_resolver() is None


def test_the_test_resolver_needs_both_environment_gates(monkeypatch):
    """Neither variable alone turns test-only auth on."""
    monkeypatch.delenv(inbound_auth.ALLOW_TEST_RESOLVER_ENV, raising=False)
    monkeypatch.setenv(inbound_auth.TEST_RESOLVER_TOKEN_ENV, "tok")
    assert inbound_auth.maybe_install_test_resolver_from_env() is False

    monkeypatch.setenv(inbound_auth.ALLOW_TEST_RESOLVER_ENV, "1")
    monkeypatch.delenv(inbound_auth.TEST_RESOLVER_TOKEN_ENV, raising=False)
    assert inbound_auth.maybe_install_test_resolver_from_env() is False
    assert inbound_auth.get_resolver() is None

    monkeypatch.setenv(inbound_auth.TEST_RESOLVER_TOKEN_ENV, "tok")
    assert inbound_auth.maybe_install_test_resolver_from_env() is True
    assert inbound_auth.resolver_is_test_only() is True


def test_a_control_plane_resolver_is_not_flagged_as_test_only(monkeypatch):
    """The seam B plugs into: install a verifier, and capture opens normally."""

    class ControlPlaneResolver:
        async def resolve(self, request, body):
            return None

    inbound_auth.install_resolver(ControlPlaneResolver())
    assert inbound_auth.get_resolver() is not None
    assert inbound_auth.resolver_is_test_only() is False


def test_a_verified_source_only_needs_three_attributes():
    """The interface is structural, so B's own Principal can satisfy it directly."""

    class TheirPrincipal:
        source_id = "their-source"
        runtime_id = "runtime-7"
        verified_by = "control-plane-session"

    assert isinstance(TheirPrincipal(), inbound_auth.VerifiedInboundSource)


# ---------------------------------------------------------------------------
# Health reporting
# ---------------------------------------------------------------------------


class _FakeKernel:
    def __init__(self, state, heartbeat):
        self.lifecycle_state = state
        self.scheduler_heartbeat = heartbeat


def _component_health_with(monkeypatch, kernel):
    from bartholomew_api_bridge_v0_1.services.api import app as api_app

    monkeypatch.setattr(api_app, "_kernel", kernel)
    return api_app._component_health()


def test_health_is_ok_when_runtime_and_scheduler_are_alive(monkeypatch):
    from bartholomew.kernel.daemon import DaemonLifecycleState

    hb = health.SchedulerHeartbeat()
    hb.beat()
    components = _component_health_with(
        monkeypatch,
        _FakeKernel(DaemonLifecycleState.RUNNING, hb),
    )

    assert components["_overall"] == "ok"
    assert components["runtime"]["status"] == "ok"
    assert components["scheduler"]["status"] == "ok"


def test_health_is_degraded_when_the_scheduler_has_died(monkeypatch):
    """The whole point: a process whose scheduler died must not answer "ok".

    This is the failure that made "always-on" untrue before -- the service
    kept serving, `/healthz` kept saying ok, and Bartholomew had quietly
    stopped being proactive.
    """
    from bartholomew.kernel.daemon import DaemonLifecycleState

    hb = health.SchedulerHeartbeat()
    hb.beat()
    hb.mark_failed("loop exited")

    components = _component_health_with(
        monkeypatch,
        _FakeKernel(DaemonLifecycleState.RUNNING, hb),
    )

    assert components["_overall"] == "degraded"
    assert components["scheduler"]["status"] == "failed"
    assert "loop exited" in components["scheduler"]["error"]


def test_health_is_degraded_when_there_is_no_runtime(monkeypatch):
    components = _component_health_with(monkeypatch, None)
    assert components["_overall"] == "degraded"
    assert components["runtime"]["status"] == "failed"
    # Unknown is not failure: with no kernel we genuinely cannot tell.
    assert components["scheduler"]["status"] == "unknown"


def test_health_reports_inbound_closed_as_ok_but_says_so(monkeypatch):
    """A deliberately closed door is working correctly, and says which it is."""
    from bartholomew.kernel.daemon import DaemonLifecycleState

    hb = health.SchedulerHeartbeat()
    hb.beat()
    components = _component_health_with(
        monkeypatch,
        _FakeKernel(DaemonLifecycleState.RUNNING, hb),
    )

    assert components["inbound"]["status"] == "ok"
    assert components["inbound"]["open"] is False
    assert components["_overall"] == "ok"


# ---------------------------------------------------------------------------
# Scheduler supervision on the daemon itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_scheduler_task_that_raises_is_recorded_not_swallowed():
    """A fire-and-forget task that dies must not die silently.

    Exercises `KernelDaemon._on_scheduler_task_done` against a real asyncio
    task, without standing up a whole daemon: the callback is the mechanism
    that turns a dead autonomy loop into an observable state.
    """
    import asyncio

    from bartholomew.kernel.daemon import DaemonLifecycleState, KernelDaemon

    daemon = KernelDaemon.__new__(KernelDaemon)
    daemon.lifecycle_state = DaemonLifecycleState.RUNNING
    daemon.scheduler_heartbeat = health.SchedulerHeartbeat()
    daemon.scheduler_heartbeat.beat()

    async def dies():
        raise RuntimeError("scheduler exploded")

    task = asyncio.create_task(dies())
    task.add_done_callback(daemon._on_scheduler_task_done)
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)  # let the callback run

    assert daemon.scheduler_heartbeat.state == health.SCHEDULER_FAILED
    assert "scheduler exploded" in daemon.scheduler_heartbeat.error


@pytest.mark.asyncio
async def test_a_cancelled_scheduler_task_is_a_clean_stop():
    """Cancellation during shutdown is intended, and is not reported as failure."""
    import asyncio

    from bartholomew.kernel.daemon import DaemonLifecycleState, KernelDaemon

    daemon = KernelDaemon.__new__(KernelDaemon)
    daemon.lifecycle_state = DaemonLifecycleState.STOPPING
    daemon.scheduler_heartbeat = health.SchedulerHeartbeat()
    daemon.scheduler_heartbeat.beat()

    async def forever():
        await asyncio.sleep(3600)

    task = asyncio.create_task(forever())
    task.add_done_callback(daemon._on_scheduler_task_done)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert daemon.scheduler_heartbeat.state == health.SCHEDULER_STOPPED
    assert daemon.scheduler_heartbeat.error is None


@pytest.mark.asyncio
async def test_the_scheduler_loop_beats_even_when_no_drive_is_due():
    """An idle loop is a live loop; the heartbeat must not need a drive to advance."""
    import asyncio
    from types import SimpleNamespace

    from bartholomew.kernel.scheduler import loop as loop_module

    beats = health.SchedulerHeartbeat()
    ctx = SimpleNamespace(scheduler_heartbeat=beats)

    loop_module._beat(ctx)
    assert beats.state == health.SCHEDULER_RUNNING
    first = beats.last_beat_monotonic

    await asyncio.sleep(0.01)
    loop_module._beat(ctx)
    assert beats.last_beat_monotonic > first
    assert beats.last_drive is None  # no drive ran, and none was invented


def test_a_context_without_a_heartbeat_does_not_break_the_loop():
    """`run_scheduler()` accepts duck-typed contexts; a missing heartbeat is not fatal."""
    from types import SimpleNamespace

    from bartholomew.kernel.scheduler import loop as loop_module

    loop_module._beat(SimpleNamespace())  # does not raise


# ---------------------------------------------------------------------------
# Failure escalation: a dead component must become a restart, not a JSON field
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_recorder():
    from bartholomew.runtime import supervision

    supervision.get_recorder().reset()
    yield
    supervision.get_recorder().reset()


class _FakeServer:
    should_exit = False


def test_a_fatal_failure_asks_the_server_to_stop_gracefully():
    """Graceful, not a kill: the existing shutdown path must still run."""
    from bartholomew.runtime import supervision

    server = _FakeServer()
    recorder = supervision.get_recorder()
    recorder.bind_server(server)

    supervision.record_fatal_failure("scheduler", "loop died")

    assert server.should_exit is True
    assert recorder.failure.component == "scheduler"
    assert recorder.failure.reason == "loop died"


def test_the_first_fatal_failure_wins():
    """A cascade is reported by its cause, not by its last consequence."""
    from bartholomew.runtime import supervision

    supervision.get_recorder().bind_server(_FakeServer())
    supervision.record_fatal_failure("scheduler", "the cause")
    supervision.record_fatal_failure("something-else", "the consequence")

    assert supervision.get_recorder().failure.component == "scheduler"


def test_recording_without_a_bound_server_terminates_nothing():
    """A daemon outside `serve` is not under this module's supervision."""
    from bartholomew.runtime import supervision

    supervision.record_fatal_failure("scheduler", "no server here")

    assert supervision.get_recorder().failure is not None  # recorded
    # Nothing to assert about termination precisely because nothing happened:
    # no server was bound, so no shutdown was requested and no exception rose.


@pytest.mark.asyncio
async def test_an_unexpected_scheduler_exit_escalates_for_restart():
    import asyncio

    from bartholomew.kernel.daemon import DaemonLifecycleState, KernelDaemon
    from bartholomew.runtime import supervision

    supervision.get_recorder().bind_server(server := _FakeServer())

    daemon = KernelDaemon.__new__(KernelDaemon)
    daemon.lifecycle_state = DaemonLifecycleState.RUNNING
    daemon.scheduler_heartbeat = health.SchedulerHeartbeat()
    daemon.scheduler_heartbeat.beat()

    async def dies():
        raise RuntimeError("scheduler exploded")

    task = asyncio.create_task(dies())
    task.add_done_callback(daemon._on_scheduler_task_done)
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert daemon.scheduler_heartbeat.state == health.SCHEDULER_FAILED
    assert server.should_exit is True, "a dead scheduler did not escalate for restart"
    assert "scheduler exploded" in supervision.get_recorder().failure.reason


@pytest.mark.asyncio
async def test_a_scheduler_that_returns_on_its_own_also_escalates():
    """The loop runs until cancelled; returning is as wrong as raising."""
    import asyncio

    from bartholomew.kernel.daemon import DaemonLifecycleState, KernelDaemon
    from bartholomew.runtime import supervision

    supervision.get_recorder().bind_server(server := _FakeServer())

    daemon = KernelDaemon.__new__(KernelDaemon)
    daemon.lifecycle_state = DaemonLifecycleState.RUNNING
    daemon.scheduler_heartbeat = health.SchedulerHeartbeat()

    async def returns_early():
        return None

    task = asyncio.create_task(returns_early())
    task.add_done_callback(daemon._on_scheduler_task_done)
    await task
    await asyncio.sleep(0)

    assert server.should_exit is True


@pytest.mark.asyncio
async def test_normal_cancellation_never_escalates():
    """Shutdown must not look like a fault, or every stop becomes a restart."""
    import asyncio

    from bartholomew.kernel.daemon import DaemonLifecycleState, KernelDaemon
    from bartholomew.runtime import supervision

    supervision.get_recorder().bind_server(server := _FakeServer())

    daemon = KernelDaemon.__new__(KernelDaemon)
    daemon.lifecycle_state = DaemonLifecycleState.STOPPING
    daemon.scheduler_heartbeat = health.SchedulerHeartbeat()
    daemon.scheduler_heartbeat.beat()

    async def forever():
        await asyncio.sleep(3600)

    task = asyncio.create_task(forever())
    task.add_done_callback(daemon._on_scheduler_task_done)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert daemon.scheduler_heartbeat.state == health.SCHEDULER_STOPPED
    assert server.should_exit is False, "a normal shutdown escalated as a fault"
    assert supervision.get_recorder().failure is None


@pytest.mark.asyncio
async def test_a_loop_that_raises_during_shutdown_is_not_a_fault():
    """Already stopping: whatever the loop did on the way out, don't restart."""
    import asyncio

    from bartholomew.kernel.daemon import DaemonLifecycleState, KernelDaemon
    from bartholomew.runtime import supervision

    supervision.get_recorder().bind_server(server := _FakeServer())

    daemon = KernelDaemon.__new__(KernelDaemon)
    daemon.lifecycle_state = DaemonLifecycleState.STOPPING
    daemon.scheduler_heartbeat = health.SchedulerHeartbeat()

    async def dies():
        raise RuntimeError("torn down mid-flight")

    task = asyncio.create_task(dies())
    task.add_done_callback(daemon._on_scheduler_task_done)
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert server.should_exit is False
    assert supervision.get_recorder().failure is None


def test_the_restart_exit_code_is_not_prevented_by_the_unit_file():
    """The unit must actually restart on the code the failure path returns.

    A `RestartPreventExitStatus` that happened to include the runtime-failure
    code would silently undo this whole mechanism: the process would exit
    correctly and systemd would decline to restart it.
    """
    from pathlib import Path

    from bartholomew.runtime import serve, supervision

    unit = (Path(__file__).resolve().parents[1] / "deploy" / "bartholomew.service").read_text()
    prevented_line = next(
        line for line in unit.splitlines() if line.startswith("RestartPreventExitStatus=")
    )
    prevented = {int(code) for code in prevented_line.split("=", 1)[1].split()}

    assert supervision.EXIT_RUNTIME_FAILURE not in prevented
    # The configuration codes, which will never succeed on retry, still are.
    assert serve.EXIT_LOCK_HELD in prevented
    assert serve.EXIT_BAD_CONFIG in prevented
    assert "Restart=on-failure" in unit
