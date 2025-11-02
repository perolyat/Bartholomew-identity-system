# FTS5 Availability & Graceful Fallback Implementation

## Status: ✅ COMPLETE

## Summary

The FTS5 availability probe and graceful fallback mechanism is fully implemented and tested. The system detects FTS5 availability at runtime and automatically degrades to vector-only retrieval when FTS5 is unavailable, keeping the API stable.

## Implementation Details

### 1. Runtime Probe (bartholomew/kernel/fts_client.py)

```python
def fts5_available(conn: sqlite3.Connection) -> bool:
    """
    Runtime probe for FTS5 availability in SQLite.

    Attempts to create a throwaway temp virtual table using FTS5.
    Returns True if FTS5 is available, False otherwise.
    """
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.__fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE temp.__fts5_probe")
        return True
    except Exception:
        return False
```

**Key Features:**
- Uses temporary table in temp schema (automatically cleaned up)
- Returns False on any exception (including None connection)
- Non-intrusive probe that leaves no side effects

### 2. One-Time Check with Caching (bartholomew/kernel/retrieval.py)

```python
_fts5_available_cache: Optional[bool] = None

def _check_fts5_once(db_path: str) -> bool:
    """
    Check if FTS5 is available (cached after first check).

    Opens a connection to the database and probes for FTS5 support.
    Result is cached to avoid repeated checks.
    """
    global _fts5_available_cache

    if _fts5_available_cache is not None:
        return _fts5_available_cache

    # Probe FTS5 availability
    try:
        conn = sqlite3.connect(db_path)
        available = fts5_available(conn)
        conn.close()
    except Exception:
        available = False

    _fts5_available_cache = available

    if not available:
        logger.warning(
            "FTS5 not available; hybrid mode will operate vector-only "
            "and fts mode will degrade to vector-only to keep API stable."
        )
    else:
        logger.debug("FTS5 is available")

    return available
```

**Key Features:**
- Probes once per process lifetime
- Caches result in module-level variable
- Logs clear WARNING when FTS5 unavailable
- Logs DEBUG when FTS5 available

### 3. Graceful Fallback (bartholomew/kernel/retrieval.py)

The `get_retriever()` factory function implements automatic degradation:

**FTS Mode → Vector Mode:**
```python
if resolved_mode == "fts" and not fts_ok:
    logger.warning(
        "FTS mode requested but FTS5 unavailable; "
        "degrading to vector-only"
    )
    resolved_mode = "vector"
```

**Hybrid Mode → Vector-Only Operation:**
```python
elif resolved_mode == "hybrid" and not fts_ok:
    logger.info(
        "Hybrid mode with FTS5 unavailable; "
        "will operate with vector-only (empty FTS candidates)"
    )
```

**Vector Mode → Unaffected:**
- Vector mode works regardless of FTS5 availability
- No fallback needed

### 4. API Stability Guarantees

- **Type Stability:** Return types remain consistent regardless of FTS5 availability
  - `mode="fts"` returns `FTSOnlyRetriever` → `VectorRetrieverAdapter` (same interface)
  - `mode="hybrid"` returns `HybridRetriever` (operates vector-only internally)
  - `mode="vector"` returns `VectorRetrieverAdapter` (unchanged)

- **Interface Stability:** All retrievers expose `.retrieve(query, top_k, filters)` method

- **Graceful Error Handling:**
  - `HybridRetriever._pull_fts_candidates()` catches exceptions and returns `[]`
  - Empty FTS candidates → pure vector retrieval
  - No crashes or exceptions propagated to callers

## Test Coverage

All 8 tests passing in `tests/test_retrieval_fts5_fallback.py`:

1. ✅ `test_fts5_available_returns_true_when_available` - Probe returns bool
2. ✅ `test_fts5_available_returns_false_on_exception` - Probe handles errors
3. ✅ `test_check_fts5_once_caches_result` - Caching works correctly
4. ✅ `test_get_retriever_degrades_fts_mode_when_unavailable` - FTS→Vector degradation
5. ✅ `test_get_retriever_hybrid_logs_warning_when_fts_unavailable` - Hybrid logging
6. ✅ `test_get_retriever_vector_mode_unaffected_by_fts_availability` - Vector unaffected
7. ✅ `test_fts5_probe_logs_warning_once` - Warning logged only once (cached)
8. ✅ `test_hybrid_retriever_empty_fts_candidates_when_unavailable` - Empty FTS results

## Logging Behavior

**When FTS5 is unavailable:**

1. **First probe (cached):**
   ```
   WARNING bartholomew.kernel.retrieval:retrieval.py:59
   FTS5 not available; hybrid mode will operate vector-only
   and fts mode will degrade to vector-only to keep API stable.
   ```

2. **When FTS mode is requested:**
   ```
   WARNING bartholomew.kernel.retrieval:retrieval.py:822
   FTS mode requested but FTS5 unavailable; degrading to vector-only
   ```

3. **When hybrid mode is requested:**
   ```
   INFO bartholomew.kernel.retrieval:retrieval.py:827
   Hybrid mode with FTS5 unavailable;
   will operate with vector-only (empty FTS candidates)
   ```

**When FTS5 is available:**
```
DEBUG bartholomew.kernel.retrieval:retrieval.py:63
FTS5 is available
```

## Usage Examples

### Normal Operation (FTS5 Available)
```python
from bartholomew.kernel.retrieval import get_retriever

# All modes work as expected
retriever = get_retriever(mode="hybrid")  # FTS + Vector fusion
retriever = get_retriever(mode="fts")     # FTS-only
retriever = get_retriever(mode="vector")  # Vector-only
```

### Graceful Degradation (FTS5 Unavailable)
```python
# Logs WARNING and degrades to vector
retriever = get_retriever(mode="fts")     # → VectorRetrieverAdapter

# Logs INFO and operates vector-only
retriever = get_retriever(mode="hybrid")  # → HybridRetriever (empty FTS)

# Unaffected
retriever = get_retriever(mode="vector")  # → VectorRetrieverAdapter

# All return same .retrieve() interface
results = retriever.retrieve("query text", top_k=10)
```

## Windows SQLite WAL Cleanup

Tests were updated to use proper WAL cleanup pattern to avoid file locking issues:

```python
def _cleanup_db_connections(db_path: str) -> None:
    """Helper to ensure all connections are closed and WAL is checkpointed"""
    gc.collect()
    time.sleep(0.05)
    try:
        conn = sqlite3.connect(db_path, timeout=1.0)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass
    gc.collect()
    time.sleep(0.05)
```

This pattern is used in all test cleanup blocks to ensure temp directories can be properly deleted on Windows.

## Future Enhancements (Optional)

1. **Eager Startup Probe:** Currently probes lazily when `get_retriever()` is first called. Could add eager probe in CLI/API startup to surface warnings earlier.

2. **Documentation:** Add note to QUICKSTART.md explaining auto-degradation behavior.

3. **Metrics:** Could expose FTS5 availability as a system health metric.

## Files Modified

- `tests/test_retrieval_fts5_fallback.py` - Added WAL cleanup helpers and try/finally blocks

## Files Already Implementing Feature

- `bartholomew/kernel/fts_client.py` - Runtime probe function
- `bartholomew/kernel/retrieval.py` - Caching, logging, and degradation logic
- `bartholomew/kernel/hybrid_retriever.py` - Graceful error handling in FTS pull

## Conclusion

The FTS5 availability probe and graceful fallback is fully implemented, tested, and working. The system will:

1. ✅ Probe FTS5 availability once at first retrieval
2. ✅ Log clear WARNING when unavailable
3. ✅ Auto-degrade FTS mode to vector-only
4. ✅ Keep API stable (consistent return types and interfaces)
5. ✅ Operate correctly in hybrid mode with empty FTS candidates
6. ✅ Leave vector mode unaffected

No additional code changes are required. The implementation satisfies all requirements from the task specification.
