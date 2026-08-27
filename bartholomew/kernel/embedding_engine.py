"""
Embedding Engine for Bartholomew
Implements privacy-first, offline-first vector embeddings for memory retrieval

Retrieval-mode truthfulness (OP-W003)
-------------------------------------
Every embedding this module produces carries a truthful *kind*, and the engine
can always say which of four states it is in (`EmbeddingMode`):

  ``real``          a genuine semantic model is loaded and serving;
  ``disabled``      embeddings are intentionally off (``BARTHO_EMBED_ENABLED``);
  ``unavailable``   a model was configured and could not be loaded -- fail
                    closed, no vectors are produced;
  ``dev_fallback``  the deterministic hash embedder is serving, and only
                    because it was **explicitly** enabled for development or
                    test via ``BARTHO_EMBED_ALLOW_FALLBACK=1``.

The deterministic embedder is not a semantic embedder and must never be
reported, stored, or searched as though it were. It produces stable normalized
vectors so tests and offline development have deterministic behaviour -- it
carries no meaning, and its similarity scores were measured *anti-correlated*
with relevance (see `competency_reasoning.DEFAULT_MIN_SHARED_TERMS`). It is
therefore written to storage under its own provider/model identity and its own
`embedder_kind`, so that `VectorStore` can keep the two populations strictly
apart rather than silently blending them.

**The fallback is never automatic.** Before this was the case, an absent
`sentence-transformers` install silently produced hash vectors labelled as
`local-sbert` / `BAAI/bge-small-en-v1.5`, which is precisely the condition
OP-W003 records: retrieval mode was not known and not truthfully reported.
Loading failure now raises `EmbedderUnavailableError` unless the fallback is
explicitly permitted.

**Model assets are never downloaded as a side effect of retrieval.** Ordinary
operation loads the model from a provisioned local path (or an existing local
cache) with the hub forced offline. Fetching model assets is a deliberate
bootstrap step -- `bartholomew embeddings provision` -- never something an
ordinary query can trigger.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

import numpy as np

logger = logging.getLogger(__name__)


#: Storage identity of the deterministic hash embedder. Deliberately shares no
#: provider or model string with any real model, so a hash vector can never
#: satisfy a semantic engine's provider/model filter even by accident.
FALLBACK_PROVIDER = "deterministic-hash"
FALLBACK_MODEL = "sha256-v1"

#: `memory_embeddings.embedder_kind` values. `UNVERIFIED` is not written by any
#: current code path: it is the migration default for rows created before the
#: kind was recorded, whose true embedder is unknowable from the row alone.
#: Unverified rows are excluded from every retrieval population until
#: `bartholomew embeddings rebuild` regenerates them from authoritative source
#: text -- excluded, never deleted.
KIND_SEMANTIC = "semantic"
KIND_DETERMINISTIC = "deterministic-hash"
KIND_UNVERIFIED = "unverified"


class EmbeddingMode(str, Enum):
    """The four states the embedding layer can truthfully be in."""

    REAL = "real"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    DEV_FALLBACK = "dev_fallback"


@dataclass(frozen=True)
class EmbeddingStatus:
    """A truthful description of what the embedding layer is actually doing.

    `semantic` is the load-bearing field: it is True only when a real model is
    serving. Callers deciding whether vector similarity means anything must
    read `semantic`, never `mode != DISABLED` and never the configured model
    name -- the configured name is what was *asked for*, which is exactly the
    thing that was previously mistaken for what was *running*.
    """

    mode: EmbeddingMode
    provider: str
    model: str
    dim: int
    #: True only for `REAL`: vector similarity carries semantic meaning.
    semantic: bool
    #: True when retrieval is running in a state weaker than intended.
    degraded: bool
    #: Human-readable explanation, always present for non-REAL modes.
    reason: str | None = None

    def as_dict(self) -> dict:
        """Serializable form for health/readiness surfaces and CLI output."""
        return {
            "mode": self.mode.value,
            "provider": self.provider,
            "model": self.model,
            "dim": self.dim,
            "semantic": self.semantic,
            "degraded": self.degraded,
            "reason": self.reason,
        }


class EmbedderUnavailableError(RuntimeError):
    """The configured embedder could not be loaded and fallback is not allowed.

    Raised rather than degraded-to-hash so that an unavailable model fails
    closed and visibly, instead of quietly producing meaningless vectors.
    """


def fallback_explicitly_allowed() -> bool:
    """Whether the deterministic fallback has been deliberately enabled.

    Development and test only. Read at call time, not import time, so tests
    can set it per-case with `monkeypatch.setenv`.
    """
    return os.getenv("BARTHO_EMBED_ALLOW_FALLBACK", "").strip().lower() in ("1", "true", "yes")


def embeddings_enabled() -> bool:
    """Whether embeddings are switched on at all (the pre-existing gate)."""
    return bool(os.getenv("BARTHO_EMBED_ENABLED"))


#: Environment variables that force the HuggingFace hub / transformers stack to
#: refuse network access. Set around model loading in ordinary operation so a
#: missing local model fails fast and loudly instead of silently downloading
#: several hundred megabytes on the first query.
_OFFLINE_ENV = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")


@contextlib.contextmanager
def _hub_offline(offline: bool):
    """Temporarily force (or leave alone) the model hub's offline flags.

    Restores the previous values on exit, including absence, so this never
    leaks a global setting into the rest of the process -- which matters when
    one runtime provisions a model while another is serving queries.
    """
    if not offline:
        yield
        return

    previous = {name: os.environ.get(name) for name in _OFFLINE_ENV}
    for name in _OFFLINE_ENV:
        os.environ[name] = "1"
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@dataclass
class EmbeddingConfig:
    """Configuration for embedding generation"""

    provider: str  # 'local-sbert', 'openai', etc.
    model: str  # Model identifier
    dim: int  # Embedding dimension
    #: Provisioned local directory holding the model assets. When set and
    #: present, this is what is loaded -- the reproducible deployment path.
    model_path: str | None = None
    #: Whether loading may reach the network for model assets. False in
    #: ordinary operation; only the explicit provisioning command sets it.
    allow_download: bool = False


class EmbeddingProvider:
    """Base class for embedding providers"""

    def embed(self, texts: list[str]) -> np.ndarray:
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
    Local sentence-transformers provider.

    Loads the configured model from a provisioned local path (or an existing
    local cache) with the model hub forced offline, so ordinary retrieval can
    never trigger a model download.

    **Failure is explicit.** If the library is missing or the model cannot be
    loaded, this raises `EmbedderUnavailableError` -- unless
    `BARTHO_EMBED_ALLOW_FALLBACK=1` deliberately permits the deterministic
    development embedder, in which case the provider serves that embedder and
    says so through `status()`. There is no path by which a load failure
    quietly becomes hash vectors wearing the real model's name.
    """

    def __init__(
        self,
        model_id: str = "BAAI/bge-small-en-v1.5",
        dim: int = 384,
        model_path: str | None = None,
        allow_download: bool = False,
    ):
        """
        Initialize provider

        Args:
            model_id: HuggingFace model identifier
            dim: Embedding dimension
            model_path: Provisioned local directory holding the model assets.
                Preferred over `model_id` when present on disk.
            allow_download: Whether loading may reach the network. False in
                ordinary operation.

        Raises:
            EmbedderUnavailableError: the model could not be loaded and the
                deterministic fallback was not explicitly allowed.
        """
        self.model_id = model_id
        self.dim = dim
        self.model_path = model_path
        self.allow_download = allow_download
        self.model = None
        self.fallback = False
        self.unavailable_reason: str | None = None

        try:
            self.model = self._load_model()
            logger.info(
                "Loaded sentence-transformers model: %s (source=%s)",
                model_id,
                self._load_source(),
            )
        except Exception as e:
            reason = self._describe_failure(e)
            if not fallback_explicitly_allowed():
                # Fail closed. Silently degrading here is exactly the OP-W003
                # condition: retrieval running on an embedder nobody chose,
                # reported as the one they did.
                raise EmbedderUnavailableError(reason) from e

            self.fallback = True
            self.unavailable_reason = reason
            logger.warning(
                "%s BARTHO_EMBED_ALLOW_FALLBACK is set, so the deterministic "
                "development embedder is serving instead. This is NOT semantic "
                "retrieval and is stored and searched separately.",
                reason,
            )

    def _load_source(self) -> str:
        """Where the model was loaded from, for logging and status."""
        if self.model_path and os.path.isdir(self.model_path):
            return self.model_path
        return f"local cache ({self.model_id})"

    def _load_model(self):
        """Load the model, offline unless downloading is explicitly allowed."""
        # Guarded here rather than at module scope: sentence-transformers is an
        # optional extra, and `tests/smoke/test_packaging_contract.py` requires
        # every optional import to be lexically wrapped. Re-raised unchanged so
        # `__init__` can tell "library absent" from "model failed to load" and
        # report the right remedy.
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise

        target = self.model_id
        if self.model_path:
            if os.path.isdir(self.model_path):
                target = self.model_path
            elif not self.allow_download:
                raise FileNotFoundError(
                    f"configured model_path does not exist: {self.model_path}",
                )

        with _hub_offline(not self.allow_download):
            return SentenceTransformer(target, device="cpu")

    def _describe_failure(self, exc: Exception) -> str:
        """A reason string that names the actual remedy, not just the error."""
        if isinstance(exc, ImportError):
            return (
                "sentence-transformers is not installed, so the configured "
                f"embedder {self.model_id!r} cannot be loaded. Install the "
                "embeddings extra (`pip install -e '.[embeddings]'`) and "
                "provision the model with `bartholomew embeddings provision`."
            )
        return (
            f"Failed to load embedding model {self.model_id!r} "
            f"(model_path={self.model_path!r}, downloads "
            f"{'allowed' if self.allow_download else 'disabled'}): {exc}. "
            "Provision the model locally with "
            "`bartholomew embeddings provision` before enabling embeddings."
        )

    def status(self, cfg_provider: str, cfg_model: str) -> EmbeddingStatus:
        """Truthful description of what this provider is actually serving."""
        if self.fallback:
            return EmbeddingStatus(
                mode=EmbeddingMode.DEV_FALLBACK,
                provider=FALLBACK_PROVIDER,
                model=FALLBACK_MODEL,
                dim=self.dim,
                semantic=False,
                degraded=True,
                reason=self.unavailable_reason,
            )
        return EmbeddingStatus(
            mode=EmbeddingMode.REAL,
            provider=cfg_provider,
            model=cfg_model,
            dim=self.dim,
            semantic=True,
            degraded=False,
            reason=None,
        )

    def embed(self, texts: list[str]) -> np.ndarray:
        """Generate embeddings (real model, or the explicit dev fallback)"""
        if not self.fallback and self.model is not None:
            return self._embed_real(texts)
        return self._embed_fallback(texts)

    def _embed_real(self, texts: list[str]) -> np.ndarray:
        """Use actual sentence-transformers model"""
        arr = self.model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return arr.astype(np.float32)

    def _embed_fallback(self, texts: list[str]) -> np.ndarray:
        """
        Deterministic hash-based embedder for testing/development.

        Produces normalized float32 vectors that are:
        - Deterministic (same text -> same vector)
        - L2-normalized (cosine similarity works via dot product)

        **These vectors carry no semantic meaning.** Similarity between two of
        them reflects SHA-256 avalanche, not relatedness -- measured
        anti-correlated with relevance during the S5.3 characterisation. They
        exist so that offline development and CI have stable, cheap, dependency
        free behaviour, and they are stored under `FALLBACK_PROVIDER` /
        `FALLBACK_MODEL` with `embedder_kind = deterministic-hash` so nothing
        downstream can mistake them for real retrieval.
        """
        embeddings = []
        for text in texts:
            # Generate multiple hash values using different seeds
            vec = np.zeros(self.dim, dtype=np.float32)

            # Use multiple hash functions to fill the vector
            for i in range(self.dim):
                seed = f"{text}:{i}".encode()
                hash_val = hashlib.sha256(seed).digest()
                # Convert first 4 bytes to float in [-1, 1]
                int_val = int.from_bytes(hash_val[:4], byteorder="big", signed=True)
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
            raise ValueError("OpenAI provider requires OPENAI_API_KEY environment variable")

        logger.info(f"Initialized OpenAI embeddings provider: {model}")

    def embed(self, texts: list[str]) -> np.ndarray:
        """
        Generate embeddings via OpenAI API

        Stub implementation for Phase 2d.
        """
        raise NotImplementedError(
            "OpenAI embeddings provider not yet implemented. "
            "Use local-sbert provider for offline-first operation.",
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

    def __init__(self, cfg: EmbeddingConfig | None = None) -> None:
        """
        Initialize embedding engine

        Args:
            cfg: Embedding configuration. If None, uses safe defaults.
        """
        if cfg is None:
            cfg = EmbeddingConfig(provider="local-sbert", model="BAAI/bge-small-en-v1.5", dim=384)

        self.config = cfg
        self.provider = self._create_provider(cfg)

    def _create_provider(self, cfg: EmbeddingConfig) -> EmbeddingProvider:
        """Create provider instance from config"""
        provider_class = self.PROVIDERS.get(cfg.provider)

        if provider_class is None:
            raise ValueError(
                f"Unknown provider: {cfg.provider}. Available: {list(self.PROVIDERS.keys())}",
            )

        if cfg.provider == "local-sbert":
            return provider_class(
                model_id=cfg.model,
                dim=cfg.dim,
                model_path=cfg.model_path,
                allow_download=cfg.allow_download,
            )
        elif cfg.provider == "openai":
            return provider_class(model=cfg.model, dim=cfg.dim)
        else:
            return provider_class()

    def status(self) -> EmbeddingStatus:
        """What this engine is actually doing right now.

        Delegates to the provider when the provider can answer, because only
        the provider knows whether the model it was asked for is the model it
        loaded. A provider that cannot answer is reported as REAL only if it
        is not the local-sbert family -- there is no "probably fine" state.
        """
        provider_status = getattr(self.provider, "status", None)
        if callable(provider_status):
            return provider_status(self.config.provider, self.config.model)

        return EmbeddingStatus(
            mode=EmbeddingMode.REAL,
            provider=self.config.provider,
            model=self.config.model,
            dim=self.config.dim,
            semantic=True,
            degraded=False,
            reason=None,
        )

    @property
    def storage_identity(self) -> tuple[str, str, str]:
        """`(provider, model, embedder_kind)` to record with every vector.

        This is the *effective* identity -- what actually produced the vector
        -- not the configured one. Writing `cfg.provider` / `cfg.model` here
        regardless of what served the request is the specific defect that made
        hash vectors indistinguishable from semantic ones in storage.
        """
        status = self.status()
        kind = KIND_SEMANTIC if status.semantic else KIND_DETERMINISTIC
        return status.provider, status.model, kind

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
                f"Provider returned wrong shape: {embeddings.shape}, expected {expected_shape}",
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
        def __init__(self, *args, **kwargs):
            pass

        def inc(self, *args, **kwargs):
            pass

    class Gauge:
        def __init__(self, *args, **kwargs):
            pass

        def set(self, *args, **kwargs):
            pass

    class Histogram:
        def __init__(self, *args, **kwargs):
            pass

        def observe(self, *args, **kwargs):
            pass


# Metrics (gated by BARTHO_METRICS=1)
_metrics_enabled = os.getenv("BARTHO_METRICS") == "1"

# Define embeddings_total once to avoid double registration across reloads
if "embeddings_total" not in globals():
    if _metrics_enabled and _metrics_available:
        embeddings_total = Counter(
            "bartholomew_embeddings_total",
            "Total number of embeddings generated",
        )
    else:
        embeddings_total = Counter("noop", "noop")


class EmbeddingEngineFactory:
    """
    Factory for atomic hot-reload of embedding engine

    Manages the current engine instance with thread-safe atomic swaps
    when configuration changes (e.g., embeddings.yaml reload).
    """

    def __init__(self):
        self._engine: EmbeddingEngine | None = None
        self._lock = threading.RLock()
        self._config_path: str | None = None
        self._last_mtime: float | None = None
        self._watch_thread: threading.Thread | None = None
        self._stop_watching = threading.Event()
        self._banner_shown = False
        # A cached load failure. Set once, cleared by an explicit rebuild or
        # config reload, so an operator who provisions the model and reloads
        # gets a fresh attempt without restarting the process.
        self._unavailable: EmbedderUnavailableError | None = None

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

        Raises:
            EmbedderUnavailableError: the configured embedder could not be
                loaded and the deterministic fallback is not explicitly
                allowed. The failure is cached, so a broken configuration
                costs one load attempt rather than one per query.
        """
        with self._lock:
            if self._unavailable is not None:
                raise EmbedderUnavailableError(str(self._unavailable))

            if self._engine is None:
                # First build: load from config and show banner
                cfg = self._load_config()
                try:
                    self._engine = EmbeddingEngine(cfg)
                except EmbedderUnavailableError as e:
                    self._unavailable = e
                    logger.error(
                        "Embeddings are configured but unavailable, so no vectors "
                        "will be produced and vector retrieval is off: %s",
                        e,
                    )
                    raise
                self._show_banner_once()
            return self._engine

    def status(self) -> EmbeddingStatus:
        """The current embedding state, without raising.

        This is what health, readiness and CLI surfaces read: asking "what is
        retrieval actually doing" must never itself fail, and must never
        answer by guessing from configuration.
        """
        cfg = self._load_config()

        if not embeddings_enabled():
            return EmbeddingStatus(
                mode=EmbeddingMode.DISABLED,
                provider=cfg.provider,
                model=cfg.model,
                dim=cfg.dim,
                semantic=False,
                # Deliberately off is not degraded: the system is doing what
                # it was told. FTS retrieval still works.
                degraded=False,
                reason="Embeddings are disabled (BARTHO_EMBED_ENABLED is not set).",
            )

        try:
            return self.get().status()
        except EmbedderUnavailableError as e:
            return EmbeddingStatus(
                mode=EmbeddingMode.UNAVAILABLE,
                provider=cfg.provider,
                model=cfg.model,
                dim=cfg.dim,
                semantic=False,
                degraded=True,
                reason=str(e),
            )
        except Exception as e:  # pragma: no cover - defensive
            return EmbeddingStatus(
                mode=EmbeddingMode.UNAVAILABLE,
                provider=cfg.provider,
                model=cfg.model,
                dim=cfg.dim,
                semantic=False,
                degraded=True,
                reason=f"Embedding engine could not be constructed: {e}",
            )

    def rebuild(self, cfg: EmbeddingConfig) -> None:
        """
        Atomically swap to a new engine with given config

        Thread-safe: readers never see half-initialized engine
        """
        try:
            new_engine = EmbeddingEngine(cfg)
        except EmbedderUnavailableError as e:
            # Keep serving nothing rather than serving the previous engine
            # under a configuration that no longer describes it.
            with self._lock:
                self._engine = None
                self._unavailable = e
            logger.error("Embedding engine rebuild failed, embeddings unavailable: %s", e)
            raise

        with self._lock:
            self._engine = new_engine
            self._unavailable = None

        logger.info(
            f"Rebuilt embedding engine: provider={cfg.provider} model={cfg.model} dim={cfg.dim}",
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
        model_path: str | None = None
        # Never download by default. Fetching model assets is a deliberate
        # provisioning step, not something ordinary retrieval can trigger.
        allow_download = False

        if not self._config_path:
            return EmbeddingConfig(
                provider=provider,
                model=model,
                dim=dim,
                model_path=os.getenv("BARTHO_EMBED_MODEL_PATH"),
                allow_download=allow_download,
            )

        try:
            import yaml

            with open(self._config_path) as f:
                data = yaml.safe_load(f) or {}

            emb = data.get("embeddings", {})
            provider = emb.get("default_provider", provider)
            model = emb.get("default_model", model)
            dim = emb.get("default_dim", dim)
            model_path = emb.get("model_path", model_path)
            allow_download = bool(emb.get("allow_download", allow_download))
        except Exception as e:
            logger.warning(f"Failed to load embeddings.yaml: {e}, using defaults")

        # The environment wins over the file for the local path, so a
        # deployment can point at its own provisioned model directory without
        # editing tracked configuration.
        model_path = os.getenv("BARTHO_EMBED_MODEL_PATH") or model_path

        return EmbeddingConfig(
            provider=provider,
            model=model,
            dim=dim,
            model_path=model_path,
            allow_download=allow_download,
        )

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

        status = self._engine.status() if self._engine else self.status()

        # The banner reports the *effective* mode, not the configured model.
        # Reporting the configured name while the hash embedder was serving is
        # the OP-W003 condition this line used to reproduce.
        message = (
            f"Embeddings enabled: mode={status.mode.value} "
            f"provider={status.provider} model={status.model} dim={status.dim} "
            f"semantic={str(status.semantic).lower()} vss={vss_status}"
        )
        if status.degraded:
            logger.warning("%s -- DEGRADED: %s", message, status.reason)
        else:
            logger.info(message)

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

    Raises:
        EmbedderUnavailableError: the configured embedder could not be loaded
            and the deterministic fallback was not explicitly allowed.

    Per-runtime isolation note: this singleton holds an immutable loaded model
    and configuration only. It holds no user text, no query, and no derived
    cache, so it is safe to share within one runtime process. It is **not** a
    place to add any user-derived cache -- per-user isolation lives at the
    runtime/data boundary, and a shared content cache here would cross it.
    """
    return _embedding_factory.get()


def get_embedding_status() -> EmbeddingStatus:
    """The current, truthful embedding mode. Never raises.

    The single accessor for health, readiness, CLI and retrieval-result
    surfaces, so they cannot drift apart or answer from configuration.
    """
    return _embedding_factory.status()
