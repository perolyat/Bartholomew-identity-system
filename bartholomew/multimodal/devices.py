"""The narrow, read-only view of a device's declared capabilities.

**This is a consumer, not a registry.** Contract §3.3 gives device enrolment,
the capability manifest and its persistence to Session E. Package C must not
own any of that, so this module defines only the smallest question C needs to
ask -- "does device X declare capability kind K at version V?" -- as a
`Protocol`, plus a `StaticCapabilityResolver` that answers it from an
in-memory manifest for tests and local alpha use until E's registry exists.

`StaticCapabilityResolver` is explicitly a stand-in. It performs no enrolment,
issues no credentials, verifies no device identity and persists nothing. When
Session F wires E's registry in, F replaces the resolver object; nothing else
in this package changes, because nothing else in this package knows where the
manifest came from.

**Unknown is unsupported, never approximated** (§3.3). A device that does not
declare a kind, declares it at another version, or cannot be resolved at all
yields `DeviceCapability(supported=False)` with a reason -- which denies the
session. A resolver that raises is also a denial: an unreadable device state
fails closed, per invariant 3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .modality import CAPABILITY_KIND, CAPABILITY_VERSION, Modality

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceCapability:
    """Whether one device supports one capability kind, and why not if not."""

    device_id: str
    kind: str
    version: int
    supported: bool
    reason: str | None = None
    #: Verification level of the device identity itself, mirroring §3.1's
    #: `source.verification`. Package C never upgrades this: a claimed device
    #: stays claimed in every record it produces (invariant 15).
    verification: str = "claimed"


@runtime_checkable
class DeviceCapabilityResolver(Protocol):
    """The one question Package C asks about a device.

    Session E owns the answer. Session F supplies the implementation. This
    signature is the exact interface E must satisfy -- see the closeout.
    """

    def resolve(self, device_id: str, kind: str, version: int) -> DeviceCapability: ...


@dataclass
class StaticCapabilityResolver:
    """A manifest-in-a-dict resolver for tests and local alpha.

    Holds `{device_id: {(kind, version), ...}}` and answers from it. It is not
    a registry: it enrols nothing and remembers nothing across a process.
    """

    manifests: dict[str, set[tuple[str, int]]] = field(default_factory=dict)
    #: Verification level to report for devices in this manifest. Defaults to
    #: "claimed" because a static dict has verified nothing.
    verification: str = "claimed"

    def declare(self, device_id: str, kinds: list[str], version: int = CAPABILITY_VERSION) -> None:
        """Record that a device declares these capability kinds."""
        self.manifests.setdefault(device_id, set()).update((k, version) for k in kinds)

    def resolve(self, device_id: str, kind: str, version: int) -> DeviceCapability:
        manifest = self.manifests.get(device_id)
        if manifest is None:
            return DeviceCapability(
                device_id=device_id,
                kind=kind,
                version=version,
                supported=False,
                reason=f"device {device_id!r} is not enrolled",
                verification=self.verification,
            )
        if (kind, version) not in manifest:
            declared = sorted(k for k, v in manifest if v == version)
            return DeviceCapability(
                device_id=device_id,
                kind=kind,
                version=version,
                supported=False,
                reason=(
                    f"device {device_id!r} does not declare {kind} v{version} "
                    f"(declares: {declared or 'nothing at this version'})"
                ),
                verification=self.verification,
            )
        return DeviceCapability(
            device_id=device_id,
            kind=kind,
            version=version,
            supported=True,
            verification=self.verification,
        )


def resolve_modality_capability(
    resolver: DeviceCapabilityResolver | None,
    device_id: str,
    modality: Modality,
) -> DeviceCapability:
    """Resolve one modality's capability, failing closed on every unknown.

    A missing resolver denies: Package C will not assume a device can do
    something because nobody was available to say it could not.
    """
    kind = CAPABILITY_KIND[modality]
    if resolver is None:
        return DeviceCapability(
            device_id=device_id,
            kind=kind,
            version=CAPABILITY_VERSION,
            supported=False,
            reason="no device capability resolver is configured (fail-closed)",
        )
    try:
        return resolver.resolve(device_id, kind, CAPABILITY_VERSION)
    except Exception as exc:
        logger.exception("Device capability resolution failed; failing closed")
        return DeviceCapability(
            device_id=device_id,
            kind=kind,
            version=CAPABILITY_VERSION,
            supported=False,
            reason=f"device capability resolution errored: {exc}",
        )
