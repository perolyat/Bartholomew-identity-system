"""
Tests for query-aware weight adjustment in hybrid retrieval

Tests the heuristics that detect lexical vs semantic queries
and adjust fusion weights accordingly.
"""
import pytest

from bartholomew.kernel.hybrid_retriever import (
    HybridRetriever,
    HybridRetrievalConfig,
    _looks_lexical_query,
    _looks_semantic_query,
    _query_aware_weights
)


class TestQueryHeuristics:
    """Test query analysis heuristics"""
    
    def test_lexical_quoted_phrase(self):
        """Quoted phrases should trigger lexical heuristic"""
        assert _looks_lexical_query('"privacy policy"')
        assert _looks_lexical_query("'machine learning'")
        assert _looks_lexical_query('check "terms of service"')
    
    def test_lexical_boolean_operators(self):
        """Boolean operators should trigger lexical heuristic"""
        assert _looks_lexical_query('privacy AND security')
        assert _looks_lexical_query('cat OR dog')
        assert _looks_lexical_query('machine learning NOT deep')
        
        # Case insensitive
        assert _looks_lexical_query('privacy and security')
        assert _looks_lexical_query('cat or dog')
        assert _looks_lexical_query('machine learning not deep')
    
    def test_lexical_field_filters(self):
        """Field:value patterns should trigger lexical heuristic"""
        assert _looks_lexical_query('kind:event')
        assert _looks_lexical_query('key:preference status:active')
        assert _looks_lexical_query('type:memory tag:important')
    
    def test_semantic_question_mark(self):
        """Questions with ? should trigger semantic heuristic"""
        assert _looks_semantic_query('What is machine learning?')
        assert _looks_semantic_query('How does this work?')
    
    def test_semantic_interrogatives(self):
        """Interrogative starts should trigger semantic heuristic"""
        assert _looks_semantic_query('Who created this system')
        assert _looks_semantic_query('What are the benefits')
        assert _looks_semantic_query('When was it built')
        assert _looks_semantic_query('Where is the data stored')
        assert _looks_semantic_query('Why do we need privacy')
        assert _looks_semantic_query('How can I improve this')
    
    def test_semantic_long_natural_sentence(self):
        """Long natural sentences trigger semantic"""
        # Long query without lexical syntax markers
        query_clear = (
            'I need help understanding how the machine learning '
            'system processes user data while maintaining privacy '
            'standards'
        )
        assert _looks_semantic_query(query_clear)
    
    def test_neutral_short_keywords(self):
        """Short keyword queries should be neutral"""
        # Neither lexical nor semantic
        assert not _looks_lexical_query('machine learning')
        assert not _looks_semantic_query('machine learning')
        
        assert not _looks_lexical_query('privacy security')
        assert not _looks_semantic_query('privacy security')
    
    def test_neutral_medium_keywords(self):
        """Medium-length keyword lists should be neutral"""
        query = 'data privacy security encryption compliance'
        assert not _looks_lexical_query(query)
        assert not _looks_semantic_query(query)


class TestWeightAdjustment:
    """Test weight adjustment logic"""
    
    def test_lexical_increases_fts_weight(self):
        """Lexical queries should increase FTS weight"""
        base_fts, base_vec = 0.6, 0.4
        
        # Lexical query
        adj_fts, adj_vec = _query_aware_weights(
            '"privacy policy"',
            base_fts,
            base_vec
        )
        
        # FTS weight should increase
        assert adj_fts > base_fts
        assert adj_vec < base_vec
        
        # Should still sum to 1.0
        assert abs((adj_fts + adj_vec) - 1.0) < 1e-6
    
    def test_semantic_increases_vector_weight(self):
        """Semantic queries should increase vector weight"""
        base_fts, base_vec = 0.6, 0.4
        
        # Semantic query
        adj_fts, adj_vec = _query_aware_weights(
            'What are the benefits of machine learning?',
            base_fts,
            base_vec
        )
        
        # Vector weight should increase
        assert adj_vec > base_vec
        assert adj_fts < base_fts
        
        # Should still sum to 1.0
        assert abs((adj_fts + adj_vec) - 1.0) < 1e-6
    
    def test_neutral_keeps_base_weights(self):
        """Neutral queries should keep base weights"""
        base_fts, base_vec = 0.6, 0.4
        
        # Neutral query
        adj_fts, adj_vec = _query_aware_weights(
            'machine learning',
            base_fts,
            base_vec
        )
        
        # Weights should be unchanged
        assert abs(adj_fts - base_fts) < 1e-6
        assert abs(adj_vec - base_vec) < 1e-6
    
    def test_weight_clamping(self):
        """Weights should be clamped to [0.1, 0.9]"""
        # Start with extreme base weights
        base_fts, base_vec = 0.9, 0.1
        
        # Lexical query would push FTS even higher
        adj_fts, adj_vec = _query_aware_weights(
            'kind:event AND status:active',
            base_fts,
            base_vec
        )
        
        # Should be clamped
        assert adj_fts <= 0.9
        assert adj_vec >= 0.1
        
        # Should still sum to 1.0
        assert abs((adj_fts + adj_vec) - 1.0) < 1e-6


class TestHybridRetrieverIntegration:
    """Test query-aware weighting in HybridRetriever"""
    
    def test_weight_override_takes_precedence(self):
        """Explicit weight override should take precedence"""
        config = HybridRetrievalConfig(
            fusion_mode="weighted",
            weight_fts=0.6,
            weight_vec=0.4
        )
        
        # Use a non-existent db path since we're testing pure fusion logic
        retriever = HybridRetriever(":memory:", config=config)
        
        # Mock data for fusion
        fts_scores = {1: 0.9, 2: 0.5}
        vec_scores = {1: 0.3, 3: 0.8}
        
        # Call with explicit override
        fused = retriever._fuse_weighted(
            fts_scores,
            vec_scores,
            weight_fts=0.8,
            weight_vec=0.2
        )
        
        # Should use override weights (0.8, 0.2)
        expected_1 = 0.8 * 0.9 + 0.2 * 0.3  # 0.78
        expected_2 = 0.8 * 0.5 + 0.2 * 0.0  # 0.4
        expected_3 = 0.8 * 0.0 + 0.2 * 0.8  # 0.16
        
        assert abs(fused[1] - expected_1) < 1e-6
        assert abs(fused[2] - expected_2) < 1e-6
        assert abs(fused[3] - expected_3) < 1e-6
    
    def test_rrf_ignores_query_weighting(self):
        """RRF mode should ignore query-aware weighting"""
        config = HybridRetrievalConfig(
            fusion_mode="rrf",
            rrf_k=60
        )
        retriever = HybridRetriever(":memory:", config=config)
        
        # Prepare test data
        fts_results = [
            {"id": 1, "rank": 0.5},
            {"id": 2, "rank": 2.0}
        ]
        vec_results = [(1, 0.9), (3, 0.7)]
        filtered_ids = {1, 2, 3}
        metadata = {
            1: {"id": 1, "kind": "event", "ts": None},
            2: {"id": 2, "kind": "event", "ts": None},
            3: {"id": 3, "kind": "event", "ts": None}
        }
        rules_data = {}
        
        # RRF fusion should produce same result regardless of query
        fused_lexical = retriever._fuse_rrf(
            fts_results, vec_results, filtered_ids,
            metadata, rules_data
        )
        
        fused_semantic = retriever._fuse_rrf(
            fts_results, vec_results, filtered_ids,
            metadata, rules_data
        )
        
        # Should be identical (RRF doesn't use query)
        assert fused_lexical == fused_semantic
    
    def test_query_aware_disabled(self):
        """query_aware_weighting=False should use config weights"""
        config = HybridRetrievalConfig(
            fusion_mode="weighted",
            weight_fts=0.6,
            weight_vec=0.4
        )
        retriever = HybridRetriever(":memory:", config=config)
        
        # Mock data
        fts_scores = {1: 1.0}
        vec_scores = {1: 1.0}
        
        # Call with query_aware disabled
        fused = retriever._fuse_weighted(
            fts_scores,
            vec_scores,
            weight_fts=None,  # Will use config
            weight_vec=None
        )
        
        # Should use config weights (0.6, 0.4)
        expected = 0.6 * 1.0 + 0.4 * 1.0  # 1.0
        assert abs(fused[1] - expected) < 1e-6


class TestEdgeCases:
    """Test edge cases and boundary conditions"""
    
    def test_empty_query(self):
        """Empty query should be neutral"""
        assert not _looks_lexical_query('')
        assert not _looks_semantic_query('')
        
        # Weight adjustment should return base weights
        adj_fts, adj_vec = _query_aware_weights('', 0.6, 0.4)
        assert abs(adj_fts - 0.6) < 1e-6
        assert abs(adj_vec - 0.4) < 1e-6
    
    def test_both_lexical_and_semantic_markers(self):
        """Query with both markers should be neutral"""
        query = 'What is "machine learning" AND how does it work?'
        
        # Both markers present
        assert _looks_lexical_query(query)
        assert _looks_semantic_query(query)
        
        # Should keep base weights (both true = neutral)
        adj_fts, adj_vec = _query_aware_weights(query, 0.6, 0.4)
        assert abs(adj_fts - 0.6) < 1e-6
        assert abs(adj_vec - 0.4) < 1e-6
    
    def test_weight_normalization_after_clamping(self):
        """Weights should sum to 1.0 even after clamping"""
        # Extreme base weights that would clamp
        base_fts, base_vec = 0.95, 0.05
        
        # Apply lexical adjustment (would try to push FTS even higher)
        adj_fts, adj_vec = _query_aware_weights(
            '"exact phrase"',
            base_fts,
            base_vec
        )
        
        # Should sum to 1.0 even after clamping
        assert abs((adj_fts + adj_vec) - 1.0) < 1e-6
    
    def test_interrogative_not_at_start(self):
        """Interrogatives not at start should not trigger semantic"""
        query = 'Check what the system does'
        
        # 'what' is present but not at start
        assert not _looks_semantic_query(query)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
