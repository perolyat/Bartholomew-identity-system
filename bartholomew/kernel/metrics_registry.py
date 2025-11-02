"""
Prometheus metrics registry with duplicate collector protection.

Provides a singleton registry pattern with thread-safe initialization
to prevent "Duplicated timeseries in CollectorRegistry" errors when
modules are reloaded (e.g., by uvicorn's auto-reload).
"""
from __future__ import annotations
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level state
_registry: Optional["CollectorRegistry"] = None
_registry_lock = threading.Lock()
_initialized = False


try:
    from prometheus_client import CollectorRegistry
    _prometheus_available = True
except ImportError:
    _prometheus_available = False
    
    # Stub for when prometheus_client is not installed
    class CollectorRegistry:
        def __init__(self, *args, **kwargs):
            pass


def get_metrics_registry() -> CollectorRegistry:
    """
    Get or create the global metrics registry.
    
    Thread-safe singleton pattern ensures only one registry is created
    even if called from multiple threads or during module reloads.
    
    Returns:
        CollectorRegistry instance (shared across all callers)
    """
    global _registry, _initialized
    
    with _registry_lock:
        if not _initialized:
            if _prometheus_available:
                _registry = CollectorRegistry(auto_describe=True)
                logger.debug("Created Prometheus metrics registry")
            else:
                _registry = CollectorRegistry()
                logger.debug(
                    "prometheus_client not available, using stub registry"
                )
            _initialized = True
        
        return _registry


def reset_metrics_registry() -> None:
    """
    Reset the global metrics registry.
    
    USE WITH CAUTION: This is primarily for testing. Resetting the
    registry while metrics are being collected can cause issues.
    """
    global _registry, _initialized
    
    with _registry_lock:
        _registry = None
        _initialized = False
        logger.debug("Reset metrics registry")
