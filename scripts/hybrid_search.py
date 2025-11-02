"""
CLI demo for hybrid search with explainable score breakdown

Usage:
    python -m scripts.hybrid_search --query "privacy protection" --k 10
    python -m scripts.hybrid_search --query "robot learning" --explain
    python -m scripts.hybrid_search --query "encryption" --mode vector
    python -m scripts.hybrid_search --query "search" --rrf --rrf-k 30
"""

import argparse
import os
import sys


# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bartholomew.kernel.hybrid_retriever import HybridRetriever  # noqa: E402
from bartholomew.kernel.retrieval import get_retriever  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Hybrid search demo with explainable scores")
    parser.add_argument("--query", "-q", required=True, help="Search query text")
    parser.add_argument("--k", type=int, default=10, help="Number of results (default: 10)")
    parser.add_argument(
        "--mode",
        choices=["hybrid", "vector", "fts"],
        default=None,
        help="Retrieval mode (default: from config/env, typically hybrid)",
    )
    parser.add_argument("--rrf", action="store_true", help="Use RRF fusion mode (only for hybrid)")
    parser.add_argument("--rrf-k", type=int, default=60, help="RRF k parameter (default: 60)")
    parser.add_argument("--db", help="Database path (default: from config/env)")
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Show detailed score breakdown (hybrid mode only)",
    )
    parser.add_argument(
        "--show-features",
        action="store_true",
        help="Show timings and per-result feature breakdown (hybrid only)",
    )

    args = parser.parse_args()

    # Get retriever
    try:
        retriever = get_retriever(mode=args.mode, db_path=args.db)
    except Exception as e:
        print(f"Error creating retriever: {e}", file=sys.stderr)
        return 1

    # Enable debug mode if --show-features requested (HybridRetriever only)
    if args.show_features and isinstance(retriever, HybridRetriever):
        retriever.debug_enabled = True

    # Override RRF mode if requested
    use_rrf = args.rrf if args.mode != "fts" else None

    # Perform search
    try:
        results = retriever.retrieve(args.query, top_k=args.k, use_rrf=use_rrf)
    except Exception as e:
        print(f"Error during retrieval: {e}", file=sys.stderr)
        return 1

    # Display results
    mode_name = args.mode or "hybrid"
    print(f"\nSearch Results ({mode_name} mode)")
    print(f"Query: '{args.query}'")
    print(f"Found {len(results)} results\n")
    print("=" * 80)

    if not results:
        print("No results found.")
        return 0

    # Show detailed breakdown if explain mode and hybrid
    if args.explain and isinstance(retriever, HybridRetriever):
        print("\nDetailed Score Breakdown (Hybrid Mode)")
        print(f"Fusion: {retriever.config.fusion_mode}")
        print(
            f"Weights: FTS={retriever.config.weight_fts:.2f}, "
            f"Vec={retriever.config.weight_vec:.2f}",
        )
        print(f"Recency half-life: {retriever.config.half_life_hours}h")
        if retriever.config.kind_boosts:
            print(f"Kind boosts: {retriever.config.kind_boosts}")
        print("=" * 80)

    for i, result in enumerate(results, 1):
        print(f"\n[{i}] Memory ID: {result.memory_id}")
        print(f"Score: {result.score:.4f}")

        if result.kind:
            print(f"Kind: {result.kind}")

        if result.recall_policy:
            print(f"Recall Policy: {result.recall_policy}")

        if result.context_only:
            print("⚠️  Context-only (not directly retrievable)")

        print(f"Snippet: {result.snippet}")

        # For explain mode with hybrid, show component scores
        # Note: This would require modifying HybridRetriever to expose
        # component scores, which is beyond the current implementation
        # For now, just show the final score

        if i < len(results):
            print("-" * 80)

    print("\n" + "=" * 80)
    print(f"Total results: {len(results)}")

    # Show debug features if requested and available
    if args.show_features:
        if isinstance(retriever, HybridRetriever) and retriever.last_debug:
            print("\n" + "=" * 80)
            print("DEBUG OBSERVABILITY")
            print("=" * 80)

            # Timings
            timings = retriever.last_debug.get("timings", {})
            print("\nTimings:")
            print(f"  FTS:    {timings.get('fts_ms', 0):.1f} ms")
            print(f"  Vec:    {timings.get('vec_ms', 0):.1f} ms")
            print(f"  Fusion: {timings.get('fusion_ms', 0):.1f} ms")

            # Fusion info
            fusion_mode = retriever.last_debug.get("fusion_mode", "unknown")
            print(f"\nFusion Mode: {fusion_mode}")

            if fusion_mode == "weighted":
                weights = retriever.last_debug.get("weights_used", {})
                print(
                    f"Weights: FTS={weights.get('fts', 0):.3f}, Vec={weights.get('vec', 0):.3f}",
                )

            # Per-result features
            per_result = retriever.last_debug.get("per_result", [])
            if per_result:
                print(f"\nPer-Result Features (top-{len(per_result)}):")
                print("Rank   ID       BM25     Vec      Recency  Kind     Final   ")
                print("-" * 62)
                for i, feat in enumerate(per_result, 1):
                    print(
                        f"{i:<6} "
                        f"{feat['memory_id']:<8} "
                        f"{feat['bm25_norm']:<8.4f} "
                        f"{feat['vec_norm']:<8.4f} "
                        f"{feat['recency']:<8.4f} "
                        f"{feat['kind_boost']:<8.4f} "
                        f"{feat['final']:<8.4f}",
                    )
        elif not isinstance(retriever, HybridRetriever):
            print("\nNote: --show-features only supported in hybrid mode")

    return 0


if __name__ == "__main__":
    sys.exit(main())
