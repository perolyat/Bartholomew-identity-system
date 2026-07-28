# Bartholomew AI Identity System - Copilot Instructions

> **Corrected 2026-07-28** (documentation reconciliation pass 2): this file is read automatically
> by Copilot/coding agents, so it is corrected in place rather than archived. Fixes below: removed
> the uninstalled `barth` CLI command; removed hardcoded `D:/workspace/bartholomew0.0.1` /
> `d:\workspace\bartholomew0.0.1` developer-machine paths; fixed the `model_mapping` example's
> internal inconsistency; removed the "constitutional framework" self-description (this is a
> Copilot-facing pattern guide, not the enduring architecture document — see `CONSTITUTION.md` for
> that); added a pointer to the governed Runtime Contract seam so agents don't call
> `Orchestrator.handle_input()` directly as if it were the primary integration point.
>
> **GOLDEN RULE**: This document defines Copilot-facing patterns for the Bartholomew AI Identity
> System. Development, architecture decisions, and integrations should align with these patterns
> and with the canonical documentation set (`CONSTITUTION.md`, `MASTER_PLAN.md`,
> `COGNITIVE_RUNTIME.md`, `ROADMAP.md`, `DECISIONS.md`) — where this file and those disagree, the
> canonical docs win, per `MASTER_PLAN.md`'s "Canonical docs" section.

## Strategic Vision & End Goals

**Bartholomew** represents a paradigm shift toward **configurable, explainable, and ethically-bounded AI systems**. The end goal is a production-ready AI companion that:

1. **Operates within explicit ethical boundaries** defined in `Identity.yaml`
2. **Provides full explainability** for every decision with YAML path rationales
3. **Prioritizes user consent and autonomy** through systematic consent management
4. **Enables budget-conscious deployment** with offline-first, local-primary architecture
5. **Scales from personal companion to enterprise policy enforcement**

## Architecture Overview

**Bartholomew** is an AI identity configuration and policy enforcement system centered around a single canonical `Identity.yaml` file that defines AI behavior, ethics, safety constraints, and operational parameters.

### Core Components

- **`Identity.yaml`**: The central configuration file defining all AI behavior, safety policies, ethics, and operational parameters
- **`identity_interpreter/`**: Python package for parsing, validating, and normalizing the identity configuration
  - `loader.py`: YAML parsing with JSON Schema validation via `IdentityLoadError` exceptions
  - `models.py`: Pydantic v2 models providing type-safe access to all configuration sections
  - `normalizer.py`: Computes derived values (e.g., dynamic memory sizing, model parameter overlays)
  - `schema/identity.schema.json`: JSON Schema for configuration validation
  - `policies/`: Policy engines for model routing, safety checks, tool control, confidence handling
  - `adapters/`: Interface stubs for external systems (LLM, storage, metrics, consent, tools)
  - `orchestrator/`: Central controller coordinating input, memory, and model routing with JSONL logging
- **`bartholomew_api_bridge_v0_1/`**: FastAPI bridge exposing REST endpoints for UI integration
  - `services/api/app.py`: Core API with chat, health, water logging endpoints
  - `app.py`: Root import stub for uvicorn deployment
- **`chat.py`**: Interactive terminal interface demonstrating full system integration
- **`test_bartholomew.py`**: Integration test with real Ollama backend

### Key Design Patterns

**Path-Based Configuration Access**: Use dotted notation for configuration paths (e.g., `meta.deployment_profile.budgets.low_balance_behavior`). The `Decision` model includes `rationale` with YAML paths explaining policy decisions.

**Three-Layer Processing**:
1. **Load**: Parse YAML → Validate against schema → Raise `IdentityLoadError` on failure
2. **Model**: Convert to Pydantic types for type safety and validation
3. **Normalize**: Compute derived values (dynamic memory sizing, effective model parameters)

**Policy Engine Pattern**: Model-selection policy:
```python
from identity_interpreter.policies import select_model
decision = select_model(identity, task_type="code", budget_exhausted=False)
# Decision includes 'decision', 'rationale', 'confidence', 'requires_consent'
```
Tool-use policy (corrected 2026-07-28: `identity_interpreter.policies.check_tool_allowed` was
deleted 2026-07-22 — see `DECISIONS.md`'s "Retire the deprecated tool-policy module" entry — its
functional successor is `evaluate_tool_policy()`, consulted via a declarative `IdentityContext`):
```python
from identity_interpreter.identity_context import build_identity_context
from bartholomew.kernel.policy_engine import evaluate_tool_policy
context = build_identity_context(identity)
policy_decision = evaluate_tool_policy(context, "web_fetch")
# PolicyDecision includes 'allowed', 'requires_consent', 'rationale', 'reason'
```

**Adapter Stub Pattern**: External integrations use consistent interface stubs in `adapters/`:
- `LLMAdapter`: Ollama integration with model name mapping
- `StorageAdapter`: Memory persistence with encryption
- `ConversationTurn`: Structured chat history storage
- `MetricsLogger`: Decision tracking and audit logs

**Orchestrator Pipeline**: Request processing through configurable pipeline with JSONL trace logging:
```python
# Pipeline: inject_memory_context → route_model → format_response
data = orchestrator.handle_input(user_input)
# Logs to logs/orchestrator/orchestrator.log with timing and session tracking
```
**Do not call `orchestrator.handle_input()` directly as the primary integration point for new
code** (corrected 2026-07-28). The live `/api/chat` endpoint and any new agent-facing integration
should go through the governed Runtime Contract seam,
`bartholomew.kernel.runtime_contract.run_chat_through_runtime_contract()`, which wraps
`handle_input()` and additionally runs the Observation/Interpretation/Governance/Reflection stages
(parking brake, Identity Policy Decision, Experience Kernel context, memory persistence) — see
`COGNITIVE_RUNTIME.md`. `handle_input()` remains the actual backend `respond_fn` the seam calls;
it is not itself the governed entry point.

## Critical Configuration Sections

> **IMMUTABLE PRINCIPLES**: These configuration sections represent core behavioral contracts that define system identity. Changes to these areas require careful consideration of ethical and safety implications.

- **`red_lines`**: Immutable behavioral boundaries that cannot be overridden under any circumstances
- **`safety_and_alignment.controls.kill_switch`**: Emergency safety mechanism with mandatory test requirements
- **`tool_use.default_allowed`**: Security model is deny-by-default with explicit allowlisting (never compromise)
- **`memory_policy.encryption`**: Data protection requirements for persistent storage (privacy-first)
- **`governance.change_control`**: Defines what configuration changes require human approval (human-in-the-loop)
- **`persona.personality_modes`**: Multi-mode personality switching (exploration, tactical, healthcare)

## Development Workflows

**Environment Setup**:
```bash
pip install -e .                    # Install in development mode
```

**CLI Commands** (run from your activated virtual environment, any OS — corrected 2026-07-28: the
`barth` entry point below was never installed by `pyproject.toml`; use `python -m
identity_interpreter.cli ...`, and the hardcoded developer-machine path previously shown here was
removed — activate your own virtualenv first, e.g. `.venv/bin/activate` or `.venv\Scripts\activate`):
```bash
# Primary validation workflow
python -m identity_interpreter.cli lint Identity.yaml

# Policy decision tracing
python -m identity_interpreter.cli explain Identity.yaml --task-type code --confidence 0.4 --tool web_fetch
```

**Testing & Development**:
- `python test_bartholomew.py` - Basic integration test with Ollama
- `pytest tests/` - Full test suite with policy engine tests
- `python chat.py` - Interactive chat interface for end-to-end testing
- `ollama list` - Verify available local models before testing

**API Bridge Development** (corrected 2026-07-28: removed the hardcoded developer-machine path;
run from the repository root):
```bash
uvicorn app:app --reload --port 5173
# Test endpoints: /api/health, /api/chat, /api/water/log (legacy example, see RISKS.md)
```

**Model Integration**: The `LLMAdapter` maps Identity.yaml model names to Ollama models:
```python
model_mapping = {
    "Mistral-7B-Instruct-GGUF-Q4_K_M": "mistral:7b-instruct",
    "TinyLlama 1.1B": "tinyllama",
    "Phi-4 3B": "phi3:mini"
}
```
*(Corrected 2026-07-28: this example previously mapped an "Instruct" model name to
`qwen2.5-coder:7b`, a coder model — an internal inconsistency. Verify the exact mapping against
`identity_interpreter/adapters/llm_stub.py`'s actual `model_mapping` before relying on this
example.)*

**Entry Point Usage** (corrected 2026-07-28): `pyproject.toml` — not `setup.py` — is the manifest
that actually installs. It declares `bartholomew` (an admin CLI for embeddings/parking-brake, see
`bartholomew/cli.py`) and `bartholomew-backfill-fts`, not `barth`. `setup.py` separately declares a
`barth` entry point pointing at `identity_interpreter.cli:main`, but `pyproject.toml` is what
actually installs when you `pip install -e .` — `barth` is **not** on `PATH`. This is tracked as an
open item (two competing packaging manifests) in `RISKS.md` finding F9; use `python -m
identity_interpreter.cli ...` for lint/explain until that's resolved.

## Critical File Dependencies

- **Always validate first**: `load_identity()` before any operations
- **Schema validation**: All changes must pass `identity.schema.json` validation
- **Model parameters**: Use `get_model_parameters(identity, model_name)` for runtime config
- **Budget-aware model selection**: `get_available_models(identity, budget_exhausted)`
- **PYTHONPATH setup**: run `uvicorn app:app` from the repository root (the root `app.py` import
  stub resolves correctly from there); no hardcoded developer-machine path is required

## Project Structure Patterns

**Package Layout**: Standard Python package with CLI entry point and orchestrator
```
identity_interpreter/
├── __init__.py           # Package exports (load_identity, normalize_identity)
├── loader.py             # YAML → Schema → Pydantic pipeline
├── models.py             # Type-safe Pydantic v2 models
├── normalizer.py         # Derived value computation
├── cli.py                # Typer-based CLI with rich formatting
├── policies/             # Decision engines (pure functions)
├── adapters/             # External system stubs (Ollama, storage, etc.)
└── orchestrator/         # Central pipeline controller with JSONL logging
```

**API Bridge Structure**: FastAPI service bridge for UI integration
```
bartholomew_api_bridge_v0_1/
├── app.py                # Root import stub for uvicorn
├── services/api/         # Core API implementation
│   ├── app.py           # FastAPI app with chat/health endpoints
│   ├── models.py        # Pydantic API models
│   └── db.py            # SQLite for water logging
└── ui/minimal/          # Basic web UI components
```

**Test Organization**:
- `test_bartholomew.py` - Integration test using `chat.py` and real Ollama
- `tests/test_policies.py` - Unit tests for policy engines
- `chat.py` - Full-stack interactive test interface
- `scenarios/` - Planned scenario-based testing (currently empty)

## Security & Ethics Patterns

> **ETHICAL FOUNDATION**: These patterns embody the core values of transparency, consent, and user autonomy. They are not optional features but fundamental requirements for responsible AI deployment.

**Explainable Decisions**: All policy decisions return `Decision` objects with YAML path rationales for audit trails and regulatory compliance.

**Consent-First Design**: Sensitive operations require explicit consent flags throughout configuration—no assumed permissions.

**Offline-First**: System prioritizes local models (`local_primary`, `local_fallbacks`) with cloud as optional fallback for budget consciousness and privacy.

**Sandboxed Tool Use**: Filesystem/network access explicitly constrained via `tool_use.sandbox` configuration—security by design.

**Multi-Modal Persona**: Adaptive personality modes for different contexts (exploration, tactical, healthcare) with explicit mode switching.

## Data Flow

> **CANONICAL PIPELINE**: This represents the immutable data processing pipeline. All features must flow through this architecture to maintain consistency and auditability.

```
Identity.yaml → loader.py → Pydantic models → normalizer.py → Policy engines → Orchestrator pipeline → Adapter stubs → Runtime
```

**Full Request Flow**: User input → Red line check → Model selection → Prompt building → LLM generation → Response validation → Confidence evaluation → Memory storage → Response formatting

All runtime decisions flow through policy engines that reference specific YAML configuration paths for explainability. The orchestrator logs all pipeline steps with timing data to `logs/orchestrator/orchestrator.log` for debugging and performance analysis.
