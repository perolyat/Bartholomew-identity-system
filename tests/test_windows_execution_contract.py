"""Acceptance suite for the Windows test-execution contract.

The contract is documented in `docs/WINDOWS_TEST_EXECUTION_CONTRACT.md`; the
implementation is `scripts/ci/xdist_contract.py` and
`scripts/ci/execution_trace.py`. These tests drive the two of them the way
the Merge Candidate does -- through a real pytest run, in a real xdist
controller -- rather than asserting on internals, so the clauses survive a
replacement of either module.

Every test here is deterministic and platform-independent. The failures
they pin were only ever *observed* on Windows, because Windows is where
tests are slow enough to trip a timeout and workers are therefore lost; the
defects themselves are in the controller and are reproducible anywhere once
a worker is made to die on purpose.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

xdist = pytest.importorskip("xdist")


# ---------------------------------------------------------------------------
# Clause: worker replacement. A worker that dies while a replacement for an
# earlier death is still collecting must not crash the controller.
# ---------------------------------------------------------------------------


class _FakeGateway:
    def __init__(self, ident: str) -> None:
        self.id = ident


class _FakeNode:
    """The two attributes the load-scope scheduler touches on a node."""

    def __init__(self, ident: str) -> None:
        self.gateway = _FakeGateway(ident)
        self.shutting_down = False
        self.sent: list[list[int]] = []

    def send_runtest_some(self, indices: list[int]) -> None:
        self.sent.append(list(indices))

    def shutdown(self) -> None:
        self.shutting_down = True

    def __repr__(self) -> str:  # pragma: no cover - only for failure messages
        return f"<WorkerController {self.gateway.id}>"


def _schedule_with_a_still_collecting_replacement(scheduler):
    """Drive a scheduler into the exact state the controller crashed in.

    ``gw0`` has collected and owns a file's worth of work. ``gw1`` is a
    replacement that has reported ready -- so the scheduler knows about it
    -- but has not finished collecting. ``gw0`` then dies. ``remove_node``
    re-queues gw0's work and reschedules every node it knows about,
    including the replacement.
    """
    collection = [
        "tests/test_alpha.py::test_one",
        "tests/test_alpha.py::test_two",
        "tests/test_beta.py::test_three",
    ]
    gw0 = _FakeNode("gw0")
    scheduler.add_node(gw0)
    scheduler.add_node_collection(gw0, collection)
    scheduler.schedule()

    # The replacement: ready (so add_node has run) but still collecting
    # (so add_node_collection has not).
    gw1 = _FakeNode("gw1")
    scheduler.add_node(gw1)

    return scheduler.remove_node(gw0)


def _make_config(pytester: pytest.Pytester, dist: str = "loadfile") -> pytest.Config:
    pytester.makepyfile(test_alpha="def test_one(): pass\ndef test_two(): pass\n")
    # `-n` is translated into `--tx` by xdist's command-line hook, which
    # parseconfigure does not run; the tx spec is what the scheduler reads.
    return pytester.parseconfigure("--tx", "popen", "--dist", dist)


def test_stock_xdist_scheduler_crashes_on_a_still_collecting_replacement(pytester):
    """The defect, pinned as a defect.

    If pytest-xdist ever fixes this upstream, this test fails and the
    override in `scripts/ci/xdist_contract.py` can be retired -- which is
    the only way anyone would find out.
    """
    from xdist.scheduler.loadfile import LoadFileScheduling

    config = _make_config(pytester)
    with pytest.raises(KeyError):
        _schedule_with_a_still_collecting_replacement(LoadFileScheduling(config))


def test_the_contract_scheduler_survives_a_still_collecting_replacement(pytester):
    from scripts.ci.xdist_contract import make_safe_scheduler

    config = _make_config(pytester)
    scheduler = make_safe_scheduler(config)
    assert scheduler is not None

    crashitem = _schedule_with_a_still_collecting_replacement(scheduler)

    # The dead worker's in-flight test is still identified, and its file is
    # back on the queue for whoever can take it.
    assert crashitem == "tests/test_alpha.py::test_one"
    assert len(scheduler.workqueue) >= 1


def test_the_replacement_gets_the_requeued_work_once_it_has_collected(pytester):
    """Replacement succeeds: the work a lost worker owned is run, not dropped."""
    from scripts.ci.xdist_contract import make_safe_scheduler

    config = _make_config(pytester)
    scheduler = make_safe_scheduler(config)
    _schedule_with_a_still_collecting_replacement(scheduler)

    replacement = next(node for node in scheduler.assigned_work if node.gateway.id == "gw1")
    # What DSession.worker_collectionfinish does when a node finishes
    # collecting: register the collection, then schedule across every node.
    scheduler.add_node_collection(
        replacement,
        [
            "tests/test_alpha.py::test_one",
            "tests/test_alpha.py::test_two",
            "tests/test_beta.py::test_three",
        ],
    )
    scheduler.schedule()

    assert replacement.sent, "the replacement was never given the re-queued work"
    assigned = [nodeid for unit in scheduler.assigned_work[replacement].values() for nodeid in unit]
    assert "tests/test_alpha.py::test_one" in assigned


def test_a_scheduler_override_is_not_imposed_on_other_distribution_modes(pytester):
    from scripts.ci.xdist_contract import make_safe_scheduler

    assert make_safe_scheduler(_make_config(pytester, dist="load")) is None
    assert make_safe_scheduler(_make_config(pytester, dist="each")) is None


# ---------------------------------------------------------------------------
# Clause: no silent loss of work. Every collected test produces a report, or
# the run says which ones did not and fails.
# ---------------------------------------------------------------------------


def test_work_accounting_is_silent_when_every_test_reported():
    from scripts.ci.xdist_contract import WorkAccounting

    accounting = WorkAccounting()
    accounting.expected = {"a::t1", "a::t2"}
    accounting.reported = {"a::t1", "a::t2"}

    assert accounting.unreported == []


def test_work_accounting_names_every_test_that_never_reported():
    from scripts.ci.xdist_contract import WorkAccounting

    accounting = WorkAccounting()
    accounting.expected = {"a::t1", "a::t2", "b::t3"}
    accounting.reported = {"a::t1"}

    assert accounting.unreported == ["a::t2", "b::t3"]


def test_a_worker_that_dies_mid_file_cannot_end_the_run_green(pytester):
    """The whole clause, through a real parallel run.

    One test kills its own worker outright, the way pytest-timeout's thread
    method does when a test overruns on a slow runner. The tests that
    follow it in the same file are the work that worker owned. The run must
    not end reporting only what happened to survive.
    """
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})

        def pytest_xdist_make_scheduler(config, log):
            from scripts.ci.xdist_contract import make_safe_scheduler
            return make_safe_scheduler(config, log)

        def pytest_configure(config):
            from scripts.ci import xdist_contract
            xdist_contract.install(config)
        """,
    )
    pytester.makepyfile(
        test_victim="""
        import os

        def test_kills_its_own_worker():
            # Exactly what pytest-timeout's thread method does on overrun.
            os._exit(1)

        def test_never_runs_a(): pass
        def test_never_runs_b(): pass
        """,
        test_bystander="""
        def test_unaffected(): pass
        """,
    )

    result = pytester.runpytest_subprocess(
        "-n",
        "2",
        "--dist",
        "loadfile",
        "-p",
        "no:cacheprovider",
    )

    assert result.ret != 0, "a run that lost work reported success"
    result.stdout.fnmatch_lines(["*work accounting: TESTS LOST*"])
    result.stdout.fnmatch_lines(["*test_never_runs_*"])


def test_an_intentional_early_stop_is_not_reported_as_lost_work(pytester):
    """`-x` leaves tests unrun by design; saying so would bury the real reason."""
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})

        def pytest_configure(config):
            from scripts.ci import xdist_contract
            xdist_contract.install(config)
        """,
    )
    pytester.makepyfile(
        test_stops="""
        def test_fails(): assert False
        def test_would_have_run(): pass
        """,
    )

    result = pytester.runpytest_subprocess("-x", "-p", "no:cacheprovider")

    assert result.ret != 0
    assert "work accounting: TESTS LOST" not in result.stdout.str()


# ---------------------------------------------------------------------------
# Clause: diagnostics. A stall must be attributable without inference from a
# CI cancellation, and the trace must distinguish slow work from slow
# transport.
# ---------------------------------------------------------------------------


def _trace_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _run_traced(pytester: pytest.Pytester, trace_dir: Path, *args: str):
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})

        def pytest_xdist_make_scheduler(config, log):
            from scripts.ci.xdist_contract import make_safe_scheduler
            return make_safe_scheduler(config, log)

        def pytest_configure(config):
            from scripts.ci import execution_trace, xdist_contract
            xdist_contract.install(config)
            execution_trace.install(config)
        """,
    )
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


def test_the_trace_brackets_every_report_at_both_ends(pytester, tmp_path, monkeypatch):
    """The measurement the controller log could never make.

    A worker's `phase` event is written before the report is sent; the
    controller's `report_received` when it arrives. With both, a twenty-
    minute gap is attributable to the test or to the transport, which is
    the question every previous diagnosis had to leave open.
    """
    trace_dir = tmp_path / "trace"
    monkeypatch.setenv("BARTHO_EXEC_TRACE", "1")
    monkeypatch.setenv("BARTHO_EXEC_TRACE_DIR", str(trace_dir))
    pytester.makepyfile(test_traced="def test_one(): pass\n")

    result = _run_traced(pytester, trace_dir, "-n", "1", "--dist", "loadfile")
    assert result.ret == 0

    controller = _trace_events(trace_dir / "controller.jsonl")
    worker = _trace_events(trace_dir / "gw0.jsonl")

    assert [e["event"] for e in controller][0] == "session_start"
    assert {"node_created", "node_ready", "node_collected"} <= {e["event"] for e in controller}

    call_phase = next(e for e in worker if e["event"] == "phase" and e["when"] == "call")
    arrival = next(
        e
        for e in controller
        if e["event"] == "report_received"
        and e["when"] == "call"
        and e["nodeid"].endswith("test_one")
    )
    assert call_phase["t"] <= arrival["t"], "the worker recorded a report after it arrived"
    assert "sqlite_connections" in call_phase


def test_a_lost_worker_is_recorded_as_lost_and_not_as_a_slow_test(pytester, tmp_path, monkeypatch):
    trace_dir = tmp_path / "trace"
    monkeypatch.setenv("BARTHO_EXEC_TRACE", "1")
    monkeypatch.setenv("BARTHO_EXEC_TRACE_DIR", str(trace_dir))
    pytester.makepyfile(
        test_dies="""
        import os
        def test_dies(): os._exit(1)
        """,
        test_ok="def test_ok(): pass\n",
    )

    _run_traced(pytester, trace_dir, "-n", "2", "--dist", "loadfile")

    controller = _trace_events(trace_dir / "controller.jsonl")
    downs = [e for e in controller if e["event"] == "node_down" and e["crashed"]]
    assert downs, "a worker that stopped existing left no node_down record"
    assert downs[0]["error"], "a lost worker was recorded without its error text"
    # A replacement is created, and that is visible too.
    created = [e["gateway"] for e in controller if e["event"] == "node_created"]
    assert len(created) > 2, "no replacement worker was created for the lost one"


def test_the_controller_ends_a_stalled_run_itself_rather_than_waiting_for_the_cap(
    pytester,
    tmp_path,
    monkeypatch,
):
    """Gate 11: a stall must produce its own attributable ending.

    The watchdog's abort bound is set to a few seconds here and the test
    body sleeps past it. The run must end with the contract's banner and
    an `abort` event naming the outstanding work -- not with a bare
    cancellation that says nothing.
    """
    trace_dir = tmp_path / "trace"
    monkeypatch.setenv("BARTHO_EXEC_TRACE", "1")
    monkeypatch.setenv("BARTHO_EXEC_TRACE_DIR", str(trace_dir))
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})

        def pytest_xdist_make_scheduler(config, log):
            from scripts.ci.xdist_contract import make_safe_scheduler
            return make_safe_scheduler(config, log)

        def pytest_configure(config):
            from scripts.ci import execution_trace, xdist_contract
            xdist_contract.install(config)
            execution_trace.install(config)
        """,
    )
    pytester.makepyfile(
        test_stalls="""
        import time
        def test_stalls(): time.sleep(120)
        """,
    )

    monkeypatch.setenv("BARTHO_EXEC_STALL_WARN_S", "3")
    monkeypatch.setenv("BARTHO_EXEC_STALL_ABORT_S", "10")
    # The point of the exercise is a stall the per-test timeout does not end,
    # which is what the Windows tail has always been.
    monkeypatch.setenv("PYTEST_TIMEOUT", "0")

    result = pytester.runpytest_subprocess(
        "-n",
        "1",
        "--dist",
        "loadfile",
        "-p",
        "no:cacheprovider",
    )

    assert result.ret == 3, f"expected the watchdog's own exit code, got {result.ret}"
    assert "EXECUTION CONTRACT VIOLATED" in result.stderr.str()

    controller = _trace_events(trace_dir / "controller.jsonl")
    aborts = [e for e in controller if e["event"] == "abort"]
    assert aborts, "the run ended without recording why"
    assert aborts[0]["idle_s"] >= 10
    assert aborts[0]["outstanding"], "the abort record does not name the outstanding work"

    stalls = [e for e in controller if e["event"] == "stall"]
    assert stalls, "no stall was reported before the abort"


@pytest.mark.skipif(sys.platform == "win32", reason="signal-free stack dump path differs")
def test_a_stalled_worker_writes_its_own_stacks(pytester, tmp_path, monkeypatch):
    """Requirement 11: tell a product deadlock from slow work.

    The stacks of every thread in the stalled worker are what separates
    "blocked on a lock" from "computing". They are written by the worker
    itself, so they survive the controller ending the run.
    """
    trace_dir = tmp_path / "trace"
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})

        def pytest_configure(config):
            from scripts.ci import execution_trace
            execution_trace.install(config)
        """,
    )
    pytester.makepyfile(
        test_slow="""
        import time
        def test_slow(): time.sleep(8)
        """,
    )

    monkeypatch.setenv("BARTHO_EXEC_TRACE", "1")
    monkeypatch.setenv("BARTHO_EXEC_TRACE_DIR", str(trace_dir))
    monkeypatch.setenv("BARTHO_EXEC_STALL_WARN_S", "3")
    monkeypatch.setenv("BARTHO_EXEC_STALL_ABORT_S", "0")
    monkeypatch.setenv("PYTEST_TIMEOUT", "0")

    pytester.runpytest_subprocess("-n", "1", "--dist", "loadfile", "-p", "no:cacheprovider")

    stacks = trace_dir / "gw0.stacks.txt"
    assert stacks.exists(), "a stalled worker produced no stack dump"
    text = stacks.read_text(encoding="utf-8")
    assert "stalled" in text
    assert "test_slow" in text

    worker = _trace_events(trace_dir / "gw0.jsonl")
    stall_events = [e for e in worker if e["event"] == "stall"]
    assert stall_events
    assert stall_events[0]["nodeid"].endswith("test_slow")
