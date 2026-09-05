"""What an ordinary person needs to see: is it listening, watching, speaking?

Contract §7 deliverable 7 and gate 18: active capture and output state must be
visible. "Visible" here means a person who is not reading logs can answer, at a
glance:

* is Bartholomew listening right now?
* is Bartholomew observing my screen -- and *which* window, screen or region?
* is Bartholomew speaking?
* when did this start, and when does it stop on its own?
* how do I stop it immediately?
* is any of this unavailable because hardware or permission is missing?

`status_snapshot()` answers all of those in one dict, in plain words. It is
deliberately a *derived* view over the session store rather than a second
record of what is happening: there is one authority for whether a session is
live, and it is the session's own state machine.

Hardware availability is included even when nothing is running, because "you
have no microphone" is something a person needs to see *before* they wonder
why nothing was heard.
"""

from __future__ import annotations

from typing import Any

from .microphone import MicrophoneSessionAdapter
from .modality import Modality
from .session import MultimodalSession
from .speech import output_available
from .store import SessionStore


def _describe(session: MultimodalSession) -> dict[str, Any]:
    """One live session, in words rather than state names."""
    verb = {
        Modality.MICROPHONE: "listening on the microphone",
        Modality.SCREEN: "observing the screen",
        Modality.SPOKEN_OUTPUT: "speaking aloud",
    }[session.modality]
    remaining = session.seconds_remaining()
    return {
        "session_id": session.session_id,
        "modality": session.modality.value,
        "summary": (
            f"Bartholomew is {verb}" + (f" ({session.scope.describe()})" if session.scope else "")
        ),
        "scope": session.scope.describe() if session.scope else None,
        "started_at": session.started_at,
        "expires_at": session.expires_at,
        "seconds_remaining": remaining,
        "stops_automatically_in": (
            f"{int(remaining)}s" if remaining is not None else "not started"
        ),
        "how_to_stop": f"POST /api/multimodal/sessions/{session.session_id}/stop",
        "state": session.state.value,
    }


def hardware_status(
    microphone_backend: Any | None = None,
) -> dict[str, Any]:
    """Whether this machine can listen and speak, truthfully.

    Screen capture availability is intentionally not probed here: probing it
    means touching the display, and this surface is read by anyone loading a
    status page. Screen backend availability is reported by the diagnostic
    command instead, which a person runs deliberately.
    """
    microphone = MicrophoneSessionAdapter(microphone_backend).probe()
    speech_ok, speech_detail = output_available()
    return {
        "microphone": microphone.as_dict(),
        "spoken_output": {"available": speech_ok, "detail": speech_detail},
    }


def status_snapshot(
    store: SessionStore,
    *,
    tenant_id: str | None = None,
    microphone_backend: Any | None = None,
    include_hardware: bool = True,
) -> dict[str, Any]:
    """The complete visible state of multimodal presence, for API and UI."""
    live = store.live(tenant_id)
    by_modality = {m: [s for s in live if s.modality is m] for m in Modality}

    snapshot: dict[str, Any] = {
        "listening": bool(by_modality[Modality.MICROPHONE]),
        "observing_screen": bool(by_modality[Modality.SCREEN]),
        "speaking": bool(by_modality[Modality.SPOKEN_OUTPUT]),
        "active_sessions": [_describe(s) for s in live],
        "active_session_count": len(live),
    }
    snapshot["summary"] = (
        "Bartholomew is not listening, observing your screen or speaking."
        if not live
        else "; ".join(item["summary"] for item in snapshot["active_sessions"]) + "."
    )
    if include_hardware:
        snapshot["hardware"] = hardware_status(microphone_backend)
    return snapshot
