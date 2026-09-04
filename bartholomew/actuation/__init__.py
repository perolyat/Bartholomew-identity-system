"""The governed Windows action boundary: models, governance, and durable state.

This package is the **server side** of Bartholomew's actuation, and it is a
deliberately different trust channel from observation.
`bartholomew/companion/` submits observations one way and can never receive an
executable action; this package admits, governs, records and hands out typed
action requests, and never observes anything.

**Nothing in this package touches an operating system.** There is no
`ctypes`, no `subprocess`, no window handle and no clipboard here. It decides
*whether* an action may happen and records *what happened*; the machinery that
makes it happen lives in `bartholomew/windows_actuation/`, on the device,
behind a separately authenticated channel. Keeping the decision and the
mechanism in different packages is what makes "the server cannot be tricked
into acting" and "the device cannot act unbidden" two separate arguments
rather than one hopeful one.

Read these four modules and you have read the entire action vocabulary:

* `capabilities.py` -- the nine typed, versioned capability kinds. A closed
  enum. An unknown kind or version is refused, never approximated.
* `parameters.py`   -- the canonical typed parameters for each kind, validated
  before anything reaches a device. There is no `command`, `script`, `shell`,
  `args` or free-form field anywhere in it.
* `approval.py`     -- what an approval binds to, and everything it therefore
  cannot authorise.
* `seam.py`         -- the eleven-point admission every action passes, in
  order, through the repository's existing Governance authorities.

`tests/test_windows_action_prohibitions.py` asserts the absences over this
package's source rather than trusting this docstring.
"""

from .capabilities import (
    ALL_CAPABILITIES,
    CURRENT_CAPABILITY_VERSION,
    ApprovalRequirement,
    CapabilityKind,
    RiskClass,
    UnsupportedCapabilityError,
    describe,
    require_supported,
)

__all__ = [
    "ALL_CAPABILITIES",
    "CURRENT_CAPABILITY_VERSION",
    "ApprovalRequirement",
    "CapabilityKind",
    "RiskClass",
    "UnsupportedCapabilityError",
    "describe",
    "require_supported",
]
