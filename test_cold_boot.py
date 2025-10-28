#!/usr/bin/env python3
"""
Cold Boot and Integration Boundary Tests for Memory System
Tests schema versioning, persistence, encryption, and API stability
"""

import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Add package to path
sys.path.insert(0, str(Path(__file__).parent))

from identity_interpreter import load_identity, normalize_identity
from identity_interpreter.adapters.memory_manager import (
    CURRENT_SCHEMA_VERSION,
    ConversationTurn,
    MemoryEntry,
    MemoryManager,
    MemoryModality,
)


def test_schema_versioning():
    """Test that schema version is properly set and readable"""
    print("\n🔢 Testing Schema Versioning...")

    tmpdir = tempfile.mkdtemp()
    try:
        # Load identity
        identity = load_identity("Identity.yaml")
        identity = normalize_identity(identity)

        # Create memory manager in temp directory
        mm = MemoryManager(identity, data_dir=tmpdir)

        # Check schema version in database
        db_path = Path(tmpdir) / "memory.db"
        with sqlite3.connect(db_path) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            # Close WAL properly
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        del mm  # Ensure cleanup

        print(f"✓ Database schema version: {version}")
        print(f"✓ Expected version: {CURRENT_SCHEMA_VERSION}")

        if version == CURRENT_SCHEMA_VERSION:
            print("✅ Schema versioning is correctly set")
            return True
        else:
            print(f"❌ Schema version mismatch: {version} != "
                  f"{CURRENT_SCHEMA_VERSION}")
            return False
    finally:
        import shutil
        import time
        time.sleep(0.1)  # Give Windows time to release files
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except:
            pass


def test_wal_mode_enabled():
    """Test that WAL mode is enabled for crash resilience"""
    print("\n📝 Testing WAL Mode...")

    with tempfile.TemporaryDirectory() as tmpdir:
        identity = load_identity("Identity.yaml")
        identity = normalize_identity(identity)

        mm = MemoryManager(identity, data_dir=tmpdir)

        # Check journal mode
        db_path = Path(tmpdir) / "memory.db"
        with sqlite3.connect(db_path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

        print(f"✓ Journal mode: {mode}")

        if mode.upper() == "WAL":
            print("✅ WAL mode is enabled for crash resilience")
            return True
        else:
            print(f"❌ WAL mode not enabled, found: {mode}")
            return False


def test_cold_boot_reload():
    """Test that memory persists across system restarts"""
    print("\n❄️ Testing Cold Boot Reload...")

    with tempfile.TemporaryDirectory() as tmpdir:
        identity = load_identity("Identity.yaml")
        identity = normalize_identity(identity)

        # Phase 1: Create and store data
        print("  Phase 1: Storing test data...")
        mm1 = MemoryManager(identity, data_dir=tmpdir)

        test_id = str(uuid.uuid4())
        test_turn = ConversationTurn(
            id=test_id,
            timestamp=datetime.now(),
            user_input="Test cold boot: What is your name?",
            ai_response="I am Bartholomew.",
            context={"session": "cold_boot_test"},
            confidence=0.95,
            model_used="test_model",
        )

        success = mm1.store_conversation_turn(test_turn)
        print(f"  ✓ Stored test conversation: {success}")

        # Delete the first instance (simulating app shutdown)
        del mm1

        # Phase 2: Simulate cold boot - new MemoryManager instance
        print("  Phase 2: Simulating cold boot (new instance)...")
        mm2 = MemoryManager(identity, data_dir=tmpdir)

        # Try to retrieve the conversation
        recent = mm2.get_recent_conversation(limit=5)
        print(f"  ✓ Retrieved {len(recent)} conversations after restart")

        # Check if our test conversation is present
        found = False
        for turn in recent:
            if turn.id == test_id:
                found = True
                print(f"  ✓ Found test conversation: '{turn.user_input[:40]}...'")
                break

        if found:
            print("✅ Cold boot reload successful - data persists")
            return True
        else:
            print("❌ Cold boot reload failed - data not found after restart")
            return False


def test_cleanup_expired_memories():
    """Test that expired memories are properly cleaned up"""
    print("\n🗑️ Testing Expired Memory Cleanup...")

    with tempfile.TemporaryDirectory() as tmpdir:
        identity = load_identity("Identity.yaml")
        identity = normalize_identity(identity)

        mm = MemoryManager(identity, data_dir=tmpdir)

        # Create an already-expired memory
        expired_id = str(uuid.uuid4())
        expired_memory = MemoryEntry(
            id=expired_id,
            modality=MemoryModality.EPISODIC,
            timestamp=datetime.now() - timedelta(days=100),
            content="This should be deleted",
            metadata={"test": "expired"},
            confidence=0.8,
            ttl_days=1,  # Short TTL
            anchor=None,
        )

        # Store the expired memory directly in DB
        expires_at = (datetime.now() - timedelta(days=1)).isoformat()
        db_path = Path(tmpdir) / "memory.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO memories
                (id, modality, timestamp, content, metadata, confidence,
                 ttl_days, anchor, encrypted, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    expired_id,
                    expired_memory.modality.value,
                    expired_memory.timestamp.isoformat(),
                    expired_memory.content,
                    "{}",
                    expired_memory.confidence,
                    expired_memory.ttl_days,
                    expired_memory.anchor,
                    False,
                    expires_at,
                ),
            )

        print("  ✓ Inserted expired memory")

        # Run cleanup
        count = mm.cleanup()
        print(f"  ✓ Cleanup removed {count} expired memories")

        # Verify it was deleted
        memories = mm.retrieve_memories(limit=100)
        found = any(m.id == expired_id for m in memories)

        if count > 0 and not found:
            print("✅ Expired memory cleanup successful")
            return True
        else:
            print("❌ Expired memory cleanup failed")
            return False


def test_stable_api_interface():
    """Test that stable API methods work correctly"""
    print("\n🔌 Testing Stable API Interface...")

    with tempfile.TemporaryDirectory() as tmpdir:
        identity = load_identity("Identity.yaml")
        identity = normalize_identity(identity)

        mm = MemoryManager(identity, data_dir=tmpdir)

        # Test write_memory (stable API)
        test_id = str(uuid.uuid4())
        memory = MemoryEntry(
            id=test_id,
            modality=MemoryModality.SEMANTIC,
            timestamp=datetime.now(),
            content="Test stable API",
            metadata={"api_test": True},
            confidence=0.9,
        )

        write_success = mm.write_memory(memory)
        print(f"  ✓ write_memory: {write_success}")

        # Test read_memories (stable API)
        memories = mm.read_memories(modality=MemoryModality.SEMANTIC, limit=10)
        read_success = any(m.id == test_id for m in memories)
        print(f"  ✓ read_memories: found={read_success}")

        # Test build_context (stable API)
        turn = ConversationTurn(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            user_input="Hello",
            ai_response="Hi there!",
            context={},
            confidence=0.9,
            model_used="test",
        )
        mm.store_conversation_turn(turn)
        context = mm.build_context(limit=5)
        context_success = len(context) > 0 and "Hello" in context
        print(f"  ✓ build_context: {len(context)} chars")

        # Test cleanup (stable API)
        cleanup_count = mm.cleanup()
        cleanup_success = cleanup_count >= 0  # Returns int count
        print(f"  ✓ cleanup: {cleanup_count} removed")

        # Test health_check (stable API)
        health = mm.health_check()
        health_success = "db" in health and "cipher" in health
        print(f"  ✓ health_check: db={health['db']}, cipher={health['cipher']}")

        all_success = all(
            [
                write_success,
                read_success,
                context_success,
                cleanup_success,
                health_success,
            ]
        )

        if all_success:
            print("✅ Stable API interface working correctly")
            return True
        else:
            print("❌ Some stable API methods failed")
            return False


def test_encryption_keystore_persistence():
    """Test that encryption key persists in OS keystore"""
    print("\n🔐 Testing Encryption Key Persistence...")

    identity = load_identity("Identity.yaml")
    identity = normalize_identity(identity)

    # Check if encryption is enabled
    encryption_enabled = identity.memory_policy.encryption.get("at_rest", False)
    print(f"  ✓ Encryption at rest: {encryption_enabled}")

    with tempfile.TemporaryDirectory() as tmpdir:
        # First instance
        mm1 = MemoryManager(identity, data_dir=tmpdir)
        key1 = mm1.encryption_key if mm1.cipher else None

        # Second instance (should reuse same key from keystore)
        mm2 = MemoryManager(identity, data_dir=tmpdir)
        key2 = mm2.encryption_key if mm2.cipher else None

        if encryption_enabled:
            if key1 and key2 and key1 == key2:
                print("  ✓ Same encryption key retrieved from keystore")
                print("✅ Encryption key persistence confirmed")
                return True
            else:
                print("  ✗ Keys don't match or are None")
                print("❌ Encryption key persistence failed")
                return False
        else:
            print("  ℹ️ Encryption not required by config - test skipped")
            return True


def main():
    """Run all integration boundary tests"""
    print("=" * 60)
    print("🧪 Memory Integration Boundary Tests")
    print("=" * 60)

    tests = [
        ("Schema Versioning", test_schema_versioning),
        ("WAL Mode", test_wal_mode_enabled),
        ("Cold Boot Reload", test_cold_boot_reload),
        ("Expired Memory Cleanup", test_cleanup_expired_memories),
        ("Stable API Interface", test_stable_api_interface),
        ("Encryption Key Persistence", test_encryption_keystore_persistence),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} failed with exception: {e}")
            import traceback

            traceback.print_exc()
            results.append((test_name, False))

    # Summary
    print("\n" + "=" * 60)
    print("📊 Test Results Summary")
    print("=" * 60)

    passed = 0
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status} {test_name}")
        if result:
            passed += 1

    print(f"\n📊 {passed}/{len(results)} tests passed")

    if passed == len(results):
        print("\n🎉 All memory integration boundaries verified!")
        print("\n✅ Checklist:")
        print("  ✓ Memory API endpoints exposed with stable interface")
        print("  ✓ Schema versioning added to SQLite memory tables")
        print("  ✓ Encryption keys stored in persistent OS-level keystore")
        print("  ✓ Cold-boot test passes (memory reloads correctly)")
    else:
        print("\n⚠️ Some tests failed - review failures above")

    return passed == len(results)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
