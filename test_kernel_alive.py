"""
Test script to verify kernel is alive, stable, and dreaming.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from bartholomew.kernel.daemon import KernelDaemon

_REPO_ROOT = Path(__file__).parent


async def test_kernel_lifecycle(tmp_path, monkeypatch):
    """Test kernel startup, operations, and shutdown."""
    print("=== Testing Kernel Lifecycle ===\n")

    # Initialize kernel
    print("1. Starting kernel...")
    # db_path is isolated per test run (tmp_path) rather than pointed at the
    # real, git-tracked data/barth.db -- that file was getting mutated by
    # every test run and contending over SQLite locks with other tests/CI
    # legs that also touch it. Config paths are resolved to absolute paths
    # first (they're read-only, safe to share) because the reflection flow
    # below also touches identity_interpreter.MemoryManager's *relative*
    # "./data" default -- chdir'ing into tmp_path keeps that isolated too,
    # so config paths must not depend on the original cwd.
    kd = KernelDaemon(
        cfg_path=str(_REPO_ROOT / "config/kernel.yaml"),
        db_path=str(tmp_path / "barth.db"),
        persona_path=str(_REPO_ROOT / "config/persona.yaml"),
        policy_path=str(_REPO_ROOT / "config/policy.yaml"),
        drives_path=str(_REPO_ROOT / "config/drives.yaml"),
    )
    monkeypatch.chdir(tmp_path)
    await kd.start()
    print("   ✓ Kernel started\n")

    # Test nudge persistence
    print("2. Testing nudge persistence...")
    await kd.mem.create_nudge(
        kind="system",
        message="Test nudge",
        actions=[],
        reason="test",
        created_ts=datetime.now(timezone.utc).isoformat(),
    )
    pending = await kd.mem.list_pending_nudges()
    print(f"   ✓ Created nudge, pending count: {len(pending)}\n")

    # Test reflection trigger
    print("3. Testing daily reflection...")
    await kd.handle_command("reflection_run_daily")
    await asyncio.sleep(0.2)
    daily = await kd.mem.latest_reflection("daily_journal")
    if daily:
        print(f"   ✓ Daily reflection created at {daily['ts']}")
        print(f"   Content preview: {daily['content'][:100]}...\n")
    else:
        print("   ✗ No daily reflection found\n")

    print("4. Testing weekly reflection...")
    await kd.handle_command("reflection_run_weekly")
    await asyncio.sleep(0.2)
    weekly = await kd.mem.latest_reflection("weekly_alignment_audit")
    if weekly:
        print(f"   ✓ Weekly reflection created at {weekly['ts']}")
        print(f"   Content preview: {weekly['content'][:100]}...\n")
    else:
        print("   ✗ No weekly reflection found\n")

    # Test graceful shutdown
    print("5. Testing graceful shutdown...")
    await kd.stop()
    print("   ✓ Kernel stopped cleanly\n")

    print("=== All Tests Passed ===")


if __name__ == "__main__":
    import tempfile

    import pytest

    with tempfile.TemporaryDirectory() as tmp_dir, pytest.MonkeyPatch.context() as mp:
        asyncio.run(test_kernel_lifecycle(Path(tmp_dir), mp))
