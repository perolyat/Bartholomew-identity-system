"""
Prometheus metrics endpoint for kernel observability.
"""

from fastapi import APIRouter, Request, Response
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
import time

router = APIRouter(tags=["metrics"])

# Per-drive counter labels
KERNEL_TICKS_TOTAL = Counter(
    "kernel_ticks_total",
    "Total number of kernel ticks observed by the API bridge, labeled by active drive.",
    ["drive"],
)

BARTHOLOMEW_TICKS_TOTAL = Counter(
    "bartholomew_ticks_total",
    "Total number of Bartholomew ticks observed by the API bridge, "
    "labeled by active drive.",
    ["drive"],
)

KERNEL_UPTIME_SECONDS = Gauge(
    "kernel_uptime_seconds",
    "Process uptime in seconds since API bridge start."
)


@router.get("/metrics")
def metrics(request: Request) -> Response:
    """
    Prometheus text exposition format endpoint.
    
    Exposes kernel_uptime_seconds and kernel_ticks_total{drive=<name>}.
    """
    # Update uptime gauge right before scrape (cheap and fresh)
    start = getattr(request.app.state, "start_monotonic", None)
    if start is not None:
        KERNEL_UPTIME_SECONDS.set(max(0.0, time.monotonic() - start))
    payload = generate_latest()
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
