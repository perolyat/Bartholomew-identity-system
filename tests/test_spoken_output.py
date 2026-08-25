"""
Local spoken output (text-to-speech) -- adapter and governed seam.

Bartholomew can now say an answer out loud on the machine it runs on. This
suite proves the capability works through a *real* subprocess (a recorder
script standing in for a speech binary, so the path under test is the actual
`subprocess.run()` one rather than a mock of it), and pins the boundaries
that make an audio-output capability safe to have at all:

  * **output only** -- there is no capture code behind any of this, and a
    structural test says so rather than leaving it to be believed;
  * **default OFF** -- the shipped config makes no sound, and the seam's
    `enabled` parameter defaults to False so a caller that forgets cannot
    accidentally enable it;
  * **the `voice` Parking Brake silences it** regardless of configuration;
  * **silence is never reported as speech** -- a missing engine, a failing
    engine and a timeout are each their own distinct, truthful outcome.

Real audio cannot be verified in CI (no speech binary is installed in the
container, which `test_no_engine_is_reported_truthfully` relies on and
documents). That last mile is a manual check on a machine with `espeak-ng`
or macOS `say`.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest

from bartholomew.kernel import spoken_output
from bartholomew.kernel.runtime_contract import (
    run_spoken_output_through_runtime_contract,
)
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from identity_interpreter.identity_context import IdentityContext

ALLOW_SPEECH = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["notify", "tasks", "voice_speak"],
)
DENY_SPEECH = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["notify", "tasks"],
)


@pytest.fixture
def recorder(tmp_path):
    """A real executable standing in for a speech binary.

    Deliberately a genuine subprocess rather than a patched `subprocess.run`:
    the argv construction, the exit-code handling and the "no shell" property
    are the parts worth testing, and a mock would test none of them.
    """
    if sys.platform.startswith("win"):
        pytest.skip("POSIX recorder script; Windows speech support is not implemented")

    log = tmp_path / "spoken.log"
    script = tmp_path / "fake-tts"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" >> "{log}"\n'
        'if [ "${FAKE_TTS_FAIL:-0}" = "1" ]; then echo "engine exploded" >&2; exit 3; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script, log


@pytest.fixture
def db_path(tmp_path):
    from bartholomew.kernel.scheduler import persistence as sp

    path = str(tmp_path / "speech.db")
    sp.ensure_schema(path)
    return path


class _Recorded:
    """An injected `speak_fn`, for the governance cases where what matters is
    whether the capability was reached at all."""

    def __init__(self, spoken: bool = True, detail: str | None = None):
        self.calls: list[str] = []
        self._spoken = spoken
        self._detail = detail

    def __call__(self, text: str):
        self.calls.append(text)
        return spoken_output.SpeechResult(
            spoken=self._spoken,
            engine="test",
            detail=self._detail,
            text=text,
        )


# ---------------------------------------------------------------------------
# 1. Text preparation.
# ---------------------------------------------------------------------------
class TestPrepareText:
    def test_ordinary_text_survives_intact(self):
        assert spoken_output.prepare_text("Your rego is due on Friday.") == (
            "Your rego is due on Friday."
        )

    def test_whitespace_is_collapsed(self):
        assert spoken_output.prepare_text("  hello \n\n  there  ") == "hello there"

    def test_control_characters_are_removed(self):
        assert "\x1b" not in spoken_output.prepare_text("hello \x1b[31m there \x07")

    def test_length_is_bounded(self):
        prepared = spoken_output.prepare_text("word " * 500)
        assert len(prepared) <= spoken_output.MAX_SPEECH_CHARS

    def test_a_leading_dash_cannot_be_read_as_an_option(self):
        prepared = spoken_output.prepare_text("--version")
        assert not prepared.startswith("-")
        assert "--version" in prepared

    @pytest.mark.parametrize("empty", ["", "   ", None, "\n\t"])
    def test_nothing_to_say_is_empty(self, empty):
        assert spoken_output.prepare_text(empty) == ""

    def test_preparation_is_deterministic(self):
        assert spoken_output.prepare_text("hi  there") == spoken_output.prepare_text("hi  there")


# ---------------------------------------------------------------------------
# 2. The adapter, against a real subprocess.
# ---------------------------------------------------------------------------
class TestSpeakText:
    def test_it_runs_the_engine_with_the_prepared_text(self, recorder, monkeypatch):
        script, log = recorder
        monkeypatch.setenv(spoken_output.ENGINE_COMMAND_ENV, str(script))

        result = spoken_output.speak_text("Your rego is due on Friday.")

        assert result.spoken is True
        assert result.engine == "fake-tts"
        assert log.read_text(encoding="utf-8").strip() == "Your rego is due on Friday."

    def test_the_text_is_one_argument_not_a_shell_string(self, recorder, monkeypatch):
        """Argv, never a shell -- so shell metacharacters are just characters."""
        script, log = recorder
        monkeypatch.setenv(spoken_output.ENGINE_COMMAND_ENV, str(script))

        result = spoken_output.speak_text("say this; rm -rf / && echo $(whoami)")

        assert result.spoken is True
        logged = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(logged) == 1, "the text was split into multiple arguments"
        assert logged[0] == "say this; rm -rf / && echo $(whoami)"

    def test_a_failing_engine_is_not_reported_as_spoken(self, recorder, monkeypatch):
        script, _log = recorder
        monkeypatch.setenv(spoken_output.ENGINE_COMMAND_ENV, str(script))
        monkeypatch.setenv("FAKE_TTS_FAIL", "1")

        result = spoken_output.speak_text("hello")

        assert result.spoken is False
        assert "exited 3" in (result.detail or "")
        assert "engine exploded" in (result.detail or "")

    def test_no_engine_is_reported_truthfully(self, monkeypatch):
        """A machine with no speech binary says so. This is also the CI
        container's real state, which is why real audio is a manual check."""
        monkeypatch.delenv(spoken_output.ENGINE_COMMAND_ENV, raising=False)
        monkeypatch.setattr(spoken_output.shutil, "which", lambda _name: None)

        result = spoken_output.speak_text("hello")

        assert result.spoken is False
        assert "no local speech engine" in (result.detail or "")

    def test_empty_text_speaks_nothing(self, recorder, monkeypatch):
        script, log = recorder
        monkeypatch.setenv(spoken_output.ENGINE_COMMAND_ENV, str(script))

        result = spoken_output.speak_text("   ")

        assert result.spoken is False
        assert result.detail == "nothing to say"
        assert not log.exists(), "an engine was run for nothing"

    def test_a_wedged_engine_is_abandoned_not_waited_on(self, tmp_path, monkeypatch):
        script = tmp_path / "hanging-tts"
        script.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv(spoken_output.ENGINE_COMMAND_ENV, str(script))

        result = spoken_output.speak_text("hello", timeout=0.5)

        assert result.spoken is False
        assert "timed out" in (result.detail or "")

    def test_an_override_naming_nothing_falls_back_to_discovery(self, monkeypatch):
        monkeypatch.setenv(spoken_output.ENGINE_COMMAND_ENV, "definitely-not-a-real-binary")
        monkeypatch.setattr(spoken_output.shutil, "which", lambda _name: None)

        assert spoken_output.available_engine() is None

    def test_speak_text_never_raises(self, tmp_path, monkeypatch):
        """Every failure mode is a result, not an exception -- a caller that
        cannot tell "spoke" from "did not speak" is the thing this must never
        produce."""
        missing = tmp_path / "not-executable"
        missing.write_text("not a program", encoding="utf-8")
        monkeypatch.setenv(spoken_output.ENGINE_COMMAND_ENV, str(missing))

        result = spoken_output.speak_text("hello")

        assert result.spoken is False
        assert result.detail


# ---------------------------------------------------------------------------
# 3. Enablement -- default OFF.
# ---------------------------------------------------------------------------
class TestDefaultOff:
    def test_the_shipped_config_is_silent(self):
        import yaml

        cfg = yaml.safe_load(open("config/kernel.yaml", encoding="utf-8"))
        assert cfg["voice"]["spoken_output"] is False
        assert spoken_output.enabled_for(cfg) is False

    @pytest.mark.parametrize("cfg", [None, {}, {"voice": {}}, {"timezone": "UTC"}])
    def test_anything_that_does_not_say_yes_means_no(self, cfg):
        assert spoken_output.enabled_for(cfg) is False

    def test_only_an_explicit_true_enables_it(self):
        assert spoken_output.enabled_for({"voice": {"spoken_output": True}}) is True

    async def test_the_seam_defaults_to_disabled(self, db_path):
        """A caller that forgets to pass `enabled` gets silence, not speech."""
        speak = _Recorded()

        result = await run_spoken_output_through_runtime_contract(
            "hello",
            db_path=db_path,
            speak_fn=speak,
        )

        assert result.governance_allowed is False
        assert result.started is False
        assert speak.calls == []

    async def test_disabled_reports_which_switch_is_off(self, db_path):
        result = await run_spoken_output_through_runtime_contract(
            "hello",
            enabled=False,
            db_path=db_path,
            speak_fn=_Recorded(),
        )
        assert "voice.spoken_output" in (result.reason or "")

    def test_the_engine_env_var_cannot_enable_speech(self, monkeypatch):
        """`BARTH_TTS_COMMAND` selects an engine; it is not a second
        enablement authority."""
        monkeypatch.setenv(spoken_output.ENGINE_COMMAND_ENV, "/bin/echo")
        assert spoken_output.enabled_for({"voice": {"spoken_output": False}}) is False


# ---------------------------------------------------------------------------
# 4. Governance.
# ---------------------------------------------------------------------------
class TestGovernance:
    async def test_it_speaks_once_every_gate_passes(self, db_path):
        speak = _Recorded()

        result = await run_spoken_output_through_runtime_contract(
            "Your rego is due on Friday.",
            enabled=True,
            db_path=db_path,
            identity_context=ALLOW_SPEECH,
            speak_fn=speak,
        )

        assert result.governance_allowed is True
        assert result.started is True
        assert result.outcome == "started"
        assert speak.calls == ["Your rego is due on Friday."]

    async def test_the_voice_parking_brake_silences_it(self, db_path):
        GovernanceStore(db_path).engage("voice", reason="test", actor="test")
        speak = _Recorded()

        result = await run_spoken_output_through_runtime_contract(
            "hello",
            enabled=True,
            db_path=db_path,
            identity_context=ALLOW_SPEECH,
            speak_fn=speak,
        )

        assert result.governance_allowed is False
        assert result.outcome == "parking_brake_denied"
        assert speak.calls == [], "the brake was engaged and it spoke anyway"

    async def test_a_global_brake_silences_it(self, db_path):
        GovernanceStore(db_path).engage("global", reason="test", actor="test")
        speak = _Recorded()

        result = await run_spoken_output_through_runtime_contract(
            "hello",
            enabled=True,
            db_path=db_path,
            speak_fn=speak,
        )

        assert result.governance_allowed is False
        assert speak.calls == []

    async def test_identity_policy_can_deny_it(self, db_path):
        speak = _Recorded()

        result = await run_spoken_output_through_runtime_contract(
            "hello",
            enabled=True,
            db_path=db_path,
            identity_context=DENY_SPEECH,
            speak_fn=speak,
        )

        assert result.governance_allowed is False
        assert result.outcome == "governance_denied"
        assert "Identity policy" in (result.reason or "")
        assert speak.calls == []

    async def test_the_shipped_identity_allowlists_speaking(self):
        import yaml

        identity = yaml.safe_load(open("Identity.yaml", encoding="utf-8"))
        assert "voice_speak" in identity["tool_use"]["allowlist"]

    async def test_speaking_is_a_separate_kind_from_streaming(self):
        """Allowing Bartholomew to speak must never read as allowing it to
        listen, so the two are distinct allowlist entries."""
        from bartholomew.kernel.runtime_contract import (
            _VOICE_SPEAK_KIND,
            _VOICE_STREAM_KIND,
        )

        assert _VOICE_SPEAK_KIND != _VOICE_STREAM_KIND

        import yaml

        identity = yaml.safe_load(open("Identity.yaml", encoding="utf-8"))
        assert _VOICE_STREAM_KIND not in identity["tool_use"]["allowlist"]

    async def test_every_attempt_is_reflected(self, db_path):
        import sqlite3

        await run_spoken_output_through_runtime_contract(
            "hello",
            enabled=True,
            db_path=db_path,
            speak_fn=_Recorded(),
        )

        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT meta FROM reflections ORDER BY id DESC LIMIT 3").fetchall()
        finally:
            conn.close()
        blob = " ".join(row[0] or "" for row in rows)
        assert "voice_speak" in blob
        assert "voice_output" in blob

    async def test_a_denial_is_reflected_too(self, db_path):
        import sqlite3

        await run_spoken_output_through_runtime_contract(
            "hello",
            enabled=False,
            db_path=db_path,
            speak_fn=_Recorded(),
        )

        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT meta FROM reflections ORDER BY id DESC LIMIT 3").fetchall()
        finally:
            conn.close()
        assert any("governance_denied" in (row[0] or "") for row in rows)


# ---------------------------------------------------------------------------
# 5. Silence is never reported as speech.
# ---------------------------------------------------------------------------
class TestTruthfulness:
    async def test_an_engine_that_did_not_speak_is_not_reported_as_started(self, db_path):
        speak = _Recorded(spoken=False, detail="no local speech engine found")

        result = await run_spoken_output_through_runtime_contract(
            "hello",
            enabled=True,
            db_path=db_path,
            speak_fn=speak,
        )

        assert result.governance_allowed is True, "governance did allow the attempt"
        assert result.started is False, "silence was reported as speech"
        assert result.outcome == "error"
        assert "no local speech engine" in (result.reason or "")

    async def test_an_exploding_engine_does_not_raise_into_the_caller(self, db_path):
        def _boom(_text):
            raise RuntimeError("audio subsystem gone")

        result = await run_spoken_output_through_runtime_contract(
            "hello",
            enabled=True,
            db_path=db_path,
            speak_fn=_boom,
        )

        assert result.started is False
        assert result.outcome == "error"
        assert "audio subsystem gone" in (result.reason or "")

    async def test_the_real_adapter_path_works_end_to_end(self, db_path, recorder, monkeypatch):
        """No injected `speak_fn`: the seam reaches the real adapter, which
        runs a real subprocess."""
        script, log = recorder
        monkeypatch.setenv(spoken_output.ENGINE_COMMAND_ENV, str(script))

        result = await run_spoken_output_through_runtime_contract(
            "Your rego is due on Friday.",
            enabled=True,
            db_path=db_path,
            identity_context=ALLOW_SPEECH,
        )

        assert result.started is True
        assert log.read_text(encoding="utf-8").strip() == "Your rego is due on Friday."


# ---------------------------------------------------------------------------
# 6. Output only -- structural, not merely asserted in prose.
# ---------------------------------------------------------------------------
class TestOutputOnly:
    def test_the_adapter_contains_no_capture_machinery(self):
        import inspect

        source = inspect.getsource(spoken_output).lower()
        # Words that appear legitimately in the module's own explanation of
        # what it does NOT do are excluded by checking for capture *calls*
        # and capture libraries, not for the English words.
        for forbidden in (
            "pyaudio",
            "sounddevice",
            "speech_recognition",
            "openmicrophone",
            "def listen",
            "def record",
            "def capture",
            "input_device",
        ):
            assert forbidden not in source, f"spoken_output must not contain {forbidden!r}"

    def test_the_adapter_never_uses_a_shell(self):
        import inspect

        source = inspect.getsource(spoken_output)
        assert "shell=True" not in source
        assert "os.system" not in source
        assert "os.popen" not in source
        # And positively: the one invocation passes an argv list.
        assert "resolved.argv(prepared)" in source

    def test_the_seam_reaches_this_machine_only(self):
        import inspect

        from bartholomew.kernel import runtime_contract

        source = inspect.getsource(runtime_contract.run_spoken_output_through_runtime_contract)
        for forbidden in ("requests.", "http://", "https://", "socket", "urlopen"):
            assert forbidden not in source, f"spoken output must not reach {forbidden!r}"

    def test_the_engine_list_is_local_binaries_only(self):
        for name, _flags in spoken_output._KNOWN_ENGINES:
            assert "/" not in name and ":" not in name
            assert not name.startswith("http")


# ---------------------------------------------------------------------------
# 7. The CLI entry point exists and is discoverable.
# ---------------------------------------------------------------------------
class TestCli:
    def test_the_say_command_is_registered(self):
        from bartholomew import cli

        names = {
            getattr(command, "name", None) or command.callback.__name__
            for command in cli.app.registered_commands
        }
        assert "say" in names

    def test_the_say_command_is_silent_by_default(self, tmp_path, monkeypatch):
        """Running the shipped configuration must not make a sound."""
        import subprocess

        env = {**os.environ, "PYTHONPATH": os.getcwd()}
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "bartholomew.cli",
                "say",
                "hello",
                "--db",
                str(tmp_path / "cli.db"),
            ],
            capture_output=True,
            env=env,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 1
        assert b"Nothing was spoken" in completed.stdout
