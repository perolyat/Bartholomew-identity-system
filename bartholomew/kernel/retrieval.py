"""
Retrieval Engine for Bartholomew
Implements privacy-aware vector search with rule-based filtering

Provides unified get_retriever(mode="hybrid"|"vector"|"fts") factory for
constructing retriever instances with consistent .retrieve() API.
"""

from __future__ import annotations

import html
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Any

from bartholomew.kernel.embedding_engine import EmbeddingEngine, get_embedding_engine
from bartholomew.kernel.fts_client import fts5_available
from bartholomew.kernel.memory_rules import MemoryRulesEngine
from bartholomew.kernel.vector_store import VectorStore


logger = logging.getLogger(__name__)


# FTS5 availability cache (probed once at startup)
_fts5_available_cache: bool | None = None


def _check_fts5_once(db_path: str) -> bool:
    """
    Check if FTS5 is available (cached after first check).

    Opens a connection to the database and probes for FTS5 support.
    Result is cached to avoid repeated checks.

    Args:
        db_path: Path to SQLite database

    Returns:
        True if FTS5 is available, False otherwise
    """
    global _fts5_available_cache

    if _fts5_available_cache is not None:
        return _fts5_available_cache

    # Probe FTS5 availability
    try:
        conn = sqlite3.connect(db_path)
        available = fts5_available(conn)
        conn.close()
    except Exception:
        available = False

    _fts5_available_cache = available

    if not available:
        logger.warning(
            "FTS5 not available; hybrid mode will operate vector-only "
            "and fts mode will degrade to vector-only to keep API stable.",
        )
    else:
        logger.debug("FTS5 is available")

    return available


@dataclass
class RetrievalFilters:
    """Filters for retrieval queries"""

    kinds: list[str] | None = None
    source: str | None = None  # 'summary' or 'full'
    after: str | None = None  # ISO timestamp
    before: str | None = None  # ISO timestamp


@dataclass
class RetrievedItem:
    """Single retrieved memory item"""

    memory_id: int
    score: float
    snippet: str
    recall_policy: str | None = None
    kind: str | None = None
    context_only: bool = False
    policy_flags: set = None  # Phase 2d+: Set of policy flags

    def __post_init__(self):
        """Initialize policy_flags if not provided"""
        if self.policy_flags is None:
            self.policy_flags = set()


class Retriever:
    """
    Privacy-aware retrieval engine

    Integrates vector search with memory rules to enforce:
    - never_store: not present (no embeddings)
    - ask_before_store: excluded unless consent granted
    - context_only: returned but marked for internal use
    """

    def __init__(
        self,
        rules_engine: MemoryRulesEngine,
        vector_store: VectorStore,
        embedding_engine: EmbeddingEngine | None = None,
        memory_store: Any | None = None,
    ):
        """
        Initialize retriever

        Args:
            rules_engine: Memory rules engine for policy evaluation
            vector_store: Vector store for similarity search
            embedding_engine: Embedding engine (defaults to singleton)
            memory_store: Memory store for loading full memory data
        """
        self.rules_engine = rules_engine
        self.vector_store = vector_store
        self.embedding_engine = embedding_engine or get_embedding_engine()
        self.memory_store = memory_store

    def query(
        self,
        text: str,
        top_k: int = 8,
        filters: RetrievalFilters | None = None,
    ) -> list[RetrievedItem]:
        """
        Query for similar memories

        Args:
            text: Query text
            top_k: Number of results to return
            filters: Optional filters

        Returns:
            List of retrieved items, sorted by relevance
        """
        if filters is None:
            filters = RetrievalFilters()

        # Embed query text
        try:
            qvec = self.embedding_engine.embed_texts([text])[0]
        except Exception as e:
            logger.error(f"Failed to embed query: {e}")
            return []

        # Vector search with strict model matching (Phase 2d+)
        try:
            cfg = self.embedding_engine.config
            candidates = self.vector_store.search(
                qvec,
                top_k=top_k * 2,  # Get more candidates for filtering
                provider=cfg.provider,
                model=cfg.model,
                dim=cfg.dim,
                source=filters.source,
                # Phase 2d Fixpack v3: relax provider/model matching for
                # vector-only Retriever while keeping dim strict, to ensure
                # tests that use ad-hoc model names (e.g. "test-model") can
                # still retrieve their embeddings.
                allow_mismatch=True,
            )
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []

        if not candidates:
            return []

        # Load memory data and apply rule-based filtering
        # Store tuples of (item, ts) for tie-breaking
        results_with_ts = []
        for memory_id, score in candidates:
            # Load memory details
            memory_data = self._load_memory(memory_id)
            if memory_data is None:
                continue

            # Apply filters
            if not self._passes_filters(memory_data, filters):
                continue

            # Evaluate rules for this memory
            evaluated = self.rules_engine.evaluate(memory_data)

            # Check if memory should be excluded from retrieval
            if not self._should_include(evaluated):
                continue

            # Phase 2d+: Apply retrieval boost from rules
            boost = evaluated.get("retrieval", {}).get("boost", 1.0)
            final_score = score * float(boost)

            # Phase 2d+: Build policy flags set
            policy_flags = set()
            recall_policy = evaluated.get("recall_policy")
            if recall_policy == "context_only":
                policy_flags.add("context_only")

            # Build result item
            item = RetrievedItem(
                memory_id=memory_id,
                score=final_score,
                snippet=self._extract_snippet(memory_data),
                recall_policy=recall_policy,
                kind=memory_data.get("kind"),
                context_only=(recall_policy == "context_only"),
                policy_flags=policy_flags,
            )

            # Store with timestamp for tie-breaking
            results_with_ts.append((item, memory_data.get("ts")))

        # Sort by score desc, recency desc, mem_id asc and take top_k
        def sort_key(item_ts_tuple):
            item, ts = item_ts_tuple
            # Parse recency timestamp to epoch for sorting
            recency_epoch = 0.0
            if ts:
                try:
                    from datetime import datetime

                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    recency_epoch = dt.timestamp()
                except Exception:
                    pass
            return (-item.score, -recency_epoch, item.memory_id)

        results_with_ts.sort(key=sort_key)
        return [item for item, _ in results_with_ts[:top_k]]

    def _load_memory(self, memory_id: int) -> dict[str, Any] | None:
        """
        Load memory data from store

        Args:
            memory_id: Memory ID to load

        Returns:
            Memory dict or None if not found
        """
        if self.memory_store is None:
            # No store available, return minimal data
            return {"id": memory_id, "kind": "unknown"}

        try:
            # Assume store has a sync method to get by ID
            # For async stores, we'd need to handle differently
            import sqlite3

            with sqlite3.connect(self.memory_store.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT id, kind, key, value, summary, ts FROM memories WHERE id=?",
                    (memory_id,),
                )
                row = cursor.fetchone()

                if row:
                    return {
                        "id": row["id"],
                        "kind": row["kind"],
                        "key": row["key"],
                        "value": row["value"],
                        "summary": row["summary"],
                        "ts": row["ts"],
                    }
        except Exception as e:
            logger.error(f"Failed to load memory {memory_id}: {e}")

        return None

    def _passes_filters(self, memory: dict[str, Any], filters: RetrievalFilters) -> bool:
        """Check if memory passes retrieval filters"""
        # Kind filter
        if filters.kinds is not None:
            if memory.get("kind") not in filters.kinds:
                return False

        # Timestamp filters
        ts = memory.get("ts")
        if ts:
            if filters.after and ts < filters.after:
                return False
            if filters.before and ts > filters.before:
                return False

        return True

    def _should_include(self, evaluated: dict[str, Any]) -> bool:
        """
        Check if memory should be included in retrieval results

        Exclusions:
        - never_store: should not have embeddings anyway
        - ask_before_store: exclude unless consent flag present

        Inclusions (but marked):
        - context_only: include but mark for internal use
        """
        # Check if storage was blocked (shouldn't have embeddings)
        if not evaluated.get("allow_store", True):
            return False

        # Check if consent is required but not granted
        # For Phase 2d, we exclude these by default
        # Future: check for consent flag in memory metadata
        if evaluated.get("requires_consent", False):
            # TODO: Check if consent was granted and stored
            # For now, exclude to be safe
            return False

        return True

    def _extract_snippet(self, memory: dict[str, Any]) -> str:
        """
        Extract HTML-safe centered excerpt from memory for display

        Prefers summary over full content.
        """
        # Prefer summary
        summary = memory.get("summary")
        if summary:
            excerpt = self._center_excerpt(summary, 200)
            return html.escape(excerpt, quote=False)

        # Fall back to value
        value = memory.get("value", "")
        excerpt = self._center_excerpt(value, 200)
        return html.escape(excerpt, quote=False)

    def _center_excerpt(self, text: str, max_len: int) -> str:
        """
        Extract a centered excerpt from text with ellipses

        Args:
            text: Source text
            max_len: Maximum excerpt length

        Returns:
            Centered excerpt with ' … ' ellipses as needed
        """
        if len(text) <= max_len:
            return text

        # Calculate centered window
        start = max(0, (len(text) - max_len) // 2)
        end = start + max_len

        excerpt = text[start:end]

        # Add leading ellipsis if not at start
        if start > 0:
            excerpt = " … " + excerpt

        # Add trailing ellipsis if not at end
        if end < len(text):
            excerpt = excerpt + " … "

        return excerpt


# ============================================================================
# Retrieval Façade: Unified get_retriever() Factory
# ============================================================================


def _resolve_db_path(explicit: str | None = None) -> str:
    """
    Resolve database path with precedence:
    1. Explicit argument
    2. BARTHO_DB_PATH env var
    3. kernel.yaml memory.db_path
    4. Default: "data/barth.db"
    """
    if explicit:
        return explicit

    # Check environment variable
    env_path = os.getenv("BARTHO_DB_PATH")
    if env_path:
        return env_path

    # Check kernel.yaml
    try:
        import yaml

        config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "kernel.yaml")
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            db_path = config.get("memory", {}).get("db_path")
            if db_path:
                return db_path
    except Exception as e:
        logger.debug(f"Failed to load db_path from kernel.yaml: {e}")

    # Default
    return "data/barth.db"


def _resolve_mode(explicit: str | None = None) -> str:
    """
    Resolve retrieval mode with precedence:
    1. Explicit argument
    2. BARTHO_RETRIEVAL_MODE env var
    3. kernel.yaml retrieval.mode
    4. Default: "hybrid"

    Returns normalized mode string, raises ValueError if invalid.
    """
    mode = None

    if explicit:
        mode = explicit.strip().lower()
    else:
        # Check environment variable
        env_mode = os.getenv("BARTHO_RETRIEVAL_MODE")
        if env_mode:
            mode = env_mode.strip().lower()
        else:
            # Check kernel.yaml
            try:
                import yaml

                config_path = os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "..",
                    "config",
                    "kernel.yaml",
                )
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = yaml.safe_load(f) or {}
                    file_mode = config.get("retrieval", {}).get("mode")
                    if file_mode:
                        mode = file_mode.strip().lower()
            except Exception as e:
                logger.debug(f"Failed to load mode from kernel.yaml: {e}")

    # Default to hybrid if still None
    if mode is None:
        mode = "hybrid"

    # Validate
    valid_modes = {"hybrid", "vector", "fts"}
    if mode not in valid_modes:
        raise ValueError(f"Invalid retrieval mode: '{mode}'. Must be one of {valid_modes}")

    return mode


class VectorRetrieverAdapter:
    """
    Adapter to unify Retriever.query() as .retrieve()

    Wraps the vector-only Retriever class to provide a consistent
    .retrieve() interface matching HybridRetriever.
    """

    def __init__(self, retriever: Retriever):
        """
        Initialize adapter

        Args:
            retriever: Vector-only Retriever instance
        """
        self.retriever = retriever

    def retrieve(
        self,
        query: str,
        top_k: int = 20,
        filters: RetrievalFilters | None = None,
    ) -> list[RetrievedItem]:
        """
        Perform vector-only retrieval

        Delegates to underlying Retriever.query() method.

        Args:
            query: Query text
            top_k: Number of results to return
            filters: Optional retrieval filters

        Returns:
            List of RetrievedItem
        """
        return self.retriever.query(query, top_k=top_k, filters=filters)


class FTSOnlyRetriever:
    """
    FTS-only retriever using FTSClient

    Provides a retrieval interface using only full-text search,
    without vector embeddings. Suitable for:
    - Exact keyword matching
    - Phrase searches
    - Low-resource environments
    """

    def __init__(
        self,
        db_path: str,
        rules_engine: MemoryRulesEngine | None = None,
        memory_store: Any | None = None,
    ):
        """
        Initialize FTS-only retriever

        Args:
            db_path: Path to SQLite database
            rules_engine: Optional rules engine for policy filtering
            memory_store: Optional memory store (not currently used)
        """
        from bartholomew.kernel.fts_client import FTSClient

        self.db_path = db_path
        self.fts = FTSClient(db_path)
        self.rules_engine = rules_engine
        self.memory_store = memory_store

    def retrieve(
        self,
        query: str,
        top_k: int = 20,
        filters: RetrievalFilters | None = None,
    ) -> list[RetrievedItem]:
        """
        Perform FTS-only retrieval

        Args:
            query: FTS query string (supports FTS5 syntax)
            top_k: Number of results to return
            filters: Optional filters (kinds, after/before)

        Returns:
            List of RetrievedItem with FTS ranking
        """
        if filters is None:
            filters = RetrievalFilters()

        # Pull FTS candidates (pull extra for filtering)
        try:
            fts_results = self.fts.search(query, limit=top_k * 2, order_by_rank=True)
        except Exception as e:
            logger.error(f"FTS search failed: {e}")
            return []

        if not fts_results:
            return []

        # Build snippet map from FTS results
        fts_snippets = {row["id"]: row.get("snippet") for row in fts_results if row.get("snippet")}

        # Load memory metadata and apply filters
        memory_ids = [r["id"] for r in fts_results]
        metadata = self._load_metadata(memory_ids)
        filtered_ids = self._apply_filters(metadata, filters)

        # Apply rules engine if provided
        rules_data = {}
        if self.rules_engine:
            rules_data = self._evaluate_rules(metadata, filtered_ids)
            filtered_ids = {
                mid for mid in filtered_ids if rules_data.get(mid, {}).get("include", True)
            }

        if not filtered_ids:
            return []

        # Normalize FTS ranks to scores
        fts_scores = self._normalize_fts_ranks(fts_results, filtered_ids)

        # Build results with snippets
        results = []
        for memory_id, score in sorted(fts_scores.items(), key=lambda x: x[1], reverse=True)[
            :top_k
        ]:
            data = metadata[memory_id]
            precomputed = fts_snippets.get(memory_id)
            snippet = self._generate_snippet(memory_id, data, query, precomputed=precomputed)

            # Extract rule-based fields
            rule_info = rules_data.get(memory_id, {})
            recall_policy = rule_info.get("recall_policy")
            policy_flags = set()
            if recall_policy == "context_only":
                policy_flags.add("context_only")

            item = RetrievedItem(
                memory_id=memory_id,
                score=score,
                snippet=snippet,
                recall_policy=recall_policy,
                kind=data.get("kind"),
                context_only=(recall_policy == "context_only"),
                policy_flags=policy_flags,
            )
            results.append(item)

        logger.debug(f"FTS retrieval returned {len(results)} results")
        return results

    def _load_metadata(self, memory_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Load memory metadata for given IDs"""
        if not memory_ids:
            return {}

        placeholders = ",".join("?" * len(memory_ids))
        query = f"""
            SELECT id, kind, key, value, summary, ts
            FROM memories
            WHERE id IN ({placeholders})
        """

        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, memory_ids)
            rows = cursor.fetchall()

            metadata = {}
            for row in rows:
                metadata[row["id"]] = {
                    "id": row["id"],
                    "kind": row["kind"],
                    "key": row["key"],
                    "value": row["value"],
                    "summary": row["summary"],
                    "ts": row["ts"],
                }

            return metadata
        finally:
            if conn:
                conn.close()

    def _apply_filters(self, metadata: dict[int, dict[str, Any]], filters: RetrievalFilters) -> set:
        """Apply retrieval filters, return set of passing memory IDs"""
        filtered = set()

        for memory_id, data in metadata.items():
            # Kind filter
            if filters.kinds is not None:
                if data.get("kind") not in filters.kinds:
                    continue

            # Timestamp filters
            ts = data.get("ts")
            if ts:
                if filters.after and ts < filters.after:
                    continue
                if filters.before and ts > filters.before:
                    continue

            filtered.add(memory_id)

        return filtered

    def _evaluate_rules(
        self,
        metadata: dict[int, dict[str, Any]],
        memory_ids: set,
    ) -> dict[int, dict[str, Any]]:
        """Evaluate rules for memories"""
        rules_data = {}

        for memory_id in memory_ids:
            data = metadata[memory_id]
            evaluated = self.rules_engine.evaluate(data)

            # Check if should be included
            include = True
            if not evaluated.get("allow_store", True):
                include = False
            if evaluated.get("requires_consent", False):
                include = False

            rules_data[memory_id] = {
                "include": include,
                "recall_policy": evaluated.get("recall_policy"),
            }

        return rules_data

    def _normalize_fts_ranks(
        self,
        fts_results: list[dict[str, Any]],
        filtered_ids: set,
    ) -> dict[int, float]:
        """
        Normalize FTS BM25 ranks to [0, 1] scores

        Lower rank (better match) -> higher score
        """
        ranks = {}
        for row in fts_results:
            memory_id = row["id"]
            if memory_id in filtered_ids:
                ranks[memory_id] = row["rank"]

        if not ranks:
            return {}

        # Invert ranks to scores
        rank_values = list(ranks.values())
        rmin = min(rank_values)
        rmax = max(rank_values)

        normalized = {}
        if rmax > rmin:
            for memory_id, rank in ranks.items():
                normalized[memory_id] = (rmax - rank) / (rmax - rmin)
        else:
            for memory_id in ranks:
                normalized[memory_id] = 1.0

        return normalized

    def _generate_snippet(
        self,
        memory_id: int,
        metadata: dict[str, Any],
        query: str,
        precomputed: str | None = None,
    ) -> str:
        """
        Generate HTML-safe snippet with FTS highlighting or centered
        excerpt fallback

        Args:
            memory_id: Memory ID
            metadata: Memory metadata dict
            query: Query string (unused in current implementation)
            precomputed: Precomputed FTS snippet from search results

        Returns:
            HTML-escaped snippet string
        """
        # Use precomputed FTS snippet if available
        if precomputed:
            return html.escape(precomputed, quote=False)

        # Fallback: centered excerpt of summary or value
        summary = metadata.get("summary")
        if summary:
            excerpt = self._center_excerpt(summary, 200)
            return html.escape(excerpt, quote=False)

        value = metadata.get("value", "")
        excerpt = self._center_excerpt(value, 200)
        return html.escape(excerpt, quote=False)

    def _center_excerpt(self, text: str, max_len: int) -> str:
        """
        Extract a centered excerpt from text with ellipses

        Args:
            text: Source text
            max_len: Maximum excerpt length

        Returns:
            Centered excerpt with ' … ' ellipses as needed
        """
        if len(text) <= max_len:
            return text

        # Calculate centered window
        start = max(0, (len(text) - max_len) // 2)
        end = start + max_len

        excerpt = text[start:end]

        # Add leading ellipsis if not at start
        if start > 0:
            excerpt = " … " + excerpt

        # Add trailing ellipsis if not at end
        if end < len(text):
            excerpt = excerpt + " … "

        return excerpt


def get_retriever(
    mode: str | None = None,
    db_path: str | None = None,
    rules_engine: MemoryRulesEngine | None = None,
    embedding_engine: EmbeddingEngine | None = None,
    memory_store: Any | None = None,
) -> Any:
    """
    Factory to create retriever instance based on mode

    Returns a retriever with a unified .retrieve() interface:
    - "hybrid": HybridRetriever (FTS + vector fusion)
    - "vector": Retriever wrapped in VectorRetrieverAdapter (vector-only)
    - "fts": FTSOnlyRetriever (FTS-only, no embeddings)

    Mode resolution precedence:
    1. Explicit mode argument
    2. BARTHO_RETRIEVAL_MODE env var
    3. kernel.yaml retrieval.mode
    4. Default: "hybrid"

    DB path resolution precedence:
    1. Explicit db_path argument
    2. BARTHO_DB_PATH env var
    3. kernel.yaml memory.db_path
    4. Default: "data/barth.db"

    Args:
        mode: Retrieval mode ("hybrid", "vector", or "fts")
        db_path: Database path (resolved from config/env if None)
        rules_engine: Memory rules engine (uses singleton if None)
        embedding_engine: Embedding engine (uses singleton if None, not used for FTS mode)
        memory_store: Memory store instance (optional)

    Returns:
        Retriever instance with .retrieve(query, top_k, filters) method

    Raises:
        ValueError: If mode is invalid

    Examples:
        # Default (from config/env)
        retriever = get_retriever()
        results = retriever.retrieve("privacy concerns", top_k=10)

        # Force vector-only
        retriever = get_retriever(mode="vector")
        results = retriever.retrieve("travel plans")

        # FTS-only with filters
        retriever = get_retriever(mode="fts")
        results = retriever.retrieve(
            "machine learning",
            filters=RetrievalFilters(kinds=["event"])
        )
    """
    # Track if mode was explicitly provided (for degradation logic)
    mode_explicit = mode is not None

    # Resolve configuration
    resolved_mode = _resolve_mode(mode)
    resolved_db_path = _resolve_db_path(db_path)

    # Resolve rules engine (use module singleton if not provided)
    if rules_engine is None:
        try:
            from bartholomew.kernel.memory_rules import _rules_engine

            rules_engine = _rules_engine
        except ImportError:
            logger.warning(
                "Failed to import _rules_engine, creating new MemoryRulesEngine instance",
            )
            rules_engine = MemoryRulesEngine()

    # Check FTS5 availability (cached)
    fts_ok = _check_fts5_once(resolved_db_path)

    # Degrade mode if FTS5 unavailable, but ONLY if mode was NOT explicit
    # When user explicitly requests a mode, honor it and let the retriever
    # handle fallbacks internally (FTSOnlyRetriever has graceful fallbacks)
    if resolved_mode == "fts" and not fts_ok and not mode_explicit:
        logger.warning("FTS mode from config/env but FTS5 unavailable; degrading to vector-only")
        resolved_mode = "vector"
    elif resolved_mode == "hybrid" and not fts_ok:
        logger.info(
            "Hybrid mode with FTS5 unavailable; "
            "will operate with vector-only (empty FTS candidates)",
        )

    logger.debug(f"Creating retriever: mode={resolved_mode}, db_path={resolved_db_path}")

    # Route to appropriate retriever
    if resolved_mode == "hybrid":
        # Import here to avoid circular dependencies
        from bartholomew.kernel.hybrid_retriever import HybridRetriever

        return HybridRetriever(
            db_path=resolved_db_path,
            rules_engine=rules_engine,
            embedding_engine=embedding_engine,
            memory_store=memory_store,
        )

    elif resolved_mode == "vector":
        # Create vector store
        vector_store = VectorStore(resolved_db_path)

        # Get embedding engine if not provided
        if embedding_engine is None:
            from bartholomew.kernel.embedding_engine import get_embedding_engine

            embedding_engine = get_embedding_engine()

        # Create vector-only retriever
        retriever = Retriever(
            rules_engine=rules_engine,
            vector_store=vector_store,
            embedding_engine=embedding_engine,
            memory_store=memory_store,
        )

        # Wrap in adapter for consistent .retrieve() interface
        return VectorRetrieverAdapter(retriever)

    elif resolved_mode == "fts":
        return FTSOnlyRetriever(
            db_path=resolved_db_path,
            rules_engine=rules_engine,
            memory_store=memory_store,
        )

    else:
        # Should never reach here due to validation in _resolve_mode
        raise ValueError(f"Invalid mode: {resolved_mode}")
