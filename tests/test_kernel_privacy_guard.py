import asyncio
from pathlib import Path

import aiosqlite

from bartholomew.kernel.memory_store import MemoryStore


DB_PATH = "data/test_privacy_guard.db"


async def run_test():
    # Ensure the data directory exists
    Path("data").mkdir(exist_ok=True)

    store = MemoryStore(DB_PATH)
    await store.init()

    # This call should trigger the privacy guard and prompt:
    # [Bartholomew] I detected something sensitive:
    # "Taylor's address is 42 High Street"
    # Do you want me to remember this? (yes/no)
    #
    # If the user types "no", upsert_memory returns early and nothing
    # is written.
    await store.upsert_memory(
        kind="reflection",
        key="private_thought",
        value="Taylor's address is 42 High Street",
        ts="2025-11-01T08:00:00Z",
    )

    # Verify nothing was stored when answering "no"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM memories WHERE kind=? AND key=?",
            ("reflection", "private_thought"),
        )
        row = await cur.fetchone()
        count = int(row[0] or 0)

    if count == 0:
        print("✓ Verified: no memory stored after answering 'no'.")
    else:
        print(f"✗ ERROR: Found {count} row(s) for that key; expected 0 after 'no'.")


if __name__ == "__main__":
    asyncio.run(run_test())
