"""
EXEC-02: the explicit activation of Executive goal deliberation on the chat surface.

An adapter, like every module in this package. It owns no cognition, no
policy, no store and no vocabulary: it holds the three pieces of *platform
authority* the Executive requires and cannot read from a sentence — whose
Bartholomew this is, which machine is being talked about, and who is asking —
and it hangs them on the runtime context so `runtime_contract.py`'s chat
dispatch can reach the Executive that already exists.

Why this exists at all
----------------------
`bartholomew/executive/seam.py` takes `tenant_id`, `device_id` and
`requested_by` as required keyword arguments and has a default for none of
them. On the operator route those come from the HTTP request's own resolved
authority (`routes/operator.py`). A chat turn arrives at
`run_chat_through_runtime_contract()` with a daemon and a string and nothing
else, so the authority has to be *configured* rather than *derived* — and
configuring it is exactly the explicit operator decision this package is
required to preserve.

Two switches, and deliberately two
-----------------------------------
`BARTH_EXECUTIVE_DELIBERATION` installs EXEC-01's `DeliberationPort` (model
cognition inside the Executive, for every Executive caller including the
operator console). `BARTH_CONVERSATIONAL_EXECUTIVE` routes qualifying
conversational goals into the Executive at all.

They are separate because they widen different things, and neither is implied
by the other:

* deliberation **without** conversational routing is EXEC-01's existing
  posture, reachable only from the operator console;
* conversational routing **without** deliberation is a genuine and safe
  configuration — chat reaches the Executive, the Executive reads instructions
  literally, and an outcome it cannot recognise becomes a question rather than
  a plan.

Both default **off**, and — the invariant that matters — *a model being
reachable enables neither*. `install_deliberation_port` has always refused to
be automatic, and this module does not make it so: a router being present is a
precondition here, never a trigger.

What activation does not do
----------------------------
It does not grant anything. Every action the Executive proposes from a
conversational goal goes through the same action envelope, the same Parking
Brake, the same capability validation, the same device allowlists, the same
approval requirement and the same independent verification as one proposed
from the operator console. Turning this on changes what Bartholomew will
*think about*. It changes nothing about what he may *do*.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Enables conversational Executive goal routing. Off unless explicitly set.
ENV_ENABLED = "BARTH_CONVERSATIONAL_EXECUTIVE"
#: The machine a conversational goal is about. **Required**: there is no
#: default, and activation is refused without one. A device id guessed from a
#: registry would mean a sentence typed into chat could end up planning against
#: a machine nobody named.
ENV_DEVICE_ID = "BARTH_CONVERSATIONAL_EXECUTIVE_DEVICE_ID"
#: Whose Bartholomew this is, and who is asking. Unlike the device id, neither
#: is a choice between machines: the tenant is resolved from the platform's own
#: authority (see `resolve_tenant_id`) and these variables are overrides for a
#: deployment that genuinely needs one, not the normal path.
ENV_TENANT_ID = "BARTH_CONVERSATIONAL_EXECUTIVE_TENANT_ID"
ENV_REQUESTED_BY = "BARTH_CONVERSATIONAL_EXECUTIVE_REQUESTED_BY"

#: Enables EXEC-01's cognition port. Independent of the above; see the module
#: docstring for why the two are not one switch.
ENV_DELIBERATION = "BARTH_EXECUTIVE_DELIBERATION"

#: The sentinel tenant for an unbound process --- the single-runtime local
#: deployment, where there is exactly one tenant.
#:
#: **This must equal `device_action_auth.LOCAL_TENANT`, and a test pins that it
#: does.** It is duplicated rather than imported because `bartholomew/` must not
#: acquire an import edge to the API bridge. It is not a second opinion about
#: what the tenant is: `resolve_tenant_id` below reaches the same answer the
#: operator route reaches, by the same reads, in the same order.
#:
#: Getting this wrong is not cosmetic. A device enrolled through the platform is
#: tenant-qualified, so planning a conversational goal under some other string
#: would report a normally-enrolled machine as **not enrolled** --- the feature
#: would look configured and refuse everything. An earlier cut of this module
#: used a synthetic "default" here and did exactly that.
LOCAL_TENANT = "local"
DEFAULT_REQUESTED_BY = "chat"

#: The attribute the chat dispatch reads. Absent means "not configured", which
#: is the default and which produces exactly the pre-EXEC-02 chat turn.
CTX_ATTRIBUTE = "conversational_executive"

_TRUE = frozenset({"1", "true", "yes", "on"})


def _flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in _TRUE


def resolve_tenant_id() -> str:
    """Whose Bartholomew this is --- the same answer the operator route reaches.

    `routes/operator.py` resolves the tenant through
    `device_action_auth.resolved_tenant_id(request)`, which reads the verified
    principal on the request and then this process's own runtime binding,
    falling back to the `local` sentinel for an unbound process. A chat turn has
    no request and therefore no principal, so the two reads available here are
    the binding and the sentinel --- which is exactly what that function does
    for a call carrying no principal.

    An explicit environment override is honoured first, for a deployment whose
    devices are enrolled under some other tenant string. It is an override, not
    the normal path: leaving it unset is correct.
    """
    override = (os.getenv(ENV_TENANT_ID) or "").strip()
    if override:
        return override

    try:
        from bartholomew.platform.runtime_registry import bound_runtime_user_id

        bound = bound_runtime_user_id()
    except Exception:  # noqa: BLE001 - an unreadable binding is an unbound one
        logger.exception("Could not read this process's runtime binding")
        bound = None

    return bound or LOCAL_TENANT


@dataclass(frozen=True)
class ConversationalExecutive:
    """The platform authority a conversational goal is planned under.

    Frozen, and carrying nothing else. In particular it holds no policy, no
    capability list and no permission: it is the answer to "on whose behalf,
    against which machine, at whose request", which the action envelope then
    checks for real.
    """

    tenant_id: str
    device_id: str
    requested_by: str = DEFAULT_REQUESTED_BY

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "device_id", "requested_by"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"conversational Executive activation needs a real {field_name}; "
                    "refusing to plan against an unnamed one",
                )


def install_conversational_executive(
    ctx: Any,
    *,
    tenant_id: str,
    device_id: str,
    requested_by: str = DEFAULT_REQUESTED_BY,
) -> ConversationalExecutive:
    """Hang the activation on a runtime context. Explicit by construction.

    Returns the installed configuration. Raises `ValueError` rather than
    installing a half-specified one: a partially-configured activation is the
    state in which a goal gets planned against something nobody chose.
    """
    config = ConversationalExecutive(
        tenant_id=tenant_id,
        device_id=device_id,
        requested_by=requested_by,
    )
    setattr(ctx, CTX_ATTRIBUTE, config)
    return config


def resolve(ctx: Any) -> ConversationalExecutive | None:
    """The activation on this context, or None.

    `None` is the default and the overwhelmingly common answer. It is read
    through `getattr` with a default, so every existing duck-typed runtime
    context — and every test context written before EXEC-02 — is unaffected.
    """
    config = getattr(ctx, CTX_ATTRIBUTE, None)
    return config if isinstance(config, ConversationalExecutive) else None


def configure_from_environment(ctx: Any, router: Any = None) -> dict[str, Any]:
    """Apply both EXEC-02 switches to a real runtime, and report what happened.

    Best-effort by design and never raises: a misconfigured activation must
    degrade to *today's behaviour*, not to a failed startup. The returned
    record is the operator's account of which posture the process actually
    came up in — an activation that silently did not happen is the worst of
    the available outcomes, so it is always stated.
    """
    report: dict[str, Any] = {
        "deliberation_installed": False,
        "conversational_executive_enabled": False,
        "reasons": [],
    }

    if _flag(ENV_DELIBERATION):
        if router is None:
            report["reasons"].append(
                f"{ENV_DELIBERATION} is set but no model router exists; the Executive "
                "will read instructions literally",
            )
        else:
            try:
                from bartholomew.integration.deliberation_adapter import (
                    install_deliberation_port,
                )

                installed = install_deliberation_port(ctx, router)
                report["deliberation_installed"] = installed is not None
            except Exception as error:  # noqa: BLE001 - startup must not fail closed to a crash
                logger.exception("Could not install the Executive deliberation port")
                report["reasons"].append(f"the deliberation port could not be installed: {error}")
    else:
        report["reasons"].append(
            f"{ENV_DELIBERATION} is not set; Executive cognition stays off and "
            "instructions are read literally",
        )

    if not _flag(ENV_ENABLED):
        report["reasons"].append(
            f"{ENV_ENABLED} is not set; conversational goals stay ordinary conversation",
        )
        return report

    device_id = (os.getenv(ENV_DEVICE_ID) or "").strip()
    if not device_id:
        report["reasons"].append(
            f"{ENV_ENABLED} is set but {ENV_DEVICE_ID} is not; refusing to plan "
            "conversational goals against an unnamed machine",
        )
        return report

    tenant_id = resolve_tenant_id()
    try:
        install_conversational_executive(
            ctx,
            tenant_id=tenant_id,
            device_id=device_id,
            requested_by=(os.getenv(ENV_REQUESTED_BY) or "").strip() or DEFAULT_REQUESTED_BY,
        )
    except Exception as error:  # noqa: BLE001 - see the docstring
        logger.exception("Could not enable the conversational Executive")
        report["reasons"].append(f"conversational Executive activation failed: {error}")
        return report

    report["conversational_executive_enabled"] = True
    report["device_id"] = device_id
    # Named on the startup line, because "which tenant are conversational goals
    # planned under" is the question whose wrong answer refuses every enrolled
    # device while still looking configured.
    report["tenant_id"] = tenant_id
    return report


__all__ = [
    "CTX_ATTRIBUTE",
    "DEFAULT_REQUESTED_BY",
    "LOCAL_TENANT",
    "ENV_DELIBERATION",
    "ENV_DEVICE_ID",
    "ENV_ENABLED",
    "ENV_REQUESTED_BY",
    "ENV_TENANT_ID",
    "ConversationalExecutive",
    "configure_from_environment",
    "install_conversational_executive",
    "resolve",
    "resolve_tenant_id",
]
