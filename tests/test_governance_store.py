"""
Tests for bartholomew.orchestrator.safety.governance_store (Phase B stage
B3). Isolated tests against the new governance schema -- the current,
still-live ParkingBrake/BrakeStorage (bartholomew.orchestrator.safety
.parking_brake) is untouched by this stage and not exercised here.
"""

import json
import sqlite3

import pytest

from bartholomew.orchestrator.safety.governance_store import (
    GovernanceStore,
    StaleGovernanceWriteError,
    ensure_schema,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "governance.db")


def test_fresh_schema_has_default_disengaged_state(db_path):
    store = GovernanceStore(db_path)
    state = store.state()
    assert state.engaged is False
    assert state.scopes == frozenset()
    assert state.revision == 0


def test_fresh_schema_writes_no_audit_entry(db_path):
    """A brand-new store (no prior legacy state) hasn't had a real
    transition happen yet, so governance_audit should start empty."""
    GovernanceStore(db_path)
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM governance_audit").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_engage_replaces_scopes_and_increments_revision(db_path):
    store = GovernanceStore(db_path)
    state = store.engage("skills", "sight", reason="test lockdown")
    assert state.engaged is True
    assert state.scopes == frozenset({"skills", "sight"})
    assert state.revision == 1

    # Replace semantics: a second engage() with different scopes replaces,
    # not unions, the previous set (this stage's approved direction).
    state2 = store.engage("voice", reason="narrower lockdown")
    assert state2.scopes == frozenset({"voice"})
    assert state2.revision == 2


def test_engage_default_scope_is_global(db_path):
    store = GovernanceStore(db_path)
    state = store.engage()
    assert state.scopes == frozenset({"global"})


def test_is_blocked_global_vs_scoped(db_path):
    store = GovernanceStore(db_path)
    store.engage("skills")
    assert store.is_blocked("skills") is True
    assert store.is_blocked("voice") is False

    store.engage()  # global
    assert store.is_blocked("anything") is True


def test_disengage_succeeds_against_own_cached_revision(db_path):
    store = GovernanceStore(db_path)
    store.engage("skills")
    state = store.disengage(reason="incident resolved")
    assert state.engaged is False
    assert state.scopes == frozenset()
    assert state.revision == 2


def test_disengage_raises_on_stale_revision(db_path):
    """The core B3 invariant: a delayed disengage() based on a stale
    snapshot must not silently regress a more-recent, more-restrictive
    state."""
    writer = GovernanceStore(db_path)
    writer.engage("skills")  # revision 1

    stale_reader = GovernanceStore(db_path)  # loads revision 1
    writer.engage("skills", "voice")  # revision 2, more restrictive

    with pytest.raises(StaleGovernanceWriteError):
        stale_reader.disengage(reason="attempting stale loosening")

    # State must be unchanged by the rejected write.
    current = GovernanceStore(db_path).state()
    assert current.engaged is True
    assert current.scopes == frozenset({"skills", "voice"})
    assert current.revision == 2


def test_disengage_succeeds_after_refresh(db_path):
    writer = GovernanceStore(db_path)
    writer.engage("skills")

    stale_reader = GovernanceStore(db_path)
    writer.engage("skills", "voice")

    stale_reader.refresh()
    state = stale_reader.disengage(reason="confirmed after refresh")
    assert state.engaged is False
    assert state.revision == 3


def test_disengage_explicit_expected_revision_can_force(db_path):
    """A caller that explicitly names a fresh revision (not just relying
    on its own stale cache) can still disengage without a manual
    refresh() -- expected_revision is the deliberate override path."""
    writer = GovernanceStore(db_path)
    writer.engage("skills")  # revision 1

    stale_reader = GovernanceStore(db_path)  # cached at revision 1
    writer.engage("skills", "voice")  # revision 2

    state = stale_reader.disengage(reason="explicit override", expected_revision=2)
    assert state.engaged is False
    assert state.revision == 3


def test_engage_never_refused_regardless_of_staleness(db_path):
    """Tightening is never refused -- only disengage() (loosening) is
    revision-guarded, per this stage's design."""
    writer = GovernanceStore(db_path)
    writer.engage("skills")

    stale_writer = GovernanceStore(db_path)
    writer.engage("skills", "voice")

    # stale_writer's cached revision is behind, but engage() must still
    # succeed -- tightening is always allowed through.
    state = stale_writer.engage("global", reason="emergency stop")
    assert state.engaged is True
    assert state.scopes == frozenset({"global"})


def test_audit_records_structured_reason(db_path):
    store = GovernanceStore(db_path)
    store.engage("skills", reason="scheduled maintenance")
    store.disengage(reason="maintenance complete")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT action, scopes, reason, revision FROM governance_audit ORDER BY id",
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 2
    assert rows[0][0] == "engaged"
    assert json.loads(rows[0][1]) == ["skills"]
    assert rows[0][2] == "scheduled maintenance"
    assert rows[0][3] == 1
    assert rows[1][0] == "disengaged"
    assert rows[1][2] == "maintenance complete"
    assert rows[1][3] == 2


def test_audit_reason_is_optional(db_path):
    store = GovernanceStore(db_path)
    store.engage("skills")  # no reason given

    conn = sqlite3.connect(db_path)
    try:
        reason = conn.execute("SELECT reason FROM governance_audit").fetchone()[0]
    finally:
        conn.close()
    assert reason is None


# -- Actor field (Stage 1, S1.1) --------------------------------------------


def test_engage_and_disengage_record_actor(db_path):
    store = GovernanceStore(db_path)
    store.engage("skills", reason="lockdown", actor="user")
    store.disengage(reason="resolved", actor="user")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT action, actor FROM governance_audit ORDER BY id",
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("engaged", "user"), ("disengaged", "user")]


def test_actor_is_optional_and_defaults_to_none(db_path):
    store = GovernanceStore(db_path)
    store.engage("skills")  # no actor given

    conn = sqlite3.connect(db_path)
    try:
        actor = conn.execute("SELECT actor FROM governance_audit").fetchone()[0]
    finally:
        conn.close()
    assert actor is None


def test_legacy_migration_records_actor_as_migration(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE system_flags (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)",
        )
        conn.execute(
            "INSERT INTO system_flags VALUES ('parking_brake', ?, '1700000000')",
            (json.dumps({"engaged": True, "scopes": ["skills"]}),),
        )
        conn.commit()
    finally:
        conn.close()

    GovernanceStore(db_path)

    conn = sqlite3.connect(db_path)
    try:
        actor = conn.execute(
            "SELECT actor FROM governance_audit WHERE action = 'migrated'",
        ).fetchone()[0]
    finally:
        conn.close()
    assert actor == "migration"


def test_actor_column_is_backfilled_on_pre_existing_governance_audit_table(db_path):
    """A database created before the `actor` column existed must not break
    ensure_schema() -- the column is added in place, existing rows get a
    NULL actor rather than the migration failing or the table being
    dropped and recreated."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE parking_brake_state (id INTEGER PRIMARY KEY CHECK (id = 1), "
            "engaged INTEGER NOT NULL DEFAULT 0, scopes TEXT NOT NULL DEFAULT '[]', "
            "revision INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL)",
        )
        conn.execute(
            "INSERT INTO parking_brake_state (id, engaged, scopes, revision, updated_at) "
            "VALUES (1, 1, '[\"skills\"]', 1, 1700000000)",
        )
        conn.execute(
            "CREATE TABLE governance_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts INTEGER NOT NULL, action TEXT NOT NULL, scopes TEXT NOT NULL, "
            "reason TEXT, revision INTEGER NOT NULL)",
        )
        conn.execute(
            "INSERT INTO governance_audit (ts, action, scopes, reason, revision) "
            "VALUES (1700000000, 'engaged', '[\"skills\"]', 'pre-actor row', 1)",
        )
        conn.commit()
    finally:
        conn.close()

    store = GovernanceStore(db_path)
    assert store.state().engaged is True  # pre-existing state untouched

    store.engage("skills", reason="post-migration", actor="user")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT reason, actor FROM governance_audit ORDER BY id",
        ).fetchall()
    finally:
        conn.close()
    assert rows[0] == ("pre-actor row", None)  # backfilled, not lost
    assert rows[1] == ("post-migration", "user")


# -- Audit log read (Stage 1, S1.5) ------------------------------------------


def test_list_audit_returns_entries_newest_first(db_path):
    store = GovernanceStore(db_path)
    store.engage("skills", reason="first", actor="user")
    store.disengage(reason="second", actor="user")
    store.engage("voice", reason="third", actor="cli")

    entries = store.list_audit()

    assert [e["reason"] for e in entries] == ["third", "second", "first"]
    assert [e["action"] for e in entries] == ["engaged", "disengaged", "engaged"]


def test_list_audit_decodes_scopes_and_includes_actor(db_path):
    store = GovernanceStore(db_path)
    store.engage("skills", "voice", reason="lockdown", actor="user")

    entry = store.list_audit()[0]

    assert entry["scopes"] == ["skills", "voice"]
    assert entry["actor"] == "user"
    assert entry["revision"] == 1
    assert isinstance(entry["ts"], int)


def test_list_audit_respects_limit(db_path):
    store = GovernanceStore(db_path)
    for i in range(5):
        store.engage("skills", reason=f"entry {i}")

    entries = store.list_audit(limit=2)

    assert len(entries) == 2
    assert entries[0]["reason"] == "entry 4"
    assert entries[1]["reason"] == "entry 3"


def test_list_audit_empty_for_fresh_store(db_path):
    store = GovernanceStore(db_path)
    assert store.list_audit() == []


# -- Legacy migration -------------------------------------------------------


def test_legacy_system_flags_state_is_imported(db_path):
    """An existing deployment's system_flags 'parking_brake' row must not
    be silently lost when the new schema is introduced."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE system_flags (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)",
        )
        conn.execute(
            "INSERT INTO system_flags VALUES ('parking_brake', ?, '1700000000')",
            (json.dumps({"engaged": True, "scopes": ["skills", "voice"]}),),
        )
        conn.commit()
    finally:
        conn.close()

    store = GovernanceStore(db_path)
    state = store.state()
    assert state.engaged is True
    assert state.scopes == frozenset({"skills", "voice"})
    assert state.revision == 1

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT action, reason FROM governance_audit WHERE action = 'migrated'",
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "migrated"


def test_legacy_migration_does_not_touch_system_flags(db_path):
    """The import is additive/non-destructive -- the legacy row and the
    still-live ParkingBrake reading it are untouched."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE system_flags (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)",
        )
        conn.execute(
            "INSERT INTO system_flags VALUES ('parking_brake', ?, '1700000000')",
            (json.dumps({"engaged": True, "scopes": ["skills"]}),),
        )
        conn.commit()
    finally:
        conn.close()

    GovernanceStore(db_path)

    conn = sqlite3.connect(db_path)
    try:
        legacy_value = conn.execute(
            "SELECT value FROM system_flags WHERE key = 'parking_brake'",
        ).fetchone()[0]
    finally:
        conn.close()
    assert json.loads(legacy_value) == {"engaged": True, "scopes": ["skills"]}


def test_migration_is_idempotent(db_path):
    """Re-running ensure_schema() (e.g. an interrupted first run retried)
    must not duplicate the imported state or its audit record."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE system_flags (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)",
        )
        conn.execute(
            "INSERT INTO system_flags VALUES ('parking_brake', ?, '1700000000')",
            (json.dumps({"engaged": True, "scopes": ["skills"]}),),
        )
        conn.commit()
    finally:
        conn.close()

    ensure_schema(db_path)
    ensure_schema(db_path)
    ensure_schema(db_path)

    conn = sqlite3.connect(db_path)
    try:
        state_rows = conn.execute("SELECT COUNT(*) FROM parking_brake_state").fetchone()[0]
        audit_rows = conn.execute(
            "SELECT COUNT(*) FROM governance_audit WHERE action = 'migrated'",
        ).fetchone()[0]
    finally:
        conn.close()
    assert state_rows == 1
    assert audit_rows == 1


def test_fresh_default_migration_is_idempotent_and_unaudited(db_path):
    """No legacy row at all: ensure_schema() run multiple times still
    produces exactly one default row and zero audit entries."""
    ensure_schema(db_path)
    ensure_schema(db_path)

    conn = sqlite3.connect(db_path)
    try:
        state_rows = conn.execute("SELECT COUNT(*) FROM parking_brake_state").fetchone()[0]
        audit_rows = conn.execute("SELECT COUNT(*) FROM governance_audit").fetchone()[0]
    finally:
        conn.close()
    assert state_rows == 1
    assert audit_rows == 0


# -- Crash consistency --------------------------------------------------


def test_write_failure_between_state_and_audit_leaves_neither_persisted(db_path):
    """If the process dies (or an error occurs) between the state UPDATE
    and the audit INSERT, neither must be visible -- the transaction
    wrapping both must roll back completely, not leave a state change
    with no corresponding audit record.

    sqlite3.Connection is a C type and can't be monkeypatched directly, so
    the failure is injected with a real SQLite trigger instead -- closer
    to a genuine mid-transaction fault than mocking Python internals."""
    store = GovernanceStore(db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TRIGGER simulate_crash_before_audit_insert "
            "BEFORE INSERT ON governance_audit "
            "WHEN NEW.reason = '__SIMULATE_CRASH__' "
            "BEGIN SELECT RAISE(ABORT, 'simulated crash before audit insert'); END",
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(sqlite3.Error):
        store.engage("skills", reason="__SIMULATE_CRASH__")

    conn = sqlite3.connect(db_path)
    try:
        state_row = conn.execute(
            "SELECT engaged, revision FROM parking_brake_state WHERE id = 1",
        ).fetchone()
        audit_count = conn.execute("SELECT COUNT(*) FROM governance_audit").fetchone()[0]
    finally:
        conn.close()

    # Neither the state change nor an audit row was committed.
    assert state_row == (0, 0)
    assert audit_count == 0


def test_successful_write_survives_connection_reopen_with_both_rows_present(db_path):
    """After a successful write, a brand-new connection (simulating a
    process restart) must see the state change and its audit record
    together -- never one without the other."""
    store = GovernanceStore(db_path)
    store.engage("skills", reason="pre-restart")
    del store  # drop all in-process references; only the DB file remains

    conn = sqlite3.connect(db_path)
    try:
        state_row = conn.execute(
            "SELECT engaged, scopes, revision FROM parking_brake_state WHERE id = 1",
        ).fetchone()
        audit_row = conn.execute(
            "SELECT action, revision FROM governance_audit ORDER BY id DESC LIMIT 1",
        ).fetchone()
    finally:
        conn.close()

    assert state_row == (1, json.dumps(["skills"]), 1)
    assert audit_row == ("engaged", 1)

    # And a freshly constructed store picks up exactly that state.
    reopened = GovernanceStore(db_path)
    assert reopened.state().revision == 1
    assert reopened.state().scopes == frozenset({"skills"})


def test_stale_write_rejection_itself_leaves_no_partial_state(db_path):
    """A rejected stale disengage() must not leave any trace -- no state
    change, no audit entry -- distinct from the "successful write" crash
    case above."""
    writer = GovernanceStore(db_path)
    writer.engage("skills")  # revision 1
    stale_reader = GovernanceStore(db_path)
    writer.engage("skills", "voice")  # revision 2

    with pytest.raises(StaleGovernanceWriteError):
        stale_reader.disengage(reason="stale")

    conn = sqlite3.connect(db_path)
    try:
        audit_count = conn.execute("SELECT COUNT(*) FROM governance_audit").fetchone()[0]
        revision = conn.execute(
            "SELECT revision FROM parking_brake_state WHERE id = 1",
        ).fetchone()[0]
    finally:
        conn.close()
    assert audit_count == 2  # only the two real engage() calls above
    assert revision == 2
