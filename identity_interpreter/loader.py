"""
Identity loader with YAML parsing and JSON Schema validation
"""

import json
from pathlib import Path

import yaml
from jsonschema import ValidationError as JSONValidationError
from jsonschema import validate

from .models import Identity


class IdentityLoadError(Exception):
    """Raised when identity loading or validation fails"""

    pass


def load_identity(
    path: str | Path,
    schema_path: str | Path | None = None,
) -> Identity:
    """
    Load and validate identity.yaml

    Args:
        path: Path to identity.yaml file
        schema_path: Optional path to JSON schema (defaults to bundled schema)

    Returns:
        Validated Identity object

    Raises:
        IdentityLoadError: If loading or validation fails
    """
    path = Path(path)

    if not path.exists():
        raise IdentityLoadError(f"Identity file not found: {path}")

    # Load YAML
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise IdentityLoadError(f"YAML parsing failed: {e}") from e
    except Exception as e:
        raise IdentityLoadError(f"Failed to read file: {e}") from e

    # Validate against JSON Schema
    if schema_path is None:
        schema_path = Path(__file__).parent / "schema" / "identity.schema.json"

    try:
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)

        validate(instance=data, schema=schema)
    except JSONValidationError as e:
        # Extract path for better error messages
        path_str = ".".join(str(p) for p in e.absolute_path)
        raise IdentityLoadError(
            f"Schema validation failed at '{path_str}': {e.message}",
        ) from e
    except FileNotFoundError as e:
        raise IdentityLoadError(f"Schema file not found: {schema_path}") from e
    except Exception as e:
        raise IdentityLoadError(f"Schema validation error: {e}") from e

    # Parse with Pydantic
    try:
        identity = Identity(**data)
        return identity
    except Exception as e:
        raise IdentityLoadError(f"Pydantic parsing failed: {e}") from e


def lint_identity(path: str | Path) -> list[str]:
    """
    Lint identity file and return warnings (not errors)

    Returns:
        List of warning messages
    """
    warnings = []

    try:
        identity = load_identity(path)

        # Check for recommended settings
        if not identity.safety_and_alignment.controls.kill_switch.enabled:
            warnings.append(
                "Kill switch is disabled (safety_and_alignment.controls.kill_switch.enabled)",
            )

        if identity.tool_use.default_allowed:
            warnings.append(
                "Tool use is default-allowed; consider deny-by-default for security (tool_use.default_allowed)",
            )

        if not identity.memory_policy.encryption.get("at_rest"):
            warnings.append(
                "Memory encryption at rest is not enabled (memory_policy.encryption.at_rest)",
            )

        # Check for empty critical lists
        if not identity.red_lines:
            warnings.append("No red lines defined (red_lines)")

        if not identity.values_and_principles.core_values:
            warnings.append("No core values defined (values_and_principles.core_values)")

        # Check confidence threshold
        threshold = identity.safety_and_alignment.confidence_policy.low_confidence_threshold
        if threshold > 0.7:
            warnings.append(
                f"Confidence threshold is high ({threshold}); "
                f"may reduce safety checks (safety_and_alignment.confidence_policy.low_confidence_threshold)",
            )

    except IdentityLoadError as e:
        warnings.append(f"Error loading identity: {e}")

    return warnings
