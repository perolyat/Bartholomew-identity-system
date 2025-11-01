"""
Retrieval Engine for Bartholomew
Implements privacy-aware vector search with rule-based filtering
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from bartholomew.kernel.embedding_engine import (
    EmbeddingEngine,
    get_embedding_engine
)
from bartholomew.kernel.vector_store import VectorStore
from bartholomew.kernel.memory_rules import MemoryRulesEngine

logger = logging.getLogger(__name__)


@dataclass
class RetrievalFilters:
    """Filters for retrieval queries"""
    kinds: Optional[List[str]] = None
    source: Optional[str] = None  # 'summary' or 'full'
    after: Optional[str] = None   # ISO timestamp
    before: Optional[str] = None  # ISO timestamp


@dataclass
class RetrievedItem:
    """Single retrieved memory item"""
    memory_id: int
    score: float
    snippet: str
    recall_policy: Optional[str] = None
    kind: Optional[str] = None
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
        embedding_engine: Optional[EmbeddingEngine] = None,
        memory_store: Optional[Any] = None
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
        self.embedding_engine = (
            embedding_engine or get_embedding_engine()
        )
        self.memory_store = memory_store
    
    def query(
        self,
        text: str,
        top_k: int = 8,
        filters: Optional[RetrievalFilters] = None
    ) -> List[RetrievedItem]:
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
                allow_mismatch=False
            )
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []
        
        if not candidates:
            return []
        
        # Load memory data and apply rule-based filtering
        results = []
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
                policy_flags=policy_flags
            )
            
            results.append(item)
        
        # Sort by final_score (after boost) descending and take top_k
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]
    
    def _load_memory(self, memory_id: int) -> Optional[Dict[str, Any]]:
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
                    "SELECT id, kind, key, value, summary, ts "
                    "FROM memories WHERE id=?",
                    (memory_id,)
                )
                row = cursor.fetchone()
                
                if row:
                    return {
                        "id": row["id"],
                        "kind": row["kind"],
                        "key": row["key"],
                        "value": row["value"],
                        "summary": row["summary"],
                        "ts": row["ts"]
                    }
        except Exception as e:
            logger.error(f"Failed to load memory {memory_id}: {e}")
        
        return None
    
    def _passes_filters(
        self,
        memory: Dict[str, Any],
        filters: RetrievalFilters
    ) -> bool:
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
    
    def _should_include(self, evaluated: Dict[str, Any]) -> bool:
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
    
    def _extract_snippet(self, memory: Dict[str, Any]) -> str:
        """
        Extract a snippet from memory for display
        
        Prefers summary over full content.
        """
        # Prefer summary
        summary = memory.get("summary")
        if summary:
            return self._truncate(summary, 200)
        
        # Fall back to value
        value = memory.get("value", "")
        return self._truncate(value, 200)
    
    def _truncate(self, text: str, max_len: int) -> str:
        """Truncate text to max length with ellipsis"""
        if len(text) <= max_len:
            return text
        
        # Try to truncate at word boundary
        truncated = text[:max_len]
        last_space = truncated.rfind(' ')
        if last_space > max_len // 2:
            truncated = truncated[:last_space]
        
        return truncated + "..."
