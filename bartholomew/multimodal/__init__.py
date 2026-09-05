"""Explicit-session multimodal presence: hearing, speaking, seeing a screen.

Package C of the Windows Capability Wave. Everything here is reachable only
through an **explicit, visible, time-bounded session** that a person asked for
and consented to. There is no ambient path: no wake word, no always-listening
mode, no background screen recorder, no automatic restart of a finished
session. A session begins because a human asked for one, and it ends on stop,
expiry, brake or failure -- never on its own initiative.

**Three separate permissions, never one switch.** `multimodal.microphone_session`,
`multimodal.screen_capture` and `multimodal.spoken_output` are distinct
capability kinds with distinct consent prompts, distinct Identity-policy
decisions and distinct Parking Brake checks. Permission to speak is not
permission to listen; permission to listen is not permission to look at the
screen; permission to capture one window is not permission to capture another
or the whole desktop. `tests/test_multimodal_separation.py` asserts this
structurally rather than trusting this paragraph.

**This package owns no authority it did not create.** Governance is the
existing Runtime Contract seam; the brake is the existing `GovernanceStore`;
consent is the existing `privacy_guard` handler; spoken output is the existing
`bartholomew.kernel.spoken_output` adapter. Nothing here is a second engine
for any of those.

Module map:

* `modality.py`  -- the three capability kinds and the bounded capture scope.
* `session.py`   -- the session record and its validated state machine.
* `store.py`     -- the session registry, including restart reconciliation.
* `devices.py`   -- the narrow read-only view of Session E's device contract.
* `privacy.py`   -- privacy/retention classification and secret redaction.
* `microphone.py`-- microphone adapter and hardware diagnostics.
* `accessibility.py` -- accessibility-tree observation (preferred source).
* `screen.py`    -- bounded screenshot fallback (used only when accessibility
                    is insufficient and the session explicitly permits it).
* `speech.py`    -- integration with the existing spoken-output seam.
* `events.py`    -- logical canonical-event serializers and the Session F adapter.
* `status.py`    -- the visible "what is Bartholomew doing right now" surface.
* `diagnostics.py` -- the Windows multimodal diagnostic report.
"""
