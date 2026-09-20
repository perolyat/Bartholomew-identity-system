"""
R-RETRIEVAL-1: the FTS5 availability contract, proved by its forbidden states.

`tests/test_retrieval_fts5_fallback.py` already pins what *must* happen when
FTS5 is missing. This module exists for the other half, which is where the
defect lived: what must **not** happen.

The defect, restated from `RISKS.md`:

* FTS5 availability was cached in one process-global, unkeyed boolean, so the
  first probe in a process answered for every database for the life of that
  process;
* every failure -- a SQLite build without FTS5, a locked database, an
  unopenable path, a test that patched the probe -- collapsed into the single
  value `False`, at two sites, the lower of which destroyed the distinction
  before the upper one could see it;
* nothing in `describe_retrieval()` reported any of it.

So the statements below are mostly negative: a transient failure must not
latch, one database must not answer for another, an unfinished probe must not
be recorded as a capability fact, and reporting must not claim either health
or degradation it has not established. Several of them fail against the
implementation this package replaces; that is the point of them.
"""

import os
import sqlite3
from unittest.mock import patch

import pytest

from bartholomew.kernel import retrieval
from bartholomew.kernel.fts_client import (
    FTS5ProbeResult,
    FTS5Status,
    fts5_available,
    probe_fts5,
)
from bartholomew.kernel.retrieval import (
    _check_fts5_once,
    check_fts5,
    describe_retrieval,
    get_retriever,
    reset_fts5_cache,
)
from conftest import SKIP_WINDOWS_FTS

#: FTS5 is genuinely missing from this SQLite build. Conclusive.
ABSENT = FTS5ProbeResult(FTS5Status.ABSENT, "no such module: fts5")
#: The probe could not be completed. Says nothing either way.
TRANSIENT = FTS5ProbeResult(FTS5Status.PROBE_ERROR, "OperationalError: database is locked")
#: FTS5 works.
AVAILABLE = FTS5ProbeResult(FTS5Status.AVAILABLE)


@pytest.fixture(autouse=True)
def _clean_fts5_cache():
    """No test in this module may inherit another's cached answer."""
    reset_fts5_cache()
    yield
    reset_fts5_cache()


def _make_db(path: str) -> str:
    """A minimal, openable database at `path`."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memories ("
        "id INTEGER PRIMARY KEY, kind TEXT, key TEXT, "
        "value TEXT, summary TEXT, ts TEXT)",
    )
    conn.commit()
    conn.close()
    return path


class _FakeConn:
    """A connection whose CREATE/DROP outcomes the test chooses."""

    def __init__(self, create_error=None, drop_error=None):
        self._create_error = create_error
        self._drop_error = drop_error
        self.statements = []

    def execute(self, sql, *args):
        self.statements.append(sql)
        if "CREATE VIRTUAL TABLE" in sql and self._create_error is not None:
            raise self._create_error
        if "DROP TABLE" in sql and self._drop_error is not None:
            raise self._drop_error

    def close(self):
        pass


# ---------------------------------------------------------------------------
# 1. Failure classification: absence and failure are different facts
# ---------------------------------------------------------------------------


class TestProbeClassification:
    def test_genuine_absence_is_reported_as_absence_and_is_conclusive(self):
        conn = _FakeConn(create_error=sqlite3.OperationalError("no such module: fts5"))

        result = probe_fts5(conn)

        assert result.status is FTS5Status.ABSENT
        assert result.conclusive is True
        assert result.available is False
        assert "fts5" in (result.detail or "").lower()

    def test_transient_sqlite_failure_is_not_reported_as_absence(self):
        """A locked database is not a SQLite build without FTS5.

        This is the collapse R-RETRIEVAL-1 is about. Under the previous
        implementation both answers were the bare value `False` and nothing
        downstream could tell them apart.
        """
        conn = _FakeConn(create_error=sqlite3.OperationalError("database is locked"))

        result = probe_fts5(conn)

        assert result.status is FTS5Status.PROBE_ERROR
        assert result.status is not FTS5Status.ABSENT
        assert result.conclusive is False
        assert result.available is False

    def test_arbitrary_exception_is_a_probe_error_not_an_absence_claim(self):
        """Including the degenerate case of no connection at all."""
        result = probe_fts5(None)

        assert result.status is FTS5Status.PROBE_ERROR
        assert result.conclusive is False

    def test_a_successful_create_is_not_unproved_by_a_failed_cleanup(self):
        """FTS5 demonstrably worked; dropping the scratch table is cleanup.

        The previous implementation reported this case as unavailable, which
        is a false negative on a capability that had just been exercised.
        """
        conn = _FakeConn(drop_error=sqlite3.OperationalError("database is locked"))

        result = probe_fts5(conn)

        assert result.status is FTS5Status.AVAILABLE
        assert result.available is True

    @pytest.mark.parametrize("failure", [ABSENT, TRANSIENT])
    def test_boolean_view_stays_fail_safe_for_every_non_available_outcome(self, failure):
        """`fts5_available()` keeps its old contract: False unless proven.

        The repair adds a truthful status; it does not make any existing
        caller optimistic. What it forbids is *durable* decisions being made
        on this boolean alone.
        """
        with patch("bartholomew.kernel.fts_client.probe_fts5", return_value=failure):
            assert fts5_available(_FakeConn()) is False

    def test_boolean_view_is_true_when_available(self):
        conn = sqlite3.connect(":memory:")
        try:
            assert fts5_available(conn) is probe_fts5(conn).available
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 2. Availability scope: one database never answers for another
# ---------------------------------------------------------------------------


class TestAvailabilityScope:
    def test_a_result_from_database_a_does_not_determine_database_b(self, tmp_path):
        """The headline regression.

        Database A probes absent; database B probes available. B must get B's
        answer. Against the process-global cache this fails: A's `False` is
        returned for B, for the life of the process.
        """
        db_a = _make_db(str(tmp_path / "a.db"))
        db_b = _make_db(str(tmp_path / "b.db"))
        answers = {os.path.abspath(db_a): ABSENT, os.path.abspath(db_b): AVAILABLE}
        asked = []

        def fake_probe(conn):
            # `probe_fts5` is handed a connection, not a path, so the test
            # answers for whichever database `check_fts5` was last asked about.
            return answers[os.path.abspath(asked[-1])]

        def ask(db_path):
            asked.append(db_path)
            return check_fts5(db_path)

        with patch("bartholomew.kernel.retrieval.probe_fts5", side_effect=fake_probe):
            result_a = ask(db_a)
            result_b = ask(db_b)

        assert result_a.status is FTS5Status.ABSENT
        assert result_b.status is FTS5Status.AVAILABLE
        assert result_b.available is True

    def test_two_spellings_of_one_database_share_a_single_answer(self, tmp_path, monkeypatch):
        """Scoping by database must mean the database, not the string."""
        monkeypatch.chdir(tmp_path)
        _make_db(str(tmp_path / "same.db"))

        with patch(
            "bartholomew.kernel.retrieval.probe_fts5",
            return_value=AVAILABLE,
        ) as mock:
            check_fts5("same.db")
            check_fts5(str(tmp_path / "same.db"))
            check_fts5("./same.db")

        assert mock.call_count == 1

    def test_resetting_one_database_leaves_the_others_cached(self, tmp_path):
        db_a = _make_db(str(tmp_path / "a.db"))
        db_b = _make_db(str(tmp_path / "b.db"))

        with patch(
            "bartholomew.kernel.retrieval.probe_fts5",
            return_value=AVAILABLE,
        ) as mock:
            check_fts5(db_a)
            check_fts5(db_b)
            assert mock.call_count == 2

            reset_fts5_cache(db_a)

            check_fts5(db_b)  # still cached
            assert mock.call_count == 2

            check_fts5(db_a)  # re-probed
            assert mock.call_count == 3


# ---------------------------------------------------------------------------
# 3. Recovery: a failure that answered nothing must not persist as an answer
# ---------------------------------------------------------------------------


class TestRecovery:
    def test_a_transient_probe_failure_does_not_poison_a_later_valid_probe(self, tmp_path):
        """The second headline regression.

        One failed probe, then a good one. The good one must win. Against the
        previous implementation the first `False` latched for the process and
        the second probe never ran at all.
        """
        db = _make_db(str(tmp_path / "recover.db"))

        with patch(
            "bartholomew.kernel.retrieval.probe_fts5",
            side_effect=[TRANSIENT, AVAILABLE],
        ) as mock:
            first = check_fts5(db)
            second = check_fts5(db)

        assert first.status is FTS5Status.PROBE_ERROR
        assert second.status is FTS5Status.AVAILABLE
        assert second.available is True
        assert mock.call_count == 2, "a failed probe must not suppress the next one"

    def test_an_inconclusive_probe_is_never_recorded_as_a_capability_answer(self, tmp_path):
        """A failed or incomplete probe is not a successful capability check."""
        db = _make_db(str(tmp_path / "unknown.db"))

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=TRANSIENT):
            check_fts5(db)

        assert retrieval._fts5_probe_cache == {}, "an unfinished probe was cached as an answer"

    def test_an_unopenable_database_is_unknown_not_absent(self, tmp_path):
        """A path that cannot be opened says nothing about the SQLite build.

        The previous implementation wrapped `sqlite3.connect()` in a bare
        `except Exception: available = False` and cached that, so one bad path
        answered "FTS5 does not exist" for every database in the process.
        """
        unopenable = str(tmp_path)  # a directory, not a database

        result = check_fts5(unopenable)

        assert result.status is FTS5Status.PROBE_ERROR
        assert result.status is not FTS5Status.ABSENT
        assert result.conclusive is False
        assert retrieval._fts5_probe_cache == {}

    def test_conclusive_answers_are_cached_and_not_reprobed(self, tmp_path):
        """Recovery must not cost a probe on every retrieval."""
        db = _make_db(str(tmp_path / "cached.db"))

        with patch(
            "bartholomew.kernel.retrieval.probe_fts5",
            return_value=AVAILABLE,
        ) as mock:
            for _ in range(5):
                assert check_fts5(db).available is True

        assert mock.call_count == 1

    def test_absence_is_also_cached_rather_than_reprobed_forever(self, tmp_path):
        db = _make_db(str(tmp_path / "absent.db"))

        with patch(
            "bartholomew.kernel.retrieval.probe_fts5",
            return_value=ABSENT,
        ) as mock:
            for _ in range(3):
                assert check_fts5(db).status is FTS5Status.ABSENT

        assert mock.call_count == 1

    def test_the_boolean_helper_still_answers_from_the_same_state(self, tmp_path):
        db = _make_db(str(tmp_path / "helper.db"))

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=ABSENT):
            assert _check_fts5_once(db) is False

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=AVAILABLE):
            # Still the cached conclusive answer, not the new patch.
            assert _check_fts5_once(db) is False
            reset_fts5_cache(db)
            assert _check_fts5_once(db) is True


# ---------------------------------------------------------------------------
# 4. Configuration is never silently changed by fallback or recovery
# ---------------------------------------------------------------------------


@SKIP_WINDOWS_FTS
class TestConfigurationIsNotRewritten:
    def test_config_fts_degrades_to_vector_only_on_proven_absence(self, tmp_path, monkeypatch):
        """The existing, deliberate behaviour -- unchanged by this package."""
        db = _make_db(str(tmp_path / "cfg.db"))
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "fts")

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=ABSENT):
            retriever = get_retriever(db_path=db)

        assert type(retriever).__name__ == "VectorRetrieverAdapter"

    def test_config_fts_does_not_degrade_on_an_unproven_absence(self, tmp_path, monkeypatch):
        """Dropping the lexical arm requires proof, not a failed probe.

        This is the costly case: in this repository's default environment the
        degraded `vector` mode can raise `EmbedderUnavailableError` and return
        no retriever at all, so degrading on an inconclusive probe can turn a
        transient error into a total retrieval outage.
        """
        db = _make_db(str(tmp_path / "cfg2.db"))
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "fts")

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=TRANSIENT):
            retriever = get_retriever(db_path=db)

        assert type(retriever).__name__ == "FTSOnlyRetriever"

    def test_an_explicit_fts_request_is_never_rewritten_by_fallback_logic(
        self,
        tmp_path,
        monkeypatch,
    ):
        db = _make_db(str(tmp_path / "explicit.db"))
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "hybrid")

        for outcome in (ABSENT, TRANSIENT):
            reset_fts5_cache()
            with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=outcome):
                retriever = get_retriever(mode="fts", db_path=db)
            assert type(retriever).__name__ == "FTSOnlyRetriever"

    def test_an_explicit_vector_request_is_unaffected_by_fts_recovery(self, tmp_path):
        db = _make_db(str(tmp_path / "vec.db"))

        with patch(
            "bartholomew.kernel.retrieval.probe_fts5",
            side_effect=[TRANSIENT, AVAILABLE],
        ):
            first = get_retriever(mode="vector", db_path=db)
            second = get_retriever(mode="vector", db_path=db)

        assert type(first).__name__ == "VectorRetrieverAdapter"
        assert type(second).__name__ == "VectorRetrieverAdapter"

    def test_recovery_restores_the_configured_mode_and_invents_no_other(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Reprobing must not become a back door that changes retrieval mode.

        After an absent probe degrades a configured `fts` to vector, clearing
        the cache and probing available must return the *configured* mode --
        not hybrid, not a mode nothing asked for.
        """
        db = _make_db(str(tmp_path / "restore.db"))
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "fts")

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=ABSENT):
            degraded = get_retriever(db_path=db)
        assert type(degraded).__name__ == "VectorRetrieverAdapter"

        reset_fts5_cache(db)
        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=AVAILABLE):
            recovered = get_retriever(db_path=db)
        assert type(recovered).__name__ == "FTSOnlyRetriever"


# ---------------------------------------------------------------------------
# 5. The default hybrid / chat path keeps its lexical arm
# ---------------------------------------------------------------------------


@SKIP_WINDOWS_FTS
class TestDefaultHybridPath:
    def test_hybrid_keeps_its_lexical_arm_when_another_database_probed_absent(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Unrelated cached state must not cost the chat path its FTS arm.

        `runtime_contract.py` reaches retrieval as `get_retriever(db_path=...,
        memory_store=...)` with the mode resolved from `kernel.yaml` --
        `hybrid`. A probe that answered "absent" about some *other* database
        must have no bearing on it.
        """
        other = _make_db(str(tmp_path / "other.db"))
        chat_db = _make_db(str(tmp_path / "chat.db"))
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "hybrid")

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=ABSENT):
            check_fts5(other)

        assert check_fts5(chat_db).available is True

        retriever = get_retriever(db_path=chat_db)
        assert type(retriever).__name__ == "HybridRetriever"

    @pytest.mark.asyncio
    async def test_lexical_recall_survives_an_unrelated_absent_probe(self, tmp_path, monkeypatch):
        """End to end: the seeded row still comes back.

        Type stability is not recall. This seeds a memory, poisons the cache
        with another database's "absent", and asserts the row is still
        retrieved through the ordinary hybrid path.
        """
        from bartholomew.kernel.memory_store import MemoryStore

        other = _make_db(str(tmp_path / "unrelated.db"))
        db = str(tmp_path / "recall.db")
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "hybrid")

        store = MemoryStore(db)
        await store.init()
        await store.upsert_memory(
            "fact",
            "kestrel_note",
            "The kestrel hovers above the motorway verge every morning",
            "2026-01-01T09:00:00Z",
        )

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=ABSENT):
            check_fts5(other)

        retriever = get_retriever(db_path=db, memory_store=store)
        results = retriever.retrieve("kestrel")

        assert type(retriever).__name__ == "HybridRetriever"
        assert results, "lexical recall was lost to an unrelated database's probe result"
        assert any("kestrel" in (r.snippet or "").lower() for r in results)


# ---------------------------------------------------------------------------
# 6. Reporting: neither false health nor false degradation
# ---------------------------------------------------------------------------


@SKIP_WINDOWS_FTS
class TestReporting:
    def test_reporting_does_not_claim_effective_lexical_retrieval_when_absent(
        self,
        tmp_path,
        monkeypatch,
    ):
        db = _make_db(str(tmp_path / "report_absent.db"))
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "hybrid")

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=ABSENT):
            described = describe_retrieval(db_path=db)

        assert described["fts"]["status"] == "absent"
        assert described["fts"]["available"] is False
        assert described["mode_configured"] == "hybrid"
        assert described["mode_effective"] != "hybrid"
        assert described["mode_effective"] in ("vector", "none")
        assert described["degraded"] is True
        assert "FTS5" in (described["reason"] or "")

    def test_reporting_does_not_claim_degradation_when_fts_is_available(
        self,
        tmp_path,
        monkeypatch,
    ):
        db = _make_db(str(tmp_path / "report_ok.db"))
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "hybrid")

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=AVAILABLE):
            described = describe_retrieval(db_path=db)

        assert described["fts"]["status"] == "available"
        assert described["fts"]["available"] is True
        assert described["mode_effective"] == "hybrid"
        assert "FTS5 is absent" not in (described["reason"] or "")

    def test_reporting_does_not_invent_degradation_from_an_unknown_probe(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Unknown is reported as unknown -- not as absence, not as health.

        Claiming degradation that has not been established is the same class
        of untruth as hiding degradation that has.
        """
        db = _make_db(str(tmp_path / "report_unknown.db"))
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "hybrid")

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=TRANSIENT):
            described = describe_retrieval(db_path=db)

        assert described["fts"]["status"] == "probe_error"
        assert described["fts"]["available"] is False
        assert described["fts"]["conclusive"] is False
        assert described["mode_effective"] == "hybrid", "an unknown probe changed the mode"
        assert "FTS5 is absent" not in (described["reason"] or "")

    def test_reporting_never_claims_availability_for_an_unprobeable_database(self, tmp_path):
        described = describe_retrieval(db_path=str(tmp_path))  # a directory

        assert described["fts"]["available"] is False
        assert described["fts"]["status"] == "probe_error"

    def test_reporting_names_the_database_it_measured(self, tmp_path, monkeypatch):
        """Configuration and runtime capability are different questions."""
        db = _make_db(str(tmp_path / "named.db"))
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "hybrid")

        described = describe_retrieval(db_path=db)

        assert described["fts"]["db_path"] == db

    def test_explicit_fts_on_an_absent_build_is_reported_as_no_effective_arm(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Honouring the request does not make the request work."""
        db = _make_db(str(tmp_path / "explicit_report.db"))

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=ABSENT):
            described = describe_retrieval(mode="fts", db_path=db)

        assert described["mode_configured"] == "fts"
        assert described["mode_effective"] == "none"
        assert described["degraded"] is True

    def test_health_surface_carries_the_fts_answer(self, tmp_path, monkeypatch):
        """`/api/health` reads the same accessor, so it cannot drift from it."""
        from bartholomew_api_bridge_v0_1.services.api.app import _retrieval_health

        db = _make_db(str(tmp_path / "health.db"))
        monkeypatch.setenv("BARTHO_DB_PATH", db)

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=ABSENT):
            payload = _retrieval_health()

        assert payload["retrieval_fts_status"] == "absent"
        assert payload["retrieval_fts_available"] is False


# ---------------------------------------------------------------------------
# 7. A separate defect, recorded here rather than repaired here
# ---------------------------------------------------------------------------


@SKIP_WINDOWS_FTS
class TestSeparatelyRecordedDefect:
    def test_hybrid_without_embedder_or_fts_returns_a_retriever_that_cannot_retrieve(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Characterisation test for R-RETRIEVAL-2 -- NOT a repair.

        With no embedder *and* no FTS5, `get_retriever()` still hands back an
        `FTSOnlyRetriever` over a database with no FTS5 behind it. Reporting
        is truthful about that -- `describe_retrieval()` says `none` -- but the
        factory's own contract is not: it returns an object that cannot serve
        its purpose, rather than raising as the analogous vector-only case
        does.

        This is recorded as a separate risk because it is a *factory contract*
        defect, not the availability-state defect R-RETRIEVAL-1 covers, and
        repairing it is not needed to make availability truthful. This test
        pins today's behaviour so the separate fix has a starting point; if it
        starts failing because `get_retriever()` began raising, that is
        R-RETRIEVAL-2 being closed, not a regression here.
        """
        import bartholomew.kernel.embedding_engine as engine_module

        db = _make_db(str(tmp_path / "nothing.db"))
        monkeypatch.setenv("BARTHO_RETRIEVAL_MODE", "hybrid")
        monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
        monkeypatch.delenv("BARTHO_EMBED_ALLOW_FALLBACK", raising=False)
        monkeypatch.setattr(
            engine_module,
            "_embedding_factory",
            engine_module.EmbeddingEngineFactory(),
        )

        status = engine_module.get_embedding_status()
        if status.mode is not engine_module.EmbeddingMode.UNAVAILABLE:
            pytest.skip(f"a real embedder is provisioned here ({status.mode.value})")

        with patch("bartholomew.kernel.retrieval.probe_fts5", return_value=ABSENT):
            described = describe_retrieval(db_path=db)
            retriever = get_retriever(db_path=db)

        # Reporting is truthful: nothing is operational.
        assert described["mode_effective"] == "none"
        assert described["degraded"] is True

        # The factory is not: it returns a lexical retriever with no lexical
        # capability behind it. Recorded as R-RETRIEVAL-2.
        assert type(retriever).__name__ == "FTSOnlyRetriever"
