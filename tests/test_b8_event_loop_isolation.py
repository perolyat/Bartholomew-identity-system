"""
Phase B stage B8 (first sub-stage): confirms the event-loop-blocking
call sites this sub-stage closed actually run off the event loop now,
not just that behavior is preserved (already covered by the existing
test suites for daemon lifecycle, memory_store embeddings, and skill
registry, all re-run clean after this change).

Three groups of real, confirmed-by-direct-read blocking SQLite calls,
previously invoked directly from async methods with no async methods of
their own (ExperienceKernel/WorkingMemoryManager/PersonaPackManager/
VectorStore all have zero `async def` methods -- their blocking-ness was
entirely a function of how/whether callers routed them off the event
loop), now routed through bartholomew.kernel.blocking_executor
.run_off_loop() like every other daemon-owned blocking call:

1. daemon.py's _init_experience_kernel() (start()) and the experience/
   working-memory persist calls in stop().
2. memory_store.py's VectorStore construction and upsert/delete calls in
   _handle_embeddings(), persist_embeddings_for(), reembed_memory().
3. skill_registry.py's _persist_skill_state() (load_skill()) and
   _audit_execution() (_finish(), every skill action).

Proof technique: each fixture wires a real SingleWorkerExecutor and
records the thread identity every wrapped call actually ran on,
confirming it differs from the test's own (event-loop) thread.
"""

from __future__ import annotations

import threading

import pytest

from bartholomew.kernel.blocking_executor import SingleWorkerExecutor
from bartholomew.kernel.daemon import KernelDaemon
from bartholomew.kernel.memory_store import MemoryStore


@pytest.fixture
def config_files(tmp_path):
    (tmp_path / "kernel.yaml").write_text(
        'timezone: "Australia/Brisbane"\nloop_interval_seconds: 1\n',
    )
    (tmp_path / "persona.yaml").write_text('name: "Test Bartholomew"\n')
    (tmp_path / "policy.yaml").write_text("policies: []\n")
    (tmp_path / "drives.yaml").write_text("drives: []\n")
    return {
        "cfg_path": str(tmp_path / "kernel.yaml"),
        "db_path": str(tmp_path / "test.db"),
        "persona_path": str(tmp_path / "persona.yaml"),
        "policy_path": str(tmp_path / "policy.yaml"),
        "drives_path": str(tmp_path / "drives.yaml"),
    }


# -- Group 1: daemon.py startup/shutdown ------------------------------------


async def test_persona_switch_at_startup_runs_off_the_event_loop(config_files, monkeypatch):
    daemon = KernelDaemon(**config_files)
    main_thread_id = threading.get_ident()
    seen_thread_ids: list[int] = []

    real_switch_pack = daemon.persona_manager.switch_pack

    def spying_switch_pack(*args, **kwargs):
        seen_thread_ids.append(threading.get_ident())
        return real_switch_pack(*args, **kwargs)

    monkeypatch.setattr(daemon.persona_manager, "switch_pack", spying_switch_pack)
    # _init_experience_kernel() only calls switch_pack() when no persona is
    # already active -- the real repo config's default.yaml auto-activates
    # on PersonaPackManager construction, so force the precondition this
    # test needs rather than depending on real config's incidental shape.
    daemon.persona_manager._active_pack_id = None

    await daemon.start()
    try:
        assert seen_thread_ids, "switch_pack() was never called -- test precondition not met"
        assert all(tid != main_thread_id for tid in seen_thread_ids)
    finally:
        await daemon.stop()


async def test_experience_snapshot_persist_at_shutdown_runs_off_the_event_loop(
    config_files,
    monkeypatch,
):
    daemon = KernelDaemon(**config_files)
    await daemon.start()

    main_thread_id = threading.get_ident()
    seen_thread_ids: list[int] = []

    real_persist = daemon.experience.persist_snapshot

    def spying_persist(*args, **kwargs):
        seen_thread_ids.append(threading.get_ident())
        return real_persist(*args, **kwargs)

    monkeypatch.setattr(daemon.experience, "persist_snapshot", spying_persist)

    await daemon.stop()

    assert seen_thread_ids, "persist_snapshot() was never called -- test precondition not met"
    assert all(tid != main_thread_id for tid in seen_thread_ids)


async def test_working_memory_snapshot_persist_at_shutdown_runs_off_the_event_loop(
    config_files,
    monkeypatch,
):
    daemon = KernelDaemon(**config_files)
    await daemon.start()

    main_thread_id = threading.get_ident()
    seen_thread_ids: list[int] = []

    real_persist = daemon.working_memory.persist_snapshot

    def spying_persist(*args, **kwargs):
        seen_thread_ids.append(threading.get_ident())
        return real_persist(*args, **kwargs)

    monkeypatch.setattr(daemon.working_memory, "persist_snapshot", spying_persist)

    await daemon.stop()

    assert seen_thread_ids, "persist_snapshot() was never called -- test precondition not met"
    assert all(tid != main_thread_id for tid in seen_thread_ids)


# -- Group 2: memory_store.py VectorStore/embedding calls --------------------


async def test_persist_embeddings_for_runs_vector_store_calls_off_the_event_loop(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
    monkeypatch.setenv("BARTHO_EMBED_RELOAD", "0")

    executor = SingleWorkerExecutor(thread_name_prefix="test-b8-embed")
    try:
        db_path = str(tmp_path / "test.db")
        store = MemoryStore(db_path, blocking_executor=executor)
        await store.init()

        main_thread_id = threading.get_ident()
        seen_thread_ids: list[int] = []

        import bartholomew.kernel.memory_store as memory_store_module

        real_get_components = memory_store_module._get_embedding_components

        def spying_get_components(*args, **kwargs):
            seen_thread_ids.append(threading.get_ident())
            return real_get_components(*args, **kwargs)

        monkeypatch.setattr(
            memory_store_module,
            "_get_embedding_components",
            spying_get_components,
        )

        result = await store.upsert_memory(
            kind="test",
            key="embed_off_loop",
            value="Some content to embed for the off-loop regression test.",
            ts="2024-01-01T00:00:00Z",
        )

        count = await store.persist_embeddings_for(result.memory_id)

        assert count > 0, "test precondition not met -- no embeddings were persisted"
        assert seen_thread_ids, "_get_embedding_components() was never called"
        assert all(tid != main_thread_id for tid in seen_thread_ids)
    finally:
        await executor.close()


async def test_reembed_memory_deletes_via_vector_store_off_the_event_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("BARTHO_EMBED_ENABLED", "1")
    monkeypatch.setenv("BARTHO_EMBED_RELOAD", "0")

    executor = SingleWorkerExecutor(thread_name_prefix="test-b8-reembed")
    try:
        db_path = str(tmp_path / "test.db")
        store = MemoryStore(db_path, blocking_executor=executor)
        await store.init()

        result = await store.upsert_memory(
            kind="test",
            key="reembed_off_loop",
            value="Some content to re-embed for the off-loop regression test.",
            ts="2024-01-01T00:00:00Z",
        )
        await store.persist_embeddings_for(result.memory_id)

        main_thread_id = threading.get_ident()
        seen_thread_ids: list[int] = []

        import bartholomew.kernel.vector_store as vector_store_module

        real_delete = vector_store_module.VectorStore.delete_for_memory

        def spying_delete(self, *args, **kwargs):
            seen_thread_ids.append(threading.get_ident())
            return real_delete(self, *args, **kwargs)

        monkeypatch.setattr(vector_store_module.VectorStore, "delete_for_memory", spying_delete)

        await store.reembed_memory(result.memory_id, sources=["summary"])

        assert seen_thread_ids, "delete_for_memory() was never called"
        assert all(tid != main_thread_id for tid in seen_thread_ids)
    finally:
        await executor.close()


# -- Group 3: skill_registry.py audit/state persistence ----------------------


async def test_skill_state_persistence_runs_off_the_event_loop(tmp_path):
    """Matches tests/test_skill_registry.py::test_load_skill's own
    established pattern -- patch _instantiate_skill to return a MockSkill
    directly rather than relying on a real dynamic import."""
    from bartholomew.kernel.skill_registry import SkillRegistry
    from tests.test_skill_registry import MockSkill

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    import yaml

    manifest = {
        "skill_id": "b8_test_skill",
        "name": "B8 Test Skill",
        "version": "1.0.0",
        "description": "A test skill for B8's off-loop regression",
        "entry_module": "tests.test_skill_registry",
        "entry_class": "MockSkill",
        "permissions": {"level": "auto", "requires": [], "sandbox": {}},
        "actions": [{"name": "do_something", "description": "does something", "parameters": []}],
        "author": "test",
        "enabled": True,
    }
    with open(skills_dir / "b8_test_skill.yaml", "w") as f:
        yaml.safe_dump(manifest, f)

    executor = SingleWorkerExecutor(thread_name_prefix="test-b8-skills")
    try:
        db_path = str(tmp_path / "test.db")
        registry = SkillRegistry(
            skills_dir=str(skills_dir),
            db_path=db_path,
            blocking_executor=executor,
        )

        main_thread_id = threading.get_ident()
        seen_thread_ids: list[int] = []

        real_persist = registry._persist_skill_state

        def spying_persist(*args, **kwargs):
            seen_thread_ids.append(threading.get_ident())
            return real_persist(*args, **kwargs)

        registry._persist_skill_state = spying_persist

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(registry, "_instantiate_skill", lambda manifest: MockSkill())
            loaded = await registry.load_skill("b8_test_skill")

        assert loaded is True, "test precondition not met -- skill failed to load"
        assert seen_thread_ids, "_persist_skill_state() was never called"
        assert all(tid != main_thread_id for tid in seen_thread_ids)
    finally:
        await executor.close()
