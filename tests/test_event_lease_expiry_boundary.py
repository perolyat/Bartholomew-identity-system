"""A claim lease lasts at least as long as it was asked for.

Why this exists. `claim_batch()` takes `now = int(time.time())` and stores
`lease_expires_ts = now + lease_seconds`; a later pass recovers expired claims
by comparing that against its own, separately truncated `int(time.time())`.
Both ends are whole seconds, and the two calls do not happen at the same
sub-second phase. While the recovery comparison was `<=`, a lease was released
as soon as the second counter *reached* its expiry -- so a claim taken at
`T.996` was already expired 5 ms later at `T+1.001`, and an `N`-second lease
really lasted somewhere in `(N-1, N]`.

That is this module's only mutual exclusion: a lease released the moment after
it is granted lets two passes hold one event at once. It surfaced as an
intermittent failure of
`test_event_backbone_processing.py::test_a_crash_after_claiming_loses_nothing_
and_duplicates_nothing`, which claims with `lease_seconds=1` and asserts that
an immediate second pass claims nothing.

These tests drive the clock rather than hoping for a phase, so they fail
deterministically against the old comparison and pass deterministically
against the new one.
"""

from __future__ import annotations

import time

import pytest

from bartholomew.kernel.event_processing import store


def _seed(db_path: str, *, event_id: str = "evt-1") -> None:
    """One captured event, inserted directly: these tests are about the lease,
    not about the capture seam."""
    with store._connect(db_path, "lease_boundary_seed") as conn:
        conn.executescript(store.SCHEMA)
        conn.execute(
            "INSERT INTO event_processing ("
            " envelope_version, source_id, event_id, event_type, payload_sha256,"
            " received_at, received_ts, enqueued_at, state, attempts"
            ") VALUES (1, 'src', ?, 'observation.note', 'sha', "
            "'2026-01-01T00:00:00Z', 1, '2026-01-01T00:00:00Z', ?, 0)",
            (event_id, store.STATE_CAPTURED),
        )
        conn.commit()


def _claim(db_path: str, *, at: int | None, lease_seconds: int = 1):
    return store.claim_batch(
        db_path,
        runtime_id=None,
        limit=1,
        lease_seconds=lease_seconds,
        max_attempts=3,
        now_ts=at,
    )


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "events.db")
    _seed(path)
    return path


class TestALeaseLastsAtLeastAsLongAsAsked:
    """`now_ts` lets these state the phase exactly instead of racing it."""

    def test_the_expiry_second_still_belongs_to_the_holder(self, db):
        """The whole defect, in one assertion.

        A claim at second `T` with `lease_seconds=1` stores an expiry of
        `T + 1`. A pass whose own truncated clock reads `T + 1` is somewhere in
        the second that lease was granted *for* -- as little as a millisecond
        after it was taken. It must not recover the claim.
        """
        first = _claim(db, at=1000)
        assert len(first) == 1

        second = _claim(db, at=1001)
        assert second == [], (
            "the lease was released during the second it was granted for; "
            "an N-second lease must not be able to last less than N seconds"
        )
        assert store.get(db, "src", "evt-1").state == store.STATE_CLAIMED

    def test_the_lease_is_still_recovered_once_it_is_genuinely_past(self, db):
        """The other half: erring long must not mean never."""
        assert len(_claim(db, at=1000)) == 1
        recovered = _claim(db, at=1002)
        assert len(recovered) == 1, "a genuinely expired lease must be recovered"
        assert recovered[0].event_id == "evt-1"

    @pytest.mark.parametrize("lease_seconds", [1, 2, 5, 30])
    def test_no_lease_length_can_expire_early(self, tmp_path, lease_seconds):
        path = str(tmp_path / f"events_{lease_seconds}.db")
        _seed(path)
        assert len(_claim(path, at=1000, lease_seconds=lease_seconds)) == 1
        # Every whole second strictly inside the requested window is held.
        for offset in range(1, lease_seconds + 1):
            assert (
                _claim(path, at=1000 + offset, lease_seconds=lease_seconds) == []
            ), f"a {lease_seconds}s lease was released at +{offset}s"
        assert len(_claim(path, at=1000 + lease_seconds + 1, lease_seconds=lease_seconds)) == 1

    def test_the_recovered_claim_keeps_its_spent_attempt(self, db):
        """Unchanged by this fix, asserted so the fix cannot quietly alter it:
        a crash-loop stays bounded."""
        assert len(_claim(db, at=1000)) == 1
        recovered = _claim(db, at=1002)
        assert recovered[0].attempts == 2, "the spent attempt must not be refunded"


class TestTheRaceItRepairs:
    """The defect as it actually presented: real wall-clock, real phase."""

    def test_a_claim_taken_at_a_second_boundary_survives_the_next_pass(self, tmp_path):
        """Busy-waits to just before a second boundary, so the claim and the
        immediate recheck straddle it. Against the old `<=` comparison this
        failed on roughly one run in six; against `<` it cannot fail.
        """
        losses = []
        trials = 12
        for n in range(trials):
            path = str(tmp_path / f"boundary_{n}.db")
            _seed(path)

            deadline = int(time.time()) + 1 - 0.004
            while time.time() < deadline:  # busy-wait: sleep() is too coarse here
                pass

            taken = time.time()
            assert len(_claim(path, at=None)) == 1
            if _claim(path, at=None):
                losses.append((taken - int(taken), time.time() % 1))

        assert losses == [], (
            f"{len(losses)}/{trials} leases were released by the very next call; "
            f"(claim, recheck) sub-second phases: {losses}"
        )
