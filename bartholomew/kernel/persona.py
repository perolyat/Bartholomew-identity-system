from __future__ import annotations
import yaml
from typing import Dict, Any


def load_persona(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
