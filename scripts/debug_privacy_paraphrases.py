"""Small debug helper for inspecting privacy gates on paraphrase rows.

Usage:
    python -m scripts.debug_privacy_paraphrases
or:
    python scripts/debug_privacy_paraphrases.py

This script:

- Reads data/hybrid_paraphrases.csv
- For each row, builds the same memory dict shape that
  MemoryStore.upsert_memory passes into the rules engine
- Calls memory_rules._rules_engine.evaluate(memory_dict)
- Prints: id, privacy_marker, trimmed text, allow_store, requires_consent

Rows with privacy_marker == "requires_consent" (26–30 in the stock
CSV) are highlighted with a [FOCUS] prefix for quick inspection.
"""

from __future__ import annotations

import csv
from pathlib import Path

from bartholomew.kernel.memory_rules import _rules_engine


def build_memory_dict(row: dict[str, str]) -> dict[str, str]:
    """Build a memory dict matching MemoryStore.upsert_memory.

    In MemoryStore.upsert_memory we pass:
        {
            "kind": kind,
            "key": key,
            "value": value,
            "ts": ts,
        }

    Here we mirror that shape using the paraphrase CSV columns.
    """
    kind = row["kind"]
    # Match the test harness pattern for keys for fidelity, though
    # the rules engine only really cares about kind/content.
    key = f"para_{row['id']}"
    value = row["text"]
    ts = row["ts"]

    return {
        "kind": kind,
        "key": key,
        "value": value,
        "ts": ts,
    }


def load_paraphrases() -> list[dict[str, str]]:
    """Load the hybrid paraphrase dataset from the repo-root data/ dir."""
    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data" / "hybrid_paraphrases.csv"

    rows: list[dict[str, str]] = []
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def _trim_text(text: str, max_len: int = 80) -> str:
    """Normalize and truncate text for compact debug printing."""
    normalized = " ".join(text.split())  # collapse whitespace/newlines
    if len(normalized) <= max_len:
        return normalized
    return normalized[: max_len - 3] + "..."


def main() -> None:
    rows = load_paraphrases()

    print("Inspecting privacy flags for hybrid_paraphrases.csv")
    print("(Rows with privacy_marker == 'requires_consent' are marked [FOCUS])")
    print()

    header = (
        "FLAG",
        "ID",
        "MARKER",
        "ALLOW_STORE",
        "REQUIRES_CONSENT",
        "TEXT",
    )
    print(
        f"{header[0]:<8} {header[1]:>3} {header[2]:<18} "
        f"{header[3]:<12} {header[4]:<17} {header[5]}",
    )
    print("-" * 90)

    for row in rows:
        mem = build_memory_dict(row)
        evaluated = _rules_engine.evaluate(mem)

        allow_store = evaluated.get("allow_store")
        requires_consent = evaluated.get("requires_consent")

        marker = row.get("privacy_marker", "") or "-"
        focus = marker == "requires_consent"
        flag = "[FOCUS]" if focus else ""

        text = _trim_text(row["text"])

        print(
            f"{flag:<8} {row['id']:>3} {marker:<18} "
            f"{str(allow_store):<12} {str(requires_consent):<17} {text}",
        )


if __name__ == "__main__":  # pragma: no cover
    main()
