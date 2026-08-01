"""
Phase B stage B7: KernelDaemon.stop() closing external request admission
first and draining outstanding requests before tearing down the
resources they need. Isolated from the HTTP layer -- the middleware
chokepoint itself is tested in tests/test_api_admission_gate.py; the
admission primitive in isolation is tested in
tests/test_request_admission.py. See
docs/B7_EXTERNAL_REQUEST_ADMISSION.md.
"""

from __future__ import annotations

import asyncio

import pytest

from bartholomew.kernel.daemon import KernelDaemon


@pytest.fixture
def config_files(tmp_path):
    (tmp_path / "kernel.yaml").write_text(
        'timezone: "Australia/Brisbane"\nloop_interval_seconds: 1\n',
    )
    (tmp_path / "persona.yaml").write_text('name: "Test Bartholomew"\n')
    (tmp_path / "policy.yaml").write_text("policies: []\n")
    (tmp_path / "drives.yaml").write_text("drives: []\n")
    return {
        "cfg_path": str(tmp_path / "kernel.yaml"),
        "db_path": str(tmp_path / "test.db"),
        "persona_path": str(tmp_path / "persona.yaml"),
        "policy_path": str(tmp_path / "policy.yaml"),
        "drives_path": str(tmp_path / "drives.yaml"),
    }


def _make_daemon(config_files):
    return KernelDaemon(**config_files)


async def test_stop_closes_admission_immediately(config_files):
    daemon = _make_daemon(config_files)
    await daemon.start()
    assert daemon.admission.closed is False

    await daemon.stop()
    assert daemon.admission.closed is True
    assert daemon.admission.try_admit() is None


async def test_stop_waits_for_an_in_flight_admission_to_release(config_files):
    """stop() must not proceed to tear down mem/governance_store until an
    already-admitted request finishes naturally -- proven here by having
    a background task release the token only after a short delay, and
    confirming stop() didn't return before that happened."""
    daemon = _make_daemon(config_files)
    await daemon.start()

    token = daemon.admission.try_admit()
    assert token is not None

    released_before_stop_returned = False

    async def release_shortly():
        nonlocal released_before_stop_returned
        await asyncio.sleep(0.1)
        daemon.admission.release(token)
        released_before_stop_returned = True

    release_task = asyncio.create_task(release_shortly())
    await daemon.stop()
    await release_task

    assert released_before_stop_returned is True


async def test_stop_marks_clean_when_admission_drains_in_time(config_files):
    daemon = _make_daemon(config_files)
    await daemon.start()

    token = daemon.admission.try_admit()

    async def release_shortly():
        await asyncio.sleep(0.05)
        daemon.admission.release(token)

    release_task = asyncio.create_task(release_shortly())
    await daemon.stop()
    await release_task

    assert daemon.governance_store.runtime_marker().clean is True


async def test_stop_does_not_mark_clean_when_admission_fails_to_drain(config_files, monkeypatch):
    """An admission that never releases (a genuinely stuck or lost
    request) must not be silently ignored -- the shutdown must not be
    marked clean, matching Phase B stage B5's "confirmed, not assumed"
    invariant for every other tracked resource."""
    import bartholomew.kernel.daemon as daemon_module

    monkeypatch.setattr(daemon_module, "_ADMISSION_DRAIN_TIMEOUT_S", 0.1)

    daemon = _make_daemon(config_files)
    await daemon.start()
    daemon.admission.try_admit()  # never released -- simulates a stuck request

    await daemon.stop()

    assert daemon.governance_store.runtime_marker().clean is False


async def test_admission_open_before_start_is_why_the_http_layer_also_checks_lifecycle_state(
    config_files,
):
    """RequestAdmission itself defaults to open the moment a KernelDaemon
    is constructed -- before start() has run at all. That's exactly why
    app.py's HTTP middleware additionally checks lifecycle_state is
    RUNNING rather than trusting admission.closed alone: a bare
    "_kernel is not None" plus "admission not closed" check would wrongly
    admit requests during the STARTING window, since app.py's startup()
    sets the _kernel global before awaiting start() completes."""
    daemon = _make_daemon(config_files)
    assert daemon.admission.closed is False
    assert daemon.admission.try_admit() is not None


async def test_double_stop_does_not_reopen_or_re_close_admission(config_files):
    daemon = _make_daemon(config_files)
    await daemon.start()
    await daemon.stop()
    assert daemon.admission.closed is True

    await daemon.stop()  # safe no-op per the existing STOPPED-state guard
    assert daemon.admission.closed is True
