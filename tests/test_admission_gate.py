"""
Isolated tests for bartholomew.kernel.admission_gate (Phase B, stage B7).

Not HTTP-integration tests -- those live in
tests/test_api_admission_shutdown.py. These exercise AdmissionGate itself.
"""

from __future__ import annotations

import asyncio

import pytest

from bartholomew.kernel.admission_gate import AdmissionFrozenError, AdmissionGate


@pytest.mark.asyncio
async def test_admit_and_release_roundtrip():
    gate = AdmissionGate()
    token = await gate.admit("req-1")
    assert gate.in_flight_count == 1
    await token.release()
    assert gate.in_flight_count == 0


@pytest.mark.asyncio
async def test_multiple_admissions_tracked_independently():
    gate = AdmissionGate()
    t1 = await gate.admit("req-1")
    t2 = await gate.admit("req-2")
    assert gate.in_flight_count == 2

    await t1.release()
    assert gate.in_flight_count == 1

    await t2.release()
    assert gate.in_flight_count == 0


@pytest.mark.asyncio
async def test_release_is_idempotent():
    gate = AdmissionGate()
    token = await gate.admit("req-1")
    await token.release()
    await token.release()  # must not double-decrement
    assert gate.in_flight_count == 0


@pytest.mark.asyncio
async def test_release_only_affects_its_own_token():
    """Identity-bound release ownership: releasing one token must not
    affect another's admission slot."""
    gate = AdmissionGate()
    t1 = await gate.admit("req-1")
    await gate.admit("req-2")

    await t1.release()
    assert gate.in_flight_count == 1  # req-2's slot untouched


@pytest.mark.asyncio
async def test_admit_assigns_a_default_identity_when_none_given():
    gate = AdmissionGate()
    token = await gate.admit()
    assert token.identity  # non-empty, auto-generated

    other = await gate.admit()
    assert other.identity != token.identity


@pytest.mark.asyncio
async def test_freeze_rejects_new_admissions():
    gate = AdmissionGate()
    await gate.freeze()

    with pytest.raises(AdmissionFrozenError):
        await gate.admit("too-late")


@pytest.mark.asyncio
async def test_freeze_does_not_affect_already_admitted_work():
    gate = AdmissionGate()
    token = await gate.admit("in-flight")
    await gate.freeze()

    assert gate.in_flight_count == 1
    await token.release()  # still releasable after freeze
    assert gate.in_flight_count == 0


@pytest.mark.asyncio
async def test_freeze_is_idempotent():
    gate = AdmissionGate()
    await gate.freeze()
    await gate.freeze()
    assert gate.frozen is True


@pytest.mark.asyncio
async def test_drain_returns_true_immediately_when_nothing_in_flight():
    gate = AdmissionGate()
    assert await gate.drain(timeout=1.0) is True


@pytest.mark.asyncio
async def test_drain_waits_for_in_flight_work_to_release():
    gate = AdmissionGate()
    token = await gate.admit("slow-request")

    async def _release_later():
        await asyncio.sleep(0.1)
        await token.release()

    release_task = asyncio.create_task(_release_later())
    drained = await gate.drain(timeout=5.0)
    assert drained is True
    await release_task


@pytest.mark.asyncio
async def test_drain_times_out_and_reports_undrained():
    gate = AdmissionGate()
    await gate.admit("stuck-request")  # never released

    drained = await gate.drain(timeout=0.1)
    assert drained is False
    assert gate.in_flight_count == 1  # abandoned, not force-cleared


@pytest.mark.asyncio
async def test_drain_after_freeze_confirms_new_admissions_cannot_reopen_the_window():
    gate = AdmissionGate()
    token = await gate.admit("in-flight")
    await gate.freeze()

    async def _release_soon():
        await asyncio.sleep(0.05)
        await token.release()

    release_task = asyncio.create_task(_release_soon())
    assert await gate.drain(timeout=5.0) is True
    await release_task

    with pytest.raises(AdmissionFrozenError):
        await gate.admit("after-drain")


@pytest.mark.asyncio
async def test_concurrent_admissions_and_releases_stay_consistent():
    gate = AdmissionGate()
    tokens = [await gate.admit(f"req-{i}") for i in range(20)]
    assert gate.in_flight_count == 20

    await asyncio.gather(*(t.release() for t in tokens))
    assert gate.in_flight_count == 0
    assert await gate.drain(timeout=1.0) is True
