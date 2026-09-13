"""What Bartholomew can actually do, described well enough to reason with.

`selection.py` answers one question --- *may this device be asked for the
capability the person already named?* --- and answers it well. Deliberation asks
the question the other way round: *given an outcome, which of the things I can
actually do would establish it?* That question cannot be answered from a
capability identifier alone. It needs the power described, its parameters named,
its cost recorded, and --- decisively --- it needs to be the set this machine
really declares rather than the set this build implements.

This module builds that description. It adds no third notion of what a
capability is: the vocabulary and its risk/approval facts are
`bartholomew.actuation.capabilities`, availability is
`selection.select_capability`, and the allowlists are the device's own
enrolment. Everything here is a *rendering* of facts that already exist.

Three properties this module exists to hold
--------------------------------------------

1. **Nothing unavailable is offered.** A capability this device did not declare
   is present in the catalogue as unavailable, with the reason, and is never
   rendered into the text cognition reads. A model cannot propose a capability
   it was never shown, and if it proposes one anyway `deliberation.py` refuses
   it against this same catalogue.

2. **The description is bounded, and it is of the *power*, not of the machine.**
   The summary is `CapabilityDescriptor.summary` --- the sentence written for an
   approver to read. Allowlist *keys* are included because an executive that
   cannot see that `notepad` is available cannot propose starting it; the
   executable *paths* those keys resolve to are not, because nothing reasoning
   about an outcome needs them and they are the device operator's configuration.

3. **It describes; it does not validate.** The parameter specifications below
   exist so cognition knows what shape to propose. They are deliberately *not*
   a second copy of `parameters.py`'s rules, and nothing downstream trusts them:
   every proposed parameter set is put through the real
   `bartholomew.actuation.parameters.validate()` against the device's own
   `ValidationContext` before it can become a step. A description that drifted
   from the validator would cost a refusal, never a wrong action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bartholomew.actuation.capabilities import (
    ALL_CAPABILITIES,
    CapabilityKind,
    describe,
)
from bartholomew.actuation.parameters import (
    ACCESSIBILITY_OPERATIONS,
    MAX_CLIPBOARD_CHARS,
    MAX_ELEMENT_NAME_CHARS,
    MAX_PATH_CHARS,
    MAX_TYPED_CHARS,
    MAX_URL_CHARS,
    WINDOW_OPERATIONS,
)

from .selection import select_capability

#: How many allowlist entries of one kind are rendered for cognition. A bound
#: rather than a policy: an enrolment with four hundred allowlisted applications
#: should not turn into four hundred lines of prompt, and a catalogue that grew
#: without limit would be a catalogue whose cost grew with somebody else's
#: configuration. The count of what was withheld is always stated, so a model
#: reading a truncated list is told that it is truncated.
MAX_RENDERED_ALLOWLIST_ENTRIES = 40


@dataclass(frozen=True)
class ParameterSpec:
    """One parameter of one capability, described for a reasoner.

    `constraint` is prose, not a schema fragment, and that is deliberate: the
    authority on shape is `parameters.py`, and expressing the rule twice in two
    languages is how the two drift. This tells cognition what to aim at; the
    validator decides whether it hit.
    """

    name: str
    required: bool
    constraint: str
    choices: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        described: dict[str, Any] = {
            "name": self.name,
            "required": self.required,
            "constraint": self.constraint,
        }
        if self.choices:
            described["choices"] = list(self.choices)
        return described


#: What each capability takes. Mirrors `parameters.py`'s validators in prose so
#: cognition can aim at the right shape; see property 3 in the module docstring
#: for why a mirror is safe here and a second validator would not be.
_PARAMETERS: dict[CapabilityKind, tuple[ParameterSpec, ...]] = {
    CapabilityKind.OPEN_URL: (
        ParameterSpec(
            name="url",
            required=True,
            constraint=(
                f"one http or https URL, at most {MAX_URL_CHARS} characters, whose host "
                "is one of the allowlisted domains below. No other scheme, and no "
                "credentials embedded in the URL."
            ),
        ),
    ),
    CapabilityKind.OPEN_PATH: (
        ParameterSpec(
            name="path",
            required=True,
            constraint=(
                f"one absolute Windows path, at most {MAX_PATH_CHARS} characters, that "
                "lies inside one of the allowlisted filesystem roots below. The file or "
                "folder must already exist: this opens, it never creates."
            ),
        ),
    ),
    CapabilityKind.LAUNCH_APP: (
        ParameterSpec(
            name="app_id",
            required=True,
            constraint=(
                "one key from the allowlisted application keys below, exactly as "
                "spelled there. Not a path, not an executable name, and no arguments."
            ),
        ),
    ),
    CapabilityKind.FOCUS_WINDOW: (
        ParameterSpec(
            name="app_id",
            required=True,
            constraint=(
                "one key from the allowlisted application keys below. The window must "
                "already be open; this does not start anything."
            ),
        ),
    ),
    CapabilityKind.MANAGE_WINDOW: (
        ParameterSpec(
            name="app_id",
            required=True,
            constraint="one key from the allowlisted application keys below.",
        ),
        ParameterSpec(
            name="operation",
            required=True,
            constraint="which window operation to perform.",
            choices=tuple(WINDOW_OPERATIONS),
        ),
    ),
    CapabilityKind.CLIPBOARD_READ: (),
    CapabilityKind.CLIPBOARD_WRITE: (
        ParameterSpec(
            name="text",
            required=True,
            constraint=(
                f"ordinary text, at most {MAX_CLIPBOARD_CHARS} characters, containing "
                "nothing that looks like a credential."
            ),
        ),
    ),
    CapabilityKind.TYPE_TEXT: (
        ParameterSpec(
            name="text",
            required=True,
            constraint=(
                f"ordinary text, at most {MAX_TYPED_CHARS} characters. It cannot contain "
                "Enter, Tab or any control character, so it cannot press Send, Submit or "
                "Confirm. It goes into whatever control currently has focus, so a step "
                "that establishes the right focus must come first."
            ),
        ),
    ),
    CapabilityKind.ACCESSIBILITY_ACTION: (
        ParameterSpec(
            name="app_id",
            required=True,
            constraint="one key from the allowlisted application keys below.",
        ),
        ParameterSpec(
            name="operation",
            required=True,
            constraint="which accessibility operation to perform.",
            choices=tuple(ACCESSIBILITY_OPERATIONS),
        ),
        ParameterSpec(
            name="element_name",
            required=False,
            constraint=(
                f"the exact name of the control, at most {MAX_ELEMENT_NAME_CHARS} "
                "characters. Required for expand, collapse and focus_element."
            ),
        ),
    ),
}


@dataclass(frozen=True)
class CapabilityOffer:
    """One capability, as cognition is allowed to see it."""

    capability: str
    summary: str
    risk: str
    approval_requirement: str
    version: int | None
    available: bool
    parameters: tuple[ParameterSpec, ...] = ()
    unavailable_reason: str | None = None
    #: True when this build can read the resulting state back and check it.
    #: Read for *preference* only --- a plan whose steps can be verified is a
    #: better plan --- never for permission. `verification.py` decides what
    #: actually happened, and it is not consulted here.
    outcome_verifiable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "summary": self.summary,
            "risk": self.risk,
            "approval_requirement": self.approval_requirement,
            "version": self.version,
            "available": self.available,
            "parameters": [p.as_dict() for p in self.parameters],
            "unavailable_reason": self.unavailable_reason,
            "outcome_verifiable": self.outcome_verifiable,
        }


#: Capabilities whose effect this build can read back off the machine. Focus and
#: window management change window state, which W03-A's read-back observes.
_OUTCOME_VERIFIABLE = frozenset(
    {
        CapabilityKind.LAUNCH_APP,
        CapabilityKind.FOCUS_WINDOW,
        CapabilityKind.MANAGE_WINDOW,
    },
)


def _truncate(values: tuple[str, ...]) -> tuple[tuple[str, ...], int]:
    """The rendered head of a list, and how many entries were withheld."""
    if len(values) <= MAX_RENDERED_ALLOWLIST_ENTRIES:
        return values, 0
    return values[:MAX_RENDERED_ALLOWLIST_ENTRIES], len(values) - MAX_RENDERED_ALLOWLIST_ENTRIES


@dataclass(frozen=True)
class CapabilityCatalogue:
    """Everything available on one device, plus what its allowlists permit.

    Constructed by `build_catalogue`. Cognition reads `render()`; deterministic
    validation reads `offer()`. Both read the same object, which is the point:
    the set a model is shown and the set a proposal is checked against cannot
    drift apart, because there is only one of them.
    """

    offers: tuple[CapabilityOffer, ...] = ()
    application_keys: tuple[str, ...] = ()
    url_domains: tuple[str, ...] = ()
    filesystem_roots: tuple[str, ...] = ()
    device_resolved: bool = False

    @property
    def available(self) -> tuple[CapabilityOffer, ...]:
        """Only what this device really declares. The set cognition may use."""
        return tuple(o for o in self.offers if o.available)

    def offer(self, capability: str) -> CapabilityOffer | None:
        """The offer for a capability identifier, or None if there is no such thing."""
        for candidate in self.offers:
            if candidate.capability == capability:
                return candidate
        return None

    def is_available(self, capability: str) -> bool:
        found = self.offer(capability)
        return bool(found and found.available)

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_resolved": self.device_resolved,
            "capabilities": [o.as_dict() for o in self.offers],
            "application_keys": list(self.application_keys),
            "url_domains": list(self.url_domains),
            "filesystem_roots": list(self.filesystem_roots),
        }

    def render(self) -> str:
        """The bounded description cognition reads.

        Only available capabilities appear. A capability the device did not
        declare is not described as unavailable here --- it is simply absent,
        because a model told about a power it cannot use is a model being
        invited to ask for it.
        """
        lines: list[str] = []
        if not self.device_resolved:
            return (
                "No enrolled device was resolved, so there are no capabilities "
                "available at all. Nothing can be proposed."
            )
        if not self.available:
            return (
                "This device is enrolled but declares no capabilities this build "
                "implements. Nothing can be proposed."
            )

        lines.append("CAPABILITIES AVAILABLE ON THIS DEVICE")
        lines.append(
            "These are the only actions that exist. There is no other capability, and "
            "anything not on this list cannot be done at all --- it is not unimplemented, "
            "it is inexpressible.",
        )
        lines.append("")
        for offer in self.available:
            lines.append(f"- {offer.capability}")
            lines.append(f"    what it does: {offer.summary}")
            lines.append(
                f"    risk: {offer.risk} | approval: {offer.approval_requirement} | "
                f"outcome can be checked afterwards: "
                f"{'yes' if offer.outcome_verifiable else 'no'}",
            )
            if offer.parameters:
                for spec in offer.parameters:
                    requirement = "required" if spec.required else "optional"
                    line = f"    parameter {spec.name} ({requirement}): {spec.constraint}"
                    if spec.choices:
                        line += f" One of: {', '.join(spec.choices)}."
                    lines.append(line)
            else:
                lines.append("    parameters: none")
            lines.append("")

        lines.append("WHAT THIS DEVICE'S ALLOWLISTS PERMIT")
        for label, values in (
            ("application keys (for app_id)", self.application_keys),
            ("URL domains (for url)", self.url_domains),
            ("filesystem roots (for path)", self.filesystem_roots),
        ):
            shown, withheld = _truncate(values)
            if not values:
                lines.append(f"- {label}: none. Any step needing one is impossible.")
                continue
            rendered = ", ".join(shown)
            if withheld:
                rendered += f" (and {withheld} more not shown)"
            lines.append(f"- {label}: {rendered}")
        return "\n".join(lines)


def build_catalogue(device: Any) -> CapabilityCatalogue:
    """Describe what `device` can actually be asked for.

    `device` is an `EnrolledDevice`, or anything with the same surface. `None`
    is not permissive: with no device nothing declared anything, every offer is
    unavailable, and `render()` says so in one sentence.

    Availability is `select_capability`'s verdict rather than a second reading
    of the enrolment, so a capability this catalogue offers is exactly a
    capability `plan.py` will accept, including the version check.
    """
    offers: list[CapabilityOffer] = []
    for kind in ALL_CAPABILITIES:
        descriptor = describe(kind)
        selection = select_capability(kind.value, device)
        offers.append(
            CapabilityOffer(
                capability=kind.value,
                summary=descriptor.summary,
                risk=selection.risk or descriptor.risk.value,
                approval_requirement=(selection.approval_requirement or descriptor.approval.value),
                version=selection.version,
                available=selection.available,
                parameters=_PARAMETERS.get(kind, ()),
                unavailable_reason=selection.refusal_reason,
                outcome_verifiable=kind in _OUTCOME_VERIFIABLE,
            ),
        )

    applications: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    roots: tuple[str, ...] = ()
    if device is not None:
        applications = tuple(getattr(getattr(device, "applications", None), "keys", ()) or ())
        domains = tuple(sorted(getattr(getattr(device, "url_domains", None), "hosts", ()) or ()))
        roots = tuple(getattr(getattr(device, "filesystem_roots", None), "roots", ()) or ())

    return CapabilityCatalogue(
        offers=tuple(offers),
        application_keys=applications,
        url_domains=domains,
        filesystem_roots=roots,
        device_resolved=device is not None,
    )


__all__ = [
    "MAX_RENDERED_ALLOWLIST_ENTRIES",
    "CapabilityCatalogue",
    "CapabilityOffer",
    "ParameterSpec",
    "build_catalogue",
]
