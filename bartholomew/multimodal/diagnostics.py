"""`bartholomew multimodal diagnose` -- what works on this machine, and why not.

The deployment requirement in contract §7 is a diagnostic command, and the
reason it matters here more than usual is that the target machine may have a
broken or absent microphone. A person in that situation needs to be told which
of several different things is wrong -- no optional dependency, no device, OS
permission denied -- because the fix differs each time.

**Diagnostics observe nothing.** Probing asks the operating system whether a
device and a backend exist. It does not open a stream, does not capture audio,
does not read the accessibility tree of whatever happens to be on screen and
does not take a screenshot. Running this command therefore needs no session
and no consent, because it collects nothing about the user -- only about the
machine's capabilities.
"""

from __future__ import annotations

import platform
import sys
from typing import Any

from .accessibility import default_provider
from .microphone import MicrophoneSessionAdapter
from .screen import default_backend as default_screen_backend
from .speech import output_available


def diagnose(
    *,
    microphone_backend: Any | None = None,
    accessibility_provider: Any | None = None,
    screen_backend: Any | None = None,
) -> dict[str, Any]:
    """A full capability report for this machine. Never raises."""
    microphone = MicrophoneSessionAdapter(microphone_backend).probe()

    provider = accessibility_provider or default_provider()
    try:
        a11y_ok, a11y_detail = provider.available()
    except Exception as exc:
        a11y_ok, a11y_detail = False, str(exc)

    backend = screen_backend or default_screen_backend()
    try:
        screen_ok, screen_detail = backend.available()
    except Exception as exc:
        screen_ok, screen_detail = False, str(exc)

    speech_ok, speech_detail = output_available()

    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python": sys.version.split()[0],
        },
        "microphone": microphone.as_dict(),
        "spoken_output": {"available": speech_ok, "detail": speech_detail},
        "accessibility": {"available": a11y_ok, "detail": a11y_detail},
        "screen_capture": {"available": screen_ok, "detail": screen_detail},
        "notes": _notes(microphone.usable, speech_ok, a11y_ok, screen_ok),
    }


def _notes(mic: bool, speech: bool, a11y: bool, screen: bool) -> list[str]:
    """Plain-language next steps for whatever is missing."""
    notes: list[str] = []
    if not mic:
        notes.append(
            "Microphone sessions will report 'unavailable'. This is a supported "
            "state: Bartholomew will refuse to listen rather than pretend to. "
            "See docs/C_MULTIMODAL_WINDOWS_PRESENCE.md for the microphone "
            "troubleshooting steps.",
        )
    if not speech:
        notes.append(
            "Spoken output has no engine on this machine, so 'speak' will "
            "truthfully report that nothing was said. On Windows this is "
            "expected today -- the existing spoken-output adapter finds no "
            "argv-based speech binary there.",
        )
    if not a11y:
        notes.append(
            "Accessibility observation is unavailable, so screen sessions "
            "cannot use the preferred structured path. Install the optional "
            "'uiautomation' dependency on Windows.",
        )
    if not screen:
        notes.append(
            "Screen capture is unavailable; the screenshot fallback cannot run "
            "even when a session authorises it. Install the optional 'mss' "
            "dependency.",
        )
    if not notes:
        notes.append("All multimodal capabilities are available on this machine.")
    return notes


def format_report(report: dict[str, Any]) -> str:
    """Human-readable rendering for the CLI."""
    lines = [
        "Bartholomew multimodal diagnostics",
        "==================================",
        f"Platform : {report['platform']['system']} {report['platform']['release']}",
        f"Python   : {report['platform']['python']}",
        "",
        f"Microphone      : {'OK' if report['microphone']['usable'] else 'UNAVAILABLE'}"
        f" -- {report['microphone']['detail']}",
        f"Spoken output   : {'OK' if report['spoken_output']['available'] else 'UNAVAILABLE'}"
        f" -- {report['spoken_output']['detail']}",
        f"Accessibility   : {'OK' if report['accessibility']['available'] else 'UNAVAILABLE'}"
        f" -- {report['accessibility']['detail']}",
        f"Screen capture  : {'OK' if report['screen_capture']['available'] else 'UNAVAILABLE'}"
        f" -- {report['screen_capture']['detail']}",
        "",
        "Notes:",
    ]
    lines.extend(f"  * {note}" for note in report["notes"])
    lines.append("")
    lines.append(
        "This command observed nothing: no audio was captured, no screen was "
        "read, no image was taken.",
    )
    return "\n".join(lines)
