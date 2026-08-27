"""OP-W003: retrieval mode must be known and truthfully reported.

Real-World Test #1 ran retrieval on a deterministic fallback embedder, and
nothing said so. These tests pin the three properties that made that state
unreportable, so it cannot return:

  1. the fallback is explicit, never automatic;
  2. stored provenance describes what actually ran, and hash vectors are never
     searched as part of a semantic population;
  3. every surface answers "what is retrieval doing" from one accessor, and
     answers it about the running state rather than the configured one.

None of these assert a retrieval *quality* number. Quality is measured, not
gated -- see `tests/test_retrieval_eval_fixture.py`.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from bartholomew.kernel.embedding_engine import (
    FALLBACK_MODEL,
    FALLBACK_PROVIDER,
    KIND_DETERMINISTIC,
    KIND_SEMANTIC,
    KIND_UNVERIFIED,
    EmbedderUnavailableError,
    EmbeddingConfig,
    EmbeddingEngine,
    EmbeddingEngineFactory,
    EmbeddingMode,
    LocalSBERTProvider,
)
from bartholomew.kernel.vector_store import VectorStore


def _unit_vector(seed: int, dim: int = 384) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim).astype(np.float32)
    return vec / np.linalg.norm(vec)


def _memories_table(db_path: str, ids=(1, 2, 3)) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS memories ("
            "id INTEGER PRIMARY KEY, kind TEXT, key TEXT, value TEXT, summary TEXT, ts TEXT)",
        )
        for memory_id in ids:
            conn.execute(
                "INSERT OR REPLACE INTO memories (id, kind, key, value, summary, ts) "
                "VALUES (?, 'note', ?, ?, ?, '2026-01-01')",
                (memory_id, f"k{memory_id}", f"value {memory_id}", f"summary {memory_id}"),
            )
        conn.commit()


class TestFallbackIsExplicit:
    """The deterministic embedder serves only when deliberately permitted."""

    def test_missing_library_fails_closed_without_permission(self, monkeypatch):
        monkeypatch.delenv("BARTHO_EMBED_ALLOW_FALLBACK", raising=False)

        with pytest.raises(EmbedderUnavailableError) as excinfo:
            LocalSBERTProvider(model_id="definitely-not-a-real-model", dim=384)

        # The reason must name the remedy, not just the error: an operator
        # reading this needs to know what to do about it.
        assert "provision" in str(excinfo.value).lower()

    def test_fallback_serves_only_when_explicitly_allowed(self, monkeypatch):
        monkeypatch.setenv("BARTHO_EMBED_ALLOW_FALLBACK", "1")

        provider = LocalSBERTProvider(model_id="definitely-not-a-real-model", dim=384)

        assert provider.fallback is True
        vectors = provider.embed(["hello"])
        assert vectors.shape == (1, 384)
        assert np.isclose(np.linalg.norm(vectors[0]), 1.0, atol=1e-5)

    def test_fallback_never_claims_to_be_the_configured_model(self, monkeypatch):
        """The specific OP-W003 defect: hash vectors wearing a real model's name."""
        monkeypatch.setenv("BARTHO_EMBED_ALLOW_FALLBACK", "1")

        engine = EmbeddingEngine(
            EmbeddingConfig(provider="local-sbert", model="BAAI/bge-small-en-v1.5", dim=384),
        )
        status = engine.status()

        assert status.mode is EmbeddingMode.DEV_FALLBACK
        assert status.semantic is False
        assert status.degraded is True
        # Neither the configured provider nor the configured model survives
        # into the reported or stored identity.
        assert status.provider == FALLBACK_PROVIDER
        assert status.model == FALLBACK_MODEL
        assert engine.storage_identity == (
            FALLBACK_PROVIDER,
            FALLBACK_MODEL,
            KIND_DETERMINISTIC,
        )

    def test_ordinary_load_does_not_permit_downloads(self):
        """Retrieval must never be able to trigger a model fetch."""
        cfg = EmbeddingEngineFactory()._load_config()
        assert cfg.allow_download is False


class TestStoredProvenanceIsTruthful:
    """Hash vectors and semantic vectors are separate populations, always."""

    def test_upsert_rejects_a_non_writable_kind(self, tmp_path):
        db_path = str(tmp_path / "v.db")
        _memories_table(db_path)
        store = VectorStore(db_path)

        # 'unverified' is a migration marker. No writer may claim it, because
        # claiming it would make a fresh write indistinguishable from a row
        # whose embedder is genuinely unknown.
        with pytest.raises(ValueError):
            store.upsert(1, _unit_vector(1), "summary", "p", "m", KIND_UNVERIFIED)

    def test_semantic_search_never_returns_deterministic_vectors(self, tmp_path):
        db_path = str(tmp_path / "v.db")
        _memories_table(db_path)
        store = VectorStore(db_path)

        vec = _unit_vector(7)
        store.upsert(1, vec, "summary", FALLBACK_PROVIDER, FALLBACK_MODEL, KIND_DETERMINISTIC)
        store.upsert(2, vec, "summary", "local-sbert", "real-model", KIND_SEMANTIC)

        semantic = store.search(
            vec,
            top_k=10,
            embedder_kind=KIND_SEMANTIC,
            apply_consent_gate=False,
        )
        deterministic = store.search(
            vec,
            top_k=10,
            embedder_kind=KIND_DETERMINISTIC,
            apply_consent_gate=False,
        )

        assert [mid for mid, _ in semantic] == [2]
        assert [mid for mid, _ in deterministic] == [1]

    def test_population_filter_survives_allow_mismatch(self, tmp_path):
        """allow_mismatch relaxes provider/model. It must not relax the kind."""
        db_path = str(tmp_path / "v.db")
        _memories_table(db_path)
        store = VectorStore(db_path)

        vec = _unit_vector(11)
        store.upsert(1, vec, "summary", FALLBACK_PROVIDER, FALLBACK_MODEL, KIND_DETERMINISTIC)

        results = store.search(
            vec,
            top_k=10,
            provider="local-sbert",
            model="real-model",
            dim=384,
            allow_mismatch=True,
            embedder_kind=KIND_SEMANTIC,
            apply_consent_gate=False,
        )

        assert results == []


class TestLegacyRowsAreInvalidatedNotTrusted:
    """Rows predating the column are excluded, and nothing is destroyed."""

    def _legacy_db(self, tmp_path) -> str:
        db_path = str(tmp_path / "legacy.db")
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY, kind TEXT, key TEXT,
                    value TEXT, summary TEXT, ts TEXT
                );
                CREATE TABLE memory_embeddings (
                    embedding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL,
                    source TEXT NOT NULL CHECK(source IN ('summary','full')),
                    dim INTEGER NOT NULL, vec BLOB NOT NULL, norm REAL NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """,
            )
            conn.execute(
                "INSERT INTO memories VALUES (1,'note','k','text','text','2026-01-01')",
            )
            vec = _unit_vector(3)
            conn.execute(
                "INSERT INTO memory_embeddings "
                "(memory_id, source, dim, vec, norm, provider, model) "
                "VALUES (1,'summary',384,?,1.0,'local-sbert','BAAI/bge-small-en-v1.5')",
                (vec.tobytes(),),
            )
            conn.commit()
        return db_path

    def test_migration_marks_pre_existing_rows_unverified(self, tmp_path):
        db_path = self._legacy_db(tmp_path)

        VectorStore(db_path)  # migrates on construction

        with sqlite3.connect(db_path) as conn:
            kinds = [row[0] for row in conn.execute("SELECT embedder_kind FROM memory_embeddings")]
        # Not guessed as semantic just because the row *says* a real model.
        assert kinds == [KIND_UNVERIFIED]

    def test_unverified_rows_are_excluded_from_every_population(self, tmp_path):
        db_path = self._legacy_db(tmp_path)
        store = VectorStore(db_path)
        qvec = _unit_vector(3)

        assert store.search(qvec, embedder_kind=KIND_SEMANTIC, apply_consent_gate=False) == []
        assert store.search(qvec, embedder_kind=KIND_DETERMINISTIC, apply_consent_gate=False) == []
        # And also from an unfiltered search, which is the default any older
        # caller would hit.
        assert store.search(qvec, apply_consent_gate=False) == []

    def test_migration_destroys_nothing(self, tmp_path):
        db_path = self._legacy_db(tmp_path)

        VectorStore(db_path)

        with sqlite3.connect(db_path) as conn:
            memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            embeddings = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
        # Excluded from retrieval, still recoverable by a later rebuild.
        assert (memories, embeddings) == (1, 1)

    def test_migration_is_idempotent(self, tmp_path):
        db_path = self._legacy_db(tmp_path)

        VectorStore(db_path)
        VectorStore(db_path)

        store = VectorStore(db_path)
        assert store.count_by_kind() == {KIND_UNVERIFIED: 1}


class TestModeIsReportable:
    """Every state is distinguishable, and one accessor answers for all."""

    def test_disabled_is_reported_as_disabled_not_degraded(self, monkeypatch):
        monkeypatch.delenv("BARTHO_EMBED_ENABLED", raising=False)

        status = EmbeddingEngineFactory().status()

        assert status.mode is EmbeddingMode.DISABLED
        assert status.semantic is False
        # Deliberately off is doing what it was told, not failing.
        assert status.degraded is False

    def test_unavailable_is_reported_without_raising(self, monkeypatch):
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        monkeypatch.delenv("BARTHO_EMBED_ALLOW_FALLBACK", raising=False)
        monkeypatch.setenv("BARTHO_EMBED_MODEL_PATH", "/nonexistent/model/path")

        status = EmbeddingEngineFactory().status()

        # Asking what retrieval is doing must never itself fail.
        assert status.mode is EmbeddingMode.UNAVAILABLE
        assert status.semantic is False
        assert status.degraded is True
        assert status.reason

    def test_dev_fallback_is_reported_as_non_semantic(self, monkeypatch):
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        monkeypatch.setenv("BARTHO_EMBED_ALLOW_FALLBACK", "1")

        status = EmbeddingEngineFactory().status()

        assert status.mode is EmbeddingMode.DEV_FALLBACK
        assert status.semantic is False

    def test_describe_retrieval_separates_mode_from_meaning(self, monkeypatch):
        """A hybrid config on a hash embedder is not semantic retrieval."""
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        monkeypatch.setenv("BARTHO_EMBED_ALLOW_FALLBACK", "1")
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "hybrid")

        import bartholomew.kernel.embedding_engine as engine_module
        from bartholomew.kernel.retrieval import describe_retrieval

        monkeypatch.setattr(engine_module, "_embedding_factory", EmbeddingEngineFactory())

        described = describe_retrieval()

        assert described["mode_configured"] == "hybrid"
        assert described["semantic"] is False
        assert described["degraded"] is True
        assert described["reason"]

    def test_health_reports_retrieval_state(self):
        """The health surface answers from the same accessor, not from config."""
        from fastapi.testclient import TestClient

        from bartholomew_api_bridge_v0_1.services.api.app import app

        with TestClient(app) as client:
            payload = client.get("/api/health").json()

        assert "retrieval_mode_configured" in payload
        assert "retrieval_mode_effective" in payload
        assert "retrieval_semantic" in payload
        assert "embedding_mode" in payload
