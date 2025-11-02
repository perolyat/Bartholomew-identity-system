"""Quick smoke test for FTS snippet functionality"""

import os
import shutil
import sqlite3
import tempfile

import pytest

from bartholomew.kernel.fts_client import FTSClient


@pytest.mark.smoke
def test_fts_snippet_functionality():
    """Test FTS snippet generation with highlighting markers"""
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

        # Verify results
        assert len(results) > 0, "No results returned for search"

        result = results[0]
        assert "snippet" in result, "Snippet field not found in results"

        snippet = result["snippet"]
        assert isinstance(snippet, str), "Snippet should be a string"
        assert len(snippet) > 0, "Snippet should not be empty"

        # Check for highlighting markers or content
        assert "fox" in snippet.lower(), "Search term should appear in snippet"

    finally:
        # Cleanup
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass  # Ignore cleanup errors
