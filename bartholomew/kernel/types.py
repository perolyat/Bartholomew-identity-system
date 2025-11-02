from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Type definitions for kernel system


@dataclass
class Result:
    """
    Ergonomic result type for hybrid retrieval with rich introspection

    Exposes normalized scores, boosts, and metadata for analysis and debugging.
    Used when calling retriever.retrieve(..., api="result").

    Fields:
        mem_id: Memory ID
        score: Final fused score (after normalization, boosting, and fusion)
        snippet: HTML-escaped snippet for display
        bm25_norm: Normalized BM25 score from FTS
            (0.0 if not in FTS results)
        vec_norm: Normalized vector similarity score
            (0.0 if not in vector results)
        recency: Recency boost multiplier (1.0 = no decay)
        kind_boost: Kind-specific boost multiplier from config
        context_only: True if recall_policy is "context_only"
        metadata: Full memory metadata dict (id, kind, key, value, summary, ts)
    """

    mem_id: int
    score: float
    snippet: str
    bm25_norm: float
    vec_norm: float
    recency: float
    kind_boost: float
    context_only: bool
    metadata: dict[str, Any] = field(default_factory=dict)
