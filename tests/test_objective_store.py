"""
The durable objective record and its append-only history.

What matters here is not that a table exists. It is that four properties hold
against real SQLite, because the slice's promises rest on them:

  * an objective survives the process that created it;
  * completion and abandonment are genuinely terminal -- every further
    transition refuses, so nothing can resurface a finished objective;
  * a `proposal` can never be read as evidence;
  * "what changed since last time" is derived from the events, so it cannot
    drift from what actually happened.
"""

from __future__ import annotations

import sqlite3

import pytest

from bartholomew.kernel.objective_store import (
    EVENT_ACTION,
    EVENT_DECISION,
    EVENT_FACT,
    EVENT_PROPOSAL,
    EVENT_STATE_CHANGE,
    EVENT_SURFACED,
    HORIZON_THIS_WEEK,
    RESOLUTION_ACHIEVED,
    RESOLUTION_NO_LONGER_NEEDED,
    STATUS_ABANDONED,
    STATUS_ACTIVE,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    InvalidTransitionError,
    Objective,
    ObjectiveNotFoundError,
    ObjectiveStore,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "objectives.db")


@pytest.fixture
def store(db_path):
    return ObjectiveStore(db_path)


@pytest.fixture
def roof(store):
    return store.open(
        title="get the roof repaired",
        outcome_statement="the roofer needs to come this week",
        horizon_kind=HORIZON_THIS_WEEK,
    )


class TestOpening:
    def test_an_opened_objective_is_active_and_carries_its_horizon(self, roof):
        assert isinstance(roof, Objective)
        assert roof.status == STATUS_ACTIVE
        assert roof.title == "get the roof repaired"
        assert roof.horizon_kind == HORIZON_THIS_WEEK
        assert roof.completed_at is None
        assert roof.is_terminal is False

    def test_opening_writes_its_own_state_change_event(self, store, roof):
        events = store.events(roof.id)
        assert len(events) == 1
        assert events[0].event_kind == EVENT_STATE_CHANGE
        assert events[0].provenance["status"] == STATUS_ACTIVE

    def test_a_titleless_objective_is_refused(self, store):
        with pytest.raises(ValueError):
            store.open(title="   ")

    def test_an_unknown_horizon_kind_is_refused(self, store):
        with pytest.raises(ValueError):
            store.open(title="x", horizon_kind="next_fortnight")

    def test_the_session_b_identity_seam_is_present_and_unused(self, roof):
        """Nullable, written by nothing in this slice -- so identity can be
        supplied later without a migration."""
        assert roof.subject_ref is None


class TestDurability:
    def test_an_objective_outlives_the_store_that_created_it(self, db_path):
        """The product promise: the user establishes an objective once, and
        does not have to re-establish it after a restart."""
        first = ObjectiveStore(db_path)
        opened = first.open(title="get the roof repaired", horizon_kind=HORIZON_THIS_WEEK)
        first.record(opened.id, event_kind=EVENT_DECISION, summary="going with the quote")
        del first

        # A wholly separate store object over the same file, as a restarted
        # process would construct.
        second = ObjectiveStore(db_path)
        reloaded = second.get(opened.id)
        assert reloaded is not None
        assert reloaded.title == "get the roof repaired"
        assert reloaded.status == STATUS_ACTIVE
        assert [e.summary for e in second.evidence_events(opened.id)] == [
            "going with the quote",
        ]

    def test_ensure_schema_is_idempotent(self, db_path):
        ObjectiveStore(db_path)
        ObjectiveStore(db_path)
        assert ObjectiveStore(db_path).list() == []


class TestEventSeparation:
    def test_a_proposal_is_never_evidence(self, store, roof):
        store.record(
            roof.id,
            event_kind=EVENT_PROPOSAL,
            summary="could ring a second roofer for a quote",
        )
        store.record(roof.id, event_kind=EVENT_FACT, summary="rain forecast Thursday")

        evidence = store.evidence_events(roof.id)
        summaries = [e.summary for e in evidence]
        assert "rain forecast Thursday" in summaries
        assert "could ring a second roofer for a quote" not in summaries

        # And the row itself knows what it is, wherever it is read.
        proposals = store.events(roof.id, kinds={EVENT_PROPOSAL})
        assert len(proposals) == 1
        assert proposals[0].is_evidence is False

    def test_facts_decisions_and_actions_are_all_evidence(self, store, roof):
        for kind in (EVENT_FACT, EVENT_DECISION, EVENT_ACTION):
            store.record(roof.id, event_kind=kind, summary=f"{kind} happened")
        assert len(store.evidence_events(roof.id)) == 3
        assert all(e.is_evidence for e in store.evidence_events(roof.id))

    def test_bookkeeping_events_are_not_evidence_either(self, store, roof):
        store.surface(roof.id)
        kinds = {e.event_kind for e in store.evidence_events(roof.id)}
        assert EVENT_SURFACED not in kinds
        assert EVENT_STATE_CHANGE not in kinds

    def test_an_unclassified_event_cannot_be_written(self, store, roof):
        with pytest.raises(ValueError):
            store.record(roof.id, event_kind="note", summary="something")

    def test_the_database_itself_refuses_an_invented_kind(self, store, roof, db_path):
        """Not merely a Python-side check: the schema constrains it, so no
        future write path can introduce an unclassifiable row."""
        conn = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO objective_events "
                    "(objective_id, occurred_at, event_kind, summary) VALUES (?,?,?,?)",
                    (roof.id, "2026-08-27T00:00:00Z", "speculation", "x"),
                )
                conn.commit()
        finally:
            conn.close()

    def test_a_free_standing_state_change_cannot_be_forged(self, store, roof):
        """State changes belong to the transitions that cause them."""
        with pytest.raises(ValueError):
            store.record(roof.id, event_kind=EVENT_STATE_CHANGE, summary="completed")

    def test_provenance_survives_the_round_trip(self, store, roof):
        provenance = {
            "provider_host": "api.open-meteo.com",
            "source_kind": "external_capability",
            "evidence": True,
        }
        store.record(
            roof.id,
            event_kind=EVENT_FACT,
            summary="rain Thursday",
            provenance=provenance,
        )
        stored = store.evidence_events(roof.id)[0]
        assert stored.provenance == provenance
        assert stored.provenance["evidence"] is True


class TestTerminalStates:
    def test_completion_is_terminal_and_refuses_every_further_transition(self, store, roof):
        completed = store.complete(roof.id, resolution=RESOLUTION_ACHIEVED)
        assert completed.status == STATUS_COMPLETED
        assert completed.is_terminal is True
        assert completed.completed_at is not None

        # Every path that could possibly resurface it, refused.
        with pytest.raises(InvalidTransitionError):
            store.surface(roof.id)
        with pytest.raises(InvalidTransitionError):
            store.record(roof.id, event_kind=EVENT_FACT, summary="anything")
        with pytest.raises(InvalidTransitionError):
            store.block(roof.id, reason="anything")
        with pytest.raises(InvalidTransitionError):
            store.complete(roof.id)
        with pytest.raises(InvalidTransitionError):
            store.abandon(roof.id)

    def test_abandonment_is_just_as_terminal(self, store, roof):
        abandoned = store.abandon(roof.id, outcome_note="not worth it")
        assert abandoned.status == STATUS_ABANDONED
        with pytest.raises(InvalidTransitionError):
            store.surface(roof.id)

    def test_a_completed_objective_leaves_the_live_list(self, store, roof):
        assert [o.id for o in store.list_live()] == [roof.id]
        store.complete(roof.id)
        assert store.list_live() == []

    def test_the_outcome_is_recorded_not_merely_the_fact_of_completion(self, store, roof):
        completed = store.complete(
            roof.id,
            resolution=RESOLUTION_NO_LONGER_NEEDED,
            outcome_note="sold the house",
        )
        assert completed.resolution == RESOLUTION_NO_LONGER_NEEDED
        assert completed.outcome_note == "sold the house"

    def test_an_invalid_resolution_is_refused(self, store, roof):
        with pytest.raises(InvalidTransitionError):
            store.complete(roof.id, resolution="vibes")

    def test_a_missing_objective_is_not_found_rather_than_silently_ignored(self, store):
        with pytest.raises(ObjectiveNotFoundError):
            store.complete(999)
        with pytest.raises(ObjectiveNotFoundError):
            store.record(999, event_kind=EVENT_FACT, summary="x")


class TestBlocking:
    def test_a_blocked_objective_is_still_carried(self, store, roof):
        blocked = store.block(roof.id, reason="waiting on the quote")
        assert blocked.status == STATUS_BLOCKED
        # Still Bartholomew's responsibility -- blocked is not finished.
        assert [o.id for o in store.list_live()] == [roof.id]

    def test_blocking_and_unblocking_both_leave_a_trace(self, store, roof):
        store.block(roof.id, reason="waiting on the quote")
        store.unblock(roof.id)
        summaries = [e.summary for e in store.events(roof.id, kinds={EVENT_STATE_CHANGE})]
        assert any("blocked: waiting on the quote" == s for s in summaries)
        assert "no longer blocked" in summaries


class TestWhatChangedSinceLastTime:
    def test_surfacing_stamps_the_window_and_records_that_it_happened(self, store, roof):
        assert roof.last_surfaced_at is None
        surfaced = store.surface(roof.id)
        assert surfaced.last_surfaced_at is not None
        assert [e.event_kind for e in store.events(roof.id)][-1] == EVENT_SURFACED

    def test_since_returns_only_what_happened_after_the_last_surfacing(self, store, roof):
        store.record(roof.id, event_kind=EVENT_FACT, summary="before")
        surfaced = store.surface(roof.id)
        store.record(roof.id, event_kind=EVENT_FACT, summary="after")

        changed = store.evidence_events(
            roof.id,
            after_event_id=surfaced.last_surfaced_event_id,
        )
        assert [e.summary for e in changed] == ["after"]

    def test_the_window_holds_when_everything_happens_in_the_same_second(self, store, roof):
        """The reason the window is by event id and not by timestamp.

        `occurred_at` is second-granular, and a real turn can easily record
        evidence and surface the objective inside one tick. A timestamp
        window would drop the change or repeat it; both read to the user as
        Bartholomew being unreliable about their own history."""
        store.record(roof.id, event_kind=EVENT_FACT, summary="before")
        surfaced = store.surface(roof.id)
        store.record(roof.id, event_kind=EVENT_FACT, summary="after")

        all_events = store.events(roof.id)
        # Same second for everything -- the condition that breaks a
        # timestamp window.
        assert len({e.occurred_at for e in all_events}) <= 2

        changed = store.evidence_events(
            roof.id,
            after_event_id=surfaced.last_surfaced_event_id,
        )
        assert [e.summary for e in changed] == ["after"]

    def test_nothing_new_reads_as_nothing_new(self, store, roof):
        surfaced = store.surface(roof.id)
        assert (
            store.evidence_events(
                roof.id,
                after_event_id=surfaced.last_surfaced_event_id,
            )
            == []
        )

    def test_history_is_ordered_oldest_first(self, store, roof):
        for i in range(3):
            store.record(roof.id, event_kind=EVENT_FACT, summary=f"event {i}")
        summaries = [e.summary for e in store.evidence_events(roof.id)]
        assert summaries == ["event 0", "event 1", "event 2"]

    def test_recording_touches_updated_at(self, store, roof):
        store.record(roof.id, event_kind=EVENT_ACTION, summary="rang the roofer")
        assert store.get(roof.id).updated_at >= roof.updated_at


class TestConnectionDiscipline:
    def test_no_bare_sqlite3_connect_call_in_the_module(self):
        """WP-A2's structural rule: a module that writes governed state uses
        `db_ctx.connect()` + `set_wal_pragmas()`, never a bare
        `sqlite3.connect()`.

        Checked over the parsed AST rather than the source text, so the
        module's own prose about the rule cannot trip its own test."""
        import ast
        import inspect

        from bartholomew.kernel import objective_store

        tree = ast.parse(inspect.getsource(objective_store))
        bare = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "sqlite3"
        ]
        assert bare == []
