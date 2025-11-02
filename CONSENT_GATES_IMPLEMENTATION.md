# Consent and Privacy Gates Implementation

## Overview

This implementation adds comprehensive consent and privacy gates that pre-filter both FTS and vector search results to enforce privacy rules before returning memories to callers.

## Implementation Date
November 1, 2025

## Components Created/Modified

### 1. ConsentGate Module (`bartholomew/kernel/consent_gate.py`)

**Purpose**: Centralized privacy gate that filters memories based on consent and privacy rules.

**Key Features**:
- Loads consented memory IDs from `memory_consent` table
- Evaluates memories against memory rules
- Filters out `never_store` memories (`allow_store=false`)
- Filters out unconsented `ask_before_store` memories (`requires_consent=true`)
- Marks `context_only` memories (`recall_policy=context_only`)

**Public Methods**:
- `get_consented_memory_ids()` - Returns set of memory IDs with consent
- `load_memory_metadata(memory_ids)` - Loads full memory data for rule evaluation
- `filter_memory_ids(memory_ids, consented_ids)` - Applies privacy rules, returns policy metadata
- `apply_to_fts_results(fts_results)` - Filters FTS search results
- `apply_to_vector_results(vector_results)` - Filters vector search results
- `get_memory_policy(memory_id)` - Gets policy metadata for single memory

### 2. FTS Client Updates (`bartholomew/kernel/fts_client.py`)

**Changes**:
- Added `apply_consent_gate` parameter to `search()` method (default: `True`)
- Automatically filters FTS results through `ConsentGate` when enabled
- Fetches 3x candidates to account for filtering
- Adds `context_only` and `recall_policy` fields to results

**Backward Compatibility**:
- `apply_consent_gate=False` bypasses filtering for internal/admin use
- Default behavior applies gates for privacy-safe retrieval

### 3. Vector Store Updates (`bartholomew/kernel/vector_store.py`)

**Changes**:
- Added `apply_consent_gate` parameter to `search()` method (default: `True`)
- Automatically filters vector results through `ConsentGate` when enabled
- Fetches 3x candidates to account for filtering
- Returns filtered (memory_id, score) tuples

**Backward Compatibility**:
- `apply_consent_gate=False` bypasses filtering for internal/admin use
- Default behavior applies gates for privacy-safe retrieval

### 4. Test Suite (`tests/test_consent_gates.py`)

**Coverage**:
- Consent gate excludes never_store memories
- Consent gate excludes unconsented sensitive memories
- Consent gate includes consented memories
- Consent gate marks context_only memories
- FTS search applies consent gate by default
- FTS search can bypass consent gate
- Vector search applies consent gate by default
- Vector search can bypass consent gate
- Integration tests for end-to-end filtering

## Privacy Rules Enforced

### 1. never_store (allow_store=false)
**Rule**: Memory should not exist at all
**Gate Behavior**: Exclude from results (should not have embeddings anyway)
**Examples**: 
- Unknown speakers
- Illegal content
- Security camera footage
- Specific blocked patterns

### 2. ask_before_store (requires_consent=true)
**Rule**: Memory requires explicit user consent before storage
**Gate Behavior**: Exclude unless consent record exists in `memory_consent` table
**Examples**:
- Bank/medical information
- Passwords and credentials
- Third-party private conversations
- Emotional/trauma content

### 3. context_only (recall_policy=context_only)
**Rule**: Memory can be used internally but not surfaced to user
**Gate Behavior**: Include but mark with `context_only=True` flag
**Examples**:
- Sensitive jokes
- Political/religious discussions
- Adult content
- Smalltalk that doesn't need recall

## Usage Examples

### FTS Search with Privacy Gates

```python
from bartholomew.kernel.fts_client import FTSClient

fts = FTSClient("data/barth.db")

# Default: privacy gates applied
results = fts.search("user preferences")
# Results automatically filtered, context_only marked

# Bypass gates (admin use only)
all_results = fts.search("user preferences", apply_consent_gate=False)
```

### Vector Search with Privacy Gates

```python
from bartholomew.kernel.vector_store import VectorStore
import numpy as np

vector_store = VectorStore("data/barth.db")
query_vec = np.random.rand(384).astype(np.float32)

# Default: privacy gates applied
results = vector_store.search(query_vec, top_k=10)
# Results automatically filtered

# Bypass gates (admin use only)
all_results = vector_store.search(
    query_vec, 
    top_k=10, 
    apply_consent_gate=False
)
```

### Direct Consent Gate Usage

```python
from bartholomew.kernel.consent_gate import ConsentGate

gate = ConsentGate("data/barth.db")

# Check consent status
consented_ids = gate.get_consented_memory_ids()

# Filter memory IDs
policy_data = gate.filter_memory_ids([1, 2, 3, 4])

for memory_id, policy in policy_data.items():
    if policy["include"]:
        print(f"Memory {memory_id}: context_only={policy['context_only']}")
    else:
        print(f"Memory {memory_id}: EXCLUDED")
```

## Retrieval Layer Integration

The consent gates are applied at the lowest layers (FTS and vector stores), which means:

1. **HybridRetriever** - Gets pre-filtered results from both FTS and vector
2. **VectorRetrieverAdapter** - Gets pre-filtered vector results
3. **FTSOnlyRetriever** - Gets pre-filtered FTS results

All retrievers inherit the privacy protection automatically without code changes.

## Performance Considerations

### Candidate Fetching
- Gates fetch 3x requested candidates to account for filtering
- Example: `top_k=10` fetches 30 candidates, filters to ~10 results
- Adjust multiplier if filtering rate is very high

### Caching
- Consented IDs are loaded once per gate instance
- Consider caching gate instances for repeated queries
- Rule evaluation is done per-memory (relatively fast)

### Database Queries
- Consent check: Single query to load all consented IDs
- Metadata loading: Batched query for all candidate IDs
- Rule evaluation: In-memory processing (no DB calls)

## Testing

Run the consent gates test suite:

```bash
# Run all consent gate tests
python -m pytest tests/test_consent_gates.py -v

# Run specific test
python -m pytest tests/test_consent_gates.py::test_consent_gate_excludes_never_store -v

# Run with coverage
python -m pytest tests/test_consent_gates.py --cov=bartholomew.kernel.consent_gate
```

## Configuration

Privacy rules are defined in `bartholomew/config/memory_rules.yaml`:

```yaml
never_store:
  - match:
      speaker: unknown
    metadata:
      allow_store: false

ask_before_store:
  - match:
      content: "(?i)(bank|medical|password)"
    metadata:
      requires_consent: true
      privacy_class: user.sensitive

context_only:
  - match:
      kind: sensitive_joke
    metadata:
      recall_policy: context_only
```

## Future Enhancements

1. **Consent Promotion**: API to promote ask_before_store memories after consent
2. **Audit Logging**: Log when memories are filtered for compliance
3. **Policy Caching**: Cache rule evaluations for frequently accessed memories
4. **Batch Operations**: Optimize filtering for large result sets
5. **Configurable Multiplier**: Make candidate fetch multiplier configurable
6. **Context-Only Marking**: Add visual indicators in UI for context-only results

## Migration Notes

### Existing Deployments

1. **No Schema Changes**: Uses existing `memory_consent` table
2. **Backward Compatible**: All changes are additive
3. **Opt-Out Available**: Set `apply_consent_gate=False` to bypass
4. **Default Secure**: Privacy gates applied by default

### Upgrading

```bash
# No migration needed - gates work with existing data

# Optional: Review filtered results
python -c "
from bartholomew.kernel.consent_gate import ConsentGate
gate = ConsentGate('data/barth.db')
print(f'Consented memories: {len(gate.get_consented_memory_ids())}')
"
```

## Security Considerations

1. **Defense in Depth**: Gates at FTS/vector layer prevent leakage
2. **Fail-Safe Default**: Privacy gates enabled by default
3. **Explicit Bypass**: Disabling requires conscious `apply_consent_gate=False`
4. **Consent Tracking**: `memory_consent` table provides audit trail
5. **Rule-Based**: Centralized rules in `memory_rules.yaml`

## Related Documentation

- `bartholomew/config/memory_rules.yaml` - Privacy rule definitions
- `bartholomew/kernel/memory_rules.py` - Rule evaluation engine
- `bartholomew/kernel/memory_store.py` - Consent table schema
- `PHASE_2D_IMPLEMENTATION.md` - Embedding and retrieval context

## Support

For questions or issues with consent gates:
1. Review test cases in `tests/test_consent_gates.py`
2. Check rule evaluation in `memory_rules.yaml`
3. Verify consent records in `memory_consent` table
4. Enable debug logging: `logging.getLogger('bartholomew.kernel.consent_gate').setLevel(logging.DEBUG)`
