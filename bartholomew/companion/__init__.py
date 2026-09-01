"""Bartholomew's PC companion: an observation-only device boundary.

A small process that runs on a personal computer and tells Bartholomew a
deliberately narrow amount about what is happening there -- whether the
companion is running, whether the person is active or idle, which application
has focus by name, and a little static host state. It submits those observations
through the **existing** governed inbound boundary
(`POST /api/inbound/events`), so the Parking Brake, the Identity policy gate,
provenance recording and idempotency are the ones already built, not new ones.

**This package cannot control the computer, and that is structural.** It has no
actuation verb, no command field, no response-driven dispatch, and does not
import `subprocess`, `os.system` or any input-synthesis API anywhere. It reads a
closed vocabulary of state fields (`observation.py`) and writes them in one
direction. `tests/test_companion_no_actuation.py` asserts each of those
properties over this package's source rather than leaving them to the docstring.

See `docs/D_PC_COMPANION_OBSERVATION.md` for the observation vocabulary, the
provenance model, and -- importantly -- the device-authentication limitations
this prototype does not close.
"""

from .config import COMPANION_VERSION

__all__ = ["COMPANION_VERSION"]
