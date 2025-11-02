"""
Synthetic data generators for deterministic testing

All generators use fixed seeds for reproducible results.
"""

import numpy as np


def create_synthetic_embeddings(
    group_id: int,
    variant_idx: int,
    dim: int = 384,
    seed: int = 42,
) -> np.ndarray:
    """
    Create synthetic embeddings for paraphrase groups

    Each group gets a stable centroid; variants get centroid + small noise.
    This creates embeddings where semantically similar items cluster together.

    Args:
        group_id: Group identifier (same group = similar embeddings)
        variant_idx: Variant within group (adds small noise)
        dim: Embedding dimension
        seed: Base seed for reproducibility

    Returns:
        L2-normalized embedding vector as float32 numpy array
    """
    # Stable centroid per group
    np.random.seed(seed + int(group_id))
    centroid = np.random.randn(dim).astype(np.float32)

    # Add small variant noise
    np.random.seed(seed + int(group_id) * 1000 + variant_idx)
    noise = np.random.randn(dim).astype(np.float32) * 0.1

    vec = centroid + noise

    # L2 normalize
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm

    return vec


def create_uncorrelated_embeddings(item_id: int, dim: int = 384, seed: int = 1337) -> np.ndarray:
    """
    Create uncorrelated random embeddings

    Each item gets a completely random vector with no semantic clustering.
    Useful for testing lexical vs vector scenarios where vectors don't help.

    Args:
        item_id: Item identifier
        dim: Embedding dimension
        seed: Base seed for reproducibility

    Returns:
        L2-normalized embedding vector as float32 numpy array
    """
    np.random.seed(seed + int(item_id))
    vec = np.random.randn(dim).astype(np.float32)

    # L2 normalize
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm

    return vec


def make_rare_token(index: int, length: int = 8, seed: int = 1337) -> str:
    """
    Generate a deterministic rare token string

    Creates improbable character sequences that are unlikely to match
    via semantic similarity but will match exactly via FTS.

    Args:
        index: Token index for uniqueness
        length: Length of token string
        seed: Base seed for reproducibility

    Returns:
        Rare token string
    """
    rng = np.random.default_rng(seed + index)

    # Use mix of lowercase, digits, and some special chars
    # Avoid common words
    chars = "qxzjkw0123456789"
    token_chars = rng.choice(list(chars), size=length)
    token = "".join(token_chars)

    # Prefix with unique marker to ensure uniqueness
    return f"tkn{index:04d}{token}"


def seeded_rng(key: int, seed: int = 42) -> np.random.Generator:
    """
    Create a seeded random number generator

    Args:
        key: Key to mix with base seed
        seed: Base seed

    Returns:
        Numpy random generator
    """
    return np.random.default_rng(seed + key)


def create_near_equal_scores(
    num_items: int,
    base_score: float = 0.7,
    noise_std: float = 0.01,
    seed: int = 42,
) -> list[float]:
    """
    Create near-equal scores for testing tie-breaking

    Args:
        num_items: Number of scores to generate
        base_score: Base score value
        noise_std: Standard deviation of noise
        seed: Random seed

    Returns:
        List of near-equal scores
    """
    rng = np.random.default_rng(seed)
    scores = base_score + rng.normal(0, noise_std, num_items)

    # Clamp to valid range
    scores = np.clip(scores, 0.0, 1.0)

    return scores.tolist()
