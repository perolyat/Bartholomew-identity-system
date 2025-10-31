"""
Guard rail test to enforce using db_ctx.wal_db() instead of raw sqlite3.connect.

This ensures the WAL cleanup pattern is consistently applied across the API layer.
"""

import re
from pathlib import Path

import pytest


@pytest.mark.database
def test_no_raw_sqlite_connect_in_api():
    """
    Verify that API code uses db_ctx.wal_db() instead of raw sqlite3.connect.
    
    This enforces the WAL cleanup pattern and prevents future regressions
    that could lead to file handle leaks on Windows.
    """
    # Find the API directory
    api_dir = Path(__file__).parents[1] / "bartholomew_api_bridge_v0_1" / "services" / "api"
    
    # Files that are allowed to use sqlite3.connect directly
    allowlist = {
        "db_ctx.py",  # This module provides the abstraction
        "__init__.py",  # Usually just imports
    }
    
    # Regex pattern to find sqlite3.connect calls
    # Matches: sqlite3.connect( or sqlite3 . connect(
    pattern = re.compile(r"sqlite3\s*\.\s*connect\s*\(")
    
    offenders = []
    
    for path in api_dir.rglob("*.py"):
        if path.name in allowlist:
            continue
        
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            
            if pattern.search(text):
                # Found a violation
                offenders.append(str(path.relative_to(api_dir.parent.parent)))
        except Exception as e:
            # Skip files that can't be read
            print(f"Warning: Could not read {path}: {e}")
            continue
    
    assert not offenders, (
        f"Found raw sqlite3.connect() in API layer. "
        f"Use db_ctx.wal_db() instead for proper WAL cleanup.\n"
        f"Offending files: {', '.join(offenders)}\n\n"
        f"Example:\n"
        f"  # Bad:\n"
        f"  with sqlite3.connect(db_path) as conn:\n"
        f"      ...\n\n"
        f"  # Good:\n"
        f"  with db_ctx.wal_db(db_path) as conn:\n"
        f"      ...\n"
    )
