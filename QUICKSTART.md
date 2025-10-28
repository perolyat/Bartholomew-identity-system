# Quick Start Guide

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Install package
pip install -e .

# Install email validator (required by Pydantic)
pip install email-validator
```

## Basic Usage

### 1. Validate Your Identity Configuration

```bash
barth lint Identity.yaml
```

Output:
```
✓ Schema validation passed
✓ Pydantic parsing passed
✓ No warnings
Identity is valid!
```

### 2. Explain Policy Decisions

```bash
barth explain Identity.yaml --task-type code --confidence 0.4 --tool web_fetch
```

This shows:
- **Model Selection**: Which model is chosen for the task type
- **Confidence Policy**: Actions required for low confidence scenarios
- **Tool Policy**: Whether tool is allowed and consent requirements
- **Persona Configuration**: Active traits and tone

### 3. Use in Code

```python
from identity_interpreter import load_identity, normalize_identity
from identity_interpreter.policies import select_model, check_tool_allowed

# Load identity
identity = load_identity("Identity.yaml")
identity = normalize_identity(identity)

# Make policy decisions
model_decision = select_model(identity, task_type="code")
print(f"Model: {model_decision.decision['model']}")
print(f"Because: {model_decision.rationale}")

# Check tool access
tool_decision = check_tool_allowed(identity, "web_fetch")
print(f"Allowed: {tool_decision.decision['allowed']}")
print(f"Requires consent: {tool_decision.requires_consent}")
```

## Key Features Demonstrated

✅ **Schema Validation**: Your Identity.yaml is validated against JSON Schema
✅ **Model Routing**: Task-based model selection (code task → Mistral-7B)
✅ **Confidence Policy**: Low confidence (0.4 < 0.55) triggers safety actions
✅ **Tool Control**: web_fetch is on allowlist, requires per-session consent
✅ **Explainability**: Every decision shows YAML path references
✅ **Persona**: Traits (empathetic, calm, etc.) and tone (plainspoken, warm, direct)

## Next Steps

1. **Run Tests**: `pytest tests/`
2. **Explore Policies**: Check `identity_interpreter/policies/` for all policy engines
3. **Wire Backends**: Replace adapter stubs with real implementations
4. **Add Scenarios**: Create test scenarios in `scenarios/` directory

## Documentation

- Full docs: [docs/README.md](docs/README.md)
- Architecture: See policy engines and adapter system
- Extending: Add custom policies and adapters

## Common Commands

```bash
# Lint with different identity file
barth lint path/to/identity.yaml

# Explain with different parameters
barth explain Identity.yaml --task-type general --confidence 0.8

# Run tests
pytest tests/

# Run tests with coverage
pytest --cov=identity_interpreter tests/
