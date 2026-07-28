> **STALE BANNER (added 2026-07-28, examples corrected 2026-07-28) — reference only, not current
> guidance on one remaining count.** This document's "Stage 3" numbering predates and does not
> match the current stage terminology in `MASTER_PLAN.md`/`ROADMAP.md` (Stage 0/1/5, Phase A/B) —
> do not use "Stage 3" here as a cross-reference to those documents' stage gates. The
> `ModelRouter`/`ResponseFormatter`/logging descriptions below appear architecturally accurate and
> are retained for that reference value. **The "Usage Examples" section's direct
> `orch.handle_input(...)` calls (a banner-only warning, previously) have been replaced below with
> the actual governed pattern** — `orch.handle_input()` remains real and correct as the injected
> `respond_fn` *capability* the seam calls, exactly matching what the live `/api/chat` endpoint
> does (`bartholomew_api_bridge_v0_1/services/api/app.py`'s `chat()`), but it is no longer shown as
> something to call directly/standalone as the primary integration point.

# Stage 3 Orchestration Integration - Complete

## ✅ Implementation Summary

Successfully implemented the Stage 3 orchestration layer for Bartholomew with all requested features.

## 📦 Components Created

### Core Orchestration Package (`identity_interpreter/orchestrator/`)

1. **`__init__.py`** - Package exports
2. **`orchestrator.py`** - Central controller coordinating all subsystems
3. **`pipeline.py`** - Sequential step executor
4. **`context_builder.py`** - Memory context injection (optional)
5. **`state_manager.py`** - Session state management
6. **`model_router.py`** - LLM backend routing with configurable defaults
7. **`response_formatter.py`** - Tone and emotion tag formatting
8. **`system_health.py`** - Health check utilities

### Configuration

- **`identity_interpreter/contracts/orchestration.yaml`** - Orchestration contract defining:
  - Pipeline stages
  - Routing configuration (stub, openai, anthropic, local)
  - Formatting modes (tags, structured)
  - Logging configuration
  - Supported tones and emotions

### CLI Integration

- **`identity_interpreter/cli.py`** - Added `health` command:
  ```bash
  python -m identity_interpreter.cli health
  ```

### Tests

- **`tests/test_orchestration_integration.py`** - Comprehensive test suite:
  - 15 tests covering all components
  - All tests passing ✅
  - Tests for routing, formatting, logging, state management, and pipeline execution

## 🎯 Key Features

### 1. ModelRouter
- Replaces mock routing logic with configurable routing
- Supports multiple backends: stub, openai, anthropic, local
- Backend-specific temperature and model configuration
- Easy to extend for additional LLM providers

### 2. ResponseFormatter
- **Tags Mode** (default): Prepends `[tone: X]` and `[emotion: Y]` to responses
- **Structured Mode**: Returns dict with `{text, tone, emotion, metadata}`
- Validates tone/emotion values against supported lists
- Supported tones: neutral, empathetic, authoritative, playful
- Supported emotions: warm, neutral, serious, enthusiastic

### 3. Orchestration Logging
- JSONL format logs written to `logs/orchestrator/orchestrator.log`
- Tracks:
  - All pipeline steps (inject_memory_context, route_model, format_response)
  - Timing data (duration_ms)
  - Input/output lengths
  - Session IDs
  - Routing decisions
  - Errors with stack traces

### 4. Memory Integration
- ContextBuilder optionally integrates with MemoryManager
- Gracefully handles missing identity configuration
- Injects conversation context into prompts when memory is available

### 5. Health Monitoring
- CLI command: `python -m identity_interpreter.cli health`
- Checks:
  - Memory subsystem (DB, encryption)
  - Orchestrator log directory (exists, writable)
  - Contract file presence

## 📊 Test Results

```
================ 15 passed, 45 warnings in 1.30s ================
```

All tests pass successfully:
- ✅ Context injection
- ✅ Session ID persistence
- ✅ Router selection and execution
- ✅ Formatter tags and structured modes
- ✅ Tone and emotion application
- ✅ Logging creates files and tracks steps
- ✅ Context builder operations
- ✅ State manager functionality
- ✅ Health check execution
- ✅ Log directory creation
- ✅ Pipeline step ordering

## 🚀 Usage Examples

### Basic Orchestration (corrected 2026-07-28 — via the governed Runtime Contract seam)

**Do not call `orch.handle_input()` directly as the integration point.** Route it through
`bartholomew.kernel.runtime_contract.run_chat_through_runtime_contract()`, which runs the full
Observation → Interpretation → Executive → Governance → Capability → Execution → Reflection →
Memory seam (see `COGNITIVE_RUNTIME.md`) and is what the live `/api/chat` endpoint actually does.
`orch.handle_input()` is still real and correct here — as the `respond_fn` capability the seam
calls, not as something invoked on its own:

```python
from identity_interpreter.orchestrator import Orchestrator
from bartholomew.kernel.runtime_contract import run_chat_through_runtime_contract

# Create orchestrator (memory optional)
orch = Orchestrator()

async def _respond(prompt: str) -> str:
    return orch.handle_input(prompt)

# `daemon` is a running KernelDaemon (e.g. the live API bridge's module-level `_kernel`).
# Governance (parking brake + Identity Policy) runs before `_respond` is ever called.
result = await run_chat_through_runtime_contract(daemon, "Hello, how are you?", _respond)
if not result.governance_allowed:
    raise RuntimeError(result.governance_reason or "Blocked by governance")
print(result.response)
```

### With Tone and Emotion (corrected 2026-07-28 — same governed seam)
```python
orch = Orchestrator()
orch.state.set("tone", "empathetic")
orch.state.set("emotion", "warm")

async def _respond(prompt: str) -> str:
    return orch.handle_input(prompt)

result = await run_chat_through_runtime_contract(daemon, "Tell me about yourself", _respond)
print(result.response)
# Output: [tone: empathetic] [emotion: warm] <response text>
```

*(The pre-correction versions of both examples called `orch.handle_input(...)` directly, with no
Runtime Contract seam and no `daemon` — that pattern is what the earlier stale banner on this
document warned about. It is preserved in `docs/archive/ENGINEERING_LOG_2026.md`'s and
`RISKS.md`'s historical accounts of the 2026-07-28 reconciliation, not here.)*

### Custom Routing
```python
from identity_interpreter.orchestrator import ModelRouter

router = ModelRouter()

# Select specific backend
data = {"backend": "openai"}
route = router.select_route(data)
# Returns: {"backend": "openai", "model": "gpt-4o-mini", ...}
```

### Health Check
```bash
python -m identity_interpreter.cli health
```

Output:
```
🧠 Memory Subsystem Health Report:
--------------------------------------------------
  db: True
  cipher: True

⚙️  Orchestrator Subsystem Health Report:
--------------------------------------------------
  log_directory: logs\orchestrator (exists)
  log_directory_writable: True
  contract_file: identity_interpreter\contracts\orchestration.yaml (exists)

✅ Health check complete
```

## 📝 Log Format

Example JSONL log entry:
```json
{
  "ts": "2025-10-29T02:08:36.123456",
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "step": "route_model",
  "event": "routed",
  "backend": "stub",
  "model": "stub-llm"
}
```

## 🔄 Next Steps (Optional Enhancements)

1. **Production LLM Integration**
   - Connect ModelRouter to actual OpenAI/Anthropic/Local backends
   - Implement retry logic and error handling
   - Add streaming support

2. **Advanced Memory Features**
   - Semantic search with embeddings
   - Memory consolidation
   - Context window optimization

3. **Enhanced Logging**
   - Log rotation (based on contract settings)
   - Structured error reporting
   - Performance metrics dashboard

4. **Response Formatting**
   - Support for custom emotion/tone vocabularies
   - Template-based formatting
   - Multi-language support

## ✅ Acceptance Criteria Met

- ✅ Full orchestration skeleton created
- ✅ Stable API links to memory (optional, graceful degradation)
- ✅ CLI health command integrated and working
- ✅ Orchestration contract YAML created
- ✅ Comprehensive test suite (15 tests, all passing)
- ✅ ModelRouter class implemented
- ✅ ResponseFormatter with emotion tags and tone shaping
- ✅ Orchestration trace logging under /logs/orchestrator/

All requirements from the Stage 3 scaffold have been successfully implemented and tested.
