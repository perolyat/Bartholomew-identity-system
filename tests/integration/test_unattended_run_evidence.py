"""The unattended-run acceptance scenario, against real supervised processes.

`tests/integration/test_always_on_service.py` proves the *runtime* behaves
like a service. This proves the *evidence* of an unattended period is
trustworthy afterwards -- which is the open Band A item: "reliable
evidence/logging -- Test #1's own shutdown-capture gap (OP-W005) must not
recur" (`ROADMAP.md`). A run whose record cannot say what happened is not
evidence, however well the runtime behaved while nobody was watching.

Everything here is a real `python -m bartholomew serve` subprocess talking
real HTTP, for the same reason as the always-on suite: the claims are about
processes ending in ways that a test double cannot honestly imitate. A killed
process is genuinely killed.

The scenario, end to end, is `test_an_unattended_run_produces_a_trustworthy_
record`; the tests around it pin the individual properties it depends on, so
a failure says which one broke.

Marked `integration` -- excluded by the default marker expression, run by
CI's `critical` job.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bartholomew.runtime.evidence import END_CLEAN, END_FAILED, END_LOST, EvidenceStore
from bartholomew.runtime.unattended import UnattendedRun, new_run_id

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]

STARTUP_TIMEOUT = 90.0

#: Credentials for the double-gated test-only inbound resolver -- the same
#: pair `test_always_on_service.py` uses. Present so the run can observe a
#: *governed* activity that already exists (inbound capture goes through
#: Governance and records its own outcome), rather than inventing one.
TEST_TOKEN = "integration-only-token"
TEST_RESOLVER_ENV = {
    "BARTH_INBOUND_ALLOW_TEST_RESOLVER": "1",
    "BARTH_INBOUND_TEST_TOKEN": TEST_TOKEN,
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _base_env(db_path: Path, port: int, **extra) -> dict:
    env = dict(os.environ)
    env.update(
        {
            "BARTH_DB_PATH": str(db_path),
            "BARTH_API_PORT": str(port),
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONUNBUFFERED": "1",
            "BARTH_DRIVE_PACE_S": "0.5",
        },
    )
    env.update(extra)
    return env


def _start(run: UnattendedRun, db_path: Path, **env_extra) -> tuple[subprocess.Popen, int]:
    """Start one service incarnation inside `run` and wait for it to be up."""
    port = _free_port()
    proc = run.spawn(
        [sys.executable, "-m", "bartholomew", "serve"],
        cwd=str(REPO_ROOT),
        env=_base_env(db_path, port, **env_extra),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    run.wait_for_health(port, proc=proc, timeout=STARTUP_TIMEOUT)
    return proc, port


def _post_inbound(port: int, event_id: str) -> int:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(  # noqa: S310 - fixed localhost URL
        f"http://127.0.0.1:{port}/api/inbound/events",
        data=json.dumps(
            {
                "source_id": "test-source",
                "event_id": event_id,
                "event_type": "evidence.probe",
                "payload": {"marker": event_id},
            },
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Bartholomew-Test-Token": TEST_TOKEN,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15.0) as r:  # noqa: S310
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


# ---------------------------------------------------------------------------
# The acceptance scenario
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_an_unattended_run_produces_a_trustworthy_record(tmp_path):
    """Start, identify, prove health, observe governed work, restart, freeze.

    Every assertion is against the *frozen artifact*, not against live state:
    the question this answers is what a reviewer can determine days later from
    the file alone, with every process long gone.
    """
    db_path = tmp_path / "unattended.db"
    report_path = tmp_path / "evidence.json"
    run_id = new_run_id("acceptance")

    with UnattendedRun(str(db_path), run_id=run_id) as run:
        # 1-3. Start through the supported service path, under this run's
        #      identity, and confirm runtime and scheduler health from the
        #      runtime's own health surface.
        first_proc, first_port = _start(run, db_path, **TEST_RESOLVER_ENV)
        health = run.sample_health(first_port)
        assert health["health"]["components"]["scheduler"]["status"] == "ok"

        # 4. Observe an existing governed activity: inbound capture, which
        #    passes through Governance and records its own outcome.
        assert _post_inbound(first_port, "before-restart") == 202

        # Let the scheduler actually get round its loop a few times, so the
        # durable tick record has something in it on both sides of the restart.
        time.sleep(8)
        run.sample_health(first_port)

        # 5. A controlled restart: a real stop, then a real fresh process.
        run.stop(first_proc)
        second_proc, second_port = _start(run, db_path, **TEST_RESOLVER_ENV)
        run.sample_health(second_port)
        assert _post_inbound(second_port, "after-restart") == 202
        time.sleep(8)

        # 7. Terminate cleanly.
        run.stop(second_proc)

        # 8. Freeze.
        envelope = run.freeze(str(report_path))

    record = json.loads(report_path.read_text())["record"]

    # 2. The run is identifiable, and both incarnations belong to it.
    assert record["run_id"] == run_id
    incarnations = record["incarnations"]
    assert len(incarnations) == 2, incarnations
    assert all(i["run_id"] == run_id for i in incarnations)

    # 6. Pre- and post-restart evidence is not confused: two distinct runtime
    #    ids, and the record keeps them apart.
    runtime_ids = [i["runtime_id"] for i in incarnations]
    assert all(runtime_ids), "an incarnation recorded no runtime_id"
    assert len(set(runtime_ids)) == 2, "the restart reused a runtime id"

    # 7. Both stops were clean, and recorded by the processes themselves.
    assert [i["end_kind"] for i in incarnations] == [END_CLEAN, END_CLEAN]
    assert not any(i["end_inferred"] for i in incarnations)

    # 3. Health was demonstrated, from the runtime's own surface.
    samples = [o for o in record["observations"] if o["kind"] == "health_sample"]
    running = [
        s
        for s in samples
        if s["payload"].get("reachable")
        and s["payload"]["health"]["components"]["scheduler"]["status"] == "ok"
    ]
    assert len(running) >= 2, "no evidence of a healthy scheduler on both sides of the restart"

    # 4. Governed activity is in the record, on both sides of the restart, and
    #    each event lands in the incarnation that was actually running.
    inbound = record["sources"]["inbound_events"]
    assert inbound["available"] is True
    captured = {i["event_id"]: i for i in inbound["items"]}
    assert captured["before-restart"]["outcome"] == "captured"
    assert captured["after-restart"]["outcome"] == "captured"
    assert inbound["attributed"]["unattributed"] == 0
    assert inbound["attributed"]["per_incarnation"] == {
        str(incarnations[0]["id"]): 1,
        str(incarnations[1]["id"]): 1,
    }

    # 3 (durable half). Scheduler activity survived the restart and every tick
    #    is placed in a specific incarnation.
    #
    #    Not asserted per-incarnation: drive schedules are durable, so a drive
    #    that ran shortly before a restart is legitimately not due again
    #    shortly after one, and the second incarnation can honestly record
    #    zero ticks in a short window. Demanding otherwise would be testing
    #    the scheduler's cadence, not the evidence -- and the property that
    #    matters here is that nothing is unplaced or double-counted.
    ticks = record["sources"]["scheduler_ticks"]
    assert ticks["available"] is True
    per_incarnation = ticks["attributed"]["per_incarnation"]
    assert set(per_incarnation) == {str(i["id"]) for i in incarnations}
    assert ticks["attributed"]["unattributed"] == 0
    assert sum(per_incarnation.values()) == ticks["count"] > 0
    assert per_incarnation[str(incarnations[0]["id"])] > 0

    # 8. A reviewer can tell what happened, and the seal is meaningful.
    assert record["summary"]["complete"] is True
    assert record["summary"]["incarnation_count"] == 2
    assert record["sources"]["startup_incidents"]["count"] == 0
    assert record["sources"]["final_runtime_marker"]["clean"] is True

    from bartholomew.runtime.evidence_report import digest

    assert digest(record) == envelope["digest"], "the frozen digest does not seal its own record"


# ---------------------------------------------------------------------------
# The individual properties the scenario depends on
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_a_killed_process_is_recorded_as_lost_and_never_as_clean(tmp_path):
    """OP-W005 in one test: a run that ends by being killed still says so.

    This is the failure Test #1 actually hit -- the record could not
    distinguish "stopped" from "disappeared". After this, it can, and the
    inference is labelled as an inference.
    """
    db_path = tmp_path / "killed.db"
    run_id = new_run_id("killed")

    with UnattendedRun(str(db_path), run_id=run_id) as run:
        first_proc, first_port = _start(run, db_path)
        time.sleep(4)
        # SIGKILL: no shutdown hook runs, so nothing records an ending.
        run.kill(first_proc)

        # The next incarnation is what discovers and closes the gap.
        second_proc, _ = _start(run, db_path)
        run.stop(second_proc)

        record = run.freeze(str(tmp_path / "killed.json"))["record"]

    first, second = record["incarnations"]
    assert first["end_kind"] == END_LOST
    assert first["end_inferred"] is True
    assert second["end_kind"] == END_CLEAN
    assert second["end_inferred"] is False

    summary = record["summary"]
    assert summary["complete"] is False
    assert summary["lost_endings"] == 1
    assert "closed as lost" in summary["verdict"]


@pytest.mark.slow
def test_evidence_recording_is_off_unless_the_run_id_is_set(tmp_path):
    """A normal deployment gains no new writer and no new table."""
    import sqlite3

    db_path = tmp_path / "plain.db"
    port = _free_port()
    env = _base_env(db_path, port)
    env.pop("BARTH_UNATTENDED_RUN_ID", None)

    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "bartholomew", "serve"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        import urllib.request

        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"serve exited early: {proc.returncode}")
            try:
                with urllib.request.urlopen(  # noqa: S310
                    f"http://127.0.0.1:{port}/api/health",
                    timeout=5,
                ) as r:
                    if json.loads(r.read().decode())["components"]["runtime"]["state"] == "running":
                        break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise AssertionError("serve never became healthy")
    finally:
        proc.terminate()
        proc.wait(timeout=60)

    conn = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "unattended_run_incarnations" not in tables
    assert "unattended_run_observations" not in tables


@pytest.mark.slow
def test_a_runtime_failure_is_recorded_as_a_failed_ending(tmp_path):
    """A supervisor-visible fatal failure is a distinct, recorded ending.

    The existing escalation path (`bartholomew.runtime.supervision`) shuts the
    process down gracefully and exits non-zero. That shutdown *is* clean, so
    without this the record would read exactly like a deliberate stop -- and
    an unattended run that was cut short by a dying scheduler would look, in
    the evidence, like one that finished.
    """
    from bartholomew.runtime import supervision

    driver = tmp_path / "crashing_scheduler.py"
    driver.write_text(
        "\n".join(
            [
                "import asyncio, sys",
                "import bartholomew.kernel.scheduler.loop as loop_module",
                "async def _exploding_scheduler(ctx):",
                "    await asyncio.sleep(3)",
                "    raise RuntimeError('injected scheduler failure')",
                "loop_module.run_scheduler = _exploding_scheduler",
                "from bartholomew.runtime.serve import serve",
                "sys.exit(serve())",
            ],
        ),
    )

    db_path = tmp_path / "failed.db"
    run_id = new_run_id("failed")
    port = _free_port()
    env = _base_env(db_path, port, BARTH_UNATTENDED_RUN_ID=run_id)

    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(driver)],
        check=False,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=STARTUP_TIMEOUT + 60,
    )
    assert proc.returncode == supervision.EXIT_RUNTIME_FAILURE, proc.stderr[-3000:]

    incarnations = EvidenceStore(str(db_path)).incarnations(run_id)
    assert len(incarnations) == 1
    only = incarnations[0]
    assert only.end_kind == END_FAILED
    assert only.inferred is False
    assert "injected scheduler failure" in (only.end_detail or "")


@pytest.mark.slow
def test_a_failed_run_leaves_no_process_holding_the_database(tmp_path):
    """Cleanup is an evidence property, not tidiness.

    An orphaned `serve` keeps the kernel's process lock, so the *next* run
    cannot start -- and a run that cannot start produces no evidence at all.
    Leaving the context manager must therefore stop what it started even when
    the run itself blew up.
    """
    db_path = tmp_path / "orphans.db"

    class BoomError(RuntimeError):
        pass

    run = UnattendedRun(str(db_path), run_id=new_run_id("orphan"))
    proc = None
    with pytest.raises(BoomError):
        with run:
            proc, _ = _start(run, db_path)
            raise BoomError("the run failed part-way")

    assert proc is not None
    assert proc.poll() is not None, "the service was left running after the run failed"

    # And the database is genuinely free: a fresh run can take it.
    with UnattendedRun(str(db_path), run_id=new_run_id("after-orphan")) as second_run:
        second_proc, _ = _start(second_run, db_path)
        second_run.stop(second_proc)
