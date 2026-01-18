#!/usr/bin/env python3
"""
Strict validation for extracted features JSON files.
Enforces schema, gate labels, and quote length constraints.
Exits with code 1 on any validation error.
"""
import json
import sys
from pathlib import Path
from typing import Any


VALID_GATES = {"Gate 0", "Gate 1", "Gate 2", "Gate 3"}
MAX_QUOTE_LENGTH = 200


def validate_feature(feature: dict[str, Any], index: int) -> list[str]:
    """
    Validate a single feature entry.
    Returns list of error messages (empty if valid).
    """
    errors = []

    # Required string fields
    required_strings = ["feature", "summary", "rationale", "suggested_stage_gate"]
    for field in required_strings:
        if field not in feature:
            errors.append(f"Feature {index}: Missing required field '{field}'")
        elif not isinstance(feature[field], str):
            errors.append(f"Feature {index}: Field '{field}' must be a string")
        elif not feature[field].strip():
            errors.append(f"Feature {index}: Field '{field}' cannot be empty")

    # Required array fields
    required_arrays = ["constraints", "dependencies", "evidence"]
    for field in required_arrays:
        if field not in feature:
            errors.append(f"Feature {index}: Missing required field '{field}'")
        elif not isinstance(feature[field], list):
            errors.append(f"Feature {index}: Field '{field}' must be an array")

    # Validate gate label (STRICT)
    if "suggested_stage_gate" in feature:
        gate = feature["suggested_stage_gate"]
        if gate not in VALID_GATES:
            errors.append(
                f"Feature {index}: Invalid gate '{gate}'. "
                f"Must be one of: {', '.join(sorted(VALID_GATES))}",
            )

    # Validate evidence array
    if "evidence" in feature and isinstance(feature["evidence"], list):
        if len(feature["evidence"]) == 0:
            errors.append(f"Feature {index}: 'evidence' array cannot be empty")

        for ev_idx, evidence in enumerate(feature["evidence"]):
            if not isinstance(evidence, dict):
                errors.append(
                    f"Feature {index}, Evidence {ev_idx}: Evidence item must be an object",
                )
                continue

            # Required evidence fields
            if "conversation_title" not in evidence:
                errors.append(
                    f"Feature {index}, Evidence {ev_idx}: Missing 'conversation_title'",
                )
            elif not isinstance(evidence["conversation_title"], str):
                errors.append(
                    f"Feature {index}, Evidence {ev_idx}: 'conversation_title' must be a string",
                )

            if "quote" not in evidence:
                errors.append(f"Feature {index}, Evidence {ev_idx}: Missing 'quote'")
            elif not isinstance(evidence["quote"], str):
                errors.append(f"Feature {index}, Evidence {ev_idx}: 'quote' must be a string")
            else:
                # STRICT quote length check
                quote_len = len(evidence["quote"])
                if quote_len > MAX_QUOTE_LENGTH:
                    errors.append(
                        f"Feature {index}, Evidence {ev_idx}: "
                        f"Quote length {quote_len} exceeds maximum "
                        f"{MAX_QUOTE_LENGTH} characters",
                    )

    return errors


def validate_file(filepath: Path) -> tuple[bool, list[str], dict[str, Any]]:
    """
    Validate a features JSON file.
    Returns (success, errors, stats).
    """
    errors = []
    stats = {
        "total_features": 0,
        "gate_distribution": {},
        "quote_lengths": [],
        "avg_quote_length": 0,
        "max_quote_length": 0,
    }

    # Load file
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"JSON decode error: {e}")
        return False, errors, stats
    except Exception as e:
        errors.append(f"Failed to read file: {e}")
        return False, errors, stats

    # Must be an array
    if not isinstance(data, list):
        errors.append("Root element must be an array")
        return False, errors, stats

    if len(data) == 0:
        errors.append("Feature array is empty")
        return False, errors, stats

    stats["total_features"] = len(data)

    # Validate each feature
    for idx, feature in enumerate(data, 1):
        if not isinstance(feature, dict):
            errors.append(f"Feature {idx}: Must be an object")
            continue

        feature_errors = validate_feature(feature, idx)
        errors.extend(feature_errors)

        # Collect stats (only if feature is valid enough)
        if "suggested_stage_gate" in feature:
            gate = feature.get("suggested_stage_gate", "unknown")
            stats["gate_distribution"][gate] = stats["gate_distribution"].get(gate, 0) + 1

        if "evidence" in feature and isinstance(feature["evidence"], list):
            for evidence in feature["evidence"]:
                if isinstance(evidence, dict) and "quote" in evidence:
                    if isinstance(evidence["quote"], str):
                        quote_len = len(evidence["quote"])
                        stats["quote_lengths"].append(quote_len)
                        stats["max_quote_length"] = max(stats["max_quote_length"], quote_len)

    # Calculate average quote length
    if stats["quote_lengths"]:
        stats["avg_quote_length"] = sum(stats["quote_lengths"]) / len(stats["quote_lengths"])

    return len(errors) == 0, errors, stats


def main():
    """Main validation routine."""
    if len(sys.argv) < 2:
        print("Usage: python validate_features.py <file1.json> [file2.json ...]")
        sys.exit(1)

    files = [Path(f) for f in sys.argv[1:]]

    # Check all files exist
    missing = [f for f in files if not f.exists()]
    if missing:
        print("ERROR: Files not found:")
        for f in missing:
            print(f"  - {f}")
        sys.exit(1)

    print("=" * 70)
    print("FEATURE VALIDATION REPORT")
    print("=" * 70)

    all_valid = True
    total_features = 0
    total_errors = 0

    for filepath in files:
        print(f"\nValidating: {filepath}")
        print("-" * 70)

        success, errors, stats = validate_file(filepath)

        if success:
            print(f"✓ PASS - {stats['total_features']} features validated")
        else:
            print(f"✗ FAIL - {len(errors)} validation error(s) found")
            all_valid = False
            total_errors += len(errors)

        # Display stats
        print(f"\nFeatures: {stats['total_features']}")
        if stats["gate_distribution"]:
            print("Gate Distribution:")
            for gate in sorted(stats["gate_distribution"].keys()):
                count = stats["gate_distribution"][gate]
                print(f"  {gate}: {count}")

        if stats["quote_lengths"]:
            print("Quote Lengths:")
            print(f"  Average: {stats['avg_quote_length']:.1f} chars")
            print(f"  Maximum: {stats['max_quote_length']} chars")
            print(f"  Total quotes: {len(stats['quote_lengths'])}")

        # Display errors
        if errors:
            print(f"\nERRORS ({len(errors)}):")
            for error in errors:
                print(f"  • {error}")

        total_features += stats["total_features"]

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Files validated: {len(files)}")
    print(f"Total features: {total_features}")

    if all_valid:
        print("\n✓ ALL FILES PASSED VALIDATION")
        sys.exit(0)
    else:
        print(f"\n✗ VALIDATION FAILED - {total_errors} total error(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
