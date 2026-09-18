"""An `N`-second claim lease lasts `N` seconds -- not more, and not less.

Why this exists. A lease has two clock readings: one when it is granted
(`lease_expires_ts = now + lease_seconds`) and one, in a later pass, when it is
tested for expiry. Both used to be truncated with `int()`, and truncation
throws away the sub-second phase at which each call happened -- so the two
readings were not measuring the same thing, and a `lease_seconds=N` lease had
no reliable duration in either direction:

  * **Too short.** A claim taken at `T.996` stored `T+1` and was already
    expired 5 ms later, when the second counter reached `T+1`. Measured before
    the fix: 10 of 60 claims deliberately aligned to 4 ms before a second
    boundary were released by the very next call, each showing the claim at
    fraction `.996` and the recheck at `.001` of the next second. Away from the
    boundary it never happened -- which is why it presented as an intermittent
    test failure rather than as the defect it is.
  * **Too long.** A claim taken at `T.004` was held for almost a full second
    beyond the `N` asked for.

The first is the one that matters: a claim is this module's only mutual
exclusion, so a lease released the moment after it is granted lets two passes
hold one event at once. It presented as an intermittent failure of
`test_event_backbone_processing.py::
test_a_crash_after_claiming_loses_nothing_and_duplicates_nothing`.

Both ends are asserted here, because fixing only one end moves the defect
rather than removing it. Making the expiry comparison strict stops the early
expiry but makes an `N`-second lease last up to `N+1` seconds, which breaks
every caller that waits `N + 0.1` for a recovery -- there are four in this
repository. `TestALeaseIsNotTooLong` is what fails if someone reaches for that.
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


def _claim(db_path: str, *, at: float | None, lease_seconds: int = 1):
    """`now_ts` is what makes these deterministic: the phase is stated, not
    raced. Every `at` below is an exact instant the old clock would have
    rounded away."""
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


class TestALeaseIsNotTooShort:
    def test_a_lease_is_held_for_every_instant_inside_its_window(self, db):
        """The defect, stated at the phase that used to trigger it.

        A claim at `T.996` with `lease_seconds=1` must still be held 5 ms
        later. Under the old whole-second clock it stored `T+1`, and the
        recheck's own truncated clock also read `T+1`, so it was recovered.
        """
        assert len(_claim(db, at=1000.996)) == 1
        assert _claim(db, at=1001.001) == [], (
            "the lease was released 5 ms after it was granted; an N-second "
            "lease must never last less than N seconds"
        )
        assert store.get(db, "src", "evt-1").state == store.STATE_CLAIMED

    @pytest.mark.parametrize("phase", [0.0, 0.004, 0.5, 0.9, 0.996, 0.9999])
    def test_no_sub_second_phase_can_shorten_a_lease(self, tmp_path, phase):
        """The whole defect was that the answer depended on this argument."""
        path = str(tmp_path / f"phase_{phase}.db")
        _seed(path)
        assert len(_claim(path, at=1000 + phase)) == 1
        assert (
            _claim(path, at=1000 + phase + 0.999) == []
        ), f"a 1s lease taken at phase {phase} did not last 1s"

    @pytest.mark.parametrize("lease_seconds", [1, 2, 5, 30])
    def test_no_lease_length_can_expire_early(self, tmp_path, lease_seconds):
        path = str(tmp_path / f"events_{lease_seconds}.db")
        _seed(path)
        assert len(_claim(path, at=1000.996, lease_seconds=lease_seconds)) == 1
        for offset in range(1, lease_seconds + 1):
            at = 1000.996 + offset - 0.001
            assert (
                _claim(path, at=at, lease_seconds=lease_seconds) == []
            ), f"a {lease_seconds}s lease was released {offset - 0.001}s in"


class TestALeaseIsNotTooLong:
    """The other end, and the reason this is a clock change rather than a
    stricter comparison. A strict comparison satisfies every assertion in the
    class above and still fails these: the guarantee would move from one end to
    the other, and the four `lease_seconds=1` callers in this repository that
    wait 1.1 s for a recovery would hold a dead process's work for longer."""

    def test_the_lease_is_recovered_once_it_is_genuinely_past(self, db):
        assert len(_claim(db, at=1000.5)) == 1
        recovered = _claim(db, at=1001.6)
        assert len(recovered) == 1, "a genuinely expired lease must be recovered"
        assert recovered[0].event_id == "evt-1"

    @pytest.mark.parametrize("phase", [0.0, 0.004, 0.5, 0.9, 0.996])
    def test_a_one_second_lease_is_always_recoverable_after_1_1_seconds(
        self,
        tmp_path,
        phase,
    ):
        """The exact pattern four existing tests rely on, at every phase."""
        path = str(tmp_path / f"long_{phase}.db")
        _seed(path)
        assert len(_claim(path, at=1000 + phase)) == 1
        assert (
            len(_claim(path, at=1000 + phase + 1.1)) == 1
        ), f"a 1s lease taken at phase {phase} was not recoverable after 1.1s"

    def test_the_recovered_claim_keeps_its_spent_attempt(self, db):
        """Unchanged by this fix, asserted so the fix cannot quietly alter it:
        a crash-loop stays bounded."""
        assert len(_claim(db, at=1000.5)) == 1
        assert _claim(db, at=1001.6)[0].attempts == 2, "the spent attempt was refunded"


class TestTheRaceItRepairs:
    """The defect as it actually presented: real wall-clock, real phase."""

    def test_a_claim_taken_at_a_second_boundary_survives_the_next_pass(self, tmp_path):
        """Busy-waits to just before a second boundary, so the claim and the
        immediate recheck straddle it. Against the old whole-second clock this
        failed on roughly one run in six; against a real-seconds clock it
        cannot fail.
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
