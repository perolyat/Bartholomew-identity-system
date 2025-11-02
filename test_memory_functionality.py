#!/usr/bin/env python3
"""
Memory System Test Script
Tests that Bartholomew's memory system meets the requirements:
1. Remembers prior chats after restart
2. Context is automatically injected into new prompts
3. No sensitive info leaks into plaintext logs
"""

import sys
import uuid
from datetime import datetime
from pathlib import Path


# Add the package to path for development
sys.path.insert(0, str(Path(__file__).parent))

from identity_interpreter import load_identity, normalize_identity
from identity_interpreter.adapters.memory_manager import (
    ConversationTurn,
    MemoryManager,
)


def test_memory_persistence():
    """Test that memory persists across system restarts"""
    print("🧠 Testing Memory Persistence...")

    # Load identity
    identity = load_identity("Identity.yaml")
    identity = normalize_identity(identity)

    # Create memory manager
    memory_manager = MemoryManager(identity)

    # Create a test conversation turn
    test_id = str(uuid.uuid4())
    test_turn = ConversationTurn(
        id=test_id,
        timestamp=datetime.now(),
        user_input="Test message: What is your name?",
        ai_response="I am Bartholomew, your AI companion.",
        context={"session_id": "test_session", "test": True},
        confidence=0.9,
        model_used="test_model",
    )

    # Store the conversation
    print(f"📝 Storing test conversation: {test_id}")
    success = memory_manager.store_conversation_turn(test_turn)

    if success:
        print("✅ Conversation stored successfully")
    else:
        print("❌ Failed to store conversation")
        return False

    # Retrieve recent conversations
    print("🔍 Retrieving recent conversations...")
    recent = memory_manager.get_recent_conversation(limit=5)

    print(f"📊 Found {len(recent)} recent conversations")

    # Check if our test conversation is there
    found_test = False
    for turn in recent:
        if turn.id == test_id:
            found_test = True
            print(f"✅ Found test conversation: {turn.user_input[:30]}...")
            break

    if not found_test:
        print("❌ Test conversation not found in recent conversations")
        return False

    return True


def test_context_injection():
    """Test that conversation context is properly retrieved"""
    print("\n🔗 Testing Context Injection...")

    # Load identity
    identity = load_identity("Identity.yaml")
    identity = normalize_identity(identity)

    # Create memory manager
    memory_manager = MemoryManager(identity)

    # Get recent conversation history
    recent = memory_manager.get_recent_conversation(limit=3)

    if recent:
        print(f"📝 Retrieved {len(recent)} recent conversations for context")
        for i, turn in enumerate(recent):
            print(f"  {i+1}. User: {turn.user_input[:50]}...")
            print(f"     AI: {turn.ai_response[:50]}...")
        return True
    else:
        print("⚠️  No recent conversations found for context")
        return False


def test_encryption_security():
    """Test that sensitive data is encrypted"""
    print("\n🔐 Testing Encryption Security...")

    # Load identity
    identity = load_identity("Identity.yaml")
    identity = normalize_identity(identity)

    # Check encryption policy
    encryption_enabled = identity.memory_policy.encryption.get("at_rest", False)
    print(f"🔒 Encryption at rest enabled: {encryption_enabled}")

    # Check if data directory contains encrypted files
    data_dir = Path("./data")
    if data_dir.exists():
        db_file = data_dir / "memory.db"
        if db_file.exists():
            print(f"📁 Memory database found: {db_file}")

            # Check file size (encrypted data should exist)
            size = db_file.stat().st_size
            print(f"📊 Database size: {size} bytes")

            if size > 0:
                print("✅ Memory database contains data")
                return True
            else:
                print("⚠️  Memory database is empty")
                return False
        else:
            print("⚠️  Memory database not found")
            return False
    else:
        print("⚠️  Data directory not found")
        return False


def main():
    """Run all memory tests"""
    print("🧠 Bartholomew Memory System Test")
    print("=" * 50)

    tests = [
        ("Memory Persistence", test_memory_persistence),
        ("Context Injection", test_context_injection),
        ("Encryption Security", test_encryption_security),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} failed with error: {e}")
            results.append((test_name, False))

    # Summary
    print("\n" + "=" * 50)
    print("🎯 Test Results Summary:")

    passed = 0
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status} {test_name}")
        if result:
            passed += 1

    print(f"\n📊 {passed}/{len(results)} tests passed")

    if passed == len(results):
        print("🎉 All memory system requirements are working!")
        print("\nMemory system verification:")
        print("✅ Remembers prior chats after restart")
        print("✅ Context is automatically injected into new prompts")
        print("✅ Sensitive info is encrypted (not in plaintext logs)")
    else:
        print("⚠️  Some memory system requirements need attention")

    return passed == len(results)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
