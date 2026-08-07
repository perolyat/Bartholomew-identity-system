"""
Tests for Stage 5, S5.5's dry-run interception in
bartholomew.kernel.skill_registry.SkillRegistry.execute_action(). See
docs/S5_5_DRY_RUN_MODE_DESIGN.md Sec 5/6/13.

Uses the real `notify` skill (config/skills/notify.yaml, bartholomew.
skills.notify.NotifySkill) rather than a synthetic test skill, since the
whole point of these tests is proving a *real* capability's real
`execute()` is never reached under dry-run for its side-effecting
actions, and genuinely is reached for its declared-safe read-only ones.
"""

from __future__ import annotations

import sqlite3

import pytest

from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.reflection import REFLECTION_KIND
from bartholomew.kernel.skill_base import SkillResultStatus
from bartholomew.kernel.skill_registry import SkillRegistry
from bartholomew.orchestrator.safety.governance_store import GovernanceStore


@pytest.fixture
async def registry(tmp_path):
    db_path = str(tmp_path / "test.db")
    mem = MemoryStore(db_path)
    await mem.init()
    governance_store = GovernanceStore(db_path)
    reg = SkillRegistry(
        skills_dir="config/skills",
        db_path=db_path,
        memory_store=mem,
        governance_store=governance_store,
    )
    reg.discover_skills()
    loaded = await reg.load_skill("notify")
    assert loaded, "notify skill failed to load"
    reg._test_mem = mem  # stash for row-count assertions
    reg._test_governance_store = governance_store
    yield reg
    await mem.close()


def _audit_row_count(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM skill_action_audit").fetchone()[0]
    finally:
        conn.close()


def _reflection_row_count(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM reflections WHERE kind = ?",
            (REFLECTION_KIND,),
        ).fetchone()[0]
    finally:
        conn.close()


def _dry_run_row_count(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        # The table is created lazily on first write/read (dry_run.py's
        # ensure_schema()) -- a fresh db that never had a dry run yet
        # genuinely has no such table, which is itself "zero rows."
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dry_run_results'",
        ).fetchone()
        if not exists:
            return 0
        return conn.execute("SELECT COUNT(*) FROM dry_run_results").fetchone()[0]
    finally:
        conn.close()


async def test_side_effecting_action_never_calls_real_execute_under_dry_run(
    registry,
    monkeypatch,
):
    """The core guarantee: `send` (unclassified in the manifest -> the
    fail-closed side_effect=True default) must never reach NotifySkill's
    own execute() under dry-run."""
    loaded = registry._loaded["notify"]
    real_execute = loaded.instance.execute
    calls = []

    async def spy(action, params):
        calls.append(action)
        return await real_execute(action, params)

    monkeypatch.setattr(loaded.instance, "execute", spy)

    result = await registry.execute_action(
        "notify",
        "send",
        {"message": "hello"},
        dry_run=True,
    )
    assert calls == []  # never reached
    assert result.status == SkillResultStatus.DRY_RUN
    assert result.success is False  # DRY_RUN must never read as success


async def test_non_vacuity_live_call_does_reach_real_execute(registry, monkeypatch):
    """Non-vacuity control for the test above: the exact same spy, the
    exact same action, with dry_run=False -- proves the spy would have
    caught a regression, rather than the previous test passing vacuously."""
    loaded = registry._loaded["notify"]
    real_execute = loaded.instance.execute
    calls = []

    async def spy(action, params):
        calls.append(action)
        return await real_execute(action, params)

    monkeypatch.setattr(loaded.instance, "execute", spy)

    result = await registry.execute_action("notify", "send", {"message": "hello"})
    assert calls == ["send"]
    assert result.status == SkillResultStatus.SUCCESS


async def test_read_only_action_genuinely_executes_under_dry_run(registry, monkeypatch):
    """Fidelity: `is_quiet_hours` is declared side_effect: false in
    notify.yaml, so it must actually run for real even under dry-run --
    maximizing simulation fidelity at zero risk (design doc Sec 3)."""
    loaded = registry._loaded["notify"]
    real_execute = loaded.instance.execute
    calls = []

    async def spy(action, params):
        calls.append(action)
        return await real_execute(action, params)

    monkeypatch.setattr(loaded.instance, "execute", spy)

    result = await registry.execute_action("notify", "is_quiet_hours", {}, dry_run=True)
    assert calls == ["is_quiet_hours"]  # genuinely executed
    assert result.status == SkillResultStatus.SUCCESS  # not DRY_RUN


async def test_unclassified_action_defaults_to_side_effecting(registry, monkeypatch):
    """`queue` has no side_effect entry in notify.yaml -- confirms the
    fail-closed default (True) applies to it too, not just `send`."""
    loaded = registry._loaded["notify"]
    real_execute = loaded.instance.execute
    calls = []

    async def spy(action, params):
        calls.append(action)
        return await real_execute(action, params)

    monkeypatch.setattr(loaded.instance, "execute", spy)

    result = await registry.execute_action(
        "notify",
        "queue",
        {"message": "later"},
        dry_run=True,
    )
    assert calls == []
    assert result.status == SkillResultStatus.DRY_RUN


async def test_dry_run_writes_zero_real_ground_truth_rows(registry):
    db_path = registry._db_path
    audit_before = _audit_row_count(db_path)
    reflection_before = _reflection_row_count(db_path)

    await registry.execute_action("notify", "send", {"message": "hello"}, dry_run=True)

    assert _audit_row_count(db_path) == audit_before
    assert _reflection_row_count(db_path) == reflection_before
    assert _dry_run_row_count(db_path) == 1


async def test_live_call_does_write_real_ground_truth_rows(registry):
    """Companion non-vacuity check for the row-count test above."""
    db_path = registry._db_path
    audit_before = _audit_row_count(db_path)
    reflection_before = _reflection_row_count(db_path)

    await registry.execute_action("notify", "send", {"message": "hello"})

    assert _audit_row_count(db_path) == audit_before + 1
    assert _reflection_row_count(db_path) == reflection_before + 1
    assert _dry_run_row_count(db_path) == 0


async def test_global_switch_forces_dry_run_without_caller_flag(registry, monkeypatch):
    loaded = registry._loaded["notify"]
    real_execute = loaded.instance.execute
    calls = []

    async def spy(action, params):
        calls.append(action)
        return await real_execute(action, params)

    monkeypatch.setattr(loaded.instance, "execute", spy)

    registry._test_governance_store.engage_dry_run("skills", reason="test")
    result = await registry.execute_action("notify", "send", {"message": "hi"})  # no dry_run kwarg
    assert calls == []
    assert result.status == SkillResultStatus.DRY_RUN


async def test_caller_dry_run_false_cannot_override_engaged_global_switch(registry, monkeypatch):
    """S5.5 design doc Sec 11's non-negotiable: the global switch can only
    be pushed toward simulation, never away from it by a caller."""
    loaded = registry._loaded["notify"]
    real_execute = loaded.instance.execute
    calls = []

    async def spy(action, params):
        calls.append(action)
        return await real_execute(action, params)

    monkeypatch.setattr(loaded.instance, "execute", spy)

    registry._test_governance_store.engage_dry_run("skills", reason="test")
    result = await registry.execute_action(
        "notify",
        "send",
        {"message": "hi"},
        dry_run=False,  # explicit -- must not weaken the engaged switch
    )
    assert calls == []
    assert result.status == SkillResultStatus.DRY_RUN


async def test_disengaging_the_switch_restores_live_execution(registry, monkeypatch):
    loaded = registry._loaded["notify"]
    real_execute = loaded.instance.execute
    calls = []

    async def spy(action, params):
        calls.append(action)
        return await real_execute(action, params)

    monkeypatch.setattr(loaded.instance, "execute", spy)

    registry._test_governance_store.engage_dry_run("skills", reason="test")
    await registry.execute_action("notify", "send", {"message": "hi"})
    assert calls == []

    registry._test_governance_store.disengage_dry_run(reason="done")
    result = await registry.execute_action("notify", "send", {"message": "hi"})
    assert calls == ["send"]
    assert result.status == SkillResultStatus.SUCCESS


async def test_fail_closed_when_switch_resolution_errors_and_caller_wanted_live(
    registry,
    monkeypatch,
):
    """S5.5 design doc Sec 10: an error resolving the global switch must
    deny outright (never execute for real, never silently simulate) when
    the caller did not already ask for dry_run=True."""
    loaded = registry._loaded["notify"]
    real_execute = loaded.instance.execute
    calls = []

    async def spy(action, params):
        calls.append(action)
        return await real_execute(action, params)

    monkeypatch.setattr(loaded.instance, "execute", spy)

    import bartholomew.orchestrator.safety.governance_store as gs_module

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(gs_module, "is_dry_run_engaged_fail_closed_off_loop", boom)

    result = await registry.execute_action("notify", "send", {"message": "hi"}, dry_run=False)
    assert calls == []
    assert result.success is False
    assert result.status != SkillResultStatus.DRY_RUN


async def test_caller_dry_run_true_still_simulates_despite_switch_resolution_error(
    registry,
    monkeypatch,
):
    """Companion case: if the caller already explicitly asked for
    dry_run=True, a failure to also resolve the global switch changes
    nothing -- still simulate, exactly as requested."""
    loaded = registry._loaded["notify"]
    real_execute = loaded.instance.execute
    calls = []

    async def spy(action, params):
        calls.append(action)
        return await real_execute(action, params)

    monkeypatch.setattr(loaded.instance, "execute", spy)

    import bartholomew.orchestrator.safety.governance_store as gs_module

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(gs_module, "is_dry_run_engaged_fail_closed_off_loop", boom)

    result = await registry.execute_action("notify", "send", {"message": "hi"}, dry_run=True)
    assert calls == []
    assert result.status == SkillResultStatus.DRY_RUN


async def test_governance_denial_never_reaches_dry_run_branch(registry, monkeypatch):
    """A brake-denied call fails via the existing, unrelated denial path
    -- dry-run is never even consulted, and the real capability is never
    reached either way."""
    loaded = registry._loaded["notify"]
    real_execute = loaded.instance.execute
    calls = []

    async def spy(action, params):
        calls.append(action)
        return await real_execute(action, params)

    monkeypatch.setattr(loaded.instance, "execute", spy)

    real_gov = registry._governance_store
    real_gov.engage("skills", reason="brake test")

    result = await registry.execute_action("notify", "send", {"message": "hi"}, dry_run=True)
    assert calls == []
    assert result.status != SkillResultStatus.DRY_RUN
    assert "parking brake" in (result.error or "").lower()
