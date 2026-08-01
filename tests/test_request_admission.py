"""
Tests for bartholomew.kernel.request_admission (Phase B stage B7): the
identity-bound external-request admission primitive KernelDaemon.stop()
uses to drain in-flight requests before tearing down the resources they
need. Isolated from KernelDaemon/FastAPI -- daemon-level integration
(closing admission first in stop(), factoring drain success into the
clean-shutdown marker) is tested in tests/test_daemon_lifecycle_integrity
.py; the HTTP middleware chokepoint is tested in
tests/test_api_admission_gate.py.
"""

from __future__ import annotations

import asyncio

import pytest

from bartholomew.kernel.request_admission import RequestAdmission


def test_try_admit_returns_a_token():
    admission = RequestAdmission()
    token = admission.try_admit()
    assert token is not None
    assert admission.admitted_count == 1


def test_distinct_admissions_get_distinct_tokens():
    admission = RequestAdmission()
    first = admission.try_admit()
    second = admission.try_admit()
    assert first != second
    assert admission.admitted_count == 2


def test_release_removes_exactly_that_token():
    admission = RequestAdmission()
    first = admission.try_admit()
    second = admission.try_admit()

    admission.release(first)
    assert admission.admitted_count == 1

    admission.release(second)
    assert admission.admitted_count == 0


def test_release_with_foreign_token_does_not_affect_real_admissions():
    """Regression for the real prior-design finding docs/PHASE_B_RISK_MAP
    .md's B7 rows name: a release() with no identity argument could
    release any in-flight admission, not just the caller's own."""
    admission = RequestAdmission()
    real_token = admission.try_admit()

    admission.release("not-a-real-token")  # must not raise, must not affect real_token
    assert admission.admitted_count == 1

    admission.release(real_token)
    assert admission.admitted_count == 0


def test_release_is_idempotent_for_the_same_token():
    admission = RequestAdmission()
    token = admission.try_admit()
    admission.release(token)
    admission.release(token)  # must not raise
    assert admission.admitted_count == 0


def test_release_none_is_a_safe_noop():
    admission = RequestAdmission()
    admission.release(None)  # must not raise
    assert admission.admitted_count == 0


def test_close_refuses_new_admission():
    admission = RequestAdmission()
    admission.close()
    assert admission.try_admit() is None
    assert admission.admitted_count == 0


def test_close_does_not_affect_already_admitted_tokens():
    admission = RequestAdmission()
    token = admission.try_admit()
    admission.close()
    assert admission.admitted_count == 1
    admission.release(token)
    assert admission.admitted_count == 0


def test_close_is_idempotent():
    admission = RequestAdmission()
    admission.close()
    admission.close()  # must not raise
    assert admission.closed is True


async def test_drain_returns_true_immediately_when_nothing_admitted():
    admission = RequestAdmission()
    assert await admission.drain(timeout=1.0) is True


async def test_drain_returns_true_once_all_tokens_released():
    admission = RequestAdmission()
    token = admission.try_admit()

    async def release_shortly():
        await asyncio.sleep(0.05)
        admission.release(token)

    release_task = asyncio.create_task(release_shortly())
    drained = await admission.drain(timeout=2.0)
    await release_task
    assert drained is True


async def test_drain_returns_false_on_timeout_with_outstanding_admission():
    admission = RequestAdmission()
    admission.try_admit()  # never released
    drained = await admission.drain(timeout=0.1)
    assert drained is False
    assert admission.admitted_count == 1


@pytest.mark.parametrize("n", [1, 3, 5])
async def test_drain_waits_for_every_outstanding_token(n):
    admission = RequestAdmission()
    tokens = [admission.try_admit() for _ in range(n)]

    async def release_all_shortly():
        await asyncio.sleep(0.05)
        for t in tokens:
            admission.release(t)

    release_task = asyncio.create_task(release_all_shortly())
    drained = await admission.drain(timeout=2.0)
    await release_task
    assert drained is True
    assert admission.admitted_count == 0
