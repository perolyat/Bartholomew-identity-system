"""
Test memory write with sensitive content
Verifies that Bartholomew prompts for consent and respects user's decision
"""

import asyncio
import uuid
from datetime import datetime
from pathlib import Path

from identity_interpreter.adapters.memory_manager import (
    MemoryEntry,
    MemoryManager,
    MemoryModality,
)
from identity_interpreter.loader import load_identity


async def run_test():
    """
    Test that sensitive content triggers consent prompt and respects
    'no' response
    """

    # Load identity configuration
    identity_path = Path("Identity.yaml")
    if not identity_path.exists():
        print(f"ERROR: Identity.yaml not found at {identity_path}")
        return

    config = load_identity(identity_path)

    # Initialize MemoryManager
    manager = MemoryManager(config, data_dir="./data")

    print("=" * 70)
    print("MEMORY PRIVACY TEST")
    print("=" * 70)
    print()
    print("This test will attempt to store sensitive content.")
    print("Bartholomew should detect it and ask for permission.")
    print()

    # Create entry with sensitive content containing "password" keyword
    entry = MemoryEntry(
        id=str(uuid.uuid4()),
        modality=MemoryModality.EPISODIC,
        timestamp=datetime.fromisoformat("2025-11-01T07:00:00"),
        content="My bank password is Hunter2",
        metadata={"test": True, "source": "privacy_test"},
        confidence=1.0,
        ttl_days=90,
        anchor=None,
        encrypted=False,
    )

    # Attempt to store the memory
    # This should trigger the privacy guard and prompt for consent
    print("Attempting to store sensitive memory...")
    print()

    result = manager.store_memory(entry)

    print()
    print("=" * 70)
    print(f"Storage result: {'STORED' if result else 'NOT STORED'}")
    print("=" * 70)
    print()

    # Check if memory was actually written to database
    if not result:
        print("✓ Memory was NOT stored (as expected after 'no' response)")
        print("✓ Privacy guard worked correctly")

        # Verify nothing in database
        memories = manager.retrieve_memories(limit=1000)
        test_memories = [m for m in memories if m.metadata.get("test")]

        if len(test_memories) == 0:
            print("✓ Verified: No test memories in database")
        else:
            count = len(test_memories)
            print(f"✗ ERROR: Found {count} test memories in database!")
            print("  Database should be empty after 'no' response")
    else:
        print("✗ Memory WAS stored (unexpected!)")
        print("  This means either:")
        print("  - You said 'yes' instead of 'no'")
        print("  - The privacy guard is not working correctly")


if __name__ == "__main__":
    print()
    print("Starting privacy test...")
    print()
    asyncio.run(run_test())
    print()
    print("Test complete!")
    print()
