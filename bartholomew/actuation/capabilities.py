"""The complete, closed vocabulary of what Bartholomew may do to a Windows PC.

Nine kinds. Each is typed, each is versioned, and the set is a closed `Enum`
rather than a string that something later parses. That is the whole point: a
capability Bartholomew does not have is not "unimplemented", it is
**inexpressible** -- there is no value of `CapabilityKind` that means "run a
command", so no request, no model output and no compromised caller can ask for
one.

**Versions are refused, not approximated.** A device declares the capability
kinds *and versions* it supports (see `devices.py`). A request naming a version
this build does not implement is refused with
`UnsupportedCapabilityError`; it is never downgraded to a version that "should
be close enough". A downgrade would mean executing parameters under a contract
neither side agreed to, which is precisely the class of bug that turns a
narrow capability into a broad one.

Risk posture
------------
Each kind carries two orthogonal facts:

* `RiskClass`           -- how much a mistake costs. Recorded on every request
                           and every audit row, so an audit can be filtered by
                           consequence rather than by capability name.
* `ApprovalRequirement` -- whether a human must approve *this exact action*,
                           and whether the kind is even eligible to be placed
                           under configured trusted autonomy later.

`ApprovalRequirement.ALWAYS` is not a default that configuration can move. The
three kinds that carry it (`clipboard_read`, `type_text`,
`accessibility_action`) read or synthesise content on the person's behalf, and
`trusted_autonomy_eligible()` returns False for them, so
`devices.EnrolledDevice` refuses to carry them in a trusted-autonomy set at
all. There is no configuration file that makes typing text autonomous.

The other six require an approval today. Three of them
(`launch_app`, `focus_window`, `manage_window`) are *eligible* to be granted
configured, per-device trusted autonomy; the eligibility is recorded here so
enabling it later is an explicit, reviewable change to a device's enrolment
rather than a new code path. The remaining three (`open_url`, `open_path`,
`clipboard_write`) are deliberately not eligible in this build: each was
described as lowering its risk only once a further control exists (a domain
allowlist that is trusted rather than merely present, a narrower filesystem
root), and encoding the eligibility before the control would be encoding an
intention as a permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: The only capability contract version this build implements. Bumping it is a
#: deliberate, reviewable act: a device declaring a different version is
#: refused rather than served a best-effort translation.
CURRENT_CAPABILITY_VERSION = 1


class UnsupportedCapabilityError(ValueError):
    """A capability kind or version this build does not implement.

    Its own type so the refusal is greppable and can never be collapsed into
    an ordinary validation error by a broad `except ValueError`. Unknown means
    refused; it never means "use the nearest thing".
    """


class CapabilityKind(str, Enum):
    """Every action that can be asked for. A closed set, by design.

    Deliberately absent, and absent structurally rather than by policy: any
    form of command, script, shell or interpreter execution; any arbitrary
    executable path; file creation, deletion, movement, renaming or editing;
    software installation; sending a message; submitting a form; publishing;
    a purchase; an account or security change; credential entry; control of a
    machine that is not the enrolled device; and any non-Windows platform.
    """

    OPEN_URL = "windows.open_url"
    OPEN_PATH = "windows.open_path"
    LAUNCH_APP = "windows.launch_app"
    FOCUS_WINDOW = "windows.focus_window"
    MANAGE_WINDOW = "windows.manage_window"
    CLIPBOARD_READ = "windows.clipboard_read"
    CLIPBOARD_WRITE = "windows.clipboard_write"
    TYPE_TEXT = "windows.type_text"
    ACCESSIBILITY_ACTION = "windows.accessibility_action"


class RiskClass(str, Enum):
    """How much a mistake on this capability costs.

    Not a permission and not a gate -- `ApprovalRequirement` is the gate. This
    is the axis an audit is read along, and the axis a future policy would
    reason over.
    """

    #: Changes what is on screen and nothing else. Reversible by looking away.
    LOW = "low"
    #: Starts something, or opens something, that the person then sees.
    MODERATE = "moderate"
    #: Reads or writes content on the person's behalf.
    HIGH = "high"
    #: Synthesises input, or acts through the accessibility tree, where a
    #: mistake can reach a control the person did not intend to press.
    SENSITIVE = "sensitive"


class ApprovalRequirement(str, Enum):
    """Whether a human must approve this exact action before it runs."""

    #: An explicit, per-action approval bound to this exact request. Never
    #: eligible for trusted autonomy, at any configuration.
    ALWAYS = "always"
    #: An approval is required today. The kind is eligible to be granted
    #: configured, per-device trusted autonomy by an explicit enrolment change.
    REQUIRED_AUTONOMY_ELIGIBLE = "required_autonomy_eligible"
    #: An approval is required, and this build offers no autonomy path for it.
    REQUIRED = "required"


@dataclass(frozen=True)
class CapabilityDescriptor:
    """Everything the governance layer needs to know about one capability."""

    kind: CapabilityKind
    version: int
    risk: RiskClass
    approval: ApprovalRequirement
    #: One sentence an approver can read. Shown on the approval surface; it is
    #: the description of the *power*, never of a particular request.
    summary: str
    #: Whether performing this action twice has the same effect as performing
    #: it once. **Almost nothing is.** Launching an application twice is two
    #: applications; typing text twice is the text twice; opening a URL twice
    #: is two tabs. Only the two pure state-setting capabilities qualify:
    #: focusing an already-focused window, or maximising an already-maximised
    #: one, changes nothing the second time.
    #:
    #: This is what decides an action's `Repeatability`, and it is decided
    #: here rather than by the caller. A caller-chosen value would be a field
    #: on the wire that switches off *both* replay defences at once -- the
    #: server's one-lease guard and the device's durable executed-ledger --
    #: which would let one human approval be spent twice.
    idempotent_eligible: bool = False

    @property
    def trusted_autonomy_eligible(self) -> bool:
        """Whether an enrolment may ever place this kind under autonomy."""
        return self.approval is ApprovalRequirement.REQUIRED_AUTONOMY_ELIGIBLE


_DESCRIPTORS: dict[CapabilityKind, CapabilityDescriptor] = {
    CapabilityKind.OPEN_URL: CapabilityDescriptor(
        kind=CapabilityKind.OPEN_URL,
        version=CURRENT_CAPABILITY_VERSION,
        risk=RiskClass.MODERATE,
        approval=ApprovalRequirement.REQUIRED,
        summary=(
            "Open one http/https URL from an explicitly allowlisted domain in the "
            "default browser. No file:, javascript:, custom scheme or embedded "
            "credentials."
        ),
    ),
    CapabilityKind.OPEN_PATH: CapabilityDescriptor(
        kind=CapabilityKind.OPEN_PATH,
        version=CURRENT_CAPABILITY_VERSION,
        risk=RiskClass.MODERATE,
        approval=ApprovalRequirement.REQUIRED,
        summary=(
            "Open one existing file or folder that lies inside an explicitly "
            "allowlisted filesystem root. Never creates, deletes, moves, renames, "
            "overwrites or edits anything, and never opens an executable or script."
        ),
    ),
    CapabilityKind.LAUNCH_APP: CapabilityDescriptor(
        kind=CapabilityKind.LAUNCH_APP,
        version=CURRENT_CAPABILITY_VERSION,
        risk=RiskClass.MODERATE,
        approval=ApprovalRequirement.REQUIRED_AUTONOMY_ELIGIBLE,
        summary=(
            "Start one application named by an allowlist key. The executable path "
            "comes from the allowlist, never from the request, and no command-line "
            "argument can be supplied at all."
        ),
    ),
    CapabilityKind.FOCUS_WINDOW: CapabilityDescriptor(
        kind=CapabilityKind.FOCUS_WINDOW,
        version=CURRENT_CAPABILITY_VERSION,
        idempotent_eligible=True,
        risk=RiskClass.LOW,
        approval=ApprovalRequirement.REQUIRED_AUTONOMY_ELIGIBLE,
        summary=(
            "Bring one already-open window of an allowlisted application to the "
            "foreground. Refuses if the window cannot be resolved to exactly one "
            "match, and never falls back to synthesising keystrokes."
        ),
    ),
    CapabilityKind.MANAGE_WINDOW: CapabilityDescriptor(
        kind=CapabilityKind.MANAGE_WINDOW,
        version=CURRENT_CAPABILITY_VERSION,
        idempotent_eligible=True,
        risk=RiskClass.LOW,
        approval=ApprovalRequirement.REQUIRED_AUTONOMY_ELIGIBLE,
        summary=(
            "Focus, minimise, maximise, restore, or move/resize one already-open "
            "window of an allowlisted application within the visible desktop."
        ),
    ),
    CapabilityKind.CLIPBOARD_READ: CapabilityDescriptor(
        kind=CapabilityKind.CLIPBOARD_READ,
        version=CURRENT_CAPABILITY_VERSION,
        risk=RiskClass.HIGH,
        approval=ApprovalRequirement.ALWAYS,
        summary=(
            "Read the current clipboard text once. Sensitive content is refused "
            "rather than returned, and the content is not persisted unless the "
            "device operator has explicitly opted in."
        ),
    ),
    CapabilityKind.CLIPBOARD_WRITE: CapabilityDescriptor(
        kind=CapabilityKind.CLIPBOARD_WRITE,
        version=CURRENT_CAPABILITY_VERSION,
        risk=RiskClass.HIGH,
        approval=ApprovalRequirement.REQUIRED,
        summary=(
            "Replace the clipboard with one piece of bounded, ordinary text. "
            "Refuses anything the secret detector recognises as credential "
            "material."
        ),
    ),
    CapabilityKind.TYPE_TEXT: CapabilityDescriptor(
        kind=CapabilityKind.TYPE_TEXT,
        version=CURRENT_CAPABILITY_VERSION,
        risk=RiskClass.SENSITIVE,
        approval=ApprovalRequirement.ALWAYS,
        summary=(
            "Type one piece of bounded, ordinary text into the focused field. "
            "Cannot contain Enter, Tab or any control character, so it cannot "
            "press Send, Submit, Confirm, Purchase or Delete; refuses outright "
            "into a password, PIN, token or payment field."
        ),
    ),
    CapabilityKind.ACCESSIBILITY_ACTION: CapabilityDescriptor(
        kind=CapabilityKind.ACCESSIBILITY_ACTION,
        version=CURRENT_CAPABILITY_VERSION,
        risk=RiskClass.SENSITIVE,
        approval=ApprovalRequirement.ALWAYS,
        summary=(
            "Perform one allowlisted, non-consequential accessibility operation "
            "(expand, collapse, scroll, focus) on a named element of an "
            "allowlisted application. Invoking a control is deliberately absent."
        ),
    ),
}

#: Every capability this build implements, in a stable order.
ALL_CAPABILITIES: tuple[CapabilityKind, ...] = tuple(CapabilityKind)

#: The kinds an enrolment may place under configured trusted autonomy. Derived
#: from the descriptors rather than written twice, so the two can never drift.
TRUSTED_AUTONOMY_ELIGIBLE: frozenset[CapabilityKind] = frozenset(
    kind for kind, d in _DESCRIPTORS.items() if d.trusted_autonomy_eligible
)

#: The kinds that require a per-action approval no configuration can remove.
ALWAYS_APPROVAL: frozenset[CapabilityKind] = frozenset(
    kind for kind, d in _DESCRIPTORS.items() if d.approval is ApprovalRequirement.ALWAYS
)

#: The kinds a caller may declare idempotent. Everything else runs at most
#: once, whatever a request asks for -- see `CapabilityDescriptor`.
IDEMPOTENT_ELIGIBLE: frozenset[CapabilityKind] = frozenset(
    kind for kind, d in _DESCRIPTORS.items() if d.idempotent_eligible
)


def describe(kind: CapabilityKind) -> CapabilityDescriptor:
    """The descriptor for a known kind."""
    try:
        return _DESCRIPTORS[kind]
    except KeyError as e:  # pragma: no cover - unreachable while the enum is closed
        raise UnsupportedCapabilityError(f"no descriptor for {kind!r}") from e


def parse_kind(raw: str) -> CapabilityKind:
    """Turn a wire string into a `CapabilityKind`, or refuse it.

    The single place an untrusted string becomes a capability. It is a lookup
    against a closed set with no prefix matching, no case folding and no
    aliasing: `"windows.open_url "` with a trailing space is not
    `windows.open_url`, and `"windows.run"` is not anything at all.
    """
    if not isinstance(raw, str):
        raise UnsupportedCapabilityError(
            f"capability must be a string, not {type(raw).__name__}",
        )
    try:
        return CapabilityKind(raw)
    except ValueError as e:
        raise UnsupportedCapabilityError(
            f"{raw!r} is not a capability this build implements. The permitted "
            f"capabilities are {[k.value for k in ALL_CAPABILITIES]}.",
        ) from e


def require_supported(raw_kind: str, raw_version: object) -> CapabilityDescriptor:
    """Resolve a wire (kind, version) pair, or refuse it.

    Both halves are refused independently and for their own reason, so the
    caller's audit row says which one was wrong. A version mismatch is never
    resolved by picking the nearest implemented version.
    """
    kind = parse_kind(raw_kind)
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        raise UnsupportedCapabilityError(
            f"capability version must be an integer, not {type(raw_version).__name__}",
        )
    descriptor = describe(kind)
    if raw_version != descriptor.version:
        raise UnsupportedCapabilityError(
            f"{kind.value} version {raw_version} is not implemented by this build "
            f"(this build implements version {descriptor.version}). An unsupported "
            f"version is refused, never approximated.",
        )
    return descriptor
