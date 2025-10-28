# Bartholomew Identity Interpreter

Python implementation of the Identity Interpreter for Bartholomew AI system.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Install package
pip install -e .

# Validate Identity.yaml
barth lint Identity.yaml

# Explain policy decisions
barth explain Identity.yaml --task-type code --confidence 0.4
```

## Features

- ✅ JSON Schema validation of Identity.yaml
- ✅ Pydantic v2 type-safe models
- ✅ Policy engines (model routing, tool control, safety, persona)
- ✅ Explainable decisions with YAML path references
- ✅ CLI tools (lint, explain, simulate)
- ✅ Adapter stubs (LLM, tools, consent, metrics, storage)
- ✅ Test suite with pytest

## Documentation

See [docs/README.md](docs/README.md) for full documentation.

## Project Structure

```
├── identity_interpreter/     # Core package
│   ├── policies/            # Policy engines
│   ├── adapters/            # External integrations (stubs)
│   └── schema/              # JSON Schema
├── tests/                   # Test suite
├── docs/                    # Documentation
├── scenarios/               # Test scenarios
└── exports/                 # Runtime outputs
```

## Next Steps

1. Wire real backends (Llama.cpp, Ollama, cloud APIs)
2. Implement real tool sandboxing
3. Add scenario-based testing
4. Build metrics dashboard

## License

CC-BY-NC-4.0
