# Safety Nets Implementation

## Overview

This implementation adds two safety nets to the Bartholomew kernel:

1. **Optional stricter indexing rule**: Don't index memories marked `encrypt: strong` when policy prohibits it
2. **Prometheus duplicate collector guard**: Prevent duplicate metric registration during module reloads

## Implementation Details

### 1. Indexing Policy Guard

#### Files Modified/Created:
- `config/policy.yaml` - Added `indexing.disallow_strong_only` flag (default: false)
- `bartholomew/kernel/policy.py` - Added `can_index()` function
- `bartholomew/kernel/memory_store.py` - Integrated guard into FTS and vector indexing paths
- `tests/test_indexing_policy_guard.py` - Comprehensive test coverage

#### How It Works:
1. Policy configuration in `config/policy.yaml`:
   ```yaml
   indexing:
     disallow_strong_only: false  # Set to true to enable stricter rule
   ```

2. The `can_index(evaluated_meta)` function in `policy.py`:
   - Checks if `policy.indexing.disallow_strong_only` is enabled
   - If enabled, blocks indexing when `encrypt: "strong"` is detected
   - Returns `True` (allow indexing) or `False` (block indexing)

3. Integration points in `memory_store.py`:
   - **FTS indexing**: Guard applied before updating `memory_fts` table
   - **Vector embeddings**: Guard applied before generating embeddings
   - When blocked, logs INFO message and skips indexing (memory still stored)

#### Behavior:
- **Default (policy disabled)**: All memories indexed normally
- **Policy enabled + encrypt: strong**: Memory stored encrypted but NOT indexed (neither FTS nor vector)
- **Policy enabled + other encryption**: Memory indexed normally
- **No encryption**: Memory indexed normally

#### Testing:
- Test default behavior (indexing allowed)
- Test policy enabled behavior (strong encryption blocked)
- Test case-insensitive matching
- Integration test with MemoryStore

### 2. Prometheus Duplicate Collector Guard

#### Files Modified/Created:
- `bartholomew/kernel/metrics_registry.py` - New module with singleton pattern
- `bartholomew_api_bridge_v0_1/services/api/routes/metrics.py` - Updated to use shared registry
- `tests/test_metrics_registry_guard.py` - Comprehensive test coverage

#### How It Works:
1. **Singleton Registry Pattern** in `metrics_registry.py`:
   - Module-level `_registry` with thread-safe initialization
   - `get_metrics_registry()` function returns same instance across calls
   - `_initialized` flag prevents re-creation
   - Thread lock ensures concurrent safety

2. **Metrics Route Protection** in `routes/metrics.py`:
   - Uses shared registry via `get_metrics_registry()`
   - Module-level `_metrics_registered` flag
   - `_init_metrics_once()` function (idempotent)
   - Metrics only registered on first call

3. **Graceful Fallback**:
   - Works without `prometheus_client` installed
   - Provides stub `CollectorRegistry` class for testing

#### Behavior:
- **First import**: Registry created, metrics registered
- **Module reload**: Same registry reused, no duplicate registration
- **Multiple threads**: All get same registry instance
- **Missing prometheus_client**: Graceful fallback to stub

#### Testing:
- Test singleton behavior
- Test thread safety
- Test registry reset functionality
- Test no duplicate collectors on double init
- Test fallback when prometheus_client unavailable
- Test metrics route idempotent initialization

## Usage Examples

### Enable Stricter Indexing Policy

Edit `config/policy.yaml`:
```yaml
indexing:
  disallow_strong_only: true
```

Now memories with `encrypt: strong` (e.g., medical records, passwords) won't be indexed in FTS or vector stores, preventing any search leakage while still storing them encrypted.

### Metrics Registry (Automatic)

The metrics registry guard is automatic. Developers adding new metrics should use the shared registry:

```python
from bartholomew.kernel.metrics_registry import get_metrics_registry

REGISTRY = get_metrics_registry()

my_counter = Counter(
    'my_metric_name',
    'Description',
    registry=REGISTRY
)
```

## Test Results

### Indexing Policy Guard Tests
```
tests/test_indexing_policy_guard.py::test_can_index_default_policy PASSED
tests/test_indexing_policy_guard.py::test_can_index_with_policy_enabled PASSED
tests/test_indexing_policy_guard.py::test_can_index_case_insensitive PASSED
tests/test_indexing_policy_guard.py::test_memory_store_respects_indexing_policy PASSED
```
**Result: 4/4 passed ✓**

### Prometheus Registry Guard Tests
```
tests/test_metrics_registry_guard.py::test_metrics_registry_singleton PASSED
tests/test_metrics_registry_guard.py::test_metrics_registry_thread_safe PASSED
tests/test_metrics_registry_guard.py::test_metrics_registry_reset PASSED
tests/test_metrics_registry_guard.py::test_no_duplicate_collectors_on_double_init PASSED
tests/test_metrics_registry_guard.py::test_metrics_registry_with_prometheus_unavailable SKIPPED
tests/test_metrics_registry_guard.py::test_metrics_route_idempotent_init PASSED
```
**Result: 5/6 passed, 1 skipped (expected) ✓**

## Observability

### Indexing Policy Logs

When indexing is blocked by policy:
```
INFO:bartholomew.kernel.policy:Indexing blocked by policy: encrypt=strong with disallow_strong_only enabled
INFO:bartholomew.kernel.memory_store:FTS indexing blocked by policy for memory 123
INFO:bartholomew.kernel.memory_store:Vector embedding blocked by policy for memory 123
```

### Metrics Registry Logs

During initialization:
```
DEBUG:bartholomew.kernel.metrics_registry:Created Prometheus metrics registry
```

## Backward Compatibility

Both features maintain full backward compatibility:
- Indexing policy defaults to `false` (existing behavior)
- Metrics registry transparently replaces direct CollectorRegistry usage
- No changes required to existing code

## Performance Impact

- **Indexing Guard**: Negligible (single dict lookup + string comparison per memory)
- **Metrics Registry**: Negligible (lock contention only during first initialization)

## Security Considerations

The indexing policy guard provides defense-in-depth:
- Even if other protections fail, highly sensitive data won't be searchable
- Encrypted at rest AND not indexed
- Reduces attack surface for search-based information disclosure

## Future Enhancements

Possible future improvements:
1. Add metrics for `index_skip_total{reason="strong_only_policy"}` 
2. Support per-memory-type indexing policies
3. Add admin API to toggle policy at runtime
4. Extend to other indexing backends if added
