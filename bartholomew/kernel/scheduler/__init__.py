"""
Bartholomew Scheduler / Autonomy Loop

This package provides a durable scheduler that runs internal drives on cadence,
persists their ticks and outputs, and exposes activity through /api/liveness endpoints.
"""

from .loop import run_scheduler
from .drives import REGISTRY

__all__ = ["run_scheduler", "REGISTRY"]
