"""
What a companion device may claim it can do, and what Bartholomew will believe.

A **frozen vocabulary**, deliberately, and the freezing is the security
property rather than a tidiness one. Three separate things follow from it:

* **Unknown is unsupported, never approximated.** A capability kind this
  module does not know, or a known kind at a version this module does not
  know, is *not* silently mapped onto the nearest thing that looks similar.
  `windows.open_url` at version 2 is not version 1 with extras; it is an
  unknown contract, and acting on an unknown contract is how "open this URL"
  becomes "open this path". `supports()` returns False for both cases and
  there is no fuzzy branch anywhere below.

* **Declaring is not authorising.** A manifest records what a device says it
  can do. It never widens what the device is permitted to do: the registry
  stores the declaration, and `authorized_capabilities()` intersects it with
  this vocabulary. A device that declares `windows.launch_app` at an unknown
  version has declared something, and is authorised for nothing by it.

* **Portable contract, no actuation.** The kinds below name a portable
  capability surface. **This module implements none of them**, and neither
  does anything else in Package E: there is no Windows actuation code, no
  microphone, no screen capture and no speech synthesis behind these names.
  They exist so that Sessions B and C can ask "may I, on this device?" and
  get a fail-closed answer *before* they write the handler that would act.

Versions are integers, not semantic version strings, and each `(kind,
version)` pair is a distinct contract. A device declaring several versions of
one kind is legal and means it speaks both; the caller asks for the exact
version it is about to use.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: The one platform this wave targets. A device may enrol declaring another
#: platform string -- the registry records it verbatim -- but no capability
#: below is implemented for one, and `supports()` does not consult it: the
#: platform is provenance about the machine, not a second authorisation axis
#: that could accidentally grant something the manifest did not.
PLATFORM_WINDOWS = "windows"

#: Every capability kind this vocabulary knows, mapped to the set of contract
#: versions it knows for that kind. Both halves are checked; see the module
#: docstring for why an unknown version is not "close enough".
#:
#: Frozen as of Package E. Adding an entry is a deliberate, separately
#: reviewed act -- it is the moment a new thing becomes sayable about a
#: device -- and `tests/test_device_registry_trust.py` pins the exact set so
#: that widening it cannot happen incidentally in a refactor.
CAPABILITY_VERSIONS: dict[str, frozenset[int]] = {
    "windows.open_url": frozenset({1}),
    "windows.open_path": frozenset({1}),
    "windows.launch_app": frozenset({1}),
    "windows.focus_window": frozenset({1}),
    "windows.manage_window": frozenset({1}),
    "windows.clipboard_read": frozenset({1}),
    "windows.clipboard_write": frozenset({1}),
    "windows.type_text": frozenset({1}),
    "windows.accessibility_action": frozenset({1}),
    "multimodal.microphone_session": frozenset({1}),
    "multimodal.screen_capture": frozenset({1}),
    "multimodal.spoken_output": frozenset({1}),
}

#: The known kinds, in declaration order. Ordered so operator output and the
#: documented manifest read the same way every time.
CAPABILITY_KINDS: tuple[str, ...] = tuple(CAPABILITY_VERSIONS)

#: Longest kind/version a manifest entry may carry before it is refused.
#: A registry row is not a place to put arbitrary caller-supplied text.
_MAX_KIND_LENGTH = 64
_MAX_VERSION = 1_000_000

#: Manifest revision recorded when a device has never declared one.
MANIFEST_VERSION_NONE = 0


class ManifestError(ValueError):
    """A declared manifest is malformed and cannot be recorded at all.

    Distinct from *unsupported*: an unsupported capability is a well-formed
    claim this deployment does not understand, and is recorded as declared
    while authorising nothing. A malformed manifest is not a claim -- there
    is nothing to record -- so it is refused rather than partially accepted.
    """


@dataclass(frozen=True)
class Capability:
    """One `(kind, version)` contract a device claims to speak."""

    kind: str
    version: int

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "version": self.version}

    @property
    def is_known(self) -> bool:
        return supports(self.kind, self.version)

    def __str__(self) -> str:  # pragma: no cover - operator display only
        return f"{self.kind}@v{self.version}"


def supports(kind: str, version: int) -> bool:
    """Whether this deployment understands `(kind, version)` at all.

    The single place the vocabulary is consulted. Returns False for an
    unknown kind *and* for a known kind at an unknown version, with no
    nearest-match, no "highest version we know", and no truthy default.
    """
    known = CAPABILITY_VERSIONS.get(kind)
    if known is None:
        return False
    try:
        return int(version) in known
    except (TypeError, ValueError):
        return False


def parse_capability(entry: Any) -> Capability:
    """One manifest entry -> `Capability`, or raise `ManifestError`.

    Shape validation only. Whether the resulting capability is *known* is a
    separate question, answered by `Capability.is_known`, because a device
    running a newer companion must be able to enrol and be told truthfully
    which of its claims this deployment can act on.
    """
    if not isinstance(entry, dict):
        raise ManifestError(f"capability entries must be objects, got {type(entry).__name__}")
    kind = entry.get("kind")
    version = entry.get("version")
    if not isinstance(kind, str) or not kind.strip():
        raise ManifestError("capability 'kind' must be a non-empty string")
    kind = kind.strip()
    if len(kind) > _MAX_KIND_LENGTH:
        raise ManifestError(f"capability kind exceeds {_MAX_KIND_LENGTH} characters")
    if isinstance(version, bool) or not isinstance(version, int):
        # `bool` is an `int` in Python, and `{"version": true}` reaching the
        # registry as version 1 would be a capability granted by a typo.
        raise ManifestError(f"capability 'version' must be an integer, got {version!r}")
    if version < 1 or version > _MAX_VERSION:
        raise ManifestError(f"capability version {version} is out of range")
    return Capability(kind=kind, version=version)


@dataclass(frozen=True)
class DeviceCapabilityManifest:
    """What one enrolled device declared about itself.

    The logical shape is the frozen contract:

        {"device_id": ..., "platform": "windows", "companion_version": ...,
         "capabilities": [{"kind": ..., "version": 1}, ...]}

    `device_id` here is the **registry's** device id, never a caller-supplied
    label: `from_declaration()` takes it as a separate argument and ignores
    any `device_id` present in the declared body, so a companion cannot
    describe itself as another device by putting a different id in its
    manifest. See `bartholomew.platform.devices` for the same rule applied to
    every other authenticated surface.
    """

    device_id: str
    platform: str
    companion_version: str
    capabilities: tuple[Capability, ...] = ()

    # -- projections -----------------------------------------------------

    @property
    def known(self) -> tuple[Capability, ...]:
        """Declared capabilities this deployment understands."""
        return tuple(c for c in self.capabilities if c.is_known)

    @property
    def unknown(self) -> tuple[Capability, ...]:
        """Declared capabilities this deployment does not understand.

        Recorded, reported to the operator, and authorising nothing.
        """
        return tuple(c for c in self.capabilities if not c.is_known)

    def declares(self, kind: str, version: int) -> bool:
        """Whether the device claimed `(kind, version)`, known or not."""
        return any(c.kind == kind and c.version == version for c in self.capabilities)

    def authorizes(self, kind: str, version: int) -> bool:
        """Whether this manifest authorises `(kind, version)`.

        Both halves must hold: the device declared it, **and** this
        deployment understands it. Either alone is not authorisation -- a
        declaration this deployment cannot interpret is not a licence to
        guess, and a capability this deployment understands perfectly well is
        not a licence to use a device that never claimed it.
        """
        return supports(kind, version) and self.declares(kind, version)

    # -- serialisation ---------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "platform": self.platform,
            "companion_version": self.companion_version,
            "capabilities": [c.as_dict() for c in self.capabilities],
        }

    def capabilities_json(self) -> str:
        """The canonical stored form of the declared capability list."""
        return json.dumps([c.as_dict() for c in self.capabilities], separators=(",", ":"))

    # -- construction ----------------------------------------------------

    @classmethod
    def from_declaration(
        cls,
        declaration: dict[str, Any],
        *,
        device_id: str,
    ) -> DeviceCapabilityManifest:
        """Build a manifest from what a companion declared.

        `device_id` is supplied by the caller from verified state. Any
        `device_id` inside `declaration` is ignored, not merely overwritten
        after the fact, so there is no window in which the claimed value is
        the live one.
        """
        if not isinstance(declaration, dict):
            raise ManifestError("a capability manifest must be a JSON object")

        platform = declaration.get("platform")
        if not isinstance(platform, str) or not platform.strip():
            raise ManifestError("manifest 'platform' must be a non-empty string")
        companion_version = declaration.get("companion_version")
        if not isinstance(companion_version, str) or not companion_version.strip():
            raise ManifestError("manifest 'companion_version' must be a non-empty string")

        raw = declaration.get("capabilities", [])
        if not isinstance(raw, list):
            raise ManifestError("manifest 'capabilities' must be a list")

        seen: set[tuple[str, int]] = set()
        parsed: list[Capability] = []
        for entry in raw:
            capability = parse_capability(entry)
            pair = (capability.kind, capability.version)
            if pair in seen:
                # Silently de-duplicating would make the stored manifest
                # differ from the declaration in a way nothing recorded.
                raise ManifestError(f"capability {capability} is declared more than once")
            seen.add(pair)
            parsed.append(capability)

        return cls(
            device_id=device_id,
            platform=platform.strip()[:64],
            companion_version=companion_version.strip()[:64],
            capabilities=tuple(parsed),
        )

    @classmethod
    def from_row(
        cls,
        *,
        device_id: str,
        platform: str,
        companion_version: str | None,
        capabilities_json: str | None,
    ) -> DeviceCapabilityManifest:
        """Rebuild a manifest from its stored registry row.

        A stored row that no longer parses yields an **empty** capability
        list rather than raising: the device then authorises nothing, which
        is the fail-closed reading of "we cannot tell what this device
        declared". Raising here would instead take the whole registry down on
        one corrupt row.
        """
        capabilities: list[Capability] = []
        try:
            decoded = json.loads(capabilities_json or "[]")
            if isinstance(decoded, list):
                for entry in decoded:
                    try:
                        capabilities.append(parse_capability(entry))
                    except ManifestError:
                        continue
        except (TypeError, ValueError):
            capabilities = []
        return cls(
            device_id=device_id,
            platform=platform,
            companion_version=companion_version or "",
            capabilities=tuple(capabilities),
        )


def describe_vocabulary() -> list[dict[str, Any]]:
    """The known vocabulary, for operator output and documentation."""
    return [
        {"kind": kind, "versions": sorted(versions)}
        for kind, versions in CAPABILITY_VERSIONS.items()
    ]


__all__ = [
    "CAPABILITY_KINDS",
    "CAPABILITY_VERSIONS",
    "MANIFEST_VERSION_NONE",
    "PLATFORM_WINDOWS",
    "Capability",
    "DeviceCapabilityManifest",
    "ManifestError",
    "describe_vocabulary",
    "parse_capability",
    "supports",
]
