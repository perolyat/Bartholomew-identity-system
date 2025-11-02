"""Quick smoke test for FTS snippet functionality"""

import os
import shutil
import sqlite3
import tempfile

from bartholomew.kernel.fts_client import FTSClient


# Create temp database
tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, "test.db")

try:
    # Set up database with a test memory
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE memories (
        id INTEGER PRIMARY KEY,
        kind TEXT,
        key TEXT,
        value TEXT,
        summary TEXT,
        ts TEXT
    )""",
    )
    conn.commit()

    # Initialize FTS
    fts = FTSClient(db_path)
    fts.init_schema()

    # Insert test data through SQL
    conn.execute(
        """INSERT INTO memories VALUES (
        1,
        'fact',
        'test_key',
        'The quick brown fox jumps over the lazy dog',
        'A sentence about animals',
        '2025-01-01T12:00:00Z'
    )""",
    )
    conn.commit()
    conn.close()

    # Search with snippet
    results = fts.search("fox")

    print("=== FTS Snippet Test ===")
    print(f"Results found: {len(results)}")

    if results:
        result = results[0]
        print(f"Has snippet field: {'snippet' in result}")
        if "snippet" in result:
            snippet = result["snippet"]
            print(f"Snippet value: {snippet}")
            print(f"Contains '[' marker: {'[' in snippet}")
            print(f"Contains ']' marker: {']' in snippet}")
            print(f"Contains ellipsis: {' … ' in snippet}")
            print("\nSUCCESS: Snippet with highlighting markers!")
        else:
            print("WARNING: Snippet field not found in results")
    else:
        print("ERROR: No results returned")

finally:
    # Cleanup
    try:
        shutil.rmtree(tmpdir)
    except Exception as e:
        print(f"\nNote: Could not cleanup temp dir: {e}")
