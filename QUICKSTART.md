# Quick Start Guide

## Installation

```bash
# Install dependencies (pinned with hashes for reproducibility)
pip install -r requirements.txt

# Install package
pip install -e .

# Install email validator (required by Pydantic)
pip install email-validator
```

### Dependency Management

This project uses `pip-tools` to manage dependencies:

- **requirements.in**: Human-readable, unpinned dependencies (edit this)
- **requirements.txt**: Auto-generated, fully pinned with hashes (install from this)
- **requirements.lock**: Frozen snapshot of the entire environment

To update dependencies:
```bash
# Install pip-tools
pip install pip-tools

# Compile new requirements.txt from requirements.in
pip-compile --resolver=backtracking --generate-hashes -o requirements.txt requirements.in

# Update requirements.lock
pip freeze > requirements.lock
```

## Running the API Bridge (Optional)

### Local Development

To run the local REST API and minimal UI:

```bash
# Start the API server
uvicorn app:app --reload --port 5173

# API docs available at: http://localhost:5173/docs
# Minimal UI at: bartholomew_api_bridge_v0_1/ui/minimal/index.html
```

The API provides:
- **`/healthz`** - Minimal liveness endpoint (for load balancers/monitoring)
- **`/api/health`** - Detailed health check with timezone, orchestrator, and version info
- **`/api/liveness/self`** - Quick self-test snapshot for Brain Console
- **`/metrics`** - Prometheus metrics endpoint
- **`/api/chat`** - Chat with Bartholomew
- **`/api/water/log`** - Log water intake
- **`/api/water/today`** - Get today's water total

### Liveness & Metrics Endpoints

**GET /api/liveness/self**
- Purpose: Quick self-test snapshot for Brain Console
- Returns: `{"uptime": <int seconds>, "drives": [<str>...], "last_tick": "YYYY-MM-DDTHH:MM:SSZ"}`
- Example:
  ```bash
  curl -s http://localhost:5173/api/liveness/self | jq
  ```

**GET /metrics**
- Purpose: Prometheus text exposition format endpoint
- Series:
  - `kernel_uptime_seconds` (gauge) - Process uptime since API bridge start
  - `kernel_ticks_total{drive="<name>"}` (counter) - Total kernel ticks, labeled by active drive
- The API registers ProcessCollector and PlatformCollector on startup to expose CPU/memory/process/platform metrics
- Example:
  ```bash
  curl -s http://localhost:5173/metrics
  ```

**Security Note (Production):**

The `/metrics` endpoint is unauthenticated by default, which is fine for local development and testing. For production deployments, use the `METRICS_INTERNAL_ONLY` environment variable to move metrics to `/internal/metrics` and restrict access:

**Development/Test (default):**
```bash
# /metrics is public (no env var set)
uvicorn app:app --port 5173
curl http://localhost:5173/metrics
```

**Production Mode:**
```bash
# Set METRICS_INTERNAL_ONLY to move endpoint to /internal/metrics
export METRICS_INTERNAL_ONLY=1  # or "true", "yes", "on"
uvicorn app:app --port 5173

# Now /metrics returns 404
# Metrics are at /internal/metrics
curl http://localhost:5173/internal/metrics
```

**Production Deployment Options:**

1. **Reverse Proxy IP Allowlist (Recommended)**

   Nginx example:
   ```nginx
   location /internal/metrics {
       # Only allow Prometheus server and localhost
       allow 127.0.0.1;
       allow 10.0.0.5;  # Prometheus server IP
       deny all;
       proxy_pass http://127.0.0.1:5173;
   }
   ```

   Traefik example (docker-compose labels):
   ```yaml
   labels:
     - "traefik.http.routers.metrics.rule=Path(`/internal/metrics`)"
     - "traefik.http.routers.metrics.middlewares=metrics-ipwhitelist"
     - "traefik.http.middlewares.metrics-ipwhitelist.ipwhitelist.sourcerange=127.0.0.1/32,10.0.0.5/32"
   ```

2. **Private Network Only**

   Bind the API to a private interface and only allow Prometheus/sidecar access:
   ```bash
   # Bind to loopback or private network interface
   uvicorn app:app --host 127.0.0.1 --port 5173

   # Or bind to private subnet
   uvicorn app:app --host 10.0.0.10 --port 5173
   ```

3. **Prometheus Scrape Configuration**

   Update your Prometheus config to scrape `/internal/metrics`:
   ```yaml
   scrape_configs:
     - job_name: 'bartholomew'
       static_configs:
         - targets: ['localhost:5173']
       metrics_path: '/internal/metrics'  # Changed from /metrics
   ```

**Environment Variables:**
- `METRICS_INTERNAL_ONLY`: Set to `1`, `true`, `yes`, or `on` to enable production mode

### Docker Deployment

Run the API in a container:

```bash
# Using Docker Compose (recommended)
docker-compose up -d

# Or build and run manually
docker build -t bartholomew-api .
docker run -p 5173:5173 -v $(pwd)/data:/app/data bartholomew-api
```

### Environment Configuration

- **`ALLOWED_ORIGINS`**: Comma-separated CORS origins (default: `http://localhost,http://localhost:5173,http://127.0.0.1:5173,http://127.0.0.1`)
- **`TZ`**: Timezone for date/time operations (default: `Australia/Brisbane`)

Example:
```bash
ALLOWED_ORIGINS=http://localhost:3000,http://myapp.com TZ=Australia/Brisbane uvicorn app:app --port 5173
```

### Timezone Handling

All timestamps are handled in **Australia/Brisbane** timezone:
- Water logs use Brisbane time for "today" calculations
- ISO8601 timestamps are timezone-aware
- Server exposes current timezone via `/api/health`

See `bartholomew_api_bridge_v0_1/README_API_BRIDGE.md` for full details.

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

## Hybrid Retrieval Testing and Demo

### Running Unit Tests

Test individual components of hybrid retrieval:

```bash
# Test fusion math (normalization, weighted fusion, imputation)
pytest tests/test_hybrid_fusion_math.py -v

# Test recency shaping (exponential decay)
pytest tests/test_hybrid_recency.py -v

# Test kind boosts and RRF fusion
pytest tests/test_hybrid_rrf.py -v
```

### Running Integration Benchmark

Prove hybrid retrieval outperforms single-channel on paraphrases:

```bash
# Run paraphrase benchmark (requires ≥50-row dataset)
pytest tests/integration/test_hybrid_paraphrase_benchmark.py -v

# Expected output shows hit rates:
# Hit rates - FTS: 0.XX, Vector: 0.YY, Hybrid: 0.ZZ
# Assertion: Hybrid ≥ max(FTS, Vector)
```

The integration test validates:
- Hybrid beats best single-channel on paraphrase retrieval
- Privacy gates exclude `never_store` and `requires_consent` memories

### Using the Hybrid Search CLI

Demo hybrid search with explainable scores:

```bash
# Basic search (hybrid mode, default)
python -m scripts.hybrid_search --query "privacy protection" --k 10

# Explain mode: show fusion config and score breakdown
python -m scripts.hybrid_search --query "robot learning" --explain

# Use specific retrieval mode
python -m scripts.hybrid_search --query "encryption" --mode vector
python -m scripts.hybrid_search --query "search" --mode fts

# Use RRF fusion (Reciprocal Rank Fusion)
python -m scripts.hybrid_search --query "memory" --rrf --rrf-k 30

# Custom database path
python -m scripts.hybrid_search --query "test" --db data/custom.db
```

Example output with `--explain`:
```
Search Results (hybrid mode)
Query: 'privacy protection'
Found 5 results

Detailed Score Breakdown (Hybrid Mode)
Fusion: weighted
Weights: FTS=0.60, Vec=0.40
Recency half-life: 168.0h

[1] Memory ID: 7
Score: 0.8542
Kind: fact
Snippet: Protecting user privacy is fundamental...
```

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

# Run hybrid retrieval tests only
pytest tests/test_hybrid_*.py tests/integration/ -v

# Smoke test the API
bash bartholomew_api_bridge_v0_1/scripts/curl_smoke.sh http://localhost:5173

# Run Docker Compose
docker-compose up -d
docker-compose logs -f
docker-compose down
```

## CI/CD

The project includes a GitHub Actions workflow (`.github/workflows/smoke.yml`) that:
- Boots the API server
- Validates `/healthz` returns `{"status": "ok", "version": "0.1.0"}`
- Tests `/api/health` and `/docs` endpoints

This ensures the API can start successfully on every commit.
