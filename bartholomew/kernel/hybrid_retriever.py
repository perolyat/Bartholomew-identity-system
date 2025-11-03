"""
Hybrid Retrieval Engine for Bartholomew

Combines FTS (Full-Text Search) and Vector (embedding-based) retrieval
with advanced fusion, normalization, and boosting strategies.

Features:
- Dual candidate pulls: FTS@N and Vec@M
- Per-source score normalization (min-max)
- Recency boost with configurable half-life decay
- Kind-specific boost multipliers
- Fusion modes: weighted average (default) or RRF
- Snippet generation via FTS snippet() with fallback to summary excerpt
- Optional integration with MemoryRulesEngine for policy-aware retrieval
- Query-aware weight adjustment: adapts weights based on query syntax
- Debug observability: timing and per-result feature breakdown
"""

from __future__ import annotations

import html
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from bartholomew.kernel.db_ctx import set_wal_pragmas
from bartholomew.kernel.embedding_engine import EmbeddingEngine, get_embedding_engine
from bartholomew.kernel.fts_client import FTSClient
from bartholomew.kernel.memory_rules import MemoryRulesEngine
from bartholomew.kernel.retrieval import RetrievalFilters, RetrievedItem
from bartholomew.kernel.types import Result
from bartholomew.kernel.vector_store import VectorStore


logger = logging.getLogger(__name__)


# ============================================================================
# Query Analysis Heuristics
# ============================================================================


def _looks_lexical_query(query: str) -> bool:
    """
    Check if query appears to be lexical/keyword-based

    Indicators:
    - Contains quoted phrases: "privacy policy"
    - Contains boolean operators: AND, OR, NOT
    - Contains field:value patterns: kind:event, key:foo

    Args:
        query: Query string

    Returns:
        True if query appears to be lexical/keyword-based
    """
    q = query.strip()

    # Check for quoted phrases
    if '"' in q or "'" in q:
        return True

    # Check for boolean operators (case-insensitive, token boundaries)
    bool_pattern = r"\b(AND|OR|NOT)\b"
    if re.search(bool_pattern, q, re.IGNORECASE):
        return True

    # Check for field:value patterns (common in structured queries)
    field_pattern = r"\b\w+:\w+"
    if re.search(field_pattern, q):
        return True

    return False


def _looks_semantic_query(query: str) -> bool:
    """
    Check if query appears to be semantic/natural language

    Indicators:
    - Contains question mark
    - Starts with interrogatives: who, what, when, where, why, how
    - Long natural sentence (8+ tokens with sentence structure)

    Args:
        query: Query string

    Returns:
        True if query appears to be semantic/natural language
    """
    q = query.strip()

    # Check for question mark
    if "?" in q:
        return True

    # Check for interrogatives at start
    interrogatives = ["who", "what", "when", "where", "why", "how"]
    q_lower = q.lower()
    for word in interrogatives:
        if q_lower.startswith(word + " "):
            return True

    # Check for long natural sentence
    tokens = q.split()
    if len(tokens) >= 8:
        # Long queries without lexical syntax are likely semantic
        if not _looks_lexical_query(q):
            return True

    return False


def _query_aware_weights(query: str, base_fts: float, base_vec: float) -> tuple[float, float]:
    """
    Compute query-aware weights based on heuristics

    Strategy:
    - Lexical queries: boost FTS (1.3x), reduce vector (0.8x)
    - Semantic queries: reduce FTS (0.8x), boost vector (1.3x)
    - Neutral: use base weights
    - Clamp to [0.1, 0.9] range to avoid extreme behavior

    Args:
        query: Query string
        base_fts: Base FTS weight from config
        base_vec: Base vector weight from config

    Returns:
        Tuple of (adjusted_fts_weight, adjusted_vec_weight)
    """
    is_lexical = _looks_lexical_query(query)
    is_semantic = _looks_semantic_query(query)

    # If both or neither, use base weights
    if is_lexical == is_semantic:
        return (base_fts, base_vec)

    # Apply multipliers
    if is_lexical:
        adj_fts = base_fts * 1.3
        adj_vec = base_vec * 0.8
    else:  # is_semantic
        adj_fts = base_fts * 0.8
        adj_vec = base_vec * 1.3

    # Normalize
    total = adj_fts + adj_vec
    if total > 0:
        adj_fts /= total
        adj_vec /= total
    else:
        adj_fts = 0.5
        adj_vec = 0.5

    # Clamp to reasonable bounds [0.1, 0.9]
    adj_fts = max(0.1, min(0.9, adj_fts))
    adj_vec = max(0.1, min(0.9, adj_vec))

    # Re-normalize after clamping
    total = adj_fts + adj_vec
    if total > 0:
        adj_fts /= total
        adj_vec /= total

    logger.debug(
        f"Query-aware weights: "
        f"{'lexical' if is_lexical else 'semantic'} query detected, "
        f"weights adjusted from ({base_fts:.2f}, {base_vec:.2f}) "
        f"to ({adj_fts:.2f}, {adj_vec:.2f})",
    )

    return (adj_fts, adj_vec)


@dataclass
class HybridRetrievalConfig:
    """Configuration for hybrid retrieval"""

    # Candidate set sizes (performance guardrails)
    fts_candidates: int = 200
    vec_candidates: int = 200
    default_top_k: int = 20

    # Fusion mode and weights
    fusion_mode: str = "weighted"  # "weighted" or "rrf"
    weight_fts: float = 0.6
    weight_vec: float = 0.4
    rrf_k: int = 60  # RRF denominator constant

    # Boosting parameters
    half_life_hours: float = 168.0  # 1 week default
    kind_boosts: dict[str, float] = field(default_factory=dict)

    # Recency and snippet configuration
    recency_field: str = "ts"  # Column to use for recency calculation
    snippet_tokens: int = 12
    snippet_column: str = "value"  # "value" or "summary"
    max_snippet_chars: int = 200

    # Normalization strategy
    normalization: str = "minmax"  # Currently only minmax supported

    def __post_init__(self):
        """Validate and normalize configuration"""
        if self.fusion_mode not in ("weighted", "rrf"):
            raise ValueError(f"fusion_mode must be 'weighted' or 'rrf', got {self.fusion_mode}")

        if self.normalization != "minmax":
            raise ValueError(f"normalization must be 'minmax', got {self.normalization}")

        # Normalize weights to sum to 1.0
        weight_sum = self.weight_fts + self.weight_vec
        if weight_sum > 0:
            self.weight_fts /= weight_sum
            self.weight_vec /= weight_sum
        else:
            # Default to equal weights if both are zero
            self.weight_fts = 0.5
            self.weight_vec = 0.5


class HybridRetriever:
    """
    Hybrid retrieval engine combining FTS and vector search

    Example usage:
        retriever = HybridRetriever(db_path="data/memories.db")
        results = retriever.retrieve(
            query="privacy concerns",
            top_k=10,
            filters=RetrievalFilters(kinds=["event"])
        )
    """

    def __init__(
        self,
        db_path: str,
        fts: FTSClient | None = None,
        vector_store: VectorStore | None = None,
        embedding_engine: EmbeddingEngine | None = None,
        rules_engine: MemoryRulesEngine | None = None,
        memory_store: Any | None = None,
        config: HybridRetrievalConfig | None = None,
    ):
        """
        Initialize hybrid retriever

        Args:
            db_path: Path to SQLite database
            fts: FTS client (created if not provided)
            vector_store: Vector store (created if not provided)
            embedding_engine: Embedding engine (uses singleton if not provided)
            rules_engine: Optional memory rules engine for policy filtering
            memory_store: Optional memory store for db_path reference
            config: Retrieval configuration (uses hot-reload manager if not
                    provided)
        """
        self.db_path = db_path
        self.fts = fts or FTSClient(db_path)
        self.vector_store = vector_store or VectorStore(db_path)
        self.embedding_engine = embedding_engine or get_embedding_engine()
        self.rules_engine = rules_engine
        self.memory_store = memory_store

        # Use hot-reload config manager if no explicit config provided
        if config is None:
            from bartholomew.kernel.retrieval_config import (
                get_retrieval_config_manager,
            )  # noqa: PLC0415 (lazy import to avoid circular dependency)

            manager = get_retrieval_config_manager()
            self.config = manager.get_hybrid_config()
        else:
            self.config = config

        # Debug observability (enabled via env or CLI)
        self.debug_enabled = os.getenv("BARTHO_RETRIEVAL_DEBUG") == "1"
        self.last_debug: dict[str, Any] = {}

        logger.debug(
            f"HybridRetriever initialized: fusion={self.config.fusion_mode}, "
            f"fts_cand={self.config.fts_candidates}, "
            f"vec_cand={self.config.vec_candidates}",
        )

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        filters: RetrievalFilters | None = None,
        use_rrf: bool | None = None,
        query_aware_weighting: bool = True,
        weight_override: tuple[float, float] | None = None,
        api: str = "item",
        now: datetime | None = None,
    ) -> list[RetrievedItem] | list[Result]:
        """
        Perform hybrid retrieval

        Args:
            query: Query text
            top_k: Number of final results to return (uses config default
                   if None)
            filters: Optional filters (kinds, source, after/before)
            use_rrf: Override config fusion_mode (True=rrf, False=weighted)
            query_aware_weighting: Apply query-aware weight adjustment
                for weighted fusion (default: True). Ignored if
                use_rrf=True or weight_override is provided.
            weight_override: Explicit (fts_weight, vec_weight) tuple
                to use for this call only. Takes precedence over
                query_aware_weighting.
            api: Output format - "item" (default, RetrievedItem) or
                "result" (Result with rich introspection fields)
            now: Optional reference time for pure-function semantics.
                Used for deterministic recency boost computation in tests.
                If None, uses current time (default runtime behavior).

        Returns:
            List of RetrievedItem (api="item") or List of Result
            (api="result"), sorted by fused score descending
        """
        if filters is None:
            filters = RetrievalFilters()

        # Use config default_top_k if not specified
        final_top_k = top_k if top_k is not None else self.config.default_top_k

        # Determine fusion mode
        fusion_mode = self.config.fusion_mode
        if use_rrf is not None:
            fusion_mode = "rrf" if use_rrf else "weighted"

        # Debug timing (if enabled)
        t_fts_start = time.perf_counter() if self.debug_enabled else 0

        # Step 1: Pull FTS candidates
        fts_results = self._pull_fts_candidates(query)

        t_fts_end = time.perf_counter() if self.debug_enabled else 0
        t_vec_start = time.perf_counter() if self.debug_enabled else 0

        # Build snippet map from FTS results
        fts_snippets = {row["id"]: row.get("snippet") for row in fts_results if row.get("snippet")}

        # Step 2: Pull vector candidates (includes embedding)
        vec_results = self._pull_vector_candidates(query, filters)

        t_vec_end = time.perf_counter() if self.debug_enabled else 0

        # Step 3: Gather union of memory IDs and load metadata
        memory_ids = set()
        for row in fts_results:
            memory_ids.add(row["id"])
        for memory_id, _score in vec_results:
            memory_ids.add(memory_id)

        if not memory_ids:
            return []

        memory_metadata = self._load_metadata(list(memory_ids))

        # Step 4: Apply filters
        filtered_ids = self._apply_filters(memory_metadata, filters)

        # Step 5: Apply rules engine if provided
        rules_data = {}
        if self.rules_engine:
            rules_data = self._evaluate_rules(memory_metadata, filtered_ids)
            # Remove memories blocked by rules
            filtered_ids = {
                mid for mid in filtered_ids if rules_data.get(mid, {}).get("include", True)
            }

        if not filtered_ids:
            return []

        # Step 6: Normalize scores per source
        fts_scores = self._normalize_fts_scores(fts_results, filtered_ids)
        vec_scores = self._normalize_vec_scores(vec_results, filtered_ids)

        # Step 7: Compute boosts and apply to normalized scores
        boosted_fts, boosted_vec, boost_map = self._apply_boosts(
            fts_scores,
            vec_scores,
            memory_metadata,
            rules_data,
            now,
        )

        t_fusion_start = time.perf_counter() if self.debug_enabled else 0

        # Step 8: Fuse scores
        if fusion_mode == "rrf":
            fused_scores = self._fuse_rrf(
                fts_results,
                vec_results,
                filtered_ids,
                memory_metadata,
                rules_data,
                now,
            )
        else:
            # Determine per-call weights for weighted fusion
            call_weight_fts = None
            call_weight_vec = None

            if weight_override is not None:
                # Explicit override takes precedence
                call_weight_fts, call_weight_vec = weight_override
                logger.debug(
                    f"Using explicit weight override: "
                    f"fts={call_weight_fts:.2f}, vec={call_weight_vec:.2f}",
                )
            elif query_aware_weighting:
                # Apply query-aware adjustment
                call_weight_fts, call_weight_vec = _query_aware_weights(
                    query,
                    self.config.weight_fts,
                    self.config.weight_vec,
                )
            # else: use default config weights (None will trigger fallback)

            fused_scores = self._fuse_weighted(
                boosted_fts,
                boosted_vec,
                weight_fts=call_weight_fts,
                weight_vec=call_weight_vec,
            )

        t_fusion_end = time.perf_counter() if self.debug_enabled else 0

        # Step 9: Rank with tie-breakers
        def sort_key(item):
            memory_id, score = item
            metadata = memory_metadata[memory_id]

            # Parse recency timestamp to epoch for sorting
            recency_ts = metadata.get(self.config.recency_field)
            recency_epoch = 0.0
            if recency_ts:
                try:
                    dt = datetime.fromisoformat(recency_ts.replace("Z", "+00:00"))
                    recency_epoch = dt.timestamp()
                except Exception:
                    recency_epoch = 0.0

            # Return tuple for sorting: (score desc, recency desc, id asc)
            return (-score, -recency_epoch, memory_id)

        ranked = sorted(fused_scores.items(), key=sort_key)[:final_top_k]

        # Debug: Build per-result feature breakdown (if enabled)
        if self.debug_enabled:
            self._build_debug_info(
                ranked,
                fts_scores,
                vec_scores,
                boost_map,
                fused_scores,
                fusion_mode,
                call_weight_fts,
                call_weight_vec,
                t_fts_start,
                t_fts_end,
                t_vec_start,
                t_vec_end,
                t_fusion_start,
                t_fusion_end,
            )

        # Step 10: Build result items with snippets
        results = []
        for memory_id, score in ranked:
            metadata = memory_metadata[memory_id]
            precomputed = fts_snippets.get(memory_id)
            snippet = self._generate_snippet(memory_id, metadata, query, precomputed=precomputed)

            # Extract rule-based fields
            rule_info = rules_data.get(memory_id, {})
            recall_policy = rule_info.get("recall_policy")

            if api == "result":
                # Build Result with rich introspection fields
                boosts = boost_map.get(memory_id, {})
                result_item = Result(
                    mem_id=memory_id,
                    score=score,
                    snippet=snippet,
                    bm25_norm=fts_scores.get(memory_id, 0.0),
                    vec_norm=vec_scores.get(memory_id, 0.0),
                    recency=boosts.get("recency", 1.0),
                    kind_boost=boosts.get("kind", 1.0),
                    context_only=(recall_policy == "context_only"),
                    metadata=metadata,
                )
                results.append(result_item)
            else:
                # Build RetrievedItem (default/legacy behavior)
                policy_flags = set()
                if recall_policy == "context_only":
                    policy_flags.add("context_only")

                item = RetrievedItem(
                    memory_id=memory_id,
                    score=score,
                    snippet=snippet,
                    recall_policy=recall_policy,
                    kind=metadata.get("kind"),
                    context_only=(recall_policy == "context_only"),
                    policy_flags=policy_flags,
                )
                results.append(item)

        logger.debug(
            f"Hybrid retrieval returned {len(results)} results "
            f"(fusion={fusion_mode}, api={api})",
        )
        return results

    def _pull_fts_candidates(self, query: str) -> list[dict[str, Any]]:
        """Pull FTS candidates"""
        try:
            results = self.fts.search(query, limit=self.config.fts_candidates, order_by_rank=True)
            logger.debug(f"FTS returned {len(results)} candidates")
            return results
        except Exception as e:
            logger.error(f"FTS search failed: {e}")
            return []

    def _pull_vector_candidates(
        self,
        query: str,
        filters: RetrievalFilters,
    ) -> list[tuple[int, float]]:
        """Pull vector candidates"""
        try:
            # Embed query
            qvec = self.embedding_engine.embed_texts([query])[0]

            # Search with strict model matching
            cfg = self.embedding_engine.config
            results = self.vector_store.search(
                qvec,
                top_k=self.config.vec_candidates,
                provider=cfg.provider,
                model=cfg.model,
                dim=cfg.dim,
                source=filters.source,
                allow_mismatch=False,
            )
            logger.debug(f"Vector search returned {len(results)} candidates")
            return results
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []

    def _load_metadata(self, memory_ids: list[int]) -> dict[int, dict[str, Any]]:
        """
        Load memory metadata for given IDs

        Returns:
            Dict mapping memory_id to metadata dict
        """
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
            set_wal_pragmas(conn)
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
        """
        Evaluate rules for memories

        Returns:
            Dict mapping memory_id to rule evaluation results
        """
        rules_data = {}

        for memory_id in memory_ids:
            data = metadata[memory_id]
            evaluated = self.rules_engine.evaluate(data)

            # Check if should be included (matching retrieval.Retriever logic)
            include = True
            if not evaluated.get("allow_store", True):
                include = False
            if evaluated.get("requires_consent", False):
                # Exclude unless consent granted
                # TODO: Check memory_consent table
                include = False

            rules_data[memory_id] = {
                "include": include,
                "boost": evaluated.get("retrieval", {}).get("boost", 1.0),
                "recall_policy": evaluated.get("recall_policy"),
            }

        return rules_data

    def _normalize_fts_scores(
        self,
        fts_results: list[dict[str, Any]],
        filtered_ids: set,
    ) -> dict[int, float]:
        """
        Normalize FTS scores (BM25 ranks) to [0, 1]

        FTS returns ranks where lower is better. We invert and normalize.
        """
        if not fts_results:
            return {}

        # Build rank dict for filtered IDs
        ranks = {}
        for row in fts_results:
            memory_id = row["id"]
            if memory_id in filtered_ids:
                ranks[memory_id] = row["rank"]

        if not ranks:
            return {}

        # Invert ranks to scores (lower rank = higher score)
        rank_values = list(ranks.values())
        rmin = min(rank_values)
        rmax = max(rank_values)

        normalized = {}
        if rmax > rmin:
            # Invert: higher rank (worse) -> lower score
            for memory_id, rank in ranks.items():
                normalized[memory_id] = (rmax - rank) / (rmax - rmin)
        else:
            # All ranks equal
            for memory_id in ranks:
                normalized[memory_id] = 1.0

        return normalized

    def _normalize_vec_scores(
        self,
        vec_results: list[tuple[int, float]],
        filtered_ids: set,
    ) -> dict[int, float]:
        """
        Normalize vector scores (cosine similarity) to [0, 1]

        Vector scores are already in [0, 1] but we still min-max normalize
        for consistency across result sets.
        """
        if not vec_results:
            return {}

        # Filter to relevant IDs
        scores = {}
        for memory_id, score in vec_results:
            if memory_id in filtered_ids:
                scores[memory_id] = score

        if not scores:
            return {}

        # Min-max normalization
        score_values = list(scores.values())
        smin = min(score_values)
        smax = max(score_values)

        normalized = {}
        if smax > smin:
            for memory_id, score in scores.items():
                normalized[memory_id] = (score - smin) / (smax - smin)
        else:
            # All scores equal
            for memory_id in scores:
                normalized[memory_id] = 1.0

        return normalized

    def _compute_recency_boost(self, ts: str | None, now: datetime | None = None) -> float:
        """
        Compute recency boost with exponential decay

        Formula: exp(-ln(2) * age_days / half_life_days)
        Equivalently: 2^(-(age_seconds) / (half_life_seconds))

        Future-dated items have age clamped to 0, resulting in boost = 1.0

        Args:
            ts: ISO timestamp of memory
            now: Optional reference time for pure-function semantics.
                If None, uses current time (default behavior).
        """
        if not ts or self.config.half_life_hours <= 0:
            return 1.0

        try:
            # Parse ISO timestamp
            ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            # Use provided now or current time
            now_dt = now if now is not None else datetime.now(ts_dt.tzinfo or timezone.utc)

            # Clamp age at 0 for future-dated items
            age_seconds = max(0.0, (now_dt - ts_dt).total_seconds())
            half_life_seconds = self.config.half_life_hours * 3600

            boost = 2 ** (-(age_seconds / half_life_seconds))
            return boost
        except Exception as e:
            logger.debug(f"Failed to compute recency boost: {e}")
            return 1.0

    def _apply_boosts(
        self,
        fts_scores: dict[int, float],
        vec_scores: dict[int, float],
        metadata: dict[int, dict[str, Any]],
        rules_data: dict[int, dict[str, Any]],
        now: datetime | None = None,
    ) -> tuple[dict[int, float], dict[int, float], dict[int, dict]]:
        """
        Apply recency, kind, and rule boosts to normalized scores

        Args:
            fts_scores: Normalized FTS scores
            vec_scores: Normalized vector scores
            metadata: Memory metadata
            rules_data: Rules evaluation results
            now: Optional reference time for deterministic recency boost

        Returns:
            (boosted_fts_scores, boosted_vec_scores, boost_map)
            boost_map contains per-memory_id boost breakdown for debug
        """
        # Union of all memory IDs
        all_ids = set(fts_scores.keys()) | set(vec_scores.keys())

        boosted_fts = {}
        boosted_vec = {}
        boost_map = {}

        for memory_id in all_ids:
            data = metadata[memory_id]

            # Compute boosts
            recency_boost = self._compute_recency_boost(data.get(self.config.recency_field), now)
            kind_boost = self.config.kind_boosts.get(data.get("kind", ""), 1.0)
            rule_boost = rules_data.get(memory_id, {}).get("boost", 1.0)

            # Store for debug
            boost_map[memory_id] = {"recency": recency_boost, "kind": kind_boost}

            # Combined boost
            total_boost = recency_boost * kind_boost * rule_boost

            # Apply to each source
            if memory_id in fts_scores:
                boosted_fts[memory_id] = fts_scores[memory_id] * total_boost

            if memory_id in vec_scores:
                boosted_vec[memory_id] = vec_scores[memory_id] * total_boost

        return boosted_fts, boosted_vec, boost_map

    def _fuse_weighted(
        self,
        fts_scores: dict[int, float],
        vec_scores: dict[int, float],
        weight_fts: float | None = None,
        weight_vec: float | None = None,
    ) -> dict[int, float]:
        """
        Fuse scores using weighted average

        fused = w_fts * s_fts + w_vec * s_vec

        Missing scores are treated as 0.0

        Args:
            fts_scores: FTS scores by memory_id
            vec_scores: Vector scores by memory_id
            weight_fts: Optional FTS weight override for this call
            weight_vec: Optional vector weight override for this call

        Returns:
            Fused scores by memory_id
        """
        # Use provided weights or fall back to config
        w_fts = weight_fts if weight_fts is not None else self.config.weight_fts
        w_vec = weight_vec if weight_vec is not None else self.config.weight_vec

        all_ids = set(fts_scores.keys()) | set(vec_scores.keys())

        fused = {}
        for memory_id in all_ids:
            s_fts = fts_scores.get(memory_id, 0.0)
            s_vec = vec_scores.get(memory_id, 0.0)

            fused[memory_id] = w_fts * s_fts + w_vec * s_vec

        return fused

    def _fuse_rrf(
        self,
        fts_results: list[dict[str, Any]],
        vec_results: list[tuple[int, float]],
        filtered_ids: set,
        metadata: dict[int, dict[str, Any]],
        rules_data: dict[int, dict[str, Any]],
        now: datetime | None = None,
    ) -> dict[int, float]:
        """
        Fuse scores using Reciprocal Rank Fusion (RRF) with boosts

        RRF contribution per source: (1 / (k + rank)) * boosts

        Args:
            fts_results: FTS results with ranks
            vec_results: Vector results with scores
            filtered_ids: IDs that passed filters
            metadata: Memory metadata
            rules_data: Rules evaluation results
            now: Optional reference time for deterministic recency boost
        """
        # Build rank maps (1-based)
        fts_ranks = {}
        for rank, row in enumerate(fts_results, start=1):
            memory_id = row["id"]
            if memory_id in filtered_ids:
                fts_ranks[memory_id] = rank

        vec_ranks = {}
        for rank, (memory_id, _score) in enumerate(vec_results, start=1):
            if memory_id in filtered_ids:
                vec_ranks[memory_id] = rank

        # Compute RRF scores with boosts
        all_ids = set(fts_ranks.keys()) | set(vec_ranks.keys())
        rrf_scores = {}

        for memory_id in all_ids:
            data = metadata[memory_id]

            # Compute boosts
            recency_boost = self._compute_recency_boost(data.get(self.config.recency_field), now)
            kind_boost = self.config.kind_boosts.get(data.get("kind", ""), 1.0)
            rule_boost = rules_data.get(memory_id, {}).get("boost", 1.0)
            total_boost = recency_boost * kind_boost * rule_boost

            # RRF contributions
            rrf_contrib = 0.0

            if memory_id in fts_ranks:
                rrf_contrib += 1.0 / (self.config.rrf_k + fts_ranks[memory_id])

            if memory_id in vec_ranks:
                rrf_contrib += 1.0 / (self.config.rrf_k + vec_ranks[memory_id])

            # Apply boost
            rrf_scores[memory_id] = rrf_contrib * total_boost

        return rrf_scores

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
            excerpt = self._center_excerpt(summary, self.config.max_snippet_chars)
            return html.escape(excerpt, quote=False)

        value = metadata.get("value", "")
        excerpt = self._center_excerpt(value, self.config.max_snippet_chars)
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

    def _build_debug_info(
        self,
        ranked: list[tuple[int, float]],
        fts_scores: dict[int, float],
        vec_scores: dict[int, float],
        boost_map: dict[int, dict],
        fused_scores: dict[int, float],
        fusion_mode: str,
        call_weight_fts: float | None,
        call_weight_vec: float | None,
        t_fts_start: float,
        t_fts_end: float,
        t_vec_start: float,
        t_vec_end: float,
        t_fusion_start: float,
        t_fusion_end: float,
    ) -> None:
        """Build debug information structure for observability"""
        # Compute timings in milliseconds
        fts_ms = (t_fts_end - t_fts_start) * 1000
        vec_ms = (t_vec_end - t_vec_start) * 1000
        fusion_ms = (t_fusion_end - t_fusion_start) * 1000

        # Build per-result feature breakdown
        per_result = []
        for memory_id, final_score in ranked:
            boosts = boost_map.get(memory_id, {})
            per_result.append(
                {
                    "memory_id": memory_id,
                    "bm25_norm": fts_scores.get(memory_id, 0.0),
                    "vec_norm": vec_scores.get(memory_id, 0.0),
                    "recency": boosts.get("recency", 1.0),
                    "kind_boost": boosts.get("kind", 1.0),
                    "final": final_score,
                },
            )

        # Store in last_debug
        self.last_debug = {
            "timings": {"fts_ms": fts_ms, "vec_ms": vec_ms, "fusion_ms": fusion_ms},
            "fusion_mode": fusion_mode,
            "per_result": per_result,
        }

        # Add weights if in weighted mode
        if fusion_mode == "weighted":
            w_fts = call_weight_fts if call_weight_fts is not None else self.config.weight_fts
            w_vec = call_weight_vec if call_weight_vec is not None else self.config.weight_vec
            self.last_debug["weights_used"] = {"fts": w_fts, "vec": w_vec}

        # Log timings
        logger.info(
            f"Retrieval timings: FTS={fts_ms:.1f}ms, "
            f"Vec={vec_ms:.1f}ms, Fusion={fusion_ms:.1f}ms",
        )
