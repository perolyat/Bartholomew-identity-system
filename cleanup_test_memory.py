"""
Clean up test memories from the database
"""

from pathlib import Path

from identity_interpreter.adapters.memory_manager import MemoryManager
from identity_interpreter.loader import load_identity


def cleanup():
    """Remove all test memories from database"""
    identity_path = Path("Identity.yaml")
    if not identity_path.exists():
        print("ERROR: Identity.yaml not found")
        return

    config = load_identity(identity_path)
    manager = MemoryManager(config, data_dir="./data")

    # Get all memories
    all_memories = manager.retrieve_memories(limit=10000)

    # Find test memories
    test_memories = [m for m in all_memories if m.metadata.get("test")]

    if not test_memories:
        print("No test memories found. Database is clean.")
        return

    print(f"Found {len(test_memories)} test memories to remove:")
    for mem in test_memories:
        print(f"  - {mem.id}: {mem.content[:50]}...")

    # Delete test memories
    from bartholomew.kernel import db_ctx

    db_path = Path("./data/memory.db")
    with db_ctx.connect(db_path) as conn:
        db_ctx.set_wal_pragmas(conn)
        for mem in test_memories:
            conn.execute("DELETE FROM memories WHERE id = ?", (mem.id,))

    print(f"\n✓ Removed {len(test_memories)} test memories")


if __name__ == "__main__":
    cleanup()
