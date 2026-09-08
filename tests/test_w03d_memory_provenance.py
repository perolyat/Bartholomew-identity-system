"""
W03-D acceptance 1 & 2 (package-local half): provenance, validity fields and
the retrieval-side verdict.

`docs/waves/W03/W03_D_CONTRACT.md` acceptance criteria:

  1. `memories` rows carry source / source_type / asserted_by / confidence /
     valid_from / valid_to / superseded_by / revoked_at, populated **only**
     through `upsert_memory`, surfaced in retrieved items.
  2. a retrieval-side verdict (`currently_valid` / `revoked` / `superseded` /
     `expired`) is honoured by all retrievers.

This module is the fast, package-local half: schema migration, verdict
computation, expiry arithmetic, and the structural guarantee that nothing but
the governed write path sets the columns. The behavioural halves -- a poisoned
recall, a correction that changes a later turn, a revoked grant that cannot
come back -- live in `test_w03d_memory_poisoning.py`,
`test_w03d_supersession_and_tombstone.py` and
`test_w03d_instruction_data_boundary.py`, marked `integration` per
`docs/waves/W03/W03_TEST_CONTRACTS.md` Sec.8.

Non-vacuity: every test here fails if the property it names is removed. The
migration tests operate on a database built *without* the new columns, so
they cannot pass by accident on a fresh schema; the verdict tests seed rows
that a gate ignoring validity would happily return.
"""

from __future__ import annotations

import ast
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bartholomew.kernel import consent_gate as cg
from bartholomew.kernel.memory_rules import expiry_from_rules, parse_duration_seconds
from bartholomew.kernel.memory_store import (
    MEMORY_GOVERNANCE_COLUMNS,
    MEMORY_SOURCE_TYPES,
    MemoryProvenance,
    MemoryStore,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ago(**kwargs) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat()


@pytest.fixture
async def store(tmp_path):
    mem = MemoryStore(str(tmp_path / "w03d.db"))
    await mem.init()
    yield mem
    await mem.close(checkpoint=False)


# ---------------------------------------------------------------------------
# 1. The columns exist, on a fresh database and on a migrated one.
# ---------------------------------------------------------------------------


class TestSchema:
    async def test_fresh_database_carries_every_governance_column(self, store):
        conn = sqlite3.connect(store.db_path)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
        finally:
            conn.close()

        missing = set(MEMORY_GOVERNANCE_COLUMNS) - columns
        assert not missing, f"memories is missing {sorted(missing)}"

    async def test_a_pre_w03d_database_is_migrated_additively(self, tmp_path):
        """
        The ALTER path, exercised against a genuinely old schema.

        Built here by hand rather than by deleting columns, because that is
        what a real installation looks like: a `memories` table with the five
        original columns and rows already in it. The migration must add the
        new columns without touching the existing row's meaning -- NULL means
        "not recorded", which is exactly what an old row's provenance is.
        """
        db_path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE memories (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL,
              key TEXT NOT NULL,
              value TEXT NOT NULL,
              summary TEXT,
              ts TEXT NOT NULL
            );
            CREATE UNIQUE INDEX uq_memories_kind_key ON memories(kind, key);
            """,
        )
        conn.execute(
            "INSERT INTO memories(kind,key,value,ts) VALUES(?,?,?,?)",
            ("fact", "legacy", "the bin goes out on Thursdays", _now()),
        )
        conn.commit()
        conn.close()

        store = MemoryStore(db_path)
        await store.init()
        try:
            conn = sqlite3.connect(db_path)
            try:
                conn.row_factory = sqlite3.Row
                columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
                row = conn.execute(
                    "SELECT * FROM memories WHERE kind='fact' AND key='legacy'",
                ).fetchone()
            finally:
                conn.close()
        finally:
            await store.close(checkpoint=False)

        assert set(MEMORY_GOVERNANCE_COLUMNS) <= columns
        # The pre-existing row survived, and its provenance reads as
        # "not recorded" rather than as a fabricated origin.
        assert row["value"] == "the bin goes out on Thursdays"
        for column in MEMORY_GOVERNANCE_COLUMNS:
            assert row[column] is None, f"migration invented a value for {column}"

    async def test_migration_is_idempotent(self, store):
        """`init()` runs on every start; running it twice must not fail."""
        await store.init()
        await store.init()


# ---------------------------------------------------------------------------
# 2. Only the governed write path populates them.
# ---------------------------------------------------------------------------


class TestOnlyUpsertWritesProvenance:
    async def test_upsert_records_the_supplied_provenance(self, store):
        await store.upsert_memory(
            "fact",
            "provenanced",
            "the fuse box is in the hall cupboard",
            _now(),
            provenance=MemoryProvenance(
                source="chat",
                source_type="user_instruction",
                asserted_by="taylor",
                confidence=0.8,
            ),
        )
        row = await store.get_memory("fact", "provenanced")
        assert row["source"] == "chat"
        assert row["source_type"] == "user_instruction"
        assert row["asserted_by"] == "taylor"
        assert row["confidence"] == pytest.approx(0.8)
        # valid_from defaults to the write's own timestamp: a claim with no
        # stated start holds from when it was made.
        assert row["valid_from"] == row["ts"]
        assert row["valid_to"] is None
        assert row["superseded_by"] is None
        assert row["revoked_at"] is None

    async def test_omitting_provenance_changes_nothing_for_an_existing_caller(self, store):
        """Every pre-W03-D call site passes no provenance. Those writes must
        behave exactly as before, with the columns left explicitly unrecorded
        rather than defaulted to something invented."""
        await store.upsert_memory("fact", "plain", "no provenance here", _now())
        row = await store.get_memory("fact", "plain")
        for column in ("source", "source_type", "asserted_by", "confidence"):
            assert row[column] is None

    async def test_an_unknown_source_type_is_refused(self):
        with pytest.raises(ValueError, match="unknown memory source_type"):
            MemoryProvenance(source_type="whatever_i_like")

    async def test_confidence_outside_the_unit_interval_is_refused(self):
        with pytest.raises(ValueError, match="confidence must be between"):
            MemoryProvenance(confidence=1.5)

    async def test_confidence_none_means_unknown_not_low(self, store):
        """`None` is a distinct value from 0.0, and must survive as one.

        A fact somebody simply stated has no measured confidence. Recording
        0.0 would be a fabricated measurement, and the selection gate treats
        unknown and low differently on purpose."""
        await store.upsert_memory(
            "fact",
            "unknown_confidence",
            "stated plainly",
            _now(),
            provenance=MemoryProvenance(source_type="user_instruction"),
        )
        row = await store.get_memory("fact", "unknown_confidence")
        assert row["confidence"] is None

    def test_no_module_outside_the_store_writes_the_governance_columns(self):
        """
        Structural, AST-based: `memories.source`, `.confidence`,
        `.valid_to`, `.superseded_by` and `.revoked_at` may only be written
        by `memory_store.py`.

        Provenance a caller can edit after the fact is provenance nobody can
        rely on -- and the retrieval verdict relies on it completely. This is
        the guard that keeps "populated only through `upsert_memory`" true as
        the codebase grows, rather than true only on the day it was written.
        """
        governed = {f"memories set {column}" for column in MEMORY_GOVERNANCE_COLUMNS}
        offenders: list[str] = []
        allowed = {
            REPO_ROOT / "bartholomew" / "kernel" / "memory_store.py",
        }

        for root in (
            REPO_ROOT / "bartholomew",
            REPO_ROOT / "bartholomew_api_bridge_v0_1",
            REPO_ROOT / "identity_interpreter",
        ):
            if not root.exists():
                continue
            for py_file in root.rglob("*.py"):
                if py_file in allowed:
                    continue
                try:
                    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
                except SyntaxError:  # pragma: no cover - defensive
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                        continue
                    sql = " ".join(node.value.lower().split())
                    if "update memories" not in sql:
                        continue
                    for column in MEMORY_GOVERNANCE_COLUMNS:
                        if f"set {column}" in sql or f", {column}=" in sql:
                            offenders.append(f"{py_file}:{node.lineno} ({column})")

        assert offenders == [], (
            "the memories provenance/validity columns must only be written by "
            f"memory_store.py; found writes in: {offenders} (governed set: {sorted(governed)})"
        )


# ---------------------------------------------------------------------------
# 3. Expiry arithmetic -- `auto_expire` finally means something.
# ---------------------------------------------------------------------------


class TestExpiryArithmetic:
    @pytest.mark.parametrize(
        ("spec", "seconds"),
        [("30s", 30), ("30m", 1800), ("2h", 7200), ("7d", 604800), ("1w", 604800)],
    )
    def test_durations_parse(self, spec, seconds):
        assert parse_duration_seconds(spec) == pytest.approx(seconds)

    def test_an_absent_expiry_is_none_not_zero(self):
        assert parse_duration_seconds(None) is None

    def test_an_unparseable_expiry_yields_an_already_closed_window(self):
        """Fail-closed. A retention rule nobody can read must not be read as
        'no retention rule' -- that would turn a typo into indefinite
        retention of exactly the material the rule wanted bounded."""
        written = _now()
        end = expiry_from_rules({"expires_in": "two hours-ish"}, written)
        assert end is not None
        assert end <= _now()

    def test_no_rule_means_no_window(self):
        assert expiry_from_rules({}, _now()) is None

    async def test_a_rule_declared_window_is_recorded_at_write_time(self, store):
        """`environment_observation` carries `expires_in: 7d` in
        `config/memory_rules.yaml`. The write must record the window it
        implies, so the read side does not have to re-derive it every time."""
        await store.upsert_memory(
            "environment_observation",
            "screen_state",
            "Notepad was in the foreground",
            _now(),
        )
        row = await store.get_memory("environment_observation", "screen_state")
        assert row["valid_to"] is not None, "the declared expires_in was not recorded"
        assert row["valid_to"] > row["ts"]

    async def test_a_caller_may_shorten_a_window_but_never_extend_it(self, store):
        """Governance sets the ceiling. A caller asking for a longer window
        than the rule declares gets the rule's."""
        far_future = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
        await store.upsert_memory(
            "environment_observation",
            "greedy",
            "keep me forever",
            _now(),
            provenance=MemoryProvenance(valid_to=far_future),
        )
        row = await store.get_memory("environment_observation", "greedy")
        assert row["valid_to"] < far_future

        soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        await store.upsert_memory(
            "environment_observation",
            "modest",
            "only briefly",
            _now(),
            provenance=MemoryProvenance(valid_to=soon),
        )
        row = await store.get_memory("environment_observation", "modest")
        assert row["valid_to"] == soon


# ---------------------------------------------------------------------------
# 4. The verdict itself.
# ---------------------------------------------------------------------------


class TestValidityVerdict:
    async def test_an_ordinary_fresh_memory_is_currently_valid(self, store):
        result = await store.upsert_memory("fact", "fresh", "still true", _now())
        verdict = cg.ConsentGate(store.db_path).validity_verdict(result.memory_id)
        assert verdict.verdict == cg.VERDICT_CURRENTLY_VALID
        assert verdict.currently_valid is True

    async def test_a_revoked_memory_reads_revoked(self, store):
        result = await store.upsert_memory("fact", "withdrawn", "used to be true", _now())
        await store.revoke_memory("fact", "withdrawn", revoked_by="taylor", reason="wrong")

        verdict = cg.ConsentGate(store.db_path).validity_verdict(result.memory_id)
        assert verdict.verdict == cg.VERDICT_REVOKED
        assert verdict.currently_valid is False
        assert "withdraw" in (verdict.reason or "") or "tombstone" in (verdict.reason or "")

    async def test_a_superseded_memory_reads_superseded(self, store):
        await store.upsert_memory("fact", "coffee", "Preference: tea", _now())
        outcome = await store.supersede_memory("fact", "coffee", "Preference: coffee")
        assert outcome.stored, outcome

        archived = await store.get_memory("memory_revision", outcome.archived_key)
        verdict = cg.ConsentGate(store.db_path).validity_verdict(archived["id"])
        assert verdict.verdict in {cg.VERDICT_SUPERSEDED, cg.VERDICT_EXPIRED}
        assert verdict.currently_valid is False

        live = cg.ConsentGate(store.db_path).validity_verdict(outcome.memory_id)
        assert live.verdict == cg.VERDICT_CURRENTLY_VALID

    async def test_a_stale_observation_reads_expired(self, store):
        """The whole point of acceptance criterion 6: a Windows observation
        stops being recalled once its declared window has passed."""
        result = await store.upsert_memory(
            "environment_observation",
            "old_screen",
            "Notepad was in the foreground",
            _ago(days=30),
        )
        verdict = cg.ConsentGate(store.db_path).validity_verdict(result.memory_id)
        assert verdict.verdict == cg.VERDICT_EXPIRED
        assert verdict.currently_valid is False

    async def test_a_recent_observation_is_still_valid(self, store):
        """Non-vacuity control for the test above: the exclusion is about the
        *window*, not about the kind."""
        result = await store.upsert_memory(
            "environment_observation",
            "recent_screen",
            "Notepad is in the foreground",
            _now(),
        )
        verdict = cg.ConsentGate(store.db_path).validity_verdict(result.memory_id)
        assert verdict.verdict == cg.VERDICT_CURRENTLY_VALID

    async def test_a_legacy_row_with_no_recorded_window_still_expires(self, store):
        """
        Fail-closed for rows written before the columns existed.

        The row here is inserted directly with no `valid_to`, which is exactly
        what a pre-W03-D `environment_observation` looks like. The verdict is
        re-derived from the rule so an unmigrated history does not become an
        unbounded one.
        """
        conn = sqlite3.connect(store.db_path)
        try:
            conn.execute(
                "INSERT INTO memories(kind,key,value,ts) VALUES(?,?,?,?)",
                (
                    "environment_observation",
                    "legacy_screen",
                    "an old capture",
                    _ago(days=90),
                ),
            )
            conn.commit()
            memory_id = conn.execute(
                "SELECT id FROM memories WHERE key='legacy_screen'",
            ).fetchone()[0]
        finally:
            conn.close()

        verdict = cg.ConsentGate(store.db_path).validity_verdict(memory_id)
        assert verdict.verdict == cg.VERDICT_EXPIRED

    async def test_a_missing_memory_fails_closed(self, store):
        verdict = cg.ConsentGate(store.db_path).validity_verdict(999_999)
        assert verdict.currently_valid is False

    async def test_verdicts_are_batched_not_per_row(self, store):
        ids = []
        for index in range(5):
            result = await store.upsert_memory("fact", f"batch_{index}", "value", _now())
            ids.append(result.memory_id)

        verdicts = cg.ConsentGate(store.db_path).validity_verdicts(ids)
        assert set(verdicts) == set(ids)
        assert all(v.currently_valid for v in verdicts.values())

    async def test_the_verdict_carries_the_provenance_a_consumer_needs(self, store):
        result = await store.upsert_memory(
            "fact",
            "sourced",
            "value",
            _now(),
            provenance=MemoryProvenance(
                source="objective:12",
                source_type="observation",
                asserted_by="bartholomew",
                confidence=0.4,
            ),
        )
        verdict = cg.ConsentGate(store.db_path).validity_verdict(result.memory_id)
        assert verdict.provenance["source"] == "objective:12"
        assert verdict.provenance["source_type"] == "observation"
        assert verdict.provenance["asserted_by"] == "bartholomew"
        assert verdict.provenance["confidence"] == pytest.approx(0.4)

    def test_the_source_type_vocabulary_separates_observation_from_inference(self):
        """The distinction W03-A carries on the event backbone must survive
        the trip into memory: a record of what was *seen* can never be
        indistinguishable from a record of what was *concluded*."""
        assert "observation" in MEMORY_SOURCE_TYPES
        assert "inference" in MEMORY_SOURCE_TYPES
