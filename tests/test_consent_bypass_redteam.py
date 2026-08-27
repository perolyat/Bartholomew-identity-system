"""
Red-team tests for RISKS.md's R1 ("Consent bypass / privacy leakage").

R1's own status note (2026-07-21) audited every `apply_consent_gate` call
site by grep and found no production bypass, and separately fixed a
fail-*closed* bug (excluded every requires_consent memory unconditionally --
see tests/test_retrieval_consent_enforcement.py). What it explicitly left
open: "No red-team test suite for bypass paths exists yet."

This module is that suite. It deliberately does NOT go through
MemoryStore.upsert_memory()'s write path: `MemoryRulesEngine.should_store()`
already hard-blocks any `requires_consent` memory at write time (not merely
"store but hide from retrieval" -- see that method's docstring), so the
scenario R1 actually names -- "a memory that should be excluded" already
existing and being retrieved -- can only be reproduced the way it would
happen for real: content that reaches the `memories`/FTS/vector tables some
other way (a rules.yaml reclassification after the fact, a migration, direct
DB access, a future write-path bug), then gets driven through every
*retrieval* surface a real caller would actually use:

  - get_retriever() (the intended factory, all three modes)
  - HybridRetriever(db_path=...) and FTSOnlyRetriever(db_path=...)
    constructed directly, with no rules_engine passed -- exactly the
    construction each class's own docstring usage example shows, and
    exactly what a future caller who skips the factory would copy-paste
  - consent granted then revoked, mid-session, on the same retriever
    instance (no stale/cached bypass)

It also turns two previously-manual, one-time audits into permanent
regression guards: that no production code path ever calls
`apply_consent_gate=False`, and that no public `.retrieve()` facade even
exposes a parameter capable of disabling the gate.
"""

from __future__ import annotations

import ast
import inspect
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import pytest

from bartholomew.kernel.embedding_engine import get_embedding_engine
from bartholomew.kernel.fts_client import FTSClient
from bartholomew.kernel.hybrid_retriever import HybridRetriever
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.retrieval import FTSOnlyRetriever, VectorRetrieverAdapter, get_retriever
from bartholomew.kernel.vector_store import VectorStore
from conftest import SKIP_WINDOWS_FTS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _index_memory(db_path: str, memory_id: int, kind: str, key: str, value: str) -> None:
    """
    Land a memory in the DB and index it in both FTS and the vector store,
    using the *real* embedding engine and its effective storage identity
    (matching what HybridRetriever/Retriever query with by default) -- bypassing MemoryStore.upsert_memory()
    entirely, since that write path already hard-blocks requires_consent
    content (see module docstring). This is the retrieval-layer's own
    responsibility being tested in isolation from the write-time guard.
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO memories (id, kind, key, value, ts) VALUES (?, ?, ?, ?, ?)",
        (memory_id, kind, key, value, _now()),
    )
    conn.commit()
    conn.close()

    fts = FTSClient(db_path)
    fts.init_schema()
    fts.upsert(memory_id, value)

    embed_engine = get_embedding_engine()
    # `storage_identity`, not `config`: the identity that actually produced the
    # vector. Seeding with the configured identity would file a deterministic
    # vector into the semantic population, and the retriever -- which searches
    # only its own engine's population -- would then never see it.
    provider, model, embedder_kind = embed_engine.storage_identity
    vec = embed_engine.embed_texts([value])[0]
    VectorStore(db_path).upsert(
        memory_id=memory_id,
        vec=vec,
        source="summary",
        provider=provider,
        model=model,
        embedder_kind=embedder_kind,
    )


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except (PermissionError, FileNotFoundError):
        pass


@pytest.fixture
async def seeded_db(temp_db):
    """
    A small corpus, each memory landed and indexed identically via
    `_index_memory()` -- only their content and `memory_consent` row differ.
    All three share "machine learning" so a single query surfaces all of
    them as raw candidates before any gating -- exclusion in the assertions
    below is therefore about the gate, not about the query simply missing
    the excluded memory.

      - plain: an ordinary memory, always retrievable
      - unconsented: matches ask_before_store ("bank"/"account number"),
        present in the DB, but with NO memory_consent row -- must never surface
      - consented: same classification, but WITH a memory_consent row --
        must surface (regression guard for the already-fixed fail-closed bug
        in tests/test_retrieval_consent_enforcement.py)
    """
    store = MemoryStore(temp_db)
    await store.init()
    await store.close()

    _index_memory(temp_db, 1001, "chat", "plain", "robot learning machine talk about hobbies")
    _index_memory(
        temp_db,
        1002,
        "user",
        "unconsented_secret",
        "my bank account number is 555000111, noted during a machine learning course",
    )
    _index_memory(
        temp_db,
        1003,
        "user",
        "consented_secret",
        "my bank account number is 555000222, noted during another machine learning course",
    )

    async with aiosqlite.connect(temp_db) as db:
        await db.execute(
            "INSERT OR IGNORE INTO memory_consent (memory_id, source) VALUES (?, ?)",
            (1003, "test"),
        )
        await db.commit()

    return {
        "db_path": temp_db,
        "plain_id": 1001,
        "unconsented_id": 1002,
        "consented_id": 1003,
    }


# =============================================================================
# 1. End-to-end: every production retrieval surface must exclude the
#    unconsented memory and include the consented/plain ones.
# =============================================================================


@SKIP_WINDOWS_FTS
class TestEndToEndSurfacesNeverLeakUnconsentedMemory:
    @pytest.mark.parametrize("mode", ["hybrid", "vector", "fts"])
    def test_get_retriever_factory_excludes_unconsented_includes_consented(self, seeded_db, mode):
        retriever = get_retriever(mode=mode, db_path=seeded_db["db_path"])
        results = retriever.retrieve("machine learning", top_k=20)
        ids = {r.memory_id for r in results}

        assert seeded_db["unconsented_id"] not in ids, "unconsented ask_before_store memory leaked"
        assert (
            seeded_db["consented_id"] in ids
        ), "consented memory wrongly excluded (fail-closed regression)"
        assert seeded_db["plain_id"] in ids, "plain memory wrongly excluded"

    def test_hybrid_retriever_constructed_directly_per_its_own_docstring_example(self, seeded_db):
        """HybridRetriever's own docstring shows `HybridRetriever(db_path=...)`
        with no `rules_engine` -- the exact construction a future caller who
        skips get_retriever() would copy-paste. Proves the lower-layer
        ConsentGate (applied unconditionally inside FTSClient.search()/
        VectorStore.search()) still holds even when this optional,
        additional layer is omitted -- defense in depth, not a single point
        of failure."""
        retriever = HybridRetriever(db_path=seeded_db["db_path"])
        results = retriever.retrieve("machine learning", top_k=20)
        ids = {r.memory_id for r in results}

        assert seeded_db["unconsented_id"] not in ids
        assert seeded_db["consented_id"] in ids

    def test_fts_only_retriever_constructed_directly_without_rules_engine(self, seeded_db):
        """Same proof for FTSOnlyRetriever, whose docstring/constructor has
        the identical optional-rules_engine shape."""
        retriever = FTSOnlyRetriever(db_path=seeded_db["db_path"])
        results = retriever.retrieve("machine learning", top_k=20)
        ids = {r.memory_id for r in results}

        assert seeded_db["unconsented_id"] not in ids


@SKIP_WINDOWS_FTS
@pytest.mark.asyncio
class TestConsentRevocationTakesEffectImmediately:
    async def test_revoking_consent_re_excludes_on_the_next_call(self, seeded_db):
        """No caching/staleness bypass: the same retriever instance must
        reflect a consent revocation on its very next call."""
        db_path = seeded_db["db_path"]
        retriever = get_retriever(mode="hybrid", db_path=db_path)

        before = {r.memory_id for r in retriever.retrieve("machine learning", top_k=20)}
        assert seeded_db["consented_id"] in before

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "DELETE FROM memory_consent WHERE memory_id = ?",
                (seeded_db["consented_id"],),
            )
            await db.commit()

        after = {r.memory_id for r in retriever.retrieve("machine learning", top_k=20)}
        assert seeded_db["consented_id"] not in after


# =============================================================================
# 2. never_store content that ended up in the DB anyway (bypassing the
#    write-time guard entirely -- direct insert, migration, a future
#    write-path bug) must still never be surfaced by the retrieval layer.
#    This is the retrieval-side backstop R1 actually names, independent of
#    the write-time guard tested in tests/test_consent_gates.py.
# =============================================================================


@SKIP_WINDOWS_FTS
class TestNeverStoreContentPresentInDbIsStillNeverSurfaced:
    @pytest.fixture
    async def db_with_smuggled_never_store_row(self, temp_db):
        store = MemoryStore(temp_db)
        await store.init()
        await store.close()

        _index_memory(
            temp_db,
            999001,
            "environment",
            "smuggled",
            "this describes illegal content mixed with machine learning terms",
        )
        return temp_db

    def test_never_store_row_absent_from_hybrid_retrieval(self, db_with_smuggled_never_store_row):
        retriever = get_retriever(mode="hybrid", db_path=db_with_smuggled_never_store_row)
        results = retriever.retrieve("machine learning", top_k=20)
        assert 999001 not in {r.memory_id for r in results}

    def test_never_store_row_absent_from_fts_search_directly(
        self,
        db_with_smuggled_never_store_row,
    ):
        fts = FTSClient(db_with_smuggled_never_store_row)
        results = fts.search("illegal", apply_consent_gate=True)
        assert 999001 not in {r["id"] for r in results}


# =============================================================================
# 3. The bypass knob itself: no production caller may disable the gate, and
#    no public retrieval facade even exposes a way to.
# =============================================================================


class TestNoProductionCallerDisablesTheGate:
    def test_no_apply_consent_gate_false_in_production_code(self):
        """Formalizes R1's 2026-07-21 one-time grep audit ("the only
        `=False` uses anywhere in the repo are two tests intentionally
        exercising the 'gate off' baseline") into a permanent regression
        guard, using the AST (a real keyword=False argument, not text that
        merely mentions the parameter in a docstring/comment)."""
        repo_root = Path(__file__).resolve().parent.parent
        search_roots = [
            repo_root / "bartholomew",
            repo_root / "bartholomew_api_bridge_v0_1",
            repo_root / "identity_interpreter",
        ]

        offenders = []
        for root in search_roots:
            if not root.exists():
                continue
            for py_file in root.rglob("*.py"):
                tree = ast.parse(py_file.read_text(), filename=str(py_file))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    for kw in node.keywords:
                        if kw.arg != "apply_consent_gate":
                            continue
                        value = kw.value
                        is_false = isinstance(value, ast.Constant) and value.value is False
                        if is_false:
                            offenders.append(f"{py_file}:{node.lineno}")

        assert offenders == [], f"Production call(s) disabling the consent gate: {offenders}"

    def test_no_public_retrieve_facade_exposes_a_gate_bypass_parameter(self):
        """The three retriever facades a real caller uses -- HybridRetriever,
        FTSOnlyRetriever, VectorRetrieverAdapter -- must not surface any
        parameter capable of disabling consent filtering. The knob only
        exists two layers down (FTSClient.search()/VectorStore.search(),
        both audited above), never on the retrieval facade itself."""
        for cls in (HybridRetriever, FTSOnlyRetriever, VectorRetrieverAdapter):
            sig = inspect.signature(cls.retrieve)
            assert (
                "apply_consent_gate" not in sig.parameters
            ), f"{cls.__name__}.retrieve() must not expose a gate-bypass parameter"
