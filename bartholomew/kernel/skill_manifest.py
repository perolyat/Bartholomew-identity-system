"""
Skill Manifest
==============

Dataclasses for loading and validating skill manifests from YAML files.
Part of Stage 4: Skill Registry + Starter Skills.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


logger = logging.getLogger(__name__)


@dataclass
class ActionParameter:
    """Parameter definition for a skill action."""

    name: str
    type: str
    required: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionParameter:
        return cls(
            name=data["name"],
            type=data["type"],
            required=data.get("required", False),
            description=data.get("description", ""),
        )


@dataclass
class SkillAction:
    """Action definition for a skill."""

    name: str
    description: str = ""
    parameters: list[ActionParameter] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [p.to_dict() for p in self.parameters],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillAction:
        params = [ActionParameter.from_dict(p) for p in data.get("parameters", [])]
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            parameters=params,
        )


@dataclass
class SkillSubscription:
    """Channel subscription definition."""

    channel: str
    events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "events": self.events,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillSubscription:
        return cls(
            channel=data["channel"],
            events=data.get("events", []),
        )


@dataclass
class SkillEmit:
    """Event emission definition."""

    channel: str
    events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "events": self.events,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillEmit:
        return cls(
            channel=data["channel"],
            events=data.get("events", []),
        )


@dataclass
class SkillSandbox:
    """Sandbox restrictions for a skill."""

    filesystem: list[str] = field(default_factory=list)
    network: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filesystem": self.filesystem,
            "network": self.network,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillSandbox:
        return cls(
            filesystem=data.get("filesystem", []),
            network=data.get("network", []),
        )


@dataclass
class SkillPermissions:
    """Permission requirements for a skill."""

    level: str  # "ask", "auto", or "never"
    requires: list[str] = field(default_factory=list)
    sandbox: SkillSandbox = field(default_factory=SkillSandbox)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "requires": self.requires,
            "sandbox": self.sandbox.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillPermissions:
        sandbox_data = data.get("sandbox", {})
        return cls(
            level=data.get("level", "ask"),
            requires=data.get("requires", []),
            sandbox=SkillSandbox.from_dict(sandbox_data),
        )


@dataclass
class SkillManifest:
    """
    Complete skill manifest loaded from YAML.

    Represents all metadata, permissions, actions, and configuration
    needed to load and manage a skill.
    """

    # Required fields
    skill_id: str
    name: str
    version: str
    entry_module: str
    entry_class: str
    permissions: SkillPermissions

    # Optional fields
    description: str = ""
    subscriptions: list[SkillSubscription] = field(default_factory=list)
    emits: list[SkillEmit] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    auto_activate_on: list[str] = field(default_factory=list)
    actions: list[SkillAction] = field(default_factory=list)
    author: str = "unknown"
    license: str = "MIT"
    enabled: bool = True

    # Internal tracking
    manifest_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize manifest to dictionary."""
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "entry_module": self.entry_module,
            "entry_class": self.entry_class,
            "permissions": self.permissions.to_dict(),
            "subscriptions": [s.to_dict() for s in self.subscriptions],
            "emits": [e.to_dict() for e in self.emits],
            "depends_on": self.depends_on,
            "auto_activate_on": self.auto_activate_on,
            "actions": [a.to_dict() for a in self.actions],
            "author": self.author,
            "license": self.license,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], manifest_path: str | None = None) -> SkillManifest:
        """Create manifest from dictionary."""
        permissions_data = data.get("permissions", {"level": "ask"})
        subscriptions = [SkillSubscription.from_dict(s) for s in data.get("subscriptions", [])]
        emits = [SkillEmit.from_dict(e) for e in data.get("emits", [])]
        actions = [SkillAction.from_dict(a) for a in data.get("actions", [])]

        return cls(
            skill_id=data["skill_id"],
            name=data["name"],
            version=data["version"],
            entry_module=data["entry_module"],
            entry_class=data["entry_class"],
            permissions=SkillPermissions.from_dict(permissions_data),
            description=data.get("description", ""),
            subscriptions=subscriptions,
            emits=emits,
            depends_on=data.get("depends_on", []),
            auto_activate_on=data.get("auto_activate_on", []),
            actions=actions,
            author=data.get("author", "unknown"),
            license=data.get("license", "MIT"),
            enabled=data.get("enabled", True),
            manifest_path=manifest_path,
        )

    @classmethod
    def load_from_yaml(cls, path: str | Path) -> SkillManifest:
        """Load manifest from YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {path}")

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError(f"Empty manifest: {path}")

        return cls.from_dict(data, manifest_path=str(path))

    def save_to_yaml(self, path: str | Path) -> None:
        """Save manifest to YAML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)

    def validate(self) -> list[str]:
        """
        Validate manifest and return list of validation errors.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Required fields
        if not self.skill_id:
            errors.append("skill_id is required")
        elif not self.skill_id.replace("_", "").isalnum():
            errors.append("skill_id must be alphanumeric with underscores only")

        if not self.name:
            errors.append("name is required")

        if not self.version:
            errors.append("version is required")

        if not self.entry_module:
            errors.append("entry_module is required")

        if not self.entry_class:
            errors.append("entry_class is required")

        # Permission level validation
        valid_levels = {"ask", "auto", "never"}
        if self.permissions.level not in valid_levels:
            errors.append(f"permissions.level must be one of: {valid_levels}")

        # Action validation
        action_names = set()
        for action in self.actions:
            if not action.name:
                errors.append("action.name is required for all actions")
            elif action.name in action_names:
                errors.append(f"duplicate action name: {action.name}")
            else:
                action_names.add(action.name)

        return errors

    def get_action(self, name: str) -> SkillAction | None:
        """Get action by name."""
        for action in self.actions:
            if action.name == name:
                return action
        return None

    def has_permission(self, permission: str) -> bool:
        """Check if skill requires a specific permission."""
        return permission in self.permissions.requires

    def subscribes_to(self, channel: str) -> bool:
        """Check if skill subscribes to a channel."""
        return any(s.channel == channel for s in self.subscriptions)

    def __repr__(self) -> str:
        return f"SkillManifest(id={self.skill_id!r}, name={self.name!r}, version={self.version!r})"


def discover_manifests(skills_dir: str | Path) -> list[SkillManifest]:
    """
    Discover all skill manifests in a directory.

    Args:
        skills_dir: Directory to scan for .yaml manifest files

    Returns:
        List of loaded SkillManifest objects
    """
    skills_dir = Path(skills_dir)
    if not skills_dir.exists():
        logger.warning("Skills directory does not exist: %s", skills_dir)
        return []

    manifests = []
    for yaml_file in skills_dir.glob("*.yaml"):
        # Skip schema file
        if yaml_file.name == "skill.schema.json":
            continue

        try:
            manifest = SkillManifest.load_from_yaml(yaml_file)
            errors = manifest.validate()
            if errors:
                logger.warning("Invalid manifest %s: %s", yaml_file.name, errors)
                continue
            manifests.append(manifest)
            logger.debug("Discovered skill: %s v%s", manifest.skill_id, manifest.version)
        except Exception as e:
            logger.warning("Failed to load manifest %s: %s", yaml_file.name, e)

    return manifests
