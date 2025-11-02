"""
Embedding Engine for Bartholomew
Implements privacy-first, offline-first vector embeddings for memory retrieval
"""
from __future__ import annotations
import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from typing import Iterable, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingConfig:
    """Configuration for embedding generation"""
    provider: str           # 'local-sbert', 'openai', etc.
    model: str              # Model identifier
    dim: int                # Embedding dimension


class EmbeddingProvider:
    """Base class for embedding providers"""
    
    def embed(self, texts: List[str]) -> np.ndarray:
        """
        Generate embeddings for texts
        
        Args:
            texts: List of text strings to embed
            
        Returns:
            numpy array of shape (N, dim) with float32 dtype
            Vectors are L2-normalized (norm = 1.0)
        """
        raise NotImplementedError


class LocalSBERTProvider(EmbeddingProvider):
    """
    Local sentence-transformers provider with fallback
    
    Attempts to use sentence-transformers library with specified model.
    If import fails or model can't be loaded (e.g., offline, CI), falls back
    to a deterministic hashing-based embedder that produces stable normalized
    vectors for consistent behavior across environments.
    """
    
    def __init__(self, model_id: str = "BAAI/bge-small-en-v1.5", dim: int = 384):
        """
        Initialize provider
        
        Args:
            model_id: HuggingFace model identifier
            dim: Embedding dimension (used for fallback)
        """
        self.model_id = model_id
        self.dim = dim
        self.model = None
        self.fallback = False
        
        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(model_id, device="cpu")
            logger.info(
                f"Loaded sentence-transformers model: {model_id}"
            )
        except Exception as e:
            logger.warning(
                f"Failed to load sentence-transformers model {model_id}: {e}. "
                "Using deterministic fallback embedder."
            )
            self.fallback = True
    
    def embed(self, texts: List[str]) -> np.ndarray:
        """Generate embeddings (real or fallback)"""
        if not self.fallback and self.model is not None:
            return self._embed_real(texts)
        else:
            return self._embed_fallback(texts)
    
    def _embed_real(self, texts: List[str]) -> np.ndarray:
        """Use actual sentence-transformers model"""
        arr = self.model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False
        )
        return arr.astype(np.float32)
    
    def _embed_fallback(self, texts: List[str]) -> np.ndarray:
        """
        Deterministic hash-based embedder for testing/offline environments
        
        Produces normalized float32 vectors that are:
        - Deterministic (same text -> same vector)
        - Reasonably distributed (uses multiple hash seeds)
        - L2-normalized (cosine similarity works via dot product)
        """
        embeddings = []
        for text in texts:
            # Generate multiple hash values using different seeds
            vec = np.zeros(self.dim, dtype=np.float32)
            
            # Use multiple hash functions to fill the vector
            for i in range(self.dim):
                seed = f"{text}:{i}".encode('utf-8')
                hash_val = hashlib.sha256(seed).digest()
                # Convert first 4 bytes to float in [-1, 1]
                int_val = int.from_bytes(
                    hash_val[:4], byteorder='big', signed=True
                )
                vec[i] = int_val / (2**31)  # Normalize to [-1, 1]
            
            # L2 normalize
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            
            embeddings.append(vec)
        
        return np.array(embeddings, dtype=np.float32)


class OpenAIEmbeddingsProvider(EmbeddingProvider):
    """
    OpenAI embeddings provider (network-based)
    
    Only used when:
    - allow_remote: true in rules
    - OPENAI_API_KEY env var is set
    
    This is a stub for Phase 2d. Future implementation would use openai library.
    """
    
    def __init__(self, model: str = "text-embedding-ada-002", dim: int = 1536):
        self.model = model
        self.dim = dim
        self.api_key = os.getenv("OPENAI_API_KEY")
        
        if not self.api_key:
            raise ValueError(
                "OpenAI provider requires OPENAI_API_KEY "
                "environment variable"
            )
        
        logger.info(
            f"Initialized OpenAI embeddings provider: {model}"
        )
    
    def embed(self, texts: List[str]) -> np.ndarray:
        """
        Generate embeddings via OpenAI API
        
        Stub implementation for Phase 2d.
        """
        raise NotImplementedError(
            "OpenAI embeddings provider not yet implemented. "
            "Use local-sbert provider for offline-first operation."
        )


class EmbeddingEngine:
    """
    Orchestrates embedding generation with provider management
    
    Supports multiple providers with offline-first defaults.
    """
    
    # Provider registry
    PROVIDERS = {
        "local-sbert": LocalSBERTProvider,
        "openai": OpenAIEmbeddingsProvider,
    }
    
    def __init__(self, cfg: Optional[EmbeddingConfig] = None) -> None:
        """
        Initialize embedding engine
        
        Args:
            cfg: Embedding configuration. If None, uses safe defaults.
        """
        if cfg is None:
            cfg = EmbeddingConfig(
                provider="local-sbert",
                model="BAAI/bge-small-en-v1.5",
                dim=384
            )
        
        self.config = cfg
        self.provider = self._create_provider(cfg)
    
    def _create_provider(self, cfg: EmbeddingConfig) -> EmbeddingProvider:
        """Create provider instance from config"""
        provider_class = self.PROVIDERS.get(cfg.provider)
        
        if provider_class is None:
            raise ValueError(
                f"Unknown provider: {cfg.provider}. "
                f"Available: {list(self.PROVIDERS.keys())}"
            )
        
        if cfg.provider == "local-sbert":
            return provider_class(model_id=cfg.model, dim=cfg.dim)
        elif cfg.provider == "openai":
            return provider_class(model=cfg.model, dim=cfg.dim)
        else:
            return provider_class()
    
    def embed_texts(self, texts: Iterable[str]) -> np.ndarray:
        """
        Generate embeddings for texts
        
        Args:
            texts: Iterable of text strings
            
        Returns:
            numpy array of shape (N, dim) with float32 dtype
            Vectors are L2-normalized (norm ≈ 1.0)
        """
        texts_list = list(texts)
        
        if not texts_list:
            # Return empty array with correct shape
            return np.zeros((0, self.config.dim), dtype=np.float32)
        
        embeddings = self.provider.embed(texts_list)
        
        # Verify shape and dtype
        expected_shape = (len(texts_list), self.config.dim)
        if embeddings.shape != expected_shape:
            raise ValueError(
                f"Provider returned wrong shape: {embeddings.shape}, "
                f"expected {expected_shape}"
            )
        
        if embeddings.dtype != np.float32:
            embeddings = embeddings.astype(np.float32)
        
        return embeddings


# Optional metrics (gracefully fallback if prometheus_client unavailable)
try:
    from prometheus_client import Counter, Gauge, Histogram
    _metrics_available = True
except ImportError:
    _metrics_available = False
    # No-op fallbacks
    class Counter:
        def __init__(self, *args, **kwargs): pass
        def inc(self, *args, **kwargs): pass
    
    class Gauge:
        def __init__(self, *args, **kwargs): pass
        def set(self, *args, **kwargs): pass
    
    class Histogram:
        def __init__(self, *args, **kwargs): pass
        def observe(self, *args, **kwargs): pass


# Metrics (gated by BARTHO_METRICS=1)
_metrics_enabled = os.getenv("BARTHO_METRICS") == "1"

# Define embeddings_total once to avoid double registration across reloads
if 'embeddings_total' not in globals():
    if _metrics_enabled and _metrics_available:
        embeddings_total = Counter(
            'bartholomew_embeddings_total',
            'Total number of embeddings generated'
        )
    else:
        embeddings_total = Counter('noop', 'noop')


class EmbeddingEngineFactory:
    """
    Factory for atomic hot-reload of embedding engine
    
    Manages the current engine instance with thread-safe atomic swaps
    when configuration changes (e.g., embeddings.yaml reload).
    """
    
    def __init__(self):
        self._engine: Optional[EmbeddingEngine] = None
        self._lock = threading.RLock()
        self._config_path: Optional[str] = None
        self._last_mtime: Optional[float] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._stop_watching = threading.Event()
        self._banner_shown = False
        
        # Find config path
        for path in [
            os.path.join("bartholomew", "config", "embeddings.yaml"),
            os.path.join("config", "embeddings.yaml"),
        ]:
            if os.path.exists(path):
                self._config_path = path
                self._last_mtime = os.path.getmtime(path)
                break
    
    def get(self) -> EmbeddingEngine:
        """
        Get the current embedding engine, creating on first use
        
        Thread-safe: multiple callers always get a consistent engine
        """
        with self._lock:
            if self._engine is None:
                # First build: load from config and show banner
                cfg = self._load_config()
                self._engine = EmbeddingEngine(cfg)
                self._show_banner_once()
            return self._engine
    
    def rebuild(self, cfg: EmbeddingConfig) -> None:
        """
        Atomically swap to a new engine with given config
        
        Thread-safe: readers never see half-initialized engine
        """
        new_engine = EmbeddingEngine(cfg)
        
        with self._lock:
            self._engine = new_engine
        
        logger.info(
            f"Rebuilt embedding engine: "
            f"provider={cfg.provider} model={cfg.model} dim={cfg.dim}"
        )
    
    def reload_from_file(self) -> None:
        """
        Reload config from embeddings.yaml and rebuild engine
        
        Called by file watcher or manually
        """
        cfg = self._load_config()
        self.rebuild(cfg)
        
        # Update mtime
        if self._config_path and os.path.exists(self._config_path):
            self._last_mtime = os.path.getmtime(self._config_path)
    
    def _load_config(self) -> EmbeddingConfig:
        """Load configuration from embeddings.yaml or use defaults"""
        # Defaults
        provider = "local-sbert"
        model = "BAAI/bge-small-en-v1.5"
        dim = 384
        
        if not self._config_path:
            return EmbeddingConfig(provider=provider, model=model, dim=dim)
        
        try:
            import yaml
            with open(self._config_path, 'r') as f:
                data = yaml.safe_load(f) or {}
            
            emb = data.get("embeddings", {})
            provider = emb.get("default_provider", provider)
            model = emb.get("default_model", model)
            dim = emb.get("default_dim", dim)
        except Exception as e:
            logger.warning(f"Failed to load embeddings.yaml: {e}, using defaults")
        
        return EmbeddingConfig(provider=provider, model=model, dim=dim)
    
    def _show_banner_once(self) -> None:
        """Show startup banner exactly once when env gate is ON"""
        if self._banner_shown:
            return
        
        if os.getenv("BARTHO_EMBED_ENABLED") != "1":
            return
        
        self._banner_shown = True
        
        # Determine VSS status (check if vss0 can load)
        vss_status = "off"
        try:
            import sqlite3
            conn = sqlite3.connect(":memory:")
            conn.enable_load_extension(True)
            conn.load_extension("vss0")
            vss_status = "on"
            conn.close()
        except Exception:
            pass
        
        # Determine fallback status
        fallback = "unknown"
        if self._engine and hasattr(self._engine.provider, 'fallback'):
            fallback = str(self._engine.provider.fallback).lower()
        
        cfg = self._engine.config if self._engine else self._load_config()
        
        logger.info(
            f"Embeddings enabled: provider={cfg.provider} "
            f"model={cfg.model} dim={cfg.dim} "
            f"vss={vss_status} fallback={fallback}"
        )
    
    def start_watcher(self) -> None:
        """Start background file watcher for hot-reload"""
        # Check if watcher is disabled via env var
        import os
        if os.getenv("BARTHO_EMBED_RELOAD") in ("0", "false", "False"):
            logger.debug("Embeddings watcher disabled via BARTHO_EMBED_RELOAD=0")
            return
        
        if self._watch_thread is not None:
            return  # Already watching
        
        def watch_loop():
            while not self._stop_watching.is_set():
                try:
                    if self._config_path and os.path.exists(self._config_path):
                        current_mtime = os.path.getmtime(self._config_path)
                        if self._last_mtime is None or current_mtime != self._last_mtime:
                            logger.info("Detected embeddings.yaml change, reloading...")
                            self.reload_from_file()
                except Exception as e:
                    logger.error(f"Error in embedding config watch loop: {e}")
                
                # Sleep 10s or until stop signal
                self._stop_watching.wait(10)
        
        self._watch_thread = threading.Thread(target=watch_loop, daemon=True)
        self._watch_thread.start()
        logger.debug("Started background watcher for embeddings.yaml")
    
    def stop_watcher(self) -> None:
        """Stop background watcher"""
        if self._watch_thread:
            self._stop_watching.set()
            self._watch_thread.join(timeout=1)
            self._watch_thread = None
            logger.debug("Stopped embeddings.yaml watcher")


# Module-level factory singleton
_embedding_factory = EmbeddingEngineFactory()


def get_embedding_engine() -> EmbeddingEngine:
    """
    Get or create the global embedding engine singleton
    
    Thread-safe: uses factory for atomic hot-reload support
    Uses default configuration (local-sbert, BAAI/bge-small-en-v1.5, dim=384)
    """
    return _embedding_factory.get()
