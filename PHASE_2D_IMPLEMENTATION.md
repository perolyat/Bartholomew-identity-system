# Phase 2d — Vector Embeddings Implementation

## Overview

Privacy-first, offline-first vector embeddings for memory retrieval in Bartholomew. Embeddings happen **after** summarization and **before** encryption in the pipeline: Rules → Redact → Summarize → **Embed** → Encrypt → Store.

## Architecture

### Core Components

1. **EmbeddingEngine** (`bartholomew/kernel/embedding_engine.py`)
   - Manages embedding generation with provider abstraction
   - Default: `local-sbert` with `BAAI/bge-small-en-v1.5` (dim=384)
   - Fallback: Deterministic hash-based embedder for CI/offline environments
   - Optional: OpenAI provider stub (requires env vars + rule permission)

2. **VectorStore** (`bartholomew/kernel/vector_store.py`)
   - SQLite-backed vector storage with BLOB encoding
   - Schema: `memory_embeddings(embedding_id, memory_id, source, dim, vec, norm, provider, model, created_at)`
   - Search: Brute-force cosine similarity (optional sqlite-vss acceleration)
   - Foreign key cascade: deletes embeddings when memory is deleted

3. **Retriever** (`bartholomew/kernel/retrieval.py`)
   - Privacy-aware retrieval with rule-based filtering
   - Excludes: `never_store`, `ask_before_store` (without consent)
   - Marks: `context_only` for internal use
   - Returns: Ranked results with snippets (summary preferred)

### Integration Points

- **MemoryStore.upsert_memory**: Embedding hook after encryption
  - Gated by: `BARTHO_EMBED_ENABLED=1` + per-rule `embed_store: true`
  - Embeds: Original (redacted) content, not encrypted ciphertext
  - Sources: summary and/or full per rule `embed` setting

- **Memory Rules**: Extended with embedding metadata
  - `embed`: none | summary | full | both (default: summary)
  - `embed_store`: bool (default: false for backwards compat)
  - `embed_remote_ok`: bool (default: false)
  - `embed_dim`: int (default: 384)

## Configuration

### embeddings.yaml

```yaml
embeddings:
  default_provider: local-sbert
  default_model: BAAI/bge-small-en-v1.5
  default_dim: 384
  default_mode: summary
  allow_remote: false
  store: false  # Global default (off for safety)
  encrypt: false  # Vectors stored plaintext for ANN search
```

### Environment Variables

- `BARTHO_EMBED_ENABLED=1`: Master switch to enable embeddings
- `OPENAI_API_KEY`: Required for OpenAI provider (if allowed by rules)

### Per-Rule Overrides (memory_rules.yaml)

```yaml
- kind: conversation.transcript
  embed: summary           # What to embed
  embed_store: true        # Persist embeddings
  embed_remote_ok: false   # Allow network providers
  retrieval:
    boost: 1.0             # Optional scoring weight
```

## Database Schema

```sql
CREATE TABLE memory_embeddings (
  embedding_id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id    INTEGER NOT NULL,
  source       TEXT NOT NULL CHECK(source IN ('summary','full')),
  dim          INTEGER NOT NULL,
  vec          BLOB NOT NULL,        -- float32 bytes
  norm         REAL NOT NULL,        -- L2 norm (≈1.0 for normalized)
  provider     TEXT NOT NULL,
  model        TEXT NOT NULL,
  created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
);

CREATE INDEX idx_mememb_memory_id ON memory_embeddings(memory_id);
CREATE INDEX idx_mememb_source ON memory_embeddings(source);
CREATE INDEX idx_mememb_dim ON memory_embeddings(dim);
```

## Usage

### Enabling Embeddings

```bash
# Set environment variable
export BARTHO_EMBED_ENABLED=1

# Add rule to memory_rules.yaml
- kind: conversation.transcript
  embed: summary
  embed_store: true
```

### Querying

```python
from bartholomew.kernel.retrieval import Retriever, RetrievalFilters
from bartholomew.kernel.vector_store import VectorStore
from bartholomew.kernel.embedding_engine import get_embedding_engine
from bartholomew.kernel.memory_rules import _rules_engine

# Initialize components
vec_store = VectorStore("path/to/db.db")
engine = get_embedding_engine()
retriever = Retriever(_rules_engine, vec_store, engine, memory_store)

# Search
results = retriever.query(
    "What did we discuss about AI?",
    top_k=5,
    filters=RetrievalFilters(kinds=["conversation.transcript"])
)

for item in results:
    print(f"Score: {item.score:.3f} | {item.snippet}")
```

## Privacy & Security

### What's Protected

- **Summary-first**: Default mode embeds summaries, not full content
- **Offline-first**: Local model by default, no network calls
- **Rule-governed**: Explicit opt-in per memory kind
- **Redaction-aware**: Embeddings generated from redacted content
- **Unencrypted at rest**: Vectors stored plaintext for search (mitigation: local-only, summary-first, OS file security)

### What's Not Protected (Known Limitations)

- Embeddings may leak semantic information even from summaries
- Vectors stored unencrypted to enable ANN search
- No obfuscation layer in Phase 2d (deferred to 2d+)

### Future Hardening (2d+)

- Orthonormal rotation matrix for at-rest obfuscation
- Per-user encryption keys with rotation sync
- Hybrid FTS + vector retrieval (Phase 2e)

## Dependencies

### Required
- `numpy>=1.24.0`

### Optional
- `sentence-transformers>=2.2.0`: Real SBERT embeddings
  - Falls back to deterministic hash-based embedder if unavailable
  - Useful for production; CI/tests work without it

- `sqlite-vss`: ANN search acceleration
  - Autodetected at runtime; graceful fallback to brute-force
  - Not required; install separately if needed

## Testing

```bash
# Run Phase 2d tests
pytest tests/test_phase2d_embeddings.py -v

# With embeddings enabled
BARTHO_EMBED_ENABLED=1 pytest tests/test_phase2d_embeddings.py -v
```

### Coverage

- Unit: EmbeddingEngine, VectorStore, Retriever
- Integration: MemoryStore.upsert_memory with embeddings
- Config: YAML structure validation
- Fallback: Deterministic embedder for CI

## Performance

- **Brute-force search**: O(N) in number of embeddings
  - Acceptable for <10k vectors
  - Single-threaded NumPy dot products
  - Future: sqlite-vss for O(log N) ANN search

- **Embedding generation**: O(M) in text length
  - Local SBERT: ~50ms per text on CPU
  - Fallback: <1ms per text (hash-based)

## Troubleshooting

### No embeddings stored

1. Check `BARTHO_EMBED_ENABLED=1` is set
2. Verify rule has `embed_store: true`
3. Check logs for lazy-load failures

### Import errors

```bash
# Install numpy
pip install numpy>=1.24.0

# Optional: Install sentence-transformers
pip install sentence-transformers>=2.2.0
```

### sqlite-vss not loading

- Expected behavior; system falls back to brute-force
- To install: see https://github.com/asg017/sqlite-vss

## Compute-Only Embeddings (Ephemeral)

### Overview

Embeddings can be computed without persisting to the database when `embed_store=false`. This is useful for:
- **Ask-before-store workflows**: Generate embeddings, get user consent, then persist
- **Temporary analysis**: Compute similarity without permanent storage
- **Testing and development**: Verify embedding quality without database writes

### Usage

```python
from bartholomew.kernel.memory_store import MemoryStore, StoreResult

store = MemoryStore("path/to.db")
await store.init()

# With embed=summary, embed_store=false in rules
result = await store.upsert_memory(
    kind="test",
    key="test1",
    value="Content here...",
    ts="2024-01-01T00:00:00Z"
)

# Access ephemeral embeddings
assert isinstance(result, StoreResult)
assert len(result.ephemeral_embeddings) > 0

for source, vec in result.ephemeral_embeddings:
    print(f"Source: {source}, shape: {vec.shape}, dtype: {vec.dtype}")
    # vec is numpy array, float32, L2-normalized
```

### Post-Consent Promotion

```python
# Later, after consent granted
count = await store.persist_embeddings_for(result.memory_id)
print(f"Persisted {count} embeddings")

# Now retrievable via vector search
from bartholomew.kernel.retrieval import Retriever
results = retriever.query("content", top_k=5)
```

### Specifications

- **Return type**: `List[Tuple[str, np.ndarray]]` where str is source ("summary"|"full")
- **Vector properties**:
  - dtype: `np.float32`
  - shape: `(dim,)` where dim matches config (default 384)
  - L2-normalized: `np.linalg.norm(vec) ≈ 1.0`
- **Behavior**:
  - Summary-first with fallback to redacted content (~500 chars)
  - Respects `embed` setting ("summary", "full", "both")
  - No database writes when `embed_store=false`
  - Not retrievable via `Retriever.query()` until persisted

## Watcher Control

The embeddings config file watcher can be disabled for tests/CI:

```bash
# Disable background watcher
export BARTHO_EMBED_RELOAD=0

# Run tests without watcher thread
BARTHO_EMBED_RELOAD=0 pytest tests/
```

This prevents the `EmbeddingEngineFactory` from starting a background thread that monitors `embeddings.yaml` for changes.

## Admin CLI

### Installation

```bash
# Install in editable mode to expose CLI
pip install -e .

# Verify installation
bartholomew --help
```

### Commands

#### Show Statistics

```bash
# Default database path
bartholomew embeddings stats

# Custom path
bartholomew embeddings stats --db path/to/db.db
```

Output includes:
- Enabled status (BARTHO_EMBED_ENABLED)
- Provider, model, dimension
- Fallback mode status
- SQLite VSS availability
- Total embedding count
- Distribution by (provider, model, dim)
- Distribution by source (summary/full)

#### Rebuild VSS

```bash
# Rebuild VSS virtual table and triggers
bartholomew embeddings rebuild-vss --db path/to/db.db
```

Use this after:
- Changing model/provider/dim in config
- Manual database modifications
- VSS corruption or migration

## Design Decisions

| Decision | Rationale | Trade-off |
|----------|-----------|-----------|
| Summary-first | Reduces leakage, smaller vectors | May miss nuanced full-text matches |
| Unencrypted vectors | Enables ANN search | Semantic leakage risk (mitigated: local-only) |
| Brute-force default | No external deps, simple, portable | O(N) search (acceptable for <10k) |
| Lazy loading | Optional feature, no impact when disabled | Slightly more complex init |
| Hash fallback | CI-friendly, deterministic | Lower quality than real embeddings |
| Compute-only | Flexible consent workflows | Requires explicit persistence call |
| Strict matching | Prevents cross-model bugs | Must rebuild-vss after config change |

## Related Phases

- **Phase 2a**: Redaction (embeddings use redacted content)
- **Phase 2b**: Encryption (embeddings stored before encryption)
- **Phase 2c**: Summarization (embeddings prefer summaries)
- **Phase 2e** (future): Hybrid retrieval (FTS + vectors)
- **Phase 2f** (future): Chunking for long documents

## References

- Embedding model: [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5)
- sqlite-vss: https://github.com/asg017/sqlite-vss
- sentence-transformers: https://sbert.net
