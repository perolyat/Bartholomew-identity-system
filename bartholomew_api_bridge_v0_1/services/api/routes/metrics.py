"""
Prometheus metrics endpoint for kernel observability.

Uses shared metrics registry with duplicate collector protection
to prevent registration errors during module reloads.
"""

import os
import sys
import time

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest


# Import the shared metrics registry
sys.path.insert(  # noqa: E402
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "bartholomew", "kernel"),
)
from metrics_registry import get_metrics_registry  # noqa: E402


router = APIRouter(tags=["metrics"])

# Get shared registry (singleton, safe for reloads)
REGISTRY = get_metrics_registry()

# Module-level flag to ensure metrics are only registered once
_metrics_registered = False

# Metrics holders (will be initialized once)
KERNEL_TICKS_TOTAL = None
BARTHOLOMEW_TICKS_TOTAL = None
KERNEL_UPTIME_SECONDS = None


def _init_metrics_once():
    """Initialize metrics collectors exactly once."""
    global _metrics_registered
    global KERNEL_TICKS_TOTAL, BARTHOLOMEW_TICKS_TOTAL, KERNEL_UPTIME_SECONDS

    if _metrics_registered:
        return

    # Register metrics with shared registry
    KERNEL_TICKS_TOTAL = Counter(
        "kernel_ticks_total",
        "Total number of kernel ticks observed by the API bridge, labeled by active drive.",
        ["drive"],
        registry=REGISTRY,
    )

    BARTHOLOMEW_TICKS_TOTAL = Counter(
        "bartholomew_ticks_total",
        "Total number of Bartholomew ticks observed by the API bridge, labeled by active drive.",
        ["drive"],
        registry=REGISTRY,
    )

    KERNEL_UPTIME_SECONDS = Gauge(
        "kernel_uptime_seconds",
        "Process uptime in seconds since API bridge start.",
        registry=REGISTRY,
    )

    _metrics_registered = True


# Initialize metrics on module load
_init_metrics_once()


@router.get("/metrics")
def metrics(request: Request) -> Response:
    """
    Prometheus text exposition format endpoint.

    Exposes kernel_uptime_seconds and kernel_ticks_total{drive=<name>}.
    Uses shared registry with duplicate collector protection.
    """
    # Ensure metrics are initialized (idempotent)
    _init_metrics_once()

    # Update uptime gauge right before scrape (cheap and fresh)
    if KERNEL_UPTIME_SECONDS is not None:
        start = getattr(request.app.state, "start_monotonic", None)
        if start is not None:
            KERNEL_UPTIME_SECONDS.set(max(0.0, time.monotonic() - start))

    payload = generate_latest(REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
