# Bartholomew Identity Interpreter

> **Scope note (2026-07-27):** this is a module reference for the `identity_interpreter`
> package. It is **not** an authority on project status — see `MASTER_PLAN.md` (the SSOT),
> `ROADMAP.md` for stage gates, and `COGNITIVE_RUNTIME.md` for how the runtime actually fits
> together today.
>
> **Corrected 2026-07-27:** this document described three modules that no longer exist —
> `policies/tool_policy.py`, `policies/persona.py` and `adapters/kill_switch.py` — as current
> API. All three were deliberately removed in items 11.12–11.14 (2026-07-22) once their last
> callers were migrated, under `DECISIONS.md`'s "One authority per architectural concept". The
> `check_tool_allowed` example this file showed no longer imports.

## Overview

The Identity Interpreter parses, validates, and enforces the Identity.yaml configuration for the Bartholomew AI system. It provides:

- **Schema Validation**: JSON Schema validation of identity files
- **Policy Engines**: Model routing, tool control, safety checks, persona shaping
- **Explainability**: Every decision includes YAML path references
- **CLI Tools**: Lint, explain, and simulate commands
- **Adapter System**: Pluggable backends for LLMs, tools, consent, metrics

> 🧠 **Understanding Bartholomew's Brain**: For a comprehensive overview of the cognitive architecture and how all components work together as an integrated brain, see [brain.md](brain.md).

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Install in development mode
pip install -e .
```

## Quick Start

### Validate Identity Configuration

```bash
python -m identity_interpreter.cli lint Identity.yaml
```

### Explain Decisions

```bash
# Explain model selection and confidence policy
python -m identity_interpreter.cli explain Identity.yaml \
  --task-type code \
  --confidence 0.4 \
  --tool web_fetch
```

### Use in Code

```python
from identity_interpreter import load_identity, normalize_identity
from identity_interpreter.policies import select_model

# Load and normalize
identity = load_identity("Identity.yaml")
identity = normalize_identity(identity)

# Make policy decisions
decision = select_model(identity, task_type="code")
print(f"Selected model: {decision.decision['model']}")
print(f"Rationale: {decision.rationale}")
```

## CLI Commands

### `lint`

Validates identity.yaml against JSON Schema and checks for warnings:

```bash
python -m identity_interpreter.cli lint Identity.yaml
```

Checks:
- Schema compliance
- Pydantic parsing
- Recommended settings (kill switch, encryption, etc.)
- Configuration warnings

### `explain`

Shows decision trace for a scenario:

```bash
python -m identity_interpreter.cli explain Identity.yaml \
  --task-type general \
  --confidence 0.5 \
  --tool web_fetch
```

Explains:
- Model selection with rationale
- Confidence policy actions
- Tool allowlist and consent requirements
- Persona configuration

### `simulate`

Runs full decision simulation from scenario file (future):

```bash
python -m identity_interpreter.cli simulate scenarios/medical_consent.yaml
```

## Architecture

### Core Components

1. **Loader** (`loader.py`)
   - YAML parsing
   - JSON Schema validation
   - Pydantic model construction
   - Linting with warnings

2. **Models** (`models.py`)
   - Pydantic v2 models for type safety
   - `Identity`: Root configuration model
   - `Decision`: Policy decision with explainability

3. **Normalizer** (`normalizer.py`)
   - Resolves derived values (e.g., dynamic_resize)
   - Applies model parameter overlays
   - Budget-driven model filtering

4. **Policy Engines** (`policies/`) — the modules that exist today
   - `model_router`: Task-based model *selection* (`select_model`, reads `Identity.yaml`'s
     `by_task_type`). Distinct from `orchestrator/model_router.py`, which does backend
     *routing* — reclassified as not-a-duplicate in item 11.15.
   - `safety`: Red lines, sensitive modes, crisis protocols
   - `confidence`: Low confidence threshold actions
   - ~~`tool_policy`~~ — **removed 2026-07-22 (item 11.14).** The `tool_use`-allowlist check now
     lives in `bartholomew/kernel/policy_engine.py`'s `evaluate_tool_policy()`, built from a
     declarative `IdentityContext`. Use that.
   - ~~`persona`~~ — **removed 2026-07-22 (item 11.12).** `bartholomew/kernel/persona_pack.py`'s
     `PersonaPackManager` is the single persona authority. Note the deliberate split: persona
     packs own tone/style (how Bartholomew presents), `Identity.yaml` owns `traits` (who it is).

5. **Adapters** (`adapters/`) — the modules that exist today
   - `llm_stub`: LLM interface (local/cloud)
   - `tools_stub`: Tool execution with sandboxing
   - `consent_terminal`: User consent prompts
   - `metrics_logger`: Alignment metrics tracking
   - `storage`: Audit logs and ethical journal
   - `memory_manager` / `context_builder`: used by `reflection_generator`'s own `Orchestrator`;
     deliberately **not** used for chat context (see `DECISIONS.md`)
   - `sight/`, `voice_io/`: compatibility entry points (`start_capture()`, `start_stream()`)
     that delegate exclusively to the governed Runtime Contract seams. The capture/stream
     capability itself is an inert Stage 6 placeholder.
   - ~~`kill_switch`~~ — **removed 2026-07-22 (item 11.13);** it was print-only with zero live
     callers. `bartholomew/orchestrator/safety/parking_brake.py`'s `ParkingBrake` is the sole
     kill-switch authority.

### Decision Flow

```
Input (task, confidence, tool request, etc.)
  ↓
Identity Configuration (Identity.yaml)
  ↓
Normalizer (derived values)
  ↓
Policy Engines (model_router, tool_policy, safety, etc.)
  ↓
Decision Object (decision + rationale + consent flag)
  ↓
Adapters (LLM, tools, metrics, storage)
  ↓
Output (with audit trail)
```

## Policy Examples

### Model Selection

```python
from identity_interpreter.policies import select_model

decision = select_model(identity, task_type="code", budget_exhausted=False)
# decision.decision: {'model': 'Mistral-7B-Instruct-GGUF-Q4_K_M', ...}
# decision.rationale: ['meta.deployment_profile.model_policies...']
```

### Tool Allowlist

*(Rewritten 2026-07-27. The previous example imported `check_tool_allowed` from
`identity_interpreter.policies`, which raises `ImportError` — the module was removed in item
11.14. Identity now publishes a declarative context; the Kernel's Executive turns it into an
executable decision.)*

```python
from identity_interpreter.identity_context import build_identity_context
from bartholomew.kernel.policy_engine import evaluate_tool_policy

context = build_identity_context(identity)          # declarative: "who am I?"
decision = evaluate_tool_policy(context, "web_fetch")  # executable: "may I do this now?"
# decision.allowed, decision.requires_consent, decision.rationale, decision.reason
```

### Red Lines

```python
from identity_interpreter.policies import check_red_lines

decision = check_red_lines(identity, "I am a human")
# decision.decision: {'violations': [...], 'blocked': True/False}
```

### Confidence Policy

```python
from identity_interpreter.policies import handle_low_confidence

decision = handle_low_confidence(identity, confidence_score=0.3)
# decision.decision: {'is_low_confidence': True, 'required_actions': [...]}
```

## Extending the Interpreter

### Adding New Policy Engines

```python
# identity_interpreter/policies/my_policy.py
from ..models import Identity, Decision

def my_policy_check(identity: Identity, input_data: dict) -> Decision:
    rationale = ["path.to.config"]

    # Your logic here
    result = {...}

    return Decision(
        decision=result,
        rationale=rationale,
        requires_consent=False
    )
```

### Custom Adapters

```python
# identity_interpreter/adapters/my_adapter.py
class MyAdapter:
    def __init__(self, identity_config):
        self.identity = identity_config

    def execute(self, params):
        # Your implementation
        pass
```

## Testing

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest --cov=identity_interpreter tests/

# Run specific test
pytest tests/test_policies.py::test_model_selection
```

## Development

### Project Structure

```
identity_interpreter/
├── __init__.py           # Package exports
├── loader.py             # YAML loading and validation
├── models.py             # Pydantic models
├── normalizer.py         # Value derivation
├── identity_context.py   # Declarative IdentityContext (consumed by the Kernel Executive)
├── cli.py                # CLI commands
├── policies/             # Policy engines
│   ├── model_router.py   # select_model (Identity task-type selection)
│   ├── safety.py
│   └── confidence.py
├── orchestrator/         # Live chat path (Orchestrator, ModelRouter, ContextBuilder)
├── adapters/             # External integrations (stubs)
│   ├── llm_stub.py
│   ├── tools_stub.py
│   ├── consent_terminal.py
│   ├── metrics_logger.py
│   ├── memory_manager.py
│   ├── reflection_generator.py
│   ├── storage.py
│   ├── sight/            # start_capture() -> governed runtime-contract seam
│   └── voice_io/         # start_stream()  -> governed runtime-contract seam
└── schema/
    └── identity.schema.json
```

*(Tree corrected 2026-07-27 against the actual package contents: `policies/tool_policy.py`,
`policies/persona.py` and `adapters/kill_switch.py` were removed in items 11.12–11.14.)*

### Code Style

- Python 3.10+
- Type hints required
- Pydantic v2 models
- Docstrings for public APIs
- pytest for testing

## Next Steps

1. **Wire Real Backends**: Replace adapter stubs with actual implementations
   - Connect to Llama.cpp/Ollama for local models
   - Implement cloud model APIs (OpenAI, Anthropic)
   - Add real tool execution with sandboxing

2. **Enhanced Safety**: Upgrade red line detection
   - Use NLP models for semantic analysis
   - Add context-aware violation detection
   - Implement graduated responses

3. **Metrics Dashboard**: Visualize alignment metrics
   - Track value_alignment_score over time
   - Monitor confidence distributions
   - Alert on policy violations

4. **Scenario Testing**: Build comprehensive test suite
   - Medical consent scenarios
   - Crisis protocol activation
   - Budget exhaustion fallback
   - Tool sandbox violations

## License

CC-BY-NC-4.0 (matches Identity.yaml license)

## Contact

Taylor Paul - tpaul733@gmail.com
