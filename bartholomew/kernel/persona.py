from __future__ import annotations

from typing import Any

import yaml


def load_persona(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
