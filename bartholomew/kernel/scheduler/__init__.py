"""
Bartholomew Scheduler / Autonomy Loop

This package provides a durable scheduler that runs internal drives on cadence,
persists their ticks and outputs, and exposes activity through /api/liveness endpoints.
"""

from .drives import REGISTRY
from .loop import run_scheduler


__all__ = ["run_scheduler", "REGISTRY"]
