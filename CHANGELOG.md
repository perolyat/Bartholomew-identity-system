# Changelog

All notable changes to Bartholomew will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.1] - 2025-01-11

### Added - Phase 2d: Vector Embeddings

#### Core Features
- **Compute-only embeddings**: Generate embeddings without persisting them (`embed_store=false`)
  - Returns ephemeral embeddings in `StoreResult.ephemeral_embeddings`
  - Supports post-consent promotion via `persist_embeddings_for()`
- **Strict model matching**: Vector search enforces provider/model/dim matching
  - Prevents cross-model contamination
  - Configurable via `allow_mismatch` parameter
- **Summary fallback**: Automatically falls back to redacted content when summary missing
  - Logs warning on first occurrence
  - Trims to ~500 chars as summary substitute
- **Retrieval boost scoring**: Apply multiplicative boost from rules (`retrieval.boost`)
  - Affects final ranking in search results
- **Policy flags**: `RetrievedItem.policy_flags` set for rule-based filtering
  - Includes `context_only` flag when `recall_policy="context_only"`

#### Admin Tools
- **CLI tool**: New `bartholomew` command-line interface
  - `bartholomew embeddings stats`: Show configuration and database statistics
  - `bartholomew embeddings rebuild-vss`: Rebuild SQLite VSS virtual table and triggers
  - Install via `pip install -e .` to expose the script

#### Configuration
- **Watcher control**: `BARTHO_EMBED_RELOAD=0` disables embeddings.yaml file watcher
  - Useful for tests/CI environments
  - Prevents background thread creation

#### Testing
- Comprehensive integration tests for all new features
- Coverage for fallback, boost ranking, policy flags, and compute-only paths

### Changed
- `MemoryStore.upsert_memory()` now returns `StoreResult` with memory_id, stored status, and ephemeral embeddings
- Backward compatible: existing code ignoring return value continues to work

### Fixed
- Summary-missing fallback prevents embedding failures
- Strict model matching prevents accidental cross-provider queries

### Documentation
- Updated PHASE_2D_IMPLEMENTATION.md with compute-only section
- Added BARTHO_EMBED_RELOAD documentation
- Added admin CLI usage examples
- README/QUICKSTART updates for Phase 2d features

## [Unreleased]

### Added
- **Unified retrieval factory**: `get_retriever(mode="hybrid"|"vector"|"fts")` 
  - Single entry point for all retrieval modes
  - Configuration precedence: explicit arg > BARTHO_RETRIEVAL_MODE env > kernel.yaml retrieval.mode > "hybrid"
  - Returns retrievers with unified `.retrieve(query, top_k, filters)` interface
- **FTS-only retriever**: `FTSOnlyRetriever` for full-text search without embeddings
  - Suitable for exact keyword matching and low-resource environments
  - Supports RetrievalFilters (kinds, after/before timestamps)
  - BM25 ranking with normalized scores
- **Vector retriever adapter**: `VectorRetrieverAdapter` wraps existing `Retriever.query()` as `.retrieve()`
  - Provides consistent interface across all retrieval modes
- **Retrieval mode configuration**: Added `retrieval.mode` to config/kernel.yaml
  - Default: "hybrid" (FTS + vector fusion via HybridRetriever)
  - Options: "hybrid", "vector", "fts"

### Changed
- Retrieval interface unified: all modes expose `.retrieve(query, top_k, filters)` method
- Backward compatible: existing `Retriever.query()` usage unchanged

### Planned
- Phase 2e: Hybrid retrieval enhancements
- Phase 2f: Chunking for long documents
- Phase 2d+: Orthonormal rotation for at-rest obfuscation
- Per-user encryption keys with rotation sync
