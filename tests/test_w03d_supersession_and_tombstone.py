"""
W03-D acceptance 4 & 5, and `W03_TEST_CONTRACTS.md` Sec.3 (supersession) --
currently-valid wins, history is preserved, a revoked identity stays revoked.

The contract's scenarios, each with a test below:

  * a changed preference
  * a revoked permission
  * a temporary exception
  * a conflicting observation
  * an explicit user correction

and the two properties that make them safe:

  * the currently-valid state wins over the obsolete one while audit history
    is preserved (`key@rN` archive, extended from competency records to
    ordinary memories);
  * a revoked `(kind, key)` cannot be silently recreated by re-learning,
    re-training or personal-fact capture.

Marked `integration` per `W03_TEST_CONTRACTS.md` Sec.8 ("Supersession -- full
suite (Integration)"), except the structural checks at the end, which are
cheap and stay in the PR Fast tier.

Non-vacuity throughout: each scenario asserts both halves -- that the new
state is recalled *and* that the old one is not. A change that dropped the
supersession machinery but kept the write would fail the second half; one
that dropped the archive would fail the first.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from bartholomew.kernel import consent_gate as cg
from bartholomew.kernel.memory_store import (
    MEMORY_REVISION_KIND,
    MemoryProvenance,
    MemoryStore,
)

pytestmark = pytest.mark.integration


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
async def store(tmp_path):
    mem = MemoryStore(str(tmp_path / "w03d_supersession.db"))
    await mem.init()
    yield mem
    await mem.close(checkpoint=False)


def _valid(store: MemoryStore, memory_id: int) -> bool:
    return cg.ConsentGate(store.db_path).validity_verdict(memory_id).currently_valid


# ---------------------------------------------------------------------------
# 1. The five contract scenarios.
# ---------------------------------------------------------------------------


class TestSupersessionScenarios:
    async def test_a_changed_preference_supersedes_the_old_one(self, store):
        await store.upsert_memory(
            "user_profile",
            "preference.morning_drink",
            "Preference: tea in the morning",
            _now(),
            provenance=MemoryProvenance(source_type="user_instruction", asserted_by="user"),
        )
        outcome = await store.supersede_memory(
            "user_profile",
            "preference.morning_drink",
            "Preference: coffee in the morning",
            provenance=MemoryProvenance(source_type="correction", asserted_by="user"),
        )
        assert outcome.stored, outcome

        # The currently-valid state is the new one...
        live = await store.get_memory("user_profile", "preference.morning_drink")
        assert "coffee" in live["value"]
        assert _valid(store, live["id"])

        # ...and the old one is preserved, readable, and never valid.
        archived = await store.get_memory(MEMORY_REVISION_KIND, outcome.archived_key)
        assert archived is not None, "the superseded claim was destroyed"
        assert "tea" in archived["value"]
        assert not _valid(store, archived["id"])

    async def test_a_revoked_permission_does_not_come_back(self, store):
        await store.upsert_memory(
            "user_profile",
            "permission.open_email",
            "Permission: may open my email client",
            _now(),
        )
        await store.revoke_memory(
            "user_profile",
            "permission.open_email",
            revoked_by="taylor",
            reason="changed my mind",
        )

        # Re-learning the identical claim is refused, not merged, not queued.
        result = await store.upsert_memory(
            "user_profile",
            "permission.open_email",
            "Permission: may open my email client",
            _now(),
        )
        assert result.stored is False
        assert result.outcome == "refused_revoked"

    async def test_a_temporary_exception_stops_applying_when_its_window_closes(self, store):
        """A temporary exception is exactly a validity window, which is why
        W03-D models it as one rather than as a special kind."""
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        result = await store.upsert_memory(
            "user_profile",
            "exception.quiet_hours_tonight",
            "Exception: quiet hours suspended for tonight",
            _now(),
            provenance=MemoryProvenance(source_type="user_instruction", valid_to=past),
        )
        assert result.stored
        verdict = cg.ConsentGate(store.db_path).validity_verdict(result.memory_id)
        assert verdict.verdict == cg.VERDICT_EXPIRED

    async def test_a_still_open_temporary_exception_does_apply(self, store):
        """Non-vacuity control for the scenario above."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        result = await store.upsert_memory(
            "user_profile",
            "exception.quiet_hours_now",
            "Exception: quiet hours suspended for the next hour",
            _now(),
            provenance=MemoryProvenance(source_type="user_instruction", valid_to=future),
        )
        assert _valid(store, result.memory_id)

    async def test_a_conflicting_observation_supersedes_rather_than_accumulates(self, store):
        """
        Two observations of the same thing must not both be recallable as
        current -- that is how a system ends up asserting the printer is both
        online and offline. The later one supersedes.
        """
        await store.upsert_memory(
            "environment_observation",
            "printer_state",
            "Observed: the printer is offline",
            _now(),
            provenance=MemoryProvenance(source_type="observation", confidence=0.9),
        )
        outcome = await store.supersede_memory(
            "environment_observation",
            "printer_state",
            "Observed: the printer is online",
            provenance=MemoryProvenance(source_type="observation", confidence=0.9),
        )
        assert outcome.stored, outcome

        live = await store.get_memory("environment_observation", "printer_state")
        assert "online" in live["value"]
        archived = await store.get_memory(MEMORY_REVISION_KIND, outcome.archived_key)
        assert "offline" in archived["value"]
        assert not _valid(store, archived["id"])

    async def test_an_explicit_user_correction_goes_through_supersession(self, store):
        """`correct_memory()` is the Memory Agency edit path. It must archive,
        not overwrite -- 'what did he believe before I corrected this?' has to
        stay answerable for an ordinary memory as it already does for a
        competency record."""
        await store.upsert_memory("fact", "bin_day", "The bin goes out on Thursdays", _now())
        result = await store.correct_memory("fact", "bin_day", "The bin goes out on Fridays")
        assert result.stored

        revisions = await store.list_memories_by_kind([MEMORY_REVISION_KIND])
        archived = [row for row in revisions if "fact/bin_day@r" in row["key"]]
        assert archived, "a correction destroyed the claim it replaced"
        assert "Thursdays" in archived[0]["value"]

        live = await store.get_memory("fact", "bin_day")
        assert "Fridays" in live["value"]


# ---------------------------------------------------------------------------
# 2. History and revision numbering.
# ---------------------------------------------------------------------------


class TestAuditHistory:
    async def test_successive_corrections_each_get_their_own_revision(self, store):
        await store.upsert_memory("fact", "colour", "Colour: red", _now())
        first = await store.supersede_memory("fact", "colour", "Colour: green")
        second = await store.supersede_memory("fact", "colour", "Colour: blue")

        assert first.superseded_revision == 1
        assert second.superseded_revision == 2
        assert first.archived_key != second.archived_key

        assert (await store.get_memory(MEMORY_REVISION_KIND, first.archived_key))["value"] == (
            "Colour: red"
        )
        assert (await store.get_memory(MEMORY_REVISION_KIND, second.archived_key))["value"] == (
            "Colour: green"
        )

    async def test_the_archive_points_forwards_at_what_replaced_it(self, store):
        await store.upsert_memory("fact", "linked", "before", _now())
        outcome = await store.supersede_memory("fact", "linked", "after")
        archived = await store.get_memory(MEMORY_REVISION_KIND, outcome.archived_key)
        assert archived["superseded_by"] == outcome.memory_id

    async def test_an_archived_revision_is_never_retrievable_as_current(self, store):
        """
        `MEMORY_REVISION_KIND` is absent from the retrieval seam's kind
        allowlist *and* every archived row carries a closed validity window.
        Two independent reasons, because one of them being removed by a
        future refactor must not silently resurrect a superseded belief.
        """
        from bartholomew.kernel import personal_facts
        from bartholomew.kernel.competency import COMPETENCY_KINDS

        assert MEMORY_REVISION_KIND not in COMPETENCY_KINDS
        assert MEMORY_REVISION_KIND not in personal_facts.PERSONAL_FACT_KINDS

        await store.upsert_memory("fact", "hidden", "obsolete claim", _now())
        outcome = await store.supersede_memory("fact", "hidden", "current claim")
        archived = await store.get_memory(MEMORY_REVISION_KIND, outcome.archived_key)
        assert not _valid(store, archived["id"])

    async def test_nothing_is_written_when_the_archive_cannot_land(self, store, monkeypatch):
        """
        Losing a correction is recoverable. Losing the belief it replaced is
        not. If the archive is refused, the replacement must not be written.
        """
        await store.upsert_memory("fact", "guarded", "original", _now())

        real_upsert = store.upsert_memory

        async def _refuse_archive(kind, key, *args, **kwargs):
            if kind == MEMORY_REVISION_KIND:
                from bartholomew.kernel.memory_store import StoreResult

                return StoreResult(stored=False, outcome="refused")
            return await real_upsert(kind, key, *args, **kwargs)

        monkeypatch.setattr(store, "upsert_memory", _refuse_archive)

        outcome = await store.supersede_memory("fact", "guarded", "replacement")
        assert outcome.stored is False
        assert outcome.outcome == "archive_failed"

        monkeypatch.undo()
        live = await store.get_memory("fact", "guarded")
        assert live["value"] == "original", "the replacement landed without an archive"


# ---------------------------------------------------------------------------
# 3. The tombstone: revoked stays revoked, across every recreation vector.
# ---------------------------------------------------------------------------


class TestRevocationTombstone:
    async def test_re_learning_cannot_recreate_a_revoked_identity(self, store):
        await store.upsert_memory("competency_heuristic", "est.lesson", "{}", _now())
        await store.revoke_memory(
            "competency_heuristic",
            "est.lesson",
            revoked_by="taylor",
        )
        result = await store.upsert_memory("competency_heuristic", "est.lesson", "{}", _now())
        assert result.outcome == "refused_revoked"

    async def test_personal_fact_capture_cannot_recreate_a_forgotten_fact(self, store):
        """
        The vector acceptance criterion 5 names explicitly, exercised through
        the surface a user actually uses: they delete a fact, and saying the
        same thing again does not quietly put it back.
        """
        await store.upsert_memory("user_profile", "birthday", "Birthday: 3rd March", _now())
        assert await store.forget_memory("user_profile", "birthday")

        result = await store.upsert_memory(
            "user_profile",
            "birthday",
            "Birthday: 3rd March",
            _now(),
            provenance=MemoryProvenance(source_type="user_instruction", asserted_by="user"),
        )
        assert result.stored is False
        assert result.outcome == "refused_revoked"

    async def test_the_refusal_precedes_the_consent_queue(self, store):
        """
        A revoked identity must not merely be blocked at the end of the write
        path -- it must be refused before anything can queue it for a human to
        be asked about. Asking someone to re-approve what they already
        withdrew is the exact failure this ordering prevents.
        """
        await store.upsert_memory("user_profile", "bank", "ordinary value", _now())
        await store.forget_memory("user_profile", "bank")

        result = await store.upsert_memory(
            "user_profile",
            "bank",
            "my bank account number is 12345678",
            _now(),
        )
        assert result.outcome == "refused_revoked"
        pending = await store.list_pending_sensitive_writes(limit=50)
        assert not [row for row in pending if row["key"] == "bank"], pending

    async def test_a_tombstone_is_visible_and_attributed(self, store):
        await store.upsert_memory("fact", "listed", "value", _now())
        await store.revoke_memory("fact", "listed", revoked_by="taylor", reason="stale")

        revocations = await store.list_revocations()
        entry = next(r for r in revocations if r["kind"] == "fact" and r["key"] == "listed")
        assert entry["revoked_by"] == "taylor"
        assert entry["reason"] == "stale"
        assert entry["revoked_at"]

    async def test_revocation_is_never_anonymous(self, store):
        await store.upsert_memory("fact", "anon", "value", _now())
        with pytest.raises(ValueError, match="never anonymous"):
            await store.revoke_memory("fact", "anon", revoked_by="")

    async def test_reinstating_is_a_deliberate_named_act(self, store):
        await store.upsert_memory("fact", "restored", "value", _now())
        await store.revoke_memory("fact", "restored", revoked_by="taylor")
        assert (await store.upsert_memory("fact", "restored", "value", _now())).stored is False

        with pytest.raises(ValueError, match="never anonymous"):
            await store.reinstate_memory("fact", "restored", reinstated_by="")

        outcome = await store.reinstate_memory(
            "fact",
            "restored",
            reinstated_by="taylor",
            reason="changed my mind",
        )
        assert outcome.reinstated is True
        assert (await store.upsert_memory("fact", "restored", "value", _now())).stored is True

    async def test_a_revoked_row_is_kept_but_never_valid(self, store):
        """Revocation removes the knowledge, not the record that it was once
        known. 'Was this ever known, and who withdrew it?' stays answerable --
        which is precisely what the previous hard delete destroyed."""
        result = await store.upsert_memory("fact", "audited", "value", _now())
        await store.revoke_memory("fact", "audited", revoked_by="taylor")

        row = await store.get_memory("fact", "audited")
        assert row is not None, "revocation destroyed the audit record"
        assert row["revoked_at"] is not None
        assert not _valid(store, result.memory_id)

    async def test_forgetting_still_erases_the_content(self, store):
        """The Memory Agency promise is unchanged: a forgotten memory's
        content is gone. Only the identity is remembered as withdrawn, and
        the tombstone stores no content at all."""
        await store.upsert_memory("fact", "erased", "the spare key is under the mat", _now())
        assert await store.forget_memory("fact", "erased")
        assert await store.get_memory("fact", "erased") is None

        entry = next(r for r in await store.list_revocations() if r["key"] == "erased")
        assert "spare key" not in str(entry)

    async def test_the_mechanical_delete_primitive_lays_no_tombstone(self, store):
        """`delete_memory()` is internal maintenance, not a user withdrawal.
        Giving it a tombstone would make every internal cleanup permanently
        block the identity it touched."""
        await store.upsert_memory("fact", "maintenance", "value", _now())
        assert await store.delete_memory("fact", "maintenance")
        assert await store.is_revoked("fact", "maintenance") is False
        assert (await store.upsert_memory("fact", "maintenance", "value", _now())).stored is True


# ---------------------------------------------------------------------------
# 4. Structural guards (cheap; these run in PR Fast as well).
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestStructuralGuards:
    def test_the_write_path_exposes_no_revocation_bypass_parameter(self):
        """
        The refusal must not be switchable off from the call site.

        `reinstate_memory()` exists precisely so that lifting a withdrawal is
        a separate, named, attributed act. A `skip_revocation=True` on the
        write path would mean any caller that found the refusal inconvenient
        could set it, which is how a governance control becomes decorative.
        """
        params = set(inspect.signature(MemoryStore.upsert_memory).parameters)
        forbidden = {
            "skip_revocation",
            "skip_tombstone",
            "ignore_revocation",
            "allow_revoked",
            "force",
        }
        assert not (
            params & forbidden
        ), f"upsert_memory() must not expose a revocation bypass; found {params & forbidden}"

    def test_the_only_documented_bypasses_remain_the_two_audited_ones(self):
        """
        A guard on the *shape* of the write path, not on one parameter name.

        `skip_privacy_guard` and `skip_rule_consent` are the two gate
        bypasses that exist, both used only by
        `approve_pending_sensitive_write()` re-running the pipeline on content
        a human already reviewed. A third one appearing is a governance
        change that must be argued for, not slipped in.
        """
        skips = {
            name
            for name in inspect.signature(MemoryStore.upsert_memory).parameters
            if name.startswith(("skip_", "bypass_", "ignore_", "allow_", "disable_"))
        }
        assert skips == {"skip_privacy_guard", "skip_rule_consent"}, skips
