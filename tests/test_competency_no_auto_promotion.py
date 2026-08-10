"""
The `classification` invariant (S5.1), revised approach.

The invariant is NOT "the string 'potentially_generalisable' may only
appear in bartholomew/kernel/competency.py" -- that would incorrectly
prohibit a future, separately-approved stage (S5.4, or an eventual governed
generalisation pipeline) from ever legitimately reading the classification
it exists to record. See docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md Sec 5.

The actual invariant, tested three ways below:

1. `test_classification_has_no_storage_side_effects` -- behavioral, not
   textual: writing an otherwise-identical record with a different
   `classification` value produces identical storage/governance behaviour.
   Proves classification triggers no different treatment today -- the only
   thing S5.1 could possibly make it "do."

2. `test_governance_and_storage_modules_are_classification_blind` -- the
   specific, existing, shared Memory-substrate/governance modules this
   change's approval explicitly requires stay unchanged cannot possibly be
   the thing that "triggers" anything based on classification, because they
   never reference it at all. Scoped to exactly those five named files, not
   the whole repository.

3. `test_no_promotion_export_mechanism_introduced` -- this stage's own new
   file (`competency.py`) imports nothing capable of transport, execution,
   or persistence. Guards against this specific change introducing a
   promotion/export mechanism; says nothing about any other file.

None of the three prevent a future, separately-approved module from reading
`classification` as its explicit job.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest

from bartholomew.kernel.competency import CompetencyEnvelope, CompetencyEvidence, Provenance
from bartholomew.kernel.memory_store import MemoryStore

TS = "2026-08-09T00:00:00Z"

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_DIR = REPO_ROOT / "bartholomew" / "kernel"

# The specific tables whose row counts we track for the behavioral test.
# Matches memory_store.py's SCHEMA (the memory_fts virtual table and its
# FTS5 shadow tables are deliberately excluded -- querying COUNT(*) on FTS5
# shadow tables is an internal implementation detail, not part of the
# behaviour being asserted here).
TRACKED_TABLES = (
    "memories",
    "memory_chunks",
    "nudges",
    "reflections",
    "memory_consent",
    "pending_sensitive_writes",
    "system_flags",
)


def _table_counts(db_path: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TRACKED_TABLES}
    finally:
        conn.close()


def _envelope(classification: str) -> CompetencyEnvelope:
    # recorded_at/updated_at default to the real clock at construction time
    # (see competency.py's _utcnow_iso default_factory), which would
    # otherwise differ by microseconds across this test's loop iterations --
    # pinned explicitly so the three records are identical apart from
    # classification, matching this test's own comparison below.
    return CompetencyEnvelope(
        competency_id="estate_management",
        classification=classification,
        provenance=Provenance(source_type="experience", detail="test fixture", recorded_at=TS),
        confidence=0.5,
        updated_at=TS,
    )


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "no_auto_promotion.db"))
    await s.init()
    return s


@pytest.mark.asyncio
async def test_classification_has_no_storage_side_effects(store, monkeypatch) -> None:
    """Three otherwise-identical CompetencyEvidence records, differing only
    in `classification`, must produce byte-for-byte identical table
    row-count deltas and JSON blobs that differ only in that one field.

    Found while implementing (fourth pre-existing bug, unrelated to and not
    fixed by S5.1): several tests/integration/*.py files disable embeddings
    via `os.environ["BARTHO_EMBED_ENABLED"] = "0"` directly (not
    `monkeypatch.setenv`, so it's never reverted), matching the "1"/"0"
    convention every *other* embed-related check in the codebase uses (e.g.
    `os.getenv("BARTHO_EMBED_ENABLED") == "1"` in embedding_engine.py and
    memory_rules.py). But memory_store.py's own `_get_embedding_components()`
    instead checks `if not os.getenv("BARTHO_EMBED_ENABLED")`, and `"0"` is a
    non-empty string -- truthy in Python -- so that guard does NOT return
    early for `"0"`; it treats it as enabled. Net effect: once any of those
    integration tests runs earlier in the same pytest session, embeddings
    stay silently "on" (and stay on) for every later test in the process,
    inserting an unexpected memory_consent row per write. Explicitly
    unsetting it here makes this test hermetic to that ambient pollution,
    matching its own behaviour when run in isolation.
    """
    monkeypatch.delenv("BARTHO_EMBED_ENABLED", raising=False)
    classifications = ["personal", "potentially_generalisable", "system"]
    deltas: dict[str, dict[str, int]] = {}
    stored_dicts: dict[str, dict] = {}

    for classification in classifications:
        record = CompetencyEvidence(
            envelope=_envelope(classification),
            slug=f"evidence_{classification}",
            situation="Routine maintenance was performed on schedule.",
            outcome="No issues found.",
        )
        before = _table_counts(store.db_path)
        result = await store.upsert_memory(
            kind=record.KIND,
            key=record.key(),
            value=json.dumps(record.to_dict()),
            ts=TS,
            summary=record.to_summary_text(),
            # "Routine" trips privacy_guard.is_sensitive()'s raw substring
            # match against SENSITIVE_KEYWORDS' "routine" entry -- see
            # tests/test_competency_memory_shapes.py's
            # test_each_kind_round_trips_through_upsert_memory comment for
            # the same pre-existing, unrelated-to-S5.1 behaviour.
            skip_privacy_guard=True,
        )
        after = _table_counts(store.db_path)
        assert result.stored is True

        deltas[classification] = {t: after[t] - before[t] for t in TRACKED_TABLES}

        conn = sqlite3.connect(store.db_path)
        try:
            raw_value = conn.execute(
                "SELECT value FROM memories WHERE id = ?",
                (result.memory_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        stored_dicts[classification] = json.loads(raw_value)

    # 1. Identical side effects across every tracked table, regardless of
    #    classification value.
    baseline = deltas["personal"]
    assert deltas["potentially_generalisable"] == baseline
    assert deltas["system"] == baseline
    assert baseline["memories"] == 1
    assert baseline["pending_sensitive_writes"] == 0
    assert baseline["memory_consent"] == 0

    # 2. The stored records differ ONLY in the classification field itself
    #    (and the slug/key which had to differ to avoid an upsert
    #    collision) -- nothing else about storage was influenced by it.
    for classification in classifications:
        d = dict(stored_dicts[classification])
        assert d.pop("classification") == classification
        # updated_at/revision/provenance.recorded_at are identical across
        # all three since the records were built from the same envelope
        # shape with only classification varying.
        assert d == {
            "competency_id": "estate_management",
            "provenance": stored_dicts["personal"]["provenance"],
            "confidence": 0.5,
            "supervision": {"requires_review": False, "reason": None},
            "revision": 1,
            "updated_at": stored_dicts["personal"]["updated_at"],
            "situation": "Routine maintenance was performed on schedule.",
            "action_taken": "",
            "outcome": "No issues found.",
            "judgement_was_correct": None,
            "lesson": "",
        }


def test_governance_and_storage_modules_are_classification_blind() -> None:
    """The specific, existing, shared Memory substrate and governance
    modules must remain entirely unaware of competency `classification` --
    proving they cannot be the mechanism that acts on it, since they never
    parse it at all (it is opaque JSON content inside `memories.value` to
    every one of these).

    Checks two things, deliberately not a bare substring search on the
    generic word "classification": one of these files already contains that
    word today, in pre-existing prose unrelated to competency classification
    (memory_store.py's `pending_sensitive_writes` docstring: "`privacy_class`
    records the matched rule's classification for display") -- a bare
    substring check on "classification" would false-positive on that. The
    two checks below are precise instead:

    1. `potentially_generalisable` is the one classification value whose
       entire reason to exist is the S5.1 candidacy marker -- it has no
       other legitimate reason to appear in any of these files' source.
    2. None of these files import anything from `competency.py` (AST-based,
       so the module's own docstrings/comments can't produce a false
       positive) -- proving they cannot even reach the classification field,
       let alone act on it.
    """
    files = [
        KERNEL_DIR / "memory_store.py",
        KERNEL_DIR / "memory_rules.py",
        KERNEL_DIR / "consent_gate.py",
        KERNEL_DIR / "retrieval.py",
        KERNEL_DIR / "hybrid_retriever.py",
    ]

    for path in files:
        assert path.exists(), f"expected file not found: {path}"
        text = path.read_text(encoding="utf-8")
        assert "potentially_generalisable" not in text, (
            f"{path} references 'potentially_generalisable' -- the shared Memory "
            "substrate/governance path must stay unaware of competency classification "
            "(see docs/S5_1_COMPETENCY_ARCHITECTURE_DESIGN.md Sec 5)"
        )

        tree = ast.parse(text, filename=str(path))
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

        offending = {m for m in imported_modules if "competency" in m}
        assert not offending, (
            f"{path} imports from {offending} -- the shared Memory substrate/governance "
            "path must not depend on the competency module at all"
        )


def test_no_promotion_export_mechanism_introduced() -> None:
    """`competency.py` -- the only new production file this stage adds --
    must import nothing capable of transport, execution, or persistence.
    AST-based (not substring-based) specifically so the module's own
    docstring, which explains this exact invariant in prose, can freely
    mention words like "sqlite3" or "MemoryStore" without a false
    positive -- only real `import`/`from ... import` statements count."""
    path = KERNEL_DIR / "competency.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module.split(".")[0])

    forbidden_imports = {
        # Outbound transport / execution -- would be required for any
        # cross-instance transfer, export, or promotion mechanism.
        "requests",
        "httpx",
        "aiohttp",
        "urllib",
        "smtplib",
        "subprocess",
        "socket",
        # Persistence -- competency.py must never write to the database
        # itself; MemoryStore remains the sole write path.
        "aiosqlite",
        "sqlite3",
    }
    offending = imported_names & forbidden_imports
    assert not offending, f"competency.py imports forbidden module(s): {offending}"

    # Belt-and-suspenders: no direct reference to skill-execution machinery
    # or MemoryStore either, checked as actual Name/Attribute nodes (not a
    # substring match, for the same docstring-false-positive reason above).
    referenced_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced_names.add(node.attr)

    forbidden_references = {"SkillRegistry", "execute_action", "MemoryStore"}
    offending_refs = referenced_names & forbidden_references
    assert not offending_refs, f"competency.py references forbidden name(s): {offending_refs}"
