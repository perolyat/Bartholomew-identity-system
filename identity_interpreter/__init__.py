"""
Bartholomew Identity Interpreter
Parses, validates, and enforces identity.yaml configuration
"""

__version__ = "0.1.0"

from .loader import load_identity
from .models import Decision, Identity
from .normalizer import normalize_identity


__all__ = [
    "load_identity",
    "normalize_identity",
    "Identity",
    "Decision",
]
