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
to the outside world -- the action-channel resolver, and the External
Capability Interface's endpoint channel -- stays behind its own explicit
environment gate. Installing the seams makes the system coherent; it does not
make it permissive.
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
    #: FND-04. Two independent facts, kept apart because they fail
    #: independently: whether the boundary will admit an endpoint at all, and
    #: whether Bartholomew has a seam able to answer one.
    eci_endpoint_channel: str = "closed"
    eci_responder: str = "not installed"
    event_types: tuple[str, ...] = ()
    #: The Wave 3 cross-package adapters (`integration/seams.py`), keyed by
    #: seam name. Reported alongside the wave-two seams so "is this deployment
    #: actually integrated" still has ONE answer rather than two.
    w03_seams: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_registry": self.device_registry,
            "capability_resolver": self.capability_resolver,
            "multimodal_sink": self.multimodal_sink,
            "action_resolver": self.action_resolver,
            "eci_endpoint_channel": self.eci_endpoint_channel,
            "eci_responder": self.eci_responder,
            "event_types": list(self.event_types),
            "w03_seams": dict(self.w03_seams),
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
    runtime_cfg: dict[str, Any] | None = None,
) -> SeamReport:
    """Put every Session F seam in place. Returns what happened.

    `tenant_id` is this process's runtime binding. Where it is absent the
    seams that *require* a tenant to be safe are not installed at all, and the
    package default -- which refuses -- stays in force. That is the correct
    outcome for an unbound process: there is no tenant whose devices could be
    resolved, so resolving none is right.

    `runtime_cfg` is the loaded kernel config. It is passed through to the
    External Capability Interface responder so that `voice.spoken_output` is
    read from `config/kernel.yaml` rather than from a second opinion about
    where enablement lives. With none, `spoken_output.enabled_for(None)`
    returns False and the responder issues nothing -- the right answer for a
    caller that has no kernel config.
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
        # W03-F: the unbound rule, which W03-A §5.1 explicitly left to this
        # session to decide ("the live retest should run bound, or W03-F should
        # decide the unbound rule in install.py").
        #
        # DECISION: the rule is unchanged --- an unbound process resolves no
        # devices --- and what changes is that it now says what that COSTS.
        # Installing a resolver here would mean W03-F deciding, on its own
        # authority, that an unbound process may resolve a device's capability to
        # be asked to observe a person's screen. This module's own docstring
        # rules that out: "Installing the seams makes the system coherent; it
        # does not make it permissive."
        #
        # But silence was the real defect. Without the resolver
        # `resolve_modality_capability` fails closed, so NO observation session
        # can start; and because W03-A's read-back requires an active consented
        # session, the server-side verify verdict is then always UNVERIFIABLE
        # too. On an unbound deployment the Observe and Verify halves of the
        # Wave 3 loop are inert while the Act half works --- which is safe, and
        # is exactly the kind of thing an operator must be told rather than
        # discover during an acceptance test.
        #
        # The recorded live procedure (docs/G §8) binds BARTH_RUNTIME_USER_ID, so
        # a bound process is the intended live configuration; this names the
        # variable so the fix is one line rather than an investigation.
        report.capability_resolver = (
            "not installed: this process has no runtime binding, so there is no "
            "tenant whose devices could be resolved (fail-closed). No observation "
            "session can start, and the server-side verify verdict is therefore "
            "always 'unverifiable'. Set BARTH_RUNTIME_USER_ID to bind this process "
            "to an account (docs/G §8) if the Observe/Verify half of the loop is "
            "needed."
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

    # -- FND-04: the External Capability Interface -------------------------
    #
    # Two separate installs, in this order, because they are two separate
    # decisions and either may be made without the other.
    #
    # The responder goes in FIRST. It is what lets Bartholomew *answer* an
    # exchange, and installing it changes nothing about who may reach the
    # boundary -- the spoken-output seam it delegates to still runs its own
    # enablement, brake and policy gates, and with `voice.spoken_output` off
    # (the default) it produces no directive at all. Installing it before the
    # channel means there is no window in which an endpoint could be admitted
    # by a boundary that had no seam able to answer it.
    #
    # The endpoint channel goes in second and stays behind its own explicit
    # environment gate, exactly as the action channel does. Opening a boundary
    # to external endpoints is a deployment decision, not a consequence of
    # integrating the system.
    try:
        from bartholomew.integration.eci_responder import install_eci_responder

        report.eci_responder = install_eci_responder(
            db_path=db_path,
            runtime_cfg=runtime_cfg,
        )
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"eci responder: {type(e).__name__}: {e}")
        logger.exception("Could not install the External Capability Interface responder")

    try:
        from bartholomew.eci import endpoint_auth

        if endpoint_auth.maybe_install_from_env(db_path=platform_db_path):
            report.eci_endpoint_channel = "open (registry device credentials)"
        else:
            report.eci_endpoint_channel = (
                f"closed (set {endpoint_auth.ENDPOINT_AUTH_ENV} to open the boundary "
                "to enrolled endpoints)"
            )
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"eci endpoint channel: {type(e).__name__}: {e}")
        report.eci_endpoint_channel = "closed (installation failed)"
        logger.exception("Could not open the External Capability Interface channel")

    # -- W03: the Wave 3 cross-package adapters ---------------------------
    #
    # Last, because they plug packages into each other rather than into the
    # platform: the read-back provider is only useful once the device truth and
    # the event sink above are in place. `install_w03_seams` reports rather
    # than raises, on the same reasoning as every seam above -- a deployment
    # that cannot verify is worse than one that cannot start only if it also
    # claims to have verified, and W03-C's `verify_effect()` answers
    # UNVERIFIABLE with no provider installed.
    try:
        from bartholomew.integration.seams import install_w03_seams

        report.w03_seams = install_w03_seams(db_path=db_path)
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"w03 seams: {type(e).__name__}: {e}")
        logger.exception("Could not install the Wave 3 cross-package seams")

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
