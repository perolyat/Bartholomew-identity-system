"""Which devices are enrolled, and what each one is allowed to be asked to do.

**This is the seam Session E replaces.** Device and group enrolment, the
production registry schema and its lifecycle belong to Session E; Package B
must not build them and has not. What is here is (a) the narrow interface the
governance layer actually needs, written so that Session E can satisfy it
without changing a single call site, and (b) a truthful, operator-provisioned
file-backed implementation so that the boundary can be exercised, tested and
run in a Windows alpha before the production registry exists.

`StaticDeviceRegistry` is not a mock. It reads a real JSON file that an
operator really wrote, it really refuses devices that are not in it, and it is
really the thing an alpha install uses. What it is *not* is a registry: it has
no enrolment ceremony, no key material, no revocation list, no groups and no
lifecycle. It says so on its own `describe()`, and the health surface repeats
it, so nobody can be unsure which one is running.

What Session E must provide
---------------------------
An object satisfying `DeviceCapabilityRegistry` -- one method, `lookup`.
Everything the governance layer asks about a device is on `EnrolledDevice`, so
the whole of the contract between Package B and Session E is those two types:

* `EnrolledDevice.tenant_id`        -- the device belongs to exactly one tenant,
                                       and a lookup for the wrong tenant must
                                       return None rather than the device.
* `.platform`                       -- `"windows"`; anything else is refused,
                                       because this build actuates Windows only.
* `.enrolled`                       -- a device row that exists but is revoked
                                       must return `enrolled=False`, not None,
                                       so the refusal reason is truthful.
* `.capabilities`                   -- the exact (kind, version) pairs this
                                       device declares. A capability that is
                                       not declared is refused; a version that
                                       does not match is refused.
* `.applications`/`.url_domains`/`.filesystem_roots` -- the three allowlists
                                       the server validates parameters against.
* `.trusted_autonomy`               -- the kinds this device may perform without
                                       a per-action approval. Default empty, and
                                       structurally incapable of holding a kind
                                       whose `ApprovalRequirement` is `ALWAYS`.

A lookup that raises is treated as a denial by `seam.py`, so an unreachable
registry fails closed rather than admitting an unverified device.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .allowlists import (
    AllowlistError,
    ApplicationAllowlist,
    FilesystemRootAllowlist,
    UrlDomainAllowlist,
)
from .capabilities import (
    TRUSTED_AUTONOMY_ELIGIBLE,
    CapabilityKind,
    UnsupportedCapabilityError,
    parse_kind,
)
from .parameters import ValidationContext

logger = logging.getLogger(__name__)

#: Environment variable naming the enrolment file the interim registry reads.
#: Absent means no device is enrolled, which means no action can be dispatched.
ENROLMENT_PATH_ENV = "BARTH_ACTION_DEVICE_ENROLMENT"

#: The only platform this build actuates.
SUPPORTED_PLATFORM = "windows"


class DeviceRegistryError(RuntimeError):
    """The registry could not answer. Callers must treat this as a denial."""


@dataclass(frozen=True)
class DeclaredCapability:
    """One (kind, version) pair a device says it can perform."""

    kind: CapabilityKind
    version: int


@dataclass(frozen=True)
class EnrolledDevice:
    """Everything governance needs to know about one device. Frozen."""

    device_id: str
    tenant_id: str
    platform: str
    enrolled: bool
    capabilities: tuple[DeclaredCapability, ...] = ()
    applications: ApplicationAllowlist = field(
        default_factory=lambda: ApplicationAllowlist.from_pairs({}),
    )
    url_domains: UrlDomainAllowlist = field(
        default_factory=lambda: UrlDomainAllowlist.from_iterable(()),
    )
    filesystem_roots: FilesystemRootAllowlist = field(
        default_factory=lambda: FilesystemRootAllowlist.from_iterable(()),
    )
    #: Kinds this device may perform with no per-action approval. Empty by
    #: default, and validated on construction: a kind whose approval
    #: requirement is ALWAYS is refused here, so there is no configuration
    #: that makes typing text or reading the clipboard autonomous.
    trusted_autonomy: frozenset[CapabilityKind] = frozenset()

    def __post_init__(self) -> None:
        ineligible = sorted(
            k.value for k in self.trusted_autonomy if k not in TRUSTED_AUTONOMY_ELIGIBLE
        )
        if ineligible:
            raise DeviceRegistryError(
                f"device {self.device_id!r} declares trusted autonomy for {ineligible}, "
                "which is refused. Those capabilities require an explicit per-action "
                "approval that no enrolment may remove.",
            )
        undeclared = sorted(k.value for k in self.trusted_autonomy if not self.declares(k))
        if undeclared:
            raise DeviceRegistryError(
                f"device {self.device_id!r} claims trusted autonomy for {undeclared} "
                "without declaring the capability at all",
            )

    def declares(self, kind: CapabilityKind, version: int | None = None) -> bool:
        """Whether this device declares `kind` (and, if given, that exact version)."""
        for declared in self.capabilities:
            if declared.kind is kind and (version is None or declared.version == version):
                return True
        return False

    def declared_version(self, kind: CapabilityKind) -> int | None:
        for declared in self.capabilities:
            if declared.kind is kind:
                return declared.version
        return None

    def autonomous_for(self, kind: CapabilityKind) -> bool:
        """Whether this device may run `kind` with no per-action approval."""
        return kind in self.trusted_autonomy

    def validation_context(self) -> ValidationContext:
        """The context the server validates this device's parameters against.

        `filesystem_available=False`: the governing process cannot resolve a
        path on the device's disk, so it does the lexical half of the check and
        the device does the resolving half. Neither side skips its half.
        """
        return ValidationContext(
            applications=self.applications,
            url_domains=self.url_domains,
            filesystem_roots=self.filesystem_roots,
            filesystem_available=False,
        )

    def describe(self) -> dict[str, Any]:
        """A non-sensitive summary, for the inspection surface."""
        return {
            "device_id": self.device_id,
            "tenant_id": self.tenant_id,
            "platform": self.platform,
            "enrolled": self.enrolled,
            "capabilities": [
                {"capability": c.kind.value, "version": c.version} for c in self.capabilities
            ],
            "application_keys": list(self.applications.keys),
            "url_domains": sorted(self.url_domains.hosts),
            "filesystem_roots": list(self.filesystem_roots.roots),
            "trusted_autonomy": sorted(k.value for k in self.trusted_autonomy),
        }


@runtime_checkable
class DeviceCapabilityRegistry(Protocol):
    """The whole of what Package B asks of a device registry. One method.

    Structural, not nominal, so Session E's registry satisfies it by having the
    method rather than by importing anything from here.

    `lookup` returns None when the tenant/device pair is not known at all, and
    an `EnrolledDevice` with `enrolled=False` when it is known but revoked --
    the difference is what lets a refusal say "that device is not enrolled here"
    rather than the less useful "unknown device".
    """

    def lookup(self, *, tenant_id: str, device_id: str) -> EnrolledDevice | None: ...


def _parse_capabilities(raw: Iterable[Any] | None) -> tuple[DeclaredCapability, ...]:
    declared: list[DeclaredCapability] = []
    for entry in raw or ():
        if isinstance(entry, str):
            # Shorthand: a bare kind means version 1, the only version this
            # build implements. Written out rather than inferred silently.
            kind, version = parse_kind(entry), 1
        elif isinstance(entry, dict):
            kind = parse_kind(str(entry.get("capability", "")))
            raw_version = entry.get("version", 1)
            if isinstance(raw_version, bool) or not isinstance(raw_version, int):
                raise UnsupportedCapabilityError(
                    f"capability version for {kind.value} must be an integer",
                )
            version = raw_version
        else:
            raise UnsupportedCapabilityError(
                f"a declared capability must be a string or an object, not "
                f"{type(entry).__name__}",
            )
        declared.append(DeclaredCapability(kind=kind, version=version))
    return tuple(declared)


def device_from_mapping(raw: dict[str, Any]) -> EnrolledDevice:
    """Build an `EnrolledDevice` from one enrolment record, or refuse it.

    Shared by the interim registry and by the tests, so an enrolment that the
    tests accept is exactly one the runtime accepts.
    """
    device_id = str(raw.get("device_id") or "").strip()
    tenant_id = str(raw.get("tenant_id") or "").strip()
    if not device_id or not tenant_id:
        raise DeviceRegistryError(
            "every enrolment record must name both a device_id and a tenant_id",
        )
    platform = str(raw.get("platform") or SUPPORTED_PLATFORM).strip().lower()
    try:
        return EnrolledDevice(
            device_id=device_id,
            tenant_id=tenant_id,
            platform=platform,
            enrolled=bool(raw.get("enrolled", True)),
            capabilities=_parse_capabilities(raw.get("capabilities")),
            applications=ApplicationAllowlist.from_pairs(raw.get("applications") or {}),
            url_domains=UrlDomainAllowlist.from_iterable(raw.get("url_domains") or ()),
            filesystem_roots=FilesystemRootAllowlist.from_iterable(
                raw.get("filesystem_roots") or (),
            ),
            trusted_autonomy=frozenset(
                parse_kind(str(k)) for k in (raw.get("trusted_autonomy") or ())
            ),
        )
    except (AllowlistError, UnsupportedCapabilityError) as e:
        raise DeviceRegistryError(f"enrolment for {device_id!r} is invalid: {e}") from e


class StaticDeviceRegistry:
    """The interim, operator-provisioned registry. Truthful about being interim.

    Reads a JSON file of enrolment records, re-reading it whenever its
    modification time changes so an operator can revoke a device without
    restarting the service. Fails closed in every direction: no file, an
    unreadable file, a malformed record and an unknown device all produce a
    denial, never a permissive default.

    **Not a substitute for Session E.** There is no enrolment ceremony here and
    no device key material: a device is "enrolled" because a human wrote it
    into a file, and the device channel's own resolver is what actually
    authenticates a connection. `describe()` says exactly that, and the health
    surface repeats it.
    """

    #: Reported wherever this registry is in use, so an operator can never be
    #: unsure whether the production registry has landed.
    LABEL = "static-file-registry (interim; Session E replaces this)"

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self._path = Path(path) if path else None
        self._cache: dict[tuple[str, str], EnrolledDevice] = {}
        self._stamp: tuple[float, int] | None = None
        self._loaded = False

    # -- loading ---------------------------------------------------------

    def _resolve_path(self) -> Path | None:
        if self._path is not None:
            return self._path
        configured = (os.getenv(ENROLMENT_PATH_ENV) or "").strip()
        return Path(configured) if configured else None

    def _load_if_changed(self) -> None:
        path = self._resolve_path()
        if path is None:
            self._cache = {}
            self._stamp = None
            self._loaded = True
            return
        try:
            stat = path.stat()
        except OSError as e:
            # A configured-but-unreadable enrolment file is not "no devices",
            # it is "we cannot tell", and telling a caller "not enrolled" would
            # be a truthful-sounding answer to a question we could not answer.
            raise DeviceRegistryError(
                f"the device enrolment file at {path} could not be read "
                f"({type(e).__name__}); refusing every device until it can be.",
            ) from e
        stamp = (stat.st_mtime, stat.st_size)
        if self._loaded and stamp == self._stamp:
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise DeviceRegistryError(
                f"the device enrolment file at {path} is not readable JSON "
                f"({type(e).__name__}); refusing every device until it is.",
            ) from e
        records = raw.get("devices") if isinstance(raw, dict) else raw
        if not isinstance(records, list):
            raise DeviceRegistryError(
                f"the device enrolment file at {path} must contain a list of devices, "
                "or an object with a 'devices' list.",
            )
        cache: dict[tuple[str, str], EnrolledDevice] = {}
        for record in records:
            if not isinstance(record, dict):
                raise DeviceRegistryError("every enrolment record must be an object")
            device = device_from_mapping(record)
            cache[(device.tenant_id, device.device_id)] = device
        self._cache = cache
        self._stamp = stamp
        self._loaded = True

    # -- the interface ---------------------------------------------------

    def lookup(self, *, tenant_id: str, device_id: str) -> EnrolledDevice | None:
        """The enrolment for this exact (tenant, device), or None.

        Tenant-qualified, so a device id known in one tenant is simply unknown
        in another. That is the whole of the cross-tenant containment at this
        layer, and it is one dictionary key rather than a predicate somebody
        has to remember to add.
        """
        self._load_if_changed()
        return self._cache.get((str(tenant_id), str(device_id)))

    def describe(self) -> dict[str, Any]:
        """What an operator or the health surface is told about this registry."""
        path = self._resolve_path()
        try:
            self._load_if_changed()
            error = None
        except DeviceRegistryError as e:
            error = str(e)
        return {
            "registry": self.LABEL,
            "enrolment_file": str(path) if path else None,
            "device_count": len(self._cache) if error is None else 0,
            "readable": error is None,
            "error": error,
            "interim": True,
            "replaced_by": "Session E device/group registry",
        }


class NoDeviceRegistry:
    """The default: nothing is enrolled, so nothing can be dispatched.

    Installed when no enrolment has been configured. It is a real registry that
    really answers, and its answer is always None -- which is the correct
    behaviour for a deployment that has not enrolled a device, and is very
    different from having no registry at all and skipping the check.
    """

    LABEL = "no-device-registry (nothing is enrolled)"

    def lookup(self, *, tenant_id: str, device_id: str) -> EnrolledDevice | None:
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "registry": self.LABEL,
            "enrolment_file": None,
            "device_count": 0,
            "readable": True,
            "error": None,
            "interim": True,
            "replaced_by": "Session E device/group registry",
        }


#: The installed registry, in a holder rather than a bare module global, so
#: installation mutates a container instead of rebinding a module attribute --
#: the same shape `governance_store._HALT_AUTHORITY` uses for its registration
#: hook, and for the same reason: a rebound attribute is invisible to a module
#: that imported the name.
_INSTALLED: dict[str, DeviceCapabilityRegistry | None] = {"registry": None}


def get_registry() -> DeviceCapabilityRegistry:
    """The installed registry, defaulting to the interim file-backed one.

    Never returns None: "no registry" would be an unanswered question, and the
    admission path must always have something to ask. With nothing configured,
    the answer is `NoDeviceRegistry`, which enrols nothing.
    """
    registry = _INSTALLED["registry"]
    if registry is None:
        registry = (
            StaticDeviceRegistry()
            if (os.getenv(ENROLMENT_PATH_ENV) or "").strip()
            else NoDeviceRegistry()
        )
        _INSTALLED["registry"] = registry
    return registry


def install_registry(registry: DeviceCapabilityRegistry | None) -> None:
    """Install a registry -- Session E's production one, or a test's.

    Passing None resets to the default resolution above, which is what a test
    fixture does on teardown.
    """
    _INSTALLED["registry"] = registry
    if registry is not None:
        logger.info(
            "Device capability registry installed: %s",
            getattr(registry, "LABEL", type(registry).__name__),
        )
