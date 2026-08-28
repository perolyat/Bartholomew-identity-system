"""
WP-A1 -- Queue Containment and Obligation-Safe Deduplication.

Deterministic proof for the two Test #1 queue failure modes recorded in the
Post-Test #1 Decision Register v2.2 (and propagated into `RISKS.md`,
`DECISIONS.md` and `ROADMAP.md`'s readiness bands), plus the directly
necessary part of D2 obligation preservation:

  B-F001     Self-sustaining queue growth: `drive_self_check` warned about a
             high pending-nudge count by creating another pending nudge, so
             the warning incremented the condition it reported. 49 new nudges
             (28 curiosity / 21 health) accumulated over one unattended run.
  NUDGE-F001 Semantically equivalent unresolved system-generated items
             (recurring curiosity prompts) persisting as separate rows.
  D2         Genuine obligations are never silently dropped; deferred,
             consolidated or suppressed items stay durably represented and
             auditable; `awaiting_response` remains the canonical durable
             external-reply obligation mechanism.
  S1         Queue/interruption containment (Band A gate).

The accelerated soak reproducing B-F001's *shape* lives in
tests/test_scheduler_queue_soak.py.

A note on what these tests refuse to accept as a pass: none of them clears
existing rows. Every assertion about a bounded queue is made against a queue
that still contains everything genuine that was ever put into it.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from bartholomew.kernel.awaiting_response_store import AwaitingResponseStore
from bartholomew.kernel.scheduler import containment
from bartholomew.kernel.scheduler import persistence as sp
from bartholomew.kernel.scheduler.health import (
    PENDING_NUDGE_DRIFT_THRESHOLD,
    SELF_CHECK_DRIFT_REASON,
    check_drift,
    get_system_metrics,
)
from bartholomew.kernel.scheduler.models import Nudge
from bartholomew.kernel.scheduler.store import SchedulerStore

CURIOSITY_ACTIONS = [
    {"label": "Reflect", "cmd": "open_journal"},
    {"label": "Later", "cmd": "dismiss"},
]


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "containment.db")
    sp.ensure_schema(path)
    return path


def _pending(db_path: str, kind: str | None = None) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if kind is None:
            return conn.execute(
                "SELECT * FROM nudges WHERE status='pending' ORDER BY id",
            ).fetchall()
        return conn.execute(
            "SELECT * FROM nudges WHERE status='pending' AND kind=? ORDER BY id",
            (kind,),
        ).fetchall()
    finally:
        conn.close()


def _all_nudges(db_path: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM nudges ORDER BY id").fetchall()
    finally:
        conn.close()


def _emit_curiosity(db_path: str, ts: int, message: str = "How are you feeling right now?"):
    return sp.insert_nudge_contained(
        db_path,
        "curiosity",
        message,
        CURIOSITY_ACTIONS,
        "curiosity_probe",
        ts,
    )


def _emit_health(db_path: str, ts: int, drift: str = "high_pending_nudges:37"):
    return sp.insert_nudge_contained(
        db_path,
        "system_health",
        f"System drift detected: {drift}",
        [],
        SELF_CHECK_DRIFT_REASON,
        ts,
    )


# ---------------------------------------------------------------------------
# 1. No recursive queue-health growth (B-F001, requirement A)
# ---------------------------------------------------------------------------


class TestNoRecursiveQueueHealthGrowth:
    """Requirement A: repeated self-check execution above the pending
    threshold must not create unresolved queue-health entries whose own
    existence increments the same condition being warned about -- while the
    underlying condition stays observable."""

    def test_health_warnings_are_excluded_from_their_own_trigger_count(self, db_path):
        # A genuine backlog above the threshold, none of it self-check output.
        genuine = PENDING_NUDGE_DRIFT_THRESHOLD + 1
        for i in range(genuine):
            sp.insert_nudge(db_path, "task", f"genuine obligation {i}", [], "user_task", 1000 + i)

        # Self-check fires repeatedly, each time emitting its drift warning.
        for tick in range(25):
            metrics = get_system_metrics(db_path)
            drift = check_drift(metrics)
            assert drift is not None, "the genuine backlog must remain reported"
            _emit_health(db_path, 2000 + tick, drift)

        health_rows = _pending(db_path, "system_health")
        assert len(health_rows) == 1, (
            "25 self-check executions above the threshold must leave exactly one "
            f"unresolved queue-health item, found {len(health_rows)}"
        )

        # The health item's own existence must not have moved the count the
        # warning is derived from.
        metrics = get_system_metrics(db_path)
        assert metrics["pending_nudges"] == genuine + 1  # true total, observable
        assert metrics["pending_nudges_self_generated"] == 1
        assert metrics["pending_nudges_countable"] == genuine
        assert check_drift(metrics) == f"high_pending_nudges:{genuine}"

        # No genuine item was touched to achieve this.
        assert len(_pending(db_path, "task")) == genuine

    def test_health_warning_alone_never_manufactures_drift(self, db_path):
        """The pure B-F001 loop: an empty queue plus repeated self-checks. If
        health items counted toward their own trigger, a large enough burst
        would eventually cross the threshold on its own."""
        for tick in range(200):
            metrics = get_system_metrics(db_path)
            drift = check_drift(metrics)
            if drift:
                _emit_health(db_path, 3000 + tick, drift)

        assert (
            _pending(db_path) == []
        ), "self-check must not generate queue pressure out of an empty queue"
        assert get_system_metrics(db_path)["pending_nudges"] == 0

    def test_drift_is_keyed_on_class_not_on_the_volatile_measurement(self, db_path):
        """Keying equivalence on the whole message would make every changed
        pending count a 'new' warning -- exactly B-F001's unbounded growth."""
        for i, count in enumerate([21, 22, 37, 51, 55]):
            _emit_health(db_path, 4000 + i, f"high_pending_nudges:{count}")

        assert len(_pending(db_path, "system_health")) == 1
        assert containment.dedup_key_for(
            "system_health",
            "System drift detected: high_pending_nudges:21",
            SELF_CHECK_DRIFT_REASON,
        ) == containment.dedup_key_for(
            "system_health",
            "System drift detected: high_pending_nudges:55",
            SELF_CHECK_DRIFT_REASON,
        )

    def test_distinct_drift_classes_stay_distinct(self, db_path):
        """Containment of the feedback loop must not collapse genuinely
        different health conditions into one another."""
        _emit_health(db_path, 5000, "high_pending_nudges:37")
        _emit_health(db_path, 5001, "stale_daily_reflection:41h")
        _emit_health(db_path, 5002, "database_unreachable")

        kinds = {json.dumps(r["message"]) for r in _pending(db_path, "system_health")}
        assert len(kinds) == 3, "three different drift classes are three conditions"


# ---------------------------------------------------------------------------
# 2 & 3. Equivalent duplicates / re-representation after resolution
# ---------------------------------------------------------------------------


class TestNoEquivalentUnresolvedDuplicates:
    """Requirement B / NUDGE-F001."""

    def test_repeated_curiosity_emissions_leave_exactly_one_unresolved_item(self, db_path):
        outcomes = [_emit_curiosity(db_path, 1000 + i) for i in range(40)]

        pending = _pending(db_path, "curiosity")
        assert (
            len(pending) == 1
        ), f"expected exactly 1 unresolved curiosity item, got {len(pending)}"
        assert outcomes[0]["outcome"] == sp.OUTCOME_INSERTED
        assert all(o["outcome"] == sp.OUTCOME_SUPPRESSED_DUPLICATE for o in outcomes[1:])

    def test_rotating_prompt_wording_is_one_obligation_not_three(self, db_path):
        """Test #1's curiosity prompts rotate between three fixed strings.
        They are presentation of one open invitation, not three commitments."""
        for i, text in enumerate(
            [
                "What's one thing you learned today?",
                "How are you feeling right now?",
                "Any highlights from today worth remembering?",
            ]
            * 5,
        ):
            _emit_curiosity(db_path, 6000 + i, text)

        assert len(_pending(db_path, "curiosity")) == 1

    def test_suppression_leaves_machine_inspectable_evidence(self, db_path):
        """Requirement D: any suppression must leave inspectable evidence of
        what happened and why."""
        first = _emit_curiosity(db_path, 7000)
        for i in range(1, 6):
            _emit_curiosity(db_path, 7000 + i)

        events = sp.list_containment_events(db_path)
        assert len(events) == 5
        for ev in events:
            assert ev["outcome"] == sp.OUTCOME_SUPPRESSED_DUPLICATE
            assert ev["existing_nudge_id"] == first["nudge_id"]
            assert ev["dedup_key"] == first["dedup_key"]
            assert ev["reason"] == "curiosity_probe"
            assert ev["detail"]["suppressed_message"]

        # ... and the emission frequency is not hidden by the containment.
        row = _pending(db_path, "curiosity")[0]
        assert row["occurrence_count"] == 6
        assert row["last_occurrence_ts"] == 7005

    def test_an_explicit_escalation_is_a_distinct_item(self, db_path):
        """Requirement B's carve-out: containment applies to an ordinary
        repeat firing, not to an explicitly distinct escalation."""
        sp.insert_nudge_contained(
            db_path,
            "curiosity",
            "How are you feeling right now?",
            CURIOSITY_ACTIONS,
            "curiosity_probe",
            8000,
        )
        escalated = sp.insert_nudge_contained(
            db_path,
            "curiosity",
            "You have not checked in for three days.",
            CURIOSITY_ACTIONS,
            "curiosity_probe",
            8001,
            "three_day_silence",
        )
        assert escalated["outcome"] == sp.OUTCOME_INSERTED
        assert len(_pending(db_path, "curiosity")) == 2

        # ... and the escalation itself is then contained on repeat.
        for i in range(5):
            sp.insert_nudge_contained(
                db_path,
                "curiosity",
                "You have not checked in for three days.",
                CURIOSITY_ACTIONS,
                "curiosity_probe",
                8002 + i,
                "three_day_silence",
            )
        assert len(_pending(db_path, "curiosity")) == 2


class TestResolvedWorkMayBeRepresentedAgain:
    """Required test 3: containment defers a repeat, it does not permanently
    silence a recurring item."""

    @pytest.mark.parametrize("resolution", ["acked", "dismissed"])
    def test_resolved_item_frees_the_key_for_a_later_occurrence(self, db_path, resolution):
        first = _emit_curiosity(db_path, 9000)
        assert _emit_curiosity(db_path, 9001)["outcome"] == sp.OUTCOME_SUPPRESSED_DUPLICATE

        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE nudges SET status=?, acted_ts=? WHERE id=?",
                (resolution, "2026-08-21T00:00:00+00:00", first["nudge_id"]),
            )
            conn.commit()
        finally:
            conn.close()

        later = _emit_curiosity(db_path, 9002)
        assert later["outcome"] == sp.OUTCOME_INSERTED
        assert later["nudge_id"] != first["nudge_id"]
        assert len(_pending(db_path, "curiosity")) == 1
        # The resolved one is still on the record -- nothing was rewritten.
        assert len(_all_nudges(db_path)) == 2

    def test_resolving_the_health_item_lets_a_later_drift_reappear(self, db_path):
        first = _emit_health(db_path, 9100)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE nudges SET status='dismissed' WHERE id=?", (first["nudge_id"],))
            conn.commit()
        finally:
            conn.close()

        assert _emit_health(db_path, 9200)["outcome"] == sp.OUTCOME_INSERTED


# ---------------------------------------------------------------------------
# 4 & 6. Genuine obligations are preserved (D2, requirement C)
# ---------------------------------------------------------------------------


class TestDistinctObligationsStayDistinct:
    """Required test 4."""

    def test_similar_wording_in_a_non_eligible_kind_is_never_merged(self, db_path):
        """Two user obligations that read almost identically are two
        obligations. Containment is an explicit allowlist keyed on `reason`,
        so neither is even a candidate."""
        for i in range(2):
            out = sp.insert_nudge_contained(
                db_path,
                "task",
                "Reply to the landlord about the lease",
                [],
                "user_task",
                10000 + i,
            )
            assert out["outcome"] == sp.OUTCOME_INSERTED_NOT_ELIGIBLE
            assert out["dedup_key"] is None

        assert len(_pending(db_path, "task")) == 2
        assert sp.list_containment_events(db_path) == []

    def test_same_reason_under_a_different_kind_does_not_collide(self, db_path):
        a = containment.dedup_key_for("curiosity", "x", "curiosity_probe")
        b = containment.dedup_key_for("system_health", "x", "curiosity_probe")
        assert a is not None and b is not None and a != b

    def test_policy_eligibility_is_an_allowlist_not_a_default(self):
        # Exact set, deliberately: the allowlist growing is a decision, and it
        # should have to be made here as well as in containment.py. The third
        # entry is Usable POC slice 2's schedule reminders, approved
        # 2026-08-25 (docs/POC_SLICE_2_PROACTIVE_REMINDERS.md) -- recurring,
        # system-generated, user-facing material of exactly the kind WP-A1's
        # policy exists for. The property this test is really about is
        # unchanged and is asserted below: anything NOT listed is ineligible.
        assert containment.eligible_reasons() == frozenset(
            {
                "curiosity_probe",
                SELF_CHECK_DRIFT_REASON,
                "schedule_reminder",
                # Golden Path slice 2. Added here consciously, which is the
                # point of pinning the allowlist: a drive does not get
                # containment by writing a new reason string, only by being
                # named in both places.
                "objective_reengagement",
            },
        )
        for reason in [None, "", "user_task", "awaiting_response", "notify", "reminder"]:
            assert not containment.is_policy_eligible(reason)
            assert containment.dedup_key_for("anything", "anything", reason) is None


class TestContainmentNeverDestroysAnObligation:
    """Required test 6 / requirement C+D: the design has no delete, no
    auto-resolve and no cap-shedding path at all -- the system-generated
    queue is bounded by the finite set of equivalence keys instead."""

    def test_no_containment_code_path_removes_or_resolves_a_nudge(self):
        import inspect

        source = inspect.getsource(sp.insert_nudge_contained) + inspect.getsource(containment)
        lowered = source.lower()
        for forbidden in ("delete from nudges", "drop table", "set status"):
            assert forbidden not in lowered, f"containment must never {forbidden!r}"

    def test_a_heavy_system_generated_burst_leaves_every_genuine_row_untouched(self, db_path):
        genuine_ids = []
        for i in range(30):
            genuine_ids.append(
                sp.insert_nudge(db_path, "task", f"obligation {i}", [], "user_task", 11000 + i),
            )
        before = {r["id"]: dict(r) for r in _all_nudges(db_path)}

        for i in range(500):
            _emit_curiosity(db_path, 12000 + i)
            _emit_health(db_path, 12000 + i)

        after = {r["id"]: dict(r) for r in _all_nudges(db_path)}
        for gid in genuine_ids:
            assert gid in after, "a genuine obligation disappeared"
            assert after[gid] == before[gid], "a genuine obligation was mutated"

        # 1000 system-generated emissions, two surviving unresolved items.
        assert len(_pending(db_path, "curiosity")) == 1
        assert len(_pending(db_path, "system_health")) == 1
        assert len(_pending(db_path, "task")) == 30

    def test_capacity_is_bounded_by_identity_and_never_by_shedding(self, db_path):
        for i in range(300):
            _emit_curiosity(db_path, 13000 + i)
        # Every suppression is accounted for; nothing vanished silently.
        events = sp.list_containment_events(db_path, limit=1000)
        assert len(events) == 299
        assert _pending(db_path, "curiosity")[0]["occurrence_count"] == 300


# ---------------------------------------------------------------------------
# 5. awaiting_response is untouched (D2, requirement C)
# ---------------------------------------------------------------------------


class TestAwaitingResponseSurvivesContainment:
    """Required test 5. `awaiting_response` is a separate table under a
    separate authority (docs/S1_4_AWAITING_RESPONSE_DESIGN.md); its exemption
    from generic expiry/cap shedding is structural, because containment has
    no expiry or shedding to be exempt from."""

    def test_open_obligations_are_bit_for_bit_unchanged(self, db_path):
        store = AwaitingResponseStore(db_path)
        entries = [
            store.open(subject=f"reply about {topic}", origin_surface="chat", actor="taylor")
            for topic in ("the lease", "the invoice", "the lease")  # deliberately similar
        ]
        before = [e.to_dict() for e in entries]

        for i in range(400):
            _emit_curiosity(db_path, 14000 + i)
            _emit_health(db_path, 14000 + i)

        after = [store.get(e.id).to_dict() for e in entries]
        assert after == before
        assert (
            len(store.list(status="open")) == 3
        ), "similar-sounding external-reply obligations must remain distinct"

    def test_containment_writes_nothing_to_the_awaiting_response_tables(self, db_path):
        store = AwaitingResponseStore(db_path)
        store.open(subject="reply about the lease", origin_surface="chat")

        conn = sqlite3.connect(db_path)
        try:
            audit_before = conn.execute(
                "SELECT COUNT(*) FROM awaiting_response_audit",
            ).fetchone()[0]
        finally:
            conn.close()

        for i in range(50):
            _emit_curiosity(db_path, 15000 + i)

        conn = sqlite3.connect(db_path)
        try:
            assert (
                conn.execute("SELECT COUNT(*) FROM awaiting_response_audit").fetchone()[0]
                == audit_before
            )
            assert conn.execute("SELECT COUNT(*) FROM awaiting_response").fetchone()[0] == 1
        finally:
            conn.close()

    def test_awaiting_response_reason_is_not_policy_eligible(self):
        for reason in ("awaiting_response", "awaiting_response_check", "reminder", "escalation"):
            assert not containment.is_policy_eligible(reason)


# ---------------------------------------------------------------------------
# 7. Failure semantics (requirement E)
# ---------------------------------------------------------------------------


class TestPersistenceFailureIsVisibleAndSafe:
    """Required test 7. A read/write failure during containment evaluation
    must never be resolved into 'already represented' or 'disposable'."""

    def test_a_failing_database_raises_rather_than_dropping_the_item(self, tmp_path):
        missing = str(tmp_path / "no-schema.db")
        sqlite3.connect(missing).close()  # exists, but has no nudges table
        with pytest.raises(sqlite3.Error):
            _emit_curiosity(missing, 16000)

    def test_a_locked_database_raises_rather_than_dropping_the_item(self, db_path, monkeypatch):
        def boom(*_a, **_k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(sp, "wal_db", boom)
        with pytest.raises(sqlite3.OperationalError):
            _emit_curiosity(db_path, 16100)

    def test_a_failure_after_the_conflict_rolls_the_whole_decision_back(self, db_path):
        """If the evidence write fails, the suppression is not committed
        half-way: no orphan counter bump, no missing evidence row."""
        _emit_curiosity(db_path, 16200)
        before_count = _pending(db_path, "curiosity")[0]["occurrence_count"]

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("ALTER TABLE nudge_containment_events RENAME TO _moved")
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(sqlite3.Error):
            _emit_curiosity(db_path, 16201)

        assert _pending(db_path, "curiosity")[0]["occurrence_count"] == before_count

    async def test_scheduler_loop_records_a_persistence_failure_as_a_failed_tick(
        self,
        db_path,
        monkeypatch,
        caplog,
    ):
        """The scheduler loop used to wrap this in contextlib.suppress, so a
        dropped item was indistinguishable from no item at all."""
        import logging

        from bartholomew.kernel.scheduler import loop as loop_mod

        store = SchedulerStore(db_path)
        ctx = _MinimalCtx(db_path, store)

        async def failing(*_a, **_k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(store, "insert_nudge_contained", failing)

        recorded: dict = {}

        async def capture_tick(task_id, started, finished, success, key, meta):
            recorded.update({"success": success, "meta": meta})
            return 1

        monkeypatch.setattr(store, "insert_tick", capture_tick)
        monkeypatch.setattr(store, "tick_exists", _false)
        monkeypatch.setattr(store, "update_next_run", _noop)
        monkeypatch.setattr(store, "upsert_scheduled_tasks", _noop)
        monkeypatch.setattr(store, "ensure_schema", _noop_no_args)
        monkeypatch.setattr(store, "next_due_task", _one_curiosity_task_then_stop())

        with caplog.at_level(logging.ERROR, logger=loop_mod.log.name):
            with pytest.raises(_StopLoop):
                await loop_mod.run_scheduler(ctx)

        assert recorded["success"] == 0, "a tick whose nudge was lost must not report success"
        assert recorded["meta"]["nudge"]["outcome"] == "persistence_failed"
        assert "database is locked" in recorded["meta"]["nudge"]["error"]
        assert any("Nudge persistence FAILED" in r.message for r in caplog.records)

        await store.close()


class _StopLoop(BaseException):
    """Escapes run_scheduler's `except Exception` guard to end the test loop."""


class _MinimalCtx:
    """The duck-typed scheduler context the existing scheduler tests use."""

    def __init__(self, db_path: str, store: SchedulerStore):
        self.mem = type("Mem", (), {"db_path": db_path})()
        self.scheduler_store = store


async def _noop(*_a, **_k):
    return None


async def _noop_no_args():
    return None


async def _false(*_a, **_k):
    return False


def _one_curiosity_task_then_stop():
    state = {"served": False}

    async def next_due_task(_now_ts):
        if state["served"]:
            raise _StopLoop
        state["served"] = True
        return {
            "id": "curiosity_probe",
            "cadence": "every:900",
            "next_run_ts": 1000,
            "last_run_ts": None,
            "window_state": None,
        }

    return next_due_task


# ---------------------------------------------------------------------------
# 8. Existing semantics preserved (requirement F)
# ---------------------------------------------------------------------------


class TestExistingSemanticsPreserved:
    def test_non_eligible_persistence_matches_the_pre_wpa1_row_exactly(self, db_path):
        """`insert_nudge_contained` on non-eligible material must write the
        same row `insert_nudge` always did."""
        legacy_id = sp.insert_nudge(db_path, "task", "do the thing", [{"a": 1}], "user_task", 17000)
        new = sp.insert_nudge_contained(
            db_path,
            "task",
            "do the thing",
            [{"a": 1}],
            "user_task",
            17000,
        )

        rows = {r["id"]: dict(r) for r in _all_nudges(db_path)}
        legacy, contained = rows[legacy_id], rows[new["nudge_id"]]
        for column in (
            "kind",
            "message",
            "actions",
            "reason",
            "created_ts",
            "created_ts_s",
            "status",
        ):
            assert legacy[column] == contained[column]
        assert contained["dedup_key"] is None

    async def test_parking_brake_still_blocks_the_scheduler_before_any_persistence(self, db_path):
        """Governance stays above Executive: an engaged brake still raises out
        of the drive seam, so the containment path -- which is downstream of
        it -- never runs at all and nothing reaches the queue."""
        from bartholomew.kernel.scheduler.drives import drive_curiosity_probe
        from bartholomew.kernel.scheduler.loop import _run_drive
        from bartholomew.orchestrator.safety.parking_brake import BrakeStorage, ParkingBrake

        store = SchedulerStore(db_path)
        ctx = _MinimalCtx(db_path, store)
        brake = ParkingBrake(BrakeStorage(db_path))
        brake.engage("scheduler")
        try:
            with pytest.raises(RuntimeError, match="ParkingBrake: scheduler blocked"):
                await _run_drive(ctx, "curiosity_probe", drive_curiosity_probe)
        finally:
            brake.disengage()
            await store.close()

        assert _pending(db_path) == []
        assert sp.list_containment_events(db_path) == []

    def test_self_maintenance_drive_exemption_is_unchanged(self):
        from bartholomew.kernel.runtime_contract import _SELF_MAINTENANCE_DRIVES

        assert _SELF_MAINTENANCE_DRIVES == frozenset(
            {"self_check", "curiosity_probe", "reflection_micro", "fts_optimize"},
        )

    def test_nudge_model_is_backwards_compatible(self):
        n = Nudge(kind="curiosity", message="m", actions=[], reason="curiosity_probe", created_ts=1)
        assert n.escalation is None
        assert n.to_dict()["escalation"] is None

    def test_ensure_schema_is_idempotent_and_installs_the_invariant(self, db_path):
        sp.ensure_schema(db_path)
        sp.ensure_schema(db_path)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            indexes = {r["name"]: r for r in conn.execute("PRAGMA index_list(nudges)")}
        finally:
            conn.close()
        invariant = indexes.get("idx_nudges_dedup_pending")
        assert invariant is not None, "the NUDGE-F001 invariant index is missing"
        assert invariant["unique"] == 1
        assert invariant["partial"] == 1
