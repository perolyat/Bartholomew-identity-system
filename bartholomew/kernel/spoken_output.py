"""
Local spoken output (text-to-speech) -- adapter only
====================================================

Lets Bartholomew say an answer out loud on the machine it is running on.

**Output only.** Nothing in this module, and nothing that calls it, opens a
microphone, listens, monitors for a wake word, captures ambient audio, or
reaches any device other than this machine's own default audio output. There
is no capture path here to disable, because none is implemented.

**Default OFF.** `config/kernel.yaml`'s `voice.spoken_output` (default
`false`) is the consent to make sound, and `enabled_for()` below is the one
place that reads it. Turning it on is a deliberate operator act; nothing
speaks without it, and the `voice` Parking Brake scope stops it regardless.
Governance lives in `runtime_contract.run_spoken_output_through_runtime_
contract()` -- this module is the capability the seam calls once every gate
has passed, exactly as `capture_fn`/`stream_fn` are for the sight and voice
seams. It is deliberately not reachable any other way in production.

**No new dependency.** This shells out to whatever speech binary the machine
already has (`espeak-ng`, `espeak`, `spd-say`, macOS `say`). If none is
present, that is reported truthfully as "no engine" -- never as speech that
happened. Adding a Python TTS package (and its platform binding tree) for a
prototype would be the "half the sprint on a speech platform" this work was
told not to do.

**How the subprocess is invoked, and why.** `subprocess.run()` with an
argument *list* and no shell at all -- there is no shell to inject into. Text is
bounded, stripped of control characters, and guarded against being read as a
leading option flag. The call is time-bounded, so a wedged engine cannot hang
the worker thread it runs on.

Known limitation, recorded rather than papered over: Windows has no
argv-based system speech binary of this kind, so `available_engine()` finds
none there and `speak_text()` reports "no engine". Adding Windows support
means driving SAPI, which means constructing a PowerShell command string
around user text -- a different and larger safety question than argv, and
deliberately not answered here.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess  # noqa: S404 - argv list only, never a shell; see module docstring
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Config key holding the operator's consent to make sound.
CONFIG_SECTION = "voice"
CONFIG_KEY = "spoken_output"

#: Optional explicit engine selection, for a machine whose speech binary is
#: not on PATH under one of the known names, and for tests that want to point
#: at a recorder. This selects WHICH engine is used; it is deliberately NOT an
#: enablement switch -- an unset config flag still means silence, so this can
#: never be the thing that makes Bartholomew start speaking.
ENGINE_COMMAND_ENV = "BARTH_TTS_COMMAND"

#: A spoken answer is a sentence or two, not a document. Anything longer is
#: truncated rather than narrated at length -- provisional, like every other
#: POC constant.
MAX_SPEECH_CHARS = 600

#: A wedged engine must not hold its worker thread forever.
DEFAULT_TIMEOUT_SECONDS = 20.0

#: Known argv-based local speech binaries, in preference order. Every entry
#: takes its text as a single positional argument after any flags, so no
#: shell string is ever constructed.
_KNOWN_ENGINES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("espeak-ng", ()),
    ("espeak", ()),
    ("spd-say", ("--wait",)),
    ("say", ()),
)

_CONTROL_CHARS = {chr(code) for code in range(32)} | {chr(127)}


@dataclass(frozen=True)
class SpeechEngine:
    """A resolved local speech binary."""

    name: str
    path: str
    flags: tuple[str, ...] = ()

    def argv(self, text: str) -> list[str]:
        return [self.path, *self.flags, text]


@dataclass(frozen=True)
class SpeechResult:
    """What actually happened to one spoken-output attempt.

    `spoken` is True only when an engine ran and exited successfully. Every
    other case -- no engine, empty text, a non-zero exit, a timeout -- is
    False with a `detail` saying which, so a caller can never read silence as
    speech.
    """

    spoken: bool
    engine: str | None = None
    detail: str | None = None
    text: str = ""


def enabled_for(cfg: dict | None) -> bool:
    """
    Whether the operator has turned spoken output on.

    One authority for reading the flag, and one flag: `voice.spoken_output`,
    default `false`. There is deliberately no environment-variable override
    of *enablement* -- `ENGINE_COMMAND_ENV` selects an engine and cannot
    switch the capability on -- so there is exactly one place that decides
    whether Bartholomew may make sound.
    """
    section = (cfg or {}).get(CONFIG_SECTION) or {}
    return bool(section.get(CONFIG_KEY, False))


def prepare_text(text: str | None) -> str:
    """
    Bound and clean text for an argv-based speech engine. Pure.

    Three things, each for a stated reason:
      * control characters are dropped -- a terminal escape sequence in a
        spoken string is meaningless to an engine and meaningful to a
        terminal;
      * length is bounded, so a long answer is truncated rather than read
        aloud indefinitely;
      * a leading "-" is neutralised, so text can never be parsed as an
        option by the engine. (Injection is already impossible -- the argv
        list never becomes a shell string -- this is about the engine's own
        argument parsing, not about the shell.)
    """
    if not text:
        return ""
    cleaned = "".join(" " if char in _CONTROL_CHARS else char for char in text)
    cleaned = " ".join(cleaned.split()).strip()
    if not cleaned:
        return ""
    if len(cleaned) > MAX_SPEECH_CHARS:
        cleaned = cleaned[:MAX_SPEECH_CHARS].rstrip()
    if cleaned.startswith("-"):
        cleaned = f" {cleaned}"
    return cleaned


def available_engine() -> SpeechEngine | None:
    """
    The local speech binary this machine has, or None.

    None is a real and expected answer (a headless CI container, a machine
    with no speech package, Windows). It is reported as "no engine", never
    smoothed over.
    """
    override = (os.getenv(ENGINE_COMMAND_ENV) or "").strip()
    if override:
        resolved = shutil.which(override) or (override if os.path.exists(override) else None)
        if resolved:
            return SpeechEngine(name=os.path.basename(override), path=resolved)
        logger.warning(
            "%s names %r, which is not an executable on this machine; "
            "falling back to engine discovery",
            ENGINE_COMMAND_ENV,
            override,
        )

    for name, flags in _KNOWN_ENGINES:
        path = shutil.which(name)
        if path:
            return SpeechEngine(name=name, path=path, flags=flags)
    return None


def speak_text(
    text: str,
    *,
    engine: SpeechEngine | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> SpeechResult:
    """
    Say `text` out loud through a local speech engine.

    **Blocking.** Runs a subprocess, so callers on the event loop must go
    through `run_off_loop()` -- the seam does. Never raises: every failure
    mode becomes a `SpeechResult` saying what went wrong, because a caller
    that cannot tell "spoke" from "did not speak" is the one thing this must
    not produce.

    Args:
        text: what to say. Passed through `prepare_text()` first.
        engine: an already-resolved engine; discovered when omitted.
        timeout: seconds before a wedged engine is abandoned.
    """
    prepared = prepare_text(text)
    if not prepared:
        return SpeechResult(spoken=False, detail="nothing to say", text="")

    resolved = engine or available_engine()
    if resolved is None:
        return SpeechResult(
            spoken=False,
            detail=(
                "no local speech engine found (looked for "
                f"{', '.join(name for name, _ in _KNOWN_ENGINES)})"
            ),
            text=prepared,
        )

    try:
        completed = subprocess.run(  # noqa: S603 - argv list, no shell, bounded text
            resolved.argv(prepared),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Speech engine %s timed out after %.1fs", resolved.name, timeout)
        return SpeechResult(
            spoken=False,
            engine=resolved.name,
            detail=f"speech engine timed out after {timeout:.0f}s",
            text=prepared,
        )
    except OSError as exc:
        logger.warning("Speech engine %s could not be run: %s", resolved.name, exc)
        return SpeechResult(
            spoken=False,
            engine=resolved.name,
            detail=f"speech engine could not be run: {exc}",
            text=prepared,
        )

    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", "replace").strip()
        return SpeechResult(
            spoken=False,
            engine=resolved.name,
            detail=f"speech engine exited {completed.returncode}: {stderr[:200]}",
            text=prepared,
        )

    return SpeechResult(spoken=True, engine=resolved.name, text=prepared)
