"""
The Residential Estate Management worked example from
docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md Sec 6, populated end-to-end in a
temp DB via the real competency model + MemoryStore.

This is static test-fixture data only -- not a skill, not an action, not
reachable from any live path, and not Estate Management production
functionality (explicitly out of scope for S5.1 per ROADMAP.md).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from bartholomew.kernel.competency import (
    CompetencyEnvelope,
    CompetencyEvidence,
    CompetencyHeuristic,
    CompetencyProcedure,
    CompetencyRecord,
    Provenance,
)
from bartholomew.kernel.memory_store import MemoryStore

TS = "2026-08-09T00:00:00Z"
COMPETENCY_ID = "estate_management"


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "worked_example.db"))
    await s.init()
    return s


def _worked_example() -> list:
    competency = CompetencyRecord(
        envelope=CompetencyEnvelope(
            competency_id=COMPETENCY_ID,
            classification="personal",
            provenance=Provenance(source_type="user_instruction", detail="user set this up"),
            confidence=None,
        ),
        name="Residential Estate Management",
        status="learning",
        description="Managing a residential property: maintenance, contractors, warranties.",
        relevant_capabilities=["documents_read", "web_search", "calendar_draft", "notify"],
        proficiency={
            "overall": 0.3,
            "by_area": {"maintenance_triage": 0.5, "quote_comparison": 0.6, "warranty_claims": 0.1},
        },
        known_gaps=["never handled a warranty dispute", "no experience with HOA rules"],
    )

    procedure = CompetencyProcedure(
        envelope=CompetencyEnvelope(
            competency_id=COMPETENCY_ID,
            classification="potentially_generalisable",
            provenance=Provenance(
                source_type="user_instruction",
                detail="user described how they like quotes compared",
            ),
            confidence=0.7,
        ),
        slug="quote_comparison",
        name="Quote Comparison",
        steps=[
            "Obtain at least three independent quotes",
            "Compare scope of work, price, and warranty terms",
            "Prefer the contractor with the best warranty at a reasonable price, not just the cheapest",
        ],
        when_to_use="Before hiring a contractor for any non-trivial repair or replacement.",
        capability_refs=["web_search"],
    )

    heuristic = CompetencyHeuristic(
        envelope=CompetencyEnvelope(
            competency_id=COMPETENCY_ID,
            classification="potentially_generalisable",
            provenance=Provenance(
                source_type="correction",
                detail="user corrected an earlier suggestion",
            ),
            confidence=0.6,
        ),
        slug="check_warranty_before_replace",
        rule="Check whether an appliance or system is still under warranty before recommending replacement.",
        conditions="Applies to any appliance or building system under 10 years old.",
        counterexamples=["Warranty already voided by a prior unauthorised repair."],
    )

    evidence = CompetencyEvidence(
        envelope=CompetencyEnvelope(
            competency_id=COMPETENCY_ID,
            classification="personal",
            provenance=Provenance(
                source_type="experience",
                detail="observed outcome of a real repair",
            ),
            confidence=0.9,
        ),
        slug="smith_plumbing_2026_repair",
        situation="Hot water system stopped heating properly in 2026.",
        action_taken="Called Smith Plumbing for a repair quote; repair was performed.",
        outcome="Repaired for $340; issue did not recur for 14 months.",
        judgement_was_correct=True,
        lesson="Repair (not replacement) was the right call given the warranty was still active.",
    )

    return [competency, procedure, heuristic, evidence]


@pytest.mark.asyncio
async def test_worked_example_round_trips_end_to_end(store) -> None:
    records = _worked_example()

    memory_ids = {}
    for record in records:
        result = await store.upsert_memory(
            kind=record.KIND,
            key=record.key(),
            value=json.dumps(record.to_dict()),
            ts=TS,
            summary=record.to_summary_text(),
            # See tests/test_competency_memory_shapes.py's
            # test_each_kind_round_trips_through_upsert_memory comment:
            # privacy_guard.is_sensitive() does a raw substring scan of the
            # whole serialized value, and CompetencyRecord/CompetencyProcedure
            # each have their own `"name"` JSON key, which is itself in
            # privacy_guard.SENSITIVE_KEYWORDS. Pre-existing behaviour,
            # unrelated to S5.1; skip_privacy_guard=True is the same
            # sanctioned bypass approve_pending_sensitive_write() uses.
            skip_privacy_guard=True,
        )
        assert result.stored is True, f"{record.KIND}/{record.key()} failed to store"
        memory_ids[record.key()] = result.memory_id

    conn = sqlite3.connect(store.db_path)
    try:
        rows = conn.execute(
            "SELECT kind, key, value FROM memories WHERE key LIKE ? ORDER BY kind",
            (f"{COMPETENCY_ID}%",),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 4
    by_kind = {row[0]: json.loads(row[2]) for row in rows}

    assert by_kind["competency"]["name"] == "Residential Estate Management"
    assert by_kind["competency"]["proficiency"]["overall"] == 0.3
    assert by_kind["competency"]["classification"] == "personal"

    assert by_kind["competency_procedure"]["name"] == "Quote Comparison"
    assert len(by_kind["competency_procedure"]["steps"]) == 3
    assert by_kind["competency_procedure"]["classification"] == "potentially_generalisable"

    assert "warranty" in by_kind["competency_heuristic"]["rule"].lower()
    assert by_kind["competency_heuristic"]["classification"] == "potentially_generalisable"

    assert by_kind["competency_evidence"]["judgement_was_correct"] is True
    assert by_kind["competency_evidence"]["classification"] == "personal"


@pytest.mark.asyncio
async def test_worked_example_key_prefix_query_finds_all_four_records(store) -> None:
    records = _worked_example()
    for record in records:
        await store.upsert_memory(
            kind=record.KIND,
            key=record.key(),
            value=json.dumps(record.to_dict()),
            ts=TS,
            summary=record.to_summary_text(),
            skip_privacy_guard=True,  # see test_worked_example_round_trips_end_to_end
        )

    conn = sqlite3.connect(store.db_path)
    try:
        keys = {
            row[0]
            for row in conn.execute(
                "SELECT key FROM memories WHERE key LIKE ?",
                (f"{COMPETENCY_ID}.%",),
            ).fetchall()
        }
        # The competency record itself uses the bare competency_id as its
        # key (no slug -- see CompetencyRecord.key()), so it's found
        # separately, not by the ".%" prefix used for its children.
        competency_row = conn.execute(
            "SELECT key FROM memories WHERE kind = 'competency' AND key = ?",
            (COMPETENCY_ID,),
        ).fetchone()
    finally:
        conn.close()

    assert keys == {
        "estate_management.quote_comparison",
        "estate_management.check_warranty_before_replace",
        "estate_management.smith_plumbing_2026_repair",
    }
    assert competency_row is not None
