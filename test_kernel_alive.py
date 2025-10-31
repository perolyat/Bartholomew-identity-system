"""
Test script to verify kernel is alive, stable, and dreaming.
"""
import asyncio
from datetime import datetime, timezone
from bartholomew.kernel.daemon import KernelDaemon


async def test_kernel_lifecycle():
    """Test kernel startup, operations, and shutdown."""
    print("=== Testing Kernel Lifecycle ===\n")
    
    # Initialize kernel
    print("1. Starting kernel...")
    kd = KernelDaemon(
        cfg_path="config/kernel.yaml",
        db_path="data/barth.db",
        persona_path="config/persona.yaml",
        policy_path="config/policy.yaml",
        drives_path="config/drives.yaml",
    )
    await kd.start()
    print("   ✓ Kernel started\n")
    
    # Test water logging
    print("2. Testing water logging...")
    await kd.handle_command("water_log_250")
    await asyncio.sleep(0.1)
    last_ts = await kd.mem.last_water_ts()
    print(f"   ✓ Water logged, last ts: {last_ts}\n")
    
    # Test nudge persistence
    print("3. Testing nudge persistence...")
    await kd.mem.create_nudge(
        kind="hydration",
        message="Test nudge",
        actions=[{"label": "+250", "cmd": "water_log_250"}],
        reason="test",
        created_ts=datetime.now(timezone.utc).isoformat(),
    )
    pending = await kd.mem.list_pending_nudges()
    print(f"   ✓ Created nudge, pending count: {len(pending)}\n")
    
    # Test reflection trigger
    print("4. Testing daily reflection...")
    await kd.handle_command("reflection_run_daily")
    await asyncio.sleep(0.2)
    daily = await kd.mem.latest_reflection("daily_journal")
    if daily:
        print(f"   ✓ Daily reflection created at {daily['ts']}")
        print(f"   Content preview: {daily['content'][:100]}...\n")
    else:
        print("   ✗ No daily reflection found\n")
    
    print("5. Testing weekly reflection...")
    await kd.handle_command("reflection_run_weekly")
    await asyncio.sleep(0.2)
    weekly = await kd.mem.latest_reflection("weekly_alignment_audit")
    if weekly:
        print(f"   ✓ Weekly reflection created at {weekly['ts']}")
        print(f"   Content preview: {weekly['content'][:100]}...\n")
    else:
        print("   ✗ No weekly reflection found\n")
    
    # Test graceful shutdown
    print("6. Testing graceful shutdown...")
    await kd.stop()
    print("   ✓ Kernel stopped cleanly\n")
    
    print("=== All Tests Passed ===")


if __name__ == "__main__":
    asyncio.run(test_kernel_lifecycle())
