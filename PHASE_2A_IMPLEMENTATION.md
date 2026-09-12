# Phase 2a Implementation: Redaction, Encryption Routing, and Summarization Foundation

> **SUPERSEDED IN PART — read this first (2026-09-12, FND-02).**
>
> This document is retained as the historical record of what Phase 2a built and intended.
> Two of its central claims were **not true of the shipped code**, from Phase 2a until FND-02:
>
> 1. **"Pre-Storage Redaction: content redacted before any persistence" was false.**
>    `apply_redaction()` read its regex from `rule["content"]`, and the dict actually passed
>    to it — `MemoryRulesEngine.evaluate()`'s result — carries **the memory's own text**
>    under that key. The memory became the regex that redacted it. Prose that matched itself
>    was destroyed entirely; anything with a regex metacharacter matched nothing and was
>    stored, FTS-indexed and embedded raw.
> 2. **The "Safe Fallbacks" section below described a privacy defect, not a safety feature.**
>    "Invalid regex patterns return original text", "unknown strategies return original
>    text", "missing `content` field skips redaction" — each of those is a broken privacy
>    control answering with the unredacted content. FND-02 replaced all three with
>    `RedactionPolicyError` and a refused write.
>
> Also: the YAML `redact:` category this document treats as live configuration was never
> loaded by `MemoryRulesEngine.PRIORITY`, so every rule under it was dead.
>
> For what redaction actually guarantees now, see `INTERFACES.md` §3 (governance pipeline
> and error modes), `RISKS.md` R1's Correction and `R-MEM-REDACT-SCOPE`, and
> `tests/test_fnd02_memory_redaction_contract.py`. The signature below
> (`apply_redaction(text, rule)`) no longer exists; it is now
> `apply_redaction(text: str, instruction: RedactionInstruction) -> str` and it refuses a
> mapping outright.

## Overview
Phase 2a introduces rule-based content redaction with hooks for encryption routing and summarization (to be fully implemented in phases 2b and 2c).

## What Was Implemented

### 1. Redaction Engine (`bartholomew/kernel/redaction_engine.py`)
A new module providing content redaction with three strategies:
- **mask**: Replace sensitive content with `****`
- **remove**: Delete sensitive content entirely
- **replace:<text>**: Replace with custom text

Key function:
```python
apply_redaction(text: str, rule: Dict[str, Any]) -> str
```

Features:
- Case-insensitive regex matching
- Safe fallback on invalid patterns
- Logging for debugging

### 2. Enhanced Memory Rules Engine (`bartholomew/kernel/memory_rules.py`)
Updated `evaluate()` method to:
- Pass through `redact`, `redact_strategy`, `encrypt`, and `summarize` fields from rules
- Default `redact_strategy` to "mask" when `redact: true` but no strategy specified
- Preserve backward compatibility

### 3. Redaction Hooks in Memory Storage

#### Memory Manager (`identity_interpreter/adapters/memory_manager.py`)
- Added redaction hook in `store_memory()` before database write
- Applies redaction when `redact_strategy` present in evaluated rules
- Metadata fields (`encrypt`, `summarize`) attached to memory entries for future phases
- TODO comments added for Phase 2b (encryption enforcement) and 2c (summarization)

#### Memory Store (`bartholomew/kernel/memory_store.py`)
- Added redaction hook in `upsert_memory()` before database write
- Mirrors memory_manager approach for consistency
- TODO comments for Phase 2b and 2c

### 4. Enriched Memory Rules (`bartholomew/config/memory_rules.yaml`)
Updated to v2.0 with Phase 2a fields:
- Added `redact`, `redact_strategy`, `encrypt`, `summarize` fields to appropriate rules
- New `redact` section for redaction-only rules (no storage blocking)
- Examples:
  - Mask passwords/bank info in `ask_before_store`
  - Replace SSN with `[SSN REDACTED]`
  - Encrypt routing with `encrypt: standard|strong`
  - Summarization flags for context-only memories

### 5. Comprehensive Test Suite (`tests/test_phase2a_redaction.py`)
19 tests covering:
- **Redaction Engine**: All three strategies (mask, remove, replace)
- **Edge Cases**: Invalid regex, unknown strategies, missing patterns
- **Rules Enrichment**: Default behavior, metadata pass-through
- **Integration**: Multiple redactions, memory dict format
- **YAML Configuration**: Validation of rule structure and Phase 2a fields

**All tests pass ✓** *(as of Phase 2a. Several of these tests asserted the fail-open
behaviour corrected by FND-02 and were updated then — see `tests/test_phase2a_redaction.py`.)*

## Key Design Decisions

### Minimal Surface Area
- Redaction applied at the latest safe point (before storage)
- No changes to existing MemoryEntry structure
- Backward compatible with existing rules

### Safe Fallbacks
*(Corrected 2026-09-12, FND-02: these were never safe. Each line describes a redaction
control failing **open** — answering "I cannot apply this policy" with the unredacted text.
All three now raise `RedactionPolicyError` and the write is refused.)*
- ~~Invalid regex patterns return original text + log error~~ → raises; write refused
- ~~Unknown strategies return original text + log warning~~ → rejected when the instruction
  is constructed; write refused
- ~~Missing `content` field in rule skips redaction~~ → a rule that requires redaction and
  declares no `redact_patterns` refuses the write

### Metadata Pass-Through
- `encrypt` and `summarize` fields flow through evaluation
- No enforcement yet (Phase 2b/2c)
- TODO comments mark future integration points

### Testability
- Pure function for `apply_redaction()` enables unit testing
- Rules engine enrichment testable in isolation
- Integration tests verify end-to-end flow

## Usage Examples

### Example 1: Masking Passwords
```yaml
- match:
    content: "(?i)(password|bank|account number)"
  metadata:
    redact_strategy: mask
    encrypt: strong
```
Input: "My password is hunter2"
Output: "My **** is ****"

### Example 2: Replacing SSN
```yaml
- match:
    content: "(?i)ssn:?\\s*\\d{3}-\\d{2}-\\d{4}"
  metadata:
    redact_strategy: replace:[SSN REDACTED]
    encrypt: strong
```
Input: "SSN: 123-45-6789"
Output: "SSN: [SSN REDACTED]"

### Example 3: Removing Sensitive Content
```yaml
- match:
    content: "(?i)confidential"
  metadata:
    redact_strategy: remove
```
Input: "This is confidential information"
Output: "This is  information"

## Files Modified/Created

### New Files
- `bartholomew/kernel/redaction_engine.py` - Core redaction logic
- `tests/test_phase2a_redaction.py` - Comprehensive test suite
- `PHASE_2A_IMPLEMENTATION.md` - This document

### Modified Files
- `bartholomew/kernel/memory_rules.py` - Enhanced evaluate() method
- `bartholomew/config/memory_rules.yaml` - Added Phase 2a fields
- `identity_interpreter/adapters/memory_manager.py` - Redaction hook in store_memory()
- `bartholomew/kernel/memory_store.py` - Redaction hook in upsert_memory()

## Integration Points for Future Phases

### Phase 2b: Encryption Enforcement
TODO locations marked in:
- `memory_manager.py:382` - Enforce encryption based on `evaluated["encrypt"]`
- `memory_store.py:90` - Enforce encryption based on `evaluated["encrypt"]`

### Phase 2c: Summarization
TODO locations marked in:
- `memory_manager.py:383` - Generate summary if `evaluated["summarize"]` is true
- `memory_store.py:91` - Generate summary if `evaluated["summarize"]` is true

## Testing Results
```
19 tests passed in 2.04s
```

All functionality verified:
- ✓ Redaction strategies (mask, remove, replace)
- ✓ Case-insensitive matching
- ✓ Error handling for invalid patterns
- ✓ Rules enrichment with Phase 2a fields
- ✓ Metadata pass-through (encrypt, summarize)
- ✓ YAML configuration validation
- ✓ Integration with memory storage paths

## Security Considerations

*(Corrected 2026-09-12, FND-02. Items 1 and 2 as originally written were not true of the
shipped code; they are restated here as what the repaired code actually does.)*

1. **Pre-Storage Redaction**: content is redacted before any persistence, using an explicit
   `RedactionInstruction` resolved once per write and applied identically to the stored
   value, the summary, the FTS index text, chunks and embedding source text.
   *(Originally claimed as working from Phase 2a; it was not — see the note at the top.)*
2. **Regex Safety**: an invalid pattern is a **refused write**, not a caught-and-logged
   no-op. *(Originally "invalid patterns safely caught and logged", which meant the raw
   content was stored.)*
3. **No Data Loss**: original evaluation preserved in evaluated dict
4. **Audit Trail**: Redaction operations logged at DEBUG level; a refusal logs at ERROR
   with the resolved instruction's provenance.
5. **Consent Still Required**: Redaction doesn't bypass consent rules

## Performance Impact

- **Minimal**: Regex operations only on matched content
- **Lazy Evaluation**: Redaction only when rules match
- **No Extra I/O**: Applied in-memory before write
- **Cached Patterns**: Regex compiled by Python's re module

## Next Steps

*(Note added 2026-07-28: both phases below are done — see `PHASE_2B_IMPLEMENTATION.md` and
`PHASE_2C_IMPLEMENTATION.md`. Retained here as historical context for what this phase anticipated,
not as an open TODO.)*

### Phase 2b: Encryption Enforcement
- Implement `encrypt: standard` (current AES-256)
- Implement `encrypt: strong` (future upgrade path, e.g., AES-256-GCM with key rotation)
- Add encryption metadata to database schema

### Phase 2c: Summarization
- Integrate with LLM for content summarization
- Store both summary and original (or just summary for `summarize: true`)
- Add summarization scheduling/batching

## Conclusion

Phase 2a successfully implements:
- ✓ Rule-based content redaction (mask/remove/replace)
- ✓ Metadata hooks for encryption and summarization
- ✓ Comprehensive test coverage
- ✓ Backward compatibility
- ✓ Clear integration points for Phase 2b/2c

The implementation provides a solid foundation for advanced privacy features while maintaining Bartholomew's existing functionality and safety guarantees.
