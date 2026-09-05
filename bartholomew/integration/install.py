"""The one place the Session F seams are put in place, and the one place to look.

Five packages each left a documented seam and a fail-closed default. This
module installs the real implementation for each, and it is deliberately a
single function with a single report, so "is this deployment actually
integrated, or is it running on stand-ins?" has one answer rather than five.

Ordering is not incidental. The device registry goes in before anything that
resolves a device, and the event sink before anything that produces an event,
so there is no window in which a capability check or an observation could be
answered by a stand-in that a later line was about to replace.

Nothing here is enabled by a side effect of importing it. `install_seams()`
is called from application startup, and every part of it that opens a channel
to the outside world -- specifically the action-channel resolver -- stays
behind its own explicit environment gate. Installing the seams makes the
system coherent; it does not make it permissive.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SeamReport:
    """What was installed, what was not, and why. Rendered on the health surface."""

    device_registry: str = "not installed"
    capability_resolver: str = "not installed"
    multimodal_sink: str = "not installed"
    action_resolver: str = "closed"
    event_types: tuple[str, ...] = ()
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_registry": self.device_registry,
            "capability_resolver": self.capability_resolver,
            "multimodal_sink": self.multimodal_sink,
            "action_resolver": self.action_resolver,
            "event_types": list(self.event_types),
            "errors": list(self.errors),
            "integrated": not self.errors,
        }


#: The last report, so `/api/health` can say which seams are live without
#: re-running an installation as a side effect of a health probe.
_LAST_REPORT: dict[str, SeamReport | None] = {"report": None}


def last_report() -> SeamReport | None:
    return _LAST_REPORT["report"]


def install_seams(
    *,
    db_path: str,
    tenant_id: str | None = None,
    platform_db_path: str | None = None,
) -> SeamReport:
    """Put every Session F seam in place. Returns what happened.

    `tenant_id` is this process's runtime binding. Where it is absent the
    seams that *require* a tenant to be safe are not installed at all, and the
    package default -- which refuses -- stays in force. That is the correct
    outcome for an unbound process: there is no tenant whose devices could be
    resolved, so resolving none is right.
    """
    report = SeamReport()

    # -- A + E: one device truth -----------------------------------------
    #
    # One truth per deployment, chosen explicitly by the operator, rather than
    # one truth per device resolved by whichever source answered first.
    #
    # Package B ships a real, supported alpha configuration in which
    # `BARTH_ACTION_DEVICE_ENROLMENT` names a file that *is* the device
    # registry. Overriding that with Session E's registry would silently
    # unenrol every device an operator had configured that way -- the file
    # would still be read for allowlists, and the deployment would look
    # configured while refusing everything.
    #
    # So a deployment that has named an interim enrolment file keeps it, and
    # says so on the health surface (`interim: true`, and the registry names
    # what replaces it). A deployment that has not gets Session E's registry.
    # Having both would be two contradictory answers to "which devices are
    # enrolled", which is the thing this seam exists to prevent.
    try:
        import os

        from bartholomew.actuation import devices as actuation_devices
        from bartholomew.actuation.devices import ENROLMENT_PATH_ENV
        from bartholomew.integration.device_registry import RegistryBackedDeviceRegistry

        if (os.getenv(ENROLMENT_PATH_ENV) or "").strip():
            interim = actuation_devices.get_registry()
            report.device_registry = (
                f"{getattr(interim, 'LABEL', type(interim).__name__)} "
                f"-- kept because {ENROLMENT_PATH_ENV} names an enrolment file"
            )
        else:
            registry = RegistryBackedDeviceRegistry(db_path=platform_db_path)
            actuation_devices.install_registry(registry)
            report.device_registry = registry.LABEL
    except Exception as e:  # noqa: BLE001 - a failed seam is reported, not fatal
        # Deliberately not re-raised: failing to install leaves B's own
        # fail-closed default in place, which enrols nothing. A startup that
        # dies here would be a worse outcome than one that actuates nothing
        # and says so.
        report.errors.append(f"device registry: {type(e).__name__}: {e}")
        logger.exception("Could not install the device capability registry")

    # -- E -> C: the same device truth answers multimodal ------------------
    if tenant_id:
        try:
            from bartholomew.integration.device_registry import (
                RegistryBackedCapabilityResolver,
            )

            resolver = RegistryBackedCapabilityResolver(
                tenant_id=tenant_id,
                db_path=platform_db_path,
            )
            _install_multimodal_resolver(resolver)
            report.capability_resolver = f"platform-device-registry (tenant {tenant_id})"
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"capability resolver: {type(e).__name__}: {e}")
            logger.exception("Could not install the multimodal capability resolver")
    else:
        report.capability_resolver = (
            "not installed: this process has no runtime binding, so there is no "
            "tenant whose devices could be resolved (fail-closed)"
        )

    # -- C -> A: one event bus --------------------------------------------
    try:
        from bartholomew.integration.multimodal_events import CanonicalIngressSink
        from bartholomew.kernel.event_processing.registry import registered_types

        sink = CanonicalIngressSink(db_path=db_path, runtime_id=tenant_id)
        _install_multimodal_sink(sink)
        report.multimodal_sink = "canonical ingress (inbound_events)"
        report.event_types = tuple(t for t in registered_types() if t.startswith("multimodal."))
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"multimodal sink: {type(e).__name__}: {e}")
        logger.exception("Could not install the multimodal event sink")

    # -- B: the action channel, behind its own gate -----------------------
    try:
        from bartholomew.integration.device_action_resolver import (
            maybe_install_action_resolver_from_env,
        )

        if maybe_install_action_resolver_from_env():
            report.action_resolver = "open (registry device credentials)"
        else:
            report.action_resolver = "closed (no action-channel resolver configured)"
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"action resolver: {type(e).__name__}: {e}")
        report.action_resolver = "closed (installation failed)"
        logger.exception("Could not install the device action resolver")

    _LAST_REPORT["report"] = report
    if report.errors:
        logger.warning("Session F seams installed with %d error(s)", len(report.errors))
    else:
        logger.info("Session F seams installed: %s", report.to_dict())
    return report


def _install_multimodal_resolver(resolver: Any) -> None:
    """Install C's capability resolver wherever C reads it from."""
    from bartholomew.multimodal import runtime as multimodal_runtime

    multimodal_runtime.install_capability_resolver(resolver)


def _install_multimodal_sink(sink: Any) -> None:
    """Install C's event sink wherever C reads it from."""
    from bartholomew.multimodal import events as multimodal_events

    multimodal_events.install_event_sink(sink)


__all__ = ["SeamReport", "install_seams", "last_report"]
